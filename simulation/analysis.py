"""Monte Carlo aggregation.

Worth keeping in mind when reading the output: Magic has enormous variance.
Even at 100,000 games the last decimal places are noise. Everything is
therefore reported to whole percent (see ``simulation-recherche.md``).

**From Phase 2 on the metric groups are no longer name lists.** They come from
the cards' own role tags, or from their abilities, so that the same metrics mean
something for any deck. The result keys are unchanged, because
``run_simulation.py`` and the snapshot tests read them.

**From Phase 3 on the per-game values are counted rather than collected.** A
run used to keep one Python integer per game per metric per turn, which is
O(iterations) in memory and - worse - cannot be merged: two halves of a run
could not be added up. They are now :class:`Histogram` objects, which are
O(distinct values) in memory, merge by addition, survive a round trip through
JSON, and hand out percentiles that the old mean-only report could not give at
all.

A ``Histogram`` still behaves like the list it replaced, because
``tests/test_statistics.py`` is vendored and byte-identical and does
``sum(result["turn_stats"][2]["lands"])``. Iterating one yields the observed
values, and ``len()`` is how many were observed.
"""

import hashlib
import random
from collections import Counter

from simulation import agent, combos
from simulation.cards import FLAT
from simulation.game import Game
from simulation.manacost import SOURCES

DEFAULT_TURNS = 3
DEFAULT_ITERATIONS = 100_000

#: Every mana source a turn is measured in, re-exported so that a consumer on
#: the Django side can name the colours without importing the engine's cost
#: model. WUBRG, then colourless.
MANA_SOURCES = SOURCES

#: Which ``turn_stats`` field holds each colour's distribution.
COLOR_FIELD = {color: f"mana_{color}" for color in MANA_SOURCES}

#: The per-colour fields, in WUBRG order.
MANA_COLOR_FIELDS = tuple(COLOR_FIELD[color] for color in MANA_SOURCES)

#: The per-turn metrics kept as distributions rather than as running totals.
#: Everything else in ``turn_stats`` is a plain count of games.
#:
#: ``black`` and ``mana_B`` hold the same numbers, and the redundancy is
#: deliberate. ``black`` was the only colour the engine reported before it
#: learned the other four; the golden parity snapshot in
#: ``tests/fixtures/engine_golden.json`` is keyed on it, and dropping it would
#: mean regenerating the one piece of evidence that the engine still behaves
#: as it used to. Six extra histograms per turn cost a few dozen integers.
HISTOGRAM_FIELDS = ("mana", "black", "lands", *MANA_COLOR_FIELDS)

#: Whole-run distributions, counted across games rather than per turn. They
#: merge and serialise the same way, so they are listed rather than spelled out
#: three times in ``as_json``, ``from_json`` and ``merge``.
COUNTER_FIELDS = ("mulligans", "opening_lands", "first_seven")


