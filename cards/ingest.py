"""Streaming ingestion of the Scryfall bulk files.

The design constraint that shapes everything here: **peak memory must stay
around 30-50 MB regardless of file size**, because production is a 128 MiB
cloudlet that also has to hold Django, gunicorn and a Postgres connection. That
is the entire reason the JSONL files are used instead of the monolithic JSON
ones, and the reason nothing in this module ever holds a whole file.

The one thing deliberately held in memory is the tag DAG itself: 4,544 tags
with their edges, a few hundred kilobytes. The *taggings* (236,000 of them) are
never held - they are streamed past three times from a local copy of the file.
The printing ingestion holds one comparable thing, the set of known oracle ids,
for the same reason and at the same scale.

Three files, and **the order between them is a dependency, not a preference**:
`oracle_cards` first, then `default_cards` and `oracle_tags`, both of which
carry a foreign key to a card. Rows whose card is missing are counted as
skipped rather than raising.

Idempotency lives in `BulkImport.scryfall_updated_at`. A run whose timestamp
already has a successful row downloads nothing at all, which is what makes this
safe to call on boot and from a nightly beat task.
"""

import contextlib
import tempfile
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.db import reset_queries, transaction
from django.utils import timezone

from cards import scryfall
from cards.models import BulkImport, OracleCard, OracleCardTag, Printing, Tag, TagEdge

# Rows that are not cards anyone can put in a deck. They carry oracle_ids and
# would import cleanly, they are just noise: 2,243 art series pieces and 913
# token faces that no deck list will ever reference.
NON_CARD_LAYOUTS = frozenset({"art_series", "token", "double_faced_token", "emblem"})

CARD_BATCH = 1_000
TAG_LINK_BATCH = 5_000
# Printings are narrow rows - no oracle text, no legalities blob - so a larger
# batch costs little memory and saves round trips over 112,581 of them.
PRINTING_BATCH = 2_000

# Columns refreshed when a card already exists. Everything except the key.
_CARD_UPDATE_FIELDS = [
    "name", "front_name", "search_name", "mana_cost", "cmc", "type_line",
    "oracle_text", "colors", "color_identity", "produced_mana", "keywords",
    "layout", "power", "toughness", "loyalty", "legalities", "game_changer",
    "reserved", "edhrec_rank", "released_at", "scryfall_id", "scryfall_uri", "image_uri",
    "imported_at",
]

# Same idea for printings: everything except the key. `prices_updated_at` is in
# the list because a re-ingest is usually a price refresh and nothing else, and
# a price whose date did not move is a price nobody can trust.
_PRINTING_UPDATE_FIELDS = [
    "oracle_card", "set_code", "set_name", "collector_number", "rarity", "lang",
    "released_at", "finishes", "digital", "promo", "image_uri",
    "price_eur", "price_eur_foil", "prices_updated_at", "imported_at",
]


@dataclass
class IngestResult:
    """What one ingestion run did."""

    kind: str
    status: str
    rows_seen: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    peak_memory_kb: int | None = None
    message: str = ""

    @property
    def skipped(self) -> bool:
        return self.status == BulkImport.Status.SKIPPED

    def __str__(self) -> str:
        if self.skipped:
            return f"{self.kind}: skipped, {self.message}"
        memory = f", peak {self.peak_memory_kb / 1024:.1f} MB" if self.peak_memory_kb else ""
        return (
            f"{self.kind}: {self.rows_written} written, {self.rows_skipped} skipped, "
            f"{self.rows_seen} seen{memory}"
        )


@contextlib.contextmanager
def _local_copy(source: str | Path) -> Iterator[Path]:
    """Yield a local path for `source`, downloading it once if it is a URL.

    The tag ingestion reads its file three times (metadata, direct taggings,
    rolled-up taggings). Streaming from the CDN three times would be rude and
    slow; holding it in memory would blow the budget. A 6 MB temp file is the
    cheap third option.
    """
    if not str(source).startswith("http"):
        yield Path(source)
        return

    handle = tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False)
    handle.close()
    try:
        scryfall.download(str(source), Path(handle.name))
        yield Path(handle.name)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _resolve_source(kind: str, source: str | Path | None, updated_at: datetime | None):
    """Work out where to read from and which timestamp to treat as the version."""
    if source is None:
        meta = scryfall.bulk_metadata(kind)
        return meta.download_uri, meta.updated_at

    if updated_at is None:
        # A local file stands in for a bulk download; its mtime is the only
        # version marker it has.
        mtime = Path(source).stat().st_mtime
        updated_at = datetime.fromtimestamp(mtime, tz=UTC)
    return source, updated_at


