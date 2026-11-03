"""Themen-Doc, Paket 1, PR 3: which cards of a deck work together.

What matters:

1. **Pairs come from a theme's rules**: an outlet and a payoff of a found
   theme pair, each pair of cards once, never a card with itself, and
   nothing pairs outside a theme (typal neither).
2. **A pair's strength is three terms**: the rule, the rarity of the tags,
   the theme's fill; the list is varied, so one card cannot fill the top.
3. **An engine is one card per piece**: the commander and repeating cards
   first, at least one card that repeats, or what is missing named.
4. **The precons' engines are the ones a player would name.**
5. **The page**: "Combos & engines" holds the combos, the engines and the
   pairs, escaped, with the same queries whatever the deck's size.
"""

from collections import Counter

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from cards import synergy, themes
from cards.themes import STRONG, ThemeCard, detect
from decks import services as deck_services
from decks import themes as deck_themes
from decks.themes import PAIRS_MAX, PAIRS_SHOWN, Reading, deck_reading
from tests.test_deck_page import _theme
from tests.test_themes import FIXTURES, _fixture_deck, fixture_cards  # noqa: F401

SIZES = {"repeatable-sacrifice-outlet": 900, "free-sacrifice-outlet": 190}
TOTAL = 30000


def _card(name, *, tags=(), direct=(), repeats=False, **extra):
    return ThemeCard(name, extra.pop("type_line", "Creature — Human"),
                     frozenset(tags) | frozenset(direct), frozenset(direct), frozenset(),
                     repeats=repeats, **extra)


def _many(prefix, count, **kwargs):
    return [_card(f"{prefix} {n}", **kwargs) for n in range(count)]


def _aristocrats(payoffs=6, outlets=3, fodder=6, **outlet):
    return (_many("Payoff", payoffs, direct=["death-trigger"])
            + _many("Outlet", outlets, tags=["repeatable-sacrifice-outlet"], **outlet)
            + _many("Fodder", fodder, tags=["repeatable-token-generator"]))


def _read(cards):
    found = detect(cards)
    return found, synergy.pairs(found, cards, SIZES, TOTAL), synergy.engines(
        found, cards, SIZES, TOTAL)


# --- 1. pairs come from a theme's rules ------------------------------------------------


def test_an_outlet_and_a_payoff_pair():
    _found, pairs, _engines = _read(_aristocrats(payoffs=4, outlets=1, fodder=0))

    assert {(pair.first, pair.second) for pair in pairs} == {
        ("Outlet 0", f"Payoff {n}") for n in range(4)}
    assert pairs[0].sentence == "Outlet 0 sacrifices, Payoff 0 pays off each death."


def test_each_pair_of_cards_is_listed_once():
    # A card in two pieces (outlet and fodder) pairs with each payoff by
    # both rules; the stronger of the two stays.
    both = _card("Both", tags=["repeatable-sacrifice-outlet", "repeatable-token-generator"])
    cards = _aristocrats(outlets=2) + [both]

    pairs = synergy.pairs(detect(cards), cards, SIZES, TOTAL)
    keys = [frozenset((pair.first, pair.second)) for pair in pairs]
    with_both = [pair for pair in pairs if "Both" in (pair.first, pair.second)]

    assert len(keys) == len(set(keys))
    assert len(with_both) == 6
    assert all(pair.weight == 0.7 for pair in with_both), "the rarer fodder tag wins"


def test_a_card_never_pairs_with_itself():
    selfish = _card("Selfish", tags=["repeatable-sacrifice-outlet"], direct=["death-trigger"])
    cards = _aristocrats() + [selfish]

    pairs = synergy.pairs(detect(cards), cards, SIZES, TOTAL)

    assert all(pair.first != pair.second for pair in pairs)


def test_nothing_pairs_without_a_theme():
    cards = [_card("Seer", tags=["repeatable-sacrifice-outlet"]),
             _card("Artist", direct=["death-trigger"])]

    assert _read(cards) == ([], [], [])


def test_typal_makes_no_pairs_and_no_engine():
    cards = (_many("Angel", 18, type_line="Creature — Angel", repeats=True)
             + _many("Lord", 6, tags=["typal-angel"], type_line="Enchantment"))

    found, pairs, engines = _read(cards)

    assert [result.theme for result in found] == [themes.TYPAL]
    assert pairs == [] and engines == []


def test_every_theme_says_every_piece():
    for theme in themes.THEMES:
        assert {piece.key for piece in theme.flow} == {piece.key for piece in theme.pieces}
        assert all("%(card)s" in piece.does for piece in theme.pieces)
        assert all(0 < weight <= 1 for _first, _second, weight in theme.rules)


# --- 2. a pair's strength ------------------------------------------------------------


