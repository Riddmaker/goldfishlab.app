"""Keeping the catalogue current: load, derive, and the nightly job.

`load()` is what `ingest_scryfall` has always done - cards, then tags, then the
profiles whenever either changed - shared now by the command and the nightly
task, so the two can never drift apart. **The profiles are not optional**: the
engine reads `DerivedProfile`, never the raw card (see the command's docstring).

`nightly()` (phase 12 J18, "every day around midnight, with a cheap pre-check"):
Scryfall regenerates every bulk file daily, so their `updated_at` alone would
mean a 25 MB download every night. Instead one request to `/sets` gives a
fingerprint (`scryfall.sets_fingerprint`): a new set or new previews change it,
and only then are the files fetched. Errata, bans and Tagger edits change no
count, so once a week everything is loaded anyway - on Tuesday, because
Wizards announces bans and restrictions on Mondays.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from cards import ingest, profiles, scryfall
from cards.models import BulkImport, DerivedProfile, OracleCard

#: What `--kind all` means, in dependency order: a tagging needs its card.
KINDS = [BulkImport.Kind.ORACLE_CARDS, BulkImport.Kind.ORACLE_TAGS]

#: The kinds whose rows a profile is derived from.
PROFILE_INPUTS = frozenset(KINDS)

#: `date.weekday()` of the weekly full run: Tuesday.
FULL_RUN_WEEKDAY = 1


@dataclass
class Loaded:
    """What one `load()` did."""

    results: list[ingest.IngestResult] = field(default_factory=list)
    #: Why the profiles were derived again; empty when they were not.
    profiles_reason: str = ""
    profiles_written: int = 0

    def __str__(self) -> str:
        lines = [str(result) for result in self.results]
        if self.profiles_reason:
            lines.append(f"{self.profiles_written} profiles derived ({self.profiles_reason})")
        return "; ".join(lines)


def load(
    kinds=KINDS,
    *,
    source: str | None = None,
    force: bool = False,
    limit: int | None = None,
    measure: bool = False,
    on_result: Callable[[ingest.IngestResult], None] | None = None,
    on_profiles: Callable[[str], None] | None = None,
) -> Loaded:
    """Ingest `kinds`, then derive the profiles if anything they read changed.

    Raises `ScryfallError` when Scryfall cannot be reached. The callbacks let
    the command print as it goes; the task only wants the result.
    """
    loaded = Loaded()
    changed = False
    for kind in kinds:
        common = {"source": source, "force": force, "measure": measure}
        if kind == BulkImport.Kind.ORACLE_CARDS:
            result = ingest.ingest_cards(limit=limit, **common)
        else:
            result = ingest.ingest_tags(**common)
        loaded.results.append(result)
        if on_result:
            on_result(result)
        changed |= kind in PROFILE_INPUTS and not result.skipped

    missing = OracleCard.objects.count() - DerivedProfile.objects.count()
    if changed:
        loaded.profiles_reason = "cards or tags changed"
    elif missing:
        loaded.profiles_reason = f"{missing} cards had no profile"
    else:
        return loaded

    if on_profiles:
        on_profiles(loaded.profiles_reason)
    loaded.profiles_written = rebuild_profiles()
    return loaded


def rebuild_profiles() -> int:
    """Derive every profile again and have every deck counted again."""
    from decks.models import Deck
    from decks.services import recount_later

    written = profiles.rebuild()
    # A profile is what decides which cards the engine can read, so every
    # deck's red marker has to be counted again (Phase 9 C2).
    recount_later(Deck.objects.all())
    return written


def nightly(today: date) -> str:
    """The nightly job: fetch only when Scryfall has something new, or weekly.

    Returns one line for the log. Raises when Scryfall or the database fails;
    the task turns that into a FAILED `BulkImport` row for the alerts.
    """
    fingerprint = scryfall.sets_fingerprint()
    last = _last_cards_import()
    weekly = today.weekday() == FULL_RUN_WEEKDAY

    if not weekly and last is not None and last.sets_fingerprint == fingerprint:
        return f"nothing new ({fingerprint} sets:printings), nothing fetched"

    why = "weekly full run" if weekly else f"sets changed to {fingerprint}"
    loaded = load()
    # Also when the cards were skipped (this version was already loaded by
    # hand): the catalogue matches this fingerprint either way.
    if newest := _last_cards_import():
        BulkImport.objects.filter(pk=newest.pk).update(sets_fingerprint=fingerprint)
    return f"{why}: {loaded}"


def _last_cards_import() -> BulkImport | None:
    return BulkImport.objects.filter(
        kind=BulkImport.Kind.ORACLE_CARDS, status=BulkImport.Status.OK
    ).order_by("-started_at").first()
