"""The card model the simulation runs on.

A `Card` here is not a Magic card. It is the small set of facts the engine
needs in order to play one: what it costs, what it makes, when the agent wants
to cast it, and the handful of triggers that change how a turn goes. Everything
else about a real card - its art, its rules text, its flavour - is deliberately
absent, because the engine cannot use it.

Since Phase 2 the deck list itself lives in :mod:`simulation.fixtures.chainer`,
not here. A shim at the bottom of this module keeps the old import paths
working, because the four vendored test files must stay byte-identical.
"""

from dataclasses import dataclass, field

from simulation.manacost import (
    COLORLESS,
    SUBTYPE_COLORS,
    ManaCost,
    normalised,
)

# --- Card kinds ------------------------------------------------------------

LAND = "land"
ROCK = "rock"
RITUAL = "ritual"
CREATURE = "creature"
ENCHANTMENT = "enchantment"
ARTIFACT = "artifact"
SORCERY = "sorcery"
INSTANT = "instant"
PLANESWALKER = "planeswalker"

# --- Land subtypes (for Cabal Coffers / Urborg / Crypt Ghast) --------------

BASIC_SWAMP = "basic_swamp"
COFFERS = "coffers"
URBORG = "urborg"
TOWER = "tower"
BOG = "bog"

# --- Mana abilities --------------------------------------------------------
#
# From Phase 2 on, four rules replace every hardwired special case. Cabal
# Coffers, Urborg and Crypt Ghast stopped being exceptions and became instances
# of these rules - while ``tests/test_mana.py`` goes on pinning their exact
# numbers.

#: ``{T}: Add {B}`` / ``{T}: Add {C}{C}`` - basics, Sol Ring, mana creatures.
#: With an ``activation_generic`` it is a Signet: ``{1}, {T}: Add {U}{B}``.
FLAT = "flat"

#: ``{2}, {T}: Add {B} for each <subtype> you control`` - Cabal Coffers,
#: Nykthos, Gaea's Cradle, Serra's Sanctum.
PER_CONTROLLED = "per_controlled"

#: Each tapped <subtype> makes one extra {B} - Crypt Ghast, Nirkana Revenant,
#: Zendikar Resurgent.
DOUBLE_SUBTYPE = "double_subtype"

#: Every land counts as a <subtype> as well - Urborg, Yavimaya.
TYPE_ADDING = "type_adding"

#: One mana of any colour a land you control could produce - Reflecting Pool,
#: Incubation Druid (P19 R5). Which colours is the board's answer, so the
#: game works it out each turn (`Game.mana_ability`).
LANDS_COULD_PRODUCE = "lands_could_produce"

#: ``{T}: Add {G} for each creature you control`` - mana that counts the
#: board (P19 R11). ``subtype`` says what is counted: ``creature``, a creature
#: type (``elf``), ``basic:swamp``, ``graveyard:B`` (black creature cards in
#: the graveyard), ``devotion`` (Nykthos: the colour with most), or
#: ``names:A|B`` (Tron: ``produces`` once every named land is there). The game
#: works the amount out each turn (`Game.mana_ability`).
COUNTS = "counts"

#: Mana on top of what a source makes as it is tapped (P19 R13). ``subtype``
#: says which sources: ``enchanted`` (the land an Aura is on - Wild Growth;
#: ``enchanted:forest`` for Utopia Sprawl), ``land`` (every land - Mirari's
#: Wake), ``nonland`` (Kinnan), ``creature`` (Badgermole Cub), ``colorless``
#: (a source tapped for {C} - Forsaken Monument), ``chosen_land`` (a land
#: making the chosen colour - Caged Sun) and ``chosen_basic`` (Gauntlet of
#: Power). ``produces`` is the bonus; empty means one mana of a type the
#: source made, and ``color="chosen"`` the colour chosen as it entered.
EXTRA = "extra"

#: A permanent that taps for ``times`` as much (P19 R13): Mana Reflection
#: twice, Nyxbloom Ancient three times.
MULTIPLY = "multiply"

