"""Rapport PDF de pilotage, aux couleurs de Flugia.

Le HTML est produit avec Jinja2 (graphiques en SVG générés ici, sans dépendance réseau),
puis converti en PDF A4 par Chromium (Playwright).
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import brand
from .db import ROOT

TEMPLATES = Path(__file__).with_name("templates")
PER_ACCOUNT = ("essai_chaud:", "essai_bloque:", "risque:", "renouvellement:")


# ---------------------------------------------------------------- graphiques SVG
def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    import math

    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def bar_chart(labels: list[str], series: dict[str, list[float]], colors: dict[str, str],
              width=520, height=180, stacked=True, unit="") -> str:
    """Barres (empilées) avec axe Y discret, 4 graduations, étiquettes de semaines."""
    if not labels or not series or not any(any(v for v in s if v) for s in series.values()):
        return '<div class="empty">Pas encore de données sur la période.</div>'
    left, right, top, bottom = 44, 8, 8, 22
    pw, ph = width - left - right, height - top - bottom
    n = len(labels)
    totals = [sum((series[k][i] or 0) for k in series) for i in range(n)] if stacked else \
        [max((series[k][i] or 0) for k in series) for i in range(n)]
    ymax = _nice_max(max(totals))
    slot = pw / n
    bw = min(26, slot * 0.62)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">']
    for t in range(5):
        y = top + ph - ph * t / 4
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#E6EBF2" stroke-width="1"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" class="ax">{brand.num(ymax * t / 4)}{unit}</text>')
    keys = list(series)
    for i in range(n):
        x = left + slot * i + (slot - bw) / 2
        if stacked:
            y0 = top + ph
            for k in keys:
                v = series[k][i] or 0
                if not v:
                    continue
                h = ph * v / ymax
                out.append(f'<rect x="{x:.1f}" y="{y0 - h:.1f}" width="{bw:.1f}" height="{max(h - 1.5, 0.5):.1f}" fill="{colors[k]}"/>')
                y0 -= h
        else:
            sub = bw / len(keys)
            for j, k in enumerate(keys):
                v = series[k][i] or 0
                h = ph * v / ymax
                out.append(f'<rect x="{x + j * sub:.1f}" y="{top + ph - h:.1f}" width="{sub - 1.5:.1f}" height="{h:.1f}" fill="{colors[k]}" rx="1.5"/>')
        step = max(1, n // (7 if width >= 450 else 4))
        if (n - 1 - i) % step == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height - 6}" text-anchor="middle" class="ax">{html.escape(labels[i])}</text>')
    out.append("</svg>")
    return "".join(out)


def line_chart(labels: list[str], values: list[float | None], color: str, width=520, height=170, unit="", ymax=None) -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pts:
        return '<div class="empty">Pas encore de mesure.</div>'
    left, right, top, bottom = 44, 12, 10, 22
    pw, ph = width - left - right, height - top - bottom
    n = len(labels)
    ymax = ymax or _nice_max(max(v for _, v in pts))
    sx = lambda i: left + (pw * i / max(n - 1, 1))  # noqa: E731
    sy = lambda v: top + ph - ph * v / ymax  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">']
    for t in range(5):
        y = top + ph - ph * t / 4
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#E6EBF2"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" class="ax">{brand.num(ymax * t / 4)}{unit}</text>')
    path = " ".join(f"{'M' if k == 0 else 'L'}{sx(i):.1f},{sy(v):.1f}" for k, (i, v) in enumerate(pts))
    out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
    for i, v in pts:
        out.append(f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="3" fill="{color}" stroke="#fff" stroke-width="1.5"/>')
    li, lv = pts[-1]
    out.append(f'<text x="{sx(li) - 4:.1f}" y="{sy(lv) - 8:.1f}" text-anchor="end" class="lbl">{brand.num(lv)}{unit}</text>')
    step = max(1, n // (7 if width >= 450 else 4))
    for i in range(n):
        if (n - 1 - i) % step == 0:
            out.append(f'<text x="{sx(i):.1f}" y="{height - 6}" text-anchor="middle" class="ax">{html.escape(labels[i])}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- synthèse IA (markdown simple -> HTML)
def md_to_html(text: str | None) -> str:
    if not text:
        return ""
    lines, out, in_list = text.strip().splitlines(), [], None

    def inline(s):
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<strong>\1</strong>", s)  # *gras* façon Slack
        return re.sub(r"_(.+?)_", r"<em>\1</em>", s)

    for raw in lines:
        line = raw.strip()
        m_ul = re.match(r"^[-•]\s+(.*)", line)
        m_ol = re.match(r"^\d+[.)]\s+(.*)", line)
        kind = "ul" if m_ul else "ol" if m_ol else None
        if in_list and kind != in_list:
            out.append(f"</{in_list}>")
            in_list = None
        if kind:
            if not in_list:
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{inline((m_ul or m_ol).group(1))}</li>")
        elif line.startswith("#"):
            out.append(f"<h4>{inline(line.lstrip('#').strip())}</h4>")
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    return "\n".join(out)


# ---------------------------------------------------------------- assemblage
def build_html(snapshot: dict, alerts: list, summary: str | None = None, demo: bool = False) -> str:
    snap = {k: v for k, v in snapshot.items() if k != "_data"}
    h = snap["hebdo"]
    weeks = [brand.day(w) for w in h["semaines"]]
    vis_series = h["visibilite"]
    charts = {
        "visibilite": bar_chart(weeks, vis_series, {k: brand.SERIES.get(k, "#C9D3DF") for k in vis_series}),
        "geo": line_chart(weeks, h["geo_taux"], brand.COLORS["cta"], unit=" %", ymax=100),
        "visites": bar_chart(weeks, {"Visites": h["visites"]}, {"Visites": brand.COLORS["main"]}, width=250, height=150),
        "inscriptions": bar_chart(weeks, {"Inscriptions": h["inscriptions"]}, {"Inscriptions": brand.COLORS["deep"]}, width=250, height=150),
        "demos": bar_chart(weeks, {"Démos": h["demos"]}, {"Démos": brand.COLORS["cta"]}, width=250, height=150),
        "mrr": line_chart(weeks, h["mrr"], brand.COLORS["deep"], unit=" €", width=380, height=180) if any(v for v in (h["mrr"] or []) if v) else None,
        "conversions": bar_chart(weeks, {"Nouveaux clients": h["conversions"], "Résiliations": h["resiliations"]},
                                 {"Nouveaux clients": brand.COLORS["main"], "Résiliations": "#9AA5B1"}, stacked=False, width=380, height=180)
        if h.get("conversions") else None,
    }
    al = [asdict(a) for a in alerts]
    general = [a for a in al if not a["key"].startswith(PER_ACCOUNT)]
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters.update(num=brand.num, eur=brand.eur, pct=brand.pct, day=brand.day)
    env.globals.update(delta=brand.delta, C=brand.COLORS, SEV=brand.SEVERITY, SERIES=brand.SERIES)
    return env.get_template("rapport.html").render(
        s=snap, alerts=general, all_alerts=al, charts=charts, logo=brand.logo_svg(),
        summary_html=md_to_html(summary), demo=demo, gradient=brand.GRADIENT,
        legend=[(k, brand.SERIES.get(k, "#C9D3DF")) for k in vis_series],
    )


def to_pdf(html_doc: str, output: Path) -> Path:
    from playwright.sync_api import sync_playwright

    output.parent.mkdir(parents=True, exist_ok=True)
    footer = (
        '<div style="width:100%;font-family:Inter,Arial,sans-serif;font-size:7.5px;color:#6B7280;'
        'padding:0 14mm;display:flex;justify-content:space-between;">'
        "<span>Flugia · Rapport de pilotage commercial · Confidentiel</span>"
        '<span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
    )
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_doc, wait_until="networkidle")
        page.pdf(path=str(output), format="A4", print_background=True, display_header_footer=True,
                 header_template="<div></div>", footer_template=footer,
                 margin={"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        browser.close()
    return output


def build_pdf(snapshot: dict, alerts: list, summary: str | None = None, demo: bool = False,
              output: Path | None = None) -> Path:
    output = output or ROOT / "site" / f"rapport-flugia-{snapshot['au']}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    html_doc = build_html(snapshot, alerts, summary, demo)
    if os.environ.get("REPORT_KEEP_HTML"):
        output.with_suffix(".html").write_text(html_doc, encoding="utf-8")
    return to_pdf(html_doc, output)
