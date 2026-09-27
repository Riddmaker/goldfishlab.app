"""The Chainer deck list, as a fixture.

Until Phase 2 these cards sat directly in :mod:`simulation.cards`. They live
here now, so that the engine can play *any* deck and not only this one. The
content is unchanged: the same 70 hand-verified cards with the same values.

This file remains the **reference**. The tests in ``tests/test_cards.py`` and
``tests/test_statistics.py`` check against it, and the adapter has to build
exactly the same ``DeckDefinition`` out of the database - which is the evidence
that the generalization changed nothing.

The card data was taken from ``deck-v2.md`` and verified against Scryfall's
``/cards/collection`` endpoint (see CLAUDE.md for that API's traps): 1
commander in the command zone plus 99 cards = 31 swamps + 4 utility lands +
64 non-land cards.
"""

from dataclasses import replace

from simulation.cards import (
    ARTIFACT,
    BASIC_SWAMP,
    BOG,
    COFFERS,
    CREATURE,
    DOUBLE_SUBTYPE,
    ENCHANTMENT,
    FLAT,
    INSTANT,
    LAND,
    PER_CONTROLLED,
    PLANESWALKER,
    RITUAL,
    ROCK,
    SORCERY,
    SWAMP_SUBTYPE,
    TOWER,
    TYPE_ADDING,
    URBORG,
    Card,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    ManaAbility,
    TutorSpec,
    UpkeepSpec,
    _t,
)
from simulation.fixtures._priorities import PRIORITIES

# --- Commander -------------------------------------------------------------

COMMANDER = Card(
    "Chainer, Dementia Master", 5, 2, 3, CREATURE,
    _t("commander", "reanimate", "engine"),
    priority=81,
)

# --- Lands -----------------------------------------------------------------

SWAMP = Card(
    "Swamp", 0, 0, 0, LAND, _t("land"), land_type=BASIC_SWAMP,
    subtypes=frozenset({SWAMP_SUBTYPE}),
    mana_abilities=(ManaAbility(FLAT, {"black": 1}),),
)

UTILITY_LANDS = [
    # Not a swamp itself: without Urborg, Coffers taps for nothing at all and
    # can only activate its {2} ability.
    Card("Cabal Coffers", 0, 0, 0, LAND, _t("land", "ramp"), land_type=COFFERS,
         mana_abilities=(ManaAbility(PER_CONTROLLED, activation_generic=2,
                                     subtype=SWAMP_SUBTYPE),)),
    Card("Urborg, Tomb of Yawgmoth", 0, 0, 0, LAND, _t("land", "ramp"),
         land_type=URBORG,
         mana_abilities=(ManaAbility(TYPE_ADDING, subtype=SWAMP_SUBTYPE),)),
    Card("Phyrexian Tower", 0, 0, 0, LAND, _t("land", "sac_outlet"),
         land_type=TOWER,
         mana_abilities=(ManaAbility(FLAT, {"colorless": 1}),)),
    # Taps for {B} but is no swamp - Coffers does not count it without Urborg.
    Card("Bojuka Bog", 0, 0, 0, LAND, _t("land"), land_type=BOG,
         enters_tapped=True,
         mana_abilities=(ManaAbility(FLAT, {"black": 1}),)),
]

# --- Non-land cards (64) ---------------------------------------------------

