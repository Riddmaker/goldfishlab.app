"""How much of what players bring the engine can read, and whether that got worse (P19).

Every round of P19 (issue #36) teaches the engine a class of cards it could
not read. The risk in each is the card that read fine before and reads worse
after - a new rule that also fires where it should not. So every round is
measured against a **reading snapshot**: for a fixed set of cards, whether
the engine alone reads each one and *how* (the `Card` it makes of it). A
change to either shows up as a named card in a diff, and a PR that changes a
reading says so by committing the new snapshot beside the code.

The fixed set is the 2,000 most-played Commander cards (EDHREC rank) and every
card of the decks in the development database - mostly the precons. It is
exported once (`engine_coverage --export-fixture`) into
`tests/fixtures/coverage_cards.json.gz`, so the test suite can rebuild the
same profiles offline; the snapshot is `tests/fixtures/coverage_snapshot.json`.

What is compared is the reader and the engine alone: no annotation at all,
not even the built-in ones a database may or may not hold, and no deck
colours (`adapter.engine_readings`). **The gate is the test**
(`tests/test_engine_coverage.py`), on the fixture; `engine_coverage --check`
reads today's catalogue in a development database, whose Scryfall tags drift
from the fixture's, so it shows those changes too.
"""

import gzip
import json
import uuid
from collections import Counter
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from django.conf import settings
from django.db import transaction

from simulation import serial
from simulation.cards import Card
from simulations.engine import adapter
from simulations.unread import reading_reasons

FIXTURES = Path(settings.BASE_DIR) / "tests" / "fixtures"
CARDS_FIXTURE = FIXTURES / "coverage_cards.json.gz"
SNAPSHOT = FIXTURES / "coverage_snapshot.json"
DECKS_FIXTURE = FIXTURES / "coverage_decks.json"
NUMBERS = FIXTURES / "coverage_numbers.json"

#: Real three-colour precons, full of the cards the first rounds are about
#: (choice lands, land searches, lands that enter tapped unless ...). Their
#: simulated numbers are the second half of the gate: a reading that changes
#: on purpose still has to move the numbers the way the PR says it does.
NUMBER_DECKS = ("Temur Roar", "Abzan Armor", "Mardu Surge", "Jeskai Striker")
NUMBER_GAMES = 1_000
NUMBER_TURNS = 8
NUMBER_SEED = 20261008

#: How many of the most-played cards the fixed set takes.
TOP = 2_000

#: A card nobody named: every field at its default, to leave out of a reading.
_DEFAULT = serial.dump(Card(name="", mv=0, pips=0, generic=0, kind="land"))

#: The form of a reading: 2 since the decoder retired (card model phase 5),
#: a card as printed with its blocks; 1 before, the fields it was made with.
#: A frozen file says which it holds; one without the mark holds form 1.
FORM = 2


def _canonical(value):
    """Keys sorted, so a snapshot is stable. Sets are written sorted by
    `serial.dump` itself; every list keeps its order - a card's blocks, an
    effect's arguments - since the order is part of what it says."""
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _defaults(name: str) -> dict:
    """The dumped default of every field of an engine dataclass that has one."""
    if name not in _DEFAULTS:
        cls, found = serial.engine_type(name), {}
        for item in fields(cls) if cls else ():
            if item.default is not MISSING:
                found[item.name] = serial.dump(item.default)
            elif item.default_factory is not MISSING:
                found[item.name] = serial.dump(item.default_factory())
        _DEFAULTS[name] = found
    return _DEFAULTS[name]


_DEFAULTS: dict[str, dict] = {}


def _lean(value):
    """A dump without the fields of nested values that hold their default.

    A field added to an engine class is then no change for the cards that do
    not use it: without this, every card holding the class would show it.
    `serial.load` fills the missing fields back in.
    """
    if isinstance(value, dict):
        defaults = _defaults(value["_type"]) if "_type" in value else {}
        return {key: _lean(item) for key, item in value.items()
                if key not in defaults or item != defaults[key]}
    if isinstance(value, list):
        return [_lean(item) for item in value]
    return value


