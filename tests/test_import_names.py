"""Card names the importer did not know (P20 F6c: E1.48, E1.84).

A list from a Brazilian site named its 29 basic lands `Pântano` and
`Floresta`, and the review offered "Panther Habit" and "Flora Colossus". The
basic lands are now read in every language of the site; every other card is
read in English, and the pages say so. A row that is not found gets the
nearest names first, no longer the first five in the alphabet.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards.models import OracleCard
from decks import resolve, services
from decks.importers.text import PlainTextParser
from decks.models import UnresolvedRow

pytestmark = pytest.mark.django_db

User = get_user_model()
ENGLISH = "Card names in English - only basic lands also work in other languages."


def _card(name, type_line="Sorcery"):
    return OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
        type_line=type_line, oracle_text="", color_identity=[], keywords=[],
        legalities={"commander": "legal"}, mana_cost="", cmc=0)


@pytest.fixture
def basics(db):
    return {name: OracleCard.objects.filter(front_name=name).first()
            or _card(name, f"Basic Land — {name}")
            for name in resolve.BASIC_LANDS}


def _resolve(text):
    return resolve.resolve(list(PlainTextParser().parse(text)))


@pytest.mark.parametrize(("printed", "english"), [
    ("Pântano", "Swamp"), ("Floresta", "Forest"), ("Sumpf", "Swamp"), ("ÎLE", "Island"),
    ("Montaña", "Mountain"), ("平地", "Plains"), ("森", "Forest"),
])
def test_a_basic_land_is_read_in_the_sites_languages(basics, printed, english):
    found = _resolve(f"10 {printed}\n").resolutions[0]

    assert found.card == basics[english]
    assert found.rung == resolve.RUNG_BASIC_LAND


def test_an_english_name_comes_first(basics):
    island = _card("Isla")

    found = _resolve("1 Isla\n").resolutions[0]

    assert found.card == island and found.rung == resolve.RUNG_EXACT_NAME


def test_only_basic_lands_are_read_in_another_language(basics):
    assert not _resolve("1 Cultivar\n").resolutions[0].resolved


@pytest.mark.parametrize(("typed", "meant", "others"), [
    ("big scor", "Big Score",
     ["Big Apple, 3 a.m.", "Big Bertha", "Big Boa", "Big Brother", "Big Bumbo"]),
    ("unexpected windfal", "Unexpected Windfall",
     ["Unexpected Allies", "Unexpected Assistance", "Unexpected Angel",
      "Unexpected Fangs", "Unexpected Potential"]),
    ("Eternal Witnes", "Eternal Witness",
     ["Eternal Dragon", "Eternal Flame", "Eternal Scourge", "Eternal Student",
      "Eternal Thirst"]),
])
def test_the_nearest_name_is_suggested_first(db, typed, meant, others):
    for name in [*others, meant]:
        _card(name)

    suggestions = resolve._suggest(typed)

    assert suggestions[0] == meant
    assert len(suggestions) == resolve.SUGGESTION_LIMIT


def test_the_review_says_names_are_read_in_english(client, catalogue):
    user = User.objects.create_user(email="r@example.com", password="pw-x-1")
    record = services.import_deck(owner=user, raw=b"1 Sol Ring\n1 Cultivar\n",
                                  name="Rafael").record
    client.force_login(user)

    body = client.get(reverse("decks:review", args=[record.id])).content.decode()

    assert UnresolvedRow.objects.filter(raw_name="Cultivar").exists()
    assert ENGLISH in body


@pytest.mark.parametrize("url", ["guests:try", "decks:import"])
def test_the_import_hints_say_so_too(client, url):
    if url == "decks:import":
        client.force_login(User.objects.create_user(email="h@example.com", password="pw-x-1"))

    assert client.get(reverse(url)).content.decode().count(ENGLISH) == 2
