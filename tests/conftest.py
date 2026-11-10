"""Shared pytest fixtures.

`app` is a fresh BankingApplication using the in-memory event store
("POPO" = Plain Old Python Objects): super fast, nothing written to disk.
"""

from datetime import datetime, timezone

import pytest

from banking import clock
from banking.application import BankingApplication
from banking.money import to_millimes as m


@pytest.fixture
def app():
    return BankingApplication(env={"PERSISTENCE_MODULE": "eventsourcing.popo", "SNAPSHOT_INTERVAL": "5"})


@pytest.fixture
def ledger_app(app, db):
    """Same in-memory app, installed as THE app used by Django views/projector."""
    from config import ledger

    ledger.set_app(app)
    yield app
    ledger.set_app(None)


@pytest.fixture
def two_accounts(app):
    with clock.simulated_time(datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)):
        alice = app.open_account("Alice Ben Ali", "CURRENT", initial_deposit=m("1000")).account_id
        bob = app.open_account("Bob Gharbi", "CURRENT", initial_deposit=m("200")).account_id
    return alice, bob
