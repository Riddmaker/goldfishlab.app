"""Set up the demo account and photograph every screen, including simulations.

Usage:
    docker compose up -d
    py -3.13 scripts/demo_screens.py [--out DIR]

Why this exists rather than a line in the README: three of the screens worth
reviewing are *states*, not URLs. A progress bar halfway through, a finished
report and a cancelled run cannot be reached by navigating anywhere - they have
to be arranged first. This arranges them, then hands over to
`scripts/screenshots.py`.

**The demo password is generated here and never printed.** It goes into the
environment for the seeding command and into the screenshot run as an argument,
and it exists only for the life of this process. A password echoed into a
terminal is a password in every log and every scrollback that terminal reaches.
"""

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EMAIL = "demo@goldfishlab.test"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    password = secrets.token_urlsafe(24)
    environment = {**os.environ, "GOLDFISH_DEMO_PASSWORD": password}

    _setup_django()
    _clear_demo_quota()

    print("seeding the demo account and deck ...")
    # S603: the executable is this interpreter and every argument is a literal
    # from this file. A developer tool that photographs localhost is not a
    # place untrusted input can reach.
    seeded = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "seed_demo_deck", "--email", EMAIL],
        cwd=ROOT, env=environment, capture_output=True, text=True,
    )
    if seeded.returncode:
        # The command sets the password before it imports the deck, so a
        # failure here is survivable as long as the account already has a deck
        # to photograph. The usual cause is the demo account having reached its
        # own three-deck quota, which is the quota working rather than a fault.
        print(seeded.stdout.strip() or seeded.stderr.strip().splitlines()[-1])
        print("  seeding did not complete; trying the existing demo deck")

    deck = _demo_deck()
    if deck is None:
        return 1
    print(f"using deck {deck.name!r}")

    # Before the runs, and the order is the point. A run measures the combos
    # the deck was known to have when it started, so a lookup that arrives
    # afterwards leaves the panel holding a list of combos and no timings -
    # which is precisely the state the screenshots would then show for ever.
    _look_up_combos(deck)

    if not _prepare_runs(deck):
        return 1

    in_flight = _start_something_long(deck)
    pending_url = _park_an_upload_awaiting_its_columns(deck.owner)

    command = [
        sys.executable, "scripts/screenshots.py",
        # `--password=` rather than two arguments: `secrets.token_urlsafe`
        # draws from an alphabet that includes "-", and a generated password
        # starting with one is read by argparse as a flag. Roughly one run in
        # thirty died on it, which is exactly the kind of failure that looks
        # like a fluke and gets re-run instead of fixed.
        "--base", args.base, "--email", EMAIL, f"--password={password}",
    ]
    if pending_url:
        command += ["--pending-url", pending_url]
    if args.out:
        command += ["--out", args.out]
    code = subprocess.run(command, cwd=ROOT, env=environment).returncode  # noqa: S603

    if in_flight is not None:
        _stop(in_flight)
    return code


def _setup_django() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "goldfishlab.settings.dev")
    django.setup()


def _clear_demo_quota() -> None:
    """Let the demo account import its deck and start its runs again.

    `UsageRecord` is append-only by design - deleting a deck does not give the
    allowance back, because a month's usage is a month's usage. That is correct
    for a real account and useless for a throwaway one: after a few seeding
    runs the demo account can never import another deck, and the screenshot
    pass fails on a quota rather than on anything worth looking at.

    All three, because all three run out. The deck one took three passes to
    exhaust and the run one takes seven - each pass starts three runs against a
    free allowance of twenty - so this used to work for a month and then fail
    on a Wednesday for no reason connected to anything anybody had changed.
    Imports joined them when Phase 6 §2 finally connected that lever: five a
    month, one per pass, so the sixth pass would have failed the same way.

    Scoped to this one address, and to these three metrics only.
    """
    from billing.models import UsageRecord

    cleared = UsageRecord.objects.filter(
        user__email=EMAIL,
        metric__in=(UsageRecord.Metric.DECKS_CREATED,
                    UsageRecord.Metric.RUNS_STARTED,
                    UsageRecord.Metric.IMPORTS),
    ).delete()[0]
    if cleared:
        print(f"cleared {cleared} quota record(s) for the demo account")


def _look_up_combos(deck) -> None:
    """Fill the combo panel, so the screenshot shows it holding something.

    The only place in this repository that calls Commander Spellbook without a
    person pressing a button, and it is a developer tool photographing
    localhost. **Failure is survivable and deliberately not fatal**: the panel
    has a perfectly good "could not be reached" state, and a screenshot run
    should not die because somebody else's free API is having an afternoon.
    """
    from combos import services

    try:
        outcome = services.refresh(deck, force=True)
    except Exception as exc:  # noqa: BLE001 - a screenshot run, not a service
        print(f"  combo lookup failed ({exc}); the panel will show its outage state")
        return
    panel = services.panel_for(deck)
    print(f"combos: {len(panel.included)} in the deck, {len(panel.one_away)} one card away")
    if isinstance(outcome, services.Refusal):
        print(f"  (not refreshed: {outcome.reason})")


