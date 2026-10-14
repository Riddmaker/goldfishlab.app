"""One address for every page (core.hosts, search console 2026-10-12).

* A GET or HEAD on www. or the hoster's domain is a 301 to the same path and
  query on SITE_URL, in one hop from plain http as well.
* A POST is answered where it was sent; /healthz/ always answers.
* Nothing moves without SITE_URL, or with the redirect switched off.
* The changelog feed says noindex in its header.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

SITE = "https://goldfishlab.app"


@pytest.fixture
def redirecting(settings):
    settings.SITE_URL = SITE
    settings.SITE_URL_REDIRECT = True
    settings.ALLOWED_HOSTS = ["goldfishlab.app", "www.goldfishlab.app", "env.jcloud.example"]


@pytest.mark.parametrize("host", ["www.goldfishlab.app", "env.jcloud.example"])
def test_another_host_is_sent_to_the_site_with_path_and_query(client, redirecting, host):
    response = client.get("/data/commander-precons/?sort=power", HTTP_HOST=host, secure=True)

    assert response.status_code == 301
    assert response["Location"] == f"{SITE}/data/commander-precons/?sort=power"


def test_plain_http_on_www_reaches_the_site_in_one_hop(client, redirecting):
    response = client.get("/pricing/", HTTP_HOST="www.goldfishlab.app")

    assert response.status_code == 301
    assert response["Location"] == f"{SITE}/pricing/"


def test_head_is_sent_on_too(client, redirecting):
    response = client.head("/", HTTP_HOST="www.goldfishlab.app", secure=True)

    assert response.status_code == 301


def test_the_site_itself_is_answered(client, redirecting):
    response = client.get(reverse("home"), HTTP_HOST="goldfishlab.app", secure=True)

    assert response.status_code == 200


def test_a_post_is_answered_where_it_was_sent(client, redirecting):
    response = client.post(reverse("set_language"), {"language": "en"},
                           HTTP_HOST="www.goldfishlab.app", secure=True)

    assert response.status_code != 301
    assert not response.get("Location", "").startswith(SITE)


def test_healthz_answers_on_any_host(client, redirecting):
    response = client.get(reverse("healthz"), HTTP_HOST="env.jcloud.example", secure=True)

    assert response.status_code == 200


def test_a_host_not_allowed_is_still_refused(client, redirecting):
    response = client.get("/", HTTP_HOST="evil.example", secure=True)

    assert response.status_code == 400


@pytest.mark.parametrize(("site_url", "switch"), [("", True), (SITE, False)])
def test_nothing_moves_without_site_url_or_with_the_switch_off(client, settings, site_url, switch):
    settings.SITE_URL = site_url
    settings.SITE_URL_REDIRECT = switch

    response = client.get(reverse("home"))

    assert response.status_code == 200


def test_production_switches_it_on():
    from goldfishlab.settings import prod  # noqa: PLC0415 - imported only for its value

    assert prod.SITE_URL_REDIRECT is True


def test_the_changelog_feed_says_noindex(client):
    response = client.get(reverse("changelog_feed"))

    assert response.status_code == 200
    assert response["X-Robots-Tag"] == "noindex"
