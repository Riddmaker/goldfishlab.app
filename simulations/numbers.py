"""The few numbers a run is told by (P11, moved here in P20 F4).

Read off a finished run's stored result, the same one its report draws, so
the precon table, the precon page and the run's own report can never
disagree. Each is a share of games or a mean over them:

- `on_curve`: games with at least four lands in play on turn 4 - every land
  drop made, the commonest way a Commander deck stumbles early;
- `kept_seven`: games that kept the first seven cards, no mulligan;
- `mana_turn_4`: mana available on turn 4, on average;
- `spells_seen`: cards that are not lands seen by the last turn, on average
  (the land sweep's cost of a land: `datapages.sweep`).

Until P20 F4 this lived in `datapages`, and only the precon pages had "four
lands on turn 4" - the number of the article a reader came from, missing from
the report of their own deck (E1.19). `datapages` uses `simulations`, not the
other way round, so the module moved to where both can reach it.

A run shorter than four turns has no turn 4: its turn-4 numbers are None and
their sentences are left out.

Since Paket 3, PR 11 (Themen-Doc row 32) a report tells these numbers against
the precons' (`standings`): "81% of games" says little on its own, "ahead of
34 of 40 precons" says where the deck stands.
"""

import math
import statistics
from dataclasses import dataclass
from uuid import UUID

from django.core.cache import cache
from django.utils.translation import gettext

from core.l10n import number, percent
from simulations.engine import runner

#: The turn the table looks at: the last one every precon is measured on
#: that still says something about the early game.
TURN = 4

#: The z of a 95% interval: a share is told with its margin at that confidence.
Z_95 = 1.96

#: The fewest precons a deck is compared with: "ahead of 2 of 3" says nothing.
PRECONS_AT_LEAST = 5
PRECONS_KEY = "simulations:precon-numbers"
#: As long as a report is kept (`sharing.views.CACHE_SECONDS`).
PRECONS_SECONDS = 600


@dataclass(frozen=True)
class Numbers:
    games: int
    lands: int
    on_curve: float | None
    kept_seven: float
    mana_turn_4: float | None
    spells_seen: float
    #: The spread (standard deviation) of the mana on turn 4, for how exact its
    #: mean is when two runs are compared (`changes`); None without a turn 4.
    mana_spread: float | None = None


