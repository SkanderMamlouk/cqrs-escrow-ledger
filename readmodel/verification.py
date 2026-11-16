from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from django.db.models import Sum

from banking.domain import event_type_name
from readmodel.models import AccountSummary, LedgerEntry
from readmodel.projector import projection_status


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationReport:
    checks: list[Check] = field(default_factory=list)
    accounts: int = 0
    events: int = 0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, bool(passed), detail))

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "accounts": self.accounts, "events": self.events,
                "checks": [asdict(c) for c in self.checks]}


def _state(acc: Any) -> tuple:
    return (acc.version, acc.balance, acc.held, sorted(acc.holds.items()), sorted(acc.fees.items()))


def verify(app: Any) -> VerificationReport:
    report = VerificationReport()
    transfers_out = transfers_in = 0
    ids = app.iter_account_ids()
    report.accounts = len(ids)

    for account_id in ids:
        short = str(account_id)[:8]
        events = app.get_events(account_id)
        report.events += len(events)
        versions = [e.originator_version for e in events]
        report.add(f"[{short}] versions contiguous 1..{len(events)}", versions == list(range(1, len(events) + 1)))
        stamps = [e.timestamp for e in events]
        report.add(f"[{short}] timestamps monotonic", all(a <= b for a, b in zip(stamps, stamps[1:])))

        folded = app.rebuild_without_snapshot(account_id)
        with_snap = app.get_account(account_id)
        report.add(f"[{short}] snapshot rebuild == full left-fold", _state(folded) == _state(with_snap),
                   f"balance {with_snap.balance} vs {folded.balance}")

        snap_ok = True
        for v in app.snapshot_versions(account_id):
            replay_v = app.repository.get(account_id, version=v)
            from eventsourcing.application import project_aggregate

            pure_v = project_aggregate(None, app.events.get(account_id, lte=v))
            snap_ok &= _state(replay_v) == _state(pure_v)
        report.add(f"[{short}] {len(app.snapshot_versions(account_id))} snapshot(s) match replay", snap_ok)

        row = AccountSummary.objects.filter(account_id=account_id).first()
        if row is None:
            report.add(f"[{short}] read model row exists", False, "missing AccountSummary")
            continue
        same = (row.ledger_balance, row.held_amount, row.available_balance, row.last_version) == (
            folded.balance, folded.held, folded.available, folded.version)
        report.add(f"[{short}] read model == event store", same,
                   f"read {row.ledger_balance}/{row.held_amount}/v{row.last_version} "
                   f"store {folded.balance}/{folded.held}/v{folded.version}")

        lines = LedgerEntry.objects.filter(account_id=account_id)
        total = lines.aggregate(s=Sum("amount"))["s"] or 0
        report.add(f"[{short}] statement lines sum == balance", total == folded.balance and lines.count() == len(events),
                   f"{lines.count()} lines, sum {total}")
        report.add(f"[{short}] available balance >= 0", folded.available >= 0)

        for e in events:
            if event_type_name(e) == "TransferSettled":
                if e.direction == "OUT":
                    transfers_out += e.amount
                else:
                    transfers_in += e.amount

    report.add("bank: money conservation (transfers OUT == IN)", transfers_out == transfers_in,
               f"out {transfers_out} / in {transfers_in}")
    status = projection_status(app)
    report.add("projector checkpoint at head of event log", status["lag_events"] == 0,
               f"position {status['position']} / head {status['head']}")
    return report