def _plain_read(read: dict) -> dict:
    return {key: _canonical(_lean(value)) for key, value in read.items()}


def reading(card: Card, gaps) -> dict:
    """One card as a snapshot keeps it: what is unread, and what was read."""
    return {"unread": reading_reasons(gaps), "read": _read(card)}


def _read(card: Card) -> dict:
    dumped = serial.dump(card)
    read = {key: value for key, value in dumped.items()
            if key not in ("name", "_type") and value != _DEFAULT.get(key)}
    return _plain_read(read)


def card_of(name: str, read: dict, form: int = FORM) -> Card:
    """The engine card a reading of ``form`` holds."""
    base = _DEFAULT if form == FORM else serial.legacy_default()
    return serial.load(Card, {**base, **read, "name": name})


def upgraded(entries: dict, form: int) -> dict:
    """Readings of an older ``form`` in today's, by the same keys: a frozen
    file from before phase 5 compares with today's code as it would have."""
    if form == FORM:
        return entries
    return {name: {**entry, "read": _read(card_of(name, entry["read"], form))}
            for name, entry in entries.items()}


def ids(found: dict) -> list[str]:
    """The oracle ids a snapshot covers. Names are not unique in the catalogue."""
    return [entry["id"] for entry in found.values()]


def snapshot(oracle_cards) -> dict:
    """The fixed set's readings, by card name."""
    cards = list(oracle_cards)
    found = adapter.engine_readings(cards, builtin=False)
    names = Counter(card.name for card in cards)

    def key(card):
        # An art card or a digital twin can share a real card's name.
        return card.name if names[card.name] == 1 else f"{card.name} ({card.layout})"

    return {key(card): {"id": str(card.pk), **reading(*found[card.pk])}
            for card in sorted(cards, key=lambda card: (card.name, card.layout))}


@dataclass
class Diff:
    """What changed between two snapshots, by card name."""

    #: Read before, a reading gap now. The one that must never pass unseen.
    lost: list[str] = field(default_factory=list)
    #: A reading gap before, read now. What a round is for.
    gained: list[str] = field(default_factory=list)
    #: Read both times, but read differently - looked at one by one.
    changed: list[str] = field(default_factory=list)
    #: Unread both times, for other reasons.
    reasons: list[str] = field(default_factory=list)
    #: In one snapshot and not the other: the fixed set itself changed.
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any((self.lost, self.gained, self.changed, self.reasons,
                        self.added, self.removed))

    def lines(self, before: dict, after: dict, limit: int = 30) -> list[str]:
        out = []
        for title, names in (("LOST (read before, unread now)", self.lost),
                             ("gained (read now)", self.gained),
                             ("read differently", self.changed),
                             ("unread for other reasons", self.reasons),
                             ("new in the set", self.added),
                             ("gone from the set", self.removed)):
            if not names:
                continue
            out.append(f"{title}: {len(names)}")
            for name in names[:limit]:
                old, new = before.get(name), after.get(name)
                if old and new:
                    old, new = _leaned(old), _leaned(new)
                out.append(f"  {name}")
                if old and new and old != new:
                    out.extend(f"    {line}" for line in _changes(old, new))
        return out


def changed_keys(old: dict, new: dict) -> list[str]:
    """The fields two readings of a card differ in, a block by its form;
    ``unread`` for the gaps."""
    old, new = _leaned(old), _leaned(new)
    keys = ["unread"] if old["unread"] != new["unread"] else []
    for key in sorted(set(old["read"]) | set(new["read"])):
        was, now = old["read"].get(key), new["read"].get(key)
        if was == now:
            continue
        if key == "abilities":
            keys += _changed_forms(was or [], now or [])
        else:
            keys.append(key)
    return keys


def _changed_forms(was: list, now: list) -> list[str]:
    """The forms whose blocks differ between two readings' ``abilities``."""
    def by_form(found):
        grouped: dict[str, list] = {}
        for each in found:
            grouped.setdefault(each.get("form") or each.get("kind", ""), []).append(each)
        return grouped

    was, now = by_form(was), by_form(now)
    return [form for form in sorted(set(was) | set(now)) if was.get(form) != now.get(form)]


