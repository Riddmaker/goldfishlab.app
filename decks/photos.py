"""A deck from photos (B-14): read the card titles on a few photos.

The way a photo goes, and the only way:

1. **Upload, in memory only.** `MemoryOnlyUploadHandler` keeps the files in
   RAM and stops the request past `MAX_TOTAL_BYTES`; Django's own handlers
   would write anything over 2.5 MB to a temporary file. The page shrinks the
   photos in the browser first, so this is the ceiling for a page without
   scripting, not the usual size.
2. **Checked and redrawn.** Pillow, not the file name, decides the type
   (`FORMATS`); a picture with more than `MAX_PIXELS` is refused before it is
   decoded (a decompression bomb). It is turned upright, shrunk to `EDGE`
   pixels and written anew as a JPEG: no EXIF, no GPS, nothing of the
   original file but its pixels.
3. **Read by Mistral**, one call per photo, side by side, on the EU endpoint
   (`simulations.mistral`). The model names the cards; it is never believed:
   every name goes through the import's own ladder (`decks.resolve`), so a name
   that is not a card stays an open row with suggestions.
4. **Dropped.** Nothing keeps a photo: not the database, not a cache, not a
   log. An error names the photo by its number, never its content.
5. **Checked by the person** (PR 2): what was read waits as a
   `PendingImport` of kind "photos" - card names and ids, no picture - until
   the person has corrected it, then goes through the ordinary import as a
   text list (`deck_text`), so a deck from photos is made like any other.

A Commander deck has one of each card but basic lands (and the few cards that
say a deck may have any number), so a card seen twice - in a fan, or on two
photos - is counted once.
"""

import base64
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.conf import settings
from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

from cards.models import OracleCard
from decks import resolve
from decks.analysis import unlimited
from decks.importers.base import ParsedRow
from decks.models import PendingImport
from simulations import mistral

logger = logging.getLogger(__name__)

#: Photos per run, and what a run may upload in all.
MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
#: Pillow's own bomb warning starts at about 89 million; a phone takes 12 to
#: 50 million. More than this is not a photo of cards.
MAX_PIXELS = 60_000_000
#: A PNG or WebP is decoded at full size (only a JPEG shrinks while it
#: decodes), at four bytes a pixel: 16 million is 64 MB at the peak, which a
#: web worker can carry; 60 million would not be.
MAX_PIXELS_DECODED_WHOLE = 16_000_000
FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
#: The long edge sent to the model. On synthetic photos (2026-11-25) Small
#: read every title of nine cards in a grid at this size.
EDGE = 1600
JPEG_QUALITY = 85

#: What one answer may carry: no photo shows more cards than this, and no card
#: name is longer.
CARDS_MAX = 120
NAME_MAX = 100
COUNT_MAX = 99

PROMPT = (
    "The photo shows Magic: The Gathering cards. List every card whose title you can read. "
    "For a card printed in another language, give its English Oracle name. Count copies of the "
    "same card. Leave out any card whose title you cannot read; never guess. Answer only JSON: "
    '{"cards": [{"name": "English name", "printed": "title as printed", "count": 1}]}'
)


class PhotoRefused(ValueError):
    """A photo this page does not take; the message is said to the person."""


class MemoryOnlyUploadHandler(MemoryFileUploadHandler):
    """Django's in-memory handler without its size threshold, and with a ceiling.

    Django's in-memory handler takes a file only below
    `FILE_UPLOAD_MAX_MEMORY_SIZE` and leaves the rest to the temporary-file
    handler; installed alone, this one takes every file and stops the whole
    request once `MAX_TOTAL_BYTES` have come in, saying so in `stopped`: Django
    then hands over only the files that were complete, and the view must not
    read those as if they were all.

    A declared length past the ceiling is the view's to refuse (`too_large`),
    before the body is touched: raised from `handle_raw_input`, the stop is
    not caught by Django's parser, and the person would see a 500.
    """

    stopped = False

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        self.received = 0
        self.activated = True

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > MAX_TOTAL_BYTES:
            self.stopped = True
            raise StopUpload(connection_reset=True)
        return super().receive_data_chunk(raw_data, start)


