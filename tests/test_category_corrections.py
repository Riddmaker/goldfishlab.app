"""Card model phase 3, PR 10: an owner corrects what a card counts as.

What these tests hold:

1. **A correction is relative to the card data.** ``category_add`` and
   ``category_remove`` change the derived categories, so a later reading
   still reaches the card; ``category_primary`` names the main role; "Back
   to the card data" takes all three back.
2. **The engine's roles are apart.** The judgement form offers only the four
   roles the engine reads (``engine_tags``), and replaces only those.
3. **Only the vocabulary is stored**, each category once, never both added
   and removed.
4. **A whole list from before is converted** (migration 0017) only where the
   engine plays the card and the statistics count it as before; otherwise
   it stays, and the card page can retire it.
5. **The card page** shows the categories with who decided each, and every
   change is a plain form post on the owner's own deck.
"""

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from cards import categories
from decks import services as deck_services
from simulations import annotations, provenance
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.forms import AnnotationForm
from simulations.models import CardAnnotation

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


def _profile(**fields):
    base = {"categories_version": categories.VERSION, "role_tags": ["creature", "draw"],
            "primary_role": "draw", "secondary_role": "", "mechanics": ["lifegain"]}
    return SimpleNamespace(**(base | fields))


# --- the correction itself ----------------------------------------------------------


def test_add_and_remove_are_relative_to_the_card_data():
    current = frozenset({"draw", "lifegain"})
    added = annotations.apply_category({}, "add", "ramp", current)
    assert added == {"category_add": ["ramp"]}
    removed = annotations.apply_category(added, "remove", "lifegain", current | {"ramp"})
    assert removed == {"category_add": ["ramp"], "category_remove": ["lifegain"]}
    # Each takes the other back rather than stacking up.
    assert annotations.apply_category(removed, "add", "lifegain", current) == {
        "category_add": ["ramp"]}
    assert annotations.apply_category(added, "remove", "ramp", current) == {}


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


def test_back_to_the_card_data_keeps_the_rest_and_retires_an_old_list():
    stored = {"priority": 5, "category_add": ["ramp"], "category_primary": "ramp"}
    assert annotations.apply_category(stored, "reset") == {"priority": 5}
    old = {"tags": ["removal", "sac_outlet", "creature"]}
    # The games stay as they were; only the statistics go back to the card.
    assert annotations.apply_category(old, "reset") == {"engine_tags": ["sac_outlet"]}


def test_a_deck_starts_from_what_it_inherits_and_can_empty_it():
    inherited = {"category_add": ["ramp", "tokens"]}
    said = annotations.apply_category({}, "remove", "tokens", frozenset({"ramp", "tokens"}),
                                      inherited)
    assert said == {"category_add": ["ramp"]}
    # Emptied, it is stored empty: an absent key would let "all decks" through.
    emptied = annotations.apply_category(said, "remove", "ramp", frozenset({"ramp"}), inherited)
    assert emptied == {"category_add": []}
    # A list the change does not touch stays with "all decks".
    assert annotations.apply_category({}, "remove", "draw", frozenset({"draw", "ramp"}),
                                      inherited) == {"category_remove": ["draw"]}


# --- what the engine and the statistics read ----------------------------------------


def test_the_corrections_change_the_derived_categories():
    profile = _profile()
    found = adapter._categories(profile, {"category_add": ["ramp"],
                                          "category_remove": ["lifegain"]}, DECK_SCOPE)
    assert found == {"draw", "ramp"}
    # A main role named is counted, an old list's or not.
    old = adapter._categories(profile, {"tags": ["removal"], "category_primary": "ramp"},
                              USER_SCOPE)
    assert old == {"removal", "lifegain", "ramp"}


def test_the_owners_main_role_wins_while_the_card_counts_in_it():
    card = SimpleNamespace(profile=_profile(secondary_role="ramp"))
    both = frozenset({"draw", "ramp"})
    assert adapter.primary_role(card, both, {"category_primary": "ramp"}) == "ramp"
    assert adapter.primary_role(card, frozenset({"draw"}), {"category_primary": "ramp"}) \
        == "draw"


def test_engine_tags_replace_only_the_four_the_engine_reads():
    profile = _profile(role_tags=["creature", "draw", "draw_engine"])
    assert adapter._engine_tags(profile, {"engine_tags": ["sac_outlet"]}) == {
        "creature", "draw", "sac_outlet"}
    # An old list is still the whole answer.
    assert adapter._engine_tags(profile, {"tags": ["ramp"]}) == {"ramp"}


