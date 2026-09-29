"""Deck screens: the list, the importer, the review, the deck page.

Every queryset in this module is filtered by `owner=self.request.user`. Not
`get_object_or_404(pk=...)` followed by a permission check - filtered at the
source, so a missing filter is a missing row rather than someone else's deck.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import DeleteView, DetailView, FormView, ListView, View
from django_ratelimit.decorators import ratelimit

from billing.quotas import QuotaExceeded
from billing.views import upgrade_prompt
from cards.models import OracleCard
from combos import services as combo_services
from decks import analysis as deck_analysis
from decks import services
from decks.forms import ColumnMappingForm, ImportForm
from decks.importers import columns, tabular
from decks.models import Deck, DeckCard, DeckImport, PendingImport
from decks.resolve import RUNG_LABELS, RUNG_ORDER, RUNG_UNRESOLVED
from simulations import blindspots
from simulations.engine import adapter
from simulations.forms import RunForm


class OwnedDecksMixin(LoginRequiredMixin):
    """Scope everything to the signed-in user's own decks."""

    def get_queryset(self):
        return Deck.objects.filter(owner=self.request.user)


class DeckListView(OwnedDecksMixin, ListView):
    template_name = "decks/list.html"
    context_object_name = "decks"

    def get_queryset(self):
        return super().get_queryset().select_related("commander")


