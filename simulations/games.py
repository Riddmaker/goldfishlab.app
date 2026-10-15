"""The gate corpus: about 200 decks whose games every engine change must leave alone.

Phase 4 of the card model rebuild moves the engine onto blocks one family at a
time, and the rule for PR 11 to 16 is that no game changes. The aggregated
numbers cannot show that, so this compares games: each one as its
:mod:`simulation.trace` records, turn by turn.

**The corpus** (`.data/game_corpus_v1.json`, `build_corpus`) is a list of decks
as card lists: every different deck of the development database, and decks
drawn so that every mechanism some Commander card has plays in at least three
of them. A mechanism - a *feature* - is read off the frozen engine reading of
the whole catalogue (`differential.freeze`): a mechanism field off its
default, a mana rule, a condition kind, a trigger event, a combat trigger's
subject, a land search's moment. Drawn once and kept, so that two engines are
always compared on the same decks.

**A freeze** (`freeze`) plays `GAMES` games of `TURNS` turns per deck, each
with its own seed (`trace.game_seed`), on the play and on the draw in turn, and
keeps each game's digest and its turn digests, plus the whole records of the
first `KEPT` games of each deck to show what changed. Beside them it keeps a
digest of each card's engine reading, so that **a diff** (`diff`) tells a card
that reads differently - a changed input - from a game that plays differently
on the same cards - a changed engine.

Every deck is built as rows of a throwaway owner inside a transaction that is
rolled back, and read by the adapter as any deck is: builtin annotations and
deck colours included. Nothing is written to the database.
"""

import dataclasses
import gzip
import hashlib
import json
import random
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

from django.conf import settings
from django.db import connections, transaction

from simulation import ENGINE_VERSION, serial
from simulation import trace as game_trace
from simulation.cards import Card
from simulations import coverage, differential
from simulations.engine import adapter

DATA = Path(settings.BASE_DIR) / ".data"
CORPUS = DATA / "game_corpus_v1.json"

#: Games per deck, turns per game, and how many games per deck keep their
#: whole records.
GAMES = 1_000
TURNS = 8
KEPT = 10

#: Decks a feature plays in, at least; decks with two commanders, at least.
COVER = 5
PAIRS = 3

#: A drawn deck: cards with its feature, lands, and the rest to 99.
FEATURE_CARDS = 3
LANDS = 36
NONBASIC = 8
DECK_SIZE = 99

#: The seed the drawing starts from: the corpus is drawn once and kept, but
#: drawn again it is the same corpus on the same catalogue.
DRAW_SEED = 20261015

#: Games one worker plays at a time.
BATCH = 250
PROCESSES = 12

#: Card fields that are the printed face or a hint, not a mechanism.
FACE = frozenset({
    "name", "mv", "pips", "generic", "kind", "tags", "cost", "types", "subtypes",
    "printed_subtypes", "creature_types", "legendary", "colors", "power", "toughness",
    "categories", "priority", "accelerant", "basic", "goldfish_castable", "token",
})

BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
WASTES = "Wastes"


def frozen_path(version: int = ENGINE_VERSION) -> Path:
    return DATA / f"frozen_games_v{version}.json.gz"


def latest_frozen() -> Path | None:
    found = sorted(DATA.glob("frozen_games_v*.json.gz"),
                   key=lambda path: int(path.name.split("_v")[1].split(".")[0]))
    return found[-1] if found else None


# --- features ------------------------------------------------------------------


_DEFAULT_CARD = Card(name="", mv=0, pips=0, generic=0, kind="land")


def features(card: Card) -> set[str]:
    """The mechanisms an engine card uses, as names a corpus can count."""
    found = set()
    for item in fields(Card):
        if item.name in FACE:
            continue
        if getattr(card, item.name) != getattr(_DEFAULT_CARD, item.name):
            found.add(f"field:{item.name}")
    for ability in card.mana_abilities:
        found.add(f"rule:{ability.rule}")
        if ability.only_if is not None:
            found.add(f"condition:{ability.only_if.kind}")
    if card.tapped_unless is not None:
        found.add(f"condition:{card.tapped_unless.kind}")
    for trigger in card.triggers:
        found.add(f"trigger:{trigger.event}")
    for trigger in card.combat:
        found.add(f"combat:{trigger.who}:{trigger.event}")
    if card.land_search is not None:
        found.add(f"search:{card.land_search.when}")
    return found


