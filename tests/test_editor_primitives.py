"""The editor rebuild, E1: every engine primitive is an override key.

A primitive's override has the reader's own JSON shape and replaces that
reading; `None` switches it off. "Taps for" replaces only the card's own tap
ability now, and an annotation written before keeps its meaning
(`keep_taps_for_scope`, migration 0010). A value the engine cannot play is
refused when it is saved.
"""

import importlib
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from cards import profiles
from cards.models import OracleCard
from decks.models import Deck, DeckCard
from simulations import annotations, primitives, provenance, services
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE
from simulations.models import CardAnnotation

RAMPANT = {"battlefield": 1, "hand": 0, "tapped": True, "basic": True, "types": [],
           "life": 0, "when": "cast", "sacrifice": False, "untap_at": 0, "cost": "",
           "taps": False, "share_type": False, "each": False, "condition": "",
           "sacrifices_land": False, "land_cost_types": [], "sacrifice_other": [],
           "discard": 0}


# --- structure -------------------------------------------------------------------


def test_every_primitive_is_an_allowed_annotation_key():
    from simulations.models import ALLOWED_KEYS

    assert primitives.KEYS <= set(ALLOWED_KEYS)
    assert not primitives.KEYS & annotations.EDITABLE_KEYS


def test_a_reader_shape_passes_and_junk_does_not():
    assert primitives.validate("land_search", RAMPANT) == RAMPANT
    assert primitives.validate("land_search", None) is None
    assert primitives.validate("triggers", [{"event": "landfall", "mana": 1, "color": "G"}])
    assert primitives.validate("tutor", {"to": "hand", "count": 1, "types": ["aura"]})
    for key, value in (
        ("land_search", {"battlefield": 1, "teleport": True}),
        ("land_search", {"battlefield": 10_000}),
        ("land_search", {"when": "<script>alert(1)</script>"}),
        ("land_search", ["not", "an", "object"]),
        ("triggers", [{"event": "landfall"}] * 11),
        ("tutor", {"to": "exile", "count": 1}),
        ("mox", "something"),
        ("treasures", True),
    ):
        with pytest.raises(primitives.PrimitiveError):
            primitives.validate(key, value)


def test_keep_taps_for_scope_switches_off_only_what_taps_for_switched_off():
    profile = SimpleNamespace(
        sacrifice_mana={"sacrifice": ["creature"], "amount": 2}, tap_mana=None,
        mana_store=None, mox="",
        triggers=[{"event": "landfall", "mana": 1, "color": "G", "treasures": 0},
                  {"event": "cast", "draw": 1}])
    kept = primitives.keep_taps_for_scope({"mana_produces": {}}, profile)
    assert kept["sacrifice_mana"] is None and "tap_mana" not in kept
    assert kept["triggers"] == [{"event": "landfall", "treasures": 0},
                                {"event": "cast", "draw": 1}]
    assert primitives.keep_taps_for_scope({"priority": 50}, profile) == {"priority": 50}
    # The migration keeps its own copy of the rule; both say the same.
    migration = importlib.import_module("simulations.migrations.0010_taps_for_keeps_its_scope")
    assert migration._kept({"mana_produces": {}}, profile) == kept


def test_apply_primitives_edits_one_block_and_keeps_the_rest():
    stored = {"priority": 60, "triggers": []}
    changed = annotations.apply_primitives(stored, {"land_search": RAMPANT})
    assert changed == {"priority": 60, "triggers": [], "land_search": RAMPANT}
    assert annotations.apply_primitives(changed, {"triggers": annotations.RESET}) == {
        "priority": 60, "land_search": RAMPANT}
    assert annotations.apply_primitives(changed, {"land_search": None})["land_search"] is None
    with pytest.raises(ValueError):
        annotations.apply_primitives(stored, {"priority": 1})


# --- the engine plays it -----------------------------------------------------------


