"""Settings used by pytest: fast SQLite + deterministic AI unless the CI overrides DB_MODE."""

import os
import tempfile

os.environ.setdefault("DB_MODE", "sqlite")
os.environ.setdefault("VAR_DIR", tempfile.mkdtemp(prefix="ledger-test-"))
os.environ.setdefault("AI_PROVIDER", "deterministic")
os.environ.setdefault("READ_WAIT_SECONDS", "0.3")

from config.settings import *  # noqa: E402,F401,F403
