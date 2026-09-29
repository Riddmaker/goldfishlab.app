"""Time the engine's hot loop, to check a change did not slow it down.

    ./.venv/Scripts/python.exe scripts/bench_engine.py [--games 5000] [--turns 6]

Plays the reference deck at a fixed seed and prints the best of several
repeats, in microseconds per game. Best rather than mean: on a laptop the
slower repeats measure whatever else the machine was doing, and the fastest one
is the closest thing to the engine's own cost. Run it before and after a change
to `simulation/` and compare - Phase 9 E set the bar at about 5%.

No Django, like the engine itself.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation import analysis  # noqa: E402  (after the path fix above)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=5_000)
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    timings = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        analysis.run(args.games, turns=args.turns, seed=20260917)
        timings.append(time.perf_counter() - started)

    best = min(timings)
    print(f"{args.games} games x {args.turns} turns, best of {args.repeats}: "
          f"{best:.3f}s = {best / args.games * 1e6:.1f} usec/game")


if __name__ == "__main__":
    main()