@pytest.fixture
def deck(db):
    owner = get_user_model().objects.create_user(email="editor@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost="", produced=()):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(produced),
            mana_cost=cost, keywords=[])
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return found

    cards = SimpleNamespace(
        growth=made("Odd Growth", "Sorcery", "Draw a card.", "{1}{G}"),
        altar=made("Ashnod's Altar", "Artifact",
                   "Sacrifice a creature: Add {C}{C}.", "{3}", ("C",)),
        cobra=made("Lotus Cobra", "Creature — Snake",
                   "Landfall — Whenever a land you control enters, add one mana of any color.",
                   "{1}{G}"),
    )
    deck = Deck.objects.create(owner=owner, name="Editor")
    for found in vars(cards).values():
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, **vars(cards))


def card_of(deck, oracle_card):
    return next(reading.card for reading in adapter.readings(deck)
                if reading.oracle_card.pk == oracle_card.pk)


def test_a_land_search_added_by_hand_is_played(deck):
    assert card_of(deck.deck, deck.growth).land_search is None
    services.save_annotation(deck=deck.deck, oracle_card=deck.growth, scope=DECK_SCOPE,
                             primitive_changes={"land_search": RAMPANT})
    search = card_of(deck.deck, deck.growth).land_search
    assert search is not None and search.battlefield == 1 and search.basic
    reading = next(each for each in adapter.readings(deck.deck)
                   if each.oracle_card.pk == deck.growth.pk)
    assert "basic land" in reading.searches


def test_a_primitive_switched_off_and_back(deck):
    assert card_of(deck.deck, deck.cobra).triggers
    services.save_annotation(deck=deck.deck, oracle_card=deck.cobra, scope=DECK_SCOPE,
                             primitive_changes={"triggers": None})
    assert card_of(deck.deck, deck.cobra).triggers == ()
    services.save_annotation(deck=deck.deck, oracle_card=deck.cobra, scope=DECK_SCOPE,
                             primitive_changes={"triggers": annotations.RESET})
    assert card_of(deck.deck, deck.cobra).triggers
    assert not CardAnnotation.objects.filter(oracle_card=deck.cobra).exists()


def test_a_search_to_hand_by_hand(deck):
    services.save_annotation(deck=deck.deck, oracle_card=deck.growth, scope=DECK_SCOPE,
                             primitive_changes={"tutor": {"to": "hand", "count": 1,
                                                          "types": ["creature"],
                                                          "max_mv": 3}})
    tutor = card_of(deck.deck, deck.growth).tutor
    assert tutor.to_hand and tutor.types == frozenset({"creature"}) and tutor.max_mv == 3


def test_taps_for_no_longer_switches_the_altar_off(deck):
    assert card_of(deck.deck, deck.altar).sacrifice_mana is not None
    services.save_annotation(
        deck=deck.deck, oracle_card=deck.altar, scope=DECK_SCOPE,
        judgements={key: None for key in annotations.EDITABLE_KEYS} | {"mana_produces": {}})
    card = card_of(deck.deck, deck.altar)
    assert card.mana_abilities == () and card.sacrifice_mana is not None
    services.save_annotation(deck=deck.deck, oracle_card=deck.altar, scope=DECK_SCOPE,
                             primitive_changes={"sacrifice_mana": None})
    assert card_of(deck.deck, deck.altar).sacrifice_mana is None


def test_a_primitive_the_engine_cannot_play_is_refused(deck):
    with pytest.raises(ValidationError):
        services.save_annotation(deck=deck.deck, oracle_card=deck.growth, scope=DECK_SCOPE,
                                 primitive_changes={"land_search": {"when": "cast"}})
    with pytest.raises(ValidationError):
        services.save_annotation(deck=deck.deck, oracle_card=deck.growth, scope=DECK_SCOPE,
                                 primitive_changes={"land_search": {"teleport": 1}})
    assert not CardAnnotation.objects.filter(oracle_card=deck.growth).exists()