#: Other permanents have a mana ability (P19 R13): ``subtype`` ``creature``
#: (Cryptolith Rite: each creature taps for ``produces``) or ``enchanted``
#: (Abundant Growth: the enchanted land taps for any colour instead).
GRANT = "grant"

#: ``{W/B}, {T}: Add {W}{W}, {W}{B}, or {B}{B}`` - a filter land - and
#: ``{1}, {T}: Add one mana of any color`` - a converter, Study Hall (P19 R6).
#: One mana goes in (``pays_with``: the colours it may be; empty = any) and
#: ``produces`` comes out, in place of the card's plain ``FLAT`` ability.
FILTER = "filter"

#: The subtype the mono-black rules are written against.
SWAMP_SUBTYPE = "swamp"

#: The other four basic subtypes. Together with ``SUBTYPE_COLORS`` from
#: :mod:`simulation.manacost` they give the colour a land taps for.
PLAINS_SUBTYPE = "plains"
ISLAND_SUBTYPE = "island"
MOUNTAIN_SUBTYPE = "mountain"
FOREST_SUBTYPE = "forest"

#: The card types of rule 205.2a that a Commander deck can hold, in the order
#: a deck list sorts them. Kindred is left out on purpose: it never stands
#: alone ("Kindred Instant"), so the other word on the line already counts the
#: card. What ``Card.types`` may contain.
CARD_TYPES = (
    "creature", "planeswalker", "battle", "artifact", "enchantment",
    "instant", "sorcery", "land",
)


@dataclass(frozen=True)
class TappedUnless:
    """When a land that enters tapped does not (P19 R3, engine version 7).

    ``kind`` says which question is asked of the game:

    * ``control_type`` - you control a land of one of ``types`` (check lands)
    * ``lands`` - you control ``count`` or more (``at_least``) or at most
      ``count`` lands, ``other`` than this one, ``basic`` ones, or of ``type``
      (fast, slow and battle lands, Mystic Sanctuary)
    * ``opponents`` - you have ``count`` or more opponents: a Commander table
      has three
    * ``reveal`` - you can reveal a card of one of ``types`` from your hand
    * ``pay_life`` - you pay ``count`` life (shock lands), never below the
      Phyrexian life floor
    * ``artifacts`` - you control ``count`` or more artifacts (Mox Opal,
      Spire of Industry)
    * ``permanent`` - you control a permanent of one of ``types``, a
      ``legendary`` one if set (Minas Tirith, Dedicated Commons; P19 R12)
    * ``turn`` - it is your turn ``count`` or earlier (Starting Town)
    * ``opponent_lands`` - your opponents control ``count`` or more lands,
      assuming each of three plays one a turn (the Turbulent lands)
    * ``life_at_most`` - you have ``count`` or less life; the opponents'
      life is unknown, so this holds less often than at a table

    Since engine version 8 the same question also guards a mana ability
    (P19 R4): "Activate only if you control five or more lands" is a
    ``ManaAbility`` whose ``only_if`` is ``TappedUnless("lands", count=5)``.
    The name stays because R3 put it into stored games.
    """

    kind: str
    types: frozenset[str] = field(default_factory=frozenset)
    count: int = 0
    at_least: bool = True
    other: bool = False
    basic: bool = False
    type: str = ""
    legendary: bool = False


