from __future__ import annotations

import threading

from banking.application import BankingApplication

_app: BankingApplication | None = None
_lock = threading.Lock()


def get_app() -> BankingApplication:
    global _app
    if _app is None:
        with _lock:
            if _app is None:
                from django.conf import settings

                _app = BankingApplication(env=dict(settings.EVENTSTORE_ENV))
    return _app


def set_app(app: BankingApplication | None) -> None:
    global _app
    _app = app

