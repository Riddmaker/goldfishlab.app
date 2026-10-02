"""Phase 1: the deck screens.

Two things these tests are really for:

1. **Ownership.** Every deck queryset is filtered by owner at the source. The
   test that matters is the one where another user's deck returns 404 rather
   than a permission error - because a permission error still confirms the
   deck exists.
2. **The pages render at all.** Phase 0 shipped three bugs that answered HTTP
   200 with a broken page, which is why screenshots are part of the workflow.
   A view test cannot see styling, but it can see a template that raises.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards.models import OracleCard
from decks import services
from decks.importers import columns
from decks.models import Deck, DeckCard, PendingImport

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
PASSWORD = "pw-for-test-only"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password=PASSWORD)


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def deck(owner):
    outcome = services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    )
    return outcome.deck


# --- access control ---------------------------------------------------------


@pytest.mark.parametrize("route", ["decks:list", "decks:import"])
def test_deck_screens_require_signing_in(client, route):
    response = client.get(reverse(route))
    assert response.status_code == 302
    assert "login" in response.url


def test_another_users_deck_is_not_found_rather_than_forbidden(client, deck):
    """404, not 403. A 403 confirms the deck exists, which is itself a leak."""
    intruder = User.objects.create_user(email="other@example.com", password=PASSWORD)
    client.force_login(intruder)

    assert client.get(deck.get_absolute_url()).status_code == 404
    assert client.post(reverse("decks:delete", args=[deck.id])).status_code == 404
    assert Deck.objects.filter(pk=deck.pk).exists()


def test_another_users_import_review_is_not_found(client, deck):
    record = deck.imports.first()
    intruder = User.objects.create_user(email="other2@example.com", password=PASSWORD)
    client.force_login(intruder)

    assert client.get(reverse("decks:review", args=[record.id])).status_code == 404


# --- the pages --------------------------------------------------------------


def test_the_deck_list_shows_the_users_decks(signed_in, deck):
    response = signed_in.get(reverse("decks:list"))
    assert response.status_code == 200
    assert deck.name in response.content.decode()


def test_the_empty_state_offers_the_importer(signed_in):
    response = signed_in.get(reverse("decks:list"))
    body = response.content.decode()
    assert "Nothing here yet" in body
    assert reverse("decks:import") in body


def test_the_deck_page_renders_its_analysis(signed_in, deck):
    response = signed_in.get(deck.get_absolute_url())
    body = response.content.decode()

    assert response.status_code == 200
    assert "Mana curve" in body
    assert "Commander legality" in body
    assert "Karsten" in body


def test_the_deck_page_admits_what_it_could_not_read(signed_in, deck):
    """The coverage panel ships in Phase 1, not in Phase 4.

    A page that shows a curve and a verdict without saying which cards it
    failed to understand is exactly the overclaim this product is against.
    Phase 9 D folded it away; folded is not gone.
    """
    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "What the engine cannot model" in body
    assert "could not resolve on its own" in body


def test_the_review_screen_lists_every_unmatched_row(signed_in, deck):
    record = deck.imports.first()
    body = signed_in.get(reverse("decks:review", args=[record.id])).content.decode()

    assert "Not A Real Magic Card" in body
    assert "How exact the match was" in body


# --- the import flow --------------------------------------------------------


def test_uploading_a_clean_file_lands_on_the_deck(signed_in, catalogue):
    upload = _upload("clean.txt", b"1 Sol Ring\n1 Necropotence\n")
    response = signed_in.post(reverse("decks:import"), {"file": upload}, follow=True)

    assert response.status_code == 200
    assert "Imported 2 rows" in response.content.decode()


def test_uploading_a_file_with_unknown_rows_lands_on_the_review(signed_in, catalogue):
    upload = _upload("dirty.txt", b"1 Sol Ring\n1 Definitely Not A Card\n")
    response = signed_in.post(reverse("decks:import"), {"file": upload}, follow=True)

    body = response.content.decode()
    assert "could not be matched" in body
    assert "Definitely Not A Card" in body


def test_a_csv_from_a_tool_we_have_never_heard_of_just_imports(signed_in, catalogue):
    """`Count,Card,Set` is nobody's verified format and reads perfectly.

    This file used to land on the format picker under "other formats arrive in
    a later phase". Every column in it is one the vocabulary knows, so there
    was never anything to wait for - what was missing was the realisation that
    the thing worth recognising is a column and not a website.
    """
    upload = _upload("mystery.csv", b"Count,Card,Set\n1,Sol Ring,cmd\n")
    response = signed_in.post(reverse("decks:import"), {"file": upload}, follow=True)

    assert response.status_code == 200
    assert Deck.objects.count() == 1
    assert DeckCard.objects.filter(oracle_card__front_name="Sol Ring").exists()


def test_a_csv_that_names_no_card_still_asks_rather_than_guessing(signed_in, catalogue):
    """The registry's `None`, still doing its job on the only file left."""
    upload = _upload("mystery.csv", b"col_a,col_b,col_c\n1,Sol Ring,cmd\n")
    response = signed_in.post(reverse("decks:import"), {"file": upload})

    assert response.status_code == 200
    assert "not recognised" in response.content.decode()
    assert not Deck.objects.exists()


