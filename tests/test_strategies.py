"""Your strategies, and what would feed them (phase 11 E, Z5.3, K16, F14).

What these tests hold:

1. **The strategies** are the categories with at least two cards, the most
   first, with odds *calculated* by turn four - now and with two more.
2. **The candidates** feed the strategy and fit the deck: its colours, legal
   in Commander, not in it, no land unless for Ramp, no Game Changer unless it
   plays one; the most played first.
3. **Mistral's picks are checked** against what it was offered: an unknown
   strategy or card is dropped, a strategy without cards too, three at most.
4. **The block** shows the picks with their reasons, or - without them - the
   two biggest strategies and their three most played candidates. It is there
   whether summaries are on or not, and the combos are its last part.
"""

import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards.models import OracleCard
from decks import services as deck_services
from simulations import services, strategies, summary, tasks
from simulations.engine import adapter
from simulations.models import DeckSummary, SimulationRun
from simulations.report import hypergeometric

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="strategies@example.com", password="pw-test-1234")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="deck", filename="sample.csv",
    ).deck


@pytest.fixture
def readings(deck):
    return adapter.readings(deck)


def _names(readings, key, **kwargs):
    return [card.front_name for card in strategies.candidates(readings, key, **kwargs)]


# --- the strategies ---------------------------------------------------------------


def test_a_strategy_has_two_cards_and_the_biggest_come_first(readings):
    found = strategies.found(readings)
    counts = [strategy.count for strategy in found]

    assert found, "the sample deck has strategies"
    assert min(counts) >= strategies.MIN_CARDS
    assert counts == sorted(counts, reverse=True)
    assert all(strategy.label == strategies.LABELS[strategy.key] for strategy in found)


def test_the_odds_are_calculated_by_turn_four_now_and_with_two_more(readings):
    library = sum(reading.quantity for reading in readings if not reading.is_commander)
    seen = min(summary.SEEN_BY_TURN, library)

    for strategy in strategies.found(readings):
        none_now = hypergeometric(0, library, strategy.count, seen)
        none_more = hypergeometric(0, library, strategy.count + strategies.MORE, seen)
        assert strategy.now == round(100 - none_now, 1)
        assert strategy.more == round(100 - none_more, 1)
        assert strategy.more >= strategy.now


def test_a_percent_is_never_rounded_to_all_or_nothing():
    def label(value):
        return strategies.Strategy("ramp", "Ramp", 2, value, value).now_label

    assert label(99.6) == "over 99%"
    assert label(0.4) == "under 1%"
    assert label(100.0) == "100%"
    assert label(45.2) == "45%"


# --- the candidates ----------------------------------------------------------------


def test_candidates_feed_the_strategy_and_fit_the_deck(readings):
    in_deck = {reading.oracle_card.pk for reading in readings}
    colours = strategies.identity(readings)

    for strategy in strategies.found(readings):
        cards = strategies.candidates(readings, strategy.key)
        assert len(cards) <= strategies.CANDIDATES
        for card in cards:
            assert strategy.key in card.profile.role_tags
            assert set(card.color_identity) <= colours, card.name
            assert card.is_commander_legal
            assert card.pk not in in_deck


def test_the_most_played_come_first_and_unknown_last(readings):
    cards = strategies.candidates(readings, "recursion")
    ranks = [card.edhrec_rank for card in cards]
    known = [rank for rank in ranks if rank is not None]

    assert known == sorted(known)
    assert ranks[:len(known)] == known, "a card without a rank comes after the ranked"


def test_a_land_feeds_only_ramp(readings):
    ramp = strategies.candidates(readings, "ramp", limit=100)
    others = [card for key in strategies.LABELS if key != "ramp"
              for card in strategies.candidates(readings, key, limit=100)]

    assert any(card.is_land for card in ramp), "the sample has ramp lands"
    assert not any(card.profile.kind == "land" for card in others)


def test_an_off_colour_or_banned_card_is_never_a_candidate(readings):
    card = OracleCard.objects.get(front_name=_names(readings, "recursion")[0])

    card.legalities = {**card.legalities, "commander": "banned"}
    card.save()
    assert card.front_name not in _names(readings, "recursion", limit=100)

    card.legalities = {**card.legalities, "commander": "legal"}
    card.color_identity = ["B", "G"]
    card.save()
    assert card.front_name not in _names(readings, "recursion", limit=100)


def test_a_game_changer_only_for_a_deck_that_plays_one(deck, readings):
    in_deck = [reading.oracle_card.pk for reading in readings]
    OracleCard.objects.filter(pk__in=in_deck).update(game_changer=False)
    card = OracleCard.objects.get(front_name=_names(readings, "recursion")[0])
    card.game_changer = True
    card.save()

    assert card.front_name not in _names(adapter.readings(deck), "recursion", limit=100)

    played = deck.entries.first().oracle_card
    played.game_changer = True
    played.save()
    assert card.front_name in _names(adapter.readings(deck), "recursion", limit=100)


