"""Veille des annuaires (sans API) : signale quand les fiches FLUGIA sur Crunchbase, AlternativeTo, AI Agents Directory et
Product Hunt doivent être mises à jour ou complétées, et prépare le lancement Product Hunt.

Ces sites ne permettent pas de modifier une fiche par programme et bloquent la lecture automatique de leurs pages :
les alertes indiquent quoi corriger, la mise à jour se fait à la main.

Déclencheurs, pour chaque annuaire :
  1. revue périodique : la date de dernière mise à jour (onglet Paramétrages) dépasse N jours ;
  2. changement sur flugia.com : titre, description, accroche, gamme de Co-Workers, tarifs, réseaux sociaux
     (relevés chaque nuit, comparés au relevé précédent) ;
  3. informations manquantes sur la fiche (liste cochée dans Paramétrages) ;
  4. événement saisi dans Paramétrages (levée de fonds, nouvelle offre…) pas encore reporté ;
  5. Crunchbase uniquement : nouvelle mention dans la presse ou sur un site tiers (nouveau site référent GA4).
"""
from __future__ import annotations

import hashlib
import html as html_mod
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from .config import alerts_config

SITE = os.environ.get("SITE_URL", "https://flugia.com").rstrip("/")
PAGES = {"accueil": "/", "tarifs": "/pricing-flugia/"}
BADGE_DOMAINS = ("aiagentsdirectory.com", "alternativeto.net", "producthunt.com", "theresanaiforthat.com")
SOCIAL = ("linkedin.com", "instagram.com", "x.com", "twitter.com", "facebook.com", "youtube.com", "tiktok.com")

FIELDS = {
    "accueil.titre": ("Titre du site", "moyenne", "Mettre à jour le « Short description » du profil."),
    "accueil.description": ("Description du site", "moyenne", "Aligner la description complète (« Description ») sur celle du site."),
    "accueil.accroche": ("Accroche de la page d'accueil", "info", "Vérifier que le « Short description » reflète toujours le positionnement."),
    "offre.co_workers": ("Gamme de Co-Workers", "moyenne", "Mettre à jour la description et les catégories (« Industries ») avec les Co-Workers ajoutés ou retirés."),
    "tarifs.prix": ("Tarifs", "info", "Vérifier les informations d'offre et de prix mentionnées dans la description."),
    "reseaux": ("Réseaux sociaux", "moyenne", "Mettre à jour les liens LinkedIn, Instagram et X du profil."),
    # relevés propres aux annuaires (libellés pour l'interface ; pas d'alerte « changement sur flugia.com »)
    "annuaire.badges": ("Badges d'annuaires sur flugia.com", "info", ""),
    "annuaire.aiagentsdirectory.statut": ("AI Agents Directory : fiche publique", "info", ""),
    "annuaire.aiagentsdirectory.avis": ("AI Agents Directory : nombre d'avis", "info", ""),
    "annuaire.aiagentsdirectory.note": ("AI Agents Directory : note moyenne", "info", ""),
}

EVENT_TYPES = ["Levée de fonds", "Nouvelle offre ou nouveau produit", "Effectif (recrutements, départs)", "Partenariat",
               "Prix ou distinction", "Changement d'adresse ou de dirigeant", "Autre"]
EVENT_ACTION = {
    "Levée de fonds": "Ajouter le tour de financement (« Funding Rounds » : montant, date, investisseurs).",
    "Nouvelle offre ou nouveau produit": "Mettre à jour la description et ajouter le lien de l'annonce dans « News ».",
    "Effectif (recrutements, départs)": "Mettre à jour la tranche d'effectif (« Number of Employees ») et l'équipe.",
    "Partenariat": "Ajouter l'annonce dans « News » et, si besoin, le partenaire dans la description.",
    "Prix ou distinction": "Ajouter l'article ou l'annonce dans « News ».",
    "Changement d'adresse ou de dirigeant": "Mettre à jour le siège (« Headquarters ») ou l'équipe dirigeante (« People »).",
}


