"""Phase 12 E and F: every language but German, with the real catalogues.

As `test_i18n_german`, per language: the catalogue is complete, the allauth
overrides it needs are there, and the main pages and allauth's pages render in
it without the other tone. French says "vous", Italian "tu", Spanish "tú",
Portuguese "você", Japanese is polite (Q8). Production switches languages on
with LANGUAGES_ON; the tests switch each on for themselves.
"""

import datetime
import re

import polib
import pytest
from django.conf import settings as django_settings
from django.urls import reverse
from django.utils import translation
from django.utils.formats import date_format
from django.utils.html import escape
from django.utils.translation import to_locale

from accounts import allauth_tone
from core import month_names
from simulations.annotations import NOTHING
from tests.test_i18n import LOCALE, deck, finished_run, owner  # noqa: F401

pytestmark = pytest.mark.django_db

#: The tone each language must not take.
WRONG_TONE = {
    "fr": re.compile(r"\b(tu|ton|ta|tes|toi)\b", re.IGNORECASE),
    "it": re.compile(r"\b(Lei|Suo|Sua|Suoi|vostro|vostra|La preghiamo|inserisca|utilizzi)\b"),
    "es": re.compile(r"\b(usted|ustedes|Introduzca|Ingrese|Inténtelo|Utilice|Verifique|Escriba|"
                     r"Seleccione|Haga|Solicite|Póngase|Contáctenos|desea)\b", re.IGNORECASE),
    "pt-br": re.compile(r"\b(tu|teu|tua|teus|tuas|contigo|vós|vosso|vossa)\b", re.IGNORECASE),
    # The plain style (だ・である) and bare imperatives.
    "ja": re.compile(r"である|だ。|しろ|せよ"),
}


def _others(lang):
    """Another language's overrides of allauth's or Django's text: empty in `lang`."""
    mine = set(allauth_tone.OVERRIDES.get(lang, ())) | set(month_names.OVERRIDES.get(lang, ()))
    return (allauth_tone.OVERRIDDEN | month_names.OVERRIDDEN) - mine


def _catalogue(lang):
    return polib.pofile(str(LOCALE / to_locale(lang) / "LC_MESSAGES" / "django.po"))


@pytest.mark.parametrize("lang", WRONG_TONE)
def test_every_string_has_a_translation(lang):
    # Another language's allauth overrides stay empty: allauth's own text shows.
    others = _others(lang)
    untranslated = [entry.msgid for entry in _catalogue(lang)
                    if not entry.translated() and not entry.obsolete
                    and entry.msgid not in others]
    assert untranslated == []


@pytest.mark.parametrize("lang", WRONG_TONE)
def test_the_allauth_overrides_are_translated_in_the_right_tone(lang):
    translated = {entry.msgid: entry.msgstr for entry in _catalogue(lang)}
    for msgid in allauth_tone.OVERRIDES.get(lang, ()):
        assert translated.get(msgid), msgid
        assert not WRONG_TONE[lang].search(translated[msgid]), msgid


@pytest.mark.parametrize("lang", [code for code in django_settings.LANGUAGE_NAMES if code != "en"])
def test_no_catalogue_overrides_another_languages_allauth_or_django_strings(lang):
    # Filled, it would replace allauth's or Django's own (right) text in this language.
    others = _others(lang)
    filled = [entry.msgid for entry in _catalogue(lang)
              if entry.msgid in others and entry.translated()]
    assert filled == []


@pytest.mark.parametrize("lang", [code for code in django_settings.LANGUAGE_NAMES if code != "en"])
def test_the_mana_box_help_keeps_the_word_it_reads(lang):
    # The box reads only the English word (`NOTHING`); French once said « rien ».
    help_text = _catalogue(lang).find(
        "Letters for the colours, with a number where it makes more than one: B, 2B, B C. "
        "Write “nothing” for a card that makes no mana the engine can use.")
    assert NOTHING in help_text.msgstr


def test_the_checks_find_the_wrong_tone():
    assert WRONG_TONE["fr"].search("Entre ton mot de passe")
    assert not WRONG_TONE["fr"].search("Saisissez votre mot de passe")
    assert WRONG_TONE["it"].search("Inserisca la Sua password")
    assert not WRONG_TONE["it"].search("Inserisci la tua password")
    assert WRONG_TONE["es"].search("Introduzca su contraseña")
    assert not WRONG_TONE["es"].search("Introduce tu contraseña")
    assert WRONG_TONE["pt-br"].search("Digite a tua senha")
    assert not WRONG_TONE["pt-br"].search("Digite sua senha")
    assert WRONG_TONE["ja"].search("パスワードを入力しろ")
    assert not WRONG_TONE["ja"].search("パスワードを入力してください。")


@pytest.fixture(params=list(WRONG_TONE))
def lang(request, settings, client):
    name = settings.LANGUAGE_NAMES[request.param]
    settings.LANGUAGES = [("en", "English"), (request.param, name)]
    client.cookies["django_language"] = request.param
    return request.param


def _wrong_tone(lang, body):
    return [match.group(0) for match in WRONG_TONE[lang].finditer(body)]


def test_the_main_pages(client, owner, deck, finished_run, lang):  # noqa: F811
    client.force_login(owner)
    for url in (reverse("home"), reverse("decks:list"), reverse("decks:detail", args=[deck.pk]),
                reverse("simulations:detail", args=[finished_run.pk]),
                reverse("billing:plans"), reverse("decks:import"), reverse("accounts:data")):
        body = client.get(url, follow=True).content.decode()
        assert f'<html lang="{lang}">' in body, url
        assert _wrong_tone(lang, body) == [], url
    heading = _catalogue(lang).find("Know your deck before game night.").msgstr
    assert escape(heading) in client.get(reverse("home"), follow=True).content.decode()


def test_allauths_pages(client, lang):
    for name in ("account_login", "account_signup", "account_reset_password"):
        body = client.get(reverse(name), follow=True).content.decode()
        assert f'<html lang="{lang}">' in body, name
        assert _wrong_tone(lang, body) == [], name


@pytest.mark.parametrize(("lang", "expected"), [
    ("it", "4 ottobre 2026"),  # Django's own: "04 Ottobre 2026" (phase 12 J6)
    ("de", "4. Oktober 2026"),  # German keeps Django's capital month
    ("en", "4 October 2026"),
])
def test_a_date_reads_the_way_the_language_writes_it(lang, expected):
    with translation.override(lang):
        assert date_format(datetime.date(2026, 10, 4), "DATE_FORMAT") == expected
