"""Meta : publicités Facebook/Instagram + portée organique Instagram (API Graph)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .base import CollectResult, Collector, env


class MetaCollector(Collector):
    name = "meta"
    label = "Meta (Facebook / Instagram)"
    required_env = ["META_ACCESS_TOKEN"]  # + META_AD_ACCOUNT_ID et/ou META_IG_USER_ID

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
            except Exception as exc:
                result.warnings.append(f"Page Facebook organique : {exc}")
        if not os.environ.get("META_AD_ACCOUNT_ID") and not os.environ.get("META_IG_USER_ID"):
            result.warnings.append("Ni META_AD_ACCOUNT_ID ni META_IG_USER_ID définis : rien à collecter.")
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
    def _windows(start, end, days: int = 30):
        """Meta refuse plus de 30 jours entre since et until : on découpe la période."""
        cur = start
        while cur <= end:
            stop = min(end, cur + timedelta(days=days - 1))
            yield cur, stop
            cur = stop + timedelta(days=1)

    @staticmethod
    def _ts(d, plus_one=False):
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + (timedelta(days=1) if plus_one else timedelta())
        return int(dt.timestamp())

    def _insights(self, object_id, metric, token, start, end):
        """Valeurs journalières d'une statistique, période par période. Renvoie {jour: valeur}."""
        out = {}
        for a, b in self._windows(start, end):
            data = self.http("GET", f"{self._base()}/{object_id}/insights", params={
                "metric": metric, "period": "day", "since": self._ts(a), "until": self._ts(b, True), "access_token": token,
            })
            for serie in data.get("data", []):
                for point in serie.get("values", []):
                    v = point.get("value", 0)
                    # end_time marque la fin de la journée mesurée (minuit, heure du Pacifique) : la valeur porte sur la veille
                    day = (datetime.fromisoformat(point["end_time"].replace("+0000", "+00:00")) - timedelta(days=1)).date()
                    if isinstance(v, (int, float)) and start <= day <= end:
                        out[day] = v
        return out

    def _instagram(self, result, token, start, end):
        ig = env("META_IG_USER_ID")
        for day, v in self._insights(ig, "reach", token, start, end).items():
            result.add(day, "instagram", "reach", v)

    # Statistiques de page Facebook : Meta a renommé plusieurs métriques en 2025.
    # On essaie les noms dans l'ordre et on garde le premier qui répond.
    PAGE_METRICS = {
        "impressions": ["page_media_view", "page_impressions"],
        "reach": ["page_total_media_view_unique", "page_impressions_unique"],
        "page_views": ["page_views_total"],
        "engagement": ["page_post_engagements"],
    }

    def _facebook_page(self, result, token, start, end):
        page = env("META_PAGE_ID")
        # Les statistiques de page demandent un jeton de page, obtenu à partir du jeton utilisateur système.
        try:
            page_token = self.http("GET", f"{self._base()}/{page}", params={"fields": "access_token", "access_token": token}).get("access_token") or token
        except Exception:
            page_token = token
        used, failed = [], []
        for metric, candidates in self.PAGE_METRICS.items():
            for name in candidates:
                try:
                    values = self._insights(page, name, page_token, start, end)
                except Exception as exc:
                    failed.append(f"{name} ({str(exc)[:80]})")
                    continue
                for day, v in values.items():
                    result.add(day, "facebook_organique", metric, v)
                used.append(name)
                break
        if failed:
            result.warnings.append("Page Facebook, statistiques indisponibles : " + "; ".join(failed))
        if used:
            result.warnings.append("Page Facebook, statistiques utilisées : " + ", ".join(used))
