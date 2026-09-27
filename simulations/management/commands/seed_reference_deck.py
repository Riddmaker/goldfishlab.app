"""Build the hand-annotated reference deck in the database.

    py -3.13 manage.py seed_reference_deck

This is the fixed point the whole Phase 2 boundary is measured against. It
writes the Chainer deck from `simulation/fixtures/chainer.py` into real `Deck`,
`DeckCard` and `CardAnnotation` rows, so that

    adapter.deck_definition(Deck.objects.get(...)) == chainer.DECK

can be asserted. If that equality holds, a deck stored in Postgres and a deck
written by hand in Python are the same thing to the engine - which is the
entire promise of this phase.

The annotations it writes are **built-in scope** (no owner, no deck): they are
the defaults the reference deck was researched with, not one user's opinion.

One kind of judgement is held back from that scope - see
`_without_deck_specific_colour`. A built-in default has to be true for every
deck, and "this source makes black" is only true for a black one.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from decks import seeding
from decks.models import Deck, DeckCard
from simulation.fixtures import chainer
from simulations.fixtures import annotation_overrides
from simulations.models import CardAnnotation

DECK_NAME = "Chainer, Dementia Master"

_COLORS = frozenset("WUBRG")


def _without_deck_specific_colour(overrides: dict, oracle_card) -> dict:
    """Drop the one judgement that must not become everyone's default.

    Arcane Signet reads "add one mana of any color in your commander's color
    identity". In this deck that is black - but a built-in annotation applies to
    every deck of every user, and seeding "Arcane Signet makes black" made a
    mono-green deck read its Signet as a black source.

    The test is the card, not its name: if Scryfall says it can make more than
    one colour, what it makes here is a fact about *this* deck. The adapter
    works it out per deck from the commander's colour identity, and reports the
    choice it had to make as a gap. Everything else in the annotation - how
    early to cast it, whether it keeps a one-land hand - stays, because those
    really are judgements no derivation can supply.
    """
    produced = {color for color in oracle_card.produced_mana or [] if color in _COLORS}
    if len(produced) > 1 and "mana_produces" in overrides:
        overrides = {k: v for k, v in overrides.items() if k != "mana_produces"}
    return overrides
DEFAULT_EMAIL = "reference@goldfishlab.test"


class Command(BaseCommand):
    help = "Write the hand-annotated Chainer deck into the database."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument("--name", default=DECK_NAME)

    @transaction.atomic
    def handle(self, *args, **options):
        owner = self._owner(options["email"])
        cards = self._resolve_all()

        deck, _ = Deck.objects.get_or_create(owner=owner, name=options["name"])
        DeckCard.objects.filter(deck=deck).delete()

        quantities: dict[str, int] = {}
        for card in chainer.build_deck():
            quantities[card.name] = quantities.get(card.name, 0) + 1

        DeckCard.objects.bulk_create(
            [
                DeckCard(deck=deck, oracle_card=cards[name], quantity=quantity)
                for name, quantity in quantities.items()
            ]
        )

        deck.commander = cards[chainer.COMMANDER.name]
        deck.save(update_fields=["commander", "updated_at"])

        written = self._write_annotations(cards)

        self.stdout.write(
            self.style.SUCCESS(
                f"{deck.name}: {sum(quantities.values())} cards, "
                f"{len(quantities)} distinct, {written} annotations"
            )
        )
        self.stdout.write(f"deck id: {deck.pk}")

    def _owner(self, email: str):
        user_model = get_user_model()
        owner, created = user_model.objects.get_or_create(email=email)
        if created:
            # Never signed into; it exists only to own the reference rows.
            owner.set_unusable_password()
            owner.save(update_fields=["password"])
        return owner

    def _resolve_all(self) -> dict:
        """Every fixture card, matched against the ingested catalogue.

        Through `decks.seeding.resolve`, which is the same two-rung lookup the
        test decks use. It was written twice once, and the copies did not agree
        about accent folding.
        """
        wanted = {card.name for card in chainer.build_deck()}
        wanted.add(chainer.COMMANDER.name)
        try:
            return seeding.resolve(wanted)
        except seeding.MissingCards as exc:
            raise CommandError(str(exc)) from exc

    def _write_annotations(self, cards: dict) -> int:
        """One built-in annotation per distinct fixture card that needs one."""
        written = 0
        for card in [chainer.COMMANDER, *chainer.build_deck()]:
            overrides = _without_deck_specific_colour(
                annotation_overrides(card), cards[card.name]
            )
            if not overrides:
                continue
            _, created = CardAnnotation.objects.update_or_create(
                owner=None,
                deck=None,
                oracle_card=cards[card.name],
                defaults={
                    "overrides": overrides,
                    "note": "Seeded from the hand-annotated reference deck.",
                },
            )
            written += int(created)
        return written
