"""Keep or mulligan? (Paket 5, PR 25).

A seven from the player's own deck, kept or sent back; the simulation plays
both a thousand times and says which started better. Every answer is stored
from the first day, with the hand and a few facts, never the deck list.
"""

import random
import time

import pytest
from django.core.cache import cache
from django.urls import reverse

from accounts import privacy
from decks import mulligan
from decks.models import MulliganDecision
from decks.tasks import read_mulligan
from simulation import ENGINE_VERSION, analysis
from simulation.fixtures import chainer
from simulation.game import Game
from simulations.engine import adapter
from tests.test_synergy import client_in, deck, owner  # noqa: F401

# --- 1. the engine: a game after a seven went back ---------------------------------


def test_a_game_without_mulligans_taken_deals_as_before():
    before = Game(random.Random(11), deck=chainer.DECK)
    before.take_opening_hand()
    now = Game(random.Random(11), deck=chainer.DECK)
    now.take_opening_hand(0)

    assert [card.name for card in now.hand] == [card.name for card in before.hand]
    assert now.mulligans == before.mulligans


def test_a_mulligan_taken_deals_a_fresh_seven_under_the_second_rule():
    for seed in range(40):
        game = Game(random.Random(seed), deck=chainer.DECK)
        game.take_opening_hand(1)

        assert game.mulligans >= 1
        if game.mulligans == 1:
            assert len(game.hand) == 7, "the first mulligan is free: nothing to the bottom"
            assert game.keepable(game.hand)


def test_simulate_game_takes_the_mulligans_already_taken():
    measured = analysis.simulate_game(random.Random(2), turns=2, deck=chainer.DECK, mulligans=1)

    assert measured["mulligans"] >= 1


# --- 2. the hands of a round -----------------------------------------------------------


def test_a_round_deals_the_same_hands_again():
    seeds = [mulligan.hand_seed(chainer.DECK, 77, n) for n in range(1, 11)]

    assert seeds == [mulligan.hand_seed(chainer.DECK, 77, n) for n in range(1, 11)]
    assert len(set(seeds)) == 10
    assert seeds != [mulligan.hand_seed(chainer.DECK, 78, n) for n in range(1, 11)]


def test_every_third_hand_is_borderline():
    for round_seed in range(5):
        for position in (3, 6, 9):
            seed = mulligan.hand_seed(chainer.DECK, round_seed, position)
            cards = mulligan.deal(chainer.DECK, seed)
            assert sum(card.is_land for card in cards) in mulligan.BORDERLINE_LANDS


def test_on_the_play_is_drawn_with_the_hand():
    assert mulligan.on_the_play(4) is True and mulligan.on_the_play(5) is False


def test_the_deal_is_the_playtests():
    game = Game(random.Random(5), deck=chainer.DECK)
    game.draw(7)

    assert mulligan.deal(chainer.DECK, 5) == game.hand


# --- 3. playing both sides ---------------------------------------------------------------


def _turns(mana, built, lands=4):
    land = chainer.DECK.library[[c.is_land for c in chainer.DECK.library].index(True)]
    spell = next(card for card in chainer.DECK.library if not card.is_land)
    turn = {"mana": mana, "battlefield": [land] * lands + [spell] * built}
    return {"per_turn": [turn] * 6}


@pytest.mark.parametrize("mana, built, good", [(4, 2, True), (3, 2, False), (4, 1, False),
                                               (6, 5, True)])
def test_a_good_start_is_four_mana_and_two_permanents_by_turn_four(mana, built, good):
    assert mulligan.good_start(_turns(mana, built)) is good


def test_play_counts_both_sides_the_same_each_time():
    hand = [card.name for card in mulligan.deal(chainer.DECK, 5)]

    sides = mulligan.play(chainer.DECK, hand, (), seed=5, games=20)

    assert set(sides) == {mulligan.KEEP, mulligan.MULLIGAN}
    assert all(0 <= side["good"] <= 20 and side["engines"] == 0 for side in sides.values())
    assert mulligan.play(chainer.DECK, hand, (), seed=5, games=20) == sides


# --- 4. the verdict ----------------------------------------------------------------------


