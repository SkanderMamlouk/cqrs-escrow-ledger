"""
THE APPLICATION LAYER - command handlers + event store access
==============================================================

Plain-language summary
----------------------
The web site never touches the BankAccount object directly.  It calls a
COMMAND HANDLER here, e.g. `app.deposit(account_id, amount)`, which always
follows the same 4 steps:

    1. LOAD     rebuild the account from the event store
                (latest snapshot + the few events after it)
    2. CHECK    optimistic concurrency: is the account still at the version
                the user saw on screen?  If not -> ConcurrencyConflict
    3. DECIDE   call the aggregate command (deposit, withdraw...) which
                checks the business rules and creates new pending events
    4. SAVE     append the new events to the event store in ONE atomic
                transaction.  The store has a UNIQUE (stream id, version)
                key, so if somebody else wrote the same version in the
                meantime, the save fails and nothing is written.

`BankingApplication` inherits from `eventsourcing.application.Application`,
which gives us for free:
    * self.save(...)                  -> append events (atomic)
    * self.repository.get(id, version)-> rebuild an aggregate (uses snapshots)
    * self.events / self.snapshots    -> the event store and snapshot store
    * self.recorder.select_notifications(...) -> the global, ordered log of
      all events, used by the read-model projector.

Official references used in this file
  * eventsourcing library (v9.5.6): https://github.com/pyeventsourcing/eventsourcing
  * Application and repository:     https://eventsourcing.readthedocs.io/en/stable/topics/application.html#repository
  * saving several aggregates in one transaction (transfers):
                                    https://eventsourcing.readthedocs.io/en/stable/topics/application.html#saving-multiple-aggregates
  * automatic snapshotting:          https://eventsourcing.readthedocs.io/en/stable/topics/application.html#automatic-snapshotting
  * notification log:                https://eventsourcing.readthedocs.io/en/stable/topics/application.html#notification-log

Snapshots: every SNAPSHOT_INTERVAL events (default 5), the library stores a
copy of the account state.  Rebuilding then starts from the most recent
snapshot and only replays the events after it ("snapshot + delta replay").
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable
from uuid import UUID

from eventsourcing.application import AggregateNotFoundError, Application, project_aggregate
from eventsourcing.persistence import IntegrityError, StoredEvent

from banking import clock
from banking.domain import BankAccount, describe, event_ref, event_type_name, new_ref, signed_amount
from banking.exceptions import AccountNotFound, ConcurrencyConflict, InvalidAmount


@dataclass
class RecordedEvent:
    """What the UI shows after a command: which immutable event was appended."""

    event_id: str
    event_type: str
    account_id: UUID
    version: int
    timestamp: datetime
    position: int | None  # global position in the event log (notification id)
    description: str


@dataclass
class CommandResult:
    message: str
    events: list[RecordedEvent] = field(default_factory=list)
    account_id: UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeTravelResult:
    """State of an account at a past moment, and HOW it was rebuilt."""

    account: BankAccount | None
    at: datetime
    version: int  # last event version <= at (0 = account did not exist yet)
    total_versions: int
    snapshot_version: int | None  # snapshot used as the starting point
    replayed_events: int  # events replayed on top of the snapshot


class BankingApplication(Application[UUID]):
    """Core Banking & Escrow Ledger - write side of CQRS."""

    name = "BankingLedger"
    snapshotting_intervals = {BankAccount: 5}  # overridden from env below

    def __init__(self, env: dict[str, str] | None = None) -> None:
        super().__init__(env)
        interval = int(self.env.get("SNAPSHOT_INTERVAL") or 5)
        self.snapshot_interval = interval
        self.snapshotting_intervals = {BankAccount: interval}

    # ================================================================== #
    # Internal helpers
    # ================================================================== #
    def _load(self, account_id: UUID) -> BankAccount:
        try:
            return self.repository.get(account_id)
        except AggregateNotFoundError as exc:
            raise AccountNotFound(f"Account {account_id} does not exist.") from exc

    def _recorded(self, recordings: Iterable[Any]) -> list[RecordedEvent]:
        out = []
        for rec in recordings:
            e = rec.domain_event
            out.append(
                RecordedEvent(
                    event_id=event_ref(e.originator_id, e.originator_version),
                    event_type=event_type_name(e),
                    account_id=e.originator_id,
                    version=e.originator_version,
                    timestamp=e.timestamp,
                    position=getattr(rec.notification, "id", None),
                    description=describe(e),
                )
            )
        return out

    def _execute(
        self,
        account_ids: list[UUID],
        decide: Callable[..., Any],
        expected_version: int | None = None,
        max_attempts: int = 3,
    ) -> tuple[list[RecordedEvent], Any]:
        """LOAD -> CHECK -> DECIDE -> SAVE, with optimistic concurrency.

        * If the caller gave `expected_version` (the version shown on screen)
          and the account moved on, we refuse with ConcurrencyConflict.
        * If no expected version was given, a conflict during SAVE simply
          means "somebody was faster": we reload and retry (max 3 times).
        """
        for _attempt in range(max_attempts):
            accounts = [self._load(aid) for aid in account_ids]
            first = accounts[0]
            if expected_version is not None and first.version != expected_version:
                raise ConcurrencyConflict(first.id, expected_version, first.version)
            outcome = decide(*accounts)
            try:
                recordings = self.save(*accounts)  # atomic append (all or nothing)
            except IntegrityError:
                if expected_version is not None:
                    latest = self._load(first.id)
                    if latest.version != expected_version:
                        raise ConcurrencyConflict(first.id, expected_version, latest.version) from None
                    # the account the user looked at did not change: the conflict was on the
                    # OTHER account of a transfer, so simply retry with fresh state
                continue  # retry with fresh state
            return self._recorded(recordings), outcome
        raise ConcurrencyConflict(account_ids[0], None, "unknown (too many concurrent writers)")

    # ================================================================== #
    # COMMAND HANDLERS (the "C" of CQRS)
    # ================================================================== #
    def open_account(
        self, owner_name: str, account_type: str = "CURRENT", currency: str = "TND", initial_deposit: int = 0
    ) -> CommandResult:
        account = BankAccount(owner_name=owner_name, account_type=account_type, currency=currency)
        if initial_deposit:
            account.deposit(initial_deposit, reference="Initial deposit", channel="BRANCH")
        recordings = self.save(account)
        return CommandResult(f"Account opened for {owner_name}.", self._recorded(recordings), account.id)

    def deposit(self, account_id: UUID, amount: int, reference: str = "", channel: str = "BRANCH",
                expected_version: int | None = None) -> CommandResult:
        events, _ = self._execute([account_id], lambda a: a.deposit(amount, reference, channel), expected_version)
        return CommandResult("Deposit recorded.", events, account_id)

    def withdraw(self, account_id: UUID, amount: int, reference: str = "",
                 expected_version: int | None = None) -> CommandResult:
        events, _ = self._execute([account_id], lambda a: a.withdraw(amount, reference), expected_version)
        return CommandResult("Withdrawal recorded.", events, account_id)

    def reserve_funds(self, account_id: UUID, amount: int, beneficiary_id: UUID | str, reason: str = "",
                      expected_version: int | None = None) -> CommandResult:
        try:
            beneficiary_uuid = UUID(str(beneficiary_id))
        except ValueError:
            raise InvalidAmount("An escrow hold needs a valid beneficiary account.") from None
        self._load(beneficiary_uuid)  # the beneficiary account must exist
        if beneficiary_uuid == account_id:
            raise InvalidAmount("An escrow hold cannot designate the same account as beneficiary.")
        beneficiary = str(beneficiary_uuid)
        events, hold_id = self._execute(
            [account_id], lambda a: a.reserve_funds(amount, beneficiary, reason), expected_version
        )
        return CommandResult(f"Escrow hold {hold_id} placed.", events, account_id, {"hold_id": hold_id})

    def release_hold(self, account_id: UUID, hold_id: str, reason: str = "",
                     expected_version: int | None = None) -> CommandResult:
        events, _ = self._execute([account_id], lambda a: a.release_hold(hold_id, reason), expected_version)
        return CommandResult(f"Escrow hold {hold_id} released.", events, account_id)

    def settle_escrow(self, account_id: UUID, hold_id: str, reference: str = "",
                      expected_version: int | None = None) -> CommandResult:
        """Pay the held money to the beneficiary: 2 events, 2 streams, 1 transaction."""
        account = self._load(account_id)
        hold = account.holds.get(hold_id)
        if hold is None:
            from banking.exceptions import UnknownHold

            raise UnknownHold(f"No active hold {hold_id} on this account.")
        beneficiary = UUID(hold["beneficiary_id"])
        transfer_id = new_ref("TRF")

        def decide(payer: BankAccount, payee: BankAccount) -> None:
            payer.send_transfer(transfer_id, hold["amount"], str(payee.id), hold_id=hold_id, reference=reference)
            payee.receive_transfer(transfer_id, hold["amount"], str(payer.id), reference=reference)

        events, _ = self._execute([account_id, beneficiary], decide, expected_version)
        return CommandResult(f"Escrow {hold_id} settled by transfer {transfer_id}.", events, account_id,
                             {"transfer_id": transfer_id})

    def transfer(self, from_id: UUID, to_id: UUID, amount: int, reference: str = "",
                 expected_version: int | None = None) -> CommandResult:
        if from_id == to_id:
            raise InvalidAmount("Cannot transfer to the same account.")
        transfer_id = new_ref("TRF")

        def decide(payer: BankAccount, payee: BankAccount) -> None:
            payer.send_transfer(transfer_id, amount, str(payee.id), reference=reference)
            payee.receive_transfer(transfer_id, amount, str(payer.id), reference=reference)

        events, _ = self._execute([from_id, to_id], decide, expected_version)
        return CommandResult(f"Transfer {transfer_id} settled.", events, from_id, {"transfer_id": transfer_id})

    def charge_fee(self, account_id: UUID, amount: int, reason: str = "",
                   expected_version: int | None = None) -> CommandResult:
        events, fee_id = self._execute([account_id], lambda a: a.charge_fee(amount, reason), expected_version)
        return CommandResult(f"Fee {fee_id} charged.", events, account_id, {"fee_id": fee_id})

    def reverse_fee(self, account_id: UUID, fee_id: str, reason: str = "",
                    expected_version: int | None = None) -> CommandResult:
        events, _ = self._execute([account_id], lambda a: a.reverse_fee(fee_id, reason), expected_version)
        return CommandResult(f"Fee {fee_id} reversed.", events, account_id)

    # ================================================================== #
    # WRITE-SIDE QUERIES (read the source of truth: the event store)
    # ================================================================== #
    def get_account(self, account_id: UUID, version: int | None = None) -> BankAccount:
        try:
            return self.repository.get(account_id, version=version)
        except AggregateNotFoundError as exc:
            raise AccountNotFound(f"Account {account_id} does not exist.") from exc

    def get_events(self, account_id: UUID) -> list[Any]:
        events = list(self.events.get(account_id))
        if not events:
            raise AccountNotFound(f"Account {account_id} does not exist.")
        return events

    def snapshot_versions(self, account_id: UUID) -> list[int]:
        if self.snapshots is None:
            return []
        return [s.originator_version for s in self.snapshots.get(account_id)]

    def rebuild_without_snapshot(self, account_id: UUID) -> BankAccount:
        """Pure left-fold over ALL events (used by benchmarks and the verifier)."""
        account = project_aggregate(None, self.events.get(account_id))
        if account is None:
            raise AccountNotFound(f"Account {account_id} does not exist.")
        return account

    def state_at(self, account_id: UUID, at: datetime) -> TimeTravelResult:
        """TIME TRAVEL: what did this account look like at moment `at`?"""
        events = self.get_events(account_id)
        version = 0
        for e in events:
            if e.timestamp <= at:
                version = e.originator_version
            else:
                break
        if version == 0:
            return TimeTravelResult(None, at, 0, len(events), None, 0)
        snapshot_version = None
        if self.snapshots is not None:
            snaps = list(self.snapshots.get(account_id, lte=version, desc=True, limit=1))
            if snaps:
                snapshot_version = snaps[0].originator_version
        account = self.repository.get(account_id, version=version)  # snapshot + delta replay
        replayed = version - (snapshot_version or 0)
        return TimeTravelResult(account, at, version, len(events), snapshot_version, replayed)

    def replay_steps(self, account_id: UUID) -> list[dict[str, Any]]:
        """Replay the stream ONE event at a time and record the state after each.

        This is literally the left-fold:  state_n = apply(state_{n-1}, event_n)
        """
        steps: list[dict[str, Any]] = []
        account: BankAccount | None = None
        snapshots = set(self.snapshot_versions(account_id))
        for e in self.get_events(account_id):
            account = e.mutate(account)
            steps.append(
                {
                    "version": e.originator_version,
                    "event_id": event_ref(account_id, e.originator_version),
                    "event_type": event_type_name(e),
                    "timestamp": e.timestamp,
                    "description": describe(e),
                    "signed_amount": signed_amount(e),
                    "balance": account.balance,
                    "held": account.held,
                    "available": account.available,
                    "holds": {k: dict(v) for k, v in account.holds.items()},
                    "has_snapshot": e.originator_version in snapshots,
                    "payload": {k: v for k, v in vars(e).items() if k not in ("originator_id",)},
                }
            )
        return steps

    def iter_account_ids(self) -> list[UUID]:
        """All account streams, discovered from the global event log."""
        ids: list[UUID] = []
        start = 1
        while True:
            batch = self.recorder.select_notifications(start=start, limit=500)
            if not batch:
                break
            for n in batch:
                if n.topic.endswith("BankAccount.AccountOpened"):
                    ids.append(n.originator_id)
            start = batch[-1].id + 1
        return ids

    # ================================================================== #
    # DEMONSTRATIONS used in the presentation
    # ================================================================== #
    def demonstrate_race(self, account_id: UUID, amount: int) -> dict[str, Any]:
        """Two clerks load the SAME version at the SAME time and both deposit.

        Exactly one save succeeds; the other one is rejected by the event
        store's unique (stream id, version) key -> IntegrityError.
        """
        barrier = threading.Barrier(2)
        results: dict[str, str] = {}

        def clerk(name: str) -> None:
            account = self._load(account_id)
            loaded = account.version
            account.deposit(amount, reference=f"Race test by {name}", channel="BRANCH")
            barrier.wait()  # both clerks now try to save at the same moment
            try:
                self.save(account)
                results[name] = f"SAVED as version {loaded + 1}"
            except IntegrityError:
                results[name] = f"REJECTED (version {loaded + 1} already exists) - IntegrityError"

        threads = [threading.Thread(target=clerk, args=(n,)) for n in ("Clerk A", "Clerk B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def import_legacy_v1_deposit(self, account_id: UUID, amount: int, reference: str) -> CommandResult:
        """Simulates a deposit recorded by the OLD (schema v1) software.

        The stored JSON has no "channel" field and no class_version.
        Reading it back triggers FundsDeposited.upcast_v1_v2().
        """
        account = self._load(account_id)
        state = self.mapper.transcoder.encode(
            {"timestamp": clock.now(), "amount": amount, "reference": reference}
        )
        topic = f"{BankAccount.__module__}:BankAccount.FundsDeposited"
        stored = StoredEvent(originator_id=account_id, originator_version=account.version + 1,
                             topic=topic, state=state)
        self.recorder.insert_events([stored])
        e = list(self.events.get(account_id, gt=account.version))[0]
        rec = RecordedEvent(event_ref(account_id, e.originator_version), event_type_name(e), account_id,
                            e.originator_version, e.timestamp, None, describe(e))
        return CommandResult("Legacy v1 event imported.", [rec], account_id)


def new_account_id() -> UUID:
    return uuid.uuid4()
