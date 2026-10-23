"""The art of the printings an imported file named.

The catalogue holds one printing per card (`cards.models`), so a deck list
that says `1x Animate Dead (5ed) 140` used to show whatever picture Scryfall's
oracle file happens to ship - the right card in the wrong frame. After an
import, a worker asks Scryfall's `/cards/collection` for the printings the
file named (two requests for a Commander deck) and keeps each one's image
link on the printing it belongs to.

Three rules keep this from costing the import anything:

* **It runs after the import, never inside it.** The import is the same as
  before; the art is filled in on the worker. Scryfall being slow or down
  leaves the catalogue's picture, nothing worse.
* **A printing only counts for its own card.** Scryfall's answer is matched
  back by the oracle id of the card the row resolved to, so a typo in a
  collector number cannot put another card's art on this one.
* **The deck page knows it is coming.** `mark_pending` sets a short-lived flag
  the page reads to show placeholders and fetch the grid again until the
  art is in (`is_pending`). The flag expires by itself, so a worker that
  never runs costs a few seconds of placeholders, not a page that waits for
  ever.

When Scryfall does not answer, the printings asked for are marked
`ART_MISSING`: the deck page shows the catalogue's picture with a small
button to try again (`retry`). A printing Scryfall answered without is not
marked - a typo or an unknown printing cannot be fixed by asking again.
"""

import logging
import re

from django.core.cache import cache
from django.db import transaction

from cards import scryfall
from cards.models import image_uri
from decks.models import ART_HOST, ART_MISSING, Deck, DeckCard, art_missing

logger = logging.getLogger(__name__)

#: How long the deck page waits for the art at most, in seconds.
PENDING_SECONDS = 20

