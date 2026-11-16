# Demo video script (target 4 min 30 s)

Record on the RTX PC with **Windows + Alt + R** (Xbox Game Bar), **OBS Studio** or **Clipchamp**, at 1920×1080.
Before recording, run `RESET_DEMO.bat` (clean data) and then `START.bat` (or `START_DOCKER.bat`), and close every other window.
**Run one AI audit of Yasmine *before* you start recording.** A local model can take from a few seconds (GPU) to several
minutes (CPU). During the video, start a new audit only if it is fast. Otherwise open the run you prepared from
the "Previous audit runs" list. Do **not** run `benchmark --lag` before recording: it adds a test account.
The three members share the narration (**A** = Arij, **I** = Israa, **S** = Skander).

| Time | Screen | Action | Narration (speak slowly) |
|---|---|---|---|
| 0:00 | Title slide | (none) | **A:** "Hello. We are group 6 of BI1. This is project 14: CQRS and Event Sourcing in Python and Django, applied to a core banking and escrow ledger." |
| 0:15 | Dashboard | point at the CQRS strip | **A:** "Every screen you see here is read from the read model: denormalized tables built by a projector from an append-only event store. 98 immutable events, 6 fictional customers." |
| 0:40 | Dashboard | point at the live event stream | **A:** "This stream refreshes every two seconds with HTMX. The projection lag counter shows how far the read side is behind the write side." |
| 0:55 | Yasmine's account | scroll the statement | **I:** "Yasmine is buying an apartment. Her 85,000 dinars were blocked in escrow, then paid to the seller. Each line is one immutable event, with its exact ID." |
| 1:15 | Deposit form | deposit 50 TND, MOBILE | **I:** "A command goes to the write model. Pydantic validates the input, the aggregate checks the rules, and the event is appended. The read model was updated *X* milliseconds later. That is eventual consistency." |
| 1:45 | Race button | click | **I:** "Two clerks load the same version at the same time. Only one save succeeds. The unique key on stream and version rejects the other. That is optimistic concurrency." |
| 2:05 | Replay | click v5, v6, v10 | **S:** "The temporal replay rebuilds the state one event at a time: a left fold. The gold badges are snapshots, stored every five events." |
| 2:30 | Time travel | 2026-10-20 12:00 | **S:** "What was the balance on 20 October at noon? 99,487 dinars, with 85,000 in escrow. The bar shows it was rebuilt from snapshot 5 plus only 3 delta events." |
| 2:55 | Event Store page | last page, legacy badge | **S:** "This is the raw append-only log. This amber row was written by the old software with schema version 1. The upcaster converts it on read, without ever changing the stored bytes." |
| 3:15 | Alerts | (none) | **A:** "Native event webhooks: the projector detects a large transfer, a fee reversal and a low balance, then sends HMAC-signed alerts with retries." |
| 3:35 | AI audit | run it | **I:** "The AI audit assistant summarizes the account and cites exact event IDs and timestamps. Seven deterministic checks verify every citation and every amount. A small model that invents a date is rejected and retried; if it still fails, a deterministic report is used." |
| 4:05 | System page | Run gate, then Rebuild | **S:** "The verification gate proves the event store, snapshots and read model agree: 44 out of 44. We even wipe the read model and rebuild it from the events in under a second." |
| 4:25 | Dashboard | (none) | **A:** "Events are the single source of truth. Thank you." |

Export to MP4 (H.264) under 200 MB and name it `BI1_6_14_demo.mp4`.