@dataclass(frozen=True)
class ManaAbility:
    """A mana ability as a rule rather than as a card name.

    Since colour arrived, the amount produced lives in ``produces``, a canonical
    colour -> amount mapping. It used to be two integers (``black``,
    ``colorless``), which forced every non-black source to be read as
    colourless - a forest made mana that no green spell could spend.

    ``black`` and ``colorless`` survive as properties, because the engine and
    the adapter ask for them in several places.

    Attributes:
        rule: One of the four constants above.
        produces: Mana made by a ``FLAT`` ability, e.g. ``{"B": 1}`` or
            ``{"green": 1, "colorless": 1}``. Either spelling is accepted.
        activation_generic: Generic activation cost. Cabal Coffers costs {2};
            without that cost it would be worth activating from the first swamp
            onwards, and the early turns would look better than they are. A
            ``FLAT`` ability can carry one too since engine version 3: a Signet
            is ``ManaAbility(FLAT, {"U": 1, "B": 1}, activation_generic=1)``,
            which nets one mana - read without it, it netted two.
        subtype: The subtype the rule refers to.
        color: The colour the scaling rules (``PER_CONTROLLED``,
            ``DOUBLE_SUBTYPE``) make. Empty means the subtype's colour, so
            black for swamps and green for forests.
        only_if: A condition the game checks before the ability counts. A
            card may carry a conditional ``FLAT`` ability first and an
            unconditional one after it: a Tainted land's {B}/{G} needs a
            Swamp, its {C} does not.
    """

    rule: str
    produces: tuple[tuple[str, int], ...] = ()
    activation_generic: int = 0
    subtype: str = ""
    color: str = ""
    #: The ability can be activated only while this holds (P19 R4): Temple
    #: of the False God, a Tainted land, Mox Opal. None: always.
    only_if: TappedUnless | None = None
    #: A ``FILTER``'s one mana of input: the colours it may be paid with,
    #: "WB" for {W/B}; empty for {1}, any mana (P19 R6).
    pays_with: str = ""
    #: A ``MULTIPLY``'s factor: 2 for twice as much (P19 R13).
    times: int = 1

    def __post_init__(self):
        # Canonicalise, so that two abilities making the same mana compare
        # equal - whether they came from the fixture or from the database.
        object.__setattr__(self, "produces", normalised(self.produces))

    def amount(self, color: str) -> int:
        """How much mana of this colour the ability makes."""
        return dict(self.produces).get(color, 0)

    @property
    def black(self) -> int:
        return self.amount("B")

    @property
    def colorless(self) -> int:
        return self.amount(COLORLESS)

    @property
    def total(self) -> int:
        return sum(amount for _, amount in self.produces)

    @property
    def colored_total(self) -> int:
        """Mana made that is *coloured*. Colourless does not count."""
        return sum(amount for source, amount in self.produces if source != COLORLESS)

    @property
    def scaling_color(self) -> str:
        """The colour a scaling rule makes mana in.

        Absent an explicit one, the subtype's colour: Cabal Coffers counts
        swamps and makes black, while Gaea's Cradle needs ``color="G"`` because
        it counts creatures and names no land type.
        """
        return self.color or SUBTYPE_COLORS.get(self.subtype, COLORLESS)


@dataclass(frozen=True)
class CostReduction:
    """A Jet Medallion style cost reduction.

    Reduces the generic portion **only**, never a coloured symbol - Necropotence
    still costs {B}{B}{B} with a medallion out.
    """

    amount: int = 1
    #: Only spells with at least one pip of this colour benefit.
    requires_pip: bool = True
    #: The colour ``requires_pip`` refers to. Jet Medallion discounts black
    #: spells, Emerald Medallion green ones; black used to be the only colour
    #: there was.
    color: str = "B"


@dataclass(frozen=True)
class UpkeepSpec:
    """An upkeep trigger that draws cards and costs life.

    ``life_per_mv`` is how Dark Confidant is modelled: he costs the drawn card's
    mana value in life, not a fixed amount.
    """

    draw: int = 1
    life: int = 0
    life_per_mv: bool = False


@dataclass(frozen=True)
class EndStepSpec:
    """Necropotence: trade life for cards in the end step.

    ``life_floor`` is deliberately conservative. The agent never pays below it,
    because a goldfish has no opponent to punish him for it - without a floor he
    would draw himself down to 1 life and make the deck look better than it is.
    """

    max_hand: int = 7
    life_floor: int = 25