def _share(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0.0


def margin(share: float, games: int) -> float:
    """How far a share of `games` games is from the true one, at 95%, in points.

    The normal approximation, 1.96 * sqrt(p(1 - p) / n) (P20 F4, E1.22): the
    report used to call everything under one point noise "whatever the number
    of games", which is wrong both ways - 10,000 games pin a share of 80% to
    about 0.8 points, 2,000 games only to about 1.8.
    """
    if not games:
        return 0.0
    p = share / 100
    return 100 * Z_95 * math.sqrt(p * (1 - p) / games)


def of(run) -> Numbers:
    """The numbers of one finished run."""
    result = runner.read(run.result)
    games = result["iterations"]
    mulligans = result["mulligans"]
    on_curve = mana = spread = None
    if len(result["turn_stats"]) >= TURN:
        turn = result["turn_stats"][TURN - 1]
        lands = turn["lands"].counts
        on_curve = _share(sum(times for value, times in lands.items() if value >= TURN), games)
        mana = turn["mana"].mean
        spread = _spread(turn["mana"].counts, mana)
    return Numbers(
        games=games,
        lands=run.lands_total,
        on_curve=on_curve,
        kept_seven=_share(mulligans.get(0, 0), sum(mulligans.values())),
        mana_turn_4=mana,
        spells_seen=_spells_seen(result),
        mana_spread=spread,
    )


def _spread(counts: dict, mean: float) -> float:
    """The standard deviation of a histogram's values."""
    observations = sum(counts.values())
    if not observations:
        return 0.0
    return math.sqrt(sum(times * (value - mean) ** 2 for value, times in counts.items())
                     / observations)


def _spells_seen(result: dict) -> float:
    """The spells seen by the last turn, from the report's curve counts
    (`seen` "mv:<n>" groups, spells only); 0 on a run from before them."""
    groups = result.get("seen") or {}
    games = result["iterations"]
    total = sum(group["cards"][-1] for key, group in groups.items() if key.startswith("mv:"))
    return total / games if games else 0.0


def _within(share: float, games: int) -> dict:
    return {"share": percent(share), "margin": number(margin(share, games), 1)}


def sentences(numbers: Numbers) -> list[str]:
    """What the numbers say, in the page's language. Built from the numbers
    alone: no written summary on a data page, and the same sentences on the
    report of a deck of one's own. A share says how exact it is."""
    said = []
    if numbers.on_curve is not None:
        said.append(gettext(
            "In %(share)s of games (± %(margin)s points) it has four lands in play on turn 4.")
            % _within(numbers.on_curve, numbers.games))
    said.append(gettext(
        "It keeps its first seven cards in %(share)s of games (± %(margin)s points).")
        % _within(numbers.kept_seven, numbers.games))
    if numbers.mana_turn_4 is not None:
        said.append(gettext("On turn 4 it has %(mana)s mana on average.")
                    % {"mana": number(numbers.mana_turn_4, 1)})
    return said


@dataclass(frozen=True)
class Standing:
    """Where one of a deck's numbers stands among the precons'."""

    label: str
    value: str
    #: Precons with a lower number, out of `total`.
    ahead_of: int
    total: int
    #: The precons' median.
    middle: str
    #: Each precon's place on the strip and the deck's, in % of its width.
    ticks: list[float]
    mark: float
    #: The strip's two ends, so a close race does not look like a wide one.
    low: str
    high: str


def precons() -> list[tuple[UUID, Numbers]]:
    """(deck id, numbers) of every published precon: its newest finished run,
    the one its page shows (`datapages.services.report_of`). Reached through
    the related names, so `simulations` still does not import `datapages`."""
    found = cache.get(PRECONS_KEY)
    if found is None:
        from simulations.models import SimulationRun

        runs = (SimulationRun.objects
                .filter(deck__precon__published=True, share__isnull=False,
                        status=SimulationRun.Status.DONE)
                .order_by("deck_id", "-finished_at").distinct("deck_id"))
        found = [(run.deck_id, of(run)) for run in runs]
        cache.set(PRECONS_KEY, found, PRECONS_SECONDS)
    return found


def _place(value: float, low: float, high: float) -> float:
    return 50.0 if high == low else round(100 * (value - low) / (high - low), 1)


def standings(numbers: Numbers, deck_id: UUID | None = None) -> list[Standing]:
    """The deck's numbers next to the precons', higher is better in each. Empty
    when fewer than `PRECONS_AT_LEAST` precons have a run; a precon's own page
    leaves the precon out (`deck_id`)."""
    others = [theirs for deck, theirs in precons() if deck != deck_id]
    if len(others) < PRECONS_AT_LEAST:
        return []
    # The precon table's own column names (`datapages/precons.html`).
    shown = (
        (gettext("4 lands by turn %(turn)s") % {"turn": TURN}, "on_curve", percent),
        (gettext("Kept seven"), "kept_seven", percent),
        (gettext("Mana on turn %(turn)s") % {"turn": TURN}, "mana_turn_4",
         lambda value: number(value, 1)),
    )
    found = []
    for label, field, say in shown:
        mine = getattr(numbers, field)
        theirs = [getattr(other, field) for other in others]
        theirs = [value for value in theirs if value is not None]
        if mine is None or len(theirs) < PRECONS_AT_LEAST:
            continue
        low, high = min(*theirs, mine), max(*theirs, mine)
        found.append(Standing(
            label=label, value=say(mine),
            ahead_of=sum(value < mine for value in theirs), total=len(theirs),
            middle=say(statistics.median(theirs)),
            ticks=[_place(value, low, high) for value in theirs],
            mark=_place(mine, low, high),
            low=say(low), high=say(high),
        ))
    return found


@dataclass(frozen=True)
class Change:
    """One number of this run next to the same number of the deck's run before."""

    label: str
    before: str
    now: str
    #: "up" or "down" (higher is better in each), or "same" when the difference
    #: is within what chance alone moves it, at 95%.
    moved: str


def _change(label: str, before: float, now: float, chance: float, say) -> Change:
    """Better or worse only beyond chance and when the shown numbers differ:
    "100% → 100% worse" would be true of 99.6% and still read as nonsense."""
    shown_before, shown_now = say(before), say(now)
    if abs(now - before) <= chance or shown_before == shown_now:
        moved = "same"
    else:
        moved = "up" if now > before else "down"
    return Change(label, shown_before, shown_now, moved)


def changes(before: Numbers, now: Numbers) -> list[Change]:
    """The precon table's three numbers, the run before against this one.

    Two runs of the very same deck differ a little by chance alone, so a
    difference only counts as better or worse beyond the 95% margin of the
    difference of the two: 1.96 * sqrt(se1² + se2²). A number one of the runs
    does not have (no turn 4) is left out, and a difference the shown numbers
    do not show is "about the same" too.
    """
    found = []
    for label, field in ((gettext("4 lands by turn %(turn)s") % {"turn": TURN}, "on_curve"),
                         (gettext("Kept seven"), "kept_seven")):
        old, new = getattr(before, field), getattr(now, field)
        if old is None or new is None:
            continue
        chance = math.hypot(margin(old, before.games), margin(new, now.games))
        found.append(_change(label, old, new, chance, percent))
    if before.mana_turn_4 is not None and now.mana_turn_4 is not None:
        chance = Z_95 * math.hypot(_error(before.mana_spread, before.games),
                                   _error(now.mana_spread, now.games))
        found.append(_change(gettext("Mana on turn %(turn)s") % {"turn": TURN},
                             before.mana_turn_4, now.mana_turn_4, chance,
                             lambda value: number(value, 1)))
    return found


def _error(spread: float | None, games: int) -> float:
    """The standard error of a mean; a run cached before `mana_spread` counts as 0."""
    return (spread or 0.0) / math.sqrt(games) if games else 0.0
