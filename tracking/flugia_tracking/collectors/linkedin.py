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


class LinkedInCollector(Collector):
    name = "linkedin"
    label = "LinkedIn"
    required_env = ["LINKEDIN_ACCESS_TOKEN"]  # + LINKEDIN_AD_ACCOUNT_ID et/ou LINKEDIN_ORGANIZATION_ID

    def _headers(self):
        return {
            "Authorization": f"Bearer {env('LINKEDIN_ACCESS_TOKEN')}",
            "LinkedIn-Version": os.environ.get("LINKEDIN_API_VERSION", "202510"),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def collect(self, start, end) -> CollectResult:
        result = CollectResult()
        if os.environ.get("LINKEDIN_AD_ACCOUNT_ID"):
            self._ads(result, start, end)
        if os.environ.get("LINKEDIN_ORGANIZATION_ID"):
            try:
                self._organic(result, start, end)
            except Exception as exc:
                result.warnings.append(f"LinkedIn organique : {exc}")
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
        data = self.http("GET", url, headers=self._headers())
        for row in data.get("elements", []):
            day = datetime.fromtimestamp(row["timeRange"]["start"] / 1000, tz=timezone.utc).date()
            stats = row.get("totalShareStatistics", {})
            result.add(day, "linkedin_organique", "impressions", stats.get("impressionCount", 0))
            result.add(day, "linkedin_organique", "clicks", stats.get("clickCount", 0))
            result.add(day, "linkedin_organique", "engagement", stats.get("engagement", 0))
