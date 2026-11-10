"""
THE READ MODEL - denormalized SQL tables built FROM the events
================================================================

Plain-language summary
----------------------
Replaying events is perfect for correctness and history, but a dashboard
that lists 1,000 accounts cannot replay 1,000 event streams every second.

So CQRS adds a second, separate database with ready-to-display tables.
They are "denormalized": data is copied where it is needed (e.g. the owner
name is repeated on every statement line) so that every screen is ONE
simple SELECT with no joins and no computation.

IMPORTANT: these tables are NOT the source of truth.  Only the projector
(readmodel/projector.py) writes into them, by listening to the event log.
You can delete them at any time and rebuild them from the events:
    python manage.py rebuild_projections

All amounts are integers in millimes (1 TND = 1000 millimes).
"""

from django.db import models


class AccountSummary(models.Model):
    """One row per account: everything the account list / dashboard needs."""

    account_id = models.UUIDField(primary_key=True)
    short_id = models.CharField(max_length=8, db_index=True)
    owner_name = models.CharField(max_length=80)
    account_type = models.CharField(max_length=16)
    currency = models.CharField(max_length=3, default="TND")
    ledger_balance = models.BigIntegerField(default=0)
    held_amount = models.BigIntegerField(default=0)
    available_balance = models.BigIntegerField(default=0)
    total_deposited = models.BigIntegerField(default=0)
    total_withdrawn = models.BigIntegerField(default=0)
    total_transfers_in = models.BigIntegerField(default=0)
    total_transfers_out = models.BigIntegerField(default=0)
    total_fees = models.BigIntegerField(default=0)
    total_fees_reversed = models.BigIntegerField(default=0)
    active_holds = models.IntegerField(default=0)
    event_count = models.IntegerField(default=0)
    last_version = models.IntegerField(default=0)
    opened_at = models.DateTimeField()
    last_event_at = models.DateTimeField()

    class Meta:
        ordering = ["owner_name"]

    def __str__(self) -> str:
        return f"{self.owner_name} ({self.short_id})"


class LedgerEntry(models.Model):
    """One row per event = one line of the bank statement."""

    position = models.BigIntegerField(unique=True)  # global position in the event log
    account_id = models.UUIDField(db_index=True)
    owner_name = models.CharField(max_length=80)
    version = models.IntegerField()
    event_id = models.CharField(max_length=24, db_index=True)
    event_type = models.CharField(max_length=32, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    amount = models.BigIntegerField(default=0)  # signed effect on the balance
    balance_after = models.BigIntegerField(default=0)
    available_after = models.BigIntegerField(default=0)
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=160, blank=True, default="")
    counterparty_id = models.CharField(max_length=36, blank=True, default="")
    counterparty_name = models.CharField(max_length=80, blank=True, default="")
    business_ref = models.CharField(max_length=16, blank=True, default="")  # HLD-/TRF-/FEE- id

    class Meta:
        ordering = ["-position"]
        constraints = [models.UniqueConstraint(fields=["account_id", "version"], name="uniq_account_version")]


class EscrowHold(models.Model):
    """Current status of every escrow hold (ACTIVE -> RELEASED or SETTLED)."""

    hold_id = models.CharField(max_length=16, primary_key=True)
    account_id = models.UUIDField(db_index=True)
    owner_name = models.CharField(max_length=80)
    beneficiary_id = models.CharField(max_length=36, blank=True, default="")
    beneficiary_name = models.CharField(max_length=80, blank=True, default="")
    amount = models.BigIntegerField()
    reason = models.CharField(max_length=160, blank=True, default="")
    status = models.CharField(max_length=10, default="ACTIVE", db_index=True)
    placed_at = models.DateTimeField()
    placed_event_id = models.CharField(max_length=24)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_event_id = models.CharField(max_length=24, blank=True, default="")

    class Meta:
        ordering = ["-placed_at"]


class DailyBankStats(models.Model):
    """Pre-aggregated totals per day for the dashboard chart (no GROUP BY needed)."""

    day = models.DateField(primary_key=True)
    deposits = models.BigIntegerField(default=0)
    withdrawals = models.BigIntegerField(default=0)
    transfers = models.BigIntegerField(default=0)
    fees = models.BigIntegerField(default=0)
    fees_reversed = models.BigIntegerField(default=0)
    event_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["day"]


class ProjectionCheckpoint(models.Model):
    """Where the projector stopped reading the event log ("tracking record").

    It is updated in the SAME database transaction as the read tables, so
    after a crash the projector restarts exactly where it stopped: no event
    is lost and none is applied twice.
    """

    name = models.CharField(max_length=64, primary_key=True)
    position = models.BigIntegerField(default=0)
    events_processed = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    last_lag_ms = models.FloatField(null=True, blank=True)


class WebhookOutbox(models.Model):
    """Alerts waiting to be POSTed to webhook URLs (transactional outbox)."""

    STATUS = [("PENDING", "Pending"), ("DELIVERED", "Delivered"), ("FAILED", "Failed")]
    created_at = models.DateTimeField(auto_now_add=True)
    rule = models.CharField(max_length=32)
    severity = models.CharField(max_length=8, default="INFO")
    event_id = models.CharField(max_length=24)
    account_id = models.UUIDField()
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING", db_index=True)
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["rule", "event_id"], name="uniq_rule_event")]


class WebhookInbox(models.Model):
    """Webhooks received by our own demo endpoint /webhooks/receive/."""

    received_at = models.DateTimeField(auto_now_add=True)
    rule = models.CharField(max_length=32)
    event_id = models.CharField(max_length=24)
    payload = models.JSONField()
    signature_valid = models.BooleanField(default=False)

    class Meta:
        ordering = ["-received_at"]
