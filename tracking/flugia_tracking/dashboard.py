"""Génère le tableau de bord : une page HTML autonome (aucun abonnement, s'ouvre dans un navigateur)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .db import ROOT

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
OUTPUT = ROOT / "site" / "index.html"


def build(snapshot: dict, alerts: list, summary: str | None = None, demo: bool = False, output: Path = OUTPUT) -> Path:
    payload = {k: v for k, v in snapshot.items() if k != "_data"}
    payload["alertes"] = [asdict(a) for a in alerts]
    payload["synthese"] = summary
    payload["demo"] = demo
    blob = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    (output.parent / "snapshot.json").write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    return output
