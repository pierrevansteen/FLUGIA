"""Interface de pilotage Flugia (Streamlit) : consultation en direct de la base, avec filtres.

Lancer en local :   streamlit run app_pilotage.py
En ligne :          Streamlit Community Cloud, fichier principal « tracking/app_pilotage.py »,
                    secret DATABASE_URL (idéalement un utilisateur Postgres en lecture seule).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from flugia_tracking import brand
from flugia_tracking.kpis import AI_SOURCES, SITE_FAMILY, family_from_utm, label

ROOT = Path(__file__).resolve().parent
C = brand.COLORS
FAMILY_COLORS = {**brand.SERIES, "GEO": "#7FA6F5", "Referral": "#9AA5B1", "Direct / autre": "#C9D3DF", "Site": "#9AA5B1"}

# Palette de marque (ordre fixe) pour tous les graphiques
px.defaults.color_discrete_sequence = ["#1334CB", "#4CC9F0", "#008CFC", "#4361EE", "#7FA6F5", "#0E7C9A", "#9AA5B1",
                                       "#2A2F8F", "#86D9F5", "#C9D3DF"]

st.set_page_config(page_title="Pilotage Flugia", page_icon=str(ROOT / "assets" / "logo-flugia.svg"), layout="wide")


# ------------------------------------------------------------------ accès aux données
def _database_url() -> str:
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    return os.environ.get("DATABASE_URL", "")


@st.cache_data(ttl=600, show_spinner="Chargement des données…")
def load(db_url: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from flugia_tracking.db import DB

    db = DB(db_url)
    m = pd.DataFrame(db.query("SELECT day, source, channel, metric, value FROM metrics_daily"))
    a = pd.DataFrame(db.query("SELECT * FROM accounts"))
    runs = pd.DataFrame(db.query("SELECT run_at, collector, status, rows_written, message FROM collector_runs"))
    log = pd.DataFrame(db.query("SELECT alert_key, first_sent_at, last_sent_at, payload FROM alerts_log"))
    db.close()
    if not m.empty:
        m["day"] = pd.to_datetime(m["day"].astype(str).str[:10])
        m["value"] = m["value"].astype(float)
        m["canal"] = m["channel"].map(label)
        m["famille"] = m["channel"].map(family_of)
    for col in ("created_at", "trial_ends_at", "activated_at", "converted_at", "churned_at", "contract_end_at"):
        if col in a:
            a[col] = pd.to_datetime(a[col], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    if not a.empty:
        txt = lambda v: v if isinstance(v, str) else None  # noqa: E731  (NaN pandas -> None)
        a["canal"] = [family_from_utm(txt(s), txt(m_)) for s, m_ in zip(a["first_touch_source"], a["first_touch_medium"])]
    return m, a, runs, log


def family_of(ch: str) -> str:
    if ch in ("google_ads",):
        return "Google Ads"
    if ch in ("meta_ads", "instagram", "facebook", "facebook_organique"):
        return "Meta"
    if ch.startswith("linkedin"):
        return "LinkedIn"
    if ch in ("seo", "seo_marque"):
        return "SEO"
    if ch == "geo" or ch.startswith("geo"):
        return "GEO"
    if ch.startswith("referral:"):
        return "Referral"
    if ch.startswith("site:"):
        return SITE_FAMILY.get(ch, "Site")
    if ch.startswith("manuel:"):
        return "Autres"
    return "Autres"


def visibility_rows(m: pd.DataFrame) -> pd.DataFrame:
    """Vues = impressions, ou portée quand la plateforme ne donne que ça (Instagram)."""
    return m[(m["metric"] == "impressions") | ((m["channel"] == "instagram") & (m["metric"] == "reach"))]


def period_bounds(preset: str, custom, last_day: date):
    if preset == "Personnalisée" and custom and len(custom) == 2:
        start, end = custom
    else:
        days = {"7 derniers jours": 7, "30 derniers jours": 30, "90 derniers jours": 90, "12 derniers mois": 365}[preset]
        end, start = last_day, last_day - timedelta(days=days - 1)
    length = (end - start).days + 1
    return pd.Timestamp(start), pd.Timestamp(end), pd.Timestamp(start - timedelta(days=length)), pd.Timestamp(start - timedelta(days=1))


def between(df, col, a, b):
    return df[(df[col] >= a) & (df[col] <= b)]


def pct(cur, prev):
    """Évolution en % ; signe ASCII pour que Streamlit oriente correctement la flèche."""
    if not prev:
        return None
    return f"{'+' if cur >= prev else '-'}{brand.num(abs(100 * (cur - prev) / prev), 0)} %"


def resample(df: pd.DataFrame, gran: str, by: str | None = None) -> pd.DataFrame:
    rule = {"Jour": "D", "Semaine": "W-SUN", "Mois": "MS"}[gran]
    keys = [pd.Grouper(key="day", freq=rule)] + ([by] if by else [])
    out = df.groupby(keys)["value"].sum().reset_index()
    return out


def style(fig, height=340, money=False, percent=False):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10), font=dict(family="Inter, sans-serif", size=12),
        legend=dict(orientation="h", y=1.08, x=0, title=None), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(font_family="Inter, sans-serif"), bargap=0.25,
    )
    fig.update_xaxes(title=None, showgrid=False, tickformat="%d/%m", hoverformat="%d/%m/%Y")
    fig.update_yaxes(title=None, gridcolor="rgba(150,160,175,0.18)", rangemode="tozero",
                     ticksuffix=" %" if percent else (" €" if money else ""))
    return fig


def kpi(col, title, cur, prev, fmt=brand.num, inverse=False, help_=None):
    d = pct(cur, prev) if prev is not None else None
    col.metric(title, fmt(cur) if cur is not None else "—", d, delta_color="inverse" if inverse else "normal", help=help_)


# ------------------------------------------------------------------ en-tête et filtres
st.logo(str(ROOT / "assets" / "logo-flugia.svg"), size="large")
st.markdown(
    f"""<style>
    h1, h2, h3 {{ letter-spacing: -0.01em; }}
    div[data-testid="stMetric"] {{ background: {C['bg_softer']}; border: 1px solid #E3EAF3; border-radius: 10px; padding: 10px 14px; }}
    @media (prefers-color-scheme: dark) {{ div[data-testid="stMetric"] {{ background: rgba(255,255,255,0.03); border-color: rgba(255,255,255,0.08); }} }}
    .bar {{ height: 4px; border-radius: 4px; background: {brand.GRADIENT}; margin: -6px 0 14px; }}
    </style>""",
    unsafe_allow_html=True,
)

db_url = _database_url()
m_all, accounts, runs, alert_log = load(db_url)
if m_all.empty:
    st.warning("La base ne contient encore aucune donnée. Lancez une collecte depuis GitHub Actions.")
    st.stop()

last_day = m_all["day"].max().date()
with st.sidebar:
    st.header("Filtres")
    preset = st.selectbox("Période", ["7 derniers jours", "30 derniers jours", "90 derniers jours", "12 derniers mois", "Personnalisée"], index=1)
    custom = st.date_input("Dates", value=(last_day - timedelta(days=29), last_day), max_value=last_day,
                           format="DD/MM/YYYY") if preset == "Personnalisée" else None
    gran = st.radio("Regrouper par", ["Jour", "Semaine", "Mois"], index=1, horizontal=True)
    families = sorted(m_all["famille"].unique())
    chosen = st.multiselect("Canaux", families, default=families)
    st.caption(f"Données jusqu'au {brand.day(last_day, True)} · comparaison avec la période précédente de même durée.")
    if st.button("Actualiser les données"):
        st.cache_data.clear()
        st.rerun()

start, end, pstart, pend = period_bounds(preset, custom, last_day)
m = m_all[m_all["famille"].isin(chosen)]
cur, prev = between(m, "day", start, end), between(m, "day", pstart, pend)

st.title("Pilotage commercial")
st.markdown(f"Du **{brand.day(start.date())}** au **{brand.day(end.date(), True)}**", help="Période sélectionnée dans le panneau de gauche")
st.markdown('<div class="bar"></div>', unsafe_allow_html=True)


def tot(df, metric, channel=None, prefix=None):
    d = df[df["metric"] == metric]
    if channel:
        d = d[d["channel"] == channel]
    if prefix:
        d = d[d["channel"].str.startswith(prefix)]
    return float(d["value"].sum())


def signups_series(a0, a1):
    if not accounts.empty:
        d = accounts[(accounts["created_at"] >= a0) & (accounts["created_at"] <= a1) & accounts["canal"].isin(chosen + ["Direct / autre"])]
        return float(len(d))
    return tot(between(m, "day", a0, a1), "web_signups")


tabs = st.tabs(["Vue d'ensemble", "Visibilité", "GEO (IA)", "Intérêt", "Acquisition & rétention", "Alertes", "Explorateur"])

# ------------------------------------------------------------------ 1. Vue d'ensemble
with tabs[0]:
    v_cur, v_prev = visibility_rows(cur)["value"].sum(), visibility_rows(prev)["value"].sum()
    c = st.columns(4)
    kpi(c[0], "Vues (tous canaux)", v_cur, v_prev)
    kpi(c[1], "Recherches « flugia »", tot(cur, "branded_searches"), tot(prev, "branded_searches"))
    kpi(c[2], "Visites du site", tot(cur, "sessions"), tot(prev, "sessions"))
    kpi(c[3], "Dépenses publicitaires", tot(cur, "spend"), tot(prev, "spend"), fmt=brand.eur, inverse=True)
    c = st.columns(4)
    kpi(c[0], "Inscriptions", signups_series(start, end), signups_series(pstart, pend),
        help_=None if not accounts.empty else "Événement GA4 d'inscription, en attendant la base de l'application")
    demos_cur = tot(cur, "demos_booked") or tot(cur, "web_demo_requests")
    demos_prev = tot(prev, "demos_booked") or tot(prev, "web_demo_requests")
    kpi(c[1], "Démos réservées", demos_cur, demos_prev)
    kpi(c[2], "Visites depuis les IA", tot(cur, "ai_sessions"), tot(prev, "ai_sessions"))
    geo = m_all[(m_all["metric"] == "ai_mention_rate") & (m_all["channel"] == "geo")].sort_values("day")
    g_cur = geo["value"].iloc[-1] if len(geo) else None
    g_prev = geo["value"].iloc[-2] if len(geo) > 1 else None
    c[3].metric("Citée par les IA (dernière mesure)", brand.pct(g_cur, 0) if g_cur is not None else "—",
                f"{'+' if g_cur >= g_prev else '-'}{brand.num(abs(g_cur - g_prev), 0)} pt" if g_prev is not None else None)

    vis = resample(visibility_rows(cur), gran, "famille")
    if not vis.empty:
        fig = px.bar(vis, x="day", y="value", color="famille", color_discrete_map=FAMILY_COLORS,
                     labels={"day": "", "value": "Vues", "famille": "Canal"})
        st.subheader("Vues de la marque par canal")
        st.plotly_chart(style(fig), use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Visites du site")
        s = resample(cur[cur["metric"] == "sessions"], gran)
        if not s.empty:
            st.plotly_chart(style(px.bar(s, x="day", y="value", color_discrete_sequence=[C["main"]], labels={"value": "Visites", "day": ""}), 280), use_container_width=True)
    with right:
        st.subheader("Inscriptions et démos")
        frames = []
        sg = cur[cur["metric"] == "web_signups"] if accounts.empty else pd.DataFrame(
            {"day": accounts["created_at"], "value": 1.0}).dropna().pipe(lambda d: between(d, "day", start, end))
        if not sg.empty:
            frames.append(resample(sg, gran).assign(serie="Inscriptions"))
        dm = cur[cur["metric"].isin(["demos_booked", "web_demo_requests"])]
        if not dm.empty:
            frames.append(resample(dm, gran).assign(serie="Démos réservées"))
        if frames:
            df = pd.concat(frames)
            fig = px.bar(df, x="day", y="value", color="serie", barmode="group",
                         color_discrete_map={"Inscriptions": C["deep"], "Démos réservées": C["cyan"]}, labels={"value": "", "day": ""})
            st.plotly_chart(style(fig, 280), use_container_width=True)
        else:
            st.info("Pas encore d'inscriptions ni de démos mesurées sur la période.")

# ------------------------------------------------------------------ 2. Visibilité
with tabs[1]:
    metric_label = st.selectbox("Indicateur", ["Vues", "Clics", "Dépenses", "Coût pour 1 000 vues", "Taux de clic"], key="vis_metric")
    imp = visibility_rows(cur)
    if metric_label == "Vues":
        df = resample(imp, gran, "canal")
        fig = px.bar(df, x="day", y="value", color="canal", labels={"value": "Vues", "day": "", "canal": "Canal"})
    elif metric_label in ("Clics", "Dépenses"):
        key = {"Clics": "clicks", "Dépenses": "spend"}[metric_label]
        df = resample(cur[cur["metric"] == key], gran, "canal")
        fig = px.bar(df, x="day", y="value", color="canal", labels={"value": metric_label, "day": "", "canal": "Canal"})
    else:
        num_key, den_key, mult = ("spend", "impressions", 1000) if metric_label.startswith("Coût") else ("clicks", "impressions", 100)
        a_ = resample(cur[cur["metric"] == num_key], gran, "canal").rename(columns={"value": "num"})
        b_ = resample(cur[cur["metric"] == den_key], gran, "canal").rename(columns={"value": "den"})
        df = a_.merge(b_, on=["day", "canal"])
        df = df[df["den"] > 0].assign(value=lambda d: mult * d["num"] / d["den"])
        fig = px.line(df, x="day", y="value", color="canal", markers=True, labels={"value": metric_label, "day": "", "canal": "Canal"})
    if df.empty:
        st.info("Pas de données pour cet indicateur sur la période.")
    else:
        st.plotly_chart(style(fig, 380, money=metric_label.startswith(("Dépenses", "Coût")), percent=metric_label == "Taux de clic"), use_container_width=True)

    st.subheader("Détail par canal")
    rows = []
    for ch in sorted(set(visibility_rows(m)["channel"])):
        vc = visibility_rows(cur[cur["channel"] == ch])["value"].sum()
        vp = visibility_rows(prev[prev["channel"] == ch])["value"].sum()
        clicks = tot(cur, "clicks", ch)
        spend = tot(cur, "spend", ch)
        rows.append({"Canal": label(ch), "Vues": vc, "Période précédente": vp, "Évolution": pct(vc, vp) or "—",
                     "Clics": clicks, "Taux de clic": round(100 * clicks / vc, 2) if vc else None,
                     "Dépenses (€)": spend or None, "Coût / 1 000 vues (€)": round(1000 * spend / vc, 2) if vc and spend else None})
    detail = pd.DataFrame(rows)
    for col_ in ("Taux de clic", "Dépenses (€)", "Coût / 1 000 vues (€)"):
        detail[col_] = pd.to_numeric(detail[col_], errors="coerce")
    st.dataframe(detail.sort_values("Vues", ascending=False), hide_index=True, use_container_width=True,
                 column_config={"Vues": st.column_config.NumberColumn(format="localized"),
                                "Période précédente": st.column_config.NumberColumn(format="localized"),
                                "Taux de clic": st.column_config.NumberColumn(format="%.2f %%")})

# ------------------------------------------------------------------ 3. GEO
with tabs[2]:
    geo_all = m_all[m_all["channel"].str.startswith("geo")]
    rate = geo_all[(geo_all["metric"] == "ai_mention_rate") & (geo_all["channel"] == "geo")].sort_values("day")
    if rate.empty:
        st.info("Pas encore de mesure GEO. La mesure est faite chaque lundi : Claude pose les questions de config/geo_prompts.yaml et vérifie si Flugia est citée.")
    else:
        st.subheader("Part des questions où une IA cite Flugia")
        fig = px.line(rate, x="day", y="value", markers=True, color_discrete_sequence=[C["cta"]], labels={"value": "", "day": ""})
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(style(fig, 300, percent=True), use_container_width=True)
        q = geo_all[(geo_all["metric"] == "ai_mentioned")].copy()
        q["question"] = q["channel"].str.split(":", n=1).str[1].str.replace("_", " ")
        pivot = q.pivot_table(index="question", columns="day", values="value", aggfunc="max").sort_index(axis=1).iloc[:, -12:]
        pivot.columns = [d_.strftime("%d/%m") for d_ in pivot.columns]
        st.subheader("Historique par question (12 dernières mesures)")
        st.dataframe(pivot.astype(object).map(lambda v: "✅ citée" if v == 1 else ("—" if v == 0 else "")), use_container_width=True)
        comp = geo_all[geo_all["metric"] == "ai_mentions"]
        comp = comp[comp["channel"].str.startswith("geo_concurrent:")]
        if not comp.empty:
            comp = comp.assign(concurrent=comp["channel"].str.split(":", n=1).str[1])
            st.subheader("Concurrents cités par les IA")
            fig = px.bar(comp.groupby(["day", "concurrent"])["value"].sum().reset_index(), x="day", y="value", color="concurrent",
                         barmode="group", labels={"value": "Nombre de réponses", "day": ""})
            st.plotly_chart(style(fig, 300), use_container_width=True)
    ai = resample(cur[cur["metric"] == "ai_sessions"], gran)
    if not ai.empty:
        st.subheader("Visites venant des assistants IA")
        st.plotly_chart(style(px.bar(ai, x="day", y="value", color_discrete_sequence=[C["cta"]], labels={"value": "Visites", "day": ""}), 260), use_container_width=True)

# ------------------------------------------------------------------ 4. Intérêt
with tabs[3]:
    st.subheader("Visites par canal")
    s = cur[cur["metric"] == "sessions"]
    if not s.empty:
        fig = px.bar(resample(s, gran, "canal"), x="day", y="value", color="canal", labels={"value": "Visites", "day": "", "canal": "Canal"})
        st.plotly_chart(style(fig, 340), use_container_width=True)
        t = s.groupby("canal")["value"].sum().rename("Visites").reset_index()
        tp = prev[prev["metric"] == "sessions"].groupby("canal")["value"].sum().rename("Période précédente").reset_index()
        t = t.merge(tp, on="canal", how="outer").fillna(0).rename(columns={"canal": "Canal"})
        t["Évolution"] = [pct(a_, b_) or "—" for a_, b_ in zip(t["Visites"], t["Période précédente"])]
        sg = cur[cur["metric"] == "web_signups"].groupby("canal")["value"].sum().rename("Inscriptions (GA4)").reset_index().rename(columns={"canal": "Canal"})
        t = t.merge(sg, on="Canal", how="left").fillna({"Inscriptions (GA4)": 0})
        t["Taux d'inscription"] = (100 * t["Inscriptions (GA4)"] / t["Visites"].where(t["Visites"] > 0)).round(2)
        st.dataframe(t.sort_values("Visites", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"Taux d'inscription": st.column_config.NumberColumn(format="%.2f %%")})

    st.subheader("Sites référents (presse, partenaires, annuaires)")
    r = m[(m["metric"].isin(["referral_sessions", "referral_signups"]))].copy()
    if r.empty:
        st.info("Aucune visite venant de sites tiers pour l'instant.")
    else:
        r["site"] = r["channel"].str.split(":", n=1).str[1]
        rc = between(r, "day", start, end).pivot_table(index="site", columns="metric", values="value", aggfunc="sum").fillna(0)
        rp = between(r, "day", pstart, pend).pivot_table(index="site", columns="metric", values="value", aggfunc="sum").fillna(0)
        first = r[r["metric"] == "referral_sessions"].groupby("site")["day"].min()
        out = pd.DataFrame({
            "Visites": rc.get("referral_sessions", pd.Series(dtype=float)),
            "Période précédente": rp.get("referral_sessions", pd.Series(dtype=float)),
            "Inscriptions": rc.get("referral_signups", pd.Series(dtype=float)),
        }).fillna(0)
        out["Première visite"] = first.dt.strftime("%d/%m/%Y")
        out = out[(out["Visites"] > 0) | (out["Période précédente"] > 0)].sort_values("Visites", ascending=False)
        st.dataframe(out.reset_index().rename(columns={"site": "Site"}), hide_index=True, use_container_width=True)

    st.subheader("Démos")
    d = cur[cur["metric"].isin(["demos_booked", "demos_held", "web_demo_requests"])]
    if d.empty:
        st.info("Pas de démos mesurées sur la période (Google Agenda ou pages de remerciement GA4).")
    else:
        d = d.assign(serie=d["metric"].map({"demos_booked": "Réservées (agenda)", "demos_held": "Tenues", "web_demo_requests": "Demandes (site)"}))
        st.plotly_chart(style(px.bar(resample(d, gran, "serie"), x="day", y="value", color="serie", barmode="group",
                                     labels={"value": "", "day": "", "serie": ""}), 280), use_container_width=True)

# ------------------------------------------------------------------ 5. Acquisition & rétention
with tabs[4]:
    if accounts.empty:
        st.info("Cet onglet s'activera quand la base de l'application (ou la facturation) sera raccordée : conversion après l'essai, "
                "clients par canal, churn, prolongations et listes de comptes à traiter.")
        mrr = m_all[m_all["metric"] == "mrr"].sort_values("day")
        if not mrr.empty:
            st.metric("MRR", brand.eur(mrr["value"].iloc[-1]))
    else:
        a = accounts
        a_cur = a[(a["created_at"] >= start) & (a["created_at"] <= end)]
        ended = a[(a["trial_ends_at"] >= start) & (a["trial_ends_at"] <= end)]
        ended_p = a[(a["trial_ends_at"] >= pstart) & (a["trial_ends_at"] <= pend)]
        conv = 100 * ended["converted_at"].notna().mean() if len(ended) else None
        conv_p = 100 * ended_p["converted_at"].notna().mean() if len(ended_p) else None
        paying = a[a["status"].isin(["active", "past_due"])]
        churned = a[(a["churned_at"] >= start) & (a["churned_at"] <= end) & a["converted_at"].notna()]
        c = st.columns(4)
        c[0].metric("Inscriptions", brand.num(len(a_cur)))
        c[1].metric("Conversion après l'essai", brand.pct(conv, 0) if conv is not None else "—",
                    f"{'+' if (conv or 0) >= (conv_p or 0) else '−'}{brand.num(abs((conv or 0) - (conv_p or 0)), 1)} pt" if conv is not None and conv_p is not None else None)
        c[2].metric("Clients payants", brand.num(len(paying)), help="Aujourd'hui")
        c[3].metric("Résiliations sur la période", brand.num(len(churned)), delta_color="inverse")

        st.subheader("Origine des inscriptions et des clients")
        g = a_cur.groupby("canal").agg(Inscriptions=("account_id", "count"), Clients=("converted_at", "count")).reset_index()
        spend = cur[cur["metric"] == "spend"].groupby("famille")["value"].sum()
        g["Taux"] = (100 * g["Clients"] / g["Inscriptions"]).round(1)
        g["Dépenses (€)"] = g["canal"].map(spend).fillna(0).round(0)
        g["Coût / client (€)"] = (g["Dépenses (€)"] / g["Clients"].where(g["Clients"] > 0)).round(0)
        st.dataframe(g.rename(columns={"canal": "Canal"}).sort_values("Inscriptions", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"Taux": st.column_config.NumberColumn(format="%.1f %%")})

        st.subheader("Comptes")
        f1, f2, f3 = st.columns(3)
        statuses = sorted(a["status"].dropna().unique())
        sel_status = f1.multiselect("Statut", statuses, default=[s_ for s_ in statuses if s_ in ("trial", "active")] or statuses)
        sel_canal = f2.multiselect("Origine", sorted(a["canal"].unique()), default=sorted(a["canal"].unique()))
        search = f3.text_input("Recherche (nom, e-mail)")
        view = a[a["status"].isin(sel_status) & a["canal"].isin(sel_canal)]
        if search:
            mask = view["name"].fillna("").str.contains(search, case=False) | view.get("email", pd.Series("", index=view.index)).fillna("").str.contains(search, case=False)
            view = view[mask]
        cols = [c_ for c_ in ["name", "email", "status", "plan", "canal", "created_at", "trial_ends_at", "activated_at", "converted_at",
                              "churned_at", "mrr", "usage_7d", "usage_30d", "usage_prev_30d"] if c_ in view]
        st.dataframe(view[cols].sort_values("created_at", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"name": "Compte", "email": "E-mail", "status": "Statut", "plan": "Offre", "canal": "Origine",
                                    "created_at": st.column_config.DateColumn("Inscription", format="DD/MM/YYYY"),
                                    "trial_ends_at": st.column_config.DateColumn("Fin d'essai", format="DD/MM/YYYY"),
                                    "activated_at": st.column_config.DateColumn("Activation", format="DD/MM/YYYY"),
                                    "converted_at": st.column_config.DateColumn("Conversion", format="DD/MM/YYYY"),
                                    "churned_at": st.column_config.DateColumn("Résiliation", format="DD/MM/YYYY"),
                                    "mrr": st.column_config.NumberColumn("MRR (€)"), "usage_7d": "Crédits 7 j",
                                    "usage_30d": "Crédits 30 j", "usage_prev_30d": "Crédits 30 j préc."})

# ------------------------------------------------------------------ 6. Alertes
with tabs[5]:
    from flugia_tracking import alerts as alerts_mod, kpis as kpis_mod
    from flugia_tracking.db import DB

    @st.cache_data(ttl=600, show_spinner=False)
    def current_alerts(db_url: str, as_of: date):
        db = DB(db_url)
        snap = kpis_mod.compute(db, as_of)
        out = [a.__dict__ for a in alerts_mod.evaluate(snap)]
        db.close()
        return out

    al = current_alerts(db_url, end.date())
    st.caption(f"Points d'attention calculés au {brand.day(end.date(), True)} (fin de la période choisie).")
    levels = st.segmented_control("Priorité", ["haute", "moyenne", "info"], default=["haute", "moyenne", "info"],
                                  selection_mode="multi", format_func=lambda s_: brand.SEVERITY[s_]["label"])
    shown = [x for x in al if x["severite"] in (levels or [])]
    if not shown:
        st.success("Aucun point d'attention pour ces critères.")
    for x in shown:
        icon = {"haute": "🔴", "moyenne": "🟠", "info": "🔵"}[x["severite"]]
        with st.container(border=True):
            st.markdown(f"{icon} **{x['titre']}**  \n<small>{brand.SEVERITY[x['severite']]['label']} · {x['etape']}</small>", unsafe_allow_html=True)
            st.write(x["detail"])
            st.markdown(f"**Action recommandée :** {x['action']}")
    if not alert_log.empty:
        with st.expander("Historique des alertes envoyées"):
            import json as _json
            h = alert_log.copy()
            h["Alerte"] = [(_json.loads(p) or {}).get("titre", k) if p else k for p, k in zip(h["payload"], h["alert_key"])]
            h["Dernier envoi"] = pd.to_datetime(h["last_sent_at"], utc=True).dt.strftime("%d/%m/%Y %H:%M")
            st.dataframe(h[["Alerte", "Dernier envoi"]].sort_values("Dernier envoi", ascending=False), hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ 7. Explorateur
with tabs[6]:
    st.caption("Croisez librement n'importe quelle mesure collectée, par canal et par période, puis exportez le résultat.")
    e1, e2, e3 = st.columns([2, 2, 1])
    metrics = sorted(m["metric"].unique())
    sel_m = e1.multiselect("Mesures", metrics, default=[x for x in ["impressions", "sessions"] if x in metrics])
    chans = sorted(m[m["metric"].isin(sel_m)]["canal"].unique())
    sel_c = e2.multiselect("Canaux", chans, default=chans)
    split = e3.selectbox("Découper par", ["Canal", "Mesure", "Aucun"])
    ex = cur[cur["metric"].isin(sel_m) & cur["canal"].isin(sel_c)]
    if ex.empty:
        st.info("Aucune donnée pour cette sélection.")
    else:
        by = {"Canal": "canal", "Mesure": "metric", "Aucun": None}[split]
        r_ = resample(ex, gran, by)
        fig = px.line(r_, x="day", y="value", color=by, markers=True, labels={"value": "", "day": "", "canal": "Canal", "metric": "Mesure"})
        st.plotly_chart(style(fig, 360), use_container_width=True)
        table = ex.pivot_table(index=pd.Grouper(key="day", freq={"Jour": "D", "Semaine": "W-SUN", "Mois": "MS"}[gran]),
                               columns=["metric", "canal"], values="value", aggfunc="sum").fillna(0)
        table.index = table.index.strftime("%d/%m/%Y")
        st.dataframe(table, use_container_width=True)
        st.download_button("Télécharger en CSV", ex.drop(columns=["famille"]).to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           file_name=f"flugia-export-{start.date()}-{end.date()}.csv", mime="text/csv")

# ------------------------------------------------------------------ pied de page
with st.sidebar:
    st.divider()
    if not runs.empty:
        last = runs.sort_values("run_at").groupby("collector").tail(1)
        ko = last[last["status"] == "erreur"]
        st.caption(f"Dernière collecte : {pd.to_datetime(runs['run_at'].max()).strftime('%d/%m/%Y %H:%M')} UTC")
        if not ko.empty:
            st.error("Sources en erreur : " + ", ".join(ko["collector"]))
