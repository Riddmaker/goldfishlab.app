"""Collection screens.

Two of them, and neither takes an id: there is one collection per user and it
is always `request.user`'s, so there is nothing in a path for somebody to
change. That is the strongest form of the rule the deck and simulation views
follow by filtering on owner.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.generic import FormView, TemplateView
from django_ratelimit.decorators import ratelimit

from billing.quotas import QuotaExceeded
from billing.views import upgrade_prompt
from collection import services
from collection.forms import CollectionImportForm
from decks import services as import_services
from decks.models import Deck, PendingImport
from decks.services import ImportError_, UnknownFormat
from decks.views import UNKNOWN_FORMAT_HELP


class CollectionView(LoginRequiredMixin, TemplateView):
    """What you own, and which of your decks you could build from it."""

    template_name = "collection/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collection = services.for_user(self.request.user)
        context["collection"] = collection

        if collection is None:
            return context

        # `printing` joined as well, because every row asks it for a price and
        # a finish. Without it this is 183 extra queries behind a `<details>`.
        items = list(
            collection.items.select_related("oracle_card", "printing")
            .order_by("oracle_card__name", "set_code")
        )
        context["items"] = items
        context["prices"] = services.price_summary(items)
        # One row per deck, answering the one question this app exists for.
        # N decks means N shortfall computations, each two queries; a person
        # with three decks is the case, and a person with three hundred would
        # be a paginated page rather than a faster loop.
        context["decks"] = [
            {"deck": deck, "shortfall": services.shortfall(deck, collection)}
            for deck in Deck.objects.filter(owner=self.request.user)
            .select_related("commander").prefetch_related("entries__oracle_card")
        ]
        return context


# Half the deck importer's rate. A collection export is the biggest file this
# application accepts - the user's own is 214 rows and a serious collection is
# tens of thousands - and re-uploading one five times a minute is not a thing
# anybody does on purpose.
@method_decorator(ratelimit(key="user", rate="5/m", method="POST"), name="post")
class CollectionImportView(LoginRequiredMixin, FormView):
    """Upload a collection export."""

    template_name = "collection/import.html"
    form_class = CollectionImportForm

    def form_valid(self, form):
        upload = form.cleaned_data["file"]
        chosen_format = form.cleaned_data.get("format", "")

        # Identical to the deck importer as far as the columns are concerned,
        # and deliberately so: the same export feeds both, and a second way of
        # deciding what a column means would be a second answer.
        try:
            preparation = import_services.prepare(upload.read(), chosen_format)
        except UnknownFormat:
            form.add_error("format", UNKNOWN_FORMAT_HELP)
            return self.form_invalid(form)
        except ImportError_ as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)

        if preparation.needs_mapping:
            pending = import_services.hold(
                owner=self.request.user,
                preparation=preparation,
                kind=PendingImport.Kind.COLLECTION,
                filename=upload.name,
            )
            return redirect(pending.get_absolute_url())

        try:
            outcome = services.import_collection(
                owner=self.request.user,
                text=preparation.text,
                filename=upload.name,
                parser_name=chosen_format,
            )
        except QuotaExceeded as exc:
            return upgrade_prompt(self.request, str(exc))
        except ImportError_ as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)

        if outcome.clean:
            messages.success(
                self.request,
                f"{outcome.collection.total_cards} cards imported, "
                f"{outcome.collection.distinct_cards} of them different.",
            )
        else:
            # Same rule as the deck importer: nothing is dropped silently. The
            # difference is that a collection has no review screen yet, so the
            # count is said out loud here instead of being a link.
            messages.warning(
                self.request,
                f"{len(outcome.report.unresolved)} of "
                f"{outcome.report.rows_total} rows did not match a card and "
                "were left out. Everything else was imported.",
            )
        return redirect("collection:detail")
