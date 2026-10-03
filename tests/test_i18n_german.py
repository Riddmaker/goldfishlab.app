"""Phase 12 D: German, with the real catalogue.

`test_i18n_pages` proves every word goes through a catalogue; this proves the
German one is complete and says "du" (Q8) - also where the text is allauth's.
"""

import re

import polib
import pytest
from django.urls import reverse

from accounts import allauth_tone
from tests.test_i18n import GERMAN, LOCALE, deck, finished_run, owner  # noqa: F401

pytestmark = pytest.mark.django_db

CATALOGUE = LOCALE / "de" / "LC_MESSAGES" / "django.po"

#: The polite address: "Ihr Konto", "Ihnen", "Geben Sie", "verwenden Sie".
#: Not every "Sie": "Sie braucht" is "it needs" when it opens a sentence.
_SIE = re.compile(r"\b(Ihnen|Ihre[mnrs]?)\b|\b\w+en Sie\b|\bIhr (Konto|Passwort)\b")


def test_every_string_has_a_german_translation():
    untranslated = [entry.msgid for entry in polib.pofile(str(CATALOGUE))
                    if not entry.translated() and not entry.obsolete]
    assert untranslated == []


def test_every_allauth_override_is_translated_and_says_du():
    german = {entry.msgid: entry.msgstr for entry in polib.pofile(str(CATALOGUE))}
    for msgid in allauth_tone.OVERRIDDEN:
        assert german.get(msgid), msgid
        assert not _SIE.search(german[msgid]), msgid


def test_the_check_finds_the_polite_address():
    assert _SIE.search("Bitte geben Sie Ihr Passwort ein.")
    assert _SIE.search("um Ihr Konto zu schützen")
    assert not _SIE.search("Kein Zeitpunkt für diese Combo: Sie braucht eine Kartenart.")


@pytest.fixture
def german(settings):
    settings.LANGUAGES = GERMAN


def _polite(body: str) -> list[str]:
    return [match.group(0) for match in _SIE.finditer(body)]


def test_the_main_pages_in_german(client, owner, deck, finished_run, german):  # noqa: F811
    client.force_login(owner)
    client.cookies["django_language"] = "de"
    for url in (reverse("home"), reverse("decks:list"), reverse("decks:detail", args=[deck.pk]),
                reverse("simulations:detail", args=[finished_run.pk]),
                reverse("billing:plans"), reverse("decks:import"), reverse("accounts:data")):
        body = client.get(url).content.decode()
        assert '<html lang="de">' in body, url
        assert _polite(body) == [], url
    assert "Kenne dein Deck vor dem Spieleabend." in client.get(reverse("home")).content.decode()


def test_allauths_pages_say_du(client, german):
    client.cookies["django_language"] = "de"
    for name in ("account_login", "account_signup", "account_reset_password"):
        body = client.get(reverse(name)).content.decode()
        assert _polite(body) == [], name
    login = client.get(reverse("account_login")).content.decode()
    assert "Anmeldung" not in login
    assert "Anmelden" in login
