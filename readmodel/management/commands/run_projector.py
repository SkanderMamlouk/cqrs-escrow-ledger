"""python manage.py run_projector  [--once]

Starts the asynchronous read-model projector worker (Step 2 of the PDF).
--once : process everything that is pending, deliver webhooks, then exit.
"""

from django.core.management.base import BaseCommand

from config.ledger import get_app
from readmodel.projector import LedgerProjector
from readmodel.webhooks import deliver_pending


class Command(BaseCommand):
    help = "Run the CQRS read-model projector (event log -> SQL read tables)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Catch up once and exit.")
        parser.add_argument("--poll", type=float, default=None, help="Polling interval in seconds.")

    def handle(self, *args, **opts):
        projector = LedgerProjector(get_app())
        if opts["once"]:
            n = projector.catch_up()
            sent = deliver_pending(limit=500)
            self.stdout.write(self.style.SUCCESS(f"Projected {n} event(s); delivered {sent} webhook(s)."))
            return
        try:
            projector.run_forever(opts["poll"])
        except KeyboardInterrupt:
            self.stdout.write("Projector stopped.")
