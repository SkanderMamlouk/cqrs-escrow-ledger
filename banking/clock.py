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
    previous = getattr(_local, "simulated", None)
    _local.simulated = moment
    try:
        yield
    finally:
        _local.simulated = previous

