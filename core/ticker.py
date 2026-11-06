"""What others are doing: the ticker on the home page (phase 11 C, K11, K12).

Built from tables that exist - finished runs, playtests, applied imports and
new accounts - never a model of its own. A line names nobody: the actor is
"Another user" or "Another guest", the deck is only its commander, and there
is no time on it, so a line cannot be matched to a person by the minute.

Robust under load: `recent()` merges the newest `KEEP` events across the
sources at most once a `REFRESH` and keeps them in the cache. The site's own
account (`is_system`, the precons' runs) is not news and never shows. A request only
filters out the viewer's own events and slices (`lines_for`), so a crowd on
the home page costs the database nothing more.

The cache holds events, not sentences (phase 12): the sentence is written per
request, in the viewer's language, from the event's kind and parts.

The loop (Paket 4, PR 16): the page gets every one of the `KEEP` events, shows
`SHOWN` and plays the rest in a loop (static/js/ticker.js). After every seven
events comes a fun line - so at most every eighth line, and none on a quiet
site. A fun line is set apart in look and, for screen readers, in words; it is
never an event. Lines quoted from pop culture stay English; ours are translated.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from heapq import nlargest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Case, F, Value, When
from django.db.models.functions import Concat
from django.utils.translation import get_language, gettext, gettext_noop, ngettext

from core.l10n import number
from decks.models import DeckImport
from playtest.models import PlaytestSession
from simulations.models import SimulationRun

#: Events kept across the sources: room for a viewer's own to be dropped and
#: still fill `SHOWN` lines.
KEEP = 40
#: Lines on the page at a time (P8); the rest wait their turn in the loop.
SHOWN = 8
#: A fun line takes at most every this many places: one after every seven events.
FUN_EVERY = 8
#: Seconds the merged list lives in the cache; the page asks every 30 s.
REFRESH = 60
#: v2 (phase 12): events without their sentence; a v1 entry held sentences.
CACHE_KEY = "home-ticker:v2"

RUN = "run"
PLAYTEST = "playtest"
IMPORT = "import"
JOIN = "join"
FUN = "fun"

#: The symbol beside each kind of event (core/icons/).
ICONS = {RUN: "simulation", PLAYTEST: "hand", IMPORT: "import", JOIN: "new-user"}


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
    def icon(self) -> str:
        return ICONS[self.kind]

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
    queryset = queryset.filter(owner__is_system=False)
    return queryset.values("pk", "owner_id", "at", "owner__is_guest", *fields)[:KEEP]


def build() -> list[Event]:
    """The newest `KEEP` events across the sources, newest first."""
    # "Krark + Thrasios" for a deck with two commanders (P20 F2b).
    commander = Case(
        When(deck__partner__isnull=False,
             then=Concat(F("deck__commander__name"), Value(" + "), F("deck__partner__name"))),
        default=F("deck__commander__name"),
    )
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
        get_user_model().objects.filter(is_guest=False, is_system=False)
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


def lines_for(user) -> list["Event | Fun"]:
    """Every event that is not the viewer's own, newest first, fun lines between.

    A guest is a user row of its own (`is_guest`), so the same filter hides a
    guest's trial from the guest. An anonymous visitor has nothing to hide.
    """
    own = user.pk if user.is_authenticated else None
    return with_fun([event for event in recent() if event.owner_id != own])


@dataclass(frozen=True)
class Fun:
    """A line from the fish, not from anybody: no owner, never cached."""

    name: str
    line: str
    #: Quoted from pop culture: stays in English, the way everybody knows it.
    pop: bool = False
    kind = FUN
    owner_id = None

    @property
    def key(self) -> str:
        return f"fun-{self.name}"

    @property
    def icon(self) -> str:
        return f"fun-{self.name}"

    @property
    def text(self) -> str:
        return self.line if self.pop else gettext(self.line)

    @property
    def lang(self) -> str:
        """`en` where an English quote stands in a page of another language."""
        return "en" if self.pop and not (get_language() or "en").startswith("en") else ""


#: The ten fun lines, each with its drawing core/icons/fun-<name>.svg.
FUN_LINES = (
    Fun("base", "All your base are belong to us.", pop=True),
    Fun("hello", "Hello, World!", pop=True),
    Fun("press-f", "Press F to pay respects.", pop=True),
    Fun("machine", "It works on my machine.", pop=True),
    Fun("never-blocks", gettext_noop("The goldfish never blocks.")),
    Fun("two-lander", gettext_noop("Keep a two-lander? Bold.")),
    Fun("no-harm", gettext_noop("No fish were harmed in this simulation.")),
    Fun("shuffle", gettext_noop("Shuffling very carefully. Again.")),
    Fun("tapped", gettext_noop("Tapped out. Emotionally and literally.")),
    Fun("variance", gettext_noop("Thousands of turns, zero opponents, still lost to variance.")),
)


def with_fun(events: list[Event]) -> list["Event | Fun"]:
    """A fun line after every seven events, each a different one, picked anew."""
    run = FUN_EVERY - 1
    picked = secrets.SystemRandom().sample(FUN_LINES, min(len(events) // run, len(FUN_LINES)))
    lines: list[Event | Fun] = []
    for place, event in enumerate(events, start=1):
        lines.append(event)
        if place % run == 0 and picked:
            lines.append(picked.pop())
    return lines
