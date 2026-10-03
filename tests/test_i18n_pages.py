"""Phase 12 B: the pages, rendered in a language whose every word is marked.

`test_i18n_templates` reads the templates; it cannot see a sentence built in
Python and handed over as a variable. This test can. It writes a pseudo
catalogue - every translation is its English text in «guillemets» - renders the
main pages in it and reads them as a browser shows them. Every word left
outside «» never went through a catalogue: it would stay English on a German
page.

What may stay outside: names (cards, the deck, the product, the services), an
element with its own `lang` (the Fan Content notice, the game log, an engine's
or a service's own words), numbers and mana symbols.
"""

import re

import polib
import pytest
from django.urls import reverse
from django.utils import translation
from django.utils.translation import trans_real

from cards.models import OracleCard
from decks import services as deck_services
from playtest import services as playtest_services
from simulations.models import SimulationRun
from tests.test_i18n import GERMAN, LOCALE, deck, finished_run, owner  # noqa: F401
from tests.test_i18n_templates import NAMES, WORD, _Reader

pytestmark = pytest.mark.django_db

#: A placeholder or a tag stays outside the marks, as a translator keeps it.
_KEPT = re.compile(r"(%\(\w+\)[sdif]|%[sdif]|\{\w+\}|<[^>]+>)")
_MARKED = re.compile(r"«[^«»]*»")


def pseudo(text: str) -> str:
    """"Turn %(turn)s" -> "«Turn »%(turn)s"."""
    return "".join(part if _KEPT.fullmatch(part) or not part.strip() else f"«{part}»"
                   for part in _KEPT.split(text))


@pytest.fixture
def marked(settings, tmp_path):
    """German, with the pseudo catalogue in place of the real one."""
    catalogue = polib.pofile(str(LOCALE / "de" / "LC_MESSAGES" / "django.po"))
    for entry in catalogue:
        if entry.msgid_plural:
            entry.msgstr_plural = {0: pseudo(entry.msgid), 1: pseudo(entry.msgid_plural)}
        else:
            entry.msgstr = pseudo(entry.msgid)
    target = tmp_path / "de" / "LC_MESSAGES"
    target.mkdir(parents=True)
    catalogue.save_as_mofile(str(target / "django.mo"))
    settings.LOCALE_PATHS = [tmp_path]
    settings.LANGUAGES = GERMAN
    # Django keeps every catalogue it has loaded for the life of the process.
    trans_real._translations = {}
    trans_real._default = None
    yield
    trans_real._translations = {}
    trans_real._default = None
    translation.activate("en")


class _Page(_Reader):
    """The template checker's reader, for a rendered page."""

    def __init__(self, allowed):
        super().__init__()
        self.allowed = sorted(allowed, key=len, reverse=True)

    def _check(self, text):
        text = " ".join(text.split())
        words = _MARKED.sub(" ", text)
        for name in self.allowed:
            words = words.replace(name, " ")
        if WORD.search(words):
            self.found.append(text)


def english_on(body: str, allowed) -> list[str]:
    page = _Page(allowed)
    page.feed(body)
    page.close()
    return page.found


def test_the_check_finds_a_word_that_was_never_marked():
    """Guard against a check that can never fail."""
    assert english_on("<p>«Turn» 3</p><p>Lands</p>", set()) == ["Lands"]
    assert english_on('<p>«Turn» 3 — Sol Ring</p><p lang="en">Drew a card</p>',
                      {"Sol Ring"}) == []
    assert pseudo("Turn %(turn)s of <b>%(total)s</b>") == "«Turn »%(turn)s« of »<b>%(total)s</b>"


@pytest.fixture
def pages(client, owner, deck, finished_run, marked):  # noqa: F811
    """Every main page, signed in, in the pseudo language."""
    unresolved = deck_services.import_deck(
        owner=owner, raw=b"1 Sol Ring\n1 Nosuch Cardname\n", name="Lost", filename="list.txt",
    ).record
    running = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=4,
                                           seed=8)
    session = playtest_services.start(deck, owner, seed=3, on_the_play=True)
    card = deck.entries.select_related("oracle_card").first().oracle_card

    client.force_login(owner)
    client.cookies["django_language"] = "de"
    urls = {
        "home": reverse("home"),
        "decks": reverse("decks:list"),
        "deck": reverse("decks:detail", args=[deck.pk]),
        "report": reverse("simulations:detail", args=[finished_run.pk]),
        "running": reverse("simulations:detail", args=[running.pk]),
        "annotate": reverse("simulations:annotate", args=[deck.pk, card.pk]),
        "playtest": reverse("playtest:detail", args=[session.pk]),
        "plans": reverse("billing:plans"),
        "import": reverse("decks:import"),
        "review": reverse("decks:review", args=[unresolved.pk]),
        "data": reverse("accounts:data"),
        "delete": reverse("decks:delete", args=[deck.pk]),
    }
    rendered = {}
    for name, url in urls.items():
        response = client.get(url)
        assert response.status_code == 200, name
        rendered[name] = response.content.decode()
    return rendered


def _allowed(owner):  # noqa: F811
    names = {name for pair in OracleCard.objects.values_list("name", "front_name")
             for name in pair if name}
    return NAMES | names | {"Chainer", "Lost", "list.txt", "sample.csv", "Nosuch Cardname",
                            owner.email}


def test_no_page_shows_a_word_that_never_went_through_a_catalogue(pages, owner):  # noqa: F811
    allowed = _allowed(owner)
    found = {name: english_on(body, allowed) for name, body in pages.items()}
    assert {name: words for name, words in found.items() if words} == {}


def test_the_guest_trial_page_too(client, marked, catalogue):
    client.cookies["django_language"] = "de"
    body = client.get(reverse("guests:try")).content.decode()
    assert english_on(body, NAMES) == []


__all__ = ["english_on", "pseudo"]