@dataclass(frozen=True)
class TutorSpec:
    """A tutor: search the library for a card.

    Attributes:
        to_hand: To hand (Demonic Tutor) rather than to the graveyard.
        count: How many cards are searched for (Buried Alive: 3).
        life: Life cost (Grim Tutor: 3).
        kind: Restrict the search to this card kind; empty means any.
    """

    to_hand: bool = True
    count: int = 1
    life: int = 0
    kind: str = ""
    #: P19 R10: straight onto the battlefield (Green Sun's Zenith); then
    #: ``to_hand`` says nothing. ``kind`` is matched against the card's types
    #: there, so Whir of Invention finds a mana rock as well as an artifact.
    to_battlefield: bool = False
    #: P19 R10: "a green creature card": the colour its cost must have.
    color: str = ""
    #: P19 R10: "with mana value 3 or less", or with ``max_mv_x`` "X or less".
    max_mv: int | None = None
    max_mv_x: bool = False
    #: P19 R15: Eldritch Evolution - "X is 2 plus the sacrificed creature's
    #: mana value": this much more than what its additional cost sacrificed.
    max_mv_sacrificed: int | None = None
    #: P19 R12: on top of the library, drawn next turn (Vampiric Tutor); then
    #: ``to_hand`` says nothing. ``types`` limits it by card type, any of them
    #: (Enlightened Tutor: artifact or enchantment); empty means any card.
    to_top: bool = False
    types: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class LandSearch:
    """A search that puts lands onto the battlefield (P19 R2, engine version 6).

    Rampant Growth, Cultivate, a fetch land, Wood Elves, Sakura-Tribe Elder.

    Attributes:
        battlefield: How many lands go onto the battlefield.
        hand: How many go to hand after them (Cultivate: one).
        tapped: Whether the ones on the battlefield enter tapped.
        basic: Only basic lands.
        types: The land types it may find (Farseek: plains, island, swamp,
            mountain); empty means any land.
        life: Life paid (a fetch land: 1).
        when: ``cast`` (a spell), ``enters`` (a permanent's arrival),
            ``play`` (a fetch land, the moment it is played) or ``activate``
            (Wayfarer's Bauble, Myriad Landscape: an ability paid for with
            ``cost``; P19 R14).
        sacrifice: The card itself goes to the graveyard (a fetch land,
            Sakura-Tribe Elder).
        untap_at: Fabled Passage: the land is untapped once you control at
            least this many lands. 0: never.
        cost: What activating it costs, the card's own sacrifice aside.
        taps: ``{T}`` is part of that cost: a land that pays it makes no
            mana this turn.
        share_type: Myriad Landscape: every land found shares a land type.
        each: Krosan Verge: one land of each of ``types``.
        condition: ``opponent_more_lands`` - Knight of the White Orchid:
            only if an opponent controls more lands than you, which rests on
            the assumption that each opponent plays a land a turn.
        sacrifices_land: Springbloom Druid: a land of yours is sacrificed
            first (P19 R15). Worth it only when more lands come back. On an
            activated search it is part of the cost: Elvish Reclaimer.
        land_cost_types: That land must be of one of these types: Knight of
            the Reliquary's "a Forest or Plains". Empty: any land.
        sacrifice_other: Wight of the Reliquary: another permanent of these
            types is sacrificed as part of the cost.
        discard: Cards discarded as part of the cost (Silverglade Pathfinder).
    """

    battlefield: int = 1
    hand: int = 0
    tapped: bool = True
    basic: bool = True
    types: frozenset[str] = field(default_factory=frozenset)
    life: int = 0
    when: str = "cast"
    sacrifice: bool = False
    untap_at: int = 0
    cost: ManaCost | None = None
    taps: bool = False
    share_type: bool = False
    each: bool = False
    condition: str = ""
    sacrifices_land: bool = False
    land_cost_types: frozenset[str] = field(default_factory=frozenset)
    sacrifice_other: frozenset[str] = field(default_factory=frozenset)
    discard: int = 0