def catalogue_features(path: Path) -> dict[str, set[str]]:
    """Each Commander card's features by oracle id, from a frozen reading."""
    found = {}
    for name, entry in differential.load(path)["cards"].items():
        card = serial.load(Card, {**coverage._DEFAULT, **entry["read"],
                                  "name": name.rsplit(" [", 1)[0]})
        found[entry["id"]] = features(card)
    return found


# --- the corpus -------------------------------------------------------------------


@dataclass
class _Pool:
    """The catalogue a deck is drawn from."""

    cards: dict[str, dict]
    features: dict[str, set[str]]
    commanders: list[str] = field(default_factory=list)
    partners: list[str] = field(default_factory=list)

    def fits(self, card_id: str, identity: set[str]) -> bool:
        return set(self.cards[card_id]["color_identity"]) <= identity


def _pool(feature_path: Path) -> _Pool:
    from cards.models import OracleCard

    rows = (OracleCard.objects.filter(legalities__commander="legal")
            .values("pk", "name", "type_line", "color_identity", "keywords")
            .order_by("name", "pk"))
    cards = {str(row["pk"]): row for row in rows}
    pool = _Pool(cards=cards, features=catalogue_features(feature_path))
    for card_id, row in cards.items():
        front = row["type_line"].split("//")[0]
        if "Legendary" in front and "Creature" in front:
            pool.commanders.append(card_id)
            if "Partner" in row["keywords"]:
                pool.partners.append(card_id)
    return pool


def _spec(name: str, source: str, commander, partner, cards: Counter, names: dict) -> dict:
    return {
        "name": name,
        "source": source,
        "commander": commander or "",
        "partner": partner or "",
        "cards": sorted([quantity, card_id, names[card_id]]
                        for card_id, quantity in cards.items()),
    }


def database_decks() -> list[dict]:
    """Every different deck list of this database, as corpus specs."""
    from decks.models import Deck

    found, seen = [], set()
    for deck in Deck.objects.order_by("name", "pk"):
        entries = list(deck.entries.select_related("oracle_card"))
        cards = Counter({str(entry.oracle_card_id): entry.quantity for entry in entries})
        names = {str(entry.oracle_card_id): entry.oracle_card.name for entry in entries}
        commander = str(deck.commander_id) if deck.commander_id else ""
        partner = str(deck.partner_id) if deck.partner_id else ""
        key = (commander, partner, tuple(sorted(cards.items())))
        if key in seen:
            continue
        seen.add(key)
        found.append(_spec(deck.name, "database", commander, partner, cards, names))
    return found


def _identity(pool: _Pool, *card_ids: str) -> set[str]:
    return {color for card_id in card_ids for color in pool.cards[card_id]["color_identity"]}


def _draw(pool: _Pool, rng: random.Random, feature: str | None, number: int,
          pair: bool = False) -> dict | None:
    """One deck around ``feature`` (or a pair of commanders), or None if none fits."""
    if pair:
        commander, partner = rng.sample(pool.partners, 2)
        identity = _identity(pool, commander, partner)
        chosen: list[str] = []
    else:
        partner = ""
        holders = [card_id for card_id, found in pool.features.items()
                   if feature in found and card_id in pool.cards]
        if not holders:
            return None
        first = rng.choice(holders)
        need = set(pool.cards[first]["color_identity"])
        commanders = [card_id for card_id in pool.commanders
                      if card_id != first and need <= set(pool.cards[card_id]["color_identity"])]
        if not commanders:
            return None
        commander = rng.choice(commanders)
        identity = _identity(pool, commander)
        others = [card_id for card_id in holders
                  if card_id not in (first, commander) and pool.fits(card_id, identity)]
        chosen = [first, *rng.sample(others, min(FEATURE_CARDS - 1, len(others)))]

    taken = set(chosen) | {commander, partner}
    lands = [card_id for card_id in pool.cards
             if card_id not in taken and _is_land(pool, card_id)
             and pool.cards[card_id]["name"] not in (*BASICS.values(), WASTES)
             and pool.fits(card_id, identity)]
    deck = Counter(chosen)
    land_count = sum(_is_land(pool, card_id) for card_id in chosen)
    for card_id in rng.sample(lands, min(NONBASIC, len(lands))):
        deck[card_id] += 1
        land_count += 1
    basics = [BASICS[color] for color in "WUBRG" if color in identity] or [WASTES]
    by_name = {pool.cards[card_id]["name"]: card_id for card_id in pool.cards
               if pool.cards[card_id]["name"] in (*BASICS.values(), WASTES)}
    for index in range(max(0, LANDS - land_count)):
        deck[by_name[basics[index % len(basics)]]] += 1
    spells = [card_id for card_id in pool.cards
              if card_id not in taken and not _is_land(pool, card_id)
              and pool.fits(card_id, identity)]
    for card_id in rng.sample(spells, max(0, DECK_SIZE - sum(deck.values()))):
        deck[card_id] += 1
    names = {card_id: pool.cards[card_id]["name"] for card_id in deck}
    label = "pair" if pair else feature
    return _spec(f"drawn {number:03d} ({label})", "drawn", commander, partner, deck, names)


