"""Meta : publicités Facebook/Instagram + organique Instagram et page Facebook (API Graph)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .base import CollectResult, Collector, env


class MetaCollector(Collector):
    name = "meta"
    label = "Meta (Facebook / Instagram)"
    required_env = ["META_ACCESS_TOKEN"]  # + META_AD_ACCOUNT_ID, META_IG_USER_ID et/ou META_PAGE_ID

    def _base(self) -> str:
        return f"https://graph.facebook.com/{os.environ.get('META_API_VERSION', 'v23.0')}"

    def collect(self, start, end) -> CollectResult:
        result = CollectResult()
        token = env("META_ACCESS_TOKEN")
        if os.environ.get("META_AD_ACCOUNT_ID"):
            self._ads(result, token, start, end)
        if os.environ.get("META_IG_USER_ID"):
            try:
                self._instagram(result, token, start, end)
            except Exception as exc:  # l'organique ne doit pas bloquer les publicités
                result.warnings.append(f"Instagram organique : {exc}")
        if os.environ.get("META_PAGE_ID"):
            try:
                self._facebook_page(result, token, start, end)
            except Exception as exc:  # idem : la page ne doit pas bloquer le reste
                result.warnings.append(f"Page Facebook : {exc}")
        if not any(os.environ.get(k) for k in ("META_AD_ACCOUNT_ID", "META_IG_USER_ID", "META_PAGE_ID")):
            result.warnings.append("Ni META_AD_ACCOUNT_ID, ni META_IG_USER_ID, ni META_PAGE_ID définis : rien à collecter.")
        return result

    def _ads(self, result, token, start, end):
        account = env("META_AD_ACCOUNT_ID").removeprefix("act_")
        url = f"{self._base()}/act_{account}/insights"
        params = {
            "fields": "impressions,reach,clicks,spend",
            "time_range": json.dumps({"since": str(start), "until": str(end)}),
            "time_increment": 1,
            "level": "account",
            "limit": 500,
            "access_token": token,
        }
        data = self.http("GET", url, params=params)
        while True:
            for row in data.get("data", []):
                day = row["date_start"]
                result.add(day, "meta_ads", "impressions", int(row.get("impressions", 0)))
                result.add(day, "meta_ads", "reach", int(row.get("reach", 0)))
                result.add(day, "meta_ads", "clicks", int(row.get("clicks", 0)))
                result.add(day, "meta_ads", "spend", float(row.get("spend", 0)))
            nxt = data.get("paging", {}).get("next")
            if not nxt:
                break
            data = self.http("GET", nxt)

    @staticmethod
    def _windows(start, end, days):
        """Découpe la période : Meta limite chaque appel de statistiques (30 jours pour Instagram)."""
        cur = start
        while cur <= end:
            stop = min(end, cur + timedelta(days=days - 1))
            since = datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc)
            until = datetime(stop.year, stop.month, stop.day, tzinfo=timezone.utc) + timedelta(days=1)
            yield int(since.timestamp()), int(until.timestamp())
            cur = stop + timedelta(days=1)

    def _instagram(self, result, token, start, end):
        ig = env("META_IG_USER_ID")
        for since, until in self._windows(start, end, 30):
            data = self.http(
                "GET",
                f"{self._base()}/{ig}/insights",
                params={
                    "metric": "reach",
                    "period": "day",
                    "since": since,
                    "until": until,
                    "access_token": token,
                },
            )
            for serie in data.get("data", []):
                for point in serie.get("values", []):
                    day = point["end_time"][:10]
                    result.add(day, "instagram", "reach", point.get("value", 0))

    # Meta remplace progressivement les « impressions » de page par les « vues » : on essaie dans l'ordre.
    PAGE_METRICS = {
        "impressions": ("page_media_view", "page_impressions"),
        "engagement": ("page_post_engagements",),
    }

    def _facebook_page(self, result, token, start, end):
        page = env("META_PAGE_ID")
        # Les statistiques de page exigent un jeton de page, que le jeton système permet d'obtenir.
        page_token = self.http(
            "GET", f"{self._base()}/{page}", params={"fields": "access_token", "access_token": token}
        ).get("access_token") or token
        windows = list(self._windows(start, end, 90))
        for name, candidates in self.PAGE_METRICS.items():
            errors = []
            for metric in candidates:
                try:
                    responses = [
                        self.http(
                            "GET",
                            f"{self._base()}/{page}/insights",
                            params={
                                "metric": metric,
                                "period": "day",
                                "since": since,
                                "until": until,
                                "access_token": page_token,
                            },
                        )
                        for since, until in windows
                    ]
                except Exception as exc:
                    errors.append(f"{metric} : {exc}")
                    continue
                for data in responses:
                    for serie in data.get("data", []):
                        for point in serie.get("values", []):
                            # end_time = minuit (heure du Pacifique) le lendemain du jour mesuré
                            day = (datetime.fromisoformat(point["end_time"].replace("+0000", "+00:00")) - timedelta(days=1)).date()
                            result.add(day, "facebook", name, point.get("value", 0))
                break
            else:
                result.warnings.append(f"Page Facebook, {name} indisponible ({'; '.join(errors)[:300]})")
