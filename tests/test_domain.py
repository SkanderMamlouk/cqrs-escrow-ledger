"""Step 1 - the aggregate alone (pure Python, no database, no Django)."""

import pytest

from banking.domain import BankAccount, event_ref, event_type_name, signed_amount
from banking.exceptions import InsufficientFunds, InvalidAmount, UnknownFee, UnknownHold


def new_account(balance=1000_000):
    a = BankAccount(owner_name="Test", account_type="CURRENT")
    if balance:
        a.deposit(balance, "init")
    return a


def test_opening_creates_version_1_event():
    a = BankAccount(owner_name="Amira", account_type="SAVINGS")
    events = a.collect_events()
    assert [event_type_name(e) for e in events] == ["AccountOpened"]
    assert a.version == 1 and a.balance == 0


def test_commands_emit_past_tense_events_and_increment_version():
    a = new_account()
    a.withdraw(200_000, "atm")
    names = [event_type_name(e) for e in a.collect_events()]
    assert names == ["AccountOpened", "FundsDeposited", "FundsWithdrawn"]
    assert a.version == 3 and a.balance == 800_000


@pytest.mark.parametrize("amount", [0, -5])
def test_invalid_amounts_are_refused_and_create_no_event(amount):
    a = new_account()
    a.collect_events()
    with pytest.raises(InvalidAmount):
        a.deposit(amount)
    assert a.collect_events() == []


def test_cannot_withdraw_more_than_available():
    a = new_account(100_000)
    with pytest.raises(InsufficientFunds):
        a.withdraw(100_001)


def test_escrow_hold_reduces_available_but_not_balance():
    a = new_account(1000_000)
    hold = a.reserve_funds(600_000, "beneficiary", "escrow")
    assert a.balance == 1000_000 and a.held == 600_000 and a.available == 400_000
    with pytest.raises(InsufficientFunds):
        a.withdraw(500_000)  # money is blocked
    a.release_hold(hold)
    assert a.available == 1000_000


def test_escrow_settlement_must_match_held_amount():
    a = new_account(1000_000)
    hold = a.reserve_funds(600_000, "b", "x")
    with pytest.raises(InvalidAmount):
        a.send_transfer("TRF-1", 500_000, "b", hold_id=hold)
    a.send_transfer("TRF-1", 600_000, "b", hold_id=hold)
    assert a.balance == 400_000 and a.holds == {}


def test_unknown_hold_refused():
    with pytest.raises(UnknownHold):
        new_account().release_hold("HLD-00000000")


def test_fee_can_be_reversed_only_once():
    a = new_account(100_000)
    fee = a.charge_fee(12_500, "monthly")
    assert a.balance == 87_500
    a.reverse_fee(fee, "goodwill")
    assert a.balance == 100_000
    with pytest.raises(UnknownFee):
        a.reverse_fee(fee, "again")


def test_state_is_a_left_fold_over_events():
    a = new_account(500_000)
    a.charge_fee(1_000)
    a.withdraw(99_000)
    events = a.collect_events()
    rebuilt = None
    for e in events:
        rebuilt = e.mutate(rebuilt)
    assert rebuilt.balance == a.balance == 400_000
    assert sum(signed_amount(e) for e in events) == a.balance


def test_event_ref_format():
    assert event_ref("7f3a9c21-aaaa-bbbb-cccc-000000000000", 7) == "EVT-7f3a9c21-0007"