def _already_done(kind: str, updated_at: datetime) -> bool:
    return BulkImport.objects.filter(
        kind=kind, scryfall_updated_at=updated_at, status=BulkImport.Status.OK
    ).exists()


@contextlib.contextmanager
def _run(
    kind: str, updated_at: datetime, measure: bool
) -> Iterator[tuple[BulkImport, IngestResult]]:
    """Bookkeeping around one ingestion: the BulkImport row and memory metering."""
    record = BulkImport.objects.create(kind=kind, scryfall_updated_at=updated_at)
    result = IngestResult(kind=kind, status=BulkImport.Status.OK)
    if measure:
        tracemalloc.start()
    try:
        yield record, result
    except Exception as exc:
        record.status = BulkImport.Status.FAILED
        record.message = f"{type(exc).__name__}: {exc}"[:2000]
        record.finished_at = timezone.now()
        record.save(update_fields=["status", "message", "finished_at"])
        raise
    else:
        if measure:
            _, peak = tracemalloc.get_traced_memory()
            result.peak_memory_kb = peak // 1024
        record.status = result.status
        record.rows_seen = result.rows_seen
        record.rows_written = result.rows_written
        record.rows_skipped = result.rows_skipped
        record.peak_memory_kb = result.peak_memory_kb
        record.message = result.message[:2000]
        record.finished_at = timezone.now()
        record.save()
    finally:
        if measure:
            tracemalloc.stop()


def ingest_cards(
    *,
    source: str | Path | None = None,
    updated_at: datetime | None = None,
    force: bool = False,
    limit: int | None = None,
    measure: bool = False,
) -> IngestResult:
    """Load `oracle_cards` into `OracleCard`.

    Existing rows are updated in place, so a card whose oracle text was errataed
    is corrected without breaking the decks that reference it.
    """
    kind = BulkImport.Kind.ORACLE_CARDS
    location, version = _resolve_source(kind, source, updated_at)

    if not force and _already_done(kind, version):
        return IngestResult(
            kind=kind,
            status=BulkImport.Status.SKIPPED,
            message=f"already imported {version:%Y-%m-%d %H:%M}",
        )

    with _run(kind, version, measure) as (_record, result):
        batch: list[OracleCard] = []
        for row in scryfall.stream_jsonl(location):
            result.rows_seen += 1
            if limit is not None and result.rows_seen > limit:
                result.rows_seen -= 1
                break

            if row.get("layout") in NON_CARD_LAYOUTS or not row.get("oracle_id"):
                result.rows_skipped += 1
                continue

            batch.append(OracleCard.from_scryfall(row))
            if len(batch) >= CARD_BATCH:
                result.rows_written += _flush_cards(batch)
                batch = []

        result.rows_written += _flush_cards(batch)

    return result


def _flush_cards(batch: list[OracleCard]) -> int:
    if not batch:
        return 0
    OracleCard.objects.bulk_create(
        batch,
        batch_size=CARD_BATCH,
        update_conflicts=True,
        update_fields=_CARD_UPDATE_FIELDS,
        unique_fields=["oracle_id"],
    )
    _forget_queries()
    return len(batch)


