"""Optional push of Tier-1 alert candidates: a webhook (Slack-compatible JSON) and/or e-mail."""
from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx

from .config import Config
from .store import Store

log = logging.getLogger("newsflow.alerts")


def _format_alert(a: dict[str, Any]) -> str:
    cats = ", ".join(json.loads(a.get("alert_categories") or "[]"))
    when = a.get("published_at") or a.get("first_seen_at")
    return f"[{a.get('name_ids')}] {a['title']}\n{a.get('source_name') or a.get('source_domain')} · {a.get('country')} · {a.get('lang')} · {when} · {cats}\n{a['canonical_url']}"


def push_alerts(cfg: Config, store: Store, now: datetime | None = None) -> int:
    """Send unnotified alert candidates from the last `alerts.lookback_hours`. Returns count sent."""
    if not cfg.alerts.get("enabled", False):
        return 0
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=float(cfg.alerts.get("lookback_hours", 24)))
    alerts = store.unnotified_alerts(since)
    if not alerts:
        return 0
    text = "\n\n".join(_format_alert(a) for a in alerts)
    sent = False
    webhook = cfg.alerts.get("webhook_url") or os.environ.get("NEWSFLOW_WEBHOOK_URL", "")
    if webhook:
        try:
            httpx.post(webhook, json={"text": f"Newsflow alert candidates ({len(alerts)}):\n\n{text}"}, timeout=15)
            sent = True
        except httpx.HTTPError as exc:
            log.warning("webhook failed: %s", exc)
    smtp = cfg.alerts.get("smtp") or {}
    if smtp.get("host") and smtp.get("to"):
        try:
            msg = EmailMessage()
            msg["Subject"] = f"Newsflow: {len(alerts)} alert candidate(s)"
            msg["From"] = smtp.get("from", smtp.get("user", ""))
            msg["To"] = ", ".join(smtp["to"])
            msg.set_content(text)
            with smtplib.SMTP(smtp["host"], int(smtp.get("port", 587))) as s:
                s.starttls()
                pw = os.environ.get(smtp.get("password_env", "NEWSFLOW_SMTP_PASSWORD"), "")
                if smtp.get("user"):
                    s.login(smtp["user"], pw)
                s.send_message(msg)
            sent = True
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("smtp failed: %s", exc)
    if sent:
        store.mark_notified([int(a["id"]) for a in alerts], now)
        return len(alerts)
    return 0
