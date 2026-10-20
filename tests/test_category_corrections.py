"""Card model phase 3, PR 10: an owner corrects what a card counts as.

What these tests hold:

1. **A correction is relative to the card data.** ``category_add`` and
   ``category_remove`` change the derived categories, so a later reading
   still reaches the card; ``category_primary`` names the main role; "Back
   to the card data" takes all three back.
2. **The engine plays by the categories** (card model PR 17): its four roles
   are read off them, so one correction moves the statistics and the games.
3. **Only the vocabulary is stored**, each category once, never both added
   and removed.
4. **A whole list from before is converted** (migrations 0017 and 0019) so
   the engine plays the card and the statistics count it as before.
5. **The card page** shows the categories with who decided each, and every
   change is a plain form post on the owner's own deck.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from cards import categories
from decks import services as deck_services
from simulation.cards import DRAW_ENGINE
from simulations import annotations, provenance
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.models import CardAnnotation

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


def _profile(**fields):
    base = {"categories_version": categories.VERSION, "role_tags": ["creature", "draw"],
            "primary_role": "draw", "secondary_role": "", "mechanics": ["lifegain"]}
    return SimpleNamespace(**(base | fields))


# --- the correction itself ----------------------------------------------------------


def test_add_and_remove_are_relative_to_the_card_data():
    base = frozenset({"draw", "lifegain"})
    added = annotations.apply_category({}, "add", "ramp", base)
    assert added == {"category_add": ["ramp"]}
    removed = annotations.apply_category(added, "remove", "lifegain", base)
    assert removed == {"category_add": ["ramp"], "category_remove": ["lifegain"]}
    # Each takes the other back rather than stacking up.
    assert annotations.apply_category(removed, "add", "lifegain", base) == {
        "category_add": ["ramp"]}
    assert annotations.apply_category(added, "remove", "ramp", base) == {}


def test_a_category_the_card_already_has_is_not_added_twice():
    assert annotations.apply_category({}, "add", "draw", frozenset({"draw"})) == {}


def test_the_main_role_is_counted_and_goes_with_its_removal():
    said = annotations.apply_category({}, "primary", "removal", frozenset({"draw"}))
    assert said == {"category_add": ["removal"], "category_primary": "removal"}
    assert annotations.apply_category(said, "remove", "removal", frozenset()) == {}


def test_a_mechanic_is_no_main_role_and_an_unknown_key_no_category():
    for action, key in (("primary", "lifegain"), ("add", "gamechanger"), ("drop", "draw")):
        with pytest.raises(ValueError):
            annotations.apply_category({}, action, key)


def test_back_to_the_card_data_keeps_the_rest():
    stored = {"priority": 5, "category_add": ["ramp"], "category_primary": "ramp"}
    assert annotations.apply_category(stored, "reset") == {"priority": 5}


def test_a_deck_starts_from_what_it_inherits_and_can_empty_it():
    inherited = {"category_add": ["ramp", "tokens"]}
    said = annotations.apply_category({}, "remove", "tokens", frozenset(), inherited)
    assert said == {"category_add": ["ramp"]}
    # Emptied, it is stored empty: an absent key would let "all decks" through.
    emptied = annotations.apply_category(said, "remove", "ramp", frozenset(), inherited)
    assert emptied == {"category_add": []}
    # A list the change does not touch stays with "all decks".
    assert annotations.apply_category({}, "remove", "draw", frozenset({"draw"}),
                                      inherited) == {"category_remove": ["draw"]}


def test_a_category_another_deck_added_is_added_for_all_decks_too():
    # Relative to the card data, not to what this deck counts: a deck's own
    # addition does not make adding it for all decks a no-op.
    assert annotations.apply_category({}, "add", "ramp", frozenset({"draw"})) == {
        "category_add": ["ramp"]}


# --- what the engine and the statistics read ----------------------------------------


def test_the_corrections_change_the_derived_categories():
    profile = _profile()
    found = adapter._categories(profile, {"category_add": ["ramp"],
                                          "category_remove": ["lifegain"]})
    assert found == {"draw", "ramp"}
    # A deck takes away the main role all decks gave: it stays away.
    away = adapter._categories(profile, {"category_add": ["ramp"], "category_primary": "ramp",
                                         "category_remove": ["ramp"]})
    assert away == {"draw", "lifegain"}
    assert adapter._categories(profile, {"category_primary": "ramp"}) == {
        "draw", "lifegain", "ramp"}


def test_the_owners_main_role_wins_while_the_card_counts_in_it():
    card = SimpleNamespace(profile=_profile(secondary_role="ramp"))
    both = frozenset({"draw", "ramp"})
    assert adapter.primary_role(card, both, {"category_primary": "ramp"}) == "ramp"
    assert adapter.primary_role(card, frozenset({"draw"}), {"category_primary": "ramp"}) \
        == "draw"
    # Not before the profile is derived again: no card has a main role yet.
    before = SimpleNamespace(profile=_profile(categories_version=0))
    assert adapter.primary_role(before, both, {"category_primary": "ramp"}) == ""


def _engine(**fields):
    return _profile(**({"kind": "enchantment", "tempo": {}} | fields))


REPEATS = {"triggered:0": {"rate": "event", "needs": "none", "draws": True}}
ONCE = {"resolves:0": {"rate": "one_shot", "needs": "none", "draws": True}}
#: Draws once when it enters, and taps for mana again and again.
ELSEWHERE = {"triggered:0": {"rate": "one_shot", "needs": "none", "draws": True},
             "mana:0": {"rate": "repeatable", "needs": "none"}}


def test_three_engine_roles_are_categories_of_their_own():
    found = adapter.engine_roles(frozenset({"sac_outlet", "recursive", "drain_payoff",
                                            "removal"}), _engine())
    assert found == {"sac_outlet", "recursive", "drain_payoff"}


@pytest.mark.parametrize(("fields", "engine"), [
    # Drawing again and again is a draw engine; drawing once is a draw spell.
    ({"tempo": REPEATS}, True),
    ({"tempo": ONCE}, False),
    # The ability that repeats has to be the one that draws ...
    ({"tempo": ELSEWHERE}, False),
    # ... where the blocks name one: a card that digs, said to draw.
    ({"tempo": {"triggered:0": {"rate": "event", "needs": "none"}}}, True),
    # Without blocks there is no tempo: a permanent that draws is one ...
    ({}, True),
    # ... a spell only when the community says so.
    ({"kind": "sorcery"}, False),
    ({"kind": "sorcery", "role_tags": ["draw", "draw_engine"]}, True),
    # Derived before an ability said whether it draws: the same as no blocks.
    ({"tempo": ELSEWHERE, "categories_version": 1}, True),
    ({"tempo": ONCE, "categories_version": 1, "kind": "sorcery"}, False),
])
def test_a_draw_engine_is_a_card_that_draws_again_and_again(fields, engine):
    profile = _engine(**fields)
    assert (DRAW_ENGINE in adapter.engine_roles(frozenset({"draw"}), profile)) is engine
    assert not adapter.engine_roles(frozenset(), profile)


def test_before_the_profile_is_derived_the_community_names_the_four():
    profile = _engine(categories_version=0, tempo=ONCE)
    assert adapter.engine_roles(frozenset({"draw", "draw_engine", "ramp"}), profile) == {
        DRAW_ENGINE}


def test_a_list_of_engine_roles_is_said_in_categories():
    profile = _engine(tempo=REPEATS)
    said, missed = adapter.roles_as_categories({"priority": 3}, profile,
                                               {"sac_outlet"})
    assert said == {"priority": 3, "category_add": ["sac_outlet"],
                    "category_remove": ["draw"]}
    assert not missed
    assert adapter.engine_roles(adapter._categories(profile, said), profile) == {
        "sac_outlet"}
    # A correction the list takes back goes rather than stacking up.
    back, _ = adapter.roles_as_categories(said, profile, {DRAW_ENGINE})
    assert back == {"priority": 3}
    # A draw spell cannot be made a draw engine by its categories.
    _, missed = adapter.roles_as_categories({}, _engine(tempo=ONCE), {DRAW_ENGINE})
    assert missed == {DRAW_ENGINE}


def test_the_provenance_row_reads_the_categories():
    row = next(spec for spec in provenance.FIELDS if spec.attribute == "roles")
    assert row.fallback == "categories"
    assert "roles" not in provenance.ANNOTATION_KEY


# --- what is stored -------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    {"category_primary": "lifegain"},
    {"category_add": ["gamechanger"]},
    {"category_add": ["ramp", "ramp"]},
    {"category_add": "ramp"},
    {"category_add": ["ramp"], "category_remove": ["ramp"]},
    {"engine_tags": ["sac_outlet"]},
    {"tags": ["ramp"]},
])
@pytest.mark.django_db
def test_only_the_vocabulary_is_stored_and_each_key_once(overrides, catalogue):
    from cards.models import OracleCard

    row = CardAnnotation(oracle_card=OracleCard.objects.first(), overrides=overrides)
    with pytest.raises(ValidationError):
        row.full_clean()


# --- an old list, converted ------------------------------------------------------------


def test_an_old_list_is_said_relative_to_the_card_data():
    derived = frozenset({"draw", "lifegain", "tokens"})
    said = annotations.tags_as_categories(
        {"tags": ["removal", "draw_engine", "creature"], "priority": 3}, derived,
        ["creature", "draw", "draw_engine"])
    # The engine's four did not change; the list's roles and the mechanics
    # no list could name are what it counts as.
    assert said == {"priority": 3, "category_add": ["removal"], "category_remove": ["draw"]}
    assert annotations.tags_as_categories({"priority": 3}, derived, []) is None
    engine = annotations.tags_as_categories({"tags": []}, frozenset(), ["sac_outlet"])
    assert engine == {"engine_tags": []}


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="corrects@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    ).deck


def _reading(deck, card):
    return next(reading for reading in adapter.readings(deck)
                if reading.oracle_card.pk == card.pk)


def _derived_card(deck):
    """A nonland card of the deck whose profile is derived into categories."""
    return next(reading.oracle_card for reading in adapter.readings(deck)
                if reading.oracle_card.profile.categories_version
                and not reading.card.is_land)


# --- the card page ---------------------------------------------------------------------


def _url(deck, card):
    return reverse("simulations:category", args=[deck.pk, card.pk])


@pytest.mark.django_db
def test_the_card_page_shows_the_categories_and_who_decided_them(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    page = client.get(reverse("simulations:annotate", args=[deck.pk, card.pk]))
    assert page.status_code == 200
    block = page.context["categories"]
    assert {line.key for line in block.lines if not line.removed} \
        == _reading(deck, card).card.categories
    assert all(line.status == "card" for line in block.lines)
    assert 'id="categories"' in page.content.decode()


@pytest.mark.django_db
def test_a_change_is_stored_for_this_deck_and_taken_back(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    key = next(key for key in categories.ROLES
               if key not in _reading(deck, card).card.categories)

    response = client.post(_url(deck, card), {"action": "primary", "key": key,
                                              "scope": DECK_SCOPE})
    assert response.status_code == 302
    assert response.url.endswith("#categories")
    reading = _reading(deck, card)
    assert key in reading.card.categories and reading.primary == key
    line = next(line for line in client.get(
        reverse("simulations:annotate", args=[deck.pk, card.pk])).context["categories"].lines
        if line.key == key)
    assert line.main and line.status == DECK_SCOPE

    client.post(_url(deck, card), {"action": "reset", "scope": DECK_SCOPE})
    assert key not in _reading(deck, card).card.categories
    assert not CardAnnotation.objects.filter(oracle_card=card, deck=deck).exists()


@pytest.mark.django_db
def test_a_deck_can_take_away_what_all_decks_add(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    key = next(key for key in categories.MECHANICS
               if key not in _reading(deck, card).card.categories)
    client.post(_url(deck, card), {"action": "add", "key": key, "scope": USER_SCOPE})
    assert key in _reading(deck, card).card.categories

    client.post(_url(deck, card), {"action": "remove", "key": key, "scope": DECK_SCOPE})

    assert key not in _reading(deck, card).card.categories
    assert CardAnnotation.objects.get(oracle_card=card, deck=None, owner=owner).overrides \
        == {"category_add": [key]}
    # Not card data, so it is simply offered again.
    block = client.get(reverse("simulations:annotate", args=[deck.pk, card.pk])).context[
        "categories"]
    assert key in dict(block.addable) and key not in {line.key for line in block.lines}


@pytest.mark.django_db
def test_a_deck_can_take_away_the_main_role_all_decks_gave(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    key = next(key for key in categories.ROLES
               if key not in _reading(deck, card).card.categories)
    client.post(_url(deck, card), {"action": "primary", "key": key, "scope": USER_SCOPE})
    assert _reading(deck, card).primary == key

    client.post(_url(deck, card), {"action": "remove", "key": key, "scope": DECK_SCOPE})

    reading = _reading(deck, card)
    assert key not in reading.card.categories and reading.primary != key
    client.post(_url(deck, card), {"action": "primary", "key": key, "scope": DECK_SCOPE})
    reading = _reading(deck, card)
    assert key in reading.card.categories and reading.primary == key


@pytest.mark.django_db
def test_an_unknown_change_stores_nothing(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    response = client.post(_url(deck, card), {"action": "add", "key": "gamechanger"},
                           follow=True)
    assert response.status_code == 200
    assert not CardAnnotation.objects.filter(oracle_card=card, owner=owner).exists()


@pytest.mark.django_db
def test_somebody_elses_deck_is_not_found(client, deck):
    stranger = User.objects.create_user(email="stranger@example.com",
                                        password="pw-for-test-only")
    client.force_login(stranger)
    card = _derived_card(deck)
    response = client.post(_url(deck, card), {"action": "add", "key": "ramp"})
    assert response.status_code == 404
    assert not CardAnnotation.objects.filter(oracle_card=card).exists()
