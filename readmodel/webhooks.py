"""
WEBHOOKS - native event-driven workflow automation
==================================================

Plain-language summary
----------------------
A webhook is simply an HTTP POST that one program sends to another to say
"something happened".  When the projector sees an important event (a large
transfer, a fee reversal, an account running low), it writes an ALERT row in
the `WebhookOutbox` table - inside the same transaction as the read model.

This module then sends every pending alert as JSON to the URLs listed in the
WEBHOOK_URLS setting (by default our own demo receiver /webhooks/receive/,
visible on the "Alerts" page; you could also point it to n8n, Slack, Teams..).

* Each request is SIGNED with HMAC-SHA256 using WEBHOOK_SECRET, so the
  receiver can prove the alert really comes from our ledger.
* If the receiver is down, we retry later with exponential back-off
  (2, 4, 8, 16, 32 s); after 6 failed attempts the alert is marked FAILED.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx
from django.conf import settings
from django.utils import timezone

from readmodel.models import WebhookOutbox

log = logging.getLogger("ledger.webhooks")
MAX_ATTEMPTS = 6


def sign(body: bytes, secret: str | None = None) -> str:
    key = (secret or settings.WEBHOOK_SECRET).encode()
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, header_value: str, secret: str | None = None) -> bool:
    return hmac.compare_digest(sign(body, secret), header_value or "")


def deliver_pending(limit: int = 5) -> int:
    now = timezone.now()
    due = list(
        WebhookOutbox.objects.filter(status="PENDING", next_attempt_at__lte=now).order_by("created_at")[:limit]
    )
    delivered = 0
    for alert in due:
        body = json.dumps(alert.payload, sort_keys=True).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Ledger-Rule": alert.rule,
            "X-Ledger-Event-Id": alert.event_id,
            "X-Ledger-Signature": sign(body),
        }
        errors = []
        for url in settings.WEBHOOK_URLS:
            try:
                response = httpx.post(url, content=body, headers=headers, timeout=2.0)
                if response.status_code >= 300:
                    errors.append(f"{url} -> HTTP {response.status_code}")
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                errors.append(f"{url} -> {type(exc).__name__}")
        alert.attempts += 1
        if not errors:
            alert.status, alert.delivered_at, alert.last_error = "DELIVERED", timezone.now(), ""
            delivered += 1
        else:
            alert.last_error = "; ".join(errors)[:255]
            if alert.attempts >= MAX_ATTEMPTS:
                alert.status = "FAILED"
            else:
                alert.next_attempt_at = timezone.now() + timedelta(seconds=2 ** alert.attempts)
        alert.save()
    if delivered:
        log.info("Delivered %d webhook alert(s)", delivered)
    return delivered
