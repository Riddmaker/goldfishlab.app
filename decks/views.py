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
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views.generic import DeleteView, DetailView, FormView, ListView, View
from django_ratelimit.decorators import ratelimit

from billing import quotas
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
from simulations import blindspots, deck_cards, review, summary
from simulations.engine import adapter
from simulations.forms import RunForm
from simulations.models import SimulationRun


class OwnedDecksMixin(LoginRequiredMixin):
    """Scope everything to the signed-in user's own decks."""

    def get_queryset(self):
        return Deck.objects.filter(owner=self.request.user)


class DeckListView(OwnedDecksMixin, ListView):
    template_name = "decks/list.html"
    context_object_name = "decks"

    def get_queryset(self):
        return super().get_queryset().select_related("commander")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Counted once per deck and stored; after that the marker is a column
        # read, not a reading of every card on every visit.
        for deck in context["decks"]:
            review.open_questions(deck)
        return context


class DeckDetailView(OwnedDecksMixin, DetailView):
    """The deck page: what to do first, then what the deck is (Phase 9 D).

    An htmx request from the grid's filter form gets the grid alone - the rest
    of the page does not change when a chip is clicked, so it is not worked
    out again. A history restore is a whole page, which htmx asks for with its
    own header when the back button finds nothing cached.
    """

    template_name = "decks/detail.html"
    grid_template = "decks/_card_grid.html"
    context_object_name = "deck"

    def get_queryset(self):
        entries = DeckCard.objects.select_related("oracle_card", "oracle_card__profile")
        return (
            super()
            .get_queryset()
            .select_related("commander")
            .prefetch_related(Prefetch("entries", queryset=entries))
        )

    def get(self, request, *args, **kwargs):
        headers = request.headers
        if headers.get("HX-Request") and not headers.get("HX-History-Restore-Request"):
            self.object = self.get_object()
            readings = adapter.readings(self.object)
            response = render(request, self.grid_template,
                              self.grid_context(self.object, readings))
        else:
            response = super().get(request, *args, **kwargs)
        # One URL, two bodies: a cache must not hand the fragment to a page load.
        patch_vary_headers(response, ("HX-Request",))
        return response

    def grid_context(self, deck, readings) -> dict:
        grid = deck_cards.cards(readings, review.queue(deck, readings))
        filters = deck_cards.Filters.from_request(self.request.GET)
        type_bars, category_bars = deck_cards.bars(grid)
        return {
            "deck": deck,
            "grid": deck_cards.shown(grid, filters),
            "grid_total": len(grid),
            "filters": filters,
            "type_bars": type_bars,
            "category_bars": category_bars,
        }

    @staticmethod
    def last_run_context(deck, runs) -> dict:
        """"Your last run" (phase 11 F11): the newest finished run, a run still
        going instead when there is one, and whether the deck changed since.

        Runs are never deleted; before this they were only reachable folded
        under "Earlier runs and games". A run from before `deck_print` existed
        says nothing about changes rather than guess.
        """
        going = next((run for run in runs[:1] if not run.is_finished), None)
        done = next((run for run in runs if run.status == SimulationRun.Status.DONE), None)
        changed = bool(done and done.deck_print
                       and done.deck_print != summary.fingerprint(deck))
        return {"running_run": going, "last_run": done, "deck_changed": changed}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deck = self.object
        readings = adapter.readings(deck)
        review.open_questions(deck)
        context.update(self.grid_context(deck, readings))
        analysis = deck_analysis.analyse(deck)
        context["analysis"] = analysis
        context["legality_problems"] = sum(1 for verdict in analysis.legality if not verdict.ok)
        context["last_import"] = deck.imports.first()
        context["curve_max"] = max(analysis.curve.values() or [1]) or 1
        context["run_form"] = RunForm(plan=quotas.plan_for(self.request.user))
        context["runs"] = runs = list(deck.runs.all()[:5])
        context.update(self.last_run_context(deck, runs))
        # Sessions, not games: a playtest is one game played by hand and is
        # resumed rather than re-run, so the list is of things to go back to.
        context["playtests"] = deck.playtests.all()[:5]
        # Read from cache and never fetched here. The panel's own button is the
        # only thing in this application that talks to Commander Spellbook, so
        # a deck page cannot be made slow - or made to fail - by their uptime.
        context["combos"] = combo_services.panel_for(deck)
        # The full panel, folded in "What the engine cannot model" - since the
        # card list moved here (phase 9 I) this is the deck's one copy of it;
        # every report carries the other.
        context["blindspots"] = blindspots.find(readings)
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


class ImportFlowMixin:
    """Upload -> prepare -> (map | import) -> land, for any owner.

    Shared by the signed-in importer below and the guest trial
    (`guests.views.TryView`), which differ only in who owns the result and
    where a clean import lands. `import_owner()` is asked only after the file
    has been read, so a bad upload creates nothing - not even a guest.
    """

    template_name = "decks/import.html"
    form_class = ImportForm

    def import_owner(self):
        return self.request.user

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

        owner = self.import_owner()

        # The columns do not answer for themselves, so a person does. Nothing
        # is written and no quota is spent until they have.
        if preparation.needs_mapping:
            pending = services.hold(
                owner=owner,
                preparation=preparation,
                kind=PendingImport.Kind.DECK,
                filename=filename,
                deck_name=form.cleaned_data.get("name", ""),
            )
            return redirect(pending.get_absolute_url())

        try:
            outcome = services.import_deck(
                owner=owner,
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

        return self.landed(outcome)

    def landed(self, outcome):
        """Where an import that went through ends up."""
        return imported(self.request, outcome)


def imported(request, outcome):
    """The deck page for a clean import, the review for one with gaps."""
    if outcome.clean:
        messages.success(
            request,
            f"Imported {outcome.record.rows_resolved} rows into {outcome.deck.name}.",
        )
        return redirect(outcome.deck.get_absolute_url())

    messages.warning(
        request,
        f"{outcome.record.rows_unresolved} of {outcome.record.rows_total} rows "
        "could not be matched. Nothing was dropped - they are listed below.",
    )
    return redirect(reverse("decks:review", args=[outcome.record.id]))


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class DeckImportView(LoginRequiredMixin, ImportFlowMixin, FormView):
    """Upload a deck list and turn it into a deck.

    Rate limited because this is one of the two endpoints where a stranger
    hands the server a file and the server does work proportional to it. The
    quota system already bounds how many decks an account may own; it does not
    bound how fast somebody may ask, and parsing is the expensive half.

    Keyed on the user rather than the address because the view is
    login-required, so there is always one. Registering accounts to get around
    it runs into allauth's own signup limit, which is keyed on the address.
    """


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
        if getattr(request.user, "is_guest", False):
            # A guest's upload runs by itself, whichever way it came in.
            from guests.views import land

            return land(request, outcome)
        return imported(request, outcome)

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
