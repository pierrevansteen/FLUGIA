"""GEO (Generative Engine Optimization) : Flugia est-elle citée quand on demande conseil à une IA ?

Pour chaque question de config/geo_prompts.yaml, on interroge Claude avec la recherche web activée,
comme le ferait un prospect, puis on vérifie par simple recherche de texte si la marque est citée
(et quels concurrents le sont). Le comptage est fait par le code, pas par l'IA.

Coût : une requête + quelques recherches web par question. Par défaut la collecte tourne une fois
par semaine (GEO_RUN_WEEKDAY, 0 = lundi), sauf si on force avec --only geo.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import yaml

from .base import CollectResult, Collector, CollectorError

CONFIG = Path(__file__).resolve().parents[2] / "config" / "geo_prompts.yaml"
MODEL = os.environ.get("GEO_MODEL", "claude-opus-5-5")


class GeoCollector(Collector):
    name = "geo"
    label = "GEO (visibilité dans les réponses IA)"
    required_env = ["ANTHROPIC_API_KEY"]
    forced = False  # mis à True par --only geo

    def should_run_today(self, today: date) -> bool:
        weekday = os.environ.get("GEO_RUN_WEEKDAY", "0")
        return self.forced or weekday == "*" or today.weekday() == int(weekday)

    def collect(self, start, end) -> CollectResult:
        import anthropic

        result = CollectResult()
        today = date.today()
        if not self.should_run_today(today):
            result.warnings.append("GEO : pas le jour de mesure (hebdomadaire), rien à faire.")
            return result

        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        brand = [b.lower() for b in cfg.get("marque", ["flugia"])]
        competitors = cfg.get("concurrents", [])
        prompts = cfg.get("questions", [])
        country = cfg.get("pays", "BE")
        client = anthropic.Anthropic()

        mentioned_total = 0
        for item in prompts:
            text = self._ask(client, item["question"], country)
            low = text.lower()
            hit = any(b in low for b in brand)
            mentioned_total += int(hit)
            result.add(today, f"geo:{item['id']}", "ai_mentioned", int(hit))
            for comp in competitors:
                if re.search(r"\b" + re.escape(comp.lower()) + r"\b", low):
                    result.add(today, f"geo_concurrent:{comp}", "ai_mentions", 1)

        if prompts:
            result.add(today, "geo", "ai_prompts_tested", len(prompts))
            result.add(today, "geo", "ai_mentions", mentioned_total)
            result.add(today, "geo", "ai_mention_rate", round(100 * mentioned_total / len(prompts), 1))
        return result

    def _ask(self, client, question: str, country: str) -> str:
        import anthropic

        messages = [{"role": "user", "content": question}]
        tools = [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 3,
            "user_location": {"type": "approximate", "country": country},
        }]
        for _ in range(4):  # relances si la réponse est mise en pause (pause_turn)
            try:
                response = client.beta.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    output_config={"effort": "medium"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    tools=tools,
                    messages=messages,
                )
            except anthropic.RateLimitError as exc:
                raise CollectorError(f"GEO : limite de requêtes atteinte ({exc.message})") from exc
            except anthropic.APIStatusError as exc:
                raise CollectorError(f"GEO : erreur API Claude {exc.status_code} ({exc.message})") from exc
            except anthropic.APIConnectionError as exc:
                raise CollectorError("GEO : API Claude injoignable") from exc

            if response.stop_reason == "refusal":
                return ""
            if response.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": response.content}]
                continue
            return "\n".join(b.text for b in response.content if b.type == "text")
        return ""
