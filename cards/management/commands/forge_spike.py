"""Card model phase 0.5 + 0.6: how far Forge's scripts carry, and where they disagree.

    manage.py forge_spike                     # checkout in .data/forge
    manage.py forge_spike --forge DIR --examples 10

Needs a checkout of Forge's card scripts (only ``forge-gui/res/cardsfolder``,
at one pinned commit) and a frozen reading (`engine_coverage --freeze`).
Prints, for the 3,070-card coverage set and for every Commander-legal card:

* how many cards have a script (matched by the front face's name);
* what share uses nothing outside the N most used constructs (0.5);
* where the five goldfish facts of `cards.forge.facts` differ between the
  script and our reader (0.6).

A spike's report: nothing is written, nothing of Forge's is committed.
"""

from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cards import forge
from simulation import serial
from simulation.cards import Card
from simulations import coverage, differential

SIZES = (60, 100, 180, 250, 400)


def our_facts(card: Card) -> dict:
    """The same five facts, as our reader and engine see the card."""
    taps_for = {letter for ability in card.mana_abilities if ability.rule == "flat"
                for key, _amount in ability.produces for letter in key}
    treasures = bool(card.treasures or any(t.treasures for t in card.triggers)
                     or any(t.treasures for t in card.combat))
    return {"taps_for": "".join(sorted(taps_for)),
            "draws": "x" if card.draws_x else card.draw_on_cast,
            "land_search": card.land_search is not None,
            "enters_tapped": card.enters_tapped or card.tapped_unless is not None,
            "treasures": treasures}


class Command(BaseCommand):
    help = "Phase-0 spike: coverage of Forge's card scripts and a cross-check with our reader."

    def add_arguments(self, parser):
        parser.add_argument("--forge", default=str(Path(settings.BASE_DIR) / ".data" / "forge"))
        parser.add_argument("--examples", type=int, default=8)

    def handle(self, *args, **options):
        from cards.models import OracleCard

        root = Path(options["forge"])
        if not (root / forge.SCRIPTS).exists():
            raise CommandError(f"no scripts under {root / forge.SCRIPTS}")
        frozen_path = differential.latest_frozen()
        if frozen_path is None:
            raise CommandError("nothing frozen yet: run engine_coverage --freeze first")

        scripts = forge.load(root)
        self.stdout.write(f"Forge commit {forge.commit_of(root)[:10]}: {len(scripts)} scripts")
        frozen = differential.load(frozen_path)["cards"]
        fixed = set(coverage.ids(coverage.load_snapshot()))

        by_id = {}
        unmatched = {"catalogue": [], "fixed": []}
        for pk, name, front in (OracleCard.objects.filter(legalities__commander="legal")
                                .values_list("pk", "name", "front_name")):
            script = scripts.get(front or name) or scripts.get(name)
            if script is None:
                unmatched["catalogue"].append(name)
                if str(pk) in fixed:
                    unmatched["fixed"].append(name)
            else:
                by_id[str(pk)] = (name, script)

        sets = {"fixed": {pk: value for pk, value in by_id.items() if pk in fixed},
                "catalogue": by_id}
        totals = {"fixed": len(fixed), "catalogue": len(by_id) + len(unmatched["catalogue"])}
        every = [script for _name, script in by_id.values()]
        for label, found in sets.items():
            self.stdout.write(f"\n== {label}: {len(found)} of {totals[label]} cards have a "
                              f"script; without: {unmatched[label][:options['examples']]}")
        self._coverage(sets, every, options)
        self._cross_check(sets, frozen, options)

    def _coverage(self, sets, every, options):
        self.stdout.write("\n== 0.5: cards using only the N most used constructs")
        used: Counter = Counter()
        for script in every:
            used.update(set(forge.constructs(script)))
        self.stdout.write(f"distinct constructs on Commander-legal cards: {len(used)}")
        for size in SIZES:
            words = forge.vocabulary(every, size)
            shares = "  ".join(
                f"{label} {forge.coverage([s for _n, s in found.values()], words).share:.1%}"
                for label, found in sets.items())
            self.stdout.write(f"  N={size:4d}  {shares}")
        words = forge.vocabulary(every, 180)
        missing = forge.coverage([s for _n, s in sets["fixed"].values()], words).missing
        self.stdout.write(f"most used outside N=180 (fixed set): {missing.most_common(15)}")
        heads: Counter = Counter()
        for script in every:
            heads.update(forge.count_heads(script))
        self.stdout.write(f"Count$ heads: {len(heads)}, top {heads.most_common(10)}")

    def _cross_check(self, sets, frozen, options):
        self.stdout.write("\n== 0.6: the five goldfish facts, script against our reader")
        default = coverage._DEFAULT
        readings = {entry["id"]: (key, entry) for key, entry in frozen.items()}
        for label, found in sets.items():
            agree, differ = Counter(), {}
            for pk, (name, script) in found.items():
                if pk not in readings:
                    continue
                key, entry = readings[pk]
                card = serial.load(Card, {**default, **entry["read"], "name": name})
                theirs, ours = forge.facts(script), our_facts(card)
                for fact in theirs:
                    if theirs[fact] == ours[fact]:
                        agree[fact] += 1
                    else:
                        differ.setdefault(fact, []).append(
                            f"{name}: script {theirs[fact]!r}, ours {ours[fact]!r}"
                            + (" (unread)" if entry["unread"] else ""))
            self.stdout.write(f"-- {label}")
            for fact in ("taps_for", "draws", "land_search", "enters_tapped", "treasures"):
                rows = differ.get(fact, [])
                self.stdout.write(f"  {fact:14s} agree {agree[fact]:6d}  differ {len(rows):5d}")
                if label == "fixed":
                    for row in rows[:options["examples"]]:
                        self.stdout.write(f"      {row}")
