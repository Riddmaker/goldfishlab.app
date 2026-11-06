"""Mistral explains the engines (Themen-Doc, Paket 1, PR 6).

The explanation rides in the deck summary's one call: the engines that can
run go into the facts, a sentence or two on each comes back in "engines",
checked, and the deck page shows it under the engine it was written about.
Mistral is never called: `urlopen` is a stand-in.
"""

import json
from dataclasses import replace

import pytest

from cards import synergy
from decks import themes as deck_themes
from decks.themes import Reading
from simulations import mistral, summary, tasks
from simulations.engine import adapter
from simulations.models import DeckSummary
from tests.test_deck_summary_written import GOOD, _answer, _Response
from tests.test_synergy import client_in, deck, owner  # noqa: F401

ARISTOCRATS = synergy.Engine("aristocrats", "Aristocrats", "strong",
                             (("Viscera Seer", "%(card)s sacrifices"),
                              ("Blood Artist", "%(card)s pays off each death")),
                             (), ("Blood Artist",))
TOKENS = synergy.Engine("tokens", "Tokens", "medium",
                        (("Bitterblossom", "%(card)s makes tokens"),
                         ("Intangible Virtue", "%(card)s pays off tokens")),
                        (), ("Bitterblossom",))

OFFERED = [{"theme": "Aristocrats", "cards": ["Viscera Seer", "Blood Artist"],
            "what_each_does": "Viscera Seer sacrifices, Blood Artist pays off each death."}]


@pytest.fixture
def key(settings):
    settings.MISTRAL_API_KEY = "test-key-not-real"
    return settings


@pytest.fixture
def shown(monkeypatch):
    """Patch the engines the deck page and the summary read."""
    def show(*engines):
        def reading(_deck, _cards=None):
            return Reading(engines=list(engines))
        monkeypatch.setattr(deck_themes, "deck_reading", reading)
        monkeypatch.setattr("decks.views.deck_reading", reading)
    return show


# --- 1. what Mistral is told -----------------------------------------------------


def test_the_facts_hold_each_engine_that_can_run(deck, shown):  # noqa: F811
    short = replace(TOKENS, missing=("Pays off tokens",))
    second = replace(ARISTOCRATS, steps=ARISTOCRATS.steps[:1])
    shown(ARISTOCRATS, short, second)

    facts = summary.facts(deck, adapter.readings(deck))

    assert facts["engines"] == [{
        "theme": "Aristocrats", "cards": ["Viscera Seer", "Blood Artist"],
        "what_each_does": "Viscera Seer sacrifices, Blood Artist pays off each death."}], \
        "one a piece short and a second of the same theme are left out"


def test_the_facts_are_in_english_whatever_the_page_language(deck, shown):  # noqa: F811
    from django.utils import translation

    shown(ARISTOCRATS)

    with translation.override("de"):
        facts = summary.facts(deck, adapter.readings(deck))

    assert facts["engines"][0]["what_each_does"].startswith("Viscera Seer sacrifices")


def test_an_engine_reading_that_fails_leaves_the_engines_out(deck, monkeypatch):  # noqa: F811
    def broken(_deck, _cards=None):
        raise RuntimeError("no reading")
    monkeypatch.setattr(deck_themes, "deck_reading", broken)

    assert summary.facts(deck, adapter.readings(deck))["engines"] == []


def test_the_prompt_asks_how_each_engine_plays():
    text = summary.messages({"engines": OFFERED}, "de")[0]["content"]

    assert summary.PROMPT_VERSION == 6
    assert '"engines"' in text and "what stops the engine" in text
    assert "theme name" in text, "theme names stay English, as the facts give them"


# --- 2. what comes back is checked ----------------------------------------------


def _parse(engines, offered=OFFERED):
    return summary.parse(json.dumps({**GOOD, "engines": engines}), None, offered)["engines"]


def test_an_explanation_is_kept_by_theme_and_cards():
    said = _parse([{"theme": "aristocrats ", "how": "Get the **Seer** out first."}])

    assert said == [{"theme": "aristocrats", "cards": ["Blood Artist", "Viscera Seer"],
                     "how": "Get the Seer out first."}]


