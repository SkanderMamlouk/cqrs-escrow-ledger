"""python manage.py reset_all --yes

DANGER (demo only): deletes the event store AND the read database, then
recreates empty tables.  Use it to restart the demo from zero.
"""

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from config import dbmode, ledger


class Command(BaseCommand):
    help = "Delete ALL events and read models (demo reset)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Confirm you really want to delete everything.")

    def handle(self, *args, **opts):
        if not opts["yes"]:
            raise CommandError("This deletes every event. Re-run with --yes to confirm.")
        app = ledger._app
        if app is not None:
            app.close()
            ledger.set_app(None)
        connections.close_all()

        if settings.DB_MODE == "sqlite":
            for key in ("SQLITE_DBNAME",):
                path = Path(settings.EVENTSTORE_ENV[key])
                for suffix in ("", "-wal", "-shm"):
                    p = Path(str(path) + suffix)
                    if p.exists():
                        p.unlink()
            read = Path(settings.DATABASES["default"]["NAME"])
            if read.exists():
                read.unlink()
        else:
            import psycopg

            db = settings.DATABASES["default"]
            conninfo = (f"host={db['HOST']} port={db['PORT']} user={db['USER']} "
                        f"password={db['PASSWORD']} dbname=postgres")
            with psycopg.connect(conninfo, autocommit=True) as conn:
                for name in (dbmode.EVENTS_DB_NAME, dbmode.READ_DB_NAME):
                    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                    conn.execute(f'CREATE DATABASE "{name}"')
        call_command("migrate", verbosity=0)
        self.stdout.write(self.style.SUCCESS("Event store and read model are now empty."))
