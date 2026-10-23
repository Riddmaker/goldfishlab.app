"""Work an import leaves for the worker."""

from celery import shared_task


@shared_task(name="decks.fetch_printing_art")
def fetch_printing_art(deck_id: str) -> int:
    """Find the art of the printings a just-imported file named."""
    from decks import printing_art
    from decks.models import Deck

    try:
        deck = Deck.objects.select_related("commander", "partner").filter(pk=deck_id).first()
        # Deleted before the worker got to it (a guest's deck can be).
        return printing_art.attach(deck) if deck else 0
    finally:
        printing_art.done(deck_id)
