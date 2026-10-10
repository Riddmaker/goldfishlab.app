"""The mana model, with colour.

The decisive difference from an off-the-shelf library such as
``mtg-mana-simulator``: here **black mana is distinguished from colourless**.
Sol Ring, Mind Stone and Phyrexian Tower make colourless mana and *cannot* pay
the {B}{B}{B} of Necropotence. Without that distinction every Necropotence and
Chainer number would come out too optimistic.

**From Phase 2 on this module knows no card names.** Cabal Coffers, Urborg and
Crypt Ghast were hardwired by name until then; they are now instances of the
four rules from :mod:`simulation.cards`:

===================  ====================================================
``FLAT``             ``{T}: Add {B}`` - swamps, Sol Ring, signets
``PER_CONTROLLED``   ``{2}, {T}: Add {B} per <subtype>`` - Cabal Coffers
``DOUBLE_SUBTYPE``   each tapped <subtype> gives +1 - Crypt Ghast
``TYPE_ADDING``      every land counts as <subtype> - Urborg
===================  ====================================================

Those same three cards stay exactly as expensive and exactly as strong as they
were - ``tests/test_mana.py`` pins every single number - while Nykthos, Gaea's
Cradle, Nirkana Revenant and Yavimaya work without a line of code.

The function names and signatures are unchanged, because the four engine test
files have to stay byte-identical.
"""

from dataclasses import dataclass, field, replace

from simulation.cards import (
    ARTIFACT,
    CREATURE,
    DOUBLE_SUBTYPE,
    FILTER,
    FLAT,
    PER_CONTROLLED,
    ROCK,
    SWAMP_SUBTYPE,
    TYPE_ADDING,
    SpellFilter,
)
from simulation.manacost import (
    COLORLESS,
    COLORS,
    SOURCES,
    SUBTYPE_COLORS,
    ManaCost,
    can_pay_with,
    choice,
    is_choice,
    normalised,
    plan_payment,
)


@dataclass
class RestrictedMana:
    """The mana one source made that only some spells may spend (P19 R17).

    Cavern of Souls taps for {C}, or for one mana of any colour that only a
    creature spell of the chosen type may spend. The pool opens every source
    at once, so the choice waits until the mana is spent, as a Treasure's
    sacrifice does: ``made`` is the restricted mana, ``otherwise`` what the
    source makes instead. Spending from ``made`` settles it (``open`` goes
    False); spending ``otherwise`` takes the whole source out of the pool.
    Ancient Ziggurat has no ``otherwise``: its mana is a creature spell's or
    nobody's. Whatever a doubler adds on top stays restricted too - for Mana
    Reflection exactly right, for a trigger less than it is, never more.
    """

    made: dict[str, int]
    spend_only: tuple[SpellFilter, ...]
    otherwise: dict[str, int] = field(default_factory=dict)
    source: str = ""
    open: bool = True

    @property
    def total(self) -> int:
        """The most this source can still put towards some spell."""
        made = sum(self.made.values())
        return max(made, sum(self.otherwise.values())) if self.open else made

    def copy(self) -> "RestrictedMana":
        return RestrictedMana(dict(self.made), self.spend_only, dict(self.otherwise),
                              self.source, self.open)


def may_spend(spend_only, spell) -> bool:
    """May restricted mana pay for this spell? ``None`` is an ability's cost."""
    return spell is not None and any(_fits(wanted, spell) for wanted in spend_only)


def _fits(wanted, spell) -> bool:
    types = spell.types or frozenset({ARTIFACT if spell.kind == ROCK else spell.kind})
    if wanted.types and not wanted.types & types:
        return False
    if wanted.noncreature and CREATURE in types:
        return False
    if wanted.subtypes and not wanted.subtypes & (spell.printed_subtypes
                                                  | spell.creature_types):
        return False
    if wanted.legendary and not spell.legendary:
        return False
    # A hand-written fixture card has no colours of its own; its cost has.
    colors = spell.colors if (spell.colors or spell.types) else spell.mana_cost.colors
    if wanted.colorless and colors:
        return False
    if wanted.only_color and set(colors) != {wanted.only_color}:
        return False
    return not (wanted.multicolored and len(colors) < 2)


