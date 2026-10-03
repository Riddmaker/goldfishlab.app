"""Phase 12 A: the language switch, and numbers a machine reads.

**Numbers first.** With a language on, Django formats every float it prints in
that language: German writes 12.5 as "12,5". In text that is right; in an SVG
coordinate, a CSS width or an `aria-valuenow` it breaks the chart without an
error. Django ships German formats, so German is switched on here (with our
German catalogue, phase 12 D) and the pages with charts and bars are rendered in it.

**The switch.** No language in the URL (Q1): the account's choice, then the
cookie the footer sets, then the browser, then English. Only a language that is
switched on (`LANGUAGES_ON`) is accepted anywhere.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts import privacy
from decks import services as deck_services
from guests import services as guest_services
from simulations import services, tasks
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
GERMAN = [("en", "English"), ("de", "Deutsch")]


class FakeRedis:
    """The run slot counter, without a network (as in test_simulations_runs)."""

    def __init__(self):
        self.values = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    def expire(self, key, seconds):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True


@pytest.fixture
def german(settings):
    settings.LANGUAGES = GERMAN


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="sprache@example.com", password="pw-test-only")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    ).deck


@pytest.fixture
def finished_run(owner, deck, monkeypatch):
    monkeypatch.setattr(services, "_redis", FakeRedis)
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=4, seed=7)
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    return run


# --- numbers a machine reads -------------------------------------------------

#: Attributes whose value a browser parses as numbers. `points` is apart: it
#: uses commas between x and y on purpose.
_NUMERIC = re.compile(
    r'\s((?:x|y|x1|x2|y1|y2|cx|cy|r|width|height|viewBox|transform|style|'
    r'aria-value\w+|data-[\w-]+|stroke-dash\w+))="([^"]*)"'
)
_POINTS = re.compile(r'\spoints="([^"]*)"')
_DECIMAL_COMMA = re.compile(r"\d,\d")


def machine_number_faults(body: str) -> list[str]:
    faults = [f'{name}="{value}"' for name, value in _NUMERIC.findall(body)
              if _DECIMAL_COMMA.search(value)]
    for points in _POINTS.findall(body):
        faults += [f'points="{pair}"' for pair in points.split() if pair.count(",") != 1]
    return faults


def test_the_check_finds_a_decimal_comma():
    """Guard against a check that can never fail."""
    assert machine_number_faults('<rect width="12,5" style="width: 3,5%">')
    assert machine_number_faults('<polyline points="1,5,2 3,4">')
    assert not machine_number_faults('<polyline points="1.5,2 3,4" width="12.5">')


def test_the_report_draws_its_charts_with_points_in_german(client, owner, finished_run, german):
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    response = client.get(reverse("simulations:detail", args=[finished_run.pk]))
    body = response.content.decode()

    assert response.headers["Content-Language"] == "de"
    assert "<svg" in body
    assert machine_number_faults(body) == []


def test_the_progress_bar_and_the_deck_page_in_german(client, owner, deck, german):
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=4, seed=7,
                                       games_done=13, status=SimulationRun.Status.RUNNING)
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    pages = [
        reverse("simulations:detail", args=[run.pk]),
        reverse("decks:detail", args=[deck.pk]),
        reverse("billing:plans"),
    ]
    for url in pages:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers["Content-Language"] == "de", url
        assert machine_number_faults(response.content.decode()) == [], url


# --- which language a request gets ----------------------------------------------


def test_english_alone_shows_no_switcher(client):
    body = client.get(reverse("home")).content.decode()

    assert 'name="language"' not in body


def test_with_a_second_language_the_footer_offers_each_in_its_own_name(client, german):
    body = client.get(reverse("home")).content.decode()

    assert 'action="/i18n/"' in body
    assert '<option value="de" lang="de"' in body
    assert "Deutsch" in body


def test_on_a_german_page_english_is_still_called_english(client, german):
    """Django's own tag would print "Englisch" here, from its catalogue."""
    client.cookies["django_language"] = "de"

    body = client.get(reverse("home")).content.decode()

    assert '<option value="en" lang="en"' in body
    assert "Englisch" not in body


def test_the_browser_decides_when_nothing_was_picked(client, german):
    response = client.get(reverse("home"), headers={"Accept-Language": "de-CH,de;q=0.9"})

    assert response.headers["Content-Language"] == "de"
    assert "Accept-Language" in response.headers["Vary"]


def test_a_language_that_is_off_is_never_served(client):
    """German exists in Django, but it is not switched on here."""
    client.cookies["django_language"] = "de"

    response = client.get(reverse("home"), headers={"Accept-Language": "de"})

    assert response.headers["Content-Language"] == "en"


