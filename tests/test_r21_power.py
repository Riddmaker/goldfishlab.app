"""P19 R21 (engine version 25): mana by power and +1/+1 counters.

A creature's power is the power printed on it plus its +1/+1 counters - no
pumps, no anthems. Kami of Whispered Hopes, Marwyn and Vivi Ornitier tap for
their power, Selvala for the greatest power, Arbor Adherent for the greatest
toughness of the others, Gyre Sage for its counters. The counters come from
creatures that enter with them, Rishkar, Evolve, Increment, Adapt and
"another Elf enters"; Kami adds one to each. Growing Rites of Itlimoc turns
into its land at four creatures. Every assumption is said on the deck page.
"""

import random
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import OracleCard
from cards.profiles import derive
from decks.models import Deck, DeckCard
from simulation import actions, agent, build, serial
from simulation.cards import (
    COUNTS,
    CREATURE,
    ENCHANTMENT,
    FLAT,
    GRANT,
    INSTANT,
    LANDS_COULD_PRODUCE,
    Card,
    DeckDefinition,
    Growth,
    ManaAbility,
    TappedUnless,
)
from simulation.game import Game
from simulation.mana import ManaPool
from simulation.manacost import parse
from simulation.trace import named
from simulations import deck_cards
from simulations.engine import adapter
from tests.engine_builders import marks, tap

ANY = "WUBRG"


def oracle(text, name="Test Card", type_line="Creature — Elf Druid", power="1",
           toughness="1"):
    return OracleCard(name=name, front_name=name.split(" // ")[0], oracle_text=text,
                      type_line=type_line, produced_mana=["G"], power=power,
                      toughness=toughness)


def read(text, **kwargs):
    profile = derive(oracle(text, **kwargs), set())
    return profile.mana_rule, profile.growth, profile.review_reasons


# --- the reader -----------------------------------------------------------------


def test_mana_by_its_own_power_is_read():
    rule, growth, reasons = read(
        "If one or more +1/+1 counters would be put on a permanent you control, that many "
        "plus one +1/+1 counters are put on that permanent instead.\n{T}: Add X mana of any "
        "one color, where X is this creature's power.", name="Kami of Whispered Hopes")
    assert rule == {"rule": "counts", "subtype": "power", "color": "", "activation": 0}
    assert growth == {"bonus": 1} and reasons == []


def test_marwyn_grows_with_each_elf():
    rule, growth, reasons = read(
        "Whenever another Elf you control enters, put a +1/+1 counter on Marwyn.\n{T}: Add an "
        "amount of {G} equal to Marwyn's power.", name="Marwyn, the Nurturer")
    assert (rule["subtype"], rule["color"]) == ("power", "G")
    assert growth == {"grows": ["enters:elf"]} and reasons == []


def test_vivi_once_a_turn_in_blue_and_red():
    rule, growth, reasons = read(
        "{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi Ornitier's "
        "power. Activate only during your turn and only once each turn.\nWhenever you cast a "
        "noncreature spell, put a +1/+1 counter on Vivi Ornitier and it deals 1 damage to each "
        "opponent.", name="Vivi Ornitier", power="0", toughness="3")
    assert rule == {"rule": "counts", "subtype": "power", "color": "UR", "activation": 0,
                    "once": True}
    assert growth == {"grows": ["cast:noncreature"]} and reasons == []


def test_selvala_counts_the_greatest_power_and_draws():
    rule, growth, reasons = read(
        "Whenever another creature enters, its controller may draw a card if its power is "
        "greater than each other creature's power.\n{G}, {T}: Add X mana in any combination of "
        "colors, where X is the greatest power among creatures you control.",
        name="Selvala, Heart of the Wilds")
    assert rule == {"rule": "counts", "subtype": "power:greatest", "color": ANY,
                    "activation": 1, "pays": "G"}
    assert growth == {"draws_on_greatest": True} and reasons == []