def _park_an_upload_awaiting_its_columns(owner) -> str:
    """Leave one upload sitting on the column-mapping screen.

    That screen is a state rather than a URL: it exists only while a file is
    waiting for somebody to say which column is which. Nothing on the site
    navigates to it from a standing start, so without this the one screen in
    the importer that a person has to read would never be photographed - and
    the phase record is unambiguous about which bugs the screenshot pass finds
    and every test misses.

    The file is deliberately the awkward case: a header with no quantity
    column, which is the shape that once turned 28 Swamps into one.
    """
    from decks import services
    from decks.models import PendingImport

    sample = (
        "Card Name,Set Code,Collector Number,Condition\n"
        "Sol Ring,cmd,263,Near Mint\n"
        "Swamp,tor,341,Near Mint\n"
        "Dark Ritual,tor,61,Lightly Played\n"
    )
    pending = services.hold(
        owner=owner,
        preparation=services.prepare_text(sample, "csv"),
        kind=PendingImport.Kind.DECK,
        filename="an-export-with-no-quantity-column.csv",
    )
    print("parked one upload on the column-mapping screen")
    return pending.get_absolute_url()


def _demo_deck():
    """The deck every screenshot in this pass is about."""
    from decks.models import Deck

    deck = Deck.objects.filter(owner__email=EMAIL).order_by("-created_at").first()
    if deck is None:
        print("no demo deck; did seed_demo_deck run?", file=sys.stderr)
    return deck


def _prepare_runs(deck) -> bool:
    """Leave the demo deck holding one finished, one cancelled and one live run."""
    from simulations import services
    from simulations.models import SimulationRun

    SimulationRun.objects.filter(deck=deck).delete()
    _free_slots(deck.owner)

    # Four thousand rather than one, and the number is load-bearing: a run
    # only prices a missing card when it can afford a thousand games for one,
    # and half of a thousand-game run is five hundred. At 1,000 the combo
    # panel photographs its "run more games" state for ever, which is honest
    # and is not the state worth photographing.
    print("running a simulation to completion ...")
    finished = services.start_run(owner=deck.owner, deck=deck, games=4000, turns=6)
    if not _await(finished):
        return False

    print("cancelling one mid-flight ...")
    cancelled = services.start_run(owner=deck.owner, deck=deck, games=10_000, turns=6)
    time.sleep(3)
    services.request_cancel(cancelled)
    if not _await(cancelled):
        return False

    return True


def _start_something_long(deck):
    """Leave a run genuinely in flight, so the progress bar can be photographed.

    It has to be big. A 10,000-game run finishes in about ten seconds, which is
    less time than the screenshot pass takes to reach it - the first attempt at
    this captured a "Finished" page and called it a progress bar. The plan's
    per-run cap is raised for the length of the call and put straight back,
    because the cap is a real limit and this is a screenshot.
    """
    from billing.models import Plan
    from decks.models import Deck
    from simulations import services

    # Re-fetched deliberately. `quotas.plan_for` reads `user.subscription.plan`,
    # which Django caches on the instance - so raising the limit below is
    # invisible to an owner object loaded earlier in this process, and the run
    # is refused with the old cap. Cost an entire screenshot pass to work out.
    deck = Deck.objects.get(pk=deck.pk)
    plan = Plan.objects.get(is_default=True)
    original = plan.max_games_per_run
    plan.max_games_per_run = 500_000
    plan.save(update_fields=["max_games_per_run"])
    # The run just cancelled above may still be releasing its concurrency slot.
    time.sleep(2)
    try:
        run = services.start_run(
            owner=deck.owner, deck=deck, games=200_000, turns=6, on_the_play=False
        )
        print("started a 200,000-game run to photograph in progress")
    except Exception as exc:  # noqa: BLE001 - a missing worker is reported, not fatal
        print(f"  could not start the long run: {exc}", file=sys.stderr)
        run = None
    finally:
        plan.max_games_per_run = original
        plan.save(update_fields=["max_games_per_run"])

    if run is not None:
        # Let it get past zero, so the bar has something to show.
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            run.refresh_from_db()
            if run.games_done:
                break
            time.sleep(1)
    return run


def _stop(run) -> None:
    """Cancel the photographed run rather than leaving it to grind on."""
    from simulations import services

    services.request_cancel(run)
    print("cancelled the long run")


def _await(run, timeout: float = 180.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        run.refresh_from_db()
        if run.is_finished:
            print(f"  {run.status}")
            return True
        time.sleep(1)
    print(f"  run {run.pk} never finished - is a worker running?", file=sys.stderr)
    return False


def _free_slots(owner) -> None:
    """Clear any concurrency slots left behind by earlier runs of this script."""
    from simulations import services

    try:
        services._redis().delete(f"sim:active:{owner.pk}")
    except Exception as exc:  # noqa: BLE001 - a missing Redis is reported, not fatal
        print(f"  could not clear the concurrency slot: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
