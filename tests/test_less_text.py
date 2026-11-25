"""The deck page with less text (B-15): number before sentence.

DESIGN.html: a block leads with a number, a symbol or a bar; words only where
a number cannot carry it, then one sentence; the explanation behind "More",
on the methodology page. These tests hold the sentences that left the deck
page gone, and each explanation where "More" points.
"""

import re

from django.urls import reverse

from simulations import cuts
from tests.test_deck_page import client_in, deck, owner  # noqa: F401


def _page(client, deck):  # noqa: F811
    return client.get(deck.get_absolute_url()).content.decode()


def test_the_buttons_carry_their_own_word_and_no_line_explains_them(client_in, deck):  # noqa: F811
    body = _page(client_in, deck)

    for hook in ("data-tutor-link", "data-mulligan-link", "data-in-hand-link"):
        assert hook in body, hook
    assert "which plan does a hand from this deck have?" not in body
    assert "shuffle it, cut it, look at the top cards." not in body


def test_a_deck_without_a_commander_says_it_in_one_sentence_number_first(client_in, deck):  # noqa: F811
    type(deck).objects.filter(pk=deck.pk).update(commander=None)
    deck.refresh_from_db()
    assert not deck.is_commander_deck

    body = _page(client_in, deck)

    note = re.search(r"<p [^>]*data-not-commander>(.*?)</p>", body, re.S).group(1)
    text = " ".join(re.sub(r"<[^>]+>", " ", note).split())
    assert text.startswith(f"{deck.total_with_commander} cards, no commander")
    assert text.count(".") == 1
    assert f'href="{reverse("methodology")}#commander"' in note


def test_each_explanation_that_left_the_page_is_on_the_methodology(client):
    body = client.get(reverse("methodology")).content.decode()
    strategies = body[body.index('id="strategies"'):body.index('id="versions"')]

    assert "calculated from your list" in strategies
    assert "easiest to cut" in strategies
    assert "how many the template has" in strategies


def test_a_card_that_could_go_names_its_role_and_numbers_only():
    cut = cuts.Cut(name="Sol Ring", mana_value=1, kind=cuts.OVER,
                   counted=(("ramp", 14),))

    assert cut.rule == f"Ramp 14 / ~{cuts.BAND_HIGH['ramp']}"


# --- the whole page: a word budget -----------------------------------------------

#: 185 words on 2026-11-25 for the sample deck without a run (552 after a run before B-15).
SENTENCE_BUDGET = 200


def _sentence_words(body: str) -> int:
    """Words in the page's visible sentences: paragraphs and list items of five
    words or more, outside folds and screen-reader text, as a reader sees them."""
    main = body[body.index("<main"):body.index("</main>")]
    main = re.sub(r"<(script|details)\b.*?</\1>", " ", main, flags=re.S)
    main = re.sub(r'<span class="sr-only">.*?</span>', " ", main, flags=re.S)
    total = 0
    for block in re.findall(r"<(?:p|li)\b[^>]*>(.*?)</(?:p|li)>", main, re.S):
        words = re.sub(r"<[^>]+>", " ", block).split()
        if len(words) >= 5:
            total += len(words)
    return total


def test_the_deck_page_keeps_to_its_word_budget(client_in, deck):  # noqa: F811
    words = _sentence_words(_page(client_in, deck))

    assert 0 < words <= SENTENCE_BUDGET, words


def test_what_left_the_page_is_said_shorter_or_behind_more(client_in, deck):  # noqa: F811
    body = _page(client_in, deck)

    for gone in ("Spells by mana value.", "Click a row to see its cards.",
                 "counts in every category it fits", "theme is a plan made of roles",
                 "make value again and again, read from"):
        assert gone not in body, gone
    for anchor in ("drew", "engines"):
        assert f'href="{reverse("methodology")}#{anchor}"' in body, anchor
    assert re.search(r"<details[^>]*>\s*<summary[^>]*>How it is counted</summary>", body)