def test_a_rare_tag_is_a_stronger_sign():
    assert synergy.spec(20, TOTAL) == 1.0
    assert synergy.spec(190, TOTAL) > synergy.spec(900, TOTAL) > synergy.spec(9000, TOTAL)
    assert synergy.spec(TOTAL, TOTAL) == synergy.SPEC_FLOOR
    assert synergy.spec(0, TOTAL) == 1.0


def test_a_card_counts_its_rarest_matching_tag_or_the_plain_rarity():
    piece = themes.BY_KEY["aristocrats"].piece("outlet")
    both = _card("Seer", tags=["repeatable-sacrifice-outlet", "free-sacrifice-outlet"])
    by_type = _card("Bear")

    assert synergy.card_spec(both, piece, SIZES, TOTAL) == synergy.spec(190, TOTAL)
    assert synergy.card_spec(by_type, piece, SIZES, TOTAL) == synergy.SPEC_PLAIN


def test_the_score_is_the_product_of_its_terms():
    pair = synergy.Pair("aristocrats", "A", "B", ("", ""), 0.7, 0.5, 0.8, 0.9)

    assert pair.score == round(0.7 * 0.5 * 0.8 * 0.9, 3)


def test_a_stronger_rule_scores_higher():
    sizes = {"repeatable-sacrifice-outlet": 900, "repeatable-token-generator": 900}
    cards = _aristocrats(payoffs=4, outlets=1, fodder=1)
    found = detect(cards)

    scored = {(pair.first, pair.second): pair
              for pair in synergy.pairs(found, cards, sizes, TOTAL)}

    outlet, fodder = scored["Outlet 0", "Payoff 0"], scored["Fodder 0", "Payoff 0"]
    assert (outlet.weight, fodder.weight) == (1.0, 0.7)
    assert outlet.score > fodder.score
    assert outlet.density == found[0].fill


def test_one_card_cannot_fill_the_top_rows():
    # One rare outlet would pair with every payoff at the top score.
    rare = _card("Rare Outlet", tags=["free-sacrifice-outlet"])
    cards = _aristocrats(outlets=3) + [rare]

    top = synergy.pairs(detect(cards), cards, SIZES, TOTAL)[:PAIRS_SHOWN]
    named = Counter(name for pair in top for name in (pair.first, pair.second))

    assert max(named.values()) == 1


# --- 3. engines ----------------------------------------------------------------------


def test_an_engine_is_one_card_per_piece_in_the_rules_order():
    _found, _pairs, engines = _read(_aristocrats(repeats=True))

    engine = engines[0]
    assert engine.theme == "aristocrats" and engine.level == STRONG
    assert engine.cards == ("Outlet 0", "Payoff 0", "Fodder 0")
    assert engine.sentence == ("Outlet 0 sacrifices, Payoff 0 pays off each death, "
                               "Fodder 0 brings more bodies.")
    assert engine.repeating == ("Outlet 0",) and engine.missing == ()


def test_the_commander_and_a_repeating_card_come_first():
    cards = _aristocrats() + [_card("Zulu Outlet", tags=["repeatable-sacrifice-outlet"],
                                    repeats=True),
                              _card("Zed Payoff", direct=["death-trigger"], commander=True)]

    engine = _read(cards)[2][0]

    assert engine.cards[:2] == ("Zulu Outlet", "Zed Payoff")


def test_an_engine_needs_a_card_that_repeats():
    assert _read(_aristocrats())[2] == []


def test_an_engine_a_piece_short_names_what_is_missing():
    _found, _pairs, engines = _read(_aristocrats(outlets=0))

    engine = engines[0]
    assert engine.cards == ("Payoff 0", "Fodder 0")
    assert engine.missing == ("Sacrifice outlet",)
    assert engine.pieces == 3


def test_the_sentence_speaks_the_page_s_language():
    engine = _read(_aristocrats(repeats=True))[2][0]

    with translation.override("de"):
        assert "Outlet 0" in engine.sentence and "sacrifices" not in engine.sentence


# --- 4. the precons ------------------------------------------------------------------


def _precon(fixture_cards, name):  # noqa: F811
    cards = [ThemeCard(card.name, card.type_line, card.tags, card.direct, card.mechanics,
                       card.quantity, card.commander, card.every_type,
                       themes.repeats({}, card.tags))
             for card in _fixture_deck(fixture_cards, name)]
    sizes = Counter(slug for card in {card.name: card for card in cards}.values()
                    for slug in card.tags & themes.SLUGS)
    found = detect(cards)
    return {engine.theme: engine for engine in synergy.engines(found, cards, sizes, 3070)}