def test_the_form_offers_the_four_engine_roles_and_says_none():
    field = AnnotationForm().fields["engine_tags"]
    assert {key for key, _ in field.choices} == set(adapter.runner.ENGINE_ROLE_TAGS)
    form = AnnotationForm({"scope": DECK_SCOPE, "replace_engine_tags": "on"})
    assert form.is_valid(), form.errors
    assert form.judgements()["engine_tags"] == []
    assert AnnotationForm.initial_for(SimpleNamespace(
        overrides={"engine_tags": ["recursive"]}, note="")) == {
        "note": "", "replace_engine_tags": True, "engine_tags": ["recursive"]}


def test_the_provenance_row_is_the_engine_s_roles():
    assert provenance.ANNOTATION_KEY["roles"] == "engine_tags"
    assert "tags" in provenance.PRIMITIVE_KEYS["roles"]


# --- what is stored -------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    {"category_primary": "lifegain"},
    {"category_add": ["gamechanger"]},
    {"category_add": ["ramp", "ramp"]},
    {"category_add": "ramp"},
    {"category_add": ["ramp"], "category_remove": ["ramp"]},
    {"engine_tags": ["ramp"]},
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


def _migrate():
    importlib.import_module("simulations.migrations.0017_tags_as_categories").forwards(
        apps, None)


@pytest.mark.django_db
def test_the_migration_converts_a_list_that_plays_and_counts_the_same(deck):
    card = _derived_card(deck)
    before = _reading(deck, card).card
    old = sorted({"removal", "lifegain"} | (before.tags & {"sac_outlet"}))
    row = CardAnnotation.objects.create(owner=deck.owner, deck=deck, oracle_card=card,
                                        overrides={"tags": old})
    counted = _reading(deck, card).card.categories

    _migrate()

    row.refresh_from_db()
    assert "tags" not in row.overrides
    after = _reading(deck, card).card
    assert after.categories == counted
    assert after.tags & set(adapter.runner.ENGINE_ROLE_TAGS) \
        == set(old) & set(adapter.runner.ENGINE_ROLE_TAGS)


@pytest.mark.django_db
def test_the_migration_converts_an_owners_rows_together_or_not_at_all(deck):
    card = _derived_card(deck)
    mine = CardAnnotation.objects.create(owner=deck.owner, deck=None, oracle_card=card,
                                         overrides={"tags": ["removal"]})
    # The deck's own list of additions would hide the converted one for all
    # decks, key by key: "removal" would stop counting in this deck.
    here = CardAnnotation.objects.create(owner=deck.owner, deck=deck, oracle_card=card,
                                         overrides={"category_add": ["tokens"]})
    builtin = CardAnnotation.objects.create(oracle_card=card, overrides={"tags": ["ramp"]})

    _migrate()

    for row in (mine, here, builtin):
        row.refresh_from_db()
    assert mine.overrides == {"tags": ["removal"]}
    assert here.overrides == {"category_add": ["tokens"]}
    assert builtin.overrides == {"tags": ["ramp"]}


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
def test_an_unknown_change_stores_nothing(client, owner, deck):
    card = _derived_card(deck)
    client.force_login(owner)
    response = client.post(_url(deck, card), {"action": "add", "key": "gamechanger"},
                           follow=True)
    assert response.status_code == 200
    assert not CardAnnotation.objects.filter(oracle_card=card, owner=owner).exists()


@pytest.mark.django_db
def test_an_old_list_is_named_and_can_be_retired(client, owner, deck):
    card = _derived_card(deck)
    CardAnnotation.objects.create(owner=owner, deck=deck, oracle_card=card,
                                  overrides={"tags": ["removal", "recursive"]})
    client.force_login(owner)
    page = client.get(reverse("simulations:annotate", args=[deck.pk, card.pk]))
    assert page.context["categories"].legacy == DECK_SCOPE
    assert "data-legacy" in page.content.decode()

    client.post(_url(deck, card), {"action": "reset", "scope": DECK_SCOPE})

    assert CardAnnotation.objects.get(oracle_card=card, deck=deck).overrides == {
        "engine_tags": ["recursive"]}
    assert "recursive" in _reading(deck, card).card.tags


@pytest.mark.django_db
def test_somebody_elses_deck_is_not_found(client, deck):
    stranger = User.objects.create_user(email="stranger@example.com",
                                        password="pw-for-test-only")
    client.force_login(stranger)
    card = _derived_card(deck)
    response = client.post(_url(deck, card), {"action": "add", "key": "ramp"})
    assert response.status_code == 404
    assert not CardAnnotation.objects.filter(oracle_card=card).exists()
