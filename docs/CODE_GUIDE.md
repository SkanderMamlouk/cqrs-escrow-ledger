# Code guide: every file, explained simply

This guide explains the code of the project file by file. It is written for someone who has never used
Python web frameworks before. Read `GUIDE_FOR_BEGINNERS.md` first (the ideas), then this guide (the code).

How to read it: open the file on the left in VS Code and this guide on the right. Each section follows the
file from top to bottom.

---

## Part 1. The tools in ten minutes

### 1.1 Python words you will see everywhere

| You see | It means |
|---|---|
| `def deposit(self, amount):` | a **function** (a named block of instructions). `self` is the object the function belongs to |
| `class BankAccount(Aggregate):` | a **class**: a mould for objects. `(Aggregate)` means "BankAccount inherits everything Aggregate already knows" |
| `amount: int`, `-> str`, `int \| None` | **type hints**: labels saying what kind of value is expected (`int` = whole number, `str` = text, `None` = nothing). Python does not enforce them; they help the reader |
| `@event(FundsDeposited)`, `@property`, `@require_POST` | a **decorator**: a label placed above a function that adds behaviour to it |
| `f"Deposit of {amount}"` | an **f-string**: text with values inserted between `{ }` |
| `lambda a: a.deposit(x)` | a tiny function without a name, written on one line |
| `[e for e in events if e.amount > 0]` | a **list comprehension**: "the list of all `e` in `events` such that ..." |
| `try: ... except InsufficientFunds:` | "try this; if this error happens, do that instead" |
| `raise InvalidAmount("...")` | stop and report an error |
| `with transaction.atomic():` | "everything inside this block happens completely or not at all" |
| `@dataclass` | a class that only stores values (like a record or a form) |
| `from banking.money import fmt` | use the function `fmt` written in the file `banking/money.py` |

### 1.2 Django in ten minutes

**Django** is a Python framework for building websites. It gives us ready-made parts so we only write what is
specific to our bank. The words:

| Django word | What it is | In our project |
|---|---|---|
| **project settings** | one Python file with all the configuration | `config/settings.py` |
| **app** | a folder that groups related code; Django loads the apps listed in `INSTALLED_APPS` | `readmodel`, `ai_audit`, `web` |
| **URL list** | which address calls which function | `config/urls.py` |
| **view** | a Python function that receives a browser request and returns a page | `web/views.py` |
| **template** | an HTML page with holes `{{ ... }}` that the view fills in | `web/templates/web/*.html` |
| **model** | a Python class that describes one database table; each attribute is a column | `readmodel/models.py`, `ai_audit/models.py` |
| **ORM** | lets us read and write tables with Python instead of SQL: `AccountSummary.objects.filter(...)` | used in views and the projector |
| **migration** | a generated file that creates or changes tables to match the models | `*/migrations/0001_initial.py` |
| **management command** | a custom command run as `python manage.py <name>` | `readmodel/management/commands/` |
| **manage.py** | the "remote control" of the project | `python manage.py runserver`, `migrate`, `seed_demo` ... |
| **context processor** | adds values to every page automatically | `web/context_processors.py` (the top bar status) |
| **template filter** | a small helper usable inside HTML: `{{ amount\|tnd }}` | `web/templatetags/ledger_extras.py` |

**The life of one page request** (for example opening the dashboard):

```
browser asks for  http://127.0.0.1:8000/
   -> config/urls.py finds the line  path("", views.dashboard)
   -> web/views.py  dashboard(request)  reads the tables with the ORM
   -> render("web/dashboard.html", values)  fills the HTML holes
   -> the finished HTML page goes back to the browser
```

Important: **Django is only used on the read side and for the web pages.** The bank rules and the event store
(`banking/`) are plain Python and do not know Django exists. A test (`tests/test_architecture.py`) checks this.

### 1.3 The other libraries

| Library | Used for | Where |
|---|---|---|
| **eventsourcing 9.5.6** | the event store, the `Aggregate` base class, the `@event` decorator, snapshots | `banking/domain.py`, `banking/application.py` |
| **Pydantic v2** | checks that input has the right shape (positive amount, valid id, max length) | `banking/commands.py`, `ai_audit/schemas.py` |
| **psycopg 3** | talks to PostgreSQL | used by eventsourcing and Django |
| **pgserver** | starts a real PostgreSQL 16 inside the project folder (no installation) | `config/dbmode.py` |
| **httpx** | sends HTTP requests (to Ollama, Gemini, and the webhooks) | `ai_audit/providers.py`, `readmodel/webhooks.py` |
| **HTMX** | lets a page refresh one part of itself without reloading (a small JavaScript file) | `hx-get` attributes in the templates |
| **Chart.js** | draws the charts in the browser | dashboard and replay pages |
| **whitenoise** | serves the CSS and JavaScript files | `config/settings.py` |
| **pytest** | runs the automatic tests | `tests/` |
| **matplotlib** | draws the benchmark charts (PNG) | `benchmark.py` |

