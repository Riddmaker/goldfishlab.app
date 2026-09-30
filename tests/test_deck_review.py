"""Phase 9 C2: the red marker on a deck, and the card-by-card review behind it.

What these tests are for, in order of how much they matter:

1. **The count is honest.** Only cards the engine could not read count, and a
   card stops counting once its owner has said anything about it - a value,
   or only "Looks right". A built-in annotation is nobody's answer.
2. **The count is never stale.** Every one of the four things that can change
   it - import, commander change, a saved or forgotten answer, a profile
   rebuild - forgets it, and counting again never bumps `updated_at`.
3. **"Looks right" loses nothing.** It keeps every value already stored, and a
   row carrying only the flag is kept rather than cleaned away as empty.
4. **The queue is stable** and the review walks it: Save and next goes to the
   next open card, the last one lands on the deck, "Ready".
5. **Ownership.** Somebody else's deck is a 404 here too.
"""

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from cards import ingest, profiles
from cards.models import DerivedProfile
from decks import services as deck_services
from decks.models import Deck
from simulations import review, services
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.models import CardAnnotation

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)


@pytest.fixture
def catalogue():
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz", updated_at=VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz", updated_at=VERSION)
    profiles.rebuild()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password="pw-for-test-only")


def _import(owner, name="Chainer"):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name=name, filename="sample.csv"
    ).deck


@pytest.fixture
def deck(owner):
    """The sample deck, with three more cards the engine cannot read.

    The sample catalogue reads nearly everything, which makes a queue of one;
    marking three profiles for review is how the deriver itself says it could
    not read a card, so the gaps are the real kind.
    """
    made = _import(owner)
    unread = made.entries.exclude(oracle_card__type_line__contains="Land").order_by(
        "oracle_card__name").values_list("oracle_card_id", flat=True)[:3]
    DerivedProfile.objects.filter(oracle_card_id__in=list(unread)).update(
        needs_review=True, review_reasons=["test: the cost could not be read"])
    return made


@pytest.fixture
def questions(deck):
    found = review.queue(deck)
    assert len(found) >= 3
    return found


def _stored(deck):
    return Deck.objects.values_list("open_questions", flat=True).get(pk=deck.pk)


# --- the count ---------------------------------------------------------------

def test_only_the_cards_the_engine_could_not_read_are_in_the_queue(deck, questions):
    unreadable = {card.oracle_card.pk for card in review.adapter.readings(deck)
                  if card.unreadable}

    assert {question.oracle_card.pk for question in questions} == unreadable


def test_the_queue_is_in_name_order(questions):
    names = [question.name.lower() for question in questions]

    assert names == sorted(names)


def test_any_answer_of_the_owner_takes_a_card_off_the_count(deck, questions):
    first, second = questions[0].oracle_card, questions[1].oracle_card
    before = review.open_questions(deck)

    services.confirm_annotation(deck=deck, oracle_card=first, scope=DECK_SCOPE)
    services.save_annotation(deck=deck, oracle_card=second, scope=USER_SCOPE,
                             judgements={}, note="checked it")

    assert review.open_questions(Deck.objects.get(pk=deck.pk)) == before - 2


def test_a_built_in_annotation_is_nobodys_answer(deck, questions):
    before = review.open_questions(deck)
    CardAnnotation.objects.update_or_create(
        owner=None, deck=None, oracle_card=questions[0].oracle_card,
        defaults={"confirmed": True},
    )
    deck_services.recount_later(Deck.objects.filter(pk=deck.pk))

    assert review.open_questions(Deck.objects.get(pk=deck.pk)) == before


def test_another_decks_answer_does_not_count_here(owner, deck, questions):
    other = _import(owner, name="Other")
    services.confirm_annotation(deck=other, oracle_card=questions[0].oracle_card,
                                scope=DECK_SCOPE)

    assert not review.queue(Deck.objects.get(pk=deck.pk))[0].answered


def test_counting_is_stored_and_is_not_an_edit(deck):
    Deck.objects.filter(pk=deck.pk).update(open_questions=None)
    fresh = Deck.objects.get(pk=deck.pk)
    edited = fresh.updated_at

    count = review.open_questions(fresh)

    assert _stored(deck) == count
    assert Deck.objects.get(pk=deck.pk).updated_at == edited


# --- never stale -------------------------------------------------------------

def _counted(deck):
    review.open_questions(Deck.objects.get(pk=deck.pk))
    assert _stored(deck) is not None


def test_an_import_forgets_the_count(owner, deck):
    _counted(deck)
    deck_services.import_deck(owner=owner, raw=ARCHIDEKT_CSV.read_bytes(),
                              filename="sample.csv", deck=deck)

    assert _stored(deck) is None


def test_a_new_commander_forgets_the_count(deck):
    _counted(deck)
    card = next(entry.oracle_card for entry in deck.entries.all()
                if entry.oracle_card_id != deck.commander_id)

    deck_services.set_commander(Deck.objects.get(pk=deck.pk), card)

    assert _stored(deck) is None


