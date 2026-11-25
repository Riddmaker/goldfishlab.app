"""A look per deck (B-10, PR 4). The page is the deck's: on the free plan a
change is for every deck; a paid plan may keep it to this deck, up to the
plan's number of looks (`Plan.features["looks"]`)."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse

from accounts.models import User
from billing.models import Plan, Subscription
from decks.models import Deck
from looks import presets, services
from looks.models import Look


@pytest.fixture(autouse=True)
def share(settings, tmp_path):
    settings.CUSTOM_IMAGES_DIR = tmp_path / "share"


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="per-deck@example.com", password="pw-for-test-only")


@pytest.fixture
def paid(owner):
    # Every account gets a subscription on sign-up; this one moves to Koi.
    Subscription.objects.filter(user=owner).update(plan=Plan.objects.get(slug="koi"))
    return User.objects.get(pk=owner.pk)


@pytest.fixture
def decks(owner):
    return [Deck.objects.create(owner=owner, name=f"Deck {n}") for n in range(2)]


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _post(client, deck, **fields):
    data = {"mat": presets.STANDARD_MAT, "sleeve": presets.STANDARD_SLEEVE, **fields}
    return client.post(reverse("decks:look", args=[deck.pk]), data)


def _worn(user, deck) -> str:
    request = RequestFactory().get("/")
    request.user = user
    return Template("{% load looks %}{% look_style deck %}").render(
        Context({"request": request, "deck": deck}))


def test_the_plans_say_how_many_looks(db):
    assert Plan.objects.get(slug="free").features.get("looks") is None
    assert {Plan.objects.get(slug=s).features["looks"] for s in ("koi", "kraken")} == {10}


# --- free: one look, every deck --------------------------------------------------


def test_free_a_change_from_any_deck_is_for_every_deck(signed_in, owner, decks):
    page = signed_in.get(reverse("decks:look", args=[decks[0].pk])).content.decode()
    assert 'name="scope"' not in page
    assert reverse("pricing") in page

    _post(signed_in, decks[0], mat="felt", scope=services.DECK)  # not on this plan

    look = Look.objects.get()
    assert look.is_default and look.deck is None
    assert _worn(owner, decks[1]) == _worn(owner, decks[0]) != ""


# --- paid: this deck alone -------------------------------------------------------


def test_paid_this_deck_alone(signed_in, paid, decks):
    _post(signed_in, decks[0], mat="felt")
    _post(signed_in, decks[1], mat="blood", scope=services.DECK)

    assert presets.MATS["blood"][1] in _worn(paid, decks[1])
    assert presets.MATS["felt"][1] in _worn(paid, decks[0])
    page = signed_in.get(reverse("decks:look", args=[decks[1].pk])).content.decode()
    assert 'value="deck" checked' in page
    assert "Back to the look of all decks" in page


def test_paid_back_to_all_drops_the_decks_own(signed_in, paid, decks):
    _post(signed_in, decks[0], mat="felt")
    _post(signed_in, decks[1], mat="blood", scope=services.DECK)

    _post(signed_in, decks[1], mat="water", scope=services.ALL)

    assert not Look.objects.filter(deck=decks[1]).exists()
    assert presets.MATS["water"][1] in _worn(paid, decks[0])
    assert _worn(paid, decks[1]) == _worn(paid, decks[0])


def test_paid_reset_on_a_deck_goes_back_to_the_look_of_all(signed_in, paid, decks):
    _post(signed_in, decks[0], mat="felt")
    _post(signed_in, decks[1], mat="blood", scope=services.DECK)

    response = signed_in.post(reverse("decks:look", args=[decks[1].pk]), {"reset": "1"})

    assert response.status_code == 302
    assert list(Look.objects.values_list("is_default", flat=True)) == [True]
    assert presets.MATS["felt"][1] in _worn(paid, decks[1])


def test_the_plans_number_of_looks_holds(signed_in, paid):
    Look.objects.create(owner=paid, is_default=True)
    for n in range(9):
        Look.objects.create(owner=paid, deck=Deck.objects.create(owner=paid, name=f"d{n}"))
    one_more = Deck.objects.create(owner=paid, name="eleventh")

    response = _post(signed_in, one_more, mat="blood", scope=services.DECK)

    assert response.status_code == 400
    assert "Your plan keeps 10 looks." in response.context["form"].errors["scope"][0]
    assert Look.objects.filter(owner=paid).count() == 10


def test_after_a_downgrade_the_decks_own_look_rests(paid, decks):
    Look.objects.create(owner=paid, is_default=True, mat_preset="felt")
    Look.objects.create(owner=paid, deck=decks[1], mat_preset="blood")
    Subscription.objects.filter(user=paid).update(plan=Plan.objects.get(slug="free"))
    paid = User.objects.get(pk=paid.pk)

    assert presets.MATS["felt"][1] in _worn(paid, decks[1])
    assert Look.objects.filter(deck=decks[1]).exists()  # back with the plan


# --- the rows --------------------------------------------------------------------


def test_a_look_is_the_accounts_or_a_decks_never_both(owner, decks):
    with pytest.raises(IntegrityError), transaction.atomic():
        Look.objects.create(owner=owner, is_default=True, deck=decks[0])


def test_deleting_the_deck_deletes_its_look(owner, decks):
    Look.objects.create(owner=owner, deck=decks[0])

    decks[0].delete()

    assert not Look.objects.exists()


def test_nobody_else_sees_a_decks_look(paid, decks):
    Look.objects.create(owner=paid, deck=decks[0], mat_preset="blood")

    assert _worn(AnonymousUser(), decks[0]) == ""
