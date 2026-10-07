"""Reading Commander precon lists from MTGJSON (P11).

MTGJSON (https://mtgjson.com, MIT licence) publishes every preconstructed
deck as a JSON file, with each card's Scryfall oracle id - which is what
`decks.resolve` matches on first, so a precon's cards are found by id, never
by a guessed name. Three files are read:

- `DeckList.json`, every deck with its type, set code and release date;
- `decks/<fileName>.json`, one deck's commander and main board;
- `SetList.json` (11 MB), only when a set's name is not known yet.

`source` is a local directory with the same layout instead of the web: the
tests read one, and so can a machine whose Python cannot reach the site.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings

from decks.importers.base import ParsedRow

#: Precons released on or after this day get a page (the launch plan's
#: "every Commander precon since 2025").
SINCE = date(2025, 1, 1)
DECK_TYPE = "Commander Deck"
#: Secret Lair's Commander decks are limited drops, not precons in a store.
EXCLUDED_SETS = frozenset({"SLD"})

TIMEOUT_SECONDS = 60
RETRIES = 3


class MTGJSONError(Exception):
    """MTGJSON could not be read; nothing was changed."""


@dataclass(frozen=True)
class Listing:
    """One deck in `DeckList.json`."""

    file_name: str
    name: str
    set_code: str
    released: date


def base_url() -> str:
    return settings.MTGJSON_URL.rstrip("/") + "/"


def _read(path: str, source: Path | None) -> dict:
    if source is not None:
        try:
            return json.loads((source / path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MTGJSONError(f"cannot read {source / path}: {exc}") from exc
    url = urllib.parse.urljoin(base_url(), path)
    if urllib.parse.urlparse(url).scheme != "https":
        raise MTGJSONError(f"refusing to open {url}: not https")
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url, headers={"User-Agent": settings.SCRYFALL_USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise MTGJSONError(f"HTTP {exc.code} from {url}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        time.sleep(2**attempt)
    raise MTGJSONError(f"giving up on {url}: {last_error}")


def is_precon(entry: dict) -> bool:
    """A store Commander precon from `SINCE` on - not its Collector's Edition,
    which is the same list in foil, and not a Secret Lair."""
    return (
        entry.get("type") == DECK_TYPE
        and entry.get("code", "").upper() not in EXCLUDED_SETS
        and "collector" not in entry.get("name", "").casefold()
        and date.fromisoformat(entry["releaseDate"]) >= SINCE
    )


def listings(source: Path | None = None) -> list[Listing]:
    """Every precon `is_precon` keeps, oldest first."""
    data = _read("DeckList.json", source).get("data", [])
    found = [
        Listing(file_name=entry["fileName"], name=entry["name"],
                set_code=entry["code"].upper(),
                released=date.fromisoformat(entry["releaseDate"]))
        for entry in data if is_precon(entry)
    ]
    return sorted(found, key=lambda listing: (listing.released, listing.name))


def deck(listing: Listing, source: Path | None = None) -> dict:
    """The deck file's `data`: `commander` and `mainBoard` are what is read."""
    return _read(f"decks/{listing.file_name}.json", source)["data"]


def rows(data: dict) -> list[ParsedRow]:
    """The deck as importer rows: the commander marked, every card with its
    oracle id, so `decks.resolve` matches by id."""
    found = []
    for board, is_commander in (("commander", True), ("mainBoard", False)):
        for card in data.get(board) or []:
            found.append(ParsedRow(
                line_number=len(found) + 1,
                name=card["name"],
                quantity=int(card.get("count", 1)),
                oracle_id=(card.get("identifiers") or {}).get("scryfallOracleId", ""),
                is_commander=is_commander,
            ))
    return found


def set_names(source: Path | None = None) -> dict[str, str]:
    """{set code: set name} for every set MTGJSON knows."""
    return {entry["code"].upper(): entry["name"]
            for entry in _read("SetList.json", source).get("data", [])}
