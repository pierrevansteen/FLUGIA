"""Accès à la base : Postgres (Supabase, etc.) si DATABASE_URL est défini, sinon SQLite local.

Toutes les requêtes du projet utilisent le marqueur `?` ; il est converti en `%s` pour Postgres.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE = ROOT / "data" / "tracking.db"

SCHEMA = [
    # Une ligne = une valeur de métrique, pour un jour, une source et un canal.
    """CREATE TABLE IF NOT EXISTS metrics_daily (
        day TEXT NOT NULL,
        source TEXT NOT NULL,
        channel TEXT NOT NULL,
        metric TEXT NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        collected_at TEXT NOT NULL,
        PRIMARY KEY (day, source, channel, metric)
    )""",
    # Un compte Flugia (essai ou client). Source de vérité : la base de l'app, sinon Stripe.
    """CREATE TABLE IF NOT EXISTS accounts (
        account_id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT,
        created_at TEXT,
        first_touch_source TEXT,
        first_touch_medium TEXT,
        first_touch_campaign TEXT,
        plan TEXT,
        status TEXT,
        trial_ends_at TEXT,
        activated_at TEXT,
        converted_at TEXT,
        churned_at TEXT,
        contract_end_at TEXT,
        mrr DOUBLE PRECISION,
        usage_7d DOUBLE PRECISION,
        usage_30d DOUBLE PRECISION,
        usage_prev_30d DOUBLE PRECISION,
        origin TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS collector_runs (
        run_at TEXT NOT NULL,
        collector TEXT NOT NULL,
        status TEXT NOT NULL,
        rows_written INTEGER,
        message TEXT
    )""",
    # Mémoire des alertes déjà envoyées, pour ne pas répéter la même chaque nuit.
    """CREATE TABLE IF NOT EXISTS alerts_log (
        alert_key TEXT PRIMARY KEY,
        first_sent_at TEXT NOT NULL,
        last_sent_at TEXT NOT NULL,
        payload TEXT
    )""",
    # Interface de pilotage : objectifs mensuels (onglet Paramétrages) et alertes marquées comme traitées.
    """CREATE TABLE IF NOT EXISTS ui_objectifs (
        cle TEXT PRIMARY KEY,
        valeur DOUBLE PRECISION,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ui_alertes_traitees (
        alert_key TEXT PRIMARY KEY,
        done_at TEXT NOT NULL,
        done_by TEXT
    )""",
    # Veille Crunchbase : paramètres et événements saisis dans l'interface, relevés de flugia.com.
    """CREATE TABLE IF NOT EXISTS ui_parametres (
        cle TEXT PRIMARY KEY,
        valeur TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ui_evenements (
        id TEXT PRIMARY KEY,
        cree_le TEXT NOT NULL,
        type TEXT NOT NULL,
        description TEXT,
        reporte_le TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS veille_site (
        champ TEXT PRIMARY KEY,
        valeur TEXT,
        vu_le TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS veille_changements (
        detecte_le TEXT NOT NULL,
        champ TEXT NOT NULL,
        avant TEXT,
        apres TEXT
    )""",
]

ACCOUNT_COLUMNS = [
    "account_id", "name", "email", "created_at", "first_touch_source", "first_touch_medium",
    "first_touch_campaign", "plan", "status", "trial_ends_at", "activated_at", "converted_at",
    "churned_at", "contract_end_at", "mrr", "usage_7d", "usage_30d", "usage_prev_30d", "origin",
]


class DB:
    def __init__(self, url: str | None = None, read_only: bool = False, create_tables: bool | None = None):
        """read_only=True : simple lecture (ex. interface en ligne avec un utilisateur Postgres en lecture seule),
        sans création des tables ni écriture."""
        self.url = url if url is not None else os.environ.get("DATABASE_URL", "")
        if self.url.startswith(("postgres://", "postgresql://")):
            import psycopg  # import tardif : inutile en local

            self.kind = "postgres"
            self.conn = psycopg.connect(self.url, autocommit=False)
            if read_only:
                self.conn.read_only = True
        else:
            path = Path(self.url.removeprefix("sqlite:///")) if self.url else DEFAULT_SQLITE
            path.parent.mkdir(parents=True, exist_ok=True)
            self.kind = "sqlite"
            self.conn = sqlite3.connect(path)
        # create_tables=False : écriture possible sans créer les tables (utilisateur aux droits limités de l'interface).
        if (not read_only) if create_tables is None else create_tables:
            self.init_schema()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def execute(self, sql: str, params: tuple | list = ()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def executemany(self, sql: str, rows: list):
        if not rows:
            return
        cur = self.conn.cursor()
        cur.executemany(self._sql(sql), rows)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        cur = self.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def init_schema(self):
        for stmt in SCHEMA:
            self.execute(stmt)
        self.commit()

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- écritures -------------------------------------------------------
    def upsert_metrics(self, rows: list[tuple]):
        """rows : (day, source, channel, metric, value, collected_at)"""
        self.executemany(
            """INSERT INTO metrics_daily (day, source, channel, metric, value, collected_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (day, source, channel, metric)
               DO UPDATE SET value = excluded.value, collected_at = excluded.collected_at""",
            rows,
        )

    def replace_metrics(self, source: str, rows: list[tuple]):
        """Remplace les données d'une source pour les jours re-collectés (gère les canaux renommés ou disparus)."""
        days = sorted({r[0] for r in rows})
        for i in range(0, len(days), 500):
            chunk = days[i:i + 500]
            self.execute(
                f"DELETE FROM metrics_daily WHERE source = ? AND day IN ({', '.join('?' for _ in chunk)})",
                [source, *chunk],
            )
        self.upsert_metrics(rows)

    def upsert_accounts(self, accounts: list[dict], updated_at: str):
        cols = ACCOUNT_COLUMNS + ["updated_at"]
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "account_id")
        rows = [tuple([a.get(c) for c in ACCOUNT_COLUMNS] + [updated_at]) for a in accounts]
        self.executemany(
            f"""INSERT INTO accounts ({", ".join(cols)}) VALUES ({placeholders})
                ON CONFLICT (account_id) DO UPDATE SET {updates}""",
            rows,
        )

    def log_run(self, run_at: str, collector: str, status: str, rows: int, message: str = ""):
        self.execute(
            "INSERT INTO collector_runs (run_at, collector, status, rows_written, message) VALUES (?, ?, ?, ?, ?)",
            (run_at, collector, status, rows, message[:2000]),
        )