def _changes(old: dict, new: dict) -> list[str]:
    lines = []
    forms = {each.get("form") or each.get("kind", ""): each
             for entry in (old, new) for each in entry["read"].get("abilities", ())}
    for key in changed_keys(old, new):
        if key == "unread":
            lines.append(f"unread: {old['unread']} -> {new['unread']}")
        elif key in forms and key not in old["read"] and key not in new["read"]:
            lines.append(f"{key}: {json.dumps(_of_form(old, key))} -> "
                         f"{json.dumps(_of_form(new, key))}")
        else:
            lines.append(f"{key}: {json.dumps(old['read'].get(key))} -> "
                         f"{json.dumps(new['read'].get(key))}")
    return lines


def _of_form(entry: dict, form: str) -> list:
    return [each for each in entry["read"].get("abilities", ())
            if (each.get("form") or each.get("kind", "")) == form]


def _leaned(entry: dict) -> dict:
    return {**entry, "read": _plain_read(entry["read"])}


def compare(before: dict, after: dict) -> Diff:
    diff = Diff()
    diff.added = sorted(set(after) - set(before))
    diff.removed = sorted(set(before) - set(after))
    for name in sorted(set(before) & set(after)):
        # A snapshot written before `_lean` still holds nested defaults.
        old, new = _leaned(before[name]), _leaned(after[name])
        if old == new:
            continue
        if not old["unread"] and new["unread"]:
            diff.lost.append(name)
        elif old["unread"] and not new["unread"]:
            diff.gained.append(name)
        elif old["read"] != new["read"]:
            diff.changed.append(name)
        else:
            diff.reasons.append(name)
    return diff