# Contenu attendu sur la fiche Crunchbase : clé -> (libellé, priorité, ce qu'il faut ajouter)
PROFILE_FIELDS = {
    "logo": ("Logo", "moyenne", "Ajouter le logo carré (« Profile Image ») : sans logo, la fiche paraît inactive."),
    "description": ("Descriptions courte et complète", "moyenne", "Renseigner « Description » et « Full Description »."),
    "site": ("Site web", "moyenne", "Renseigner « Website » : https://flugia.com."),
    "linkedin": ("Page LinkedIn", "info", "Renseigner « LinkedIn » : https://www.linkedin.com/company/flugia_ai."),
    "x": ("Compte X", "info", "Renseigner « X (Twitter) » : https://x.com/flugia_ai."),
    "facebook": ("Page Facebook", "info", "Renseigner « Facebook » : https://www.facebook.com/FLUGIAapp."),
    "creation": ("Date de création", "info", "Renseigner « Founded Date »."),
    "effectif": ("Effectif", "info", "Renseigner « Number of Employees »."),
    "siege": ("Siège", "info", "Renseigner « Headquarters » (Galerie Ravenstein 25, 1000 Bruxelles)."),
    "secteurs": ("Secteurs", "info", "Ajouter les « Industries » (Artificial Intelligence, SaaS, Marketing Automation…)."),
    "fondateurs": ("Fondateurs", "info", "Ajouter les fondateurs (« Founders ») avec leur profil."),
    "equipe": ("Équipe mise en avant", "info", "Ajouter 2 ou 3 personnes clés dans « Featured Employees »."),
    "actualites": ("Actualités (au moins un article)", "info", "Ajouter un article ou une annonce sur FLUGIA dans « News »."),
    "contact": ("E-mail et téléphone de contact", "info", "Renseigner « Contact Email » et « Phone Number »."),
}


AT_FIELDS = {
    "publiee": ("Fiche validée et publiée", "moyenne",
                "Suivre la validation dans « My submissions ». La file d'attente est longue ; l'option payante « Get reviewed sooner » "
                "la ramène à 1-2 jours ouvrés."),
    "logo": ("Logo", "moyenne", "Ajouter le logo carré de FLUGIA."),
    "description": ("Description", "moyenne", "Renseigner une description claire (Co-Workers, intégrations, prix, langues)."),
    "site": ("Site web", "moyenne", "Renseigner le lien officiel https://flugia.com."),
    "reseaux": ("Réseaux sociaux", "info", "Ajouter LinkedIn, X, Facebook et Instagram dans « Social networks »."),
    "captures": ("Captures d'écran", "moyenne", "Ajouter 3 à 5 captures de l'interface (tableau de bord, validation, Co-Workers)."),
    "fonctionnalites": ("Fonctionnalités", "info", "Ajouter les fonctionnalités (Marketing Automation, AI Agent, Customer Support…)."),
    "tags": ("Tags", "info", "Ajouter des tags (ai-agent, sales-automation, seo, crm…)."),
    "tarif": ("Prix et licence", "info", "Renseigner « Cost / License » : abonnement, logiciel propriétaire, essai gratuit."),
    "langues": ("Langues", "info", "Indiquer l'anglais, le français et le néerlandais."),
    "plateformes": ("Plateformes", "info", "Indiquer « Online » et « Software as a Service (SaaS) »."),
    "alternatives": ("Liens avec les concurrents", "moyenne",
                     "Proposer FLUGIA comme alternative sur les fiches des concurrents : {concurrents}."),
}
AT_SITE_ACTIONS = {
    "accueil.titre": "Mettre à jour la description courte.",
    "accueil.description": "Aligner la description sur celle du site.",
    "accueil.accroche": "Vérifier que la description reflète toujours le positionnement.",
    "offre.co_workers": "Mettre à jour la description et les fonctionnalités avec les Co-Workers ajoutés ou retirés.",
    "tarifs.prix": "Mettre à jour « Cost / License » et le prix cité dans la description.",
    "reseaux": "Mettre à jour les liens « Social networks ».",
}
AT_EVENT_ACTION = {
    "Nouvelle offre ou nouveau produit": "Mettre à jour la description et les fonctionnalités de la fiche.",
    "Partenariat": "Ajouter l'intégration ou le partenaire dans la description si c'est un argument produit.",
    "Prix ou distinction": "Mentionner la distinction dans la description.",
}

AAD_FIELDS = {
    "logo": ("Logo", "moyenne", "Ajouter le logo carré (« Media > Logo »)."),
    "miniature": ("Image de présentation", "info", "Ajouter une capture de l'interface (« Thumbnail image »)."),
    "description": ("Accroche et description", "moyenne", "Renseigner « Tagline » et « Description »."),
    "fonctionnalites": ("Fonctionnalités clés", "info", "Lister 4 à 6 « Key features »."),
    "cas_usage": ("Cas d'usage", "info", "Lister 4 à 6 « Use cases »."),
    "documentation": ("Lien vers la documentation", "info", "Renseigner « Documentation » : https://flugia.com/documentation/."),
    "demo": ("Lien de démo", "info", "Renseigner « Demo » : https://flugia.com/demo-flugia/."),
    "reseaux": ("LinkedIn et X", "info", "Renseigner « LinkedIn » et « Twitter »."),
    "createur": ("Créateur (« Created by »)", "info", "Renseigner « Created by » : FLUGIA (elavi srl)."),
}
AAD_SITE_ACTIONS = {
    "accueil.titre": "Mettre à jour la « Tagline ».",
    "accueil.description": "Aligner la « Description » sur celle du site.",
    "accueil.accroche": "Vérifier que la « Tagline » reflète toujours le positionnement.",
    "offre.co_workers": "Mettre à jour la description, les « Key features » et les « Use cases ».",
    "tarifs.prix": "Mettre à jour le prix cité dans la description (et le « Pricing model » si l'offre gratuite change).",
    "reseaux": "Mettre à jour les liens LinkedIn et Twitter.",
}

