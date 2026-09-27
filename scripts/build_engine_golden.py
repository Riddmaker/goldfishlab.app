"""Generate the golden aggregate snapshot from the ORIGINAL engine.

    py -3.13 scripts/build_engine_golden.py [path/to/magic-project]

The Phase 2 generalization rewrote almost every line of `simulation/`. The 54
unit tests say the parts still work; only this says the *whole* still produces
the same numbers. It imports the untouched original from the sibling
magic-project repository, runs it at a fixed seed, and writes the aggregates to
`tests/fixtures/engine_golden.json`.

`tests/test_engine_parity.py` then checks the current engine against that file,
offline and forever - long after the sibling repository has moved or gone.

**Regenerate this only when a behaviour change is intended**, and say so in the
commit. Regenerating it to make a red test go green destroys the only evidence
that the engine still does what it used to.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "engine_golden.json"
DEFAULT_SOURCE = ROOT.parent / "magic-project"

#: Small enough to stay in the fast test loop, large enough that a real
#: behaviour change moves at least one counter.
SCENARIOS = [
    {"iterations": 1500, "turns": 3, "seed": 20260917, "on_the_play": True},
    {"iterations": 1500, "turns": 3, "seed": 424242, "on_the_play": False},
    {"iterations": 600, "turns": 6, "seed": 99, "on_the_play": True},
]


def load_original(source: Path):
    """Import the untouched engine under the package name `oldsim`."""
    package = source / "simulation"
    if not package.is_dir():
        raise SystemExit(f"no simulation package at {package}")

    staging = Path(tempfile.mkdtemp()) / "oldsim"
    shutil.copytree(package, staging)
    for file in staging.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        text = text.replace("simulation.", "oldsim.")
        text = text.replace("from simulation import", "from oldsim import")
        file.write_text(text, encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "oldsim", staging / "__init__.py", submodule_search_locations=[str(staging)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["oldsim"] = module
    spec.loader.exec_module(module)

    import oldsim.analysis as analysis

    return analysis


def summarise(result: dict) -> dict:
    """Reduce a run() result to something stable, comparable and small.

    Per-game lists become (mean, count): storing 1,500 raw values per turn
    would make the fixture unreadable without making it any stricter.
    """
    summary = {
        "iterations": result["iterations"],
        "turns": result["turns"],
        "on_the_play": result["on_the_play"],
        "mulligans": {str(k): v for k, v in sorted(result["mulligans"].items())},
        "opening_lands": {str(k): v for k, v in sorted(result["opening_lands"].items())},
        "turn_stats": [],
    }
    for stats in result["turn_stats"]:
        entry = {}
        for key, value in sorted(stats.items()):
            if isinstance(value, list):
                entry[key] = {
                    "mean": round(sum(value) / len(value), 9),
                    "count": len(value),
                    "total": sum(value),
                }
            else:
                entry[key] = value
        summary["turn_stats"].append(entry)
    return summary


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    analysis = load_original(source)

    print(f"reading the original engine from {source}")
    snapshots = []
    for scenario in SCENARIOS:
        print(f"  {scenario['iterations']:>5} games, {scenario['turns']} turns, "
              f"seed {scenario['seed']}, on_the_play={scenario['on_the_play']}")
        result = analysis.run(
            scenario["iterations"],
            on_the_play=scenario["on_the_play"],
            turns=scenario["turns"],
            seed=scenario["seed"],
        )
        snapshots.append({"scenario": scenario, "summary": summarise(result)})

    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(snapshots, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {GOLDEN.relative_to(ROOT)} ({GOLDEN.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
