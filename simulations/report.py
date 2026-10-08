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

from django.utils.translation import gettext, gettext_noop, ngettext

from core.l10n import number
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
#:
#: Every label in this module is English and marked with `gettext_noop`
#: (phase 12): the English text is also a key - matched against Mistral's
#: answer, sent to it in the facts - so it is translated only where a page
#: shows it, with `gettext(label)`.
COLOR_NAMES = {
    "W": gettext_noop("White"),
    "U": gettext_noop("Blue"),
    "B": gettext_noop("Black"),
    "R": gettext_noop("Red"),
    "G": gettext_noop("Green"),
    "C": gettext_noop("Colourless"),
}

#: What a page calls each printed card type (`simulation.cards.CARD_TYPES`).
CARD_TYPE_NAMES = {
    "creature": gettext_noop("Creature"),
    "planeswalker": gettext_noop("Planeswalker"),
    "battle": gettext_noop("Battle"),
    "artifact": gettext_noop("Artifact"),
    "enchantment": gettext_noop("Enchantment"),
    "instant": gettext_noop("Instant"),
    "sorcery": gettext_noop("Sorcery"),
    "land": gettext_noop("Land"),
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
            columns.append((color, gettext(COLOR_NAMES[color]) if color in COLOR_NAMES
                            else color))
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
    ("commander", gettext_noop("Commander on the battlefield")),
    ("draw_engine", gettext_noop("A card-advantage engine in play")),
    ("ramp_engine", gettext_noop("Two or more scaling mana sources")),
    ("sac_outlet", gettext_noop("A sacrifice outlet in play")),
    ("recursive", gettext_noop("A recursive creature in play")),
    ("engine_online", gettext_noop("Sacrifice outlet and recursion together")),
    ("drain", gettext_noop("A drain payoff in play")),
    ("necropotence", gettext_noop("Trading the draw step for cards")),
    ("sol_ring", gettext_noop("A mana source that made more than it cost")),
)


#: What each milestone means, for the info line under the chart (phase 10
#: T5.2). Kept beside the labels so a new milestone cannot arrive without one;
#: a test holds the two together.
MILESTONE_INFO = {
    "commander": gettext_noop("Your commander has been cast and is on the battlefield."),
    "draw_engine": gettext_noop(
        "A permanent that keeps drawing you cards, such as Phyrexian Arena."),
    "ramp_engine": gettext_noop("Two mana sources that make more as your board grows, "
                                "such as Cabal Coffers and Crypt Ghast."),
    "sac_outlet": gettext_noop(
        "A permanent that lets you sacrifice creatures again and again."),
    "recursive": gettext_noop("A creature that comes back from your graveyard by itself."),
    "engine_online": gettext_noop(
        "A sacrifice outlet and a recursive creature at once: a loop."),
    "drain": gettext_noop(
        "A permanent that makes your opponents lose life when something happens."),
    "necropotence": gettext_noop("A card that trades your draw step for something better, "
                                 "such as Necropotence."),
    "sol_ring": gettext_noop("An artifact that made more mana than it cost, such as Sol Ring."),
}

#: The share of games that makes a turn the "typical" one: half of them. It
#: feeds the info sentence only; the dashed marker on the chart is gone
#: (phase 11 K14 - the sentence said the same thing).
TYPICAL_SHARE = 50.0

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
    infos = {
        row["key"]: " ".join(filter(None, (
            gettext(MILESTONE_INFO[row["key"]]) if row["key"] in MILESTONE_INFO else "",
            _typical_sentence(row["shares"], GET_THERE, turns))))
        for row in rows
    }
    return charts.line_chart(series, [str(turn) for turn in range(1, turns + 1)],
                             top_value=charts.PERCENT_TOP, y_ticks=charts.percent_ticks(),
                             infos=infos)


#: The two things `_typical_sentence` says a game did: a milestone it got to,
#: a category it had a card of. Whole sentences each, so a language can put
#: the verb where it belongs.
GET_THERE = "get there"
HAVE_ONE = "have one"