def load_snapshot(path: Path = SNAPSHOT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_snapshot(found: dict, path: Path = SNAPSHOT) -> None:
    """One line per card: still JSON, and a PR's diff is the cards that changed."""
    lines = [f"{json.dumps(name, ensure_ascii=False)}: "
             f"{json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}"
             for name, entry in sorted(found.items())]
    # LF on every machine: a snapshot written on Windows must diff as cleanly.
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")


# --- the fixed set ------------------------------------------------------------


def fixed_set():
    """The cards a snapshot covers, from the full catalogue of a development
    database: the most-played Commander cards, and every card of its decks."""
    from cards.models import OracleCard
    from decks.models import Deck, DeckCard

    top = (OracleCard.objects.filter(legalities__commander="legal", edhrec_rank__isnull=False)
           .order_by("edhrec_rank").values_list("pk", flat=True)[:TOP])
    in_decks = DeckCard.objects.values_list("oracle_card_id", flat=True)
    commanders = Deck.objects.exclude(commander=None).values_list("commander_id", flat=True)
    partners = Deck.objects.exclude(partner=None).values_list("partner_id", flat=True)
    return OracleCard.objects.filter(pk__in={*top, *in_decks, *commanders, *partners})


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


#: The card columns the reader and the adapter read. Everything else - the
#: legalities, the image and Scryfall links, the prices' ranks - only chose the
#: fixed set, and would only make the fixture bigger. P19 R20: the toughness
#: Wall of Roots spends as its counters. P19 R21: the power "a creature with
#: power 4 or greater" and Selvala's mana ask for.
CARD_COLUMNS = ("oracle_id", "name", "front_name", "search_name", "mana_cost", "cmc",
                "type_line", "oracle_text", "colors", "color_identity", "produced_mana",
                "keywords", "layout", "game_changer", "toughness", "power")


def pack(cards: list[dict], tags: dict, edges: list, links: list) -> dict:
    """The fixture's form: cards by `CARD_COLUMNS`, tags by slug, and every
    reference between them a position in those lists rather than a uuid.

    `cards` are column -> value mappings, `tags` maps a tag id to its slug,
    `edges` are (parent id, child id) and `links` (card id, tag id, direct,
    weight). Only the tags a link or an edge names are kept.
    """
    tags = {str(tag): slug for tag, slug in tags.items()}
    cards = sorted(cards, key=lambda card: str(card["oracle_id"]))
    card_index = {str(card["oracle_id"]): index for index, card in enumerate(cards)}
    used = {str(tag) for _card, tag, _direct, _weight in links}
    used |= {str(tag) for edge in edges for tag in edge}
    slugs = sorted(tags[tag] for tag in set(tags) & used)
    tag_index = {slug: index for index, slug in enumerate(slugs)}

    def tag(tag_id):
        return tag_index[tags[str(tag_id)]]

    return {
        "card_columns": list(CARD_COLUMNS),
        "cards": [[_plain(card[column]) for column in CARD_COLUMNS] for card in cards],
        "tags": slugs,
        "edges": sorted([tag(parent), tag(child)] for parent, child in edges),
        "links": sorted([card_index[str(card)], tag(tag_id), direct, weight]
                        for card, tag_id, direct, weight in links),
    }


def export_fixture(cards, path: Path = CARDS_FIXTURE) -> int:
    """Write the cards, their tags and the tag tree, for `load_fixture`."""
    from cards.models import OracleCardTag, Tag, TagEdge

    rows = list(cards.values(*CARD_COLUMNS))
    links = list(OracleCardTag.objects.filter(oracle_card__in=[row["oracle_id"] for row in rows])
                 .values_list("oracle_card_id", "tag_id", "is_direct", "weight"))
    data = pack(rows, dict(Tag.objects.values_list("pk", "slug")),
                list(TagEdge.objects.values_list("parent_id", "child_id")), links)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
    return len(rows)


@transaction.atomic
def load_fixture(path: Path = CARDS_FIXTURE) -> list:
    """Put the fixed set into an empty catalogue and derive its profiles."""
    from cards import forge, forge_import, profiles
    from cards.models import OracleCard, OracleCardTag, Tag, TagEdge

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    fields = {f.attname: f for f in OracleCard._meta.concrete_fields}
    cards = [OracleCard(**{column: fields[column].to_python(value)
                           for column, value in zip(data["card_columns"], row, strict=True)})
             for row in data["cards"]]
    OracleCard.objects.bulk_create(cards, batch_size=1_000)
    tags = Tag.objects.bulk_create([Tag(pk=uuid.uuid4(), slug=slug, label=slug)
                                    for slug in data["tags"]], batch_size=2_000)
    TagEdge.objects.bulk_create([TagEdge(parent=tags[parent], child=tags[child])
                                 for parent, child in data["edges"]], batch_size=2_000)
    OracleCardTag.objects.bulk_create(
        [OracleCardTag(oracle_card=cards[card], tag=tags[tag], is_direct=direct, weight=weight)
         for card, tag, direct, weight in data["links"]], batch_size=5_000)
    # The blocks of the pinned scripts, which fill in what the reader left
    # open (card model phase 1) - from the committed pack itself, not the
    # small one the other tests read (`tests/conftest.py`).
    forge_import.run(forge.PIN.with_name("forge_scripts.jsonl.gz"), force=True)
    profiles.rebuild(OracleCard.objects.filter(pk__in=[card.pk for card in cards]))
    return cards


# --- the number snapshot -------------------------------------------------------


def export_decks(path: Path = DECKS_FIXTURE) -> int:
    """The `NUMBER_DECKS` as card lists, from a development database."""
    from decks.models import Deck

    found = []
    for name in NUMBER_DECKS:
        deck = Deck.objects.get(name=name, owner__is_system=True)
        found.append({
            "name": deck.name,
            "commander": deck.commander.name if deck.commander_id else "",
            "cards": sorted([entry.quantity, entry.oracle_card.name]
                            for entry in deck.entries.select_related("oracle_card")),
        })
    path.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                    newline="\n")
    return len(found)