---

## Part 2. The big picture

```
             WRITE SIDE (commands)                            READ SIDE (queries)
  web form ---> banking/commands.py (shape check)
            ---> banking/application.py (load, check version, decide, save)
            ---> banking/domain.py (bank rules, creates events)
            ---> EVENT STORE (database ledger_events, append only)
                                   |
                                   |  readmodel/projector.py polls every 0.2 s
                                   v
                        READ MODEL (database ledger_read)  <--- web/views.py shows pages from here
                                   |
                                   +--> webhooks outbox ---> signed HTTP alerts

  ai_audit/: reads the event store, asks an AI for a report, checks every line of it (verifier)
  config/:   settings, which database to use, URL list
```

Folder by folder:

| Folder | Role | Uses Django? |
|---|---|---|
| `banking/` | the bank itself: rules, events, commands | **no** (pure Python) |
| `readmodel/` | the summary tables, the projector, webhooks, verification gate, commands | yes |
| `ai_audit/` | the AI audit assistant and its verifier | yes (to save audit runs) |
| `web/` | the pages | yes |
| `config/` | settings and wiring | yes |
| `tests/` | 59 automatic tests | yes (pytest-django) |
| `scripts/` | PowerShell installers and the screenshot script | no |

---

## Part 3. `config/`: settings and wiring

### `config/settings.py`
The configuration of the whole project, read once at start-up.
* `_load_dotenv()` reads the file `.env` (one `NAME=value` per line) and puts each value in the environment,
  so you can change settings without touching code. `setdefault` means a value already set in Windows wins.
* `env_bool()` turns text like `1`, `true`, `yes` into True.
* `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`: standard Django security settings.
  `DEBUG=1` shows detailed error pages (fine for a demo).
* `INSTALLED_APPS`: the Django apps we load. We keep only what we use: content types, messages (the green and
  red banners), static files, and our three apps.
* `MIDDLEWARE`: layers every request goes through (security headers, static files, CSRF protection against
  forged forms, messages).
* `TEMPLATES`: where the HTML files are and which context processors run (ours is `web.context_processors.system_status`).
* `DB_MODE, EVENTSTORE_ENV, _READ_DB = dbmode.resolve(BASE_DIR)`: asks `dbmode.py` which databases to use.
  `EVENTSTORE_ENV` goes to the eventsourcing library (write side), `DATABASES` goes to Django (read side).
* `SNAPSHOT_INTERVAL` (5), `PROJECTOR_POLL_SECONDS` (0.2), `READ_WAIT_SECONDS` (2.5): how often to take a snapshot,
  how often the projector looks for new events, how long a page waits for the read model after a command.
* `ALERT_LARGE_TRANSFER_TND` (10000), `ALERT_LOW_BALANCE_TND` (100), `WEBHOOK_URLS`, `WEBHOOK_SECRET`: alert rules.
* `AI_PROVIDER`, `OLLAMA_*`, `GEMINI_*`, `AI_TIMEOUT_SECONDS`, `AI_MAX_ATTEMPTS`: AI settings.
* `TIME_ZONE = "Africa/Tunis"`, `USE_TZ = True`: dates are stored in UTC and shown in Tunis time.
* `STATIC_*`, `STORAGES`: where CSS and JavaScript are served from.
* `LOGGING`: the projector and webhook messages are printed in the black window.

### `config/dbmode.py`
Decides where the data lives. Three modes, chosen with `DB_MODE` in `.env`:
* `embedded` (default): starts a real PostgreSQL 16 inside `var/pgdata` with the `pgserver` package. Nothing to install.
* `postgres`: uses an existing PostgreSQL server (Docker or CI).
* `sqlite`: two simple files, used by the tests and as a fallback.

Functions:
* `resolve()` reads `DB_MODE`. If `embedded` is asked but `pgserver` is not installed (Python 3.13+), it prints a
  warning and falls back to `sqlite`. It returns three things: the mode, the settings for the event store, and the
  settings for Django.
* `_start_embedded_postgres()` starts the embedded server. Before that, it copies time zone files into pgserver's
  folder, because pgserver on Windows ships without them and PostgreSQL would refuse to start.
* `_ensure_databases_exist()` connects to PostgreSQL and creates the two databases `ledger_events` (write side)
  and `ledger_read` (read side) if they are missing. **Two separate databases = CQRS.**