PH_PREP = {
    "verification": "Compte vérifié (e-mail professionnel, LinkedIn ou GitHub)",
    "profil": "Profil complété (photo, bio, liens)",
    "engagement": "Activité régulière sur Product Hunt (votes et commentaires sur d'autres produits)",
    "fiche": "Fiche produit préparée : nom, tagline (60 caractères max), description, logo 240×240",
    "galerie": "Galerie : 3 à 5 visuels 1270×760, et une courte vidéo si possible",
    "commentaire": "Premier commentaire du maker rédigé (histoire, problème résolu, offre)",
    "offre": "Offre spéciale Product Hunt prête, avec sa page d'atterrissage",
    "soutiens": "Liste des soutiens à prévenir le jour J (clients, réseau, équipe)",
    "disponibilite": "Équipe disponible toute la journée du lancement pour répondre aux commentaires",
}
PH_FIELDS = {
    "logo": ("Logo", "moyenne", "Vérifier le logo de la fiche produit (240×240)."),
    "tagline": ("Tagline", "moyenne", "Renseigner une tagline claire de 60 caractères maximum."),
    "description": ("Description", "moyenne", "Renseigner la description (Co-Workers, validation avant envoi, prix, essai gratuit)."),
    "galerie": ("Galerie d'images", "info", "Ajouter 3 à 5 visuels 1270×760 de l'interface."),
    "video": ("Vidéo", "info", "Ajouter une courte vidéo de démonstration."),
    "makers": ("Makers", "info", "Ajouter les membres de l'équipe comme makers."),
    "topics": ("Catégories (topics)", "info", "Choisir les topics : Artificial Intelligence, SaaS, Marketing, Sales, Productivity."),
    "site": ("Lien vers le site", "info", "Renseigner le lien https://flugia.com."),
    "badge": ("Badge Product Hunt sur flugia.com", "info", "Ajouter le badge « Featured on Product Hunt » dans le pied de page de flugia.com."),
}
PH_SITE_ACTIONS = {
    "accueil.titre": "Mettre à jour la tagline de la fiche produit.",
    "accueil.description": "Aligner la description de la fiche produit sur celle du site.",
    "accueil.accroche": "Vérifier que la tagline reflète toujours le positionnement.",
    "offre.co_workers": "Mettre à jour la description et la galerie avec les Co-Workers ajoutés ou retirés.",
    "tarifs.prix": "Mettre à jour le prix et l'offre cités sur la fiche produit.",
    "reseaux": "Mettre à jour les liens de la fiche et du profil maker.",
}

PLATFORMS = {
    "crunchbase": {"nom": "Crunchbase", "fields": PROFILE_FIELDS, "presse": True,
                   "revue": "description, effectif, logo, catégories, liens et actualités",
                   "pourquoi": "investisseurs et partenaires consultent ce profil",
                   "complet": "Une fiche complète est mieux classée sur Crunchbase et inspire confiance aux investisseurs et partenaires."},
    "alternativeto": {"nom": "AlternativeTo", "fields": AT_FIELDS, "presse": False,
                      "revue": "description, prix, fonctionnalités, captures, liens et concurrents liés",
                      "pourquoi": "les prospects y comparent FLUGIA à ses concurrents",
                      "complet": "Une fiche complète remonte dans les listes « alternatives à » et rassure les prospects qui comparent."},
    "aiagentsdirectory": {"nom": "AI Agents Directory", "fields": AAD_FIELDS, "presse": False,
                          "revue": "accroche, description, fonctionnalités, cas d'usage, prix, liens et images",
                          "pourquoi": "les entreprises qui cherchent un agent IA y comparent les solutions",
                          "complet": "Une fiche complète est mieux mise en avant dans les catégories et les recherches de l'annuaire."},
    "producthunt": {"nom": "Product Hunt", "fields": PH_FIELDS, "presse": False,
                    "revue": "tagline, description, galerie, offre, makers et liens",
                    "pourquoi": "la fiche reste consultée et référencée longtemps après le lancement",
                    "complet": "Une fiche complète convertit mieux les visiteurs qui arrivent de Product Hunt."},
}
SITE_ACTIONS = {"alternativeto": AT_SITE_ACTIONS, "aiagentsdirectory": AAD_SITE_ACTIONS, "producthunt": PH_SITE_ACTIONS}