class ManaPool:
    """Available mana, separated by colour.

    Internally the pool carries all five colours plus colourless. From the
    outside it looks unchanged: ``ManaPool(black=3, colorless=2)``,
    ``pool.black += 1``, ``pool.can_pay(pips, generic)``. The 21 tests in
    ``tests/test_mana.py`` pin that behaviour and stay untouched.

    ``black`` and ``colorless`` are deliberately writable properties rather than
    fields: the engine does ``pool.black += 1`` in several places and that has
    to keep working, while underneath sits a full WUBRG pool.
    """

    __slots__ = ("_pool", "converters", "treasures", "restricted")

    def __init__(self, black: int = 0, colorless: int = 0, **colors: int):
        #: Converters tapped for {C} that could instead turn that {C} and one
        #: more mana into one of these (P19 R6): Study Hall, Prismatic Lens.
        #: Worth it only when a colour is missing, so payment decides.
        self.converters: list[str] = []
        #: Treasure tokens the game lends the pool, by the mana each makes
        #: (P19 R7). Sacrificed only when a payment needs them.
        self.treasures: list[str] = []
        #: Mana only some spells may spend, source by source (P19 R17).
        self.restricted: list[RestrictedMana] = []
        self._pool = dict.fromkeys(SOURCES, 0)
        self._pool[COLORLESS] = colorless
        self._pool["B"] = black
        # Through ``normalised``, not through the first letter: "blue" starts
        # with the same letter as "black", and a pool that books blue mana as
        # black would not show up in a single test.
        for color, amount in normalised(colors):
            self._pool[color] = self._pool.get(color, 0) + amount

    # --- the old surface ---------------------------------------------------

    @property
    def black(self) -> int:
        return self._pool["B"]

    @black.setter
    def black(self, value: int) -> None:
        self._pool["B"] = value

    @property
    def colorless(self) -> int:
        return self._pool[COLORLESS]

    @colorless.setter
    def colorless(self, value: int) -> None:
        self._pool[COLORLESS] = value

    @property
    def total(self) -> int:
        """How much mana there is in total."""
        return sum(self._pool.values())

    def by_color(self) -> dict[str, int]:
        """What the pool holds, colour by colour, empties left out.

        The honest form of ``pool.black``. A report built on the total alone
        says a deck had five mana on turn three without saying that four of it
        was green in a deck whose spells all want black - which is the
        difference between a working mana base and a broken one.

        Empties are omitted rather than reported as zero, so that a caller
        asking about a colour the deck never makes gets nothing rather than a
        row of noughts; the aggregation in :mod:`simulation.analysis` fills the
        zeroes back in for the colours it is actually counting.
        """
        return {color: amount for color, amount in self._pool.items() if amount}

    def can_pay(self, pips: int, generic: int) -> bool:
        """Can this cost be paid?

        Args:
            pips: How many {B} symbols; these must be paid with black.
            generic: The generic portion, payable with any colour.
        """
        return self.can_pay_cost(ManaCost.mono(pips, generic))

    def pay(self, pips: int, generic: int) -> None:
        """Pay the cost out of the pool.

        Colourless mana is preferred for the generic portion, so that coloured
        mana is left over for later coloured costs.

        Raises:
            ValueError: When the cost cannot be paid.
        """
        if self.pay_cost(ManaCost.mono(pips, generic)) is None:
            raise ValueError(
                f"cost {pips}B + {generic} cannot be paid from {self}")

    # --- the general surface -----------------------------------------------

    def amount(self, color: str) -> int:
        """How much mana of exactly this source is in the pool - a choice such
        as ``"UR"`` counts under its own key, not under ``U`` or ``R``."""
        return self._pool.get(color, 0)

    def reach(self) -> dict[str, int]:
        """How much of each colour the pool could pay, empties left out.

        A choice counts toward every colour it offers: one ``"UR"`` is a blue
        mana *or* a red one, and a deck's blue is what it could spend on a
        blue spell. So the colours together can add up to more than
        :attr:`total` - each says "up to". Colourless is only ever itself.
        """
        found: dict[str, int] = {}
        for source, amount in self._pool.items():
            if not amount:
                continue
            for color in (source if is_choice(source) else (source,)):
                found[color] = found.get(color, 0) + amount
        # A converter could make its colours, as many as there is {C} for.
        for output in self.converters[:self._pool.get(COLORLESS, 0)]:
            for color in output:
                if color != COLORLESS:
                    found[color] = found.get(color, 0) + 1
        # Restricted mana counts too, for the spells it may pay (P19 R17).
        for each in self.restricted:
            reached: dict[str, int] = {}
            for made in (each.made, each.otherwise if each.open else {}):
                counted: dict[str, int] = {}
                for source, amount in made.items():
                    for color in (source if is_choice(source) else (source,)):
                        counted[color] = counted.get(color, 0) + amount
                for color, amount in counted.items():
                    reached[color] = max(reached.get(color, 0), amount)
            for color, amount in reached.items():
                found[color] = found.get(color, 0) + amount
        return {source: found[source] for source in SOURCES if found.get(source)}

    def add(self, color: str, amount: int = 1) -> None:
        """Put mana of one colour into the pool."""
        self._pool[color] = self._pool.get(color, 0) + amount

    @property
    def restricted_total(self) -> int:
        """The restricted mana in the pool, each source at its most."""
        return sum(each.total for each in self.restricted)

    def can_pay_cost(self, cost: ManaCost, *, life: int = 40, spell=None) -> bool:
        """Can this cost structure be paid exactly?

        ``spell`` is the card being cast: restricted mana asks what it is
        (P19 R17). ``None`` for an ability's cost, which no restricted mana
        the engine reads may pay.
        """
        if not self.restricted:
            return self._plan(cost, life)[0] is not None
        return self.copy().pay_cost(cost, life=life, spell=spell) is not None

    def pay_cost(self, cost: ManaCost, *, life: int = 40, spell=None):
        """Pay the cost and deduct it.

        Restricted mana the spell may spend goes first - nothing else can use
        it. A source's ``otherwise`` mana goes last, one source at a time,
        because spending it gives up that source's restricted mana (P19 R17).

        Returns:
            Payment | None: What was paid - ``None`` when the cost could not be
            paid. An object rather than a ``True``, because Phyrexian mana costs
            life and the caller has to deduct that life; a bare ``True`` would
            have kept quiet about it.
        """
        if not self.restricted:
            return self._pay_plain(cost, life)
        matching = [index for index, each in enumerate(self.restricted)
                    if any(each.made.values()) and may_spend(each.spend_only, spell)]
        if matching:
            payment = self._pay_restricted_first(cost, life, matching)
            if payment is None:
                payment = self._pay_merged(cost, life, matching)
            if payment is not None:
                return payment
        payment = self._pay_plain(cost, life)
        if payment is not None:
            return payment
        giving = [index for index, each in enumerate(self.restricted)
                  if each.open and each.otherwise]
        for count in range(1, len(giving) + 1):
            trial = self.copy()
            for index in reversed(giving[:count]):
                trial._give_up(index)
            payment = trial._pay_plain(cost, life)
            if payment is not None:
                self._take_state(trial)
                return payment
        return None

    def _pay_plain(self, cost: ManaCost, life: int):
        payment, pool, used, sacrificed = self._plan(cost, life)
        if payment is None:
            return None
        self._pool = pool
        del self.converters[:used]
        del self.treasures[:sacrificed]
        for color, amount in payment.spent.items():
            self._pool[color] -= amount
        return payment

    def _give_up(self, index: int) -> None:
        """Spend a source's ``otherwise`` mana: its restricted mana is gone."""
        source = self.restricted.pop(index)
        for key, amount in source.otherwise.items():
            self.add(key, amount)

    def _take_state(self, other: "ManaPool") -> None:
        self._pool = other._pool
        self.converters = other.converters
        self.treasures = other.treasures
        self.restricted = other.restricted

    def _pay_restricted_first(self, cost: ManaCost, life: int, matching: list[int]):
        """The spell's restricted mana on the symbols it can pay, the rest as usual.

        Coloured pips first, then {C}, then generic: a restricted choice is
        worth most on a pip the rest of the pool might be short of.
        """
        trial = self.copy()
        sources = [trial.restricted[index] for index in matching]
        # Settled sources before open ones: an open one may still be wanted
        # for its ``otherwise`` mana.
        sources.sort(key=lambda each: each.open)
        spent: dict[str, int] = {}

        def take(want: str | None) -> bool:
            for source in sources:
                for key in sorted(source.made, key=lambda key: (is_choice(key), len(key))):
                    if source.made[key] and (want is None or can_pay_with(key, want)):
                        source.made[key] -= 1
                        source.open = False
                        spent[key] = spent.get(key, 0) + 1
                        return True
            return False

        pips = dict(cost.pips)
        for color in pips:
            while pips[color] and take(color):
                pips[color] -= 1
        colorless = cost.colorless
        while colorless and take(COLORLESS):
            colorless -= 1
        generic = cost.generic
        while generic and take(None):
            generic -= 1
        if not spent:
            return None
        rest = replace(cost, pips=tuple((color, count) for color, count in pips.items()
                                        if count),
                       colorless=colorless, generic=generic)
        payment = trial._pay_plain(rest, life)
        if payment is None:
            return None
        trial._drop_spent()
        self._take_state(trial)
        for key, amount in payment.spent.items():
            spent[key] = spent.get(key, 0) + amount
        return replace(payment, spent=spent)

    def _pay_merged(self, cost: ManaCost, life: int, matching: list[int]):
        """The planner's own search, with the spell's restricted mana pooled.

        The fallback for when spending restricted mana first leaves a colour
        short - a choice taken for the wrong pip. Whatever was spent comes off
        the restricted mana first: it is the mana nothing else could use.
        """
        trial = self.copy()
        sources = [trial.restricted[index] for index in matching]
        held: dict[str, int] = {}
        for source in sources:
            for key, amount in source.made.items():
                held[key] = held.get(key, 0) + amount
                trial.add(key, amount)
        payment = trial._pay_plain(cost, life)
        if payment is None:
            return None
        for key, amount in held.items():
            left = trial._pool.get(key, 0)
            unspent = min(left, max(0, amount - payment.spent.get(key, 0)))
            trial._pool[key] = left - unspent
            for source in sources:
                have = source.made.get(key, 0)
                keep = min(have, unspent)
                if keep < have:
                    source.open = False
                source.made[key] = keep
                unspent -= keep
        trial._drop_spent()
        self._take_state(trial)
        return payment

    def _drop_spent(self) -> None:
        self.restricted = [each for each in self.restricted
                           if any(each.made.values()) or (each.open and each.otherwise)]

    def _plan(self, cost: ManaCost, life: int):
        """A payment, the pool it is paid from, and the converters and
        Treasures it used.

        The pool as it stands first. Only when that fails are converters
        used - one, then two - each turning its own {C} and one other mana
        into a mana of its colours, every way that can be done (P19 R6).
        Using one costs a mana, which is why it is never done to no purpose.
        Treasures come last, one at a time: they stay for the next turn if
        nothing needs them (P19 R7).
        """
        if self.treasures:
            # Not even with every Treasure: the common answer, given at once
            # rather than after trying each count in turn.
            everything = dict(self._pool)
            for key in self.treasures:
                everything[key] = everything.get(key, 0) + 1
            if self._plan_converting(everything, cost, life)[0] is None:
                return None, self._pool, 0, 0
        for sacrificed in range(len(self.treasures) + 1):
            start = dict(self._pool)
            for key in self.treasures[:sacrificed]:
                start[key] = start.get(key, 0) + 1
            payment, pool, used = self._plan_converting(start, cost, life)
            if payment is not None:
                return payment, pool, used, sacrificed
        return None, self._pool, 0, 0

    def _plan_converting(self, start: dict, cost: ManaCost, life: int):
        payment = plan_payment(dict(start), cost, life=life)
        if payment is not None or not self.converters:
            return payment, start, 0
        seen = {_frozen(start)}
        layer = [start]
        for used, output in enumerate(self.converters, start=1):
            layer = [converted for pool in layer for converted in _convert(pool, output)
                     if _frozen(converted) not in seen and not seen.add(_frozen(converted))]
            for pool in layer:
                payment = plan_payment(dict(pool), cost, life=life)
                if payment is not None:
                    return payment, pool, used
        return None, start, 0

    def copy(self) -> "ManaPool":
        """A copy that can be paid from without touching this pool."""
        clone = ManaPool()
        clone._pool = dict(self._pool)
        clone.converters = list(self.converters)
        clone.treasures = list(self.treasures)
        clone.restricted = [each.copy() for each in self.restricted]
        return clone

    def __eq__(self, other) -> bool:
        if not isinstance(other, ManaPool):
            return NotImplemented
        return self._pool == other._pool

    def __repr__(self) -> str:
        return f"ManaPool({self})"

    def __str__(self) -> str:
        """Every colour actually in the pool.

        The pool shows itself in full now that it can hold more than two
        colours - an error reading "cost cannot be paid from 0B + 0C" would
        otherwise be wrong the moment green mana is in play.
        """
        parts = [f"{amount}{color}" for color, amount in self._pool.items() if amount]
        return " + ".join(parts) or "0"


