"""A theme's chance to stand by turn four, and its cards shown (deck page v2, PR E).

What these tests hold:

1. **The chance is exact**: a card of every piece among the cards seen,
   pieces that share cards counted once - checked against counting every
   possible draw of a small library.
2. **A piece the commander holds is always there**, and a piece without a
   card in the library never is.
3. **A card is shown by its deck's picture**, by full name or front face,
   else the picture it was given, else its name.
"""

from itertools import combinations
from types import SimpleNamespace

import pytest
from django.template import Context, Template

from cards.themes import Found, Part
from simulations import theme_odds


def _reading(pk, name, *, commander=False, quantity=1):
    return SimpleNamespace(oracle_card=SimpleNamespace(pk=pk, name=name,
                                                       front_name=name.split(" // ")[0]),
                           is_commander=commander, quantity=quantity)


def _found(*pieces):
    parts = tuple(Part(f"p{index}", f"Piece {index}", len(cards), 1, tuple(cards))
                  for index, cards in enumerate(pieces))
    return Found("aristocrats", "Aristocrats", "", "strong", 1.0, parts)


def _counted(library: int, pieces, seen: int) -> float:
    """Every draw of `seen` of cards 0..library-1, counted."""
    hits = total = 0
    for drawn in combinations(range(library), seen):
        total += 1
        hits += all(set(drawn) & piece for piece in pieces)
    return round(100 * hits / total, 1)


def test_overlapping_pieces_are_counted_exactly():
    readings = [_reading(index, f"Card {index}") for index in range(9)]
    found = _found(["Card 0", "Card 1", "Card 2"], ["Card 2", "Card 3"], ["Card 4"])

    chance = theme_odds.stands_by(found, readings, seen=4)

    assert chance == _counted(9, [{0, 1, 2}, {2, 3}, {4}], 4)


def test_a_double_faced_card_is_one_card_whichever_name_a_piece_uses():
    readings = [_reading(0, "Westvale Abbey // Ormendahl")] + [
        _reading(index, f"Card {index}") for index in range(1, 6)]
    found = _found(["Westvale Abbey // Ormendahl"], ["Westvale Abbey", "Card 1"])

    assert theme_odds.stands_by(found, readings, seen=3) == _counted(6, [{0}, {0, 1}], 3)


def test_the_commander_s_piece_is_always_there():
    readings = [_reading(99, "Boss", commander=True)] + [
        _reading(index, f"Card {index}") for index in range(6)]
    with_boss = _found(["Boss"], ["Card 0"])

    assert theme_odds.stands_by(with_boss, readings, seen=3) == _counted(6, [{0}], 3)


def test_a_piece_without_a_card_never_stands():
    readings = [_reading(index, f"Card {index}") for index in range(5)]

    assert theme_odds.stands_by(_found(["Card 0"], []), readings) == 0.0
    assert theme_odds.stands_by(_found(["Card 0"]), []) == 0.0


def test_seeing_the_whole_library_is_certain():
    readings = [_reading(index, f"Card {index}") for index in range(5)]

    assert theme_odds.stands_by(_found(["Card 0"], ["Card 4"]), readings, seen=10) == 100.0


# --- {% card_tile %} -----------------------------------------------------------------


class _Deck:
    def __init__(self, images):
        self.images, self.calls = images, 0

    def card_images(self):
        self.calls += 1
        return self.images


def _tile(deck, source, **context):
    return Template("{% load cards_shown %}" + source).render(Context({"deck": deck, **context}))


@pytest.mark.parametrize(("name", "expected"), [
    ("Blood Artist", 'src="https://cards.scryfall.io/normal/artist.jpg"'),
    ("Westvale Abbey // Ormendahl", 'src="https://cards.scryfall.io/normal/abbey.jpg"'),
])
def test_a_tile_shows_the_deck_s_picture(name, expected):
    deck = _Deck({"Blood Artist": "https://cards.scryfall.io/normal/artist.jpg",
                  "Westvale Abbey": "https://cards.scryfall.io/normal/abbey.jpg"})

    html = _tile(deck, "{% card_tile name 'pays off' %}{% card_tile name %}".replace(
        "name", f"'{name}'"))

    assert expected in html and "pays off" in html
    assert deck.calls == 1, "one read per deck and render"


def test_a_card_the_deck_lacks_shows_its_fallback_or_its_name():
    deck = _Deck({})

    fallback = "{% card_tile 'A' fallback='https://x.test/a.jpg' %}"

    assert 'src="https://x.test/a.jpg"' in _tile(deck, fallback)
    assert "card-tile-text" in _tile(deck, "{% card_tile 'A' %}")


def test_a_name_is_escaped():
    html = _tile(_Deck({}), "{% card_tile name name %}", name="<b>x</b>")

    assert "<b>" not in html and "&lt;b&gt;" in html
