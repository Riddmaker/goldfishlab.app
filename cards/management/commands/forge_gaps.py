"""Rank what the reader leaves open, to pick the next construct to teach it.

    manage.py forge_gaps [--top 20]

Reads the stored blocks (`cards.models.ForgeScript`) of the Commander-legal
cards, and ranks every unmodeled construct and unread clause by the decks
its cards are in, then by the number of cards (`cards.forge_import.rank_gaps`).
Prints a Markdown table. One construct per pull request: teach it to the
reader in `cards/forge_import.py`, with a test, then run this again
(docs/phases/automation.md, "Forge gaps").
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from cards import forge_import
from cards.models import ForgeScript


class Command(BaseCommand):
    help = "Rank the constructs the Forge reader leaves open, by the decks they touch."

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=20, help="rows to print")

    def handle(self, *args, **options):
        rows = (ForgeScript.objects
                .filter(oracle_card__legalities__commander="legal")
                .annotate(decks=Count("oracle_card__in_decks__deck", distinct=True))
                .values_list("oracle_card__name", "unmodeled", "unread", "decks"))
        total = 0
        cards = []
        for name, unmodeled, unread, decks in rows.iterator():
            total += 1
            if unmodeled or unread:
                cards.append((name, [*unmodeled, *unread], decks))
        gaps = forge_import.rank_gaps(cards)
        read = total - len(cards)
        self.stdout.write(f"Commander-legal cards with a script: {total}, read in full: "
                          f"{read} ({read / total:.0%})\n" if total else "No scripts stored.\n")
        self.stdout.write("| Construct | Cards | In decks | For example |")
        self.stdout.write("| --- | --: | --: | --- |")
        for gap in gaps[:options["top"]]:
            examples = ", ".join(gap.examples)
            self.stdout.write(f"| `{gap.construct}` | {gap.cards} | {gap.decks} | {examples} |")
