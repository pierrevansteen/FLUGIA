"""LinkedIn : publicités (adAnalytics) + page entreprise (statistiques des publications).

Nécessite une app LinkedIn validée pour le « Marketing Developer Platform » / « Community Management API ».
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .base import CollectResult, Collector, env


def _d(d):
    return f"(year:{d.year},month:{d.month},day:{d.day})"


def _default_version() -> str:
    """LinkedIn retire ses versions d'API après environ un an : on vise celle d'il y a deux mois."""
    d = datetime.now(timezone.utc) - timedelta(days=60)
    return f"{d.year}{d.month:02d}"


class LinkedInCollector(Collector):
    name = "linkedin"
    label = "LinkedIn"
    # Jeton direct (expire après 60 jours) ou, mieux, jeton de rafraîchissement + identifiants de l'app.
    # LINKEDIN_ORGANIC_ACCESS_TOKEN : jeton d'une 2e app si LinkedIn impose une app séparée pour la page.

    def enabled(self) -> bool:
        return any(os.environ.get(k) for k in ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_REFRESH_TOKEN", "LINKEDIN_ORGANIC_ACCESS_TOKEN"))

    def missing_env(self) -> list[str]:
        return [] if self.enabled() else ["LINKEDIN_ACCESS_TOKEN (ou LINKEDIN_REFRESH_TOKEN)"]

    _cached: str | None = None

    def _token(self, organic: bool = False) -> str:
        if organic and os.environ.get("LINKEDIN_ORGANIC_ACCESS_TOKEN"):
            return os.environ["LINKEDIN_ORGANIC_ACCESS_TOKEN"]
        if os.environ.get("LINKEDIN_REFRESH_TOKEN"):
            if not self._cached:
                data = self.http("POST", "https://www.linkedin.com/oauth/v2/accessToken", data={
                    "grant_type": "refresh_token",
                    "refresh_token": env("LINKEDIN_REFRESH_TOKEN"),
                    "client_id": env("LINKEDIN_CLIENT_ID"),
                    "client_secret": env("LINKEDIN_CLIENT_SECRET"),
                })
                self._cached = data["access_token"]
            return self._cached
        return env("LINKEDIN_ACCESS_TOKEN")

    def _headers(self, organic: bool = False):
        return {
            "Authorization": f"Bearer {self._token(organic)}",
            "LinkedIn-Version": os.environ.get("LINKEDIN_API_VERSION") or _default_version(),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def collect(self, start, end) -> CollectResult:
        result = CollectResult()
        if os.environ.get("LINKEDIN_AD_ACCOUNT_ID") and (os.environ.get("LINKEDIN_ACCESS_TOKEN") or os.environ.get("LINKEDIN_REFRESH_TOKEN")):
            self._ads(result, start, end)
        if os.environ.get("LINKEDIN_ORGANIZATION_ID"):
            try:
                self._organic(result, start, end)
            except Exception as exc:
                result.warnings.append(f"LinkedIn organique : {exc}")
        if not os.environ.get("LINKEDIN_AD_ACCOUNT_ID") and not os.environ.get("LINKEDIN_ORGANIZATION_ID"):
            result.warnings.append("Ni LINKEDIN_AD_ACCOUNT_ID ni LINKEDIN_ORGANIZATION_ID définis : rien à collecter.")
        return result

    def _ads(self, result, start, end):
        account = quote(f"urn:li:sponsoredAccount:{env('LINKEDIN_AD_ACCOUNT_ID')}", safe="")
        url = (
            "https://api.linkedin.com/rest/adAnalytics?q=analytics&pivot=ACCOUNT&timeGranularity=DAILY"
            f"&dateRange=(start:{_d(start)},end:{_d(end)})"
            f"&accounts=List({account})"
            "&fields=impressions,clicks,costInLocalCurrency,dateRange"
        )
        data = self.http("GET", url, headers=self._headers())
        for row in data.get("elements", []):
            s = row["dateRange"]["start"]
            day = f"{s['year']:04d}-{s['month']:02d}-{s['day']:02d}"
            result.add(day, "linkedin_ads", "impressions", row.get("impressions", 0))
            result.add(day, "linkedin_ads", "clicks", row.get("clicks", 0))
            result.add(day, "linkedin_ads", "spend", float(row.get("costInLocalCurrency", 0) or 0))

    def _organic(self, result, start, end):
        org = quote(f"urn:li:organization:{env('LINKEDIN_ORGANIZATION_ID')}", safe="")
        t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
        t1 = int((datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)).timestamp() * 1000)
        url = (
            "https://api.linkedin.com/rest/organizationalEntityShareStatistics?q=organizationalEntity"
            f"&organizationalEntity={org}"
            f"&timeIntervals=(timeRange:(start:{t0},end:{t1}),timeGranularityType:DAY)"
        )
        data = self.http("GET", url, headers=self._headers(organic=True))
        for row in data.get("elements", []):
            day = datetime.fromtimestamp(row["timeRange"]["start"] / 1000, tz=timezone.utc).date()
            stats = row.get("totalShareStatistics", {})
            result.add(day, "linkedin_organique", "impressions", stats.get("impressionCount", 0))
            result.add(day, "linkedin_organique", "clicks", stats.get("clickCount", 0))
            result.add(day, "linkedin_organique", "engagement", stats.get("engagement", 0))