def test_the_model_refuses_a_bad_primitive_on_any_save(deck):
    with pytest.raises(ValidationError):
        CardAnnotation.objects.create(owner=deck.owner, deck=deck.deck,
                                      oracle_card=deck.growth,
                                      overrides={"tutor": {"to": "exile"}})


def test_the_panel_credits_you_for_a_primitive(deck):
    services.save_annotation(deck=deck.deck, oracle_card=deck.growth, scope=DECK_SCOPE,
                             primitive_changes={"land_search": RAMPANT})
    entry = provenance.for_card(deck.deck, deck.growth)
    row = next(row for row in entry.rows if row.key == "searches")
    assert row.source == "deck" and row.is_yours


def test_the_reading_is_where_the_editor_starts(deck):
    cobra = deck.cobra.profile
    assert primitives.reading_of("triggers", cobra) == cobra.triggers
    assert primitives.reading_of("land_search", deck.growth.profile) is None


# --- E2: the card page ------------------------------------------------------------


def page(client, deck, oracle_card, query=""):
    url = reverse("simulations:annotate", args=[deck.deck.pk, oracle_card.pk])
    return client.get(url + query)


def post(client, deck, oracle_card, key, data):
    url = reverse("simulations:primitive", args=[deck.deck.pk, oracle_card.pk, key])
    return client.post(url, {"scope": DECK_SCOPE, **data})


def test_the_page_shows_what_the_engine_plays(deck, client):
    client.force_login(deck.owner)
    text = page(client, deck, deck.cobra).content.decode()
    assert "What the engine plays" in text and "From the card" in text
    assert "whenever a land of yours enters" in text
    assert "Add a building block" in text


def test_a_land_search_added_through_the_form(deck, client):
    client.force_login(deck.owner)
    text = page(client, deck, deck.growth, "?edit=land_search").content.decode()
    assert 'id="editing"' in text and "Lands onto the battlefield" in text
    response = post(client, deck, deck.growth, "land_search", {
        "action": "save", "land_search-when": "cast", "land_search-battlefield": "2",
        "land_search-hand": "0", "land_search-tapped": "on", "land_search-basic": "on",
        "land_search-condition": "", "land_search-repeat": "0", "land_search-tempting": "0",
        "land_search-life": "0"})
    assert response.status_code == 302
    search = card_of(deck.deck, deck.growth).land_search
    assert search.battlefield == 2 and search.tapped and search.basic
    text = page(client, deck, deck.growth).content.decode()
    assert "Yours, this deck" in text and "Back to the card data" in text


def test_off_and_back_through_the_page(deck, client):
    client.force_login(deck.owner)
    assert post(client, deck, deck.cobra, "triggers", {"action": "off"}).status_code == 302
    assert card_of(deck.deck, deck.cobra).triggers == ()
    assert "switched off" in page(client, deck, deck.cobra).content.decode()
    post(client, deck, deck.cobra, "triggers", {"action": "reset"})
    assert card_of(deck.deck, deck.cobra).triggers


def test_a_bad_box_is_refused_with_its_reason(deck, client):
    client.force_login(deck.owner)
    response = post(client, deck, deck.growth, "land_search", {
        "action": "save", "land_search-when": "activate", "land_search-battlefield": "1",
        "land_search-cost": "two mana", "land_search-condition": ""})
    assert response.status_code == 400
    assert "is not a mana cost" in response.content.decode()
    response = post(client, deck, deck.growth, "tutor", {
        "action": "save", "tutor-to": "hand", "tutor-count": "1", "tutor-kind": "",
        "tutor-types": "<script>", "tutor-color": ""})
    assert response.status_code == 400
    assert not CardAnnotation.objects.filter(oracle_card=deck.growth).exists()


def test_a_search_with_types_through_the_form(deck, client):
    client.force_login(deck.owner)
    post(client, deck, deck.growth, "tutor", {
        "action": "save", "tutor-to": "hand", "tutor-count": "1", "tutor-kind": "",
        "tutor-types": "Aura, equipment", "tutor-max_mv": "", "tutor-color": ""})
    tutor = card_of(deck.deck, deck.growth).tutor
    assert tutor.types == frozenset({"aura", "equipment"}) and tutor.max_mv is None


