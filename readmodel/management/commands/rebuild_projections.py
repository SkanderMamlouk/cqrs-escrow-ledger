import time

from django.core.management.base import BaseCommand

from config.ledger import get_app
from readmodel.projector import LedgerProjector, reset_read_model


class Command(BaseCommand):
    help = "Wipe and rebuild all CQRS read models from the immutable event log."

    def handle(self, *args, **opts):
        started = time.perf_counter()
        reset_read_model()
        n = LedgerProjector(get_app()).catch_up()
        ms = (time.perf_counter() - started) * 1000
        self.stdout.write(self.style.SUCCESS(f"Rebuilt read model from {n} events in {ms:.0f} ms."))