def _typical_sentence(shares, verb: str, turns: int) -> str:
    """"Half your games have one by turn 4." - or the honest negative."""
    for turn, share in enumerate(shares, start=1):
        if share >= TYPICAL_SHARE:
            text = (gettext("Half your games get there by turn %(turn)s.") if verb == GET_THERE
                    else gettext("Half your games have one by turn %(turn)s."))
            return text % {"turn": turn}
    text = (gettext("Fewer than half your games get there by turn %(turn)s.")
            if verb == GET_THERE
            else gettext("Fewer than half your games have one by turn %(turn)s."))
    return text % {"turn": turns}


def _average_sentence(spread: "Spread", turns: int) -> str:
    """"On average 0.8 ± 0.7 drawn by turn 6." - the ± only when it was counted."""
    mean = number(spread.mean, 1)
    if spread.sd is not None:
        mean = f"{mean} ± {number(spread.sd, 1)}"
    return gettext("On average %(mean)s drawn by turn %(turn)s.") % {"mean": mean,
                                                                    "turn": turns}


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
        rows.append({"key": key, "label": gettext(label), "shares": shares})
    return rows


#: The Commander Brackets and the fewest turns Wizards expects a game in each
#: to last (Commander Brackets update, 21 October 2025: "expect to be able to
#: play at least" nine, eight, six and four turns). Bracket 5, cEDH, "could end
#: on any turn" and has no row. The names are Wizards' and stay in English.
BRACKETS = (
    (1, "Exhibition", 9),
    (2, "Core", 8),
    (3, "Upgraded", 6),
    (4, "Optimized", 4),
)
CEDH = (5, "cEDH")
BRACKETS_SOURCE = ("https://magic.wizards.com/en/news/announcements/"
                   "commander-brackets-beta-update-october-21-2025")

#: A bracket fits while fewer than half the games have a game-ending combo
#: together before its turn - Wizards says "expect", and half is what the rest
#: of the report calls typical. A constant, so a stricter reading is one edit.
BRACKET_FITS_BELOW = TYPICAL_SHARE


@dataclass(frozen=True)
class BracketRow:
    """One bracket, and how often this deck's games would have ended too soon."""

    number: int
    name: str
    #: The fewest turns a game in this bracket is expected to last.
    turns: int
    #: % of games with a game-ending combo together before `turns`; None when
    #: the run did not play the turns it takes to know.
    share: float | None

    @property
    def fits(self) -> bool | None:
        return None if self.share is None else self.share < BRACKET_FITS_BELOW

    @property
    def needs_turns(self) -> int:
        """The turns a run has to play to check this bracket."""
        return self.turns - 1


def bracket_tempo(result: dict, timed_combos: bool) -> dict | None:
    """How fast a game-ending combo comes together, against the brackets (P8).

    A combo that is together at the end of turn N is read as ending the game on
    turn N: a game in a six-turn bracket is cut short by one that is together
    by turn five. "Together" is all the simulator knows - it does not play the
    combo out - so the page says so.

    Args:
        timed_combos: whether this run measured any combo at all. A result with
            no ``wins`` block but with timed combos is from before P8 and gets
            "run it again"; one with neither timed nothing.

    Returns None for a result with nothing to say and nothing to ask, which is
    a run that timed no combo: the section is then left off the page.
    """
    wins = result.get("wins")
    if wins is None:
        return {"rerun": True} if timed_combos else None
    games, by_turn, turns = wins["games"], wins["by_turn"], result["turns"]

    rows = []
    for number_, name, least in BRACKETS:
        index = least - 2  # together by the end of the turn before
        share = _pct(by_turn[index], games) if index < len(by_turn) else None
        rows.append(BracketRow(number=number_, name=name, turns=least, share=share))

    # The lowest bracket that fits. Brackets nest - a deck that ends games
    # too soon for six turns ends them too soon for eight - so when every
    # measured one is too slow, so would the unmeasured ones be, and only cEDH
    # is left. A run too short to check any bracket says nothing either way.
    measured = [row for row in rows if row.share is not None]
    verdict = next((row for row in measured if row.fits), None)
    cedh = bool(measured) and verdict is None
    unchecked = [] if cedh else [
        row for row in rows
        if row.share is None and (verdict is None or row.number < verdict.number)
    ]
    return {
        "rerun": False,
        "rows": rows,
        "verdict": verdict,
        "cedh": CEDH if cedh else None,
        "unchecked": unchecked,
        "needs_turns": max((row.needs_turns for row in unchecked), default=0),
        "typical": _typical_win_sentence(by_turn, games, turns),
        "source": BRACKETS_SOURCE,
    }


