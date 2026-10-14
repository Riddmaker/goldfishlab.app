"""Card model phase 3, PR 9: the derived categories players see.

What these tests hold:

1. **`Card.categories`** is the derived roles and mechanics once a profile
   is derived again (`categories_version`), and `role_tags` until then; an
   owner's replaced list still wins over every key it could name.
2. **The main role** is the derived one, or the first of the owner's in the
   fixed order, and none before the profile is derived again.
3. **The six mechanics** are counted, charted in a chart of their own,
   named in every list, and offered as strategies with candidates.
4. **The deck page** counts every card in a category and, under it, the
   cards whose main role it is; those and the cards without one make the
   deck.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards import categories
from cards.models import DerivedProfile, OracleCard
from decks import services as deck_services
from simulation import analysis
from simulation.cards import CREATURE, Card, DeckDefinition
from simulations import deck_cards, report, review, strategies
from simulations.engine import adapter

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


def _profile(**fields):
    base = {"categories_version": categories.VERSION, "role_tags": ["creature", "ramp"],
            "primary_role": "", "secondary_role": "", "mechanics": []}
    return SimpleNamespace(**(base | fields))


# --- Card.categories ------------------------------------------------------------------


def test_a_profile_not_derived_again_keeps_the_community_roles():
    profile = _profile(categories_version=0, role_tags=["creature", "draw_engine", "draw"])
    assert adapter.derived_categories(profile) == {"creature", "draw_engine", "draw"}


def test_a_derived_profile_gives_its_roles_and_mechanics():
    profile = _profile(primary_role="draw", secondary_role="",
                       mechanics=["sac_outlet", "lifegain"])
    assert adapter.derived_categories(profile) == {"draw", "sac_outlet", "lifegain"}


def test_the_owners_list_wins_over_what_it_could_name():
    profile = _profile(primary_role="draw", mechanics=["sac_outlet", "lifegain"])
    found = adapter._categories(profile, {"tags": ["removal"]}, adapter.USER_SCOPE)
    # The list drops draw and the sacrifice outlet; lifegain came after it.
    assert found == {"removal", "lifegain"}
    builtin = adapter._categories(profile, {"tags": ["removal"]}, adapter.BUILTIN_SCOPE)
    assert builtin == {"draw", "sac_outlet", "lifegain"}


def test_the_main_role_is_the_derived_one_or_the_first_in_the_order():
    card = SimpleNamespace(profile=_profile(primary_role="draw", secondary_role="ramp"))
    assert adapter.primary_role(card, frozenset({"draw", "ramp"})) == "draw"
    # The owner's list left draw out: its first role in the fixed order.
    assert adapter.primary_role(card, frozenset({"ramp", "removal"})) == "removal"
    assert adapter.primary_role(card, frozenset({"lifegain"})) == ""
    before = SimpleNamespace(profile=_profile(categories_version=0, primary_role="draw"))
    assert adapter.primary_role(before, frozenset({"draw"})) == ""


# --- the six mechanics ------------------------------------------------------------------


def test_every_mechanic_has_a_name_and_a_meaning_on_the_page():
    assert [key for key, _ in report.SEEN_MECHANIC_ROLES] == list(analysis.SEEN_MECHANICS)
    assert analysis.SEEN_MECHANICS == categories.NEW_MECHANICS
    assert set(report.SEEN_MECHANIC_INFO) == set(analysis.SEEN_MECHANICS)
    assert set(analysis.SEEN_STRATEGIES) | set(analysis.SEEN_MECHANICS) | {"recursive"} \
        == set(categories.MECHANICS)


def test_a_mechanic_is_counted_like_a_strategy():
    card = Card("Test Pridemate", 2, 0, 2, CREATURE, frozenset(),
                types=frozenset({"creature"}), categories=frozenset({"lifegain", "sac_outlet"}))
    keys, _ = analysis.seen_groups(DeckDefinition(name="d", commander=None, library=(card,)))
    assert keys == ("type:creature", "role:sac_outlet", "role:lifegain", "mv:2")


def _result(groups: dict) -> dict:
    return {"iterations": 10, "turns": 2,
            "seen": {key: {"games": [1, 2], "cards": [1, 2]} for key in groups}}


def test_the_mechanics_have_a_chart_of_their_own_and_an_old_run_none():
    seen = report.seen(_result(["role:sac_outlet", "role:lifegain", "role:tax"]))
    assert [line.key for line in seen["strategies"].lines] == ["sac_outlet"]
    assert [line.key for line in seen["mechanics"].lines] == ["lifegain", "tax"]
    old = report.seen(_result(["role:sac_outlet"]))
    assert not old["mechanics"].lines


def test_a_chart_never_needs_more_lines_than_it_has_colours():
    # assets/css/input.css styles .seen-line-0 to .seen-line-7.
    for roles in (report.SEEN_ROLES, report.SEEN_STRATEGY_ROLES, report.SEEN_MECHANIC_ROLES):
        assert len(roles) <= 8


# --- in a deck ---------------------------------------------------------------------------


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="shown@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    ).deck


def _grid(deck):
    readings = adapter.readings(deck)
    return deck_cards.cards(readings, review.queue(deck, readings))


@pytest.mark.django_db
def test_the_bars_count_every_card_and_its_main_role_once(deck):
    grid = _grid(deck)
    _, bars = deck_cards.bars(grid)
    population = sum(card.quantity for card in grid)

    assert all(bar.slots is not None and bar.slots <= bar.count for bar in bars)
    # The main roles and the cards without one make the deck.
    assert sum(bar.slots for bar in bars) + deck_cards.unranked(grid) == population
    for card in grid:
        assert not card.primary or card.primary in card.categories


@pytest.mark.django_db
def test_before_the_profiles_are_derived_again_there_is_no_main_role(deck):
    DerivedProfile.objects.update(categories_version=0)
    grid = _grid(deck)
    _, bars = deck_cards.bars(grid)

    assert all(bar.slots is None for bar in bars)
    assert deck_cards.unranked(grid) is None


@pytest.mark.django_db
def test_the_deck_page_says_main_and_what_has_none(client, owner, deck):
    client.force_login(owner)
    page = client.get(reverse("decks:detail", args=[deck.pk])).content.decode()
    assert " main</span>" in page
    assert "in its main role." in page
    DerivedProfile.objects.update(categories_version=0)
    page = client.get(reverse("decks:detail", args=[deck.pk])).content.decode()
    assert "in its main role." not in page


# --- the strategies ------------------------------------------------------------------------


@pytest.mark.django_db
def test_candidates_come_from_the_derived_categories(owner, deck):
    readings = adapter.readings(deck)
    in_deck = {reading.oracle_card.pk for reading in readings}
    colours = sorted(strategies.identity(readings))
    spare = (OracleCard.objects.exclude(pk__in=in_deck)
             .filter(legalities__commander="legal", game_changer=False,
                     color_identity__contained_by=colours)
             .exclude(profile__kind=DerivedProfile.Kind.LAND))
    lifegain, secondary, old = list(spare[:3])
    DerivedProfile.objects.filter(oracle_card=lifegain).update(mechanics=["lifegain"])
    DerivedProfile.objects.filter(oracle_card=secondary).update(secondary_role="wipe")
    DerivedProfile.objects.filter(oracle_card=old).update(categories_version=0,
                                                          role_tags=["wipe"])

    assert lifegain in strategies.candidates(readings, "lifegain", limit=1000)
    wipes = strategies.candidates(readings, "wipe", limit=1000)
    assert secondary in wipes and old in wipes
    # A derived profile is read by its new fields, not its community roles.
    DerivedProfile.objects.filter(oracle_card=secondary).update(secondary_role="",
                                                                role_tags=["wipe"])
    assert secondary not in strategies.candidates(readings, "wipe", limit=1000)


def test_a_mechanic_is_a_strategy_with_its_label():
    assert strategies.LABELS["lifegain"] == "Lifegain"
    assert set(analysis.SEEN_MECHANICS) <= set(strategies.LABELS)