SPELLS = [
    # --- Mana value 1 ---
    Card("Carrion Feeder", 1, 1, 0, CREATURE, _t("creature", "sac_outlet")),
    Card("Dark Ritual", 1, 1, 0, RITUAL, _t("ritual"), ritual_gain=3,
         accelerant=True),
    Card("Gravecrawler", 1, 1, 0, CREATURE, _t("creature", "recursive")),
    Card("Innocent Blood", 1, 1, 0, SORCERY, _t("removal"),
         goldfish_castable=False),
    Card("Phyrexian Reclamation", 1, 1, 0, ENCHANTMENT, _t("recursion")),
    Card("Reanimate", 1, 1, 0, SORCERY, _t("reanimate"),
         needs_creature_in_yard=True),
    Card("Sol Ring", 1, 0, 1, ROCK, _t("ramp"),
         mana_abilities=(ManaAbility(FLAT, {"colorless": 2}),), priority=100,
         accelerant=True),
    Card("Tragic Slip", 1, 1, 0, INSTANT, _t("removal"),
         goldfish_castable=False),

    # --- Mana value 2 ---
    Card("Animate Dead", 2, 1, 1, ENCHANTMENT, _t("reanimate"),
         needs_creature_in_yard=True),
    Card("Arcane Signet", 2, 0, 2, ROCK, _t("ramp"),
         mana_abilities=(ManaAbility(FLAT, {"black": 1}),), priority=92, accelerant=True),
    Card("Blood Artist", 2, 1, 1, CREATURE, _t("creature", "drain_payoff")),
    Card("Bloodghast", 2, 2, 0, CREATURE, _t("creature", "recursive")),
    Card("Cabal Ritual", 2, 1, 1, RITUAL, _t("ritual"), ritual_gain=3),
    Card("Charcoal Diamond", 2, 0, 2, ROCK, _t("ramp"), enters_tapped=True,
         mana_abilities=(ManaAbility(FLAT, {"black": 1}),), priority=86, accelerant=True),
    # Life equal to the drawn card's mana value, not a fixed amount.
    Card("Dark Confidant", 2, 1, 1, CREATURE, _t("creature", "draw_engine"),
         upkeep=UpkeepSpec(draw=1, life_per_mv=True), priority=85),
    Card("Dauthi Voidwalker", 2, 2, 0, CREATURE, _t("creature", "evasion")),
    Card("Demonic Tutor", 2, 1, 1, SORCERY, _t("tutor"),
         tutor=TutorSpec(to_hand=True, count=1), priority=94),
    Card("Feed the Swarm", 2, 1, 1, SORCERY, _t("removal"),
         goldfish_castable=False),
    Card("Gate to Phyrexia", 2, 2, 0, ENCHANTMENT, _t("sac_outlet")),
    Card("Hymn to Tourach", 2, 2, 0, SORCERY, _t("lock")),
    Card("Infernal Grasp", 2, 1, 1, INSTANT, _t("removal"),
         goldfish_castable=False),
    # Makes no mana: it only lowers costs. So it is not an accelerant in the
    # sense of the mulligan rule either.
    Card("Jet Medallion", 2, 0, 2, ROCK, _t("ramp", "cost_reducer"),
         cost_reduction=CostReduction(amount=1), priority=88),
    Card("Liliana's Caress", 2, 1, 1, ENCHANTMENT, _t("drain_payoff")),
    Card("Mind Stone", 2, 0, 2, ROCK, _t("ramp"),
         mana_abilities=(ManaAbility(FLAT, {"colorless": 1}),), priority=90, accelerant=True),
    Card("Nether Traitor", 2, 2, 0, CREATURE, _t("creature", "recursive",
                                                 "evasion")),
    Card("Night's Whisper", 2, 1, 1, SORCERY, _t("draw"),
         draw_on_cast=2, life_on_cast=2, priority=75),
    Card("Reassembling Skeleton", 2, 1, 1, CREATURE,
         _t("creature", "recursive")),
    Card("Terror", 2, 1, 1, INSTANT, _t("removal"), goldfish_castable=False),

    # --- Mana value 3 ---
    Card("Ashnod's Altar", 3, 0, 3, ARTIFACT, _t("sac_outlet", "ramp")),
    Card("Bottomless Pit", 3, 2, 1, ENCHANTMENT, _t("lock")),
    Card("Braids, Arisen Nightmare", 3, 2, 1, CREATURE,
         _t("creature", "lock", "draw_engine")),
    Card("Buried Alive", 3, 1, 2, SORCERY, _t("enabler"),
         tutor=TutorSpec(to_hand=False, count=3, kind=CREATURE), priority=58),
    Card("Dauthi Embrace", 3, 1, 2, ENCHANTMENT, _t("evasion")),
    Card("Fleshbag Marauder", 3, 1, 2, CREATURE, _t("creature", "removal")),
    Card("Grim Tutor", 3, 2, 1, SORCERY, _t("tutor"),
         tutor=TutorSpec(to_hand=True, count=1, life=3), priority=76),
    Card("Hypnotic Specter", 3, 2, 1, CREATURE, _t("creature", "evasion",
                                                   "lock")),
    Card("Morbid Opportunist", 3, 1, 2, CREATURE,
         _t("creature", "draw_engine")),
    Card("Necromancy", 3, 1, 2, ENCHANTMENT, _t("reanimate"),
         needs_creature_in_yard=True),
    Card("Necropotence", 3, 3, 0, ENCHANTMENT, _t("draw_engine", "gamechanger"),
         skips_draw_step=True, end_step=EndStepSpec(max_hand=7, life_floor=25),
         priority=96),
    Card("Phyrexian Arena", 3, 2, 1, ENCHANTMENT, _t("draw_engine"),
         upkeep=UpkeepSpec(draw=1, life=1), priority=87),
    Card("Phyrexian Tribute", 3, 1, 2, SORCERY, _t("removal"),
         goldfish_castable=False),
    Card("Toxic Deluge", 3, 1, 2, SORCERY, _t("wipe"), goldfish_castable=False),
    Card("Vampire Nighthawk", 3, 2, 1, CREATURE, _t("creature", "evasion")),
    Card("Victimize", 3, 1, 2, SORCERY, _t("reanimate"),
         needs_creature_in_yard=True, needs_creature_on_bf=True),

    # --- Mana value 4 ---
    Card("Crypt Ghast", 4, 1, 3, CREATURE, _t("creature", "ramp"),
         mana_abilities=(ManaAbility(DOUBLE_SUBTYPE, subtype=SWAMP_SUBTYPE),),
         priority=84),
    Card("Dread Presence", 4, 1, 3, CREATURE,
         _t("creature", "draw_engine", "drain_payoff")),
    Card("Grave Pact", 4, 3, 1, ENCHANTMENT, _t("lock", "engine")),
    Card("Greed", 4, 1, 3, ENCHANTMENT, _t("draw_engine")),
    Card("Helm of Possession", 4, 0, 4, ARTIFACT, _t("steal", "sac_outlet")),
    Card("Mutilate", 4, 2, 2, SORCERY, _t("wipe"), goldfish_castable=False),
    Card("Nekrataal", 4, 2, 2, CREATURE, _t("creature", "removal")),
    Card("Ritual of the Machine", 4, 2, 2, SORCERY, _t("steal"),
         goldfish_castable=False),
    Card("Snuff Out", 4, 1, 3, INSTANT, _t("removal"), goldfish_castable=False),
    Card("Tainted Aether", 4, 2, 2, ENCHANTMENT, _t("lock")),

    # --- Mana value 5 ---
    Card("Cao Cao, Lord of Wei", 5, 2, 3, CREATURE, _t("creature", "lock")),
    Card("Exquisite Blood", 5, 1, 4, ENCHANTMENT, _t("drain_payoff")),
    Card("Gray Merchant of Asphodel", 5, 2, 3, CREATURE,
         _t("creature", "drain_payoff")),
    Card("Living Death", 5, 2, 3, SORCERY, _t("reanimate", "wipe")),
    Card("Painful Quandary", 5, 2, 3, ENCHANTMENT, _t("lock")),
    Card("Syr Konrad, the Grim", 5, 2, 3, CREATURE,
         _t("creature", "drain_payoff")),
    Card("Tergrid, God of Fright", 5, 2, 3, CREATURE,
         _t("creature", "steal", "gamechanger")),

    # --- Mana value 6 ---
    Card("Liliana, Dreadhorde General", 6, 2, 4, PLANESWALKER,
         _t("draw_engine")),
    Card("Mikaeus, the Unhallowed", 6, 3, 3, CREATURE,
         _t("creature", "engine")),

    # --- Mana value 7 ---
    Card("Lim-Dul the Necromancer", 7, 2, 5, CREATURE, _t("creature", "steal")),
]