def _frozen(pool: dict) -> frozenset:
    return frozenset((key, amount) for key, amount in pool.items() if amount)


def _convert(pool: dict, output: str):
    """Every pool one converter can make: its {C} and one other mana become one
    mana of ``output``."""
    if pool.get(COLORLESS, 0) < 1:
        return
    base = dict(pool)
    base[COLORLESS] -= 1
    for source, amount in base.items():
        if amount and source != output:
            converted = dict(base)
            converted[source] -= 1
            converted[output] = converted.get(output, 0) + 1
            yield converted


def _apply_filter(pool: ManaPool, ability) -> None:
    """A filter land, tapped for {C}, filters instead when that is no worse.

    ``{W/B}, {T}: Add {W}{W}, {W}{B}, or {B}{B}``: its {C} and one mana that is
    white or black become two that are each white or black. Done only with a
    mana whose colours are all among the filter's - then the two coming out
    can pay for anything the two going in could, and nothing is narrowed
    (a Command Tower's five colours are never traded for two). With no such
    mana it stays the {C} it tapped for (P19 R6).
    """
    allowed = set(ability.pays_with)
    if pool.amount(COLORLESS) < 1:
        return
    inputs = [source for source in pool._pool
              if pool._pool[source] and source != COLORLESS and set(source) <= allowed]
    if not inputs:
        return
    # The least flexible goes: a plain colour before a choice.
    taken = min(inputs, key=lambda source: (len(source), -pool._pool[source]))
    pool._pool[COLORLESS] -= 1
    pool._pool[taken] -= 1
    _add_produced(pool, ability)


