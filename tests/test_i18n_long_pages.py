"""Phase 12 H: the methodology page translated, the legal pages English (Q4 b).

The methodology is long prose with literal percent signs (`blocktranslate`
writes them "%%"): one translator's single "%" is a 500 for that language
alone, so the page is rendered in every language. The legal pages keep their
English body, marked `lang="en"`, under one translated line; English readers
do not see that line.
"""

import pytest
from django.urls import reverse
from django.utils.html import escape

from decks.analysis import KARSTEN_MAX, KARSTEN_MIN
from tests.test_i18n_languages import WRONG_TONE, _catalogue, lang  # noqa: F401

pytestmark = pytest.mark.django_db

LEGAL = ("privacy", "terms", "imprint")
NOTE = "This page is only in English for now."


def test_the_methodology_in_every_language(client, lang):  # noqa: F811
    response = client.get(reverse("methodology"))
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
def test_a_legal_page_says_it_is_english_in_the_readers_language(client, lang, name):  # noqa: F811
    body = client.get(reverse(name)).content.decode()

    assert f'<html lang="{lang}">' in body
    # A literal's translation is marked safe: never escaped, as the markup test knows.
    assert _catalogue(lang).find(NOTE).msgstr in body
    assert '<section class="max-w-3xl" lang="en">' in body


@pytest.mark.parametrize("name", LEGAL)
def test_an_english_reader_sees_no_note(client, name):
    body = client.get(reverse(name)).content.decode()

    assert NOTE not in body
    assert 'role="note"' not in body
