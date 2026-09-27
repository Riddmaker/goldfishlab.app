"""The one and only Commander Spellbook client.

Nothing outside this module may talk to Commander Spellbook. Same rule as
`cards/scryfall.py`, for the same reason: one choke point is what makes the
rate limit, the headers and the retry policy enforceable instead of
aspirational.

## Why there is no bulk mirror here

The phase plan said to stream-parse `variants.json` with `ijson` for a nightly
mirror. It was measured first, because trap 35 exists, and the measurement
argued against it:

| | |
|---|---|
| `variants.json` | **656.8 MB**, and the host offers **no gzip at all** |
| One record | 4,756 bytes, of which **`uses` is 3,350 - 70%** |
| What that 70% is | **Ten Scryfall image URLs per card**, five of them `null` |
| This endpoint | a whole 99-card deck in **78 KiB on the wire**, gzipped |

Three-quarters of the largest file this project would ever download is image
URLs for cards whose images are already in `OracleCard.image_uri`, and the one
field worth having - `uses[].card.oracleId` - joins straight to the catalogue.
So combos are fetched per deck and kept, and `combos.models.Combo` becomes a
mirror that accumulates from real demand rather than from a download.

## What the API actually answers, and the honesty trap in it

A combo is **named cards plus templates**. `Mikaeus, the Unhallowed` +
`Carrion Feeder` also needs *a creature with persist* - a category defined by a
Scryfall search, not a card anybody can name. A deck holding both named cards
and no persist creature **does not have that combo**, and Spellbook's own
`included` / `almostIncluded` split already accounts for it.

That is why `requires` is parsed and kept rather than dropped as detail. Saying
"your deck contains this combo" when a template requirement is unmet would be
the combo-detection version of reading 28 Swamps as one.

Why `urllib` and not `requests`: the production container is a 128 MiB
cloudlet, and this module needs one verb.
"""

import gzip
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from django.conf import settings

ENDPOINT = "https://backend.commanderspellbook.com/find-my-combos/"

#: Documented ceiling is roughly 80 requests a minute, which is one every
#: 0.75 s. We take a full second, the same way the Scryfall client takes the
#: slow end of Scryfall's own advice.
MIN_INTERVAL = 1.0

MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: The whole lookup, retries and waits included, has to finish inside this.
#: It runs inside a web request - the deck page's "Find combos" button - and
#: gunicorn kills a sync worker after 30 seconds. Until the 2026-09-25 review a
#: lookup could take 30 s per attempt for three attempts plus whatever
#: `Retry-After` asked for, so a Spellbook outage held both web workers until
#: gunicorn shot them, and the site with them.
DEADLINE_SECONDS = 20

#: One attempt's socket timeout, never more than what is left of the deadline.
ATTEMPT_TIMEOUT = 8

#: The longest `Retry-After` honoured. A server asking for more is told no by
#: giving up, which the panel reports as "could not be reached" - better than a
#: request that hangs until gunicorn kills it.
MAX_RETRY_WAIT = 3.0

#: A deck, not a collection. The largest legal Commander deck is 100 cards and
#: this project's own reference export is 214 rows; anything past this is a
#: caller bug, and sending it would be rude to somebody else's free API.
MAX_CARDS = 500

#: Ceiling on the *decompressed* response. The wire format is gzip and the body
#: comes from a third party, so an unbounded `GzipFile.read()` is a decompression
#: bomb waiting for a bad day. A 99-card deck decompresses to 842 KiB; 16 MB is
#: twenty times the worst real case and still a rounding error against the
#: memory budget.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

ALLOWED_SCHEMES = frozenset({"https"})

_last_request_at = 0.0


class SpellbookError(RuntimeError):
    """Commander Spellbook could not be reached, or answered unusably."""


