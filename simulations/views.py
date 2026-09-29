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
from simulations import blindspots, provenance, report, review, services
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE
from simulations.forms import AnnotationForm, RunForm
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
# Two screens, and the reason they exist: a result nobody can check is a
# result nobody should believe. The tune page says what the engine reads off
# every card and where each reading came from; the card page lets a human
# disagree. (A third, the deck's casting order, went in Phase 9 C: the product
# is statistics about a deck, not steering a game, and the engine's own rule -
# cheapest first - is an answer nobody has to be asked for.)
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
    """Every card of one deck, as a grid, marked the way the deck is.

    The page that answers "why does my deck simulate like that". Cards that
    still need their owner come first - a grid sorted by name would bury them
    among thirty Swamps - and each one opens its own page.
    """

    def get(self, request, pk):
        deck = self.get_deck(request, pk)
        entries = provenance.for_deck(deck)
        spots = blindspots.find(entry.reading for entry in entries)
        questions = review.queue(deck, [entry.reading for entry in entries])
        answered = {q.oracle_card.pk for q in questions if q.answered}
        open_ids = {q.oracle_card.pk for q in questions if not q.answered}
        review.open_questions(deck)

        return render(request, "simulations/tune.html", {
            "deck": deck,
            "cards": sorted(
                (
                    {"entry": entry,
                     "open": entry.oracle_card.pk in open_ids,
                     "answered": entry.oracle_card.pk in answered}
                    for entry in entries
                ),
                key=lambda card: (not card["open"], card["entry"].name.lower()),
            ),
            "cards_total": len(entries),
            "blindspots": spots,
        })


class DeckReviewView(DeckScopedView):
    """The way in from the red marker: the first card that still needs you.

    Nothing left to answer is "Ready", said on the deck page, which is where
    the marker that led here is.
    """

    def get(self, request, pk):
        deck = self.get_deck(request, pk)
        questions = review.queue(deck)
        first = next((question for question in questions if not question.answered), None)
        if first is None:
            messages.success(request, READY)
            return redirect(deck.get_absolute_url())
        return redirect(reverse("simulations:annotate", args=[deck.pk, first.oracle_card.pk]))


#: What the deck page says once every card the engine could not read is answered.
READY = "Ready. Every card the engine could not read has an answer."


class CardAnnotateView(DeckScopedView):
    """One card: what the engine reads, and a form to disagree with it.

    A card the engine could not read is also one step of the review (Phase 9
    C2): the page says "2 of 5", and Save and next, Looks right, Skip and Back
    walk the same stable queue the red marker counts.
    """

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

        if request.POST.get("action") == "confirm":
            scope = self._scope(request.POST.get("scope"))
            services.confirm_annotation(deck=deck, oracle_card=card, scope=scope)
            messages.success(request, f"Noted: {card.front_name} looks right to you.")
            return self._onwards(request, deck, card, scope)

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
                return self._onwards(request, deck, card, scope)

        scope = self._scope(form.data.get("scope"))
        return render(request, "simulations/annotate.html", self._context(
            deck, card, form=form,
            annotation=services.annotation_at(deck, card, scope),
            scope=scope,
        ), status=400)

    def _onwards(self, request, deck, card, scope):
        """After an answer: the next card still open, else the deck, "Ready".

        A card that was never in the queue goes back to the card list, where
        its owner came from.
        """
        questions = review.queue(deck)
        ids = [question.oracle_card.pk for question in questions]
        if card.pk not in ids:
            return redirect(reverse("simulations:tune", args=[deck.pk]))
        here = ids.index(card.pk)
        # From here to the end, then round from the start: "next" is the next
        # one that still needs an answer, wherever it is.
        for question in questions[here + 1:] + questions[:here]:
            if not question.answered:
                return redirect(self._url(deck, question.oracle_card, scope))
        messages.success(request, READY)
        return redirect(deck.get_absolute_url())

    @staticmethod
    def _url(deck, oracle_card, scope) -> str:
        url = reverse("simulations:annotate", args=[deck.pk, oracle_card.pk])
        return f"{url}?scope={scope}" if scope != DECK_SCOPE else url

    def _context(self, deck, card, *, form, annotation, scope) -> dict:
        entries = provenance.for_deck(deck)
        entry = next(entry for entry in entries if entry.oracle_card.pk == card.pk)
        questions = review.queue(deck, [each.reading for each in entries])
        ids = [question.oracle_card.pk for question in questions]
        first, more = form.split(
            {gap.field for gap in entry.reading_gaps}, is_land=entry.reading.card.is_land,
        )
        context = {
            "deck": deck,
            "card": card,
            "entry": entry,
            "form": form,
            "first_fields": first,
            "more_fields": more,
            "annotation": annotation,
            "scope": scope,
            "step": None,
        }
        if card.pk in ids:
            here = ids.index(card.pk)
            context["step"] = {
                "position": here + 1,
                "total": len(questions),
                "open": sum(1 for question in questions if not question.answered),
                "back": self._url(deck, questions[here - 1].oracle_card, scope)
                if here else None,
                "skip": self._url(deck, questions[here + 1].oracle_card, scope)
                if here + 1 < len(questions) else deck.get_absolute_url(),
            }
        return context

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
