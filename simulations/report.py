"""Turning a stored run into the numbers a report page shows.

Kept apart from the view, because this is where the honesty of the product
lives and it deserves to be testable without a browser.

:func:`opening_lands` puts the simulated distribution of opening-hand lands
next to the **exact hypergeometric probability** for the same deck. It
demonstrates that the simulation reproduces closed-form mathematics everywhere
closed-form mathematics can reach, which is the only honest way to ask someone
to believe the parts where it cannot. Since phase 10 (T5.5) the page no longer
shows that table - the user test found it read as noise - but the comparison is
still asserted, by `tests/test_simulations_runs.py`, on every test run, and the
methodology page still says so.

`math.comb` rather than scipy: scipy is ~90 MB and this needs one binomial
coefficient. It is a test dependency and must never enter the production image,
which runs on a 128 MiB cloudlet.
"""

from dataclasses import dataclass
from math import comb, sqrt

from simulations import charts
from simulations.engine import runner

#: Cards in an opening hand, before any mulligan.
HAND_SIZE = 7

#: Percentiles worth showing. The median is what usually happens; the tenth is
#: the game that goes wrong, which is the one a deck is actually fixed for.
PERCENTILES = (10, 50, 90)

#: Rows below this in both columns are noise and only make the table longer.
NOISE_FLOOR_PCT = 0.05

#: The word a report uses for each mana letter. Presentation, so it lives here
#: rather than in the engine - and spelled the way the rest of the site spells
#: things, which is British.
COLOR_NAMES = {
    "W": "White",
    "U": "Blue",
    "B": "Black",
    "R": "Red",
    "G": "Green",
    "C": "Colourless",
}


@dataclass(frozen=True)
class Row:
    """One row of a distribution table."""

    label: str
    count: int
    share: float
    #: The exact probability, where one exists. `None` elsewhere, and the
    #: template leaves the cell empty rather than inventing a dash.
    exact: float | None = None

    @property
    def gap(self) -> float | None:
        """Percentage points between simulation and exact maths."""
        return None if self.exact is None else self.share - self.exact


@dataclass(frozen=True)
class ColorRow:
    """What one turn's mana was actually made of.

    `amounts` is a tuple in the same order as the report's colour columns, for
    the same reason `TurnRow` keeps its percentiles that way: a Django template
    cannot index a dict by a loop variable without a custom filter.
    """

    turn: int
    amounts: tuple[float, ...]
    total: float


@dataclass(frozen=True)
class TurnRow:
    """What one turn looked like across every game.

    The percentiles are tuples in `PERCENTILES` order rather than dicts keyed
    by percentile: a Django template cannot look a dict up by a loop variable
    without a custom filter, and a custom filter is a lot of machinery to buy
    for a column heading.
    """

    turn: int
    mana_mean: float
    lands_mean: float
    mana_percentiles: tuple[int, ...]
    lands_percentiles: tuple[int, ...]


def hypergeometric(successes_drawn: int, population: int, successes: int,
                   drawn: int) -> float:
    """P(exactly `successes_drawn` lands in an opening hand), as a percentage.

    The textbook hypergeometric probability mass function: drawing `drawn`
    cards from `population` of which `successes` are lands, without
    replacement.
    """
    if not population or drawn > population or successes > population:
        return 0.0
    if successes_drawn > successes or drawn - successes_drawn > population - successes:
        return 0.0
    return 100.0 * (
        comb(successes, successes_drawn)
        * comb(population - successes, drawn - successes_drawn)
        / comb(population, drawn)
    )


def mulligans(result: dict) -> list[Row]:
    """How often the opening hand was thrown back."""
    counts = result["mulligans"]
    total = sum(counts.values())
    return [
        Row(label=_mulligan_label(taken), count=hits, share=_pct(hits, total))
        for taken, hits in sorted(counts.items())
        if hits
    ]


def opening_lands(result: dict, library_size: int, lands_total: int) -> list[Row]:
    """The first seven cards, simulated against the exact distribution.

    **The first seven, not the hand that was kept.** The kept hand cannot do
    this job: the mulligan rule throws back hands with no lands and hands with
    six, which reshapes the distribution into something no closed form
    describes. Comparing the kept hand against the hypergeometric would show a
    systematic disagreement of ten points or more and would invite exactly the
    wrong conclusion - that the simulation is broken, when what is actually
    being measured is the mulligan rule doing its job.

    Against the first seven the two columns must agree to sampling error, and
    that agreement is the most credibility-building thing on the page: one
    column comes from playing the deck thousands of times, the other from
    counting combinations, and they land on the same numbers.
    """
    counts = result["first_seven"]
    total = sum(counts.values())

    rows = []
    for lands in range(HAND_SIZE + 1):
        hits = counts.get(lands, 0)
        share = _pct(hits, total)
        exact = hypergeometric(lands, library_size, lands_total, HAND_SIZE)
        if share < NOISE_FLOOR_PCT and exact < NOISE_FLOOR_PCT:
            continue
        rows.append(Row(label=str(lands), count=hits, share=share, exact=exact))
    return rows


