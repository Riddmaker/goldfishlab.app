"""Themen-Doc, Paket 1, PR 1: what a deck is built around.

What matters:

1. **A plan, not a few cards**: a theme needs its core piece filled (four
   payoffs make Aristocrats, three lifegain cards make nothing), and a theme
   with an empty piece is a hint at most.
2. **The right cards count, and how often**: copies count, the commander
   twice, basic lands never; a card that triggers on its own death is
   fodder, not a payoff; a missing tag is made up for by our own mechanics.
3. **Typal is a type and its payoffs**: the most common creature type,
   changelings included, and only with cards that reward it.
4. **The precons say what they are about**: five fixture decks are read as
   their names promise.
5. **The catalogue's tags exist**, and a deck's themes cost two queries.
"""

import gzip
import json
from collections import defaultdict
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cards import themes
from cards.themes import HINT, MEDIUM, STRONG, ThemeCard, detect
from decks import services as deck_services
from decks.themes import deck_themes, theme_cards

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _card(name, *, tags=(), direct=(), mechanics=(), type_line="Creature — Human",
          **extra):
    return ThemeCard(name, type_line, frozenset(tags) | frozenset(direct), frozenset(direct),
                     frozenset(mechanics), **extra)


def _many(prefix, count, **kwargs):
    return [_card(f"{prefix} {n}", **kwargs) for n in range(count)]


def _found(cards, key):
    return next((result for result in detect(cards) if result.theme == key), None)


# --- 1. a plan, not a few cards --------------------------------------------------------


def test_a_full_theme_is_strong():
    cards = (_many("Payoff", 6, direct=["death-trigger"])
             + _many("Outlet", 3, tags=["repeatable-sacrifice-outlet"])
             + _many("Fodder", 6, tags=["repeatable-token-generator"]))

    found = _found(cards, "aristocrats")

    assert found.level == STRONG
    assert found.fill == 1.0
    assert [(part.key, part.count) for part in found.parts] == [
        ("payoff", 6), ("outlet", 3), ("fodder", 6)]


def test_too_few_core_cards_are_no_theme():
    cards = (_many("Payoff", themes.CORE_MIN - 1, direct=["death-trigger"])
             + _many("Outlet", 9, tags=["repeatable-sacrifice-outlet"])
             + _many("Fodder", 9, tags=["repeatable-token-generator"]))

    assert _found(cards, "aristocrats") is None


def test_an_empty_piece_makes_a_hint_at_most():
    cards = (_many("Payoff", 6, direct=["death-trigger"])
             + _many("Fodder", 6, tags=["repeatable-token-generator"]))

    assert _found(cards, "aristocrats").level == HINT


def test_the_levels_follow_the_fill():
    assert [themes._level(fill) for fill in (1.0, 0.75, 0.6, 0.45, 0.2)] == [
        STRONG, STRONG, MEDIUM, HINT, ""]


def test_a_deck_without_a_plan_has_no_theme():
    assert detect(_many("Bear", 30)) == []
    assert detect([]) == []


def test_the_strongest_theme_comes_first():
    cards = (_many("Thief", 8, tags=["theft"])
             + _many("Outlet", 2, tags=["sacrifice-outlet"])
             + _many("Lifelinker", 4, tags=["lifegain-matters"])
             + _many("Gainer", 4, tags=["repeatable-lifegain"]))

    assert [result.theme for result in detect(cards)] == ["theft", "lifegain"]


# --- 2. the right cards count, and how often ------------------------------------------


def test_copies_count_the_commander_twice_and_basics_never():
    assert _card("Rat", quantity=30).weight == 30
    assert _card("Chainer", commander=True).weight == 2
    assert _card("Swamp", type_line="Basic Land — Swamp", quantity=30).weight == 0
    assert _card("Bog", type_line="Land").weight == 1


def test_a_commander_fills_a_piece_twice():
    cards = ([_card("Commander", tags=["theft"], commander=True)]
             + _many("Thief", 2, tags=["theft"])
             + _many("Outlet", 2, tags=["sacrifice-outlet"]))

    assert _found(cards, "theft").parts[0].count == 4


def test_dying_itself_is_fodder_not_a_payoff():
    # `death-trigger-self` rolls up into `death-trigger`: the payoff piece
    # reads the card's own tags only.
    self_dying = _many("Self", 6, tags=["death-trigger-self", "death-trigger"],
                       direct=["death-trigger-self"])
    cards = self_dying + _many("Outlet", 3, tags=["repeatable-sacrifice-outlet"])

    assert _found(cards, "aristocrats") is None


def test_our_mechanics_stand_in_for_a_missing_tag():
    cards = (_many("Thief", 8, mechanics=["steal"])
             + _many("Outlet", 2, mechanics=["sac_outlet"]))

    assert _found(cards, "theft").level == STRONG


def test_type_line_words_count_the_front_face_only():
    sword = _card("Sword", type_line="Artifact — Equipment")
    spell_land = _card("Pathway", type_line="Instant // Land")

    assert "Equipment" in sword.types
    assert spell_land.types == frozenset({"Instant"})


