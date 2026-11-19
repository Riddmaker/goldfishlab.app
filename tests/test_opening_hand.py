""""Your opening hand" under the mana curve (deck page v2, PR G, decision D4).

What these tests hold:

1. **The numbers are the textbook's**: the columns are the hypergeometric
   chance of 0 to 7 lands in seven, they add up to the whole, and the 2 to 4
   a hand keeps on are the sum of their three columns.
2. **A deck too small to deal a hand** shows no box rather than a wrong one.
3. **The page** shows the box under the curve, before any run, with the way
   to "Keep or mulligan?", in the page's language.
"""

from math import comb

import pytest
from django.utils import translation

from simulations import report
from tests.test_deck_page import client_in, deck, owner  # noqa: F401
from tests.test_i18n_german import german  # noqa: F401


def test_the_columns_are_the_hypergeometric_chance_and_add_up():
    hand = report.opening_hand(99, 37)

    exact = [100 * comb(37, k) * comb(62, 7 - k) / comb(99, 7) for k in range(8)]
    assert [column.share for column in hand.columns] == pytest.approx(exact)
    assert sum(column.share for column in hand.columns) == pytest.approx(100)
    assert [column.lands for column in hand.columns if column.keepable] == [2, 3, 4]
    assert hand.keepable == pytest.approx(sum(exact[2:5]))
    assert hand.likeliest == 3


def test_a_deck_without_lands_never_keeps():
    hand = report.opening_hand(60, 0)

    assert hand.keepable == 0
    assert hand.columns[0].share == pytest.approx(100)


def test_too_small_a_deck_deals_no_hand():
    assert report.opening_hand(6, 3) is None


def test_the_labels_are_in_the_page_s_language():
    hand = report.opening_hand(99, 37)

    with translation.override("de"):
        assert hand.keepable_label.endswith(" %")


@pytest.mark.django_db
def test_the_page_shows_the_box_and_the_way_to_the_quiz(client_in, deck):  # noqa: F811
    response = client_in.get(deck.get_absolute_url())
    body = response.content.decode()

    hand = response.context["opening_hand"]
    analysis = response.context["analysis"]
    assert hand == report.opening_hand(analysis.total_cards, analysis.land_count)
    assert "data-opening-hand" in body
    assert body.index("data-curve") < body.index("data-opening-hand") < body.index(
        "What is in it")
    assert f'href="/decks/{deck.pk}/mulligan/" data-hand-quiz' in body
    assert body.count("deck-hand-keep") == 3


@pytest.mark.django_db
def test_the_box_speaks_german(client_in, deck, german):  # noqa: F811
    client_in.cookies["django_language"] = "de"
    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "Deine Starthand" in body
    assert "deiner Starthände mit sieben Karten haben 2 bis 4 Länder" in body
