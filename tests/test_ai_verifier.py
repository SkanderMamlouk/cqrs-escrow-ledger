import copy
import json

import pytest

from ai_audit import assistant
from ai_audit.evidence import build_evidence
from ai_audit.providers import ProviderInfo
from ai_audit.verifier import extract_amounts, verify
from banking.money import to_millimes as m


@pytest.fixture
def evidence(app):
    a = app.open_account("Karim B.", initial_deposit=m("3000")).account_id
    b = app.open_account("Seller", initial_deposit=m("1")).account_id
    app.deposit(a, m("20000"), "invoice", "WIRE")
    hold = app.reserve_funds(a, m("15000"), b, "escrow").extra["hold_id"]
    fee = app.charge_fee(a, m("12.500"), "monthly").extra["fee_id"]
    app.reverse_fee(a, fee, "goodwill")
    app.settle_escrow(a, hold, "done")
    return build_evidence(app, a)


@pytest.fixture
def good(evidence):
    return assistant.deterministic_report(evidence)


def test_deterministic_report_passes_all_seven_checks(evidence, good):
    result = verify(good, evidence)
    assert result.passed, result.feedback()
    assert len(result.checks) == 7


def test_invented_event_id_is_rejected(evidence, good):
    bad = copy.deepcopy(good)
    bad["findings"][0]["citations"][0]["event_id"] = "EVT-deadbeef-0099"
    assert not verify(bad, evidence).passed


def test_wrong_timestamp_is_rejected(evidence, good):
    bad = copy.deepcopy(good)
    bad["findings"][0]["citations"][0]["timestamp"] = "2020-01-01T00:00:00Z"
    r = verify(bad, evidence)
    assert not r.passed and "timestamp" in r.feedback()


def test_invented_amount_is_rejected(evidence, good):
    bad = copy.deepcopy(good)
    bad["findings"][1]["statement"] += " An extra 777.000 TND was also received."
    r = verify(bad, evidence)
    assert not r.passed and "777.000" in r.feedback()


def test_wrong_closing_balance_is_rejected(evidence, good):
    bad = dict(good, closing_balance_tnd="1.000")
    assert not verify(bad, evidence).passed


def test_hiding_a_material_event_is_rejected(evidence, good):
    bad = copy.deepcopy(good)
    bad["findings"] = [f for f in bad["findings"] if f["category"] not in ("reversal", "escrow", "transfer", "risk")]
    r = verify(bad, evidence)
    assert not r.passed and "Material events" in r.feedback()


def test_category_must_match_event_type(evidence, good):
    bad = copy.deepcopy(good)
    for f in bad["findings"]:
        if f["category"] == "fee":
            f["category"] = "withdrawal"
    assert not verify(bad, evidence).passed


def test_free_text_answer_is_rejected(evidence):
    r = verify("The account looks fine to me!", evidence)
    assert not r.passed and r.checks[0].name.startswith("1.")


def test_invented_number_in_headline_is_rejected(evidence, good):
    bad = dict(good, headline="Karim secretly withdrew 999999.000 TND")
    r = verify(bad, evidence)
    assert not r.passed and "999999.000" in r.feedback()


@pytest.mark.parametrize("sentence", [
    "An extra TND 77777.000 was received.",
    "An extra 55555.000 Tunisian dinars arrived.",
    "The balance was -96500.000 at some point.",
    "There were 9 suspicious deposits.",
])
def test_any_ungrounded_number_is_rejected(evidence, good, sentence):
    bad = copy.deepcopy(good)
    bad["findings"][1]["statement"] = (bad["findings"][1]["statement"] + " " + sentence)[:400]
    assert not verify(bad, evidence).passed


@pytest.mark.parametrize("sentence", [
    "See also EVT-deadbeef-0042 for details.",
    "Hold HLD-ffffffff was also placed.",
    "It happened on 31 December 1999.",
    "It happened on 1999-12-31.",
])
def test_invented_references_and_dates_in_text_are_rejected(evidence, good, sentence):
    bad = copy.deepcopy(good)
    bad["findings"][0]["statement"] = (bad["findings"][0]["statement"] + " " + sentence)[:400]
    assert not verify(bad, evidence).passed


def test_real_dates_and_references_in_text_are_accepted(evidence, good):
    ok = copy.deepcopy(good)
    first = evidence.events[0]
    ok["findings"][0]["statement"] += f" The account was opened on {first['timestamp'][:10]}."
    assert verify(ok, evidence).passed


def test_fallback_template_is_always_verifiable(app):
    acc = app.open_account("Stress Test", initial_deposit=m("500000")).account_id
    other = app.open_account("Beneficiary", initial_deposit=m("1")).account_id
    for i in range(5):
        fee = app.charge_fee(acc, m("12.5"), f"fee {i}").extra["fee_id"]
        app.reverse_fee(acc, fee, "customer said 123.456 DT was charged twice")
    for i in range(6):
        app.reserve_funds(acc, m(str(20000 + i)), other, f"escrow {i} worth 99999.999 TND")
    ev = build_evidence(app, acc)
    result = verify(assistant.deterministic_report(ev), ev)
    assert result.passed, result.feedback()


@pytest.mark.django_db
def test_empty_window_is_refused_cleanly(app, evidence):
    from datetime import datetime, timezone

    with pytest.raises(assistant.EmptyWindow):
        assistant.run_audit(app, evidence.account_id, start=datetime(2030, 1, 1, tzinfo=timezone.utc))


def test_amount_extraction():
    assert extract_amounts("Paid 1,500.250 TND and 45 TND, version 12") == [m("1500.250") / 1000, 45]


class FakeProvider:
    name, model = "fake", "fake-llm"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate(self, system, user):
        self.prompts.append(user)
        return self.answers.pop(0)


@pytest.mark.django_db
def test_retry_with_feedback_then_verified(app, evidence, good, monkeypatch):
    hallucinated = copy.deepcopy(good)
    hallucinated["closing_balance_tnd"] = "999999.000"
    fake = FakeProvider([json.dumps(hallucinated), json.dumps(good)])
    monkeypatch.setattr(assistant, "select_provider", lambda: (fake, ProviderInfo("fake", "fake-llm", "test")))
    run = assistant.run_audit(app, evidence.account_id)
    assert run.status == "VERIFIED" and len(run.attempts) == 2
    assert "REJECTED BY THE VERIFIER" in fake.prompts[1]


@pytest.mark.django_db
def test_always_hallucinating_model_falls_back_to_deterministic(app, evidence, monkeypatch):
    fake = FakeProvider(["not json"] * 5)
    monkeypatch.setattr(assistant, "select_provider", lambda: (fake, ProviderInfo("fake", "fake-llm", "test")))
    run = assistant.run_audit(app, evidence.account_id)
    assert run.status == "FALLBACK" and run.provider == "deterministic"
    assert run.verification["passed"] is True

