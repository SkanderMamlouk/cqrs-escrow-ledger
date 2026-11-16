from __future__ import annotations

from typing import Any
from uuid import UUID

from eventsourcing.domain import Aggregate, event

from banking import clock, identity
from banking.exceptions import InsufficientFunds, InvalidAmount, UnknownFee, UnknownHold
from banking.money import fmt


def new_ref(prefix: str) -> str:
    return f"{prefix}-{identity.new_hex8()}"


def event_ref(account_id: UUID | str, version: int) -> str:
    return f"EVT-{str(account_id)[:8]}-{version:04d}"


class BankAccount(Aggregate):
    class Event(Aggregate.Event):
        @staticmethod
        def create_timestamp():
            return clock.now()

    @staticmethod
    def create_id(**_kwargs) -> UUID:
        return identity.new_uuid()

    class AccountOpened(Aggregate.Created):
        owner_name: str
        account_type: str
        currency: str

    @event(AccountOpened)
    def __init__(self, owner_name: str, account_type: str, currency: str = "TND"):
        self.owner_name = owner_name
        self.account_type = account_type
        self.currency = currency
        self.balance = 0
        self.holds: dict[str, dict[str, Any]] = {}
        self.fees: dict[str, dict[str, Any]] = {}
        self.total_deposited = 0
        self.total_withdrawn = 0

    @property
    def held(self) -> int:
        return sum(h["amount"] for h in self.holds.values())

    @property
    def available(self) -> int:
        return self.balance - self.held

    class FundsDeposited(Aggregate.Event):
        amount: int
        reference: str
        channel: str

        class_version = 2

        @staticmethod
        def upcast_v1_v2(state: dict) -> None:
            state["channel"] = "LEGACY-UNKNOWN"

    def deposit(self, amount: int, reference: str = "", channel: str = "BRANCH") -> None:
        if amount <= 0:
            raise InvalidAmount("A deposit must be greater than zero.")
        self._on_deposited(amount=amount, reference=reference, channel=channel)

    @event(FundsDeposited)
    def _on_deposited(self, amount: int, reference: str, channel: str) -> None:
        self.balance += amount
        self.total_deposited += amount

    class FundsWithdrawn(Aggregate.Event):
        amount: int
        reference: str

    def withdraw(self, amount: int, reference: str = "") -> None:
        if amount <= 0:
            raise InvalidAmount("A withdrawal must be greater than zero.")
        if amount > self.available:
            raise InsufficientFunds("Not enough available funds for this withdrawal.")
        self._on_withdrawn(amount=amount, reference=reference)

    @event(FundsWithdrawn)
    def _on_withdrawn(self, amount: int, reference: str) -> None:
        self.balance -= amount
        self.total_withdrawn += amount

    class FundsReserved(Aggregate.Event):
        hold_id: str
        amount: int
        beneficiary_id: str
        reason: str

    def reserve_funds(self, amount: int, beneficiary_id: str = "", reason: str = "") -> str:
        if amount <= 0:
            raise InvalidAmount("A hold must be greater than zero.")
        if amount > self.available:
            raise InsufficientFunds("Not enough available funds to place this hold.")
        hold_id = new_ref("HLD")
        self._on_reserved(hold_id=hold_id, amount=amount, beneficiary_id=beneficiary_id, reason=reason)
        return hold_id

    @event(FundsReserved)
    def _on_reserved(self, hold_id: str, amount: int, beneficiary_id: str, reason: str) -> None:
        self.holds[hold_id] = {"amount": amount, "beneficiary_id": beneficiary_id, "reason": reason}

    class HoldReleased(Aggregate.Event):
        hold_id: str
        amount: int
        reason: str

    def release_hold(self, hold_id: str, reason: str = "") -> None:
        if hold_id not in self.holds:
            raise UnknownHold(f"No active hold {hold_id} on this account.")
        self._on_hold_released(hold_id=hold_id, amount=self.holds[hold_id]["amount"], reason=reason)

    @event(HoldReleased)
    def _on_hold_released(self, hold_id: str, amount: int, reason: str) -> None:
        del self.holds[hold_id]

    class TransferSettled(Aggregate.Event):
        transfer_id: str
        direction: str
        amount: int
        counterparty_id: str
        hold_id: str
        reference: str

    def send_transfer(
        self, transfer_id: str, amount: int, counterparty_id: str, hold_id: str = "", reference: str = ""
    ) -> None:
        if amount <= 0:
            raise InvalidAmount("A transfer must be greater than zero.")
        if hold_id:
            hold = self.holds.get(hold_id)
            if hold is None:
                raise UnknownHold(f"No active hold {hold_id} on this account.")
            if hold["amount"] != amount:
                raise InvalidAmount("An escrow settlement must pay exactly the held amount.")
        elif amount > self.available:
            raise InsufficientFunds("Not enough available funds for this transfer.")
        self._on_transfer(
            transfer_id=transfer_id, direction="OUT", amount=amount,
            counterparty_id=counterparty_id, hold_id=hold_id, reference=reference,
        )

    def receive_transfer(self, transfer_id: str, amount: int, counterparty_id: str, reference: str = "") -> None:
        if amount <= 0:
            raise InvalidAmount("A transfer must be greater than zero.")
        self._on_transfer(
            transfer_id=transfer_id, direction="IN", amount=amount,
            counterparty_id=counterparty_id, hold_id="", reference=reference,
        )

    @event(TransferSettled)
    def _on_transfer(
        self, transfer_id: str, direction: str, amount: int, counterparty_id: str, hold_id: str, reference: str
    ) -> None:
        if direction == "OUT":
            self.balance -= amount
            if hold_id:
                self.holds.pop(hold_id, None)
        else:
            self.balance += amount

    class FeeCharged(Aggregate.Event):
        fee_id: str
        amount: int
        reason: str

    def charge_fee(self, amount: int, reason: str = "") -> str:
        if amount <= 0:
            raise InvalidAmount("A fee must be greater than zero.")
        if amount > self.available:
            raise InsufficientFunds("Not enough available funds to charge this fee.")
        fee_id = new_ref("FEE")
        self._on_fee_charged(fee_id=fee_id, amount=amount, reason=reason)
        return fee_id

    @event(FeeCharged)
    def _on_fee_charged(self, fee_id: str, amount: int, reason: str) -> None:
        self.balance -= amount
        self.fees[fee_id] = {"amount": amount, "reason": reason, "reversed": False}

    class FeeReversed(Aggregate.Event):
        fee_id: str
        amount: int
        reason: str

    def reverse_fee(self, fee_id: str, reason: str = "") -> None:
        fee = self.fees.get(fee_id)
        if fee is None:
            raise UnknownFee(f"No fee {fee_id} on this account.")
        if fee["reversed"]:
            raise UnknownFee(f"Fee {fee_id} was already reversed.")
        self._on_fee_reversed(fee_id=fee_id, amount=fee["amount"], reason=reason)

    @event(FeeReversed)
    def _on_fee_reversed(self, fee_id: str, amount: int, reason: str) -> None:
        self.balance += amount
        self.fees[fee_id]["reversed"] = True


