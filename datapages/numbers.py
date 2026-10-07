"""The few numbers a precon is compared by (P11).

Read off a finished run's stored result, the same one its report draws, so
the table and the page can never disagree. Each is a share of games or a
mean over them:

- `on_curve`: games with at least four lands in play on turn 4 - every land
  drop made, the commonest way a Commander deck stumbles early;
- `kept_seven`: games that kept the first seven cards, no mulligan;
- `mana_turn_4`: mana available on turn 4, on average.
"""

from dataclasses import dataclass

from django.utils.translation import gettext

from core.l10n import number, percent
from simulations.engine import runner

#: The turn the table looks at: the last one every precon is measured on
#: that still says something about the early game.
TURN = 4


@dataclass(frozen=True)
class Numbers:
    games: int
    lands: int
    on_curve: float
    kept_seven: float
    mana_turn_4: float


def _share(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0.0


def of(run) -> Numbers:
    """The numbers of one finished run."""
    result = runner.read(run.result)
    games = result["iterations"]
    turn = result["turn_stats"][TURN - 1]
    lands = turn["lands"].counts
    mulligans = result["mulligans"]
    return Numbers(
        games=games,
        lands=run.lands_total,
        on_curve=_share(sum(times for value, times in lands.items() if value >= TURN), games),
        kept_seven=_share(mulligans.get(0, 0), sum(mulligans.values())),
        mana_turn_4=turn["mana"].mean,
    )


def sentences(numbers: Numbers) -> list[str]:
    """What the numbers say, in the page's language. Built from the numbers
    alone: no written summary on a data page."""
    return [
        gettext("In %(share)s of games it has four lands in play on turn 4.")
        % {"share": percent(numbers.on_curve)},
        gettext("It keeps its first seven cards in %(share)s of games.")
        % {"share": percent(numbers.kept_seven)},
        gettext("On turn 4 it has %(mana)s mana on average.")
        % {"mana": number(numbers.mana_turn_4, 1)},
    ]
