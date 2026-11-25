"""The look on the tables (B-10, PR 3): free play and the deck in your hand
wear the owner's mat and sleeves; nobody else ever sees them, and a lost file
falls back to the template without a word."""

import io

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from PIL import Image

from accounts.models import User
from decks import seeding
from decks.fixtures import LAND_LIGHT
from looks import css, images, presets, services
from looks.models import Look
from playtest import services as playtest
from playtest.models import PlaytestSession


@pytest.fixture(autouse=True)
def share(settings, tmp_path):
    settings.CUSTOM_IMAGES_DIR = tmp_path / "share"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="tables@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def look(owner):
    return Look.objects.create(owner=owner, is_default=True, mat_preset="blood",
                               sleeve_preset="gold", seam=True, seam_colour="#dcc79bff")


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _png():
    out = io.BytesIO()
    Image.new("RGB", (200, 300), "navy").save(out, "PNG")
    return SimpleUploadedFile("s.png", out.getvalue(), content_type="image/png")


def _tag(user, deck) -> str:
    request = RequestFactory().get("/")
    request.user = user
    return Template("{% load looks %}{% look_style deck %}").render(
        Context({"request": request, "deck": deck}))


def test_the_owner_gets_the_look(owner, deck, look):
    assert _tag(owner, deck) == css.style(look)
    assert presets.MATS["blood"][1] in _tag(owner, deck)


@pytest.mark.parametrize("who", ["other", "anonymous"])
def test_nobody_else_sees_it(owner, deck, look, who):
    user = (User.objects.create_user(email="peek@example.com", password="pw-for-test-only")
            if who == "other" else AnonymousUser())

    assert _tag(user, deck) == ""


def test_without_a_look_the_tables_stay_standard(owner, deck):
    assert _tag(owner, deck) == ""


def test_the_deck_in_hand_wears_it(signed_in, deck, look):
    page = signed_in.get(reverse("decks:hand", args=[deck.pk]) + "?seed=1&cut=0")

    assert page.status_code == 200
    assert "--mat-seam: #dcc79bff;" in page.content.decode()


def test_free_play_wears_it(signed_in, deck, owner, look):
    session = playtest.start(deck, owner, seed=7, mode=PlaytestSession.FREE)

    page = signed_in.get(reverse("playtest:detail", args=[session.id])).content.decode()

    assert 'class="playtest-felt"' in page
    assert "--mat-seam: #dcc79bff;" in page


def test_a_lost_file_falls_back_to_the_template(owner, look):
    services.set_image(look, images.SLEEVE, _png())
    look.sleeve_preset = presets.OWN
    look.save()
    assert "/sleeve.webp" in css.sleeve(look)

    images.resolve(look.sleeve_image).unlink()

    assert css.sleeve(look) == presets.SLEEVES[presets.STANDARD_SLEEVE][1]


def test_a_row_pointing_out_of_the_share_falls_back(owner, look):
    look.mat_preset, look.mat_image = presets.OWN, "../../etc/passwd"

    assert css.mat(look) == presets.MATS[presets.STANDARD_MAT][1]
