"""python manage.py verify_ledger [--catch-up] [--json PATH]

Deterministic verification gate (see readmodel/verification.py).
Exit code 0 = everything consistent, 1 = at least one check failed.
"""

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from config.ledger import get_app
from readmodel.projector import LedgerProjector
from readmodel.verification import verify


class Command(BaseCommand):
    help = "Prove that event store, snapshots and read model are consistent."

    def add_arguments(self, parser):
        parser.add_argument("--catch-up", action="store_true", help="Run the projector first.")
        parser.add_argument("--json", default="", help="Also write the report as JSON to this file.")

    def handle(self, *args, **opts):
        app = get_app()
        if opts["catch_up"]:
            LedgerProjector(app).catch_up()
        report = verify(app)
        for c in report.checks:
            mark = self.style.SUCCESS("PASS") if c.passed else self.style.ERROR("FAIL")
            self.stdout.write(f"  {mark}  {c.name}" + (f"   ({c.detail})" if c.detail and not c.passed else ""))
        summary = f"{sum(c.passed for c in report.checks)}/{len(report.checks)} checks passed " \
                  f"on {report.accounts} accounts / {report.events} events"
        if opts["json"]:
            Path(opts["json"]).parent.mkdir(parents=True, exist_ok=True)
            Path(opts["json"]).write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        if report.passed:
            self.stdout.write(self.style.SUCCESS(f"VERIFICATION GATE: PASSED - {summary}"))
        else:
            self.stdout.write(self.style.ERROR(f"VERIFICATION GATE: FAILED - {summary}"))
            sys.exit(1)
