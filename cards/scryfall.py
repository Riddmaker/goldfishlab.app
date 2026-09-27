"""The one and only Scryfall client.

Nothing outside this module may talk to Scryfall. One choke point is what makes
the rate limit, the mandatory headers and the retry policy enforceable instead
of aspirational.

Three traps, all of them already paid for during the deck research:

1. **`User-Agent` *and* `Accept` are both mandatory.** Omit either and
   `/cards/collection` answers HTTP 400, not 403 - so it reads like a malformed
   body, and you go looking in the wrong place.
2. **Stay under 10 requests/second.** Scryfall asks for 50-100 ms between
   requests; `MIN_INTERVAL` is 100 ms and is enforced process-wide.
3. **Bulk, never per-card, for the catalogue.** The per-card endpoints are for
   filling gaps during an import. Walking 38,000 cards through them would take
   an hour and is exactly what the bulk files exist to prevent.

Why `urllib` and not `requests`/`httpx`: the production container runs on a
128 MiB cloudlet, and this module needs three verbs. A dependency that has to
be audited, pinned and patched forever should buy more than `.json()`.
"""

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils.dateparse import parse_datetime

BULK_ENDPOINT = "https://api.scryfall.com/bulk-data"
COLLECTION_ENDPOINT = "https://api.scryfall.com/cards/collection"

# Scryfall's documented ceiling is 10 requests/second; they ask for 50-100 ms
# of delay. We take the slow end of their own advice.
MIN_INTERVAL = 0.1

# `/cards/collection` rejects more than 75 identifiers per request.
COLLECTION_BATCH = 75

MAX_RETRIES = 4
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Only these two schemes are ever opened. The guard exists because `--source`
# accepts an operator-supplied string, and urllib would happily open file://
# or ftp:// with it.
ALLOWED_SCHEMES = frozenset({"https"})

_last_request_at = 0.0


class ScryfallError(RuntimeError):
    """Scryfall could not be reached, or answered with something unusable."""


@dataclass(frozen=True)
class BulkMeta:
    """The descriptor for one bulk file."""

    kind: str
    updated_at: datetime
    download_uri: str
    compressed_size: int

    @property
    def size_mb(self) -> float:
        return self.compressed_size / 1_000_000


def _user_agent() -> str:
    return getattr(settings, "SCRYFALL_USER_AGENT", "GoldfishLab/0.1")


def _throttle() -> None:
    """Sleep just long enough to stay under the rate limit."""
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _check_scheme(url: str) -> None:
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise ScryfallError(f"refusing to open URL with scheme {scheme!r}: {url}")


def _open(url: str, *, accept: str, data: bytes | None = None, timeout: int = 60):
    """A rate-limited, retrying request. Returns an open response.

    Retries only on 429 and 5xx. A 400 or 404 is a bug in the caller and
    retrying it just makes the bug slower to find.
    """
    _check_scheme(url)
    headers = {"User-Agent": _user_agent(), "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        # _check_scheme above restricts this to https, which is what S310 asks for.
        request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
        try:
            return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES:
                raise ScryfallError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
            # Honour Retry-After when Scryfall sends one, else exponential.
            wait = float(exc.headers.get("Retry-After") or 0) or 2**attempt
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_error = exc
            time.sleep(2**attempt)

    raise ScryfallError(f"giving up on {url} after {MAX_RETRIES} attempts: {last_error}")


def bulk_metadata(kind: str) -> BulkMeta:
    """Describe one bulk file, without downloading it.

    `updated_at` is the idempotency key: unchanged means there is nothing to do,
    and the 24 MB never leaves Scryfall's CDN.
    """
    with _open(BULK_ENDPOINT, accept="application/json") as response:
        payload = json.load(response)

    for entry in payload.get("data", []):
        if entry.get("type") != kind:
            continue
        updated = parse_datetime(entry["updated_at"])
        if updated is None:
            raise ScryfallError(f"unparseable updated_at for {kind}: {entry['updated_at']!r}")
        # Scryfall serves JSONL only; there is no plain-JSON download any more.
        return BulkMeta(
            kind=kind,
            updated_at=updated,
            download_uri=entry["jsonl_download_uri"],
            compressed_size=int(entry.get("compressed_size") or 0),
        )

    available = [entry.get("type") for entry in payload.get("data", [])]
    raise ScryfallError(f"no bulk data of type {kind!r}; available: {available}")


def stream_jsonl(source: str | Path, *, timeout: int = 300) -> Iterator[dict]:
    """Yield one parsed object per line from a gzipped JSONL source.

    `source` is an https URL or a local path - the local form exists so tests
    and repeat development runs work from a committed fixture instead of
    hammering the CDN.

    The whole point of JSONL over the monolithic JSON file: peak memory is one
    line, not one file. Never replace this with `json.load()`.
    """
    path = Path(source) if not str(source).startswith("http") else None

    if path is not None:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield from _parse_lines(handle)
        return

    with _open(str(source), accept="*/*", timeout=timeout) as response:
        # Content-Type is application/gzip with no Content-Encoding header, so
        # urllib does not decompress it for us and GzipFile must.
        with gzip.GzipFile(fileobj=response) as raw:
            yield from _parse_lines(line.decode("utf-8") for line in raw)


def download(uri: str, dest: Path, *, timeout: int = 300) -> int:
    """Copy a bulk file to disk, in 1 MB chunks. Returns bytes written.

    Used when a file has to be read more than once: three passes over a local
    6 MB copy beat three trips to the CDN, and beat holding it in memory.
    """
    written = 0
    with _open(uri, accept="*/*", timeout=timeout) as response, open(dest, "wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
            written += len(chunk)
    return written


def _parse_lines(lines) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if line:
            yield json.loads(line)


def collection(identifiers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Resolve up to 75 card identifiers at a time.

    Used only to fill gaps left by an import, never to load the catalogue.
    Returns `(found, not_found)`.
    """
    found: list[dict] = []
    missing: list[dict] = []

    for start in range(0, len(identifiers), COLLECTION_BATCH):
        chunk = identifiers[start : start + COLLECTION_BATCH]
        body = json.dumps({"identifiers": chunk}).encode("utf-8")
        with _open(COLLECTION_ENDPOINT, accept="application/json", data=body) as response:
            payload = json.load(response)
        found.extend(payload.get("data", []))
        missing.extend(payload.get("not_found", []))

    return found, missing
