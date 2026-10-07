"""Turning precon lists into simulated, published reports (P11).

`refresh` is the whole job, run by `manage.py precons` and once a week by the
beat (`datapages.tasks`):

1. read MTGJSON's precons (`datapages.mtgjson`);
2. resolve each list against the catalogue; a list with an unknown card is
   held (`Precon.unmatched`) and tried again next time;
3. a new or changed list becomes the system account's deck and is simulated
   with the same settings for every precon (`GAMES`, `TURNS`, on the draw),
   and its report is frozen at once as a shared report is (`sharing.share`):
   the list cannot change under it, because nobody can edit that deck.

The page shows a precon's newest *finished* run (`report_of`), so a precon
simulated again keeps its page while the new run plays.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify

from datapages import mtgjson
from datapages.models import Precon
from decks import resolve
from decks import services as decks
from decks.importers.text import PlainTextParser
from decks.models import Deck
from sharing import services as sharing
from sharing.models import SharedReport
from simulations import services as simulations
from simulations.models import SimulationRun

logger = logging.getLogger(__name__)

#: Every precon is played the same way, so their numbers compare. 10,000
#: games put a share within about one point; six turns are the early game,
#: where a deck's mana shows; on the draw, because at a table of four three
#: players are (P11 plan, decided 2026-10-07).
GAMES = 10_000
TURNS = 6
ON_THE_PLAY = False

#: The system account. Not a mailbox: it cannot sign in, and nothing is sent.
LAB_EMAIL = "lab@goldfishlab.invalid"


def lab_account():
    """The site's own account, which owns the precons' decks and runs. Made on
    first use: inactive, no usable password, no summaries, `is_system`."""
    user_model = get_user_model()
    user = user_model.objects.filter(email=LAB_EMAIL).first()
    if user is None:
        user = user_model(email=LAB_EMAIL, is_system=True, is_active=False,
                          deck_summaries=False)
        user.set_unusable_password()
        user.save()
    return user


def slug_for(name: str, set_code: str) -> str:
    return slugify(f"{name} {set_code}")[:80]


def seed_for(slug: str) -> int:
    """The same seed for a precon every time, so a run played again on the
    same engine gives the same numbers."""
    return int.from_bytes(hashlib.sha256(slug.encode()).digest()[:8], "big") % 2**62


def list_print(rows) -> str:
    """A fingerprint of a list as its source gave it."""
    parts = sorted(f"{int(row.is_commander)}|{row.oracle_id or row.name}|{row.quantity}"
                   for row in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def report_of(precon: Precon) -> SharedReport | None:
    """The frozen report of the precon's newest finished run, if it has one."""
    if precon.deck_id is None:
        return None
    return (SharedReport.objects.select_related("run")
            .filter(run__deck_id=precon.deck_id, run__status=SimulationRun.Status.DONE)
            .order_by("-run__finished_at").first())


def simulate(precon: Precon) -> SimulationRun:
    """Play the precon's deck once more and freeze its report now."""
    with transaction.atomic():
        run = simulations.start_lab_run(
            owner=precon.deck.owner, deck=precon.deck, games=GAMES, turns=TURNS,
            on_the_play=ON_THE_PLAY, seed=seed_for(precon.slug))
        sharing.share(run)
    return run


# --- importing --------------------------------------------------------------

CREATED = "created"
CHANGED = "changed"
UNCHANGED = "unchanged"
HELD = "held"
RERUN = "rerun"


@dataclass(frozen=True)
class Source:
    """Where a list came from, and what the precon is called."""

    name: str
    set_code: str
    released: date
    kind: str = Precon.Source.MTGJSON
    key: str = ""
    set_name: str = ""


@transaction.atomic
def take(source: Source, rows, *, rerun: bool = False) -> tuple[Precon, str]:
    """Bring one precon up to date with its list. Returns what happened."""
    slug = slug_for(source.name, source.set_code)
    precon = Precon.objects.select_for_update().filter(slug=slug).first() or Precon(slug=slug)
    precon.name, precon.set_code, precon.released = source.name, source.set_code, source.released
    precon.source, precon.source_key = source.kind, source.key
    precon.set_name = source.set_name or precon.set_name
    printed = list_print(rows)

    report = resolve.resolve(rows)
    if report.unresolved:
        precon.unmatched = sorted({r.row.name for r in report.unresolved})
        precon.save()
        logger.warning("precon %s held: %d unknown cards", slug, len(precon.unmatched))
        return precon, HELD

    new = precon.deck_id is None
    changed = new or printed != precon.list_print
    precon.unmatched = []
    if new:
        precon.deck = Deck.objects.create(owner=lab_account(), name=source.name[:120])
    if changed:
        decks.fill(precon.deck, report)
        precon.list_print = printed
    precon.save()
    if changed or rerun or not precon.deck.runs.exists():
        simulate(precon)
    if new:
        return precon, CREATED
    return precon, CHANGED if changed else (RERUN if rerun else UNCHANGED)


@dataclass
class Outcome:
    """What a refresh did, one line per precon."""

    lines: list[tuple[str, str]] = field(default_factory=list)

    def add(self, precon: Precon, what: str) -> None:
        self.lines.append((what, str(precon)))

    def count(self, what: str) -> int:
        return sum(1 for done, _ in self.lines if done == what)


def refresh(*, source: Path | None = None, rerun: bool = False) -> Outcome:
    """Every MTGJSON precon, brought up to date. Raises `MTGJSONError` before
    changing anything when MTGJSON cannot be read."""
    listings = mtgjson.listings(source)
    known = dict(Precon.objects.exclude(set_name="").values_list("set_code", "set_name"))
    if any(listing.set_code not in known for listing in listings):
        known |= mtgjson.set_names(source)
    outcome = Outcome()
    for listing in listings:
        rows = mtgjson.rows(mtgjson.deck(listing, source))
        precon, what = take(Source(
            name=listing.name, set_code=listing.set_code, released=listing.released,
            key=listing.file_name, set_name=known.get(listing.set_code, "")), rows, rerun=rerun)
        outcome.add(precon, what)
    return outcome


def from_text(text: str, *, name: str, set_code: str, released: date,
              set_name: str = "") -> tuple[Precon, str]:
    """A precon from a plain decklist (`1 Sol Ring`, the commander under
    `// Commander`), for a deck out before MTGJSON lists it. MTGJSON's list
    replaces it later under the same name and set code."""
    _, rows = decks.parse(text, PlainTextParser.name)
    return take(Source(name=name, set_code=set_code.upper(), released=released,
                       kind=Precon.Source.TEXT, set_name=set_name), rows)
