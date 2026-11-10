"""Web layer: commands through HTTP, Pydantic validation, concurrency, webhooks."""

import json

import pytest
from django.test import Client

from banking.money import to_millimes as m
from readmodel.models import AccountSummary, WebhookInbox
from readmodel.projector import LedgerProjector
from readmodel.webhooks import sign

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_and_account(ledger_app):
    acc = ledger_app.open_account("Web User", initial_deposit=m("100")).account_id
    LedgerProjector(ledger_app).catch_up()
    return Client(), acc


def test_pages_render(ledger_app, client_and_account):
    c, acc = client_and_account
    for url in ["/", f"/accounts/{acc}/", f"/accounts/{acc}/replay/", f"/accounts/{acc}/audit/",
                "/events/", "/alerts/", "/system/", "/health/"]:
        assert c.get(url).status_code == 200, url


def test_deposit_command_appends_an_event(ledger_app, client_and_account):
    c, acc = client_and_account
    r = c.post(f"/accounts/{acc}/command/deposit/", {"amount": "25.5", "channel": "MOBILE", "expected_version": "2"})
    assert r.status_code == 302
    assert ledger_app.get_account(acc).balance == m("125.5")


def test_pydantic_rejects_malformed_input(ledger_app, client_and_account):
    c, acc = client_and_account
    r = c.post(f"/accounts/{acc}/command/deposit/", {"amount": "-3", "channel": "BITCOIN"}, follow=True)
    assert "Invalid input" in r.content.decode()
    assert ledger_app.get_account(acc).version == 2


def test_stale_expected_version_is_refused(ledger_app, client_and_account):
    c, acc = client_and_account
    r = c.post(f"/accounts/{acc}/command/withdraw/", {"amount": "1", "expected_version": "1"}, follow=True)
    assert "Concurrency conflict" in r.content.decode()
    assert ledger_app.get_account(acc).version == 2


def test_time_travel_api(ledger_app, client_and_account):
    c, acc = client_and_account
    data = c.get(f"/api/accounts/{acc}/balance-at/").json()
    assert data["ledger_balance_tnd"] == "100.000" and data["version"] == 2


def test_bad_query_parameters_never_crash(client_and_account):
    c, acc = client_and_account
    assert c.get(f"/accounts/{acc}/replay/?step=abc").status_code == 200
    assert c.get("/events/?page=xyz").status_code == 200
    assert c.get("/events/?page=999").status_code == 200
    assert c.get(f"/api/accounts/{acc}/balance-at/?at=not-a-date").status_code == 400
    assert c.get(f"/api/accounts/{acc}/balance-at/?at=2026-10-20T12:00:00 01:00").status_code == 200


def test_empty_audit_window_shows_a_message(client_and_account):
    c, acc = client_and_account
    r = c.post(f"/accounts/{acc}/audit/", {"start": "2030-01-01T00:00", "end": ""}, follow=True)
    assert r.status_code == 200 and "No event in this time window" in r.content.decode()


def test_webhook_receiver_checks_hmac_signature(client_and_account):
    c, _ = client_and_account
    body = json.dumps({"rule": "LARGE_TRANSFER", "event_id": "EVT-00000000-0001"}).encode()
    ok = c.post("/webhooks/receive/", body, content_type="application/json", HTTP_X_LEDGER_SIGNATURE=sign(body))
    bad = c.post("/webhooks/receive/", body, content_type="application/json", HTTP_X_LEDGER_SIGNATURE="sha256=forged")
    assert ok.status_code == 200 and bad.status_code == 401
    assert list(WebhookInbox.objects.values_list("signature_valid", flat=True).order_by("id")) == [True, False]


def test_open_account_via_form(ledger_app):
    r = Client().post("/accounts/open/", {"owner_name": "New Customer", "account_type": "SAVINGS", "initial_deposit": "10"})
    assert r.status_code == 302
    assert len(ledger_app.iter_account_ids()) == 1
    assert AccountSummary.objects.count() == 0  # read side updates only when the projector runs
