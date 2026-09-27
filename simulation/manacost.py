"""Mana costs in all five colours, and how to pay them exactly.

This is where colour is defined for the whole engine: the five letters, what a
land subtype taps for, and the one function that turns a colour *name* into a
letter. :class:`~simulation.mana.ManaPool` and :class:`~simulation.cards.Card`
both run on it.

:class:`~simulation.cards.Card` still keeps ``pips`` and ``generic`` as single
integers for the mono-black shorthand, with :meth:`ManaCost.mono` as the bridge;
``Card.cost`` carries the full form when there is one.

Why it has to be exact rather than greedy
-----------------------------------------

Hybrid symbols make payment a matching problem, not a subtraction. A pool of
one white and one blue pays ``{W/U}{W/U}`` only if the two symbols are assigned
to *different* colours; pay the first one greedily from white and the second is
stuck. With at most five colours and a handful of symbols the search space is
tiny, so the honest answer - try assignments until one works - is also the fast
one. A greedy payer would report perfectly castable spells as uncastable, and
in a simulation that becomes a deck that looks worse than it is.

Two heuristics that are choices, not rules
------------------------------------------

These are documented here **and** have to be visible in the UI, because they
change the numbers and a reader cannot infer them from a result:

* **X is paid with everything left.** A goldfish has no reason to hold mana
  back, so ``{X}`` takes whatever remains after the rest of the cost.
* **Phyrexian mana is paid with life only when mana is short**, and never
  below :data:`PHYREXIAN_LIFE_FLOOR`. The floor mirrors the Necropotence one:
  without an opponent there is nothing to punish a low life total, so an
  unbounded payer would treat life as free and overstate the deck.
"""

from dataclasses import dataclass, field, replace

#: The five colours, in the canonical WUBRG order.
COLORS = ("W", "U", "B", "R", "G")

#: Generic mana is anything; colourless {C} is its own requirement and cannot
#: be paid with coloured mana.
COLORLESS = "C"

#: Life is never paid below this for Phyrexian mana. Same reasoning as the
#: Necropotence floor in `EndStepSpec`.
PHYREXIAN_LIFE_FLOOR = 25

#: Life one Phyrexian symbol costs.
PHYREXIAN_LIFE = 2

#: Everything a pool can hold, in the order ties are broken.
SOURCES = (*COLORS, COLORLESS)

#: Readable names for the letters, so a fixture can say `{"black": 1}` and a
#: database row can say `{"B": 1}` without either having to know the other's
#: spelling.
NAMED_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
    "colorless": COLORLESS,
}

#: Which colour a land subtype taps for. This is the whole of "Yavimaya works
#: like Urborg": the engine had `swamp -> black` wired into its only mana
#: routine, and a Forest was simply not a thing it could read.
SUBTYPE_COLORS = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}


def normalised(amounts) -> tuple[tuple[str, int], ...]:
    """Canonical form for an amount of mana: `(("B", 2), ("C", 1))`.

    Accepts a dict or any iterable of pairs, keyed by either letter (`"B"`) or
    name (`"black"`). Zero and negative amounts are dropped rather than stored,
    so that "produces nothing" has exactly one representation - two `ManaAbility`
    objects that make the same mana must compare equal, or a deck that round-trips
    through the database stops being the deck that was tested.
    """
    pairs = amounts.items() if hasattr(amounts, "items") else amounts
    totals: dict[str, int] = {}
    for key, amount in pairs:
        source = NAMED_COLORS.get(str(key).lower(), str(key).upper())
        if source not in SOURCES:
            raise ValueError(f"unknown mana source {key!r}; expected one of {SOURCES}")
        totals[source] = totals.get(source, 0) + int(amount)
    return tuple((source, totals[source]) for source in SOURCES if totals.get(source, 0) > 0)


@dataclass(frozen=True)
class Hybrid:
    """One hybrid symbol: any one of several ways to pay it.

    ``{W/U}`` is ``Hybrid(colors=("W", "U"))``.
    ``{2/B}`` is ``Hybrid(colors=("B",), generic=2)`` - two generic *or* one
    black, whichever the pool can spare.
    """

    colors: tuple[str, ...] = ()
    generic: int = 0

    def __str__(self) -> str:
        parts = list(self.colors)
        if self.generic:
            parts.insert(0, str(self.generic))
        return "{" + "/".join(parts) + "}"


