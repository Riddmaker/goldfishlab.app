"""The whole catalogue, read by frozen code and by today's code (card model, phase 0).

The coverage gate (`simulations.coverage`) watches a fixed set of 3,070 cards.
The card-model rebuild replaces the reader and the engine's card under every
Commander-legal card, so before it starts, today's reading of all of them is
**frozen** into a local file, and every later step is compared against it:

    manage.py engine_coverage --freeze   # once, on engine v27
    manage.py engine_coverage --diff     # after a change: every card that moved

Both sides derive the profiles in memory from the same catalogue, so a change
to the reader shows up even before `profiles.rebuild()` ran, and nothing is
written to the database. The file lives in `/.data/` (not committed: it is
about 2 MB, and only means something next to the catalogue it was made from).
It records which Scryfall imports that catalogue came from; when they differ
at `--diff`, new oracle text or tags explain part of the diff, and it says so.

`--diff --explain` is the gate between two engine versions (card model,
gate B): every card that moved is given its **causes** - the Forge families
of `cards.forge_merge` that changed its profile, and the reader changes of
`READER_CAUSES` its text shows - and a card nothing explains fails it.
"""

import copy
import gzip
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from simulation import ENGINE_VERSION
from simulations import coverage
from simulations.engine import adapter

FROZEN_DIR = Path(settings.BASE_DIR) / ".data"

#: Cards derived and read per round trip to the database.
CHUNK = 1_000


def frozen_path(version: int = ENGINE_VERSION) -> Path:
    return FROZEN_DIR / f"frozen_engine_v{version}.json.gz"


def latest_frozen() -> Path | None:
    """The frozen file of the highest engine version on disk."""
    found = sorted(FROZEN_DIR.glob("frozen_engine_v*.json.gz"),
                   key=lambda path: int(path.name.split("_v")[1].split(".")[0]))
    return found[-1] if found else None


def catalogue():
    """Every Commander-legal card: what players can bring."""
    from cards.models import OracleCard

    return OracleCard.objects.filter(legalities__commander="legal").order_by("pk")


def key(card) -> str:
    """Name and the start of the oracle id: names are not unique in the catalogue."""
    return f"{card.name} [{str(card.pk)[:8]}]"


def readings(oracle_cards) -> dict:
    """Each card as the reader and the engine alone read it today, by `key`.

    The profile is derived here rather than loaded, so the reading is the
    code's and not whatever the last `profiles.rebuild()` stored.
    """
    found = {}
    for chunk, derive, forge in _chunks(oracle_cards):
        for card in chunk:
            # Sets the reverse one-to-one cache only; nothing is saved.
            card.profile = derive(card, forge.get(str(card.pk), ()))
        read = adapter.engine_readings(chunk, builtin=False)
        for card in chunk:
            found[key(card)] = {"id": str(card.pk), **coverage.reading(*read[card.pk])}
    return found


def _chunks(oracle_cards):
    """The cards a thousand at a time, each chunk with a way to derive a
    card's profile and the chunk's Forge blocks by oracle id."""
    from cards import forge_merge, profiles

    branches = profiles.tutor_branches()
    revivals = profiles.reanimate_branches()
    cards = list(oracle_cards)
    for start in range(0, len(cards), CHUNK):
        chunk = cards[start:start + CHUNK]
        slugs = _tags(chunk)

        def derive(card, forge, slugs=slugs):
            return profiles.derive(card, slugs.get(str(card.pk), set()),
                                   branches=branches, revivals=revivals, forge=forge)

        yield chunk, derive, forge_merge.blocks_by_card(chunk)


def _tags(chunk) -> dict[str, set[str]]:
    from cards.models import OracleCardTag

    slugs: dict[str, set[str]] = {}
    links = OracleCardTag.objects.filter(oracle_card__in=chunk).values_list(
        "oracle_card_id", "tag__slug")
    for card_id, slug in links:
        slugs.setdefault(str(card_id), set()).add(slug)
    return slugs


def imports() -> dict:
    """The imports the catalogue in this database came from, by kind: the
    Scryfall files by their date, Forge's scripts by their commit."""
    from cards.models import BulkImport

    latest = {}
    for row in BulkImport.objects.filter(status=BulkImport.Status.OK).order_by("started_at"):
        latest[row.kind] = row.forge_commit if row.kind == BulkImport.Kind.FORGE_SCRIPTS \
            else row.scryfall_updated_at.isoformat()
    return latest


