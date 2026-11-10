"""python manage.py seed_demo [--reset]

Creates SYNTHETIC demo data (fictional people and companies) for the
Core Banking & Escrow Ledger.  Every operation goes through the real
command handlers, so every rule, event, snapshot and version check is
exercised exactly like in production.  Only the business clock is set to
past dates (1 Sep -> 9 Nov 2026) so that time-travel queries are
meaningful.  Events created later from the web site use the real time.

Story
-----
* Yasmine Trabelsi buys an apartment from Atlas Immobilier SARL.  Her
  85,000 TND are blocked in ESCROW (FundsReserved) on 3 Oct and paid to
  the seller (TransferSettled) on 28 Oct after the notary's approval.
* Cafe El Medina makes many small deposits -> long stream, many snapshots.
  A duplicated POS fee is charged by mistake, then reversed.
* Sahel Logistique pays salaries and a large warehouse rent.
* Karim Ben Salah (freelancer) has an old deposit imported from the
  previous banking software (schema v1 event -> upcasting demo).
* Nour Mansouri (student) spends until her balance is low -> alert.

All operations are put in a schedule, sorted by date, then executed in
chronological order (events of a stream must have increasing timestamps).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from django.core.management import call_command
from django.core.management.base import BaseCommand

from banking import clock, identity
from banking.money import to_millimes as m
from config.ledger import get_app
from readmodel.projector import LedgerProjector

TUNIS = timezone(timedelta(hours=1))  # Africa/Tunis is UTC+1 all year


def at(month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TUNIS).astimezone(timezone.utc)


class Command(BaseCommand):
    help = "Create the synthetic demo bank (fictional customers)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete everything first.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            call_command("reset_all", yes=True)
        app = get_app()
        if app.recorder.max_notification_id():
            self.stdout.write(self.style.WARNING("Event store already contains events - nothing seeded "
                                                 "(use --reset to start from zero)."))
            return
        rnd = random.Random(14)  # fixed seed -> same demo every time
        ids: dict[str, object] = {}  # account name -> account id
        refs: dict[str, str] = {}  # remembered hold / fee ids
        plan: list[tuple[datetime, int, object]] = []

        def do(when: datetime, action) -> None:
            plan.append((when, len(plan), action))

        def opened(key: str, owner: str, kind: str, initial: str):
            return lambda: ids.__setitem__(key, app.open_account(owner, kind, initial_deposit=m(initial)).account_id)

        # ---- 1-3 Sep: accounts are opened -------------------------------
        do(at(9, 1, 9, 5), opened("yasmine", "Yasmine Trabelsi", "CURRENT", "96500"))
        do(at(9, 1, 9, 20), opened("atlas", "Atlas Immobilier SARL", "CURRENT", "15000"))
        do(at(9, 1, 9, 40), opened("cafe", "Cafe El Medina", "CURRENT", "2000"))
        do(at(9, 1, 10, 0), opened("sahel", "Sahel Logistique SA", "CURRENT", "180000"))
        do(at(9, 2, 11, 0), opened("karim", "Karim Ben Salah", "SAVINGS", "3000"))
        do(at(9, 3, 14, 30), opened("nour", "Nour Mansouri", "CURRENT", "650"))

        # Legacy deposit written by the OLD software (schema v1, no "channel")
        do(at(9, 2, 11, 30), lambda: app.import_legacy_v1_deposit(
            ids["karim"], m("1250"), "Migrated balance from legacy core banking"))

        # ---- Cafe El Medina: daily takings, supplier payments -----------
        day = at(9, 2, 19, 0)
        while day < at(11, 9):
            amount, channel = m(str(rnd.randint(280, 1250))), rnd.choice(["BRANCH", "MOBILE", "ATM"])
            do(day, lambda a=amount, c=channel, d=day: app.deposit(ids["cafe"], a, f"Daily takings {d:%d %b}", c))
            if rnd.random() < 0.3:
                w = m(str(rnd.randint(150, 600)))
                do(day + timedelta(hours=14), lambda w=w: app.withdraw(ids["cafe"], w, "Supplier - coffee beans"))
            day += timedelta(days=rnd.choice([2, 3]))
        do(at(9, 20, 16), lambda: app.transfer(ids["cafe"], ids["sahel"], m("950"), "Delivery invoice INV-2026-0912"))
        do(at(10, 18, 16), lambda: app.transfer(ids["cafe"], ids["sahel"], m("1100"), "Delivery invoice INV-2026-1017"))

        # ---- A fee charged by mistake, then reversed ---------------------
        do(at(10, 5, 8, 30), lambda: refs.__setitem__(
            "wrong_fee", app.charge_fee(ids["cafe"], m("45"), "POS terminal fee (duplicate)").extra["fee_id"]))
        do(at(10, 7, 11, 15), lambda: app.reverse_fee(
            ids["cafe"], refs["wrong_fee"], "Duplicate POS fee - customer complaint #C-1182"))

        # ---- Salaries and rent ------------------------------------------
        for month in (9, 10):
            do(at(month, 25, 8, 0), lambda mo=month: app.transfer(ids["sahel"], ids["yasmine"], m("4200"), f"Salary {mo:02d}/2026"))
            do(at(month, 25, 8, 5), lambda mo=month: app.transfer(ids["sahel"], ids["nour"], m("450"), f"Internship allowance {mo:02d}/2026"))
        do(at(10, 2, 9, 0), lambda: app.transfer(ids["sahel"], ids["atlas"], m("25000"), "Warehouse rent Q4 - Rades port"))

        # ---- Karim: freelance income and spending ------------------------
        do(at(9, 15, 15), lambda: app.deposit(ids["karim"], m("2800"), "Client invoice F-221 (web design)", "WIRE"))
        do(at(9, 21, 18), lambda: app.withdraw(ids["karim"], m("400"), "ATM withdrawal"))
        do(at(10, 12, 15), lambda: app.deposit(ids["karim"], m("3400"), "Client invoice F-224", "WIRE"))
        do(at(10, 30, 18), lambda: app.withdraw(ids["karim"], m("650"), "ATM withdrawal"))

        # ---- Yasmine: the ESCROW story -----------------------------------
        do(at(10, 3, 10, 0), lambda: refs.__setitem__("notary", app.reserve_funds(
            ids["yasmine"], m("3500"), ids["atlas"], "Notary fees deposit - Maitre L. Gharbi").extra["hold_id"]))
        do(at(10, 3, 10, 10), lambda: refs.__setitem__("escrow", app.reserve_funds(
            ids["yasmine"], m("85000"), ids["atlas"], "ESCROW apartment purchase - Lac 2, lot B12").extra["hold_id"]))
        do(at(10, 9, 12, 0), lambda: app.withdraw(ids["yasmine"], m("1200"), "Furniture deposit"))
        do(at(10, 15, 9, 0), lambda: app.release_hold(ids["yasmine"], refs["notary"], "Notary paid by certified cheque instead"))
        do(at(10, 28, 14, 45), lambda: app.settle_escrow(ids["yasmine"], refs["escrow"], "Deed signed - notary approval N-7781"))
        do(at(11, 4, 10, 0), lambda: app.withdraw(ids["yasmine"], m("2500"), "Moving company"))

        # ---- Nour: spending until the balance is low ----------------------
        for mo, d, amt in ((9, 5, "120"), (9, 12, "180"), (9, 20, "150"), (9, 27, "90"),
                           (10, 3, "260"), (10, 14, "150"), (10, 22, "60"), (10, 27, "220")):
            do(at(mo, d, 17, 0), lambda a=amt: app.withdraw(ids["nour"], m(a), "Card payment"))

        # ---- Monthly account fees ----------------------------------------
        for month, last_day in ((9, 30), (10, 31)):
            for key in ("yasmine", "atlas", "cafe", "sahel", "karim", "nour"):
                do(at(month, last_day, 23, 0), lambda k=key, mo=month: app.charge_fee(
                    ids[k], m("12.500"), f"Monthly account fee {mo:02d}/2026"))

        # ---- Execute the schedule in chronological order -----------------
        with identity.reproducible():  # same ids on every computer
            for when, _, action in sorted(plan, key=lambda p: (p[0], p[1])):
                with clock.simulated_time(when):
                    action()

        n = app.recorder.max_notification_id()
        self.stdout.write(self.style.SUCCESS(f"Seeded {n} immutable events for {len(ids)} accounts."))
        projected = LedgerProjector(app).catch_up()
        self.stdout.write(self.style.SUCCESS(f"Projector caught up: {projected} events projected to the read model."))
