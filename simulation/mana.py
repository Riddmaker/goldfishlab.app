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

from simulation.cards import (
    DOUBLE_SUBTYPE,
    FLAT,
    PER_CONTROLLED,
    SWAMP_SUBTYPE,
    TYPE_ADDING,
)
from simulation.manacost import (
    COLORLESS,
    COLORS,
    SOURCES,
    SUBTYPE_COLORS,
    ManaCost,
    choice,
    is_choice,
    normalised,
    plan_payment,
)


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

    __slots__ = ("_pool",)

    def __init__(self, black: int = 0, colorless: int = 0, **colors: int):
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
        return {source: found[source] for source in SOURCES if found.get(source)}

    def add(self, color: str, amount: int = 1) -> None:
        """Put mana of one colour into the pool."""
        self._pool[color] = self._pool.get(color, 0) + amount

    def can_pay_cost(self, cost: ManaCost, *, life: int = 40) -> bool:
        """Can this cost structure be paid exactly?"""
        return plan_payment(dict(self._pool), cost, life=life) is not None

    def pay_cost(self, cost: ManaCost, *, life: int = 40):
        """Pay the cost and deduct it.

        Returns:
            Payment | None: What was paid - ``None`` when the cost could not be
            paid. An object rather than a ``True``, because Phyrexian mana costs
            life and the caller has to deduct that life; a bare ``True`` would
            have kept quiet about it.
        """
        payment = plan_payment(dict(self._pool), cost, life=life)
        if payment is None:
            return None
        for color, amount in payment.spent.items():
            self._pool[color] -= amount
        return payment

    def copy(self) -> "ManaPool":
        """A shallow copy."""
        clone = ManaPool()
        clone._pool = dict(self._pool)
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


def effective_mana_cost(card, reductions: dict[str, int] | None = None) -> ManaCost:
    """A card's full cost after reduction.

    The path the engine takes. ``card.mana_cost`` gives the coloured form even
    for cards still described through ``pips``/``generic``.
    """
    cost = card.mana_cost
    if not reductions:
        return cost
    return cost.reduced(applicable_reduction(cost, reductions))


# --- Available mana --------------------------------------------------------


def available_mana(all_lands, untapped_lands, untapped_rocks,
                   crypt_ghast) -> ManaPool:
    """The most mana available this turn.

    Args:
        all_lands: Every controlled land, tapped ones included, for Coffers.
        untapped_lands: The lands that can still make mana.
        untapped_rocks: Mana artifacts that are not tapped yet.
        crypt_ghast: The ``DOUBLE_SUBTYPE`` effects in play, either as a
            subtype -> count mapping or as a bare number. A number means "this
            many swamp doublers", so ``True``/``False`` still work.

    Returns:
        ManaPool: The best possible pool, optimal use of Coffers included.
    """
    granted = granted_subtypes(all_lands)
    extra = _doubler_map(crypt_ghast)
    pool = ManaPool()
    #: FLAT abilities that cost generic mana to use - Signets. Activated
    #: after every free source has put its mana in, so they can be paid for.
    activated = []

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
            pool.add(color, 1)
            for bonus_color, amount in _doubler_bonus(subtypes_of(land, granted), extra):
                pool.add(bonus_color, amount)
            continue
        flat = land.ability(FLAT)
        if flat is not None:
            _use(pool, flat, activated)

    for rock in untapped_rocks:
        flat = rock.ability(FLAT)
        if flat is not None:
            _use(pool, flat, activated)

    # Before the scaling line on purpose: a Signet nets mana, and that mana
    # can then help pay Cabal Coffers' {2}.
    for ability in activated:
        _activate(pool, ability)

    if scaling_land is not None:
        pool = _best_scaling_line(pool, scaling_land, all_lands, granted, extra)

    return pool


def _add_produced(pool: ManaPool, ability) -> None:
    """Put the mana of a ``FLAT`` ability into the pool."""
    for color, amount in ability.produces:
        pool.add(color, amount)


def _use(pool: ManaPool, ability, activated: list) -> None:
    """A free ability adds its mana now; a costed one waits for the pool."""
    if ability.activation_generic:
        activated.append(ability)
    else:
        _add_produced(pool, ability)


def _activate(pool: ManaPool, ability) -> None:
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
    _add_produced(pool, ability)


def _best_scaling_line(pool: ManaPool, land, all_lands, granted: frozenset[str],
                       extra: dict[str, int]) -> ManaPool:
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
        result.add(scaling_color, matching)
        if itself_matches:
            for bonus_color, amount in bonus:
                result.add(bonus_color, amount)
        return result

    if gain_a > 0:
        result = pool.copy()
        result.add(own_color, 1)
        for bonus_color, amount in bonus:
            result.add(bonus_color, amount)
        return result

    return pool