### `config/ledger.py`
Holds the one shared `BankingApplication` object.
* `get_app()` creates it the first time it is needed, then always returns the same one. The `_lock` makes sure
  two web requests at the same moment do not create two.
* `set_app()` replaces it (used by the tests and by `reset_all`).

### `config/urls.py`
The address book of the website. Each `path("address", function)` line connects one URL to one view in
`web/views.py`. `<uuid:account_id>` means "this part of the address is an account id".

### `config/wsgi.py`
The entry point used by the production web server Gunicorn in Docker. You never edit it.

### `config/settings_test.py`
Settings for the tests: SQLite in a temporary folder, AI turned off (deterministic), shorter waits.
`pytest.ini` tells pytest to use it.

---

## Part 4. `banking/`: the bank (write side, pure Python)

### `banking/money.py`
All amounts are stored as whole numbers of **millimes** (1 TND = 1000 millimes), never as decimals with a
comma, because computers make tiny rounding mistakes with decimal numbers (0.1 + 0.2 is not exactly 0.3).
* `to_millimes("12.500")` gives `12500`. It uses `Decimal` (exact decimal arithmetic) and rounds half up.
* `to_tnd(12500)` gives `Decimal("12.500")`.
* `fmt(12500)` gives `"12.500 TND"` with thousands separators, for the screen.
* `plain(12500)` gives `"12.500"` without separators, for JSON and the AI.

### `banking/clock.py`
Where the time of every event comes from.
* `now()` returns the real current time, unless a simulated time was set.
* `simulated_time(moment)` is used by `seed_demo` to write the demo events "as if" they happened in September
  and October 2026. `threading.local()` keeps the simulated time private to the current thread.

### `banking/identity.py`
Where account ids and short references (HLD-..., FEE-..., TRF-...) come from.
* `new_uuid()` normally returns a random id. Inside `reproducible()`, it returns ids computed from a fixed seed
  (`uuid5`), so the demo bank gets **the same ids on every computer** (that is why Yasmine is always `703c6537`).
* `new_hex8()` gives the 8 characters used in references like `HLD-e9d5e56d`.

### `banking/exceptions.py`
Our business errors. They all inherit from `LedgerError`, so the web page can catch them all at once and show
"Command refused". `ConcurrencyConflict` builds a clear message: "you expected version 12 but it is now at 13".

### `banking/domain.py`
The heart of the project: **the bank account and its 8 events.** Built with the eventsourcing library.

* `new_ref("HLD")` makes a reference like `HLD-1a2b3c4d`.
* `event_ref(account_id, 13)` makes the human event id `EVT-703c6537-0013` (first 8 characters of the account,
  then the version on 4 digits).

`class BankAccount(Aggregate)`: the **aggregate root**, the strict clerk in charge of one account.
* `class Event` with `create_timestamp()`: every event of this account takes its time from `clock.now()`.
* `create_id()`: every new account takes its id from `identity.new_uuid()`.
* For each kind of event there are always **three pieces**, in this order:
  1. the **event class** (for example `FundsDeposited`) lists the facts it stores (`amount`, `reference`, `channel`);
  2. the **command method** (for example `deposit()`) checks the business rules and refuses with an error if
     they are broken; nothing is written in that case;
  3. the **apply method** decorated with `@event(...)` (for example `_on_deposited()`) changes the state.
     The decorator makes the library **create and record the event** each time this method is called, and
     **replay** this same method when the account is rebuilt from its history.

| Event | Command method | Rules checked | State change |
|---|---|---|---|
| `AccountOpened` | `BankAccount(...)` (the `__init__`) | none | owner, type, currency, balance 0 |
| `FundsDeposited` | `deposit()` | amount > 0 | balance + amount |
| `FundsWithdrawn` | `withdraw()` | amount > 0, amount <= available | balance - amount |
| `FundsReserved` | `reserve_funds()` | amount > 0, amount <= available | adds a hold (money blocked) |
| `HoldReleased` | `release_hold()` | the hold exists | removes the hold |
| `TransferSettled` | `send_transfer()` / `receive_transfer()` | amount > 0; if it settles a hold, exact held amount; otherwise enough available | balance - or + amount; closes the hold |
| `FeeCharged` | `charge_fee()` | amount > 0, enough available | balance - fee, remembers the fee |
| `FeeReversed` | `reverse_fee()` | the fee exists and was not reversed already | balance + fee, marks it reversed |

* `held` = total of the active holds. `available` = `balance - held`. They are `@property`: computed when read.
* **Upcasting**: `FundsDeposited` has `class_version = 2` and `upcast_v1_v2()`. Old deposits (version 1) were
  stored without a `channel`. When the library reads such an old event, it calls `upcast_v1_v2()` which adds
  `channel = "LEGACY-UNKNOWN"`. The stored row is never modified.
