"""Google Agenda : démos réservées (par date de réservation) et démos tenues (par date du rendez-vous).

Configuration la plus simple : partager l'agenda des démos avec l'adresse du compte de service
(« Voir tous les détails des événements »), puis renseigner GOOGLE_CALENDAR_ID.
Les rendez-vous sont reconnus par un mot-clé dans le titre (DEMO_KEYWORDS, par défaut « démo,demo »).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .base import CollectResult, Collector, env, google_access_token, parse_ts


class GoogleCalendarCollector(Collector):
    name = "google_calendar"
    label = "Google Agenda (démos)"
    required_env = ["GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_CALENDAR_ID"]

    def collect(self, start, end) -> CollectResult:
        token = google_access_token(
            ["https://www.googleapis.com/auth/calendar.readonly"],
            subject=os.environ.get("GOOGLE_CALENDAR_IMPERSONATE") or None,  # délégation Workspace, optionnel
        )
        keywords = [k.strip().lower() for k in os.environ.get("DEMO_KEYWORDS", "démo,demo").split(",") if k.strip()]
        url = f"https://www.googleapis.com/calendar/v3/calendars/{quote(env('GOOGLE_CALENDAR_ID'), safe='')}/events"
        t_min = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        # On regarde aussi 60 jours après pour compter les démos réservées pendant la période mais fixées plus tard.
        t_max = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=61)
        params = {
            "timeMin": t_min.isoformat(),
            "timeMax": t_max.isoformat(),
            "singleEvents": "true",
            "showDeleted": "true",
            "maxResults": 2500,
        }
        headers = {"Authorization": f"Bearer {token}"}
        booked, held, cancelled = {}, {}, {}
        now = datetime.now(timezone.utc)
        while True:
            data = self.http("GET", url, headers=headers, params=params)
            for ev in data.get("items", []):
                title = (ev.get("summary") or "").lower()
                if not any(k in title for k in keywords):
                    continue
                created = parse_ts(ev.get("created"))
                begins = parse_ts(ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"))
                if ev.get("status") == "cancelled":
                    if begins and start <= begins.date() <= end:
                        cancelled[begins.date()] = cancelled.get(begins.date(), 0) + 1
                    continue
                if created and start <= created.date() <= end:
                    booked[created.date()] = booked.get(created.date(), 0) + 1
                if begins and begins <= now and start <= begins.date() <= end:
                    held[begins.date()] = held.get(begins.date(), 0) + 1
            token_next = data.get("nextPageToken")
            if not token_next:
                break
            params["pageToken"] = token_next

        result = CollectResult()
        for day, n in booked.items():
            result.add(day, "demos", "demos_booked", n)
        for day, n in held.items():
            result.add(day, "demos", "demos_held", n)
        for day, n in cancelled.items():
            result.add(day, "demos", "demos_cancelled", n)
        return result
