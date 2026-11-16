from datetime import datetime, timedelta, timezone

import pytest

from banking import clock
from banking.exceptions import ConcurrencyConflict, InsufficientFunds
from banking.money import to_millimes as m


def test_transfer_appends_events_on_both_streams_atomically(app, two_accounts):
    alice, bob = two_accounts
    result = app.transfer(alice, bob, m("300"), "rent")
    assert {(e.account_id, e.event_type) for e in result.events} == {(alice, "TransferSettled"), (bob, "TransferSettled")}
    assert app.get_account(alice).balance == m("700")
    assert app.get_account(bob).balance == m("500")


def test_failed_transfer_writes_nothing(app, two_accounts):
    alice, bob = two_accounts
    head = app.recorder.max_notification_id()
    with pytest.raises(InsufficientFunds):
        app.transfer(bob, alice, m("999999"))
    assert app.recorder.max_notification_id() == head


def test_expected_version_mismatch_is_a_concurrency_conflict(app, two_accounts):
    alice, _ = two_accounts
    version_seen_on_screen = app.get_account(alice).version
    app.deposit(alice, m("1"))
    with pytest.raises(ConcurrencyConflict) as err:
        app.withdraw(alice, m("10"), expected_version=version_seen_on_screen)
    assert err.value.actual_version == version_seen_on_screen + 1


def test_race_exactly_one_writer_wins(app, two_accounts):
    alice, _ = two_accounts
    before = app.get_account(alice).version
    outcome = app.demonstrate_race(alice, m("10"))
    assert sorted(v.split()[0] for v in outcome.values()) == ["REJECTED", "SAVED"]
    assert app.get_account(alice).version == before + 1


def test_snapshot_every_n_events_and_rebuild_equivalence(app, two_accounts):
    alice, _ = two_accounts
    for i in range(12):
        app.deposit(alice, m("1"))
    account = app.get_account(alice)
    assert account.version == 14
    assert app.snapshot_versions(alice) == [5, 10]
    full = app.rebuild_without_snapshot(alice)
    assert (full.balance, full.version) == (account.balance, account.version)


def test_time_travel_returns_balance_at_any_past_moment(app):
    t0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    with clock.simulated_time(t0):
        acc = app.open_account("Time Traveller", initial_deposit=m("100")).account_id
    for day in range(1, 8):
        with clock.simulated_time(t0 + timedelta(days=day)):
            app.deposit(acc, m("10"))
    r = app.state_at(acc, t0 + timedelta(days=3, hours=1))
    assert r.account.balance == m("130") and r.version == 5
    assert r.snapshot_version == 5 and r.replayed_events == 0
    r = app.state_at(acc, t0 + timedelta(days=6, hours=1))
    assert r.account.balance == m("160") and r.snapshot_version == 5 and r.replayed_events == 3
    assert app.state_at(acc, t0 - timedelta(days=1)).account is None


def test_replay_steps_is_the_left_fold(app, two_accounts):
    alice, _ = two_accounts
    app.charge_fee(alice, m("5"))
    steps = app.replay_steps(alice)
    assert [s["version"] for s in steps] == [1, 2, 3]
    assert steps[-1]["balance"] == app.get_account(alice).balance == m("995")


def test_legacy_v1_event_is_upcast_on_read(app, two_accounts):
    _, bob = two_accounts
    app.import_legacy_v1_deposit(bob, m("50"), "old system")
    last = app.get_events(bob)[-1]
    assert type(last).__name__ == "FundsDeposited"
    assert last.channel == "LEGACY-UNKNOWN"
    stored = app.recorder.select_events(bob)[-1]
    assert b"channel" not in stored.state
    assert app.get_account(bob).balance == m("250")


def test_demo_ids_are_reproducible_on_every_computer():
    from banking import identity
    from banking.application import BankingApplication

    def build():
        a = BankingApplication(env={"PERSISTENCE_MODULE": "eventsourcing.popo"})
        with identity.reproducible():
            x = a.open_account("Same Owner", initial_deposit=m("10")).account_id
            y = a.open_account("Other Owner", initial_deposit=m("10")).account_id
            hold = a.reserve_funds(x, m("5"), y, "escrow").extra["hold_id"]
        return x, y, hold

    assert build() == build()
    first, second = build()[:2]
    assert first != second


def test_escrow_settlement_pays_beneficiary(app, two_accounts):
    alice, bob = two_accounts
    hold = app.reserve_funds(alice, m("400"), bob, "escrow").extra["hold_id"]
    assert app.get_account(alice).available == m("600")
    app.settle_escrow(alice, hold, "deed signed")
    assert app.get_account(alice).balance == m("600")
    assert app.get_account(bob).balance == m("600")
    assert app.get_account(alice).holds == {}

