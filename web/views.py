"""
WEB LAYER - Django views (HTMX + Chart.js on the front end)
===========================================================

Plain-language summary
----------------------
Look at which side each view uses - that IS the CQRS pattern:

  QUERIES (GET pages)   -> read ONLY the read-model tables (readmodel.models)
                           fast, simple SELECTs, no replay needed
  COMMANDS (POST forms) -> go ONLY through the BankingApplication
                           (banking.application), which appends events

The audit pages (replay, time travel, event explorer, AI audit) are the
exception on purpose: auditors want the SOURCE OF TRUTH, so they read the
immutable event store directly.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from pydantic import ValidationError

from ai_audit.assistant import EmptyWindow, run_audit
from ai_audit.models import AuditRun
from ai_audit.providers import describe_provider
from banking import commands as cmd
from banking.domain import event_ref
from banking.exceptions import AccountNotFound, LedgerError
from banking.money import plain, to_millimes
from config.ledger import get_app
from readmodel.models import (
    AccountSummary, DailyBankStats, EscrowHold, LedgerEntry, ProjectionCheckpoint, WebhookInbox, WebhookOutbox,
)
from readmodel.projector import LedgerProjector, projection_status, reset_read_model
from readmodel.verification import verify as verify_ledger
from readmodel.webhooks import verify_signature


# ====================================================================== #
# Helpers
# ====================================================================== #
def _pydantic_errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def _wait_for_projection(account_id: UUID, version: int, timeout: float | None = None) -> float | None:
    """Read-your-own-writes: wait until the read model shows `version`.

    Returns the measured eventual-consistency lag in ms, or None on timeout.
    """
    timeout = settings.READ_WAIT_SECONDS if timeout is None else timeout
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        row = AccountSummary.objects.filter(account_id=account_id).values_list("last_version", flat=True).first()
        if row is not None and row >= version:
            return (time.perf_counter() - started) * 1000
        time.sleep(0.03)
    return None


def _after_command(request: HttpRequest, result) -> None:
    for e in result.events:
        messages.success(request, f"Event appended: {e.event_id} - {e.event_type} (v{e.version}, "
                                  f"global position #{e.position or '-'})")
    if result.events:
        last = result.events[-1]
        lag = _wait_for_projection(last.account_id, last.version)
        if lag is None:
            messages.warning(request, "Read model not updated yet: is the projector running? "
                                      "(python manage.py run_projector). The page shows the previous state.")
        else:
            messages.info(request, f"Eventual consistency: read model updated {lag:.0f} ms after the write.")


# ====================================================================== #
# QUERY side pages (read model)
# ====================================================================== #
def dashboard(request: HttpRequest) -> HttpResponse:
    accounts = AccountSummary.objects.all()
    totals = accounts.aggregate(balance=Sum("ledger_balance"), held=Sum("held_amount"),
                                available=Sum("available_balance"), n=Count("account_id"))
    holds = EscrowHold.objects.filter(status="ACTIVE").aggregate(n=Count("hold_id"), amount=Sum("amount"))
    stats = list(DailyBankStats.objects.order_by("day"))
    chart = {
        "labels": [s.day.strftime("%d %b") for s in stats],
        "deposits": [s.deposits / 1000 for s in stats],
        "withdrawals": [s.withdrawals / 1000 for s in stats],
        "transfers": [s.transfers / 1000 for s in stats],
    }
    return render(request, "web/dashboard.html", {
        "accounts": accounts, "totals": totals, "holds": holds, "chart": json.dumps(chart),
        "alerts": WebhookOutbox.objects.count(), "status": projection_status(get_app()),
    })


def recent_events(request: HttpRequest) -> HttpResponse:
    """HTMX fragment refreshed every 2 s on the dashboard."""
    return render(request, "web/_recent.html", {
        "entries": LedgerEntry.objects.all()[:12], "status": projection_status(get_app()),
    })


def account_detail(request: HttpRequest, account_id: UUID) -> HttpResponse:
    summary = AccountSummary.objects.filter(account_id=account_id).first()
    if summary is None:
        try:
            get_app().get_events(account_id)
        except AccountNotFound:
            raise Http404("Unknown account")
        messages.warning(request, "This account exists in the event store but is not projected yet "
                                  "(eventual consistency). Start the projector.")
        return redirect("dashboard")
    entries = LedgerEntry.objects.filter(account_id=account_id)
    reversed_fees = set(entries.filter(event_type="FeeReversed").values_list("business_ref", flat=True))
    open_fees = [e for e in entries.filter(event_type="FeeCharged") if e.business_ref not in reversed_fees]
    return render(request, "web/account.html", {
        "a": summary,
        "entries": entries[:60],
        "holds": EscrowHold.objects.filter(account_id=account_id),
        "active_holds": EscrowHold.objects.filter(account_id=account_id, status="ACTIVE"),
        "open_fees": open_fees,
        "others": AccountSummary.objects.exclude(account_id=account_id),
        "write_version": get_app().get_account(account_id).version,
    })


def alerts(request: HttpRequest) -> HttpResponse:
    return render(request, "web/alerts.html", {
        "outbox": WebhookOutbox.objects.all()[:100], "inbox": WebhookInbox.objects.all()[:100],
        "urls": settings.WEBHOOK_URLS,
    })


# ====================================================================== #
# COMMAND side (write model) - every POST appends events
# ====================================================================== #
@require_POST
def open_account(request: HttpRequest) -> HttpResponse:
    try:
        c = cmd.OpenAccount(owner_name=request.POST.get("owner_name", ""),
                            account_type=request.POST.get("account_type", "CURRENT"),
                            initial_deposit=request.POST.get("initial_deposit") or "0")
        result = get_app().open_account(c.owner_name, c.account_type, initial_deposit=c.initial_millimes)
    except ValidationError as exc:
        messages.error(request, f"Invalid input - {_pydantic_errors(exc)}")
        return redirect("dashboard")
    _after_command(request, result)
    return redirect("account", account_id=result.account_id)


COMMANDS = {
    "deposit": (cmd.Deposit, lambda app, aid, c: app.deposit(aid, c.millimes, c.reference, c.channel, c.expected_version)),
    "withdraw": (cmd.Withdraw, lambda app, aid, c: app.withdraw(aid, c.millimes, c.reference, c.expected_version)),
    "reserve": (cmd.ReserveFunds, lambda app, aid, c: app.reserve_funds(aid, c.millimes, c.beneficiary_id, c.reason, c.expected_version)),
    "release": (cmd.ReleaseHold, lambda app, aid, c: app.release_hold(aid, c.hold_id, c.reason, c.expected_version)),
    "settle": (cmd.SettleEscrow, lambda app, aid, c: app.settle_escrow(aid, c.hold_id, c.reference, c.expected_version)),
    "transfer": (cmd.Transfer, lambda app, aid, c: app.transfer(aid, c.to_account_id, c.millimes, c.reference, c.expected_version)),
    "fee": (cmd.ChargeFee, lambda app, aid, c: app.charge_fee(aid, c.millimes, c.reason, c.expected_version)),
    "reverse_fee": (cmd.ReverseFee, lambda app, aid, c: app.reverse_fee(aid, c.fee_id, c.reason, c.expected_version)),
}


@require_POST
def account_command(request: HttpRequest, account_id: UUID, name: str) -> HttpResponse:
    if name not in COMMANDS:
        raise Http404("Unknown command")
    model, handler = COMMANDS[name]
    data = {k: v for k, v in request.POST.items() if k != "csrfmiddlewaretoken" and v != ""}
    try:
        command = model(**data)  # 1) Pydantic v2: is the input well-formed?
        result = handler(get_app(), account_id, command)  # 2) aggregate: is it allowed?
    except ValidationError as exc:
        messages.error(request, f"Invalid input - {_pydantic_errors(exc)}")
    except LedgerError as exc:
        messages.error(request, f"Command refused - {exc}")
    else:
        messages.success(request, result.message)
        _after_command(request, result)
    return redirect("account", account_id=account_id)


@require_POST
def race_demo(request: HttpRequest, account_id: UUID) -> HttpResponse:
    outcome = get_app().demonstrate_race(account_id, to_millimes("10"))
    for clerk, text in sorted(outcome.items()):
        (messages.success if text.startswith("SAVED") else messages.error)(
            request, f"Optimistic concurrency demo - {clerk}: {text}")
    _wait_for_projection(account_id, get_app().get_account(account_id).version)
    return redirect("account", account_id=account_id)


# ====================================================================== #
# AUDIT pages - read the immutable event store directly
# ====================================================================== #
def _parse_dt(value: str | None) -> datetime | None:
    """Parse '2026-10-20T12:00' (Africa/Tunis) or a full ISO string. Raises ValueError if invalid."""
    if not value:
        return None
    value = value.strip()
    if "T" in value and value[-6] == " ":  # '+01:00' arrived as ' 01:00' (unencoded '+' in a URL)
        value = value[:-6] + "+" + value[-5:]
    dt = datetime.fromisoformat(value)
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def replay(request: HttpRequest, account_id: UUID) -> HttpResponse:
    app = get_app()
    try:
        steps = app.replay_steps(account_id)
    except AccountNotFound:
        raise Http404("Unknown account")
    step = _int(request.GET.get("step"), len(steps))
    step = max(1, min(step, len(steps)))
    travel = None
    at_value = request.GET.get("at", "")
    if at_value:
        try:
            travel = app.state_at(account_id, _parse_dt(at_value))
        except ValueError:
            messages.error(request, "Invalid date/time.")
    chart = {
        "labels": [f"v{s['version']}" for s in steps],
        "balance": [s["balance"] / 1000 for s in steps],
        "available": [s["available"] / 1000 for s in steps],
        "snapshots": [s["version"] for s in steps if s["has_snapshot"]],
    }
    owner = steps[0]["payload"].get("owner_name", "")
    ctx = {"account_id": account_id, "owner": owner, "steps": steps, "current": steps[step - 1],
           "step": step, "chart": json.dumps(chart), "travel": travel, "at_value": at_value,
           "future": (travel.total_versions - travel.version) if travel else 0,
           "interval": app.snapshot_interval, "first_ts": steps[0]["timestamp"], "last_ts": steps[-1]["timestamp"]}
    if request.headers.get("HX-Request") and request.GET.get("fragment") == "state":
        return render(request, "web/_replay_state.html", ctx)
    return render(request, "web/replay.html", ctx)


def balance_at_api(request: HttpRequest, account_id: UUID) -> JsonResponse:
    """GET /api/accounts/<id>/balance-at/?at=2026-10-01T12:00  (time-travel API)"""
    try:
        at = _parse_dt(request.GET.get("at")) or timezone.now()
    except ValueError:
        return JsonResponse({"error": "invalid 'at' (use e.g. 2026-10-20T12:00)"}, status=400)
    try:
        r = get_app().state_at(account_id, at)
    except AccountNotFound:
        raise Http404("Unknown account")
    acc = r.account
    return JsonResponse({
        "account_id": str(account_id), "at": at.isoformat(), "version": r.version,
        "event_id": event_ref(account_id, r.version) if r.version else None,
        "ledger_balance_tnd": plain(acc.balance) if acc else None,
        "available_tnd": plain(acc.available) if acc else None,
        "held_tnd": plain(acc.held) if acc else None,
        "rebuilt_from_snapshot_version": r.snapshot_version, "delta_events_replayed": r.replayed_events,
    })


def event_store(request: HttpRequest) -> HttpResponse:
    """Raw view of the append-only event log (what is physically stored)."""
    app = get_app()
    head = app.recorder.max_notification_id() or 0
    per_page = 25
    # positions can have gaps (a rejected concurrent insert consumes a number), so we
    # read the log itself and page over the rows that really exist (newest first)
    log = list(reversed(app.recorder.select_notifications(start=1, limit=max(head, 1)))) if head else []
    pages = max(1, (len(log) + per_page - 1) // per_page)
    page = min(max(1, _int(request.GET.get("page"), 1)), pages)
    rows = []
    for n in log[(page - 1) * per_page: page * per_page]:
        raw = n.state.decode("utf-8") if isinstance(n.state, bytes) else str(n.state)
        topic = n.topic.split(":")[-1]
        rows.append({"position": n.id, "stream": str(n.originator_id), "version": n.originator_version,
                     "event_id": event_ref(n.originator_id, n.originator_version),
                     "topic": topic, "raw": raw,
                     "legacy": topic.endswith("FundsDeposited") and '"channel"' not in raw})
    head = len(log)  # number of rows really stored
    names = dict(AccountSummary.objects.values_list("account_id", "owner_name"))
    for r in rows:
        r["owner"] = names.get(UUID(r["stream"]), "")
    return render(request, "web/events.html", {"rows": rows, "head": head, "page": page, "pages": pages,
                                               "module": settings.EVENTSTORE_ENV["PERSISTENCE_MODULE"]})


def audit(request: HttpRequest, account_id: UUID) -> HttpResponse:
    summary = get_object_or_404(AccountSummary, account_id=account_id)
    if request.method == "POST":
        try:
            start = _parse_dt(request.POST.get("start"))
            end = _parse_dt(request.POST.get("end"))
        except ValueError:
            messages.error(request, "Invalid dates.")
            return redirect("audit", account_id=account_id)
        if end:
            end = end + timedelta(seconds=59)
        try:
            run = run_audit(get_app(), account_id, start, end)
        except EmptyWindow as exc:
            messages.warning(request, str(exc))
            return redirect("audit", account_id=account_id)
        return redirect("audit_run", run_id=run.id)
    return render(request, "web/audit.html", {
        "a": summary, "runs": AuditRun.objects.filter(account_id=account_id)[:20],
        "provider": describe_provider(),
    })


def audit_run(request: HttpRequest, run_id: int) -> HttpResponse:
    run = get_object_or_404(AuditRun, id=run_id)
    events = {e["event_id"]: e for e in run.evidence.get("events", [])}
    return render(request, "web/audit_run.html", {"run": run, "events": events,
                                                  "attempts_json": json.dumps(run.attempts, indent=1)})


# ====================================================================== #
# System, webhooks, health
# ====================================================================== #
def system(request: HttpRequest) -> HttpResponse:
    app = get_app()
    report = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "verify":
            report = verify_ledger(app)
        elif action == "rebuild":
            t0 = time.perf_counter()
            reset_read_model()
            n = LedgerProjector(app).catch_up()
            messages.success(request, f"Read model wiped and rebuilt from {n} events in "
                                      f"{(time.perf_counter() - t0) * 1000:.0f} ms.")
            return redirect("system")
    cp = ProjectionCheckpoint.objects.first()
    return render(request, "web/system.html", {
        "report": report, "status": projection_status(app), "checkpoint": cp,
        "provider": describe_provider(), "db_mode": settings.DB_MODE,
        "module": settings.EVENTSTORE_ENV["PERSISTENCE_MODULE"], "interval": app.snapshot_interval,
        "settings": settings,
    })


@csrf_exempt
@require_POST
def webhook_receive(request: HttpRequest) -> JsonResponse:
    """Demo receiver: proves the HMAC signature and stores the alert."""
    valid = verify_signature(request.body, request.headers.get("X-Ledger-Signature", ""))
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)
    WebhookInbox.objects.create(rule=payload.get("rule", "?"), event_id=payload.get("event_id", "?"),
                                payload=payload, signature_valid=valid)
    if not valid:
        return JsonResponse({"ok": False, "error": "bad signature"}, status=401)
    return JsonResponse({"ok": True})


def health(request: HttpRequest) -> JsonResponse:
    status = projection_status(get_app())
    return JsonResponse({"status": "ok", "db_mode": settings.DB_MODE, **{k: v for k, v in status.items()
                                                                          if k != "updated_at"}})


def search(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    rows = LedgerEntry.objects.filter(Q(event_id__icontains=q) | Q(business_ref__icontains=q) |
                                      Q(description__icontains=q) | Q(owner_name__icontains=q))[:50] if q else []
    return render(request, "web/search.html", {"q": q, "rows": rows})