# --- what Mistral gets, and what it may answer -------------------------------------


def test_the_facts_offer_each_strategy_with_its_candidates(deck, readings):
    offered = summary.facts(deck, readings)["strategies"]

    assert [row["strategy"] for row in offered] == [
        strategy.label for strategy in strategies.found(readings)
        if strategies.candidates(readings, strategy.key)
    ]
    for row in offered:
        assert set(row["candidates"][0]) == {"name", "type", "mana_cost", "text"}
        assert all(len(card["text"]) <= strategies.TEXT_MAX + 1 for card in row["candidates"])
    assert '"strategies"' in summary.SYSTEM_PROMPT
    assert "Never name a card that is not in that strategy's candidates" in summary.SYSTEM_PROMPT
    assert 'never one from "cards"' in summary.SYSTEM_PROMPT
    assert summary.PROMPT_VERSION >= 4


def test_the_land_verdict_is_worked_out_not_left_to_the_model(deck, readings):
    """Batch F: "22 lands, low against the usual 18-19" was the model's own reading."""
    deck_facts = summary.facts(deck, readings)
    low, high = (int(part) for part in deck_facts["usual_lands_for_this_size"].split("-"))
    lands = deck_facts["lands"]
    expected = "below" if lands < low else "above" if lands > high else "within"

    assert deck_facts["lands_compared_with_usual"] == expected
    assert "lands_compared_with_usual" in summary.SYSTEM_PROMPT


ANSWER = {
    "feel": "A graveyard deck.",
    "strengths": ["Strong recursion."],
    "weaknesses": [],
    "tactics": "Bring things back.",
}


def _parse(picks, offered):
    return summary.parse(json.dumps({**ANSWER, "strategies": picks}), offered)["strategies"]


def test_mistral_names_only_cards_it_was_offered(deck, readings):
    offered = summary.facts(deck, readings)["strategies"]
    first = offered[0]
    real = first["candidates"][0]["name"]

    picks = _parse([
        {"strategy": first["strategy"].upper(), "why": "It is the **plan**.",
         "cards": [{"name": real.lower(), "reason": "x" * 300},
                   {"name": "Black Lotus", "reason": "invented"},
                   {"name": real, "reason": "twice"}]},
        {"strategy": "Wincon", "why": "unknown", "cards": [{"name": real}]},
        {"strategy": offered[1]["strategy"], "why": "nothing valid",
         "cards": [{"name": "Sol Ring"}]},
    ], offered)

    assert len(picks) == 1
    assert picks[0]["key"] == strategies.found(readings)[0].key
    assert picks[0]["why"] == "It is the plan."
    assert [card["name"] for card in picks[0]["cards"]] == [real], "spelled as we spell it"
    assert len(picks[0]["cards"][0]["reason"]) <= summary.REASON_MAX + 1


def test_three_strategies_with_three_cards_at_most(deck, readings):
    offered = summary.facts(deck, readings)["strategies"]
    row = offered[0]
    picks = _parse(
        [{"strategy": row["strategy"], "cards": [{"name": card["name"]}
                                                  for card in row["candidates"]]}]
        + [{"strategy": other["strategy"], "cards": [{"name": other["candidates"][0]["name"]}]}
           for other in offered[1:]] * 3,
        offered,
    )

    assert len(picks) <= summary.STRATEGIES_MAX
    assert len(picks[0]["cards"]) == summary.CARDS_MAX
    assert len({pick["key"] for pick in picks}) == len(picks)


def test_a_card_is_named_under_one_strategy_only():
    """Batch F: Mind Stone came back under both Ramp and Card draw."""
    stone = {"name": "Mind Stone", "type": "Artifact", "mana_cost": "{2}", "text": ""}
    offered = [
        {"strategy": "Ramp", "candidates": [stone, {**stone, "name": "Sol Ring"}]},
        {"strategy": "Card draw", "candidates": [stone, {**stone, "name": "Skullclamp"}]},
    ]

    picks = _parse([
        {"strategy": "Ramp", "cards": [{"name": "Mind Stone"}, {"name": "Sol Ring"}]},
        {"strategy": "Card draw", "cards": [{"name": "Mind Stone"}, {"name": "Skullclamp"}]},
    ], offered)

    assert [[card["name"] for card in pick["cards"]] for pick in picks] == [
        ["Mind Stone", "Sol Ring"], ["Skullclamp"]]


def test_an_answer_without_strategies_is_still_a_summary(deck, readings):
    offered = summary.facts(deck, readings)["strategies"]

    assert _parse(None, offered) == []
    assert _parse("Ramp", offered) == []
    assert summary.parse(json.dumps(ANSWER), offered)["strategies"] == []


