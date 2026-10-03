"""Phase 12 E: French ("vous") and Italian ("tu"), with the real catalogues.

As `test_i18n_german`, per language: the catalogue is complete, the allauth
overrides it needs are there, and the main pages and allauth's pages render in
it without the other tone.
"""

import re

import polib
import pytest
from django.urls import reverse
from django.utils.html import escape

from accounts import allauth_tone
from tests.test_i18n import LOCALE, deck, finished_run, owner  # noqa: F401

pytestmark = pytest.mark.django_db

#: The tone each language must not take: "tu" in French, "Lei" in Italian.
WRONG_TONE = {
    "fr": re.compile(r"\b(tu|ton|ta|tes|toi)\b", re.IGNORECASE),
    "it": re.compile(r"\b(Lei|Suo|Sua|Suoi|vostro|vostra|La preghiamo|inserisca|utilizzi)\b"),
}
#: allauth strings whose own translation is wrong for us, per language.
OVERRIDES = {
    "fr": ("Sign In", "Use a security key", "Use authenticator app or code", "Use your password"),
    "it": ("Please reauthenticate to safeguard your account.",),
}
NAMES = {"fr": "Français", "it": "Italiano"}


def _catalogue(lang):
    return polib.pofile(str(LOCALE / lang / "LC_MESSAGES" / "django.po"))


@pytest.mark.parametrize("lang", WRONG_TONE)
def test_every_string_has_a_translation(lang):
    # The allauth overrides a language does not need stay empty: allauth's text shows.
    untranslated = [entry.msgid for entry in _catalogue(lang)
                    if not entry.translated() and not entry.obsolete
                    and entry.msgid not in allauth_tone.OVERRIDDEN]
    assert untranslated == []


@pytest.mark.parametrize("lang", WRONG_TONE)
def test_the_allauth_overrides_are_translated_in_the_right_tone(lang):
    translated = {entry.msgid: entry.msgstr for entry in _catalogue(lang)}
    for msgid in OVERRIDES[lang]:
        assert msgid in allauth_tone.OVERRIDDEN, msgid
        assert translated.get(msgid), msgid
        assert not WRONG_TONE[lang].search(translated[msgid]), msgid


def test_the_checks_find_the_wrong_tone():
    assert WRONG_TONE["fr"].search("Entre ton mot de passe")
    assert not WRONG_TONE["fr"].search("Saisissez votre mot de passe")
    assert WRONG_TONE["it"].search("Inserisca la Sua password")
    assert not WRONG_TONE["it"].search("Inserisci la tua password")


@pytest.fixture(params=list(WRONG_TONE))
def lang(request, settings, client):
    settings.LANGUAGES = [("en", "English"), (request.param, NAMES[request.param])]
    client.cookies["django_language"] = request.param
    return request.param


def _wrong_tone(lang, body):
    return [match.group(0) for match in WRONG_TONE[lang].finditer(body)]


def test_the_main_pages(client, owner, deck, finished_run, lang):  # noqa: F811
    client.force_login(owner)
    for url in (reverse("home"), reverse("decks:list"), reverse("decks:detail", args=[deck.pk]),
                reverse("simulations:detail", args=[finished_run.pk]),
                reverse("billing:plans"), reverse("decks:import"), reverse("accounts:data")):
        body = client.get(url).content.decode()
        assert f'<html lang="{lang}">' in body, url
        assert _wrong_tone(lang, body) == [], url
    heading = _catalogue(lang).find("Know your deck before game night.").msgstr
    assert escape(heading) in client.get(reverse("home")).content.decode()


def test_allauths_pages(client, lang):
    for name in ("account_login", "account_signup", "account_reset_password"):
        body = client.get(reverse(name)).content.decode()
        assert f'<html lang="{lang}">' in body, name
        assert _wrong_tone(lang, body) == [], name