def ingest_printings(
    *,
    source: str | Path | None = None,
    updated_at: datetime | None = None,
    force: bool = False,
    limit: int | None = None,
    measure: bool = False,
) -> IngestResult:
    """Load `default_cards` into `Printing`.

    **Cards must be loaded first.** A printing carries a foreign key to its
    oracle card, and `default_cards` includes rows for layouts `oracle_cards`
    deliberately drops. So the known-card set is read once and a printing whose
    card is absent is *skipped and counted*, never allowed to raise - the same
    treatment a tagging with no card gets, and for the same reason: an
    ingestion that dies on one row is a worse failure than one that reports
    5,000 rows it could not place.

    The known-id set holds **strings**, not `UUID` objects. `"..." in {UUID(...)}`
    is silently False, which presents as an ingestion that reports success and
    writes nothing at all. That is trap 4, paid for once already in `ingest_tags`.

    Peak memory is the set (~36,000 ids, a couple of megabytes) plus one batch.
    The 78.8 MB file itself is streamed and never held, exactly as the 24.7 MB
    one is.
    """
    kind = BulkImport.Kind.DEFAULT_CARDS
    location, version = _resolve_source(kind, source, updated_at)

    if not force and _already_done(kind, version):
        return IngestResult(
            kind=kind,
            status=BulkImport.Status.SKIPPED,
            message=f"already imported {version:%Y-%m-%d %H:%M}",
        )

    with _run(kind, version, measure) as (_record, result):
        known = {str(pk) for pk in OracleCard.objects.values_list("oracle_id", flat=True)}
        if not known:
            result.message = "no cards in the catalogue - run oracle_cards first"

        batch: list[Printing] = []
        for row in scryfall.stream_jsonl(location):
            result.rows_seen += 1
            if limit is not None and result.rows_seen > limit:
                result.rows_seen -= 1
                break

            if row.get("layout") in NON_CARD_LAYOUTS or not row.get("id"):
                result.rows_skipped += 1
                continue
            if row.get("oracle_id") not in known:
                result.rows_skipped += 1
                continue

            batch.append(Printing.from_scryfall(row, prices_updated_at=version))
            if len(batch) >= PRINTING_BATCH:
                result.rows_written += _flush_printings(batch)
                batch = []

        result.rows_written += _flush_printings(batch)

        if not result.message:
            result.message = f"prices as of {version:%Y-%m-%d}"

    return result


def _flush_printings(batch: list[Printing]) -> int:
    if not batch:
        return 0
    Printing.objects.bulk_create(
        batch,
        batch_size=PRINTING_BATCH,
        update_conflicts=True,
        update_fields=_PRINTING_UPDATE_FIELDS,
        unique_fields=["scryfall_id"],
    )
    _forget_queries()
    return len(batch)


def ingest_tags(
    *,
    source: str | Path | None = None,
    updated_at: datetime | None = None,
    force: bool = False,
    measure: bool = False,
) -> IngestResult:
    """Load `oracle_tags`, then roll every tagging up through the DAG.

    The rollup is mandatory, not an optimisation. Four of the nine role tags the
    engine cares about - `removal`, `draw`, `recursion` and `tutor` - carry
    **zero** direct taggings. Without the rollup, a query for "removal" returns
    nothing at all while looking perfectly healthy.

    The other five (`ramp`, `mana-rock`, `ritual`, `sacrifice-outlet`,
    `reanimate`) *do* carry direct taggings, so the rollup must be a union with
    them, never a replacement.
    """
    kind = BulkImport.Kind.ORACLE_TAGS
    location, version = _resolve_source(kind, source, updated_at)

    if not force and _already_done(kind, version):
        return IngestResult(
            kind=kind,
            status=BulkImport.Status.SKIPPED,
            message=f"already imported {version:%Y-%m-%d %H:%M}",
        )

    with _run(kind, version, measure) as (_record, result), _local_copy(location) as path:
        ancestors = _load_tag_dag(path, result)
        # Strings, not UUID objects: the JSONL gives strings, and `"..." in
        # {UUID(...)}` is silently False - which shows up as an ingestion that
        # reports success while writing zero rows.
        known = {str(pk) for pk in OracleCard.objects.values_list("oracle_id", flat=True)}

        # Stale links are dropped wholesale rather than diffed. A tagging that
        # vanished upstream has no row to update, and reconciling 433k rows
        # costs more than rebuilding them.
        OracleCardTag.objects.all().delete()

        result.rows_written += _write_links(path, known, ancestors, direct=True, result=result)
        result.rows_written += _write_links(path, known, ancestors, direct=False, result=result)

    return result


def _load_tag_dag(path: Path, result: IngestResult) -> dict[str, set[str]]:
    """Upsert every tag and edge, and return each tag's transitive ancestors.

    Only tag metadata is held in memory here - the `taggings` list on each row
    is the big part and is deliberately not kept.
    """
    tags: dict[str, dict] = {}
    for row in scryfall.stream_jsonl(path):
        tags[row["id"]] = {
            "slug": row.get("slug") or row.get("label") or row["id"],
            "label": row.get("label") or "",
            "description": row.get("description") or "",
            "aliases": row.get("aliases") or [],
            "parents": row.get("parent_ids") or [],
        }

    Tag.objects.bulk_create(
        [
            Tag(
                id=tag_id,
                slug=data["slug"][:128],
                label=data["label"][:128],
                description=data["description"],
                aliases=[a[:128] for a in data["aliases"]],
            )
            for tag_id, data in tags.items()
        ],
        batch_size=1_000,
        update_conflicts=True,
        update_fields=["slug", "label", "description", "aliases"],
        unique_fields=["id"],
    )

    with transaction.atomic():
        TagEdge.objects.all().delete()
        TagEdge.objects.bulk_create(
            [
                TagEdge(parent_id=parent, child_id=tag_id)
                for tag_id, data in tags.items()
                for parent in data["parents"]
                if parent in tags and parent != tag_id
            ],
            batch_size=2_000,
            ignore_conflicts=True,
        )

    result.message = f"{len(tags)} tags"
    return _ancestor_map(tags)