* `EVENT_TYPES`: the list of the 8 event names.
* `event_type_name(e)`: the name of an event (`"FundsDeposited"`).
* `signed_amount(e)`: the effect on the balance: positive for money in, negative for money out, 0 for holds.
* `describe(e)`: one readable sentence for each event, used on every screen and given to the AI.

### `banking/commands.py`
Checks the **shape** of what the user typed, before the bank even looks at it, with Pydantic.
* `PositiveTND`: a decimal number greater than 0, at most 3 decimals. `Text`: at most 160 characters.
* `_Command`: the common base. `extra="forbid"` refuses unknown fields. `expected_version` is the version the
  page showed (for optimistic concurrency). `millimes` converts the amount.
* One class per form: `OpenAccount`, `Deposit`, `Withdraw`, `ReserveFunds`, `ReleaseHold`, `SettleEscrow`,
  `Transfer`, `ChargeFee`, `ReverseFee`. For example `ReleaseHold.hold_id` must look like `HLD-` plus 8 characters,
  and a fee reversal needs a reason of at least 3 characters.

Difference to remember: `commands.py` checks the **format** ("is 50 a positive number?"); `domain.py` checks the
**business rules** ("does the account have 50 available?").

### `banking/application.py`
The **command handlers**: the layer between the web pages and the accounts. Built on the library's `Application`.

Small record classes (`@dataclass`):
* `RecordedEvent`: what we show after a command: event id, type, version, time, global position, description.
* `CommandResult`: the message, the recorded events, the account id, and extras (like the new `hold_id`).
* `TimeTravelResult`: the state of an account at a past moment, with how it was rebuilt.

`class BankingApplication(Application)`:
* `name = "BankingLedger"`: the library uses it to name the tables `bankingledger_events` and `bankingledger_snapshots`.
* `snapshotting_intervals = {BankAccount: 5}`: the library automatically stores a snapshot every 5 versions.
  `__init__` reads `SNAPSHOT_INTERVAL` so this number can be changed in `.env`.
* `get_account(id, version=None)`: loads an account. The library takes the **latest snapshot** and replays only
  the events after it. With `version=8` it rebuilds the account as it was at version 8.
* `_recorded()`: turns what the library returns after a save into our `RecordedEvent` list.
* `_execute(account_ids, decide, expected_version)`: **the command pipeline used by every command**:
  1. LOAD the account(s);
  2. CHECK: if the page sent `expected_version` and the account has moved on, refuse with `ConcurrencyConflict`;
  3. DECIDE: run the business method (`decide`), which creates the new event(s) in memory;
  4. SAVE: `self.save(...)` appends the events in **one transaction**. If another writer saved the same version
     a split second earlier, the database refuses (unique key on account + version) with `IntegrityError`; we
     then reload and either report the conflict or retry (at most 3 times).
* `open_account()`, `deposit()`, `withdraw()`, `reserve_funds()`, `release_hold()`, `charge_fee()`,
  `reverse_fee()`: each one calls `_execute` with the right business method. `reserve_funds()` also checks
  that the beneficiary account exists and is not the same account.
* `transfer()` and `settle_escrow()`: load **two** accounts and save both in one transaction, so money cannot
  leave one account without arriving in the other. `settle_escrow()` pays exactly the held amount to the
  beneficiary of the hold.
* `get_events(id)`: all events of one account, in order.
* `snapshot_versions(id)`: the versions where a snapshot exists (5, 10, ...).
* `rebuild_without_snapshot(id)`: replays **all** events from the first one, ignoring snapshots. Used by the
  verification gate and the benchmark to compare.
* `state_at(id, at)`: **time travel**. Finds the last event before the moment `at`, finds the latest snapshot
  at or before that version, and rebuilds the account at that version.
* `replay_steps(id)`: replays the events one by one and keeps the state after each one (balance, held,
  available, holds, snapshot or not). This feeds the "Temporal replay" page.
* `iter_account_ids()`: walks through the global event log and collects every account that was opened.
* `demonstrate_race(id, amount)`: the **concurrency race demo**. Two threads (Clerk A and Clerk B) load the same
  version, both create version n+1, wait for each other at a `Barrier`, then both save at the same moment. The
  database accepts one and refuses the other with `IntegrityError`.
* `import_legacy_v1_deposit()`: writes one deposit in the **old format** (no channel, version 1), directly
  through the recorder, to simulate data migrated from an old system. Used once by `seed_demo` (Karim).

---

## Part 5. `readmodel/`: the summary tables (read side)

### `readmodel/models.py`
The tables of the read database. Each class is one table; amounts are whole millimes (`BigIntegerField`).

