"""What others are doing: the ticker on the home page (phase 11 C, K11, K12).

Built from tables that exist - finished runs, playtests, applied imports and
new accounts - never a model of its own. A line names nobody: the actor is
"Another user" or "Another guest", the deck is only its commander, and there
is no time on it, so a line cannot be matched to a person by the minute.

Robust under load: `recent()` merges the newest `KEEP` events across the
sources at most once a `REFRESH` and keeps them in the cache. A request only
filters out the viewer's own events and slices (`lines_for`), so a crowd on
the home page costs the database nothing more.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from heapq import nlargest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import F

from decks.models import DeckImport
from playtest.models import PlaytestSession
from simulations.models import SimulationRun

#: Events kept across the sources: room for a viewer's own to be dropped and
#: still fill `SHOWN` lines.
KEEP = 40
#: Lines on the page (P8).
SHOWN = 8
#: Seconds the merged list lives in the cache; the page asks every 30 s.
REFRESH = 60
CACHE_KEY = "home-ticker:v1"


@dataclass(frozen=True)
class Event:
    """One line, with whose it is so the viewer's own can be left out."""

    key: str
    owner_id: int
    text: str
    at: datetime


def _actor(is_guest: bool) -> str:
    return "Another guest" if is_guest else "Another user"


def _deck(commander_name: str | None) -> str:
    # "led by" avoids "a"/"an" in front of a name like Atraxa.
    return f"a deck led by {commander_name}" if commander_name else "a deck"


def _key(kind: str, pk, at: datetime) -> str:
    # What the page tells new lines apart by. A hash, so the page carries no
    # id of somebody else's run.
    return hashlib.sha256(f"{kind}:{pk}:{at.isoformat()}".encode()).hexdigest()[:12]


def _rows(queryset, *fields):
    return queryset.values("pk", "owner_id", "at", "owner__is_guest", *fields)[:KEEP]


def build() -> list[Event]:
    """The newest `KEEP` events across the sources, newest first."""
    commander = F("deck__commander__name")
    runs = _rows(
        SimulationRun.objects.filter(status=SimulationRun.Status.DONE, finished_at__isnull=False)
        .annotate(at=F("finished_at"), commander=commander).order_by("-finished_at"),
        "commander", "games_total",
    )
    playtests = _rows(
        PlaytestSession.objects.annotate(at=F("created_at"), commander=commander)
        .order_by("-created_at"),
        "commander",
    )
    imports = _rows(
        DeckImport.objects.filter(status=DeckImport.Status.APPLIED)
        .annotate(at=F("created_at"), commander=commander).order_by("-created_at"),
        "commander",
    )
    joins = (
        get_user_model().objects.filter(is_guest=False)
        .annotate(at=F("date_joined")).order_by("-date_joined").values("pk", "at")[:KEEP]
    )

    events = [
        Event(_key("run", row["pk"], row["at"]), row["owner_id"],
              f"{_actor(row['owner__is_guest'])} simulated {_deck(row['commander'])}: "
              f"{row['games_total']:,} games.", row["at"])
        for row in runs
    ]
    events += [
        Event(_key("playtest", row["pk"], row["at"]), row["owner_id"],
              f"{_actor(row['owner__is_guest'])} playtested {_deck(row['commander'])}.",
              row["at"])
        for row in playtests
    ]
    events += [
        Event(_key("import", row["pk"], row["at"]), row["owner_id"],
              f"{_actor(row['owner__is_guest'])} imported {_deck(row['commander'])}.",
              row["at"])
        for row in imports
    ]
    events += [
        Event(_key("join", row["pk"], row["at"]), row["pk"], "A new user joined.", row["at"])
        for row in joins
    ]
    return nlargest(KEEP, events, key=lambda event: event.at)


def recent() -> list[Event]:
    """`build()`, at most once a `REFRESH`."""
    return cache.get_or_set(CACHE_KEY, build, REFRESH)


def lines_for(user) -> list[Event]:
    """The newest `SHOWN` events that are not the viewer's own.

    A guest is a user row of its own (`is_guest`), so the same filter hides a
    guest's trial from the guest. An anonymous visitor has nothing to hide.
    """
    own = user.pk if user.is_authenticated else None
    return [event for event in recent() if event.owner_id != own][:SHOWN]