def too_large(request) -> bool:
    """Does the request declare more than an upload may carry?"""
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return False
    return declared > MAX_TOTAL_BYTES + 64 * 1024


def prepare(upload) -> bytes:
    """One upload, checked and redrawn: a small JPEG with nothing but pixels.

    Raises:
        PhotoRefused: too large, not a picture, or not one of `FORMATS`.
    """
    if upload.size > MAX_PHOTO_BYTES:
        raise PhotoRefused(_("A photo may be at most 10 MB."))
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in FORMATS:
                raise PhotoRefused(_("Only JPEG, PNG and WebP photos can be read."))
            width, height = image.size
            ceiling = MAX_PIXELS if image.format == "JPEG" else MAX_PIXELS_DECODED_WHOLE
            if width * height > ceiling:
                raise PhotoRefused(_("This photo has too many pixels to be a photo of cards."))
            # A JPEG decodes straight at a fraction of its size: a 12
            # megapixel photo never takes its full 36 MB in memory. Shrunk
            # in place before it is turned or converted, so no full-size copy
            # is ever made; the box is square, so turning after is the same.
            image.draft("RGB", (EDGE, EDGE))
            image.thumbnail((EDGE, EDGE))
            picture = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise PhotoRefused(_("This file is not a photo that can be read.")) from None
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def _one_line(text: str) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())


def messages(jpeg: bytes) -> list[dict]:
    """The chat for one photo: the instruction and the picture, nothing else."""
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": data_url},
    ]}]


@dataclass(frozen=True)
class Seen:
    """One card the model named on one photo."""

    name: str
    printed: str
    count: int


def parse(content: str) -> list[Seen]:
    """The model's answer as cards; anything not shaped as asked is left out."""
    try:
        cards = json.loads(content).get("cards")
    except (ValueError, AttributeError):
        return []
    if not isinstance(cards, list):
        return []
    seen = []
    for item in cards[:CARDS_MAX]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        # One line, no control characters: the name ends up as a line of
        # the deck list, and a newline in it would add cards nobody saw.
        name = _one_line(item["name"])[:NAME_MAX]
        if not name:
            continue
        printed = item.get("printed")
        printed = _one_line(printed)[:NAME_MAX] if isinstance(printed, str) else ""
        count = item.get("count")
        count = min(max(count, 1), COUNT_MAX) if isinstance(count, int) else 1
        seen.append(Seen(name, printed, count))
    return seen


def model() -> str:
    return getattr(settings, "MISTRAL_PHOTO_MODEL", "") or "mistral-small-latest"


def ask(jpeg: bytes) -> list[Seen]:
    """One photo read by the model.

    Raises:
        mistral.MistralError: not reached, or no answer in time.
    """
    completion = mistral.complete(messages(jpeg), max_tokens=3000, temperature=0,
                                  model_name=model(), effort="",
                                  timeout=mistral.QUICK_TIMEOUT_SECONDS, retry=False)
    return parse(completion.content)


@dataclass
class Reading:
    """What the photos gave: the cards as the import resolves them, and how
    many photos could not be read."""

    report: resolve.ResolutionReport
    photos: int
    failed: list[int]

    @property
    def found_any(self) -> bool:
        return bool(self.report.resolutions)


