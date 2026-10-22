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

from dataclasses import dataclass, field, fields
from functools import cached_property
from typing import Any

from simulation.blocks import Ability
from simulation.manacost import (
    ManaCost,
)

# The classes a card's mana rules are written in live in `simulation.spec`
# (card model phase 4); they are re-exported here for the application and the
# tests.
from simulation.spec import (  # noqa: E402,F401  (re-exported)
    STORE_FREE,
    STORE_SCALES,
    STORE_TAP,
    CostReduction,
    ManaAbility,
    SpellFilter,
    Store,
    TapMana,
    TappedUnless,
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
    #: P19 R23: a search to hand may find any of ``types`` - card types,
    #: subtypes or creature types (Open the Armory: aura, equipment) - with
    #: ``max_mv`` or less (Spellseeker). ``land_tapped``: a land it finds goes
    #: onto the battlefield tapped instead (Archdruid's Charm), and it looks
    #: for one only while the hand holds no land.
    land_tapped: bool = False
    #: P19 R23: Gifts Ungiven - this many cards found, ``count`` of them to
    #: hand: the opponent puts the best ones into the graveyard.
    gifts: int = 0
    #: P19 R23: Gamble - cards discarded at random afterwards.
    random_discard: int = 0
    #: P19 R23: Demonic Counsel - any card with delirium (four card types in
    #: the graveyard).
    delirium_any: bool = False
    #: P19 R23: Defense of the Heart - not as it is cast but in an upkeep,
    #: once an opponent is assumed to have three creatures; it is sacrificed.
    upkeep: str = ""
    #: v34: onto the battlefield, a card that is no legendary one (Woodland
    #: Bellower), or any permanent card (Planar Bridge); on top, a basic
    #: land (Embermouth Sentinel).
    nonlegendary: bool = False
    permanent: bool = False
    basic: bool = False
    #: v34: Neoform - exactly ``max_mv_sacrificed`` more than what was
    #: sacrificed, not up to it.
    exact_mv: bool = False
    #: v34: an ability of a permanent that costs ``cost`` and {T}, once a
    #: turn (Moggcatcher), not something it does as it resolves.
    activate: bool = False
    cost: ManaCost | None = None


#: ``TutorSpec.upkeep``: an opponent controls three or more creatures.
OPPONENT_CREATURES = "opponent_creatures"


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
        also: P19 R23: land types found beside the basic lands (Circuitous
            Route's Gates).
        power_at, power_count: P19 R23: Entish Restoration - this many lands
            instead beside a creature with at least this much power.
        by_power: P19 R23: as many lands as the greatest power among your
            creatures (Traverse the Outlands).
        repeat: P19 R23: Claim Jumper - searched again this often, while the
            condition still holds.
        tempting: P19 R23: Tempt with Discovery - one more land for each
            opponent assumed to take the offer.
        by_x: P19 R23: X lands (Open the Way); ``reveal``: off the top of the
            library rather than searched for.
        lands_at_most: v34: Edge of Autumn - only while you control this many
            lands or fewer. 0: however many.

    ``when`` is also ``chapter2`` (v34): a Saga's second chapter, the turn
    after it entered, after the draw step (Binding the Old Gods).
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
    also: frozenset[str] = field(default_factory=frozenset)
    power_at: int = 0
    power_count: int = 0
    by_power: bool = False
    repeat: int = 0
    tempting: int = 0
    by_x: bool = False
    reveal: bool = False
    lands_at_most: int = 0


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
        sacrifice_count: How many permanents are sacrificed: Phyrexian
            Tribute's two creatures (engine version 28).
    """

    sacrifice: frozenset[str] = field(default_factory=frozenset)
    sacrifice_filter: str = ""
    life: int = 0
    life_x: bool = False
    discard: int = 0
    mana: ManaCost | None = None
    exile_from_graveyard: str = ""
    sacrifice_count: int = 1


@dataclass(frozen=True)
class Growth:
    """+1/+1 counters and what grows with them (P19 R21).

    Attributes:
        enters: +1/+1 counters it enters with (Polukranos: six).
        enters_x: ... X of them, X as it was paid (a Hydra).
        grows: What puts a counter on it: ``enters:<type>`` - another
            creature of that type enters (Marwyn: ``enters:elf``);
            ``cast:noncreature`` (Vivi Ornitier); ``evolve`` (Gyre Sage);
            ``increment`` (Topiary Lecturer).
        adapt: Adapt N - N counters when it has none, for ``adapt_cost``
            (Incubation Druid: 3 for {3}{G}{G}).
        bonus: One more on each placement on a creature you control (Kami
            of Whispered Hopes, Hardened Scales).
        puts: A counter on each of up to this many creatures as it enters
            (Rishkar: two).
        draws_on_greatest: A card when a creature enters with more power
            than each other (Selvala, Heart of the Wilds).
        look: Cards it looks at for a creature as it enters (Growing Rites of
            Itlimoc: four).
    """

    enters: int = 0
    enters_x: bool = False
    grows: tuple[str, ...] = ()
    adapt: int = 0
    adapt_cost: ManaCost | None = None
    bonus: int = 0
    puts: int = 0
    draws_on_greatest: bool = False
    look: int = 0


#: ``Card.mox``: "If this artifact would enter, you may discard a land card
#: instead" (Mox Diamond), and "Imprint - exile a nonartifact, nonland card"
#: whose colours it makes (Chrome Mox).
DISCARD_LAND = "discard_land"
IMPRINT = "imprint"


# --- Triggers (P19 R16) ------------------------------------------------------
#
# What a permanent does when something happens: makes a Treasure, adds mana,
# draws a card. Each event is one the goldfish game really has - a land
# entering, a spell cast, an upkeep - except ``OPPONENT_TURNS``, which stands for a
# round of the opponents' turns and is played on an assumption the card list
# states (see ``simulations.gaps``).

#: "Landfall — Whenever a land you control enters": Lotus Cobra.
LANDFALL = "landfall"
#: "Whenever you cast a spell": Birgi. ``filter`` narrows it.
CAST = "cast"
#: "At the beginning of your upkeep": Awakening Zone.
UPKEEP = "upkeep"
#: "At the beginning of your first main phase": Hulking Raptor.
MAIN_PHASE = "main"
#: "Whenever another creature you control dies": Pitiless Plunderer.
DIES = "dies"
#: "Whenever Ganax or another Dragon you control enters": ``filter`` is the
#: creature type, the card itself included.
ENTERS = "enters"
#: The card's own resolution, counted: Brass's Bounty's "For each land you
#: control, create a Treasure token" (``filter`` ``per_land``).
RESOLVES = "resolves"
#: The opponents' turns, once a round, assumed: Smothering Tithe's Treasure
#: from the one opponent a round who does not pay, Rhystic Study's card.
OPPONENT_TURNS = "opponents"
#: P19 R23: "Whenever you sacrifice an artifact": Crime Novelist. A Treasure
#: spent is one; ``filter`` is ``artifact``.
SACRIFICED = "sacrificed"

#: ``CAST`` filters.
INSTANT_OR_SORCERY = "instant_sorcery"
COLORLESS_SPELL = "colorless"
SECOND_SPELL = "second"
#: ``DIES`` filter: not the card itself.
ANOTHER = "another"
#: ``RESOLVES`` filter.
PER_LAND = "per_land"


@dataclass(frozen=True)
class Trigger:
    """One triggered ability the engine plays (P19 R16).

    Attributes:
        event: When it fires, one of the constants above.
        filter: What narrows the event, see the constants above.
        treasures: Treasure tokens it makes, each one mana of
            ``treasure_mana`` - ``C`` for an Eldrazi Spawn, which the engine
            keeps as a Treasure that makes {C}.
        mana: Mana it adds, of ``mana_color`` (a colour, or a choice key).
            Before the main phase opens it waits for the pool.
        draw: Cards it draws.
        life: Life it costs (Lotho: 1).
        counter: +1/+1 counters it puts on the card itself (Crime Novelist).
        connive: Times the card connives (A.I.M. Scientists; card model PR
            19): draw a card, discard a card, and a +1/+1 counter on it if
            the discarded card was no land.
        draw_by: v34: cards drawn by a count (`simulation.rules.amount.Amount`)
            instead - Champion of Dusk's Vampires; ``lose_drawn``: as much
            life lost.
    """

    event: str
    filter: str = ""
    treasures: int = 0
    treasure_mana: str = ""
    mana: int = 0
    mana_color: str = ""
    draw: int = 0
    life: int = 0
    counter: int = 0
    connive: int = 0
    draw_by: Any = None
    lose_drawn: bool = False


@dataclass(frozen=True)
class CombatTrigger:
    """A trigger in combat (P19 R22): as a creature attacks, or as it deals
    combat damage to a player - which, a goldfish having no blockers, is
    assumed to happen whenever it attacks.

    Attributes:
        event: ``attacks`` or ``damage``.
        who: Whose attack: ``self``; ``equipped`` or ``enchanted``, the
            creature the card is attached to; ``any`` (one or more of your
            creatures, of ``types`` if given - once a combat); ``modified``
            (each creature with an Equipment, an Aura or a +1/+1 counter -
            Kodama of the West Tree); ``commander`` (Guild Artisan); ``curse``
            (Curse of Opulence: you attack the cursed player).
        types: Creature types, any of them, for ``any``. Empty: any creature.
        treasures: Treasure tokens it makes (a Gold token is one), each one
            mana of ``treasure_mana``.
        mana: Mana it adds and keeps to the end of the turn, "RRRGGG".
        search: A basic land onto the battlefield, tapped - ``untapped``
            (Sword of Hearth and Home), not.
        to_hand: Engine v37: a basic land into the hand (Captain Kirk,
            Boldly Going).
        connive: Times the card itself connives (Ultron, Unlimited; card
            model phase 5): draw a card, discard a card, and a +1/+1 counter
            on it if the discarded card was no land.

    P19 R23: ``who`` ``total`` is Neheb, the Eternal: ``mana`` once for each
    point of combat damage the attackers deal, which in a goldfish is the
    life the opponents lost.
    """

    event: str
    who: str = "self"
    types: frozenset[str] = field(default_factory=frozenset)
    treasures: int = 0
    treasure_mana: str = ""
    mana: str = ""
    search: bool = False
    untapped: bool = False
    connive: int = 0
    to_hand: bool = False


#: What a Lander token does (P19 R14): "{2}, {T}, Sacrifice this token: Search
#: your library for a basic land card, put it onto the battlefield tapped".
LANDER = LandSearch(when="activate", cost=ManaCost(generic=2), taps=True, sacrifice=True)

#: P19 R22: what a search in combat does - Sword of the Animist.
COMBAT_SEARCH = LandSearch(when="combat")
#: P19 R23: Sword of Hearth and Home's land enters untapped.
COMBAT_SEARCH_UNTAPPED = LandSearch(when="combat", tapped=False)
#: Engine v37: Captain Kirk, Boldly Going - a basic land to hand.
COMBAT_SEARCH_TO_HAND = LandSearch(when="combat", battlefield=0, hand=1)

#: P19 R22: Living weapon's token, a 0/0 the Equipment makes a 1/1 or more.
GERM = "Germ"


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
        engine_roles: Which of the four roles the engine plays by the card
            is (`ENGINE_ROLES`): read off its categories by the adapter.
        land_type: Lands only, see the land subtypes above.
        enters_tapped: Enters the battlefield tapped (decisive on turns 1-3).
        goldfish_castable: False for cards with no legal target when there is no
            opponent (removal, wipes). The agent wastes no mana on them.
        needs_creature_in_yard: Reanimation needs a creature in the graveyard.
        needs_creature_on_bf: Needs one of your own creatures (a sacrifice cost).
    """

    def __deepcopy__(self, memo):
        """A card is a value that never changes: a copy of a game (the stress
        test's, `simulation.analysis`) shares its cards rather than cloning
        them, which also keeps the game's ``id(card)`` keys valid."""
        return self

    name: str
    mv: int
    pips: int
    generic: int
    kind: str
    engine_roles: frozenset[str] = field(default_factory=frozenset)
    land_type: str = ""
    goldfish_castable: bool = True
    needs_creature_in_yard: bool = False
    needs_creature_on_bf: bool = False

    # --- from Phase 2 on: what used to live in name tables -----------------
    #
    # Every one of these has a default, so that the ~70 existing positional
    # ``Card(...)`` calls keep working unchanged - including the ones in the
    # four byte-identical test files.
    priority: int | None = None                # replaces agent.PRIORITY
    accelerant: bool = False                   # replaces game.ACCELERANTS
    subtypes: frozenset[str] = field(default_factory=frozenset)

    #: The full mana cost. ``None`` means the card is described by
    #: ``pips``/``generic``, so mono-black. A new field rather than a wider
    #: ``pips``, because ``tests/test_mana.py`` builds cards positionally -
    #: ``Card("Test", 1, 1, 0, LAND)`` has to keep meaning what it meant.
    cost: ManaCost | None = None


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
    #: draw, removal and the rest - for the draw statistics. The card's
    #: derived roles and mechanics (card model phase 3) and every correction
    #: of them; ``engine_roles`` is read off them (PR 17). Empty for the
    #: hand-written fixture decks. Last, and defaulted, like ``types``.
    categories: frozenset[str] = field(default_factory=frozenset)

    #: A basic land - what "search for a basic land card" may find.
    basic: bool = False
    #: P19 R9: how many {X} the cost has - Astral Cornucopia's {X}{X}{X} is
    #: three. 0: no X. The agent casts such a spell last in its main phase,
    #: with everything left as X.
    x_count: int = 0
    #: P19 R9: the smallest X worth casting it for. A deck author may want
    #: Stroke of Genius held until X is four.
    x_min: int = 1
    #: P19 R23: the largest X allowed (Open the Way: the number of players).
    #: 0: no limit.
    x_max: int = 0
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
    #: P19 R13: an Aura's target: ``land`` or a land type (``forest``); P19
    #: R22: ``creature`` (Shiny Impetus). It cannot be cast without one.
    enchants: str = ""
    #: P19 R17: every subtype on the front face, lower case - "aura",
    #: "equipment", "dragon": what restricted mana asks a spell about.
    #: ``subtypes`` holds the basic land types only.
    printed_subtypes: frozenset[str] = field(default_factory=frozenset)
    #: P19 R18: the power printed on it, 0 for none or "*" - what "a creature
    #: with power 4 or greater" asks. The game adds its +1/+1 counters
    #: (`Game.power`, P19 R21); pumps and anthems are not counted.
    power: int = 0
    #: P19 R21: the toughness printed on it, 0 for none or "*" - Arbor
    #: Adherent's mana, and what Evolve compares.
    toughness: int = 0
    #: P19 R22: it attacks the turn it arrives.
    haste: bool = False
    #: P19 R22: a token the engine made as a card (a Germ): it goes to no
    #: graveyard.
    token: bool = False
    #: Card model phase 4: what the card does, as blocks
    #: (:mod:`simulation.blocks`). Every rule of the card lives here, in
    #: three families: mana (PR 13), lands, costs and spells (PR 14), and
    #: triggers, turns and combat (PR 15). The fields above are printed on
    #: the card or a play hint (`simulation.blocks.FACE_FIELDS`,
    #: `HINT_FIELDS`). A card's rules are written as the fields of
    #: `simulation.build.MadeWith`, which `simulation.build.card` turns into
    #: blocks; the engine and the application read the compiled blocks
    #: (`mana`, `play`, `turn`).
    abilities: tuple[Ability, ...] = ()

    @cached_property
    def mana(self):
        """The mana family's blocks compiled once (`simulation.rules.mana.ManaView`):
        what the engine reads."""
        from simulation.rules import mana

        return mana.compile_view(self.abilities)

    @cached_property
    def play(self):
        """The blocks of lands, costs and spells compiled once
        (`simulation.rules.play.PlayView`): what the engine reads."""
        from simulation.rules import play

        return play.compile_view(self.abilities)

    @cached_property
    def turn(self):
        """The blocks of triggers, turns and combat compiled once
        (`simulation.rules.turns.TurnView`): what the engine reads."""
        from simulation.rules import turns

        return turns.compile_view(self.abilities)

    def __hash__(self) -> int:
        # Over every field, as a frozen dataclass hashes - computed once: a
        # card's blocks nest deeper than its old fields did, and the engine
        # counts cards in multisets every turn.
        found = self.__dict__.get("_hash")
        if found is None:
            found = self.__dict__["_hash"] = hash(tuple(
                getattr(self, name) for name in _HASHED))
        return found

    def twin(self) -> "Card":
        """Another copy of this card: equal to it, but an object of its own,
        with what is compiled shared. Each copy in a game is its own object,
        so each permanent keeps its own state (card model PR 17)."""
        other = object.__new__(Card)
        other.__dict__.update(self.__dict__)
        return other

    def __getstate__(self):
        # What is compiled or hashed is made again where the card
        # arrives: a string's hash differs from one process to the next.
        return {key: value for key, value in self.__dict__.items()
                if key not in ("mana", "play", "turn", "_hash")}

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
        ritual = self.mana.ritual
        return bool(self.mana.entries) or (ritual is not None and (
            ritual.gain > 0 or any(each.form == "ritual" and each.effects[0].arg("amount").kind
                                   != "fixed" for each in self.abilities)))

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

    def ability(self, rule: str):
        """The first mana ability with this rule, as the engine plays it
        (`simulation.rules.mana.Source`), if there is one."""
        for ability in self.mana.entries:
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


#: The fields a card's hash is over: every one, as a frozen dataclass's is.
_HASHED = tuple(item.name for item in fields(Card))

#: The four roles the engine plays by: what a tutor finds before a bigger
#: spell, what is sacrificed first, and the milestones (card model PR 17).
DRAW_ENGINE, SAC_OUTLET, RECURSIVE, DRAIN = ("draw_engine", "sac_outlet", "recursive",
                                             "drain_payoff")
ENGINE_ROLES = frozenset({DRAW_ENGINE, SAC_OUTLET, RECURSIVE, DRAIN})


def _t(*roles: str) -> frozenset[str]:
    """Shorthand for a set of engine roles."""
    unknown = set(roles) - ENGINE_ROLES
    if unknown:
        raise ValueError(f"no engine role: {', '.join(sorted(unknown))}")
    return frozenset(roles)


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
    #: The second commander, when two share the command zone - partners, a
    #: Background, the Doctor's companion (P20 F2b). None for every deck and
    #: every caller from before, which then plays exactly as it did.
    partner: Card | None = None

    def __post_init__(self):
        if len(self.library) < 1:
            raise ValueError("a deck needs at least one card")

    @property
    def commanders(self) -> tuple[Card, ...]:
        """The command zone: none, one or two cards, first commander first."""
        return tuple(card for card in (self.commander, self.partner) if card is not None)

    @property
    def size(self) -> int:
        """Cards in the library, excluding the commander."""
        return len(self.library)

    @property
    def land_count(self) -> int:
        return sum(1 for card in self.library if card.is_land)

    def shuffled(self, rng) -> list[Card]:
        """A fresh, shuffled copy of the library, each copy of a card its own
        object (`Card.twin`): two Forests on the battlefield are two permanents."""
        library, seen = [], set()
        for card in self.library:
            library.append(card.twin() if id(card) in seen else card)
            seen.add(id(card))
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
            partner=self.partner,
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