SWAMP_COUNT = 31


# The remaining priorities are baked in here rather than written onto every
# card line. Priority is an opinion about how to play, not a property of a
# card: the same card is worth something else in a different deck. In the
# application the adapter supplies them from the user's annotations.
SPELLS = [
    replace(card, priority=PRIORITIES[card.name])
    if card.priority is None and card.name in PRIORITIES
    else card
    for card in SPELLS
]

# Cards with no entry deliberately keep ``priority=None``. That does not mean
# "forgotten" but "no opinion": the agent applies its default rule to them
# (cheaper first). Removal and wipes are never on the table in a goldfish
# anyway, because they have no legal target.


def build_deck():
    """Build the deck's 99 cards (without the commander, which sits apart).

    Returns:
        list[Card]: 31 swamps + 4 utility lands + 64 spells.
    """
    deck = [SWAMP] * SWAMP_COUNT + list(UTILITY_LANDS) + list(SPELLS)
    return deck


def land_count(deck=None) -> int:
    """How many lands are in the deck."""
    deck = build_deck() if deck is None else deck
    return sum(1 for card in deck if card.is_land)


#: The deck as a :class:`~simulation.cards.DeckDefinition`, which is what the
#: engine expects from Phase 2 on.
DECK = DeckDefinition(
    name="Chainer, Dementia Master",
    commander=COMMANDER,
    library=tuple(build_deck()),
)