def freeze(path: Path | None = None) -> int:
    """Write today's reading of the whole catalogue; return how many cards."""
    path = path or frozen_path()
    found = readings(catalogue())
    data = {
        "engine_version": ENGINE_VERSION,
        "frozen_at": datetime.now(UTC).isoformat(),
        "imports": imports(),
        "cards": found,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
    return len(found)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def diff(path: Path) -> tuple[coverage.Diff, dict, dict, list[str]]:
    """Today's reading against the frozen one: the diff, both sides, and notes."""
    frozen = load(path)
    now = readings(catalogue())
    from cards.models import BulkImport

    notes = []
    forge = BulkImport.Kind.FORGE_SCRIPTS
    before, after = dict(frozen["imports"]), imports()
    forge_before, forge_after = before.pop(forge, None), after.pop(forge, None)
    if before != after:
        notes.append("the catalogue was imported again since the freeze: new oracle text "
                     "or tags explain part of this diff "
                     f"(frozen {before}, now {after})")
    # A freeze from before the scripts were read did not depend on them.
    if forge_before is not None and forge_before != forge_after:
        notes.append(f"Forge's scripts are of another commit since the freeze "
                     f"({forge_before[:10]}, now {(forge_after or 'none')[:10]})")
    return coverage.compare(frozen["cards"], now), frozen["cards"], now, notes


def round_trip(path: Path) -> dict:
    """Every frozen card through blocks and back (card model, phase 0).

    Returns how many cards there were, the ones that did not come back the
    same, how many carry a value nothing reads (`blocks.Face.extra`), how many
    abilities the cards have, and how often each verb, cost atom and event is
    used.
    """
    from collections import Counter

    from simulation import blocks, serial
    from simulation.cards import Card

    lost, extra, verbs, abilities = [], 0, Counter(), Counter()
    for name, entry in load(path)["cards"].items():
        card = serial.load(Card, {**coverage._DEFAULT, **entry["read"], "name": name})
        found = blocks.to_blocks(card)
        if blocks.from_blocks(found) != card:
            lost.append(name)
        extra += bool(found.face.extra)
        abilities[len(found.abilities)] += 1
        verbs.update(blocks.verbs(found))
    return {"cards": sum(abilities.values()), "lost": lost, "extra": extra,
            "abilities": dict(sorted(abilities.items())), "verbs": verbs}


# --- explaining a diff (gate B) -------------------------------------------------


@dataclass(frozen=True)
class Cause:
    """A change of our reader between two versions, as a card shows it: the
    reading fields it may move, and what the card's oracle text must say."""

    name: str
    keys: frozenset[str]
    shows: Callable[[str], bool]


_REMINDER_DRAW = re.compile(r"\([^)]*\bdraws?\b[^)]*\)", re.IGNORECASE)
_TAPPED_ATTACKING = re.compile(r"\btapped and attacking\b", re.IGNORECASE)
_SACRIFICE_SOME = re.compile(r"\bsacrifice (?:two|three) ", re.IGNORECASE)

#: Engine v28 (card model, phase 1, PR 2). A draw inside reminder text is
#: Cycling's, a Clue's, a gift's ...: none is drawn as the spell resolves.
READER_CAUSES = (
    Cause("draw in reminder text", frozenset({"draw_on_cast", "discard_on_cast"}),
          lambda text: bool(_REMINDER_DRAW.search(text))),
    Cause("tapped and attacking is no entering tapped",
          frozenset({"enters_tapped", "unread"}),
          lambda text: bool(_TAPPED_ATTACKING.search(text))),
    Cause("sacrifice two or three", frozenset({"additional_costs", "unread"}),
          lambda text: bool(_SACRIFICE_SOME.search(text))),
)


@dataclass
class Explanation:
    """Every card that moved, with its causes; and the ones nothing explains."""

    diff: coverage.Diff
    notes: list[str]
    #: Card -> its causes: ``forge:<family>`` or ``reader:<cause>``.
    causes: dict[str, list[str]] = field(default_factory=dict)
    #: Card -> the reading fields no cause covers (``replay`` when deriving
    #: again did not give today's reading).
    unexplained: dict[str, list[str]] = field(default_factory=dict)

    def by_cause(self) -> Counter:
        return Counter(cause for found in self.causes.values() for cause in found)


def _profile_state(profile) -> dict:
    return {item.attname: copy.deepcopy(getattr(profile, item.attname))
            for item in type(profile)._meta.concrete_fields}


def _read(card, profile) -> dict:
    card.profile = profile
    return coverage.reading(*adapter.engine_readings([card], builtin=False)[card.pk])


def explain(path: Path) -> Explanation:
    """Today's diff against a frozen reading, every moved card with its causes.

    A card's profile is derived again without Forge, and the Forge families
    are applied to it one by one, as `forge_merge.merge` does: a family that
    changed the profile is a cause. What moved between the frozen reading and
    the one without Forge is our reader's, and has to be covered by the keys
    of a `READER_CAUSES` entry the card's text shows.
    """
    from cards import forge_merge
    from cards.models import OracleCard

    found, before, after, notes = diff(path)
    moved = {after[name]["id"]: name
             for name in (*found.lost, *found.gained, *found.changed, *found.reasons)}
    explanation = Explanation(diff=found, notes=notes)
    cards = OracleCard.objects.filter(pk__in=moved).order_by("pk")
    for chunk, derive, forge in _chunks(cards):
        for card in chunk:
            name, abilities = moved[str(card.pk)], forge.get(str(card.pk), ())
            profile = derive(card, ())
            alone, causes = _read(card, profile), []
            for family in forge_merge.FAMILIES:
                was = _profile_state(profile)
                family(profile, abilities, card)
                if _profile_state(profile) != was:
                    causes.append(f"forge:{family.__name__}")
            left = set(coverage.changed_keys(before[name], alone))
            text = card.oracle_text or ""
            for cause in READER_CAUSES:
                if left & cause.keys and cause.shows(text):
                    causes.append(f"reader:{cause.name}")
                    left -= cause.keys
            if coverage.changed_keys(_read(card, profile), after[name]):
                left.add("replay")
            explanation.causes[name] = causes
            if left:
                explanation.unexplained[name] = sorted(left)
    return explanation