| Table | One row per | Main columns |
|---|---|---|
| `AccountSummary` | account | balance, held amount, available, totals, number of events, `last_version` |
| `LedgerEntry` | event (the bank statement) | global `position`, version, event id, type, signed amount, balance after |
| `EscrowHold` | escrow hold | amount, beneficiary, status ACTIVE / RELEASED / SETTLED, opening and closing event |
| `DailyBankStats` | day | deposits, withdrawals, transfers, fees of that day (the dashboard chart) |
| `ProjectionCheckpoint` | projector | `position` = the last event already copied (the bookmark), `last_lag_ms` |
| `WebhookOutbox` | alert to send | rule, event, JSON payload, status PENDING / DELIVERED / FAILED, attempts |
| `WebhookInbox` | alert received | payload and whether the signature was valid |

`class Meta` sets the default order and the uniqueness rules (for example a `LedgerEntry` is unique per
account + version, so an event can never be copied twice).

### `readmodel/projector.py`
**The projector** (the secretary): copies new events from the event store into the summary tables.
* `LedgerProjector.__init__`: remembers the application and the alert thresholds, converted to millimes.
* `run_once()`: reads the bookmark (`ProjectionCheckpoint`), asks the event store for the next events after it
  (`select_notifications`), then **inside one database transaction** applies each event and moves the bookmark.
  If the computer stops in the middle, the transaction is cancelled and the same events are processed again
  next time: nothing is lost and nothing is counted twice. `select_for_update()` locks the bookmark so two
  projectors cannot run the same batch. It also measures the lag (time between the event and now).
* `catch_up()`: calls `run_once()` until there is nothing left.
* `run_forever()`: the loop of the projector window: project, send pending webhooks, sleep 0.2 s, repeat.
  If the database is not ready yet, it waits 2 seconds and tries again instead of crashing.
* `apply(position, event)`: updates the tables for one event:
  creates or updates the `AccountSummary` row, adds a `LedgerEntry` line, creates or closes an `EscrowHold`,
  updates `DailyBankStats` (`_daily`), and checks the alert rules (`_alerts`).
* `_owner()`: finds the owner's name for an account id. `_close_hold()`: marks a hold RELEASED or SETTLED.
* `_alerts()`: three rules. LARGE_TRANSFER (transfer out of at least 10,000 TND), FEE_REVERSED (any reversal),
  LOW_AVAILABLE_BALANCE (available balance goes below 100 TND). Each alert is written in `WebhookOutbox` in the
  **same transaction** as the read model (the "transactional outbox" pattern), so an alert is never lost.
* `reset_read_model()`: deletes all summary tables and the bookmark (the event store is not touched).
* `projection_status()`: event store head, bookmark position, lag in events, whether the projector looks stopped.

### `readmodel/webhooks.py`
Sends the alerts as signed HTTP messages.
* `sign(body)`: computes an HMAC-SHA256 signature of the message with the secret key. Only someone who knows the
  secret can produce it.
* `verify_signature(body, header)`: recomputes it and compares (with `compare_digest`, which is safe against timing tricks).
* `deliver_pending()`: takes the alerts that are due, POSTs each one to every URL in `WEBHOOK_URLS` with the
  signature in the header `X-Ledger-Signature`. Success: DELIVERED. Failure: try again later, waiting 2, 4, 8 ...
  seconds (at most 60), up to 10 attempts, then FAILED.

### `readmodel/verification.py`
**The verification gate**: proves that everything is consistent. For each account (7 checks):
1. versions are 1, 2, 3 ... without holes;
2. timestamps never go backwards;
3. rebuilding with snapshots gives exactly the same state as replaying everything;
4. every stored snapshot equals a replay up to its version;
5. the read model row equals the event store;
6. the statement lines add up to the balance, one line per event;
7. the available balance is not negative.

Then for the whole bank (2 checks): money sent by transfers equals money received, and the projector has
reached the end of the event log. With 6 accounts: 6 x 7 + 2 = **44 checks**.

### `readmodel/management/commands/`
Each file is one `python manage.py <name>` command. The class is always called `Command`, `add_arguments()`
declares the options, `handle()` does the work.

| Command | What it does |
|---|---|
| `seed_demo` | creates the demo bank (6 fictional customers, 98 events) **through the real commands**, with simulated dates and reproducible ids. `--reset` wipes everything first. The `plan` list is sorted by date, then each action runs at its simulated time |
| `reset_all --yes` | deletes and recreates both databases (or the SQLite files), then runs `migrate` |
| `run_projector` | starts the projector loop (`--once` to catch up and stop) |
| `rebuild_projections` | wipes the read model and rebuilds it from the events, and prints the time |
| `verify_ledger` | runs the 44 checks, prints PASS / FAIL, exits with an error code if one fails (used by CI). `--catch-up` runs the projector first, `--json` saves a report |
| `benchmark` | measures real performance in a separate store (B1 rebuild with and without snapshot, B2 write speed, B3 query speed, B4 lag with `--lag`), saves `benchmarks/results/benchmark_<mode>.json` and draws the charts. `--charts-only` redraws the charts from the saved JSON without measuring |

