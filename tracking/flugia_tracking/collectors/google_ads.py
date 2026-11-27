"""Google Ads : impressions, clics, dépenses, par jour (API REST Google Ads).

Connexion recommandée : le compte de service Google (GOOGLE_SERVICE_ACCOUNT_JSON, déjà utilisé pour GA4),
ajouté en « Lecture seulement » dans le compte Google Ads. Depuis septembre 2026, le niveau d'accès à l'API
dépend du projet Google Cloud et le jeton développeur n'est plus nécessaire.
Ancienne méthode encore acceptée : OAuth (GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN).
"""
from __future__ import annotations

import os

import requests

from .base import CollectResult, Collector, CollectorError, env, google_access_token

OAUTH_ENV = ["GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN"]


class GoogleAdsCollector(Collector):
    name = "google_ads"
    label = "Google Ads"
    required_env = ["GOOGLE_ADS_CUSTOMER_ID"]  # + compte de service OU identifiants OAuth

    def _uses_oauth(self) -> bool:
        return all(os.environ.get(k) for k in OAUTH_ENV)

    def missing_env(self) -> list[str]:
        missing = super().missing_env()
        if not self._uses_oauth() and not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON (ou GOOGLE_ADS_CLIENT_ID/SECRET/REFRESH_TOKEN)")
        return missing

    def enabled(self) -> bool:
        return not self.missing_env()

    def _access_token(self) -> str:
        if not self._uses_oauth():
            return google_access_token(["https://www.googleapis.com/auth/adwords"])
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
        # Chaque version est maintenue environ 12 mois : à monter quand Google en retire une.
        version = os.environ.get("GOOGLE_ADS_API_VERSION", "v25")
        customer = env("GOOGLE_ADS_CUSTOMER_ID").replace("-", "")
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"):  # facultatif depuis septembre 2026
            headers["developer-token"] = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
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