def test_only_the_owner_and_only_known_keys(deck, client):
    other = get_user_model().objects.create_user(email="other@example.com",
                                                 password="pw-for-test-only")
    client.force_login(other)
    assert post(client, deck, deck.cobra, "triggers", {"action": "off"}).status_code == 404
    client.force_login(deck.owner)
    assert post(client, deck, deck.cobra, "teleport", {"action": "off"}).status_code == 404
    assert post(client, deck, deck.cobra, "mana_rule", {"action": "save"}).status_code == 404


# --- E3: lists, one entry at a time --------------------------------------------------


TRIGGER_BOXES = {"triggers-event": "landfall", "triggers-filter": "", "triggers-treasures": "0",
                 "triggers-mana": "2", "triggers-color": "G", "triggers-draw": "0",
                 "triggers-life": "0", "triggers-counter": "0"}


def test_a_trigger_entry_changed_added_and_removed(deck, client):
    client.force_login(deck.owner)
    text = page(client, deck, deck.cobra, "?edit=triggers&item=0").content.decode()
    assert 'name="item" value="0"' in text and "Narrowed to" in text
    post(client, deck, deck.cobra, "triggers", {"action": "save", "item": "0", **TRIGGER_BOXES})
    (trigger,) = card_of(deck.deck, deck.cobra).triggers
    assert (trigger.event, trigger.mana, trigger.mana_color) == ("landfall", 2, "G")
    post(client, deck, deck.cobra, "triggers", {
        "action": "save", "item": "new", **TRIGGER_BOXES, "triggers-event": "cast",
        "triggers-mana": "0", "triggers-color": "", "triggers-treasures": "1"})
    assert [each.event for each in card_of(deck.deck, deck.cobra).triggers] == [
        "landfall", "cast"]
    post(client, deck, deck.cobra, "triggers", {"action": "remove", "item": "0"})
    assert [each.event for each in card_of(deck.deck, deck.cobra).triggers] == ["cast"]


def test_a_combat_trigger_added_with_its_equip_cost(deck, client):
    client.force_login(deck.owner)
    post(client, deck, deck.growth, "combat", {
        "action": "save", "item": "new", "combat-event": "attacks", "combat-who": "equipped",
        "combat-types": "", "combat-treasures": "0", "combat-mana": "", "combat-search": "on",
        "combat-equip": "{2}", "combat-treasure_bonus": "0"})
    card = card_of(deck.deck, deck.growth)
    assert card.combat[0].who == "equipped" and card.combat[0].search
    assert str(card.equip) == "{2}"


def test_an_additional_cost_added_by_hand(deck, client):
    client.force_login(deck.owner)
    post(client, deck, deck.growth, "additional_cost", {
        "action": "save", "item": "new", "additional_cost-sacrifice": ["creature"],
        "additional_cost-life": "0", "additional_cost-discard": "0",
        "additional_cost-mana": "", "additional_cost-exile_from_graveyard": "",
        "additional_cost-filter": ""})
    (way,) = card_of(deck.deck, deck.growth).additional_costs
    assert way.sacrifice == frozenset({"creature"})


def test_a_bad_entry_is_refused(deck, client):
    client.force_login(deck.owner)
    response = post(client, deck, deck.cobra, "triggers", {
        "action": "save", "item": "0", **TRIGGER_BOXES, "triggers-filter": "drop table"})
    assert response.status_code == 400 and "not one word" in response.content.decode()
    response = post(client, deck, deck.growth, "combat", {
        "action": "save", "item": "new", "combat-event": "attacks", "combat-who": "self",
        "combat-mana": "XYZ", "combat-treasures": "0"})
    assert response.status_code == 400
    assert not CardAnnotation.objects.exists()
