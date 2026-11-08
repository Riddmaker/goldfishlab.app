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


@shared_task(name="decks.read_hand")
def read_hand(deck_id: str, seed: int, on_the_play: bool) -> bool:
    """Play one hand of the hand tutor (`decks.tutor.measure`); its answer
    goes to the cache, where the page polls for it."""
    from decks import tutor
    from decks.models import Deck

    deck = Deck.objects.filter(pk=deck_id).first()
    # Deleted before the worker got to it (a guest's deck can be).
    if deck is None:
        return False
    tutor.measure(deck, seed, on_the_play)
    return True


@shared_task(name="decks.read_mulligan")
def read_mulligan(deck_id: str, seed: int) -> bool:
    """Play one hand of "Keep or mulligan?" both ways (`decks.mulligan.measure`);
    its answer goes to the cache, where the page polls for it."""
    from decks import mulligan
    from decks.models import Deck

    deck = Deck.objects.filter(pk=deck_id).first()
    if deck is None:
        return False
    mulligan.measure(deck, seed)
    return True
