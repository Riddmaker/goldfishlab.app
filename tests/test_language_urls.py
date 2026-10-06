"""P10: the public pages carry their language in the address.

* /pricing/ is English, /de/pricing/ German, for everybody: a search engine,
  a link, a signed-in person who picked another language.
* Each version names all of them in hreflang links, the English address also
  as `x-default`, and the sitemap lists every version with the same links.
* The English address sends a person whose language is another one (account,
  cookie, browser) on to theirs; a search engine sends no Accept-Language and
  stays. Only GET and HEAD are sent on.
* The app keeps its one address and picks the language as before (Q1).
* A shared report's link names no language; a reader is sent to theirs.
"""

import re
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import translation

from core import seo
from sharing import services as sharing_services
from tests.test_sharing import deck, owner, run  # noqa: F401

pytestmark = pytest.mark.django_db

User = get_user_model()
SITE = "https://goldfishlab.app"
THREE = [("en", "English"), ("de", "Deutsch"), ("fr", "Français")]
URL = re.compile(r"<url>(.*?)</url>", re.S)
LOC = re.compile(r"<loc>([^<]*)</loc>")
XHTML = re.compile(r'<xhtml:link rel="alternate" hreflang="([^"]+)" href="([^"]+)"/>')


@pytest.fixture(autouse=True)
def three(settings):
    settings.LANGUAGES = THREE
    settings.SITE_URL = SITE


def address(name, language, *args):
    """The address of a page in `language`, without a request around it."""
    with translation.override(language):
        return reverse(name, args=args)


