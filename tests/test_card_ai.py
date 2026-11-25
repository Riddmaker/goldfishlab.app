"""More about this card (B-22, PR 2): what else it is good for, by the AI.

What these tests are for, in order of how much they matter:

1. **The AI cannot invent a card.** A partner it was not given is dropped; a
   combo link appears only where Commander Spellbook has one.
2. **Only a fresh answer costs**: a kept one is free, a failure is refunded,
   and the free plan stops at its allowance with a sentence, not an error.
3. **Without a Mistral key there is no AI section at all**, and every word the
   AI wrote is escaped.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from billing import quotas
from billing.models import UsageRecord
from cards.models import OracleCard
from combos import services as combo_services
from decks import services as deck_services
from simulations import card_ai, mistral
from simulations.models import CardInsight
from tests.support import Calls

PASSWORD = "pw-for-test-only"
DECK_LIST = (b"1 Exquisite Blood\n1 Carrion Feeder\n1 Mikaeus, the Unhallowed\n"
             b"1 Gravecrawler\n1 Blood Artist\n30 Swamp\n")
METRIC = UsageRecord.Metric.CARD_INSIGHTS


# --- 1. what the AI may say -------------------------------------------------


def _answer(*items):
    return json.dumps({"items": [{"partner": p, "text": t} for p, t in items]})


def test_a_partner_it_was_not_given_is_dropped():
    items = card_ai.parse(_answer(("Viscera Seer", "Sacrifice for value."),
                                  ("Made Up Card", "Infinite!"),
                                  (None, "Works with any death.")),
                          ["Viscera Seer"])

    assert [(i["partner"], i["text"]) for i in items] == [
        ("Viscera Seer", "Sacrifice for value."), ("", "Works with any death.")]


def test_a_combo_on_spellbook_is_the_only_proof():
    combo = combo_services.CardCombo(spellbook_id="1-2", cards=("Blood Artist", "Viscera Seer"),
                                     produces=(), missing=(), needs=(), popularity=1)

    items = card_ai.parse(_answer(("Viscera Seer", "a"), ("Bitterblossom", "b")),
                          ["Viscera Seer", "Bitterblossom"], [combo])

    assert items[0]["proof"] == combo.url
    assert items[1]["proof"] == ""


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"items": "x"}',
                                     '{"items": [1, {"text": 2}, {"text": "  "}]}'])
def test_a_broken_answer_keeps_nothing(content):
    assert card_ai.parse(content, ["Viscera Seer"]) == []


def test_the_answer_has_a_cap():
    many = _answer(*[(None, "x" * 1000)] * 10)

    items = card_ai.parse(many, [])

    assert len(items) == card_ai.ITEMS_MAX
    assert all(len(i["text"]) == card_ai.TEXT_MAX for i in items)


def test_the_prompt_carries_card_texts_and_the_language_only():
    card = OracleCard(name="Blood Artist", type_line="Creature", oracle_text="Whenever ...")
    other = OracleCard(name="Viscera Seer", type_line="Creature", oracle_text="Sacrifice ...")

    system, user = card_ai.messages(card, [other], "de")

    assert "German" in system["content"] and "{" not in system["content"].split("JSON:")[0]
    assert json.loads(user["content"]) == {
        "card": {"name": "Blood Artist", "type": "Creature", "text": "Whenever ..."},
        "other_cards": [{"name": "Viscera Seer", "type": "Creature", "text": "Sacrifice ..."}]}


# --- 2. the allowance -------------------------------------------------------


@pytest.fixture
def owner(catalogue):
    return get_user_model().objects.create_user(email="ai@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(owner=owner, raw=DECK_LIST, name="AI").deck


@pytest.fixture
def no_spellbook(monkeypatch):
    monkeypatch.setattr(combo_services, "card_combos",
                        lambda *a, **k: combo_services.CardCombos())


@pytest.fixture
def ai(settings, monkeypatch, no_spellbook):
    """Mistral, configured and answering; set `failure` to make it fail."""
    settings.MISTRAL_API_KEY = "test-only"
    calls = Calls()

    def fake(messages, **kwargs):
        calls.append(kwargs)
        if calls.failure is not None:
            raise calls.failure
        return mistral.Completion(content=_answer((None, "Drains <b>everyone</b>.")),
                                  model="mistral-small-2603", prompt_tokens=1,
                                  completion_tokens=1)
    monkeypatch.setattr(mistral, "complete", fake)
    return calls


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _url(deck, name="Blood Artist"):
    card = OracleCard.objects.get(name=name)
    return reverse("simulations:card_ai", args=[deck.id, card.pk])


def _ask(client, deck):
    return client.post(_url(deck), HTTP_HX_REQUEST="true").content.decode()


@pytest.mark.django_db
def test_a_fresh_answer_costs_one_and_a_kept_one_nothing(signed_in, owner, deck, ai):
    first = _ask(signed_in, deck)
    second = _ask(signed_in, deck)

    assert len(ai) == 1
    assert ai[0]["retry"] is False and ai[0]["timeout"] < 30
    assert quotas.used(owner, METRIC) == 1
    assert CardInsight.objects.count() == 1
    for body in (first, second):
        assert "data-card-ai-item" in body and "Written by an AI" in body
        assert "Drains &lt;b&gt;everyone&lt;/b&gt;." in body


@pytest.mark.django_db
def test_a_failure_is_refunded_and_says_so(signed_in, owner, deck, ai):
    ai.failure = mistral.MistralError("down")

    body = _ask(signed_in, deck)

    assert quotas.used(owner, METRIC) == 0
    assert "could not be reached" in body and "data-card-ai-button" in body
    assert not CardInsight.objects.exists()


@pytest.mark.django_db
def test_the_free_plan_stops_at_its_allowance(signed_in, owner, deck, ai):
    limit = quotas.check(owner, METRIC, raise_on_fail=False).limit
    assert limit == 5
    quotas.consume(owner, METRIC, limit)

    body = _ask(signed_in, deck)

    assert ai == [] and "data-card-ai-refused" in body
    assert "5 of 5 cards this month" in body and reverse("billing:plans") in body


@pytest.mark.django_db
def test_without_javascript_it_goes_back_to_the_card_page(signed_in, deck, ai):
    response = signed_in.post(_url(deck))

    assert response.status_code == 302 and response["Location"].endswith("?insight=1#card-ai")
    page = signed_in.get(response["Location"]).content.decode()
    assert "data-card-ai-item" in page


@pytest.mark.django_db
def test_without_javascript_a_failure_is_said_on_the_card_page(signed_in, deck, ai):
    ai.failure = mistral.MistralError("down")

    page = signed_in.post(_url(deck), follow=True).content.decode()

    assert "could not be reached" in page and "data-card-ai-button" in page


@pytest.mark.django_db
def test_somebody_elses_deck_and_a_basic_land_ask_nothing(client, deck, ai):
    stranger = get_user_model().objects.create_user(email="x@example.com", password=PASSWORD)
    client.force_login(stranger)
    assert client.post(_url(deck)).status_code == 404

    client.force_login(deck.owner)
    assert client.post(_url(deck, "Swamp")).status_code == 404
    assert ai == []


# --- 3. no key, no section ---------------------------------------------------


@pytest.mark.django_db
def test_without_a_key_there_is_no_ai_at_all(signed_in, deck, settings, no_spellbook):
    settings.MISTRAL_API_KEY = ""
    card = OracleCard.objects.get(name="Blood Artist")

    fragment = signed_in.get(reverse("simulations:insight", args=[deck.id, card.pk]))

    assert "data-card-ai" not in fragment.content.decode()
    assert signed_in.post(_url(deck)).status_code == 404


@pytest.mark.django_db
def test_the_insight_offers_the_button_with_what_is_left(signed_in, deck, ai):
    card = OracleCard.objects.get(name="Blood Artist")

    body = signed_in.get(reverse("simulations:insight", args=[deck.id, card.pk])).content.decode()

    assert "data-card-ai-button" in body and "5 questions left this month." in body
    assert ai == []  # the button asks; showing it does not


def test_the_card_ai_part_speaks_the_page_language():
    from django.utils import translation

    with translation.override("de"):
        html = render_to_string("simulations/_card_ai.html", {
            "deck": type("D", (), {"id": "00000000-0000-0000-0000-000000000000"})(),
            "card": type("C", (), {"pk": "00000000-0000-0000-0000-000000000000"})(),
            "ai": None, "ai_decision": quotas.QuotaDecision(True, METRIC, None, 0, None)})

    assert "Die KI fragen" in html