@pytest.mark.parametrize("engines", [
    [{"theme": "Tokens", "how": "Not offered."}],
    [{"theme": "Aristocrats", "how": ""}],
    [{"theme": "Aristocrats"}],
    ["Aristocrats"],
    "Aristocrats: sacrifice things.",
])
def test_an_unusable_explanation_is_dropped(engines):
    assert _parse(engines) == []


def test_one_explanation_per_engine_and_each_one_short():
    said = _parse([{"theme": "Aristocrats", "how": "word " * 200},
                   {"theme": "Aristocrats", "how": "A second word."}])

    assert len(said) == 1
    assert len(said[0]["how"]) <= summary.HOW_MAX + 1 and said[0]["how"].endswith("…")


def test_an_answer_without_engines_is_still_a_summary():
    checked = summary.parse(json.dumps(GOOD), None, OFFERED)

    assert checked["engines"] == [] and checked["feel"]


# --- 3. written in the summary's one call -----------------------------------------


def test_the_task_keeps_the_explanation(key, deck, shown, monkeypatch):  # noqa: F811
    shown(ARISTOCRATS)
    row = DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                                     status=DeckSummary.Status.PENDING)
    calls = []

    def urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return _Response(_answer({**GOOD, "engines": [
            {"theme": "Aristocrats", "how": "Keep Blood Artist back."},
            {"theme": "Invented", "how": "Nothing."}]}))
    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)

    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.DONE

    row.refresh_from_db()
    assert len(calls) == 1, "no extra call for the engines"
    assert row.prompt_version == 6
    assert row.content["engines"] == [{"theme": "aristocrats",
                                       "cards": ["Blood Artist", "Viscera Seer"],
                                       "how": "Keep Blood Artist back."}]


# --- 4. on the deck page ------------------------------------------------------------


def _written(deck, engines, language="en", status=DeckSummary.Status.DONE):  # noqa: F811
    return DeckSummary.objects.create(
        deck=deck, fingerprint=summary.fingerprint(deck), status=status, language=language,
        content={**GOOD, "engines": engines})


EXPLAINED = [{"theme": "aristocrats", "cards": ["Blood Artist", "Viscera Seer"],
              "how": "Keep Blood Artist back until the wipe."}]


def test_the_deck_page_shows_the_explanation_under_its_engine(
        key, client_in, deck, shown):  # noqa: F811
    shown(ARISTOCRATS, TOKENS)
    _written(deck, EXPLAINED, language="de")

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert body.count("data-explained=") == 1
    assert 'data-explained="aristocrats"' in body
    assert '<span lang="de">Keep Blood Artist back until the wipe.</span>' in body
    assert "Written by Mistral AI. It can be wrong." in body


def test_an_engine_with_other_cards_gets_no_explanation(
        key, client_in, deck, shown):  # noqa: F811
    shown(replace(ARISTOCRATS, steps=(("Carrion Feeder", "%(card)s sacrifices"),
                                      ("Blood Artist", "%(card)s pays off each death"))))
    _written(deck, EXPLAINED)

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "data-explained" not in body


@pytest.mark.parametrize("case", ["switched off", "not written", "no key"])
def test_no_explanation_without_a_written_summary_to_show(
        settings, client_in, owner, deck, shown, case):  # noqa: F811
    settings.MISTRAL_API_KEY = "" if case == "no key" else "test-key-not-real"
    shown(ARISTOCRATS)
    _written(deck, EXPLAINED, status=(DeckSummary.Status.PENDING if case == "not written"
                                      else DeckSummary.Status.DONE))
    if case == "switched off":
        owner.deck_summaries = False
        owner.save(update_fields=["deck_summaries"])

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "data-explained" not in body


def test_the_explanation_is_escaped(key, client_in, deck, shown):  # noqa: F811
    shown(ARISTOCRATS)
    _written(deck, [{**EXPLAINED[0], "how": "<script>alert(1)</script>"}])

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_lock_button_answer_keeps_the_explanation(
        key, client_in, deck, shown):  # noqa: F811
    shown(ARISTOCRATS)
    _written(deck, EXPLAINED)

    response = client_in.post(f"/decks/{deck.pk}/marks/", {"action": "lock", "key": "x"},
                              HTTP_HX_REQUEST="true")

    assert 'data-explained="aristocrats"' in response.content.decode()
