"""Données FICTIVES pour visualiser le tableau de bord avant de brancher les vraies sources."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from .db import DB

DEMO_SUMMARY = """**En une phrase** : la visibilité se maintient, mais les inscriptions reculent nettement cette semaine.

**Les 4 étapes**
- **Visibilité** : vues en léger recul, pénalisées par Meta Ads.
- **Intérêt** : inscriptions et démos réservées en baisse.
- **Acquisition** : conversion après l'essai proche de 20 %.
- **Rétention** : base de clients payants stable, churn à surveiller.

**3 priorités de la semaine**
1. Appeler les essais à conclure avant la fin de leur période d'essai.
2. Tester le parcours d'inscription : le taux de conversion du site a été divisé par deux.
3. Contacter les clients dont l'usage chute de plus de 50 %."""

FAMILY_UTM = [  # (utm_source, utm_medium, poids)
    ("google", "cpc", 22), ("google", "organic", 25), ("linkedin", "social", 18), ("facebook", "paid", 12),
    ("chatgpt.com", "referral", 5), ("partenaire-ucm", "referral", 8), ("", "", 10),
]
NAMES = ["Boulangerie", "Salon", "Imprimerie", "Cabinet", "Brasserie", "Agence", "Garage", "Fiduciaire", "Studio", "Atelier"]
CITIES = ["Liège", "Namur", "Gand", "Bruxelles", "Mons", "Anvers", "Wavre", "Louvain", "Charleroi", "Bruges"]