### `readmodel/migrations/0001_initial.py`
Generated by Django from `models.py` (`python manage.py makemigrations`). `migrate` runs it to create the tables.
Never edited by hand.

---

## Part 6. `ai_audit/`: the AI audit assistant and its checker

The idea: an AI writes an audit report of one account, and a strict program checks every sentence of it
against the event store. If the AI is wrong, its answer is thrown away.

### `ai_audit/schemas.py`
The exact JSON shape the AI must answer with (Pydantic): a `headline`, 1 to 12 `findings`, and the
`closing_balance_tnd`. Each finding has a `category` (from a fixed list), a `statement`, and 1 to 15
`citations` (exact event id + exact timestamp). `CATEGORY_EVENT_TYPES` says which event types each category
may cite (a "fee" finding may only cite fee events).

### `ai_audit/evidence.py`
Builds the **evidence pack**: the only information the AI receives.
* `build_evidence()` replays the account's events, keeps those inside the chosen time window, and for each event
  writes its id, timestamp, type, category, amount, balance after and description. It also computes the
  `facts`: opening and closing balance, totals and counts per kind, and the materiality threshold.
* `Evidence.as_prompt_dict()`: the version sent to the AI (without internal fields). `by_id` lets the verifier
  find an event by its id instantly.

### `ai_audit/providers.py`
Talks to the AI.
* `OllamaProvider`: a local AI on this computer (`http://127.0.0.1:11434`). `available()` checks Ollama is running
  and the model is downloaded. `generate()` sends the instructions and the evidence, temperature 0 (no creativity),
  and forces the JSON shape of `schemas.py`.
* `GeminiProvider`: Google's cloud AI, used only if `GEMINI_API_KEY` is filled in `.env`.
* `select_provider()`: picks the first available one according to `AI_PROVIDER` (`auto`, `ollama`, `gemini` or
  `deterministic`). If none is available, it returns `None` and the reason.
* `describe_provider()`: the provider name shown in the top bar.

### `ai_audit/verifier.py`
**The 7 deterministic checks.** "Deterministic" means: same input, same result, no AI involved.
1. **JSON schema**: the answer must parse and match `schemas.py`.
2. **References exist**: every cited event id, and every EVT/HLD/FEE/TRF reference written in the text, must exist.
3. **Timestamps and dates exact**: each citation's timestamp must equal the real one; every date or time written
   in the text must be the date or time of a real event.
4. **Category matches**: a finding may only cite event types allowed for its category.
5. **Every number grounded**: every number written must be an amount, balance, total or count from the evidence,
   or a sum of the cited events.
6. **Closing balance**: must equal the balance computed by replaying the events.
7. **Material events covered**: every fee reversal and every transfer or hold of at least 10,000 TND must be cited.

How it reads the text: regular expressions (`REF_RE`, `ISO_RE`, `DAY_MONTH_RE`, `NUM_RE` ...) are search patterns
that find references, dates, times and numbers inside a sentence. `_Context` collects once everything that is
allowed (known references, real dates and times, real amounts). `_check_text()` looks at one sentence and lists
every problem. `verify()` runs the 7 checks and returns a `Verification` with the details. `feedback()` turns the
failures into text sent back to the AI for its second attempt. `material_events()` lists the events that must be cited.

### `ai_audit/assistant.py`
Runs one audit from start to finish.
* `SYSTEM_PROMPT`: the instructions given to the AI (rules it must follow, answer format).
* `run_audit()`: builds the evidence; refuses an empty window (`EmptyWindow`); asks the AI; verifies the answer.
  If it fails, it sends the reasons back and asks again (`AI_MAX_ATTEMPTS`, 2 by default). If it still fails, or
  if no AI is available, it uses `deterministic_report()`. Every attempt is stored, so the page can show what the
  AI said and why it was rejected. Final status: VERIFIED (AI passed), FALLBACK (template used) or REJECTED.
* `deterministic_report()`: writes the report with a fixed template from the evidence (opening balance, deposits,
  withdrawals, transfers, fees, escrow holds, material movements, closing balance). It goes through the same
  verifier, and the tests prove it always passes.

### `ai_audit/models.py`
One table, `AuditRun`: every audit with its status, report, verification result, all attempts, the evidence and
the time it took. It is shown under "Previous audit runs".

