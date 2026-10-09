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
from django.utils.translation import gettext, gettext_noop
from django.views.generic import DetailView, View

from decks.models import Deck
from playtest import services
from playtest.forms import ActionForm, ForkForm, StartForm
from playtest.models import PlaytestSession
from simulation import actions
from simulation.manacost import COLORLESS, COLORS

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


#: The battlefield, in rows, with the words a player uses. Creatures and the
#: other permanents share a row: a goldfish board rarely holds more than a
#: handful of either, and two half-empty rows read as a gap, not as order.
BATTLEFIELD_ROWS = (
    ("lands", gettext_noop("Lands"), (actions.LANDS,)),
    ("mana", gettext_noop("Mana sources"), (actions.ROCKS,)),
    ("permanents", gettext_noop("Permanents"), (actions.CREATURES, actions.OTHER)),
)

#: The engine's phases and action kinds are keys; these are their words
#: (phase 12). What happened in the game - the engine's log and its refusals -
#: stays English, like the card names in it (decided 2026-10-03).
PHASE_NAMES = {
    actions.DRAW: gettext_noop("Draw"),
    actions.MAIN1: gettext_noop("Main 1"),
    actions.COMBAT: gettext_noop("Combat"),
    actions.MAIN2: gettext_noop("Main 2"),
    actions.END: gettext_noop("End"),
}
ACTION_NAMES = {
    "begin_turn": gettext_noop("Start of turn"),
    "play_land": gettext_noop("Land played"),
    "open_main": gettext_noop("Main phase"),
    "cast_spell": gettext_noop("Spell cast"),
    "cast_commander": gettext_noop("Commander cast"),
    "end_step": gettext_noop("End step"),
    "advance_phase": gettext_noop("Next phase"),
    "mulligan": gettext_noop("Mulligan"),
    "keep_hand": gettext_noop("Hand kept"),
    "draw": gettext_noop("Card drawn"),
    "set_life": gettext_noop("Life changed"),
    "move_card": gettext_noop("Card moved"),
    "tap_permanent": gettext_noop("Permanent tapped"),
    "activate_search": gettext_noop("Land search activated"),
    "activate_lander": gettext_noop("Lander activated"),
}

#: The message tag that marks an engine refusal, so the page can say it is
#: English (`lang="en"`) - the engine names cards and costs in its own words.
ENGINE_TAG = "engine"

#: The order a mana pip is drawn in - the colour pie, then colourless.
PIP_ORDER = (*COLORS, COLORLESS)


def _tiles(cards, images, keywords=None, *, castable=(), playable=(),
           x_max=None, zone="", activatable=()) -> list[dict]:
    """One zone, as the flat rows a template can loop over without thinking.

    The alternative is a custom filter for `images[card.name]` and another for
    `index in castable`. Both would put logic in the template, where it cannot
    be tested. `x_max` gives a castable card with {X} the largest X the pool
    pays (P19 R9), which the board fills in for the player to change.
    `activatable` are the permanents in `zone` whose land search the pool
    pays for now (P19 R14).
    """
    return [
        {
            "index": index,
            "card": card,
            "image": images.get(card.name, ""),
            "keywords": (keywords or {}).get(card.name) or [],
            "castable": index in castable,
            "playable": index in playable,
            "x_max": x_max(card) if x_max and index in castable and card.x_count else None,
            "zone": zone,
            "activatable": index in activatable,
        }
        for index, card in enumerate(cards)
    ]


def pips(pool) -> list[tuple[str, int]]:
    """The floating mana, colour by colour, in the order a player reads it.

    Empty before the main phase opens the pool, which is when the board has
    nothing to show rather than a row of noughts.
    """
    if pool is None:
        return []
    held = pool.by_color()
    return [(color, held[color]) for color in PIP_ORDER if held.get(color)]


def deciding(game, live) -> bool:
    """Is the opening hand still being decided?

    The engine has no "kept" flag - a kept hand before turn one looks exactly
    like one not yet kept - so it is read off the actions: only mulligans so
    far, and no turn begun, means Mulligan and Keep are the two things to do.
    """
    return game.phase is None and game.turn == 0 and all(
        row.kind == actions.Mulligan.kind for row in live)


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
            x_max=(lambda card: game.max_x(card, game.pool))
            if getattr(game, "pool", None) is not None else None,
        ),
        "battlefield": [
            {"key": key, "label": gettext(label),
             "tiles": [tile for zone in zones
                       for tile in _tiles(getattr(game, zone), images, keywords, zone=zone,
                                          activatable={a.index for a in legal
                                                       if isinstance(a, actions.ActivateSearch)
                                                       and a.zone == zone})]}
            for key, label, zones in BATTLEFIELD_ROWS
        ],
        "landers": getattr(game, "landers", 0),
        "lander_ready": any(isinstance(a, actions.ActivateLander) for a in legal),
        "graveyard": _tiles(game.graveyard, images),
        "exiled": _tiles(game.exiled, images),
        "pips": pips(getattr(game, "pool", None)),
        "deciding": deciding(game, live),
        "to_bottom": game.cards_to_bottom(game.mulligans),
        "commander": session.deck.commander,
        "commander_castable": any(
            isinstance(a, actions.CastCommander) for a in legal),
        "commander_out": bool(session.deck.commander_id)
        and game.has(session.deck.commander.front_name),
        "phases": [(phase, gettext(PHASE_NAMES[phase])) for phase in actions.PHASES],
        "history": [(row.seq, gettext(ACTION_NAMES[row.kind]) if row.kind in ACTION_NAMES
                     else row.kind) for row in live],
        "can_undo": bool(live),
        "can_redo": session.actions.filter(undone=True).exists(),
    }


def render_board(request, session: PlaytestSession, game):
    """The fragment for htmx, the whole page for everyone else."""
    context = board_context(session, game)
    if request.headers.get("HX-Request"):
        # The page shows messages above its content; a swapped fragment has no
        # page around it, so it shows them itself - or "costs 4, the floating
        # mana is 2" is said to nobody until the next reload.
        context["board_messages"] = True
        return render(request, BOARD_FRAGMENT, context)
    return redirect(session.get_absolute_url())


class StartView(LoginRequiredMixin, View):
    """Open a session on one deck."""

    def post(self, request, deck_id):
        deck = get_object_or_404(Deck, pk=deck_id, owner=request.user)
        if not deck.entries.exists():
            messages.error(request, gettext("There is nothing in this deck to play yet."))
            return redirect(deck.get_absolute_url())

        form = StartForm(request.POST)
        if not form.is_valid():
            messages.error(request, gettext("That is not a game this application deals."))
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
            messages.error(request, str(exc), extra_tags=ENGINE_TAG)
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
            gettext("Branched. The original is untouched and still in your playtests."))
        return redirect(branch.get_absolute_url())