# --- Subtypes at runtime ---------------------------------------------------


def type_adders(cards, subtype: str = SWAMP_SUBTYPE) -> int:
    """How many permanents give every land this subtype.

    **All** cards are counted, tapped ones included: Urborg works statically and
    does not have to tap for it.
    """
    return sum(
        1
        for card in cards
        if any(a.rule == TYPE_ADDING and a.subtype == subtype for a in card.mana_abilities)
    )


def granted_subtypes(cards) -> frozenset[str]:
    """Every subtype some permanent grants to *every* land.

    The general form of ``has_urborg``: Urborg gives ``swamp``, Yavimaya gives
    ``forest``, and together they make every land both.
    """
    return frozenset(
        ability.subtype
        for card in cards
        for ability in card.mana_abilities
        if ability.rule == TYPE_ADDING and ability.subtype
    )


def subtypes_of(land, granted: frozenset[str]) -> frozenset[str]:
    """The subtypes this land currently has - its own and the granted ones."""
    return frozenset(land.subtypes) | granted


def land_color(land, granted: frozenset[str]) -> str | None:
    """Which mana this land can tap for as a basic land.

    ``None`` means none - and then only its own ``FLAT`` ability counts. A land
    with several coloured subtypes - Blood Crypt is a Swamp Mountain, or any
    land under Urborg *and* Yavimaya - makes one mana of either: a choice key
    such as ``"BR"`` (:func:`~simulation.manacost.choice`), settled when it is
    spent. Until engine version 5 the pool could not hold that, and the first
    colour in WUBRG order was picked: every shock land made only its first
    colour, without a word in the report.
    """
    subtypes = subtypes_of(land, granted)
    colors = {SUBTYPE_COLORS[subtype] for subtype in subtypes if subtype in SUBTYPE_COLORS}
    if not colors:
        return None
    return choice(colors)


