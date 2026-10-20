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
from simulation.cards import CARD_TYPES, ENGINE_ROLES
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

#: Games are seeded per block of this many; a chunk plays whole blocks.
SEED_BLOCK = analysis.SEED_BLOCK
blocks_of = analysis.blocks_of

#: The four roles the engine plays a card in, re-exported for the same reason:
#: the migrations that turn lists of them into categories read them here.
ENGINE_ROLE_TAGS = ENGINE_ROLES

#: The land types that make a land tap for a colour, and which colour each
#: one makes. Re-exported because the annotation editor offers them: a land
#: type is not decoration in this engine, it is what a basic land taps for and
#: what Cabal Coffers counts.
LAND_SUBTYPE_COLORS = dict(SUBTYPE_COLORS)

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

    `blocks` holds its games per seed block of the chunk, ``(block, games)``;
    they are always fewer than the run's: the hypothetical is a side
    question, and the answer is reported to whole percent.
    """

    key: str
    deck: object
    watch: object
    blocks: tuple[tuple[int, int], ...]

    @property
    def games(self) -> int:
        """The games this chunk plays for it."""
        return sum(games for _block, games in self.blocks)


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

    Every chunk but the last plays whole seed blocks (`analysis.SEED_BLOCK`),
    so chunk ``i`` starts at block ``sum(plan[:i]) // SEED_BLOCK`` and the
    games are the same however many chunks the speed asks for. The last
    chunk absorbs the remainder, so the sizes add up to exactly
    `games_total` - a run that simulates 99,750 of the 100,000 games it
    promised would be a quiet lie in every number it reports.

    Raises:
        ValueError: When asked for no games at all.
    """
    if games_total < 1:
        raise ValueError("a run needs at least one game")

    blocks = -(-games_total // SEED_BLOCK)
    size = -(-chunk_size_for(turns, usec_per_game) // SEED_BLOCK)
    count = max(1, min(MAX_CHUNKS, -(-blocks // size)))
    size = -(-blocks // count)
    plan = [min(size, blocks - start) * SEED_BLOCK for start in range(0, blocks, size)]
    plan[-1] -= sum(plan) - games_total
    return plan


def first_blocks(plan: list[int]) -> list[int]:
    """The seed block each chunk of `chunk_plan` starts at."""
    starts, games = [], 0
    for size in plan:
        starts.append(games // SEED_BLOCK)
        games += size
    return starts


def run_chunk(games: int, run_seed: int, first_block: int, *, turns: int,
              on_the_play: bool, deck, watch=(), samples=()) -> dict:
    """Play one chunk - ``games`` from seed block ``first_block`` on - and
    return what a broker can carry back.

    Args:
        watch: combos measured in the chunk's own games. They ride along for
            nothing - the games are played either way - which is why every
            combo the deck already contains is watched and only the
            hypotheticals are rationed.
        samples: hypothetical decks, each with games of its own in each of
            the chunk's blocks. These are the extra cost, and
            `combos/measure.py` is where it is bounded.
    """
    payload = analysis.run_chunk(
        games,
        run_seed=run_seed,
        first_block=first_block,
        on_the_play=on_the_play,
        turns=turns,
        deck=deck,
        watch=watch,
    )

    for sample in samples:
        # A run of its own per block rather than a chunk: the seed has to
        # depend on which combo this is, or two hypotheticals measured in the
        # same block would play the same games and agree for the wrong reason.
        played = [analysis.as_json(analysis.run(
            games, on_the_play=on_the_play, turns=turns,
            seed=analysis.sample_seed(run_seed, block, sample.key),
            deck=sample.deck, watch=(sample.watch,),
        )) for block, games in sample.blocks if games > 0]
        if played:
            result = analysis.merge(played)
            payload.setdefault("combos", {}).update(result.get("combos") or {})

    return payload


def merge(chunks) -> dict:
    """Add the chunks of one run together, in any order."""
    return analysis.merge(chunks)


def read(result: dict) -> dict:
    """A stored result, back in the form with histograms in it."""
    return analysis.from_json(result)
