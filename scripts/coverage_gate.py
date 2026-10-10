"""The P19 coverage gate, one round at a time (issue #36).

    .venv/bin/python scripts/coverage_gate.py save v18          # before the change
    .venv/bin/python scripts/coverage_gate.py diff v18 [--default landers=0 ...] [--ignore name]
    .venv/bin/python scripts/coverage_gate.py check             # ruff + migrations
    .venv/bin/python scripts/coverage_gate.py suite [--out FILE]

Each round of the plan teaches the engine one class of cards and must not
read a single card worse than before. `save` keeps today's snapshot and
precon numbers under `.coverage-gate/<tag>/`; `diff` rewrites them
(`COVERAGE_WRITE=1 pytest tests/test_engine_coverage.py`), lists every card
whose reading changed, the precon numbers that moved, and fails when a card
read before is unread now. A field new in this round shows up on every card
at its default; `--default name=json` leaves such values out of the diff.

The steps run at below-normal priority and wait while the machine is short
of memory (`--min-free-mb`, default 700), so a game or an editor beside them
keeps running smoothly; the full suite takes minutes either way.
"""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SNAPSHOT = FIXTURES / "coverage_snapshot.json"
NUMBERS = FIXTURES / "coverage_numbers.json"
STORE = ROOT / ".coverage-gate"
PYTHON = Path(sys.executable)


def free_mb() -> int | None:
    """Free physical memory in MB, or None where it cannot be asked."""
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.avail // 2**20
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def wait_for_memory(minimum: int) -> None:
    """Hold the next step while less than ``minimum`` MB are free."""
    while (free := free_mb()) is not None and free < minimum:
        print(f"  {free} MB free, waiting for {minimum} ...", flush=True)
        time.sleep(30)


def run(command: list[str], *, env: dict | None = None, minimum: int = 0,
        out: Path | None = None) -> int:
    """Run a step at below-normal priority; its output to ``out`` or here."""
    wait_for_memory(minimum)
    print("$", " ".join(command), flush=True)
    options: dict = {"cwd": ROOT, "env": {**os.environ, **(env or {})}}
    if os.name == "nt":
        options["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
    else:
        options["preexec_fn"] = lambda: os.nice(10)
    if out is None:
        return subprocess.run(command, check=False, **options).returncode  # noqa: S603 - own argv
    with out.open("w", encoding="utf-8") as handle:
        return subprocess.run(command, check=False, stdout=handle,  # noqa: S603 - own argv
                              stderr=subprocess.STDOUT, **options).returncode


def save(tag: str) -> int:
    folder = STORE / tag
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(SNAPSHOT, folder / SNAPSHOT.name)
    shutil.copy(NUMBERS, folder / NUMBERS.name)
    print(f"saved to {folder}")
    return 0


def _strip(value, defaults: dict, ignored: frozenset = frozenset()):
    """The reading without the keys that only carry a new field's default,
    and without the ignored ones."""
    if isinstance(value, dict):
        return {key: _strip(item, defaults, ignored) for key, item in value.items()
                if key not in ignored and not (key in defaults and item == defaults[key])}
    if isinstance(value, list):
        return [_strip(item, defaults, ignored) for item in value]
    return value


def diff(tag: str, defaults: dict, minimum: int, ignored: frozenset = frozenset()) -> int:
    folder = STORE / tag
    if not (folder / SNAPSHOT.name).exists():
        print(f"nothing saved as {tag!r}: run `save {tag}` first")
        return 2
    code = run([str(PYTHON), "-m", "pytest", "-q", "tests/test_engine_coverage.py"],
               env={"COVERAGE_WRITE": "1"}, minimum=minimum)
    if code:
        return code
    old = json.loads((folder / SNAPSHOT.name).read_text(encoding="utf-8"))
    new = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    lost, gained = [], []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before is None or after is None:
            print(f"{name}: {'new' if before is None else 'gone'} in the fixed set")
            continue
        if not before["unread"] and after["unread"]:
            lost.append(name)
        elif before["unread"] and not after["unread"]:
            gained.append(name)
        was = _strip(before["read"], defaults, ignored)
        now = _strip(after["read"], defaults, ignored)
        keys = sorted(key for key in set(was) | set(now) if was.get(key) != now.get(key))
        if keys or before["unread"] != after["unread"]:
            changes = {key: (was.get(key), now.get(key)) for key in keys}
            print(f"- {name}: {changes} | unread {before['unread']} -> {after['unread']}")
    print(f"unread before {sum(1 for v in old.values() if v['unread'])}, "
          f"after {sum(1 for v in new.values() if v['unread'])}: "
          f"{len(gained)} gained, {len(lost)} lost")

    old_numbers = json.loads((folder / NUMBERS.name).read_text(encoding="utf-8"))
    new_numbers = json.loads(NUMBERS.read_text(encoding="utf-8"))
    for deck, numbers in old_numbers.items():
        moved = [f"t{turn} {key} {a[key]}->{b[key]}"
                 for turn, (a, b) in enumerate(zip(numbers["turns"],
                                                   new_numbers[deck]["turns"], strict=False), 1)
                 for key in a if a[key] != b.get(key)]
        if moved:
            print(f"NUMBERS {deck}: {len(moved)} moved, {moved[:8]}")
    if lost:
        print("LOST:", ", ".join(lost))
        return 1
    return 0


def check() -> int:
    codes = [
        run([str(PYTHON), "-m", "ruff", "check", "."]),
        run([str(PYTHON), "manage.py", "makemigrations", "--check", "--dry-run"]),
    ]
    return max(codes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-free-mb", type=int, default=700)
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("save").add_argument("tag")
    step = steps.add_parser("diff")
    step.add_argument("tag")
    step.add_argument("--default", action="append", default=[], metavar="NAME=JSON")
    # A new field that describes every card rather than reading one - the
    # printed subtypes of R17 - would list the whole set.
    step.add_argument("--ignore", action="append", default=[], metavar="NAME")
    steps.add_parser("check")
    steps.add_parser("suite").add_argument("--out", type=Path)
    args = parser.parse_args()
    # Card names carry a minus sign or an accent; a Windows console would not.
    sys.stdout.reconfigure(encoding="utf-8")

    if args.step == "save":
        return save(args.tag)
    if args.step == "diff":
        defaults = {}
        for pair in args.default:
            name, _, value = pair.partition("=")
            defaults[name] = json.loads(value)
        return diff(args.tag, defaults, args.min_free_mb, frozenset(args.ignore))
    if args.step == "check":
        return check()
    code = run([str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
               minimum=args.min_free_mb, out=args.out)
    if args.out:
        print("".join(args.out.read_text(encoding="utf-8").splitlines(True)[-3:]))
    return code


if __name__ == "__main__":
    sys.exit(main())
