"""Card model phase 3, gate D: today's roles beside the derived categories.

    manage.py categories_report                     # every Commander-legal card
    manage.py categories_report --set fixed --examples 20
    manage.py categories_report --decks             # every deck in this database
    manage.py categories_report --decks --explain   # ... and the cards behind each move
    manage.py categories_report --cards "Path to Exile;Deadly Dispute"
    manage.py categories_report --out .data/categories.csv

Read-only. The categories are derived here, on the fly, from each card's
blocks and its stored `role_tags` (`cards.categories.derive`), so the gate
runs without a profile rebuild and nothing in the database changes.

"Today" is what `Card.categories` holds before a profile is derived again:
`role_tags`. "New" is the
primary and secondary role and the mechanics. For each category the two
counts, then the differences by family, with card names:

* ``gained`` - a role a block found that the tags do not have;
* ``dropped`` - a tag role the blocks hold only as a rider, on a card read
  whole: Path to Exile's ramp;
* ``kept`` - the same on a card read in part, so the role stayed;
* ``trimmed`` - a third role and beyond: one primary, at most one secondary;
* ``mechanic`` - a new mechanic, or a tag mechanic the tags do not have.

``--decks`` counts the cards of each deck per category, today and new, as
the draw statistics group them. ``--out`` writes one row per card (keep it
in ``.data/``, which git ignores).
"""

import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand

from cards import categories, forge_merge
from simulations import coverage

FAMILIES = ("gained", "dropped", "kept", "trimmed", "mechanic")
KEYS = categories.ROLES + categories.MECHANICS


def today(role_tags) -> frozenset[str]:
    return frozenset(tag for tag in role_tags if tag in KEYS)


def families(role_tags, found: categories.Categories) -> dict[str, list[str]]:
    """How one card's categories differ from today's, by family."""
    tagged = {tag for tag in role_tags if tag in categories.ROLES}
    roles = set(found.roles)
    dropped = [note.split(":", 1)[1] for note in found.review if note.startswith("dropped:")]
    kept = [note.split(":", 1)[1] for note in found.review if note.startswith("incidental:")]
    return {
        "gained": sorted(roles - tagged),
        "dropped": sorted(dropped),
        "kept": sorted(kept),
        "trimmed": sorted(tagged - roles - set(dropped)),
        "mechanic": sorted(set(found.mechanics) - set(role_tags)),
    }


def derive(card) -> categories.Categories:
    profile = card.profile
    return categories.derive(profile.kind, forge_merge.blocks_of(card), profile.role_tags,
                             profile)


