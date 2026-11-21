from __future__ import annotations

import importlib.util
import os
import warnings
from pathlib import Path
from urllib.parse import urlparse

EVENTS_DB_NAME = os.environ.get("EVENTS_DB_NAME", "ledger_events")
READ_DB_NAME = os.environ.get("READ_DB_NAME", "ledger_read")


def _ensure_databases_exist(host: str, port: str, user: str, password: str) -> None:
    import psycopg

    conninfo = f"host={host} port={port} user={user} password={password} dbname=postgres"
    with psycopg.connect(conninfo, autocommit=True) as conn:
        existing = {row[0] for row in conn.execute("SELECT datname FROM pg_database")}
        for name in (EVENTS_DB_NAME, READ_DB_NAME):
            if name not in existing:
                try:
                    conn.execute(f'CREATE DATABASE "{name}"')
                except psycopg.errors.DuplicateDatabase:
                    pass


def _start_embedded_postgres(var_dir: Path) -> tuple[str, str, str, str]:
    import pgserver
    import pgserver.postgres_server
    import tempfile

    _orig_ensure = pgserver.postgres_server.PostgresServer.ensure_postgres_running

    def _safe_ensure(self):
        self.log = Path(tempfile.gettempdir()) / f"pgserver_{self.pgdata.name}_{os.getpid()}.log"
        return _orig_ensure(self)

    pgserver.postgres_server.PostgresServer.ensure_postgres_running = _safe_ensure

    _orig_pg_ctl = pgserver.postgres_server.pg_ctl

    def _safe_pg_ctl(args, pgdata=None, user=None, timeout=60):
        return _orig_pg_ctl(args, pgdata=pgdata, user=user, timeout=max(timeout or 60, 60))

    pgserver.postgres_server.pg_ctl = _safe_pg_ctl

    tz_dir = Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
    if not (tz_dir / "Africa" / "Tunis").exists():
        import shutil

        import tzdata

        source = Path(tzdata.__file__).parent / "zoneinfo"
        try:
            shutil.copytree(source, tz_dir, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"))
        except (shutil.Error, OSError):
            for name in ("UTC", "GMT", "Etc/UTC", "Etc/GMT", "Africa/Tunis", "Europe/Paris"):
                target = tz_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copyfile(source / name, target)
                except OSError:
                    pass

    var_dir.mkdir(parents=True, exist_ok=True)
    pgdata = var_dir / "pgdata"
    pid_file = pgdata / "postmaster.pid"
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text().splitlines()[0])
            if not psutil.pid_exists(pid):
                pid_file.unlink(missing_ok=True)
                (pgdata / "postmaster.opts").unlink(missing_ok=True)
        except Exception:
            pass

    handle_file = pgdata / ".handle_pids.json"
    if handle_file.exists():
        try:
            import json
            import psutil
            pids = json.loads(handle_file.read_text())
            if not any(psutil.pid_exists(p) for p in pids):
                handle_file.unlink(missing_ok=True)
        except Exception:
            pass

    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    uri = urlparse(server.get_uri())
    host, port = uri.hostname or "127.0.0.1", str(uri.port or 5432)
    user, password = uri.username or "postgres", "embedded"
    return host, port, user, password


def resolve(base_dir: Path) -> tuple[str, dict[str, str], dict]:
    mode = os.environ.get("DB_MODE", "embedded").strip().lower()
    var_dir = Path(os.environ.get("VAR_DIR", base_dir / "var"))

    if mode == "embedded" and importlib.util.find_spec("pgserver") is None:
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

