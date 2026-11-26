"""Google Analytics 4 : sessions par canal (dont Referral et Organic Search), trafic venant des IA (GEO),
et inscriptions par canal via l'événement d'inscription."""
from __future__ import annotations

import os

from .base import CollectResult, Collector, env, google_access_token

# Sources de trafic considérées comme des assistants IA (GEO).
AI_SOURCES = ["chatgpt", "openai", "perplexity", "gemini", "bard", "claude", "copilot", "mistral", "deepseek", "you.com"]

CHANNEL_MAP = {
    "organic search": "seo",
    "referral": "referral",
    "paid search": "google_ads",
    "paid social": "social_payant",
    "organic social": "social_organique",
    "direct": "direct",
    "email": "email",
}


def _channel(group: str) -> str:
    return "site:" + CHANNEL_MAP.get(group.lower(), group.lower().replace(" ", "_") or "autre")


class GA4Collector(Collector):
    name = "ga4"
    label = "Google Analytics 4"
    required_env = ["GOOGLE_SERVICE_ACCOUNT_JSON", "GA4_PROPERTY_ID"]

    def _report(self, token, start, end, dimensions, metrics, dimension_filter=None):
        body = {
            "dateRanges": [{"startDate": str(start), "endDate": str(end)}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "limit": 100000,
        }
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{env('GA4_PROPERTY_ID')}:runReport"
        data = self.http("POST", url, headers={"Authorization": f"Bearer {token}"}, json=body)
        for row in data.get("rows", []):
            dims = [d["value"] for d in row["dimensionValues"]]
            vals = [float(m["value"]) for m in row["metricValues"]]
            yield dims, vals

    def collect(self, start, end) -> CollectResult:
        token = google_access_token(["https://www.googleapis.com/auth/analytics.readonly"])
        result = CollectResult()

        # 1. Sessions et visiteurs par canal
        for (d, group), (sessions, users) in self._report(
            token, start, end, ["date", "sessionDefaultChannelGroup"], ["sessions", "totalUsers"]
        ):
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            result.add(day, _channel(group), "sessions", sessions)
            result.add(day, _channel(group), "visitors", users)

        # 2. Trafic venant des assistants IA (GEO)
        ai_filter = {
            "orGroup": {
                "expressions": [
                    {"filter": {"fieldName": "sessionSource", "stringFilter": {"matchType": "CONTAINS", "value": s}}}
                    for s in AI_SOURCES
                ]
            }
        }
        geo = {}
        for (d, _src), (sessions,) in self._report(token, start, end, ["date", "sessionSource"], ["sessions"], ai_filter):
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            geo[day] = geo.get(day, 0) + sessions
        for day, sessions in geo.items():
            result.add(day, "geo", "ai_sessions", sessions)

        # 3. Inscriptions (événement GA4) par canal : sert à l'attribution tant que l'app n'est pas branchée
        event = os.environ.get("GA4_SIGNUP_EVENT", "sign_up")
        demo_event = os.environ.get("GA4_DEMO_EVENT", "")
        events = [event] + ([demo_event] if demo_event else [])
        ev_filter = {"filter": {"fieldName": "eventName", "inListFilter": {"values": events}}}
        for (d, group, name), (count,) in self._report(
            token, start, end, ["date", "sessionDefaultChannelGroup", "eventName"], ["eventCount"], ev_filter
        ):
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            metric = "web_signups" if name == event else "web_demo_requests"
            result.add(day, _channel(group), metric, count)
        return result