def test_arbor_adherent_gyre_sage_and_karametra():
    rule, _, reasons = read(
        "{T}: Add one mana of any color.\n{T}: Add X mana of any one color, where X is the "
        "greatest toughness among other creatures you control.", name="Arbor Adherent")
    assert rule["subtype"] == "toughness:greatest_other" and reasons == []
    rule, growth, reasons = read(
        "Evolve (Whenever a creature you control enters, if that creature has greater power or "
        "toughness than this creature, put a +1/+1 counter on this creature.)\n{T}: Add {G} "
        "for each +1/+1 counter on this creature.", name="Gyre Sage")
    assert (rule["subtype"], growth, reasons) == ("counters", {"grows": ["evolve"]}, [])
    rule, _, reasons = read(
        "{T}: Add an amount of {G} equal to your devotion to green. (Each {G} in the mana costs "
        "of permanents you control counts toward your devotion to green.)",
        name="Karametra's Acolyte")
    assert (rule["subtype"], rule["color"], reasons) == ("devotion:G", "G", [])


def test_rishkar_and_incubation_druid():
    rule, growth, reasons = read(
        "When Rishkar enters, put a +1/+1 counter on each of up to two target creatures.\nEach "
        "creature you control with a counter on it has \"{T}: Add {G}.\"",
        name="Rishkar, Peema Renegade")
    assert rule["subtype"] == "creature:counter" and growth == {"puts": 2} and reasons == []
    rule, growth, reasons = read(
        "{T}: Add one mana of any type that a land you control could produce. If this creature "
        "has a +1/+1 counter on it, add three mana of that type instead.\n{3}{G}{G}: Adapt 3. "
        "(If this creature has no +1/+1 counters on it, put three +1/+1 counters on it.)",
        name="Incubation Druid")
    assert (rule["rule"], rule["with_counter"]) == ("lands_could_produce", 3)
    assert growth == {"adapt": {"n": 3, "cost": "{3}{G}{G}"}} and reasons == []


def test_growing_rites_is_an_enchantment_that_turns():
    profile = derive(oracle(
        "When Growing Rites of Itlimoc enters, look at the top four cards of your library. You "
        "may reveal a creature card from among them and put it into your hand. Put the rest on "
        "the bottom of your library in any order.\nAt the beginning of your end step, if you "
        "control four or more creatures, transform Growing Rites of Itlimoc.\n//\n(Transforms "
        "from Growing Rites of Itlimoc.)\n{T}: Add {G}.\n{T}: Add {G} for each creature you "
        "control.", name="Growing Rites of Itlimoc // Itlimoc, Cradle of the Sun",
        type_line="Legendary Enchantment // Legendary Land", power="", toughness=""), set())
    assert profile.kind == "enchantment"
    assert profile.mana_rule["flip"] == 4 and profile.mana_rule["subtype"] == "creature"
    assert profile.growth == {"look": 4} and profile.review_reasons == []


def test_enters_with_counters_and_raucous_audience():
    _, growth, _ = read("Trample\nThis creature enters with X +1/+1 counters on it.",
                        name="Some Hydra", power="0", toughness="0")
    assert growth == {"enters_x": True}
    profile = derive(oracle("{T}: Add {G}. If you control a creature with power 4 or greater, "
                            "add {G}{G} instead.", name="Raucous Audience"), set())
    assert (profile.mana_amount, profile.mana_produces) == (2, {"G": 2})
    assert profile.mana_condition["otherwise"]["produces"] == {"G": 1}


def test_a_power_from_someone_else_is_not_read():
    rule, _, _ = read("{T}: Add an amount of {G} equal to enchanted creature's power.",
                      name="Not Me")
    assert rule is None


# --- the engine -------------------------------------------------------------------


def creature(name, power=1, toughness=1, mana=(), growth=None, types=("elf",), mv=2,
             cost="{1}{G}"):
    return build.card(name, mv, 0, mv, CREATURE, cost=parse(cost), types=frozenset({"creature"}),
                      creature_types=frozenset(types), mana_abilities=tuple(mana), power=power,
                      toughness=toughness, growth=growth, colors=frozenset("G"))