def read(jpegs: list[bytes]) -> Reading:
    """Every photo read side by side, then resolved and merged into one list.

    A photo that could not be read is named by its number in `failed`; only
    when none could does this raise, so a run with one bad photo still gives
    the cards of the others.

    Raises:
        mistral.MistralError: no photo could be read.
    """
    results: list[list[Seen] | None] = [None] * len(jpegs)
    failed = []
    # One thread per photo, so the slowest photo, not their sum, is the wait:
    # each call ends within `QUICK_TIMEOUT_SECONDS`, well inside a request.
    with ThreadPoolExecutor(max_workers=max(1, len(jpegs))) as pool:
        futures = [pool.submit(ask, jpeg) for jpeg in jpegs]
        for number, future in enumerate(futures, start=1):
            try:
                results[number - 1] = future.result()
            except mistral.MistralError as exc:
                logger.warning("photo %d of %d not read: %s", number, len(jpegs), exc)
                failed.append(number)
    if jpegs and len(failed) == len(jpegs):
        raise mistral.MistralError("no photo could be read")
    seen = [item for result in results if result for item in result]
    return Reading(report=_resolve(seen), photos=len(jpegs), failed=failed)


def _resolve(seen: list[Seen]) -> resolve.ResolutionReport:
    """The names through the import's ladder, the printed title as a second
    try, then one row per card."""
    rows = [ParsedRow(line_number=i, name=item.name, quantity=item.count,
                      extra={"printed": item.printed})
            for i, item in enumerate(seen, start=1)]
    report = resolve.resolve(rows)
    retry = [r for r in report.unresolved if r.row.extra.get("printed")
             and r.row.extra["printed"].casefold() != r.row.name.casefold()]
    if retry:
        second = resolve.resolve([ParsedRow(line_number=r.row.line_number,
                                            name=r.row.extra["printed"],
                                            quantity=r.row.quantity)
                                  for r in retry])
        by_line = {r.row.line_number: r for r in second.resolved}
        report = resolve.ResolutionReport([
            _keep_name(by_line.get(r.row.line_number), r) for r in report.resolutions])
    return _merge(report)


def _keep_name(found: resolve.Resolution | None, original: resolve.Resolution):
    if found is None:
        return original
    found.row = original.row
    return found


def _merge(report: resolve.ResolutionReport) -> resolve.ResolutionReport:
    """One row per card (or per unknown name), counted as a deck counts it."""
    merged: dict[str, resolve.Resolution] = {}
    for resolution in report.resolutions:
        key = (str(resolution.card.pk) if resolution.card
               else "?" + resolution.row.name.casefold())
        if key in merged:
            merged[key].row.quantity += resolution.row.quantity
        else:
            merged[key] = resolution
    # A card seen on two photos is one card; so is a name nobody could
    # resolve, which is far likelier a singleton than a basic land.
    for resolution in merged.values():
        if resolution.card is None or not unlimited(resolution.card):
            resolution.row.quantity = 1
    return resolve.ResolutionReport(list(merged.values()))


# --- PR 2: what was read, waiting to be checked -------------------------------

#: How many other cards "Not this card" offers for one row.
NEIGHBOURS = 6
#: A row's choice that keeps the name as read: the import lists it as not
#: found, with its suggestions, rather than drop it.
KEEP_AS_READ = "?"


def hold(owner, reading: Reading) -> PendingImport:
    """What the photos showed, parked for the person to check; one per person,
    a second run replaces the first (`decks.services.hold`)."""
    rows = [{
        "name": r.row.name,
        "printed": r.row.extra.get("printed", ""),
        "quantity": r.row.quantity,
        "card": str(r.card.pk) if r.card else None,
        "suggestions": r.suggestions,
    } for r in reading.report.resolutions]
    PendingImport.objects.filter(owner=owner, kind=PendingImport.Kind.PHOTOS).delete()
    return PendingImport.objects.create(
        owner=owner, kind=PendingImport.Kind.PHOTOS, parser="photos",
        text=json.dumps({"photos": reading.photos, "failed": reading.failed, "rows": rows}))


