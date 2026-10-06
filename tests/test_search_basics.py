"""Search basics before the launch (P3).

* robots.txt welcomes every crawler and names the sitemap.
* The sitemap is exactly the public pages, and each answers a stranger.
* A public page names its one address (canonical, og:url); every other page
  says noindex, error pages included.
* Title and description reach the Open Graph tags escaped once.
* /pricing/ shows the tiers to anybody, from the same rows as /billing/.
"""

import re
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.checks import Warning as CheckWarning
from django.template import Context, Template, TemplateSyntaxError
from django.urls import reverse

from billing.models import Plan
from core import checks, seo

pytestmark = pytest.mark.django_db

User = get_user_model()
SITE = "https://goldfishlab.app"
LOC = re.compile(r"<loc>([^<]*)</loc>")


class _Head(HTMLParser):
    """The <meta> and <link> tags of a page, and its <title>."""

    def __init__(self):
        super().__init__()
        self.meta, self.links, self.title, self._in_title = {}, {}, "", False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            name = attrs.get("property") or attrs.get("name")
            if name:
                self.meta[name] = attrs.get("content")
        elif tag == "link" and attrs.get("rel"):
            self.links[attrs["rel"]] = attrs.get("href")
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _head(response) -> _Head:
    parsed = _Head()
    parsed.feed(response.content.decode())
    return parsed


@pytest.fixture
def site(settings):
    settings.SITE_URL = SITE


@pytest.fixture
def no_site(settings):
    settings.SITE_URL = ""


@pytest.fixture
def guest():
    user = User.objects.create_user(email="guest@example.invalid", password=None)
    user.is_guest = True
    user.save()
    return user


@pytest.fixture
def member():
    return User.objects.create_user(email="member@example.com", password="pw-test-1234")


# --- robots.txt and the sitemap -------------------------------------------------


def test_robots_welcomes_crawlers_and_names_the_sitemap(client, site):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    assert "User-agent: *" in text
    assert f"Sitemap: {SITE}/sitemap.xml" in text
    assert "Disallow: /\n" not in text, "nothing blocks the whole site"
    for private in ("/decks/", "/runs/", "/accounts/"):
        assert f"Disallow: {private}" not in text, "private pages say noindex instead"


def test_the_sitemap_is_the_public_pages_on_the_one_address(client, site):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert "http://www.sitemaps.org/schemas/sitemap/0.9" in response.content.decode()
    locations = LOC.findall(response.content.decode())
    assert locations == [SITE + reverse(name) for name in seo.PUBLIC_PAGES]


def test_without_a_site_url_the_sitemap_uses_the_request(client, no_site):
    locations = LOC.findall(client.get("/sitemap.xml").content.decode())

    assert locations[0] == "http://testserver/"


@pytest.mark.parametrize("name", seo.PUBLIC_PAGES)
def test_every_public_page_answers_a_stranger_and_may_be_indexed(client, site, name):
    response = client.get(reverse(name))

    assert response.status_code == 200
    head = _head(response)
    assert head.links["canonical"] == SITE + reverse(name)
    assert head.meta["og:url"] == SITE + reverse(name)
    assert "robots" not in head.meta


# --- the head of a page -----------------------------------------------------------


def test_the_canonical_address_drops_the_query(client, site):
    head = _head(client.get("/pricing/?utm_source=reddit&ref=x"))

    assert head.links["canonical"] == f"{SITE}/pricing/"


def test_without_a_site_url_the_request_host_is_used(client, no_site):
    head = _head(client.get("/"))

    assert head.links["canonical"] == "http://testserver/"
    assert head.meta["og:image"].startswith("http://testserver/static/img/og-default")


def test_a_private_page_says_noindex(client, member, site):
    client.force_login(member)

    head = _head(client.get(reverse("decks:list")))

    assert head.meta["robots"] == "noindex"
    assert "canonical" not in head.links


@pytest.mark.parametrize("path", ["/accounts/login/", "/accounts/signup/", "/no-such-page/"])
def test_sign_in_screens_and_error_pages_say_noindex(client, path):
    head = _head(client.get(path))

    assert head.meta["robots"] == "noindex"


