from django.db import models


class AccountSummary(models.Model):
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
    position = models.BigIntegerField(unique=True)
    account_id = models.UUIDField(db_index=True)
    owner_name = models.CharField(max_length=80)
    version = models.IntegerField()
    event_id = models.CharField(max_length=24, db_index=True)
    event_type = models.CharField(max_length=32, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    amount = models.BigIntegerField(default=0)
    balance_after = models.BigIntegerField(default=0)
    available_after = models.BigIntegerField(default=0)
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=160, blank=True, default="")
    counterparty_id = models.CharField(max_length=36, blank=True, default="")
    counterparty_name = models.CharField(max_length=80, blank=True, default="")
    business_ref = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        ordering = ["-position"]
        constraints = [models.UniqueConstraint(fields=["account_id", "version"], name="uniq_account_version")]


class EscrowHold(models.Model):
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
    name = models.CharField(max_length=64, primary_key=True)
    position = models.BigIntegerField(default=0)
    events_processed = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    last_lag_ms = models.FloatField(null=True, blank=True)


class WebhookOutbox(models.Model):
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
    received_at = models.DateTimeField(auto_now_add=True)
    rule = models.CharField(max_length=32)
    event_id = models.CharField(max_length=24)
    payload = models.JSONField()
    signature_valid = models.BooleanField(default=False)

    class Meta:
        ordering = ["-received_at"]