@pytest.mark.parametrize("keep, sent, best", [
    (900, 500, mulligan.KEEP),
    (200, 600, mulligan.MULLIGAN),
    (500, 480, mulligan.CLOSE),       # inside the 95 % margin
    (990, 965, mulligan.CLOSE),       # outside the margin, but under three points
])
def test_the_verdict(keep, sent, best):
    assert mulligan.verdict(keep, sent, 1000).best == best


def test_close_counts_either_answer_right():
    close = mulligan.verdict(500, 490, 1000)
    kept = mulligan.verdict(900, 500, 1000)

    assert close.right("keep") and close.right("mulligan")
    assert kept.right("keep") and not kept.right("mulligan")
    assert kept.difference == pytest.approx(40)
    assert kept.keep_margin == pytest.approx(1.86, abs=0.01), "1.96 · √(0.9 · 0.1 / 1000)"


# --- 5. facts and the signed note ---------------------------------------------------------


def test_the_hand_facts():
    cards = mulligan.deal(chainer.DECK, 5)

    facts = mulligan.hand_facts(cards)

    assert facts["lands"] == sum(card.is_land for card in cards)
    assert len(facts["mana_values"]) == 7 - facts["lands"]


def test_the_note_measures_the_time_and_refuses_another_hand(settings):
    note = mulligan.shown_note(1, 2, 3)

    assert 0 <= mulligan.read_note(note, 1, 2, 3) < 5000
    assert mulligan.read_note(note, 1, 2, 4) is None, "another hand"
    assert mulligan.read_note(note + "x", 1, 2, 3) is None, "forged"


def test_an_old_note_is_refused(monkeypatch):
    note = mulligan.shown_note(1, 2, 3)
    later = time.time() + mulligan.SHOWN_MAX_AGE + 10
    monkeypatch.setattr("django.core.signing.time.time", lambda: later)

    assert mulligan.read_note(note, 1, 2, 3) is None


# --- 6. storing and judging ----------------------------------------------------------------


@pytest.fixture
def empty_cache():
    cache.clear()
    yield
    cache.clear()


def _decide(deck, owner, position=1, choice="keep", **extra):  # noqa: F811
    definition = adapter.convert(deck).definition
    seed = mulligan.hand_seed(definition, 99, position)
    return mulligan.decide(owner, deck, definition, round_seed=99, position=position, seed=seed,
                           choice=choice, sure=extra.get("sure"),
                           waited_ms=extra.get("waited_ms", 4200))


def _answer(decision, keep=900, sent=500):
    return {"hand": decision.hand, "games": 1000, "engines": False,
            "sides": {"keep": {"good": keep, "engines": 0},
                      "mulligan": {"good": sent, "engines": 0}}}


def test_a_decision_keeps_the_hand_and_facts_not_the_list(deck, owner):  # noqa: F811
    decision = _decide(deck, owner, sure=3, waited_ms=10**9)

    assert len(decision.hand) == 7 and decision.engine_version == ENGINE_VERSION
    assert decision.deck_facts["cards"] > 7 and "name" not in decision.deck_facts
    assert set(decision.deck_facts) == {"cards", "lands", "accelerants", "commanders",
                                        "commander_mana_values", "colors"}
    assert decision.decision_ms == MulliganDecision.SLOWEST_MS, "capped"
    assert decision.sure == 3 and decision.verdict == "" and decision.right is None


def test_a_hand_keeps_its_first_answer(deck, owner):  # noqa: F811
    first = _decide(deck, owner, choice="keep")
    again = _decide(deck, owner, choice="mulligan")

    assert again.pk == first.pk and again.choice == "keep"
    assert MulliganDecision.objects.count() == 1


def test_settle_writes_the_verdict_once(deck, owner):  # noqa: F811
    decision = _decide(deck, owner, choice="mulligan")

    assert mulligan.settle(decision, _answer(decision)).best == mulligan.KEEP
    decision.refresh_from_db()
    assert decision.verdict == "keep" and decision.right is False
    assert decision.result["sides"]["keep"]["good"] == 900

    mulligan.settle(decision, _answer(decision, keep=100))
    decision.refresh_from_db()
    assert decision.verdict == "keep", "the first verdict stands"