@pytest.mark.parametrize(("deck", "theme", "cards"), [
    ("Chainer, Dementia Master", "aristocrats", {"Ashnod's Altar", "Grave Pact"}),
    ("Chainer, Dementia Master", "reanimator", {"Chainer, Dementia Master"}),
    ("Prismari Artistry", "spellslinger", {"Rootha, Mastering the Moment"}),
    ("Limit Break (FINAL FANTASY VII)", "voltron", {"Cloud, Ex-SOLDIER"}),
])
def test_a_precon_s_engine_is_the_one_a_player_names(fixture_cards, deck, theme,  # noqa: F811
                                                     cards):
    engine = _precon(fixture_cards, deck)[theme]

    assert cards <= set(engine.cards)
    assert not engine.missing


# --- 5. the page ---------------------------------------------------------------------


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(email="synergy@example.com",
                                                password="pw-for-test-only")


@pytest.fixture
def deck(owner, catalogue):
    return deck_services.import_deck(
        owner=owner, raw=(FIXTURES / "archidekt_sample.csv").read_bytes(), name="Chainer",
        filename="sample.csv").deck


@pytest.fixture
def client_in(client, owner):
    client.force_login(owner)
    return client


def _engine(name="Viscera Seer", missing=()):
    return synergy.Engine("aristocrats", "Aristocrats", STRONG,
                          ((name, "%(card)s sacrifices"),
                           ("Blood Artist", "%(card)s pays off each death")),
                          missing, ("Blood Artist",))


def _pair(n=0, first="Viscera Seer"):
    return synergy.Pair("aristocrats", first, f"Payoff {n}",
                        ("%(card)s sacrifices", "%(card)s pays off each death"),
                        1.0, 0.5, 0.8, 0.9)


def _show(monkeypatch, **reading):
    monkeypatch.setattr("decks.views.deck_reading", lambda _deck: Reading(**reading))


def test_the_section_holds_combos_engines_and_pairs(client_in, deck, monkeypatch):
    _show(monkeypatch, themes=[_theme()], engines=[_engine()],
          pairs=[_pair(n) for n in range(PAIRS_SHOWN)],
          more_pairs=[_pair(n) for n in range(PAIRS_SHOWN, PAIRS_MAX)], pairs_left_out=7)

    body = client_in.get(deck.get_absolute_url()).content.decode()

    section = body[body.index('id="combos"'):body.index('id="cards"')]
    for words in ("Combos & engines", 'id="combo-panel"', "Engines", "Synergies",
                  "Viscera Seer + Blood Artist", "Aristocrats · strong",
                  "Viscera Seer sacrifices, Blood Artist pays off each death.",
                  "Works again and again: Blood Artist.", "25 more pairs",
                  "7 weaker pairs of the same kinds are not listed.",
                  "Rule 1.0 × rarity 0.50 × 0.80 × fill 0.90 = 0.36."):
        assert words in section, words


def test_an_engine_a_piece_short_is_dashed_and_says_so(client_in, deck, monkeypatch):
    _show(monkeypatch, themes=[_theme()], engines=[_engine(missing=("Makes bodies to sacrifice",))])

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "border-dashed" in body
    assert "2 of 3 pieces. Still missing:" in body
    assert "<strong>Makes bodies to sacrifice</strong>" in body


@pytest.mark.parametrize(("themes_found", "words"), [
    ([], "we found no theme"),
    ([_theme()], "has a card that works again and again"),
])
def test_no_engine_is_said(client_in, deck, monkeypatch, themes_found, words):
    _show(monkeypatch, themes=themes_found)

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert words in body
    assert "Synergies" not in body


def test_card_names_in_engines_and_pairs_are_escaped(client_in, deck, monkeypatch):
    evil = "<script>alert(1)</script>"
    _show(monkeypatch, themes=[_theme()], engines=[_engine(name=evil)],
          pairs=[_pair(first=evil)])

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert evil not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; sacrifices" in body


def test_the_combo_refresh_still_swaps_the_panel_alone(client_in, deck):
    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert 'hx-target="#combo-panel"' in body
    assert body.count('id="combo-panel"') == 1


@pytest.mark.django_db
def test_the_reading_costs_three_queries_once_the_sizes_are_cached(deck):
    cache.delete(deck_themes.TAG_SIZES_KEY)
    deck_reading(deck)

    with CaptureQueriesContext(connection) as queries:
        reading = deck_reading(deck)

    # The owner's locks, the quantities, the cards with their tags.
    assert len(queries) == 3
    assert reading.themes == deck_themes.deck_themes(deck)


@pytest.mark.django_db
def test_the_tag_sizes_count_cards_of_the_catalogue(catalogue):
    cache.delete(deck_themes.TAG_SIZES_KEY)

    sizes, total = deck_themes.tag_sizes()

    assert total > 0
    assert set(sizes) <= themes.SLUGS
    assert all(0 < size <= total for size in sizes.values())
