# The project explained simply (for the whole team)

This guide assumes **zero programming knowledge**. Read it in order. After it you will be able to
explain every slide and every screen, and answer the professor's questions.

---

## Part 1: The story in one minute

Picture an old-fashioned bank that keeps a **paper notebook** for each customer.
The clerk writes one line per operation, **in pen**:

```
1 Sep  Account opened for Yasmine
1 Sep  + 96,500 TND deposited
25 Sep + 4,200 TND salary received
3 Oct    85,000 TND BLOCKED for the apartment purchase (escrow)
28 Oct - 85,000 TND paid to the seller (deed signed)
```

The clerk never erases a line. To know the balance, they **add up the lines from the top**.
To know the balance **on 10 October**, they add the lines up to 10 October and stop.

**Our project is that notebook, done by a computer.** Each line is an **event**.
Writing lines instead of overwriting a number is called **Event Sourcing**.

The bank manager doesn't want to add up 1,000 lines every time they open the dashboard, though.
So a **secretary** (the *projector*) keeps a second, ready-to-read **summary board**:
"Yasmine: 16,175 TND". The pen notebook is used to **write**, and the summary board is used to **read**.
Keeping writing and reading separate like this is called **CQRS**.

---

## Part 2: Every concept with an everyday comparison

| Concept (English) | French | Everyday comparison | Where in our app |
|---|---|---|---|
| **Event** | événement | One line written in pen in the notebook: a fact, in the past tense ("Funds deposited") | `banking/domain.py`: `FundsDeposited`, `FeeReversed`... |
| **Event Sourcing** | sourçage par événements | Keep **all** the lines instead of one number that gets overwritten | the whole project |
| **Event store** | magasin d'événements | The notebook itself. You can only **add** lines (append-only) | PostgreSQL database `ledger_events` |
| **Left-fold** (replay) | pli à gauche / rejeu | Adding up the lines from the first to the last to find the balance | `replay_steps()`, `rebuild_without_snapshot()` |
| **Command** | commande | A *request* that can be refused: "please withdraw 500 TND" | `deposit()`, `withdraw()`... and the web forms |
| **Aggregate / aggregate root** | agrégat / racine | The strict clerk in charge of ONE notebook: they check the rules before writing | the `BankAccount` class |
| **Version** | version | The line number in the notebook (1, 2, 3…) | `v12` on every screen |
| **Optimistic concurrency** | concurrence optimiste | Two clerks both want to write line 13. The first one wins; the second is told "line 13 already exists, re-read the notebook" | `expected_version`, *Concurrency race demo* button |
| **Snapshot** | instantané | A "balance carried forward" note every 5 lines, so you don't add up from the very first page | `SNAPSHOT_INTERVAL=5`, the teal **SNAPSHOT** badges |
| **Upcasting** | migration à la lecture | An old printed form with a missing field: when you read it, you fill the field with "unknown" without changing the original paper | `FundsDeposited.upcast_v1_v2`, the "schema v1" badge on the Event Store page |
| **CQRS** | séparation commande / requête | The kitchen (write side) and the menu board (read side) are different places | `banking/` (write) vs `readmodel/` (read) |
| **Read model / projection** | modèle de lecture | The summary board, ready to read, with no calculation needed | tables `AccountSummary`, `LedgerEntry`, `EscrowHold`, `DailyBankStats` |
| **Projector** | projecteur | The secretary who reads each new notebook line and updates the board | `readmodel/projector.py`, the second black window |
| **Checkpoint** | point de reprise | The bookmark where the secretary stopped reading | table `ProjectionCheckpoint` |
| **Eventual consistency** | cohérence à terme | The board shows the new balance a fraction of a second after the notebook (about 0.2 s) | "Eventual consistency: read model updated … ms after the write" message (about 200 ms) |
| **Escrow (hold)** | séquestre / blocage | Money is blocked for someone. It is still yours, but you can't spend it. Later it is either paid out or released | `FundsReserved`, `HoldReleased`, `TransferSettled` |
| **Webhook** | webhook | An automatic text message sent when something important happens | `/alerts/` page |
| **HMAC signature** | signature HMAC | A secret stamp proving the message really comes from our bank | `X-Ledger-Signature` |
| **AI audit assistant** | assistant d'audit IA | An intern who writes a report from the notebook | `/accounts/<id>/audit/` |
| **Deterministic verifier** | vérificateur déterministe | The chief accountant who checks **every** line number, date and amount in the intern's report before signing it | `ai_audit/verifier.py` (7 checks) |
| **Hallucination** | hallucination | The intern invents a date or an amount that is not in the notebook | caught by checks 2 to 6 |