# --- the mapping screen -----------------------------------------------------
#
# The screen that replaced a plan to write seven parsers. Its whole value is in
# the two things it refuses to do: import before somebody has answered, and
# accept an answer nobody gave.


def test_a_missing_quantity_column_asks_instead_of_importing(signed_in, catalogue):
    """The 28-Swamp bug, at the level of the screen that now prevents it."""
    upload = _upload("noqty.csv", b"Name,Edition\nSol Ring,cmd\nSwamp,tor\n")
    response = signed_in.post(reverse("decks:import"), {"file": upload}, follow=True)

    assert response.status_code == 200
    body = response.content.decode()
    assert "Which column is which?" in body
    assert "Sol Ring" in body, "the file's own rows are shown, so a person can choose"
    assert not Deck.objects.exists(), "nothing is written until somebody answers"
    assert PendingImport.objects.count() == 1


def test_the_mapping_screen_will_not_be_clicked_through(signed_in, catalogue):
    """Submitting it untouched is not consent, and must not default to 1.

    The quantity dropdown starts blank *and* required precisely so that the
    lazy path is a validation error rather than a silently wrong deck. If this
    test ever passes by importing, the original bug is back behind one extra
    button.
    """
    upload = _upload("noqty.csv", b"Name,Edition\nSwamp,tor\nSol Ring,cmd\n")
    signed_in.post(reverse("decks:import"), {"file": upload})
    pending = PendingImport.objects.get()

    response = signed_in.post(reverse("decks:map", args=[pending.id]), {"name": "Name"})

    assert response.status_code == 200
    assert "Choose which column holds the quantity" in response.content.decode()
    assert not Deck.objects.exists()


def test_a_confirmed_mapping_imports_and_clears_the_pending_upload(signed_in, catalogue):
    upload = _upload("noqty.csv", b"Name,Edition\nSol Ring,cmd\n")
    signed_in.post(reverse("decks:import"), {"file": upload})
    pending = PendingImport.objects.get()

    response = signed_in.post(
        reverse("decks:map", args=[pending.id]),
        {"name": "Name", "set_code": "Edition", "quantity": columns.ABSENT},
        follow=True,
    )

    assert response.status_code == 200
    assert DeckCard.objects.filter(oracle_card__front_name="Sol Ring").exists()
    assert not PendingImport.objects.exists(), "the held upload deletes itself"


def test_a_person_may_declare_there_is_no_quantity_column(signed_in, catalogue):
    """The honesty rule, stated precisely.

    The application may not invent a quantity. A person is allowed to tell it
    there is not one - and then every row is one copy because somebody said so,
    which is a different thing from a default nobody chose.
    """
    upload = _upload("noqty.csv", b"Name,Edition\nSwamp,tor\nSwamp,cmd\n")
    signed_in.post(reverse("decks:import"), {"file": upload})
    pending = PendingImport.objects.get()

    signed_in.post(
        reverse("decks:map", args=[pending.id]),
        {"name": "Name", "set_code": "Edition", "quantity": columns.ABSENT},
    )

    entry = DeckCard.objects.get(oracle_card__front_name="Swamp")
    assert entry.quantity == 2, "two rows, one copy each, because that is what was said"


