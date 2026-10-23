"""The art of the printings an imported file named (decks/printing_art.py).

Scryfall is never called: `scryfall.collection` is replaced by a stand-in
that answers from the request, the way the real endpoint does.
"""

import io
import json

import pytest
from django.urls import reverse

from accounts.models import User
from cards import scryfall
from decks import printing_art, services
from decks.models import Deck, first_image
from decks.tasks import fetch_printing_art

ART = "https://cards.scryfall.io/normal/front/a/b/{}.jpg"

TEXT_LIST = (
    b"1x Chainer, Dementia Master (cmm) 505\n"
    b"1x Sol Ring (brc) 160\n"
    b"1x Sol Ring (sld) 1011 *F*\n"
    b"30 Swamp\n"
)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="art@example.com", password="pw-for-test-only")


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def scryfall_answer(monkeypatch, *, oracle_of=None, host=ART, fail=False):
    """Answer each identifier with a card of the deck's own oracle id - or of
    the one `oracle_of` maps a set to - and record what was asked."""
    from cards.models import OracleCard

    asked = []

    def collection(identifiers, **_):
        if fail:
            raise scryfall.ScryfallError("down")
        asked.extend(identifiers)
        cards = []
        for ident in identifiers:
            set_code = ident.get("set", "")
            number = ident.get("collector_number", "")
            name = {"brc": "Sol Ring", "sld": "Sol Ring", "cmm": "Chainer, Dementia Master",
                    "tst": "Sol Ring"}.get(set_code, "Sol Ring")
            oracle = (oracle_of or {}).get(set_code) or str(
                OracleCard.objects.get(name=name).pk)
            cards.append({"id": ident.get("id", f"{set_code}-{number}"), "oracle_id": oracle,
                          "set": set_code or "tst", "collector_number": number or "1",
                          "image_uris": {"normal": host.format(f"{set_code}{number}")}})
        return cards

    monkeypatch.setattr(scryfall, "collection", collection)
    return asked


# --- what is asked ----------------------------------------------------------


@pytest.mark.parametrize(("printing", "name", "expected"), [
    ({"set": "5ed", "number": "140"}, "Animate Dead", {"set": "5ed", "collector_number": "140"}),
    ({"set": "plst", "number": "JMP-231"}, "", {"set": "plst", "collector_number": "JMP-231"}),
    ({"set": "fem", "number": "38a"}, "", {"set": "fem", "collector_number": "38a"}),
    ({"set": "cmr", "number": ""}, "Sol Ring", {"name": "Sol Ring", "set": "cmr"}),
    ({"id": "C0698972-EB23-46C6-8EB9-49A4345F82DC", "set": "x"}, "",
     {"id": "c0698972-eb23-46c6-8eb9-49a4345f82dc"}),
    ({"set": "", "number": "", "finish": "foil"}, "Sol Ring", None),
    ({"set": "../etc", "number": "1"}, "Sol Ring", None),
    ({"set": "cmr", "number": "1 OR 1=1"}, "", None),
])
def test_only_well_formed_printings_are_asked_for(printing, name, expected):
    assert printing_art._identifier(printing, name) == expected


def test_the_collection_is_asked_in_batches_of_75(monkeypatch):
    bodies = []

    def fake_open(url, **kwargs):
        bodies.append(kwargs["body"])
        return io.BytesIO(json.dumps({"data": [{"id": "x"}], "not_found": []}).encode())

    monkeypatch.setattr(scryfall, "_open", fake_open)
    monkeypatch.setattr(scryfall.time, "sleep", lambda seconds: None)
    cards = scryfall.collection([{"set": "cmr", "collector_number": str(n)} for n in range(80)])

    assert [len(body["identifiers"]) for body in bodies] == [75, 5]
    assert len(cards) == 2


# --- the import and the worker ---------------------------------------------


def test_an_import_naming_printings_waits_for_their_art(catalogue, user):
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    assert printing_art.is_pending(deck)


def test_an_import_naming_no_printing_does_not_wait(catalogue, user):
    deck = services.import_deck(owner=user, raw=b"1 Sol Ring\n30 Swamp\n").deck
    assert not printing_art.is_pending(deck)


