"""Diffusion des alertes et du rapport : Slack (mise en forme Block Kit, PDF joint) et/ou e-mail.

Slack :
  - SLACK_WEBHOOK_URL suffit pour les alertes mises en forme ;
  - pour joindre le PDF, il faut un jeton de bot (SLACK_BOT_TOKEN, droits chat:write et files:write)
    et l'identifiant du canal (SLACK_CHANNEL_ID). Sans eux, le message renvoie vers le rapport en ligne.
"""
from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests

from .brand import SEVERITY, day, num

PER_ACCOUNT = ("essai_chaud:", "essai_bloque:", "risque:", "renouvellement:")
GROUP_TITLES = {
    "essai_chaud:": "Essais à conclure",
    "essai_bloque:": "Essais sans activation",
    "risque:": "Clients à risque de départ",
    "renouvellement:": "Renouvellements à venir",
}


def report_url() -> str | None:
    if os.environ.get("DASHBOARD_URL"):
        return os.environ["DASHBOARD_URL"]
    if os.environ.get("GITHUB_RUN_ID"):
        return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return None


def to_slack_mrkdwn(text: str) -> str:
    """Markdown standard -> mrkdwn Slack (gras, titres, listes)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#+\s*(.+)$", r"*\1*", text, flags=re.M)
    return re.sub(r"^\s*[-•]\s+", "• ", text, flags=re.M)


# ---------------------------------------------------------------- mise en forme
def _alert_attachment(a) -> dict:
    sev = SEVERITY.get(a.severite, SEVERITY["info"])
    return {
        "color": sev["color"],
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{a.titre}*\n{a.detail}\n*Action recommandée :* {a.action}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{sev['label']}  ·  {a.etape}"}]},
        ],
    }


def _group_attachment(prefix: str, items: list) -> dict:
    names = [a.titre.split(" : ", 1)[-1] for a in items]
    shown = "\n".join(f"• {n}" for n in names[:8]) + (f"\n… et {len(names) - 8} autre(s)" if len(names) > 8 else "")
    sev = SEVERITY.get(items[0].severite, SEVERITY["info"])
    return {
        "color": sev["color"],
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{GROUP_TITLES[prefix]} ({len(items)})*\n{shown}\n*Action recommandée :* {items[0].action}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{sev['label']}  ·  {items[0].etape}  ·  détail dans le rapport"}]},
        ],
    }


def alerts_message(alerts: list, title: str, intro: str | None = None, limit: int = 12) -> dict:
    general = [a for a in alerts if not a.key.startswith(PER_ACCOUNT)]
    groups = {p: [a for a in alerts if a.key.startswith(p)] for p in PER_ACCOUNT}
    attachments = [_alert_attachment(a) for a in general[:limit]]
    attachments += [_group_attachment(p, items) for p, items in groups.items() if items]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title}}]
    if intro:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": intro}})
    counts = {s: sum(1 for a in alerts if a.severite == s) for s in ("haute", "moyenne", "info")}
    summary_line = "  ·  ".join(f"{SEVERITY[s]['label']} : {n}" for s, n in counts.items() if n) or "Aucune alerte"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": summary_line}]})
    url = report_url()
    if url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Ouvrir le rapport"}, "url": url, "style": "primary"}]})
    if len(general) > limit:
        attachments.append({"color": "#C9D3DF", "blocks": [{"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"{len(general) - limit} autre(s) point(s) d'attention dans le rapport."}]}]})
    return {"text": title, "blocks": blocks, "attachments": attachments}


# ---------------------------------------------------------------- envois
def _slack_post(payload: dict) -> bool:
    token, channel, hook = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_CHANNEL_ID"), os.environ.get("SLACK_WEBHOOK_URL")
    if token and channel:
        r = requests.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {token}"},
                          json={"channel": channel, **payload}, timeout=30).json()
        if not r.get("ok"):
            raise RuntimeError(f"Slack chat.postMessage : {r.get('error')}")
        return True
    if hook:
        requests.post(hook, json=payload, timeout=30).raise_for_status()
        return True
    return False


def _slack_upload(pdf: Path, comment: str) -> bool:
    token, channel = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_CHANNEL_ID")
    if not (token and channel):
        return False
    h = {"Authorization": f"Bearer {token}"}
    data = pdf.read_bytes()
    r = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=h,
                      data={"filename": pdf.name, "length": len(data)}, timeout=30).json()
    if not r.get("ok"):
        raise RuntimeError(f"Slack files.getUploadURLExternal : {r.get('error')}")
    requests.post(r["upload_url"], files={"file": (pdf.name, data, "application/pdf")}, timeout=60).raise_for_status()
    done = requests.post("https://slack.com/api/files.completeUploadExternal", headers=h, json={
        "files": [{"id": r["file_id"], "title": pdf.stem.replace("-", " ").capitalize()}],
        "channel_id": channel, "initial_comment": comment,
    }, timeout=30).json()
    if not done.get("ok"):
        raise RuntimeError(f"Slack files.completeUploadExternal : {done.get('error')}")
    return True


def _email(subject: str, body: str, pdf: Path | None = None) -> bool:
    if not (os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO")):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(re.sub(r"\*", "", body))
    if pdf:
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return True


def _plain(alerts: list) -> str:
    return "\n\n".join(f"[{SEVERITY.get(a.severite, SEVERITY['info'])['label']}] {a.titre}\n{a.detail}\nAction recommandée : {a.action}"
                       for a in alerts) or "Aucune alerte."


def send_alerts(alerts: list, snapshot: dict) -> list[str]:
    """Message quotidien : uniquement les nouvelles alertes."""
    if not alerts:
        return []
    title = f"Flugia · Alertes du {day(snapshot['au'], True)}"
    sent = []
    if _slack_post(alerts_message(alerts, title)):
        sent.append("slack")
    if _email(title, _plain(alerts)):
        sent.append("email")
    if not sent:
        print(title + "\n\n" + _plain(alerts))
    return sent


def send_report(pdf: Path | None, summary: str | None, snapshot: dict, alerts: list) -> list[str]:
    """Rapport hebdomadaire : synthèse + PDF joint (si le jeton de bot Slack est configuré) + alertes en cours."""
    p = snapshot["periode"]
    title = f"Flugia · Rapport de pilotage, semaine du {day(p['debut'])} au {day(p['fin'], True)}"
    intro = to_slack_mrkdwn(summary) if summary else None
    sent = []
    uploaded = False
    if pdf and pdf.exists():
        try:
            uploaded = _slack_upload(pdf, f"*{title}*\nRapport complet en pièce jointe.")
        except Exception as exc:  # le PDF ne doit pas empêcher l'envoi du message
            print(f"Envoi du PDF sur Slack impossible : {exc}")
    if not uploaded and pdf and report_url():
        intro = (intro + "\n\n" if intro else "") + f"Le rapport PDF est disponible dans l'exécution du jour : {report_url()}"
    if _slack_post(alerts_message(alerts, title, intro)):
        sent.append("slack" + (" + PDF" if uploaded else ""))
    if _email(title, (summary or "") + "\n\n" + _plain(alerts), pdf if pdf and pdf.exists() else None):
        sent.append("email")
    if not sent:
        print(title + "\n\n" + (summary or "") + "\n\n" + _plain(alerts))
    return sent


def send(title: str, body: str) -> list[str]:
    """Message simple (test de configuration)."""
    sent = []
    if _slack_post({"text": title, "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}}]}):
        sent.append("slack")
    if _email(title, body):
        sent.append("email")
    if not sent:
        print(f"{title}\n{body}")
    return sent


def format_alerts(alerts) -> str:  # compatibilité
    return _plain(alerts)
