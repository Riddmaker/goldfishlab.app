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
from dataclasses import dataclass, field
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


def _canonical(value):
    """Lists sorted where their order means nothing, so a snapshot is stable.

    A frozenset dumps in hash order, which changes between runs; every list in
    a card's dump is either a set or a tuple whose order the engine does not
    read twice (mana abilities are tried in turn, but stored sorted they still
    say the same thing to a reader of the diff).
    """
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def reading(card: Card, gaps) -> dict:
    """One card as a snapshot keeps it: what is unread, and what was read."""
    dumped = serial.dump(card)
    read = {key: _canonical(value) for key, value in dumped.items()
            if key not in ("name", "_type") and value != _DEFAULT.get(key)}
    return {"unread": reading_reasons(gaps), "read": read}


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
                out.append(f"  {name}")
                if old and new and old != new:
                    out.extend(f"    {line}" for line in _changes(old, new))
        return out


def _changes(old: dict, new: dict) -> list[str]:
    lines = []
    if old["unread"] != new["unread"]:
        lines.append(f"unread: {old['unread']} -> {new['unread']}")
    for key in sorted(set(old["read"]) | set(new["read"])):
        if old["read"].get(key) != new["read"].get(key):
            lines.append(f"{key}: {json.dumps(old['read'].get(key))} -> "
                         f"{json.dumps(new['read'].get(key))}")
    return lines


def compare(before: dict, after: dict) -> Diff:
    diff = Diff()
    diff.added = sorted(set(after) - set(before))
    diff.removed = sorted(set(before) - set(after))
    for name in sorted(set(before) & set(after)):
        old, new = before[name], after[name]
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
    return OracleCard.objects.filter(pk__in={*top, *in_decks, *commanders})


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


#: The card columns the reader and the adapter read. Everything else - the
#: legalities, the image and Scryfall links, the prices' ranks - only chose the
#: fixed set, and would only make the fixture bigger.
CARD_COLUMNS = ("oracle_id", "name", "front_name", "search_name", "mana_cost", "cmc",
                "type_line", "oracle_text", "colors", "color_identity", "produced_mana",
                "keywords", "layout", "game_changer")


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
    from cards import profiles
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
        names = {name for _quantity, name in spec["cards"]} | {spec["commander"]}
        by_name = {card.name: card for card in OracleCard.objects.filter(name__in=names)}
        deck = Deck.objects.create(owner=owner, name=spec["name"],
                                   commander=by_name.get(spec["commander"]))
        DeckCard.objects.bulk_create([DeckCard(deck=deck, oracle_card=by_name[name],
                                               quantity=quantity)
                                      for quantity, name in spec["cards"]])
        built.append(deck)
    return built


def numbers(deck) -> dict:
    """One deck simulated with a fixed seed, as the snapshot keeps it."""
    from simulation import analysis

    result = analysis.run(NUMBER_GAMES, on_the_play=False, turns=NUMBER_TURNS,
                          seed=NUMBER_SEED, deck=adapter.convert(deck).definition)
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
