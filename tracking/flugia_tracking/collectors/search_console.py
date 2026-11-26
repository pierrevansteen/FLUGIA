"""Google Search Console : impressions et clics SEO, et recherches de la marque (notoriété)."""
from __future__ import annotations

import os
from urllib.parse import quote

from .base import CollectResult, Collector, env, google_access_token


class SearchConsoleCollector(Collector):
    name = "search_console"
    label = "Google Search Console (SEO)"
    required_env = ["GOOGLE_SERVICE_ACCOUNT_JSON", "SEARCH_CONSOLE_SITE"]  # ex. sc-domain:flugia.com

    def _query(self, token, start, end, dimensions):
        url = (
            "https://www.googleapis.com/webmasters/v3/sites/"
            f"{quote(env('SEARCH_CONSOLE_SITE'), safe='')}/searchAnalytics/query"
        )
        start_row = 0
        while True:
            body = {
                "startDate": str(start),
                "endDate": str(end),
                "dimensions": dimensions,
                "rowLimit": 25000,
                "startRow": start_row,
            }
            rows = self.http("POST", url, headers={"Authorization": f"Bearer {token}"}, json=body).get("rows", [])
            yield from rows
            if len(rows) < 25000:
                break
            start_row += 25000

    def collect(self, start, end) -> CollectResult:
        token = google_access_token(["https://www.googleapis.com/auth/webmasters.readonly"])
        brand_terms = [t.strip().lower() for t in os.environ.get("BRAND_TERMS", "flugia").split(",") if t.strip()]
        result = CollectResult()

        # Totaux (inclut les requêtes anonymisées par Google)
        for row in self._query(token, start, end, ["date"]):
            day = row["keys"][0]
            result.add(day, "seo", "impressions", row.get("impressions", 0))
            result.add(day, "seo", "clicks", row.get("clicks", 0))

        # Recherches contenant la marque
        branded: dict[str, list[float]] = {}
        for row in self._query(token, start, end, ["date", "query"]):
            day, query = row["keys"]
            if any(t in query.lower() for t in brand_terms):
                acc = branded.setdefault(day, [0.0, 0.0])
                acc[0] += row.get("impressions", 0)
                acc[1] += row.get("clicks", 0)
        for day, (imp, clk) in branded.items():
            result.add(day, "seo_marque", "branded_searches", imp)
            result.add(day, "seo_marque", "clicks", clk)
        return result