# --- the block ------------------------------------------------------------------


def test_without_picks_the_two_biggest_with_three_cards_each(readings):
    block = strategies.block(readings, None)

    assert not block["picked"]
    assert [item.strategy for item in block["shown"]] == strategies.found(readings)[:2]
    for item in block["shown"]:
        assert [card.name for card in item.cards] == _names(readings, item.strategy.key,
                                                            limit=3)
        assert not any(card.reason for card in item.cards)
        assert item.why == ""


def test_picks_are_read_against_the_deck_as_it_is(deck, readings):
    key = strategies.found(readings)[0].key
    in_deck = next(reading.oracle_card.front_name for reading in readings
                   if not reading.is_commander)
    content = {"strategies": [
        {"key": key, "why": "Why.", "cards": [{"name": "Reanimate", "reason": "Cheap."},
                                              {"name": in_deck, "reason": "added since"}]},
        {"key": "counterspell", "why": "gone", "cards": [{"name": "Counterspell"}]},
    ]}

    block = strategies.block(readings, content)

    assert block["picked"]
    assert len(block["shown"]) == 1
    assert [card.name for card in block["shown"][0].cards] == ["Reanimate"]
    assert block["shown"][0].cards[0].reason == "Cheap."
    assert block["shown"][0].cards[0].url.startswith("https://scryfall.com/")


# --- the page --------------------------------------------------------------------


class _NoRedis:
    def decr(self, key):
        return 0

    def set(self, *args, **kwargs):
        return True


@pytest.fixture
def finished(owner, deck, monkeypatch):
    monkeypatch.setattr(services, "_redis", lambda: _NoRedis())
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=20, turns=2, seed=7)
    tasks.finalize_run([tasks.simulate_chunk(str(run.pk), 0, 20)], str(run.pk))
    return run


def _page(client, owner, run):
    client.force_login(owner)
    return client.get(reverse("simulations:detail", args=[run.pk])).content.decode()


def test_the_block_shows_without_mistral_and_with_summaries_off(client, owner, finished,
                                                                 readings, settings):
    settings.MISTRAL_API_KEY = ""
    owner.deck_summaries = False
    owner.save()

    body = _page(client, owner, finished)
    block = body[body.index('id="strategies"'):body.index('id="advanced"')]

    assert "Your strategies, and what would feed them" in block
    assert "The most played first." in block
    assert "Picked by Mistral AI" not in block
    first = strategies.found(readings)[0]
    assert f"At least one by turn 4: {first.now_label} now, {first.more_label} with 2 more." \
        in block
    assert "calculated from your list" in block


def test_mistral_picks_show_with_their_reasons_escaped(client, owner, deck, finished,
                                                       readings, settings):
    settings.MISTRAL_API_KEY = "test-key-not-real"
    key = strategies.found(readings)[0].key
    DeckSummary.objects.create(
        deck=deck, fingerprint=summary.fingerprint(deck), status=DeckSummary.Status.DONE,
        content={**ANSWER, "strategies": [
            {"key": key, "label": "x", "why": "It <b>matters</b>.",
             "cards": [{"name": "Reanimate", "reason": "One mana <script>."}]}]},
    )

    body = _page(client, owner, finished)
    block = body[body.index('id="strategies"'):body.index('id="advanced"')]

    assert "Picked by Mistral AI from cards that fit your colours. It can be wrong." in block
    assert "It &lt;b&gt;matters&lt;/b&gt;." in block
    assert "One mana &lt;script&gt;." in block
    assert "The most played first." not in block


def test_the_combos_are_the_last_part_with_columns_that_read(db):
    from types import SimpleNamespace

    from django.template.loader import render_to_string

    def measurement(names, added="", games=2000):
        cards = [SimpleNamespace(name=name) for name in names]
        return SimpleNamespace(
            combo=SimpleNamespace(cards=SimpleNamespace(all=lambda: cards)),
            hypothetical=bool(added), added_name=added, games=games,
            share_label="12%", median_turn=5,
        )

    body = render_to_string("simulations/_strategies.html", {
        "strategies": {"shown": []},
        "run": SimpleNamespace(games_total=2000),
        "report": {"turns": 8, "combos": [
            measurement(["Gravecrawler", "Phyrexian Altar"]),
            measurement(["Ashnod's Altar", "Nim Deathmantle"], added="Nim Deathmantle",
                        games=500),
        ]},
    })

    assert 'id="strategies"' in body and 'id="combo-timings"' in body
    for column in ("Combo", "Missing card", "Together by turn 8", "When it happens, usually by"):
        assert f">{column}</th>" in body
    assert ">Games</th>" not in body and ">Half by</th>" not in body
    assert "measured over 2,000 games." in body
    assert "over 500 games" in body, "a missing card's own sample"
