"""Google Analytics 4 : sessions par canal (dont Referral et Organic Search), trafic venant des IA (GEO),
inscriptions par canal via l'événement d'inscription, et démos réservées sur le site
(visites des pages de remerciement du formulaire de réservation)."""
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
    "ai assistant": "geo_ia",   # canal GA4 « AI Assistant » (ChatGPT, Perplexity, Gemini...)
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

        # 4. Détail des sites référents (presse, partenaires, annuaires...) : visites et inscriptions par site.
        #    Métriques distinctes (referral_*) pour ne pas compter deux fois les visites du canal Referral.
        own = [d.strip().lower() for d in os.environ.get("OWN_DOMAINS", "flugia.com").split(",") if d.strip()]
        referral = {"filter": {"fieldName": "sessionDefaultChannelGroup", "stringFilter": {"matchType": "EXACT", "value": "Referral"}}}
        for (d, source), (sessions,) in self._report(token, start, end, ["date", "sessionSource"], ["sessions"], referral):
            site = _domain(source)
            if any(site == o or site.endswith("." + o) for o in own):
                continue  # auto-référencement (app.flugia.com -> flugia.com, etc.)
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            result.add(day, f"referral:{site}", "referral_sessions", sessions)
        ref_events = {"andGroup": {"expressions": [referral, {"filter": {"fieldName": "eventName", "stringFilter": {"matchType": "EXACT", "value": event}}}]}}
        for (d, source), (count,) in self._report(token, start, end, ["date", "sessionSource"], ["eventCount"], ref_events):
            site = _domain(source)
            if any(site == o or site.endswith("." + o) for o in own):
                continue
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            result.add(day, f"referral:{site}", "referral_signups", count)

        # 5. Démos réservées sur le site : le formulaire de réservation renvoie vers une page de remerciement
        #    (/thank-you-page/, /fr/merci/, /nl/bedankt/). On compte les sessions qui l'ont vue, par canal.
        paths = [p.strip() for p in os.environ.get("DEMO_THANKYOU_PATHS", "/thank-you-page/,/fr/merci/,/nl/bedankt/").split(",") if p.strip()]
        if paths and not demo_event:
            variants = sorted({v for p in paths for v in (p, p.rstrip("/") or "/")})
            page_filter = {"filter": {"fieldName": "pagePath", "inListFilter": {"values": variants}}}
            per_day = {}
            for (d, group), (sessions,) in self._report(
                token, start, end, ["date", "sessionDefaultChannelGroup"], ["sessions"], page_filter
            ):
                day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                result.add(day, _channel(group), "web_demo_requests", sessions)
                per_day[day] = per_day.get(day, 0) + sessions
            # Total des démos réservées, sauf si l'agenda des démos est branché (il fait alors foi).
            if not os.environ.get("GOOGLE_CALENDAR_ID"):
                for day, n in per_day.items():
                    result.add(day, "demos", "demos_booked", n)
        return result


def _domain(source: str) -> str:
    s = (source or "(inconnu)").lower().strip()
    for prefix in ("https://", "http://", "www.", "m.", "l.", "lm."):
        s = s.removeprefix(prefix)
    return s.split("/")[0] or "(inconnu)"
