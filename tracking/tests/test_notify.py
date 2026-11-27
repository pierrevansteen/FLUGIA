from pathlib import Path

from flugia_tracking import notify
from flugia_tracking.alerts import Alert

SNAP = {"au": "2026-11-26", "periode": {"debut": "2026-11-20", "fin": "2026-11-26"}}
ALERTS = [
    Alert("inscriptions_baisse:total", "Intérêt", "haute", "Inscriptions en baisse : −62 %", "Moyenne de 1,0…", "Tester le formulaire."),
    Alert("essai_chaud:A", "Acquisition", "haute", "Essai à conclure : Salon A", "Jour 12…", "Appeler le contact."),
    Alert("essai_chaud:B", "Acquisition", "haute", "Essai à conclure : Garage B", "Jour 11…", "Appeler le contact."),
]


class Resp:
    def __init__(self, data=None):
        self.data = data or {"ok": True}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def test_message_layout():
    msg = notify.alerts_message(ALERTS, "Flugia · Alertes du 26 nov. 2026")
    assert msg["blocks"][0]["type"] == "header"
    # 1 alerte générale + 1 groupe pour les 2 essais à conclure
    assert len(msg["attachments"]) == 2
    assert msg["attachments"][0]["color"] == "#C62F30"
    assert "Essais à conclure (2)" in msg["attachments"][1]["blocks"][0]["text"]["text"]
    assert "Salon A" in msg["attachments"][1]["blocks"][0]["text"]["text"]


def test_report_uploads_pdf_with_bot_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")

    def post(url, **kw):
        calls.append(url)
        if "getUploadURLExternal" in url:
            return Resp({"ok": True, "upload_url": "https://files.slack.test/up", "file_id": "F1"})
        return Resp()

    monkeypatch.setattr(notify.requests, "post", post)
    pdf = tmp_path / "rapport-flugia-2026-11-26.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sent = notify.send_report(pdf, "**En une phrase** : test.", SNAP, ALERTS)
    assert sent == ["slack + PDF"]
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["files.getUploadURLExternal", "up", "files.completeUploadExternal", "chat.postMessage"]


def test_report_with_webhook_only_links_to_run(monkeypatch, tmp_path):
    payloads = []
    for k in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID", "DASHBOARD_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/flugia")
    monkeypatch.setattr(notify.requests, "post", lambda url, json=None, **kw: payloads.append(json) or Resp())
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF")
    assert notify.send_report(pdf, "Synthèse", SNAP, ALERTS) == ["slack"]
    intro = payloads[0]["blocks"][1]["text"]["text"]
    assert "actions/runs/42" in intro


def test_markdown_to_slack():
    assert notify.to_slack_mrkdwn("**Gras**\n- point") == "*Gras*\n• point"