class Command(BaseCommand):
    help = "Phase 3, gate D: today's roles beside the derived categories (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--set", choices=("catalogue", "fixed"), default="catalogue",
                            help="every Commander-legal card, or the 3,070-card coverage set")
        parser.add_argument("--examples", type=int, default=12)
        parser.add_argument("--decks", action="store_true",
                            help="per deck in this database: cards per category, today and new")
        parser.add_argument("--explain", action="store_true",
                            help="with --decks: the cards behind each moved role")
        parser.add_argument("--cards", default="", help="names, separated by ';'")
        parser.add_argument("--out", type=Path)

    def handle(self, *args, **options):
        from cards.models import OracleCard

        cards = OracleCard.objects.filter(profile__isnull=False).select_related(
            "profile", "forge").order_by("name")
        if options["out"] and (options["cards"] or options["decks"]):
            self.stderr.write("--out writes the catalogue only; ignored here.")
        if options["cards"]:
            names = [name.strip() for name in options["cards"].split(";") if name.strip()]
            found = list(cards.filter(name__in=names))
            for card in found:
                self._card(card)
            missing = sorted(set(names) - {card.name for card in found})
            if missing:
                self.stderr.write("Not found (the full name, both faces of a double-faced "
                                  "card): " + "; ".join(missing))
            return
        if options["decks"]:
            self._decks(cards, options["explain"])
            return
        cards = cards.filter(legalities__commander="legal")
        if options["set"] == "fixed":
            cards = cards.filter(pk__in=coverage.ids(coverage.load_snapshot()))
        self._catalogue(list(cards), options)

    def _card(self, card) -> None:
        found = derive(card)
        self.stdout.write(f"{card.name}: today {sorted(today(card.profile.role_tags))}")
        self.stdout.write(f"    role {found.primary or '-'} / {found.secondary or '-'}; "
                          f"mechanics {list(found.mechanics)}; review {list(found.review)}")
        for ability_id, value in found.tempo.items():
            self.stdout.write(f"    {ability_id}: {value['rate']}, needs {value['needs']}")

    def _catalogue(self, cards, options) -> None:
        before, after = Counter(), Counter()
        primary = Counter()
        examples: dict[tuple[str, str], list[str]] = {}
        counts: dict[tuple[str, str], int] = Counter()
        review = Counter()
        rows = []
        for card in cards:
            found = derive(card)
            old = today(card.profile.role_tags)
            before.update(old)
            after.update(found.keys)
            primary[found.primary or "(none)"] += 1
            review.update(note.split(":")[0] for note in found.review)
            for family, names in families(card.profile.role_tags, found).items():
                for name in names:
                    counts[(family, name)] += 1
                    examples.setdefault((family, name), []).append(card.name)
            if options["out"]:
                rows.append([card.name, " ".join(sorted(old)), found.primary,
                             found.secondary, " ".join(found.mechanics),
                             " ".join(found.review)])

        self.stdout.write(f"{len(cards)} cards; contradictions: "
                          + ", ".join(f"{name} {count}" for name, count in review.items()))
        self.stdout.write(f"{'category':15s} {'today':>7s} {'new':>7s}")
        for key in KEYS:
            self.stdout.write(f"{key:15s} {before[key]:7d} {after[key]:7d}")
        self.stdout.write("primary roles: " + ", ".join(
            f"{name} {count}" for name, count in primary.most_common()))
        for family in FAMILIES:
            for key in KEYS:
                if counts[(family, key)]:
                    shown = examples[(family, key)][:options["examples"]]
                    self.stdout.write(f"  {family:9s} {key:14s} {counts[(family, key)]:6d}  "
                                      + "; ".join(shown))
        if options["out"]:
            with options["out"].open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["name", "today", "primary", "secondary", "mechanics",
                                 "review"])
                writer.writerows(rows)
            self.stdout.write(f"{len(rows)} cards written to {options['out']}")

    def _decks(self, cards, explain: bool) -> None:
        from decks.models import Deck

        by_id = {}
        for deck in Deck.objects.order_by("name").prefetch_related("entries"):
            entries = list(deck.entries.all())
            missing = [entry.oracle_card_id for entry in entries
                       if entry.oracle_card_id not in by_id]
            for card in cards.filter(pk__in=missing):
                by_id[card.pk] = (card.name, today(card.profile.role_tags), derive(card).keys)
            before, after = Counter(), Counter()
            why: dict[str, list[str]] = {}
            for entry in entries:
                if entry.oracle_card_id == deck.commander_id or entry.oracle_card_id not in by_id:
                    continue
                name, old, new = by_id[entry.oracle_card_id]
                for key in old:
                    before[key] += entry.quantity
                for key in new:
                    after[key] += entry.quantity
                for key in old ^ new:
                    why.setdefault(key, []).append(("+" if key in new else "-") + name)
            moved = [f"{key} {before[key]}->{after[key]}" for key in KEYS
                     if before[key] != after[key]]
            self.stdout.write(f"{deck.name}: " + (", ".join(moved) if moved else "no change"))
            if explain:
                for key in KEYS:
                    if key in why:
                        self.stdout.write(f"    {key}: " + "; ".join(sorted(why[key])))