def kept_lands(result: dict) -> list[Row]:
    """Lands in the hand that was actually kept, after any mulligans.

    No exact column, on purpose. This distribution is the product of the
    mulligan heuristic, and inventing a closed form for it would be inventing
    a number.
    """
    counts = result["opening_lands"]
    total = sum(counts.values())
    return [
        Row(label=str(lands), count=counts.get(lands, 0), share=_pct(counts.get(lands, 0), total))
        for lands in range(HAND_SIZE + 1)
        if _pct(counts.get(lands, 0), total) >= NOISE_FLOOR_PCT
    ]


def turns(result: dict) -> list[TurnRow]:
    """Mana and lands per turn, as a mean and as percentiles.

    Percentiles are the reason Phase 3 counts values instead of averaging
    them. A mean of 4.1 mana on turn three hides whether that is "4 mana most
    games" or "7 mana half the time and 1 the other half", and those are
    different decks.
    """
    rows = []
    for index, stats in enumerate(result["turn_stats"], start=1):
        mana, lands = stats["mana"], stats["lands"]
        rows.append(
            TurnRow(
                turn=index,
                mana_mean=mana.mean,
                lands_mean=lands.mean,
                mana_percentiles=tuple(mana.percentile(p) for p in PERCENTILES),
                lands_percentiles=tuple(lands.percentile(p) for p in PERCENTILES),
            )
        )
    return rows


def color_columns(result: dict) -> list[tuple[str, str]]:
    """The mana letters this deck actually made, with their labels.

    Only the colours that turned up. A mono-black deck has no business being
    shown four empty columns, and a reader who sees them learns to ignore the
    table rather than read it.

    Returns nothing at all for a result stored before the engine reported
    colour. That is the honest answer for an old run - the numbers were never
    measured - and it is why every caller has to cope with an empty list
    rather than assume the section is always there.
    """
    columns = []
    for color in runner.MANA_SOURCES:
        field = runner.COLOR_FIELD[color]
        # `total`, not truthiness: every game contributes a value to every
        # colour's histogram, so a colour the deck never made has a histogram
        # full of zeroes - which is a perfectly non-empty histogram.
        if any(_histogram_total(stats.get(field)) for stats in result["turn_stats"]):
            columns.append((color, COLOR_NAMES.get(color, color)))
    return columns


def color_rows(result: dict, columns: list[tuple[str, str]]) -> list[ColorRow]:
    """Mean mana per colour, turn by turn.

    The number this table exists to expose: a total of five mana on turn three
    reads as a working mana base, and five mana of which four is the wrong
    colour does not. The engine pays coloured costs out of coloured mana, so it
    already knows the difference - this is the difference being reported rather
    than averaged away.
    """
    if not columns:
        return []

    rows = []
    for index, stats in enumerate(result["turn_stats"], start=1):
        amounts = tuple(
            _histogram_mean(stats.get(runner.COLOR_FIELD[color]))
            for color, _label in columns
        )
        rows.append(
            ColorRow(turn=index, amounts=amounts, total=stats["mana"].mean)
        )
    return rows


def _histogram_mean(histogram) -> float:
    """The mean of a histogram that may not be there at all."""
    return histogram.mean if histogram is not None else 0.0


def _histogram_total(histogram) -> int:
    """Every value a histogram observed, added up. Zero when it is missing."""
    return histogram.total if histogram is not None else 0


#: The per-turn counters, and what each one means on a report page. Kept here
#: rather than in the template so that the wording is testable and so that a
#: metric cannot quietly appear on screen with no explanation of what it counts.
MILESTONES = (
    ("commander", "Commander on the battlefield"),
    ("draw_engine", "A card-advantage engine in play"),
    ("ramp_engine", "Two or more scaling mana sources"),
    ("sac_outlet", "A sacrifice outlet in play"),
    ("recursive", "A recursive creature in play"),
    ("engine_online", "Sacrifice outlet and recursion together"),
    ("drain", "A drain payoff in play"),
    ("necropotence", "Trading the draw step for cards"),
    ("sol_ring", "A mana source that made more than it cost"),
)


