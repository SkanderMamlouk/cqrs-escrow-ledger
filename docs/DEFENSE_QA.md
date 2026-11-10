# Preparing the live Q&A defense: likely questions and short answers

**Q1. What is the difference between a command and an event?**
A command is a *request* in the imperative ("Deposit 50 TND"). It can be refused, for example for insufficient funds.
An event is a *fact* in the past tense ("FundsDeposited"). It already happened, it is immutable, and it is never deleted.

**Q2. Why "left-fold"?**
The state is computed by starting from nothing and applying the events from left to right (oldest to newest):
`state = fold(apply, events, initial=None)`. See `replay_steps()` in `banking/application.py`.

**Q3. How does optimistic concurrency work in your code?**
Every event has a version. The event store table has a UNIQUE (originator_id, originator_version) key.
When two writers load version 13 and both try to insert version 14 (our "Concurrency race demo"), the database rejects the second insert
(`IntegrityError`). On top of that, the web forms send the version the user saw (`expected_version`).
If the account has moved on, we raise `ConcurrencyConflict` before even trying. Nothing is locked while the user reads, hence "optimistic".

**Q4. Why are snapshots needed? Don't they break immutability?**
Replaying 2,550 events took 65.2 ms in our measurement; starting from the snapshot at v2,500 and replaying the 50 delta
events took 1.94 ms (≈ 34× faster). A snapshot is
only a cache of the state at version N, stored separately. Events are never changed, and the verification gate checks
that every snapshot equals the full replay.

**Q5. What is upcasting, and why not just migrate the old events?**
Events are immutable history, so we don't rewrite them. When the event class changes (we added `channel`
to `FundsDeposited`), we set `class_version = 2` and write `upcast_v1_v2()`, which fills the missing field when an
old event is read. The demo contains a real v1 row (Karim's legacy deposit).

**Q6. What is eventual consistency? Isn't it dangerous for a bank?**
The read model is updated asynchronously, about 204 ms (median, p95 207 ms) after the write in our benchmark. Business
decisions (enough funds? right version?) are always taken on the **write side**, which is strongly consistent, because the
aggregate is rebuilt from the event store. Only the screens can be a fraction of a second behind, and the UI waits
for its own writes ("read your writes").

**Q7. How do you guarantee that the projector never loses or duplicates an event?**
The checkpoint (last processed position) is saved in the *same database transaction* as the read rows.
After a crash, the transaction is rolled back and the projector restarts from the last committed checkpoint.
The test `test_projector_is_idempotent_and_resumes_from_checkpoint` proves it.

**Q8. Why two databases?**
CQRS separates the models. The event store (`ledger_events`) is optimized for appending, and the read DB (`ledger_read`)
for querying. They can be scaled, indexed and even replaced independently. We can wipe the read DB and rebuild it.

**Q9. How does a transfer stay atomic across two accounts?**
`app.save(payer, payee)` writes both accounts' new events in a single database transaction. Either both
`TransferSettled` events exist or neither does. The gate also checks money conservation (total OUT == total IN).

**Q10. How does the AI avoid hallucinations?**
It doesn't by itself, which is why it never has the last word. It only receives an evidence pack computed from the
event store, and it must answer in a strict Pydantic JSON schema. Seven deterministic checks verify the event IDs,
timestamps, categories, amounts, closing balance and coverage. When it fails, the reasons are sent back for a retry, and
after that we fall back to a deterministic template. We observed it live: `qwen2.5:1.5b` invented amounts and was rejected.

**Q11. What are the drawbacks of Event Sourcing?**
More complexity (two models, projector, versioning), eventual consistency on the read side, events that are hard to
change (upcasting), GDPR "right to be forgotten" (it needs crypto-shredding or encrypted fields), and
a learning curve. It is worth it when auditability and history matter, as in banking, insurance or logistics.

**Q12. Why the `eventsourcing` library and not your own event store?**
It is a mature library (John Bywater, v9.5) that provides aggregates, the `@event` decorator, a PostgreSQL recorder with
an ordered notification log, snapshots and upcasting. We focused on the business domain, the projections and the verification.

**Q13. Where are the numbers in your slides from?**
From `python manage.py benchmark --lag` (JSON in `benchmarks/results/`), measured on our team PC (Intel Core i5-13450HX, 16 GB RAM) on 10 Nov 2026.
Anyone can re-run it. Nothing is estimated or invented.

**Q14. How do you deploy it?**
`docker compose up --build` starts 3 containers (PostgreSQL 16, Django/gunicorn, projector). Without Docker,
`SETUP.bat` and `START.bat` use an embedded PostgreSQL (this is the mode used for our screenshots). The GitHub Actions workflow
(`.github/workflows/ci.yml`) runs the 59 tests and the verification gate on PostgreSQL and SQLite at every push to the repository.