def land_colors(land, granted: frozenset[str] = frozenset()) -> frozenset[str]:
    """Every colour this land can make, as a basic land type or by its own
    ``FLAT`` ability - what a land search weighs when it picks one."""
    found = set(land_color(land, granted) or "")
    for ability in land.mana_abilities:
        if ability.rule == FLAT:
            for source, _amount in ability.produces:
                found.update(letter for letter in source if letter in COLORS)
    return frozenset(found)


def has_urborg(lands) -> bool:
    """True when Urborg is out (making every land a swamp).

    Still named this way for compatibility; what is checked is the general
    ``TYPE_ADDING`` rule, not the card name.
    """
    return type_adders(lands) > 0


def is_swamp(land, urborg: bool) -> bool:
    """Is this land currently a swamp?

    With Urborg out that holds for *every* land, Cabal Coffers and Phyrexian
    Tower included.
    """
    return urborg or land.has_subtype(SWAMP_SUBTYPE)


def count_swamps(lands) -> int:
    """How many swamps are controlled, which is what Cabal Coffers counts.

    Tapped lands count too, because Coffers looks at *controlled* swamps.
    """
    urborg = has_urborg(lands)
    return sum(1 for land in lands if is_swamp(land, urborg))


def doubler_count(permanents, subtype: str = SWAMP_SUBTYPE) -> int:
    """How much extra mana a tapped <subtype> makes on top.

    Crypt Ghast gives one extra {B} per tapped **swamp** - not per rock, and not
    per land.
    """
    return sum(
        1
        for card in permanents
        if any(a.rule == DOUBLE_SUBTYPE and a.subtype == subtype for a in card.mana_abilities)
    )


def doublers(permanents) -> dict[str, int]:
    """Every doubler in play, by subtype.

    The general form of ``doubler_count``. The subtype also decides the colour
    of the bonus: Crypt Ghast gives {B} when a **swamp** is tapped - even when
    that swamp is simultaneously a plains and was tapped for {W}.
    """
    found: dict[str, int] = {}
    for card in permanents:
        for ability in card.mana_abilities:
            if ability.rule == DOUBLE_SUBTYPE and ability.subtype:
                found[ability.subtype] = found.get(ability.subtype, 0) + 1
    return found


def _doubler_map(value) -> dict[str, int]:
    """Accept the old number just as readily as the new mapping.

    ``available_mana(..., crypt_ghast=True)`` is how the four unchangeable
    engine tests spell it; a number there means "this many swamp doublers".
    """
    if isinstance(value, dict):
        return {subtype: int(count) for subtype, count in value.items() if count}
    return {SWAMP_SUBTYPE: int(value)} if value else {}


def _doubler_bonus(subtypes: frozenset[str], extra: dict[str, int]):
    """The extra mana from tapping a land with these subtypes."""
    return [
        (SUBTYPE_COLORS.get(subtype, COLORLESS), count)
        for subtype, count in extra.items()
        if count and subtype in subtypes
    ]


# --- Mana on top (P19 R13) -------------------------------------------------


@dataclass(frozen=True)
class Extra:
    """One effect that adds mana as a source is tapped, resolved by the game.

    ``scope`` is an ``EXTRA`` rule's subtype, or ``permanent`` for a
    ``MULTIPLY``. ``mana`` is the pool key of the bonus: a colour, a choice
    (Fertile Ground's ``"WUBRG"``), or empty for one mana of a type the source
    made. ``fixes``: Abundant Growth - one land taps for any colour instead.
    """

    scope: str
    mana: str = ""
    amount: int = 1
    times: int = 1
    fixes: bool = False


#: Scopes that ask whether a source made the chosen colour.
CHOSEN_SCOPES = frozenset({"chosen_land", "chosen_basic"})
#: Scopes for the one land an Aura is on: added once, not per source.
ENCHANTED_SCOPES = frozenset({"enchanted", "enchanted:forest"})