---

## Part 7. `web/`: the pages

### `web/views.py`
One function per page or action. Helper functions first:
* `_pydantic_errors()`: turns Pydantic errors into one readable line.
* `_wait_for_projection()`: after a command, waits (max 2.5 s) until the read model shows the new version and
  returns how many milliseconds it took. That is the "read model updated 194 ms after the write" message.
* `_after_command()`: shows the green "Event appended" messages and the eventual consistency message.
* `_parse_dt()`, `_int()`: read a date or a number from the address safely (bad input never crashes the page).

Pages (all read **only** from the read model, except where noted):

| View | Address | What it does |
|---|---|---|
| `dashboard` | `/` | totals, CQRS strip, accounts table, daily chart, open-account form |
| `recent_events` | `/fragments/recent/` | the live event list; HTMX reloads it every 2 seconds |
| `account_detail` | `/accounts/<id>/` | balances, statement, holds, and the command forms |
| `open_account` | POST `/accounts/open/` | checks the form with `OpenAccount`, opens the account |
| `account_command` | POST `/accounts/<id>/command/<name>/` | the 8 command forms. `COMMANDS` maps each name to its Pydantic class and its handler. Shape errors and refused commands become red messages |
| `race_demo` | POST `/accounts/<id>/race-demo/` | runs `demonstrate_race()` and shows SAVED / REJECTED |
| `replay` | `/accounts/<id>/replay/` | step-by-step replay (`?step=`) and time travel (`?at=`). Reads the **event store** on purpose |
| `balance_at_api` | `/api/accounts/<id>/balance-at/?at=` | the same time travel as JSON |
| `event_store` | `/events/` | the raw rows of the event store, 25 per page, with the "schema v1" badge on legacy rows |
| `audit` | `/accounts/<id>/audit/` | the audit form and the previous runs; POST starts `run_audit()` |
| `audit_run` | `/audit/<n>/` | one audit: banner, findings with clickable citations, 7 checks, attempt log |
| `system` | `/system/` | settings, projector health, AI provider; buttons "Run verification gate" and "Wipe & rebuild" |
| `alerts` | `/alerts/` | the webhook outbox and inbox |
| `webhook_receive` | POST `/webhooks/receive/` | our own test receiver: checks the HMAC signature and stores the alert |
| `health` | `/health/` | a small JSON status used by Docker to know the app is up |

`@require_POST` refuses anything but a form submission. `@csrf_exempt` on the webhook receiver is needed because
the alert comes from a program, not from our form; its protection is the HMAC signature instead.

### `web/templates/web/`
The HTML pages. Django template language: `{{ value }}` prints a value, `{% if %}`, `{% for %}` are conditions and
loops, `{% url 'name' %}` builds an address, `{% csrf_token %}` adds the anti-forgery code to each form.
* `base.html`: the frame shared by every page: green header with the seal, menu, status pills (store mode,
  projection lag, AI provider), messages, footer. Other pages start with `{% extends "web/base.html" %}` and
  fill `{% block content %}`.
* `dashboard.html`, `account.html`, `replay.html`, `events.html`, `alerts.html`, `audit.html`, `audit_run.html`,
  `system.html`: one per page.
* `_recent.html` and `_replay_state.html` (names starting with `_`): small pieces that HTMX loads into a page
  without reloading it (`hx-get` = address to load, `hx-target` = where to put it, `hx-trigger="every 2s"` = when).
* Every command form contains a hidden `expected_version` field: this is how optimistic concurrency reaches the browser.

### `web/templatetags/ledger_extras.py`
Filters usable in the HTML: `tnd` (millimes to `1,234.500`), `tojson` (pretty JSON), `short` (first 8
characters of an id), `get` (read a value from a dictionary by key).

### `web/context_processors.py`
`system_status()` runs for every page and gives the header its values: projection status, AI provider (checked at
most every 30 seconds, because asking Ollama takes time) and database mode.

### `web/static/`
* `css/app.css`: the whole look of the site (colours at the top as variables: forest green, brass, paper).
* `vendor/htmx.min.js`, `vendor/chart.umd.min.js`: the two JavaScript libraries, stored locally so the demo
  works without internet.

---

## Part 8. `tests/`: 59 automatic tests

Run them with `RUN_TESTS.bat` or `python -m pytest -v`. Each test is a function whose name says what it proves.
* `conftest.py`: shared ingredients ("fixtures"). `app` is a bank in memory (`eventsourcing.popo`, very fast);
  `ledger_app` plugs it into the website; `two_accounts` opens Alice and Bob.
