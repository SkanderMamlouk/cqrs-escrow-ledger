#!/usr/bin/env python
"""Django's command-line tool.  Examples:

    python manage.py migrate          # create the read-model tables
    python manage.py seed_demo        # create the demo bank accounts (events)
    python manage.py run_projector    # start the read-model projector worker
    python manage.py runserver        # start the web site on http://127.0.0.1:8000
    python manage.py verify_ledger    # deterministic verification gate
"""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
