# CLAUDE.md - cqrs_ledger Repository Briefing

This repository (`cqrs_ledger`) is the core implementation of **DS1 Project 14: CQRS & Event Sourcing in Python and Django** (ISG Tunis, BI1 Group 6: Arij Ben Brahim, Israa Keysi, Skander Mamlouk; Supervisor: Mr. Chaouki Bayoudhi).

> **For the full multi-PC handoff and presentation details, see `../CLAUDE.md` in the parent directory.**

---

## 1. Quick CLI Commands

Run these inside `cqrs_ledger\` using the virtual environment:

```powershell
# Run the full test suite (59 tests must pass):
.venv\Scripts\python.exe -m pytest -v

# Run the 44-check deterministic verification gate:
.venv\Scripts\python.exe manage.py verify_ledger --catch-up

# Reset the database to clean 6 accounts / 98 events:
.venv\Scripts\python.exe manage.py seed_demo --reset

# Start the web server and projector (opens http://127.0.0.1:8000):
.\START.bat
# Note: Always press Ctrl+C in the web window first before closing the projector!

# Run benchmarks (updates benchmark_embedded.json):
.venv\Scripts\python.exe manage.py benchmark --lag
# ALWAYS run seed_demo --reset immediately after benchmarks!
```

---

## 2. Architecture & Design Rules

* **`banking/` (Write Model):** Must remain pure Python with **zero** Django imports. All state changes are driven by immutable events. Arithmetic is strictly integer millimes (`banking/money.py`).
* **`readmodel/` (Read Model):** Denormalized SQL tables updated by `LedgerProjector` from the PostgreSQL notification log. Checkpoints and read tables are updated atomically.
* **`ai_audit/` (AI Assistant & Verifier):** Answers generated via Ollama or Gemini are strictly audited by `ai_audit/verifier.py` (7 deterministic checks). Never weaken these checks.
* **Hardware Profile:**
  * On the 16 GB RTX PC: Local Ollama `qwen2.5:7b` is supported.
  * On the 5.7 GB Dev PC: Do NOT run Ollama. Use `GEMINI_API_KEY` in `.env` (free cloud AI) or rely on the deterministic fallback.
* **Database Mode:** Default is `DB_MODE=embedded` (PostgreSQL 16.2 via `pgserver`). Docker Compose is also provided (`docker-compose.yml`) but optional.

---

## 3. Project Status (as of 15 Nov 2026)

* **Code & Tests:** 59/59 pytest passing, 44/44 verification gate passing.
* **CI:** GitHub Actions runs #1 and #2 are green on `main`.
* **Benchmarks:** Re-measured on 13th Gen Intel Core i5-13450HX, 16 GB RAM (saved in `benchmarks/results/`).
* **Remaining Work:**
  * Step 7: Record demo video (`video/BI1_6_14_demo.mp4`) following `docs/DEMO_VIDEO_SCRIPT.md`.
  * Step 8: Run `..\MAKE_SUBMISSION_ZIP.ps1` to build `BI1_6_14.zip` and submit to `homework.isg@gmail.com` before **Tuesday 17 Nov 2026, 23:59**.
