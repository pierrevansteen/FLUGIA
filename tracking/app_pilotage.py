"""Interface de pilotage FLUGIA (Streamlit) : tunnel Visibilité > Intérêt > Conversion > Rétention, alertes et objectifs.

Lancer en local :   streamlit run app_pilotage.py
En ligne :          Streamlit Community Cloud, fichier principal « tracking/app_pilotage.py »,
                    secret DATABASE_URL (utilisateur Postgres en lecture seule, avec écriture sur les tables ui_*).
"""
from __future__ import annotations

import html
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

from flugia_tracking import brand
from flugia_tracking import objectives as obj_mod
from flugia_tracking.kpis import BEHAVIOR, family_from_utm, label

ROOT = Path(__file__).resolve().parent
C = brand.COLORS
esc = lambda t: html.escape(str(t))  # noqa: E731

st.set_page_config(page_title="Pilotage FLUGIA", page_icon=str(ROOT / "assets" / "logo-flugia.svg"), layout="wide",
                   initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ référentiels
FAMILY_ORDER = ["Google Ads", "Meta", "LinkedIn", "SEO", "GEO", "Referral", "Direct / autre"]
FAMILY_LABEL = {"Referral": "Sites référents", "GEO": "GEO (IA)"}
FAMILY_COLORS = {"Google Ads": "#1334CB", "Meta": "#4CC9F0", "LinkedIn": "#008CFC", "SEO": "#4361EE", "GEO": "#7B61FF",
                 "Referral": "#0E7C9A", "Direct / autre": "#C9D3DF"}
AD_COLORS = {"Google Ads": "#1334CB", "Meta Ads": "#4CC9F0", "LinkedIn Ads": "#008CFC"}
STAGE_COLORS = {"Visibilité": "#4361EE", "Intérêt": "#008CFC", "Conversion": "#4CC9F0", "Rétention": "#1334CB", "Système": "#6B7280"}
SEQ = px.defaults.color_discrete_sequence = ["#4361EE", "#4CC9F0", "#1334CB", "#008CFC", "#7B61FF", "#0E7C9A", "#86D9F5", "#9AA5B1",
                                       "#2A2F8F", "#C9D3DF"]

METRIC_NAMES = {
    "impressions": "Vues (impressions)", "reach": "Portée", "clicks": "Clics", "spend": "Dépenses publicitaires (€)",
    "ad_conversions": "Conversions publicitaires", "engagement": "Interactions", "sessions": "Visites du site",
    "visitors": "Visiteurs", "web_signups": "Inscriptions (site)", "web_demo_requests": "Démos réservées (site)",
    "demos_booked": "Démos réservées", "demos_held": "Démos tenues", "demos_cancelled": "Démos annulées",
    "branded_searches": "Recherches « flugia »", "ai_sessions": "Visites depuis les IA", "ai_mention_rate": "Citation par les IA (%)",
    "ai_mentioned": "Question citée par l'IA (0/1)", "ai_mentions": "Mentions dans les réponses IA", "ai_prompts_tested": "Questions testées (GEO)",
    "referral_sessions": "Visites depuis des sites référents", "referral_signups": "Inscriptions depuis des sites référents",
    "mrr": "Revenu mensuel récurrent (€)", "paying_customers": "Clients payants", "payment_failures": "Paiements échoués",
    **{m: f"Clarity · {lbl}" + (f" ({u})" if u else "") for lbl, m, _h, _g, u in BEHAVIOR},
}
METRIC_STAGE = {
    **dict.fromkeys(["impressions", "reach", "clicks", "spend", "engagement", "branded_searches", "ai_mention_rate", "ai_mentioned",
                     "ai_mentions", "ai_prompts_tested"], "Visibilité"),
    **dict.fromkeys(["sessions", "visitors", "ai_sessions", "referral_sessions"], "Intérêt"),
    **dict.fromkeys(["web_signups", "web_demo_requests", "demos_booked", "demos_held", "demos_cancelled", "ad_conversions",
                     "referral_signups"], "Conversion"),
    **dict.fromkeys(["mrr", "paying_customers", "payment_failures"], "Rétention"),
}
# Métriques à moyenner (taux, stocks) plutôt qu'à additionner
AVG_METRICS = {"ai_mention_rate", "mrr", "paying_customers"} | {m for _l, m, how, _g, _u in BEHAVIOR if how == "avg"}

STAGE_ACTION = {
    "Visibilité": "Augmenter la diffusion : renforcer le budget des campagnes au meilleur coût pour 1 000 vues et publier plus souvent sur LinkedIn.",
    "Intérêt": "Transformer les vues en visites : appels à l'action plus visibles dans les publicités, liens vers le site dans chaque publication, contenus SEO.",
    "Conversion": "Transformer les visites en inscriptions : mettre l'essai gratuit en avant sur les pages les plus visitées et tester le formulaire de bout en bout.",
    "Rétention": "Transformer les essais en clients : appeler les essais actifs avant le 10e jour et proposer une session d'accompagnement aux essais non activés.",
}


# ------------------------------------------------------------------ accès aux données
def _database_url() -> str:
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    return os.environ.get("DATABASE_URL", "")


def family_of(ch: str) -> str | None:
    """Famille de canal ; None = donnée non attribuable à un canal (toujours affichée, quel que soit le filtre)."""
    if ch == "google_ads" or ch == "site:google_ads":
        return "Google Ads"
    if ch in ("meta_ads", "instagram", "facebook", "facebook_organique", "site:social_payant"):
        return "Meta"
    if ch.startswith("linkedin") or ch == "site:social_organique":
        return "LinkedIn"
    if ch in ("seo", "seo_marque", "site:seo"):
        return "SEO"
    if ch.startswith("geo") or ch == "site:geo_ia":
        return "GEO"
    if ch.startswith("referral:") or ch == "site:referral":
        return "Referral"
    if ch.startswith("site:"):
        return "Direct / autre"
    return None


@st.cache_data(ttl=600, show_spinner="Chargement des données…")
def load(db_url: str):
    from flugia_tracking.db import DB

    db = DB(db_url, read_only=True)
    m = pd.DataFrame(db.query("SELECT day, source, channel, metric, value FROM metrics_daily"))
    a = pd.DataFrame(db.query("SELECT * FROM accounts"))
    runs = pd.DataFrame(db.query("SELECT run_at, collector, status, rows_written, message FROM collector_runs"))
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
        txt = lambda v: v if isinstance(v, str) else None  # noqa: E731
        a["famille"] = [family_from_utm(txt(s), txt(m_)) for s, m_ in zip(a["first_touch_source"], a["first_touch_medium"])]
    return m, a, runs


def _write_db():
    from flugia_tracking.db import DB

    return DB(DB_URL, create_tables=False)


@st.cache_data(ttl=60, show_spinner=False)
def load_objectives(db_url: str) -> dict:
    from flugia_tracking.db import DB

    db = DB(db_url, read_only=True)
    try:
        return obj_mod.load(db)
    finally:
        db.close()


@st.cache_data(ttl=60, show_spinner=False)
def load_done(db_url: str) -> dict[str, str]:
    from flugia_tracking.db import DB

    db = DB(db_url, read_only=True)
    try:
        return {r["alert_key"]: str(r["done_at"]) for r in db.query("SELECT alert_key, done_at FROM ui_alertes_traitees")}
    except Exception:
        return {}
    finally:
        db.close()


@st.cache_data(ttl=600, show_spinner="Calcul des alertes…")
def compute_alerts(db_url: str, as_of: date, objectives: tuple):
    from flugia_tracking import alerts as alerts_mod, kpis as kpis_mod
    from flugia_tracking.db import DB

    db = DB(db_url, read_only=True)
    try:
        snap = kpis_mod.compute(db, as_of)
        return [a.__dict__ for a in alerts_mod.evaluate(snap, dict(objectives))]
    finally:
        db.close()


def viewer() -> str | None:
    try:
        return st.user.get("email")
    except Exception:
        return None


# ------------------------------------------------------------------ calculs
def between(df, col, a, b):
    return df[(df[col] >= a) & (df[col] <= b)]


def visibility_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Vues = impressions, ou portée quand la plateforme ne donne que ça (Instagram)."""
    return df[(df["metric"] == "impressions") | ((df["channel"] == "instagram") & (df["metric"] == "reach"))]


def tot(df, metric, channel=None):
    d = df[df["metric"] == metric]
    if channel:
        d = d[d["channel"] == channel]
    return float(d["value"].sum())


def ratio(a, b, mult=100):
    return mult * a / b if b else None


def resample(df: pd.DataFrame, by: str | None = None, how: str = "sum") -> pd.DataFrame:
    rule = {"Jour": "D", "Semaine": "W-MON", "Mois": "MS"}[GRAN]
    keys = [pd.Grouper(key="day", freq=rule, label="left", closed="left")] + ([by] if by else [])
    return df.groupby(keys)["value"].agg(how).reset_index()


def period_bounds(preset: str, custom, last_day: date, compare: str):
    if preset == "Personnalisée" and custom and len(custom) == 2:
        start, end = custom
    elif preset == "Ce mois-ci":
        start, end = last_day.replace(day=1), last_day
    elif preset == "Mois dernier":
        end = last_day.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif preset == "Cette année":
        start, end = last_day.replace(month=1, day=1), last_day
    else:
        days = {"7 derniers jours": 7, "14 derniers jours": 14, "28 derniers jours": 28, "30 derniers jours": 30,
                "90 derniers jours": 90, "12 derniers mois": 365}[preset]
        end, start = last_day, last_day - timedelta(days=days - 1)
    length = (end - start).days + 1
    if compare == "Même période l'an dernier":
        pstart, pend = pd.Timestamp(start) - pd.DateOffset(years=1), pd.Timestamp(end) - pd.DateOffset(years=1)
    else:
        pstart, pend = pd.Timestamp(start - timedelta(days=length)), pd.Timestamp(start - timedelta(days=1))
    return pd.Timestamp(start), pd.Timestamp(end), pstart, pend


def fmt_period(a: pd.Timestamp, b: pd.Timestamp) -> str:
    return f"{brand.day(a.date(), a.year != b.year)} – {brand.day(b.date(), True)}"


# ------------------------------------------------------------------ composants visuels
FONT = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'


def style(fig, height=320, money=False, percent=False, legend=True):
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=30 if legend else 8, b=0), font=dict(family=FONT, size=12, color="#6B7280"),
        legend=dict(orientation="h", y=1.1, x=0, title=None, font=dict(size=12)), showlegend=legend,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(font_family=FONT, bgcolor="white", bordercolor="rgba(0,0,0,0.08)", font_color="#3F3F3F"),
        bargap=0.35, hovermode="x unified", separators=", ",
    )
    fig.update_traces(selector=dict(type="bar"), marker_cornerradius=4, marker_line_width=0)
    fig.update_traces(selector=dict(type="scatter"), line_width=2.4, marker_size=5)
    fig.update_xaxes(title=None, showgrid=False, showline=False, tickformat="%d/%m", hoverformat="%d/%m/%Y", ticks="")
    fig.update_yaxes(title=None, gridcolor="rgba(120,130,145,0.14)", zeroline=False, rangemode="tozero", nticks=5,
                     ticksuffix=" %" if percent else (" €" if money else ""), separatethousands=True)
    return fig


def donut(df: pd.DataFrame, names: str, values: str, colors: dict | None = None, height=300, center: str = ""):
    df = df[df[values] > 0]
    fig = go.Figure(go.Pie(
        labels=df[names], values=df[values], hole=0.62, sort=True, direction="clockwise",
        marker=dict(colors=[(colors or {}).get(n) or SEQ[i % len(SEQ)] for i, n in enumerate(df[names])], line=dict(color="white", width=2)),
        textinfo="percent", textposition="inside", insidetextorientation="horizontal", textfont=dict(color="white", size=12),
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} · %{percent:.1%}<extra></extra>",
    ))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), showlegend=True, font=dict(family=FONT, size=12, color="#6B7280"),
                      legend=dict(orientation="v", x=1.02, y=0.5, font=dict(size=12)), paper_bgcolor="rgba(0,0,0,0)", separators=", ",
                      annotations=[dict(text=center, x=0.5, y=0.5, showarrow=False, font=dict(size=18, color="#3F3F3F", family=FONT))] if center else [])
    fig.update_traces(texttemplate=[("%{percent:.0%}" if v / max(df[values].sum(), 1) >= 0.05 else "") for v in df[values]])
    return fig


def numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Colonnes chiffrées en float (les cellules vides s'affichent alors vides, pas « None »)."""
    for c in df.columns:
        if df[c].dtype == object:
            conv = pd.to_numeric(df[c], errors="coerce")
            if conv.notna().sum() == df[c].notna().sum():
                df[c] = conv.astype(float)
    return df


def dash(df: pd.DataFrame, formats: dict) -> pd.DataFrame:
    """Met en forme des colonnes en texte ; les valeurs absentes deviennent « — »."""
    for c, fn in formats.items():
        if c in df:
            df[c] = [fn(v) if pd.notna(v) else "—" for v in df[c]]
    return df


def fam_label(f):
    return FAMILY_LABEL.get(f, f)


def delta_html(cur, prev, good_up=True, points=False):
    if not COMPARE_ON:
        return ""
    d = brand.delta(cur, prev, good_up=good_up, points=points)
    if not d:
        return '<span class="fl-delta flat">—</span>'
    arrow = "" if d["tone"] == "flat" else ("↑ " if d["text"].startswith("+") else "↓ ")
    return f'<span class="fl-delta {d["tone"]}">{arrow}{esc(d["text"])}</span>'


def kpi_cards(items: list[dict]):
    """items : {titre, valeur, cur, prev, good_up, points, aide, objectif}"""
    cells = []
    for it in items:
        sub = it.get("aide") or ""
        cells.append(
            f'<div class="fl-kpi"><div class="t">{esc(it["titre"])}</div><div class="v">{esc(it["valeur"])}</div>'
            f'<div class="d">{delta_html(it.get("cur"), it.get("prev"), it.get("good_up", True), it.get("points", False))}'
            f'{"<span class=cmp>" + esc(COMPARE_TXT) + "</span>" if COMPARE_ON and it.get("cur") is not None else ""}</div>'
            + (f'<div class="s">{esc(sub)}</div>' if sub else "") + "</div>")
    st.markdown(f'<div class="fl-kpis">{"".join(cells)}</div>', unsafe_allow_html=True)


def section(title: str, caption: str | None = None):
    st.markdown(f'<div class="fl-section"><h3>{esc(title)}</h3>' + (f"<p>{esc(caption)}</p>" if caption else "") + "</div>",
                unsafe_allow_html=True)


def empty(msg: str):
    st.markdown(f'<div class="fl-empty">{esc(msg)}</div>', unsafe_allow_html=True)


def page_header(stage: str, title: str, question: str):
    color = STAGE_COLORS.get(stage, C["cta"])
    st.markdown(f'<div class="fl-head"><span class="fl-chip" style="--c:{color}">{esc(stage)}</span>'
                f'<h1>{esc(title)}</h1><p>{esc(question)}</p></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ apparence (style flugia.com)
st.logo(str(ROOT / "assets" / "logo-flugia.svg"), size="large")
st.markdown(
    f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp h1, .stApp h2, .stApp h3,
    div[data-testid="stMarkdownContainer"] {{ font-family: {FONT}; }}
    [data-testid="stIconMaterial"], .material-symbols-rounded {{ font-family: "Material Symbols Rounded" !important; }}
    .stApp {{ color: #3F3F3F; background: #FFFFFF; }}
    .block-container {{ padding-top: 4.4rem; padding-bottom: 4rem; max-width: 1280px; }}

    /* Barre du haut : menu de site, sans effet grisé ni outils Streamlit */
    header[data-testid="stHeader"] {{ background: #FFFFFF !important; backdrop-filter: none !important; border-bottom: 1px solid #EEF2F7;
        box-shadow: 0 1px 12px rgba(15,15,15,0.04); }}
    [data-testid="stToolbarActions"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
    [data-testid="stStatusWidget"], [data-testid="stDecoration"], #MainMenu, .stAppDeployButton {{ display: none !important; }}
    section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"],
    [data-testid="collapsedControl"] {{ display: none !important; }}
    header [data-testid="stTopNavLink"], header nav a {{ font-weight: 500; color: #3F3F3F !important; border-radius: 999px !important; }}
    header [data-testid="stTopNavLink"][aria-current="page"], header nav a[aria-current="page"] {{ color: {C['cta']} !important;
        background: rgba(67,97,238,0.08) !important; }}
    header [data-testid="stTopNavLink"]:hover {{ background: rgba(67,97,238,0.06) !important; }}

    /* Barre de filtres (période, comparaison, canaux) */
    .fl-filterlabel {{ font-size: .72rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: #6B7280; margin: 0 0 2px 2px; }}
    div[data-testid="stPopover"] button, div[data-testid="stPopoverButton"] {{ border-radius: 10px !important; border: 1px solid #DCE3EC !important;
        background: #fff !important; font-weight: 500; min-height: 40px; justify-content: space-between; }}
    div[data-testid="stPopover"] button:hover {{ border-color: {C['cta']} !important; }}
    div[data-testid="stPopoverBody"] {{ border-radius: 14px; border: 1px solid #EEF2F7; box-shadow: 0 12px 40px rgba(15,15,15,0.12); }}
    .stButton button[kind="primary"], .stFormSubmitButton button {{ border-radius: 999px !important; background: {C['cta']} !important;
        border: 0 !important; font-weight: 600; padding: 0 22px; }}
    .stButton button[kind="secondary"] {{ border-radius: 999px !important; border: 1.5px solid {C['cta']} !important; color: {C['cta']} !important;
        background: #fff !important; font-weight: 600; }}
    .stButton button[kind="tertiary"] {{ color: {C['cta']} !important; }}
    div[data-baseweb="select"] > div {{ border-radius: 10px !important; border-color: #DCE3EC !important; background: #fff !important; }}
    div[data-testid="stDateInput"] input {{ border-radius: 10px; }}

    /* En-tête de page */
    .fl-head {{ margin: .4rem 0 1.2rem; }}
    .fl-head h1 {{ font-size: clamp(1.7rem, 2.8vw, 2.3rem); font-weight: 700; color: #3F3F3F; letter-spacing: -0.02em; margin: .5rem 0 .2rem; padding: 0; }}
    .fl-head p {{ color: #6B7280; margin: 0; font-size: 1rem; }}
    .fl-chip {{ display: inline-block; font-size: .74rem; font-weight: 600; padding: 3px 10px; border-radius: 999px;
        color: var(--c); background: color-mix(in srgb, var(--c) 10%, white); letter-spacing: .02em; }}
    .fl-status {{ font-size: .8rem; color: #6B7280; display: flex; gap: .45rem; align-items: center; justify-content: flex-end; height: 100%; padding-top: 1.6rem; }}
    .fl-dot {{ width: .5rem; height: .5rem; border-radius: 50%; background: #34C759; display: inline-block; }}
    .fl-dot.warn {{ background: #FF9F0A; }}

    /* Sections */
    .fl-section {{ margin: 2.2rem 0 .7rem; }}
    .fl-section h3 {{ font-size: 1.12rem; font-weight: 700; color: #3F3F3F; letter-spacing: -0.01em; margin: 0; padding: 0; }}
    .fl-section p {{ font-size: .86rem; color: #6B7280; margin: .2rem 0 0; }}
    .fl-empty {{ border: 1px dashed #DCE3EC; border-radius: 14px; padding: 18px 20px; color: #6B7280; font-size: .9rem; background: #FBFCFE; }}

    /* Cartes indicateurs */
    .fl-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
    .fl-kpi {{ background: #fff; border: 1px solid #EEF2F7; border-radius: 16px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,15,15,0.03); }}
    .fl-kpi .t {{ font-size: .8rem; color: #6B7280; font-weight: 500; }}
    .fl-kpi .v {{ font-size: 1.7rem; font-weight: 700; color: #3F3F3F; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; margin: 4px 0 2px; }}
    .fl-kpi .d {{ font-size: .78rem; display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }}
    .fl-kpi .s {{ font-size: .74rem; color: #9AA5B1; margin-top: 4px; }}
    .cmp {{ color: #9AA5B1; }}
    .fl-delta {{ font-weight: 600; }}
    .fl-delta.good {{ color: {C['good']}; }} .fl-delta.bad {{ color: {C['bad']}; }} .fl-delta.flat {{ color: #6B7280; }}

    /* Tunnel */
    .fl-funnel {{ display: grid; grid-template-columns: minmax(0,1fr) 118px minmax(0,1fr) 118px minmax(0,1fr) 118px minmax(0,1fr); align-items: stretch; gap: 0; margin-top: .4rem; }}
    .fl-step {{ background: #fff; border: 1px solid #EEF2F7; border-radius: 18px; padding: 18px 18px 16px; position: relative; overflow: hidden;
        box-shadow: 0 2px 10px rgba(15,15,15,0.04); }}
    .fl-step::before {{ content: ""; position: absolute; left: 0; top: 0; right: 0; height: 4px; background: var(--c); }}
    .fl-step .n {{ font-size: .74rem; font-weight: 600; color: var(--c); letter-spacing: .04em; text-transform: uppercase; }}
    .fl-step .w {{ font-size: .9rem; color: #6B7280; margin-top: 2px; }}
    .fl-step .v {{ font-size: 2rem; font-weight: 700; color: #3F3F3F; letter-spacing: -0.02em; margin: 6px 0 2px; font-variant-numeric: tabular-nums; }}
    .fl-step .d {{ font-size: .8rem; min-height: 1.2em; }}
    .fl-bar {{ height: 6px; background: #EEF2F7; border-radius: 99px; margin: 12px 0 4px; overflow: hidden; }}
    .fl-bar i {{ display: block; height: 100%; border-radius: 99px; background: var(--c); }}
    .fl-step .o {{ font-size: .74rem; color: #6B7280; }}
    .fl-conv {{ display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 8px; text-align: center; }}
    .fl-conv .arrow {{ color: #C9D3DF; font-size: 1.3rem; line-height: 1; }}
    .fl-conv b {{ font-size: 1.05rem; color: #3F3F3F; font-weight: 700; margin: 2px 0; }}
    .fl-conv small {{ font-size: .7rem; color: #6B7280; line-height: 1.2; }}
    .fl-conv .fl-delta {{ font-size: .72rem; }}
    @media (max-width: 900px) {{ .fl-funnel {{ grid-template-columns: 1fr; }} .fl-conv {{ flex-direction: row; gap: 8px; padding: 8px 0; }}
        .fl-conv .arrow {{ transform: rotate(90deg); }} }}
    .fl-focus {{ margin-top: 14px; border-radius: 16px; padding: 14px 18px; background: linear-gradient(90deg, rgba(67,97,238,0.08), rgba(76,201,240,0.08));
        border: 1px solid rgba(67,97,238,0.18); color: #3F3F3F; font-size: .92rem; }}
    .fl-focus b {{ color: {C['cta']}; }}

    /* Alertes */
    .fl-alert-head {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 6px; }}
    .fl-alert-title {{ font-weight: 700; color: #3F3F3F; font-size: 1rem; letter-spacing: -0.005em; margin: 2px 0 6px; }}
    .fl-alert-detail {{ color: #6B7280; font-size: .86rem; margin-bottom: 8px; }}
    .fl-alert-act {{ font-size: .86rem; color: #3F3F3F; background: #F6F8FB; border-radius: 10px; padding: 8px 10px; }}
    .fl-alert-act b {{ color: {C['cta']}; }}
    .fl-prio {{ display: inline-block; font-size: .72rem; font-weight: 600; padding: 3px 9px; border-radius: 999px; color: var(--c); background: var(--b); }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 16px !important; border-color: #EEF2F7 !important; background: #fff; }}

    /* GEO mis en avant */
    .fl-geo {{ border-radius: 18px; padding: 18px 20px; background: linear-gradient(135deg, rgba(123,97,255,0.07), rgba(76,201,240,0.08));
        border: 1px solid rgba(123,97,255,0.18); }}
    .fl-geo h4 {{ margin: 0 0 2px; font-size: 1rem; font-weight: 700; color: #3F3F3F; }}
    .fl-geo p {{ margin: 0; color: #6B7280; font-size: .84rem; }}

    div[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; border: 1px solid #EEF2F7; }}
    div[role="radiogroup"] label p {{ font-size: .9rem; }}
    </style>""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ données
DB_URL = _database_url()
m_all, accounts, runs = load(DB_URL)
if m_all.empty:
    st.info("Aucune donnée pour l'instant. La première collecte de la nuit remplira cette page ; "
            "vous pouvez aussi la lancer depuis GitHub, onglet Actions, « Run workflow ».")
    st.stop()
last_day = m_all[m_all["metric"].isin(["sessions", "impressions"])]["day"].max().date()
FAMILIES = [f for f in FAMILY_ORDER if f in set(m_all["famille"].dropna()) or (not accounts.empty and f in set(accounts["famille"]))]

# ------------------------------------------------------------------ pages (menu du haut)
def p_accueil(): page_accueil()
def p_visibilite(): page_visibilite()
def p_interet(): page_interet()
def p_conversion(): page_conversion()
def p_retention(): page_retention()
def p_geo(): page_geo()
def p_explorer(): page_explorer()
def p_parametres(): page_parametres()


nav = st.navigation([
    st.Page(p_accueil, title="Vue d'ensemble", url_path="accueil", default=True),
    st.Page(p_visibilite, title="Visibilité", url_path="visibilite"),
    st.Page(p_interet, title="Intérêt", url_path="interet"),
    st.Page(p_conversion, title="Conversion", url_path="conversion"),
    st.Page(p_retention, title="Rétention", url_path="retention"),
    st.Page(p_geo, title="GEO", url_path="geo"),
    st.Page(p_explorer, title="Explorer", url_path="explorer"),
    st.Page(p_parametres, title="Paramétrages", url_path="parametrages"),
], position="top")

# ------------------------------------------------------------------ barre de filtres (codes GA4 / Meta Ads)
PRESETS = ["7 derniers jours", "14 derniers jours", "28 derniers jours", "30 derniers jours", "90 derniers jours",
           "Ce mois-ci", "Mois dernier", "Cette année", "12 derniers mois", "Personnalisée"]
COMPARES = ["Période précédente", "Même période l'an dernier", "Aucune comparaison"]
ss = st.session_state
ss.setdefault("f_preset", "30 derniers jours")
ss.setdefault("f_compare", "Période précédente")
ss.setdefault("f_gran", "Auto")
ss.setdefault("f_custom", (last_day - timedelta(days=29), last_day))
for f in FAMILIES:
    ss.setdefault(f"ch_{f}", True)


def _custom_changed():
    ss["f_preset"] = "Personnalisée"


def _preset_changed():
    if ss["f_preset"] != "Personnalisée":
        a_, b_, _p0, _p1 = period_bounds(ss["f_preset"], None, last_day, ss["f_compare"])
        ss["f_custom"] = (a_.date(), b_.date())


def _all_channels(value: bool):
    for f_ in FAMILIES:
        ss[f"ch_{f_}"] = value


start, end, pstart, pend = period_bounds(ss["f_preset"], ss["f_custom"], last_day, ss["f_compare"])
COMPARE_ON = ss["f_compare"] != "Aucune comparaison"
COMPARE_TXT = "vs période préc." if ss["f_compare"] == "Période précédente" else "vs an dernier"
days_n = (end - start).days + 1
GRAN = ss["f_gran"] if ss["f_gran"] != "Auto" else ("Jour" if days_n <= 31 else "Semaine" if days_n <= 180 else "Mois")
chosen = [f for f in FAMILIES if ss.get(f"ch_{f}", True)]

bar = st.columns([3.8, 2.1, 1.5, 2.6], gap="small", vertical_alignment="bottom")
with bar[0]:
    st.markdown('<div class="fl-filterlabel">Période</div>', unsafe_allow_html=True)
    cmp_lbl = f"{brand.day(pstart.date())} – {brand.day(pend.date(), pstart.year != end.year)}"
    with st.popover(f"📅  {fmt_period(start, end)}" + (f"  ·  vs {cmp_lbl}" if COMPARE_ON else ""),
                    use_container_width=True):
        c1_, c2_ = st.columns([1, 1.3])
        c1_.radio("Plage de dates", PRESETS, key="f_preset", on_change=_preset_changed)
        with c2_:
            st.date_input("Dates personnalisées", key="f_custom", max_value=last_day, format="DD/MM/YYYY", on_change=_custom_changed)
            st.selectbox("Comparer à", COMPARES, key="f_compare")
            st.caption(f"Données disponibles jusqu'au {brand.day(last_day, True)} (collecte chaque nuit).")
with bar[1]:
    st.markdown('<div class="fl-filterlabel">Canaux</div>', unsafe_allow_html=True)
    ch_label = "Tous les canaux" if len(chosen) == len(FAMILIES) else (
        "Aucun canal" if not chosen else f"{len(chosen)} canaux sur {len(FAMILIES)}")
    with st.popover(f"◎  {ch_label}", use_container_width=True):
        b1, b2 = st.columns(2)
        b1.button("Tout sélectionner", on_click=_all_channels, args=(True,), type="tertiary", use_container_width=True)
        b2.button("Tout désélectionner", on_click=_all_channels, args=(False,), type="tertiary", use_container_width=True)
        for f in FAMILIES:
            st.checkbox(fam_label(f), key=f"ch_{f}")
        st.caption("Les données sans canal (démos, comportement Clarity, facturation) restent toujours affichées.")
with bar[2]:
    st.markdown('<div class="fl-filterlabel">Afficher par</div>', unsafe_allow_html=True)
    st.selectbox("Afficher par", ["Auto", "Jour", "Semaine", "Mois"], key="f_gran", label_visibility="collapsed",
                 format_func=lambda g: f"Auto ({GRAN.lower()})" if g == "Auto" else g)
with bar[3]:
    latest_run = pd.to_datetime(runs["run_at"].max(), utc=True) if not runs.empty else None
    age_h = (datetime.now(timezone.utc) - latest_run).total_seconds() / 3600 if latest_run is not None else None
    failing = []
    if not runs.empty:
        last_runs = runs.sort_values("run_at").groupby("collector").tail(1)
        failing = list(last_runs[last_runs["status"] == "erreur"]["collector"])
    warn = failing or age_h is None or age_h > 30
    status = ("Données à jour" if not warn else "Données à vérifier") + (
        f" · il y a {brand.num(age_h, 0)} h" if age_h is not None else "") + (f" · {len(failing)} source(s) en erreur" if failing else "")
    s1, s2 = st.columns([4, 1], vertical_alignment="center")
    s1.markdown(f'<div class="fl-status"><span class="fl-dot {"warn" if warn else ""}"></span>{esc(status)}</div>', unsafe_allow_html=True)
    if s2.button("", icon=":material/refresh:", help="Actualiser les données", type="tertiary"):
        st.cache_data.clear()
        st.rerun()

if not chosen:
    st.warning("Aucun canal sélectionné : tous les canaux sont affichés.", icon=":material/info:")
    chosen = FAMILIES

m = m_all[m_all["famille"].isna() | m_all["famille"].isin(chosen)]
cur, prev = between(m, "day", start, end), between(m, "day", pstart, pend)
acc = accounts[accounts["famille"].isin(chosen)] if not accounts.empty else accounts
OBJ = load_objectives(DB_URL)


def objective(key: str) -> float | None:
    v = OBJ.get(key)
    if v is None:
        return None
    kind = obj_mod.OBJECTIVES[key][4]
    return obj_mod.prorata(v, days_n) if kind in ("somme", "plafond") else v


# ------------------------------------------------------------------ indicateurs du tunnel
def signups(a0, a1, df=None):
    if not acc.empty:
        return float(((acc["created_at"] >= a0) & (acc["created_at"] <= a1)).sum())
    return tot(between(m, "day", a0, a1) if df is None else df, "web_signups")


def payments(a0, a1):
    if acc.empty or "converted_at" not in acc:
        return None
    return float(((acc["converted_at"] >= a0) & (acc["converted_at"] <= a1)).sum())


def demos(df):
    return tot(df, "demos_booked") or tot(df, "web_demo_requests")


def geo_rate_points():
    g = m_all[(m_all["metric"] == "ai_mention_rate") & (m_all["channel"] == "geo")].sort_values("day")
    return g


def funnel_values():
    f = {
        "Visibilité": (visibility_rows(cur)["value"].sum(), visibility_rows(prev)["value"].sum(), "impressions"),
        "Intérêt": (tot(cur, "sessions"), tot(prev, "sessions"), "sessions"),
        "Conversion": (signups(start, end), signups(pstart, pend), "signups"),
        "Rétention": (payments(start, end), payments(pstart, pend), "trial_conversions"),
    }
    return f


# ------------------------------------------------------------------ alertes
def mark_done(key: str, title: str):
    ss.setdefault("done_local", {})[key] = datetime.now(timezone.utc).isoformat()
    try:
        db = _write_db()
        db.execute("DELETE FROM ui_alertes_traitees WHERE alert_key = ?", (key,))
        db.execute("INSERT INTO ui_alertes_traitees (alert_key, done_at, done_by) VALUES (?, ?, ?)",
                   (key, datetime.now(timezone.utc).isoformat(), viewer()))
        db.commit()
        db.close()
        load_done.clear()
        st.toast(f"Alerte traitée : {title[:60]}", icon=":material/check_circle:")
    except Exception:
        ss["persist_error"] = True
        st.toast("Alerte masquée pour cette session (enregistrement impossible, voir Paramétrages).", icon=":material/info:")


def restore_done(key: str):
    ss.get("done_local", {}).pop(key, None)
    try:
        db = _write_db()
        db.execute("DELETE FROM ui_alertes_traitees WHERE alert_key = ?", (key,))
        db.commit()
        db.close()
        load_done.clear()
    except Exception:
        ss["persist_error"] = True


def active_alerts():
    al = compute_alerts(DB_URL, end.date(), tuple(sorted(OBJ.items())))
    done = {**load_done(DB_URL), **ss.get("done_local", {})}
    keep_days = 7
    now = datetime.now(timezone.utc)
    shown, hidden = [], []
    for a in al:
        a = {**a, "stage": obj_mod.stage_of(a)}
        d = done.get(a["key"])
        recent = False
        if d:
            try:
                recent = now - datetime.fromisoformat(d.replace("Z", "+00:00")) < timedelta(days=keep_days)
            except ValueError:
                recent = True
        (hidden if recent else shown).append(a)
    return shown, hidden


def alert_card(a: dict, k: int):
    sev = brand.SEVERITY.get(a["severite"], brand.SEVERITY["info"])
    color = STAGE_COLORS.get(a["stage"], "#6B7280")
    with st.container(border=True):
        st.markdown(
            f'<div class="fl-alert-head"><span class="fl-chip" style="--c:{color}">{esc(a["stage"])}</span>'
            f'<span class="fl-prio" style="--c:{sev["color"]};--b:{sev["bg"]}">{esc(sev["label"])}</span></div>'
            f'<div class="fl-alert-title">{esc(a["titre"])}</div>'
            f'<div class="fl-alert-detail">{esc(a["detail"])}</div>'
            f'<div class="fl-alert-act"><b>Recommandé :</b> {esc(a["action"])}</div>',
            unsafe_allow_html=True)
        st.checkbox("Fait", key=f"done_{k}_{a['key']}", value=False, on_change=mark_done, args=(a["key"], a["titre"]))


# ------------------------------------------------------------------ 1. Vue d'ensemble
def page_accueil():
    page_header("Vue d'ensemble", "Tunnel de conversion", f"Du {fmt_period(start, end)} · de la première vue au paiement.")
    fv = funnel_values()
    parts, attain = [], {}
    stages = obj_mod.STAGES
    conv_labels = ["des vues deviennent des visites", "des visites deviennent des inscriptions", "des inscrits deviennent clients"]
    for i, s in enumerate(stages):
        c_, p_, okey = fv[s]
        color = STAGE_COLORS[s]
        if c_ is None:
            val, d_html, obj_html = "—", '<span class="fl-delta flat">Source à raccorder (base de l\'application ou facturation)</span>', ""
        else:
            val = brand.num(c_, 0)
            d_html = delta_html(c_, p_) + (f' <span class="cmp">{esc(COMPARE_TXT)}</span>' if COMPARE_ON else "")
            target = objective(okey)
            if target:
                rate = 100 * c_ / target
                attain[s] = rate
                obj_html = (f'<div class="fl-bar"><i style="width:{min(rate, 100):.0f}%"></i></div>'
                            f'<div class="o">{brand.num(rate, 0)} % de l\'objectif ({brand.num(target, 0)} sur la période)</div>')
            else:
                obj_html = '<div class="fl-bar"></div><div class="o">Pas d\'objectif fixé · Paramétrages</div>'
        parts.append(f'<div class="fl-step" style="--c:{color}"><div class="n">{i + 1} · {esc(s)}</div>'
                     f'<div class="w">{esc(obj_mod.STAGE_SUB[s])}</div><div class="v">{val}</div><div class="d">{d_html}</div>{obj_html}</div>')
        if i < 3:
            n_c, n_p = fv[stages[i + 1]][0], fv[stages[i + 1]][1]
            r_c = ratio(n_c, c_) if (n_c is not None and c_) else None
            r_p = ratio(n_p, p_) if (n_p is not None and p_) else None
            dec = 2 if (r_c or 0) < 10 else 1
            parts.append(f'<div class="fl-conv"><span class="arrow">→</span><b>{brand.pct(r_c, dec) if r_c is not None else "—"}</b>'
                         f'<small>{conv_labels[i]}</small>{delta_html(r_c, r_p, points=True) if r_c is not None else ""}</div>')
    st.markdown(f'<div class="fl-funnel">{"".join(parts)}</div>', unsafe_allow_html=True)

    behind = {s: r for s, r in attain.items() if r < 100}
    if behind:
        s_ = min(behind, key=behind.get)
        st.markdown(f'<div class="fl-focus">Étape la plus en retard sur l\'objectif : <b>{esc(s_)}</b> '
                    f'({brand.num(behind[s_], 0)} % atteint). {esc(STAGE_ACTION[s_])}</div>', unsafe_allow_html=True)
    elif attain:
        st.markdown('<div class="fl-focus">Toutes les étapes suivies atteignent leur objectif sur la période.</div>', unsafe_allow_html=True)

    # Alertes
    shown, hidden = active_alerts()
    section("Alertes et actions recommandées",
            f"Calculées au {brand.day(end.date(), True)}. Cochez « Fait » quand l'action est réalisée : l'alerte disparaît "
            "(elle revient au bout de 7 jours si le problème persiste).")
    f1, f2 = st.columns([3, 2])
    stage_opts = obj_mod.STAGES + (["Système"] if any(a["stage"] == "Système" for a in shown) else [])
    sel_stage = f1.pills("Étape", stage_opts, default=stage_opts, selection_mode="multi", key="al_stage") or stage_opts
    sel_prio = f2.pills("Priorité", ["haute", "moyenne", "info"], default=["haute", "moyenne", "info"], selection_mode="multi",
                        format_func=lambda s_: brand.SEVERITY[s_]["label"], key="al_prio") or ["haute", "moyenne", "info"]
    view = [a for a in shown if a["stage"] in sel_stage and a["severite"] in sel_prio]
    order = {s: i for i, s in enumerate(obj_mod.STAGES + ["Système"])}
    view.sort(key=lambda a: ({"haute": 0, "moyenne": 1, "info": 2}.get(a["severite"], 3), order.get(a["stage"], 9)))
    if not view:
        st.success("Rien à traiter : aucun indicateur suivi n'est hors des seuils fixés.", icon=":material/check_circle:")
    cols = st.columns(2, gap="medium")
    for k, a in enumerate(view):
        with cols[k % 2]:
            alert_card(a, k)
    if hidden:
        with st.expander(f"Alertes traitées ces 7 derniers jours ({len(hidden)})"):
            for a in hidden:
                h1, h2 = st.columns([5, 1], vertical_alignment="center")
                h1.markdown(f"~~{esc(a['titre'])}~~  \n<span style='color:#6B7280;font-size:.8rem'>{esc(a['stage'])}</span>",
                            unsafe_allow_html=True)
                h2.button("Réafficher", key=f"undo_{a['key']}", on_click=restore_done, args=(a["key"],), type="tertiary")


# ------------------------------------------------------------------ 2. Visibilité
def geo_block(context: str):
    g = geo_rate_points()
    last = g["value"].iloc[-1] if len(g) else None
    before = g["value"].iloc[-2] if len(g) > 1 else None
    ai_c, ai_p = tot(cur, "ai_sessions"), tot(prev, "ai_sessions")
    comp = m_all[(m_all["metric"] == "ai_mentions") & m_all["channel"].str.startswith("geo_concurrent:")]
    last_comp = comp[comp["day"] == comp["day"].max()] if not comp.empty else comp
    sessions = tot(cur, "sessions")
    left, right = st.columns([1.15, 1], gap="medium")
    with left:
        target = OBJ.get("geo_taux")
        if context == "visibilite":
            sub = f"Dernière mesure le {brand.day(g['day'].iloc[-1].date(), True)}" if len(g) else "Mesure chaque lundi"
            st.markdown(f'<div class="fl-geo"><h4>Visibilité dans les assistants IA (GEO)</h4><p>{esc(sub)} · '
                        f'questions posées à l\'IA par un prospect type</p></div>', unsafe_allow_html=True)
            st.write("")
            kpi_cards([
                {"titre": "Citée par les IA", "valeur": brand.pct(last, 0) if last is not None else "—", "cur": last, "prev": before,
                 "points": True, "aide": f"Objectif : {brand.pct(target, 0)} minimum" if target else "des questions testées"},
                {"titre": "Visites depuis les IA", "valeur": brand.num(ai_c, 0), "cur": ai_c, "prev": ai_p},
            ])
        else:
            st.markdown('<div class="fl-geo"><h4>Visites venant des assistants IA (GEO)</h4>'
                        '<p>ChatGPT, Perplexity, Gemini, Claude, Copilot…</p></div>', unsafe_allow_html=True)
            st.write("")
            kpi_cards([
                {"titre": "Visites depuis les IA", "valeur": brand.num(ai_c, 0), "cur": ai_c, "prev": ai_p},
                {"titre": "Part des visites totales", "valeur": brand.pct(ratio(ai_c, sessions), 1) if sessions else "—",
                 "cur": ratio(ai_c, sessions), "prev": ratio(ai_p, tot(prev, "sessions")), "points": True},
                {"titre": "Citée par les IA", "valeur": brand.pct(last, 0) if last is not None else "—", "cur": last, "prev": before, "points": True},
            ])
    with right:
        if context == "visibilite" and not last_comp.empty:
            sov = last_comp.assign(nom=last_comp["channel"].str.split(":", n=1).str[1]).groupby("nom")["value"].sum()
            tested = m_all[(m_all["metric"] == "ai_prompts_tested") & (m_all["day"] == comp["day"].max())]["value"].sum()
            flugia_hits = (last or 0) / 100 * tested if tested else None
            if flugia_hits is not None:
                sov = pd.concat([sov, pd.Series({"FLUGIA": flugia_hits})])
            sov = sov.sort_values()
            fig = go.Figure(go.Bar(x=sov.values, y=sov.index, orientation="h",
                                   marker_color=[C["cta"] if n == "FLUGIA" else "#C9D3DF" for n in sov.index],
                                   hovertemplate="%{y} : %{x:.0f} réponse(s)<extra></extra>"))
            fig.update_layout(title=dict(text="Marques citées dans les réponses IA (dernière mesure)", font=dict(size=13, color="#3F3F3F"), x=0))
            st.plotly_chart(style(fig, 70 + 30 * len(sov), legend=False).update_xaxes(tickformat=",d", rangemode="tozero")
                            .update_yaxes(type="category", tickmode="linear", ticksuffix="  ").update_layout(hovermode="closest", margin=dict(t=36)),
                            use_container_width=True)
        else:
            ai = cur[cur["metric"] == "ai_sessions"]
            if ai.empty:
                empty("Pas encore de visite venant d'un assistant IA sur la période.")
            else:
                fig = px.line(resample(ai), x="day", y="value", markers=True, color_discrete_sequence=["#7B61FF"])
                fig.update_layout(title=dict(text="Évolution des visites venant des IA", font=dict(size=13, color="#3F3F3F"), x=0))
                st.plotly_chart(style(fig, 260, legend=False).update_layout(margin=dict(t=36)), use_container_width=True)


def page_visibilite():
    page_header("Visibilité", "Vues de la marque", "Combien de personnes voient FLUGIA, sur quels canaux et à quel coût ?")
    v_c, v_p = visibility_rows(cur)["value"].sum(), visibility_rows(prev)["value"].sum()
    clicks_c, clicks_p = tot(cur, "clicks"), tot(prev, "clicks")
    spend_c, spend_p = tot(cur, "spend"), tot(prev, "spend")
    paid_imp_c = tot(cur[cur["channel"].isin(["google_ads", "meta_ads", "linkedin_ads"])], "impressions")
    paid_imp_p = tot(prev[prev["channel"].isin(["google_ads", "meta_ads", "linkedin_ads"])], "impressions")
    budget = objective("budget_pub")
    kpi_cards([
        {"titre": "Vues (tous canaux)", "valeur": brand.num(v_c, 0), "cur": v_c, "prev": v_p,
         "aide": f"Objectif : {brand.num(objective('impressions'), 0)}" if objective("impressions") else None},
        {"titre": "Clics", "valeur": brand.num(clicks_c, 0), "cur": clicks_c, "prev": clicks_p},
        {"titre": "Taux de clic", "valeur": brand.pct(ratio(clicks_c, v_c), 2), "cur": ratio(clicks_c, v_c), "prev": ratio(clicks_p, v_p), "points": True},
        {"titre": "Dépenses publicitaires", "valeur": brand.eur(spend_c), "cur": spend_c, "prev": spend_p, "good_up": False,
         "aide": f"Budget sur la période : {brand.eur(budget)}" if budget else None},
        {"titre": "Coût pour 1 000 vues", "valeur": brand.eur(ratio(spend_c, paid_imp_c, 1000)) if paid_imp_c else "—",
         "cur": ratio(spend_c, paid_imp_c, 1000), "prev": ratio(spend_p, paid_imp_p, 1000), "good_up": False, "aide": "publicité uniquement"},
        {"titre": "Recherches « flugia »", "valeur": brand.num(tot(cur, "branded_searches"), 0), "cur": tot(cur, "branded_searches"),
         "prev": tot(prev, "branded_searches"), "aide": "notoriété (Google)"},
    ])

    section("Répartition et évolution", "Quels canaux génèrent les vues, et comment elles évoluent.")
    vis = visibility_rows(cur).dropna(subset=["famille"])
    l, r = st.columns([1, 1.6], gap="medium")
    with l:
        if vis.empty:
            empty("Pas de vues mesurées sur la période.")
        else:
            share = vis.groupby("famille")["value"].sum().reset_index()
            share["famille"] = share["famille"].map(fam_label)
            st.plotly_chart(donut(share, "famille", "value", {fam_label(k): v for k, v in FAMILY_COLORS.items()}, center=brand.num(v_c, 0)),
                            use_container_width=True)
    with r:
        if not vis.empty:
            t = resample(vis.assign(famille=vis["famille"].map(fam_label)), "famille")
            fig = px.line(t, x="day", y="value", color="famille", markers=True,
                          color_discrete_map={fam_label(k): v for k, v in FAMILY_COLORS.items()}, labels={"famille": ""})
            st.plotly_chart(style(fig, 300), use_container_width=True)

    section("GEO : présence dans les réponses des IA")
    geo_block("visibilite")

    section("Détail par canal", "Ce qui fonctionne le mieux : volume, taux de clic et coût de chaque canal.")
    rows = []
    for ch in sorted(set(visibility_rows(m)["channel"])):
        vc = visibility_rows(cur[cur["channel"] == ch])["value"].sum()
        vp = visibility_rows(prev[prev["channel"] == ch])["value"].sum()
        if not vc and not vp:
            continue
        clicks, spend = tot(cur, "clicks", ch), tot(cur, "spend", ch)
        rows.append({"Canal": label(ch), "Vues": vc, "Évolution": (100 * (vc - vp) / vp) if vp else None,
                     "Clics": clicks or None, "Taux de clic": ratio(clicks, vc) if clicks else None,
                     "Dépenses": spend or None, "Coût / 1 000 vues": ratio(spend, vc, 1000) if spend else None})
    if rows:
        vt = numeric(pd.DataFrame(rows)).sort_values("Vues", ascending=False)
        vt = dash(vt, {"Clics": lambda v: brand.num(v, 0), "Taux de clic": lambda v: brand.pct(v, 2), "Dépenses": brand.eur,
                       "Coût / 1 000 vues": lambda v: f"{brand.num(v, 2)} €"})
        st.dataframe(vt, hide_index=True, use_container_width=True,
                     column_config={
                         "Vues": st.column_config.ProgressColumn("Vues", format="%d", min_value=0, max_value=float(max(r_["Vues"] for r_ in rows))),
                         "Évolution": st.column_config.NumberColumn(f"Évolution ({COMPARE_TXT})", format="%+.0f %%"),
                         })
    sp = cur[cur["metric"] == "spend"]
    if not sp.empty:
        section("Dépenses publicitaires", "Répartition du budget par régie.")
        l, r = st.columns([1, 1.6], gap="medium")
        with l:
            sh = sp.groupby("canal")["value"].sum().reset_index()
            st.plotly_chart(donut(sh, "canal", "value", AD_COLORS, center=brand.eur(spend_c)), use_container_width=True)
        with r:
            fig = px.bar(resample(sp, "canal"), x="day", y="value", color="canal", color_discrete_map=AD_COLORS, labels={"canal": ""})
            st.plotly_chart(style(fig, 300, money=True), use_container_width=True)


# ------------------------------------------------------------------ 3. Intérêt
def page_interet():
    page_header("Intérêt", "Visites du site", "Combien de personnes viennent sur flugia.com, d'où, et que font-elles sur le site ?")
    s_c, s_p = tot(cur, "sessions"), tot(prev, "sessions")
    ref_c, ref_p = tot(cur, "referral_sessions"), tot(prev, "referral_sessions")
    items = [
        {"titre": "Visites du site", "valeur": brand.num(s_c, 0), "cur": s_c, "prev": s_p,
         "aide": f"Objectif : {brand.num(objective('sessions'), 0)}" if objective("sessions") else None},
        {"titre": "Taux vue → visite", "valeur": brand.pct(ratio(s_c, visibility_rows(cur)["value"].sum()), 2),
         "cur": ratio(s_c, visibility_rows(cur)["value"].sum()), "prev": ratio(s_p, visibility_rows(prev)["value"].sum()), "points": True},
        {"titre": "Visites depuis les IA", "valeur": brand.num(tot(cur, "ai_sessions"), 0), "cur": tot(cur, "ai_sessions"), "prev": tot(prev, "ai_sessions")},
        {"titre": "Visites via sites référents", "valeur": brand.num(ref_c, 0), "cur": ref_c, "prev": ref_p, "aide": "presse, partenaires, annuaires"},
    ]
    if tot(cur, "visitors"):
        items.insert(1, {"titre": "Visiteurs", "valeur": brand.num(tot(cur, "visitors"), 0), "cur": tot(cur, "visitors"), "prev": tot(prev, "visitors")})
    kpi_cards(items)

    section("D'où viennent les visites", "Répartition par source et tendance du trafic.")
    s = cur[cur["metric"] == "sessions"]
    l, r = st.columns([1, 1.6], gap="medium")
    if s.empty:
        empty("Pas de visites mesurées sur la période (GA4).")
    else:
        with l:
            sh = s.assign(source=s["channel"].map(lambda c: label(c).replace("Site · ", ""))).groupby("source")["value"].sum().reset_index()
            st.plotly_chart(donut(sh, "source", "value", center=brand.num(s_c, 0)), use_container_width=True)
        with r:
            t = resample(s)
            tp = resample(prev[prev["metric"] == "sessions"]) if COMPARE_ON else pd.DataFrame()
            fig = go.Figure(go.Scatter(x=t["day"], y=t["value"], name="Période", mode="lines+markers", line=dict(color=C["main"])))
            if COMPARE_ON and not tp.empty:
                shift = start - pstart
                fig.add_trace(go.Scatter(x=tp["day"] + shift, y=tp["value"], name="Période de comparaison", mode="lines",
                                         line=dict(color="#C9D3DF", dash="dot")))
            st.plotly_chart(style(fig, 300), use_container_width=True)

    section("GEO : trafic venant des assistants IA")
    geo_block("interet")

    if not s.empty:
        section("Détail par source", "Volume, évolution et capacité de chaque source à générer des inscriptions.")
        t = s.groupby("canal")["value"].sum().rename("Visites").reset_index()
        tp = prev[prev["metric"] == "sessions"].groupby("canal")["value"].sum().rename("prev").reset_index()
        sg = cur[cur["metric"] == "web_signups"].groupby("canal")["value"].sum().rename("Inscriptions").reset_index()
        t = t.merge(tp, on="canal", how="left").merge(sg, on="canal", how="left").fillna(0)
        t["Évolution"] = [(100 * (a_ - b_) / b_) if b_ else None for a_, b_ in zip(t["Visites"], t["prev"])]
        t["Taux d'inscription"] = 100 * t["Inscriptions"] / t["Visites"].where(t["Visites"] > 0)
        t["canal"] = t["canal"].str.replace("Site · ", "", regex=False)
        if sg.empty:  # inscriptions suivies par la base de l'application : voir l'onglet Conversion
            t = t.drop(columns=["Inscriptions", "Taux d'inscription"])
        st.dataframe(t.drop(columns="prev").rename(columns={"canal": "Source"}).sort_values("Visites", ascending=False), hide_index=True,
                     use_container_width=True, column_config={
                         "Visites": st.column_config.ProgressColumn(format="%d", min_value=0, max_value=float(t["Visites"].max())),
                         "Évolution": st.column_config.NumberColumn(f"Évolution ({COMPARE_TXT})", format="%+.0f %%"),
                         "Inscriptions": st.column_config.NumberColumn(format="%d"),
                         "Taux d'inscription": st.column_config.NumberColumn(format="%.2f %%")})

    beh = m_all[m_all["channel"] == "clarity"]
    section("Comportement sur le site (Microsoft Clarity)", "Engagement des visiteurs et signaux de frustration.")
    bc, bp = between(beh, "day", start, end), between(beh, "day", pstart, pend)
    if bc.empty:
        empty("Pas encore de relevé Clarity sur la période (collecte quotidienne, historique limité à 3 jours par appel).")
    else:
        items = []
        for lbl, metric, how, good_up, unit in BEHAVIOR:
            vc, vp = bc[bc["metric"] == metric]["value"], bp[bp["metric"] == metric]["value"]
            if vc.empty:
                continue
            c_ = vc.sum() if how == "sum" else vc.mean()
            p_ = (vp.sum() if how == "sum" else vp.mean()) if not vp.empty else None
            val = brand.pct(c_, 1) if unit == "%" else (f"{brand.num(c_, 0)} s" if unit == "s" else brand.num(c_, 1 if how == "avg" else 0))
            items.append({"titre": lbl, "valeur": val, "cur": c_, "prev": p_, "good_up": good_up, "points": unit == "%"})
        kpi_cards(items)
        st.caption(f"{bc['day'].nunique()} jour(s) mesuré(s) sur la période. Les signaux de frustration (rage clicks, clics morts) doivent baisser.")

    r_ = m[m["metric"].isin(["referral_sessions", "referral_signups"])]
    if not r_.empty:
        section("Sites référents", "Presse, partenaires et annuaires qui envoient des visiteurs.")
        r_ = r_.assign(site=r_["channel"].str.split(":", n=1).str[1])
        rc = between(r_, "day", start, end).pivot_table(index="site", columns="metric", values="value", aggfunc="sum").fillna(0)
        rp = between(r_, "day", pstart, pend).pivot_table(index="site", columns="metric", values="value", aggfunc="sum").fillna(0)
        out = pd.DataFrame({"Visites": rc.get("referral_sessions", pd.Series(dtype=float)),
                            "Période de comparaison": rp.get("referral_sessions", pd.Series(dtype=float)),
                            "Inscriptions": rc.get("referral_signups", pd.Series(dtype=float))}).fillna(0)
        out["Première visite"] = r_[r_["metric"] == "referral_sessions"].groupby("site")["day"].min().dt.strftime("%d/%m/%Y")
        out = out[(out["Visites"] > 0) | (out["Période de comparaison"] > 0)].sort_values("Visites", ascending=False)
        st.dataframe(out.reset_index().rename(columns={"site": "Site"}), hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ 4. Conversion
def page_conversion():
    page_header("Conversion", "Inscriptions", "Combien de visiteurs s'inscrivent à l'essai gratuit ou réservent une démo, et par quel canal ?")
    sg_c, sg_p = signups(start, end), signups(pstart, pend)
    s_c, s_p = tot(cur, "sessions"), tot(prev, "sessions")
    d_c, d_p = demos(cur), demos(prev)
    sp_c, sp_p = tot(cur, "spend"), tot(prev, "spend")
    kpi_cards([
        {"titre": "Inscriptions", "valeur": brand.num(sg_c, 0), "cur": sg_c, "prev": sg_p,
         "aide": f"Objectif : {brand.num(objective('signups'), 0)}" if objective("signups") else ("base de l'application" if not acc.empty else "événement GA4")},
        {"titre": "Taux visite → inscription", "valeur": brand.pct(ratio(sg_c, s_c), 2), "cur": ratio(sg_c, s_c), "prev": ratio(sg_p, s_p), "points": True,
         "aide": f"Objectif : {brand.pct(OBJ['taux_inscription'], 1)}" if OBJ.get("taux_inscription") else None},
        {"titre": "Démos réservées", "valeur": brand.num(d_c, 0), "cur": d_c, "prev": d_p,
         "aide": f"Objectif : {brand.num(objective('demos_booked'), 0)}" if objective("demos_booked") else "pages de remerciement"},
        {"titre": "Coût par inscription", "valeur": brand.eur(sp_c / sg_c) if sg_c and sp_c else "—",
         "cur": sp_c / sg_c if sg_c and sp_c else None, "prev": sp_p / sg_p if sg_p and sp_p else None, "good_up": False, "aide": "dépenses pub ÷ inscriptions"},
    ])

    section("Origine des inscriptions et tendance")
    if not acc.empty:
        a_cur = acc[(acc["created_at"] >= start) & (acc["created_at"] <= end)]
        by = a_cur.groupby("famille").size().rename("value").reset_index()
        trend = pd.DataFrame({"day": a_cur["created_at"], "value": 1.0})
    else:
        w = cur[cur["metric"] == "web_signups"]
        by = w.dropna(subset=["famille"]).groupby("famille")["value"].sum().reset_index()
        trend = w[["day", "value"]]
    l, r = st.columns([1, 1.6], gap="medium")
    with l:
        if by.empty or by["value"].sum() == 0:
            empty("Pas d'inscription mesurée sur la période.")
        else:
            by["famille"] = by["famille"].map(fam_label)
            st.plotly_chart(donut(by, "famille", "value", {fam_label(k): v for k, v in FAMILY_COLORS.items()}, center=brand.num(sg_c, 0)),
                            use_container_width=True)
    with r:
        frames = []
        if not trend.empty:
            frames.append(resample(trend).assign(serie="Inscriptions"))
        dm = cur[cur["metric"].isin(["demos_booked"])] if tot(cur, "demos_booked") else cur[cur["metric"] == "web_demo_requests"]
        if not dm.empty:
            frames.append(resample(dm).assign(serie="Démos réservées"))
        if frames:
            fig = px.line(pd.concat(frames), x="day", y="value", color="serie", markers=True,
                          color_discrete_map={"Inscriptions": C["cta"], "Démos réservées": C["cyan"]}, labels={"serie": ""})
            st.plotly_chart(style(fig, 300), use_container_width=True)

    section("Performance par canal", "Quel canal convertit le mieux, et à quel coût par inscription.")
    rows = []
    sess = cur[cur["metric"] == "sessions"].dropna(subset=["famille"]).groupby("famille")["value"].sum()
    spend = cur[cur["metric"] == "spend"].dropna(subset=["famille"]).groupby("famille")["value"].sum()
    sgs = by.assign(famille=by["famille"]).set_index("famille")["value"] if not by.empty else pd.Series(dtype=float)
    for f in chosen:
        v, sgn, sp = sess.get(f, 0), sgs.get(fam_label(f), 0), spend.get(f, 0)
        if not v and not sgn:
            continue
        rows.append({"Canal": fam_label(f), "Visites": v, "Inscriptions": sgn, "Taux d'inscription": ratio(sgn, v) if v else None,
                     "Dépenses": sp or None, "Coût par inscription": sp / sgn if sp and sgn else None})
    if rows:
        ct = numeric(pd.DataFrame(rows)).sort_values("Inscriptions", ascending=False)
        ct = dash(ct, {"Taux d'inscription": lambda v: brand.pct(v, 2), "Dépenses": brand.eur, "Coût par inscription": brand.eur})
        st.dataframe(ct, hide_index=True, use_container_width=True,
                     column_config={"Visites": st.column_config.NumberColumn(format="%d"),
                                    "Inscriptions": st.column_config.ProgressColumn(format="%d", min_value=0, max_value=float(max(r_["Inscriptions"] for r_ in rows) or 1)),
                                    })
    else:
        empty("Pas assez de données pour comparer les canaux sur la période.")

    d = cur[cur["metric"].isin(["demos_booked", "demos_held", "demos_cancelled", "web_demo_requests"])]
    if not d.empty:
        section("Démos", "Réservations par langue de page (FR, NL, EN) et tenue des rendez-vous.")
        d = d.assign(serie=d["metric"].map(METRIC_NAMES))
        fig = px.bar(resample(d, "serie"), x="day", y="value", color="serie", barmode="group", labels={"serie": ""})
        st.plotly_chart(style(fig, 260), use_container_width=True)


# ------------------------------------------------------------------ 5. Rétention
def page_retention():
    page_header("Rétention", "Paiements", "Combien d'essais deviennent clients payants, et combien restent ?")
    mrr = m_all[m_all["metric"] == "mrr"].sort_values("day")
    if acc.empty:
        empty("Cette page s'activera quand la base de l'application (ou la facturation Stripe) sera raccordée : clients payants, "
              "conversion après l'essai, revenu mensuel, résiliations et comptes à relancer.")
        if not mrr.empty:
            kpi_cards([{"titre": "Revenu mensuel récurrent", "valeur": brand.eur(mrr["value"].iloc[-1])}])
        return
    a = acc
    pay_c, pay_p = payments(start, end), payments(pstart, pend)
    ended = a[(a["trial_ends_at"] >= start) & (a["trial_ends_at"] <= end)]
    ended_p = a[(a["trial_ends_at"] >= pstart) & (a["trial_ends_at"] <= pend)]
    conv = 100 * ended["converted_at"].notna().mean() if len(ended) else None
    conv_p = 100 * ended_p["converted_at"].notna().mean() if len(ended_p) else None
    paying = a[a["status"].isin(["active", "past_due"])]
    churn_c = float(((a["churned_at"] >= start) & (a["churned_at"] <= end)).sum())
    churn_p = float(((a["churned_at"] >= pstart) & (a["churned_at"] <= pend)).sum())
    mrr_c = between(mrr, "day", start, end)["value"]
    mrr_p = between(mrr, "day", pstart, pend)["value"]
    kpi_cards([
        {"titre": "Nouveaux clients payants", "valeur": brand.num(pay_c, 0), "cur": pay_c, "prev": pay_p,
         "aide": f"Objectif : {brand.num(objective('trial_conversions'), 0)}" if objective("trial_conversions") else None},
        {"titre": "Conversion après l'essai", "valeur": brand.pct(conv, 0), "cur": conv, "prev": conv_p, "points": True,
         "aide": f"{len(ended)} essai(s) terminé(s) sur la période"},
        {"titre": "Clients payants actifs", "valeur": brand.num(len(paying), 0), "aide": "aujourd'hui"},
        {"titre": "Revenu mensuel récurrent", "valeur": brand.eur(mrr_c.iloc[-1]) if len(mrr_c) else "—",
         "cur": mrr_c.iloc[-1] if len(mrr_c) else None, "prev": mrr_p.iloc[-1] if len(mrr_p) else None},
        {"titre": "Résiliations", "valeur": brand.num(churn_c, 0), "cur": churn_c, "prev": churn_p, "good_up": False},
    ])

    section("Origine des clients et revenu")
    l, r = st.columns([1, 1.6], gap="medium")
    conv_rows = a[(a["converted_at"] >= start) & (a["converted_at"] <= end)]
    with l:
        if conv_rows.empty:
            empty("Aucun nouveau client sur la période.")
        else:
            by = conv_rows.groupby("famille").size().rename("value").reset_index()
            by["famille"] = by["famille"].map(fam_label)
            st.plotly_chart(donut(by, "famille", "value", {fam_label(k): v for k, v in FAMILY_COLORS.items()}, center=brand.num(pay_c, 0)),
                            use_container_width=True)
    with r:
        if not mrr.empty:
            t = between(mrr, "day", start, end)
            t = t.groupby(pd.Grouper(key="day", freq={"Jour": "D", "Semaine": "W-MON", "Mois": "MS"}[GRAN]))["value"].last().reset_index()
            fig = px.area(t, x="day", y="value", color_discrete_sequence=[C["deep"]])
            fig.update_traces(fillcolor="rgba(19,52,203,0.08)")
            fig.update_layout(title=dict(text="Revenu mensuel récurrent", font=dict(size=13, color="#3F3F3F"), x=0))
            st.plotly_chart(style(fig, 300, money=True, legend=False).update_layout(margin=dict(t=36)), use_container_width=True)

    section("Conversion par canal", "Taux d'essais convertis en clients, par canal d'origine (essais terminés sur la période).")
    if len(ended):
        g = ended.groupby("famille").agg(Essais=("account_id", "count"), Clients=("converted_at", "count")).reset_index()
        g["Taux de conversion"] = 100 * g["Clients"] / g["Essais"]
        g["famille"] = g["famille"].map(fam_label)
        fig = px.bar(g.sort_values("Taux de conversion"), x="Taux de conversion", y="famille", orientation="h",
                     color_discrete_sequence=[C["cta"]], text=g.sort_values("Taux de conversion")["Taux de conversion"].map(lambda v: brand.pct(v, 0)))
        fig = style(fig, 60 + 36 * len(g), legend=False).update_layout(hovermode="closest")
        fig.update_yaxes(type="category", tickmode="linear", ticksuffix="  ").update_xaxes(ticksuffix=" %", range=[0, 100])
        fig.update_traces(hovertemplate="%{y} : %{x:.0f} %<extra></extra>")
        st.plotly_chart(fig, use_container_width=True)

    section("Comptes à traiter")
    t1, t2, t3 = st.tabs(["Essais à conclure", "Essais non activés", "Clients à risque"])
    today = pd.Timestamp(end)
    cols = {"name": "Compte", "famille": "Origine", "created_at": st.column_config.DateColumn("Inscription", format="DD/MM/YYYY"),
            "trial_ends_at": st.column_config.DateColumn("Fin d'essai", format="DD/MM/YYYY"), "usage_7d": "Crédits 7 j",
            "usage_30d": "Crédits 30 j", "usage_prev_30d": "Crédits 30 j préc.", "mrr": st.column_config.NumberColumn("MRR", format="%.0f €")}
    trial = a[a["status"] == "trial"]
    with t1:
        hot = trial[(trial["usage_7d"].fillna(0) >= 20)].sort_values("trial_ends_at")
        if hot.empty:
            empty("Aucun essai actif à forte utilisation.")
        else:
            st.dataframe(hot[["name", "famille", "created_at", "trial_ends_at", "usage_7d"]], hide_index=True, use_container_width=True, column_config=cols)
    with t2:
        cold = trial[trial["activated_at"].isna() & ((today - trial["created_at"]).dt.days >= 3)].sort_values("created_at")
        if cold.empty:
            empty("Aucun essai bloqué.")
        else:
            st.dataframe(cold[["name", "famille", "created_at", "trial_ends_at"]], hide_index=True, use_container_width=True, column_config=cols)
    with t3:
        risk = paying[(paying["usage_prev_30d"].fillna(0) >= 20) & (paying["usage_30d"].fillna(0) < 0.5 * paying["usage_prev_30d"].fillna(0))]
        if risk.empty:
            empty("Aucun client dont l'usage s'effondre.")
        else:
            st.dataframe(risk[["name", "famille", "usage_30d", "usage_prev_30d", "mrr"]], hide_index=True, use_container_width=True, column_config=cols)


# ------------------------------------------------------------------ 6. GEO
def geo_questions() -> dict[str, str]:
    try:
        cfg = yaml.safe_load((ROOT / "config" / "geo_prompts.yaml").read_text(encoding="utf-8"))
        return {q["id"]: q["question"] for q in cfg.get("questions", [])}
    except Exception:
        return {}


def page_geo():
    page_header("Visibilité", "GEO : FLUGIA dans les réponses des IA",
                "Chaque lundi, Claude pose les questions d'un prospect type et vérifie si FLUGIA est citée.")
    g = geo_rate_points()
    if g.empty:
        empty("Pas encore de mesure GEO. La mesure est faite chaque lundi à partir des questions de config/geo_prompts.yaml.")
        return
    q = m_all[(m_all["metric"] == "ai_mentioned") & m_all["channel"].str.startswith("geo:")].copy()
    q["qid"] = q["channel"].str.split(":", n=1).str[1]
    last_day_geo = g["day"].iloc[-1]
    last_q = q[q["day"] == last_day_geo]
    target = OBJ.get("geo_taux")
    kpi_cards([
        {"titre": "Citée par les IA (dernière mesure)", "valeur": brand.pct(g["value"].iloc[-1], 0), "cur": g["value"].iloc[-1],
         "prev": g["value"].iloc[-2] if len(g) > 1 else None, "points": True,
         "aide": f"Objectif : {brand.pct(target, 0)}" if target else brand.day(last_day_geo.date(), True)},
        {"titre": "Questions où FLUGIA est citée", "valeur": f"{int(last_q['value'].sum())} / {len(last_q)}" if len(last_q) else "—"},
        {"titre": "Moyenne sur la période", "valeur": brand.pct(between(g, "day", start, end)["value"].mean(), 0) if len(between(g, "day", start, end)) else "—"},
        {"titre": "Visites depuis les IA", "valeur": brand.num(tot(cur, "ai_sessions"), 0), "cur": tot(cur, "ai_sessions"), "prev": tot(prev, "ai_sessions")},
    ])

    section("Évolution du taux de citation", "Part des questions testées pour lesquelles une IA cite FLUGIA.")
    fig = px.line(g, x="day", y="value", markers=True, color_discrete_sequence=["#7B61FF"])
    if target:
        fig.add_hline(y=target, line_dash="dot", line_color="#9AA5B1", annotation_text=f"Objectif {brand.pct(target, 0)}",
                      annotation_font_color="#6B7280")
    fig.update_yaxes(range=[0, 100])
    st.plotly_chart(style(fig, 280, percent=True, legend=False), use_container_width=True)

    section("Historique par question", "Chaque colonne est une mesure hebdomadaire : bleu = FLUGIA citée, gris = absente.")
    labels = geo_questions()
    piv = q.pivot_table(index="qid", columns="day", values="value", aggfunc="max").sort_index(axis=1).iloc[:, -16:]
    if piv.empty:
        empty("Pas encore d'historique par question.")
    else:
        rate = piv.mean(axis=1).mul(100)
        piv = piv.loc[rate.sort_values(ascending=True).index]
        names = [((labels.get(i) or i.replace("_", " "))[:70] + ("…" if len(labels.get(i) or i) > 70 else "")) for i in piv.index]
        names = [f"{n}  ·  {brand.pct(rate[i], 0)}" for n, i in zip(names, piv.index)]
        heat = go.Figure(go.Heatmap(
            z=piv.values, x=[d.strftime("%d/%m") for d in piv.columns], y=names, zmin=0, zmax=1,
            colorscale=[[0, "#EEF2F7"], [0.5, "#EEF2F7"], [0.5, C["cta"]], [1, C["cta"]]], showscale=False, xgap=3, ygap=3,
            customdata=[[("Citée" if v == 1 else "Non citée" if v == 0 else "Non mesurée") for v in row] for row in piv.values],
            hovertemplate="%{y}<br>%{x} : %{customdata}<extra></extra>"))
        heat.update_layout(height=60 + 38 * len(piv), margin=dict(l=0, r=0, t=10, b=0), font=dict(family=FONT, size=12, color="#3F3F3F"),
                           plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        heat.update_xaxes(side="top", showgrid=False)
        heat.update_yaxes(showgrid=False)
        st.plotly_chart(heat, use_container_width=True)
        with st.expander("Voir les questions complètes"):
            for i in piv.index[::-1]:
                st.markdown(f"**{brand.pct(rate[i], 0)}** · {labels.get(i, i)}")

    comp = m_all[(m_all["metric"] == "ai_mentions") & m_all["channel"].str.startswith("geo_concurrent:")]
    if not comp.empty:
        section("Concurrents cités dans les réponses", "Nombre de réponses citant chaque concurrent, mesure après mesure.")
        comp = comp.assign(concurrent=comp["channel"].str.split(":", n=1).str[1])
        fig = px.line(comp.groupby(["day", "concurrent"])["value"].sum().reset_index(), x="day", y="value", color="concurrent", markers=True,
                      labels={"concurrent": ""})
        st.plotly_chart(style(fig, 300), use_container_width=True)


# ------------------------------------------------------------------ 7. Explorer
def page_explorer():
    page_header("Explorer", "Explorer les données", "Choisissez un indicateur, la façon de le découper et le type de graphique.")
    present = [x for x in METRIC_NAMES if x in set(m["metric"])] + sorted(set(m["metric"]) - set(METRIC_NAMES))
    stage_of_m = lambda x: METRIC_STAGE.get(x, "Comportement" if x.startswith("clarity_") else "Autres")  # noqa: E731
    order = {s: i for i, s in enumerate(obj_mod.STAGES + ["Comportement", "Autres"])}
    present.sort(key=lambda x: order.get(stage_of_m(x), 9))
    c1, c2, c3, c4 = st.columns([2.4, 2.4, 1.5, 1.6])
    met = c1.selectbox("Indicateur", present, index=present.index("sessions") if "sessions" in present else 0,
                       format_func=lambda x: f"{stage_of_m(x)} · {METRIC_NAMES.get(x, x)}")
    met2 = c2.selectbox("Comparer avec (facultatif)", [None] + [x for x in present if x != met],
                        format_func=lambda x: "—" if x is None else f"{stage_of_m(x)} · {METRIC_NAMES.get(x, x)}")
    split = c3.selectbox("Découper par", ["Canal", "Source détaillée", "Aucun"])
    kind = c4.selectbox("Graphique", ["Courbes", "Barres empilées", "Barres groupées", "Camembert", "Tableau"])
    how = "mean" if met in AVG_METRICS else "sum"

    ex = cur[cur["metric"] == met]
    if ex.empty:
        empty("Aucune donnée pour cet indicateur sur la période et les canaux choisis.")
        return
    by = {"Canal": "famille", "Source détaillée": "canal", "Aucun": None}[split]
    if by == "famille":
        ex = ex.assign(famille=ex["famille"].map(lambda f: fam_label(f) if isinstance(f, str) else "Sans canal"))
    total_c = ex["value"].agg(how)
    total_p = prev[prev["metric"] == met]["value"].agg(how) if not prev[prev["metric"] == met].empty else None
    kpi_cards([{"titre": METRIC_NAMES.get(met, met) + (" (moyenne)" if how == "mean" else ""), "valeur": brand.num(total_c),
                "cur": total_c, "prev": total_p}])
    st.write("")
    if kind == "Camembert" and by:
        sh = ex.groupby(by)["value"].agg(how).reset_index()
        st.plotly_chart(donut(sh, by, "value", height=380), use_container_width=True)
    elif kind != "Tableau":
        r_ = resample(ex, by, how)
        if kind == "Courbes" or kind == "Camembert":
            fig = px.line(r_, x="day", y="value", color=by, markers=True, labels={by or "": ""})
        else:
            fig = px.bar(r_, x="day", y="value", color=by, barmode="stack" if kind == "Barres empilées" else "group", labels={by or "": ""})
        if met2:
            from plotly.subplots import make_subplots
            sec = resample(cur[cur["metric"] == met2], None, "mean" if met2 in AVG_METRICS else "sum")
            fig2 = make_subplots(specs=[[{"secondary_y": True}]])
            for tr in fig.data:
                fig2.add_trace(tr, secondary_y=False)
            fig2.add_trace(go.Scatter(x=sec["day"], y=sec["value"], name=METRIC_NAMES.get(met2, met2), mode="lines",
                                      line=dict(color="#9AA5B1", dash="dot", width=2)), secondary_y=True)
            fig2.update_layout(barmode=fig.layout.barmode)
            fig = fig2
        st.plotly_chart(style(fig, 380), use_container_width=True)

    section("Tableau", "Totaux par découpage, comparés à la période de comparaison.")
    key = by or "metric"
    t = ex.groupby(key)["value"].agg(how).rename("Période").reset_index()
    pp = prev[prev["metric"] == met]
    if by == "famille":
        pp = pp.assign(famille=pp["famille"].map(lambda f: fam_label(f) if isinstance(f, str) else "Sans canal"))
    tp = pp.groupby(key)["value"].agg(how).rename("Comparaison").reset_index() if not pp.empty else pd.DataFrame(columns=[key, "Comparaison"])
    t = t.merge(tp, on=key, how="left")
    t["Évolution"] = [(100 * (a_ - b_) / b_) if b_ else None for a_, b_ in zip(t["Période"], t["Comparaison"].fillna(0))]
    t = t.rename(columns={"famille": "Canal", "canal": "Source", "metric": "Indicateur"}).sort_values("Période", ascending=False)
    st.dataframe(t, hide_index=True, use_container_width=True,
                 column_config={"Période": st.column_config.NumberColumn(format="localized"),
                                "Comparaison": st.column_config.NumberColumn(format="localized"),
                                "Évolution": st.column_config.NumberColumn(format="%+.0f %%")})
    detail = resample(ex, by, how).rename(columns={"day": "Date", "value": METRIC_NAMES.get(met, met)})
    st.download_button("Télécharger en CSV", detail.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       file_name=f"flugia-{met}-{start.date()}-{end.date()}.csv", mime="text/csv", icon=":material/download:")


# ------------------------------------------------------------------ 8. Paramétrages
SETUP_SQL = """-- À exécuter une fois dans Supabase (SQL Editor)
CREATE TABLE IF NOT EXISTS ui_objectifs (cle TEXT PRIMARY KEY, valeur DOUBLE PRECISION, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ui_alertes_traitees (alert_key TEXT PRIMARY KEY, done_at TEXT NOT NULL, done_by TEXT);
GRANT SELECT, INSERT, UPDATE, DELETE ON ui_objectifs, ui_alertes_traitees TO lecture_tableau;"""


def page_parametres():
    page_header("Paramétrages", "Objectifs", "Fixez les objectifs mensuels : ils alimentent le tunnel, les alertes et les actions recommandées.")
    st.caption("Les objectifs sont saisis par mois et ramenés automatiquement à la durée de la période affichée. "
               "La collecte de nuit s'en sert aussi pour les alertes Slack et le rapport du lundi. Laissez vide pour ne pas suivre un objectif.")
    with st.form("objectifs", border=False):
        values = {}
        for stage in obj_mod.STAGES:
            keys = [k for k, v in obj_mod.OBJECTIVES.items() if v[2] == stage]
            if not keys:
                continue
            section(f"{stage} · {obj_mod.STAGE_SUB[stage]}")
            cols = st.columns(3)
            for i, k in enumerate(keys):
                lbl, unit, _s, _m, kind = obj_mod.OBJECTIVES[k]
                values[k] = cols[i % 3].number_input(f"{lbl} ({unit})", min_value=0.0, value=float(OBJ[k]) if OBJ.get(k) is not None else None, step=1.0 if kind != "taux" else 0.5,
                                                     format="%.0f" if kind != "taux" else "%.1f", placeholder="Pas d'objectif", key=f"obj_{k}")
        st.write("")
        if st.form_submit_button("Enregistrer les objectifs", type="primary"):
            try:
                db = _write_db()
                obj_mod.save(db, values)
                db.close()
                load_objectives.clear()
                compute_alerts.clear()
                st.success("Objectifs enregistrés. Le tunnel et les alertes sont mis à jour.", icon=":material/check_circle:")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                ss["persist_error"] = True
                st.error(f"Enregistrement impossible : la base n'autorise pas encore l'écriture ({type(e).__name__}). Voir ci-dessous.")

    section("Rythme actuel par rapport aux objectifs", f"Période affichée : {fmt_period(start, end)} ({days_n} jours).")
    fv = funnel_values()
    rows = []
    for k, (lbl, unit, stage, metric, kind) in obj_mod.OBJECTIVES.items():
        target = objective(k)
        if target is None:
            continue
        if k == "geo_taux":
            g = between(geo_rate_points(), "day", start, end)["value"]
            got = g.mean() if len(g) else None
        elif k == "taux_inscription":
            got = ratio(fv["Conversion"][0], fv["Intérêt"][0])
        elif k == "demos_booked":
            got = demos(cur)
        elif k == "budget_pub":
            got = tot(cur, "spend")
        else:
            got = next((v[0] for v in fv.values() if v[2] == k), None)
        att = (100 * got / target) if got is not None and target else None
        f_ = (lambda v: brand.pct(v, 1)) if kind == "taux" else (brand.eur if k == "budget_pub" else (lambda v: brand.num(v, 0)))
        rows.append({"Étape": stage, "Objectif": lbl + (" (plafond)" if kind == "plafond" else ""),
                     "Cible sur la période": f_(target), "Réalisé": f_(got) if got is not None else "—", "Atteinte": att})
    if rows:
        st.dataframe(numeric(pd.DataFrame(rows)), hide_index=True, use_container_width=True,
                     column_config={"Atteinte": st.column_config.ProgressColumn("Atteinte (%)", format="%.0f %%", min_value=0, max_value=100)})
    else:
        empty("Aucun objectif fixé.")

    with st.expander("Enregistrement des objectifs et des alertes traitées", expanded=bool(ss.get("persist_error"))):
        st.markdown("L'interface lit la base avec un utilisateur en lecture seule. Pour enregistrer les objectifs et les alertes "
                    "cochées « Fait », cet utilisateur doit pouvoir écrire dans deux petites tables dédiées (et uniquement celles-ci) :")
        st.code(SETUP_SQL, language="sql")


nav.run()
