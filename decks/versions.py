"""Versions of a deck's list (B-01, package "Workshop").

A version is a whole snapshot of the list (`DeckVersion`). The deck's own
`DeckCard` rows always hold the version it is in (`Deck.current_version`), so
everything that reads a deck - the engine, the runs, the hand, the deck page -
reads the chosen version without knowing versions exist.

Versions are made by hand only: "New version" (`make`), never on save. Two
things change a list without that button and are kept consistent here:

- a re-import of a deck that has versions becomes a version of its own
  (`after_import`), so the list it replaced is not lost;
- a commander picked by hand corrects the version the deck is in
  (`after_commander_change`), because that is fixing a list, not a new one.

The first version is made lazily from the list as it is, the first time a
second one is made: a deck nobody versions never gets a row.
"""

from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max
from django.utils.translation import gettext

from cards.models import OracleCard
from core.l10n import percent
from decks.models import Deck, DeckCard, DeckVersion


class VersionError(Exception):
    """A change to the versions that cannot be made, said to the owner."""


def _list_print(deck: Deck) -> str:
    from simulations.summary import list_fingerprint

    return list_fingerprint(deck)


def _snapshot(deck: Deck) -> dict:
    cards = sorted(
        ({"id": str(pk), "n": quantity, "printings": printings}
         for pk, quantity, printings in deck.entries.values_list(
             "oracle_card_id", "quantity", "printings")),
        key=lambda card: card["id"],
    )
    return {
        "commander_id": deck.commander_id,
        "partner_id": deck.partner_id,
        "commander_printings": deck.commander_printings or {},
        "cards": cards,
        "list_print": _list_print(deck),
    }


def _add(deck: Deck, origin: str, comment: str = "") -> DeckVersion:
    last = deck.versions.aggregate(last=Max("number"))["last"] or 0
    version = DeckVersion.objects.create(
        deck=deck, number=last + 1, origin=origin, comment=comment[:200], **_snapshot(deck))
    Deck.objects.filter(pk=deck.pk).update(current_version=version)
    deck.current_version = version
    return version


def first(deck: Deck) -> DeckVersion:
    """The version the deck is in, made from the list as it is if it has none."""
    if deck.current_version_id:
        return deck.current_version
    existing = deck.versions.last()
    if existing is not None:
        Deck.objects.filter(pk=deck.pk).update(current_version=existing)
        deck.current_version = existing
        return existing
    return _add(deck, DeckVersion.Origin.FIRST)


def _write(deck: Deck, cards: dict[str, int], printings: dict[str, list]) -> None:
    """Make the deck's list `cards` ({oracle id: quantity}), keeping printings."""
    DeckCard.objects.filter(deck=deck).delete()
    DeckCard.objects.bulk_create(
        [DeckCard(deck=deck, oracle_card_id=oracle_id, quantity=min(quantity, 32_767),
                  printings=printings.get(oracle_id, []))
         for oracle_id, quantity in cards.items() if quantity > 0],
        batch_size=500,
    )


def _changed(deck: Deck) -> None:
    from decks.services import recount_later

    recount_later(Deck.objects.filter(pk=deck.pk))
    deck.open_questions = None
    # `updated_at`: a changed list moves the deck to the top of the list.
    deck.save(update_fields=["updated_at"])


@transaction.atomic
def make(deck: Deck, changes: dict[str, int], comment: str = "") -> DeckVersion:
    """"New version": the list the deck is in, plus `changes` ({oracle id: +n or -n}).

    The version the deck was in stays as it was; the new one becomes current.
    A change that leaves the list as it was is refused: a version that
    changes nothing is noise on the timeline.
    """
    first(deck)
    current = dict(deck.entries.values_list("oracle_card_id", "quantity"))
    cards = {str(pk): quantity for pk, quantity in current.items()}
    for oracle_id, delta in changes.items():
        cards[str(oracle_id)] = max(0, cards.get(str(oracle_id), 0) + int(delta))
    cards = {oracle_id: n for oracle_id, n in cards.items() if n > 0}
    if cards == {str(pk): n for pk, n in current.items()}:
        raise VersionError("nothing changes")
    printings = {str(pk): p for pk, p in deck.entries.values_list("oracle_card_id", "printings")}
    _write(deck, cards, printings)
    version = _add(deck, DeckVersion.Origin.NEW, comment)
    _changed(deck)
    return version


@transaction.atomic
def choose(deck: Deck, version: DeckVersion) -> None:
    """Be in `version`: its list and its commanders become the deck's."""
    if version.deck_id != deck.pk:
        raise VersionError("that version belongs to another deck")
    _write(deck, {card["id"]: card["n"] for card in version.cards},
           {card["id"]: card.get("printings", []) for card in version.cards})
    deck.commander_id = version.commander_id
    deck.partner_id = version.partner_id
    deck.commander_printings = version.commander_printings
    deck.current_version = version
    deck.save(update_fields=["commander", "partner", "commander_printings", "current_version",
                             "updated_at"])
    _changed(deck)


