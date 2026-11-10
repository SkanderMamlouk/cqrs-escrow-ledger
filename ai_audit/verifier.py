"""
THE AI VERIFICATION GATE - deterministic, no AI involved
========================================================

Plain-language summary
----------------------
A language model can "hallucinate": invent an event, a date or an amount.
In banking that is unacceptable.  So the AI's answer is never shown as-is.
It must first pass ALL of these checks, computed with plain Python against
the evidence pack (which itself comes from the immutable event store):

  1. SCHEMA      the answer is valid JSON with exactly the expected fields
                 (Pydantic v2 model ai_audit.schemas.AuditReport)
  2. EXISTENCE   every cited event_id exists in this account's stream and
                 inside the chosen time window; every reference written in
                 the text (EVT-…, HLD-…, FEE-…, TRF-…) exists in the evidence
  3. TIMESTAMP   every cited timestamp is EXACTLY the timestamp of that
                 event; every date / time written in the text is the date /
                 time of an event of the window
  4. CATEGORY    a "fee" finding cites fee events, a "withdrawal" finding
                 cites withdrawals, etc.
  5. NUMBERS     EVERY number written in the headline and the statements
                 (any format: "1,500.000 TND", "TND 1500", "-1500", "3 fees")
                 must equal an amount / balance of a cited event, a sum of
                 cited events, an official total or count from the evidence
  6. BALANCE     the closing balance equals the deterministic left-fold result
  7. COVERAGE    material events (fee reversals, transfers and escrow holds at
                 or above the large-amount threshold) are all cited

If one check fails, the answer is REJECTED and the reasons are sent back
to the AI for one more try; if it fails again, the system falls back to a
deterministic template report.  Either way, what the user sees is verified.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

from pydantic import ValidationError

from ai_audit.evidence import Evidence
from ai_audit.schemas import CATEGORY_EVENT_TYPES, AuditReport

TUNIS = timezone(timedelta(hours=1))  # Africa/Tunis (UTC+1, no DST)

REF_RE = re.compile(r"\b(?:EVT|HLD|FEE|TRF)-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*\b")
OTHER_REF_RE = re.compile(r"\b[A-Z]{1,5}-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*\b")  # e.g. INV-2026-0912, C-1182
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?Z?)?\b")
MONTHS = {m: i for i, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
     ("november", "nov"), ("december", "dec")], start=1) for m in names}
del MONTHS["may"]  # "3 may require..." is not a date; May dates are then checked as plain numbers
_M = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_M})\.?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
MONTH_DAY_RE = re.compile(rf"\b({_M})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
NUM_RE = re.compile(r"(?<![\w.])([-−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")
# kept for backward compatibility / tests
AMOUNT_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(?:TND|DT|dinars?)\b", re.IGNORECASE)


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: list[str] = field(default_factory=list)


@dataclass
class Verification:
    passed: bool
    checks: list[CheckResult]
    report: AuditReport | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": [asdict(c) for c in self.checks]}

    def feedback(self) -> str:
        lines = []
        for c in self.checks:
            if not c.passed:
                lines.append(f"- {c.name} FAILED: " + "; ".join(c.details[:6]))
        return "\n".join(lines)


def _dec(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.001"))
    except InvalidOperation:
        return None


def extract_amounts(statement: str) -> list[Decimal]:
    """Amounts written as '<number> TND' (used by tests and the UI)."""
    out = []
    for whole, frac in AMOUNT_RE.findall(statement):
        d = _dec(whole.replace(",", "") + (f".{frac}" if frac else ""))
        if d is not None:
            out.append(d)
    return out


def _parse(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    return json.loads(text)


class _Context:
    """Everything the evidence allows the text to mention."""

    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence
        self.facts_amounts = {_dec(v) for k, v in evidence.facts.items() if k.endswith("_tnd")}
        self.facts_ints = {int(v) for k, v in evidence.facts.items() if k.startswith("count_") or k == "event_count"}
        corpus = " ".join(e["description"] for e in evidence.events)
        self.known_refs = set(REF_RE.findall(corpus)) | set(OTHER_REF_RE.findall(corpus)) | set(evidence.by_id)
        self.dates: set[date] = set()
        self.times: set[tuple[int, int]] = set()
        for e in evidence.events:
            ts = datetime.strptime(e["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            for t in (ts, ts.astimezone(TUNIS)):
                self.dates.add(t.date())
                self.times.add((t.hour, t.minute))
        for bound in (evidence.window_start, evidence.window_end):
            if bound:
                b = datetime.strptime(bound, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                for t in (b, b.astimezone(TUNIS)):
                    self.dates.add(t.date())
                    self.times.add((t.hour, t.minute))
        self.years = {d.year for d in self.dates}
        self.all_event_amounts: set[Decimal | None] = set()
        for e in evidence.events:
            self.all_event_amounts |= {_dec(e["amount_tnd"]), _dec(e["balance_after_tnd"]),
                                       _dec(e["available_after_tnd"]), _dec(str(Decimal(e["signed_millimes"]) / 1000))}


def _check_text(text: str, where: str, ctx: _Context, allowed_amounts: set, allowed_ints: set,
                refs: list, dates: list, nums: list) -> None:
    """Checks references, dates, times and numbers written in free text."""
    # references
    for ref in set(REF_RE.findall(text)) | set(OTHER_REF_RE.findall(text)):
        if ref not in ctx.known_refs:
            refs.append(f"{where}: reference {ref} does not exist in the evidence")
    text = OTHER_REF_RE.sub(" ", REF_RE.sub(" ", text))
    # ISO dates (and times)
    for m in ISO_RE.finditer(text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            dates.append(f"{where}: invalid date {m.group(0)}")
            continue
        if d not in ctx.dates:
            dates.append(f"{where}: date {d.isoformat()} is not the date of any event in the window")
        if m.group(4) and (int(m.group(4)), int(m.group(5))) not in ctx.times:
            dates.append(f"{where}: time {m.group(4)}:{m.group(5)} is not the time of any event")
    text = ISO_RE.sub(" ", text)
    # dates written with month names
    for regex, day_i, month_i, year_i in ((DAY_MONTH_RE, 1, 2, 3), (MONTH_DAY_RE, 2, 1, 3)):
        for m in regex.finditer(text):
            day, month = int(m.group(day_i)), MONTHS[m.group(month_i).lower()]
            years = [int(m.group(year_i))] if m.group(year_i) else sorted(ctx.years)
            ok = False
            for y in years:
                try:
                    ok |= date(y, month, day) in ctx.dates
                except ValueError:
                    pass
            if not ok:
                dates.append(f"{where}: date '{m.group(0)}' is not the date of any event in the window")
        text = regex.sub(" ", text)
    # times
    for m in TIME_RE.finditer(text):
        if (int(m.group(1)), int(m.group(2))) not in ctx.times:
            dates.append(f"{where}: time {m.group(0)} is not the time of any event")
    text = TIME_RE.sub(" ", text)
    # every remaining number
    for sign, whole, frac in NUM_RE.findall(text):
        value = Decimal(whole.replace(",", "") + (f".{frac}" if frac else ""))
        if sign:
            value = -value
        ok = _dec(str(value)) in allowed_amounts
        if not ok and not frac and value == value.to_integral_value():
            ok = int(value) in allowed_ints
        if not ok:
            nums.append(f"{where}: number {sign}{whole}{'.' + frac if frac else ''} is not supported by the evidence")


def verify(raw: str | dict, evidence: Evidence) -> Verification:
    checks: list[CheckResult] = []

    # 1. SCHEMA -----------------------------------------------------------
    try:
        data = raw if isinstance(raw, dict) else _parse(raw)
        report = AuditReport.model_validate(data)
        checks.append(CheckResult("1. JSON schema (Pydantic v2)", True))
    except (ValueError, TypeError, ValidationError) as exc:
        msg = str(exc).splitlines()
        checks.append(CheckResult("1. JSON schema (Pydantic v2)", False, msg[:6]))
        return Verification(False, checks, None)

    ctx = _Context(evidence)
    exist, stamp, cat, nums = [], [], [], []
    cited_ids: set[str] = set()

    # headline: only facts / event values / counts, no citations needed
    _check_text(report.headline, "headline", ctx, ctx.facts_amounts | ctx.all_event_amounts,
                ctx.facts_ints | ctx.years, exist, stamp, nums)

    for i, f in enumerate(report.findings, start=1):
        cited = []
        for c in f.citations:
            ev = evidence.by_id.get(c.event_id)
            if ev is None:
                exist.append(f"finding {i}: {c.event_id} does not exist in this account/window")
                continue
            cited.append(ev)
            cited_ids.add(c.event_id)
            if c.timestamp != ev["timestamp"]:
                stamp.append(f"finding {i}: {c.event_id} timestamp {c.timestamp} != {ev['timestamp']}")
            allowed = CATEGORY_EVENT_TYPES.get(f.category)
            if allowed is not None and ev["type"] not in allowed:
                cat.append(f"finding {i}: category '{f.category}' cannot cite a {ev['type']} event ({c.event_id})")

        allowed_amounts = set(ctx.facts_amounts)
        amounts = [ev["amount_millimes"] for ev in cited if ev["amount_millimes"]]
        for ev in cited:
            allowed_amounts |= {_dec(ev["amount_tnd"]), _dec(ev["balance_after_tnd"]), _dec(ev["available_after_tnd"]),
                                _dec(str(Decimal(ev["signed_millimes"]) / 1000))}
        allowed_amounts.add(_dec(str(Decimal(sum(amounts)) / 1000)))
        if len(amounts) <= 12:  # sums of subsets of cited events of the same type
            by_type: dict[str, list[int]] = {}
            for ev in cited:
                if ev["amount_millimes"]:
                    by_type.setdefault(ev["type"], []).append(ev["amount_millimes"])
            for values in by_type.values():
                for r in range(2, min(len(values), 4) + 1):
                    for combo in combinations(values, r):
                        allowed_amounts.add(_dec(str(Decimal(sum(combo)) / 1000)))
        allowed_ints = ctx.facts_ints | ctx.years | {len(f.citations), len(cited)}
        _check_text(f.statement, f"finding {i}", ctx, allowed_amounts, allowed_ints, exist, stamp, nums)

    checks.append(CheckResult("2. Cited event ids & references exist (immutable event store)", not exist, exist))
    checks.append(CheckResult("3. Cited timestamps & dates exact", not stamp, stamp))
    checks.append(CheckResult("4. Finding category matches event types", not cat, cat))
    checks.append(CheckResult("5. Every number grounded in evidence", not nums, nums))

    # 6. BALANCE -----------------------------------------------------------
    expected = evidence.facts["closing_balance_tnd"]
    ok = _dec(report.closing_balance_tnd) == _dec(expected)
    checks.append(CheckResult("6. Closing balance == deterministic left-fold", ok,
                              [] if ok else [f"AI said {report.closing_balance_tnd}, ledger says {expected}"]))

    # 7. COVERAGE ----------------------------------------------------------
    threshold = _dec(evidence.facts.get("material_threshold_tnd", "10000"))
    missing = []
    for ev in material_events(evidence, threshold):
        if ev["event_id"] not in cited_ids:
            missing.append(f"material event {ev['event_id']} ({ev['type']} {ev['amount_tnd']} TND) not cited")
    checks.append(CheckResult("7. Material events covered", not missing, missing))

    passed = all(c.passed for c in checks)
    return Verification(passed, checks, report)


def material_events(evidence: Evidence, threshold: Decimal | None = None) -> list[dict[str, Any]]:
    threshold = threshold if threshold is not None else _dec(evidence.facts.get("material_threshold_tnd", "10000"))
    out = []
    for ev in evidence.events:
        big = ev["type"] in ("TransferSettled", "FundsReserved") and _dec(ev["amount_tnd"]) is not None \
            and _dec(ev["amount_tnd"]) >= threshold
        if ev["type"] == "FeeReversed" or big:
            out.append(ev)
    return out
