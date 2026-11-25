"""The Customisation page (B-10, PR 2): pick a template or upload, colours,
the preview's CSS, and back to the standard look."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts.models import User
from decks.models import Deck
from looks import css, images, presets
from looks.models import Look


@pytest.fixture(autouse=True)
def share(settings, tmp_path):
    settings.CUSTOM_IMAGES_DIR = tmp_path / "share"
    return settings.CUSTOM_IMAGES_DIR


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="look-page@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return Deck.objects.create(owner=owner, name="Mine")


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _url(deck):
    return reverse("decks:look", args=[deck.pk])


def _png(size=(300, 400)):
    out = io.BytesIO()
    Image.new("RGB", size, "teal").save(out, "PNG")
    return SimpleUploadedFile("mine.png", out.getvalue(), content_type="image/png")


def _post(client, deck, **fields):
    data = {"mat": presets.STANDARD_MAT, "sleeve": presets.STANDARD_SLEEVE, **fields}
    return client.post(_url(deck), data)


# --- access ----------------------------------------------------------------------


def test_the_deck_page_links_to_it(signed_in, deck):
    page = signed_in.get(deck.get_absolute_url()).content.decode()

    assert f'href="{_url(deck)}"' in page


def test_only_the_deck_owner_reaches_it(client, deck):
    assert client.get(_url(deck)).status_code == 302  # to sign in
    other = User.objects.create_user(email="other-look@example.com", password="pw-for-test-only")
    client.force_login(other)

    assert client.get(_url(deck)).status_code == 404
    assert _post(client, deck).status_code == 404
    assert not Look.objects.exists()


def test_without_a_look_the_standard_is_checked(signed_in, deck):
    page = signed_in.get(_url(deck)).content.decode()

    assert f'name="mat" value="{presets.STANDARD_MAT}"' in page
    assert 'value="ink" data-css=' in page and "checked" in page
    assert "Back to the standard" not in page  # nothing to go back from


# --- saving ----------------------------------------------------------------------


def test_a_template_and_colours_are_saved(signed_in, deck, owner):
    response = _post(signed_in, deck, mat="felt", sleeve="gold",
                     seam="on", seam_colour="#dcc79b", inner_sleeve="on",
                     inner_colour="rgba(156, 196, 220, 0.8)")

    assert response.status_code == 302
    look = Look.objects.get(owner=owner, is_default=True)
    assert (look.mat_preset, look.sleeve_preset) == ("felt", "gold")
    assert (look.seam_colour, look.inner_colour) == ("#dcc79bff", "#9cc4dccc")
    assert look.images() == []


def test_an_upload_makes_it_your_own(signed_in, deck, owner):
    response = signed_in.post(_url(deck), {"mat": "ink", "sleeve": "parchment",
                                           "sleeve_file": _png()})

    assert response.status_code == 302
    look = Look.objects.get(owner=owner)
    assert look.sleeve_preset == presets.OWN
    assert images.resolve(look.sleeve_image).is_file()
    assert reverse("looks:image", args=[look.pk, "sleeve"]) in css.sleeve(look)


def test_a_refused_image_is_shown_at_its_field_and_nothing_is_kept(signed_in, deck, share):
    fake = SimpleUploadedFile("x.png", b"<svg onload=alert(1)>", content_type="image/png")

    response = signed_in.post(_url(deck), {"mat": "ink", "sleeve": "parchment",
                                           "mat_file": fake})

    assert response.status_code == 400
    assert "Upload a PNG, JPEG or WebP image." in response.context["form"].errors["mat_file"]
    assert not Look.objects.exists()
    assert not share.exists() or not any(share.rglob("*.webp"))


def test_own_without_an_image_asks_for_one(signed_in, deck):
    response = _post(signed_in, deck, mat=presets.OWN)

    assert response.status_code == 400
    assert "Choose an image to upload." in response.context["form"].errors["mat_file"]


@pytest.mark.parametrize("typed", ["red; background:url(//evil)", "var(--x)", "#12"])
def test_a_bad_colour_never_reaches_the_page(signed_in, deck, typed):
    response = _post(signed_in, deck, inner_sleeve="on", inner_colour=typed)

    assert response.status_code == 400
    assert "inner_colour" in response.context["form"].errors
    assert "url(//evil)" not in response.context["preview_style"]
    assert not Look.objects.exists()


def test_a_flag_needs_a_colour(signed_in, deck):
    response = _post(signed_in, deck, seam="on")

    assert response.context["form"].errors["seam_colour"] == ["Choose a colour."]


def test_unknown_template_is_refused(signed_in, deck):
    assert _post(signed_in, deck, mat="url(//evil)").status_code == 400


# --- the preview -----------------------------------------------------------------


def test_the_preview_style_is_ours_alone(owner):
    look = Look.objects.create(owner=owner, is_default=True, mat_preset="blood",
                               sleeve_preset=presets.OWN, inner_sleeve=True,
                               inner_colour="#112233ff", seam=False, seam_colour="#445566ff")

    style = css.style(look)

    assert presets.MATS["blood"][1] in style
    assert presets.SLEEVES[presets.STANDARD_SLEEVE][1] in style  # own, but no image yet
    assert "--sleeve-inner: #112233ff;" in style
    assert "--mat-seam: transparent;" in style  # off
    assert css.style(None) == ""


def test_a_stored_colour_that_slipped_past_is_dropped(owner):
    look = Look(owner=owner, inner_sleeve=True, inner_colour="red;}x{")

    assert "--sleeve-inner: transparent;" in css.style(look)


# --- back to the standard --------------------------------------------------------


def test_back_to_the_standard_removes_the_look_and_its_files(
        signed_in, deck, owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        signed_in.post(_url(deck), {"mat": "ink", "sleeve": "parchment", "mat_file": _png()})
    path = images.resolve(Look.objects.get(owner=owner).mat_image)

    with django_capture_on_commit_callbacks(execute=True):
        response = signed_in.post(_url(deck), {"reset": "1"})

    assert response.status_code == 302
    assert not Look.objects.exists()
    assert not path.exists()
