"""Card model phase 2, PR 5: "please check" - a correction whose reading moved.

When the reader learns more about a card, a correction keeps what its owner
said and takes the rest; when the block it corrects is gone, it is not played.
Either way the owner is asked to look again - on the card, in the review
queue and in the count of the red marker - and keeping or discarding it never
changes the engine card. Built-in corrections are the operator's, in the admin.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards import profiles
from cards.models import DerivedProfile, OracleCard
from decks.models import Deck, DeckCard
from simulations import review, services
from simulations.engine import adapter
from simulations.engine.adapter import BUILTIN_SCOPE, DECK_SCOPE, USER_SCOPE
from simulations.models import CardAnnotation


@pytest.fixture
def deck(db):
    owner = get_user_model().objects.create_user(email="checks@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=[], mana_cost=cost,
            keywords=[])
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return OracleCard.objects.select_related("profile").get(pk=found.pk)

    cards = SimpleNamespace(
        cobra=made("Lotus Cobra", "Creature — Snake",
                   "Landfall — Whenever a land you control enters, add one mana of any "
                   "color.", "{1}{G}"),
        growth=made("Rampant Growth", "Sorcery",
                    "Search your library for a basic land card, put that card onto the "
                    "battlefield tapped, then shuffle.", "{1}{G}"),
        elf=made("Wood Elf Captain", "Legendary Creature — Elf",
                 "When this creature enters, search your library for a basic land card, "
                 "put it onto the battlefield tapped, then shuffle.", "{2}{G}"),
    )
    deck = Deck.objects.create(owner=owner, name="Checks")
    for found in (cards.cobra, cards.growth):
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, **vars(cards))


def correct(deck, card, key, value, scope=DECK_SCOPE):
    services.save_annotation(deck=deck.deck, oracle_card=card, scope=scope,
                             primitive_changes={key: value})


def move(card, **fields):
    """The reader reads the card differently now (an ingest, a new Forge pin)."""
    profile = DerivedProfile.objects.get(oracle_card=card)
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save()


def search_of(card):
    return DerivedProfile.objects.get(oracle_card=card).land_search


def reading_of(deck, card):
    return next(reading for reading in adapter.readings(deck.deck)
                if reading.oracle_card.pk == card.pk)


def checks_of(deck, card):
    return [(check.key, check.status, check.scope)
            for check in reading_of(deck, card).checks]


# --- the reading says which corrections to check -------------------------------------


def test_a_moved_reading_is_a_check_on_the_card(deck):
    read = search_of(deck.growth)
    correct(deck, deck.growth, "land_search", read | {"battlefield": 2})
    assert checks_of(deck, deck.growth) == []

    move(deck.growth, land_search=read | {"tapped": False})
    assert checks_of(deck, deck.growth) == [("land_search", "changed", DECK_SCOPE)]
    move(deck.growth, land_search=None)
    assert checks_of(deck, deck.growth) == [("land_search", "gone", DECK_SCOPE)]


def test_a_check_on_all_my_decks_says_so(deck):
    read = search_of(deck.growth)
    correct(deck, deck.growth, "land_search", read | {"battlefield": 2}, scope=USER_SCOPE)
    move(deck.growth, land_search=read | {"tapped": False})
    assert checks_of(deck, deck.growth) == [("land_search", "changed", USER_SCOPE)]


def test_a_built_in_check_is_not_the_owners(deck):
    read = search_of(deck.growth)
    CardAnnotation.objects.create(oracle_card=deck.growth,
                                  overrides={"land_search": read | {"battlefield": 2}})
    move(deck.growth, land_search=read | {"tapped": False})
    assert checks_of(deck, deck.growth) == []
    assert [check.scope for check in
            adapter.annotations_for(deck.deck).notes[deck.growth.pk]] == [BUILTIN_SCOPE]


def test_both_commanders_are_checked(deck):
    deck.deck.commander = deck.cobra
    deck.deck.partner = deck.elf
    deck.deck.save()
    read = search_of(deck.elf)
    assert read, "the partner reads a land search"
    correct(deck, deck.elf, "land_search", read | {"battlefield": 2})
    move(deck.elf, land_search=read | {"tapped": False})
    found = reading_of(deck, deck.elf)
    assert found.is_commander
    assert checks_of(deck, deck.elf) == [("land_search", "changed", DECK_SCOPE)]


# --- the review queue and the red marker -----------------------------------------------


def _moved_growth(deck, scope=DECK_SCOPE):
    read = search_of(deck.growth)
    correct(deck, deck.growth, "land_search", read | {"battlefield": 2}, scope=scope)
    move(deck.growth, land_search=read | {"tapped": False})
    return read


def _fresh(deck):
    Deck.objects.filter(pk=deck.deck.pk).update(open_questions=None)
    return Deck.objects.get(pk=deck.deck.pk)


def test_a_card_to_check_is_an_open_question(deck):
    assert review.open_questions(_fresh(deck)) == 0
    _moved_growth(deck)
    found = review.queue(deck.deck)
    assert [(question.name, question.to_check, question.answered) for question in found] \
        == [("Rampant Growth", True, False)]
    assert review.open_questions(_fresh(deck)) == 1


def test_a_built_in_check_is_no_question(deck):
    read = search_of(deck.growth)
    CardAnnotation.objects.create(oracle_card=deck.growth,
                                  overrides={"land_search": read | {"battlefield": 2}})
    move(deck.growth, land_search=read | {"tapped": False})
    assert review.queue(deck.deck) == []


def test_an_unread_card_to_check_counts_once(deck):
    _moved_growth(deck)
    DerivedProfile.objects.filter(oracle_card=deck.growth).update(
        needs_review=True, review_reasons=["test: the cost could not be read"])
    assert review.open_questions(_fresh(deck)) == 1


def test_the_deck_page_says_which_cards_to_check(deck, client):
    _moved_growth(deck)
    client.force_login(deck.owner)
    body = client.get(deck.deck.get_absolute_url()).content.decode()
    assert "please check" in body
    assert "1 card has a correction of yours to check" in body
    assert "1 card needs you" in body
    # Nothing here the engine could not read: that sentence is not about checks.
    assert "The engine read all" in body


# --- the card page: keep it, take it back, discard it, set it again ------------------


def page(client, deck, card, query=""):
    url = reverse("simulations:annotate", args=[deck.deck.pk, card.pk])
    return client.get(url + query).content.decode()


def act(client, deck, card, key, action, scope=DECK_SCOPE):
    url = reverse("simulations:primitive", args=[deck.deck.pk, card.pk, key])
    return client.post(url, {"scope": scope, "action": action})


def engine_card(deck, card):
    return reading_of(deck, card).card


def test_keep_my_correction_plays_as_before_and_ends_the_check(deck, client):
    _moved_growth(deck)
    client.force_login(deck.owner)
    text = page(client, deck, deck.growth)
    assert 'data-check="changed"' in text and "Keep my correction" in text
    assert "please check" in text  # the provenance table
    before = engine_card(deck, deck.growth)

    act(client, deck, deck.growth, "land_search", "keep")
    assert engine_card(deck, deck.growth) == before
    assert checks_of(deck, deck.growth) == []
    assert review.open_questions(_fresh(deck)) == 0
    assert "Keep my correction" not in page(client, deck, deck.growth)


def test_back_to_the_card_data_ends_the_check(deck, client):
    read = _moved_growth(deck)
    client.force_login(deck.owner)
    act(client, deck, deck.growth, "land_search", "reset")
    assert not CardAnnotation.objects.filter(oracle_card=deck.growth).exists()
    assert engine_card(deck, deck.growth).play.search is not None
    assert search_of(deck.growth) == read | {"tapped": False}


def test_a_gone_correction_has_its_own_line_and_can_be_discarded(deck, client):
    read = search_of(deck.growth)
    correct(deck, deck.growth, "land_search", read | {"battlefield": 2})
    correct(deck, deck.growth, "treasures", 1)
    move(deck.growth, land_search=None)
    client.force_login(deck.owner)
    text = page(client, deck, deck.growth)
    assert "data-gone" in text and "Discard it" in text and "Set it again" in text
    before = engine_card(deck, deck.growth)

    act(client, deck, deck.growth, "land_search", "keep")
    assert engine_card(deck, deck.growth) == before
    row = CardAnnotation.objects.get(oracle_card=deck.growth)
    assert "land_search" not in row.overrides and row.overrides["treasures"] == 1
    assert "data-gone" not in page(client, deck, deck.growth)


def test_set_it_again_starts_from_what_was_said(deck, client):
    read = search_of(deck.growth)
    correct(deck, deck.growth, "land_search", read | {"battlefield": 2})
    move(deck.growth, land_search=None)
    client.force_login(deck.owner)
    text = page(client, deck, deck.growth, "?edit=land_search")
    assert 'id="editing"' in text
    assert 'name="land_search-battlefield" value="2"' in text


def test_a_check_on_all_my_decks_is_kept_on_that_page(deck, client):
    _moved_growth(deck, scope=USER_SCOPE)
    client.force_login(deck.owner)
    here = page(client, deck, deck.growth)
    assert "check it there" in here and "Keep my correction" not in here
    assert "Keep my correction" in page(client, deck, deck.growth, "?scope=user")
    act(client, deck, deck.growth, "land_search", "keep", scope=DECK_SCOPE)
    assert checks_of(deck, deck.growth) != []
    act(client, deck, deck.growth, "land_search", "keep", scope=USER_SCOPE)
    assert checks_of(deck, deck.growth) == []


def test_looks_right_settles_every_check_of_the_card(deck, client):
    _moved_growth(deck)
    client.force_login(deck.owner)
    before = engine_card(deck, deck.growth)
    url = reverse("simulations:annotate", args=[deck.deck.pk, deck.growth.pk])
    client.post(url, {"action": "confirm", "scope": DECK_SCOPE})
    assert checks_of(deck, deck.growth) == []
    assert engine_card(deck, deck.growth) == before
    assert CardAnnotation.objects.get(oracle_card=deck.growth).confirmed


def test_nobody_else_can_settle_a_check(deck, client):
    _moved_growth(deck)
    other = get_user_model().objects.create_user(email="other@example.com",
                                                 password="pw-for-test-only")
    client.force_login(other)
    assert act(client, deck, deck.growth, "land_search", "keep").status_code == 404
    assert checks_of(deck, deck.growth) != []


# --- built-in corrections are the operator's ---------------------------------------------


def test_the_admin_finds_built_in_corrections_to_check(deck, admin_client):
    read = search_of(deck.growth)
    builtin = CardAnnotation.objects.create(
        oracle_card=deck.growth, overrides={"land_search": read | {"battlefield": 2}})
    _moved_growth(deck)  # the owner's own row: not the operator's question
    url = reverse("admin:simulations_cardannotation_changelist")
    assert admin_client.get(url, {"check": "builtin"}).context["cl"].result_count == 1
    assert list(admin_client.get(url, {"check": "builtin"}).context["cl"].result_list) \
        == [builtin]
    change = admin_client.get(reverse("admin:simulations_cardannotation_change",
                                      args=[builtin.pk])).content.decode()
    assert "<code>land_search</code>: changed" in change


def test_the_check_is_asked_in_the_page_language(deck, client, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    _moved_growth(deck)
    deck.owner.language = "de"
    deck.owner.save()
    client.force_login(deck.owner)
    url = reverse("simulations:annotate", args=[deck.deck.pk, deck.growth.pk])
    text = client.get(url, follow=True).content.decode()
    assert "Meine Korrektur behalten" in text and "bitte prüfen" in text
