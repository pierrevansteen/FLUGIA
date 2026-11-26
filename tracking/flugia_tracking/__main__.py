"""Point d'entrée : python -m flugia_tracking <commande>

  status       quelles sources sont configurées
  collect      collecte les données (par défaut les 7 derniers jours, re-collectés chaque nuit)
  run          collecte + KPIs + alertes + notification + tableau de bord (ce que lance GitHub Actions)
  dashboard    régénère le tableau de bord sans collecter
  demo         remplit une base de démonstration et génère le tableau de bord (données fictives)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import alerts as alerts_mod
from . import dashboard, kpis, notify
from .collectors import ALL_COLLECTORS
from .db import DB, ROOT


def load_dotenv():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cmd_status(_args):
    print("Sources :")
    for cls in ALL_COLLECTORS:
        c = cls()
        state = "✓ configurée" if c.enabled() else "✗ manque " + ", ".join(c.missing_env())
        print(f"  {c.label:45s} {state}")
    print("\nSorties :")
    print("  Slack :", "✓" if os.environ.get("SLACK_WEBHOOK_URL") else "✗ (SLACK_WEBHOOK_URL)")
    print("  E-mail :", "✓" if os.environ.get("SMTP_HOST") else "✗ (SMTP_HOST, ALERT_EMAIL_TO...)")
    print("  Base :", "Postgres" if os.environ.get("DATABASE_URL", "").startswith("postgres") else "SQLite locale (data/tracking.db)")


def collect(db: DB, days: int, only: list[str] | None) -> list[str]:
    end = datetime.now(timezone.utc).date() - timedelta(days=1)  # hier : journée complète
    start = end - timedelta(days=days - 1)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors = []
    for cls in ALL_COLLECTORS:
        c = cls()
        if only and c.name not in only:
            continue
        if only and c.name == "geo":
            c.forced = True
        if not c.enabled():
            continue
        print(f"→ {c.label} ({start} → {end})", flush=True)
        try:
            res = c.collect(start, end)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with db.transaction():
                db.upsert_metrics([(m.day, c.name, m.channel, m.metric, m.value, now) for m in res.metrics])
                if res.accounts:
                    db.upsert_accounts(res.accounts, now)
                db.log_run(run_at, c.name, "ok", len(res.metrics) + len(res.accounts), " | ".join(res.warnings))
            print(f"   ✓ {len(res.metrics)} métriques, {len(res.accounts)} comptes" + (f" ({'; '.join(res.warnings)})" if res.warnings else ""))
        except Exception as exc:  # une source en panne ne bloque pas les autres
            db.conn.rollback()
            db.log_run(run_at, c.name, "erreur", 0, f"{type(exc).__name__}: {exc}")
            db.commit()
            errors.append(f"{c.label} : {exc}")
            print(f"   ✗ {exc}", file=sys.stderr)
    return errors


def cmd_collect(args):
    db = DB()
    collect(db, args.days, args.only.split(",") if args.only else None)


def cmd_run(args):
    db = DB()
    collect(db, args.days, args.only.split(",") if args.only else None)
    snap = kpis.compute(db)
    all_alerts = alerts_mod.evaluate(snap)
    weekly = args.weekly or date.today().weekday() == 0
    new_alerts = alerts_mod.only_new(db, all_alerts)

    summary = None
    if weekly and os.environ.get("ANTHROPIC_API_KEY") and not args.no_ai:
        from .analyst import write_summary
        summary = write_summary(snap, all_alerts)

    dashboard.build(snap, all_alerts, summary)
    print(f"Tableau de bord : {dashboard.OUTPUT}")

    if args.no_notify:
        return
    if weekly:
        body = (summary + "\n\n" if summary else "") + "*Alertes en cours*\n" + notify.format_alerts(all_alerts)
        notify.send(f"Flugia · Pilotage de la semaine ({snap['periode']['debut']} → {snap['periode']['fin']})", body)
    elif new_alerts:
        notify.send(f"Flugia · {len(new_alerts)} nouvelle(s) alerte(s)", notify.format_alerts(new_alerts))


def cmd_dashboard(args):
    db = DB()
    snap = kpis.compute(db)
    dashboard.build(snap, alerts_mod.evaluate(snap))
    print(f"Tableau de bord : {dashboard.OUTPUT}")


def cmd_demo(args):
    from .demo import seed

    path = ROOT / "data" / "demo.db"
    if path.exists():
        path.unlink()
    db = DB(f"sqlite:///{path}")
    as_of = seed(db)
    snap = kpis.compute(db, as_of)
    al = alerts_mod.evaluate(snap)
    out = Path(args.output) if args.output else ROOT / "site" / "demo.html"
    summary = ("Exemple de synthèse : la visibilité progresse (+12 %), portée par LinkedIn, mais les inscriptions "
               "stagnent et le taux visite → inscription recule. Priorités : 1) appeler les 4 essais chauds, "
               "2) débloquer les essais sans outil connecté, 3) contacter les 2 clients dont l'usage chute.")
    dashboard.build(snap, al, summary, demo=True, output=out)
    print(f"{len(al)} alertes de démonstration\nTableau de bord de démonstration : {out}")


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(prog="flugia_tracking", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    for name, func in (("collect", cmd_collect), ("run", cmd_run)):
        sp = sub.add_parser(name)
        sp.add_argument("--days", type=int, default=7, help="nombre de jours à (re)collecter (90 pour un historique)")
        sp.add_argument("--only", help="liste de sources, ex. google_ads,meta")
        if name == "run":
            sp.add_argument("--weekly", action="store_true", help="forcer la synthèse hebdomadaire")
            sp.add_argument("--no-notify", action="store_true")
            sp.add_argument("--no-ai", action="store_true", help="ne pas appeler Claude pour la synthèse")
        sp.set_defaults(func=func)
    sub.add_parser("dashboard").set_defaults(func=cmd_dashboard)
    sp = sub.add_parser("demo")
    sp.add_argument("--output")
    sp.set_defaults(func=cmd_demo)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