def test_an_answer_for_all_decks_forgets_every_deck_holding_the_card(owner, deck, questions):
    other = _import(owner, name="Other")
    _counted(deck)
    _counted(other)

    services.confirm_annotation(deck=deck, oracle_card=questions[0].oracle_card,
                                scope=USER_SCOPE)

    assert _stored(deck) is None
    assert _stored(other) is None


def test_an_answer_for_one_deck_forgets_only_that_deck(owner, deck, questions):
    other = _import(owner, name="Other")
    _counted(deck)
    _counted(other)

    services.confirm_annotation(deck=deck, oracle_card=questions[0].oracle_card,
                                scope=DECK_SCOPE)

    assert _stored(deck) is None
    assert _stored(other) is not None


def test_forgetting_an_answer_forgets_the_count(deck, questions):
    card = questions[0].oracle_card
    services.confirm_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE)
    _counted(deck)

    assert services.delete_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE)
    assert _stored(deck) is None


def test_a_profile_rebuild_forgets_every_count(deck):
    _counted(deck)

    call_command("ingest_scryfall", "--profiles", stdout=StringIO())

    assert _stored(deck) is None


# --- "Looks right" -------------------------------------------------------------

def test_looks_right_keeps_every_stored_value(deck, questions):
    card = questions[0].oracle_card
    services.save_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE,
                             judgements={"accelerant": True})

    services.confirm_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE)

    row = CardAnnotation.objects.get(oracle_card=card, deck=deck)
    assert row.confirmed
    assert row.overrides == {"accelerant": True}


def test_a_row_with_only_looks_right_survives_an_empty_save(deck, questions):
    card = questions[0].oracle_card
    services.confirm_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE)

    kept = services.save_annotation(deck=deck, oracle_card=card, scope=DECK_SCOPE,
                                    judgements={})

    assert kept is not None and kept.confirmed


# --- the pages -----------------------------------------------------------------

def test_the_deck_list_and_page_carry_the_marker(client, owner, deck):
    client.force_login(owner)
    count = review.open_questions(deck)

    for url in (reverse("decks:list"), deck.get_absolute_url()):
        body = client.get(url).content.decode()
        assert f"{count} cards need you" in body
        assert reverse("simulations:review", args=[deck.pk]) in body


def test_the_review_starts_at_the_first_open_card(client, owner, deck, questions):
    client.force_login(owner)

    response = client.get(reverse("simulations:review", args=[deck.pk]))

    assert response.url == reverse("simulations:annotate",
                                   args=[deck.pk, questions[0].oracle_card.pk])


def test_a_step_says_where_it_is(client, owner, deck, questions):
    client.force_login(owner)

    body = client.get(reverse("simulations:annotate",
                              args=[deck.pk, questions[1].oracle_card.pk])).content.decode()

    assert f"2 of {len(questions)}" in body
    assert "Looks right" in body
    assert "Save and next" in body


def test_looks_right_goes_on_to_the_next_open_card(client, owner, deck, questions):
    client.force_login(owner)

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, questions[0].oracle_card.pk]),
        {"action": "confirm", "scope": DECK_SCOPE},
    )

    assert response.url == reverse("simulations:annotate",
                                   args=[deck.pk, questions[1].oracle_card.pk])


def test_the_last_answer_lands_on_the_deck_ready(client, owner, deck, questions):
    client.force_login(owner)
    for question in questions[:-1]:
        services.confirm_annotation(deck=deck, oracle_card=question.oracle_card,
                                    scope=DECK_SCOPE)

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, questions[-1].oracle_card.pk]),
        {"action": "confirm", "scope": DECK_SCOPE}, follow=True,
    )
    body = response.content.decode()

    assert response.redirect_chain[-1][0] == deck.get_absolute_url()
    assert "Ready" in body
    assert review.open_questions(Deck.objects.get(pk=deck.pk)) == 0


def test_a_ready_deck_has_nothing_to_review(client, owner, deck, questions):
    client.force_login(owner)
    for question in questions:
        services.confirm_annotation(deck=deck, oracle_card=question.oracle_card,
                                    scope=DECK_SCOPE)

    response = client.get(reverse("simulations:review", args=[deck.pk]))

    assert response.url == deck.get_absolute_url()


def test_the_card_grid_puts_the_open_cards_first(client, owner, deck, questions):
    client.force_login(owner)

    body = client.get(deck.get_absolute_url()).content.decode()

    assert body.count("! needs you") == len(questions)
    # The first tile of the grid is the first open card.
    first_tile = body.index('class="card-grid-item"')
    assert body.index("! needs you") < body.index('class="card-grid-item"', first_tile + 1)


def test_somebody_elses_deck_has_no_review(client, deck):
    stranger = User.objects.create_user(email="stranger@example.com", password="pw-x")
    client.force_login(stranger)

    assert client.get(reverse("simulations:review", args=[deck.pk])).status_code == 404
    assert client.post(
        reverse("simulations:annotate", args=[deck.pk, deck.entries.first().oracle_card.pk]),
        {"action": "confirm", "scope": DECK_SCOPE},
    ).status_code == 404
