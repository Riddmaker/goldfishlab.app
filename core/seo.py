"""What search engines and link previews see (P3, search basics).

Until P3 a search engine found six pages and nothing telling it which address
was the real one, and a link pasted into Discord showed a bare URL. Now:

* **One list of public pages** (`PUBLIC_PAGES`). The sitemap is that list, and
  every page not on it says `noindex` - a run, a deck, the sign-in screen, an
  error page. A private page that somebody links in public stays out of the
  index without robots.txt having to name it (a page robots.txt blocks is
  never read, so its `noindex` would never be seen).
* **One address** (`SITE_URL`). Canonical links, Open Graph URLs and the
  sitemap name it, so a page reached on www. or on the hoster's own domain
  still points at goldfishlab.app. Blank, the request's own host is used:
  development and a fork that has not set it keep working.
* **A shared report** (P4) is indexable too, but not in the sitemap.
* **The data pages** (P11): the precon table is a public page, and each
  published precon is in the sitemap too (`datapages.sitemaps`).
* **Each language at its own address** (P10). A public page is at /pricing/
  in English and /de/pricing/ in German (`goldfishlab.urls`), and names every
  version of itself in hreflang links, its English address also as
  `x-default`; the sitemap lists every version with the same links. Each
  version's canonical link is itself. `og:locale` says which language a
  preview was rendered in.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.templatetags.static import static
from django.urls import reverse, translate_url
from django.utils.encoding import escape_uri_path
from django.utils.translation import get_language

#: The pages a search engine should find, by view name. A page added here is
#: in the sitemap and indexable; tests/test_search_basics.py checks that each
#: one answers a stranger.
PUBLIC_PAGES = (
    "home",
    "guests:try",
    "pricing",
    "methodology",
    "datapages:precons",
    "terms",
    "privacy",
    "imprint",
)

#: Pages that may be indexed but are not in the sitemap: a shared report (P4)
#: is public by its owner's choice, and a search engine should find it where
#: somebody posted its link - never by us listing every link there is.
INDEXABLE_UNLISTED = ("shared:report",)

#: Indexable and in the sitemap, but one page per row: every published precon
#: (P11, `datapages.sitemaps`).
INDEXABLE_ROWS = ("datapages:precon",)

#: Open Graph wants language_TERRITORY. The territory is the one most readers
#: of each language live in; Brazilian Portuguese names its own.
OG_LOCALES = {
    "en": "en_US",
    "de": "de_DE",
    "fr": "fr_FR",
    "it": "it_IT",
    "es": "es_ES",
    "pt-br": "pt_BR",
    "ja": "ja_JP",
}

#: The preview image every page shares until a report has its own (P4).
OG_IMAGE = "img/og-default.png"


def base_url(request) -> str:
    """`SITE_URL`, or the scheme and host this request came in on."""
    return settings.SITE_URL or f"{request.scheme}://{request.get_host()}"


def absolute(request, path: str) -> str:
    """`path` on the site's one address. A URL that is already absolute (a
    static file on another host) is returned as it is."""
    if urlsplit(path).scheme:
        return path
    return base_url(request) + escape_uri_path(path)


def is_public(request) -> bool:
    """Is this response one of the public pages? Error pages, which match no
    view, are not. One that does - a stopped share link's 404, a 429 on a
    public page - says noindex through its own `robots` block in base.html."""
    match = getattr(request, "resolver_match", None)
    return match is not None and match.view_name in (
        PUBLIC_PAGES + INDEXABLE_UNLISTED + INDEXABLE_ROWS)


def alternates(request) -> list[tuple[str, str]]:
    """[(hreflang, absolute URL)] for every language this page is in, then
    `x-default`, the English address (P10). Empty for a page that is not
    public, and while English is the only language switched on.

    `translate_url` reads the address in the active language, which is the
    page's own (accounts.middleware), and writes it in each of the others."""
    if not is_public(request) or len(settings.LANGUAGES) < 2:
        return []
    links = [(code, absolute(request, translate_url(request.path, code)))
             for code, _ in settings.LANGUAGES]
    return [*links, ("x-default", dict(links)[settings.LANGUAGE_CODE])]


def context(request) -> dict:
    """What base.html needs for canonical, hreflang, robots and Open Graph tags."""
    return {
        "canonical_url": absolute(request, request.path),
        "alternates": alternates(request),
        "indexable": is_public(request),
        "og_image_url": absolute(request, static(OG_IMAGE)),
        "og_locale": OG_LOCALES.get(get_language() or "en", "en_US"),
    }


class SiteSitemap(Sitemap):
    """A part of /sitemap.xml on `SITE_URL`, each page in every language
    switched on, with the same hreflang links as the page (P10). Django's
    own `i18n` does it: `location` is called with each language active.

    No `lastmod`, `changefreq` or `priority`: Google ignores the last two, and
    a `lastmod` that is not kept true does more harm than none.
    """

    i18n = True
    alternates = True
    x_default = True

    def get_protocol(self, protocol=None):
        if settings.SITE_URL:
            return urlsplit(settings.SITE_URL).scheme
        return super().get_protocol(protocol)

    def get_domain(self, site=None):
        if settings.SITE_URL:
            return urlsplit(settings.SITE_URL).netloc
        return super().get_domain(site)


class PublicPagesSitemap(SiteSitemap):
    """The public pages, by view name."""

    def items(self):
        return list(PUBLIC_PAGES)

    def location(self, item):
        return reverse(item)
