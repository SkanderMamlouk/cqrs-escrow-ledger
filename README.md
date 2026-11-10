# Core Banking & Escrow Ledger: CQRS + Event Sourcing in Python & Django

**DS1 Project 14**, *Command Query Responsibility Segregation (CQRS) & Event Sourcing in Python and Django*
ISG Tunis · AI Engineering & Software Architecture · 2026–2027 · **Section BI1, Group 6**
Team: **Arij Ben Brahim · Israa Keysi · Skander Mamlouk**

In this banking ledger, nothing is ever overwritten. Every deposit, escrow hold, transfer, fee and
fee reversal is stored as an **immutable domain event** in an **append-only PostgreSQL event store**.
Balances are rebuilt by a **left-fold** over the events, which gives **time travel to any timestamp *t***.
A separate **projector worker** keeps **denormalized SQL read tables** up to date for the
dashboards, and it fires **signed webhooks**. An **AI audit assistant** summarizes the history of an
account, citing exact event IDs and timestamps. A **deterministic verifier** rejects any
claim it cannot prove.

> New to programming? Read [docs/GUIDE_FOR_BEGINNERS.md](docs/GUIDE_FOR_BEGINNERS.md) first.
> It explains every concept and every folder in plain language.

---

## 1. What the subject asks for, and where it is in the code

| PDF requirement | Implementation |
|---|---|
| Event-sourced aggregate root emitting `AccountOpened`, `FundsDeposited`, `FundsReserved`, `TransferSettled` (+ `FundsWithdrawn`, `HoldReleased`, `FeeCharged`, `FeeReversed`) | [banking/domain.py](banking/domain.py) |
| Command handlers with **optimistic version checks** | [banking/application.py](banking/application.py) `_execute()` + `expected_version`; race demo `demonstrate_race()` |
| Commands vs Events, input validation (Pydantic v2) | [banking/commands.py](banking/commands.py) |
| **Snapshots every N events** | `SNAPSHOT_INTERVAL` (default 5): `snapshotting_intervals` in `BankingApplication` |
| **Event schema upcasting** | `FundsDeposited.class_version = 2` + `upcast_v1_v2()`. The demo contains a real legacy v1 event |
| PostgreSQL append-only event store | `eventsourcing.postgres`, tables `bankingledger_events` / `bankingledger_snapshots` in DB `ledger_events` |
| **Asynchronous read-model projector** → denormalized SQL tables | [readmodel/projector.py](readmodel/projector.py), `python manage.py run_projector`, tables in DB `ledger_read` |
| **Temporal replay audit UI**, step by step | `/accounts/<id>/replay/`: left-fold stepper, snapshot markers, time travel at *t* |
| Balance at any historical timestamp *t* | `BankingApplication.state_at()` · UI · JSON API `/api/accounts/<id>/balance-at/?at=...` |
| **Workflow automation** (native event webhooks) | alert rules → transactional outbox → HMAC-signed webhooks: [readmodel/webhooks.py](readmodel/webhooks.py) |
| **AI audit assistant** citing exact event IDs and timestamps | [ai_audit/](ai_audit/): evidence pack → LLM (Ollama / Gemini) → verifier → retry → fallback |
| **Deterministic verification harness** (pytest, Pydantic v2) | [ai_audit/verifier.py](ai_audit/verifier.py) (7 checks), [readmodel/verification.py](readmodel/verification.py) (`verify_ledger`), 59 pytest tests, GitHub Actions CI |
| Benchmarks (no invented numbers) | `python manage.py benchmark --lag` → [benchmarks/results/](benchmarks/results/) |

---

## 2. Run it

### A. Windows without Docker (works on any PC, no admin rights)
1. Install **Python 3.12** once: `winget install Python.Python.3.12`, or use python.org and tick *Add python.exe to PATH*.
2. Double-click **`SETUP.bat`**. The first run takes 2–5 minutes. It creates `.venv`, installs the packages, starts an
   embedded **real PostgreSQL 16** (the `pgserver` package), creates the tables, loads the demo data and runs the verification gate.
3. Double-click **`START.bat`**. A second window opens for the **projector worker**, and the browser opens
   **http://127.0.0.1:8000**.

### B. Docker Compose (alternative, for a PC with Docker Desktop)
The screenshots and benchmarks in this repository were produced with mode A (embedded PostgreSQL). The Docker setup runs the same
3 processes (database, web, projector) in containers. It was not available on the development laptop, so test it once before relying on it.
1. Install **Docker Desktop** (WSL 2 backend) and start it.
2. Double-click **`START_DOCKER.bat`**, or run `docker compose up --build`.
3. Open **http://localhost:8000**. The compose file starts 3 containers: `db` (PostgreSQL 16), `web` (Django + gunicorn,
   which also migrates and seeds) and `projector`. PostgreSQL is reachable on `localhost:5433` (user and password `ledger`) for pgAdmin.

