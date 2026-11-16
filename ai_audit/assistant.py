from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings

from ai_audit.evidence import Evidence, build_evidence
from ai_audit.models import AuditRun
from ai_audit.providers import select_provider
from ai_audit.schemas import AuditReport
from ai_audit.verifier import Verification, material_events, verify


class EmptyWindow(ValueError):
    pass


SYSTEM_PROMPT = """You are a meticulous bank audit assistant for an event-sourced core banking ledger.
You receive an EVIDENCE PACK (JSON) extracted from an immutable, append-only event store.

STRICT RULES - an automatic verifier will reject your answer if you break any of them:
1. Use ONLY facts present in the evidence pack. Never invent events, dates, amounts, counts or reasons.
2. Every finding MUST cite at least one event with its exact "event_id" and exact "timestamp",
   copied character-for-character from the evidence (format EVT-xxxxxxxx-0000 and YYYY-MM-DDTHH:MM:SSZ).
3. EVERY number you write (in the headline too) must come from the evidence: an event amount, a balance_after,
   a total or a count from "facts", or the sum of the events you cite in that finding.
   Write amounts with exactly 3 decimals followed by TND, without thousands separators, e.g. 1500.000 TND.
   Write dates as YYYY-MM-DD, only dates of events of the evidence.
4. Only mention references (EVT-..., HLD-..., FEE-..., TRF-...) that appear in the evidence.
5. Choose the category correctly: deposit -> FundsDeposited events, withdrawal -> FundsWithdrawn,
   escrow -> FundsReserved / HoldReleased / TransferSettled, transfer -> TransferSettled,
   fee -> FeeCharged / FeeReversed, reversal -> FeeReversed, opening / balance / risk -> any event.
   Each event already has a "category" hint: use it.
6. Cite EVERY fee reversal and EVERY transfer or escrow hold whose amount is at least facts.material_threshold_tnd.
7. "closing_balance_tnd" must equal facts.closing_balance_tnd exactly.
8. Answer with JSON only, matching this schema:
{"headline": str, "findings": [{"category": str, "statement": str,
  "citations": [{"event_id": str, "timestamp": str}]}], "closing_balance_tnd": "0.000"}
Write 4 to 10 concise findings in English that summarize how the account changed over time."""


def _user_prompt(evidence: Evidence, feedback: str = "") -> str:
    text = "EVIDENCE PACK:\n" + json.dumps(evidence.as_prompt_dict(), indent=1, ensure_ascii=False)
    if feedback:
        text += ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED BY THE VERIFIER for these reasons:\n" + feedback +
                 "\nFix every problem and answer again with JSON only.")
    return text


