"""The home page as Home B (Paket 4, PR 17): the tank.

In order down the page: the big flask with the fish (tests/test_fish.py),
the heading, the importer on parchment, the ticker band (tests/test_ticker.py),
two tiles of what you get and the links. The importer is compact (B-12).
The tiles are information, not links - the way in is the form - and each
carries its symbol.
"""

import re

import pytest
from django.urls import reverse

from tests.test_guests import upload
from tests.test_i18n import GERMAN

pytestmark = pytest.mark.django_db


def _home(client):
    return client.get(reverse("home")).content.decode()


def _tiles(body):
    return re.findall(r'<section class="home-tile.*?</section>', body, re.S)


def test_the_page_runs_from_the_flask_down_to_the_tiles(client):
    body = _home(client)
    order = ["data-fish", "<h1", "data-home-import", "data-ticker", 'class="home-tile']

    places = [body.index(mark, body.index("<main")) for mark in order]
    assert places == sorted(places)


def test_the_footer_alone_links_method_precons_and_resources(client):
    """Deck page v2, PR A: the owner found them twice, above and in the footer."""
    body = _home(client)
    main = body[body.index("<main"):body.index("</main>")]

    for name in ("methodology", "resources", "datapages:precons"):
        assert f'href="{reverse(name)}"' not in main
        assert f'href="{reverse(name)}"' in body


def test_one_more_holds_the_export_guides_and_the_options(client):
    body = _home(client)

    assert body.count("<details") == body.count("data-export-guides") == 1
    more = body[body.index("data-export-guides"):body.index("</details>")]
    assert "Where do I get my list?" in more
    assert 'name="name"' in more and 'name="format"' in more


def test_the_tank_line_says_thousands_not_ten_thousand(client):
    body = _home(client)

    assert ("Drop your Commander list in the tank. "
            "It plays thousands of games while you watch.") in body
    assert "ten thousand" not in body


def test_two_tiles_each_with_its_symbol_and_picture_and_no_link(client):
    tiles = _tiles(_home(client))

    assert len(tiles) == 2
    for tile, picture in zip(tiles, ("home-report.webp", "home-hand.webp"), strict=True):
        assert '<svg class="icon' in tile
        assert picture in tile
        assert 'alt="' in tile
        assert "<a " not in tile, "a tile is information; the form is the way in"
    assert "Simulate" in tiles[0] and "Draw a hand" in tiles[1]


def test_a_stranger_reads_no_account_is_needed(client):
    body = _home(client)

    assert "No account needed." in body
    assert reverse("account_signup") in body
    assert "try-steps" not in body, "the steps stand on the trial page"


def test_a_member_gets_the_importer_and_their_decks(client, django_user_model):
    member = django_user_model.objects.create_user(email="h@example.com", password="pw-x-1234")
    client.force_login(member)
    body = _home(client)

    assert "data-home-import" in body
    assert reverse("decks:list") in body
    assert "No account needed." not in body


def test_a_guest_goes_back_to_its_deck_without_a_form(client):
    upload(client)
    body = _home(client)

    assert "Back to your deck" in body
    assert "data-home-import" not in body
    assert "data-fish" in body


def test_the_tank_in_german(client, settings):
    settings.LANGUAGES = GERMAN
    client.cookies["django_language"] = "de"
    body = _home(client)

    assert "Poke the goldfish" not in body
    assert "in the tank" not in body
    assert "thousands of games: lands" not in body


HINT = "Your export from Archidekt, Moxfield, ManaBox & co. – file or text."


def test_the_home_importer_is_compact_with_one_line_of_hint(client):
    """B-12: a low box, so the ticker shows without scrolling on a laptop."""
    body = _home(client)
    form = body[body.index("data-home-import"):body.index("</form>")]

    assert "import-compact" in form
    assert HINT in form
    assert "Only the card names are needed" not in form
    assert form.index("data-export-guides") < form.index('type="submit"')
    assert "import-foot" in form


def test_the_import_page_keeps_the_full_box(client, django_user_model):
    member = django_user_model.objects.create_user(email="i@example.com", password="pw-x-1234")
    client.force_login(member)
    body = client.get(reverse("decks:import")).content.decode()

    assert "import-compact" not in body
    assert HINT not in body
    assert "Only the card names are needed" in body