@transaction.atomic
def remove(version: DeckVersion) -> None:
    """Remove a version, first, last or in between; its runs stay.

    The deck keeps at least one version. Removing the one the deck is in
    moves it to the version before, or the one after when it was the first.
    """
    deck = Deck.objects.select_for_update().get(pk=version.deck_id)
    others = list(deck.versions.exclude(pk=version.pk))
    if not others:
        raise VersionError("a deck keeps at least one version")
    if deck.current_version_id == version.pk:
        before = [each for each in others if each.number < version.number]
        choose(deck, before[-1] if before else others[0])
    version.delete()


def after_import(deck: Deck) -> None:
    """A re-import of a versioned deck is a version of its own (origin "import")."""
    if deck.versions.exists():
        _add(deck, DeckVersion.Origin.IMPORT)


def after_commander_change(deck: Deck) -> None:
    """A commander picked by hand corrects the version the deck is in."""
    if not deck.current_version_id:
        return
    snapshot = _snapshot(deck)
    DeckVersion.objects.filter(pk=deck.current_version_id).update(**snapshot)


def diff(older: DeckVersion | None, newer: DeckVersion) -> dict[str, int]:
    """{oracle id: +n added or -n removed} from `older` to `newer`, commanders included."""
    def counts(version):
        if version is None:
            return {}
        result = {card["id"]: card["n"] for card in version.cards}
        for pk in (version.commander_id, version.partner_id):
            if pk:
                result[str(pk)] = result.get(str(pk), 0) + 1
        return result

    before, after = counts(older), counts(newer)
    return {oracle_id: after.get(oracle_id, 0) - before.get(oracle_id, 0)
            for oracle_id in sorted(before.keys() | after.keys())
            if after.get(oracle_id, 0) != before.get(oracle_id, 0)}


@dataclass(frozen=True)
class Step:
    """One version on the deck page's timeline (PR 3), newest first."""

    version: DeckVersion
    current: bool
    #: Cards in and out against the version before it (the one still kept).
    added: list[tuple[str, int]]
    removed: list[tuple[str, int]]
    #: The same changes by group - kind of card, then role: `[("Land", +1), ...]`.
    groups: list[tuple[str, int]]
    #: This list's runs, newest first; the first finished one is `last`.
    runs: list
    last: object | None
    #: "81%": games of `last` with four lands on turn 4 (`simulations.numbers`).
    on_curve: str | None = None


def timeline(deck: Deck) -> tuple[list[Step], list]:
    """The deck's versions with their runs, and the runs of no kept version.

    A run belongs to a version by its list (`list_print`); a run of a removed
    version, or of a list from before the versions, is "an old list". A run
    from before `list_print` existed belongs nowhere and is left out here:
    "Earlier runs" still lists it.
    """
    from simulations import numbers
    from simulations.models import SimulationRun

    kept = list(deck.versions.all())
    if not kept:
        return [], []
    runs = list(deck.runs.exclude(list_print="").defer("result").order_by("-created_at")[:200])
    by_print: dict[str, list] = {}
    for run in runs:
        by_print.setdefault(run.list_print, []).append(run)
    prints = {version.list_print for version in kept}
    old = [run for run in runs if run.list_print not in prints]

    changes = [diff(before, version)
               for before, version in zip([None, *kept[:-1]], kept, strict=True)]
    rows = OracleCard.objects.filter(
        pk__in={oracle_id for change in changes for oracle_id in change}).values_list(
        "pk", "front_name", "profile__kind", "profile__primary_role")
    named = {str(pk): name for pk, name, _kind, _role in rows}
    kinds = {str(pk): (kind, role) for pk, _name, kind, role in rows}

    steps = []
    for index, (version, change) in enumerate(zip(kept, changes, strict=True)):
        mine = by_print.get(version.list_print, [])
        done = next((run for run in mine if run.status == SimulationRun.Status.DONE), None)
        on_curve = None
        if done is not None:
            done = SimulationRun.objects.get(pk=done.pk)
            share = numbers.of(done).on_curve if done.result else None
            on_curve = percent(share) if share is not None else None
        first_one = index == 0
        steps.append(Step(
            version=version,
            current=version.pk == deck.current_version_id,
            added=[] if first_one else sorted(
                (named.get(oracle_id, "?"), n) for oracle_id, n in change.items() if n > 0),
            removed=[] if first_one else sorted(
                (named.get(oracle_id, "?"), -n) for oracle_id, n in change.items() if n < 0),
            groups=[] if first_one else _groups(change, kinds),
            runs=mine,
            last=done,
            on_curve=on_curve,
        ))
    return steps[::-1], old


def _groups(change: dict[str, int], kinds: dict) -> list[tuple[str, int]]:
    """`change` summed by kind of card, then by role, the ones that moved."""
    from cards.models import DerivedProfile
    from simulations.report import SEEN_ROLES

    kind_labels = dict(DerivedProfile.Kind.choices)
    role_labels = dict(SEEN_ROLES)
    by_kind: Counter = Counter()
    by_role: Counter = Counter()
    for oracle_id, n in change.items():
        kind, role = kinds.get(oracle_id, ("", ""))
        if kind in kind_labels:
            by_kind[kind] += n
        if role in role_labels:
            by_role[role] += n
    return ([(str(kind_labels[kind]), n) for kind, n in by_kind.items() if n]
            + [(gettext(role_labels[role]), n) for role, n in by_role.items() if n])
