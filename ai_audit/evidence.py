"""
EVIDENCE PACK - the only information the AI is allowed to use
==============================================================

Plain-language summary
----------------------
Before asking the AI anything, we compute - with ordinary deterministic
Python - an "evidence pack" straight from the immutable EVENT STORE (never
from the read model, never from memory):

  * the exact list of events in the chosen time window, each with its
    immutable id (EVT-xxxxxxxx-0007), exact timestamp, amount and the
    balance right after it (computed by left-fold replay);
  * the official totals ("facts"): opening balance, closing balance,
    total deposits, total fees, ...

The AI only REWRITES these facts in readable English.  The verifier
(ai_audit/verifier.py) then checks every citation and every number against
this same pack.  Anything not in the pack is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from banking.domain import BankAccount, describe, event_ref, event_type_name, signed_amount
from banking.money import plain, to_millimes


def iso(ts: datetime) -> str:
    """Canonical timestamp format used in citations: 2026-10-28T13:45:00Z"""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Evidence:
    account_id: str
    owner_name: str
    account_type: str
    currency: str
    window_start: str | None
    window_end: str | None
    events: list[dict[str, Any]]
    facts: dict[str, str]
    truncated: int = 0
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {e["event_id"]: e for e in self.events}

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "account": {"account_id": self.account_id, "owner_name": self.owner_name,
                        "account_type": self.account_type, "currency": self.currency},
            "window": {"from": self.window_start or "account opening", "to": self.window_end or "now"},
            "facts": self.facts,
            "events": [{k: v for k, v in e.items() if k not in ("signed_millimes", "amount_millimes")}
                       for e in self.events],
            "note": f"{self.truncated} older events of the window omitted" if self.truncated else "",
        }


def build_evidence(app: Any, account_id: UUID, start: datetime | None = None, end: datetime | None = None,
                   max_events: int = 60) -> Evidence:
    events = app.get_events(account_id)
    account: BankAccount | None = None
    opening = 0
    window: list[dict[str, Any]] = []
    keys = ("deposits", "withdrawals", "transfers_in", "transfers_out", "fees",
            "fees_reversed", "holds_placed", "holds_released", "holds_settled")
    totals = {k: 0 for k in keys}
    counts = {k: 0 for k in keys}

    def add(key: str, amount: int) -> None:
        totals[key] += amount
        counts[key] += 1

    for e in events:
        account = e.mutate(account)
        if end is not None and e.timestamp > end:
            break
        if start is not None and e.timestamp < start:
            opening = account.balance
            continue
        name = event_type_name(e)
        amount = getattr(e, "amount", 0)
        if name == "FundsDeposited":
            add("deposits", amount)
        elif name == "FundsWithdrawn":
            add("withdrawals", amount)
        elif name == "FeeCharged":
            add("fees", amount)
        elif name == "FeeReversed":
            add("fees_reversed", amount)
        elif name == "FundsReserved":
            add("holds_placed", amount)
        elif name == "HoldReleased":
            add("holds_released", amount)
        elif name == "TransferSettled":
            add("transfers_out" if e.direction == "OUT" else "transfers_in", amount)
            if e.direction == "OUT" and e.hold_id:
                add("holds_settled", amount)
        category = {"AccountOpened": "opening", "FundsDeposited": "deposit", "FundsWithdrawn": "withdrawal",
                    "FundsReserved": "escrow", "HoldReleased": "escrow", "FeeCharged": "fee",
                    "FeeReversed": "reversal"}.get(name, "transfer")
        if name == "TransferSettled" and e.hold_id:
            category = "escrow"
        window.append({
            "event_id": event_ref(account_id, e.originator_version),
            "timestamp": iso(e.timestamp),
            "type": name,
            "category": category,  # hint for the AI (context engineering)
            "version": e.originator_version,
            "amount_tnd": plain(amount) if amount else None,
            "amount_millimes": amount,
            "signed_millimes": signed_amount(e),
            "balance_after_tnd": plain(account.balance),
            "available_after_tnd": plain(account.available),
            "description": describe(e),
        })
    if account is None:
        raise ValueError("Account has no events.")
    closing = window[-1] if window else None
    from django.conf import settings

    facts = {
        "opening_balance_tnd": plain(opening),
        "closing_balance_tnd": closing["balance_after_tnd"] if closing else plain(opening),
        "closing_available_tnd": closing["available_after_tnd"] if closing else plain(opening),
        "event_count": str(len(window)),
        "material_threshold_tnd": plain(to_millimes(settings.ALERT_LARGE_TRANSFER_TND)),
        **{f"total_{k}_tnd": plain(v) for k, v in totals.items()},
        **{f"count_{k}": str(v) for k, v in counts.items()},  # computed over the WHOLE window
    }
    truncated = max(0, len(window) - max_events)
    if truncated:
        window = window[-max_events:]
    return Evidence(
        account_id=str(account_id), owner_name=account.owner_name, account_type=account.account_type,
        currency=account.currency, window_start=iso(start) if start else None,
        window_end=iso(end) if end else None, events=window, facts=facts, truncated=truncated,
    )
