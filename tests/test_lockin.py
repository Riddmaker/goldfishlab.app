"""Themen-Doc, Paket 1, PR 4: the owner locks or dismisses engines and pairs.

What matters:

1. **Only the owner's word is stored**: a suggestion is read live, a lock
   or a dismissal is a row keyed by the cards, so a new reading only adds.
2. **Locked stays, dismissed does not come back**: a locked entry is first
   and is there even when the deck no longer shows it by itself; a
   dismissed one waits in a fold and can be brought back.
3. **A card that leaves the deck makes its entry rest**, and the entry is
   back when the card is.
4. **The button can only name what the page named**: a key the reading
   does not hold stores nothing; other people's decks are not found.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cards import synergy
from cards.themes import STRONG
from decks.models import Deck, DeckMark
from decks.themes import PAIRS_SHOWN, Reading, deck_reading, merge
from tests.test_synergy import client_in, deck, owner  # noqa: F401

SAC, DEATH = "%(card)s sacrifices", "%(card)s pays off each death"


def _engine(ids=("a", "b")):
    return synergy.Engine("aristocrats", "Aristocrats", STRONG,
                          (("Viscera Seer", SAC), ("Blood Artist", DEATH)), (),
                          ("Blood Artist",), ((ids[0], "outlet"), (ids[1], "payoff")))


def _pair(n):
    return synergy.Pair("aristocrats", "Viscera Seer", f"Payoff {n}", (SAC, DEATH),
                        1.0, 0.5, 0.8, 0.9, (("a", "outlet"), (f"p{n}", "payoff")))


def _mark(item, state, pk="m1"):
    return SimpleNamespace(pk=pk, kind=item.kind, theme=item.theme, key=item.key, state=state,
                           cards=[list(part) for part in item.parts])


NAMES = {"a": "Viscera Seer", "b": "Blood Artist", **{f"p{n}": f"Payoff {n}" for n in range(9)}}


def _merge(engines=(), pairs=(), marks=(), present=None):
    present = set(NAMES) if present is None else present
    return merge(["aristocrats"], list(engines), list(pairs), list(marks), NAMES, present)


# --- 1. the key ---------------------------------------------------------------------


def test_the_key_does_not_hang_on_the_order_of_the_cards():
    assert _engine(("a", "b")).key == _engine(("b", "a")).key == "engine:aristocrats:a,b"
    assert _pair(0).key == "pair:aristocrats:a,p0"


# --- 2. locked stays, dismissed does not come back ----------------------------------


def test_a_locked_engine_comes_first_and_says_so():
    other = _engine(("c", "d"))
    reading = _merge(engines=[other, _engine()], marks=[_mark(_engine(), synergy.LOCKED)])

    assert [engine.key for engine in reading.engines] == [_engine().key, other.key]
    assert reading.engines[0].state == synergy.LOCKED
    assert reading.engines[0].mark == "m1"
    assert reading.engines[0].level == STRONG


def test_a_locked_engine_stays_when_the_deck_no_longer_shows_it():
    reading = _merge(marks=[_mark(_engine(), synergy.LOCKED)])

    engine = reading.engines[0]
    assert engine.cards == ("Viscera Seer", "Blood Artist")
    assert engine.sentence == "Viscera Seer sacrifices, Blood Artist pays off each death."
    assert engine.level == ""
    assert engine.gone == ()


def test_a_dismissed_entry_leaves_the_list_and_waits_to_be_brought_back():
    reading = _merge(engines=[_engine()], pairs=[_pair(0), _pair(1)],
                     marks=[_mark(_engine(), synergy.DISMISSED),
                            _mark(_pair(0), synergy.DISMISSED, pk="m2")])

    assert reading.engines == []
    assert [pair.key for pair in reading.pairs] == [_pair(1).key]
    assert [item.key for item in reading.dismissed] == [_engine().key, _pair(0).key]
    assert reading.engines_dismissed


def test_a_locked_pair_is_open_and_takes_no_suggestion_s_place():
    every = [_pair(n) for n in range(PAIRS_SHOWN + 2)]
    reading = _merge(pairs=every, marks=[_mark(_pair(PAIRS_SHOWN + 1), synergy.LOCKED)])

    assert reading.pairs[0].key == _pair(PAIRS_SHOWN + 1).key
    assert reading.pairs[0].state == synergy.LOCKED
    assert len(reading.pairs) == 1 + PAIRS_SHOWN
    assert [pair.key for pair in reading.more_pairs] == [_pair(PAIRS_SHOWN).key]


def test_a_locked_entry_is_never_also_a_suggestion():
    reading = _merge(engines=[_engine()], marks=[_mark(_engine(), synergy.LOCKED)])

    assert len(reading.engines) == 1
    assert reading.suggestion(_engine().key) is None


# --- 3. a missing card --------------------------------------------------------------


def test_an_entry_whose_card_left_rests_and_names_it():
    reading = _merge(marks=[_mark(_engine(), synergy.LOCKED)], present={"a"})

    assert reading.engines[0].gone == ("Blood Artist",)


def test_a_rebuilt_pair_has_no_numbers():
    pair = synergy.rebuild(synergy.PAIR, "aristocrats", [["a", "outlet"], ["p1", "payoff"]],
                           NAMES)

    assert pair.cards == ("Viscera Seer", "Payoff 1")
    assert pair.score is None


@pytest.mark.parametrize(("kind", "theme", "parts"), [
    (synergy.ENGINE, "not-a-theme", [["a", "outlet"]]),
    (synergy.PAIR, "aristocrats", [["a", "outlet"]]),
    (synergy.ENGINE, "aristocrats", [["a", "not-a-piece"]]),
])
def test_what_the_catalogue_no_longer_knows_is_not_shown(kind, theme, parts):
    assert synergy.rebuild(kind, theme, parts, NAMES) is None


def test_a_rebuilt_engine_names_the_pieces_it_never_had():
    engine = synergy.rebuild(synergy.ENGINE, "aristocrats", [["b", "payoff"]], NAMES)

    assert engine.cards == ("Blood Artist",)
    assert "Sacrifice outlet" in engine.missing
    assert len(engine.missing) == 2


# --- the page and the button --------------------------------------------------------


def _key(deck):  # noqa: F811
    return deck_reading(deck).engines[0].key


def _post(client, deck, htmx=True, **data):  # noqa: F811
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(f"/decks/{deck.pk}/marks/", data, headers=headers)


@pytest.mark.django_db
def test_a_lock_is_stored_and_the_section_comes_back(client_in, deck):  # noqa: F811
    key = _key(deck)

    response = _post(client_in, deck, action="lock", key=key)

    assert response.status_code == 200
    body = response.content.decode()
    assert body.lstrip().startswith('<div id="engines"')
    assert 'data-state="locked"' in body
    assert "Unlock" in body
    mark = DeckMark.objects.get(deck=deck)
    assert (mark.key, mark.state, mark.kind, mark.theme) == (key, "locked", "engine",
                                                              "reanimator")
    assert mark.cards == [[key.split(":")[2], "reanimate"]]


@pytest.mark.django_db
def test_twice_is_once(client_in, deck):  # noqa: F811
    key = _key(deck)
    _post(client_in, deck, action="lock", key=key)
    _post(client_in, deck, action="lock", key=key)

    assert DeckMark.objects.filter(deck=deck).count() == 1


@pytest.mark.django_db
def test_dismissed_waits_in_the_fold_and_comes_back(client_in, deck):  # noqa: F811
    key = _key(deck)

    body = _post(client_in, deck, action="dismiss", key=key).content.decode()

    assert "1 dismissed" in body
    assert "No engine left: you dismissed the ones we found." in body
    assert deck_reading(deck).engines == []

    mark = DeckMark.objects.get(deck=deck)
    body = _post(client_in, deck, action="undo", mark=mark.pk).content.decode()

    assert "dismissed" not in body
    assert not DeckMark.objects.exists()
    assert deck_reading(deck).engines[0].key == key


@pytest.mark.django_db
def test_without_scripting_the_form_goes_back_to_the_engines(client_in, deck):  # noqa: F811
    response = _post(client_in, deck, htmx=False, action="lock", key=_key(deck))

    assert response.status_code == 302
    assert response.url == f"{deck.get_absolute_url()}#engines"


@pytest.mark.django_db
def test_a_card_that_leaves_makes_the_engine_rest_until_it_is_back(client_in, deck):  # noqa: F811
    _post(client_in, deck, action="lock", key=_key(deck))
    commander = deck.commander_id

    Deck.objects.filter(pk=deck.pk).update(commander=None)
    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "Resting, a card is missing: Chainer, Dementia Master." in body
    assert 'data-state="locked"' in body

    Deck.objects.filter(pk=deck.pk).update(commander=commander)
    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "Resting" not in body
    assert 'data-state="locked"' in body


@pytest.mark.django_db
def test_a_key_the_page_did_not_show_stores_nothing(client_in, deck):  # noqa: F811
    response = _post(client_in, deck, action="lock",
                     key="engine:aristocrats:00000000-0000-0000-0000-000000000000")

    assert response.status_code == 200
    assert not DeckMark.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("data", [
    {"action": "burn", "key": "x"},
    {"action": "lock"},
    {"action": "undo", "mark": "not-a-uuid"},
])
def test_a_malformed_post_is_refused(client_in, deck, data):  # noqa: F811
    assert _post(client_in, deck, **data).status_code == 400
    assert not DeckMark.objects.exists()


@pytest.mark.django_db
def test_another_owner_s_deck_is_not_found(client, deck):  # noqa: F811
    stranger = get_user_model().objects.create_user(email="stranger@example.com",
                                                    password="pw-for-test-only")
    client.force_login(stranger)

    assert _post(client, deck, action="lock", key=_key(deck)).status_code == 404
    assert not DeckMark.objects.exists()


@pytest.mark.django_db
def test_signed_out_is_sent_to_sign_in(client, deck):  # noqa: F811
    response = _post(client, deck, action="lock", key=_key(deck))

    assert response.status_code == 302
    assert "login" in response.url
    assert not DeckMark.objects.exists()


@pytest.mark.django_db
def test_another_deck_s_mark_cannot_be_undone(client_in, deck, owner):  # noqa: F811
    _post(client_in, deck, action="lock", key=_key(deck))
    mark = DeckMark.objects.get()
    other = Deck.objects.create(owner=owner, name="Other")

    _post(client_in, other, action="undo", mark=mark.pk)

    assert DeckMark.objects.filter(pk=mark.pk).exists()


@pytest.mark.django_db
def test_the_page_shows_the_buttons(client_in, deck):  # noqa: F811
    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert f'action="/decks/{deck.pk}/marks/"' in body
    assert 'aria-label="Lock this engine: it stays, whatever the list becomes"' in body
    assert 'aria-label="Dismiss this engine: it will not be suggested again"' in body


@pytest.mark.django_db
def test_a_locked_pair_is_stored_with_its_pieces(client_in, deck, monkeypatch):  # noqa: F811
    monkeypatch.setattr("decks.views.deck_reading",
                        lambda _deck: Reading(themes=["aristocrats"], pairs=[_pair(0)]))

    _post(client_in, deck, htmx=False, action="lock", key=_pair(0).key)

    mark = DeckMark.objects.get()
    assert (mark.kind, mark.cards) == ("pair", [["a", "outlet"], ["p0", "payoff"]])


@pytest.mark.django_db
def test_marks_cost_one_query_and_a_missing_card_one_more(client_in, deck):  # noqa: F811
    cache.delete("themes:tag-sizes:v1")
    _post(client_in, deck, action="lock", key=_key(deck))

    with CaptureQueriesContext(connection) as queries:
        deck_reading(deck)
    assert len(queries) == 3

    Deck.objects.filter(pk=deck.pk).update(commander=None)
    deck.refresh_from_db()
    with CaptureQueriesContext(connection) as queries:
        deck_reading(deck)
    assert len(queries) == 4