@dataclass(frozen=True)
class CardRef:
    """One named card a combo uses."""

    oracle_id: str
    name: str
    quantity: int = 1
    must_be_commander: bool = False
    #: Where the card has to be: `B` battlefield, `G` graveyard, `H` hand,
    #: `E` exile, `L` library, `C` command zone. Kept raw and uninterpreted -
    #: it is Spellbook's vocabulary, not ours.
    zones: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateRef:
    """A *category* of card a combo needs, rather than a named one.

    `Persist Creature`, `Permanent Castable for {C}`. Defined by a Scryfall
    search, so it cannot be resolved against a deck by name - which is exactly
    why a combo whose templates are unmet is not a combo the deck has.
    """

    name: str
    quantity: int = 1
    scryfall_query: str = ""
    zones: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComboRecord:
    """One combo, with the 70% that is image URLs already gone."""

    spellbook_id: str
    identity: str = ""
    status: str = ""
    bracket_tag: str = ""
    mana_needed: str = ""
    mana_value_needed: int = 0
    notable_prerequisites: str = ""
    description: str = ""
    popularity: int = 0
    legal_commander: bool = True
    produces: tuple[str, ...] = ()
    cards: tuple[CardRef, ...] = ()
    templates: tuple[TemplateRef, ...] = ()

    @property
    def needs_templates(self) -> bool:
        return bool(self.templates)


@dataclass
class Results:
    """What one deck's lookup found.

    `out_of_identity` is a **count and not a list** on purpose. For the
    reference deck it is 112 combos that would work if the commander were a
    different colour, which is not advice about this deck - and storing it
    would triple the table to say "buy a different deck".
    """

    identity: str = ""
    included: list[ComboRecord] = field(default_factory=list)
    almost: list[ComboRecord] = field(default_factory=list)
    out_of_identity: int = 0
    needs_other_commanders: int = 0

    @property
    def total_seen(self) -> int:
        return (len(self.included) + len(self.almost)
                + self.out_of_identity + self.needs_other_commanders)


def _user_agent() -> str:
    return getattr(settings, "SPELLBOOK_USER_AGENT", None) or getattr(
        settings, "SCRYFALL_USER_AGENT", "GoldfishLab/0.1"
    )


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _read(response) -> bytes:
    """The body, decompressed, with a ceiling on how much of it we will take."""
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if response.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SpellbookError(
            f"response larger than {MAX_RESPONSE_BYTES // 1024 // 1024} MB; refusing to read on"
        )
    return raw


def _retry_after(headers) -> float:
    """`Retry-After` in seconds, if it is a number of seconds at all.

    The header may also be an HTTP date, and `float()` on one was a
    `ValueError` nothing caught - a 500 on the deck page whenever Spellbook
    answered 429 or 503 that way. A date is treated as "no advice".
    """
    try:
        return max(0.0, float(headers.get("Retry-After") or 0))
    except (TypeError, ValueError):
        return 0.0