class Histogram:
    """How often each value was observed, behaving like the list of values.

    This replaces a plain Python list of one integer per game. The values it
    holds are small non-negative integers - mana 0-40, lands 0-15 - so counting
    them costs a few dozen entries no matter how many games are played, while
    the list cost one entry per game.

    Three things follow from that, and all three are the point:

    * **It merges.** ``a + b`` is the distribution of both runs together, so a
      simulation can be split across workers and added back up.
    * **It survives JSON.** A chunk returns a few kB rather than megabytes.
    * **It has percentiles.** The mean alone hides the games where the deck did
      nothing, which are exactly the games a player wants to know about.

    It is deliberately *not* a Counter subclass. ``sum(counter)`` adds the
    counter's keys, which would read as a plausible number and be wrong - and
    ``tests/test_statistics.py`` writes exactly that ``sum()``.
    """

    __slots__ = ("counts",)

    def __init__(self, counts=None):
        self.counts = Counter()
        if counts is not None:
            for value, times in dict(counts).items():
                if times:
                    self.counts[int(value)] += int(times)

    @classmethod
    def of(cls, values) -> "Histogram":
        """A histogram of an iterable of observations."""
        histogram = cls()
        for value in values:
            histogram.add(value)
        return histogram

    def add(self, value: int, times: int = 1) -> None:
        """Record ``times`` observations of ``value``."""
        self.counts[int(value)] += times

    def __iter__(self):
        """Every observation, one at a time, smallest first.

        This is what keeps ``sum(stats["lands"])`` meaning the total number of
        lands seen. It costs one step per game, but it is only walked by the
        old consumers - the mean and the percentiles read the counts.
        """
        for value in sorted(self.counts):
            yield from (value,) * self.counts[value]

    def __len__(self) -> int:
        """How many observations there were - i.e. how many games."""
        return sum(self.counts.values())

    def __bool__(self) -> bool:
        # Without this, __len__ would make an all-zeroes histogram falsy.
        return bool(self.counts)

    def __eq__(self, other) -> bool:
        if isinstance(other, Histogram):
            return self.counts == other.counts
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented

    def __add__(self, other: "Histogram") -> "Histogram":
        if not isinstance(other, Histogram):
            return NotImplemented
        merged = Histogram()
        merged.counts = self.counts + other.counts
        return merged

    def __repr__(self) -> str:
        return f"Histogram({dict(sorted(self.counts.items()))})"

    @property
    def total(self) -> int:
        """The sum of every observation."""
        return sum(value * times for value, times in self.counts.items())

    @property
    def mean(self) -> float:
        observations = len(self)
        return self.total / observations if observations else 0.0

    def percentile(self, share: float) -> int:
        """The nearest-rank percentile: ``percentile(50)`` is the median.

        Nearest rank rather than interpolation, because these are counts of
        whole cards and whole mana. "The median game had 4 mana" is a sentence
        about a game that was actually played; 4.5 mana is not.
        """
        observations = len(self)
        if not observations:
            return 0
        target = max(1, min(observations, -(-int(share * observations) // 100)))
        seen = 0
        for value in sorted(self.counts):
            seen += self.counts[value]
            if seen >= target:
                return value
        return max(self.counts)

    def to_json(self) -> dict[str, int]:
        """JSON keys are strings, so the values become strings."""
        return {str(value): times for value, times in sorted(self.counts.items())}


# --- Metric groups ---------------------------------------------------------
#
# The tags are on the cards. A different deck brings different cards and gets
# the same metrics without a line changing here.

SAC_OUTLET_TAG = "sac_outlet"
RECURSIVE_TAG = "recursive"
DRAIN_TAG = "drain_payoff"
DRAW_ENGINE_TAG = "draw_engine"


def _tagged(cards, tag: str) -> bool:
    """Is at least one card with this tag on the battlefield?"""
    return any(tag in card.tags for card in cards)


def _scaling_mana_sources(cards) -> int:
    """Mana sources that grow with the board.

    This is the general form of what used to be the name list
    ``{Cabal Coffers, Urborg, Crypt Ghast}``: anything that makes **no** fixed
    amount but depends on how many permanents are controlled. Two of them
    together are the deck's actual ramp engine.
    """
    return sum(
        1
        for card in cards
        if any(ability.rule != FLAT for ability in card.mana_abilities)
    )


def _has_fast_mana(cards) -> bool:
    """A mana source that made more than it cost.

    In the Chainer deck that is exactly Sol Ring - {1} for two mana. A signet,
    Mind Stone and Charcoal Diamond cost {2} for one and are therefore not
    tempo, only fixing.

    **Lands do not count.** They cost no mana, so on the arithmetic every swamp
    makes more than it cost - without this exception the metric would report
    almost every game as a fast-mana start on turn one.
    """
    for card in cards:
        if card.is_land:
            continue
        # Net of what the ability costs to use: a Signet's {1} leaves one
        # mana, which is fixing and not tempo.
        produced = sum(
            a.total - a.activation_generic for a in card.mana_abilities if a.rule == FLAT
        )
        if produced > card.mv:
            return True
    return False


def _skips_draw_step(cards) -> bool:
    """The Necropotence effect: the draw step traded for card advantage."""
    return any(card.skips_draw_step for card in cards)


# --- What was seen ----------------------------------------------------------
#
# Phase 9 E. Everything above measures the board; this measures the cards a
# player has drawn so far - the opening hand they kept and every draw, but not
# a tutored card (phase 10 T5.1) - counted by type, by category and by mana
# value. "Seen" is the word because a card that was drawn and cast is still one
# the player had.
#
# Nothing here decides anything or touches the random stream, which is why it
# runs on every game without changing a single number above it, and why the
# golden parity snapshot does not see it.

#: Mana values are counted one by one below this and together from it: "7+".
MV_CAP = 7

#: The categories a Commander deck is sorted into (Phase 9, "The category
#: vocabulary"), read off ``Card.categories``. One community role each: a
#: narrower role such as ``mana_rock`` always comes with its broader one in the
#: community tags, so nothing has to be folded together here.
SEEN_CATEGORIES = (
    "ramp", "draw", "removal", "wipe", "tutor", "counterspell", "protection",
    "recursion",
)

#: What else a deck can be built around (phase 11 K17): the strategies, read
#: off ``Card.categories`` like the categories above and counted the same way,
#: as ``role:`` groups. Each comes from one community tag or the owner's list.
SEEN_STRATEGIES = (
    "sac_outlet", "reanimate", "drain_payoff", "steal", "discard",
    "cost_reducer", "evasion",
)


def seen_groups(deck) -> tuple[tuple[str, ...], dict[str, tuple[int, ...]]]:
    """The groups a deck's cards are counted in, and which groups each card is in.

    Worked out once per run, so that the per-turn count only adds integers. The
    keys are ``type:creature``, ``role:ramp`` (one of `SEEN_CATEGORIES` or
    `SEEN_STRATEGIES`) and ``mv:3``, and only the ones this deck has cards in
    exist - a deck with no planeswalker has no ``type:planeswalker``, which is
    an absent line on the chart, not a zero.

    **Lands have no mana-value group.** Every land is mana value zero, and
    thirty-seven of them would bury the one question the curve answers: are
    the spells you draw ones you can cast.

    Returns:
        The group keys, and for each card name the positions it counts in.
    """
    keys: list[str] = []
    position: dict[str, int] = {}

    def slot(key: str) -> int:
        if key not in position:
            position[key] = len(keys)
            keys.append(key)
        return position[key]

    table: dict[str, tuple[int, ...]] = {}
    # Sorted, so that the same deck always lays its groups out the same way
    # whatever order the adapter read its cards in.
    for card in sorted(deck.library, key=lambda card: card.name):
        if card.name in table:
            continue
        slots = [slot(f"type:{kind}") for kind in sorted(card.types)]
        slots += [slot(f"role:{category}")
                  for category in SEEN_CATEGORIES + SEEN_STRATEGIES
                  if category in card.categories]
        if not card.is_land:
            slots.append(slot(f"mv:{min(card.mv, MV_CAP)}"))
        table[card.name] = tuple(slots)
    return tuple(keys), table


def _count_seen(game, groups) -> list[int]:
    """How many cards of each group the player has drawn by now.

    The kept opening hand and every draw since: `Game.drawn`. Until phase 10
    (T5.1) this walked hand, battlefield, graveyard and exile, which also
    counted every card a tutor found and every land a Cultivate fetched - and
    a "Tutor" line that rose because tutors found tutors. The cards a mulligan
    put on the bottom are out of the list, and the commander is in no group.
    """
    keys, table = groups
    counts = [0] * len(keys)
    for card in game.drawn:
        for slot in table.get(card.name, ()):
            counts[slot] += 1
    return counts


def simulate_game(rng: random.Random, on_the_play: bool = True,
                  turns: int = DEFAULT_TURNS, keep_log: bool = False,
                  deck=None, watch=(), groups=None):
    """Play one goldfish game and return what was measured.

    Args:
        watch: :class:`simulation.combos.Watch` objects to look for while the
            game is played. Empty - the default - and nothing is watched and
            no ``combos`` key comes back, which is what keeps every existing
            caller, and the golden parity snapshot, untouched.
        groups: What :func:`seen_groups` returned for this deck. Given, and
            every turn's snapshot carries a ``seen`` count per group; left out,
            and it does not.

    Returns:
        dict: the game's metrics, including the state after each turn.
    """
    game = Game(rng, on_the_play=on_the_play, deck=deck)
    game.take_opening_hand()

    commander = game.deck.commander
    commander_name = commander.name if commander else ""

    opening_lands = sum(1 for card in game.hand if card.is_land)
    opening_hand = [card.name for card in game.hand]

    watcher = combos.Watcher(watch) if watch else None

    per_turn = []
    for number in range(1, turns + 1):
        agent.take_turn(game)
        if watcher is not None:
            # At the end of the turn, deliberately. A combo that exists only
            # halfway through a main phase - one piece cast, the next not yet
            # payable - is not one the player can use, and the honest snapshot
            # is the board they actually pass the turn with.
            watcher.look(game, number)
        names = game.permanent_names
        board = list(game.battlefield)
        snapshot = {
            "lands": len(game.lands),
            "mana": game.mana_available,
            "black": game.black_available,
            # One entry per colour, zeroes included: a turn with no white mana
            # is an observation about the deck, not a missing measurement, and
            # a histogram that skipped it would report a mean over the games
            # that happened to go well.
            **{
                field: game.mana_by_color.get(color, 0)
                for color, field in COLOR_FIELD.items()
            },
            "permanents": names,
            "permanent_list": [card.name for card in game.battlefield],
            "commander_out": bool(commander_name) and game.has(commander_name),
            "life": game.life,
            "hand_size": len(game.hand),
            # Carried along from Phase 2 on, so that the analysis never has to
            # fall back on card names: the metrics read the cards themselves.
            "battlefield": board,
        }
        if groups is not None:
            snapshot["seen"] = _count_seen(game, groups)
        per_turn.append(snapshot)

    measured = {
        "mulligans": game.mulligans,
        "opening_lands": opening_lands,
        "first_seven": game.first_hand_lands,
        "opening_hand": opening_hand,
        "hand_size": len(opening_hand),
        "per_turn": per_turn,
        "log": list(game.log) if keep_log else None,
        "final_life": game.life,
    }
    if watcher is not None:
        measured["combos"] = watcher.first
        measured["win"] = watcher.first_win
    return measured


def run(iterations: int = DEFAULT_ITERATIONS, on_the_play: bool = True,
        turns: int = DEFAULT_TURNS, seed: int = 20260917, deck=None, watch=()):
    """Run the Monte Carlo simulation and aggregate the results.

    Args:
        watch: combos to measure while the games are played. They cost one
            look per turn per game and add a ``combos`` key to the result;
            without them the result is byte-for-byte what it has always been.
    """
    # Walked twice - once to lay out the counters, once per game - so a
    # generator would silently measure nothing after the first line.
    watch = tuple(watch)
    rng = random.Random(seed)

    if deck is None:
        # The same default `Game` falls back on, resolved here as well because
        # the groups have to be laid out before the first game is dealt.
        from simulation.fixtures import chainer
        deck = chainer.DECK
    groups = seen_groups(deck)
    seen_cards = [[0] * len(groups[0]) for _ in range(turns)]
    seen_games = [[0] * len(groups[0]) for _ in range(turns)]
    seen_squares = [[0] * len(groups[0]) for _ in range(turns)]

    counters = {field: Counter() for field in COUNTER_FIELDS}
    turn_stats = [{
        **{field: Histogram() for field in HISTOGRAM_FIELDS},
        "commander": 0, "necropotence": 0, "draw_engine": 0,
        "sac_outlet": 0, "recursive": 0, "engine_online": 0,
        "drain": 0, "ramp_engine": 0, "sol_ring": 0,
    } for _ in range(turns)]

    assembled = {watched.key: [0] * turns for watched in watch}
    # P8: by each turn, the games in which any game-ending combo was together.
    # Counted whenever combos are watched, zeroes included: "none of these
    # combos ends the game" is an answer, and a missing key would read like a
    # run from before the count existed.
    won = [0] * turns if watch else None

    for _ in range(iterations):
        result = simulate_game(rng, on_the_play=on_the_play, turns=turns,
                               deck=deck, watch=watch, groups=groups)
        for key, first in (result.get("combos") or {}).items():
            # Cumulative on the way in: the page asks "by turn six", not "on
            # turn six", and a cumulative count merges by addition exactly as
            # a per-turn one does. A zero means it never came together in the
            # turns played, which is the usual answer and not a missing one.
            if not first:
                continue
            for index in range(first - 1, turns):
                assembled[key][index] += 1
        if won is not None and result.get("win"):
            for index in range(result["win"] - 1, turns):
                won[index] += 1
        for field in COUNTER_FIELDS:
            counters[field][result[field]] += 1

        for index, snapshot in enumerate(result["per_turn"]):
            stats = turn_stats[index]
            board = snapshot["battlefield"]
            for field in HISTOGRAM_FIELDS:
                stats[field].add(snapshot[field])
            stats["commander"] += snapshot["commander_out"]
            stats["necropotence"] += _skips_draw_step(board)
            stats["sol_ring"] += _has_fast_mana(board)
            stats["draw_engine"] += _tagged(board, DRAW_ENGINE_TAG)
            has_sac = _tagged(board, SAC_OUTLET_TAG)
            has_rec = _tagged(board, RECURSIVE_TAG)
            stats["sac_outlet"] += has_sac
            stats["recursive"] += has_rec
            stats["engine_online"] += has_sac and has_rec
            stats["drain"] += _tagged(board, DRAIN_TAG)
            stats["ramp_engine"] += _scaling_mana_sources(board) >= 2
            cards, games = seen_cards[index], seen_games[index]
            squares = seen_squares[index]
            for slot, count in enumerate(snapshot["seen"]):
                if count:
                    cards[slot] += count
                    games[slot] += 1
                    squares[slot] += count * count

    summary = {
        "iterations": iterations,
        "on_the_play": on_the_play,
        "turns": turns,
        **counters,
        "turn_stats": turn_stats,
        # Per group and turn: cards seen, summed over the games (a mean, once
        # divided by `iterations`), games that had seen at least one (a share),
        # and the sum of the squared counts (with the mean, the spread - phase
        # 10). All three are counts, so chunks merge by addition.
        "seen": {
            key: {
                "cards": [seen_cards[turn][slot] for turn in range(turns)],
                "games": [seen_games[turn][slot] for turn in range(turns)],
                "squares": [seen_squares[turn][slot] for turn in range(turns)],
            }
            for slot, key in enumerate(groups[0])
        },
    }
    if watch:
        # `games` travels with every combo rather than being read off
        # `iterations`, because a combo measured on a hypothetical deck is
        # measured over its own, smaller, sample - and a percentage whose
        # denominator is guessed at is a percentage that is wrong.
        summary["combos"] = {
            key: {"games": iterations, "by_turn": counts}
            for key, counts in assembled.items()
        }
    if won is not None:
        summary["wins"] = {"games": iterations, "by_turn": won}
    return summary


# --- Running a simulation in pieces ----------------------------------------
#
# A 100,000 game run is split across workers. Three things have to hold for
# that to be worth anything: the pieces have to be reproducible, they have to
# add up to the same thing regardless of the order they come back in, and they
# have to fit through a message broker.


def chunk_seed(run_seed: int, index: int) -> int:
    """The seed for chunk ``index`` of the run seeded with ``run_seed``.

    **A chunked run is not bit-identical to a single run with the same seed,
    and deliberately so.** Making it so would mean seeding each game
    separately, which changes the random stream of every existing run and with
    it every number in the golden parity snapshot. What is guaranteed instead:

    * the same ``(run_seed, index, iterations)`` always replays exactly, so a
      stored result can be reproduced;
    * a chunked run and a single run of the same size are the same experiment
      and agree to within sampling error, which is what
      ``tests/test_analysis_chunks.py`` asserts.

    Derived with blake2b rather than ``hash()``: Python randomises string
    hashing per process, so ``hash()`` would give a different chunk seed on
    every worker start and quietly destroy reproducibility.
    """
    digest = hashlib.blake2b(f"{int(run_seed)}:{int(index)}".encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big")


def sample_seed(run_seed: int, index: int, key: str) -> int:
    """The seed for one combo's own sample within chunk ``index``.

    A hypothetical deck - the real one with a missing card added - is a
    different experiment and needs a stream of its own. Derived from the key as
    well as the index so that two combos measured in the same chunk do not play
    the same hundred games as each other, which would make their percentages
    agree for a reason that has nothing to do with the decks.
    """
    digest = hashlib.blake2b(
        f"{int(run_seed)}:{int(index)}:{key}".encode(), digest_size=8
    )
    return int.from_bytes(digest.digest(), "big")


def run_chunk(iterations: int, run_seed: int, index: int, *,
              on_the_play: bool = True, turns: int = DEFAULT_TURNS,
              deck=None, watch=()) -> dict:
    """One chunk of a larger run, as something a broker can carry.

    The unit of work a Celery task performs. Returns the JSON form, because
    that is what has to travel back - a few kB of counters, whatever the
    iteration count.
    """
    result = run(
        iterations,
        on_the_play=on_the_play,
        turns=turns,
        seed=chunk_seed(run_seed, index),
        deck=deck,
        watch=watch,
    )
    return as_json(result)


def as_json(result: dict) -> dict:
    """A run result as plain JSON-serialisable data."""
    payload = {
        "iterations": result["iterations"],
        "on_the_play": result["on_the_play"],
        "turns": result["turns"],
        **{
            field: {str(k): v for k, v in sorted(result[field].items())}
            for field in COUNTER_FIELDS
        },
        "turn_stats": [
            {
                key: (value.to_json() if key in HISTOGRAM_FIELDS else value)
                for key, value in stats.items()
            }
            for stats in result["turn_stats"]
        ],
    }
    # Absent from a result stored before Phase 9 E, and read back as absent:
    # the page says "run again" for those rather than drawing a flat line.
    if "seen" in result:
        payload["seen"] = _copy_seen(result["seen"])
    # Absent rather than empty when nothing was watched. A result carrying an
    # empty `combos` would be indistinguishable from a run that watched a combo
    # and never saw it, and those are different answers.
    if result.get("combos"):
        payload["combos"] = {
            key: {"games": int(entry["games"]), "by_turn": list(entry["by_turn"])}
            for key, entry in result["combos"].items()
        }
    if "wins" in result:
        payload["wins"] = _copy_wins(result["wins"])
    return payload


def from_json(data: dict) -> dict:
    """The inverse of :func:`as_json`, for reading a stored result."""
    result = {
        "iterations": data["iterations"],
        "on_the_play": data["on_the_play"],
        "turns": data["turns"],
        **{
            # `.get` rather than `[...]`: a result stored before a counter
            # existed still has to be readable, or every old run on the site
            # would start raising the day a new distribution is added.
            field: Counter({int(k): v for k, v in (data.get(field) or {}).items()})
            for field in COUNTER_FIELDS
        },
        "turn_stats": [
            {
                key: (Histogram(value) if key in HISTOGRAM_FIELDS else value)
                for key, value in stats.items()
            }
            for stats in data["turn_stats"]
        ],
    }
    if "seen" in data:
        result["seen"] = _copy_seen(data["seen"])
    if data.get("combos"):
        result["combos"] = {
            key: {"games": int(entry["games"]), "by_turn": list(entry["by_turn"])}
            for key, entry in data["combos"].items()
        }
    if "wins" in data:
        result["wins"] = _copy_wins(data["wins"])
    return result


def merge(chunks) -> dict:
    """Add several chunk results together, in JSON form.

    Addition, so the result does not depend on the order the chunks finished
    in - which matters, because they finish in whatever order the workers
    happen to be free. ``tests/test_analysis_chunks.py`` asserts that directly
    by merging the same chunks shuffled.

    Raises:
        ValueError: With no chunks, or when two of them simulated different
            scenarios. Averaging a six-turn run into a three-turn one would
            produce a number that looks fine and means nothing.
    """
    chunks = list(chunks)
    if not chunks:
        raise ValueError("nothing to merge")

    first = chunks[0]
    turns, on_the_play = first["turns"], first["on_the_play"]
    for chunk in chunks[1:]:
        if (chunk["turns"], chunk["on_the_play"]) != (turns, on_the_play):
            raise ValueError(
                "chunks from different scenarios cannot be merged: "
                f"{turns} turns/on_the_play={on_the_play} vs "
                f"{chunk['turns']} turns/on_the_play={chunk['on_the_play']}"
            )

    merged = from_json(first)
    for chunk in chunks[1:]:
        other = from_json(chunk)
        merged["iterations"] += other["iterations"]
        _merge_combos(merged, other)
        _merge_wins(merged, other)
        _merge_seen(merged, other)
        for field in COUNTER_FIELDS:
            merged[field].update(other[field])
        for stats, extra in zip(merged["turn_stats"], other["turn_stats"], strict=True):
            for key, value in extra.items():
                if key in HISTOGRAM_FIELDS:
                    stats[key] = stats[key] + value
                else:
                    stats[key] += value
    return as_json(merged)


#: The counters of one ``seen`` group. ``squares`` arrived in phase 10 and is
#: absent from older results, which then show no spread.
SEEN_FIELDS = ("cards", "games", "squares")


def _copy_seen(seen: dict) -> dict:
    """The ``seen`` block, as fresh lists of plain integers."""
    return {
        key: {field: [int(n) for n in entry[field]]
              for field in SEEN_FIELDS if field in entry}
        for key, entry in seen.items()
    }


def _merge_seen(merged: dict, other: dict) -> None:
    """Add one chunk's draw counts into another's, in place.

    A group present in only one chunk is kept, like a combo: a card edited
    while the run was in flight can give one chunk a group the others lack, and
    for their games zero is the true count. A chunk from before Phase 9 E has
    no block at all, and then the merged run has none either - half a
    measurement divided by the whole run's games would be a wrong number.
    """
    if "seen" not in merged or "seen" not in other:
        merged.pop("seen", None)
        return
    seen = merged["seen"]
    for key, entry in other["seen"].items():
        held = seen.get(key)
        if held is None:
            seen[key] = {field: list(entry[field]) for field in SEEN_FIELDS if field in entry}
            continue
        for field in SEEN_FIELDS:
            if field in held and field in entry:
                held[field] = [a + b for a, b in zip(held[field], entry[field], strict=True)]
            else:
                # A chunk from before the spread was counted: the sum over
                # part of the games would be a wrong spread, so there is none.
                held.pop(field, None)


def _merge_combos(merged: dict, other: dict) -> None:
    """Add one chunk's combo counts into another's, in place.

    Keyed by combo, and a key present in only one chunk is kept rather than
    dropped: the chunks of a run are planned separately, so a lookup refreshed
    while a run was in flight can leave one chunk watching a combo the others
    did not. Both answers are true about the games that were actually played,
    and ``games`` says how many those were.
    """
    if not other.get("combos"):
        return
    combos_seen = merged.setdefault("combos", {})
    for key, entry in other["combos"].items():
        held = combos_seen.get(key)
        if held is None:
            combos_seen[key] = {"games": entry["games"], "by_turn": list(entry["by_turn"])}
            continue
        held["games"] += entry["games"]
        held["by_turn"] = [
            a + b for a, b in zip(held["by_turn"], entry["by_turn"], strict=True)
        ]


def _copy_wins(wins: dict) -> dict:
    """The ``wins`` block (P8), as fresh plain integers."""
    return {"games": int(wins["games"]), "by_turn": [int(n) for n in wins["by_turn"]]}


def _merge_wins(merged: dict, other: dict) -> None:
    """Add one chunk's game-ending-combo counts into another's, in place.

    Like a combo, and for the same reason: ``games`` travels with the counts,
    so a chunk that watched no game-ending combo - the lookup moved while the
    run was in flight - adds nothing rather than a denominator without
    numerators.
    """
    if "wins" not in other:
        return
    held = merged.get("wins")
    if held is None:
        merged["wins"] = _copy_wins(other["wins"])
        return
    held["games"] += other["wins"]["games"]
    held["by_turn"] = [
        a + b for a, b in zip(held["by_turn"], other["wins"]["by_turn"], strict=True)
    ]


def pct(count: int, total: int) -> float:
    """A percentage."""
    return 100.0 * count / total if total else 0.0


def mean(values) -> float:
    """The arithmetic mean."""
    if isinstance(values, Histogram):
        # Straight off the counts, rather than walking one entry per game.
        return values.mean
    return sum(values) / len(values) if values else 0.0


def keep_rate_no_mulligan(iterations: int = 200_000,
                          seed: int = 20260917, deck=None) -> float:
    """The share of *first* sevens the heuristic keeps.

    Deliberately without the mulligan loop, so that the value can be compared
    directly against a hypergeometric calculation.
    """
    rng = random.Random(seed)
    dummy = Game(rng, deck=deck)
    library = list(dummy.deck.library)
    keeps = 0
    for _ in range(iterations):
        hand = rng.sample(library, 7)
        keeps += dummy.keepable(hand)
    return pct(keeps, iterations)