---

## Part 3: What happens when you click "Deposit 50 TND"

```
 YOU (browser)                                                     (each arrow = a few milliseconds)
   |
   | 1. the form is sent: amount=50, expected_version=12
   v
 web/views.py  account_command()
   |
   | 2. Pydantic checks the FORMAT: is 50 a positive number with at most 3 decimals?
   v
 banking/application.py  _execute()        <- WRITE SIDE (the "C" of CQRS)
   |
   | 3. LOAD    rebuild the account: latest snapshot (v10) + events v11, v12
   | 4. CHECK   is the account still at version 12? (otherwise: "Concurrency conflict")
   | 5. DECIDE  BankAccount.deposit() checks the RULES (amount > 0) and creates the event
   |            "FundsDeposited 50 TND" as version 13
   | 6. SAVE    the event is APPENDED to PostgreSQL (database ledger_events)
   v
 event store: new line #99  (EVT-703c6537-0013)
   .
   .  ~0.2 seconds later (asynchronous)
   v
 readmodel/projector.py  (the second window)   <- READ SIDE (the "Q" of CQRS)
   |
   | 7. "is there anything after position #98?" -> yes, #99
   | 8. update AccountSummary (balance +50), add a LedgerEntry line, update DailyBankStats
   | 9. save the checkpoint = 99, in the SAME transaction (nothing lost, nothing doubled)
   | 10. if an alert rule matches -> write a webhook into the outbox -> POST it (signed)
   v
 read model (database ledger_read)
   |
   | 11. the page reloads and shows the new balance + "read model updated ~200 ms after the write"
   v
 YOU
```

---

## Part 4: Tour of the folders (what each file does)

```
cqrs_ledger/
├── SETUP.bat / START.bat ...   double-click files (no typing needed)
├── manage.py                  Django's "remote control" (python manage.py <command>)
├── requirements.txt           list of libraries to install
├── .env.example -> .env       settings you can change (database mode, AI, thresholds)
├── docker-compose.yml         recipe for 3 containers: database, web, projector
│
├── banking/       <- THE BRAIN (write side) - pure Python, no Django
│   ├── domain.py        the BankAccount aggregate + the 8 event types + business rules
│   ├── application.py   command handlers: load -> check version -> decide -> save; time travel
│   ├── commands.py      Pydantic v2 models that check form inputs
│   ├── money.py         amounts stored in millimes (integers), never floats
│   ├── clock.py         where event timestamps come from (simulated dates for the demo)
│   └── exceptions.py    business errors (insufficient funds, concurrency conflict...)
│
├── readmodel/     <- THE SUMMARY BOARD (read side)
│   ├── models.py        the denormalized tables shown on screens
│   ├── projector.py     the secretary: event log -> tables (+ checkpoint, + alert rules)
│   ├── webhooks.py      sends alerts as signed HTTP messages, with retries
│   ├── verification.py  the ledger verification gate (9 kinds of checks)
│   └── management/commands/   seed_demo, run_projector, rebuild_projections, verify_ledger, benchmark, reset_all
│
├── ai_audit/      <- THE INTERN + THE CHIEF ACCOUNTANT
│   ├── evidence.py      builds the facts sheet from the event store (exact ids, dates, amounts)
│   ├── providers.py     talks to Ollama (local AI) or Gemini (cloud AI)
│   ├── schemas.py       the exact JSON shape the AI must answer with
│   ├── verifier.py      the 7 deterministic checks
│   └── assistant.py     AI -> verify -> retry with feedback -> deterministic fallback
│
├── web/           <- THE SCREENS (Django views + HTML templates + CSS)
├── config/        <- settings, which database to use (dbmode.py), URL list
├── tests/         <- 59 automatic tests (pytest)
├── benchmarks/    <- measured performance results (JSON + charts)
└── .github/workflows/ci.yml   <- GitHub runs all tests + the verification gate on every push
```