def _cfg() -> dict:
    return {"revue_jours": 90, "changements_jours": 45, "ph_g2_avis_min": 5, "ph_compte_jours_min": 7,
            **(alerts_config().get("crunchbase") or {})}


# ------------------------------------------------------------------ relevé du site
def _text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_mod.unescape(t)).strip()


def parse_home(page: str) -> dict[str, str]:
    def first(pattern):
        m = re.search(pattern, page, re.S | re.I)
        return _text(m.group(1)) if m else ""

    paths = sorted({p for p in re.findall(r'href=["\'](?:https://flugia\.com)?(/ai-[a-z0-9-]+/)["\']', page)})
    socials = sorted({u.rstrip("/") for u in re.findall(r'href=["\'](https?://[^"\']+)["\']', page) if any(s in u for s in SOCIAL)})
    return {
        "accueil.titre": first(r"<title>(.*?)</title>"),
        "accueil.description": first(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)'),
        "accueil.accroche": first(r"<h1[^>]*>(.*?)</h1>"),
        "offre.co_workers": ", ".join(p.strip("/") for p in paths),
        "reseaux": ", ".join(socials),
        # badges « Featured on … » exigés par certains annuaires pour une fiche gratuite
        "annuaire.badges": ", ".join(sorted({d for d in BADGE_DOMAINS if d in page})) or "aucun",
    }


def parse_aad_listing(page: str) -> dict[str, str]:
    """Fiche publique AI Agents Directory : publiée ou non, nombre d'avis et note (données structurées de la page)."""
    if not page or "Page Not Found" in page[:5000] or "does not exist" in page[:20000]:
        return {"annuaire.aiagentsdirectory.statut": "non publiée", "annuaire.aiagentsdirectory.avis": "0"}
    out = {"annuaire.aiagentsdirectory.statut": "publiée"}
    data = html_mod.unescape(page)
    m = re.search(r'"reviewCount"\s*:\s*(\d+)', data)
    r = re.search(r'"ratingValue"\s*:\s*([\d.]+)', data)
    out["annuaire.aiagentsdirectory.avis"] = m.group(1) if m else "0"
    if r:
        out["annuaire.aiagentsdirectory.note"] = r.group(1)
    return out


def parse_pricing(page: str) -> dict[str, str]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    prices = sorted(set(re.sub(r"\s", "", p) for p in re.findall(r"\d[\d.,]*\s?€|€\s?\d[\d.,]*", _text(body))),
                    key=lambda p: float(re.sub(r"[^\d,]", "", p).replace(",", ".") or 0))
    return {"tarifs.prix": ", ".join(prices)}


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (FLUGIA-tracking/1.0)"}, timeout=30)
    return resp.text if resp.status_code < 500 else ""


def snapshot_site(fetch=None, aad_url: str | None = None) -> dict[str, str]:
    fetch = fetch or _get
    out = parse_home(fetch(SITE + PAGES["accueil"]))
    out.update(parse_pricing(fetch(SITE + PAGES["tarifs"])))
    if aad_url:
        try:
            out.update(parse_aad_listing(fetch(aad_url)))
        except Exception:  # noqa: BLE001  (annuaire injoignable : on garde le relevé précédent)
            pass
    return {k: v for k, v in out.items() if v}  # un champ vide = page modifiée ou erreur de lecture : on ne conclut rien


def watch_site(db, fetch=None, now: datetime | None = None) -> list[dict]:
    """Relève le site, enregistre les changements par rapport au relevé précédent. Premier passage : simple référence."""
    now = now or datetime.now(timezone.utc)
    current = snapshot_site(fetch, params(db).get("aiagentsdirectory_url"))
    previous = {r["champ"]: r["valeur"] for r in db.query("SELECT champ, valeur FROM veille_site")}
    changes = []
    for champ, valeur in current.items():
        before = previous.get(champ)
        if before is not None and before != valeur:
            changes.append({"champ": champ, "avant": before, "apres": valeur})
            db.execute("INSERT INTO veille_changements (detecte_le, champ, avant, apres) VALUES (?, ?, ?, ?)",
                       (now.isoformat(), champ, before, valeur))
        if before is None:
            db.execute("INSERT INTO veille_site (champ, valeur, vu_le) VALUES (?, ?, ?)", (champ, valeur, now.isoformat()))
        elif before != valeur:
            db.execute("UPDATE veille_site SET valeur = ?, vu_le = ? WHERE champ = ?", (valeur, now.isoformat(), champ))
    db.commit()
    return changes