# --- 3. typal ------------------------------------------------------------------------


def test_typal_is_the_most_common_type_with_its_payoffs():
    cards = (_many("Angel", 18, type_line="Creature — Angel")
             + _many("Human", 12, type_line="Creature — Human Soldier")
             + _many("Lord", 6, tags=["typal-angel"], type_line="Enchantment"))

    found = _found(cards, themes.TYPAL)

    assert found.subject == "Angel"
    assert found.level == STRONG
    assert found.parts[1].count == 18


def test_changelings_are_every_type():
    cards = (_many("Angel", 8, type_line="Creature — Angel")
             + _many("Shapeshifter", 4, type_line="Creature — Shapeshifter",
                     every_type=True)
             + _many("Lord", 6, tags=["typal-choose"], type_line="Enchantment"))

    found = _found(cards, themes.TYPAL)

    assert found.subject == "Angel"
    assert found.parts[1].count == 12


def test_many_of_a_type_without_payoffs_is_no_typal():
    assert _found(_many("Elf", 30, type_line="Creature — Elf Druid"), themes.TYPAL) is None


# --- 4. the precons ------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_cards():
    """The committed coverage fixture as `ThemeCard` makers, by card name."""
    with gzip.open(FIXTURES / "coverage_cards.json.gz", "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    columns = data["card_columns"]
    rows = [dict(zip(columns, row, strict=True)) for row in data["cards"]]
    tags, direct = defaultdict(set), defaultdict(set)
    for card, tag, is_direct, _weight in data["links"]:
        tags[card].add(data["tags"][tag])
        if is_direct:
            direct[card].add(data["tags"][tag])
    by_name = {row["name"]: (index, row) for index, row in enumerate(rows)}
    return by_name, tags, direct, set(data["tags"])


def _fixture_deck(fixture_cards, name):
    by_name, tags, direct, _slugs = fixture_cards
    spec = next(deck for deck in json.loads((FIXTURES / "trace_decks.json").read_text())
                if deck["name"] == name)
    listed = [(quantity, card, False) for quantity, card in spec["cards"]]
    listed += [(1, card, True) for card in (spec["commander"], spec["partner"]) if card]
    cards = []
    for quantity, card, commander in listed:
        index, row = by_name[card]
        cards.append(ThemeCard(card, row["type_line"], frozenset(tags[index]),
                               frozenset(direct[index]), frozenset(), quantity, commander,
                               "Changeling" in (row["keywords"] or [])))
    return cards


@pytest.mark.parametrize(("deck", "expected"), [
    ("Chainer, Dementia Master", {"theft", "aristocrats", "reanimator"}),
    ("Calling All Angels", {"typal", "lifegain"}),
    ("Keen Engineering", {"artifacts"}),
    ("Prismari Artistry", {"spellslinger"}),
    ("Limit Break (FINAL FANTASY VII)", {"voltron"}),
])
def test_a_precon_is_read_as_its_name_promises(fixture_cards, deck, expected):
    strong = {result.theme for result in detect(_fixture_deck(fixture_cards, deck))
              if result.level == STRONG}

    assert expected <= strong


def test_angels_are_the_angel_deck(fixture_cards):
    found = _found(_fixture_deck(fixture_cards, "Calling All Angels"), themes.TYPAL)

    assert found.subject == "Angel"


def test_a_deck_of_big_creatures_has_no_theme_of_ours(fixture_cards):
    # Ghalta's stompy deck: no plan this catalogue knows, and saying so is
    # better than naming the nearest one.
    assert detect(_fixture_deck(fixture_cards, "Tramplesaurus Rex")) == []


# --- 5. the catalogue's tags, the queries ----------------------------------------------


def test_every_tag_of_the_catalogue_exists(fixture_cards):
    *_rest, slugs = fixture_cards

    assert themes.SLUGS - slugs == set()


def test_every_theme_has_words_and_a_core():
    for theme in themes.THEMES:
        assert theme.label and theme.info
        assert theme.core is theme.pieces[0]
        assert all(piece.need > 0 for piece in theme.pieces)
    assert len(themes.THEMES) + 1 >= 12


@pytest.mark.django_db
def test_a_deck_reads_its_tags_and_commander_in_two_queries(catalogue):
    owner = get_user_model().objects.create_user(email="themes@example.com",
                                                 password="pw-for-test-only")
    deck = deck_services.import_deck(
        owner=owner, raw=(FIXTURES / "archidekt_sample.csv").read_bytes(), name="Chainer",
        filename="sample.csv").deck

    with CaptureQueriesContext(connection) as queries:
        cards = theme_cards(deck)

    assert len(queries) == 2
    by_name = {card.name: card for card in cards}
    assert by_name[deck.commander.name].commander
    assert any(card.tags for card in cards)
    assert all(card.direct <= card.tags for card in cards)
    assert deck_themes(deck) == detect(cards)