def _same_type(made: dict[str, int]) -> str:
    """One mana "of any type that land produced", as a pool key.

    A source of fixed colours gives one of them - Kinnan on a Signet that
    made {U}{B} adds {U} or {B}, a choice the pool can hold. A source that
    made a choice of its own gives {C}: Blood Crypt under Mirari's Wake makes
    {B}{B} or {R}{R}, and two independent choices would also pay {B}{R},
    which it cannot. Colour lost, never mana invented.
    """
    keys = [key for key, amount in made.items() if amount > 0]
    colors = {key for key in keys if key in COLORS}
    if colors and len(colors) == len(keys):
        return choice(colors)
    return COLORLESS


def _applies(extra: Extra, source, made: dict[str, int]) -> bool:
    """Whether this extra fires for this source, tapped for this mana."""
    scope = extra.scope
    if scope in ("land", "chosen_land"):
        hit = source.is_land
    elif scope == "chosen_basic":
        hit = source.is_land and source.basic
    elif scope == "nonland":
        hit = not source.is_land
    elif scope == "creature":
        hit = source.kind == CREATURE
    elif scope == "colorless":
        hit = made.get(COLORLESS, 0) > 0
    else:
        hit = scope == "permanent"
    if hit and scope in CHOSEN_SCOPES:
        hit = any(extra.mana in key and amount > 0 for key, amount in made.items())
    return hit


def _tap(pool: "ManaPool", source, made, extras, tapped: list) -> None:
    """Put what a source made into the pool, and what the extras add to it.

    In the order a player gets it: a land that could make the chosen colour
    makes it (Caged Sun - its other colours are given up for the bonus), a
    multiplier multiplies the source's own mana (Mana Reflection), and then
    each trigger adds its one mana (Mirari's Wake).
    """
    made = {key: amount for key, amount in dict(made).items() if amount > 0}
    if not made:
        return
    for extra in extras:
        if extra.scope in CHOSEN_SCOPES and _applies(extra, source, made) \
                and not made.get(extra.mana):
            key = next(key for key, amount in made.items() if extra.mana in key and amount)
            made[key] -= 1
            made[extra.mana] = made.get(extra.mana, 0) + 1
    own = {key: amount for key, amount in made.items() if amount > 0}
    for extra in extras:
        if extra.times > 1 and _applies(extra, source, own):
            for key, amount in own.items():
                bonus = COLORLESS if is_choice(key) else key
                made[bonus] = made.get(bonus, 0) + amount * (extra.times - 1)
    for key, amount in made.items():
        if amount:
            pool.add(key, amount)
    for extra in extras:
        if extra.times > 1 or extra.fixes or extra.scope in ENCHANTED_SCOPES:
            continue
        if _applies(extra, source, own):
            pool.add(extra.mana or _same_type(own), extra.amount)
    tapped.append((source, own))


def _enchanted_lands(pool: "ManaPool", tapped: list, extras, granted: frozenset[str]) -> None:
    """The Auras on a land: Wild Growth's {G}, Abundant Growth's any colour.

    Which land is not tracked: one that was tapped for mana this turn, and a
    Forest for Utopia Sprawl. An Aura cast this turn adds nothing until the
    next, because the pool was opened before it arrived.
    """
    lands = [(source, made) for source, made in tapped if source.is_land]
    for extra in extras:
        if extra.scope not in ENCHANTED_SCOPES or extra.fixes:
            continue
        wanted = extra.scope.partition(":")[2]
        if any(not wanted or wanted in subtypes_of(land, granted) for land, _ in lands):
            pool.add(extra.mana, extra.amount)
    fixes = sum(1 for extra in extras if extra.fixes)
    # On a land that makes one mana of a single colour, a colourless one first.
    plain = sorted(
        (key for _, made in lands for key in made
         if len(made) == 1 and made[key] == 1 and not is_choice(key)),
        key=lambda key: key != COLORLESS)
    for key in plain[:fixes]:
        pool.add(key, -1)
        pool.add(choice(COLORS), 1)


# --- Costs -----------------------------------------------------------------


def effective_cost(card, medallion):
    """A card's cost after reduction, as the old pair of numbers.

    Jet Medallion takes {1} off black spells. That affects the generic portion
    only and never the coloured symbols.

    The mono-coloured shorthand for :func:`effective_mana_cost`. It stays
    because ``tests/test_mana.py`` calls it directly and that file has to remain
    byte-identical; the engine itself works with the coloured form.

    Args:
        card: The card.
        medallion: How large the reduction is. ``True``/``False`` still work,
            because a ``bool`` is an ``int`` in Python - so the existing calls
            stay valid.

    Returns:
        tuple[int, int]: (pips, generic)
    """
    amount = int(medallion)
    generic = card.generic
    is_black_spell = card.pips > 0
    if amount and is_black_spell:
        generic = max(0, generic - amount)
    return card.pips, generic


