"""python manage.py benchmark [--lag] [--quick]

REAL measurements on YOUR machine (nothing is invented - "zero falsification").
Results go to benchmarks/results/benchmark_<db mode>.json and PNG charts.

B1  Aggregate reconstruction time vs stream length (150 / 550 / 1050 / 2550):
    full left-fold replay  vs  snapshot (every 100 events) + replay of the 50 delta events
B2  Write throughput: events appended per second (optimistic concurrency on)
B3  Query latency on the demo bank: read model SELECT vs replaying the streams
B4  (--lag) Eventual-consistency lag: time between the write and the moment
    the asynchronous projector makes it visible in the read model.
    NOTE: B4 adds a "Benchmark probe" account to the demo data; run
    `python manage.py seed_demo --reset` afterwards for a clean demo.

B1/B2 use a SEPARATE event store (tables benchledger_*), so the demo data
is never touched by them.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from banking.application import BankingApplication
from banking.money import to_millimes as m
from config.ledger import get_app
from readmodel.models import AccountSummary, LedgerEntry
from readmodel.projector import LedgerProjector

OUT = Path(settings.BASE_DIR) / "benchmarks" / "results"


class BenchLedger(BankingApplication):
    name = "BenchLedger"  # -> tables benchledger_events / benchledger_snapshots


def _ms(samples: list[float]) -> dict:
    s = sorted(samples)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    return {"median_ms": round(statistics.median(s), 3), "p95_ms": round(p95, 3),
            "min_ms": round(s[0], 3), "runs": len(s)}


def _timeit(fn, runs: int) -> dict:
    fn()  # warm-up run (connection pool, caches) - not counted
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return _ms(samples)


class Command(BaseCommand):
    help = "Run real performance benchmarks of the CQRS / Event Sourcing ledger."

    def add_arguments(self, parser):
        parser.add_argument("--lag", action="store_true", help="Also measure projection lag (adds a probe account).")
        parser.add_argument("--quick", action="store_true", help="Smaller sizes (for CI).")

    def _bench_env(self) -> dict:
        env = dict(settings.EVENTSTORE_ENV)
        env["SNAPSHOT_INTERVAL"] = "100"
        if env["PERSISTENCE_MODULE"].endswith("sqlite"):
            path = Path(env["SQLITE_DBNAME"]).with_name("benchmark.sqlite3")
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(path) + suffix)
                if p.exists():
                    p.unlink()
            env["SQLITE_DBNAME"] = str(path)
        else:
            import psycopg

            with psycopg.connect(host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"], user=env["POSTGRES_USER"],
                                 password=env["POSTGRES_PASSWORD"], dbname=env["POSTGRES_DBNAME"],
                                 autocommit=True) as conn:
                conn.execute("DROP TABLE IF EXISTS benchledger_events, benchledger_snapshots")
        return env

    def handle(self, *args, **opts):
        OUT.mkdir(parents=True, exist_ok=True)
        # lengths are NOT multiples of 100, so every rebuild = snapshot + 50 delta events (realistic case)
        sizes = [150, 250, 550] if opts["quick"] else [150, 550, 1050, 2550]
        runs = 5 if opts["quick"] else 15
        results: dict = {
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "machine": {"os": f"{platform.system()} {platform.release()}", "cpu": platform.processor(),
                        "cpu_count": os.cpu_count(), "python": platform.python_version()},
            "db_mode": settings.DB_MODE, "event_store": settings.EVENTSTORE_ENV["PERSISTENCE_MODULE"],
        }

        # ---------------- B1 + B2 --------------------------------------
        self.stdout.write("B1/B2: building streams in a separate benchmark event store...")
        bench = BenchLedger(env=self._bench_env())
        b1, b2 = [], []
        for size in sizes:
            acc = bench.open_account(f"Bench {size}", initial_deposit=m("1")).account_id
            t0 = time.perf_counter()
            for i in range(size - 2):
                bench.deposit(acc, m("1"), f"bench {i}")
            elapsed = time.perf_counter() - t0
            b2.append({"events": size - 2, "seconds": round(elapsed, 3),
                       "events_per_second": round((size - 2) / elapsed, 1)})
            full = _timeit(lambda: bench.rebuild_without_snapshot(acc), runs)
            snap = _timeit(lambda: bench.get_account(acc), runs)
            length = bench.get_account(acc).version
            b1.append({"stream_length": length, "delta_events_after_snapshot": length % 100, "full_replay": full,
                       "snapshot_plus_delta": snap,
                       "speedup": round(full["median_ms"] / max(snap["median_ms"], 1e-6), 1)})
            self.stdout.write(f"  stream {size:>5}: full replay {full['median_ms']:>8.2f} ms | "
                              f"snapshot+delta {snap['median_ms']:>7.2f} ms | write {b2[-1]['events_per_second']} ev/s")
        bench.close()
        results["B1_reconstruction"] = b1
        results["B2_write_throughput"] = b2

        # ---------------- B3 -------------------------------------------
        app = get_app()
        ids = app.iter_account_ids()
        if ids:
            self.stdout.write(f"B3: query latency on the demo bank ({len(ids)} accounts)...")
            def read_model():
                list(AccountSummary.objects.values_list("owner_name", "ledger_balance", "available_balance"))
            def replay_snap():
                [app.get_account(i).balance for i in ids]
            def replay_full():
                [app.rebuild_without_snapshot(i).balance for i in ids]
            def statement_read():
                list(LedgerEntry.objects.filter(account_id=ids[0])[:50].values())
            results["B3_queries"] = {
                "accounts": len(ids), "events": app.recorder.max_notification_id(),
                "read_model_account_list": _timeit(read_model, runs * 2),
                "event_store_replay_with_snapshots": _timeit(replay_snap, runs),
                "event_store_full_replay": _timeit(replay_full, runs),
                "read_model_statement_50_lines": _timeit(statement_read, runs * 2),
            }
            for k, v in results["B3_queries"].items():
                if isinstance(v, dict):
                    self.stdout.write(f"  {k:<36} median {v['median_ms']:>8.3f} ms  p95 {v['p95_ms']:>8.3f} ms")

        # ---------------- B4 -------------------------------------------
        if opts["lag"]:
            self.stdout.write("B4: eventual-consistency lag with the projector running in background...")
            stop = threading.Event()
            projector = LedgerProjector(app)

            def loop():
                from django.db import connection
                while not stop.is_set():
                    if projector.run_once() == 0:
                        time.sleep(settings.PROJECTOR_POLL_SECONDS)
                connection.close()

            th = threading.Thread(target=loop, daemon=True)
            th.start()
            probe = app.open_account("Benchmark probe (lag test)", "CURRENT", initial_deposit=m("1")).account_id
            lags = []
            for _ in range(10 if opts["quick"] else 40):
                r = app.deposit(probe, m("1"), "lag probe")
                v = r.events[0].version
                t0 = time.perf_counter()
                while not AccountSummary.objects.filter(account_id=probe, last_version__gte=v).exists():
                    time.sleep(0.002)
                lags.append((time.perf_counter() - t0) * 1000)
            stop.set()
            th.join(timeout=5)
            results["B4_projection_lag"] = {"poll_interval_s": settings.PROJECTOR_POLL_SECONDS, **_ms(lags)}
            self.stdout.write(f"  lag median {results['B4_projection_lag']['median_ms']} ms, "
                              f"p95 {results['B4_projection_lag']['p95_ms']} ms")

        path = OUT / f"benchmark_{settings.DB_MODE}.json"
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        self._charts(results)
        self.stdout.write(self.style.SUCCESS(f"Saved {path}"))

    def _charts(self, r: dict) -> None:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return
        b1 = r["B1_reconstruction"]
        x = [row["stream_length"] for row in b1]
        fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=200)
        ax.plot(x, [row["full_replay"]["median_ms"] for row in b1], "o-", color="#c2410c", lw=2.2,
                label="Full left-fold replay (all events)")
        ax.plot(x, [row["snapshot_plus_delta"]["median_ms"] for row in b1], "s-", color="#0f9d8a", lw=2.2,
                label="Snapshot (every 100) + replay of the 50 delta events")
        ax.set_xlabel("Events in the account stream")
        ax.set_ylabel("Rebuild time - median (ms)")
        ax.set_title(f"Aggregate reconstruction ({r['event_store'].split('.')[-1]}, measured {r['measured_at'][:10]})")
        ax.grid(alpha=.3)
        ax.legend()
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        fig.savefig(OUT / f"reconstruction_{r['db_mode']}.png")
        plt.close(fig)
        if "B3_queries" in r:
            q = r["B3_queries"]
            labels = ["Read model\nSELECT", "Replay with\nsnapshots", "Full replay\n(no snapshot)"]
            vals = [q["read_model_account_list"]["median_ms"], q["event_store_replay_with_snapshots"]["median_ms"],
                    q["event_store_full_replay"]["median_ms"]]
            fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=200)
            bars = ax.bar(labels, vals, color=["#1d4ed8", "#0f9d8a", "#c2410c"])
            for b, v in zip(bars, vals):
                ax.annotate(f"{v:.2f} ms", (b.get_x() + b.get_width() / 2, v), ha="center", va="bottom", fontsize=9)
            ax.set_ylabel("Median latency (ms)")
            ax.set_title(f"'List all balances' query - {q['accounts']} accounts / {q['events']} events")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            fig.tight_layout()
            fig.savefig(OUT / f"queries_{r['db_mode']}.png")
            plt.close(fig)
