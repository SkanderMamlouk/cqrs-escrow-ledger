"""
THE WRITE MODEL - the BankAccount aggregate and its domain events
==================================================================

Plain-language summary
----------------------
A classic database stores ONE row per account and overwrites the balance
on every operation (UPDATE accounts SET balance = ...).  The history is lost.

Event Sourcing does the opposite: we never overwrite anything.  We APPEND a
small immutable fact - an EVENT - each time something happens:

    v1  AccountOpened     owner = "Yasmine Trabelsi"
    v2  FundsDeposited    +5,000.000 TND
    v3  FundsReserved     hold 2,000.000 TND for an escrow
    v4  TransferSettled   -2,000.000 TND to the seller
    ...

The current balance is not stored anywhere: it is COMPUTED by replaying the
events from v1 to the last one ("state = left-fold over events").

Vocabulary
----------
* COMMAND   - a request that MAY be refused ("please deposit 50 TND").
              Commands are the public methods below: deposit(), withdraw()...
              They check the business rules first.
* EVENT     - a fact that DID happen ("50 TND were deposited").  Written in
              the past tense, immutable, never deleted.
* AGGREGATE - the object that owns a stream of events and protects the
              business rules (here: one bank account).  Its "aggregate root"
              is this BankAccount class: all changes go through it.
* VERSION   - every event gets the next number (1, 2, 3...).  Two people
              cannot both write version 8: the event store refuses the
              second one.  That is OPTIMISTIC CONCURRENCY CONTROL.

How the code is organised
-------------------------
For every event there are two pieces:

    1. a public COMMAND method (e.g. `deposit`) that validates the request
       and raises an error if a rule is broken - no event is created then;
    2. a private method decorated with `@event(...)` (e.g. `_on_deposited`)
       that APPLIES the event to the state.  The library calls this same
       method again whenever it replays history, so it must contain no
       checks - only "how does this fact change the state".

This file imports nothing from Django: the business core is pure Python
(a test in tests/test_architecture.py makes sure it stays that way).

Official references used in this file
-------------------------------------
* eventsourcing library (source code, v9.5.6): https://github.com/pyeventsourcing/eventsourcing
* documentation: https://eventsourcing.readthedocs.io/
    - aggregates and the @event decorator: https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#event-decorator
    - event timestamps (create_timestamp):  https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#timestamp-timezones
    - aggregate ids (create_id):            https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#defining-the-aggregate-id
    - class_version and upcasting:          https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#versioning
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eventsourcing.domain import Aggregate, event

from banking import clock, identity
from banking.exceptions import InsufficientFunds, InvalidAmount, UnknownFee, UnknownHold


def new_ref(prefix: str) -> str:
    """Short human-friendly business reference, e.g. 'HLD-3f9a1c2e'."""
    return f"{prefix}-{identity.new_hex8()}"


def event_ref(account_id: UUID | str, version: int) -> str:
    """Exact, immutable identifier of one stored event.

    In the event store an event is uniquely identified by
    (stream id, version) - that pair is the table's primary key.
    We print it as  EVT-<first 8 chars of the account id>-<version>,
    e.g.  EVT-7f3a9c21-0007.
    """
    return f"EVT-{str(account_id)[:8]}-{version:04d}"


class BankAccount(Aggregate):
    """One bank / escrow account = one event stream."""

    # ------------------------------------------------------------------ #
    # Base class for all events of this aggregate.  It only changes where
    # the timestamp comes from (the business clock, see banking/clock.py).
    # ------------------------------------------------------------------ #
    # Docs: https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#timestamp-timezones
    class Event(Aggregate.Event):
        @staticmethod
        def create_timestamp():
            return clock.now()

    # Docs: https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#defining-the-aggregate-id
    @staticmethod
    def create_id(**_kwargs) -> UUID:
        """Stream id of a new account (random, or reproducible during seed_demo)."""
        return identity.new_uuid()

    # ------------------------------------------------------------------ #
    # v1 of every stream: the account is opened
    # ------------------------------------------------------------------ #
    class AccountOpened(Aggregate.Created):
        owner_name: str
        account_type: str  # "CURRENT", "SAVINGS" or "ESCROW"
        currency: str

    @event(AccountOpened)
    def __init__(self, owner_name: str, account_type: str, currency: str = "TND"):
        self.owner_name = owner_name
        self.account_type = account_type
        self.currency = currency
        self.balance = 0  # ledger balance, in millimes
        self.holds: dict[str, dict[str, Any]] = {}  # active escrow holds
        self.fees: dict[str, dict[str, Any]] = {}  # fees charged (for reversals)
        self.total_deposited = 0
        self.total_withdrawn = 0

    # ------------------------------------------------------------------ #
    # Read-only helpers (computed from the state, never stored)
    # ------------------------------------------------------------------ #
    @property
    def held(self) -> int:
        return sum(h["amount"] for h in self.holds.values())

    @property
    def available(self) -> int:
        """Money the customer can still use = balance - active holds."""
        return self.balance - self.held

    # ================================================================== #
    # DEPOSIT
    # ================================================================== #
    class FundsDeposited(Aggregate.Event):
        amount: int
        reference: str
        channel: str  # added in schema version 2 (see upcast below)

        # --- Event schema UPCASTING ------------------------------------
        # Docs: https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#versioning
        # Version 1 of this event (old software) had no "channel" field.
        # Old events stay in the store untouched (they are immutable), but
        # when the library reads a v1 event it calls upcast_v1_v2() to
        # convert it on the fly into the v2 shape the code expects.
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

    # ================================================================== #
    # WITHDRAWAL
    # ================================================================== #
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

    # ================================================================== #
    # ESCROW HOLD  (money is blocked for a beneficiary, not yet paid)
    # ================================================================== #
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

    # ================================================================== #
    # TRANSFER  (recorded on BOTH accounts: OUT on the payer, IN on payee)
    # ================================================================== #
    class TransferSettled(Aggregate.Event):
        transfer_id: str
        direction: str  # "OUT" (money leaves this account) or "IN"
        amount: int
        counterparty_id: str
        hold_id: str  # "" for a direct transfer, else the escrow hold settled
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
                self.holds.pop(hold_id, None)  # the escrow hold is consumed
        else:
            self.balance += amount

    # ================================================================== #
    # FEES and FEE REVERSALS
    # ================================================================== #
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


# Short names used everywhere else (UI, projector, AI evidence)
EVENT_TYPES = [
    "AccountOpened", "FundsDeposited", "FundsWithdrawn", "FundsReserved",
    "HoldReleased", "TransferSettled", "FeeCharged", "FeeReversed",
]


def event_type_name(domain_event: object) -> str:
    """BankAccount.FundsDeposited -> 'FundsDeposited'."""
    return type(domain_event).__name__


def signed_amount(domain_event: object) -> int:
    """Effect of one event on the LEDGER balance, in millimes (+ in, - out)."""
    name = event_type_name(domain_event)
    amount = getattr(domain_event, "amount", 0)
    if name in ("FundsDeposited", "FeeReversed"):
        return amount
    if name in ("FundsWithdrawn", "FeeCharged"):
        return -amount
    if name == "TransferSettled":
        return amount if domain_event.direction == "IN" else -amount
    return 0  # AccountOpened, FundsReserved, HoldReleased don't move the balance


def describe(domain_event: object) -> str:
    """One-line English description of an event (used by UI and AI evidence)."""
    from banking.money import fmt

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
