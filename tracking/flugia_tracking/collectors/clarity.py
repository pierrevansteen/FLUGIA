"""Microsoft Clarity : comportement des visiteurs du site (sessions, engagement, défilement, frustration).

API « Data Export » : https://www.clarity.ms/export-data/api/v1/project-live-insights
Limites imposées par Clarity : 10 appels par jour et par projet, et seulement les 1 à 3 derniers jours.
On fait donc UN appel par collecte (les dernières 24 h), enregistré sur la journée d'hier : l'historique
se construit jour après jour (pas de rattrapage possible au-delà de 3 jours).
Métriques préfixées « clarity_ » pour ne pas se mélanger aux sessions de Google Analytics.
"""
from __future__ import annotations

import os

from .base import CollectResult, Collector, env

URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"

# (bloc Clarity, champ) -> métrique enregistrée
FIELDS = {
    ("Traffic", "totalSessionCount"): "clarity_sessions",
    ("Traffic", "totalBotSessionCount"): "clarity_bot_sessions",
    ("Traffic", "distinctUserCount"): "clarity_users",
    ("Traffic", "pagesPerSessionPercentage"): "clarity_pages_per_session",
    ("EngagementTime", "activeTime"): "clarity_active_time_s",
    ("EngagementTime", "totalTime"): "clarity_total_time_s",
    ("ScrollDepth", "averageScrollDepth"): "clarity_scroll_depth_pct",
    ("RageClickCount", "sessionsWithMetricPercentage"): "clarity_rage_click_pct",
    ("DeadClickCount", "sessionsWithMetricPercentage"): "clarity_dead_click_pct",
    ("QuickbackClick", "sessionsWithMetricPercentage"): "clarity_quickback_pct",
    ("ExcessiveScroll", "sessionsWithMetricPercentage"): "clarity_excessive_scroll_pct",
    ("ScriptErrorCount", "sessionsWithMetricPercentage"): "clarity_script_error_pct",
}


def _num(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


class ClarityCollector(Collector):
    name = "clarity"
    label = "Microsoft Clarity (comportement sur le site)"
    required_env = ["CLARITY_API_TOKEN"]

    def collect(self, start, end) -> CollectResult:
        days = int(os.environ.get("CLARITY_NUM_DAYS", "1"))  # 1 = dernières 24 h
        data = self.http(
            "GET", URL,
            headers={"Authorization": f"Bearer {env('CLARITY_API_TOKEN')}", "Content-Type": "application/json"},
            params={"numOfDays": days},
        )
        result = CollectResult()
        found = set()
        for block in data if isinstance(data, list) else []:
            name = block.get("metricName", "")
            info = block.get("information") or []
            if not info:
                continue
            row = info[0]  # sans dimension : une seule ligne agrégée
            for (metric_name, field), metric in FIELDS.items():
                if metric_name == name and field in row:
                    value = _num(row[field])
                    if value is not None:
                        result.add(end, "clarity", metric, value)
                        found.add(metric)
        if not found:
            result.warnings.append(f"Clarity : aucune métrique reconnue dans la réponse ({str(data)[:200]})")
        return result