@dataclass(frozen=True)
class ManaCost:
    """A parsed mana cost.

    ``pips`` holds the symbols that must be paid with that exact colour.
    Hybrid and Phyrexian symbols are kept separate because they are choices,
    and collapsing them into `pips` is what makes a payer wrong.
    """

    pips: tuple[tuple[str, int], ...] = ()
    generic: int = 0
    colorless: int = 0
    hybrid: tuple[Hybrid, ...] = ()
    phyrexian: tuple[str, ...] = ()
    has_x: bool = False

    @classmethod
    def mono(cls, pips: int = 0, generic: int = 0, color: str = "B") -> "ManaCost":
        """The old two-number cost, as a `ManaCost`.

        The bridge from `Card.pips` / `Card.generic`, which are still a single
        integer each.
        """
        return cls(pips=((color, pips),) if pips else (), generic=generic)

    @property
    def colored(self) -> dict[str, int]:
        return {color: count for color, count in self.pips if count}

    @property
    def colors(self) -> frozenset[str]:
        """Every colour this cost demands, including as a hybrid option.

        What a cost reducer looks at. Jet Medallion reduces *black* spells, and
        a spell is black if any of its symbols is - `{W/B}` counts.
        """
        found = set(self.colored)
        found.update(self.phyrexian)
        for symbol in self.hybrid:
            found.update(symbol.colors)
        return frozenset(found)

    def reduced(self, amount: int) -> "ManaCost":
        """The same cost with `amount` less generic, never below zero.

        Only the generic part moves: Necropotence with a Medallion out is still
        `{B}{B}{B}`, and a reduction larger than the generic part is not a
        rebate on the coloured pips.
        """
        if amount <= 0:
            return self
        return replace(self, generic=max(0, self.generic - amount))

    def with_generic(self, generic: int) -> "ManaCost":
        """The same cost with a different generic part - the commander tax."""
        return replace(self, generic=max(0, generic))

    @property
    def mv(self) -> int:
        """Mana value: every symbol except X counts once, generic by its size."""
        total = self.generic + self.colorless
        total += sum(count for _, count in self.pips)
        total += len(self.hybrid) + len(self.phyrexian)
        return total

    def __str__(self) -> str:
        parts = []
        if self.generic:
            parts.append("{" + str(self.generic) + "}")
        parts.extend("{C}" * self.colorless)
        for color, count in self.pips:
            parts.extend("{" + color + "}" * 1 for _ in range(count))
        parts.extend(str(h) for h in self.hybrid)
        parts.extend("{" + c + "/P}" for c in self.phyrexian)
        if self.has_x:
            parts.insert(0, "{X}")
        return "".join(parts) or "{0}"


def parse(cost: str) -> ManaCost:
    """Parse a Scryfall mana cost string such as ``{3}{B}{B}`` or ``{2/W}{B/P}``."""
    import re

    pips: dict[str, int] = {}
    generic = 0
    colorless = 0
    hybrid: list[Hybrid] = []
    phyrexian: list[str] = []
    has_x = False

    for raw in re.findall(r"\{([^}]+)\}", cost or ""):
        symbol = raw.upper()

        if symbol == "X":
            has_x = True
        elif symbol.isdigit():
            generic += int(symbol)
        elif symbol == COLORLESS:
            colorless += 1
        elif symbol in COLORS:
            pips[symbol] = pips.get(symbol, 0) + 1
        elif "/" in symbol:
            parts = symbol.split("/")
            if "P" in parts:
                colors = [p for p in parts if p in COLORS]
                phyrexian.append(colors[0] if colors else "B")
            else:
                colors = tuple(p for p in parts if p in COLORS)
                numbers = [int(p) for p in parts if p.isdigit()]
                hybrid.append(Hybrid(colors=colors, generic=numbers[0] if numbers else 0))
        # Anything else (snow, energy) is not modelled; it is deliberately
        # dropped rather than guessed at, and shows up as a coverage gap.

    return ManaCost(
        pips=tuple(sorted(pips.items())),
        generic=generic,
        colorless=colorless,
        hybrid=tuple(hybrid),
        phyrexian=tuple(phyrexian),
        has_x=has_x,
    )


@dataclass
class Payment:
    """How a cost was paid, and what it cost in life."""

    spent: dict[str, int] = field(default_factory=dict)
    life: int = 0

    @property
    def total(self) -> int:
        return sum(self.spent.values())


