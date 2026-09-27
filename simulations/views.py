"""Screens for starting a simulation, watching it, reading it, and correcting it.

Every queryset here is filtered by `owner=self.request.user` at the source, the
same rule the deck views follow: a missing filter has to produce a missing row,
never someone else's simulation.

The progress fragment is the only unusual piece. It answers with a partial page
carrying its own `hx-trigger`, and when the run reaches a terminal state the
fragment comes back **without** the trigger - so polling stops because there is
nothing left to poll with. No JavaScript is written by hand anywhere in this
application.

The second half of the module is Phase 4: the screens that let somebody find
out what the engine read off their cards, and say so when it read them wrong.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, View
from django_ratelimit.decorators import ratelimit

from billing import views as billing_views
from billing.quotas import QuotaExceeded
from cards.models import OracleCard
from decks.models import Deck
from simulations import blindspots, provenance, report, services
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.forms import AnnotationForm, PriorityChoice, PriorityForm, RunForm
from simulations.models import SimulationRun


class OwnedRunsMixin(LoginRequiredMixin):
    """Scope every run lookup to the signed-in user."""

    def get_queryset(self):
        return SimulationRun.objects.filter(owner=self.request.user).select_related("deck")


@method_decorator(ratelimit(key="user", rate="20/m", method="POST"), name="post")
class RunCreateView(LoginRequiredMixin, View):
    """Start a simulation for one deck.

    The quota already bounds how many games an account may simulate in a month
    and refunds them when a run is cancelled. That is a budget, not a valve:
    twenty small runs enqueued in a second are twenty chords of Celery tasks,
    and the quota is perfectly happy with all of them. This is the valve.

    Twenty a minute is far above anybody clicking a button and far below a loop.
    """

    def post(self, request, deck_id):
        deck = get_object_or_404(Deck, pk=deck_id, owner=request.user)
        form = RunForm(request.POST)

        if not form.is_valid():
            messages.error(request, "That is not a size this application runs.")
            return redirect(deck.get_absolute_url())

        if not deck.entries.exists():
            messages.error(request, "There is nothing in this deck to simulate yet.")
            return redirect(deck.get_absolute_url())

        try:
            run = services.start_run(
                owner=request.user,
                deck=deck,
                games=form.cleaned_data["games"],
                turns=form.cleaned_data["turns"],
                on_the_play=form.cleaned_data["on_the_play"],
            )
        except QuotaExceeded as exc:
            # Not a message on the deck page. A refusal the person cannot act
            # on is a dead end, and the one thing that answers "you have used
            # all twenty runs" is the page listing the tier with three hundred.
            return billing_views.upgrade_prompt(request, str(exc))
        except services.SimulationRefused as exc:
            messages.error(request, str(exc))
            return redirect(deck.get_absolute_url())

        return redirect(run.get_absolute_url())


class RunDetailView(OwnedRunsMixin, DetailView):
    """One run: the progress bar while it works, the report once it is done."""

    template_name = "simulations/detail.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = self.object
        if run.status == SimulationRun.Status.DONE and run.result:
            context["report"] = report.build(run)
            # Read from the deck as it stands now, not from the run: these are
            # properties of the cards rather than of the simulation, and the
            # panel says so rather than implying the run measured them.
            context["blindspots"] = blindspots.find(adapter.readings(run.deck))
        return context


class RunProgressView(OwnedRunsMixin, DetailView):
    """The polling fragment.

    Deliberately tiny and deliberately dumb: one row from the database, no
    broker call. That is what makes polling it every two seconds cheap enough
    to be the whole mechanism.

    When the run has finished, the answer to a poll is `HX-Refresh`, which
    tells htmx to reload the page - so the report appears by itself, the
    polling stops with it, and still not a line of JavaScript was written.
    """

    template_name = "simulations/_progress.html"
    context_object_name = "run"

    def render_to_response(self, context, **kwargs):
        response = super().render_to_response(context, **kwargs)
        if self.object.is_finished and self.request.headers.get("HX-Request"):
            response.headers["HX-Refresh"] = "true"
        return response


class RunCancelView(OwnedRunsMixin, View):
    """Ask a run to stop."""

    def post(self, request, pk):
        run = get_object_or_404(self.get_queryset(), pk=pk)
        if services.request_cancel(run):
            messages.success(
                request,
                "Cancelling. The chunk already in flight finishes first, so this "
                "takes a few seconds.",
            )
        else:
            messages.info(request, "That run had already finished.")
        return redirect(run.get_absolute_url())


# --- the honesty layer ------------------------------------------------------
#
# Three screens, and the reason they exist: a result nobody can check is a
# result nobody should believe. The tune page says what the engine reads off
# every card and where each reading came from; the card page lets a human
# disagree; the priority page lets them say what the deck is actually trying
# to do, which is the one thing no amount of card text implies.
#
# Ownership works the same way as everywhere else in this application: the
# deck is fetched filtered by owner, and the card is fetched filtered by deck
# membership. A card id that is not in the deck is a 404, not a saved row
# against somebody else's deck.


class DeckScopedView(LoginRequiredMixin, View):
    """Anything that edits one deck's annotations.

    `deck` comes out of a queryset filtered by owner, so a deck belonging to
    somebody else is missing rather than forbidden - the same rule the deck
    views follow, for the same reason.
    """

    def get_deck(self, request, pk) -> Deck:
        return get_object_or_404(Deck, pk=pk, owner=request.user)

    def get_card(self, deck: Deck, oracle_id):
        """One card of this deck, commander included.

        The commander is not a `DeckCard` - it sits in the command zone - so
        filtering on deck membership alone would make it un-annotatable, which
        is how it lost its priority once before.
        """
        return get_object_or_404(
            OracleCard.objects.filter(
                Q(in_decks__deck=deck) | Q(commands_decks=deck)
            ).distinct(),
            pk=oracle_id,
        )


class DeckTuneView(DeckScopedView):
    """What the engine reads off every card in one deck.

    The page that answers "why does my deck simulate like that". Cards with
    something unresolved come first, because they are the ones where the
    answer is "because nobody told it" - and a list sorted by name would bury
    them among thirty Swamps.
    """

    def get(self, request, pk):
        deck = self.get_deck(request, pk)
        entries = provenance.for_deck(deck)
        spots = blindspots.find(entry.reading for entry in entries)

        return render(request, "simulations/tune.html", {
            "deck": deck,
            "entries": sorted(
                entries,
                # Cards the engine could not read come first, then the ones
                # only the author can answer for. Two different jobs, and the
                # first one is ours rather than theirs.
                key=lambda entry: (not entry.unreadable, not entry.unjudged,
                                   entry.name.lower()),
            ),
            "cards_total": len(entries),
            "cards_with_gaps": sum(1 for entry in entries if entry.has_gaps),
            "cards_unreadable": sum(1 for entry in entries if entry.unreadable),
            "cards_unjudged": sum(1 for entry in entries if entry.unjudged),
            "blindspots": spots,
            "sources": provenance.SOURCES,
        })


class CardAnnotateView(DeckScopedView):
    """One card: what the engine reads, and a form to disagree with it."""

    def get(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)
        scope = self._scope(request.GET.get("scope"))
        annotation = services.annotation_at(deck, card, scope)

        return render(request, "simulations/annotate.html", self._context(
            deck, card,
            form=AnnotationForm(
                initial={"scope": scope, **AnnotationForm.initial_for(annotation)}
            ),
            annotation=annotation,
            scope=scope,
        ))

    def post(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)
        form = AnnotationForm(request.POST)

        if form.is_valid():
            scope = form.cleaned_data["scope"]
            try:
                services.save_annotation(
                    deck=deck, oracle_card=card, scope=scope,
                    judgements=form.judgements(),
                    note=form.cleaned_data.get("note", ""),
                )
            except ValidationError as exc:
                # The model validates on every save, so a colour the engine
                # would not recognise arrives here rather than in a worker.
                form.add_error(None, exc.messages)
            else:
                messages.success(
                    request,
                    f"Saved. {card.front_name} will be read that way from the "
                    "next run onwards — this one does not change a result that "
                    "has already been computed.",
                )
                return redirect(reverse("simulations:tune", args=[deck.pk]))

        scope = form.data.get("scope") or DECK_SCOPE
        return render(request, "simulations/annotate.html", self._context(
            deck, card, form=form,
            annotation=services.annotation_at(deck, card, self._scope(scope)),
            scope=scope,
        ), status=400)

    def _context(self, deck, card, *, form, annotation, scope) -> dict:
        return {
            "deck": deck,
            "card": card,
            "entry": provenance.for_card(deck, card),
            "form": form,
            "annotation": annotation,
            "scope": scope,
            "sources": provenance.SOURCES,
        }

    @staticmethod
    def _scope(value) -> str:
        """A scope from the query string, or the narrowest one.

        Narrowest by default, and never `builtin`: a judgement about one deck
        that silently applied to every deck of every user would be the worst
        possible default, so an unrecognised value falls back to this deck.
        """
        return value if value in {DECK_SCOPE, USER_SCOPE} else DECK_SCOPE


class AnnotationDeleteView(DeckScopedView):
    """Throw one judgement away and go back to the derived reading."""

    def post(self, request, pk, oracle_id):
        deck = self.get_deck(request, pk)
        card = self.get_card(deck, oracle_id)
        scope = CardAnnotateView._scope(request.POST.get("scope"))

        if services.delete_annotation(deck=deck, oracle_card=card, scope=scope):
            messages.success(
                request,
                f"Removed. {card.front_name} is back to what the card data says.",
            )
        else:
            messages.info(request, "There was nothing recorded for that card.")
        return redirect(reverse("simulations:tune", args=[deck.pk]))


class PriorityEditView(DeckScopedView):
    """The deck's casting order, all of it on one screen.

    Ordered by what the engine will actually do, not by name: the page is for
    seeing that the plan is wrong, and an alphabetical list hides that
    completely. Lands are left out - the engine never casts one.
    """

    def get(self, request, pk):
        deck = self.get_deck(request, pk)
        return render(request, "simulations/priority.html", {
            "deck": deck,
            "form": PriorityForm(self._choices(deck)),
        })

    def post(self, request, pk):
        deck = self.get_deck(request, pk)
        form = PriorityForm(self._choices(deck), request.POST)

        if not form.is_valid():
            messages.error(
                request, "A priority has to be a whole number between 0 and 100."
            )
            return render(
                request,
                "simulations/priority.html",
                {"deck": deck, "form": form},
                status=400,
            )

        changed = services.set_priorities(deck=deck, priorities=form.priorities())
        messages.success(
            request,
            f"{changed} card{'' if changed == 1 else 's'} changed. "
            "The next run will use the new order."
            if changed else "Nothing changed.",
        )
        return redirect(reverse("simulations:priority", args=[deck.pk]))

    @staticmethod
    def _choices(deck) -> list[PriorityChoice]:
        """Every card the agent might actually cast, best-first.

        A land has no casting order and a card with no legal target against
        nobody never comes up, so neither belongs on a screen about what to
        cast first.
        """
        stored = services.deck_priorities(deck)
        castable = [
            reading for reading in adapter.readings(deck)
            if not reading.card.is_land and reading.card.goldfish_castable
        ]
        castable.sort(
            key=lambda reading: (-reading.effective_priority,
                                 reading.oracle_card.front_name.lower())
        )
        return [
            PriorityChoice(
                oracle_id=reading.oracle_card.pk,
                label=reading.oracle_card.front_name,
                current=stored.get(reading.oracle_card.pk),
                reading=reading,
            )
            for reading in castable
        ]
