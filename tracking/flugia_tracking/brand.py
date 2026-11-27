"""Identité visuelle Flugia (reprise de flugia.com) et mise en forme française des nombres."""
from __future__ import annotations

from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COLORS = {
    "ink": "#0F0F0F",          # texte principal (logo)
    "ink_2": "#3F3F3F",
    "muted": "#6B7280",
    "line": "#C9D3DF",
    "bg_soft": "#EEF2F7",
    "bg_softer": "#F6F8FB",
    "main": "#008CFC",         # --flugia-main
    "cta": "#4361EE",          # --flugia-cta (début du dégradé du logo)
    "deep": "#1334CB",         # --flugia-deep
    "cyan": "#4CC9F0",         # --flugia-cyan (fin du dégradé du logo)
    "good": "#0A7D1E",
    "bad": "#C62F30",
    "warning": "#B87700",
}
GRADIENT = "linear-gradient(90deg, #4361EE 0%, #4CC9F0 100%)"

# Couleurs des canaux dans les graphiques (ordre fixe, jamais recyclé)
SERIES = {"Google Ads": "#1334CB", "Meta": "#4CC9F0", "LinkedIn": "#008CFC", "SEO": "#4361EE", "Autres": "#C9D3DF"}

SEVERITY = {
    "haute": {"label": "Priorité haute", "color": "#C62F30", "bg": "#FDEDED"},
    "moyenne": {"label": "À surveiller", "color": "#B87700", "bg": "#FFF5E0"},
    "info": {"label": "Information", "color": "#008CFC", "bg": "#E6F3FF"},
}

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def logo_svg() -> str:
    return (ROOT / "assets" / "logo-flugia.svg").read_text(encoding="utf-8")


def num(v, decimals: int | None = None) -> str:
    """1234.5 -> '1 234,5' (espace fine insécable, virgule décimale)."""
    if v is None:
        return "—"
    if decimals is None:
        decimals = 0 if abs(v) >= 100 or float(v).is_integer() else 1
    s = f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s


def eur(v) -> str:
    return "—" if v is None else f"{num(v, 0)} €"


def pct(v, decimals: int = 1) -> str:
    return "—" if v is None else f"{num(v, decimals)} %"


def day(d: str | date, year: bool = False) -> str:
    d = date.fromisoformat(str(d)[:10]) if not isinstance(d, date) else d
    return f"{d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if year else "")


def delta(cur, prev, good_up: bool = True, points: bool = False) -> dict | None:
    """Renvoie {'text', 'tone'} pour une évolution, ou None si non calculable."""
    if cur is None or prev is None:
        return None
    if points:
        d = cur - prev
        if abs(d) < 0.05:
            return {"text": "stable", "tone": "flat"}
        return {"text": f"{'+' if d > 0 else '−'}{num(abs(d), 1)} pt", "tone": "good" if (d > 0) == good_up else "bad"}
    if not prev:
        return {"text": "nouveau", "tone": "flat"} if cur else None
    p = 100 * (cur - prev) / prev
    if abs(p) < 0.5:
        return {"text": "stable", "tone": "flat"}
    return {"text": f"{'+' if p > 0 else '−'}{num(abs(p), 0)} %", "tone": "good" if (p > 0) == good_up else "bad"}
