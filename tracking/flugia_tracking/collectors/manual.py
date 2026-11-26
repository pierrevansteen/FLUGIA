"""Saisie manuelle : ce qu'aucune API ne fournit (salons, presse, partenariats, programme de parrainage...).

Ajoutez des lignes dans data/manual_visibility.csv :
    date,channel,metric,value,note
    2026-11-20,salon_tech_bruxelles,impressions,1500,visiteurs du stand
    2026-11-22,presse_lecho,impressions,40000,article
    2026-11-25,parrainage,referrals,3,clients ayant recommandé
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .base import CollectResult, Collector

FILE = Path(__file__).resolve().parents[2] / "data" / "manual_visibility.csv"


class ManualCollector(Collector):
    name = "manual"
    label = "Saisie manuelle (salons, presse, parrainage)"

    def enabled(self) -> bool:
        return FILE.exists()

    def collect(self, start, end) -> CollectResult:
        result = CollectResult()
        with FILE.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("date"):
                    continue
                day = date.fromisoformat(row["date"].strip())
                # La saisie manuelle est toujours rechargée en entier (corrections possibles).
                result.add(day, f"manuel:{row['channel'].strip()}", row["metric"].strip(), float(row["value"]))
        return result
