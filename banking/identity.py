"""
Identifiers (account ids, HLD-/TRF-/FEE- references).

Normally every new id is random (uuid4).  The demo-data generator wraps its
work in `reproducible()`, which makes ids deterministic (uuid5 + a counter),
so the demo bank has EXACTLY the same account ids and event ids
(e.g. EVT-xxxxxxxx-0013) on every computer - the screenshots, slides and
documentation therefore match what you see on screen.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager

_NAMESPACE = uuid.UUID("6f1d3a52-14de-4c0b-9b8e-2026d5114bb1")  # fixed namespace for Project 14
_local = threading.local()


@contextmanager
def reproducible(seed: str = "ds1-project14-demo"):
    previous = (getattr(_local, "seed", None), getattr(_local, "counter", 0))
    _local.seed, _local.counter = seed, 0
    try:
        yield
    finally:
        _local.seed, _local.counter = previous


def new_uuid() -> uuid.UUID:
    seed = getattr(_local, "seed", None)
    if seed is None:
        return uuid.uuid4()
    _local.counter += 1
    return uuid.uuid5(_NAMESPACE, f"{seed}:{_local.counter}")


def new_hex8() -> str:
    return new_uuid().hex[:8]