---

## Part 4b: The library we build on (the professor's reference links)

We did not write the event store ourselves. We use the **eventsourcing** library, the reference given on
the course sheet for subject 14:

| Link | What it is | What we take from it |
|---|---|---|
| https://github.com/pyeventsourcing/eventsourcing | the source code of the library (version 9.5.6 in our project) | the `Aggregate` class, the `@event` decorator, the `Application` class, snapshots and the PostgreSQL event store |
| https://eventsourcing.readthedocs.io/ | its official documentation | we followed one section per feature: aggregates and `@event`, versioning (upcasting), repository, snapshots, notification log. Each link is written next to the code that uses it |
| https://github.com/pyeventsourcing/eventsourcing-django | an extra package that saves the events through Django's own database layer | we tested it, but its latest version needs an older version of the library (below 9.5). We kept our version 9.5.6 and explained why (see DEFENSE_QA.md, Q15) |

In one sentence for the jury: *"The library gives us the notebook (event store, aggregates, snapshots);
we wrote the bank rules, the summary board (projector), the verification gate and the web screens."*

---

## Part 5: How to run it (step by step)

**On any Windows PC (no Docker):**
1. Install Python 3.12 (only once): open *PowerShell* and type `winget install Python.Python.3.12`.
2. Double-click **SETUP.bat** and wait until "SETUP COMPLETE" (2 to 5 minutes the first time).
3. Double-click **START.bat**. Two black windows appear: the web server and the projector.
   **Keep both open.** The browser opens by itself at http://127.0.0.1:8000.
4. To stop, close both black windows.

**On the RTX PC with Docker:** start Docker Desktop, then double-click **START_DOCKER.bat**.

**Local AI on the RTX PC:** double-click **SETUP_OLLAMA.bat** once (it downloads 4.7 GB).

---

## Part 6: Things to show during the demo (in this order)

1. **Dashboard**: the CQRS flow strip (command → event store → projector → read model), the KPIs, and the live event stream that refreshes every 2 seconds.
2. **Yasmine Trabelsi → Temporal replay**: click events one by one and watch the balance change (the left-fold).
   Point at the teal **SNAPSHOT** badges every 5 events.
3. **Time travel**: type `2026-10-20 12:00`. Balance 99,487.500 TND, with 85,000 TND blocked in escrow.
   The bar shows "snapshot @ v5 + 3 delta events replayed".
4. **Account page → Deposit 50 TND**. Read the green messages: the event id, then "read model updated XXX ms after
   the write". That is eventual consistency, measured live.
5. **Concurrency race demo** button: one clerk is SAVED and the other is REJECTED. That is optimistic concurrency.
6. **Event Store page**: the raw JSON rows. Find the orange **schema v1 → upcast** badge (Karim's legacy deposit).
7. **Webhook Alerts**: LARGE_TRANSFER (85,000 TND escrow), FEE_REVERSED, LOW_AVAILABLE_BALANCE, each with "HMAC valid".
8. **AI audit** on Yasmine: the report, the citation chips (click one and it opens the exact event), and the 7 green checks.
9. **System page → Run verification gate**: all checks PASSED. Then **Wipe & rebuild read model**: everything comes back,
   which proves the events are the only source of truth.

---

## Part 7: The 5 sentences to remember

1. "We never UPDATE or DELETE. We only APPEND immutable events. The state is a left-fold over them."
2. "Commands can be refused; events are facts that already happened."
3. "Two writers cannot both create the same version: the unique (stream, version) key gives us optimistic concurrency."
4. "Reads come from separate denormalized tables built asynchronously by a projector, so the read side is eventually consistent (≈ 0.2 s)."
5. "The AI never has the last word. A deterministic verifier checks every event id, timestamp and amount against the event store."
