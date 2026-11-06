"""The themes of every deck in this database, and any slug that went missing.

    manage.py themes_report                 # every deck
    manage.py themes_report --deck "Calling All Angels" --cards

Read-only (Themen-Doc, Paket 1). For each deck its themes with level and
fill, and each piece as count/need; ``--cards`` adds the cards behind each
piece. First, every slug of `cards.themes` the catalogue does not hold: a
tag Scryfall renamed stops matching without an error, and this is where
that shows.
"""

from django.core.management.base import BaseCommand

from cards import themes
from cards.models import Tag
from decks.models import Deck
from decks.themes import deck_themes


class Command(BaseCommand):
    help = "Print the themes of every deck, and any theme tag the catalogue lacks."

    def add_arguments(self, parser):
        parser.add_argument("--deck", help="Only decks whose name starts with this.")
        parser.add_argument("--cards", action="store_true", help="Name the cards per piece.")

    def handle(self, *args, deck=None, cards=False, **options):
        missing = sorted(themes.SLUGS - set(Tag.objects.values_list("slug", flat=True)))
        if missing:
            self.stdout.write(self.style.WARNING("Tags missing: " + ", ".join(missing)))
        decks = Deck.objects.order_by("name")
        if deck:
            decks = decks.filter(name__startswith=deck)
        for each in decks:
            found = deck_themes(each)
            self.stdout.write(f"{each.name}: " + (", ".join(_short(f) for f in found) or "-"))
            if cards:
                for result in found:
                    for part in result.parts:
                        self.stdout.write(f"    {result.theme}/{part.key}: "
                                          + ", ".join(part.cards))


def _short(found: themes.Found) -> str:
    subject = f" ({found.subject})" if found.subject else ""
    parts = " ".join(f"{part.key} {part.count}/{part.need}" for part in found.parts)
    return f"{found.label}{subject} {found.level} {found.fill:.2f} [{parts}]"