def _chunks(items: list, size: int = 15) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def deterministic_report(evidence: Evidence) -> dict[str, Any]:
    evs, facts = evidence.events, evidence.facts
    if not evs:
        raise EmptyWindow("No event in this time window.")

    def cite(items: list[dict]) -> list[dict]:
        return [{"event_id": e["event_id"], "timestamp": e["timestamp"]} for e in items[:15]]

    def biggest_first(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda e: -(e["amount_millimes"] or 0))

    def of(t: str) -> list[dict]:
        return [e for e in evs if e["type"] == t]

    opening = {"category": "opening", "citations": cite(evs[:1]),
               "statement": f"Opening balance at the start of the audited window: {facts['opening_balance_tnd']} TND "
                            f"({facts['event_count']} events in the window)."}
    closing = {"category": "balance", "citations": cite(evs[-1:]),
               "statement": f"Closing balance {facts['closing_balance_tnd']} TND with "
                            f"{facts['closing_available_tnd']} TND available."}

    required: list[dict] = []
    for chunk in _chunks(of("FeeReversed")):
        total = sum(e["amount_millimes"] for e in chunk) / 1000
        required.append({"category": "reversal", "citations": cite(chunk),
                         "statement": f"{len(chunk)} fee reversal(s) totalling {total:.3f} TND, each recorded as an "
                                      f"immutable FeeReversed event."})
    threshold = facts["material_threshold_tnd"]
    for chunk in _chunks(material_events(evidence)):
        chunk = [e for e in chunk if e["type"] != "FeeReversed"]
        if chunk:
            required.append({"category": "risk", "citations": cite(chunk),
                             "statement": f"{len(chunk)} material movement(s) of at least {threshold} TND "
                                          "require four-eyes review."})

    optional: list[dict] = []
    dep = of("FundsDeposited")
    if dep:
        big = biggest_first(dep)
        optional.append({"category": "deposit", "citations": cite(big),
                         "statement": f"{facts['count_deposits']} deposit(s) totalling {facts['total_deposits_tnd']} TND; "
                                      f"the largest was {big[0]['amount_tnd']} TND."})
    wd = of("FundsWithdrawn")
    if wd:
        big = biggest_first(wd)
        optional.append({"category": "withdrawal", "citations": cite(big),
                         "statement": f"{facts['count_withdrawals']} withdrawal(s) totalling "
                                      f"{facts['total_withdrawals_tnd']} TND; the largest was {big[0]['amount_tnd']} TND."})
    tr = of("TransferSettled")
    if tr:
        optional.append({"category": "transfer", "citations": cite(biggest_first(tr)),
                         "statement": f"Transfers received: {facts['total_transfers_in_tnd']} TND; transfers sent "
                                      f"(including escrow settlements): {facts['total_transfers_out_tnd']} TND."})
    fees = of("FeeCharged")
    if fees:
        optional.append({"category": "fee", "citations": cite(fees),
                         "statement": f"{facts['count_fees']} fee(s) charged totalling {facts['total_fees_tnd']} TND."})
    for e in biggest_first(of("FundsReserved"))[:3]:
        hold_id = e["description"].split()[2]
        closing_ev = [x for x in evs if x["type"] in ("HoldReleased", "TransferSettled") and hold_id in x["description"]]
        outcome = ("released" if closing_ev[0]["type"] == "HoldReleased" else "settled to the beneficiary") \
            if closing_ev else "still active"
        optional.append({"category": "escrow", "citations": cite([e] + closing_ev[:1]),
                         "statement": f"Escrow hold {hold_id} of {e['amount_tnd']} TND was placed and is {outcome}."})

    room = 12 - 2 - len(required)
    findings = [opening] + optional[:max(room, 0)] + required + [closing]
    return {"headline": f"Deterministic audit of {evidence.owner_name}: "
                        f"{facts['opening_balance_tnd']} TND -> {facts['closing_balance_tnd']} TND",
            "findings": findings[:12], "closing_balance_tnd": facts["closing_balance_tnd"]}


def run_audit(app: Any, account_id: UUID, start: datetime | None = None, end: datetime | None = None) -> AuditRun:
    started = time.perf_counter()
    if start and end and end < start:
        raise EmptyWindow("The end of the window is before its start.")
    evidence = build_evidence(app, account_id, start, end)
    if not evidence.events:
        raise EmptyWindow("No event in this time window - nothing to audit.")
    provider, info = select_provider()
    attempts: list[dict[str, Any]] = []
    final: Verification | None = None
    status = "FALLBACK"
    feedback = ""

    if provider is not None:
        for n in range(1, settings.AI_MAX_ATTEMPTS + 1):
            t0 = time.perf_counter()
            try:
                raw = provider.generate(SYSTEM_PROMPT, _user_prompt(evidence, feedback))
                result = verify(raw, evidence)
                error = ""
            except Exception as exc:
                raw, result, error = "", None, f"{type(exc).__name__}: {exc}"[:300]
            attempts.append({"attempt": n, "provider": provider.name, "model": provider.model,
                             "latency_ms": round((time.perf_counter() - t0) * 1000),
                             "raw": raw[:20000], "error": error,
                             "verification": result.as_dict() if result else None})
            if result and result.passed:
                final, status = result, "VERIFIED"
                break
            feedback = result.feedback() if result else f"Provider error: {error}"

    if final is None:
        report = deterministic_report(evidence)
        final = verify(report, evidence)
        attempts.append({"attempt": len(attempts) + 1, "provider": "deterministic", "model": "template",
                         "latency_ms": 0, "raw": json.dumps(report), "error": "",
                         "verification": final.as_dict()})
        if not final.passed:
            status = "REJECTED"

    report_obj: AuditReport | None = final.report
    return AuditRun.objects.create(
        account_id=account_id, owner_name=evidence.owner_name,
        window_start=start, window_end=end,
        provider=info.name if status == "VERIFIED" else "deterministic",
        model=info.model if status == "VERIFIED" else "template",
        provider_reason=info.reason[:255], status=status,
        report=report_obj.model_dump() if report_obj else {},
        verification=final.as_dict(), attempts=attempts,
        evidence=evidence.as_prompt_dict(),
        latency_ms=round((time.perf_counter() - started) * 1000),
        closing_balance_tnd=evidence.facts["closing_balance_tnd"],
    )


__all__ = ["run_audit", "deterministic_report", "SYSTEM_PROMPT", "EmptyWindow"]