def plan_payment(available: dict[str, int], cost: ManaCost, *, life: int = 40) -> Payment | None:
    """Work out one exact way to pay `cost` from `available`, or `None`.

    `available` maps colour (and ``C``) to how much is in the pool. The return
    value says how much of each was spent, so the caller can subtract it
    without re-deriving the assignment.

    The order is what makes it exact:

    1. **Coloured pips** are forced - no choice to make.
    2. **Colourless ``{C}``** can only come from colourless mana.
    3. **Hybrid symbols** are searched, hardest first: a symbol with one
       payable option must take it, so resolving those first prunes the tree.
    4. **Phyrexian** prefers mana and falls back to life, never below the floor.
    5. **Generic** takes colourless first, then the most abundant colour, so
       that scarce colours stay available for later spells in the same turn.
    """
    pool = {key: value for key, value in available.items() if value}
    spent: dict[str, int] = {}

    def take(source: str, amount: int) -> bool:
        if pool.get(source, 0) < amount:
            return False
        pool[source] -= amount
        spent[source] = spent.get(source, 0) + amount
        return True

    for color, count in cost.pips:
        if count and not take(color, count):
            return None

    if cost.colorless and not take(COLORLESS, cost.colorless):
        return None

    if not _pay_hybrids(pool, spent, list(cost.hybrid)):
        return None

    # Mana may go to a Phyrexian symbol only while enough is left for the
    # generic part. Until engine version 3 every symbol took mana whenever its
    # colour was in the pool, before generic was looked at: three blue paid
    # Phyrexian Metamorph's {U/P} with a blue and then could not pay its {3},
    # and the spell read as uncastable when two life would have cast it - the
    # greedy payer the module docstring warns about. Generic can be paid with
    # anything, so the whole question is how much mana it needs.
    spare = sum(pool.values()) - cost.generic
    life_paid = 0
    for color in cost.phyrexian:
        if pool.get(color, 0) and spare > 0:
            take(color, 1)
            spare -= 1
            continue
        if life - life_paid - PHYREXIAN_LIFE < PHYREXIAN_LIFE_FLOOR:
            return None
        life_paid += PHYREXIAN_LIFE

    remaining = cost.generic
    while remaining:
        source = _cheapest_generic_source(pool)
        if source is None:
            return None
        take(source, 1)
        remaining -= 1

    return Payment(spent=spent, life=life_paid)


def _pay_hybrids(pool: dict[str, int], spent: dict[str, int], hybrids: list[Hybrid]) -> bool:
    """Assign every hybrid symbol, backtracking when a choice dead-ends.

    Ordered by how few options each symbol has, so forced assignments happen
    first. With at most five colours this is exhaustive and still instant.
    """
    if not hybrids:
        return True

    def options(symbol: Hybrid) -> list[tuple[str, int]]:
        choices = [(color, 1) for color in symbol.colors if pool.get(color, 0)]
        if symbol.generic and sum(pool.values()) >= symbol.generic:
            choices.append((COLORLESS if pool.get(COLORLESS, 0) else "", symbol.generic))
        return choices

    hybrids = sorted(hybrids, key=lambda symbol: len(options(symbol)))
    first, rest = hybrids[0], hybrids[1:]

    for color in first.colors:
        if pool.get(color, 0):
            pool[color] -= 1
            spent[color] = spent.get(color, 0) + 1
            if _pay_hybrids(pool, spent, rest):
                return True
            pool[color] += 1
            spent[color] -= 1

    if first.generic:
        taken = []
        for _ in range(first.generic):
            source = _cheapest_generic_source(pool)
            if source is None:
                break
            pool[source] -= 1
            spent[source] = spent.get(source, 0) + 1
            taken.append(source)
        if len(taken) == first.generic and _pay_hybrids(pool, spent, rest):
            return True
        for source in taken:
            pool[source] += 1
            spent[source] -= 1

    return False


def _cheapest_generic_source(pool: dict[str, int]) -> str | None:
    """Which mana to spend on a generic cost.

    Colourless first - it can pay nothing else. Then the colour there is most
    of, so the scarce colours survive for the rest of the turn.
    """
    if pool.get(COLORLESS, 0):
        return COLORLESS
    best, best_count = None, 0
    for color in COLORS:
        count = pool.get(color, 0)
        if count > best_count:
            best, best_count = color, count
    return best
