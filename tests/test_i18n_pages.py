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
from pathlib import Path

import allauth
import django
import polib
import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation
from django.utils.translation import trans_real
from django.views.defaults import server_error

from accounts import mail_samples
from cards.models import OracleCard
from decks import services as deck_services
from playtest import services as playtest_services
from sharing import services as sharing_services
from simulations import summary, tasks
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

    def __init__(self, allowed, foreign=frozenset()):
        super().__init__()
        self.allowed = sorted(allowed, key=len, reverse=True)
        self.foreign = foreign

    def _check(self, text):
        text = " ".join(text.split())
        words = _MARKED.sub(" ", text)
        for name in self.allowed:
            words = words.replace(name, " ")
        if any(word not in self.foreign for word in WORD.findall(words)):
            self.found.append(text)


def english_on(body: str, allowed, foreign=frozenset()) -> list[str]:
    """`foreign`: words that may stand outside the marks because they are not English."""
    page = _Page(allowed, foreign)
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
    # `finished_run` has no fingerprint, so its page shows why it cannot be
    # shared; these two show the offer and the shared panel (P4).
    shareable, shared_run = (_finished(owner, deck, seed) for seed in (11, 12))
    shared = sharing_services.share(shared_run)

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
        "share offer": reverse("simulations:detail", args=[shareable.pk]),
        "share panel": reverse("simulations:detail", args=[shared_run.pk]),
        "shared, owner": shared.get_absolute_url(),
    }
    rendered = {}
    for name, url in urls.items():
        # The public ones send a German reader on to /de/... (P10).
        response = client.get(url, follow=True)
        assert response.status_code == 200, name
        rendered[name] = response.content.decode()
    client.logout()
    client.cookies["django_language"] = "de"
    response = client.get(shared.get_absolute_url(), follow=True)
    assert response.status_code == 200
    rendered["shared, reader"] = response.content.decode()
    return rendered


def _finished(owner, deck, seed):  # noqa: F811
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=4,
                                       seed=seed, deck_print=summary.fingerprint(deck))
    tasks.finalize_run([tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)],
                       str(run.pk))
    run.refresh_from_db()
    return run


def _allowed(owner):  # noqa: F811
    names = {name for pair in OracleCard.objects.values_list("name", "front_name")
             for name in pair if name}
    # Koi and Kraken are plan names, the same in every language (P5).
    return NAMES | names | {"Koi", "Kraken", "Chainer", "Lost", "list.txt", "sample.csv",
                            "Nosuch Cardname", owner.email}


#: A share link, as the copy-as-text field carries it (P4): an address, not words.
_SHARE_LINK = re.compile(r"https?://testserver/r/[\w-]+/")


def test_no_page_shows_a_word_that_never_went_through_a_catalogue(pages, owner):  # noqa: F811
    links = {link for body in pages.values() for link in _SHARE_LINK.findall(body)}
    assert links, "the shared pages carry their link"
    allowed = _allowed(owner) | links
    found = {name: english_on(body, allowed) for name, body in pages.items()}
    assert {name: words for name, words in found.items() if words} == {}


def test_the_guest_trial_page_too(client, marked, catalogue):
    client.cookies["django_language"] = "de"
    body = client.get(reverse("guests:try")).content.decode()
    assert english_on(body, NAMES) == []


# --- phase 12 C: mails, allauth's pages, the error pages --------------------------


@pytest.fixture(scope="module")
def german():
    """Words of the German catalogues that come with Django and allauth.

    Their text arrives already German (a month name, allauth's login form, a
    password rule), not through our catalogue and so not in «». A word that is
    also in an English msgid is left out: it would let English through.
    """
    roots = (Path(django.__file__).parent, Path(allauth.__file__).parent)
    german, english = set(), set()
    for root in roots:
        for path in root.rglob("locale/de/LC_MESSAGES/django.po"):
            for entry in polib.pofile(str(path)):
                text = " ".join([entry.msgstr, *entry.msgstr_plural.values()])
                german.update(WORD.findall(_KEPT.sub(" ", text)))
                english.update(WORD.findall(_KEPT.sub(" ", f"{entry.msgid} {entry.msgid_plural}")))
    return frozenset(german - english)


#: What a mail carries that is not text: links, addresses, the sample's browser.
_URL = re.compile(r"(?:https?://|mailto:)[^\s«»\"<]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CONTACT = "hello@mail.test"


def _mail_text(text: str) -> str:
    text = _URL.sub(" ", text)
    for value in (mail_samples.CODE, "203.0.113.7", "testserver",
                  "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) Safari/605.1"):
        text = text.replace(value, " ")
    return text


@pytest.mark.parametrize("prefix", list(mail_samples.SAMPLES))
def test_every_mail_in_the_visitors_language(prefix, marked, settings, german):
    """Mails go out inside the request, in the language that request has."""
    settings.LEGAL_CONTACT_EMAIL = CONTACT
    with translation.override("de"):
        msg = mail_samples.render(prefix, RequestFactory().get("/"))
    html = msg.alternatives[0][0]
    for text in (msg.subject, msg.body, html):
        assert english_on(_mail_text(text), NAMES, german) == []
    assert '<html lang="de">' in html


def test_the_account_pages(client, marked, german):
    """Ours in «», allauth's own from its catalogue (its tone is batch D)."""
    client.cookies["django_language"] = "de"
    pages = {
        "signup": reverse("account_signup"),
        "sent": reverse("account_email_verification_sent"),
        "bad link": reverse("account_confirm_email", args=["made-up"]),
        "login": reverse("account_login"),
    }
    found = {}
    for name, url in pages.items():
        body = client.get(url).content.decode()
        assert '<html lang="de">' in body
        found[name] = english_on(body, NAMES, german)
    assert {name: words for name, words in found.items() if words} == {}


def test_the_error_pages(client, marked, rf):
    client.cookies["django_language"] = "de"
    response = client.get("/no-such-page/")
    assert response.status_code == 404
    assert english_on(response.content.decode(), NAMES) == []
    with translation.override("de"):
        for page in ("403.html", "429.html"):
            body = render_to_string(page, request=rf.get("/"))
            assert english_on(body, NAMES) == [], page
        # Django renders the 500 with no request at all.
        body = server_error(rf.get("/")).content.decode()
    assert '<html lang="de">' in body
    assert english_on(body, NAMES) == []


__all__ = ["english_on", "pseudo"]