EVENT_TYPES = [
    "AccountOpened", "FundsDeposited", "FundsWithdrawn", "FundsReserved",
    "HoldReleased", "TransferSettled", "FeeCharged", "FeeReversed",
]


def event_type_name(domain_event: object) -> str:
    return type(domain_event).__name__


def signed_amount(domain_event: object) -> int:
    name = event_type_name(domain_event)
    amount = getattr(domain_event, "amount", 0)
    if name in ("FundsDeposited", "FeeReversed"):
        return amount
    if name in ("FundsWithdrawn", "FeeCharged"):
        return -amount
    if name == "TransferSettled":
        return amount if domain_event.direction == "IN" else -amount
    return 0


def describe(domain_event: object) -> str:
    name = event_type_name(domain_event)
    e: Any = domain_event
    if name == "AccountOpened":
        return f"{e.account_type.title()} account opened for {e.owner_name} ({e.currency})"
    if name == "FundsDeposited":
        return f"Deposit of {fmt(e.amount)} via {e.channel}" + (f" - {e.reference}" if e.reference else "")
    if name == "FundsWithdrawn":
        return f"Withdrawal of {fmt(e.amount)}" + (f" - {e.reference}" if e.reference else "")
    if name == "FundsReserved":
        return f"Escrow hold {e.hold_id} of {fmt(e.amount)} placed" + (f" - {e.reason}" if e.reason else "")
    if name == "HoldReleased":
        return f"Escrow hold {e.hold_id} of {fmt(e.amount)} released" + (f" - {e.reason}" if e.reason else "")
    if name == "TransferSettled":
        arrow = "sent to" if e.direction == "OUT" else "received from"
        via = f" (settles escrow {e.hold_id})" if e.hold_id else ""
        return f"Transfer {e.transfer_id}: {fmt(e.amount)} {arrow} account {e.counterparty_id[:8]}{via}"
    if name == "FeeCharged":
        return f"Fee {e.fee_id} of {fmt(e.amount)} charged" + (f" - {e.reason}" if e.reason else "")
    if name == "FeeReversed":
        return f"Fee {e.fee_id} of {fmt(e.amount)} reversed" + (f" - {e.reason}" if e.reason else "")
    return name