def test_a_page_shares_its_title_description_and_image(client, site):
    head = _head(client.get(reverse("methodology")))

    assert head.meta["og:title"] == head.title == "Methodology — Goldfish Lab"
    assert head.meta["og:description"] == head.meta["description"]
    assert head.meta["og:description"].startswith("Exactly what Goldfish Lab simulates")
    assert head.meta["og:image"].startswith(f"{SITE}/static/img/og-default")
    assert head.meta["og:locale"] == "en_US"
    assert head.meta["twitter:card"] == "summary_large_image"


def test_og_locale_follows_the_language(client, settings):
    settings.LANGUAGES = [("en", "English"), ("pt-br", "Português (Brasil)")]

    head = _head(client.get("/", HTTP_ACCEPT_LANGUAGE="pt-br"))

    assert head.meta["og:locale"] == "pt_BR"


def test_every_language_has_an_og_locale(settings):
    assert set(seo.OG_LOCALES) == set(settings.LANGUAGE_NAMES)


# --- {% capture %} ----------------------------------------------------------------


def test_capture_escapes_once_and_strips():
    rendered = Template(
        "{% load capture %}{% capture as t %}\n  {{ value }}\n{% endcapture %}"
        '<title>{{ t }}</title><meta content="{{ t }}">'
    ).render(Context({"value": 'Tom & "Jerry"'}))

    escaped = "Tom &amp; &quot;Jerry&quot;"
    assert rendered == f'<title>{escaped}</title><meta content="{escaped}">'


def test_capture_needs_a_name():
    with pytest.raises(TemplateSyntaxError):
        Template("{% load capture %}{% capture %}x{% endcapture %}")


# --- SITE_URL in production ----------------------------------------------------


def test_production_without_a_site_url_is_warned(settings):
    settings.DEBUG = False
    settings.SITE_URL = ""

    found = checks.check_site_url_is_set(None)

    assert [type(w) for w in found] == [CheckWarning]
    assert found[0].id == "core.W001"


def test_production_with_a_site_url_is_not_warned(settings):
    settings.DEBUG = False
    settings.SITE_URL = SITE

    assert checks.check_site_url_is_set(None) == []


# --- /pricing/ ------------------------------------------------------------------


def _tier_names(response) -> list[str]:
    return [plan.display_name for plan in response.context["plans"]]


def test_pricing_shows_only_the_free_plan_until_a_paid_one_can_be_bought(client, settings):
    settings.STRIPE_SECRET_KEY = ""

    response = client.get(reverse("pricing"))

    assert response.status_code == 200
    assert [plan.slug for plan in response.context["plans"]] == [
        Plan.objects.get(is_default=True).slug]
    for paid in Plan.objects.filter(is_default=False):
        assert paid.display_name not in response.content.decode()


def test_a_paid_plan_appears_as_on_the_plans_page_once_it_can_be_bought(
        client, member, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_not_a_real_key"
    paid = Plan.objects.filter(is_active=True, is_default=False).first()
    paid.stripe_price_id = "price_test"
    paid.save()

    public = client.get(reverse("pricing"))
    client.force_login(member)
    own = client.get(reverse("billing:plans"))

    shown = [plan.slug for plan in public.context["plans"]]
    assert shown == [Plan.objects.get(is_default=True).slug, paid.slug]
    assert set(shown) <= {plan.slug for plan in own.context["plans"]}
    page = public.content.decode()
    assert "Paid plans start soon." not in page
    assert "Create a free account, then choose it." in page
    assert reverse("billing:checkout", args=[paid.slug]) not in page, "no checkout for a visitor"


def test_pricing_says_paid_plans_start_soon_while_payments_are_off(client, settings):
    settings.STRIPE_SECRET_KEY = ""

    page = client.get(reverse("pricing")).content.decode()

    assert "Paid plans start soon." in page
    assert reverse("guests:try") in page


def test_a_guest_sees_pricing_with_its_own_way_on(client, guest):
    client.force_login(guest)

    response = client.get(reverse("pricing"))

    assert response.status_code == 200
    assert "Back to your deck" in response.content.decode()


def test_a_member_is_sent_to_their_own_plans_page(client, member):
    client.force_login(member)

    response = client.get(reverse("pricing"))

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:plans")


def test_the_footer_and_the_terms_link_the_public_pricing(client):
    assert reverse("pricing").encode() in client.get(reverse("home")).content
    terms = client.get(reverse("terms")).content.decode()
    assert reverse("pricing") in terms
    assert reverse("billing:plans") not in terms
