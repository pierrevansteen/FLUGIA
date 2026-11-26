from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@lru_cache
def alerts_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "alerts.yaml").read_text(encoding="utf-8"))
