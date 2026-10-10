"""P19 R15 (engine version 19): sacrifice, life and other additional costs.

"As an additional cost to cast this spell, sacrifice a creature" - Village
Rites, Diabolic Intent, Natural Order, Harrow - is paid now, with the fodder
the user decided on: a Treasure, then a Lander token, then a creature that
comes back, then the cheapest; never the commander, and never a permanent that
makes mana. A land goes only to a land search worth it. "Discard a card or pay
3 life" takes the cheapest way; "pay X life" is paid with X = 0, an assumption
the card page states; a "you may" cost is never paid.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _additional_cost, _land_search, _mana_production, _tutor, derive
from simulation import actions, agent
from simulation.cards import (
    ARTIFACT,
    CREATURE,
    FLAT,
    INSTANT,
    LAND,
    RITUAL,
    SORCERY,
    AdditionalCost,
    Card,
    DeckDefinition,
    LandSearch,
    ManaAbility,
    TutorSpec,
)
from simulation.game import LANDER_FODDER, TREASURE, Game
from simulation.manacost import parse
from simulations.engine.adapter import _way_text

Kind = DerivedProfile.Kind

# --- the reader -----------------------------------------------------------------


def ways(text):
    return _additional_cost(OracleCard(oracle_text=text)).ways


def test_sacrifice_a_creature_is_read():
    found = ways("As an additional cost to cast this spell, sacrifice a creature.\nDraw two cards.")
    assert found == [{"sacrifice": ["creature"], "filter": "", "life": 0, "life_x": False,
                      "discard": 0, "mana": "", "exile_from_graveyard": ""}]


@pytest.mark.parametrize("body, expected", [
    ("sacrifice an artifact or creature", [(["artifact", "creature"], "", 0, 0, "")]),
    ("sacrifice a green creature", [(["creature"], "G", 0, 0, "")]),
    ("sacrifice a legendary creature", [(["creature"], "legendary", 0, 0, "")]),
    ("sacrifice a land", [(["land"], "", 0, 0, "")]),
    ("discard a card or pay 3 life", [([], "", 0, 1, ""), ([], "", 3, 0, "")]),
    ("sacrifice an artifact or discard a card", [(["artifact"], "", 0, 0, ""),
                                                 ([], "", 0, 1, "")]),
    ("pay 5 life or pay {2}", [([], "", 5, 0, ""), ([], "", 0, 0, "{2}")]),
])
def test_the_ways_to_pay_are_read(body, expected):
    found = ways(f"As an additional cost to cast this spell, {body}.\nDestroy target creature.")
    assert [(way["sacrifice"], way["filter"], way["life"], way["discard"], way["mana"])
            for way in found] == expected


def test_pay_x_life_is_read_as_x():
    assert ways("As an additional cost to cast this spell, pay X life.\nAll creatures get "
                "-X/-X until end of turn.")[0]["life_x"] is True


@pytest.mark.parametrize("body", ["sacrifice X creatures", "tap two untapped creatures you control",
                                  "discard X cards"])
def test_what_it_cannot_read_stays_a_gap(body):
    found = _additional_cost(OracleCard(
        oracle_text=f"As an additional cost to cast this spell, {body}.\nDraw a card."))
    assert found.ways is None and "additional casting cost" in found.reason


def test_a_cost_one_may_pay_is_never_paid():
    found = _additional_cost(OracleCard(oracle_text=(
        "As an additional cost to cast this spell, you may blight 1.\nExile the top two cards "
        "of your library.")))
    assert found.optional and found.ways is None and not found.reason


def test_a_cost_one_may_pay_for_a_reduction_stays_a_gap():
    found = _additional_cost(OracleCard(oracle_text=(
        "As an additional cost to cast this spell, you may exile any number of blue cards from "
        "your hand. This spell costs {2} less to cast for each card exiled this way.")))
    assert found.reason


@pytest.mark.django_db
def test_a_cost_on_other_spells_has_its_own_reason():
    card = OracleCard(name="Defiler", front_name="Defiler", type_line="Creature — Phyrexian",
                      mana_cost="{3}{G}{G}", cmc=5, oracle_text=(
                          "As an additional cost to cast green permanent spells, you may pay 2 "
                          "life. Those spells cost {G} less to cast if you paid life this way."))
    profile = derive(card, set())
    assert profile.additional_cost is None
    assert profile.review_reasons == [
        "changes what other spells cost, which the engine does not model"]


@pytest.mark.django_db
def test_village_rites_reads_whole():
    card = OracleCard(name="Village Rites", front_name="Village Rites", type_line="Instant",
                      mana_cost="{B}", cmc=1, oracle_text=(
                          "As an additional cost to cast this spell, sacrifice a creature.\n"
                          "Draw two cards."))
    profile = derive(card, set())
    assert not profile.needs_review
    assert profile.additional_cost[0]["sacrifice"] == ["creature"]


def test_springbloom_druid_sacrifices_a_land_for_two():
    spec = _land_search(OracleCard(oracle_text=(
        "When this creature enters, you may sacrifice a land. If you do, search your library "
        "for up to two basic land cards, put them onto the battlefield tapped, then shuffle."),
        type_line="Creature — Elf Druid"), Kind.CREATURE).spec
    assert (spec["when"], spec["sacrifices_land"], spec["battlefield"]) == ("enters", True, 2)


def test_natural_order_is_a_tutor_not_a_land_search():
    text = ("As an additional cost to cast this spell, sacrifice a green creature.\nSearch your "
            "library for a green creature card, put it onto the battlefield, then shuffle.")
    card = OracleCard(oracle_text=text, type_line="Sorcery", mana_cost="{2}{G}{G}")
    assert _land_search(card, Kind.SORCERY).spec is None
    assert not _land_search(card, Kind.SORCERY).reason
    found = _tutor(card, {"tutor"})
    assert (found.zone, found.kind, found.filter["color"]) == ("battlefield", "creature", "G")


def test_eldritch_evolution_counts_from_the_sacrifice():
    card = OracleCard(type_line="Sorcery", mana_cost="{1}{G}{G}", oracle_text=(
        "As an additional cost to cast this spell, sacrifice a creature.\nSearch your library "
        "for a creature card with mana value X or less, where X is 2 plus the sacrificed "
        "creature's mana value. Put that card onto the battlefield, then shuffle. Exile Eldritch "
        "Evolution."))
    assert _tutor(card, {"tutor"}).filter["plus_sacrificed"] == 2


def test_the_card_page_says_what_else_is_paid():
    assert _way_text(AdditionalCost(sacrifice=frozenset({"creature"}))) == "sacrifice a Creature"
    assert _way_text(AdditionalCost(life=3)) == "3 life"
    assert _way_text(AdditionalCost(life_x=True)) == "X life, paid as 0"


# --- the engine -----------------------------------------------------------------


def land(name, subtype, *, tapped_in=False):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}), basic=True,
                enters_tapped=tapped_in)


FOREST, SWAMP, ISLAND = land("Forest", "forest"), land("Swamp", "swamp"), land("Island", "island")
SAC_CREATURE = (AdditionalCost(sacrifice=frozenset({"creature"})),)
RITES = Card("Village Rites", 1, 1, 0, INSTANT, cost=parse("{B}"), draw_on_cast=2,
             additional_costs=SAC_CREATURE)
DISPUTE = Card("Deadly Dispute", 2, 1, 1, INSTANT, cost=parse("{1}{B}"), draw_on_cast=2,
               treasures=1, additional_costs=(
                   AdditionalCost(sacrifice=frozenset({"artifact", "creature"})),))
BEAR = Card("Grizzly Bears", 2, 0, 2, CREATURE, cost=parse("{1}{G}"))
SQUIRE = Card("Squire", 2, 0, 2, CREATURE, cost=parse("{1}{W}"))
BLOODGHAST = Card("Bloodghast", 2, 2, 0, CREATURE, cost=parse("{B}{B}"),
                  tags=frozenset({"recursive"}))
ELF = Card("Llanowar Elves", 1, 0, 1, CREATURE, cost=parse("{G}"),
           mana_abilities=(ManaAbility(FLAT, {"G": 1}),))
COMMANDER = Card("Commander", 3, 0, 3, CREATURE, cost=parse("{2}{B}"))


def game_with(library=(), *, lands=(), creatures=(), others=(), hand=(), commander=None):
    game = Game(random.Random(1), deck=DeckDefinition("R15", commander, (FOREST,)))
    game.library = list(library)
    game.lands = list(lands)
    game.creatures = list(creatures)
    game.other_permanents = list(others)
    game.hand = list(hand)
    game.tapped_lands = 0
    game.turn = 3
    actions.OpenMainPhase().run(game, None)
    return game


def test_without_fodder_it_cannot_be_cast():
    game = game_with([ISLAND, ISLAND], lands=[SWAMP], hand=[RITES])
    assert not game.can_cast(RITES, game.pool)
    with pytest.raises(actions.IllegalAction, match="additional cost"):
        actions.CastSpell(index=0).run(game, None)


def test_a_mana_creature_and_the_commander_are_never_fodder():
    game = game_with([ISLAND, ISLAND], lands=[SWAMP], creatures=[ELF, COMMANDER],
                     hand=[RITES], commander=COMMANDER)
    assert not game.can_cast(RITES, game.pool)


def test_the_fodder_goes_recursive_then_cheapest():
    game = game_with(lands=[SWAMP], creatures=[SQUIRE, BLOODGHAST, BEAR])
    assert game.fodder(SAC_CREATURE[0]) == [BLOODGHAST, BEAR, SQUIRE]


def test_tokens_go_first_to_an_artifact_cost():
    game = game_with(lands=[SWAMP, SWAMP], creatures=[BEAR])
    game.treasures = ["WUBRG"]
    game.pool.treasures = ["WUBRG"]
    game.landers = 1
    assert game.fodder(DISPUTE.additional_costs[0], game.pool) == [TREASURE, LANDER_FODDER, BEAR]


def test_village_rites_sacrifices_and_draws():
    game = game_with([ISLAND, ISLAND], lands=[SWAMP], creatures=[BEAR], hand=[RITES])
    actions.CastSpell(index=0).run(game, None)
    assert BEAR in game.graveyard and not game.creatures
    assert len(game.hand) == 2


def test_a_treasure_sacrificed_leaves_the_pool_first():
    # Two Swamps and a Treasure: {1}{B} from the Swamps, the Treasure as fodder.
    game = game_with([ISLAND, ISLAND], lands=[SWAMP, SWAMP], hand=[DISPUTE])
    game.treasures = ["WUBRG"]
    game.pool.treasures = ["WUBRG"]
    assert game.can_cast(DISPUTE, game.pool)
    actions.CastSpell(index=0).run(game, None)
    # The old Treasure is gone; Dispute made a new one.
    assert game.treasures == ["WUBRG"] and game.pool.total == 0


def test_a_treasure_needed_for_the_mana_cannot_also_be_fodder():
    game = game_with([ISLAND, ISLAND], lands=[SWAMP], hand=[DISPUTE])
    game.treasures = ["WUBRG"]
    game.pool.treasures = ["WUBRG"]
    assert not game.can_cast(DISPUTE, game.pool)


def test_the_player_picks_another_fodder():
    game = game_with([ISLAND, ISLAND], lands=[SWAMP], creatures=[BLOODGHAST, BEAR], hand=[RITES])
    assert [fodder for _, fodder in game.payment_options(RITES, game.pool)] == [BLOODGHAST, BEAR]
    actions.CastSpell(index=0, payment=1).run(game, None)
    assert BEAR in game.graveyard and BLOODGHAST in game.creatures


BITTER = Card("Bitter Triumph", 2, 1, 1, INSTANT, cost=parse("{1}{B}"), additional_costs=(
    AdditionalCost(discard=1), AdditionalCost(life=3)))


def test_life_the_deck_can_spare_goes_before_a_discard():
    game = game_with(lands=[SWAMP, SWAMP], hand=[BITTER, ISLAND])
    actions.CastSpell(index=0).run(game, None)
    assert game.life == 37 and ISLAND in game.hand


def test_below_the_floor_it_discards_instead():
    game = game_with(lands=[SWAMP, SWAMP], hand=[BITTER, ISLAND])
    game.life = 26
    actions.CastSpell(index=0).run(game, None)
    assert game.life == 26 and ISLAND in game.graveyard


def test_mana_as_the_other_way_is_paid_on_top():
    redirect = Card("Redirect Lightning", 1, 0, 0, INSTANT, cost=parse("{R}"), additional_costs=(
        AdditionalCost(life=5), AdditionalCost(mana=parse("{2}"))))
    game = game_with(lands=[land("Mountain", "mountain")] * 3, hand=[redirect])
    game.life = 28
    actions.CastSpell(index=0).run(game, None)
    assert game.life == 28 and game.pool.total == 0


def test_x_life_is_paid_with_nothing():
    deluge = Card("Toxic Deluge", 3, 1, 2, SORCERY, cost=parse("{2}{B}"),
                  additional_costs=(AdditionalCost(life_x=True),))
    game = game_with(lands=[SWAMP] * 3, hand=[deluge])
    actions.CastSpell(index=0).run(game, None)
    assert game.life == 40 and deluge in game.graveyard


HARROW = Card("Harrow", 3, 0, 2, INSTANT, cost=parse("{2}{G}"),
              land_search=LandSearch(battlefield=2, tapped=False),
              additional_costs=(AdditionalCost(sacrifice=frozenset({"land"})),))
ROTATION = Card("Crop Rotation", 1, 0, 0, INSTANT, cost=parse("{G}"),
                land_search=LandSearch(tapped=False, basic=False),
                additional_costs=(AdditionalCost(sacrifice=frozenset({"land"})),))


def test_harrow_trades_one_land_for_two_untapped():
    game = game_with([SWAMP, ISLAND], lands=[FOREST, FOREST, FOREST], hand=[HARROW])
    assert agent._cast_best(game, game.pool)
    assert len(game.lands) == 4 and game.graveyard.count(FOREST) == 1
    # Two untapped lands made two mana after the three were spent.
    assert game.pool.total == 2


def test_a_land_that_came_in_tapped_is_sacrificed_first():
    game = game_with([SWAMP, ISLAND], lands=[ISLAND, FOREST, FOREST, FOREST], hand=[HARROW])
    game.tapped_lands = 1
    assert game.land_fodder()[0] is game.lands[0]


def test_crop_rotation_only_for_a_missing_colour():
    game = game_with([FOREST], lands=[FOREST, FOREST], hand=[ROTATION])
    assert not agent._cast_best(game, game.pool)
    game = game_with([ISLAND], lands=[FOREST, FOREST], hand=[ROTATION])
    assert agent._cast_best(game, game.pool)
    assert ISLAND in game.lands


def test_a_land_is_never_fodder_for_anything_else():
    culling = Card("Dark Bargain", 1, 1, 0, RITUAL, cost=parse("{B}"), ritual_gain=2,
                   additional_costs=(AdditionalCost(sacrifice=frozenset({"land"})),))
    game = game_with(lands=[SWAMP, SWAMP], hand=[culling])
    assert not agent._worth_its_land(game, culling, game.pool)


def test_eldritch_evolution_finds_up_to_two_more():
    evolution = Card("Eldritch Evolution", 3, 0, 1, SORCERY, cost=parse("{1}{G}{G}"),
                     tutor=TutorSpec(to_battlefield=True, kind="creature", max_mv_sacrificed=2),
                     additional_costs=SAC_CREATURE)
    big = Card("Big", 5, 0, 5, CREATURE, cost=parse("{5}"), types=frozenset({"creature"}))
    four = Card("Four", 4, 0, 4, CREATURE, cost=parse("{4}"), types=frozenset({"creature"}))
    game = game_with([big, four], lands=[FOREST] * 3, creatures=[BEAR], hand=[evolution])
    actions.CastSpell(index=0).run(game, None)
    assert four in game.creatures and big in game.library


def test_springbloom_druid_keeps_its_lands_without_two_to_find():
    druid = Card("Springbloom Druid", 3, 0, 2, CREATURE, cost=parse("{2}{G}"),
                 land_search=LandSearch(battlefield=2, when="enters", sacrifices_land=True))
    game = game_with([ISLAND], lands=[FOREST] * 3, hand=[druid])
    actions.CastSpell(index=0).run(game, None)
    assert len(game.lands) == 3
    game = game_with([ISLAND, SWAMP], lands=[FOREST] * 3, hand=[druid])
    actions.CastSpell(index=0).run(game, None)
    assert len(game.lands) == 4 and FOREST in game.graveyard


def test_an_artifact_fodder_from_the_board():
    clue = Card("Clue", 2, 0, 2, ARTIFACT, cost=parse("{2}"), types=frozenset({"artifact"}))
    game = game_with([ISLAND, ISLAND], lands=[SWAMP, SWAMP], others=[clue], hand=[DISPUTE])
    actions.CastSpell(index=0).run(game, None)
    assert clue in game.graveyard


# --- altars (D) -------------------------------------------------------------------


def mana_reading(text, type_line="Artifact"):
    return _mana_production(OracleCard(oracle_text=text, type_line=type_line,
                                       produced_mana=["C"]))


def test_an_altar_is_read():
    found = mana_reading("Sacrifice a creature: Add {C}{C}.").sacrifice
    assert found == {"sacrifice": ["creature"], "filter": "", "amount": 2,
                     "produces": {"C": 2}, "taps": False}


def test_phyrexian_tower_taps_and_sacrifices():
    reading = mana_reading("{T}: Add {C}.\n{T}, Sacrifice a creature: Add {B}{B}.",
                           "Legendary Land")
    assert reading.amount == 1 and reading.sacrifice["taps"] is True
    assert reading.sacrifice["produces"] == {"B": 2}


def test_skirk_prospector_wants_a_goblin():
    found = mana_reading("Sacrifice a Goblin: Add {R}.", "Creature — Goblin").sacrifice
    assert (found["sacrifice"], found["filter"]) == (["creature"], "goblin")


def test_a_food_is_not_an_altar():
    reading = mana_reading("{T}, Sacrifice a Food: Add one mana of any color.",
                           "Creature — Bird")
    assert reading.sacrifice is None and reading.notes


ASHNOD = Card("Ashnod's Altar", 3, 0, 3, ARTIFACT, cost=parse("{3}"),
              sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"})),
              sacrifice_mana_amount=2, sacrifice_mana_color="C")
TOWER = Card("Phyrexian Tower", 0, 0, 0, LAND, mana_abilities=(ManaAbility(FLAT, {"C": 1}),),
             sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"})),
             sacrifice_mana_amount=2, sacrifice_mana_color="B", sacrifice_mana_taps=True)
SKIRK = Card("Skirk Prospector", 1, 0, 0, CREATURE, cost=parse("{R}"),
             creature_types=frozenset({"goblin"}),
             sacrifice_mana=AdditionalCost(sacrifice=frozenset({"creature"}),
                                           sacrifice_filter="goblin"),
             sacrifice_mana_amount=1, sacrifice_mana_color="R")
FIVE = Card("Five Drop", 5, 0, 5, CREATURE, cost=parse("{5}"), priority=80)


def test_the_altar_sacrifices_only_when_it_unlocks_something():
    game = game_with(lands=[FOREST] * 3, others=[ASHNOD], creatures=[BEAR], hand=[FIVE])
    assert agent._try_ritual_line(game, game.pool)
    assert BEAR in game.graveyard and game.pool.total == 5
    game = game_with(lands=[FOREST] * 3, others=[ASHNOD], creatures=[BEAR])
    assert not agent._try_ritual_line(game, game.pool)


def test_the_altar_never_takes_a_mana_creature():
    game = game_with(lands=[FOREST] * 3, others=[ASHNOD], creatures=[ELF], hand=[FIVE])
    assert game.altar_fodder(ASHNOD, game.pool) == []


def test_phyrexian_tower_trades_its_colorless_for_two_black():
    game = game_with(lands=[SWAMP, TOWER], creatures=[BEAR])
    assert game.pool.total == 2
    actions.SacrificeForMana(zone=actions.LANDS, index=1).run(game, None)
    assert game.pool.by_color().get("B") == 3 and game.tapped_lands == 1
    assert not game.altar_fodder(TOWER, game.pool)


def test_skirk_prospector_may_sacrifice_itself_last():
    game = game_with(lands=[FOREST], creatures=[SKIRK])
    assert game.altar_fodder(SKIRK, game.pool) == [SKIRK]


# --- Spirit Guides (E) ------------------------------------------------------------


@pytest.mark.django_db
def test_a_spirit_guide_is_a_free_ritual_from_the_hand():
    card = OracleCard(name="Elvish Spirit Guide", front_name="Elvish Spirit Guide",
                      type_line="Creature — Elf Spirit", mana_cost="{2}{G}", cmc=3,
                      produced_mana=["G"], oracle_text="Exile this card from your hand: Add {G}.")
    profile = derive(card, set())
    assert (profile.kind, profile.mana_amount, profile.mana_from_hand) == (Kind.RITUAL, 1, True)
    assert not profile.needs_review


def test_a_spirit_guide_goes_to_exile():
    guide = Card("Elvish Spirit Guide", 3, 0, 0, RITUAL, cost=parse(""), ritual_gain=1,
                 ritual_color="G", exiled_on_cast=True)
    game = game_with(lands=[FOREST], hand=[guide])
    actions.CastSpell(index=0).run(game, None)
    assert guide in game.exiled and game.pool.total == 2


# --- optional costs (F) -----------------------------------------------------------


def test_a_squad_cost_in_its_reminder_is_optional():
    found = _additional_cost(OracleCard(oracle_text=(
        "Squad {3} (As an additional cost to cast this spell, you may pay {3} any number of "
        "times. When this creature enters, create that many tokens that are copies of it.)")))
    assert found.optional and not found.reason


# --- a creature's {T} (G) ---------------------------------------------------------


def creature_search(text):
    return _land_search(OracleCard(oracle_text=text, type_line="Creature"), Kind.CREATURE).spec


def test_wight_of_the_reliquary_is_read():
    spec = creature_search("{T}, Sacrifice another creature: Search your library for a land "
                           "card, put it onto the battlefield tapped, then shuffle.")
    assert (spec["when"], spec["taps"], spec["sacrifice"], spec["sacrifice_other"]) == (
        "activate", True, False, ["creature"])


def test_knight_of_the_reliquary_wants_a_forest_or_plains():
    spec = creature_search("{T}, Sacrifice a Forest or Plains: Search your library for a land "
                           "card, put it onto the battlefield, then shuffle.")
    assert spec["sacrifices_land"] and spec["land_cost_types"] == ["forest", "plains"]


def test_embodiment_of_spring_sacrifices_itself():
    spec = creature_search("{1}{U}, {T}, Sacrifice this creature: Search your library for a "
                           "basic land card, put it onto the battlefield tapped, then shuffle.")
    assert (spec["cost"], spec["taps"], spec["sacrifice"]) == ("{1}{U}", True, True)


WIGHT = Card("Wight of the Reliquary", 4, 0, 2, CREATURE, cost=parse("{2}{B}{G}"),
             land_search=LandSearch(basic=False, when="activate", taps=True,
                                    sacrifice_other=frozenset({"creature"})))


def test_a_creature_cannot_tap_the_turn_it_arrived():
    game = game_with([ISLAND, ISLAND], lands=[FOREST, FOREST, SWAMP, SWAMP], creatures=[BEAR],
                     hand=[WIGHT])
    actions.CastSpell(index=0).run(game, None)
    assert not game.can_activate(WIGHT, game.pool)
    game.begin_turn()
    game.library = [ISLAND]
    actions.OpenMainPhase().run(game, None)
    assert game.can_activate(WIGHT, game.pool)
    game.activate(WIGHT, game.pool)
    assert BEAR in game.graveyard and ISLAND in game.lands and WIGHT in game.creatures
    # Once a turn.
    assert not game.can_activate(WIGHT, game.pool)


def test_the_agent_trades_a_creature_for_a_land():
    game = game_with([ISLAND], lands=[FOREST] * 2, creatures=[WIGHT, BEAR])
    assert agent._try_activation(game, game.pool)
    assert ISLAND in game.lands


# --- the playtest (H) -------------------------------------------------------------


def test_the_board_offers_each_way_to_pay():
    from playtest.views import altar_labels, payment_labels
    game = game_with(lands=[SWAMP, SWAMP], creatures=[BLOODGHAST, BEAR], others=[ASHNOD],
                     hand=[RITES, BITTER, ISLAND])
    assert payment_labels(game, RITES) == ["sacrifice Bloodghast", "sacrifice Grizzly Bears"]
    assert payment_labels(game, BITTER) == ["pay 3 life", "discard 1 card"]
    assert altar_labels(game)(ASHNOD) == ["Bloodghast", "Grizzly Bears"]


def test_the_form_carries_the_payment():
    from playtest.forms import ActionForm
    form = ActionForm({"kind": "cast_spell", "index": 0, "payment": 1})
    assert form.is_valid() and form.action() == actions.CastSpell(index=0, payment=1)
