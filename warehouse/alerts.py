"""Sends a pipeline alert to a chat webhook, and always writes it to the alert log.

Set ALERT_WEBHOOK_URL to a free Discord or Slack incoming-webhook URL to get a message in a
channel. Without it the alert still goes to warehouse/data/alerts.log and the console, so a
failure is never silent.
"""
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

from warehouse import config

log = logging.getLogger("warehouse.alerts")


def send(title, details="", webhook_url=None, log_path=None):
    """Returns 'webhook' if the chat message was sent, otherwise 'log'."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = f"[retail warehouse] {title}" + (f"\n{details}" if details else "")
    log_path = log_path or config.ALERT_LOG
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{stamp}  {text}\n")
    log.error(text)
    url = (webhook_url if webhook_url is not None else os.environ.get("ALERT_WEBHOOK_URL", "")).strip()
    if not url:
        return "log"
    # Slack reads "text" and Discord reads "content"; each ignores the other's key
    body = json.dumps({"text": text[:3500], "content": text[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                          "User-Agent": "retail-warehouse"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return "webhook"
    except OSError as e:
        log.error("Could not send the alert to the webhook: %s", e)
        return "log"
