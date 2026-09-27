"""The playtest board.

Every screen here is scoped to `owner=self.request.user` at the source, like the
deck and simulation views: a missing filter has to produce a missing row, never
somebody else's game.

**Forms first, htmx second.** Each action is a real `<form method="post">`; the
submit button carries `hx-post` and swaps the board fragment in place. With
JavaScript off the same POST redirects back to the board and the page
re-renders. One code path, one source of truth, and the Django test client
exercises every action without a browser.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, View

from decks.models import Deck
from playtest import services
from playtest.forms import ActionForm, ForkForm, StartForm
from playtest.models import PlaytestSession
from simulation import actions

#: The board fragment htmx swaps in. The whole page includes it too, so the two
#: can never show different boards.
BOARD_FRAGMENT = "playtest/_board.html"


class OwnedSessionsMixin(LoginRequiredMixin):
    """Scope every session lookup to the signed-in user."""

    def get_queryset(self):
        return PlaytestSession.objects.filter(
            owner=self.request.user).select_related("deck")


def card_images(session: PlaytestSession) -> dict:
    """Card name -> Scryfall image URL, for the board.

    The engine's `Card` carries no art: it is a set of numbers, and giving it a
    URL would put a web address inside the simulation. So the picture is looked
    up here, by name, from the deck the session belongs to. The URL is
    hot-linked and never stored as bytes, which is what Scryfall asks for.
    """
    pairs = dict(session.deck.entries.values_list(
        "oracle_card__front_name", "oracle_card__image_uri"))
    if session.deck.commander_id:
        pairs[session.deck.commander.front_name] = session.deck.commander.image_uri
    return pairs


def card_keywords(session: PlaytestSession) -> dict:
    """Card name -> printed keywords, for the board.

    Looked up beside the art and for the same reason: they are properties of
    the printed card, not of the simulation, and the engine's `Card` is right
    not to carry them. **Nothing here reaches the engine.** A goldfish has no
    opponent, so flying, deathtouch and menace change nothing that is being
    simulated - but a person reading their own board still wants to see them,
    which is the whole argument for showing them and the whole limit on it.
    """
    pairs = dict(session.deck.entries.values_list(
        "oracle_card__front_name", "oracle_card__keywords"))
    if session.deck.commander_id:
        pairs[session.deck.commander.front_name] = session.deck.commander.keywords
    return pairs


#: The zones the board draws, in reading order, with the words a player uses.
BOARD_ZONES = (
    (actions.LANDS, "Lands"),
    (actions.ROCKS, "Mana artifacts"),
    (actions.CREATURES, "Creatures"),
    (actions.OTHER, "Other permanents"),
    (actions.GRAVEYARD, "Graveyard"),
    (actions.EXILED, "Exile"),
)


def _tiles(cards, images, keywords=None, *, castable=(), playable=()) -> list[dict]:
    """One zone, as the flat rows a template can loop over without thinking.

    The alternative is a custom filter for `images[card.name]` and another for
    `index in castable`. Both would put logic in the template, where it cannot
    be tested.
    """
    return [
        {
            "index": index,
            "card": card,
            "image": images.get(card.name, ""),
            "keywords": (keywords or {}).get(card.name) or [],
            "castable": index in castable,
            "playable": index in playable,
        }
        for index, card in enumerate(cards)
    ]


def board_context(session: PlaytestSession, game) -> dict:
    """Everything the board template needs, for a page and for a fragment."""
    live = list(services.live_actions(session))
    images = card_images(session)
    keywords = card_keywords(session)
    legal = actions.legal_actions(game)

    return {
        "session": session,
        "deck": session.deck,
        "game": game,
        "hand": _tiles(
            game.hand, images, keywords,
            castable={a.index for a in legal if isinstance(a, actions.CastSpell)},
            playable={a.index for a in legal if isinstance(a, actions.PlayLand)},
        ),
        "board": [
            {"name": name, "label": label,
             "tiles": _tiles(getattr(game, name), images, keywords)}
            for name, label in BOARD_ZONES
        ],
        "commander": session.deck.commander,
        "commander_castable": any(
            isinstance(a, actions.CastCommander) for a in legal),
        "phases": actions.PHASES,
        "history": live,
        "can_undo": bool(live),
        "can_redo": session.actions.filter(undone=True).exists(),
        "move_zones": actions.ZONES,
    }


def render_board(request, session: PlaytestSession, game):
    """The fragment for htmx, the whole page for everyone else."""
    context = board_context(session, game)
    if request.headers.get("HX-Request"):
        return render(request, BOARD_FRAGMENT, context)
    return redirect(session.get_absolute_url())


class StartView(LoginRequiredMixin, View):
    """Open a session on one deck."""

    def post(self, request, deck_id):
        deck = get_object_or_404(Deck, pk=deck_id, owner=request.user)
        if not deck.entries.exists():
            messages.error(request, "There is nothing in this deck to play yet.")
            return redirect(deck.get_absolute_url())

        form = StartForm(request.POST)
        if not form.is_valid():
            messages.error(request, "That is not a game this application deals.")
            return redirect(deck.get_absolute_url())

        session = services.start(
            deck, request.user,
            seed=form.cleaned_data.get("seed") or None,
            on_the_play=form.cleaned_data.get("on_the_play", True),
        )
        return redirect(session.get_absolute_url())


class BoardView(OwnedSessionsMixin, DetailView):
    """The game, as it stands."""

    template_name = "playtest/detail.html"
    context_object_name = "session"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(board_context(self.object, services.state(self.object)))
        return context


class ActView(OwnedSessionsMixin, View):
    """Carry out one action."""

    def post(self, request, pk):
        session = get_object_or_404(self.get_queryset(), pk=pk)
        form = ActionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("That is not an action.")

        try:
            game = services.record(session, form.action())
        except actions.IllegalAction as exc:
            messages.error(request, str(exc))
            game = services.state(session)
        except services.PlaytestFull as exc:
            messages.error(request, str(exc))
            game = services.state(session)

        return render_board(request, session, game)


class UndoView(OwnedSessionsMixin, View):
    def post(self, request, pk):
        session = get_object_or_404(self.get_queryset(), pk=pk)
        return render_board(request, session, services.undo(session))


class RedoView(OwnedSessionsMixin, View):
    def post(self, request, pk):
        session = get_object_or_404(self.get_queryset(), pk=pk)
        return render_board(request, session, services.redo(session))


class ForkView(OwnedSessionsMixin, View):
    """Branch this game and go on playing the branch."""

    def post(self, request, pk):
        session = get_object_or_404(self.get_queryset(), pk=pk)
        form = ForkForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("That is not a point to branch at.")
        branch = services.fork(session, form.cleaned_data["seq"])
        messages.success(
            request,
            "Branched. The original is untouched and still in your playtests.")
        return redirect(branch.get_absolute_url())