@dataclass(frozen=True)
class AdditionalCost:
    """One way to pay "As an additional cost to cast this spell, ..." (P19 R15).

    A card holds a tuple of them: one for "sacrifice a creature", two for
    "discard a card or pay 3 life", and the agent pays the cheapest.

    Attributes:
        sacrifice: The card types a permanent sacrificed may have, any of
            them (``{"artifact", "creature"}`` for Deadly Dispute). Empty: no
            sacrifice.
        sacrifice_filter: What the permanent must also be: a colour letter
            (Natural Order: ``G``), ``legendary``, or a creature type
            (``goblin``).
        life: Life paid (Bitter Triumph: 3).
        life_x: "Pay X life" (Toxic Deluge): paid with X = 0, because in a
            goldfish there is nothing for X to kill.
        discard: Cards discarded, the weakest first.
        mana: Mana paid on top of the printed cost (Redirect Lightning's {2}).
        exile_from_graveyard: A card of this type exiled from the graveyard.
    """

    sacrifice: frozenset[str] = field(default_factory=frozenset)
    sacrifice_filter: str = ""
    life: int = 0
    life_x: bool = False
    discard: int = 0
    mana: ManaCost | None = None
    exile_from_graveyard: str = ""


#: What a Lander token does (P19 R14): "{2}, {T}, Sacrifice this token: Search
#: your library for a basic land card, put it onto the battlefield tapped".
LANDER = LandSearch(when="activate", cost=ManaCost(generic=2), taps=True, sacrifice=True)


#: A card with no mana production of its own.
NO_ABILITIES: tuple[ManaAbility, ...] = ()


