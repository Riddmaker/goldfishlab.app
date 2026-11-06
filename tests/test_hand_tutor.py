"""The hand tutor: "Which plan does this hand have?" (Paket 5, PR 21).

A seven from the deck, kept and played a few hundred times; per theme the
cards in hand and how often its engine runs by turn six; one plan. Rules, no
model: the same hand gives the same answer.
"""

import random
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.urls import reverse

from cards.themes import Found, Part
from decks import tutor
from decks.tasks import read_hand
from simulation import analysis
from simulation import combos as engine_combos
from simulation.fixtures import chainer
from simulation.game import Game
from tests.test_synergy import client_in, deck, owner  # noqa: F401

HAND = [
    "Gray Merchant of Asphodel",
    "Swamp",
    "Swamp",
    "Phyrexian Tower",
    "Mind Stone",
    "Fleshbag Marauder",
    "Nekrataal",
]


# --- 1. the engine keeps a given hand ----------------------------------------------


def test_a_given_hand_is_kept_and_the_rest_is_the_library():
    game = Game(random.Random(3), deck=chainer.DECK)
    game.take_given_hand(HAND)

    assert [card.name for card in game.hand] == HAND
    assert len(game.library) == len(chainer.DECK.library) - 7
    assert sum(card.name == "Swamp" for card in game.library) == 31 - 2
    assert game.mulligans == 0 and game.first_hand_lands == 3
    assert game.drawn == game.hand


def test_a_card_the_deck_has_not_got_is_refused():
    game = Game(random.Random(3), deck=chainer.DECK)
    with pytest.raises(ValueError, match="Mind Stone"):
        game.take_given_hand(["Mind Stone", "Mind Stone"])


def test_a_game_from_a_given_hand_starts_with_it():
    measured = analysis.simulate_game(random.Random(1), turns=2, deck=chainer.DECK, hand=HAND)

    assert measured["opening_hand"] == HAND and measured["mulligans"] == 0


def test_the_deal_is_the_playtests():
    game = Game(random.Random(5), deck=chainer.DECK)
    game.draw(7)

    assert tutor.deal(chainer.DECK, 5) == [card.name for card in game.hand] == HAND


# --- 2. playing the hand --------------------------------------------------------------


def _watch(theme, *names):
    role = engine_combos.AnyOf(
        tuple(engine_combos.Requirement(name=name, zones=("B",)) for name in names)
    )
    return engine_combos.Watch(key=f"engine:{theme}", requirements=(role,), combo=False)


def test_play_counts_the_games_each_engine_runs_in():
    watches = (_watch("drain", "Gray Merchant of Asphodel"), _watch("never", "No Such Card"))

    running = tutor.play(chainer.DECK, HAND, watches, seed=5, games=40)

    assert set(running) == {"drain", "never"}
    assert running["never"] == 0
    assert 0 < running["drain"] <= 40
    assert tutor.play(chainer.DECK, HAND, watches, seed=5, games=40) == running, (
        "same hand, same answer"
    )


# --- 3. the reading ---------------------------------------------------------------------


def _found(theme, label, cards):
    return Found(
        theme, label, "", "strong", 0.9, (Part("core", "Core", len(cards), 4, tuple(cards)),)
    )


@pytest.fixture
def two_themes(monkeypatch):
    """Graveyard leads the deck, Lifegain second; the hand holds Lifegain."""
    found = [
        _found("graveyard", "Graveyard", ["Reanimate", "Entomb"]),
        _found("lifegain", "Lifegain", ["Gray Merchant of Asphodel", "Mind Stone"]),
    ]
    monkeypatch.setattr(tutor, "theme_cards", lambda _deck: [])
    monkeypatch.setattr(
        tutor, "deck_reading", lambda _deck, cards=None: SimpleNamespace(themes=found)
    )


def _answer(running, games=300, **extra):
    return {
        "hand": HAND,
        "games": games,
        "running": running,
        "keeps": True,
        "lands": 3,
        "mana": 4,
        "costs": {
            "Gray Merchant of Asphodel": 5,
            "Mind Stone": 2,
            "Fleshbag Marauder": 3,
            "Nekrataal": 4,
        },
        **extra,
    }


