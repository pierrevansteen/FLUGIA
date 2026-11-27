"""Moteur d'alertes : applique config/alerts.yaml aux données et renvoie des alertes actionnables."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .config import alerts_config
from .db import DB
from .kpis import Data, label

SEVERITY_ORDER = {"haute": 0, "moyenne": 1, "info": 2}


@dataclass
class Alert:
    key: str          # identifiant stable (pour ne pas renvoyer la même alerte chaque nuit)
    etape: str        # Visibilité / Intérêt / Acquisition / Rétention / Système
    severite: str     # haute / moyenne / info
    titre: str
    detail: str
    action: str


def _avg(series, start, end):
    days = (end - start).days + 1
    return sum(v for d, v in series.items() if start <= d <= end) / days


def _fmt(v):
    return f"{v:,.0f}".replace(",", " ") if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")


def evaluate(snapshot: dict) -> list[Alert]:
    data: Data = snapshot["_data"]
    cfg = alerts_config()
    g = cfg["general"]
    as_of = data.as_of
    c0 = as_of - timedelta(days=g["fenetre_jours"] - 1)
    r1 = c0 - timedelta(days=1)
    r0 = r1 - timedelta(days=g["reference_jours"] - 1)
    alerts: list[Alert] = []

    # 1. Variations
    for rule in cfg.get("variations", []):
        if "ratio" in rule:
            num, den, mult = rule["ratio"]
            chans = sorted(set(data.channels(num)) & set(data.channels(den))) if rule.get("par_canal") else [None]
            pairs = [(ch, data.series(num, ch), data.series(den, ch), mult) for ch in chans]
        else:
            m = rule["metrique"]
            if m == "impressions":
                chans = sorted({ch for _d, _s, ch, mm, _v in data.rows if mm == "impressions"}) if rule.get("par_canal") else [None]
                pairs = [(ch, data.visibility(ch), None, None) for ch in chans]
            else:
                chans = data.channels(m) if rule.get("par_canal") else [None]
                pairs = [(ch, data.series(m, ch), None, None) for ch in chans]

        for ch, s1, s2, mult in pairs:
            if s2 is None:
                ref_total = sum(v for d, v in s1.items() if r0 <= d <= r1)
                if ref_total < g["volume_minimum"]:
                    continue
                cur, ref = _avg(s1, c0, as_of), _avg(s1, r0, r1)
            else:
                ref_den = sum(v for d, v in s2.items() if r0 <= d <= r1)
                cur_den = sum(v for d, v in s2.items() if c0 <= d <= as_of)
                if ref_den < g["volume_minimum"] or not cur_den:
                    continue
                cur = mult * sum(v for d, v in s1.items() if c0 <= d <= as_of) / cur_den
                ref = mult * sum(v for d, v in s1.items() if r0 <= d <= r1) / ref_den
            if not ref:
                continue
            var = 100 * (cur - ref) / ref
            threshold = rule["variation"]
            if (threshold < 0 and var <= threshold) or (threshold > 0 and var >= threshold):
                what = rule.get("metrique") or f"{rule['ratio'][0]}/{rule['ratio'][1]}"
                where = f" — {label(ch)}" if ch else ""
                alerts.append(Alert(
                    key=f"{rule['id']}:{ch or 'total'}",
                    etape=rule["etape"],
                    severite=rule["severite"],
                    titre=f"{_titre(rule['id'])}{where} : {var:+.0f} %",
                    detail=(f"{what} : {_fmt(cur)} par jour sur {g['fenetre_jours']} j contre {_fmt(ref)} sur les {g['reference_jours']} j précédents."
                            if s2 is None else
                            f"{_ratio_label(rule['id'])} : {_fmt(cur)} sur {g['fenetre_jours']} j contre {_fmt(ref)} sur les {g['reference_jours']} j précédents."),
                    action=rule["action"],
                ))

    # 2. Objectifs hebdomadaires
    w0 = as_of - timedelta(days=6)
    for metric, target in (cfg.get("objectifs_semaine") or {}).items():
        if metric == "trial_conversions" and not data.has_accounts:
            continue
        s = data.series(metric)
        if not s:
            continue
        got = sum(v for d, v in s.items() if w0 <= d <= as_of)
        if got < target:
            alerts.append(Alert(
                key=f"objectif:{metric}:{as_of.isocalendar()[1]}",
                etape="Intérêt" if metric != "trial_conversions" else "Acquisition",
                severite="moyenne",
                titre=f"Objectif hebdo non atteint : {_titre(metric)} {got:.0f} / {target}",
                detail=f"Sur les 7 derniers jours ({w0} → {as_of}).",
                action="Identifier le canal qui a décroché (tableau de bord) et relancer les prospects en cours.",
            ))

    # 2 bis. Nouveaux sites référents (article de presse, mention, partenaire...)
    rcfg = cfg.get("referents", {})
    for site in snapshot["interet"].get("sites_referents", []):
        if site["nouveau"] and site["visites_7j"] >= rcfg.get("visites_min_nouveau", 5):
            alerts.append(Alert(
                key=f"nouveau_referent:{site['site']}", etape="Visibilité", severite="info",
                titre=f"Nouveau site qui parle de Flugia : {site['site']} ({site['visites_7j']:.0f} visites en 7 j)",
                detail=f"Aucune visite venant de ce site les {rcfg.get('jours_historique', 60)} jours précédents. "
                       f"Inscriptions venant de ce site (30 j) : {site['inscriptions_30j']:.0f}.",
                action="Retrouver l'article ou la mention, le relayer sur LinkedIn et remercier l'auteur.",
            ))

    # 3. GEO
    geo = snapshot["visibilite"]["geo_taux_citation"]
    gcfg = cfg.get("geo", {})
    if geo["cur"] is not None:
        if geo["cur"] < gcfg.get("taux_minimum", 0):
            alerts.append(Alert(
                key=f"geo_taux_bas:{geo['date']}", etape="Visibilité", severite="moyenne",
                titre=f"GEO : Flugia citée dans seulement {geo['cur']:.0f} % des réponses IA",
                detail="Questions non citées : " + ", ".join(
                    q["question"] for q in snapshot["visibilite"]["geo_par_question"] if "question" in q and not q["citee"]),
                action="Publier des pages qui répondent précisément à ces questions (comparatifs, cas clients, FAQ) et obtenir des mentions sur des sites tiers.",
            ))
        if geo["prev"] is not None and geo["prev"] - geo["cur"] >= gcfg.get("baisse_points", 999):
            alerts.append(Alert(
                key=f"geo_baisse:{geo['date']}", etape="Visibilité", severite="haute",
                titre=f"GEO : taux de citation en chute ({geo['prev']:.0f} % → {geo['cur']:.0f} %)",
                detail="Comparaison avec la mesure précédente.",
                action="Vérifier quelles questions ne citent plus Flugia et quels concurrents ont pris la place.",
            ))

    # 4. Conversion des essais
    acq = snapshot["acquisition"]
    ccfg = cfg.get("conversion_essai", {})
    if acq.get("branche") and acq.get("essais_termines_28j", 0) >= ccfg.get("essais_minimum", 5):
        rate = acq["taux_conversion"]["cur"]
        if rate is not None and rate < ccfg.get("taux_minimum", 0):
            alerts.append(Alert(
                key=f"conversion_basse:{as_of.isocalendar()[1]}", etape="Acquisition", severite="haute",
                titre=f"Conversion essai → payant à {rate:.0f} % (objectif {ccfg['taux_minimum']} %)",
                detail=f"{acq['convertis_28j']} clients sur {acq['essais_termines_28j']} essais terminés en 28 jours.",
                action="Contacter les essais en cours avant J10, vérifier l'onboarding et l'activation.",
            ))

    # 5. Listes par compte
    lists = snapshot.get("listes") or {}
    for item in lists.get("essais_chauds", []):
        alerts.append(Alert(
            key=f"essai_chaud:{item['compte']}", etape="Acquisition", severite="haute",
            titre=f"Essai chaud à convertir : {item['compte']}",
            detail=f"Jour {item['jour_essai']} de l'essai, {item['usage_7j']:.0f} crédits utilisés en 7 jours (canal : {item['canal']}).",
            action="Appeler aujourd'hui pour proposer l'offre adaptée avant la fin de l'essai.",
        ))
    for item in lists.get("essais_bloques", []):
        alerts.append(Alert(
            key=f"essai_bloque:{item['compte']}", etape="Acquisition", severite="moyenne",
            titre=f"Essai bloqué : {item['compte']} (jour {item['jour_essai']}, rien activé)",
            detail=f"Inscrit le {item['inscrit_le']}, aucun outil connecté ni Co-Worker lancé.",
            action="Proposer une session d'onboarding de 15 minutes.",
        ))
    for item in lists.get("clients_a_risque", []):
        alerts.append(Alert(
            key=f"risque:{item['compte']}", etape="Rétention", severite="haute",
            titre=f"Risque de churn : {item['compte']} (usage −{item['baisse_pct']} %)",
            detail=f"{item['usage_30j']:.0f} crédits sur 30 j contre {item['usage_30j_precedents']:.0f} avant. MRR : {item.get('mrr') or '?'} €.",
            action="Appel du responsable du compte : comprendre le blocage, proposer un nouveau cas d'usage.",
        ))
    if cfg["comptes"]["renouvellement"].get("alerte", False):
        for item in lists.get("renouvellements", []):
            alerts.append(Alert(
                key=f"renouvellement:{item['compte']}:{item['echeance']}", etape="Rétention", severite="info",
                titre=f"Renouvellement le {item['echeance']} : {item['compte']}",
                detail=f"MRR : {item.get('mrr') or '?'} €.",
                action="Préparer le point de renouvellement (bilan d'usage, montée en gamme possible).",
            ))
    if snapshot["retention"].get("paiements_echoues_7j"):
        alerts.append(Alert(
            key=f"paiements:{as_of}", etape="Rétention", severite="haute",
            titre=f"{snapshot['retention']['paiements_echoues_7j']:.0f} paiement(s) échoué(s) cette semaine",
            detail="Voir l'outil de facturation.", action="Relancer les clients concernés pour mettre à jour leur carte.",
        ))

    # 6. Santé des sources
    for src in snapshot["sources"]:
        if src["configuree"] and src["dernier_statut"] == "erreur":
            alerts.append(Alert(
                key=f"source:{src['id']}:{(src['derniere_collecte'] or '')[:10]}", etape="Système", severite="haute",
                titre=f"Collecte en échec : {src['nom']}",
                detail=src["message"] or "", action="Vérifier le jeton d'accès de cette source (souvent expiré).",
            ))

    alerts.sort(key=lambda a: (SEVERITY_ORDER.get(a.severite, 9), a.etape))
    return alerts


def _ratio_label(rule_id: str) -> str:
    return {"cpm_hausse": "Coût pour 1000 impressions (€)", "taux_inscription_baisse": "Inscriptions pour 100 visites"}.get(rule_id, "Ratio")


def _titre(key: str) -> str:
    return {
        "visibilite_baisse": "Visibilité en baisse",
        "cpm_hausse": "Coût pour 1000 impressions en hausse",
        "recherches_marque_baisse": "Recherches « flugia » en baisse",
        "trafic_ia_baisse": "Trafic venant des IA en baisse",
        "sessions_baisse": "Visites en baisse",
        "inscriptions_baisse": "Inscriptions en baisse",
        "taux_inscription_baisse": "Taux d'inscription du site en baisse",
        "demos_baisse": "Démos réservées en baisse",
        "churn_hausse": "Résiliations en hausse",
        "demos_booked": "démos réservées",
        "signups": "inscriptions",
        "trial_conversions": "essais convertis",
    }.get(key, key)


def only_new(db: DB, alerts: list[Alert], now: datetime | None = None) -> list[Alert]:
    """Filtre les alertes déjà envoyées récemment et mémorise les nouvelles."""
    now = now or datetime.now(timezone.utc)
    repeat = timedelta(days=alerts_config()["general"]["repetition_jours"])
    fresh = []
    for a in alerts:
        rows = db.query("SELECT last_sent_at FROM alerts_log WHERE alert_key = ?", (a.key,))
        if rows and now - datetime.fromisoformat(rows[0]["last_sent_at"]) < repeat:
            continue
        fresh.append(a)
        if rows:
            db.execute("UPDATE alerts_log SET last_sent_at = ?, payload = ? WHERE alert_key = ?",
                       (now.isoformat(), json.dumps(asdict(a), ensure_ascii=False), a.key))
        else:
            db.execute("INSERT INTO alerts_log (alert_key, first_sent_at, last_sent_at, payload) VALUES (?, ?, ?, ?)",
                       (a.key, now.isoformat(), now.isoformat(), json.dumps(asdict(a), ensure_ascii=False)))
    db.commit()
    return fresh