@dataclass(frozen=True)
class Card:
    """A card, reduced to what the simulation needs.

    Attributes:
        name: Card name (English, as bought on Cardmarket).
        mv: Mana value (converted mana cost).
        pips: How many {B} symbols the cost has. These *must* be paid with black
            mana - Sol Ring and Mind Stone cannot.
        generic: Generic portion of the cost (mv == pips + generic).
        kind: Card kind, see the constants above.
        tags: The card's roles, for the metrics.
        land_type: Lands only, see the land subtypes above.
        enters_tapped: Enters the battlefield tapped (decisive on turns 1-3).
        goldfish_castable: False for cards with no legal target when there is no
            opponent (removal, wipes). The agent wastes no mana on them.
        needs_creature_in_yard: Reanimation needs a creature in the graveyard.
        needs_creature_on_bf: Needs one of your own creatures (a sacrifice cost).
    """

    name: str
    mv: int
    pips: int
    generic: int
    kind: str
    tags: frozenset[str] = field(default_factory=frozenset)
    land_type: str = ""
    enters_tapped: bool = False
    goldfish_castable: bool = True
    needs_creature_in_yard: bool = False
    needs_creature_on_bf: bool = False

    # --- from Phase 2 on: what used to live in name tables -----------------
    #
    # Every one of these has a default, so that the ~70 existing positional
    # ``Card(...)`` calls keep working unchanged - including the ones in the
    # four byte-identical test files.
    mana_abilities: tuple[ManaAbility, ...] = NO_ABILITIES
    ritual_gain: int = 0                       # Dark Ritual: +3 black
    ritual_color: str = "B"                    # ... but Rite of Flame red
    cost_reduction: CostReduction | None = None
    draw_on_cast: int = 0
    life_on_cast: int = 0
    tutor: TutorSpec | None = None
    upkeep: UpkeepSpec | None = None
    end_step: EndStepSpec | None = None
    skips_draw_step: bool = False              # Necropotence
    priority: int | None = None                # replaces agent.PRIORITY
    accelerant: bool = False                   # replaces game.ACCELERANTS
    subtypes: frozenset[str] = field(default_factory=frozenset)

    #: The full mana cost. ``None`` means the card is described by
    #: ``pips``/``generic``, so mono-black. A new field rather than a wider
    #: ``pips``, because ``tests/test_mana.py`` builds cards positionally -
    #: ``Card("Test", 1, 1, 0, LAND)`` has to keep meaning what it meant.
    cost: ManaCost | None = None

    #: False for a permanent that "doesn't untap during your untap step" -
    #: Mana Vault, Grim Monolith. It makes its mana the first time the pool is
    #: opened with it on the battlefield and stays tapped from then on; see
    #: ``Game.stays_tapped``. Last, and defaulted, for the same positional
    #: reason as ``cost``.
    untaps: bool = True

    #: The card types printed on the front face, lower case: ``{"artifact",
    #: "creature"}`` for an artifact creature. **No rule reads it** - ``kind``
    #: is what the game plays by. It exists for the draw statistics
    #: (``analysis.seen_groups``), which count what a player would call the
    #: card, and ``kind`` cannot say that: it files a mana rock under "rock"
    #: and an artifact creature under "creature" only. Empty for the
    #: hand-written fixture decks, which never carried a type line. Last, and
    #: defaulted, for the same positional reason as ``cost``.
    types: frozenset[str] = field(default_factory=frozenset)

    #: The deck-building categories a player would sort the card into - ramp,
    #: draw, removal and the rest - for the draw statistics only. **Not the
    #: same set as ``tags``**, on purpose: ``tags`` is what the game's metrics
    #: read, and the application's built-in annotations may replace it to keep
    #: the reference deck playing exactly as it always has (Phyrexian Arena is
    #: ``draw_engine`` there and nothing else). The categories are the
    #: community's reading plus the user's own corrections, never the built-in
    #: ones; ``simulations/engine/adapter.py`` decides which. Empty for the
    #: hand-written fixture decks. Last, and defaulted, like ``types``.
    categories: frozenset[str] = field(default_factory=frozenset)

    #: P19 R2: lands this card puts onto the battlefield, and when.
    land_search: LandSearch | None = None
    #: A basic land - what "search for a basic land card" may find.
    basic: bool = False
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
    #: P19 R9: how many {X} the cost has - Astral Cornucopia's {X}{X}{X} is
    #: three. 0: no X. The agent casts such a spell last in its main phase,
    #: with everything left as X, the way Forge's AI picks its X.
    x_count: int = 0
    #: P19 R9: the smallest X worth casting it for. A deck author may want
    #: Stroke of Genius held until X is four.
    x_min: int = 1
    #: P19 R9: it draws X cards (Stroke of Genius, Blue Sun's Zenith), on
    #: top of ``draw_on_cast``.
    draws_x: bool = False
    #: P19 R11: the creature types on the front face, lower case - what
    #: Elvish Archdruid counts. Empty for everything else.
    creature_types: frozenset[str] = field(default_factory=frozenset)
    #: P19 R12: "Legendary" on the front face - what Minas Tirith asks for.
    legendary: bool = False
    #: P19 R13: the card's colours (Scryfall's, not its cost's: a Devoid card
    #: is colourless) - what Bloom Tender counts.
    colors: frozenset[str] = field(default_factory=frozenset)
    #: P19 R13: it has defender - what Overgrown Battlement counts.
    defender: bool = False
    #: P19 R13: an Aura's target: ``land`` or a land type (``forest``). It
    #: cannot be cast without one.
    enchants: str = ""
    #: P19 R13: a ritual whose mana counts the board when it resolves:
    #: ``creature`` (Battle Hymn) or ``tapped:<land type>`` (High Tide: each
    #: such land in this turn's pool, less the one that paid for it).
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
    #: P19 R15: it goes to exile rather than the graveyard once cast - a
    #: Spirit Guide, a ritual exiled from the hand for its mana.
    exiled_on_cast: bool = False

    @property
    def mana_cost(self) -> ManaCost:
        """The cost as a structure, however the card was built.

        The one place where the old two-integer form and the coloured form come
        together. Everything that pays a cost asks here, and so never has to
        know which source the card came from.
        """
        if self.cost is not None:
            return self.cost
        return ManaCost.mono(self.pips, self.generic)

    @property
    def is_land(self) -> bool:
        """True when the card is a land."""
        return self.kind == LAND

    @property
    def produces_mana(self) -> bool:
        """Does the card make mana, whether tapped or cast?"""
        return bool(self.mana_abilities) or self.ritual_gain > 0 or bool(self.ritual_counts)

    @property
    def is_accelerant(self) -> bool:
        """Does this card count as acceleration for the mulligan rule?

        Formerly a name list in ``game.py``. Deliberately a **field and not a
        derived rule**: which cards make a one-land hand keepable is the deck
        author's judgement, not a property of the card. Cabal Ritual makes
        exactly as much mana as Dark Ritual and is still not on the list - a
        mechanical rule would include it, and with it change the keep rate and
        every number underneath.

        In the application, the adapter fills this field from the user's
        annotations.
        """
        return self.accelerant

    def ability(self, rule: str) -> ManaAbility | None:
        """The first mana ability with this rule, if there is one."""
        for ability in self.mana_abilities:
            if ability.rule == rule:
                return ability
        return None

    def has_subtype(self, subtype: str) -> bool:
        """Does the card carry this subtype by itself?"""
        return subtype in self.subtypes

    @property
    def is_swamp(self) -> bool:
        """True for basic swamps only. Urborg turns other lands into swamps,
        which :mod:`simulation.mana` works out at runtime."""
        return self.land_type == BASIC_SWAMP

    def __str__(self) -> str:
        return self.name


