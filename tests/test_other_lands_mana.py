"""P19 R5 (engine version 9): mana in the colours of other lands.

Reflecting Pool and Incubation Druid copy what your own lands could make, so
the game works it out each turn. Exotic Orchard and Fellwar Stone copy an
opponent's lands: the deck's colours, as an assumption the card page states
and a player can replace.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _mana_rule
from simulation.cards import CREATURE, FLAT, LAND, LANDS_COULD_PRODUCE, Card, ManaAbility
from simulation.game import Game
from simulations import gaps
from simulations.engine import adapter
from simulations.models import CardAnnotation
from tests.support import load_catalogue

Kind = DerivedProfile.Kind


# --- the reader -----------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "{T}: Add one mana of any type that a land you control could produce.",
    "{T}: Add one mana of any color that a land you control could produce.",
    "{T}, Pay 1 life: Add one mana of any type that a land you control could produce.\n"
    "{3}, {T}: You may put a land card from your hand onto the battlefield tapped.",
])
def test_a_copy_of_your_own_lands_is_a_rule(text):
    rule = _mana_rule(OracleCard(oracle_text=text, type_line="Land"), Kind.LAND)
    assert rule == {"rule": "lands_could_produce", "subtype": "", "activation": 0, "color": ""}


# --- the engine -----------------------------------------------------------------


def land(name, subtypes=(), abilities=()):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset(subtypes),
                mana_abilities=tuple(abilities), types=frozenset({"land"}))


POOL = land("Reflecting Pool", abilities=[ManaAbility(LANDS_COULD_PRODUCE)])
SWAMP, ISLAND = land("Swamp", {"swamp"}), land("Island", {"island"})
WASTES = land("Wastes", abilities=[ManaAbility(FLAT, {"C": 1})])


def game_with(lands=(), creatures=()):
    game = Game(random.Random(1))
    game.lands, game.creatures = list(lands), list(creatures)
    game.tapped_lands = 0
    return game


def test_reflecting_pool_makes_a_colour_the_other_lands_make():
    pool = game_with([POOL, SWAMP, ISLAND]).mana()
    assert pool.amount("UB") == 1 and pool.total == 3


def test_beside_colourless_lands_it_makes_colourless():
    assert game_with([POOL, WASTES]).mana().amount("C") == 2


def test_two_reflecting_pools_alone_make_nothing():
    assert game_with([POOL, POOL]).mana().total == 0


def test_incubation_druid_taps_like_a_mana_creature():
    druid = Card("Incubation Druid", 2, 0, 1, CREATURE,
                 mana_abilities=(ManaAbility(LANDS_COULD_PRODUCE),))
    pool = game_with([SWAMP], [druid]).mana()
    assert pool.amount("B") == 2


# --- the assumption -------------------------------------------------------------


@pytest.fixture
def orchard(db):
    load_catalogue()
    return OracleCard.objects.get(name="Exotic Orchard")


def test_exotic_orchard_makes_the_deck_colours_as_a_stated_assumption(orchard):
    card, found = adapter.engine_readings([orchard], builtin=False)[orchard.pk]

    assert card.mana_abilities == (ManaAbility(FLAT, {"WUBRG": 1}),)
    assumed = [gap for gap in found if gap.field == "assumed_mana"]
    assert assumed and gaps.kind_of("assumed_mana") == gaps.JUDGEMENT
    assert not gaps.of_kind(found, gaps.READING), "it is no longer a card the engine cannot read"


def test_the_card_page_says_it_is_an_assumption_and_an_answer_replaces_it(
        orchard, django_user_model):
    from decks.models import Deck

    owner = django_user_model.objects.create_user(email="orchard@example.com", password="pw-only")
    deck = Deck.objects.create(owner=owner, name="Orchard")
    deck.entries.create(oracle_card=orchard, quantity=1)

    reading = next(r for r in adapter.readings(deck) if r.oracle_card == orchard)
    assert "assuming your opponents' lands" in reading.taps_for

    CardAnnotation.objects.create(owner=owner, oracle_card=orchard,
                                  overrides={"mana_produces": {"G": 1}})
    reading = next(r for r in adapter.readings(deck) if r.oracle_card == orchard)
    assert "assuming" not in reading.taps_for
    assert not [gap for gap in reading.gaps if gap.field == "assumed_mana"]