def _typical_win_sentence(by_turn, games: int, turns: int) -> str:
    """"Half your games have a game-ending combo together by turn 5." - or not."""
    if not any(by_turn):
        return gettext("No game-ending combo came together in %(turns)s turns.") % {
            "turns": turns}
    for turn, count in enumerate(by_turn, start=1):
        if _pct(count, games) >= TYPICAL_SHARE:
            return gettext("Half your games have a game-ending combo together by turn "
                           "%(turn)s.") % {"turn": turn}
    return gettext("Fewer than half your games have a game-ending combo together by turn "
                   "%(turn)s.") % {"turn": turns}


#: The categories a Commander deck is sorted into, in the order a deck list
#: sorts them, and what the page calls each (Phase 9, "The category
#: vocabulary"). The keys are `simulation.analysis.SEEN_CATEGORIES`, and a test
#: holds the two lists together.
SEEN_ROLES = (
    ("ramp", gettext_noop("Ramp")),
    ("draw", gettext_noop("Card draw")),
    ("removal", gettext_noop("Removal")),
    ("wipe", gettext_noop("Board wipe")),
    ("tutor", gettext_noop("Tutor")),
    ("counterspell", gettext_noop("Counterspell")),
    ("protection", gettext_noop("Protection")),
    ("recursion", gettext_noop("Recursion")),
)

#: What each category means (phase 10, F5 in the user test report, approved).
#: The categories are Scryfall Tagger's community tags, or the user's own.
SEEN_ROLE_INFO = {
    "ramp": gettext_noop("More mana, now or on later turns: mana rocks, mana creatures, "
                         "extra lands, rituals."),
    "draw": gettext_noop("Cards that draw you cards."),
    "removal": gettext_noop("Gets something off the table: destroy, exile, bounce, damage. "
                            "Board wipes count too."),
    "wipe": gettext_noop("Removes many things at once."),
    "tutor": gettext_noop("Searches your library for a card."),
    "counterspell": gettext_noop("Counters a spell."),
    "protection": gettext_noop("Keeps your permanents alive: hexproof, indestructible, "
                               "phasing …"),
    "recursion": gettext_noop("Gets cards back from your graveyard: to your hand, the "
                              "battlefield or your library."),
}

#: What else a deck can be built around (phase 11 K17), and what the page calls
#: each. The keys are `simulation.analysis.SEEN_STRATEGIES` - the engine's own
#: role names (`cards.profiles.ROLE_FROM_TAG`) - and a test holds the two lists
#: together. The deck summary's chips use the same labels.
SEEN_STRATEGY_ROLES = (
    ("sac_outlet", gettext_noop("Sacrifice outlet")),
    ("reanimate", gettext_noop("Reanimate")),
    ("drain_payoff", gettext_noop("Drain")),
    ("steal", gettext_noop("Theft")),
    ("discard", gettext_noop("Discard")),
    ("cost_reducer", gettext_noop("Cost reducer")),
    ("evasion", gettext_noop("Evasion")),
)

#: What each strategy means (phase 11 P2), checked against the definitions of
#: the Scryfall Tagger tags they come from.
SEEN_STRATEGY_INFO = {
    "sac_outlet": gettext_noop("Lets you sacrifice your own permanents again and again: "
                               "an engine, not a one-off."),
    "reanimate": gettext_noop("Puts creature cards from a graveyard onto the battlefield."),
    "drain_payoff": gettext_noop("Opponents lose life and you gain it."),
    "steal": gettext_noop(
        "Takes control of something an opponent owns, or plays their cards."),
    "discard": gettext_noop("Makes a player discard cards."),
    "cost_reducer": gettext_noop("Makes your spells cheaper to cast."),
    "evasion": gettext_noop(
        "Helps your creatures get past blockers: flying, menace, unblockable …"),
}

