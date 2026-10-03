"""What others are doing: the ticker on the home page (phase 11 C, K11, K12).

Built from tables that exist - finished runs, playtests, applied imports and
new accounts - never a model of its own. A line names nobody: the actor is
"Another user" or "Another guest", the deck is only its commander, and there
is no time on it, so a line cannot be matched to a person by the minute.

Robust under load: `recent()` merges the newest `KEEP` events across the
sources at most once a `REFRESH` and keeps them in the cache. A request only
filters out the viewer's own events and slices (`lines_for`), so a crowd on
the home page costs the database nothing more.

The cache holds events, not sentences (phase 12): the sentence is written per
request, in the viewer's language, from the event's kind and parts.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from heapq import nlargest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import F
from django.utils.translation import gettext, ngettext

from core.l10n import number
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
#: v2 (phase 12): events without their sentence; a v1 entry held sentences.
CACHE_KEY = "home-ticker:v2"

RUN = "run"
PLAYTEST = "playtest"
IMPORT = "import"
JOIN = "join"


@dataclass(frozen=True)
class Event:
    """One line, with whose it is so the viewer's own can be left out."""

    key: str
    owner_id: int
    kind: str
    at: datetime
    guest: bool = False
    #: The deck's commander, `None` for a deck without one.
    commander: str | None = None
    games: int = 0

    @property
    def text(self) -> str:
        """The line, in the language of whoever is reading it."""
        if self.kind == JOIN:
            return gettext("A new user joined.")
        parts = {"commander": self.commander, "games": number(self.games)}
        return _sentence(self.kind, self.guest, bool(self.commander), self.games) % parts


def _sentence(kind: str, guest: bool, led: bool, games: int) -> str:
    """Whole sentences, one per case, so every language can build its own.

    "led by" avoids "a"/"an" in front of a name like Atraxa.
    """
    if kind == RUN:
        if guest:
            return (ngettext("Another guest simulated a deck led by %(commander)s: "
                             "%(games)s game.",
                             "Another guest simulated a deck led by %(commander)s: "
                             "%(games)s games.", games) if led
                    else ngettext("Another guest simulated a deck: %(games)s game.",
                                  "Another guest simulated a deck: %(games)s games.", games))
        return (ngettext("Another user simulated a deck led by %(commander)s: %(games)s game.",
                         "Another user simulated a deck led by %(commander)s: "
                         "%(games)s games.", games) if led
                else ngettext("Another user simulated a deck: %(games)s game.",
                              "Another user simulated a deck: %(games)s games.", games))
    if kind == PLAYTEST:
        if guest:
            return (gettext("Another guest playtested a deck led by %(commander)s.") if led
                    else gettext("Another guest playtested a deck."))
        return (gettext("Another user playtested a deck led by %(commander)s.") if led
                else gettext("Another user playtested a deck."))
    if guest:
        return (gettext("Another guest imported a deck led by %(commander)s.") if led
                else gettext("Another guest imported a deck."))
    return (gettext("Another user imported a deck led by %(commander)s.") if led
            else gettext("Another user imported a deck."))


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
        Event(_key(RUN, row["pk"], row["at"]), row["owner_id"], RUN, row["at"],
              guest=row["owner__is_guest"], commander=row["commander"],
              games=row["games_total"])
        for row in runs
    ]
    events += [
        Event(_key(kind, row["pk"], row["at"]), row["owner_id"], kind, row["at"],
              guest=row["owner__is_guest"], commander=row["commander"])
        for kind, rows in ((PLAYTEST, playtests), (IMPORT, imports))
        for row in rows
    ]
    events += [
        Event(_key(JOIN, row["pk"], row["at"]), row["pk"], JOIN, row["at"])
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