def reduction_from(permanents) -> int:
    """The total cost reduction from the permanents in play.

    Replaces the former ``game.has("Jet Medallion")``: several medallions
    stack, and a different deck brings different ones.
    """
    return sum(
        card.cost_reduction.amount
        for card in permanents
        if card.cost_reduction is not None
    )


def reductions_from(permanents) -> dict[str, int]:
    """The cost reductions in play, by the colour they require.

    The empty key collects the reductions that apply to every spell (Helm of
    Awakening); ``"B"`` the ones demanding a black pip.
    """
    found: dict[str, int] = {}
    for card in permanents:
        reduction = card.cost_reduction
        if reduction is None:
            continue
        key = reduction.color if reduction.requires_pip else ""
        found[key] = found.get(key, 0) + reduction.amount
    return found


def applicable_reduction(cost: ManaCost, reductions: dict[str, int]) -> int:
    """How much reduction applies to *this* cost.

    A spell benefits from a medallion of each of its colours. A two-colour
    spell with two matching medallions gets cheaper twice - that is what the
    cards say, which is why this sums rather than takes a maximum.
    """
    total = reductions.get("", 0)
    colors = cost.colors
    return total + sum(
        amount for color, amount in reductions.items() if color and color in colors
    )


def effective_mana_cost(card, reductions: dict[str, int] | None = None,
                        x: int = 0) -> ManaCost:
    """A card's full cost after reduction, with ``x`` paid for each {X}.

    The path the engine takes. ``card.mana_cost`` gives the coloured form even
    for cards still described through ``pips``/``generic``. X is generic mana,
    so it is added before a reduction applies (P19 R9): a Medallion takes {1}
    off Stroke of Genius at any X, as it does at the table.
    """
    cost = card.mana_cost
    if x and getattr(card, "x_count", 0):
        # Once X is chosen it is plain generic mana, and the cost says so.
        cost = replace(cost, generic=cost.generic + x * card.x_count, has_x=False)
    if not reductions:
        return cost
    return cost.reduced(applicable_reduction(cost, reductions))


# --- Available mana --------------------------------------------------------


def available_mana(all_lands, untapped_lands, untapped_rocks,
                   crypt_ghast, *, ability_of=None, extras=(), otherwise_of=None) -> ManaPool:
    """The most mana available this turn.

    Args:
        all_lands: Every controlled land, tapped ones included, for Coffers.
        untapped_lands: The lands that can still make mana.
        untapped_rocks: Mana artifacts that are not tapped yet.
        crypt_ghast: The ``DOUBLE_SUBTYPE`` effects in play, either as a
            subtype -> count mapping or as a bare number. A number means "this
            many swamp doublers", so ``True``/``False`` still work.
        ability_of: Which ``FLAT`` ability a source taps for. The game passes
            :meth:`Game.mana_ability`, which checks "Activate only if ..."
            against its board (P19 R4); without it, the first one.
        extras: The :class:`Extra` effects in play (P19 R13), resolved by
            the game: Wild Growth, Mirari's Wake, Kinnan, Mana Reflection.
        otherwise_of: What a source taps for instead of its restricted
            ability (P19 R17): Cavern of Souls' {C}. The game passes
            :meth:`Game.plain_mana_ability`; without it, the first plain one.

    Returns:
        ManaPool: The best possible pool, optimal use of Coffers included.
    """
    if ability_of is None:
        def ability_of(card):
            return card.ability(FLAT)
    if otherwise_of is None:
        otherwise_of = plain_ability
    granted = granted_subtypes(all_lands)
    extra = _doubler_map(crypt_ghast)
    pool = ManaPool()
    #: FLAT abilities that cost generic mana to use - Signets. Activated
    #: after every free source has put its mana in, so they can be paid for.
    activated = []
    #: Every source tapped, with what it made: where an Aura's land is.
    tapped = []

    scaling_land = None
    for land in untapped_lands:
        ability = land.ability(PER_CONTROLLED)
        if ability is not None:
            # It has no mana ability of its own, only the activation. Under
            # Urborg it is also a swamp and could tap for {B}.
            scaling_land = land
            continue
        color = land_color(land, granted)
        if color is not None:
            # Tapped as a basic land. Its own FLAT ability goes unused in that
            # case - a land taps only once.
            _tap(pool, land, {color: 1}, extras, tapped)
            for bonus_color, amount in _doubler_bonus(subtypes_of(land, granted), extra):
                pool.add(bonus_color, amount)
            continue
        flat = ability_of(land)
        if flat is not None:
            _use(pool, land, flat, activated, extras, tapped, otherwise_of)

    for rock in untapped_rocks:
        flat = ability_of(rock)
        if flat is not None:
            _use(pool, rock, flat, activated, extras, tapped, otherwise_of)

    # Before the scaling line on purpose: a Signet nets mana, and that mana
    # can then help pay Cabal Coffers' {2}.
    for source, ability in activated:
        _activate(pool, source, ability, extras, tapped)

    # Filters and converters, on sources that did tap for their plain {C}
    # (P19 R6): a filter now, where it is never worse; a converter is left
    # to the payment, which knows whether a colour is missing.
    for source in [*untapped_lands, *untapped_rocks]:
        filtering = source.ability(FILTER)
        if filtering is None or source is scaling_land or land_color(source, granted):
            continue
        if ability_of(source) is None:
            continue
        if filtering.pays_with:
            _apply_filter(pool, filtering)
        else:
            pool.converters.extend(color for color, amount in filtering.produces
                                   for _ in range(amount))

    if scaling_land is not None:
        pool = _best_scaling_line(pool, scaling_land, all_lands, granted, extra,
                                  extras, tapped)

    _enchanted_lands(pool, tapped, extras, granted)
    return pool