def seed(db: DB, days: int = 120) -> date:
    rnd = random.Random(42)
    as_of = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = as_of - timedelta(days=days - 1)
    now = datetime.now(timezone.utc).isoformat()
    rows = []

    def add(day, source, channel, metric, value):
        rows.append((day.isoformat(), source, channel, metric, round(value, 2) if metric in ("spend", "ai_mention_rate") else round(value), now))

    for i in range(days):
        day = start + timedelta(days=i)
        growth = 1 + i / days * 0.35
        weekday = 0.65 if day.weekday() >= 5 else 1.0
        last_week = (as_of - day).days < 7
        noise = lambda: rnd.uniform(0.85, 1.15)  # noqa: E731

        imp = 1500 * growth * weekday * noise()
        add(day, "google_ads", "google_ads", "impressions", imp)
        add(day, "google_ads", "google_ads", "clicks", imp * 0.031)
        add(day, "google_ads", "google_ads", "spend", 38 * noise())
        meta = 3100 * weekday * noise() * (0.6 if last_week else 1)          # chute simulée la dernière semaine
        add(day, "meta", "meta_ads", "impressions", meta)
        add(day, "meta", "meta_ads", "clicks", meta * 0.012)
        add(day, "meta", "meta_ads", "spend", 24 * noise())
        add(day, "meta", "instagram", "reach", 320 * growth * noise())
        li = 850 * weekday * noise()
        add(day, "linkedin", "linkedin_ads", "impressions", li)
        add(day, "linkedin", "linkedin_ads", "clicks", li * 0.008)
        add(day, "linkedin", "linkedin_ads", "spend", 34 * noise() * (1.5 if last_week else 1))
        add(day, "linkedin", "linkedin_organique", "impressions", 1100 * growth ** 1.6 * weekday * noise())
        add(day, "search_console", "seo", "impressions", 900 * growth ** 1.4 * noise())
        add(day, "search_console", "seo", "clicks", 900 * growth ** 1.4 * 0.04 * noise())
        add(day, "search_console", "seo_marque", "branded_searches", 24 * growth * weekday * noise())
        for ch, base in (("site:seo", 58), ("site:google_ads", 42), ("site:social_payant", 30),
                         ("site:social_organique", 16), ("site:referral", 11), ("site:direct", 35)):
            add(day, "ga4", ch, "sessions", base * growth * weekday * noise())
        add(day, "ga4", "geo", "ai_sessions", 3 * growth ** 2 * noise())
        for site, base, since in (("ucm.be", 4, 0), ("startit.be", 3, 0), ("trends.knack.be", 2, 40),
                                  ("lecho.be", 9, days - 5), ("google-business-directory.be", 1, 0)):
            if i >= since and rnd.random() < 0.8:
                v = base * weekday * noise() * (2.5 if site == "lecho.be" else 1)
                add(day, "ga4", f"referral:{site}", "referral_sessions", v)
                if rnd.random() < 0.08 * base:
                    add(day, "ga4", f"referral:{site}", "referral_signups", 1)
        if rnd.random() < 0.9 * weekday:
            add(day, "google_calendar", "demos", "demos_booked", rnd.choice([1, 1, 1, 2]) * (0 if last_week and rnd.random() < .5 else 1))
        if rnd.random() < 0.75 * weekday:
            add(day, "google_calendar", "demos", "demos_held", 1)
        if day.weekday() == 0:  # mesure GEO hebdo
            rate = min(0.45, 0.12 + i / days * 0.25)
            hits = 0
            for q in ("pme_agent_ia_belgique", "assistant_ia_restaurant", "coworker_ia_marketing", "ia_service_client_pme",
                      "alternative_zapier_ia", "ia_rgpd_ai_act", "nl_ai_medewerker"):
                hit = int(rnd.random() < rate)
                hits += hit
                add(day, "geo", f"geo:{q}", "ai_mentioned", hit)
            for comp, p in (("Zapier", .9), ("Make", .7), ("n8n", .5), ("Lindy", .3), ("Microsoft Copilot", .8)):
                add(day, "geo", f"geo_concurrent:{comp}", "ai_mentions", sum(rnd.random() < p for _ in range(7)))
            add(day, "geo", "geo", "ai_prompts_tested", 7)
            add(day, "geo", "geo", "ai_mentions", hits)
            add(day, "geo", "geo", "ai_mention_rate", round(100 * hits / 7, 1))

    # Comptes
    accounts = []
    weights = [w for *_x, w in FAMILY_UTM]
    n = 0
    for i in range(days):
        day = start + timedelta(days=i)
        lam = 2.4 * (1 + i / days * 0.3) * (0.5 if day.weekday() >= 5 else 1) * (0.55 if (as_of - day).days < 7 else 1)
        k = sum(1 for _ in range(10) if rnd.random() < lam / 10)
        for _ in range(k):
            n += 1
            src, med, _w = rnd.choices(FAMILY_UTM, weights)[0]
            created = day
            trial_end = created + timedelta(days=15)
            activated = created + timedelta(days=rnd.randint(0, 4)) if rnd.random() < 0.62 else None
            p_conv = {"cpc": .22, "organic": .25, "social": .2, "paid": .1, "referral": .32}.get(med, .15)
            if activated:
                p_conv *= 1.6
            else:
                p_conv *= 0.2
            converted = trial_end if trial_end <= as_of and rnd.random() < p_conv else None
            churned = None
            if converted and rnd.random() < 0.3:
                churned = converted + timedelta(days=rnd.randint(8, 60))
                churned = churned if churned <= as_of else None
            if not converted and trial_end <= as_of:
                status = "expired"
            elif churned:
                status = "cancelled"
            elif converted:
                status = "active"
            else:
                status = "trial"
            plan = rnd.choice(["standard", "standard", "business"])
            mrr = (49 if plan == "standard" else 149) if status == "active" else 0
            u_prev = rnd.uniform(30, 200) if converted else 0
            u_cur = u_prev * (rnd.uniform(0.2, 0.45) if rnd.random() < 0.08 else rnd.uniform(0.8, 1.3))
            usage_7d = rnd.uniform(5, 60) if activated else 0
            renew = None
            if status == "active":
                renew = as_of + timedelta(days=(30 - (as_of - converted).days % 30))
            accounts.append({
                "account_id": f"demo:{n}",
                "name": f"{rnd.choice(NAMES)} {rnd.choice(CITIES)} #{n}",
                "email": None,
                "created_at": created.isoformat(),
                "first_touch_source": src or None, "first_touch_medium": med or None, "first_touch_campaign": None,
                "plan": plan, "status": status,
                "trial_ends_at": trial_end.isoformat(),
                "activated_at": activated.isoformat() if activated else None,
                "converted_at": converted.isoformat() if converted else None,
                "churned_at": churned.isoformat() if churned else None,
                "contract_end_at": renew.isoformat() if renew else None,
                "mrr": mrr, "usage_7d": round(usage_7d), "usage_30d": round(u_cur), "usage_prev_30d": round(u_prev),
                "origin": "demo",
            })

    # MRR journalier reconstitué
    for i in range(days):
        day = start + timedelta(days=i)
        mrr = 0
        for a in accounts:
            conv = a["converted_at"] and date.fromisoformat(a["converted_at"]) <= day
            gone = a["churned_at"] and date.fromisoformat(a["churned_at"]) <= day
            if conv and not gone:
                mrr += 49 if a["plan"] == "standard" else 149
        add(day, "stripe", "facturation", "mrr", mrr)

    with db.transaction():
        db.upsert_metrics(rows)
        db.upsert_accounts(accounts, now)
        for name in ("google_ads", "meta", "linkedin", "search_console", "ga4", "geo", "google_calendar"):
            db.log_run(now, name, "ok", 0, "données de démonstration")
    return as_of