class DeckDetailView(OwnedDecksMixin, DetailView):
    template_name = "decks/detail.html"
    context_object_name = "deck"

    def get_queryset(self):
        entries = DeckCard.objects.select_related("oracle_card", "oracle_card__profile")
        return (
            super()
            .get_queryset()
            .select_related("commander")
            .prefetch_related(Prefetch("entries", queryset=entries))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deck = self.object
        context["analysis"] = deck_analysis.analyse(deck)
        context["review_cards"] = deck_analysis.review_cards(deck)
        context["last_import"] = deck.imports.first()
        context["curve_max"] = max(context["analysis"].curve.values() or [1]) or 1
        context["run_form"] = RunForm()
        context["runs"] = deck.runs.all()[:5]
        # Sessions, not games: a playtest is one game played by hand and is
        # resumed rather than re-run, so the list is of things to go back to.
        context["playtests"] = deck.playtests.all()[:5]
        # Read from cache and never fetched here. The panel's own button is the
        # only thing in this application that talks to Commander Spellbook, so
        # a deck page cannot be made slow - or made to fail - by their uptime.
        context["combos"] = combo_services.panel_for(deck)
        # A summary only. The full panel lives on the tune page and on every
        # report; repeating it here would make the deck page the third place
        # that says the same thing, and three copies of a warning is how a
        # warning becomes furniture.
        spots = blindspots.find(adapter.readings(deck))
        context["blindspots"] = spots
        context["blindspot_cards"] = len(
            {suspect.oracle_id for spot in spots for suspect in spot.suspects}
        )
        return context


#: What to say when nothing recognised the file. It used to end "other formats
#: arrive in a later phase", which stopped being true the day any CSV became
#: importable - the honest instruction now is a single click.
#: What the preview panel says before anybody has answered the dropdowns.
#:
#: Not the parser's own refusal, which is written for the upload form and tells
#: the reader to go and re-export their file with the quantity column ticked.
#: That is sound advice *there* and absurd *here*, on the screen that exists so
#: they do not have to. The screenshot pass caught it; no test could have.
UNANSWERED_PREVIEW = (
    "Choose a column above for each of card name and quantity - or say the "
    "file has not got one - and your own rows will appear here."
)

UNKNOWN_FORMAT_HELP = (
    "That file was not recognised, so nothing has been assumed about it. "
    "Choose “CSV or TSV export (any tool)” as the format and you will be "
    "asked which column is which - it takes about thirty seconds and works "
    "for any export. Choose “Plain text list” if it is simply one card "
    "name per line."
)


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class DeckImportView(LoginRequiredMixin, FormView):
    """Upload a deck list and turn it into a deck.

    Rate limited because this is one of the two endpoints where a stranger
    hands the server a file and the server does work proportional to it. The
    quota system already bounds how many decks an account may own; it does not
    bound how fast somebody may ask, and parsing is the expensive half.

    Keyed on the user rather than the address because the view is
    login-required, so there is always one. Registering accounts to get around
    it runs into allauth's own signup limit, which is keyed on the address.
    """

    template_name = "decks/import.html"
    form_class = ImportForm

    def form_valid(self, form):
        raw, filename = form.payload()
        chosen_format = form.cleaned_data.get("format", "")
        # Where a complaint about the contents goes: under the control the
        # person actually used, or it lands in a tab they cannot see.
        source_field = "text" if form.pasting else "file"

        try:
            preparation = services.prepare(raw, chosen_format)
        except services.UnknownFormat:
            form.add_error("format", UNKNOWN_FORMAT_HELP)
            return self.form_invalid(form)
        except services.ImportError_ as exc:
            form.add_error(source_field, str(exc))
            return self.form_invalid(form)

        # The columns do not answer for themselves, so a person does. Nothing
        # is written and no quota is spent until they have.
        if preparation.needs_mapping:
            pending = services.hold(
                owner=self.request.user,
                preparation=preparation,
                kind=PendingImport.Kind.DECK,
                filename=filename,
                deck_name=form.cleaned_data.get("name", ""),
            )
            return redirect(pending.get_absolute_url())

        try:
            outcome = services.import_deck(
                owner=self.request.user,
                text=preparation.text,
                name=form.cleaned_data.get("name", ""),
                filename=filename,
                parser_name=chosen_format,
            )
        except QuotaExceeded as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        except services.ImportError_ as exc:
            form.add_error(source_field, str(exc))
            return self.form_invalid(form)

        if outcome.clean:
            messages.success(
                self.request,
                f"Imported {outcome.record.rows_resolved} rows into {outcome.deck.name}.",
            )
            return redirect(outcome.deck.get_absolute_url())

        messages.warning(
            self.request,
            f"{outcome.record.rows_unresolved} of {outcome.record.rows_total} rows "
            "could not be matched. Nothing was dropped - they are listed below.",
        )
        return redirect(reverse("decks:review", args=[outcome.record.id]))


class ImportMappingView(LoginRequiredMixin, View):
    """Confirm which column of an upload means which thing, then import it.

    The screen that replaced a plan to write seven parsers. It is shown only
    when the file's own headers do not answer the question - which is never for
    an Archidekt export and rarely for anything else - and it is the reason an
    unrecognised format now costs the person thirty seconds instead of costing
    us a phase.

    **Nothing is written and no quota is spent until the form validates.** The
    `PendingImport` holds the decoded text in the meantime and deletes itself
    the moment the import lands.
    """

    template_name = "decks/map.html"

    def get_pending(self) -> PendingImport:
        return get_object_or_404(
            PendingImport, pk=self.kwargs["pk"], owner=self.request.user
        )

    def get(self, request, pk):
        pending = self.get_pending()
        preparation = services.prepare_text(pending.text, pending.parser)
        return self.page(request, pending, preparation,
                         ColumnMappingForm(mapping=preparation.mapping))

    def post(self, request, pk):
        pending = self.get_pending()
        preparation = services.prepare_text(pending.text, pending.parser)
        form = ColumnMappingForm(request.POST, mapping=preparation.mapping)

        if not form.is_valid():
            return self.page(request, pending, preparation, form)

        overrides = form.overrides
        try:
            outcome = self.run(pending, overrides)
        except QuotaExceeded as exc:
            # A 402 that links to the tier with more, rather than a red
            # sentence on a form nobody can act on.
            return upgrade_prompt(request, str(exc))
        except services.ImportError_ as exc:
            form.add_error(None, str(exc))
            return self.page(request, pending, preparation, form, overrides)

        # Only now, with rows in the database.
        pending.delete()
        return self.done(request, pending, outcome)

    def run(self, pending: PendingImport, overrides: dict):
        return services.import_deck(
            owner=self.request.user,
            text=pending.text,
            name=pending.deck_name,
            filename=pending.filename,
            parser_name=pending.parser,
            deck=pending.deck,
            overrides=overrides,
        )

    def done(self, request, pending: PendingImport, outcome):
        if outcome.clean:
            messages.success(
                request,
                f"Imported {outcome.record.rows_resolved} rows into "
                f"{outcome.deck.name}.",
            )
            return redirect(outcome.deck.get_absolute_url())

        messages.warning(
            request,
            f"{outcome.record.rows_unresolved} of {outcome.record.rows_total} "
            "rows could not be matched. Nothing was dropped - they are listed "
            "below.",
        )
        return redirect(reverse("decks:review", args=[outcome.record.id]))

    def page(self, request, pending, preparation, form, overrides=None):
        rows, problem = self.panel(pending, overrides)
        return render(request, self.template_name, {
            "pending": pending,
            "form": form,
            "table": preparation.table,
            "headers": preparation.mapping.headers,
            # The file as it actually is, so somebody choosing a column can see
            # the columns. The parsed preview below cannot help them until they
            # have chosen, which is precisely when they need help.
            "sample": tabular.sample(pending.text, preparation.table),
            "preview_rows": rows,
            "preview_problem": problem,
            "absent": columns.ABSENT,
        })

    def panel(self, pending, overrides):
        """The preview, with the right sentence for the screen it is on.

        A mapping nobody has answered yet is not an error, so it does not get
        an error message - it gets the instruction that finishes the job.
        """
        rows, problem = services.preview(pending.text, pending.parser, overrides)
        if problem and overrides is None:
            return rows, UNANSWERED_PREVIEW
        return rows, problem


class ImportPreviewView(ImportMappingView):
    """The preview panel alone, re-rendered as the dropdowns change.

    An enhancement and never a requirement: the page works with htmx switched
    off, because the preview is also rendered server-side on the way in and the
    import itself is an ordinary form post.
    """

    template_name = "decks/_mapping_preview.html"

    def get(self, request, pk):
        return self.post(request, pk)

    def post(self, request, pk):
        pending = self.get_pending()
        preparation = services.prepare_text(pending.text, pending.parser)
        form = ColumnMappingForm(request.POST or None, mapping=preparation.mapping)

        # An incomplete answer previews as the *unconfirmed* mapping, not as a
        # half-confirmed one. Otherwise a blank quantity dropdown would render
        # a column of 1s, which is the exact lie this screen exists to stop.
        overrides = form.overrides if form.is_valid() else None
        rows, problem = self.panel(pending, overrides)
        return render(request, self.template_name, {
            "preview_rows": rows,
            "preview_problem": problem,
        })


class ImportReviewView(LoginRequiredMixin, DetailView):
    """The rows the importer could not resolve.

    This screen is the reason the importer never drops a row. It is allowed to
    be dull; it is not allowed to be absent.
    """

    template_name = "decks/review.html"
    context_object_name = "record"

    def get_queryset(self):
        return DeckImport.objects.filter(owner=self.request.user).select_related("deck")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["unresolved"] = self.object.unresolved.all()
        # How exact the import was, rung by rung. The honest answer to
        # "did it work" is this table, not a green tick.
        context["rungs"] = [
            (RUNG_LABELS[rung], self.object.rung_counts.get(rung, 0))
            for rung in [*RUNG_ORDER, RUNG_UNRESOLVED]
            if self.object.rung_counts.get(rung)
        ]
        return context


class SetCommanderView(LoginRequiredMixin, View):
    """Pick the commander by hand when the file did not say."""

    def post(self, request, pk):
        deck = get_object_or_404(Deck, pk=pk, owner=request.user)
        card = get_object_or_404(OracleCard, pk=request.POST.get("oracle_id"))

        if not deck.entries.filter(oracle_card=card).exists():
            messages.error(request, "That card is not in this deck.")
            return redirect(deck.get_absolute_url())

        services.set_commander(deck, card)
        messages.success(request, f"{card.name} is now the commander.")
        return redirect(deck.get_absolute_url())


class DeckDeleteView(OwnedDecksMixin, DeleteView):
    template_name = "decks/confirm_delete.html"
    success_url = reverse_lazy("decks:list")
    context_object_name = "deck"
