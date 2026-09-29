"""Running a simulation in pieces, sized to fit a worker.

Where `adapter.py` translates a database deck into something the engine can
play, this module decides **how the playing is split up**. It imports the
engine and no Django at all, which is deliberate: the Celery tasks in
`simulations/tasks.py` are Django modules and call into here, so that
`adapter.py` remains the only module in the codebase importing both worlds.

The split exists for one reason. A simulation is embarrassingly parallel - each
game is independent - but a single task holding 100,000 games would hit the
soft time limit, hold a worker for minutes at a time, and lose everything if it
died at 99%. Chunks give progress, cancellation and restart for free.
"""

from dataclasses import dataclass

from simulation import analysis, combos
from simulation.cards import CARD_TYPES
from simulation.manacost import SUBTYPE_COLORS

#: The mana sources a run reports, WUBRG then colourless, and which stored
#: field holds each one. Re-exported here so that `simulations/report.py` can
#: build the per-colour table without importing the engine itself - the
#: boundary is that `adapter.py` and this module are the only way across.
MANA_SOURCES = analysis.MANA_SOURCES
COLOR_FIELD = analysis.COLOR_FIELD

#: The card types and the top mana-value bucket the draw statistics count in
#: (Phase 9 E). Re-exported for `simulations/report.py`, which names them on
#: the page and must not import the engine to do it.
SEEN_CARD_TYPES = CARD_TYPES
SEEN_MV_CAP = analysis.MV_CAP

#: The land types that make a land tap for a colour, and which colour each
#: one makes. Re-exported because the annotation editor offers them: a land
#: type is not decoration in this engine, it is what a basic land taps for and
#: what Cabal Coffers counts.
LAND_SUBTYPE_COLORS = dict(SUBTYPE_COLORS)

#: The role tags the analysis reads off a card to decide whether a milestone
#: fired. Re-exported for the same reason: the annotation editor offers them as
#: roles a user may set, and a typo there would silently zero a report row
#: rather than raise anything.
ENGINE_ROLE_TAGS = (
    analysis.DRAW_ENGINE_TAG,
    analysis.SAC_OUTLET_TAG,
    analysis.RECURSIVE_TAG,
    analysis.DRAIN_TAG,
)

#: What a combo measurement is made of, re-exported for the same reason as
#: `MANA_SOURCES` below it: `combos/measure.py` builds these out of database
#: rows and must not import the engine to do it. `Unmeasurable` is the one that
#: matters most - it is how a combo this engine cannot honestly watch for ends
#: up with no number rather than a zero.
Watch = combos.Watch
Requirement = combos.Requirement
Unmeasurable = combos.Unmeasurable

#: Spellbook's zone letters that this engine can answer for. A requirement
#: naming anything else is unmeasurable; see `simulation/combos.py`.
COMBO_ZONES = combos.KNOWN_ZONES
DEFAULT_COMBO_ZONES = combos.DEFAULT_ZONES


@dataclass(frozen=True)
class Sample:
    """One hypothetical deck, and the single combo it exists to measure.

    A combo the deck is *one card away from* cannot be measured in the deck's
    own games, because the card that completes it is not in the library to be
    drawn. So it gets a deck of its own - the real one with that card added -
    and a sample of its own, played alongside the run.

    `games` is per chunk, and it is always smaller than the run: the hypothetical
    is a side question, and the answer is reported to whole percent.
    """

    key: str
    deck: object
    watch: object
    games: int


#: How long a chunk should take. Short enough that a cancellation is felt
#: quickly (the documented worst case is one chunk) and that a lost worker
#: costs little; long enough that the per-task overhead stays noise.
TARGET_CHUNK_SECONDS = 15

#: Measured on the dev machine, 2026-09-18: ~110 usec per game per turn
#: (318 usec at 3 turns, 667 at 6, 1,104 at 10 - linear in turns, as the turn
#: loop implies). Re-measure rather than adjust by feel if this looks wrong.
USEC_PER_GAME_PER_TURN = 110

#: Production runs on a cloudlet with a considerably slower core than the dev
#: machine. Sizing chunks for the fast machine would make every chunk four
#: times too long in production, which is where the time limit lives.
SLOW_CPU_FACTOR = 4

#: A chunk below the minimum spends more time on task overhead than on games;
#: above the maximum it approaches the soft time limit on a slow core. The cap
#: on the number of chunks keeps a large run from flooding the queue with
#: thousands of tiny tasks that starve every other user.
MIN_CHUNK = 250
MAX_CHUNK = 5_000
MAX_CHUNKS = 200


def default_usec_per_game(turns: int) -> float:
    """The expected cost of one game, before anything has been measured."""
    return USEC_PER_GAME_PER_TURN * max(1, turns) * SLOW_CPU_FACTOR


def chunk_size_for(turns: int, usec_per_game: float | None = None) -> int:
    """How many games one chunk should hold.

    Args:
        turns: How many turns each game plays. Cost is linear in this.
        usec_per_game: A measured rate, if one is known - the application
            passes the rate observed on this deck's last run, which is worth
            more than any estimate made here.
    """
    rate = usec_per_game or default_usec_per_game(turns)
    games = int(TARGET_CHUNK_SECONDS * 1_000_000 / max(1.0, rate))
    return max(MIN_CHUNK, min(MAX_CHUNK, games))


def chunk_plan(games_total: int, turns: int,
               usec_per_game: float | None = None) -> list[int]:
    """How many games each chunk plays, in order.

    The last chunk absorbs the remainder, so the sizes add up to exactly
    `games_total` - a run that simulates 99,750 of the 100,000 games it
    promised would be a quiet lie in every number it reports.

    Raises:
        ValueError: When asked for no games at all.
    """
    if games_total < 1:
        raise ValueError("a run needs at least one game")

    size = chunk_size_for(turns, usec_per_game)
    count = max(1, min(MAX_CHUNKS, -(-games_total // size)))
    size = -(-games_total // count)

    plan = [size] * count
    plan[-1] -= sum(plan) - games_total
    return [games for games in plan if games > 0]


def run_chunk(games: int, run_seed: int, index: int, *, turns: int,
              on_the_play: bool, deck, watch=(), samples=()) -> dict:
    """Play one chunk and return what a broker can carry back.

    Args:
        watch: combos measured in the chunk's own games. They ride along for
            nothing - the games are played either way - which is why every
            combo the deck already contains is watched and only the
            hypotheticals are rationed.
        samples: hypothetical decks, each with games of its own. These are the
            extra cost, and `combos/measure.py` is where it is bounded.
    """
    payload = analysis.run_chunk(
        games,
        run_seed=run_seed,
        index=index,
        on_the_play=on_the_play,
        turns=turns,
        deck=deck,
        watch=watch,
    )

    for sample in samples:
        if sample.games < 1:
            continue
        # A run of its own rather than a chunk of one: the seed has to depend
        # on which combo this is, or two hypotheticals measured in the same
        # chunk would play the same games and agree for the wrong reason.
        result = analysis.as_json(analysis.run(
            sample.games,
            on_the_play=on_the_play,
            turns=turns,
            seed=analysis.sample_seed(run_seed, index, sample.key),
            deck=sample.deck,
            watch=(sample.watch,),
        ))
        payload.setdefault("combos", {}).update(result.get("combos") or {})

    return payload


def merge(chunks) -> dict:
    """Add the chunks of one run together, in any order."""
    return analysis.merge(chunks)


def read(result: dict) -> dict:
    """A stored result, back in the form with histograms in it."""
    return analysis.from_json(result)