def _t(*tags: str) -> frozenset[str]:
    """Shorthand for a tag set."""
    return frozenset(tags)


# --- Deck definition -------------------------------------------------------

@dataclass(frozen=True)
class DeckDefinition:
    """A complete deck, independent of any database.

    This is the boundary between the application and the engine: the Phase 2
    adapter builds one of these, and the engine knows nothing else. Frozen and
    built from tuples, so that a deck cannot be changed by accident in the
    middle of a simulation.
    """

    name: str
    commander: Card | None
    library: tuple[Card, ...]
    #: The cards the deck is built around: the pieces of the combos it holds.
    #: A tutor with no priority list to ask takes one of these before anything
    #: else (phase 10 N1). Names, because a combo names its cards; empty by
    #: default, which is every deck and every caller from before.
    key_cards: frozenset[str] = frozenset()

    def __post_init__(self):
        if len(self.library) < 1:
            raise ValueError("a deck needs at least one card")

    @property
    def size(self) -> int:
        """Cards in the library, excluding the commander."""
        return len(self.library)

    @property
    def land_count(self) -> int:
        return sum(1 for card in self.library if card.is_land)

    def shuffled(self, rng) -> list[Card]:
        """A fresh, shuffled copy of the library."""
        library = list(self.library)
        rng.shuffle(library)
        return library

    def canonical(self) -> "DeckDefinition":
        """The same deck with its library sorted deterministically.

        Two decks holding the same cards in a different order are the same
        deck - but not the same object, and ``rng.shuffle`` gives different
        results on differently ordered lists. The adapter reads the cards in
        the database's order and the fixture in deck order, so comparison and
        simulation both use the canonical form.
        """
        return DeckDefinition(
            name=self.name,
            commander=self.commander,
            library=tuple(sorted(self.library, key=lambda card: (card.name, card.mv))),
            key_cards=self.key_cards,
        )


# --- Backwards compatibility -----------------------------------------------
#
# ``tests/test_cards.py`` and ``tests/test_statistics.py`` still import the deck
# list from this module. Those four test files are **byte-identical** to their
# originals in the magic-project repository, and that equality is the whole
# safety net of the Phase 2 generalization - so they may not be adjusted.
#
# PEP 562: the import happens on access, so that the model and the fixture do
# not form an import cycle.

_LEGACY = frozenset({
    "COMMANDER", "SWAMP", "SWAMP_COUNT", "UTILITY_LANDS", "SPELLS",
    "build_deck", "land_count",
})


def __getattr__(name: str):
    if name in _LEGACY:
        from simulation.fixtures import chainer

        return getattr(chainer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LEGACY)