def game_with(creatures=(), other=(), hand=(), library=()):
    game = Game(random.Random(1), deck=DeckDefinition("R21", None, (creature("Bear"),)))
    game.creatures, game.other_permanents = list(creatures), list(other)
    game.hand = list(hand)
    game.library = list(library) or [creature(f"Filler {n}", types=()) for n in range(20)]
    game.lands, game.rocks = [], []
    tap(game, lands=0, rocks=0)
    return game


def next_turn(game):
    game.begin_turn()
    return game.open_pool().by_color()


def power_mana(color="G"):
    return (ManaAbility(COUNTS, subtype="power", color=color),)


KAMI = creature("Kami of Whispered Hopes", 1, 1, power_mana(""), Growth(bonus=1),
                types=("spirit",))
MARWYN = creature("Marwyn, the Nurturer", 1, 1, power_mana(), Growth(grows=("enters:elf",)))
SAGE = creature("Gyre Sage", 1, 2, (ManaAbility(COUNTS, subtype="counters", color="G"),),
                Growth(grows=("evolve",)))
RISHKAR = creature("Rishkar, Peema Renegade", 2, 2,
                   (ManaAbility(GRANT, {"G": 1}, subtype="creature:counter"),),
                   Growth(puts=2), mv=3, cost="{2}{G}")
DRUID = creature("Incubation Druid", 0, 2, (ManaAbility(LANDS_COULD_PRODUCE, times=3),),
                 Growth(adapt=3, adapt_cost=parse("{3}{G}{G}")))
FOREST = build.card("Forest", 0, 0, 0, "land", basic=True, subtypes=frozenset({"forest"}),
                    mana_abilities=(ManaAbility(FLAT, {"G": 1}),))


def test_marwyn_taps_for_her_power_and_grows_with_each_elf():
    game = game_with(creatures=[MARWYN])
    assert next_turn(game) == {"G": 1}
    game.cast(creature("Llanowar Elves"), ManaPool(G=2))
    assert named(game, "plus_counters") == {"Marwyn, the Nurturer": 1}
    assert next_turn(game) == {"G": 2}


def test_kami_adds_one_to_every_placement():
    game = game_with(creatures=[KAMI, MARWYN])
    game.cast(creature("Llanowar Elves"), ManaPool(G=2))
    assert named(game, "plus_counters") == {"Marwyn, the Nurturer": 2}
    assert game.power(MARWYN) == 3


def test_gyre_sage_evolves_by_printed_power_and_counters():
    game = game_with(creatures=[SAGE])
    assert next_turn(game) == {}
    game.cast(creature("Bear", 2, 2, types=()), ManaPool(G=2))
    game.cast(creature("Small", 1, 1, types=()), ManaPool(G=2))
    assert named(game, "plus_counters") == {"Gyre Sage": 1}
    assert next_turn(game) == {"G": 1}


def test_rishkar_puts_his_counters_where_they_make_most_mana():
    bear = creature("Bear", 2, 2, types=())
    game = game_with(creatures=[bear, DRUID], hand=[RISHKAR])
    game.lands = [FOREST]
    game.cast(RISHKAR, ManaPool(G=3))
    assert set(named(game, "plus_counters")) == {"Incubation Druid", "Bear"}
    # The Druid's three, the Bear's granted {G}, the Forest's {G}.
    assert next_turn(game) == {"G": 5}


def test_incubation_druid_adapts_with_spare_mana():
    game = game_with(creatures=[DRUID])
    game.lands = [FOREST]
    pool = ManaPool(G=5)
    assert [type(each) for each in actions.adaptations(game, pool)] == [actions.Adapt]
    game.adapt(DRUID, pool)
    assert named(game, "plus_counters") == {"Incubation Druid": 3}
    assert not game.can_adapt(DRUID, ManaPool(G=5))
    assert next_turn(game) == {"G": 4}


def test_the_agent_adapts_when_nothing_else_wants_the_mana():
    game = game_with(creatures=[DRUID])
    game.lands = [FOREST] * 4
    game.hand = []
    agent.take_turn(game)
    assert named(game, "plus_counters") == {"Incubation Druid": 3}


