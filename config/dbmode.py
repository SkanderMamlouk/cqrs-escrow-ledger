"""
Where do the two databases live?  (read this first if you are new)
=================================================================

CQRS means we keep TWO separate stores:

    1. the EVENT STORE  (write side)  -> every event ever, append-only
    2. the READ DATABASE (query side) -> tables pre-computed for fast screens

This module decides *where* both stores live, based on one setting called
DB_MODE (read from the environment / the .env file):

    DB_MODE=postgres   -> a PostgreSQL server you already have
                          (this is what Docker Compose uses: host "db")
    DB_MODE=embedded   -> a REAL PostgreSQL 16 server that is started
                          automatically from the Python package "pgserver".
                          Nothing to install, no admin rights needed.
                          This is the default when you run without Docker.
    DB_MODE=sqlite     -> two simple SQLite files (last-resort fallback)

Whatever the mode, the result is always the same two dictionaries:

    EVENTSTORE_ENV  -> settings given to the `eventsourcing` library
    DJANGO_DATABASE -> settings given to Django for the read database
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

EVENTS_DB_NAME = os.environ.get("EVENTS_DB_NAME", "ledger_events")
READ_DB_NAME = os.environ.get("READ_DB_NAME", "ledger_read")


def _ensure_databases_exist(host: str, port: str, user: str, password: str) -> None:
    """Create the two PostgreSQL databases if they are missing (idempotent)."""
    import psycopg

    conninfo = f"host={host} port={port} user={user} password={password} dbname=postgres"
    with psycopg.connect(conninfo, autocommit=True) as conn:
        existing = {row[0] for row in conn.execute("SELECT datname FROM pg_database")}
        for name in (EVENTS_DB_NAME, READ_DB_NAME):
            if name not in existing:
                try:
                    conn.execute(f'CREATE DATABASE "{name}"')
                except psycopg.errors.DuplicateDatabase:
                    pass  # another process created it at the same moment


def _start_embedded_postgres(var_dir: Path) -> tuple[str, str, str, str]:
    """Start (or re-use) the embedded PostgreSQL server stored in var/pgdata."""
    import pgserver  # real PostgreSQL binaries shipped as a pip package

    # The Windows build of pgserver ships without PostgreSQL's time-zone
    # database, so even "SET TIME ZONE 'UTC'" fails.  The Python package
    # `tzdata` contains exactly the same compiled IANA zone files, so we
    # copy them once into the place PostgreSQL looks for them.
    tz_dir = Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
    if not (tz_dir / "Africa" / "Tunis").exists():
        import shutil

        import tzdata

        source = Path(tzdata.__file__).parent / "zoneinfo"
        try:
            # only the binary zone files: skip Python files and __pycache__
            # (they are useless here and can exceed Windows' 260-char path limit)
            shutil.copytree(source, tz_dir, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"))
        except (shutil.Error, OSError):
            # very long install path: copy at least the zones we really use
            for name in ("UTC", "GMT", "Etc/UTC", "Etc/GMT", "Africa/Tunis", "Europe/Paris"):
                target = tz_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copyfile(source / name, target)
                except OSError:
                    pass

    var_dir.mkdir(parents=True, exist_ok=True)
    # cleanup_mode="stop": the server stops automatically when the last
    # process using it (web server, projector...) exits.
    server = pgserver.get_server(var_dir / "pgdata", cleanup_mode="stop")
    uri = urlparse(server.get_uri())
    host, port = uri.hostname or "127.0.0.1", str(uri.port or 5432)
    user, password = uri.username or "postgres", "embedded"  # trust auth: any password
    return host, port, user, password


def resolve(base_dir: Path) -> tuple[str, dict[str, str], dict]:
    """Return (mode, EVENTSTORE_ENV, DJANGO_DATABASE)."""
    mode = os.environ.get("DB_MODE", "embedded").strip().lower()
    var_dir = Path(os.environ.get("VAR_DIR", base_dir / "var"))

    if mode == "embedded":
        try:
            import pgserver  # noqa: F401
        except ImportError:
            import warnings

            warnings.warn("DB_MODE=embedded needs the 'pgserver' package (Python 3.11 or 3.12). "
                          "Falling back to DB_MODE=sqlite.", RuntimeWarning, stacklevel=2)
            mode = "sqlite"

    if mode == "sqlite":
        var_dir.mkdir(parents=True, exist_ok=True)
        eventstore_env = {
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(var_dir / "events.sqlite3"),
        }
        django_db = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": var_dir / "read.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
        return mode, eventstore_env, django_db

    if mode == "embedded":
        host, port, user, password = _start_embedded_postgres(var_dir)
    elif mode == "postgres":
        host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
        port = os.environ.get("POSTGRES_PORT", "5432")
        user = os.environ.get("POSTGRES_USER", "postgres")
        password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    else:
        raise RuntimeError(f"Unknown DB_MODE={mode!r} (use postgres, embedded or sqlite)")

    if os.environ.get("SKIP_DB_BOOTSTRAP") != "1":
        _ensure_databases_exist(host, port, user, password)

    eventstore_env = {
        "PERSISTENCE_MODULE": "eventsourcing.postgres",
        "POSTGRES_DBNAME": EVENTS_DB_NAME,
        "POSTGRES_HOST": host,
        "POSTGRES_PORT": port,
        "POSTGRES_USER": user,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_POOL_SIZE": os.environ.get("POSTGRES_POOL_SIZE", "5"),
    }
    django_db = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": READ_DB_NAME,
        "HOST": host,
        "PORT": port,
        "USER": user,
        "PASSWORD": password,
        "CONN_MAX_AGE": 60,
    }
    return mode, eventstore_env, django_db
