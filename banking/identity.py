from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager

_NAMESPACE = uuid.UUID("6f1d3a52-14de-4c0b-9b8e-2026d5114bb1")
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