def test_somebody_elses_pending_upload_is_a_404(signed_in, catalogue):
    """Their file, not merely their import. Filtered at the source."""
    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    pending = PendingImport.objects.create(
        owner=stranger,
        kind=PendingImport.Kind.DECK,
        text="Name,Edition\nSol Ring,cmd\n",
        parser="csv",
    )

    assert signed_in.get(reverse("decks:map", args=[pending.id])).status_code == 404


def test_the_preview_panel_reports_the_mapping_it_was_given(signed_in, catalogue):
    """htmx fetches this fragment on every change of a dropdown."""
    upload = _upload("noqty.csv", b"Name,Edition\nSol Ring,cmd\n")
    signed_in.post(reverse("decks:import"), {"file": upload})
    pending = PendingImport.objects.get()

    complete = signed_in.post(
        reverse("decks:map-preview", args=[pending.id]),
        {"name": "Name", "set_code": "Edition", "quantity": columns.ABSENT},
    )
    assert "Sol Ring" in complete.content.decode()

    # An unfinished answer previews as the unconfirmed mapping, never as a
    # column of 1s - which would be the exact lie the screen exists to stop.
    # And it says so in the words of this screen, not in the parser's, which
    # tell the reader to go and re-export the file they are standing on.
    partial = signed_in.post(reverse("decks:map-preview", args=[pending.id]), {"name": "Name"})
    body = partial.content.decode()
    assert "Choose a column above" in body
    assert "Re-export" not in body
    assert "Sol Ring" not in body, "no rows previewed from a mapping nobody finished"


def test_setting_the_commander_by_hand(signed_in, catalogue, owner):
    outcome = services.import_deck(owner=owner, raw=b"1 Necropotence\n1 Sol Ring\n", name="Manual")
    deck = outcome.deck
    assert deck.commander is None

    card = OracleCard.objects.get(front_name="Necropotence")
    signed_in.post(reverse("decks:set-commander", args=[deck.id]), {"oracle_id": str(card.pk)})

    deck.refresh_from_db()
    assert deck.commander_id == card.pk
    # It moved to the command zone; it did not stay in the 99 as well.
    assert not deck.entries.filter(oracle_card=card).exists()


def test_replacing_the_commander_puts_the_old_one_back_in_the_99(signed_in, catalogue, owner):
    """Which card leaves a deck is its owner's call, not a side effect."""
    deck = services.import_deck(
        owner=owner, raw=b"1 Necropotence\n1 Sol Ring\n", name="Manual"
    ).deck
    necro = OracleCard.objects.get(front_name="Necropotence")
    ring = OracleCard.objects.get(front_name="Sol Ring")
    url = reverse("decks:set-commander", args=[deck.id])

    signed_in.post(url, {"oracle_id": str(necro.pk)})
    signed_in.post(url, {"oracle_id": str(ring.pk)})

    deck.refresh_from_db()
    assert deck.commander_id == ring.pk
    assert list(deck.entries.values_list("oracle_card__front_name", flat=True)) == [
        "Necropotence"
    ]


def test_a_card_outside_the_deck_cannot_be_made_its_commander(signed_in, catalogue, owner):
    outcome = services.import_deck(owner=owner, raw=b"1 Sol Ring\n", name="Manual")
    outsider = OracleCard.objects.exclude(
        pk__in=DeckCard.objects.filter(deck=outcome.deck).values("oracle_card")
    ).first()

    signed_in.post(
        reverse("decks:set-commander", args=[outcome.deck.id]), {"oracle_id": str(outsider.pk)}
    )

    outcome.deck.refresh_from_db()
    assert outcome.deck.commander is None


