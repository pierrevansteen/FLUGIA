"""Google Ads : impressions, clics, dépenses, par jour (API REST Google Ads)."""
from __future__ import annotations

import os

import requests

from .base import CollectResult, Collector, CollectorError, env


class GoogleAdsCollector(Collector):
    name = "google_ads"
    label = "Google Ads"
    required_env = [
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
    ]

    def _access_token(self) -> str:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": env("GOOGLE_ADS_CLIENT_ID"),
                "client_secret": env("GOOGLE_ADS_CLIENT_SECRET"),
                "refresh_token": env("GOOGLE_ADS_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise CollectorError(f"Google Ads : rafraîchissement du jeton impossible ({resp.text[:300]})")
        return resp.json()["access_token"]

    def collect(self, start, end) -> CollectResult:
        version = os.environ.get("GOOGLE_ADS_API_VERSION", "v22")  # à monter quand Google retire une version
        customer = env("GOOGLE_ADS_CUSTOMER_ID").replace("-", "")
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "developer-token": env("GOOGLE_ADS_DEVELOPER_TOKEN"),
        }
        if os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"):
            headers["login-customer-id"] = os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"].replace("-", "")

        query = (
            "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
            f"FROM customer WHERE segments.date BETWEEN '{start}' AND '{end}'"
        )
        url = f"https://googleads.googleapis.com/{version}/customers/{customer}/googleAds:searchStream"
        chunks = self.http("POST", url, headers=headers, json={"query": query})

        result = CollectResult()
        for chunk in chunks if isinstance(chunks, list) else [chunks]:
            for row in chunk.get("results", []):
                day = row["segments"]["date"]
                m = row.get("metrics", {})
                result.add(day, "google_ads", "impressions", int(m.get("impressions", 0)))
                result.add(day, "google_ads", "clicks", int(m.get("clicks", 0)))
                result.add(day, "google_ads", "spend", int(m.get("costMicros", 0)) / 1_000_000)
                result.add(day, "google_ads", "ad_conversions", float(m.get("conversions", 0)))
        return result
