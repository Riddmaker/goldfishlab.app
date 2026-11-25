"""A deck from photos (B-14, PR 2): upload, read, check, save.

Two screens. "From photos" takes up to `photos.MAX_PHOTOS` photos, reads them
and parks what was read (`photos.hold`); "Check the cards" shows it row by
row, lets the person keep, change or drop each card and find one that was
missed, and saves the result through the ordinary import. Only the owner's
own parked reading is ever found: every lookup is filtered by `owner`.
"""

import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext, gettext_noop, ngettext
from django.views import View
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django_ratelimit.decorators import ratelimit

from billing import quotas
from billing.models import UsageRecord
from billing.quotas import QuotaExceeded
from billing.views import refusal, upgrade_prompt
from cards.models import OracleCard
from decks import photos, services
from decks.models import PendingImport
from simulations import mistral

#: Runs a day per account, beside the month's allowance: ten photos are ten
#: model calls, and a slip of the finger should not spend a month in an hour.
RUNS_PER_DAY = "3/d"
#: Cards a search finds, and the most a list may add by hand.
SEARCH_RESULTS = 12
MAX_ADDED = 40

NOT_READ = gettext_noop("The photos could not be read just now. Nothing was counted; "
                        "try again in a moment.")
NOTHING_FOUND = gettext_noop("No card title could be read on these photos. Nothing was "
                             "counted. Lay the cards side by side with their titles in "
                             "the light, and try again.")

NO_CONSENT = gettext_noop("Tick the box first: the photos are only sent once you agree.")


def _metric():
    return UsageRecord.Metric.PHOTO_IMPORTS


class PhotoUploadView(LoginRequiredMixin, View):
    """"From photos": the upload, read on the spot.

    CSRF is checked by hand, after the upload handler is set: Django reads the
    body for the token, and a handler can only be set before the body is
    read (the pattern of Django's own upload-handler documentation). The
    handler keeps the photos in memory and stops the request at the ceiling.
    """

    template_name = "decks/photos.html"

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        if not mistral.is_configured():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self.page(request)

    @method_decorator(ratelimit(key="user", rate=RUNS_PER_DAY, method="POST"))
    def post(self, request):
        request.upload_handlers = [photos.MemoryOnlyUploadHandler(request)]
        return csrf_protect(self.upload)(request)

    def upload(self, request):
        files = request.FILES.getlist("photos")
        if not files:
            return self.page(request, gettext("Choose at least one photo."), status=400)
        if len(files) > photos.MAX_PHOTOS:
            return self.page(request, ngettext(
                "At most %(count)s photo at a time.", "At most %(count)s photos at a time.",
                photos.MAX_PHOTOS) % {"count": photos.MAX_PHOTOS}, status=400)
        # Asked once per account, before anything is counted or sent.
        asked = request.user.photos_consent_since is None
        if asked and request.POST.get("consent") != "yes":
            return self.page(request, gettext(NO_CONSENT), status=400)
        # Call site 7, before any work on the photos.
        try:
            quotas.check(request.user, _metric())
        except QuotaExceeded as exc:
            return upgrade_prompt(request, exc)
        jpegs = []
        for number, upload in enumerate(files, start=1):
            try:
                jpegs.append(photos.prepare(upload))
            except photos.PhotoRefused as exc:
                return self.page(request, gettext("Photo %(number)s: %(reason)s") % {
                    "number": number, "reason": exc}, status=400)
        if asked:
            request.user.photos_consent_since = timezone.now()
            request.user.save(update_fields=["photos_consent_since"])
        quotas.consume(request.user, _metric())
        try:
            reading = photos.read(jpegs)
        except mistral.MistralError:
            quotas.refund(request.user, _metric())
            return self.page(request, gettext(NOT_READ), status=503)
        if not reading.found_any:
            quotas.refund(request.user, _metric())
            return self.page(request, gettext(NOTHING_FOUND), status=422)
        return redirect(photos.hold(request.user, reading).get_absolute_url())

    def page(self, request, error="", status=200):
        decision = quotas.check(request.user, _metric(), raise_on_fail=False)
        blocked = "" if decision.allowed else refusal(QuotaExceeded(
            "", metric=_metric(), limit=decision.limit, used=decision.used))
        return render(request, self.template_name, {
            "error": error, "decision": decision, "blocked": blocked,
            "max_photos": photos.MAX_PHOTOS,
            "ask_consent": request.user.photos_consent_since is None,
        }, status=status)


