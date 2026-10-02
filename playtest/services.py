"""Starting, replaying and branching a playtest.

Everything the views do goes through here, so that "what the game currently
looks like" has exactly one answer. The rule the whole module is built to keep:

    **the actions are the truth, the cached state is only ever a shortcut.**

Any function that changes the action list clears the cache before it returns. A
cache that can disagree with a replay is worse than no cache at all - the
disagreement is what a player would report as "it forgot my turn", and it would
not reproduce.

This module *does* import Django and the engine, like
`simulations/engine/adapter.py`. The engine still imports no Django.
"""

import random
import secrets
from dataclasses import asdict

from django.db import transaction
from django.db.models import Max

from playtest.models import PlaytestAction, PlaytestSession
from simulation import actions, serial
from simulation.game import STARTING_HAND_SIZE, Game
from simulations.engine import adapter

#: The most actions one session may hold. A playtest is a few dozen; anything
#: past this is a loop somewhere, and a replay is only cheap while the list is
#: short.
MAX_ACTIONS = 2000


class PlaytestFull(RuntimeError):
    """The session has taken as many actions as it is allowed."""


# --- Starting --------------------------------------------------------------

@transaction.atomic
def start(deck, owner, *, seed: int | None = None,
          on_the_play: bool = True) -> PlaytestSession:
    """Open a session on a deck, with its opening seven already drawn.

    The deck is converted **once**, here, and the reading is frozen into the
    session. The coverage that reading came with is frozen with it, because a
    board built from a deck the engine only half understands must not look like
    a board built from one it fully does.
    """
    conversion = adapter.convert(deck)
    return PlaytestSession.objects.create(
        deck=deck,
        owner=owner,
        deck_snapshot=serial.dump_deck(conversion.definition),
        seed=secrets.randbelow(2**62) if seed is None else seed,
        on_the_play=on_the_play,
        gaps=[asdict(gap) for gap in conversion.gaps],
        cards_total=conversion.cards_total,
        cards_with_gaps=conversion.cards_with_gaps,
        copies_total=conversion.copies_total,
        copies_unreadable=conversion.copies_unreadable,
    )


def _base_game(session: PlaytestSession) -> Game:
    """The game before any action: the seeded shuffle, seven cards drawn.

    The opening seven are not an action. They are implied by the seed, the same
    way the shuffle is - a session that stored them would let two replays of
    the same seed disagree.
    """
    deck = serial.load_deck(session.deck_snapshot)
    game = Game(random.Random(session.seed), on_the_play=session.on_the_play,
                deck=deck)
    game.draw(STARTING_HAND_SIZE)
    game.first_hand_lands = sum(1 for card in game.hand if card.is_land)
    return game


# --- Reading the current state --------------------------------------------

def live_actions(session: PlaytestSession):
    """The actions that count, in order. Undone ones are skipped, not deleted."""
    return session.actions.filter(undone=False).order_by("seq")


def state(session: PlaytestSession) -> Game:
    """What the board looks like right now.

    Served from `cached_state` when it is exactly current, and replayed from
    the actions otherwise. The cache is keyed on the last live action's `seq`,
    so a stale one can be recognised rather than trusted.
    """
    rows = list(live_actions(session))
    last_seq = rows[-1].seq if rows else 0

    if session.cached_seq == last_seq and session.cached_state:
        return serial.load_game(session.cached_state,
                                serial.load_deck(session.deck_snapshot))

    game = _base_game(session)
    for row in rows:
        actions.apply(game, row.as_action(), None)

    _remember(session, game, last_seq)
    return game


def _remember(session: PlaytestSession, game: Game, seq: int) -> None:
    """Store a replay so the next request does not have to repeat it."""
    session.cached_state = serial.dump_game(game)
    session.cached_seq = seq
    session.save(update_fields=["cached_state", "cached_seq", "updated_at"])


# --- Doing something -------------------------------------------------------

@transaction.atomic
def record(session: PlaytestSession, action: actions.Action) -> Game:
    """Carry out one action and write it down.

    The action is applied to the *current* state, which is what makes an
    illegal one fail before anything is stored: `apply` raises, the transaction
    rolls back, and the session is untouched.

    Anything still sitting in the redo queue is dropped, because a new action
    branches away from it. That is what `fork` is for, and a redo that survived
    a divergent action would replay into a different game than it was recorded
    in.
    """
    game = state(session)
    actions.apply(game, action, None)

    session.actions.filter(undone=True).delete()
    seq = (session.actions.aggregate(highest=Max("seq"))["highest"] or 0) + 1
    if seq > MAX_ACTIONS:
        raise PlaytestFull(
            f"a session holds at most {MAX_ACTIONS} actions; start a new one")

    PlaytestAction.objects.create(
        session=session, seq=seq, kind=action.kind, payload=action.payload(),
    )
    _remember(session, game, seq)
    return game


@transaction.atomic
def undo(session: PlaytestSession, count: int = 1) -> Game:
    """Take back the last `count` actions."""
    tail = list(live_actions(session).reverse()[:count])
    if tail:
        session.actions.filter(pk__in=[row.pk for row in tail]).update(undone=True)
        session.invalidate()
        session.save(update_fields=["cached_state", "cached_seq", "updated_at"])
    return state(session)


@transaction.atomic
def redo(session: PlaytestSession, count: int = 1) -> Game:
    """Put back what `undo` took, oldest first."""
    queued = list(session.actions.filter(undone=True).order_by("seq")[:count])
    if queued:
        session.actions.filter(pk__in=[row.pk for row in queued]).update(undone=False)
        session.invalidate()
        session.save(update_fields=["cached_state", "cached_seq", "updated_at"])
    return state(session)


@transaction.atomic
def fork(session: PlaytestSession, seq: int) -> PlaytestSession:
    """A new session holding this one's actions up to and including `seq`.

    "Replay turn three differently" - the reason the state is a list of actions
    rather than a blob. The snapshot and the seed come along unchanged, so the
    fork is the same game up to the point it branches.
    """
    copy = PlaytestSession.objects.create(
        deck=session.deck,
        owner=session.owner,
        deck_snapshot=session.deck_snapshot,
        seed=session.seed,
        on_the_play=session.on_the_play,
        gaps=session.gaps,
        cards_total=session.cards_total,
        cards_with_gaps=session.cards_with_gaps,
        copies_total=session.copies_total,
        copies_unreadable=session.copies_unreadable,
        forked_from=session,
        forked_at_seq=seq,
    )
    PlaytestAction.objects.bulk_create([
        PlaytestAction(session=copy, seq=row.seq, kind=row.kind,
                       payload=row.payload)
        for row in live_actions(session).filter(seq__lte=seq)
    ])
    return copy
