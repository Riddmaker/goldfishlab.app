"""How a card is made: the fields of its rules, turned into its blocks (card model phase 5).

A `~simulation.cards.Card` holds what is printed on it, its play hints and
its blocks (``abilities``). Its rules are written as the fields below -
what the adapter compiles a profile into, what a test or a fixture deck
spells out - and `card` turns them into blocks with each family's
``encode``: the mana family (`simulation.rules.mana`), lands, costs and
spells (`simulation.rules.play`), and triggers, turns and combat
(`simulation.rules.turns`).

The way is one way only. Nothing turns blocks back into these fields: the
engine and the application read the compiled views (`Card.mana`, `play`,
`turn`), and a card is stored as its blocks (`simulation.serial`). Until
phase 5 a card took these fields as arguments and decoded them again for
storing; a card stored so still loads, through `MadeWith`.
"""

from dataclasses import dataclass, fields

from simulation.cards import (
    AdditionalCost,
    Card,
    CombatTrigger,
    EndStepSpec,
    Growth,
    LandSearch,
    Trigger,
    TutorSpec,
    UpkeepSpec,
)
from simulation.manacost import ManaCost
from simulation.spec import CostReduction, ManaAbility, Store, TapMana, TappedUnless


@dataclass(frozen=True)
class MadeWith:
    """The fields of a card's rules, each at the value that makes no block."""

    enters_tapped: bool = False
    mana_abilities: tuple[ManaAbility, ...] = ()
    ritual_gain: int = 0  # Dark Ritual: +3 black
    ritual_color: str = "B"  # ... but Rite of Flame red
    cost_reduction: CostReduction | None = None
    draw_on_cast: int = 0
    life_on_cast: int = 0
    tutor: TutorSpec | None = None
    upkeep: UpkeepSpec | None = None
    end_step: EndStepSpec | None = None
    skips_draw_step: bool = False  # Necropotence
    #: False for a permanent that "doesn't untap during your untap step" -
    #: Mana Vault, Grim Monolith. It makes its mana the first time the pool is
    #: opened with it on the battlefield and stays tapped from then on; see
    #: ``Game.stays_tapped``.
    untaps: bool = True
    #: P19 R2: lands this card puts onto the battlefield, and when.
    land_search: LandSearch | None = None
    #: P19 R3: with ``enters_tapped``, the condition under which it does not.
    tapped_unless: TappedUnless | None = None
    #: P19 R7: Treasure tokens it makes as it resolves - a spell, or a
    #: permanent's "When this creature enters" (Big Score: 2, Rapacious
    #: Dragon: 2). Each is one mana of ``treasure_mana``, sacrificed when spent.
    treasures: int = 0
    treasure_mana: str = ""
    #: P19 R14: Lander tokens it makes as it resolves or enters, each a
    #: :data:`LANDER` search to activate later.
    landers: int = 0
    #: P19 R7: "As an additional cost to cast this spell, discard a card."
    discard_cost: int = 0
    #: P19 R8: cards discarded right after ``draw_on_cast`` - Faithless
    #: Looting draws two, then discards two.
    discard_on_cast: int = 0
    #: P19 R8: cards put back on top of the library after that draw -
    #: Brainstorm draws three, then puts two back.
    put_back_on_cast: int = 0
    #: P19 R9: it draws X cards (Stroke of Genius, Blue Sun's Zenith), on
    #: top of ``draw_on_cast``.
    draws_x: bool = False
    #: P19 R13: a ritual whose mana counts the board when it resolves:
    #: ``creature`` (Battle Hymn) or ``tapped:<land type>`` (High Tide: each
    #: such land in this turn's pool, less the one that paid for it). P19
    #: R18: ``named:<name>`` adds one more for each card of that name in the
    #: graveyard (Rite of Flame), ``threshold:<cards>:<amount>`` makes
    #: ``amount`` instead with that many cards there (Cabal Ritual).
    ritual_counts: str = ""
    #: P19 R15: the ways its additional cost can be paid, any one of them -
    #: empty for none. "Discard a card" alone stays ``discard_cost``.
    additional_costs: tuple[AdditionalCost, ...] = ()
    #: P19 R15: a mana ability whose cost is sacrificing another permanent -
    #: Ashnod's Altar, Phyrexian Tower: the types it takes (see
    #: :class:`AdditionalCost`), what one gives, and in what colour.
    sacrifice_mana: AdditionalCost | None = None
    sacrifice_mana_amount: int = 0
    sacrifice_mana_color: str = ""
    #: P19 R15: {T} is part of that ability's cost (Phyrexian Tower).
    sacrifice_mana_taps: bool = False
    #: P19 R16: "Sacrifice another creature" - never the altar itself
    #: (Warren Soultrader). Its life, if any, is ``sacrifice_mana.life``.
    sacrifice_mana_other: bool = False
    #: Engine v33: cards drawn beside the mana (Priest of Forgotten Gods).
    sacrifice_mana_draw: int = 0
    #: P19 R16: the triggered abilities the engine plays.
    triggers: tuple[Trigger, ...] = ()
    #: P19 R15: it goes to exile rather than the graveyard once cast - a
    #: Spirit Guide, a ritual exiled from the hand for its mana.
    exiled_on_cast: bool = False
    #: P19 R19: mana for tapping other permanents (Relic of Legends, Urza).
    tap_mana: TapMana | None = None
    #: P19 R19: ``DISCARD_LAND`` or ``IMPRINT``, see there; empty for none.
    mox: str = ""
    #: P19 R19: cards it mills each time it is tapped for mana (Millikin).
    mana_mills: int = 0
    #: P19 R20: mana it keeps as counters, energy or a Food. Its
    #: ``mana_abilities`` are what it taps for without them.
    store: Store | None = None
    #: P19 R21: +1/+1 counters and what grows with them.
    growth: Growth | None = None
    #: P19 R21: Growing Rites of Itlimoc - it turns into its land at your end
    #: step once you control this many creatures, and makes mana only then.
    #: 0: no such card.
    flip: int = 0
    #: P19 R22: its triggers in combat.
    combat: tuple[CombatTrigger, ...] = ()
    #: P19 R22: an Equipment's equip cost - read only where something rides
    #: on it (a trigger in combat, Paradise Mantle's mana). None: not equipped.
    equip: ManaCost | None = None
    #: P19 R22: Living weapon - it enters with a 0/0 Germ attached.
    germ: bool = False
    #: P19 R22: each Treasure makes this much more mana (Goldspan Dragon).
    treasure_bonus: int = 0

#: The names of the fields, in the order a card declared them.
FIELDS = tuple(item.name for item in fields(MadeWith))


def families() -> tuple:
    """The modules of the families, each with ``FIELDS`` and ``encode``,
    in the order a card's blocks are made. Imported when first asked: they
    import :mod:`simulation.cards`."""
    from simulation.rules import mana, play, turns

    return (mana, play, turns)


def blocks(**values) -> tuple:
    """The blocks the fields given make, each family's in turn."""
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise TypeError(f"no such field of a card's rules: {', '.join(sorted(unknown))}")
    found: tuple = ()
    for family in families():
        own = {name: value for name, value in values.items() if name in family.FIELDS}
        if own:
            found += family.encode(own)
    return found


def card(*printed, abilities: tuple = (), **values) -> Card:
    """A card: printed as ``printed`` and the `Card` fields among ``values``,
    its rules made from the rest. ``abilities`` are blocks of its own, which
    come first, as a source's do."""
    rules = {name: value for name, value in values.items() if name in _FIELDS}
    face = {name: value for name, value in values.items() if name not in _FIELDS}
    return Card(*printed, abilities=tuple(abilities) + blocks(**rules), **face)


_FIELDS = frozenset(FIELDS)