Useful commands: `docker compose ps` · `docker compose logs -f projector` · `docker compose down` (stop) ·
`docker compose down -v` (stop and **delete** all events).

### C. Manual commands (Windows / Linux / macOS)
```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt     # includes: eventsourcing django psycopg2-binary (PDF) + psycopg 3
copy .env.example .env              # Linux/macOS: cp .env.example .env
python manage.py migrate            # creates the read-model tables (the event store creates its own)
python manage.py seed_demo          # synthetic demo bank, built ONLY through real commands
python manage.py run_projector      # terminal 1: asynchronous projector worker
python manage.py runserver          # terminal 2: http://127.0.0.1:8000
```

### Database modes (`DB_MODE` in `.env`)
| Mode | What it does | When |
|---|---|---|
| `embedded` *(default)* | starts a real PostgreSQL 16 from the `pgserver` pip package, with data in `var/pgdata` | any PC, no install |
| `postgres` | uses an existing server (`POSTGRES_HOST/PORT/USER/PASSWORD`) | Docker, CI, a local PostgreSQL |
| `sqlite` | two files `var/events.sqlite3` + `var/read.sqlite3` | last-resort fallback |

Both stores are always separate databases: **`ledger_events`** (write side) and **`ledger_read`** (read side).

---

## 3. AI audit assistant (local or cloud, always verified)

`AI_PROVIDER=auto` uses Ollama if it is running with `OLLAMA_MODEL`. If not, it uses Gemini when `GEMINI_API_KEY` is set.
Otherwise it uses a **deterministic template**. Every answer goes through the same 7 checks:

1. Pydantic v2 JSON schema
2. cited event IDs exist in the immutable stream and window, and every reference written in the text (EVT-, HLD-, FEE-, TRF-) exists
3. cited timestamps match exactly, and every date or time written in the text is the date or time of an event
4. the finding category matches the event type
5. every number in the headline and statements (any format, sign kept, counts included) is grounded in the evidence
6. the closing balance equals the deterministic left-fold
7. material events (fee reversals, transfers and holds ≥ `ALERT_LARGE_TRANSFER_TND`, default 10,000 TND) are all cited

When a check fails, the reasons are sent back to the model for a retry. When the retry also fails, the deterministic template is used.
Every attempt is stored in `AuditRun` and shown on the page.

* **Local AI (RTX PC):** double-click `SETUP_OLLAMA.bat`. It installs Ollama and pulls `qwen2.5:7b` (4.7 GB).
* **Cloud AI (low-RAM PCs):** create a free key at https://aistudio.google.com/apikey and set `GEMINI_API_KEY=` in `.env`.
  You can change the model name in `GEMINI_MODEL` if Google renames it.
* **Observed result (honest):** on the 5.7 GB development laptop, a live run with `qwen2.5:1.5b` (CPU) was rejected by the
  verifier on both attempts (the exact failed checks are shown in screenshot `16_ai_audit_report_verification.png`), and the
  deterministic fallback was shown. That is the gate working as designed. `qwen2.5:7b` (GPU) and Gemini are configured, but they
  were not exercised on the development laptop.

---

## 4. Command reference
| Command | Purpose |
|---|---|
| `python manage.py seed_demo [--reset]` | demo data (6 fictional customers, 98 events, 1 Sep → 9 Nov 2026) |
| `python manage.py run_projector [--once]` | projector worker (event log → read tables, webhooks) |
| `python manage.py rebuild_projections` | **wipe and rebuild** the read model from the events |
| `python manage.py verify_ledger [--catch-up] [--json f]` | deterministic consistency gate (exit code 1 when it fails) |
| `python manage.py benchmark [--lag] [--quick]` | real performance measurements + PNG charts |
| `python manage.py reset_all --yes` | delete everything (demo reset) |
| `python -m pytest -v` | 59 unit and integration tests |
| `python scripts/take_screenshots.py` | re-take the 17 screenshots (app running, after `RESET_DEMO.bat`; needs `pip install playwright`) |

## 5. Project structure
```
banking/        WRITE MODEL - pure Python (no Django): aggregate, events, command handlers, validation
readmodel/      READ MODEL - Django models (denormalized tables), projector, webhooks, verification gate
ai_audit/       AI audit assistant: evidence pack, providers, Pydantic schema, deterministic verifier
web/            Django views + templates (HTMX, Chart.js stored locally: works offline)
config/         settings, DB-mode resolver (postgres / embedded / sqlite), URLs
tests/          pytest suite (domain, application, projector, AI verifier, web, architecture rule)
benchmarks/     measured results (JSON + PNG)
docs/           beginner guide, architecture diagrams (Mermaid), demo video script, Q&A preparation
scripts/        Windows setup / start scripts used by the .bat files
.github/        CI pipeline (tests + seed + rebuild + verification gate on PostgreSQL and SQLite)
```

