"""Step 2 - asynchronous read-model projector + Step 4 verification gate."""

import pytest

from banking.money import to_millimes as m
from readmodel.models import AccountSummary, EscrowHold, LedgerEntry, ProjectionCheckpoint, WebhookOutbox
from readmodel.projector import LedgerProjector, projection_status, reset_read_model
from readmodel.verification import verify

pytestmark = pytest.mark.django_db


def make_history(app):
    a = app.open_account("Yasmine T.", initial_deposit=m("100000")).account_id
    b = app.open_account("Atlas SARL", initial_deposit=m("500")).account_id
    hold = app.reserve_funds(a, m("85000"), b, "apartment escrow").extra["hold_id"]
    fee = app.charge_fee(a, m("45"), "duplicate").extra["fee_id"]
    app.reverse_fee(a, fee, "complaint")
    app.settle_escrow(a, hold, "deed signed")
    app.withdraw(b, m("85450"), "empty it")
    return a, b


def test_read_model_is_eventually_consistent(ledger_app):
    a, b = make_history(ledger_app)
    assert AccountSummary.objects.count() == 0  # nothing projected yet
    assert projection_status(ledger_app)["lag_events"] > 0
    LedgerProjector(ledger_app).catch_up()
    row = AccountSummary.objects.get(account_id=a)
    acc = ledger_app.get_account(a)
    assert (row.ledger_balance, row.held_amount, row.last_version) == (acc.balance, acc.held, acc.version)
    assert EscrowHold.objects.get(account_id=a).status == "SETTLED"
    assert projection_status(ledger_app)["lag_events"] == 0


def test_projector_is_idempotent_and_resumes_from_checkpoint(ledger_app):
    make_history(ledger_app)
    p = LedgerProjector(ledger_app)
    first = p.catch_up()
    assert first == ledger_app.recorder.max_notification_id()
    assert p.catch_up() == 0  # nothing applied twice
    assert LedgerEntry.objects.count() == first
    assert ProjectionCheckpoint.objects.get().position == first


def test_rebuild_from_events_gives_identical_read_model(ledger_app):
    make_history(ledger_app)
    p = LedgerProjector(ledger_app)
    p.catch_up()
    before = list(AccountSummary.objects.values_list("account_id", "ledger_balance", "available_balance"))
    reset_read_model()
    assert AccountSummary.objects.count() == 0
    p.catch_up()
    after = list(AccountSummary.objects.values_list("account_id", "ledger_balance", "available_balance"))
    assert before == after


def test_alert_rules_fill_the_outbox_once(ledger_app):
    make_history(ledger_app)
    p = LedgerProjector(ledger_app)
    p.catch_up()
    rules = set(WebhookOutbox.objects.values_list("rule", flat=True))
    assert {"LARGE_TRANSFER", "FEE_REVERSED", "LOW_AVAILABLE_BALANCE"} <= rules
    n = WebhookOutbox.objects.count()
    reset_read_model()
    p.catch_up()
    assert WebhookOutbox.objects.count() == n  # rebuild never duplicates alerts


def test_verification_gate_passes_then_detects_tampering(ledger_app):
    a, _ = make_history(ledger_app)
    LedgerProjector(ledger_app).catch_up()
    assert verify(ledger_app).passed
    AccountSummary.objects.filter(account_id=a).update(ledger_balance=1)  # corrupt the read model
    report = verify(ledger_app)
    assert not report.passed
    assert any("read model == event store" in c.name and not c.passed for c in report.checks)
