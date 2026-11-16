from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--skip-ai", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        def shot(name: str, full: bool = True) -> None:
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        for _ in range(90):
            page.goto(base + "/alerts/")
            if "HMAC valid" in page.content() and "PENDING" not in page.content():
                break
            page.wait_for_timeout(2000)

        page.goto(base + "/")
        page.wait_for_selector("text=Live event stream")
        shot("01_dashboard")

        def account_url(owner: str) -> str:
            href = page.locator(f"a:has-text('{owner}')").first.get_attribute("href")
            return base + href

        yasmine = account_url("Yasmine Trabelsi")
        cafe = account_url("Cafe El Medina")
        karim = account_url("Karim Ben Salah")

        page.goto(yasmine)
        shot("02_account_before_command")
        page.goto(yasmine.rstrip("/") + "/replay/?step=6")
        page.wait_for_selector("text=State after")
        shot("06_replay_escrow_hold_step")
        page.goto(yasmine.rstrip("/") + "/replay/?at=2026-10-20T12:00")
        page.wait_for_selector("text=How it was rebuilt")
        shot("07_time_travel_snapshot_plus_delta", full=False)
        page.goto(cafe.rstrip("/") + "/replay/?step=23")
        page.wait_for_selector("text=State after")
        shot("08_replay_long_stream_snapshots")
        page.goto(yasmine.rstrip("/").replace("/accounts/", "/api/accounts/") + "/balance-at/?at=2026-10-20T12:00")
        shot("09_time_travel_json_api", full=False)
        page.goto(base + "/events/")
        pages = int(re.search(r"page 1 / (\d+)", page.content()).group(1))
        page.goto(base + f"/events/?page={pages}")
        shot("10_event_store_raw_rows_upcast_v1")
        page.goto(karim)
        shot("11_account_karim_legacy_upcast")

        if not args.skip_ai:
            page.goto(yasmine.rstrip("/") + "/audit/")
            shot("15_ai_audit_form", full=False)
            page.set_default_timeout(15 * 60 * 1000)
            page.locator("button:has-text('Generate verified audit')").click()
            page.wait_for_selector("text=Verification gate")
            shot("16_ai_audit_report_verification")
            page.set_default_timeout(30_000)

        page.goto(yasmine)
        form = page.locator("form[action$='/command/deposit/']")
        form.locator("input[name=amount]").fill("50")
        form.locator("select[name=channel]").select_option("MOBILE")
        form.locator("input[name=reference]").fill("Live demo deposit")
        form.locator("button").click()
        page.wait_for_selector("text=Eventual consistency")
        shot("03_command_appended_event_and_lag", full=False)
        info = page.locator(".messages").inner_text()
        lag = re.search(r"updated (\d+) ms after the write", info)
        evt = re.search(r"Event appended: (EVT-[0-9a-f]{8}-\d{4}) - FundsDeposited \(v(\d+), global position #(\d+)\)", info)
        facts = {"command_lag_ms": int(lag.group(1)) if lag else None,
                 "command_event_id": evt.group(1) if evt else None,
                 "command_version": int(evt.group(2)) if evt else None,
                 "command_position": int(evt.group(3)) if evt else None}

        page.locator("button:has-text('Concurrency race demo')").click()
        page.wait_for_selector("text=Optimistic concurrency demo")
        shot("04_optimistic_concurrency_race", full=False)

        page.goto(yasmine)
        stale = page.locator("form[action$='/command/withdraw/']")
        stale.locator("input[name=expected_version]").evaluate("el => el.value = '3'")
        stale.locator("input[name=amount]").fill("10")
        stale.locator("button").click()
        page.wait_for_selector("text=Concurrency conflict")
        shot("05_stale_version_conflict", full=False)

        time.sleep(2)
        page.goto(base + "/alerts/")
        shot("12_webhook_alerts_outbox_inbox")

        page.goto(base + "/system/")
        page.locator("button:has-text('Run verification gate now')").click()
        page.wait_for_selector("text=PASSED")
        shot("13_verification_gate_passed")
        page.once("dialog", lambda d: d.accept())
        page.locator("button:has-text('Wipe')").click()
        page.wait_for_selector("text=rebuilt from")
        shot("14_read_model_rebuilt_from_events", full=False)
        rebuilt = re.search(r"rebuilt from (\d+) events in (\d+) ms", page.locator(".messages").inner_text())
        facts["rebuild_events"] = int(rebuilt.group(1)) if rebuilt else None
        facts["rebuild_ms"] = int(rebuilt.group(2)) if rebuilt else None

        page.goto(base + "/")
        shot("17_dashboard_after_demo")
        browser.close()
        import json

        (OUT / "observed_facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
        print("observed facts:", facts)


if __name__ == "__main__":
    main()