class _Alternates(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.canonical = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "alternate":
            self.found.append((attrs["hreflang"], attrs["href"]))
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs["href"]


def _alternates(response) -> _Alternates:
    parsed = _Alternates()
    parsed.feed(response.content.decode())
    return parsed


# --- one address per language -------------------------------------------------


@pytest.mark.parametrize("name", seo.PUBLIC_PAGES)
def test_every_public_page_has_an_address_in_each_language(client, name):
    english, german, french = (address(name, code) for code in ("en", "de", "fr"))

    response = client.get(german)

    assert german == f"/de{english}"
    assert response.status_code == 200
    assert response.headers["Content-Language"] == "de"
    assert '<html lang="de">' in response.content.decode()
    links = _alternates(response)
    assert links.canonical == SITE + german
    assert links.found == [("en", SITE + english), ("de", SITE + german),
                           ("fr", SITE + french), ("x-default", SITE + english)]


def test_the_english_address_is_english_for_a_search_engine(client):
    response = client.get("/pricing/")

    assert response.status_code == 200
    assert response.headers["Content-Language"] == "en"
    assert "Accept-Language" in response.headers["Vary"]
    links = _alternates(response)
    assert links.canonical == f"{SITE}/pricing/"
    assert ("x-default", f"{SITE}/pricing/") in links.found


def test_a_language_that_is_off_has_no_address(client):
    assert client.get("/ja/pricing/").status_code == 404


def test_with_english_alone_nothing_changes(client, settings):
    settings.LANGUAGES = THREE[:1]

    response = client.get("/pricing/", headers={"Accept-Language": "de"})

    assert response.status_code == 200
    assert _alternates(response).found == []
    assert client.get("/de/pricing/").status_code == 404


def test_a_private_page_names_no_other_language(client, owner):  # noqa: F811
    client.force_login(owner)

    response = client.get(reverse("decks:list"))

    assert _alternates(response).found == []


def test_an_unknown_share_link_names_no_other_language(client):
    response = client.get("/de/r/nosuchtoken/")

    assert response.status_code == 404
    assert _alternates(response).found == []


# --- who is sent where ---------------------------------------------------------


def test_the_cookie_sends_a_reader_on_with_the_query(client):
    client.cookies["django_language"] = "de"

    response = client.get("/pricing/?ref=discord")

    assert response.status_code == 302
    assert response.url == "/de/pricing/?ref=discord"
    assert {"Accept-Language", "Cookie"} <= {
        part.strip() for part in response.headers["Vary"].split(",")}


def test_the_browser_sends_a_reader_on(client):
    response = client.get("/", headers={"Accept-Language": "fr-CH,fr;q=0.9"})

    assert response.status_code == 302 and response.url == "/fr/"


def test_an_english_reader_stays(client):
    client.cookies["django_language"] = "en"

    response = client.get("/pricing/", headers={"Accept-Language": "de"})

    assert response.status_code == 200


def test_the_account_beats_the_cookie(client, owner):  # noqa: F811
    owner.language = "fr"
    owner.save(update_fields=["language"])
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    assert client.get("/pricing/").url == "/fr/pricing/"


def test_the_address_beats_the_account_and_the_cookie(client, owner):  # noqa: F811
    owner.language = "fr"
    owner.save(update_fields=["language"])
    client.force_login(owner)
    client.cookies["django_language"] = "fr"

    response = client.get("/de/about/methodology/")  # /pricing/ sends a member to /billing/

    assert response.status_code == 200
    assert response.headers["Content-Language"] == "de"


def test_only_a_safe_request_is_sent_on(client):
    client.cookies["django_language"] = "de"

    assert client.head("/pricing/").status_code == 302
    # Answered where it was asked; the pricing page takes no POST.
    assert client.post("/pricing/").status_code == 405


def test_an_address_without_its_slash_still_arrives(client):
    client.cookies["django_language"] = "de"

    response = client.get("/pricing", follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1] == ("/de/pricing/", 302)


def test_the_app_keeps_one_address_in_the_cookies_language(client):
    client.cookies["django_language"] = "de"

    response = client.get("/accounts/login/")

    assert response.status_code == 200
    assert response.headers["Content-Language"] == "de"
    assert client.get("/de/accounts/login/").status_code == 404


def test_a_guest_starts_on_the_try_page_in_their_language(client):
    client.cookies["django_language"] = "de"

    response = client.get("/try/", follow=True)

    assert response.redirect_chain == [("/de/try/", 302)]
    assert response.status_code == 200
    assert '<html lang="de">' in response.content.decode()


# --- the switcher ----------------------------------------------------------------


@pytest.mark.parametrize(("cookie", "page", "picked", "lands"), [
    ("en", "/pricing/", "de", "/de/pricing/"),
    ("de", "/de/pricing/", "en", "/pricing/"),
    # A French address read with a German cookie: still the same page.
    ("de", "/fr/about/methodology/", "de", "/de/about/methodology/"),
    ("fr", "/fr/terms/", "de", "/de/terms/"),
    # The app has one address in every language.
    ("de", "/decks/", "fr", "/decks/"),
])
def test_switching_lands_on_the_same_page_in_the_new_language(client, cookie, page, picked,
                                                               lands):
    client.cookies["django_language"] = cookie

    response = client.post(reverse("set_language"), {"language": picked, "next": page})

    assert response.status_code == 302 and response.url == lands
    assert response.cookies["django_language"].value == picked


# --- the sitemap -------------------------------------------------------------------


def test_the_sitemap_lists_every_language_with_its_links(client):
    body = client.get("/sitemap.xml").content.decode()
    entries = URL.findall(body)

    expected = [SITE + address(name, code)
                for name in seo.PUBLIC_PAGES for code, _ in THREE]
    assert [LOC.search(entry)[1] for entry in entries] == expected
    for entry in entries:
        links = XHTML.findall(entry)
        assert [code for code, _ in links] == ["en", "de", "fr", "x-default"]
        assert links[-1][1] == links[0][1]  # x-default is the English address
        assert "/en/" not in links[0][1]


# --- a shared report ----------------------------------------------------------------


def test_a_share_link_names_no_language_whoever_shares_it(run):  # noqa: F811
    with translation.override("de"):
        shared = sharing_services.share(run)

        assert shared.get_absolute_url() == f"/r/{shared.token}/"


def test_a_reader_of_a_share_link_gets_it_in_their_language(client, run):  # noqa: F811
    shared = sharing_services.share(run)
    link = shared.get_absolute_url()
    client.cookies["django_language"] = "fr"

    response = client.get(link, follow=True)

    assert response.redirect_chain == [(f"/fr{link}", 302)]
    assert response.headers["Content-Language"] == "fr"
    found = _alternates(response).found
    assert ("x-default", SITE + link) in found
    assert ("de", f"{SITE}/de{link}") in found
    # The link to pass on, on the page itself, is still the one without a language.
    assert f"{SITE}{link}" in response.content.decode()
