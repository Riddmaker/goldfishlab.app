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
"""

import math
from dataclasses import dataclass

from django.utils.translation import gettext

from core.l10n import number, percent
from simulations.engine import runner

#: The turn the table looks at: the last one every precon is measured on
#: that still says something about the early game.
TURN = 4

#: The z of a 95% interval: a share is told with its margin at that confidence.
Z_95 = 1.96


@dataclass(frozen=True)
class Numbers:
    games: int
    lands: int
    on_curve: float | None
    kept_seven: float
    mana_turn_4: float | None
    spells_seen: float


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
    on_curve = mana = None
    if len(result["turn_stats"]) >= TURN:
        turn = result["turn_stats"][TURN - 1]
        lands = turn["lands"].counts
        on_curve = _share(sum(times for value, times in lands.items() if value >= TURN), games)
        mana = turn["mana"].mean
    return Numbers(
        games=games,
        lands=run.lands_total,
        on_curve=on_curve,
        kept_seven=_share(mulligans.get(0, 0), sum(mulligans.values())),
        mana_turn_4=mana,
        spells_seen=_spells_seen(result),
    )


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