def _is_land(pool: _Pool, card_id: str) -> bool:
    return "Land" in pool.cards[card_id]["type_line"].split("//")[0]


def _deck_features(pool: _Pool, spec: dict) -> set[str]:
    found = set()
    for card_id in (spec["commander"], spec["partner"],
                    *(card_id for _quantity, card_id, _name in spec["cards"])):
        found |= pool.features.get(card_id, set())
    return found


def build_corpus(feature_path: Path, path: Path = CORPUS) -> dict:
    """Draw the corpus once and write it; return it."""
    pool = _pool(feature_path)
    rng = random.Random(DRAW_SEED)  # noqa: S311  (a reproducible draw, not a secret)
    decks = database_decks()
    covered = Counter()
    for spec in decks:
        covered.update(_deck_features(pool, spec))
    wanted = Counter()
    for card_id, found in pool.features.items():
        if card_id in pool.cards:
            wanted.update(found)
    # The rarest first: a deck drawn for one also covers common ones.
    for feature in sorted(wanted, key=lambda name: (wanted[name], name)):
        while covered[feature] < COVER:
            spec = _draw(pool, rng, feature, len(decks) + 1)
            if spec is None:
                break
            decks.append(spec)
            covered.update(_deck_features(pool, spec))
    pairs = sum(1 for spec in decks if spec["partner"])
    while pairs < PAIRS and len(pool.partners) >= 2:
        spec = _draw(pool, rng, None, len(decks) + 1, pair=True)
        decks.append(spec)
        covered.update(_deck_features(pool, spec))
        pairs += 1
    corpus = {
        "version": 1,
        "drawn_at": datetime.now(UTC).isoformat(),
        "frozen_reading": feature_path.name,
        "uncovered": sorted(name for name in wanted if covered[name] < COVER),
        "decks": decks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return corpus


def load_corpus(path: Path = CORPUS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- reading the decks ---------------------------------------------------------------


def definitions(specs) -> list:
    """Each spec as the engine deck the adapter makes of it today, with the
    digest of each card's reading by name. Built in a transaction rolled back."""
    from accounts.models import User
    from cards.models import OracleCard
    from decks.models import Deck, DeckCard

    found = []
    with transaction.atomic():
        owner = User.objects.create_user(email="corpus@goldfishlab.invalid")
        ids = {card_id for spec in specs for _quantity, card_id, _name in spec["cards"]}
        ids |= {spec[key] for spec in specs for key in ("commander", "partner") if spec[key]}
        cards = OracleCard.objects.in_bulk(list(ids))
        for spec in specs:
            deck = Deck.objects.create(owner=owner, name=spec["name"],
                                       commander=cards.get(_uuid(spec["commander"])),
                                       partner=cards.get(_uuid(spec["partner"])))
            DeckCard.objects.bulk_create([
                DeckCard(deck=deck, oracle_card=cards[_uuid(card_id)], quantity=quantity)
                for quantity, card_id, _name in spec["cards"] if _uuid(card_id) in cards])
            definition = adapter.convert(deck).definition
            found.append((definition, card_digests(definition)))
        transaction.set_rollback(True)
    return found


def _uuid(card_id: str):
    import uuid

    return uuid.UUID(card_id) if card_id else None


def card_digests(definition) -> dict[str, str]:
    """A digest of each card's engine reading, by name."""
    cards = {card.name: card for card in (*definition.commanders, *definition.library)}
    return {name: _digest(_stable(card)) for name, card in sorted(cards.items())}


def _stable(value):
    """`serial.dump` with every set sorted: a frozenset dumps in hash order,
    which changes from one process to the next."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"_type": type(value).__name__,
                **{item.name: _stable(getattr(value, item.name))
                   for item in dataclasses.fields(value)}}
    if isinstance(value, (frozenset, set)):
        return sorted((_stable(item) for item in value),
                      key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in value.items()}
    return value


def _digest(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# --- playing ------------------------------------------------------------------------


def _play_batch(task) -> tuple[int, int, list]:
    """Games ``start`` to ``stop`` of one deck: digests, and the kept records."""
    number, deck, start, stop, turns = task
    out = []
    for index in range(start, stop):
        records = game_trace.play(deck, index, turns)
        out.append((game_trace.digests(records), records if index < KEPT else None))
    return number, start, out


def play(definitions_, games: int = GAMES, turns: int = TURNS,
         processes: int = PROCESSES) -> list[dict]:
    """Every deck's games, in processes: per deck a list of game digests and
    the kept records."""
    tasks = [(number, deck, start, min(start + BATCH, games), turns)
             for number, deck in enumerate(definitions_)
             for start in range(0, games, BATCH)]
    played = [{"games": [None] * games, "kept": [None] * min(KEPT, games)}
              for _ in definitions_]
    connections.close_all()
    with ProcessPoolExecutor(processes, mp_context=get_context("fork")) as pool:
        for number, start, out in pool.map(_play_batch, tasks, chunksize=1):
            for offset, (digests, records) in enumerate(out):
                index = start + offset
                played[number]["games"][index] = digests
                if records is not None:
                    played[number]["kept"][index] = records
    return played


def freeze(path: Path | None = None, corpus_path: Path = CORPUS,
           games: int = GAMES, processes: int = PROCESSES) -> int:
    """Play the corpus and write what it played; return how many games."""
    path = path or frozen_path()
    corpus = load_corpus(corpus_path)
    built = definitions(corpus["decks"])
    played = play([deck for deck, _cards in built], games, processes=processes)
    data = {
        "engine_version": ENGINE_VERSION,
        "frozen_at": datetime.now(UTC).isoformat(),
        "imports": differential.imports(),
        "games": games,
        "turns": TURNS,
        "decks": [{"name": spec["name"], "cards": cards, **found}
                  for spec, (_deck, cards), found in zip(corpus["decks"], built, played,
                                                         strict=True)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
    return games * len(built)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass
class DeckDiff:
    """What moved in one deck between a freeze and today."""

    name: str
    cards: list[str] = field(default_factory=list)
    games: int = 0
    first: int | None = None
    turn: int | None = None
    keys: list[str] = field(default_factory=list)
    logs: tuple[list, list] = ((), ())


def diff(path: Path, corpus_path: Path = CORPUS, processes: int = PROCESSES) -> list[DeckDiff]:
    """Today's games against the frozen ones: the decks that moved."""
    frozen = load(path)
    corpus = load_corpus(corpus_path)
    built = definitions(corpus["decks"])
    played = play([deck for deck, _cards in built], frozen["games"], frozen["turns"],
                  processes=processes)
    moved = []
    for before, (deck, cards), now in zip(frozen["decks"], built, played, strict=True):
        found = DeckDiff(name=before["name"])
        found.cards = sorted(name for name in set(before["cards"]) | set(cards)
                             if before["cards"].get(name) != cards.get(name))
        for index, (old, new) in enumerate(zip(before["games"], now["games"], strict=True)):
            if old["game"] == new["game"]:
                continue
            found.games += 1
            if found.first is None:
                found.first = index
                _locate(found, deck, index, old, new, before["kept"], frozen["turns"])
        if found.cards or found.games:
            moved.append(found)
    return moved


def _locate(found: DeckDiff, deck, index: int, old: dict, new: dict, kept: list,
            turns: int) -> None:
    turn = next(number for number, (was, now) in enumerate(
        zip(old["turns"], new["turns"], strict=True)) if was != now)
    found.turn = turn
    records = game_trace.play(deck, index, turns)
    if index < len(kept):
        before = kept[index][turn]
        found.keys = game_trace.differences(before, records[turn])
        found.logs = (before["log"], records[turn]["log"])
    else:
        found.logs = ([], records[turn]["log"])


def lines(moved: list[DeckDiff], limit: int = 30) -> list[str]:
    out = []
    for deck in moved[:limit]:
        out.append(f"{deck.name}: {deck.games} game(s) differ"
                   + (f", {len(deck.cards)} card(s) read differently" if deck.cards else ""))
        if deck.cards:
            out.append(f"  cards: {', '.join(deck.cards[:10])}")
        if deck.first is not None:
            out.append(f"  first: game {deck.first}, record {deck.turn} "
                       f"(0 = opening hand): {', '.join(deck.keys) or 'see the logs'}")
            out.extend(f"    before: {line}" for line in deck.logs[0])
            out.extend(f"    now:    {line}" for line in deck.logs[1])
    return out


# --- the CI trace fixture ----------------------------------------------------------

TRACE_DECKS = coverage.FIXTURES / "trace_decks.json"
TRACES = coverage.FIXTURES / "game_traces.json"

#: Games per deck the CI test compares, and games it plays again restoring a
#: saved game after every turn.
CI_GAMES = 25
CI_RESTORED = 5


def export_trace_decks(path: Path = TRACE_DECKS, cards_path: Path = coverage.CARDS_FIXTURE) -> int:
    """The decks of this database as card lists, plus one deck with two
    commanders, all from the cards of the coverage fixture.

    A card the fixture does not hold (a deck added after it was exported) is
    left out: the CI decks only have to play the same games every time.
    """
    with gzip.open(cards_path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    columns = data["card_columns"]
    rows = [dict(zip(columns, row, strict=True)) for row in data["cards"]]
    names = {row["name"] for row in rows}
    found = []
    for spec in database_decks():
        found.append({
            "name": spec["name"],
            "commander": _name_of(spec["commander"]),
            "partner": _name_of(spec["partner"]),
            "cards": [[quantity, name] for quantity, _card_id, name in spec["cards"]
                      if name in names],
        })
    found.append(_pair_deck(rows))
    path.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                    newline="\n")
    return len(found)


def _name_of(card_id: str) -> str:
    from cards.models import OracleCard

    return OracleCard.objects.get(pk=card_id).name if card_id else ""


def _pair_deck(rows: list[dict]) -> dict:
    """Two partners of different colours and 98 cards of theirs, by name."""
    def front(row):
        return row["type_line"].split("//")[0]

    partners = sorted((row for row in rows if "Partner" in (row["keywords"] or [])
                       and "Legendary" in front(row) and "Creature" in front(row)
                       and len(row["color_identity"]) == 1), key=lambda row: row["name"])
    first = partners[0]
    second = next(row for row in partners
                  if row["color_identity"] != first["color_identity"])
    identity = set(first["color_identity"]) | set(second["color_identity"])
    rng = random.Random(DRAW_SEED)  # noqa: S311  (a reproducible draw, not a secret)

    def fits(row):
        return (set(row["color_identity"]) <= identity
                and row["name"] not in (first["name"], second["name"]))

    lands = sorted(row["name"] for row in rows if fits(row) and "Land" in front(row)
                   and row["name"] not in BASICS.values())
    spells = sorted(row["name"] for row in rows if fits(row) and "Land" not in front(row))
    cards = Counter(rng.sample(lands, min(NONBASIC, len(lands))))
    basics = [BASICS[color] for color in "WUBRG" if color in identity]
    for index in range(LANDS - sum(cards.values())):
        cards[basics[index % len(basics)]] += 1
    cards.update(rng.sample(spells, DECK_SIZE - 1 - sum(cards.values())))
    return {"name": "Two commanders", "commander": first["name"], "partner": second["name"],
            "cards": sorted([quantity, name] for name, quantity in cards.items())}


def ci_traces(decks) -> dict:
    """What the CI test compares: each deck's first games, as digests."""
    found = {}
    for deck in decks:
        definition = adapter.convert(deck).definition
        found[deck.name] = [game_trace.digests(game_trace.play(definition, index, TURNS))
                            for index in range(CI_GAMES)]
    return found


def write_traces(found: dict, path: Path = TRACES) -> None:
    lines = [f"{json.dumps(name, ensure_ascii=False)}: "
             f"{json.dumps(games_, separators=(',', ':'))}"
             for name, games_ in sorted(found.items())]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")


# --- the numbers of the database decks -----------------------------------------------

NUMBERS_GAMES = 10_000


def numbers_path(version: int = ENGINE_VERSION) -> Path:
    return DATA / f"numbers_v{version}.json"


def _numbers_of(task) -> tuple[str, dict]:
    name, deck, count = task
    return name, coverage.definition_numbers(deck, count)


def numbers(games_: int = NUMBERS_GAMES, processes: int = PROCESSES,
            corpus_path: Path = CORPUS) -> dict:
    """The database decks of the corpus through `analysis.run`, one seed, as
    the coverage numbers are: the baseline of the numbers gate (gate E).

    Not the chunked path production runs take, whose chunk count follows the
    deck's measured speed.
    """
    specs = [spec for spec in load_corpus(corpus_path)["decks"] if spec["source"] == "database"]
    built = definitions(specs)
    tasks = [(spec["name"], deck, games_) for spec, (deck, _cards) in zip(specs, built,
                                                                           strict=True)]
    connections.close_all()
    with ProcessPoolExecutor(processes, mp_context=get_context("fork")) as pool:
        return dict(sorted(pool.map(_numbers_of, tasks, chunksize=1)))