def _ancestor_map(tags: dict[str, dict]) -> dict[str, set[str]]:
    """Every tag's transitive ancestors, computed iteratively.

    Iterative rather than recursive on purpose: the DAG is community-maintained
    and a cycle introduced upstream must degrade into a wrong answer for one
    tag, not a `RecursionError` that takes the nightly ingestion down.
    """
    resolved: dict[str, set[str]] = {}

    for start in tags:
        if start in resolved:
            continue
        # Depth-first with an explicit stack, post-order so children resolve first.
        stack = [(start, iter(tags[start]["parents"]))]
        on_stack = {start}
        while stack:
            node, parents = stack[-1]
            advanced = False
            for parent in parents:
                if parent not in tags or parent in on_stack:
                    continue  # unknown parent, or a cycle: ignore the edge
                if parent not in resolved:
                    stack.append((parent, iter(tags[parent]["parents"])))
                    on_stack.add(parent)
                    advanced = True
                    break
            if advanced:
                continue
            stack.pop()
            on_stack.discard(node)
            found: set[str] = set()
            for parent in tags[node]["parents"]:
                if parent in tags and parent != node:
                    found.add(parent)
                    found |= resolved.get(parent, set())
            resolved[node] = found

    return resolved


def _write_links(
    path: Path,
    known: set,
    ancestors: dict[str, set[str]],
    *,
    direct: bool,
    result: IngestResult,
) -> int:
    """Stream the taggings once and write either the direct or the rolled-up rows.

    Two passes, in this order, because they race for the same unique row:
    a card tagged `tutor-creature-giant` gets `tutor` rolled up, and may *also*
    be tagged `tutor` directly. Writing direct rows first and rolled-up rows
    with `ignore_conflicts` means the stronger claim always wins.
    """
    written = 0
    batch: list[OracleCardTag] = []

    for row in scryfall.stream_jsonl(path):
        tag_id = row["id"]
        parents = () if direct else ancestors.get(tag_id, ())
        if not direct and not parents:
            continue

        for tagging in row.get("taggings") or []:
            oracle_id = tagging.get("oracle_id")
            if oracle_id not in known:
                if direct:
                    # Tokens, art series and cards we deliberately did not load.
                    result.rows_skipped += 1
                continue

            if direct:
                batch.append(
                    OracleCardTag(
                        oracle_card_id=oracle_id,
                        tag_id=tag_id,
                        is_direct=True,
                        weight=(tagging.get("weight") or "")[:16],
                    )
                )
            else:
                batch.extend(
                    OracleCardTag(oracle_card_id=oracle_id, tag_id=parent, is_direct=False)
                    for parent in parents
                )

            if len(batch) >= TAG_LINK_BATCH:
                written += _flush_links(batch)
                batch = []

        if direct:
            result.rows_seen += len(row.get("taggings") or [])

    written += _flush_links(batch)
    return written


def _flush_links(batch: list[OracleCardTag]) -> int:
    if not batch:
        return 0
    OracleCardTag.objects.bulk_create(batch, batch_size=TAG_LINK_BATCH, ignore_conflicts=True)
    _forget_queries()
    # `bulk_create` cannot report how many rows ON CONFLICT DO NOTHING actually
    # inserted - it returns the objects it was handed, with no primary keys. So
    # this counts rows *offered*. The authoritative number is the row count in
    # the table, which the tests assert against.
    return len(batch)


def _forget_queries() -> None:
    """Drop Django's in-memory SQL log.

    With DEBUG=True Django keeps every query it has run, and a bulk INSERT of
    1,000 cards is a megabyte of SQL. Over a full ingestion that log alone was
    measured at ~60 MB - more than the entire streaming budget, and invisible
    until you measure. Django documents `reset_queries()` for exactly this case
    in long-running processes.
    """
    reset_queries()