def test_the_plan_is_the_engine_this_hand_runs_most(two_themes):
    reading = tutor.explain(None, _answer({"graveyard": 66, "lifegain": 138}))

    assert reading.plan == ("lifegain",)
    assert reading.main.theme == "graveyard"
    rows = {row.theme: row for row in reading.rows}
    assert rows["lifegain"].cards == ("Gray Merchant of Asphodel", "Mind Stone")
    assert rows["lifegain"].early == ("Mind Stone",), "Gray Merchant costs 5: not early"
    assert rows["graveyard"].cards == ()
    assert rows["lifegain"].share == "46%" and rows["graveyard"].share == "22%"
    assert reading.sentences == (
        "Your deck is built mainly around Graveyard.",
        "This hand holds no card for it.",
        "With this hand Lifegain runs by turn 6 in 46% of the games, Graveyard in 22%.",
        "Plan: build Lifegain first; Graveyard comes later.",
    )


def test_the_decks_own_theme_leads_when_it_is_a_plan(two_themes):
    reading = tutor.explain(None, _answer({"graveyard": 140, "lifegain": 138}))

    assert set(reading.plan) == {"graveyard", "lifegain"}, "within five points: both"
    assert reading.sentences[-2:] == (
        "Plan: Graveyard.",
        "Lifegain is about as likely: 300 games cannot tell them apart.",
    )
    assert set(reading.answers) == {"graveyard", "lifegain"}


def test_no_engine_running_often_is_no_plan(two_themes):
    reading = tutor.explain(None, _answer({"graveyard": 20, "lifegain": 30}))

    assert reading.plan == () and reading.answers == (tutor.NONE,)
    assert reading.sentences[-1].startswith("No engine runs by turn 6 in as many as 15%")


def test_a_deck_whose_engines_cannot_run_is_read_without_a_plan(two_themes):
    reading = tutor.explain(None, _answer({}, games=0))

    assert not reading.measured and reading.answers == (tutor.NONE,)
    assert all(row.share is None for row in reading.rows)
    assert "cannot measure a plan" in reading.sentences[-1]


def test_a_deck_without_a_theme_says_so(monkeypatch):
    monkeypatch.setattr(tutor, "theme_cards", lambda _deck: [])
    monkeypatch.setattr(tutor, "deck_reading", lambda _deck, cards=None: SimpleNamespace(themes=[]))

    reading = tutor.explain(None, _answer({}))

    assert reading.main is None and len(reading.sentences) == 1
    assert "finds no theme" in reading.sentences[0]


def test_the_usual_share_is_the_decks_last_run(two_themes):
    run = SimpleNamespace(
        result={"engines": {"lifegain": {"games": 100, "by_turn": [0] * 5 + [31]}}}
    )

    rows = {row.theme: row for row in tutor.explain(None, _answer({"lifegain": 138}), run).rows}

    assert rows["lifegain"].usual == "31%" and rows["graveyard"].usual is None


# --- 4. measuring, the worker and the cache -------------------------------------------------


@pytest.fixture
def empty_cache():
    cache.clear()
    yield
    cache.clear()


def test_measure_keeps_its_answer_for_the_page(deck, empty_cache):  # noqa: F811
    answer = tutor.measure(deck, 7, True)

    assert len(answer["hand"]) == 7
    assert 0 <= answer["lands"] <= 7 and isinstance(answer["keeps"], bool)
    assert tutor.cached(deck, 7, True) == answer
    assert tutor.cached(deck, 8, True) is None


def test_a_changed_deck_asks_again(deck, empty_cache):  # noqa: F811
    tutor.measure(deck, 7, True)
    deck.entries.filter(oracle_card__name="Swamp").update(quantity=1)

    assert tutor.cached(deck, 7, True) is None


