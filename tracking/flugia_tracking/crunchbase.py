"""Veille Crunchbase (sans API) : signale quand le profil Crunchbase de FLUGIA doit être mis à jour.

Crunchbase ne permet pas de modifier un profil par programme : les alertes indiquent quoi corriger,
la mise à jour se fait à la main (crunchbase.com, « Edit profile »).

Quatre déclencheurs :
  1. revue périodique : la date de dernière mise à jour (onglet Paramétrages) dépasse N jours ;
  2. changement sur flugia.com : titre, description, accroche, gamme de Co-Workers, tarifs, réseaux sociaux
     (relevés chaque nuit, comparés au relevé précédent) ;
  3. nouvelle mention dans la presse ou sur un site tiers (nouveau site référent GA4) ;
  4. événement saisi dans Paramétrages (levée de fonds, nouvelle offre, effectif…) pas encore reporté.
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
SOCIAL = ("linkedin.com", "instagram.com", "x.com", "twitter.com", "facebook.com", "youtube.com", "tiktok.com")

FIELDS = {
    "accueil.titre": ("Titre du site", "moyenne", "Mettre à jour le « Short description » du profil."),
    "accueil.description": ("Description du site", "moyenne", "Aligner la description complète (« Description ») sur celle du site."),
    "accueil.accroche": ("Accroche de la page d'accueil", "info", "Vérifier que le « Short description » reflète toujours le positionnement."),
    "offre.co_workers": ("Gamme de Co-Workers", "moyenne", "Mettre à jour la description et les catégories (« Industries ») avec les Co-Workers ajoutés ou retirés."),
    "tarifs.prix": ("Tarifs", "info", "Vérifier les informations d'offre et de prix mentionnées dans la description."),
    "reseaux": ("Réseaux sociaux", "moyenne", "Mettre à jour les liens LinkedIn, Instagram et X du profil."),
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


def _cfg() -> dict:
    return {"revue_jours": 90, "changements_jours": 45, **(alerts_config().get("crunchbase") or {})}


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
    }


def parse_pricing(page: str) -> dict[str, str]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    prices = sorted(set(re.sub(r"\s", "", p) for p in re.findall(r"\d[\d.,]*\s?€|€\s?\d[\d.,]*", _text(body))),
                    key=lambda p: float(re.sub(r"[^\d,]", "", p).replace(",", ".") or 0))
    return {"tarifs.prix": ", ".join(prices)}


def snapshot_site(fetch=None) -> dict[str, str]:
    fetch = fetch or (lambda url: requests.get(url, headers={"User-Agent": "FLUGIA-tracking/1.0"}, timeout=30).text)
    out = parse_home(fetch(SITE + PAGES["accueil"]))
    out.update(parse_pricing(fetch(SITE + PAGES["tarifs"])))
    return {k: v for k, v in out.items() if v}  # un champ vide = page modifiée ou erreur de lecture : on ne conclut rien


def watch_site(db, fetch=None, now: datetime | None = None) -> list[dict]:
    """Relève le site, enregistre les changements par rapport au relevé précédent. Premier passage : simple référence."""
    now = now or datetime.now(timezone.utc)
    current = snapshot_site(fetch)
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


def mark_field(db, field: str, present: bool = True) -> None:
    """Ajoute (ou retire) une information de la liste de ce qui figure sur la fiche."""
    done = set(filter(None, (params(db).get("crunchbase_fiche") or "").split(",")))
    done = done | {field} if present else done - {field}
    set_param(db, "crunchbase_fiche", ",".join(sorted(done)) or ",")


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


def alerts(db, snapshot: dict, as_of: date) -> list:
    from .alerts import Alert
    from .brand import day as fday, num

    cfg = _cfg()
    p = params(db)
    where = f" ({p['crunchbase_url']})" if p.get("crunchbase_url") else ""
    out = []

    # 1. Revue périodique
    last = p.get("crunchbase_maj")
    age = (as_of - date.fromisoformat(last[:10])).days if last else None
    if age is None or age > cfg["revue_jours"]:
        out.append(Alert(
            key=f"crunchbase_revue:{as_of:%Y-%m}", etape="Visibilité", severite="info",
            titre="Crunchbase : profil à revoir" + (f" (dernière mise à jour il y a {num(age, 0)} jours)" if age is not None
                                                   else " (date de dernière mise à jour non renseignée)"),
            detail=f"Une revue complète est conseillée tous les {cfg['revue_jours']} jours : investisseurs et partenaires consultent ce profil.",
            action=f"Relire le profil{where} : description, effectif, logo, catégories, liens et actualités. "
                   "Cocher « Fait » enregistre la date du jour comme dernière mise à jour.",
        ))

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
        lbl, sev, action = FIELDS.get(champ, (champ, "info", "Vérifier le profil Crunchbase."))
        out.append(Alert(
            key=f"crunchbase_site:{champ}:{hashlib.sha1((c['apres'] or '').encode()).hexdigest()[:8]}",
            etape="Visibilité", severite=sev,
            titre=f"Crunchbase : changement sur flugia.com ({lbl[0].lower() + lbl[1:]})",
            detail=f"Changement détecté le {fday(str(c['detecte_le'])[:10])}. Avant : « {_short(c['avant'])} ». "
                   f"Maintenant : « {_short(c['apres'])} ».",
            action=action + (f" Profil : {p['crunchbase_url']}." if p.get("crunchbase_url") else ""),
        ))

    # 3. Nouvelle mention sur un site tiers
    rcfg = alerts_config().get("referents", {})
    for site in (snapshot.get("interet") or {}).get("sites_referents", []):
        if site.get("nouveau") and site.get("visites_7j", 0) >= rcfg.get("visites_min_nouveau", 5):
            out.append(Alert(
                key=f"crunchbase_presse:{site['site']}", etape="Visibilité", severite="info",
                titre=f"Crunchbase : nouvelle mention à ajouter ({site['site']})",
                detail=f"{site['site']} envoie des visiteurs depuis peu ({num(site['visites_7j'], 0)} visites en 7 jours) : "
                       "probablement un article ou une mention de FLUGIA.",
                action="Retrouver l'article et l'ajouter dans la section « News » du profil s'il parle de FLUGIA.",
            ))

    # 4 bis. Informations manquantes sur la fiche (liste cochée dans Paramétrages)
    present = p.get("crunchbase_fiche")
    if present is None and p.get("crunchbase_url"):
        out.append(Alert(
            key="crunchbase_fiche:liste", etape="Visibilité", severite="info",
            titre="Crunchbase : indiquer ce qui figure déjà sur la fiche",
            detail="Sans cette liste, impossible de signaler les informations manquantes (logo, réseaux sociaux, actualités…).",
            action="Dans Paramétrages > Veille Crunchbase, cocher les informations présentes sur la fiche et enregistrer.",
        ))
    elif present is not None:
        done = set(filter(None, present.split(",")))
        for k, (lbl, sev, action) in PROFILE_FIELDS.items():
            if k not in done:
                out.append(Alert(
                    key=f"crunchbase_fiche:{k}", etape="Visibilité", severite=sev,
                    titre=f"Crunchbase : information à ajouter sur la fiche ({lbl[0].lower() + lbl[1:]})",
                    detail="Une fiche complète est mieux classée sur Crunchbase et inspire confiance aux investisseurs et partenaires.",
                    action=action + where + " Cocher « Fait » une fois l'information ajoutée.",
                ))

    # 4. Événements saisis, pas encore reportés
    for e in events(db, pending_only=True):
        out.append(Alert(
            key=f"crunchbase_evenement:{e['id']}", etape="Visibilité", severite="moyenne",
            titre=f"Crunchbase : à reporter — {e['type'].lower()}",
            detail=f"{e['description']} (saisi le {fday(str(e['cree_le'])[:10])}).",
            action=EVENT_ACTION.get(e["type"], "Mettre à jour la rubrique concernée du profil.") + where,
        ))
    return out