# ------------------------------------------------------------------ paramètres et événements (interface)
def params(db) -> dict[str, str]:
    try:
        return {r["cle"]: r["valeur"] for r in db.query("SELECT cle, valeur FROM ui_parametres")}
    except Exception:
        return {}


def set_param(db, key: str, value: str | None) -> None:
    db.execute("DELETE FROM ui_parametres WHERE cle = ?", (key,))
    if value:
        db.execute("INSERT INTO ui_parametres (cle, valeur, updated_at) VALUES (?, ?, ?)",
                   (key, value, datetime.now(timezone.utc).isoformat()))
    db.commit()


def mark_field(db, field: str, present: bool = True, platform: str = "crunchbase") -> None:
    """Ajoute (ou retire) une information de la liste de ce qui figure sur la fiche."""
    done = set(filter(None, (params(db).get(f"{platform}_fiche") or "").split(",")))
    done = done | {field} if present else done - {field}
    set_param(db, f"{platform}_fiche", ",".join(sorted(done)) or ",")


def events(db, pending_only: bool = False) -> list[dict]:
    try:
        rows = db.query("SELECT id, cree_le, type, description, reporte_le FROM ui_evenements ORDER BY cree_le DESC")
    except Exception:
        return []
    return [r for r in rows if not (pending_only and r["reporte_le"])]