def test_the_worker_skips_a_deleted_deck(db):
    assert read_hand("00000000-0000-0000-0000-000000000000", 1, True) is False


def test_asking_twice_sends_one_job(deck, empty_cache, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr("decks.tasks.read_hand.delay", lambda *args: sent.append(args))

    tutor.ask(deck, 9, True)
    tutor.ask(deck, 9, True)

    assert sent == [(str(deck.pk), 9, True)]


# --- 5. the page -----------------------------------------------------------------------------


def _url(target, **query):
    url = reverse("decks:tutor", args=[target.pk])
    return url + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


def test_without_a_hand_one_is_dealt(client_in, deck):  # noqa: F811
    response = client_in.get(_url(deck, step=2))

    assert response.status_code == 302
    assert "hand=" in response["Location"] and "step=2" in response["Location"]


def test_an_unplayed_hand_polls(client_in, deck, empty_cache, monkeypatch):  # noqa: F811
    asked = []
    monkeypatch.setattr(tutor, "ask", lambda *args: asked.append(args))

    response = client_in.get(_url(deck, hand=11))

    page = response.content.decode()
    assert 'hx-trigger="every 1s"' in page and 'aria-busy="true"' in page
    assert asked and asked[0][1:] == (11, True)


@pytest.fixture
def played(deck, empty_cache, monkeypatch, two_themes):  # noqa: F811
    monkeypatch.setattr(tutor, "cached", lambda *args: _answer({"graveyard": 66, "lifegain": 138}))
    return deck


def test_step_one_shows_the_plan(client_in, played):  # noqa: F811
    page = client_in.get(_url(played, hand=11)).content.decode()

    assert "Plan: build Lifegain first; Graveyard comes later." in page
    assert "data-tutor-question" not in page
    assert page.count('class="hand-slot"') == 7


def test_step_two_asks_with_help(client_in, played):  # noqa: F811
    page = client_in.get(_url(played, hand=11, step=2)).content.decode()

    assert "data-tutor-question" in page and "data-tutor-help" in page
    assert "Plan:" not in page and "46%" not in page
    assert 'value="lifegain"' in page and 'value="none"' in page and 'value="show"' in page


def test_step_three_asks_without_help(client_in, played):  # noqa: F811
    page = client_in.get(_url(played, hand=11, step=3)).content.decode()

    assert "data-tutor-question" in page and "data-tutor-help" not in page


@pytest.mark.parametrize(
    "guess, verdict",
    [
        ("lifegain", "Right."),
        ("graveyard", "Not this time."),
        ("none", "Not this time."),
        ("show", None),
    ],
)
def test_a_guess_shows_the_answer_with_a_word_on_it(client_in, played, guess, verdict):  # noqa: F811
    page = client_in.get(_url(played, hand=11, step=3, guess=guess)).content.decode()

    assert "Plan: build Lifegain first" in page
    assert ("data-tutor-verdict" in page) is (verdict is not None)
    if verdict:
        assert verdict in page


def test_an_unknown_guess_is_ignored(client_in, played):  # noqa: F811
    page = client_in.get(_url(played, hand=11, step=2, guess="<b>x</b>")).content.decode()

    assert "data-tutor-question" in page and "&lt;b&gt;" not in page and "<b>x</b>" not in page


@pytest.mark.parametrize("query", [{"hand": "-1"}, {"hand": "abc"}, {"step": "4"}])
def test_a_bad_address_is_refused(client_in, deck, query):  # noqa: F811
    assert client_in.get(_url(deck, **query)).status_code == 400


def test_someone_elses_deck_is_not_found(client, deck, django_user_model):  # noqa: F811
    other = django_user_model.objects.create_user(
        email="other@example.com", password="pw-for-test-only"
    )
    client.force_login(other)

    assert client.get(_url(deck, hand=1)).status_code == 404


def test_the_deck_page_links_the_tutor(client_in, deck):  # noqa: F811
    page = client_in.get(deck.get_absolute_url()).content.decode()

    assert f'href="{reverse("decks:tutor", args=[deck.pk])}"' in page