_SET = re.compile(r"[a-z0-9]{2,12}")
_NUMBER = re.compile(r"[0-9a-z★†\-]{1,16}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _key(deck_id) -> str:
    return f"printing-art:{deck_id}"


def is_pending(deck: Deck) -> bool:
    """Whether the art of this deck's printings is still being looked up."""
    return bool(cache.get(_key(deck.pk)))


def later(deck: Deck) -> bool:
    """Look the art up on the worker once the import is saved - if the file
    named any printing at all. Returns whether a lookup was started.

    `cache.add` is the lock: while one lookup is pending no second one is
    queued, however often the retry button is pressed.
    """
    if not any(_shape(p) for group in _groups(deck) for p in group):
        return False
    deck_id = str(deck.pk)
    if not cache.add(_key(deck_id), 1, PENDING_SECONDS):
        return False
    from decks.tasks import fetch_printing_art

    transaction.on_commit(lambda: fetch_printing_art.delay(deck_id))
    return True


def retry(deck: Deck) -> bool:
    """Ask again for the art Scryfall did not answer with - the deck page's
    retry button. Nothing happens for a deck with no such printing."""
    if not any(art_missing(group) for group in _groups(deck)):
        return False
    return later(deck)


def _groups(deck: Deck) -> list[list]:
    """Every card's printings: the deck's rows, then its commanders'."""
    rows = DeckCard.objects.filter(deck=deck).values_list("printings", flat=True)
    return [*rows, *(deck.commander_printings or {}).values()]


def _shape(printing: dict) -> tuple | None:
    """How one printing is named: `("id", id)`, `("number", set, number)`,
    `("set", set)`, or `None` when it names no printing.

    Every value is checked against its shape before it leaves the server:
    what a file says is data, and a set code is a few letters and digits.
    """
    scryfall_id = str(printing.get("id") or "").lower()
    set_code = str(printing.get("set") or "").lower()
    # As written: Scryfall matches `JMP-231` but not `jmp-231`.
    number = str(printing.get("number") or "")
    if _UUID.fullmatch(scryfall_id):
        return ("id", scryfall_id)
    if not _SET.fullmatch(set_code):
        return None
    if _NUMBER.fullmatch(number.lower()):
        return ("number", set_code, number)
    return ("set", set_code)


def _identifier(printing: dict, name: str) -> dict | None:
    """What to ask Scryfall for one printing, or `None`."""
    shape = _shape(printing)
    if shape is None:
        return None
    if shape[0] == "id":
        return {"id": shape[1]}
    if shape[0] == "number":
        return {"set": shape[1], "collector_number": shape[2]}
    return {"name": name, "set": shape[1]} if name else None


def _oracle_id(card: dict) -> str:
    """A Scryfall card's oracle id; a reversible card keeps it on its faces."""
    if card.get("oracle_id"):
        return str(card["oracle_id"])
    faces = card.get("card_faces") or [{}]
    return str(faces[0].get("oracle_id") or "")


def _index(cards: list[dict]) -> dict:
    """Scryfall's answer by every way a printing can be asked for."""
    index = {}
    for card in cards:
        image = image_uri(card)
        if not image.startswith(ART_HOST):
            continue
        oracle_id = _oracle_id(card)
        set_code = str(card.get("set") or "").lower()
        index[("id", str(card.get("id") or "").lower())] = (oracle_id, image)
        index[("number", oracle_id, set_code,
               str(card.get("collector_number") or "").lower())] = (oracle_id, image)
        index.setdefault(("set", oracle_id, set_code), (oracle_id, image))
    return index


def _image_for(printing: dict, oracle_id: str, index: dict) -> str:
    shape = _shape(printing)
    if shape is None:
        return ""
    if shape[0] == "number":
        key = ("number", oracle_id, shape[1], shape[2].lower())
    elif shape[0] == "set":
        key = ("set", oracle_id, shape[1])
    else:
        key = shape
    found = index.get(key)
    if found and found[0] == oracle_id:
        return found[1]
    return ""


def attach(deck: Deck) -> int:
    """Look up and keep the art of every printing the deck's file named.

    Returns how many printings got their art. Scryfall failing is logged and
    leaves the deck as it was.
    """
    entries = list(DeckCard.objects.filter(deck=deck).select_related("oracle_card"))
    command = deck.commander_printings or {}
    names = {str(entry.oracle_card_id): entry.oracle_card.name for entry in entries}
    names.update({str(card.pk): card.name for card in deck.commanders})

    groups = [(str(entry.oracle_card_id), entry.printings) for entry in entries]
    groups += [(oracle_id, printings) for oracle_id, printings in command.items()]
    identifiers = []
    for oracle_id, printings in groups:
        for printing in printings:
            asked = _identifier(printing, names.get(oracle_id, ""))
            if asked and asked not in identifiers:
                identifiers.append(asked)
    if not identifiers:
        return 0

    try:
        index = _index(scryfall.collection(identifiers))
    except (scryfall.ScryfallError, ValueError) as exc:
        logger.warning("printing art for deck %s not fetched: %s", deck.pk, exc)
        _save(deck, entries, command, lambda printings, oracle_id: _mark(
            printings, names.get(oracle_id, "")))
        return 0

    return _save(deck, entries, command,
                 lambda printings, oracle_id: _fill(printings, oracle_id, index))


def _save(deck: Deck, entries: list, command: dict, change) -> int:
    """Apply `change(printings, oracle id) -> (found, changed)` to every
    card's printings and save what changed. Returns how many were found."""
    found = 0
    rows = []
    for entry in entries:
        count, changed = change(entry.printings, str(entry.oracle_card_id))
        found += count
        if changed:
            rows.append(entry)
    DeckCard.objects.bulk_update(rows, ["printings"])
    command_changed = False
    for oracle_id, printings in command.items():
        count, changed = change(printings, oracle_id)
        found += count
        command_changed |= changed
    if command_changed:
        # `update`, not `save()`: art is not an edit, and `updated_at` orders
        # the deck list.
        Deck.objects.filter(pk=deck.pk).update(commander_printings=command)
    return found


def _fill(printings: list, oracle_id: str, index: dict) -> tuple[int, bool]:
    """Scryfall answered: keep each printing's art, and drop the mark of an
    earlier failure either way."""
    count, changed = 0, False
    for printing in printings:
        if printing.pop("art", None) is not None:
            changed = True
        if image := _image_for(printing, oracle_id, index):
            printing["image"] = image
            count += 1
            changed = True
    return count, changed


def _mark(printings: list, name: str) -> tuple[int, bool]:
    """Scryfall did not answer: mark the printings that were asked for."""
    changed = False
    for printing in printings:
        if (_identifier(printing, name) and not printing.get("image")
                and printing.get("art") != ART_MISSING):
            printing["art"] = ART_MISSING
            changed = True
    return 0, changed


def done(deck_id) -> None:
    """The lookup is over, found or not: the page stops waiting."""
    cache.delete(_key(deck_id))