@dataclass
class Row:
    """One line of the check list: what was read, and what the person chose."""

    index: int
    name: str
    printed: str
    card: OracleCard | None
    suggestions: list[OracleCard]
    #: A card's id, `KEEP_AS_READ`, or "" to leave the row out.
    chosen: str = ""
    quantity: int = 1
    #: "Not this card" was pressed: other cards are offered.
    opened: bool = False
    neighbours: list[OracleCard] | None = None

    @property
    def found(self) -> bool:
        return self.card is not None

    @property
    def options(self) -> list[tuple[str, OracleCard, bool]]:
        """`(value, card, chosen)` for each card this row may be: the card
        found, the suggestions, and after "Not this card" its neighbours."""
        cards = [self.card] if self.card else []
        seen, out = set(), []
        for card in [*cards, *self.suggestions, *(self.neighbours or [])]:
            if card.pk not in seen:
                seen.add(card.pk)
                out.append((str(card.pk), card, self.chosen == str(card.pk)))
        return out

    @property
    def keeps_card(self) -> bool:
        return bool(self.card) and self.chosen == str(self.card.pk)

    @property
    def kept_as_read(self) -> bool:
        return self.chosen == KEEP_AS_READ

    @property
    def read_differently(self) -> bool:
        """The card found is not the name as read: the person should see what was read."""
        return bool(self.card and self.card.name.casefold() != self.name.casefold()
                    and self.card.front_name.casefold() != self.name.casefold())


@dataclass
class Held:
    photos: int
    failed: list[int]
    rows: list[Row]


def load(pending: PendingImport) -> Held:
    """The parked reading, its cards fetched in one query."""
    data = json.loads(pending.text)
    names = {name for row in data["rows"] for name in row["suggestions"]}
    ids = {row["card"] for row in data["rows"] if row["card"]}
    cards = OracleCard.objects.filter(Q(pk__in=ids) | Q(name__in=names))
    by_id = {str(card.pk): card for card in cards}
    by_name = {card.name: card for card in cards}
    rows = []
    for index, row in enumerate(data["rows"]):
        card = by_id.get(row["card"] or "")
        rows.append(Row(
            index=index, name=row["name"], printed=row["printed"], card=card,
            suggestions=[by_name[name] for name in row["suggestions"] if name in by_name],
            chosen=str(card.pk) if card else KEEP_AS_READ, quantity=row["quantity"]))
    # The rows to look at first: what was not found, then what was read
    # differently from the card found, then the rest by name.
    rows.sort(key=lambda row: (row.found, not row.read_differently,
                               (row.card.name if row.card else row.name).casefold()))
    return Held(photos=data["photos"], failed=data["failed"], rows=rows)


def neighbours(row: Row) -> list[OracleCard]:
    """Other cards a row may have meant: near the name as read and as
    printed, the card found left out."""
    names = []
    for name in (row.name, row.printed):
        for found in resolve.suggest(name, limit=NEIGHBOURS) if name else []:
            if found not in names:
                names.append(found)
    cards = {card.name: card for card in OracleCard.objects.filter(name__in=names)}
    found = [cards[name] for name in names if name in cards and cards[name] != row.card]
    return found[:NEIGHBOURS]


def deck_text(rows: list[Row], added: list[OracleCard]) -> str:
    """The checked list as the text importer reads it, one card per line."""
    by_id = {str(card.pk): card for row in rows
             for card in [row.card, *row.suggestions, *(row.neighbours or [])] if card}
    lines = []
    for row in rows:
        if row.chosen == KEEP_AS_READ:
            lines.append(f"{row.quantity} {row.name}")
        elif row.chosen:
            card = by_id.get(row.chosen) or OracleCard.objects.filter(pk=row.chosen).first()
            if card is not None:
                lines.append(f"{row.quantity} {card.name}")
    lines += [f"1 {card.name}" for card in added]
    return "\n".join(lines)


__all__ = ["KEEP_AS_READ", "MAX_PHOTOS", "MemoryOnlyUploadHandler", "PhotoRefused", "Reading",
           "Row", "Seen", "deck_text", "hold", "load", "messages", "neighbours", "parse",
           "prepare", "read"]
