"""Which cards of a deck still need their owner, and in what order (Phase 9 C2).

A card needs its owner when the engine could not read its mana - a `reading`
gap - and the owner has not said anything about it yet. "Said anything" is any
row of theirs for that card, on this deck or on all their decks: a saved value,
or only "Looks right". The built-in annotations are not an answer, because
nobody asked this user anything by shipping them.

The queue is stable: every card with a reading gap, by name, answered or not.
Answering a card ticks it rather than taking it out, so "2 of 5" still means
the second of the same five after a save, and Back goes where it went before.

The count lives on `Deck.open_questions`, `None` until somebody needs it. The
four things that can change it set it back to `None` through
`decks.services.recount_later`; nothing here has to know what they were.
"""

from dataclasses import dataclass

from django.db.models import Q

from decks.models import Deck
from simulations.engine import adapter
from simulations.models import CardAnnotation


@dataclass(frozen=True)
class Question:
    """One card of the queue."""

    reading: adapter.Reading
    answered: bool

    @property
    def oracle_card(self):
        return self.reading.oracle_card

    @property
    def name(self) -> str:
        return self.reading.oracle_card.front_name


def queue(deck: Deck, readings: list | None = None) -> list[Question]:
    """Every card of the deck the engine could not read, by name.

    `readings` may be handed in by a caller that has them already - the tune
    page does - so one page does not read the deck twice.
    """
    if readings is None:
        readings = adapter.readings(deck)
    unreadable = [reading for reading in readings if reading.unreadable]
    answered = answered_ids(deck, [reading.oracle_card.pk for reading in unreadable])
    return sorted(
        (Question(reading=reading, answered=reading.oracle_card.pk in answered)
         for reading in unreadable),
        key=lambda question: question.name.lower(),
    )


def answered_ids(deck: Deck, oracle_ids) -> set:
    """The cards among `oracle_ids` the deck's owner has said something about."""
    return set(
        CardAnnotation.objects.filter(owner=deck.owner, oracle_card_id__in=oracle_ids)
        .filter(Q(deck__isnull=True) | Q(deck=deck))
        .values_list("oracle_card_id", flat=True)
    )


def open_questions(deck: Deck) -> int:
    """How many cards of the deck still need their owner, counted at most once.

    Stored with `update`, so counting neither bumps `updated_at` - which orders
    the deck list - nor races a save of the deck's other fields.
    """
    if deck.open_questions is None:
        count = sum(1 for question in queue(deck) if not question.answered)
        Deck.objects.filter(pk=deck.pk).update(open_questions=count)
        deck.open_questions = count
    return deck.open_questions
