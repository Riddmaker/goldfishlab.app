"""The land sweep behind "How many lands in Commander?" (P11).

A few precons, each rebuilt with every land count from `LANDS` and played as
the precons are. The same deck at eight land counts says what one land more
or less does - which comparing precons cannot, as they differ in everything
else too.

**Which precons:** the `PRECONS` the engine reads best (`readable_pct`), so
the cards that are not lands are played as written. Ties go by name.

**How a deck is rebuilt** - simple rules the article states:

- *More lands:* the most expensive cards that are not lands leave (highest
  mana value, then name), and basic lands come in, spread over the basics the
  deck already has in proportion.
- *Fewer lands:* basic lands leave, the most numerous first, and second
  copies of the deck's own spells of mana value 2 and 3 come in, by name.
  That breaks the one-copy rule; it is a simulation shortcut that keeps the
  deck's curve, and the article says so.
"""

import logging

from django.db import transaction

from datapages import services
from datapages.models import LandSweep, Precon
from decks.models import Deck, DeckCard
from simulations import services as simulations

logger = logging.getLogger(__name__)

LANDS = range(33, 41)
PRECONS = 3
#: The mana values a card added on the way down may have.
FILLER_MANA = (2, 3)


class SweepError(ValueError):
    """This deck cannot be rebuilt by the rules (no basics, no cheap spells)."""


def chosen() -> list[Precon]:
    """The precons the sweep uses."""
    candidates = []
    for precon in Precon.objects.filter(published=True, deck__isnull=False):
        shared = services.report_of(precon)
        if shared is not None:
            candidates.append((-shared.run.readable_pct, precon.name, precon))
    return [precon for *_, precon in sorted(candidates, key=lambda item: item[:2])[:PRECONS]]


def _is_basic(card) -> bool:
    return card.type_line.startswith("Basic Land")


def rebuild(counts: dict, lands: int) -> dict:
    """`counts` ({card: copies}, the 99) with `lands` lands, by the rules
    above. A new dict; the number of cards stays the same."""
    counts = dict(counts)
    delta = lands - sum(copies for card, copies in counts.items() if card.is_land)
    basics = sorted((card for card in counts if _is_basic(card)), key=lambda card: card.name)
    if delta and not basics:
        raise SweepError("the deck has no basic lands")
    if delta > 0:
        spells = sorted((card for card in counts if not card.is_land),
                        key=lambda card: (-card.cmc, card.name))
        for card in _take(spells, counts, delta):
            counts[card] -= 1
        original = {card: counts[card] for card in basics}
        for _ in range(delta):
            # The basic furthest below its share of what is added.
            card = min(basics, key=lambda c: ((counts[c] - original[c] + 1) / original[c], c.name))
            counts[card] += 1
    elif delta < 0:
        for _ in range(-delta):
            card = max(basics, key=lambda c: (counts[c], c.name))
            if counts[card] < 1:
                raise SweepError("not enough basic lands to take out")
            counts[card] -= 1
        fillers = sorted((card for card in counts
                          if not card.is_land and card.cmc in FILLER_MANA),
                         key=lambda card: card.name)
        if not fillers:
            raise SweepError("the deck has no spells of mana value 2 or 3")
        for index in range(-delta):
            counts[fillers[index % len(fillers)]] += 1
    return {card: copies for card, copies in counts.items() if copies > 0}


def _take(spells, counts, how_many):
    """`how_many` cards off the front of `spells`, a copy at a time."""
    taken, left = [], dict(counts)
    for card in spells:
        while left[card] and len(taken) < how_many:
            left[card] -= 1
            taken.append(card)
    if len(taken) < how_many:
        raise SweepError("not enough cards that are not lands")
    return taken


@transaction.atomic
def build(precon: Precon, lands: int) -> LandSweep:
    """The precon with `lands` lands, as a deck of the system account, started."""
    # The old variant's deck, and with it the variant and its runs.
    Deck.objects.filter(land_sweep__precon=precon, land_sweep__lands=lands).delete()
    source = precon.deck
    counts = {entry.oracle_card: entry.quantity
              for entry in source.entries.select_related("oracle_card")}
    deck = Deck.objects.create(owner=source.owner, commander=source.commander,
                               name=f"{precon.name} ({lands} lands)"[:120])
    DeckCard.objects.bulk_create(DeckCard(deck=deck, oracle_card=card, quantity=copies)
                                 for card, copies in rebuild(counts, lands).items())
    sweep = LandSweep.objects.create(precon=precon, lands=lands, deck=deck,
                                     list_print=precon.list_print)
    simulations.start_lab_run(
        owner=source.owner, deck=deck, games=services.GAMES, turns=services.TURNS,
        on_the_play=services.ON_THE_PLAY, seed=services.seed_for(f"{precon.slug}-{lands}"))
    return sweep


def run(*, rerun: bool = False) -> list[tuple[str, str]]:
    """Every chosen precon at every land count, built where it is missing or
    its precon changed (or every one, with `rerun`). One line per variant."""
    lines = []
    for precon in chosen():
        done = {sweep.lands: sweep for sweep in precon.sweeps.all()}
        for lands in LANDS:
            existing = done.get(lands)
            if existing and existing.list_print == precon.list_print and not rerun:
                lines.append((services.UNCHANGED, str(existing)))
                continue
            try:
                sweep = build(precon, lands)
            except SweepError as exc:
                logger.warning("sweep %s at %d lands: %s", precon.slug, lands, exc)
                lines.append((services.HELD, f"{precon}, {lands} lands: {exc}"))
                continue
            lines.append((services.CREATED if existing is None else services.CHANGED,
                          str(sweep)))
    return lines