#: The line chart has eight colours (DESIGN.md, "Line charts"). A deck that
#: reaches all nine milestones shows the eight most frequent as lines; every
#: one stays in the table under the chart (phase 10 T5.8).
MAX_LINES = 8


def milestone_chart(rows: list[dict], turns: int) -> charts.LineChart | None:
    """The milestones as lines on the percent scale, the most frequent first."""
    if not rows:
        return None
    ranked = sorted(rows, key=lambda row: (-row["shares"][-1], row["label"]))[:MAX_LINES]
    shown = {row["key"] for row in ranked}
    series = [(row["key"], row["label"], row["shares"]) for row in rows if row["key"] in shown]
    return charts.line_chart(series, [str(turn) for turn in range(1, turns + 1)],
                             top_value=charts.PERCENT_TOP, y_ticks=charts.PERCENT_TICKS)


def milestones(result: dict) -> list[dict]:
    """For each metric, the share of games it had happened by each turn.

    Replaces the hardcoded groups of the old report: the engine reads these
    off the cards' own role tags, so a deck that has no sacrifice outlets
    simply shows zeroes rather than needing a different report.
    """
    total = result["iterations"]
    rows = []
    for key, label in MILESTONES:
        shares = [_pct(stats[key], total) for stats in result["turn_stats"]]
        if not any(shares):
            # A metric that never fires for this deck is noise on the page.
            continue
        rows.append({"key": key, "label": label, "shares": shares})
    return rows


#: The categories a Commander deck is sorted into, in the order a deck list
#: sorts them, and what the page calls each (Phase 9, "The category
#: vocabulary"). The keys are `simulation.analysis.SEEN_CATEGORIES`, and a test
#: holds the two lists together.
SEEN_ROLES = (
    ("ramp", "Ramp"),
    ("draw", "Card draw"),
    ("removal", "Removal"),
    ("wipe", "Board wipe"),
    ("tutor", "Tutor"),
    ("counterspell", "Counterspell"),
    ("protection", "Protection"),
    ("recursion", "Recursion"),
)


@dataclass(frozen=True)
class Spread:
    """A count per game: its mean and, when the run counted it, its spread.

    `sd` is the standard deviation over the games, `None` on a run from before
    phase 10, whose page then shows the mean alone.
    """

    mean: float
    sd: float | None


def _spreads(entry: dict, games: int) -> list[Spread]:
    """Mean and standard deviation per turn, from the sums the engine kept."""
    if not games:
        return [Spread(0.0, None) for _ in entry["cards"]]
    squares = entry.get("squares")
    spreads = []
    for turn, total in enumerate(entry["cards"]):
        mean = total / games
        sd = None
        if squares is not None:
            # max(0, ...): rounding can leave a hair below zero on a flat line.
            sd = sqrt(max(0.0, squares[turn] / games - mean * mean))
        spreads.append(Spread(mean, sd))
    return spreads


@dataclass(frozen=True)
class CurveBar:
    """One mana value in the "what you drew" curve, at one turn."""

    label: str
    mean: float
    #: Height as a share of the tallest bar on the chart, 0-100.
    height: float


def seen(result: dict) -> dict | None:
    """What the player had seen by each turn, as the page draws it.

    `None` for a run made before the engine counted it (Phase 9 E), and the
    page says "run again" - an old run is not a deck that never draws ramp.

    Three pictures:

    * **roles** - the share of games that had seen at least one card of the
      category by that turn. "Ramp by turn two" is a yes/no question per game,
      and the share is its answer.
    * **types** - how many cards of each type had been seen, on average.
      Almost every game sees a creature by turn one, so the share would be a
      flat line at the top; the count is the part that differs between decks.
    * **curve** - the same count by mana value, spells only, one set of bars
      per turn.

    A category the deck has no card in has no line: an absent line is the
    honest picture of "none in the deck", where a line along the bottom would
    read as "never drawn".
    """
    groups = result.get("seen")
    if groups is None:
        return None
    games = result["iterations"]
    turns = result["turns"]
    x_labels = [str(turn) for turn in range(1, turns + 1)]

    def shares(key):
        return [_pct(count, games) for count in groups[key]["games"]]

    def means(key):
        return [count / games if games else 0.0 for count in groups[key]["cards"]]

    role_series = [(key, label, shares(f"role:{key}"))
                   for key, label in SEEN_ROLES if f"role:{key}" in groups]
    type_series = [(kind, kind.title(), means(f"type:{kind}"))
                   for kind in runner.SEEN_CARD_TYPES if f"type:{kind}" in groups]
    largest = max((value for _, _, values in type_series for value in values),
                  default=0.0)
    count_top, count_ticks = charts.count_scale(largest)

    return {
        "roles": charts.line_chart(role_series, x_labels, top_value=charts.PERCENT_TOP,
                                   y_ticks=charts.PERCENT_TICKS),
        "types": charts.line_chart(type_series, x_labels, top_value=count_top,
                                   y_ticks=count_ticks),
        # "The numbers" under the charts (phase 10 T5.3): a count reads
        # "3.2 ± 1.1", a share stays a plain percentage (K3).
        "type_spreads": [
            {"label": kind.title(), "spreads": _spreads(groups[f"type:{kind}"], games)}
            for kind in runner.SEEN_CARD_TYPES if f"type:{kind}" in groups
        ],
        "curve": _curve(groups, games, turns),
        "turns": x_labels,
    }