def test_an_answer_about_another_hand_is_not_a_verdict(deck, owner):  # noqa: F811
    decision = _decide(deck, owner)

    assert mulligan.settle(decision, {**_answer(decision), "hand": ["Swamp"] * 7}) is None


def test_the_record_counts_by_how_sure(deck, owner):  # noqa: F811
    for position, (choice, sure, keep) in enumerate(
            [("keep", 3, 900), ("mulligan", 3, 900), ("keep", 1, 500)], start=1):
        decision = _decide(deck, owner, position=position, choice=choice, sure=sure)
        mulligan.settle(decision, _answer(decision, keep=keep, sent=500))

    record = mulligan.record(owner)

    assert (record["all"].decided, record["all"].right, record["all"].close) == (3, 2, 1)
    assert record["by_sure"][3].decided == 2 and record["by_sure"][3].right == 1
    assert record["by_sure"][2].decided == 0


def test_a_deleted_deck_leaves_the_decision_without_it(deck, owner):  # noqa: F811
    decision = _decide(deck, owner)
    deck.delete()

    decision.refresh_from_db()
    assert decision.deck is None and decision.hand


def test_a_deleted_account_takes_its_decisions(deck, owner):  # noqa: F811
    _decide(deck, owner)
    owner.delete()

    assert not MulliganDecision.objects.exists()


def test_the_export_holds_the_decisions(deck, owner):  # noqa: F811
    _decide(deck, owner)

    assert len(privacy.export(owner)["mulligan_decisions"]) == 1


def test_measure_keeps_its_answer_for_the_page(deck, empty_cache, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mulligan, "GAMES", 10)

    answer = mulligan.measure(deck, 7)

    assert len(answer["hand"]) == 7 and answer["games"] == 10
    assert mulligan.cached(deck, 7) == answer


def test_the_worker_skips_a_deleted_deck(db):
    assert read_mulligan("00000000-0000-0000-0000-000000000000", 1) is False


