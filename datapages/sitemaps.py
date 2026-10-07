"""Every published precon page in the sitemap, in every language (P11).

The table and the article are in `core.seo.PUBLIC_PAGES`; the precons are
rows, so they are listed here, with the same hreflang links as the pages."""

from core.seo import SiteSitemap
from datapages import views


class PreconSitemap(SiteSitemap):
    def items(self):
        return [row.precon for row in views.rows()]