def add_event(db, kind: str, description: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    eid = hashlib.sha1(f"{now}{kind}{description}".encode()).hexdigest()[:10]
    db.execute("INSERT INTO ui_evenements (id, cree_le, type, description, reporte_le) VALUES (?, ?, ?, ?, NULL)",
               (eid, now, kind, description))
    db.commit()


def mark_event(db, eid: str, reported: bool = True) -> None:
    db.execute("UPDATE ui_evenements SET reporte_le = ? WHERE id = ?",
               (datetime.now(timezone.utc).isoformat() if reported else None, eid))
    db.commit()


def delete_event(db, eid: str) -> None:
    db.execute("DELETE FROM ui_evenements WHERE id = ?", (eid,))
    db.commit()


# ------------------------------------------------------------------ alertes
def _short(v: str, n: int = 140) -> str:
    v = v or "—"
    return v if len(v) <= n else v[: n - 1] + "…"


def _lower(lbl: str) -> str:
    return lbl[0].lower() + lbl[1:]


def alerts(db, snapshot: dict, as_of: date) -> list:
    out = []
    for platform in PLATFORMS:
        out.extend(platform_alerts(db, snapshot, as_of, platform))
    return out


def platform_alerts(db, snapshot: dict, as_of: date, platform: str) -> list:
    from .alerts import Alert
    from .brand import day as fday, num

    cfg = _cfg()
    conf = PLATFORMS[platform]
    nom = conf["nom"]
    p = params(db)
    url = p.get(f"{platform}_url")
    where = f" ({url})" if url else ""
    out = []
    if platform == "producthunt":  # avant le lancement : seules les alertes de préparation et de calendrier
        launch_alerts, launched = _ph_launch_alerts(p, as_of, cfg)
        out.extend(launch_alerts)
        if not launched:
            return out

    # 1. Revue périodique (seulement une fois la fiche déclarée, pour ne pas alerter sur un annuaire non utilisé)
    last = p.get(f"{platform}_maj")
    if url or platform == "crunchbase":
        age = (as_of - date.fromisoformat(last[:10])).days if last else None
        if age is None or age > cfg["revue_jours"]:
            out.append(Alert(
                key=f"{platform}_revue:{as_of:%Y-%m}", etape="Visibilité", severite="info",
                titre=f"{nom} : fiche à revoir" + (f" (dernière mise à jour il y a {num(age, 0)} jours)" if age is not None
                                                   else " (date de dernière mise à jour non renseignée)"),
                detail=f"Une revue complète est conseillée tous les {cfg['revue_jours']} jours : {conf['pourquoi']}.",
                action=f"Relire la fiche{where} : {conf['revue']}. Cocher « Fait » enregistre la date du jour comme dernière mise à jour.",
            ))
    if not url and platform != "crunchbase":
        return out

    # 2. Changements détectés sur flugia.com
    since = (datetime.combine(as_of, datetime.min.time()) - timedelta(days=cfg["changements_jours"])).isoformat()
    try:
        changes = db.query("SELECT detecte_le, champ, avant, apres FROM veille_changements WHERE detecte_le >= ? ORDER BY detecte_le",
                           (since,))
    except Exception:
        changes = []
    latest = {}
    for c in changes:  # un seul signalement par champ : le plus récent
        latest[c["champ"]] = c
    for champ, c in latest.items():
        if champ.startswith("annuaire."):  # relevés propres aux annuaires : traités plus bas
            continue
        lbl, sev, action = FIELDS.get(champ, (champ, "info", "Vérifier la fiche."))
        if platform in SITE_ACTIONS:
            action = SITE_ACTIONS[platform].get(champ, "Vérifier la fiche.")
        out.append(Alert(
            key=f"{platform}_site:{champ}:{hashlib.sha1((c['apres'] or '').encode()).hexdigest()[:8]}",
            etape="Visibilité", severite=sev,
            titre=f"{nom} : changement sur flugia.com ({_lower(lbl)})",
            detail=f"Changement détecté le {fday(str(c['detecte_le'])[:10])}. Avant : « {_short(c['avant'])} ». "
                   f"Maintenant : « {_short(c['apres'])} ».",
            action=action + (f" Fiche : {url}." if url else ""),
        ))

    # 3. Nouvelle mention sur un site tiers (Crunchbase : rubrique « News »)
    if conf["presse"]:
        rcfg = alerts_config().get("referents", {})
        for site in (snapshot.get("interet") or {}).get("sites_referents", []):
            if site.get("nouveau") and site.get("visites_7j", 0) >= rcfg.get("visites_min_nouveau", 5):
                out.append(Alert(
                    key=f"{platform}_presse:{site['site']}", etape="Visibilité", severite="info",
                    titre=f"{nom} : nouvelle mention à ajouter ({site['site']})",
                    detail=f"{site['site']} envoie des visiteurs depuis peu ({num(site['visites_7j'], 0)} visites en 7 jours) : "
                           "probablement un article ou une mention de FLUGIA.",
                    action="Retrouver l'article et l'ajouter dans la section « News » du profil s'il parle de FLUGIA.",
                ))

    # 4. Informations manquantes sur la fiche (liste cochée dans Paramétrages)
    present = p.get(f"{platform}_fiche")
    if present is None and url:
        out.append(Alert(
            key=f"{platform}_fiche:liste", etape="Visibilité", severite="info",
            titre=f"{nom} : indiquer ce qui figure déjà sur la fiche",
            detail="Sans cette liste, impossible de signaler les informations manquantes.",
            action=f"Dans Paramétrages > Veille des annuaires > {nom}, cocher les informations présentes sur la fiche et enregistrer.",
        ))
    elif present is not None:
        done = set(filter(None, present.split(",")))
        competitors = ", ".join(_competitors()) or "les principaux concurrents"
        for k, (lbl, sev, action) in conf["fields"].items():
            if k not in done:
                out.append(Alert(
                    key=f"{platform}_fiche:{k}", etape="Visibilité", severite=sev,
                    titre=(f"{nom} : fiche en attente de validation" if k == "publiee"
                           else f"{nom} : information à ajouter sur la fiche ({_lower(lbl)})"),
                    detail=conf["complet"],
                    action=action.format(concurrents=competitors) + where
                    + (" Cocher « Fait » une fois la fiche publiée." if k == "publiee" else " Cocher « Fait » une fois l'information ajoutée."),
                ))

    # 5. Événements saisis
    if platform == "crunchbase":
        for e in events(db, pending_only=True):
            out.append(Alert(
                key=f"crunchbase_evenement:{e['id']}", etape="Visibilité", severite="moyenne",
                titre=f"Crunchbase : à reporter — {e['type'].lower()}",
                detail=f"{e['description']} (saisi le {fday(str(e['cree_le'])[:10])}).",
                action=EVENT_ACTION.get(e["type"], "Mettre à jour la rubrique concernée du profil.") + where,
            ))
    if platform == "aiagentsdirectory":
        out.extend(_aad_alerts(db, as_of, url, latest))
    if platform != "crunchbase":  # seuls les événements qui changent la description du produit ; « Fait » les masque définitivement
        recent = (as_of - timedelta(days=180)).isoformat()
        for e in events(db):
            if e["type"] in AT_EVENT_ACTION and str(e["cree_le"])[:10] >= recent:
                out.append(Alert(
                    key=f"{platform}_evenement:{e['id']}", etape="Visibilité", severite="info",
                    titre=f"{nom} : à reporter — {e['type'].lower()}",
                    detail=f"{e['description']} (saisi le {fday(str(e['cree_le'])[:10])}).",
                    action=AT_EVENT_ACTION[e["type"]] + where,
                ))
    return out


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def ph_slots(after: date, count: int = 2) -> list[str]:
    """Prochains créneaux de lancement : mardi ou mercredi à 00:01 heure du Pacifique, exprimés à l'heure de Bruxelles."""
    from datetime import time

    from .brand import day as fday
    out, d = [], after + timedelta(days=1)
    while len(out) < count:
        if d.weekday() in (1, 2):
            try:
                from zoneinfo import ZoneInfo
                local = datetime.combine(d, time(0, 1), ZoneInfo("America/Los_Angeles")).astimezone(ZoneInfo("Europe/Brussels"))
                hour = local.strftime("%H:%M")
            except Exception:  # noqa: BLE001  (base des fuseaux absente : décalage d'hiver)
                hour = "09:01"
            out.append(f"{JOURS[d.weekday()]} {fday(d)} à {hour}")
        d += timedelta(days=1)
    return out


def ph_readiness(p: dict, as_of: date, cfg: dict | None = None) -> list[str]:
    """Conditions de lancement Product Hunt encore manquantes (liste vide = prêt)."""
    cfg = cfg or _cfg()
    missing = []
    g2 = int(float(p.get("producthunt_g2") or 0))
    if g2 < cfg["ph_g2_avis_min"]:
        missing.append(f"G2 : {g2} avis sur {cfg['ph_g2_avis_min']} minimum")
    created = p.get("producthunt_compte")
    age = (as_of - date.fromisoformat(created[:10])).days if created else None
    if age is None:
        missing.append("date de création du compte Product Hunt non renseignée")
    elif age < cfg["ph_compte_jours_min"]:
        missing.append(f"compte Product Hunt : {age} jour(s) d'activité sur {cfg['ph_compte_jours_min']} minimum")
    done = set(filter(None, (p.get("producthunt_prepa") or "").split(",")))
    missing += [lbl[0].lower() + lbl[1:] for k, lbl in PH_PREP.items() if k not in done]
    return missing


def _ph_launch_alerts(p: dict, as_of: date, cfg: dict) -> tuple[list, bool]:
    from .alerts import Alert
    from .brand import day as fday

    rule = "Le bon moment : quand G2 a quelques avis et que le compte a quelques jours d'activité. " \
           "Lancer un mardi ou un mercredi à 00:01, heure du Pacifique."
    planned = date.fromisoformat(p["producthunt_lancement"][:10]) if p.get("producthunt_lancement") else None
    if planned and planned < as_of:  # lancement passé : suivi après lancement, puis veille normale de la fiche
        out = []
        if (as_of - planned).days <= 3:
            out.append(Alert(
                key=f"producthunt_lancement:suite:{planned}", etape="Visibilité", severite="info",
                titre="Product Hunt : suites du lancement",
                detail=f"Lancement du {fday(planned, True)}.",
                action="Remercier les soutiens, répondre aux derniers commentaires, ajouter le badge Product Hunt sur flugia.com "
                       "et enregistrer l'adresse de la fiche produit dans Paramétrages.",
            ))
        return out, True

    missing = ph_readiness(p, as_of, cfg)
    slots = ph_slots(as_of)
    out = []
    if not planned:
        if missing:
            out.append(Alert(
                key=f"producthunt_lancement:attente:{as_of:%Y-%m}", etape="Visibilité", severite="info",
                titre=f"Product Hunt : lancement pas encore recommandé ({len(missing)} point(s) à réunir)",
                detail="À réunir : " + " ; ".join(missing) + ". " + rule,
                action="Demander des avis G2 aux clients actuels, utiliser le compte chaque jour (votes, commentaires) "
                       "et cocher la préparation dans Paramétrages > Veille des annuaires > Product Hunt.",
            ))
        else:
            out.append(Alert(
                key="producthunt_lancement:pret", etape="Visibilité", severite="moyenne",
                titre="Product Hunt : conditions réunies, le lancement peut être planifié",
                detail=f"Prochains créneaux : {' ou '.join(slots)} (heure de Bruxelles), soit 00:01 heure du Pacifique.",
                action="Programmer le lancement sur Product Hunt pour l'un de ces créneaux, puis saisir la date dans Paramétrages.",
            ))
        return out, False

    days_to = (planned - as_of).days
    when = f"{JOURS[planned.weekday()]} {fday(planned, True)}"
    if planned.weekday() not in (1, 2):
        out.append(Alert(
            key=f"producthunt_lancement:jour:{planned}", etape="Visibilité", severite="moyenne",
            titre=f"Product Hunt : lancement prévu un {JOURS[planned.weekday()]}",
            detail=f"Date prévue : {when}. {rule}",
            action=f"Déplacer le lancement sur l'un des prochains créneaux : {' ou '.join(slots)} (heure de Bruxelles).",
        ))
    if missing:
        out.append(Alert(
            key=f"producthunt_lancement:manquant:{planned}", etape="Visibilité", severite="haute" if days_to <= 7 else "moyenne",
            titre=f"Product Hunt : lancement du {fday(planned)} prévu, {len(missing)} point(s) pas encore prêts",
            detail="À réunir : " + " ; ".join(missing) + ".",
            action="Compléter ces points avant le lancement, ou le repousser à un mardi ou mercredi ultérieur.",
        ))
    if days_to <= 3:
        steps = {
            0: "Publier le premier commentaire du maker dès la mise en ligne (09:01), prévenir les soutiens, partager sur LinkedIn "
               "et répondre à chaque commentaire toute la journée.",
            1: "Vérifier la fiche programmée (tagline, galerie, offre), préparer les messages aux soutiens et bloquer l'agenda de demain.",
        }
        out.append(Alert(
            key=f"producthunt_lancement:J-{days_to}:{planned}", etape="Visibilité", severite="haute",
            titre="Product Hunt : lancement " + ("aujourd'hui" if days_to == 0 else f"dans {days_to} jour(s)")
                  + " à 09:01 (00:01 heure du Pacifique)",
            detail=f"Lancement prévu le {when}.",
            action=steps.get(days_to, "Relire la fiche, finaliser la galerie et l'offre, confirmer la liste des soutiens."),
        ))
    return out, False


def _aad_alerts(db, as_of: date, url: str, latest_changes: dict) -> list:
    """AI Agents Directory : badge exigé sur le site pour la fiche gratuite, publication, nouveaux avis (relevés automatiques)."""
    from .alerts import Alert
    from .brand import day as fday

    try:
        current = {r["champ"]: r["valeur"] for r in db.query("SELECT champ, valeur FROM veille_site WHERE champ LIKE 'annuaire.%'")}
    except Exception:
        current = {}
    out = []
    badges = current.get("annuaire.badges")
    status = current.get("annuaire.aiagentsdirectory.statut")
    badge = f'<a href="{url}" target="_blank" rel="noopener"><img src="https://aiagentsdirectory.com/featured-badge.svg?v=2024" ' \
            'alt="FLUGIA - Featured on AI Agents Directory" width="200" height="50" /></a>'
    if badges is not None and "aiagentsdirectory.com" not in badges:
        published = status == "publiée"
        out.append(Alert(
            key=f"aiagentsdirectory_badge:{'publiee' if published else 'attente'}",
            etape="Visibilité", severite="moyenne" if published else "haute",
            titre=("AI Agents Directory : le badge a disparu de flugia.com" if published
                   else "AI Agents Directory : badge à ajouter sur flugia.com pour valider la fiche gratuite"),
            detail=("La fiche gratuite exige ce badge sur le site : sans lui, elle peut être retirée." if published
                    else "La fiche gratuite n'est validée que si le badge apparaît sur la page d'accueil ou le pied de page "
                         "dans les 72 heures suivant la soumission. Il n'a pas été trouvé lors du dernier relevé de flugia.com."),
            action=f"Ajouter ce code dans le pied de page de flugia.com : {badge}",
        ))
    if status == "non publiée":
        out.append(Alert(
            key="aiagentsdirectory_statut:attente", etape="Visibilité", severite="info",
            titre="AI Agents Directory : fiche en attente de validation",
            detail="La page publique de la fiche n'est pas encore en ligne. L'alerte disparaît d'elle-même à la publication.",
            action="Vérifier le statut dans « My Dashboard » de l'annuaire" + (" et que le badge est bien en place." if badges and
                                                                              "aiagentsdirectory.com" not in badges else "."),
        ))
    c = latest_changes.get("annuaire.aiagentsdirectory.avis")
    if c and (c["apres"] or "0").isdigit() and int(c["apres"] or 0) > int(c["avant"] or 0 if (c["avant"] or "0").isdigit() else 0):
        out.append(Alert(
            key=f"aiagentsdirectory_avis:{c['apres']}", etape="Visibilité", severite="moyenne",
            titre=f"AI Agents Directory : nouvel avis sur la fiche ({c['avant']} → {c['apres']})",
            detail=f"Détecté le {fday(str(c['detecte_le'])[:10])}" + (f" ; note moyenne : {current['annuaire.aiagentsdirectory.note']}/5."
                                                                       if current.get("annuaire.aiagentsdirectory.note") else "."),
            action=f"Lire l'avis et y répondre depuis la fiche{f' ({url})' if url else ''}.",
        ))
    return out


def _competitors() -> list[str]:
    try:
        import yaml

        from .config import ROOT
        return list(yaml.safe_load((ROOT / "config" / "geo_prompts.yaml").read_text(encoding="utf-8")).get("concurrents") or [])
    except Exception:
        return []