# --- the paste tab ----------------------------------------------------------
#
# Phase 9 A: a pasted list is the fastest way in for a beginner. It must reach
# the importer through the same door as a file - the same decode and the same
# ceilings - and the tab the person chose decides which input counts.


def test_a_pasted_list_imports_like_a_file(signed_in, catalogue):
    text = "// Commander\n1 Chainer, Dementia Master\n// Deck\n1 Sol Ring\n"
    response = signed_in.post(
        reverse("decks:import"), {"source": "paste", "text": text}, follow=True
    )

    assert response.status_code == 200
    deck = Deck.objects.get()
    assert deck.commander.front_name == "Chainer, Dementia Master"
    assert DeckCard.objects.filter(deck=deck, oracle_card__front_name="Sol Ring").exists()


def test_the_chosen_tab_decides_which_input_counts(signed_in, catalogue):
    """Text left behind in the other tab is not imported by accident."""
    upload = _upload("deck.txt", b"1 Sol Ring\n")
    signed_in.post(
        reverse("decks:import"),
        {"source": "file", "file": upload, "text": "1 Necropotence\n"},
    )

    assert DeckCard.objects.filter(oracle_card__front_name="Sol Ring").exists()
    assert not DeckCard.objects.filter(oracle_card__front_name="Necropotence").exists()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"source": "paste", "text": "   "}, "Paste your deck list first."),
        ({"source": "file"}, "Choose a file first"),
        ({}, "Choose a file first"),
    ],
)
def test_an_empty_import_says_what_is_missing(signed_in, catalogue, data, message):
    response = signed_in.post(reverse("decks:import"), data)

    assert response.status_code == 200
    assert message in response.content.decode()
    assert not Deck.objects.exists()


def test_a_pasted_list_has_the_same_size_ceiling_as_a_file(signed_in, catalogue):
    text = "1 Sol Ring\n" * (services.MAX_UPLOAD_BYTES // 11 + 1)
    response = signed_in.post(reverse("decks:import"), {"source": "paste", "text": text})

    body = response.content.decode()
    assert "The limit is" in body
    assert not Deck.objects.exists()


def test_a_refused_paste_comes_back_on_the_paste_tab(signed_in, catalogue):
    """An error under a tab nobody can see is an error nobody reads."""
    response = signed_in.post(reverse("decks:import"), {"source": "paste", "text": ""})

    body = response.content.decode()
    assert re.search(r'id="source-paste"\s+checked', body)
    assert not re.search(r'id="source-file"\s+checked', body)


def test_an_unrecognised_list_opens_the_options_it_points_to(signed_in, catalogue):
    """The "not recognised" help names the format picker, so it must be visible."""
    response = signed_in.post(
        reverse("decks:import"), {"source": "paste", "text": "col_a,col_b\n1,Sol Ring\n"}
    )

    body = response.content.decode()
    assert "not recognised" in body
    assert re.search(r"<details[^>]*\sopen>", body)


def test_the_import_page_offers_both_ways_in(signed_in):
    body = signed_in.get(reverse("decks:import")).content.decode()

    assert "Drop your deck file here" in body
    assert "Paste a list" in body
    assert "Any CSV works." in body


def test_the_import_page_says_what_a_deck_file_needs(signed_in):
    """Phase 10 T2.1: visible, not only for screen readers - and true.

    The importer's own tests prove the sentence: bare names, quantities and set
    codes all parse (`test_plain_text_line_shapes`), and a CSV without a
    quantity column is counted once per row on the mapping screen.
    """
    body = signed_in.get(reverse("decks:import")).content.decode()

    assert "Only the card names are needed. Quantities and set codes work too." in body
    assert 'id="dropzone-hint" class="sr-only"' not in body


def test_deleting_a_deck_leaves_the_catalogue_alone(signed_in, deck):
    cards_before = OracleCard.objects.count()
    signed_in.post(reverse("decks:delete", args=[deck.id]))

    assert not Deck.objects.filter(pk=deck.pk).exists()
    assert OracleCard.objects.count() == cards_before


def _upload(name: str, content: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type="text/plain")