def _curve(groups: dict, games: int, turns: int) -> list[dict]:
    """Per turn, the mean number of spells seen at each mana value.

    Every bar on every turn shares one scale - the tallest bar of the last
    turn, which is the tallest there is, because nothing seen is ever unseen.
    Switching turns then shows the curve growing rather than re-scaling itself
    to look the same each time.
    """
    cap = runner.SEEN_MV_CAP
    labels = [(f"mv:{value}", str(value)) for value in range(cap)]
    labels.append((f"mv:{cap}", f"{cap}+"))

    def mean(key, turn):
        entry = groups.get(key)
        return entry["cards"][turn] / games if entry and games else 0.0

    tallest = max((mean(key, turns - 1) for key, _ in labels), default=0.0)
    return [
        {
            "turn": turn + 1,
            "bars": [
                CurveBar(label=label, mean=mean(key, turn),
                         height=100.0 * mean(key, turn) / tallest if tallest else 0.0)
                for key, label in labels
            ],
        }
        for turn in range(turns)
    ]


def annotations_changed_since(run) -> bool:
    """Whether anybody has recorded a judgement since this run was computed.

    A stored result is never re-read: it is what the engine saw at the time,
    and rewriting history to match today's annotations would destroy the one
    thing a stored run is good for. So the report says instead that the inputs
    have moved on, and offers a re-run.

    The same scope filter the adapter merges with, so "changed" means changed
    in a way that would actually alter this deck's next reading.
    """
    from simulations.engine.adapter import cards_filter, scope_filter
    from simulations.models import CardAnnotation

    if run.finished_at is None:
        return False
    return (
        CardAnnotation.objects.filter(cards_filter(run.deck))
        .filter(scope_filter(run.deck), updated_at__gt=run.finished_at)
        .exists()
    )


def build(run) -> dict:
    """Everything the report template needs, from one stored run."""
    result = runner.read(run.result)
    columns = color_columns(result)
    milestone_rows = milestones(result)
    return {
        "annotations_changed": annotations_changed_since(run),
        "iterations": result["iterations"],
        "turns": result["turns"],
        "on_the_play": result["on_the_play"],
        "mulligans": mulligans(result),
        "turn_rows": turns(result),
        "color_columns": columns,
        "color_rows": color_rows(result, columns),
        "milestones": milestone_rows,
        "milestone_chart": milestone_chart(milestone_rows, result["turns"]),
        "milestones_hidden": max(0, len(milestone_rows) - MAX_LINES),
        "seen": seen(result),
        "combos": combo_measurements(run),
        "percentiles": PERCENTILES,
    }


def combo_measurements(run) -> list:
    """What this run found out about the deck's combos, if anything.

    Read off the run rather than off the deck, like everything else in a
    report: these are the numbers that were true for the deck the simulation
    actually shuffled, and a later run of a changed deck gets its own.

    Reached through the related name rather than by importing `combos`:
    `combos` is the consumer of `simulations` - a measurement points at the run
    that made it - and the arrow only has to go one way.
    """
    return list(
        run.combo_measurements.select_related("combo")
        .prefetch_related("combo__cards")
    )


def _pct(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


def _mulligan_label(taken: int) -> str:
    if taken == 0:
        return "Kept the first seven"
    # The first mulligan is free in multiplayer Commander, so the hand size
    # only starts shrinking with the second.
    kept = HAND_SIZE - max(0, taken - 1)
    return f"{taken} mulligan{'s' if taken > 1 else ''} (keep {kept})"
