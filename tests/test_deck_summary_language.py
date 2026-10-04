"""The deck summary in the language of whoever starts it (phase 12 G, Q3).

Mistral is never called, as in `test_deck_summary_written`. The language is
kept on the row and is never part of the fingerprint: switching languages must
not rewrite a summary, because writing one costs a run.
"""

# The fixtures come from test_deck_summary_written; pytest finds them by name.
# ruff: noqa: F811

import json

import pytest
from django.conf import settings as django_settings
from django.utils import translation

from simulations import mistral, summary, tasks
from simulations.engine import adapter
from simulations.models import DeckSummary
from tests.test_deck_summary_written import (  # noqa: F401
    GOOD,
    _answer,
    _page,
    _Response,
    _start,
    deck,
    finished,
    key,
    no_runs,
    owner,
    queued,
)

pytestmark = pytest.mark.django_db


def test_every_language_of_the_site_has_a_line_in_the_prompt():
    assert set(summary.WRITE_IN) == set(django_settings.LANGUAGE_NAMES)
    assert summary.PROMPT_VERSION >= 5


def test_the_prompt_names_the_language_and_keeps_the_names_english(owner, deck):
    facts = summary.facts(deck, adapter.readings(deck))

    english = summary.messages(facts)[0]["content"]
    german = summary.messages(facts, "de")[0]["content"]

    assert english.endswith("Write in English.")
    assert "German, addressing the reader informally (du)" in german
    assert "strategy name and card name exactly as given, in English" in german
    assert summary.messages(facts, "xx")[0]["content"] == english


@pytest.mark.parametrize(("active", "stored"), [("de", "de"), ("pt-br", "pt-br"),
                                                ("en", "en"), ("de-ch", "en")])
def test_a_summary_is_written_in_the_language_it_was_started_in(
        key, owner, deck, no_runs, queued, active, stored):
    with translation.override(active):
        _start(owner, deck)

    assert DeckSummary.objects.get(deck=deck).language == stored


def test_switching_languages_does_not_rewrite_or_charge(key, owner, deck, no_runs, queued):
    with translation.override("de"):
        _start(owner, deck)
    row = DeckSummary.objects.get(deck=deck)
    DeckSummary.objects.filter(pk=row.pk).update(status=DeckSummary.Status.DONE)

    with translation.override("fr"):
        assert not summary.due(owner, deck)
        _start(owner, deck)

    row.refresh_from_db()
    assert (row.status, row.language) == (DeckSummary.Status.DONE, "de")


def test_the_task_asks_in_the_rows_language(key, deck, monkeypatch):
    row = DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                                     status=DeckSummary.Status.PENDING, language="ja")
    sent = []

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return _Response(_answer(GOOD))

    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)

    # The worker runs in English; the row decides.
    assert tasks.write_summary(str(row.pk)) == DeckSummary.Status.DONE
    assert "Japanese, in the polite style" in sent[0]["messages"][0]["content"]


def test_sentences_sent_as_a_list_are_one_paragraph():
    """2 of 8 Spanish and Portuguese answers sent "tactics" this way."""
    answer = dict(GOOD, tactics=["Ramp first.", "Then **drain**."], feel=["Slow.", "Patient."])

    checked = summary.parse(json.dumps(answer))

    assert checked["tactics"] == "Ramp first. Then drain."
    assert checked["feel"] == "Slow. Patient."
    with pytest.raises(ValueError):
        summary.parse(json.dumps(dict(GOOD, tactics=["Ramp.", 3])))


def test_a_long_japanese_text_is_cut_near_the_limit():
    """No spaces between words: the only space is inside a card name."""
    text = "Sol Ringを" + "で" * 600

    feel = summary.parse(json.dumps(dict(GOOD, feel=text)))["feel"]

    assert len(feel) == summary.FEEL_MAX + 1
    assert feel.endswith("で…")


def test_the_text_keeps_its_own_language_on_the_page(key, client, owner, deck, finished):
    DeckSummary.objects.create(deck=deck, fingerprint=summary.fingerprint(deck),
                               status=DeckSummary.Status.DONE, language="de",
                               content=summary.parse(json.dumps(GOOD)))

    body = _page(client, owner, finished)
    block = body[body.index('id="summary"'):body.index('id="advanced"')]

    assert '<p class="max-w-3xl text-ink-900" lang="de">A graveyard deck' in block
    assert block.count('lang="de"') == 6
