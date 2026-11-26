"""Stripe (si c'est votre outil de facturation, à brancher plus tard) : MRR, paiements échoués,
et — tant que la base de l'app n'est pas branchée — la liste des comptes (essais, conversions, résiliations)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .base import CollectResult, Collector, env, iso

MONTHLY = {"day": 30.0, "week": 52 / 12, "month": 1.0, "year": 1 / 12}


class StripeCollector(Collector):
    name = "stripe"
    label = "Stripe (facturation)"
    required_env = ["STRIPE_API_KEY"]  # clé restreinte en lecture seule recommandée

    def _list(self, path, params):
        params = dict(params, limit=100)
        while True:
            data = self.http("GET", f"https://api.stripe.com/v1/{path}", auth=(env("STRIPE_API_KEY"), ""), params=params)
            yield from data.get("data", [])
            if not data.get("has_more"):
                break
            params["starting_after"] = data["data"][-1]["id"]

    def collect(self, start, end) -> CollectResult:
        result = CollectResult()
        today = datetime.now(timezone.utc).date()
        fill_accounts = os.environ.get("STRIPE_FILL_ACCOUNTS", "auto")
        if fill_accounts == "auto":
            fill_accounts = "0" if os.environ.get("APP_DATABASE_URL") else "1"

        mrr_total, active = 0.0, 0
        for sub in self._list("subscriptions", {"status": "all", "expand[]": "data.customer"}):
            mrr = 0.0
            for item in sub.get("items", {}).get("data", []):
                price = item.get("price") or {}
                rec = price.get("recurring") or {}
                unit = (price.get("unit_amount") or 0) / 100
                per = MONTHLY.get(rec.get("interval", "month"), 1.0) / (rec.get("interval_count") or 1)
                mrr += unit * (item.get("quantity") or 1) * per
            status = sub.get("status")
            paying = status in ("active", "past_due")
            if paying:
                mrr_total += mrr
                active += 1

            if fill_accounts == "1":
                cust = sub.get("customer") if isinstance(sub.get("customer"), dict) else {}
                meta = cust.get("metadata") or {}
                trial_end = sub.get("trial_end")
                ended = sub.get("ended_at") or sub.get("canceled_at")
                converted = None
                if trial_end and datetime.fromtimestamp(trial_end, tz=timezone.utc).date() <= today:
                    if paying or (ended and ended > trial_end):
                        converted = trial_end
                elif not trial_end and status not in ("incomplete", "incomplete_expired"):
                    converted = sub.get("start_date")
                result.accounts.append({
                    "account_id": f"stripe:{cust.get('id') or sub.get('customer')}",
                    "name": cust.get("name"),
                    "email": cust.get("email"),
                    "created_at": iso(sub.get("trial_start") or sub.get("start_date") or sub.get("created")),
                    "first_touch_source": meta.get("utm_source"),
                    "first_touch_medium": meta.get("utm_medium"),
                    "first_touch_campaign": meta.get("utm_campaign"),
                    "plan": ((sub.get("items", {}).get("data") or [{}])[0].get("price") or {}).get("nickname"),
                    "status": {"trialing": "trial", "canceled": "cancelled"}.get(status, status),
                    "trial_ends_at": iso(trial_end),
                    "converted_at": iso(converted),
                    "churned_at": iso(ended) if status in ("canceled", "incomplete_expired") else None,
                    "contract_end_at": iso(sub.get("current_period_end")),
                    "mrr": round(mrr, 2) if paying else 0,
                    "origin": "stripe",
                })

        result.add(today, "facturation", "mrr", round(mrr_total, 2))
        result.add(today, "facturation", "paying_customers", active)

        since = int((datetime.now(timezone.utc) - timedelta(days=min(30, (today - start).days + 1))).timestamp())
        failures: dict = {}
        for ev in self._list("events", {"type": "invoice.payment_failed", "created[gte]": since}):
            day = datetime.fromtimestamp(ev["created"], tz=timezone.utc).date()
            failures[day] = failures.get(day, 0) + 1
        for day, n in failures.items():
            result.add(day, "facturation", "payment_failures", n)
        return result
