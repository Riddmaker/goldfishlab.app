"""Renaming a deck (E1.73, E2.5): the pencil beside its name.

Two imports of one deck were both called after their commander and stood in
the list alike; a deck could not be renamed at all. The deck's id tells decks
apart, its name is the owner's - any name, also one another deck has.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import services
from decks.models import Deck

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password="pw-for-test-only")


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def deck(owner):
    return services.import_deck(owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer",
                                filename="sample.csv").deck


def rename_url(deck):
    return reverse("decks:rename", args=[deck.pk])


def test_the_field_saves_the_name_and_answers_with_it(signed_in, deck):
    before = deck.updated_at

    response = signed_in.post(rename_url(deck), {"name": "  Chainer, 35 lands  "}, **JSON)

    deck.refresh_from_db()
    assert response.status_code == 200
    assert response.json() == {"name": "Chainer, 35 lands"}
    assert deck.name == "Chainer, 35 lands"
    assert deck.updated_at == before, "a name is not an edit: the list keeps its order"


def test_two_decks_may_share_a_name(signed_in, owner, deck):
    other = Deck.objects.create(owner=owner, name="Other")

    signed_in.post(rename_url(other), {"name": "Chainer"}, **JSON)

    assert set(owner.decks.values_list("name", flat=True)) == {"Chainer"}
    assert owner.decks.count() == 2


@pytest.mark.parametrize(("name", "error"), [
    ("   ", "A deck needs a name."),
    ("x" * 121, "Ensure this value has at most 120 characters (it has 121)."),
])
def test_a_name_that_cannot_be_kept_keeps_the_old_one(signed_in, deck, name, error):
    response = signed_in.post(rename_url(deck), {"name": name}, **JSON)

    deck.refresh_from_db()
    assert response.status_code == 400
    assert response.json() == {"error": error}
    assert deck.name == "Chainer"


def test_without_the_script_the_form_goes_back_where_it_came_from(signed_in, deck):
    response = signed_in.post(rename_url(deck), {"name": "Chainer v2",
                                                 "next": reverse("decks:list")}, follow=True)

    assert response.redirect_chain == [(reverse("decks:list"), 302)]
    assert "The deck is now called Chainer v2." in response.content.decode()


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", ""])
def test_it_goes_back_only_to_this_site(signed_in, deck, target):
    response = signed_in.post(rename_url(deck), {"name": "Chainer v2", "next": target})

    assert response.status_code == 302
    assert response.url == deck.get_absolute_url()


def test_without_the_script_a_refused_name_says_why_on_the_page(signed_in, deck):
    response = signed_in.post(rename_url(deck), {"name": ""})

    body = response.content.decode()
    assert response.status_code == 400
    assert 'role="alert"' in body and "A deck needs a name." in body
    assert 'aria-invalid="true"' in body


def test_the_pencils_page_shows_the_name_to_change(signed_in, deck):
    response = signed_in.get(rename_url(deck) + "?next=/decks/")

    body = response.content.decode()
    assert response.status_code == 200
    assert 'value="Chainer"' in body and 'name="next" value="/decks/"' in body


def test_another_users_deck_is_not_found(client, deck):
    client.force_login(User.objects.create_user(email="other@example.com", password="pw-x-1"))

    assert client.get(rename_url(deck)).status_code == 404
    assert client.post(rename_url(deck), {"name": "Mine now"}, **JSON).status_code == 404
    deck.refresh_from_db()
    assert deck.name == "Chainer"


def test_signed_out_is_sent_to_sign_in(client, deck):
    response = client.post(rename_url(deck), {"name": "Mine now"})

    assert response.status_code == 302 and "login" in response.url


@pytest.mark.parametrize("page", ["list", "detail"])
def test_both_pages_carry_the_pencil_and_a_labelled_field(signed_in, deck, page):
    url = reverse("decks:list") if page == "list" else deck.get_absolute_url()

    body = signed_in.get(url).content.decode()

    assert f'href="{rename_url(deck)}?next=' in body
    assert f'<label for="deck-name-field-{deck.pk}"' in body
    assert f'id="deck-name-field-{deck.pk}"' in body
    assert "js/deck-name.js" in body


def test_in_the_list_the_name_still_opens_the_deck(signed_in, deck):
    body = signed_in.get(reverse("decks:list")).content.decode()

    assert f'<a id="deck-name-{deck.pk}" href="{deck.get_absolute_url()}"' in body


def test_only_the_deck_page_opens_the_field_on_a_click(signed_in, deck):
    assert "data-click-edits" in signed_in.get(deck.get_absolute_url()).content.decode()
    assert "data-click-edits" not in signed_in.get(reverse("decks:list")).content.decode()


def test_a_name_is_text_not_markup(signed_in, deck):
    signed_in.post(rename_url(deck), {"name": "<script>alert(1)</script>"}, **JSON)

    for url in (reverse("decks:list"), deck.get_absolute_url(), rename_url(deck)):
        body = signed_in.get(url).content.decode()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