def test_vivi_grows_with_noncreature_spells():
    vivi = creature("Vivi Ornitier", 0, 3, power_mana("UR"), Growth(grows=("cast:noncreature",)),
                    types=("wizard",))
    shock = Card("Shock", 1, 1, 0, INSTANT, cost=parse("{R}"), colors=frozenset("R"))
    game = game_with(creatures=[vivi])
    assert next_turn(game) == {}
    game.cast(shock, ManaPool(R=1))
    game.cast(creature("Bear", 2, 2, types=()), ManaPool(G=2))
    assert game.power(vivi) == 1
    assert next_turn(game) == {"UR": 1}


def test_selvala_counts_the_greatest_power_and_draws_for_a_bigger_one():
    selvala = creature("Selvala, Heart of the Wilds", 2, 3,
                       (ManaAbility(COUNTS, subtype="power:greatest", color=ANY,
                                    activation_generic=1),),
                       Growth(draws_on_greatest=True), types=("elf",), mv=3)
    game = game_with(creatures=[selvala])
    game.lands = [FOREST]
    hand = len(game.hand)
    game.cast(creature("Big", 5, 5, types=()), ManaPool(G=2))
    assert len(game.hand) == hand + 1
    game.cast(creature("Small", 1, 1, types=()), ManaPool(G=2))
    assert len(game.hand) == hand + 1
    # Five, its {G} paid with the Forest's.
    assert next_turn(game) == {ANY: 5}


def test_arbor_adherent_counts_the_toughness_of_the_others():
    adherent = creature("Arbor Adherent", 2, 4,
                        (ManaAbility(COUNTS, subtype="toughness:greatest_other", color="G"),
                         ManaAbility(FLAT, {"G": 1})))
    game = game_with(creatures=[adherent])
    assert next_turn(game) == {"G": 1}
    game.creatures.append(creature("Wall", 0, 5, types=()))
    assert next_turn(game) == {"G": 5}


def test_power_four_counts_counters():
    fanatic = creature("Fanatic of Rhonas", 1, 4, (
        ManaAbility(FLAT, {"G": 4}, only_if=TappedUnless(kind="power", count=4)),
        ManaAbility(FLAT, {"G": 1})))
    game = game_with(creatures=[fanatic])
    assert next_turn(game) == {"G": 1}
    game.add_counters(fanatic, 3)
    assert next_turn(game) == {"G": 4}


def test_growing_rites_looks_for_a_creature_and_turns_at_four():
    rites = build.card("Growing Rites of Itlimoc", 3, 0, 3, ENCHANTMENT, cost=parse("{2}{G}"),
                       types=frozenset({"enchantment"}), flip=4, growth=Growth(look=4),
                       mana_abilities=(ManaAbility(COUNTS, subtype="creature", color="G"),
                                       ManaAbility(FLAT, {"G": 1})))
    library = [Card("Land", 0, 0, 0, "land"), creature("Huge", 6, 6, mv=6, types=()),
               creature("Tiny", 1, 1, types=())] + [creature(f"F{n}") for n in range(10)]
    game = game_with(creatures=[creature(f"C{n}") for n in range(3)], library=library)
    game.cast(rites, ManaPool(G=3))
    assert [each.name for each in game.hand] == ["Huge"]
    assert [each.name for each in game.library[-3:]] == ["Land", "Tiny", "F0"]
    game.end_step()
    assert named(game, "flipped") == []
    assert next_turn(game) == {}
    game.creatures.append(creature("C3"))
    game.end_step()
    assert named(game, "flipped") == ["Growing Rites of Itlimoc"]
    assert next_turn(game) == {"G": 4}


def test_a_saved_game_keeps_counters_and_flips():
    game = game_with(creatures=[MARWYN])
    marks(game, plus_counters={"Marwyn, the Nurturer": 2})
    marks(game, flipped=["Growing Rites of Itlimoc"])
    loaded = serial.load_game(serial.dump_game(game), game.deck)
    assert (named(loaded, "plus_counters"), named(loaded, "flipped")) == (
        named(game, "plus_counters"), named(game, "flipped"))
    assert loaded.creatures[0].abilities == MARWYN.abilities