* `test_architecture.py` (1): reads every file of `banking/` and fails if one imports Django.
* `test_domain.py` (11, one test runs with several amounts): rules of the account (refused amounts create no event, cannot withdraw more than available,
  escrow blocks money, a fee can be reversed only once, the state is a left-fold, event id format).
* `test_application.py` (10): transfers are atomic, a failed transfer writes nothing, version conflict, the race
  (exactly one writer wins), snapshots every 5 events give the same state, time travel, replay, upcasting,
  reproducible ids, escrow settlement.
* `test_projector.py` (5): eventual consistency, the projector resumes from its bookmark without double counting,
  rebuild gives identical tables, alerts are written once, the gate detects tampering.
* `test_web.py` (9): every page opens, a deposit through the page appends an event, bad input is refused, a stale
  version is refused, the time travel API, bad addresses never crash, empty audit window, HMAC check, open account.
* `test_ai_verifier.py` (23, some tests run several cases): the verifier rejects invented ids, wrong timestamps, invented amounts, wrong closing
  balance, hidden material events, wrong categories, free text, invented numbers, dates and references; it accepts
  real ones; the fallback always passes; the retry loop works; an AI that always invents ends in fallback.

---

## Part 9. Files around the code

| File | What it is |
|---|---|
| `manage.py` | Django's remote control. `python manage.py <command>` |
| `requirements.txt` | the exact library versions to install (`pip install -r requirements.txt`) |
| `.env.example` | the settings template. `SETUP.bat` copies it to `.env` (your private settings, never shared) |
| `pytest.ini` | tells pytest which settings to use and where the tests are |
| `.gitignore` | files Git must not save: `.env`, `.venv/`, `var/` (the database), caches |
| `.dockerignore` | files Docker must not copy into the image |
| `SETUP.bat` -> `scripts/setup.ps1` | finds or installs Python 3.12, creates `.venv`, installs libraries, creates `.env`, creates tables, loads the demo, runs the gate |
| `START.bat` -> `scripts/start.ps1` | creates missing tables, opens the projector window, starts the web server, opens the browser |
| `RESET_DEMO.bat` | `seed_demo --reset`: back to the 98 demo events |
| `RUN_TESTS.bat` | pytest + verification gate |
| `SETUP_OLLAMA.bat` -> `scripts/setup_ollama.ps1` | installs Ollama and downloads the AI model (16 GB PC only) |
| `START_DOCKER.bat` | `docker compose up` and opens the browser |
| `Dockerfile` | recipe of the application container (Python 3.12, the libraries, the static files) |
| `docker-compose.yml` | three containers: `db` (PostgreSQL 16), `web` (migrate, seed, Gunicorn), `projector` |
| `.github/workflows/ci.yml` | on every push GitHub runs: tests, seed, rebuild, gate, quick benchmark, on PostgreSQL and on SQLite |
| `scripts/take_screenshots.py` | opens the site in an invisible browser (Playwright), takes the 17 screenshots and writes `observed_facts.json` |
| `benchmarks/results/` | the measured benchmark JSON and its two charts |
| `docs/` | this guide, the beginner guide, the defense questions, the video script, the architecture diagrams, screenshots |
| `var/` | created at run time: the embedded database and static files. Not shared, recreated automatically |
| `.venv/` | created by SETUP.bat: the private Python with all libraries. Not shared |

---

## Part 10. One deposit, function by function

You type 50 in the deposit slip and click "Deposit" on Yasmine's account (version 12):

1. The browser POSTs the form to `/accounts/<id>/command/deposit/`. `config/urls.py` sends it to `views.account_command`.
2. `account_command` finds `"deposit"` in `COMMANDS`, builds `commands.Deposit(amount="50", channel=..., expected_version=12)`.
   Pydantic checks the format.
3. The handler calls `BankingApplication.deposit()`, which calls `_execute()`.
4. `_execute` loads the account with `get_account()` (snapshot v10 + events v11, v12), sees version 12 = expected.
5. It calls `BankAccount.deposit(50000)`: the rule "amount > 0" passes, so `_on_deposited()` runs; the `@event`
   decorator creates `FundsDeposited` version 13 and the balance goes up by 50,000 millimes.
6. `self.save(account)` appends the event to `bankingledger_events` in PostgreSQL (global position #99).
7. Back in the view, `_after_command()` shows "Event appended: EVT-703c6537-0013" and starts waiting.
8. Meanwhile, in the projector window, `run_forever()` calls `run_once()`, which finds position #99, calls
   `apply()` (AccountSummary +50, new LedgerEntry, DailyBankStats) and moves the bookmark to 99, all in one transaction.
9. `_wait_for_projection()` sees `last_version = 13` and reports the delay (about 200 ms).
10. The page reloads `/accounts/<id>/` and `account_detail` shows the new balance from the read model.
