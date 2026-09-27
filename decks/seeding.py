"""Writing a `DeckShape` into the database.

The one place that turns the pure data in `decks/fixtures.py` into `Deck`,
`DeckCard` and `CardAnnotation` rows. Both the test suite and
`manage.py seed_test_decks` come through here, so a deck built for a screenshot
and a deck built for an assertion are the same deck - which is the only way a
screenshot can be evidence about the thing the tests cover.

**Deck-scoped annotations, always.** A shape's judgements are written with an
explicit owner *and* deck. A built-in row (`owner=None, deck=None`) applies to
every deck of every user, so seeding one from a test fixture would leak one
deck's opinion into every other deck in the database - including the reference
deck, whose numbers are the fixed point everything else is measured against.
"""

from dataclasses import dataclass

from django.db import transaction

from cards.models import OracleCard
from cards.names import normalise
from decks.fixtures import DeckShape
from decks.models import Deck, DeckCard


class MissingCards(LookupError):
    """A shape names cards the catalogue does not have.

    Its own exception rather than a bare `LookupError`, because the two callers
    want different things from it: the management command prints it and stops,
    and the test suite treats it as "the fixture sample needs rebuilding" - a
    specific, fixable thing rather than a mystery.
    """

    def __init__(self, names):
        self.names = sorted(names)
        super().__init__(
            "not in the catalogue: " + ", ".join(self.names)
            + "\nRun `manage.py ingest_scryfall`, or rebuild the test fixture "
              "with `scripts/build_card_fixtures.py`."
        )


def resolve(names) -> dict[str, OracleCard]:
    """Match card names against the catalogue, by name and then by folded name.

    Rungs 4 and 5 of `decks/resolve.py`'s ladder, and only those two: a shape is
    written by hand and has no printing or oracle id to offer, so the rungs
    above have nothing to work with. Rung 5 is not decoration - it is what finds
    `Lim-Dul the Necromancer`, which Scryfall spells with a circumflex and every
    deck list in the world spells without one.

    Two queries for a whole deck, not two per card.
    """
    names = set(names)
    by_name = {
        card.front_name: card
        for card in OracleCard.objects.filter(front_name__in=names)
    }

    outstanding = names - set(by_name)
    if outstanding:
        folded = {normalise(name): name for name in outstanding}
        for card in OracleCard.objects.filter(search_name__in=folded):
            by_name.setdefault(folded[card.search_name], card)

    missing = names - set(by_name)
    if missing:
        raise MissingCards(missing)
    return by_name


@dataclass(frozen=True)
class Seeded:
    """What a build produced, for a caller that wants to report on it."""

    deck: Deck
    shape: DeckShape
    annotations: int

    def __str__(self) -> str:
        return (
            f"{self.shape.key:20s} {self.deck.card_count:3d} cards, "
            f"{self.shape.land_count:2d} lands, "
            f"{self.annotations} annotations, "
            f"commander: {self.deck.commander.front_name if self.deck.commander_id else '-'}"
        )


@transaction.atomic
def build(shape: DeckShape, owner, *, name: str | None = None) -> Seeded:
    """Write one shape into the database, replacing any previous copy.

    Replacing rather than adding, because both callers run repeatedly: the
    command while iterating on a screenshot, the tests once per test. A build
    that piled up would make the second run of either mean something different
    from the first.
    """
    cards = resolve(shape.names)

    deck, _ = Deck.objects.get_or_create(owner=owner, name=name or shape.name)
    DeckCard.objects.filter(deck=deck).delete()
    DeckCard.objects.bulk_create(
        [
            DeckCard(deck=deck, oracle_card=cards[card_name], quantity=quantity)
            for quantity, card_name in shape.cards
        ]
    )

    deck.commander = cards[shape.commander] if shape.commander else None
    deck.save(update_fields=["commander", "updated_at"])

    written = _write_annotations(shape, deck, cards)
    return Seeded(deck=deck, shape=shape, annotations=written)


def _write_annotations(shape: DeckShape, deck: Deck, cards: dict) -> int:
    """This shape's judgements, scoped to this deck and nowhere wider."""
    from simulations.models import CardAnnotation

    # Clear this deck's own rows first. Without it, editing a shape would leave
    # the previous judgement behind on every card the new one no longer names,
    # and the deck would go on simulating with an opinion no file records.
    CardAnnotation.objects.filter(deck=deck).delete()

    for card_name, overrides in shape.annotations.items():
        CardAnnotation.objects.create(
            owner=deck.owner,
            deck=deck,
            oracle_card=cards[card_name],
            # `dict` rather than the mapping proxy the fixture holds: a
            # JSONField serialises with `json.dumps`, which does not know what
            # a mappingproxy is.
            overrides=dict(overrides),
            note=f"Seeded with the '{shape.key}' test deck.",
        )
    return len(shape.annotations)


def build_all(owner, shapes=None) -> list[Seeded]:
    """Every shape, for one owner."""
    from decks.fixtures import SHAPES

    return [build(shape, owner) for shape in (shapes if shapes is not None else SHAPES)]
