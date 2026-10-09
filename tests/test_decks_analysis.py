"""Phase 1: what the deck page claims about a deck.

Nothing here is simulated. Every number is exact arithmetic over the card list,
which is what lets the page state them plainly - and what makes a wrong one a
real bug rather than a modelling disagreement.

The verdicts are tested for their *sentences*, not only their booleans. A red
badge with no explanation next to it is how users learn to ignore badges.
"""


import pytest
from django.contrib.auth import get_user_model

from cards.models import OracleCard
from decks.analysis import KARSTEN_MAX, KARSTEN_MIN, analyse
from decks.models import Deck, DeckCard

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def deck(catalogue):
    owner = User.objects.create_user(email="a@example.com", password="pw-for-test-only")
    deck = Deck.objects.create(owner=owner, name="Chainer")
    deck.commander = OracleCard.objects.get(front_name="Chainer, Dementia Master")
    deck.save()
    return deck


def add(deck, name, quantity=1):
    DeckCard.objects.create(
        deck=deck, oracle_card=OracleCard.objects.get(front_name=name), quantity=quantity
    )


def test_lands_and_spells_are_counted_separately(deck):
    add(deck, "Swamp", 35)
    add(deck, "Sol Ring")
    add(deck, "Necropotence")

    result = analyse(deck)
    assert result.land_count == 35
    assert result.nonland_count == 2
    assert result.total_cards == 37


def test_the_curve_excludes_lands(deck):
    """A 35-land deck whose curve shows 35 cards at zero is a useless chart."""
    add(deck, "Swamp", 35)
    add(deck, "Sol Ring")  # mana value 1

    result = analyse(deck)
    assert result.curve[0] == 0
    assert result.curve[1] == 1


def test_expensive_cards_land_in_the_top_bucket(deck):
    add(deck, "Lim-Dûl the Necromancer")  # mana value 7
    result = analyse(deck)
    assert result.curve[7] == 1


def test_land_count_is_judged_against_karsten_and_says_so(deck):
    add(deck, "Swamp", KARSTEN_MIN)
    inside = analyse(deck).lands_verdict
    assert inside.ok
    assert "Karsten" in inside.detail

    deck.entries.all().delete()
    add(deck, "Swamp", KARSTEN_MIN - 10)
    outside = analyse(deck).lands_verdict
    assert not outside.ok
    assert "Below" in outside.detail
    assert str(KARSTEN_MIN) in outside.detail and str(KARSTEN_MAX) in outside.detail


def test_game_changers_drive_the_bracket_hint(deck):
    add(deck, "Necropotence")
    add(deck, "Demonic Tutor")

    verdict = analyse(deck).bracket_verdict
    assert verdict.ok
    assert "Bracket 3 allows up to 3" in verdict.detail


def test_a_deck_over_the_game_changer_limit_is_named_as_bracket_4(deck):
    changers = OracleCard.objects.filter(game_changer=True).values_list("front_name", flat=True)
    for name in changers[:4]:
        add(deck, name)

    verdict = analyse(deck).bracket_verdict
    assert not verdict.ok
    assert "Bracket 4" in verdict.detail


def test_deck_size_is_checked_against_one_hundred(deck):
    add(deck, "Swamp", 99)
    size = analyse(deck).legality[0]
    assert size.ok, "99 cards plus a commander is exactly 100"

    deck.entries.all().delete()
    add(deck, "Swamp", 50)
    assert not analyse(deck).legality[0].ok


def test_basic_lands_are_exempt_from_the_singleton_rule(deck):
    add(deck, "Swamp", 31)
    add(deck, "Sol Ring")

    singleton = analyse(deck).legality[1]
    assert singleton.ok


def test_a_duplicated_nonbasic_breaks_singleton_and_is_named(deck):
    add(deck, "Sol Ring", 2)
    singleton = analyse(deck).legality[1]
    assert not singleton.ok
    assert "Sol Ring" in singleton.detail


def test_colour_identity_violations_are_listed(deck):
    """The commander is mono-black; anything else is illegal and gets named."""
    off_colour = (
        OracleCard.objects.exclude(color_identity=[])
        .exclude(color_identity=["B"])
        .first()
    )
    if off_colour is None:
        pytest.skip("fixture has no off-colour card")

    add(deck, off_colour.front_name)
    identity = analyse(deck).legality[2]
    assert not identity.ok
    assert off_colour.name in identity.detail


def test_a_deck_without_a_commander_fails_plainly(catalogue):
    owner = User.objects.create_user(email="b@example.com", password="pw-for-test-only")
    deck = Deck.objects.create(owner=owner, name="No commander")
    add(deck, "Sol Ring")

    verdicts = analyse(deck).legality
    no_commander = next(v for v in verdicts if "commander" in v.message.lower())
    assert not no_commander.ok


def test_a_banned_card_in_the_99_is_named(deck):
    """Only the commander's legality was ever checked, until the 2026-09-25 review."""
    add(deck, "Sol Ring")
    card = OracleCard.objects.get(front_name="Sol Ring")
    card.legalities = {**card.legalities, "commander": "banned"}
    card.save(update_fields=["legalities"])

    legal = analyse(deck).legality[-1]
    assert not legal.ok
    assert "Banned: Sol Ring" in legal.detail
    assert not analyse(deck).is_legal


def test_a_deck_of_legal_cards_passes_the_card_check(deck):
    add(deck, "Sol Ring")
    assert analyse(deck).legality[-1].ok


def test_coverage_reports_what_the_reader_could_not_resolve(deck):
    """The honesty number, present from Phase 1 so no page can overclaim."""
    add(deck, "Phyrexian Tower")  # mana for a sacrificed creature - unresolvable
    add(deck, "Sol Ring")  # fully resolved

    result = analyse(deck)
    assert 1 <= result.cards_needing_review < result.total_cards


def test_an_empty_deck_does_not_divide_by_zero(deck):
    result = analyse(deck)
    assert result.cards_needing_review == 0
    assert result.average_mv == 0.0
