"""Base de données de l'app Flugia (à brancher plus tard) : comptes, essais, activation, usage, churn.

Le schéma de votre base n'étant pas encore connu, la requête est écrite dans config/app_queries.yaml.
Elle doit renvoyer les colonnes de la table `accounts` (voir ce fichier). Accès en LECTURE SEULE recommandé.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .base import CollectResult, Collector, CollectorError, env, iso

CONFIG = Path(__file__).resolve().parents[2] / "config" / "app_queries.yaml"
DATE_FIELDS = ["created_at", "trial_ends_at", "activated_at", "converted_at", "churned_at", "contract_end_at"]


def _connect(url: str):
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg

        return psycopg.connect(url)
    if url.startswith("mysql://"):
        import pymysql  # pip install pymysql

        u = urlparse(url)
        return pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                               password=u.password, database=u.path.lstrip("/"))
    if url.startswith("sqlite:///"):
        return sqlite3.connect(url.removeprefix("sqlite:///"))
    raise CollectorError("APP_DATABASE_URL doit commencer par postgresql://, mysql:// ou sqlite:///")


class AppDBCollector(Collector):
    name = "app_db"
    label = "Base de l'app Flugia"
    required_env = ["APP_DATABASE_URL"]

    def collect(self, start, end) -> CollectResult:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        sql = (cfg.get("accounts") or "").strip()
        if not sql:
            raise CollectorError("config/app_queries.yaml : la requête 'accounts' est vide.")
        conn = _connect(env("APP_DATABASE_URL"))
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()

        result = CollectResult()
        for r in rows:
            if not r.get("account_id"):
                continue
            acc = {k: r.get(k) for k in r}
            acc["account_id"] = f"app:{r['account_id']}"
            for f in DATE_FIELDS:
                acc[f] = iso(r.get(f))
            acc["origin"] = "app"
            result.accounts.append(acc)
        return result
