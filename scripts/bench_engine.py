"""Time the engine's hot loop, to check a change did not slow it down.

    ./.venv/Scripts/python.exe scripts/bench_engine.py [--games 5000] [--turns 6]
    .venv/bin/python scripts/bench_engine.py --baseline ../main-worktree [--repeats 5]

Plays the reference deck at a fixed seed and prints the best of several
repeats, in microseconds per game. Best rather than mean: on a laptop the
slower repeats measure whatever else the machine was doing, and the fastest one
is the closest thing to the engine's own cost. Run it before and after a change
to `simulation/` and compare - Phase 9 E set the bar at about 5%.

No Django, like the engine itself.

`--baseline <worktree>` compares two engines instead (card model, phase 4):
`BENCH` - `analysis.run` on the reference deck and the four number decks of
the development database, 2,000 games of eight turns each - runs alternately
in a worktree of the last merged main and here, `--repeats` times each, and
the medians and their ratio are printed. PR 12 to 16 need a ratio of at least
0.90. `BENCH` uses only what both engines have, so any main can be the
baseline. The worktree reads this checkout's `.env` through a link the script
makes when it has none.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

#: The ratio of speeds a phase 4 PR must keep: baseline time over ours.
GATE = 0.90

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation import analysis  # noqa: E402  (after the path fix above)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=5_000)
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--baseline", type=Path,
                        help="a worktree of the last merged main to compare against")
    args = parser.parse_args()

    if args.baseline is not None:
        compare(args.baseline.resolve(), args.repeats)
        return

    timings = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        analysis.run(args.games, turns=args.turns, seed=20260917)
        timings.append(time.perf_counter() - started)

    best = min(timings)
    print(f"{args.games} games x {args.turns} turns, best of {args.repeats}: "
          f"{best:.3f}s = {best / args.games * 1e6:.1f} usec/game")


#: Run by `manage.py shell` in each checkout: the seconds the five decks take.
BENCH = """
import json, time
from decks.models import Deck
from simulation import analysis
from simulation.fixtures import chainer
from simulations.engine import adapter
decks = [chainer.DECK]
for name in ("Temur Roar", "Abzan Armor", "Mardu Surge", "Jeskai Striker"):
    deck = Deck.objects.filter(name=name, owner__is_system=True).first()
    if deck is not None:
        decks.append(adapter.convert(deck).definition)
started = time.perf_counter()
for deck in decks:
    analysis.run(2000, on_the_play=False, turns=8, seed=1, deck=deck)
print(json.dumps({"decks": len(decks), "total": time.perf_counter() - started}))
"""


def _bench(checkout: Path) -> float:
    found = subprocess.run([sys.executable, "manage.py", "shell", "-c", BENCH],  # noqa: S603
                           cwd=checkout, capture_output=True, text=True, check=True)
    return json.loads(found.stdout.strip().splitlines()[-1])["total"]


def compare(baseline: Path, repeats: int) -> None:
    """Both engines in turn; their medians, and the ratio the gate reads."""
    env = baseline / ".env"
    if not env.exists() and (HERE / ".env").exists():
        env.symlink_to(HERE / ".env")
    before, after = [], []
    for _ in range(repeats):
        before.append(_bench(baseline))
        after.append(_bench(HERE))
    old, new = statistics.median(before), statistics.median(after)
    ratio = old / new
    print(f"baseline {old:.2f}s, here {new:.2f}s (medians of {repeats}): "
          f"speed ratio {ratio:.3f} ({'ok' if ratio >= GATE else 'below'} {GATE})")


if __name__ == "__main__":
    main()