def build_decks(owner, path: Path = DECKS_FIXTURE) -> list:
    """The exported decks as rows of `owner`, from the cards in this catalogue."""
    from cards.models import OracleCard
    from decks.models import Deck, DeckCard

    built = []
    for spec in json.loads(path.read_text(encoding="utf-8")):
        names = {name for _quantity, name in spec["cards"]}
        names |= {spec["commander"], spec.get("partner", "")}
        by_name = {card.name: card for card in OracleCard.objects.filter(name__in=names)}
        deck = Deck.objects.create(owner=owner, name=spec["name"],
                                   commander=by_name.get(spec["commander"]),
                                   partner=by_name.get(spec.get("partner", "")))
        DeckCard.objects.bulk_create([DeckCard(deck=deck, oracle_card=by_name[name],
                                               quantity=quantity)
                                      for quantity, name in spec["cards"]])
        built.append(deck)
    return built


def numbers(deck) -> dict:
    """One deck simulated with a fixed seed, as the snapshot keeps it."""
    return definition_numbers(adapter.convert(deck).definition)


def definition_numbers(definition, games: int = NUMBER_GAMES) -> dict:
    """An engine deck simulated with the fixed seed, as the snapshot keeps it."""
    from simulation import analysis

    result = analysis.run(games, on_the_play=False, turns=NUMBER_TURNS,
                          seed=NUMBER_SEED, deck=definition)
    turns = []
    for stats in result["turn_stats"]:
        turns.append({key: round(value.mean, 6) if isinstance(value, analysis.Histogram)
                      else value for key, value in sorted(stats.items())})
    return {
        "mulligans": {str(key): value for key, value in sorted(result["mulligans"].items())},
        "opening_lands": {str(key): value
                          for key, value in sorted(result["opening_lands"].items())},
        "turns": turns,
    }


def compare_numbers(before: dict, after: dict) -> list[str]:
    """Every number that moved, as `deck / turn n / field: old -> new`."""
    lines = []
    for deck in sorted(set(before) | set(after)):
        old, new = before.get(deck), after.get(deck)
        if old is None or new is None:
            lines.append(f"{deck}: {'new' if old is None else 'gone'}")
            continue
        for key in ("mulligans", "opening_lands"):
            if old[key] != new[key]:
                lines.append(f"{deck} / {key}: {old[key]} -> {new[key]}")
        for turn, (was, now) in enumerate(zip(old["turns"], new["turns"], strict=True), 1):
            for field_ in sorted(set(was) | set(now)):
                if was.get(field_) != now.get(field_):
                    lines.append(f"{deck} / turn {turn} / {field_}: "
                                 f"{was.get(field_)} -> {now.get(field_)}")
    return lines


# --- the numbers the plan is measured by -------------------------------------


def report() -> list[str]:
    """Coverage of the catalogue and of the decks in this database, in lines."""
    from cards.models import DerivedProfile
    from decks.models import Deck

    legal = DerivedProfile.objects.filter(oracle_card__legalities__commander="legal")
    top = legal.filter(oracle_card__edhrec_rank__lte=TOP)
    lines = [
        f"commander-legal cards the reader flags: "
        f"{legal.filter(needs_review=True).count()} of {legal.count()}",
        f"... of the {TOP} most-played: {top.filter(needs_review=True).count()} "
        f"of {top.count()}",
    ]

    seen, slots, reasons = set(), 0, Counter()
    decks = 0
    for deck in Deck.objects.all():
        key = tuple(sorted(deck.entries.values_list("oracle_card_id", flat=True)))
        if key in seen:
            continue
        seen.add(key)
        decks += 1
        for reading_ in adapter.readings(deck):
            if reading_.unreadable:
                slots += 1
                reasons.update(_bucket(reason) for reason in reading_reasons(reading_.gaps))
    if decks:
        lines.append(f"unreadable cards per deck: {slots / decks:.1f} "
                     f"({slots} over {decks} different decks)")
        lines.extend(f"  {count:4d}  {reason}" for reason, count in reasons.most_common(15))
    return lines


def _bucket(reason: str) -> str:
    """A reason with its values taken out, so the same gap counts as one."""
    if "cannot hold a choice" in reason:
        return "makes one of several colours (a choice)"
    return reason