def _post(payload: dict, *, timeout: int = ATTEMPT_TIMEOUT) -> dict:
    """A rate-limited, retrying POST with a deadline. Returns the decoded body."""
    scheme = urllib.parse.urlparse(ENDPOINT).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise SpellbookError(f"refusing to open URL with scheme {scheme!r}")

    body = json.dumps(payload).encode()
    headers = {
        "User-Agent": _user_agent(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }

    deadline = time.monotonic() + DEADLINE_SECONDS
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        # The scheme check above restricts this to https, which is what S310 asks.
        request = urllib.request.Request(ENDPOINT, data=body, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=min(timeout, remaining)
            ) as response:
                return json.loads(_read(response))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES:
                # A 400 here is a bug in the payload we sent. Retrying it only
                # makes the bug slower to find, and rude while it does.
                raise SpellbookError(f"HTTP {exc.code} from Spellbook: {exc.reason}") from exc
            wait = _retry_after(exc.headers) or 2**attempt
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            wait = 2**attempt
        except json.JSONDecodeError as exc:
            raise SpellbookError(
                f"Spellbook answered with something that is not JSON: {exc}"
            ) from exc
        wait = min(wait, MAX_RETRY_WAIT, max(0.0, deadline - time.monotonic() - 1))
        if wait:
            time.sleep(wait)

    raise SpellbookError(f"giving up after {MAX_RETRIES} attempts: {last_error}")


def find_my_combos(main, commanders=(), *, timeout: int = ATTEMPT_TIMEOUT) -> Results:
    """Which combos a list of card names contains, and which it nearly does.

    Names rather than ids because that is what the endpoint takes. They come
    from `OracleCard.front_name`, so they are Scryfall's own spelling.
    """
    main = list(main)
    commanders = list(commanders)
    if len(main) + len(commanders) > MAX_CARDS:
        raise SpellbookError(
            f"{len(main) + len(commanders)} cards is more than a deck; the limit is {MAX_CARDS}"
        )
    if not main and not commanders:
        return Results()

    payload = {
        "commanders": [{"card": name, "quantity": 1} for name in commanders],
        "main": [{"card": name, "quantity": 1} for name in main],
    }
    return parse(_post(payload, timeout=timeout))


def parse(payload: dict) -> Results:
    """Turn one API response into the part of it worth keeping.

    Separate from the request so the committed fixture can be run through the
    identical code path without a network, and so a change in what we keep is
    a change to one pure function.
    """
    results = payload.get("results", payload) or {}
    return Results(
        identity=str(results.get("identity") or ""),
        included=[_record(v) for v in results.get("included") or []],
        almost=[_record(v) for v in results.get("almostIncluded") or []],
        out_of_identity=len(results.get("almostIncludedByAddingColors") or []),
        needs_other_commanders=(
            len(results.get("includedByChangingCommanders") or [])
            + len(results.get("almostIncludedByChangingCommanders") or [])
            + len(results.get("almostIncludedByAddingColorsAndChangingCommanders") or [])
        ),
    )


def _record(variant: dict) -> ComboRecord:
    legalities = variant.get("legalities") or {}
    return ComboRecord(
        spellbook_id=str(variant.get("id") or "")[:32],
        identity=str(variant.get("identity") or "")[:8],
        status=str(variant.get("status") or "")[:16],
        bracket_tag=str(variant.get("bracketTag") or "")[:8],
        mana_needed=str(variant.get("manaNeeded") or "")[:64],
        mana_value_needed=_int(variant.get("manaValueNeeded")),
        notable_prerequisites=str(variant.get("notablePrerequisites") or "")[:2000],
        description=str(variant.get("description") or "")[:4000],
        popularity=_int(variant.get("popularity")),
        # One boolean out of a 320-byte object listing every format there is.
        legal_commander=bool(legalities.get("commander", True)),
        produces=tuple(
            str((p.get("feature") or {}).get("name") or "")[:128]
            for p in variant.get("produces") or []
        ),
        cards=tuple(_card(u) for u in variant.get("uses") or []),
        templates=tuple(_template(t) for t in variant.get("requires") or []),
    )


def _card(use: dict) -> CardRef:
    card = use.get("card") or {}
    return CardRef(
        # The only field of `card` worth a byte: everything else is a name we
        # already have or an image URL we already have.
        oracle_id=str(card.get("oracleId") or "")[:64],
        name=str(card.get("name") or "")[:256],
        quantity=_int(use.get("quantity")) or 1,
        must_be_commander=bool(use.get("mustBeCommander")),
        zones=tuple(str(z)[:4] for z in use.get("zoneLocations") or []),
    )


def _template(requirement: dict) -> TemplateRef:
    template = requirement.get("template") or {}
    return TemplateRef(
        name=str(template.get("name") or "")[:128],
        quantity=_int(requirement.get("quantity")) or 1,
        scryfall_query=str(template.get("scryfallApi") or "")[:512],
        zones=tuple(str(z)[:4] for z in requirement.get("zoneLocations") or []),
    )


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
