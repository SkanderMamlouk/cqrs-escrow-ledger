"""
The business clock.

Every domain event records the moment it happened (its `timestamp`).
Normally that is simply "now".  The demo-data generator (`seed_demo`) needs
to create a realistic history spread over several weeks, so it can tell the
clock to use a *simulated* date instead.  This is clearly flagged in the
README: seed data is synthetic demo data; events created through the web
site always use the real current time.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_local = threading.local()


def now() -> datetime:
    simulated = getattr(_local, "simulated", None)
    return simulated if simulated is not None else datetime.now(timezone.utc)


@contextmanager
def simulated_time(moment: datetime):
    """with simulated_time(datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)): ..."""
    previous = getattr(_local, "simulated", None)
    _local.simulated = moment
    try:
        yield
    finally:
        _local.simulated = previous
