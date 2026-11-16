from __future__ import annotations

import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from eventsourcing.persistence import PersistenceError

from banking.domain import describe, event_ref, event_type_name, signed_amount
from banking.money import fmt, plain, to_millimes
from readmodel.models import (
    AccountSummary,
    DailyBankStats,
    EscrowHold,
    LedgerEntry,
    ProjectionCheckpoint,
    WebhookOutbox,
)

log = logging.getLogger("ledger.projector")

PROJECTION_NAME = "ledger_read_model_v1"


class LedgerProjector:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.large_transfer = to_millimes(Decimal(settings.ALERT_LARGE_TRANSFER_TND))
        self.low_balance = to_millimes(Decimal(settings.ALERT_LOW_BALANCE_TND))

    def run_once(self, batch_size: int = 200, track_lag: bool = True) -> int:
        checkpoint, _ = ProjectionCheckpoint.objects.get_or_create(name=PROJECTION_NAME)
        notifications = self.app.recorder.select_notifications(start=checkpoint.position + 1, limit=batch_size)
        if not notifications:
            return 0
        processed = 0
        with transaction.atomic():
            checkpoint = ProjectionCheckpoint.objects.select_for_update().get(name=PROJECTION_NAME)
            for notification in notifications:
                if notification.id <= checkpoint.position:
                    continue
                domain_event = self.app.mapper.to_domain_event(notification)
                self.apply(notification.id, domain_event)
                checkpoint.position = notification.id
                checkpoint.events_processed += 1
                processed += 1
                if track_lag:
                    age_ms = (timezone.now() - domain_event.timestamp).total_seconds() * 1000
                    if 0 <= age_ms < 60_000:
                        checkpoint.last_lag_ms = round(age_ms, 1)
            checkpoint.save()
        return processed

    def catch_up(self) -> int:
        total = 0
        while True:
            n = self.run_once(track_lag=False)
            total += n
            if n == 0:
                return total

    def run_forever(self, poll_seconds: float | None = None) -> None:
        from readmodel.webhooks import deliver_pending

        poll = poll_seconds if poll_seconds is not None else settings.PROJECTOR_POLL_SECONDS
        log.info("Projector started (poll every %ss). Press Ctrl+C to stop.", poll)
        while True:
            try:
                n = self.run_once()
                if n:
                    log.info("Projected %d event(s)", n)
                deliver_pending()
                if n == 0:
                    time.sleep(poll)
            except (OperationalError, ProgrammingError, PersistenceError, ProjectionCheckpoint.DoesNotExist) as exc:
                log.warning("Database not ready (%s) - retrying in 2 s", (str(exc).splitlines() or ["?"])[0])
                close_old_connections()
                time.sleep(2)
            except Exception:
                log.exception("Unexpected projector error - retrying in 2 s")
                close_old_connections()
                time.sleep(2)

    def apply(self, position: int, e: Any) -> None:
        name = event_type_name(e)
        account_id = e.originator_id
        version = e.originator_version
        eid = event_ref(account_id, version)

        if name == "AccountOpened":
            summary = AccountSummary.objects.create(
                account_id=account_id, short_id=str(account_id)[:8], owner_name=e.owner_name,
                account_type=e.account_type, currency=e.currency, opened_at=e.timestamp,
                last_event_at=e.timestamp,
            )
        else:
            summary = AccountSummary.objects.get(account_id=account_id)

        available_before = summary.available_balance
        delta = signed_amount(e)
        summary.ledger_balance += delta
        business_ref, counterparty_id, counterparty_name, reference = "", "", "", getattr(e, "reference", "")

        if name == "FundsDeposited":
            summary.total_deposited += e.amount
        elif name == "FundsWithdrawn":
            summary.total_withdrawn += e.amount
        elif name == "FeeCharged":
            summary.total_fees += e.amount
            business_ref, reference = e.fee_id, e.reason
        elif name == "FeeReversed":
            summary.total_fees_reversed += e.amount
            business_ref, reference = e.fee_id, e.reason
        elif name == "FundsReserved":
            summary.held_amount += e.amount
            summary.active_holds += 1
            business_ref, reference, counterparty_id = e.hold_id, e.reason, e.beneficiary_id
            counterparty_name = self._owner(counterparty_id)
            EscrowHold.objects.create(
                hold_id=e.hold_id, account_id=account_id, owner_name=summary.owner_name,
                beneficiary_id=e.beneficiary_id, beneficiary_name=counterparty_name, amount=e.amount,
                reason=e.reason, placed_at=e.timestamp, placed_event_id=eid,
            )
        elif name == "HoldReleased":
            self._close_hold(summary, e.hold_id, "RELEASED", e.timestamp, eid)
            business_ref, reference = e.hold_id, e.reason
        elif name == "TransferSettled":
            business_ref = e.transfer_id
            counterparty_id = e.counterparty_id
            counterparty_name = self._owner(counterparty_id)
            if e.direction == "OUT":
                summary.total_transfers_out += e.amount
                if e.hold_id:
                    self._close_hold(summary, e.hold_id, "SETTLED", e.timestamp, eid)
            else:
                summary.total_transfers_in += e.amount

        summary.available_balance = summary.ledger_balance - summary.held_amount
        summary.event_count += 1
        summary.last_version = version
        summary.last_event_at = e.timestamp
        summary.save()

        LedgerEntry.objects.create(
            position=position, account_id=account_id, owner_name=summary.owner_name, version=version,
            event_id=eid, event_type=name, timestamp=e.timestamp, amount=delta,
            balance_after=summary.ledger_balance, available_after=summary.available_balance,
            description=describe(e)[:255], reference=(reference or "")[:160],
            counterparty_id=counterparty_id or "", counterparty_name=counterparty_name or "",
            business_ref=business_ref,
        )
        self._daily(e, name)
        self._alerts(e, name, eid, position, summary, available_before)

    def _owner(self, account_id: str) -> str:
        if not account_id:
            return ""
        row = AccountSummary.objects.filter(account_id=account_id).values_list("owner_name", flat=True).first()
        return row or ""

    def _close_hold(self, summary: AccountSummary, hold_id: str, status: str, when, eid: str) -> None:
        hold = EscrowHold.objects.get(hold_id=hold_id)
        hold.status, hold.closed_at, hold.closed_event_id = status, when, eid
        hold.save()
        summary.held_amount -= hold.amount
        summary.active_holds -= 1

    def _daily(self, e: Any, name: str) -> None:
        day = timezone.localtime(e.timestamp).date()
        stats, _ = DailyBankStats.objects.get_or_create(day=day)
        stats.event_count += 1
        if name == "FundsDeposited":
            stats.deposits += e.amount
        elif name == "FundsWithdrawn":
            stats.withdrawals += e.amount
        elif name == "TransferSettled" and e.direction == "OUT":
            stats.transfers += e.amount
        elif name == "FeeCharged":
            stats.fees += e.amount
        elif name == "FeeReversed":
            stats.fees_reversed += e.amount
        stats.save()

    def _alerts(self, e: Any, name: str, eid: str, position: int, summary: AccountSummary,
                available_before: int) -> None:
        alerts: list[tuple[str, str, str]] = []
        if name == "TransferSettled" and e.direction == "OUT" and e.amount >= self.large_transfer:
            alerts.append(("LARGE_TRANSFER", "HIGH",
                           f"Large transfer of {fmt(e.amount)} from {summary.owner_name} ({e.transfer_id})."))
        if name == "FeeReversed":
            alerts.append(("FEE_REVERSED", "MEDIUM",
                           f"Fee {e.fee_id} of {fmt(e.amount)} reversed for {summary.owner_name}: {e.reason}"))
        if available_before >= self.low_balance > summary.available_balance:
            alerts.append(("LOW_AVAILABLE_BALANCE", "MEDIUM",
                           f"Available balance of {summary.owner_name} fell to {fmt(summary.available_balance)}."))
        for rule, severity, message in alerts:
            payload = {
                "rule": rule, "severity": severity, "message": message, "event_id": eid,
                "event_type": name, "position": position, "account_id": str(e.originator_id),
                "owner_name": summary.owner_name, "amount_tnd": plain(getattr(e, "amount", 0)),
                "timestamp": e.timestamp.isoformat(),
            }
            WebhookOutbox.objects.get_or_create(
                rule=rule, event_id=eid,
                defaults={"severity": severity, "account_id": e.originator_id,
                          "next_attempt_at": timezone.now(), "payload": payload},
            )


def reset_read_model() -> None:
    with transaction.atomic():
        ProjectionCheckpoint.objects.get_or_create(name=PROJECTION_NAME)
        ProjectionCheckpoint.objects.select_for_update().get(name=PROJECTION_NAME)
        for model in (LedgerEntry, EscrowHold, DailyBankStats, AccountSummary):
            model.objects.all().delete()
        ProjectionCheckpoint.objects.filter(name=PROJECTION_NAME).delete()


def projection_status(app: Any) -> dict[str, Any]:
    head = app.recorder.max_notification_id() or 0
    cp = ProjectionCheckpoint.objects.filter(name=PROJECTION_NAME).first()
    position = cp.position if cp else 0
    stale = cp is None or (head > position and timezone.now() - cp.updated_at > timedelta(seconds=10))
    return {
        "head": head,
        "position": position,
        "lag_events": max(head - position, 0),
        "last_lag_ms": cp.last_lag_ms if cp else None,
        "updated_at": cp.updated_at if cp else None,
        "projector_stalled": stale,
    }

