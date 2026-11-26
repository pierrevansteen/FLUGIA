"""Envoi des alertes et de la synthèse : Slack (webhook entrant, gratuit) et/ou e-mail (SMTP)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import requests

ICON = {"haute": "🔴", "moyenne": "🟠", "info": "🔵"}


def format_alerts(alerts, limit: int = 25) -> str:
    if not alerts:
        return "✅ Aucune nouvelle alerte."
    lines = []
    for a in alerts[:limit]:
        lines.append(f"{ICON.get(a.severite, '•')} *[{a.etape}] {a.titre}*\n   {a.detail}\n   👉 {a.action}")
    if len(alerts) > limit:
        lines.append(f"… et {len(alerts) - limit} autres (voir le tableau de bord).")
    return "\n".join(lines)


def send(title: str, body: str, dashboard_url: str | None = None) -> list[str]:
    dashboard_url = dashboard_url or os.environ.get("DASHBOARD_URL")
    text = f"*{title}*\n\n{body}" + (f"\n\n📊 Tableau de bord : {dashboard_url}" if dashboard_url else "")
    sent = []

    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook:
        resp = requests.post(hook, json={"text": text}, timeout=30)
        resp.raise_for_status()
        sent.append("slack")

    if os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO"):
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
        msg["To"] = os.environ["ALERT_EMAIL_TO"]
        msg.set_content(text.replace("*", ""))
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
            smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        sent.append("email")

    if not sent:
        print(text)  # aucun canal configuré : affichage dans les logs
    return sent
