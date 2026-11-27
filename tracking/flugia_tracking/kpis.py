"""Calcul des KPIs Visibilité / Intérêt / Acquisition / Rétention à partir de la base.

Tout est calculé par le code (chiffres exacts et reproductibles). L'IA n'intervient qu'après,
pour commenter.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .collectors.base import parse_ts
from .config import alerts_config
from .db import DB

AI_SOURCES = ("chatgpt", "openai", "perplexity", "gemini", "bard", "claude", "copilot", "mistral", "deepseek")

CHANNEL_LABELS = {
    "google_ads": "Google Ads",
    "meta_ads": "Meta Ads",
    "instagram": "Instagram (organique)",
    "facebook_organique": "Facebook (page, organique)",
    "facebook": "Facebook (organique)",
    "linkedin_ads": "LinkedIn Ads",
    "linkedin_organique": "LinkedIn (organique)",
    "seo": "SEO (Google)",
    "seo_marque": "Recherches « flugia »",
    "geo": "GEO (IA)",
    "clarity": "Clarity (comportement sur le site)",
    "demos": "Démos",
    "facturation": "Facturation",
}

# Canal "famille" utilisé pour l'attribution (de l'impression au client payant)
FAMILIES = ["Google Ads", "Meta", "LinkedIn", "SEO", "GEO", "Referral", "Direct / autre"]
SPEND_FAMILY = {"google_ads": "Google Ads", "meta_ads": "Meta", "linkedin_ads": "LinkedIn"}
SITE_FAMILY = {
    "site:google_ads": "Google Ads",
    "site:seo": "SEO",
    "site:referral": "Referral",
    "site:social_payant": "Meta",
    "site:social_organique": "LinkedIn",
    "site:geo_ia": "GEO",
}


SITE_LABELS = {
    "site:seo": "Recherche Google (SEO)",
    "site:direct": "Accès direct",
    "site:referral": "Sites référents",
    "site:google_ads": "Google Ads",
    "site:social_payant": "Réseaux sociaux payants",
    "site:social_organique": "Réseaux sociaux (organique)",
    "site:geo_ia": "Assistants IA (GEO)",
    "site:email": "E-mail",
    "site:unassigned": "Non attribué",
    "site:paid_other": "Autre publicité",
    "site:display": "Display",
    "site:organic_video": "Vidéo (organique)",
}


def label(channel: str) -> str:
    if channel in CHANNEL_LABELS:
        return CHANNEL_LABELS[channel]
    if channel.startswith("manuel:"):
        return "Manuel · " + channel.split(":", 1)[1].replace("_", " ")
    if channel in SITE_LABELS:
        return "Site · " + SITE_LABELS[channel]
    if channel.startswith("site:"):
        return "Site · " + channel.split(":", 1)[1].replace("_", " ")
    return channel


def family_from_utm(source: str | None, medium: str | None) -> str:
    s, m = (source or "").lower(), (medium or "").lower()
    if any(a in s for a in AI_SOURCES):
        return "GEO"
    if "linkedin" in s or s == "li":
        return "LinkedIn"
    if s in ("facebook", "instagram", "meta", "fb", "ig") or "facebook" in s or "instagram" in s:
        return "Meta"
    if "google" in s and m in ("cpc", "ppc", "paid", "ads", "sem"):
        return "Google Ads"
    if m in ("organic", "seo") or s in ("google", "bing", "duckduckgo", "ecosia", "yahoo"):
        return "SEO"
    if m in ("referral", "partner", "partenaire", "affiliate", "parrainage") or (s and s not in ("direct", "(direct)")):
        return "Referral"
    return "Direct / autre"


def _pct(cur, prev):
    if prev in (None, 0) or cur is None:
        return None
    return round(100 * (cur - prev) / prev, 1)


def _d(value) -> date | None:
    dt = parse_ts(value)
    return dt.date() if dt else None


class Data:
    """Charge en mémoire les 400 derniers jours de métriques et tous les comptes."""

    def __init__(self, db: DB, as_of: date):
        self.as_of = as_of
        since = (as_of - timedelta(days=400)).isoformat()
        self.rows = [
            (date.fromisoformat(str(r["day"])[:10]), r["source"], r["channel"], r["metric"], float(r["value"]))
            for r in db.query(
                "SELECT day, source, channel, metric, value FROM metrics_daily WHERE day >= ? AND day <= ?",
                (since, as_of.isoformat()),
            )
        ]
        self.accounts = db.query("SELECT * FROM accounts")
        for a in self.accounts:
            for f in ("created_at", "trial_ends_at", "activated_at", "converted_at", "churned_at", "contract_end_at"):
                a[f] = _d(a.get(f))
        self.runs = db.query(
            "SELECT collector, status, run_at, message, rows_written FROM collector_runs ORDER BY run_at DESC"
        )
        self.has_accounts = bool(self.accounts)

    # --- séries journalières --------------------------------------------
    def series(self, metric: str, channel: str | None = None, prefix: str | None = None) -> dict[date, float]:
        if metric in ("signups", "activations", "trial_conversions", "churned") and self.has_accounts:
            field = {"signups": "created_at", "activations": "activated_at",
                     "trial_conversions": "converted_at", "churned": "churned_at"}[metric]
            out: dict[date, float] = defaultdict(float)
            for a in self.accounts:
                if a[field] and (channel is None or family_from_utm(a.get("first_touch_source"), a.get("first_touch_medium")) == channel):
                    out[a[field]] += 1
            return out
        if metric == "signups":  # repli : événement d'inscription GA4
            metric = "web_signups"
        out = defaultdict(float)
        for day, _src, ch, m, v in self.rows:
            if m != metric:
                continue
            if channel is not None and ch != channel:
                continue
            if prefix is not None and not ch.startswith(prefix):
                continue
            out[day] += v
        return out

    def visibility(self, channel: str | None = None) -> dict[date, float]:
        """Vues : impressions, ou portée quand la plateforme ne fournit que ça (Instagram)."""
        out = defaultdict(float)
        for day, _src, ch, m, v in self.rows:
            if channel is not None and ch != channel:
                continue
            if m == "impressions" or (ch == "instagram" and m == "reach"):
                out[day] += v
        return out

    def channels(self, metric: str) -> list[str]:
        return sorted({ch for _d_, _s, ch, m, _v in self.rows if m == metric})

    def total(self, s: dict[date, float], start: date, end: date) -> float:
        return sum(v for d, v in s.items() if start <= d <= end)

    def latest(self, metric: str, channel: str | None = None) -> tuple[date | None, float | None]:
        s = self.series(metric, channel)
        if not s:
            return None, None
        d = max(s)
        return d, s[d]


def windows(as_of: date, days: int = 7):
    cur = (as_of - timedelta(days=days - 1), as_of)
    prev = (cur[0] - timedelta(days=days), cur[0] - timedelta(days=1))
    return cur, prev


def is_trial(a, as_of) -> bool:
    if a.get("status") in ("trial", "trialing"):
        return True
    return not a["converted_at"] and not a["churned_at"] and bool(a["trial_ends_at"]) and a["trial_ends_at"] >= as_of


def is_paying(a, as_of) -> bool:
    if a.get("status") in ("active", "past_due"):
        return True
    return bool(a["converted_at"]) and a["converted_at"] <= as_of and not a["churned_at"]


def compute(db: DB, as_of: date | None = None) -> dict:
    as_of = as_of or (datetime.now(timezone.utc).date() - timedelta(days=1))
    data = Data(db, as_of)
    cfg = alerts_config()
    (c0, c1), (p0, p1) = windows(as_of, 7)
    T = data.total

    # ---------------- Visibilité ----------------
    vis_channels = sorted({ch for _d_, _s, ch, m, _v in data.rows
                           if m == "impressions" or (ch == "instagram" and m == "reach")})
    by_channel = []
    for ch in vis_channels:
        s = data.visibility(ch)
        by_channel.append({"canal": label(ch), "id": ch, "cur": T(s, c0, c1), "prev": T(s, p0, p1)})
    by_channel.sort(key=lambda r: -r["cur"])
    vis_all = data.visibility()
    spend = data.series("spend")
    branded = data.series("branded_searches")
    geo_rate = data.series("ai_mention_rate", "geo")
    geo_points = sorted(geo_rate.items())
    visibilite = {
        "total": {"cur": T(vis_all, c0, c1), "prev": T(vis_all, p0, p1)},
        "par_canal": by_channel,
        "depenses": {"cur": round(T(spend, c0, c1), 2), "prev": round(T(spend, p0, p1), 2)},
        "recherches_marque": {"cur": T(branded, c0, c1), "prev": T(branded, p0, p1)},
        "trafic_ia": {"cur": T(data.series("ai_sessions"), c0, c1), "prev": T(data.series("ai_sessions"), p0, p1)},
        "geo_taux_citation": {
            "cur": geo_points[-1][1] if geo_points else None,
            "prev": geo_points[-2][1] if len(geo_points) > 1 else None,
            "date": geo_points[-1][0].isoformat() if geo_points else None,
        },
        "geo_par_question": _geo_detail(data),
    }

    # ---------------- Intérêt ----------------
    sessions = data.series("sessions")
    signups = data.series("signups")
    demos = data.series("demos_booked")
    held = data.series("demos_held")
    s_cur, s_prev = T(sessions, c0, c1), T(sessions, p0, p1)
    g_cur, g_prev = T(signups, c0, c1), T(signups, p0, p1)
    interet = {
        "source_inscriptions": "app" if data.has_accounts else ("ga4" if data.series("web_signups") else None),
        "visites": {"cur": s_cur, "prev": s_prev},
        "inscriptions": {"cur": g_cur, "prev": g_prev},
        "demos_reservees": {"cur": T(demos, c0, c1), "prev": T(demos, p0, p1)},
        "demos_tenues": {"cur": T(held, c0, c1), "prev": T(held, p0, p1)},
        "taux_inscription": {
            "cur": round(100 * g_cur / s_cur, 2) if s_cur else None,
            "prev": round(100 * g_prev / s_prev, 2) if s_prev else None,
        },
        "visites_par_canal": [
            {"canal": label(ch), "cur": T(data.series("sessions", ch), c0, c1), "prev": T(data.series("sessions", ch), p0, p1)}
            for ch in data.channels("sessions")
        ],
        "sites_referents": _referrers(data, as_of, cfg.get("referents", {})),
    }

    # ---------------- Acquisition ----------------
    acq = {"branche": data.has_accounts}
    a28_0, a28_1 = as_of - timedelta(days=27), as_of
    b28_0, b28_1 = a28_0 - timedelta(days=28), a28_0 - timedelta(days=1)
    if data.has_accounts:
        def conv(start, end):
            ended = [a for a in data.accounts if a["trial_ends_at"] and start <= a["trial_ends_at"] <= end]
            won = [a for a in ended if a["converted_at"]]
            return len(ended), len(won)

        e_cur, w_cur = conv(a28_0, a28_1)
        e_prev, w_prev = conv(b28_0, b28_1)
        new_signups = [a for a in data.accounts if a["created_at"] and a28_0 - timedelta(days=14) <= a["created_at"] <= a28_1 - timedelta(days=0)]
        activated = [a for a in new_signups if a["activated_at"]]
        acq.update({
            "essais_termines_28j": e_cur,
            "convertis_28j": w_cur,
            "taux_conversion": {"cur": round(100 * w_cur / e_cur, 1) if e_cur else None,
                                 "prev": round(100 * w_prev / e_prev, 1) if e_prev else None},
            "nouveaux_clients": {"cur": T(data.series("trial_conversions"), c0, c1),
                                 "prev": T(data.series("trial_conversions"), p0, p1)},
            "taux_activation": round(100 * len(activated) / len(new_signups), 1) if new_signups else None,
        })
    acq["attribution_90j"] = _attribution(data, as_of)

    # ---------------- Rétention ----------------
    ret = {"branche": data.has_accounts}
    _, mrr_metric = data.latest("mrr", "facturation")
    if data.has_accounts:
        paying = [a for a in data.accounts if is_paying(a, as_of)]
        churn_cur = [a for a in data.accounts if a["churned_at"] and as_of - timedelta(days=29) <= a["churned_at"] <= as_of and a["converted_at"]]
        churn_prev = [a for a in data.accounts if a["churned_at"] and as_of - timedelta(days=59) <= a["churned_at"] <= as_of - timedelta(days=30) and a["converted_at"]]

        def prolongation(days):
            cohort = [a for a in data.accounts if a["converted_at"] and a["converted_at"] <= as_of - timedelta(days=days)]
            kept = [a for a in cohort if not a["churned_at"] or a["churned_at"] > a["converted_at"] + timedelta(days=days)]
            return (round(100 * len(kept) / len(cohort), 1) if cohort else None), len(cohort)

        p30, n30 = prolongation(30)
        p90, n90 = prolongation(90)
        base = len(paying) + len(churn_cur)
        ret.update({
            "clients_payants": len(paying),
            "mrr": round(sum(a.get("mrr") or 0 for a in paying), 2) or mrr_metric,
            "resiliations_30j": {"cur": len(churn_cur), "prev": len(churn_prev)},
            "taux_churn_30j": round(100 * len(churn_cur) / base, 1) if base else None,
            "prolongation_1_mois": {"taux": p30, "cohorte": n30},
            "prolongation_3_mois": {"taux": p90, "cohorte": n90},
        })
    else:
        ret["mrr"] = mrr_metric
    ret["paiements_echoues_7j"] = T(data.series("payment_failures"), c0, c1)

    snapshot = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "au": as_of.isoformat(),
        "periode": {"debut": c0.isoformat(), "fin": c1.isoformat(),
                    "precedente_debut": p0.isoformat(), "precedente_fin": p1.isoformat()},
        "visibilite": visibilite,
        "interet": interet,
        "acquisition": acq,
        "retention": ret,
        "comportement": _behavior(data, c0, c1, p0, p1),
        "hebdo": _weekly(data, as_of),
        "listes": _lists(data, as_of, cfg["comptes"]) if data.has_accounts else {},
        "sources": _sources(data),
    }
    snapshot["_data"] = data  # usage interne (alertes) ; retiré avant export
    return snapshot


# Comportement sur le site (Microsoft Clarity) : (libellé, métrique, agrégation, hausse = bien ?, unité)
BEHAVIOR = [
    ("Sessions", "clarity_sessions", "sum", True, ""),
    ("Visiteurs", "clarity_users", "sum", True, ""),
    ("Pages par session", "clarity_pages_per_session", "avg", True, ""),
    ("Temps actif moyen", "clarity_active_time_s", "avg", True, "s"),
    ("Profondeur de défilement", "clarity_scroll_depth_pct", "avg", True, "%"),
    ("Sessions avec rage clicks", "clarity_rage_click_pct", "avg", False, "%"),
    ("Sessions avec clics morts", "clarity_dead_click_pct", "avg", False, "%"),
    ("Retours rapides", "clarity_quickback_pct", "avg", False, "%"),
    ("Défilement excessif", "clarity_excessive_scroll_pct", "avg", False, "%"),
    ("Sessions avec erreur JavaScript", "clarity_script_error_pct", "avg", False, "%"),
]


def _behavior(data: Data, c0: date, c1: date, p0: date, p1: date) -> dict:
    """Moyenne (ou total) des relevés quotidiens Clarity sur 7 jours, comparée à la semaine précédente."""
    def agg(metric, how, a, b):
        vals = [v for d, v in data.series(metric, "clarity").items() if a <= d <= b]
        if not vals:
            return None
        return sum(vals) if how == "sum" else round(sum(vals) / len(vals), 1)

    days = {d for d, _s, ch, _m, _v in data.rows if ch == "clarity" and c0 <= d <= c1}
    rows = []
    for label_, metric, how, good_up, unit in BEHAVIOR:
        cur, prev = agg(metric, how, c0, c1), agg(metric, how, p0, p1)
        if cur is None and prev is None:
            continue
        rows.append({"indicateur": label_, "cur": cur, "prev": prev, "hausse_bien": good_up, "unite": unit,
                     "points": unit == "%"})
    return {"branche": bool(rows), "jours_mesures": len(days), "lignes": rows}


def _geo_detail(data: Data) -> list[dict]:
    latest: dict[str, tuple[date, float]] = {}
    for day, _s, ch, m, v in data.rows:
        if m == "ai_mentioned" and ch.startswith("geo:"):
            if ch not in latest or day > latest[ch][0]:
                latest[ch] = (day, v)
    comp: dict[str, float] = defaultdict(float)
    last_day = max((d for d, _ in latest.values()), default=None)
    for day, _s, ch, m, v in data.rows:
        if m == "ai_mentions" and ch.startswith("geo_concurrent:") and day == last_day:
            comp[ch.split(":", 1)[1]] += v
    out = [{"question": ch.split(":", 1)[1], "citee": bool(v), "date": d.isoformat()} for ch, (d, v) in sorted(latest.items())]
    if comp:
        out.append({"concurrents_cites": dict(sorted(comp.items(), key=lambda kv: -kv[1]))})
    return out


def _referrers(data: Data, as_of: date, rules: dict) -> list[dict]:
    """Sites qui envoient des visiteurs (presse, partenaires, annuaires) : 30 derniers jours vs 30 précédents.

    Un site est « nouveau » s'il n'avait envoyé aucune visite pendant les `jours_historique` jours
    précédant la semaine en cours.
    """
    history = rules.get("jours_historique", 60)
    w0 = as_of - timedelta(days=6)
    c0, p0 = as_of - timedelta(days=29), as_of - timedelta(days=59)
    sites: dict[str, dict] = {}
    for day, _s, ch, m, v in data.rows:
        if not ch.startswith("referral:") or m not in ("referral_sessions", "referral_signups"):
            continue
        r = sites.setdefault(ch.split(":", 1)[1], {"site": ch.split(":", 1)[1], "visites_30j": 0.0, "visites_30j_prec": 0.0,
                                                   "inscriptions_30j": 0.0, "visites_7j": 0.0, "avant": 0.0, "premiere_visite": day})
        r["premiere_visite"] = min(r["premiere_visite"], day)
        if m == "referral_sessions":
            if c0 <= day <= as_of:
                r["visites_30j"] += v
            elif p0 <= day < c0:
                r["visites_30j_prec"] += v
            if w0 <= day <= as_of:
                r["visites_7j"] += v
            elif w0 - timedelta(days=history) <= day < w0:
                r["avant"] += v
        elif c0 <= day <= as_of:
            r["inscriptions_30j"] += v
    out = []
    for r in sites.values():
        if not (r["visites_30j"] or r["visites_30j_prec"]):
            continue
        r["nouveau"] = r["avant"] == 0 and r["visites_7j"] > 0
        r["premiere_visite"] = r["premiere_visite"].isoformat()
        del r["avant"]
        out.append(r)
    out.sort(key=lambda r: -r["visites_30j"])
    return out


def _attribution(data: Data, as_of: date) -> list[dict]:
    start = as_of - timedelta(days=89)
    rows = {f: {"canal": f, "inscriptions": 0, "clients": 0, "depenses": 0.0} for f in FAMILIES}
    if data.has_accounts:
        for a in data.accounts:
            if a["created_at"] and start <= a["created_at"] <= as_of:
                f = family_from_utm(a.get("first_touch_source"), a.get("first_touch_medium"))
                rows[f]["inscriptions"] += 1
                if a["converted_at"]:
                    rows[f]["clients"] += 1
    else:
        for day, _s, ch, m, v in data.rows:
            if m == "web_signups" and start <= day <= as_of:
                rows[SITE_FAMILY.get(ch, "Direct / autre")]["inscriptions"] += v
    for day, _s, ch, m, v in data.rows:
        if m == "spend" and ch in SPEND_FAMILY and start <= day <= as_of:
            rows[SPEND_FAMILY[ch]]["depenses"] += v
    out = []
    for r in rows.values():
        r["depenses"] = round(r["depenses"], 2)
        r["cout_par_client"] = round(r["depenses"] / r["clients"], 2) if r["clients"] and r["depenses"] else None
        r["cout_par_inscription"] = round(r["depenses"] / r["inscriptions"], 2) if r["inscriptions"] and r["depenses"] else None
        if r["inscriptions"] or r["depenses"]:
            out.append(r)
    return out


def _weekly(data: Data, as_of: date, weeks: int = 13) -> dict:
    ends = [as_of - timedelta(days=7 * i) for i in reversed(range(weeks))]
    labels = [e.isoformat() for e in ends]

    def wk(series):
        return [round(sum(v for d, v in series.items() if e - timedelta(days=6) <= d <= e), 2) for e in ends]

    fam_vis = defaultdict(lambda: defaultdict(float))
    fam_of = {"google_ads": "Google Ads", "meta_ads": "Meta", "instagram": "Meta", "facebook_organique": "Meta", "facebook": "Meta", "linkedin_ads": "LinkedIn",
              "linkedin_organique": "LinkedIn", "seo": "SEO"}
    for day, _s, ch, m, v in data.rows:
        if m == "impressions" or (ch == "instagram" and m == "reach"):
            fam_vis[fam_of.get(ch, "Autres")][day] += v
    order = ["Google Ads", "Meta", "LinkedIn", "SEO", "Autres"]
    geo = data.series("ai_mention_rate", "geo")
    mrr = data.series("mrr", "facturation")
    return {
        "semaines": labels,
        "visibilite": {f: wk(fam_vis[f]) for f in order if f in fam_vis},
        "visites": wk(data.series("sessions")),
        "inscriptions": wk(data.series("signups")),
        "demos": wk(data.series("demos_booked")),
        "conversions": wk(data.series("trial_conversions")) if data.has_accounts else None,
        "resiliations": wk(data.series("churned")) if data.has_accounts else None,
        "geo_taux": [next((geo[d] for d in sorted(geo, reverse=True) if e - timedelta(days=6) <= d <= e), None) for e in ends],
        "mrr": [next((mrr[d] for d in sorted(mrr, reverse=True) if d <= e), None) for e in ends],
    }


def _lists(data: Data, as_of: date, rules: dict) -> dict:
    def row(a, **extra):
        return {"compte": a.get("name") or a.get("email") or a["account_id"], "email": a.get("email"),
                "plan": a.get("plan"), "canal": family_from_utm(a.get("first_touch_source"), a.get("first_touch_medium")),
                **extra}

    hot, stalled, risk, renew = [], [], [], []
    r_hot, r_st, r_risk, r_ren = rules["essai_chaud"], rules["essai_bloque"], rules["risque_churn"], rules["renouvellement"]
    for a in data.accounts:
        if is_trial(a, as_of) and a["created_at"]:
            day = (as_of - a["created_at"]).days + 1
            usage = a.get("usage_7d") or 0
            day = min(day, 15)
            if day >= r_hot["jour_essai_min"] and usage >= r_hot["usage_7j_min"]:
                hot.append(row(a, jour_essai=day, usage_7j=usage, fin_essai=str(a["trial_ends_at"] or "")))
            if day > r_st["jours_depuis_inscription"] and not a["activated_at"]:
                stalled.append(row(a, jour_essai=day, inscrit_le=str(a["created_at"])))
        if is_paying(a, as_of):
            prev, cur = a.get("usage_prev_30d") or 0, a.get("usage_30d") or 0
            if prev >= r_risk["usage_prev_30j_min"] and cur <= prev * (1 - r_risk["baisse_usage_pct"] / 100):
                risk.append(row(a, usage_30j=cur, usage_30j_precedents=prev, baisse_pct=round(100 * (prev - cur) / prev), mrr=a.get("mrr")))
            if a["contract_end_at"] and as_of <= a["contract_end_at"] <= as_of + timedelta(days=r_ren["jours_avant_echeance"]):
                renew.append(row(a, echeance=str(a["contract_end_at"]), mrr=a.get("mrr")))
    hot.sort(key=lambda r: -r["usage_7j"])
    stalled.sort(key=lambda r: -r["jour_essai"])
    risk.sort(key=lambda r: -(r.get("mrr") or 0))
    renew.sort(key=lambda r: r["echeance"])
    return {"essais_chauds": hot, "essais_bloques": stalled, "clients_a_risque": risk, "renouvellements": renew}


def _sources(data: Data) -> list[dict]:
    from .collectors import ALL_COLLECTORS

    last = {}
    for r in data.runs:
        last.setdefault(r["collector"], r)
    out = []
    for cls in ALL_COLLECTORS:
        c = cls()
        r = last.get(c.name)
        out.append({"id": c.name, "nom": c.label, "configuree": c.enabled(),
                    "dernier_statut": r["status"] if r else None, "derniere_collecte": r["run_at"] if r else None,
                    "message": (r["message"] or "")[:300] if r else None})
    return out