def test_switching_sets_a_year_long_cookie_and_goes_back(client, german):
    response = client.post(reverse("set_language"),
                           {"language": "de", "next": "/about/methodology/"})

    assert response.status_code == 302
    assert response.url == "/about/methodology/"
    cookie = response.cookies["django_language"]
    assert cookie.value == "de"
    assert cookie["max-age"] == 365 * 24 * 60 * 60
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"


def test_switching_never_sends_anybody_to_another_site(client, german):
    response = client.post(reverse("set_language"),
                           {"language": "de", "next": "https://evil.example/"})

    assert response.url == "/"


def test_a_language_that_is_off_cannot_be_picked(client, owner):
    client.force_login(owner)

    response = client.post(reverse("set_language"), {"language": "de", "next": "/"})

    assert response.status_code == 400
    assert "django_language" not in response.cookies
    owner.refresh_from_db()
    assert owner.language == ""


def test_switching_is_a_post(client, german):
    response = client.get(reverse("set_language"), {"language": "de"})

    assert response.status_code == 405


def test_a_signed_in_choice_is_saved_and_wins_over_the_browser(client, owner, german):
    client.force_login(owner)
    client.post(reverse("set_language"), {"language": "de", "next": "/"})
    owner.refresh_from_db()
    assert owner.language == "de"

    # Another browser: no cookie, an English one.
    client.cookies.clear()
    client.force_login(owner)
    response = client.get(reverse("home"), headers={"Accept-Language": "en"})

    assert response.headers["Content-Language"] == "de"


def test_a_saved_language_that_was_switched_off_falls_back(client, owner):
    owner.language = "de"
    owner.save(update_fields=["language"])
    client.force_login(owner)

    response = client.get(reverse("home"))

    assert response.headers["Content-Language"] == "en"


def test_a_guest_can_switch_and_keeps_it_when_saving(client, owner, german):
    guest = User.objects.create_user(email="guest@guest.invalid", is_guest=True)
    client.force_login(guest)

    response = client.post(reverse("set_language"), {"language": "de", "next": "/try/"})
    guest.refresh_from_db()

    assert response.status_code == 302
    assert guest.language == "de"
    guest_services.claim(guest, owner)
    owner.refresh_from_db()
    assert owner.language == "de"


def test_the_export_names_the_language(owner):
    owner.language = "de"

    assert privacy.export(owner)["account"]["language"] == "de"


def test_the_privacy_page_names_the_language_cookie(client):
    body = client.get(reverse("privacy")).content.decode()

    assert "Four, all strictly necessary" in body
    assert "django_language" in body


# --- the catalogues -------------------------------------------------------------

LOCALE = Path(__file__).resolve().parent.parent / "locale"
CATALOGUES = sorted(LOCALE.glob("*/LC_MESSAGES/*.po"))
_PLACEHOLDER = re.compile(r"%\(\w+\)[sdif]|%[sdif]")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _entries(po_path):
    import polib

    return [entry for entry in polib.pofile(str(po_path)) if entry.translated()]


def _pairs(entry):
    """(source, translation) for every form, plural ones included."""
    if entry.msgid_plural:
        # A language with one form (Japanese) uses it for every count, 1 too.
        single = len(entry.msgstr_plural) == 1
        return [(entry.msgid_plural if index or single else entry.msgid, text)
                for index, text in sorted(entry.msgstr_plural.items())]
    return [(entry.msgid, entry.msgstr)]


@pytest.mark.parametrize("po_path", CATALOGUES, ids=lambda path: path.parts[-3])
def test_every_compiled_catalogue_matches_its_source(po_path):
    """A .po edited without `scripts/compilemessages.py` ships the old text."""
    import polib

    mo_path = po_path.with_suffix(".mo")

    assert mo_path.exists(), f"run scripts/compilemessages.py ({mo_path.name} missing)"
    assert mo_path.read_bytes() == polib.pofile(str(po_path)).to_binary()


@pytest.mark.parametrize("po_path", CATALOGUES, ids=lambda path: path.parts[-3])
def test_no_translation_breaks_a_placeholder_or_adds_markup(po_path):
    """A misspelt %(count)s is a 500 for that language alone, and a translation
    is not escaped: markup the English does not have would reach the page."""
    faults = []
    for entry in _entries(po_path):
        for source, text in _pairs(entry):
            # Leaving one out is allowed ("eine Karte" for "%(count)s card");
            # one the English does not have is the crash.
            if not set(_PLACEHOLDER.findall(text)) <= set(_PLACEHOLDER.findall(source)):
                faults.append(f"placeholders: {source!r}")
            if not set(_TAG.findall(text)) <= set(_TAG.findall(source)):
                faults.append(f"markup: {source!r}")

    assert faults == []
