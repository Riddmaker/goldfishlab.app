"""The language switcher's endpoint (phase 12, Q1).

Django's `set_language` does the work - POST only, CSRF, the `next` address
checked against this host, the cookie - and this adds two things to it: only a
language that is switched on is accepted (Django's own check only asks whether
a catalogue exists, which is true for every language Django ships), and a
signed-in person's choice is saved on the account, guests included, so
`guests.services.claim` can carry it over.

Django's view also moves `next` to the address in the new language (P10),
/de/pricing/ to /pricing/; but it reads `next` in the language that is
active, and an address that names another one only reads in that one. So it
is read in the language the address names, or English for one without.

Mounted at `/i18n/`, outside the guest fence, so a guest can switch too.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.utils import translation
from django.views.decorators.http import require_POST
from django.views.i18n import LANGUAGE_QUERY_PARAMETER
from django.views.i18n import set_language as django_set_language


@require_POST
def set_language(request):
    code = request.POST.get(LANGUAGE_QUERY_PARAMETER, "")
    if code not in dict(settings.LANGUAGES):
        return HttpResponseBadRequest("Unknown language.")
    user = request.user
    if user.is_authenticated and user.language != code:
        user.language = code
        user.save(update_fields=["language"])
    next_url = (request.POST.get("next") or request.GET.get("next")
                or request.META.get("HTTP_REFERER") or "")
    path = urlsplit(next_url).path
    with translation.override(translation.get_language_from_path(path)
                              or settings.LANGUAGE_CODE):
        return django_set_language(request)
