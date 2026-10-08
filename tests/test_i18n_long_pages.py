"""Phase 12 H and I: the methodology and the legal pages in every language.

The methodology is long prose with literal percent signs (`blocktranslate`
writes them "%%"): one translator's single "%" is a 500 for that language
alone, so the page is rendered in every language. The legal pages are
translated too (I, Q4 changed to a by the user); only the English text binds,
and a translated note at the top says so, with a button back to English.
English readers do not see that note.
"""

import pytest
from django.urls import reverse
from django.utils.html import escape

from decks.analysis import KARSTEN_MAX, KARSTEN_MIN
from tests.test_i18n_languages import WRONG_TONE, _catalogue, lang  # noqa: F401

pytestmark = pytest.mark.django_db

LEGAL = {"privacy": "Privacy policy", "terms": "Terms of service", "imprint": "Legal notice"}
NOTE = ("This page was translated automatically from English. "
        "Only the English version is legally binding.")
BUTTON = "Read the English version"
PRECEDENCE = "if a translation differs from the English text, the English text applies."


def test_the_methodology_in_every_language(client, lang):  # noqa: F811
    response = client.get(reverse("methodology"), follow=True)
    body = response.content.decode()

    assert response.status_code == 200
    assert f'<html lang="{lang}">' in body
    heading = _catalogue(lang).find("How this works").msgstr
    assert f">{escape(heading)}</h1>" in body
    assert f"{KARSTEN_MIN}" in body and f"{KARSTEN_MAX}" in body
    # The sources' titles stay English, marked as such.
    assert 'lang="en"\n             href="https://edhrec.com/' in body
    assert not WRONG_TONE[lang].search(body)


@pytest.mark.parametrize("name", LEGAL)
def test_a_legal_page_in_every_language(client, lang, name):  # noqa: F811
    response = client.get(reverse(name), follow=True)
    body = response.content.decode()
    catalogue = _catalogue(lang)

    assert response.status_code == 200
    assert f'<html lang="{lang}">' in body
    # A literal's translation is marked safe: never escaped, as the markup test knows.
    assert f">{catalogue.find(LEGAL[name]).msgstr}</h1>" in body
    assert catalogue.find(NOTE).msgstr in body
    assert catalogue.find(BUTTON).msgstr in body
    # The text itself is no longer marked English; only the Fan Content notice is.
    assert '<section class="max-w-3xl">' in body
    assert '<section class="max-w-3xl" lang="en">' not in body
    assert not WRONG_TONE[lang].search(body)


@pytest.mark.parametrize("name", ["privacy", "terms"])
def test_the_date_is_written_the_readers_way(client, lang, name):  # noqa: F811
    body = client.get(reverse(name), follow=True).content.decode()

    assert "October" not in body
    assert "2026" in body


@pytest.mark.parametrize("name", LEGAL)
def test_the_button_goes_back_to_the_same_page_in_english(client, lang, name):  # noqa: F811
    url = reverse(name)

    response = client.post(reverse("set_language"), {"language": "en", "next": url})

    assert response.status_code == 302 and response["Location"] == url
    body = client.get(url, follow=True).content.decode()
    assert '<html lang="en">' in body
    assert NOTE not in body


@pytest.mark.parametrize("name", LEGAL)
def test_an_english_reader_sees_no_note(client, name):
    body = client.get(reverse(name), follow=True).content.decode()

    assert NOTE not in body
    assert 'role="note"' not in body


@pytest.mark.parametrize(("name", "updated"), [("privacy", "8 October 2026"),
                                               ("terms", "8 October 2026")])
def test_the_english_text_says_it_prevails(client, name, updated):
    body = client.get(reverse(name), follow=True).content.decode()

    assert PRECEDENCE in body
    assert f"Last updated {updated}." in body
