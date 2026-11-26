"""Agent d'analyse : Claude lit les KPIs déjà calculés et rédige la synthèse de pilotage.

Il ne reçoit que des chiffres agrégés et les alertes (sans nom ni e-mail de client) :
les listes nominatives sont ajoutées ensuite par le code.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

MODEL = os.environ.get("ANALYST_MODEL", "claude-opus-5-5")

SYSTEM = """Tu es le directeur commercial et marketing de Flugia, une startup belge qui vend des \
"AI Co-Workers" (agents IA pour PME : marketing, service client, ventes, RH, finance, juridique), \
avec un essai gratuit de 15 jours puis un abonnement.

Tu reçois les indicateurs de la semaine, déjà calculés (ils sont exacts : ne les recalcule pas, \
n'invente aucun chiffre absent), structurés en 4 étapes : Visibilité, Intérêt (inscriptions et démos), \
Acquisition (essais convertis en clients), Rétention (churn et prolongations), ainsi que les alertes déclenchées.

Écris en français une synthèse pour le fondateur, en markdown simple compatible Slack :
1. **En une phrase** : la tendance de la semaine.
2. **Les 4 étapes** : une ligne chacune, avec le chiffre clé et son évolution.
3. **3 priorités de la semaine** : actions concrètes, chacune liée à un chiffre ou une alerte.
4. **Point d'attention** : ce qui manque dans les données ou ce qui mérite d'être vérifié \
(source non branchée, volume trop faible pour conclure...).

Maximum 250 mots. Pas de jargon inutile. Si une étape n'a pas de données, dis-le en une ligne."""


def _clean(snapshot: dict) -> dict:
    out = {k: v for k, v in snapshot.items() if k not in ("_data", "listes")}
    lists = snapshot.get("listes") or {}
    out["listes_nombre"] = {k: len(v) for k, v in lists.items()}
    return out


def write_summary(snapshot: dict, alerts: list) -> str:
    import anthropic

    payload = {
        "indicateurs": _clean(snapshot),
        "alertes": [{k: v for k, v in asdict(a).items() if k != "key"} for a in alerts
                    if not a.key.startswith(("essai_chaud:", "essai_bloque:", "risque:", "renouvellement:"))],
    }
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIStatusError as exc:
        return f"_Synthèse IA indisponible (erreur API {exc.status_code}). Les alertes ci-dessous restent valables._"
    except anthropic.APIConnectionError:
        return "_Synthèse IA indisponible (API injoignable). Les alertes ci-dessous restent valables._"

    if response.stop_reason == "refusal":
        return "_Synthèse IA non générée pour cette exécution._"
    return "\n".join(b.text for b in response.content if b.type == "text").strip()