## 6. Pages
| URL | Side | What you see |
|---|---|---|
| `/` | read model | KPIs, CQRS flow, daily volume chart, accounts, live event stream (HTMX every 2 s) |
| `/accounts/<id>/` | both | statement (read) + command forms with `expected_version` (write), race demo |
| `/accounts/<id>/replay/` | event store | step-by-step left-fold, snapshot markers, time travel *t*, snapshot + delta bar |
| `/accounts/<id>/audit/` | event store | AI audit runs: report with citation chips, 7 checks, attempt log, evidence |
| `/events/` | event store | raw append-only rows (position, stream, version, topic, JSON); legacy v1 rows flagged |
| `/alerts/` | read model | webhook outbox (sent) and inbox (received, HMAC valid) |
| `/system/` | both | configuration, projector checkpoint and lag, **Run verification gate**, **Wipe & rebuild read model** |

## 7. Measured performance (team PC)
Measured by `python manage.py benchmark --lag` on 10 Nov 2026 (23:34 UTC), on a 13th Gen Intel Core i5-13450HX with 16 GB RAM, Windows 11,
Python 3.12 and embedded PostgreSQL 16, on the clean demo data (6 accounts, 98 events). Raw data:
[benchmarks/results/benchmark_embedded.json](benchmarks/results/benchmark_embedded.json).
Re-run the command on your own PC. The JSON file and the charts are rewritten in `benchmarks/results/`.
Stream lengths are deliberately **not** multiples of the snapshot interval, so every snapshot rebuild also replays 50 delta events.

| Measurement (median after warm-up; 15–40 runs) | Result |
|---|---|
| Rebuild a 2,550-event account, full left-fold | 65.2 ms |
| Same from the snapshot at v2,500 + 50 delta events | 1.94 ms (≈ 34× faster) |
| Rebuild a 150-event account: full / snapshot + 50 delta | 4.1 ms / 1.71 ms |
| "List all balances" (6 accounts, 98 events): read-model SELECT | 0.28 ms |
| Same by replaying the streams: with snapshots / without | 2.72 ms / 4.26 ms |
| Write throughput (load + decide + atomic append, one command at a time, 1 event each) | 351–403 commands/s |
| Eventual-consistency lag (projector polls every 0.2 s, 40 probes) | median 204 ms · p95 207 ms |

## 8. Troubleshooting
* **The read model is not updated** / "projector not running?" → START.bat opens a second window running `run_projector`. Keep it open.
* **Port 8000 is busy** → `python manage.py runserver 127.0.0.1:8001`
* **Start from zero** → `RESET_DEMO.bat`, or `python manage.py seed_demo --reset`
* **Docker: `host.docker.internal` is unreachable for Ollama** → start the Ollama app on Windows and check `ollama list`. If it still fails, set the Windows environment variable `OLLAMA_HOST=0.0.0.0`, then restart Ollama.
* **Python 3.13 or newer** → the embedded PostgreSQL (`pgserver`) only exists for Python 3.11 and 3.12. SETUP.bat installs 3.12 automatically; otherwise the app falls back to SQLite.
* **Embedded PostgreSQL won't start** → delete the `var/` folder (it only holds local data), then run SETUP.bat again. You can also set `DB_MODE=sqlite`.

## 9. References
* J. Bywater, *eventsourcing* Python library, v9.5.6 documentation (accessed Nov 2026): https://eventsourcing.readthedocs.io/
* M. Fowler, "Event Sourcing" (2005): https://martinfowler.com/eaaDev/EventSourcing.html
* M. Fowler, "CQRS" (2011): https://martinfowler.com/bliki/CQRS.html
* G. Young, "CQRS Documents" (2010): https://cqrs.files.wordpress.com/2010/11/cqrs_documents.pdf
* Microsoft Azure Architecture Center, "Event Sourcing pattern" & "CQRS pattern" (accessed Nov 2026): https://learn.microsoft.com/azure/architecture/patterns/event-sourcing
* Django 5.2 documentation: https://docs.djangoproject.com/en/5.2/ · Pydantic v2: https://docs.pydantic.dev/
* PostgreSQL 16 documentation: https://www.postgresql.org/docs/16/

*All customers, companies and transactions in the demo are fictional (synthetic data).*