def _add_produced(pool: ManaPool, ability) -> None:
    """Put the mana of a ``FLAT`` ability into the pool."""
    for color, amount in ability.produces:
        pool.add(color, amount)


def plain_ability(card):
    """The first ``FLAT`` ability any spell may spend and nothing guards."""
    for ability in card.mana_abilities:
        if ability.rule == FLAT and not ability.spend_only and ability.only_if is None:
            return ability
    return None


def _use(pool: ManaPool, source, ability, activated: list, extras, tapped: list,
         otherwise_of=plain_ability) -> None:
    """A free ability adds its mana now; a costed one waits for the pool.

    Restricted mana goes to the pool's own list, beside what the source
    would make instead (P19 R17). One that costs mana to make is not
    played: its plain ability stands in.
    """
    if ability.spend_only:
        plain = otherwise_of(source)
        if ability.activation_generic:
            if plain is not None and not plain.spend_only:
                _use(pool, source, plain, activated, extras, tapped)
            return
        made = ManaPool()
        _tap(made, source, dict(ability.produces), extras, tapped)
        otherwise = ManaPool()
        if plain is not None and not plain.activation_generic and not plain.spend_only:
            _tap(otherwise, source, dict(plain.produces), extras, [])
        if made.total or otherwise.total:
            pool.restricted.append(RestrictedMana(
                made.by_color(), ability.spend_only, otherwise.by_color(), source.name))
        return
    if ability.activation_generic:
        activated.append((source, ability))
    else:
        _tap(pool, source, dict(ability.produces), extras, tapped)


def _activate(pool: ManaPool, source, ability, extras, tapped: list) -> None:
    """Pay a ``FLAT`` ability's generic cost out of the pool, if it is worth it.

    A Signet takes {1} and gives back {U}{B}: one more mana, and the colours
    changed. One that would not make more than it costs is left alone - that
    is a filter, and a pool that counts mana cannot use one. The generic cost
    is paid the way every generic cost is (colourless first, then the most
    plentiful colour), so the scarce colours survive for the spells.
    """
    cost = ability.activation_generic
    if ability.total <= cost or pool.total < cost:
        return
    pool.pay(0, cost)
    _tap(pool, source, dict(ability.produces), extras, tapped)


def _best_scaling_line(pool: ManaPool, land, all_lands, granted: frozenset[str],
                       extra: dict[str, int], extras=(), tapped=None) -> ManaPool:
    """Pick the better of the two Cabal Coffers lines.

    Line A: tap Coffers as a swamp for {B} (only possible with Urborg).
    Line B: {2}, {T}: Add {B} for each swamp you control.

    Line B only pays off from 3 swamps upwards, because of the 2 mana
    activation cost. A naive agent that always activates Coffers loses mana
    early.
    """
    ability = land.ability(PER_CONTROLLED)
    subtype = ability.subtype
    activation = ability.activation_generic
    scaling_color = ability.scaling_color

    own_subtypes = subtypes_of(land, granted)
    matching = sum(1 for other in all_lands
                   if subtype in subtypes_of(other, granted))
    itself_matches = subtype in own_subtypes
    bonus = _doubler_bonus(own_subtypes, extra)
    bonus_total = sum(amount for _, amount in bonus)

    # Line A: tap the land itself as a basic land, if it carries a coloured
    # subtype.
    own_color = land_color(land, granted)
    gain_a = (1 + bonus_total) if own_color is not None else 0

    # Line B: the activation. The doubler triggers once if the land itself
    # carries the subtype (a swamp is being tapped for mana).
    gain_b = -1
    if pool.total >= activation:
        gain_b = matching + (bonus_total if itself_matches else 0) - activation

    if gain_b > gain_a and gain_b >= 0:
        result = pool.copy()
        result.pay(0, activation)
        _tap(result, land, {scaling_color: matching}, extras, [] if tapped is None else tapped)
        if itself_matches:
            for bonus_color, amount in bonus:
                result.add(bonus_color, amount)
        return result

    if gain_a > 0:
        result = pool.copy()
        _tap(result, land, {own_color: 1}, extras, [] if tapped is None else tapped)
        for bonus_color, amount in bonus:
            result.add(bonus_color, amount)
        return result

    return pool