def test_the_worker_keeps_each_printings_art_and_stops_the_wait(catalogue, user, monkeypatch):
    asked = scryfall_answer(monkeypatch)
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    services.set_commander(deck, deck.entries.get(oracle_card__name="Chainer, Dementia Master")
                           .oracle_card)

    assert fetch_printing_art(str(deck.pk)) == 3
    deck.refresh_from_db()
    sol_ring = deck.entries.get(oracle_card__name="Sol Ring")
    assert [p["image"] for p in sol_ring.printings] == [ART.format("brc160"),
                                                       ART.format("sld1011")]
    assert sol_ring.printings_shown.image == ART.format("brc160")
    assert first_image(deck.commander_printings[str(deck.commander_id)]) == ART.format("cmm505")
    assert {"set": "brc", "collector_number": "160"} in asked
    assert not printing_art.is_pending(deck)


def test_a_printing_of_another_card_is_never_shown(catalogue, user, monkeypatch):
    """A typo in a collector number names some other card: its art stays out."""
    from cards.models import OracleCard

    other = str(OracleCard.objects.get(name="Swamp").pk)
    scryfall_answer(monkeypatch, oracle_of={"brc": other})
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck

    fetch_printing_art(str(deck.pk))
    sol_ring = deck.entries.get(oracle_card__name="Sol Ring")
    assert [p.get("image", "") for p in sol_ring.printings] == ["", ART.format("sld1011")]


def test_art_from_anywhere_but_scryfalls_image_host_is_refused(catalogue, user, monkeypatch):
    scryfall_answer(monkeypatch, host="https://evil.example/{}.jpg")
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck

    assert fetch_printing_art(str(deck.pk)) == 0
    assert deck.printing_images() == {}


def test_scryfall_down_leaves_the_deck_as_it_was_and_stops_the_wait(catalogue, user,
                                                                    monkeypatch):
    scryfall_answer(monkeypatch, fail=True)
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    before = list(deck.entries.values_list("printings", flat=True))

    assert fetch_printing_art(str(deck.pk)) == 0
    assert list(deck.entries.values_list("printings", flat=True)) == before
    assert not printing_art.is_pending(deck)


def test_a_deleted_deck_is_no_error(db):
    assert fetch_printing_art("00000000-0000-0000-0000-000000000000") == 0


def test_the_art_does_not_touch_the_decks_updated_at(catalogue, user, monkeypatch):
    scryfall_answer(monkeypatch)
    deck = services.import_deck(owner=user, raw=b"1 Chainer, Dementia Master (cmm) 505\n"
                                                b"// Commander\n1 Sol Ring (brc) 160\n").deck
    updated = Deck.objects.get(pk=deck.pk).updated_at

    fetch_printing_art(str(deck.pk))
    assert Deck.objects.get(pk=deck.pk).updated_at == updated


# --- what the pages show ----------------------------------------------------


def test_the_deck_page_shows_placeholders_and_refetches_while_waiting(catalogue, signed_in,
                                                                      user):
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    body = signed_in.get(deck.get_absolute_url()).content.decode()

    assert "card-art-waiting" in body
    assert "Loading the card art…" in body
    assert f'hx-get="{deck.get_absolute_url()}?art=1"' in body
    assert 'aria-busy="true"' in body


def test_the_deck_page_shows_the_printings_art_once_it_is_in(catalogue, signed_in, user,
                                                             monkeypatch):
    scryfall_answer(monkeypatch)
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    services.set_commander(deck, deck.entries.get(oracle_card__name="Chainer, Dementia Master")
                           .oracle_card)
    fetch_printing_art(str(deck.pk))

    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "card-art-waiting" not in body
    assert f"hx-get=\"{deck.get_absolute_url()}?art=1\"" not in body
    assert ART.format("brc160") in body
    assert ART.format("cmm505") in body

    poll = signed_in.get(deck.get_absolute_url() + "?art=1", HTTP_HX_REQUEST="true")
    fragment = poll.content.decode()
    assert 'id="commander-art"' in fragment and 'hx-swap-oob="true"' in fragment
    assert ART.format("cmm505") in fragment


def test_the_card_page_and_the_playtest_use_the_printings_art(catalogue, signed_in, user,
                                                              monkeypatch):
    from playtest.models import PlaytestSession
    from playtest.views import card_images

    scryfall_answer(monkeypatch)
    deck = services.import_deck(owner=user, raw=TEXT_LIST).deck
    fetch_printing_art(str(deck.pk))
    sol_ring = deck.entries.get(oracle_card__name="Sol Ring").oracle_card

    page = signed_in.get(reverse("simulations:annotate", args=[deck.pk, sol_ring.pk]))
    assert ART.format("brc160") in page.content.decode()
    assert card_images(PlaytestSession(deck=deck))["Sol Ring"] == ART.format("brc160")