#: What each card type line counts. A card with two types counts in both.
SEEN_TYPE_INFO = {
    "creature": gettext_noop("Creature cards, artifact and enchantment creatures included."),
    "planeswalker": gettext_noop("Planeswalker cards."),
    "battle": gettext_noop("Battle cards."),
    "artifact": gettext_noop("Artifact cards: mana rocks, equipment, artifact creatures."),
    "enchantment": gettext_noop(
        "Enchantment cards, auras and enchantment creatures included."),
    "instant": gettext_noop("Instants: spells you can cast at any time."),
    "sorcery": gettext_noop("Sorceries: spells for your own main phase."),
    "land": gettext_noop("Lands, basic and nonbasic."),
}


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

    The pictures:

    * **roles** - the share of games that had seen at least one card of the
      category by that turn. "Ramp by turn two" is a yes/no question per game,
      and the share is its answer. **strategies** asks the same of the
      strategies (phase 11).
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

    role_series = [(key, gettext(label), shares(f"role:{key}"))
                   for key, label in SEEN_ROLES if f"role:{key}" in groups]
    strategy_series = [(key, gettext(label), shares(f"role:{key}"))
                       for key, label in SEEN_STRATEGY_ROLES if f"role:{key}" in groups]
    type_series = [(kind, gettext(CARD_TYPE_NAMES[kind]), means(f"type:{kind}"))
                   for kind in runner.SEEN_CARD_TYPES if f"type:{kind}" in groups]
    type_spreads = {kind: _spreads(groups[f"type:{kind}"], games)
                    for kind, _, _ in type_series}
    # The band's top belongs on the scale too, or it would be cut off.
    largest = max((spread.mean + (spread.sd or 0.0)
                   for spreads in type_spreads.values() for spread in spreads),
                  default=0.0)
    count_top, count_ticks = charts.count_scale(largest)

    def share_infos(series, texts):
        return {
            key: " ".join((gettext(texts[key]),
                           _typical_sentence(values, HAVE_ONE, turns),
                           _average_sentence(_spreads(groups[f"role:{key}"], games)[-1],
                                             turns)))
            for key, _, values in series
        }

    def share_chart(series, texts):
        return charts.line_chart(series, x_labels, top_value=charts.PERCENT_TOP,
                                 y_ticks=charts.percent_ticks(),
                                 infos=share_infos(series, texts))

    type_infos = {
        kind: f"{gettext(SEEN_TYPE_INFO[kind])} "
              f"{_average_sentence(type_spreads[kind][-1], turns)}"
        for kind, _, _ in type_series
    }
    bands = {kind: [spread.sd for spread in spreads]
             for kind, spreads in type_spreads.items()
             if all(spread.sd is not None for spread in spreads)}

    return {
        "roles": share_chart(role_series, SEEN_ROLE_INFO),
        # Phase 11 K17. A run from before has no strategy groups and so no
        # lines here: the chart is absent, not empty.
        "strategies": share_chart(strategy_series, SEEN_STRATEGY_INFO),
        "types": charts.line_chart(type_series, x_labels, top_value=count_top,
                                   y_ticks=count_ticks, spreads=bands, infos=type_infos),
        # "The numbers" under the charts (phase 10 T5.3): a count reads
        # "3.2 ± 1.1", a share stays a plain percentage (K3).
        "type_spreads": [
            {"label": label, "spreads": type_spreads[kind]} for kind, label, _ in type_series
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
    measured = combo_measurements(run)
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
        "combos": measured,
        "brackets": bracket_tempo(
            result, timed_combos=any(not row.hypothetical for row in measured)),
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
        return gettext("Kept the first seven")
    # The first mulligan is free in multiplayer Commander, so the hand size
    # only starts shrinking with the second.
    kept = HAND_SIZE - max(0, taken - 1)
    return ngettext("%(count)s mulligan (keep %(kept)s)", "%(count)s mulligans (keep %(kept)s)",
                    taken) % {"count": taken, "kept": kept}