@method_decorator(ratelimit(key="user", rate="30/m", method="POST"), name="post")
class PhotoCheckView(LoginRequiredMixin, View):
    """"Check the cards": every row of the reading, then "Save the deck".

    One form, as "New version" is: the search and "Not this card" post
    everything, so what was chosen stays chosen without a script. Nothing is
    saved until "Save the deck", which hands the list to the import; the
    parked reading goes only once the deck is there.
    """

    template_name = "decks/photos_check.html"

    def get_pending(self, pk) -> PendingImport:
        return get_object_or_404(PendingImport, pk=pk, owner=self.request.user,
                                 kind=PendingImport.Kind.PHOTOS)

    def get(self, request, pk):
        pending = self.get_pending(pk)
        return self.page(request, pending, photos.load(pending), [], "", pending.deck_name)

    def post(self, request, pk):
        pending = self.get_pending(pk)
        held = photos.load(pending)
        data = request.POST
        opened = {value for value in data.getlist("open") if value.isdigit()}
        action = data.get("action", "")
        if action.startswith("wrong-"):
            opened.add(action.removeprefix("wrong-"))
        for row in held.rows:
            self.apply(row, data, opened)
        added = list(OracleCard.objects.filter(
            pk__in=[value for value in data.getlist("add") if _uuid(value)])[:MAX_ADDED])
        name = data.get("name", "").strip()[:120]
        if action == "save":
            return self.save(request, pending, held, added, name)
        return self.page(request, pending, held, added, data.get("q", "").strip()[:80], name)

    @staticmethod
    def apply(row: photos.Row, data, opened: set[str]) -> None:
        """The person's choice for one row, as posted; anything not offered
        by the form is read as leaving the row out."""
        chosen = data.get(f"card-{row.index}", "")
        if chosen != photos.KEEP_AS_READ and not _uuid(chosen):
            chosen = ""
        row.chosen = chosen
        row.opened = str(row.index) in opened
        try:
            row.quantity = min(max(int(data.get(f"qty-{row.index}", row.quantity)), 1),
                               photos.COUNT_MAX)
        except ValueError:
            pass

    def save(self, request, pending, held, added, name):
        text = photos.deck_text(held.rows, added)
        if not text:
            messages.error(request, gettext("Keep at least one card to save a deck."))
            return self.page(request, pending, held, added, "", name, status=400)
        try:
            outcome = services.import_deck(owner=request.user, text=text,
                                           name=name or gettext("Deck from photos"),
                                           parser_name="text")
        except QuotaExceeded as exc:
            return upgrade_prompt(request, exc)
        except services.ImportError_ as exc:
            messages.error(request, str(exc))
            return self.page(request, pending, held, added, "", name, status=400)
        pending.delete()
        from decks.views import imported

        return imported(request, outcome)

    def page(self, request, pending, held, added, query, name, status=200):
        for row in held.rows:
            if row.opened or (row.found and row.chosen not in ("", str(row.card.pk),
                                                               photos.KEEP_AS_READ)):
                row.opened = True
                row.neighbours = photos.neighbours(row)
        taken = {str(card.pk) for card in added}
        found = []
        if len(query) >= 2:
            found = [card for card in OracleCard.objects.filter(front_name__icontains=query)
                     .order_by(F("edhrec_rank").asc(nulls_last=True), "front_name")
                     [:SEARCH_RESULTS] if str(card.pk) not in taken]
        kept = sum(1 for row in held.rows if row.chosen) + len(added)
        return render(request, self.template_name, {
            "pending": pending, "held": held, "added": added, "query": query,
            "found": found, "name": name, "kept": kept,
            "open_rows": [row for row in held.rows if not row.found],
            "keep_as_read": photos.KEEP_AS_READ,
        }, status=status)


def _uuid(value: str) -> bool:
    """A card id as the form posts it, checked before it reaches a query."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
