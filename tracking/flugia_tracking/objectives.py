"""Objectifs commerciaux (onglet « Paramétrages » de l'interface) et étapes du tunnel.

Les objectifs sont saisis par mois dans l'interface et enregistrés dans la table ui_objectifs.
La collecte de nuit les relit pour calculer les alertes « objectif non atteint » (rapport PDF, Slack).
Sans objectif enregistré, on retombe sur config/alerts.yaml (objectifs_semaine).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import alerts_config

STAGES = ["Visibilité", "Intérêt", "Conversion", "Rétention"]
STAGE_SUB = {"Visibilité": "Vues", "Intérêt": "Visites du site", "Conversion": "Inscriptions", "Rétention": "Paiements"}

# cle -> (libellé, unité, étape, métrique suivie, type)
#   type "somme" : volume cumulé sur la période (proratisé selon la durée)
#   type "taux"  : pourcentage minimum à tenir
#   type "plafond" : volume à ne pas dépasser (proratisé)
OBJECTIVES = {
    "impressions": ("Vues de la marque", "vues / mois", "Visibilité", "impressions", "somme"),
    "geo_taux": ("Citation par les IA", "% des questions, minimum", "Visibilité", "ai_mention_rate", "taux"),
    "sessions": ("Visites du site", "visites / mois", "Intérêt", "sessions", "somme"),
    "signups": ("Inscriptions", "inscriptions / mois", "Conversion", "signups", "somme"),
    "demos_booked": ("Démos réservées", "démos / mois", "Conversion", "demos_booked", "somme"),
    "taux_inscription": ("Taux visite → inscription", "% minimum", "Conversion", None, "taux"),
    "trial_conversions": ("Nouveaux clients payants", "clients / mois", "Rétention", "trial_conversions", "somme"),
    "budget_pub": ("Budget publicitaire", "€ / mois, maximum", "Visibilité", "spend", "plafond"),
}

DAYS_PER_MONTH = 365 / 12


def defaults() -> dict[str, float]:
    """Valeurs de départ tirées de config/alerts.yaml (objectifs hebdomadaires convertis en mensuels)."""
    cfg = alerts_config()
    out: dict[str, float] = {}
    for k, v in (cfg.get("objectifs_semaine") or {}).items():
        if k in OBJECTIVES and v:
            out[k] = round(v * DAYS_PER_MONTH / 7)
    if cfg.get("geo", {}).get("taux_minimum"):
        out["geo_taux"] = float(cfg["geo"]["taux_minimum"])
    return out


def load(db) -> dict[str, float]:
    """Objectifs enregistrés ; valeurs par défaut si la table est absente ou vide."""
    try:
        rows = db.query("SELECT cle, valeur FROM ui_objectifs")
    except Exception:
        rows = []
    saved = {r["cle"]: float(r["valeur"]) for r in rows if r.get("valeur") is not None and r["cle"] in OBJECTIVES}
    return saved if rows else defaults()


def save(db, values: dict[str, float | None]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("DELETE FROM ui_objectifs")
    for k, v in values.items():
        if v is not None and k in OBJECTIVES:
            db.execute("INSERT INTO ui_objectifs (cle, valeur, updated_at) VALUES (?, ?, ?)", (k, float(v), now))
    db.commit()


def prorata(monthly: float, days: int) -> float:
    return monthly * days / DAYS_PER_MONTH


def stage_of(alert: dict) -> str:
    """Étape du tunnel (Visibilité, Intérêt, Conversion, Rétention) ou « Système »."""
    key, etape = alert.get("key", ""), alert.get("etape", "")
    if etape == "Système" or key.startswith("source:"):
        return "Système"
    conversion = ("inscriptions_baisse", "taux_inscription_baisse", "demos_baisse", "objectif:signups", "objectif:demos_booked",
                  "objectif:taux_inscription")
    if key.startswith(conversion):
        return "Conversion"
    if etape == "Acquisition" or key.startswith(("objectif:trial_conversions", "conversion_basse", "essai_", "risque:", "churn", "paiements")):
        return "Rétention"
    if etape in STAGES:
        return etape
    return "Intérêt"
