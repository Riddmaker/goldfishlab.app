"""The classes a card's mana rules are written in (card model, phase 4 and 5).

Until PR 13 the engine played these: a mana ability as a ``ManaAbility`` with
its rule, ``SpellFilter``, ``TapMana``, ``Store`` and ``CostReduction``. Since
PR 13 the engine plays blocks (:mod:`simulation.rules.mana`), and these
classes are how a card's rules are written down: what the adapter compiles a
profile into and a test or a fixture deck spells out, which
:mod:`simulation.build` turns into blocks. Nothing turns blocks back into
them since the decoder retired (phase 5).

``TappedUnless`` - a land that enters tapped unless a condition holds - is
here too: it is written so, and the engine asks it as a
:class:`~simulation.rules.condition.Condition`.

**The engine never imports this module** (`tests/test_rules.py` holds it):
:mod:`simulation.game`, :mod:`simulation.mana` and :mod:`simulation.agent`
read a card's compiled blocks only. :mod:`simulation.cards` re-exports every
name, so the application and the tests import them as before.
"""

from dataclasses import dataclass, field

from simulation.manacost import COLORLESS, SUBTYPE_COLORS, normalised

#: A store's use: see ``Store``.
STORE_FREE = "free"
STORE_TAP = "tap"
STORE_SCALES = "scales"


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
    * ``power`` - you control a creature with power ``count`` or greater,
      by the power printed on it (Fanatic of Rhonas; P19 R18)
    * ``tap_fodder`` - a permanent of one of ``types`` (a ``legendary`` one
      if set) is there for this card to tap besides itself: Springleaf
      Drum, Survivors' Encampment (P19 R19). See ``Game.tap_fodder``.

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
class SpellFilter:
    """One kind of spell restricted mana may be spent on (P19 R17).

    "Spend this mana only to cast ..." names one or more of them, any one of
    which will do: Master of Dark Rites' "Vampire, Cleric, and/or Demon
    spells" is three filters of one subtype each. Within one filter every
    field must hold: Eldrazi Temple's "colorless Eldrazi spells" is
    ``SpellFilter(subtypes={"eldrazi"}, colorless=True)``.

    Attributes:
        types: Card types, any one of them (``CARD_TYPES``): "artifact or
            creature spells". Empty: any type.
        subtypes: Subtypes, any one of them, lower case: "dragon". Empty: any.
        legendary: A legendary spell only (Plaza of Heroes).
        colorless: A colourless spell only (Shrine of the Forsaken Gods).
        multicolored: A spell of two or more colours (Pillar of the Paruns).
        noncreature: Not a creature spell.
        only_color: A spell of this one colour and no other (Throne of
            Eldraine's chosen colour, P19 R18). Empty: any.
    """

    types: frozenset[str] = field(default_factory=frozenset)
    subtypes: frozenset[str] = field(default_factory=frozenset)
    legendary: bool = False
    colorless: bool = False
    multicolored: bool = False
    noncreature: bool = False
    only_color: str = ""


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
    #: P19 R17: "Spend this mana only to cast ..." - the spells its mana may
    #: pay for, any one of these will do. Empty: any spell or ability.
    spend_only: tuple[SpellFilter, ...] = ()

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
class TapMana:
    """Mana for tapping other permanents, without the card's own {T} (P19 R19).

    Relic of Legends taps a legendary creature for one mana of any colour,
    Urza an artifact for {U}, Grand Architect a blue creature for {C}{C} only
    artifact spells may spend. One activation for each permanent there is
    to tap: only one that makes no mana of its own, which keeps the ramp it
    is; a creature tapped so does not attack (P19 R22). Grand Architect may
    tap itself.

    Attributes:
        types: The card types a permanent tapped may have, any of them;
            ``permanent`` for any.
        filter: What it must also be: ``legendary`` or a colour letter.
        mana: What one activation adds: a colour, ``C`` or a choice key.
        amount: How much of it.
        spend_only: The spells it may pay for (P19 R17). Empty: any.
        tokens: Artifact tokens the card made as it entered, tapped too:
            Urza's Construct.
        per: How many one activation taps (v34): Heritage Druid's three
            Elves for {G}{G}{G}.
    """

    types: frozenset[str] = field(default_factory=frozenset)
    filter: str = ""
    mana: str = ""
    amount: int = 1
    spend_only: tuple[SpellFilter, ...] = ()
    tokens: int = 0
    per: int = 1


#: ``Store.use``: spent whenever a payment needs it, as a Treasure (Pentad
#: Prism); one use each turn the card taps (Vivid lands, Aether Hub, Wall of
#: Roots); or one mana for each counter, which stay (Everflowing Chalice).
@dataclass(frozen=True)
class Store:
    """Mana a permanent keeps as counters, energy or a Food (P19 R20).

    Attributes:
        use: ``STORE_FREE``, ``STORE_TAP`` or ``STORE_SCALES``.
        mana: What one use adds - a colour, ``C`` or a choice key; for
            ``STORE_SCALES`` empty means one colour the hand wants.
        amount: How much of it, for each counter when it scales.
        per_use: Counters one use spends (Runaway Steam-Kin: three).
        energy: The counters are the player's energy, shared by all.
        start: Counters as it enters.
        start_from: ``x`` (Astral Cornucopia, Mana Bloom), ``colors``
            (Sunburst, Converge) or ``kicks`` (Everflowing Chalice, cast as a
            spell with X); empty: ``start``.
        grows: ``tap`` - its {T} adds one each turn (Crystalline Crawler);
            ``use`` - each use adds one before counting (Empowered
            Autogenerator); ``red_spell`` - one for each red spell cast.
        empty: With the last counter gone, at the start of the next turn:
            ``sacrifice`` (storage lands, Gemstone Mine), ``dies`` (Wall of
            Roots) or ``hand`` (Mana Bloom).
    """

    use: str = STORE_TAP
    mana: str = ""
    amount: int = 1
    per_use: int = 1
    energy: bool = False
    start: int = 0
    start_from: str = ""
    grows: str = ""
    empty: str = ""