def test_asking_twice_sends_one_job(deck, empty_cache, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr("decks.tasks.read_mulligan.delay", lambda *args: sent.append(args))

    mulligan.ask(deck, 9)
    mulligan.ask(deck, 9)

    assert sent == [(str(deck.pk), 9)]


# --- 7. the page -----------------------------------------------------------------------------


def _url(target, **query):
    url = reverse("decks:mulligan", args=[target.pk])
    return url + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


@pytest.fixture
def no_worker(monkeypatch):
    asked = []
    monkeypatch.setattr(mulligan, "ask", lambda *args: asked.append(args))
    return asked


def test_without_a_round_one_starts(client_in, deck):  # noqa: F811
    response = client_in.get(_url(deck))

    assert response.status_code == 302
    assert "round=" in response["Location"] and "n=1" in response["Location"]


def test_a_hand_asks_the_question_and_starts_the_worker(client_in, deck, empty_cache, no_worker):  # noqa: F811
    page = client_in.get(_url(deck, round=99, n=2)).content.decode()

    assert "data-mulligan-question" in page and 'name="shown"' in page
    assert "csrfmiddlewaretoken" in page and "data-mulligan-stored" in page
    assert page.count('class="hand-slot"') == 7
    assert "Hand 2 of 10" in page
    assert no_worker, "the worker plays the hand while the player thinks"


def _post(client, target, choice="keep", round_seed=99, n=1, **extra):
    page = client.get(_url(target, round=round_seed, n=n)).content.decode()
    shown = page.split('name="shown" value="')[1].split('"')[0]
    return client.post(_url(target, round=round_seed, n=n),
                       {"choice": choice, "shown": shown, **extra})


def test_an_answer_is_stored_and_shows_the_verdict(client_in, deck, owner, empty_cache,  # noqa: F811
                                                    no_worker, monkeypatch):
    response = _post(client_in, deck, "mulligan", sure="2")

    assert response.status_code == 302
    decision = MulliganDecision.objects.get()
    assert decision.choice == "mulligan" and decision.sure == 2 and decision.user == owner

    waiting = client_in.get(_url(deck, round=99, n=1)).content.decode()
    assert 'hx-trigger="every 1s"' in waiting and "data-mulligan-question" not in waiting

    monkeypatch.setattr(mulligan, "cached", lambda *args: _answer(decision))
    page = client_in.get(_url(deck, round=99, n=1)).content.decode()
    assert "Keeping was better (+40 points)." in page and "Not this time." in page
    assert "90% ± 2" in page and "data-mulligan-next" in page
    decision.refresh_from_db()
    assert decision.verdict == "keep"


def test_a_judged_hand_reads_its_stored_answer(client_in, deck, empty_cache, no_worker,  # noqa: F811
                                               monkeypatch):
    _post(client_in, deck, "keep")
    decision = MulliganDecision.objects.get()
    mulligan.settle(decision, _answer(decision, keep=500, sent=495))
    monkeypatch.setattr(mulligan, "cached", lambda *args: None)

    page = client_in.get(_url(deck, round=99, n=1)).content.decode()

    assert "Close: both are fine." in page and "Right." in page


@pytest.mark.parametrize("data", [
    {"choice": "keep", "shown": "forged"},
    {"choice": "fold", "shown": None},
    {"choice": "keep", "shown": None, "sure": "9"},
])
def test_a_bad_answer_is_refused(client_in, deck, empty_cache, no_worker, data):  # noqa: F811
    page = client_in.get(_url(deck, round=99, n=1)).content.decode()
    shown = page.split('name="shown" value="')[1].split('"')[0]
    data = {**data, "shown": data["shown"] or shown}

    assert client_in.post(_url(deck, round=99, n=1), data).status_code == 400
    assert not MulliganDecision.objects.exists()


def test_a_note_from_another_hand_is_refused(client_in, deck, empty_cache, no_worker):  # noqa: F811
    page = client_in.get(_url(deck, round=99, n=1)).content.decode()
    shown = page.split('name="shown" value="')[1].split('"')[0]

    response = client_in.post(_url(deck, round=99, n=2), {"choice": "keep", "shown": shown})

    assert response.status_code == 400


@pytest.mark.parametrize("query", [{"round": "-1"}, {"round": "x"}, {"n": "0"}, {"n": "12"}])
def test_a_bad_address_is_refused(client_in, deck, query):  # noqa: F811
    assert client_in.get(_url(deck, **query)).status_code == 400


def test_the_end_of_a_round_counts_it(client_in, deck, owner, empty_cache, no_worker):  # noqa: F811
    for position, (choice, keep) in enumerate([("keep", 900), ("keep", 500)], start=1):
        decision = _decide(deck, owner, position=position, choice=choice)
        mulligan.settle(decision, _answer(decision, keep=keep, sent=500))

    page = client_in.get(_url(deck, round=99, n=11)).content.decode()

    assert "2 of 2 hands right." in page and "1 of them was close" in page
    assert "data-mulligan-sure" in page and "data-mulligan-new" in page


def test_someone_elses_deck_is_not_found(client, deck, django_user_model):  # noqa: F811
    other = django_user_model.objects.create_user(email="other@example.com",
                                                  password="pw-for-test-only")
    client.force_login(other)

    assert client.get(_url(deck, round=1, n=1)).status_code == 404


def test_signed_out_is_sent_to_sign_in(client, deck):  # noqa: F811
    assert client.get(_url(deck, round=1, n=1)).status_code == 302
    assert not MulliganDecision.objects.exists()


def test_the_deck_page_and_the_tutor_link_the_quiz(client_in, deck, empty_cache, no_worker,  # noqa: F811
                                                   monkeypatch):
    monkeypatch.setattr("decks.tutor.ask", lambda *args: None)
    link = f'href="{reverse("decks:mulligan", args=[deck.pk])}"'

    assert link in client_in.get(deck.get_absolute_url()).content.decode()
    tutor_page = client_in.get(reverse("decks:tutor", args=[deck.pk]) + "?hand=1")
    assert link in tutor_page.content.decode()


def test_the_privacy_policy_names_the_answers(client):
    page = client.get(reverse("privacy")).content.decode()

    assert "Your answers in Keep or mulligan?" in page
    assert "no scoring of users" not in page