# --- the adapter and the deck page --------------------------------------------------


@pytest.fixture
def power_deck(db):
    owner = get_user_model().objects.create_user(email="r21@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost, power="", toughness=""):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name.split(" // ")[0],
            search_name=name.lower(), type_line=type_line, oracle_text=text,
            produced_mana=["G"], mana_cost=cost, power=power, toughness=toughness)
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    marwyn = made("Marwyn, the Nurturer", "Legendary Creature — Elf Druid",
                  "Whenever another Elf you control enters, put a +1/+1 counter on Marwyn.\n"
                  "{T}: Add an amount of {G} equal to Marwyn's power.", "{2}{G}", "1", "1")
    rishkar = made("Rishkar, Peema Renegade", "Legendary Creature — Elf Druid",
                   "When Rishkar enters, put a +1/+1 counter on each of up to two target "
                   "creatures.\nEach creature you control with a counter on it has \"{T}: Add "
                   "{G}.\"", "{2}{G}", "2", "2")
    druid = made("Incubation Druid", "Creature — Elf Druid",
                 "{T}: Add one mana of any type that a land you control could produce. If this "
                 "creature has a +1/+1 counter on it, add three mana of that type instead.\n"
                 "{3}{G}{G}: Adapt 3. (If this creature has no +1/+1 counters on it, put three "
                 "+1/+1 counters on it.)", "{1}{G}", "0", "2")
    rites = made("Growing Rites of Itlimoc // Itlimoc, Cradle of the Sun",
                 "Legendary Enchantment // Legendary Land",
                 "When Growing Rites of Itlimoc enters, look at the top four cards of your "
                 "library. You may reveal a creature card from among them and put it into your "
                 "hand. Put the rest on the bottom of your library in any order.\nAt the "
                 "beginning of your end step, if you control four or more creatures, transform "
                 "Growing Rites of Itlimoc.\n//\n(Transforms from Growing Rites of Itlimoc.)\n"
                 "{T}: Add {G}.\n{T}: Add {G} for each creature you control.", "{2}{G}")
    deck = Deck.objects.create(owner=owner, name="Counters")
    for found in (marwyn, rishkar, druid, rites):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, marwyn=marwyn, rishkar=rishkar,
                           druid=druid, rites=rites)


def reading_of(deck, oracle_card):
    return next(each for each in adapter.readings(deck) if each.oracle_card.pk == oracle_card.pk)


def assumed(reading):
    return " ".join(gap.text for gap in reading.gaps if gap.field == "assumed_cost")


def test_every_card_states_its_assumption_beside_it(power_deck):
    found = {name: reading_of(power_deck.deck, getattr(power_deck, name))
             for name in ("marwyn", "rishkar", "druid", "rites")}
    for name, reading in found.items():
        assert not reading.unreadable, name
        grid = deck_cards.GridCard(reading=reading, open=False, answered=False)
        assert grid.assumptions, name
    assert "counts its power as the turn's mana is opened" in assumed(found["marwyn"])
    assert found["marwyn"].card.power == 1 and found["marwyn"].card.toughness == 1
    assert "Incubation Druid first" in assumed(found["rishkar"])
    assert "adapts as soon as the mana left" in assumed(found["druid"])
    rites = found["rites"]
    assert rites.card.mana.flip == 4 and rites.card.kind == ENCHANTMENT
    assert "turns into its land at your end step" in assumed(rites)
    assert "highest mana value" in assumed(rites)


def test_the_deck_page_shows_the_power_assumption(power_deck, client):
    client.force_login(power_deck.owner)
    page = client.get(power_deck.deck.get_absolute_url()).content.decode()
    assert "Assumed:" in page
    assert "counts its power as the turn&#x27;s mana is opened" in page


def test_the_playtest_can_post_adapt():
    from playtest.forms import ActionForm
    from playtest.views import ACTION_NAMES
    assert "adapt" in ACTION_NAMES
    form = ActionForm({"kind": "adapt", "index": 0})
    assert form.is_valid() and form.action() == actions.Adapt(index=0)
