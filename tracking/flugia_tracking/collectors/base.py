"""Socle commun à tous les collecteurs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import requests

TIMEOUT = 60


class CollectorError(Exception):
    pass


@dataclass
class Metric:
    day: str          # AAAA-MM-JJ
    channel: str      # ex. "google_ads", "meta_ads", "seo", "geo"
    metric: str       # ex. "impressions", "clicks", "spend"
    value: float


@dataclass
class CollectResult:
    metrics: list[Metric] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, day, channel, metric, value):
        if value is None:
            return
        self.metrics.append(Metric(str(day)[:10], channel, metric, float(value)))


class Collector:
    """Un collecteur par source. Il est actif dès que ses variables d'environnement existent."""

    name = "base"
    label = "Source"
    required_env: list[str] = []

    def enabled(self) -> bool:
        return all(os.environ.get(k) for k in self.required_env)

    def missing_env(self) -> list[str]:
        return [k for k in self.required_env if not os.environ.get(k)]

    def collect(self, start: date, end: date) -> CollectResult:  # pragma: no cover
        raise NotImplementedError

    # --- utilitaires HTTP -------------------------------------------------
    @staticmethod
    def http(method: str, url: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", TIMEOUT)
        resp = requests.request(method, url, **kwargs)
        if resp.status_code in (401, 403):
            raise CollectorError(
                f"Accès refusé ({resp.status_code}) sur {url.split('?')[0]} : jeton expiré ou droits insuffisants. "
                f"Réponse : {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise CollectorError(f"Erreur {resp.status_code} sur {url.split('?')[0]} : {resp.text[:500]}")
        return resp.json() if resp.content else {}


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise CollectorError(f"Variable d'environnement manquante : {name}")
    return value


def google_access_token(scopes: list[str], subject: str | None = None) -> str:
    """Jeton d'accès Google à partir de GOOGLE_SERVICE_ACCOUNT_JSON (contenu JSON ou chemin du fichier).

    Accepte une clé de compte de service classique, ou le fichier d'identifiants créé par
    google-github-actions/auth (fédération d'identité de charge de travail, sans clé).
    """
    from google.auth import identity_pool
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    raw = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    if info.get("type") == "service_account":
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        if subject:
            creds = creds.with_subject(subject)
    else:
        creds = identity_pool.Credentials.from_info(info, scopes=scopes)
    creds.refresh(Request())
    return creds.token


def parse_ts(value) -> datetime | None:
    """Accepte un timestamp Unix, une date ISO ou un datetime ; renvoie un datetime UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(value) -> str | None:
    dt = parse_ts(value)
    return dt.isoformat() if dt else None
