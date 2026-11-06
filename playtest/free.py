"""Free mode: the board as a kitchen table (Paket 4, PR 18).

A strict session plays the engine's turn: phases, a pool, a cost for every
card. A free one does what a player with a deck in their hands does - draws,
puts a card down, starts a new turn - and the application blocks none of it.
What the rules would not have allowed is **said, not refused**: the journal
notes it as an unusual move.

Three decisions hold this module up:

1. **The engine is unchanged.** These actions subclass `simulation.actions.
   Action` and are stored and replayed like the engine's own, but they live
   here and only a free session accepts them. A free play puts the card where
   it belongs and nothing more - no cost, no trigger, no search - so the
   engine's mechanics never run on a game they were not written for.
2. **A new hand is a new deal in the same session**, its seed stored with the
   action. Undo takes it back and the old hand returns, which a new session
   could not do.
3. **The journal is pure.** `journal()` reads what happened (`Step`s, taken
   during a replay) and nothing else, so its guesses can be tested without a
   database, and a guess the player disagrees with changes nothing stored.
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext, ngettext

from simulation import actions
from simulation.cards import ARTIFACT, CREATURE, ENCHANTMENT, PLANESWALKER, ROCK

# --- The actions -----------------------------------------------------------

#: Where a card played for free goes. A land is a land; anything not here -
#: an instant, a sorcery, a ritual - is cast and goes to the graveyard.
ZONE_BY_KIND = {
    ROCK: actions.ROCKS,
    CREATURE: actions.CREATURES,
    ENCHANTMENT: actions.OTHER,
    ARTIFACT: actions.OTHER,
    PLANESWALKER: actions.OTHER,
}


def zone_for(card) -> str:
    """The zone a card lands in when it is simply put down."""
    if card.is_land:
        return actions.LANDS
    return ZONE_BY_KIND.get(card.kind, actions.GRAVEYARD)


@dataclass(frozen=True)
class FreePlay(actions.Action):
    """Put the card at ``index`` in hand where it belongs, paying nothing."""

    kind = "free_play"
    index: int = 0

    def run(self, game, policy) -> None:
        card = actions._take(game, actions.HAND, self.index)
        actions.zone_of(game, zone_for(card)).append(card)
        if card.is_land:
            game.land_drop_used = True
        game.note(f"{card.name} (free)")


@dataclass(frozen=True)
class FreeCommander(actions.Action):
    """Put a commander onto the battlefield, paying nothing.

    Counted as a cast, so the journal can say what the next one would cost.
    One copy only: a second Atraxa is not a move, it is a bug.
    """

    kind = "free_commander"
    index: int = 0

    def run(self, game, policy) -> None:
        commanders = game.deck.commanders
        if not 0 <= self.index < len(commanders):
            raise actions.IllegalAction("this deck has no such commander")
        commander = commanders[self.index]
        if game.has(commander.name):
            raise actions.IllegalAction(f"{commander.name} is already on the battlefield")
        if commander is game.deck.partner:
            game.partner_casts += 1
        else:
            game.commander_casts += 1
        game.creatures.append(commander)
        game.note(f"{commander.name} (commander, free)")


@dataclass(frozen=True)
class NextTurn(actions.Action):
    """Untap and draw for the turn - and nothing else happens on its own."""

    kind = "next_turn"

    def run(self, game, policy) -> None:
        game.turn = max(game.turn, 1) + 1
        game.lands.untap()
        game.rocks.untap()
        game.land_drop_used = False
        game.tapped_creatures = []
        game.arrived = []
        game.note(f"--- Turn {game.turn} ---")
        game.draw(1)


@dataclass(frozen=True)
class NewHand(actions.Action):
    """Shuffle everything back and deal seven again.

    The seed is part of the action, chosen by the view, so a replay deals the
    same seven and undo brings the hand before it back. `playtest.services`
    carries it out: it replaces the game rather than changing it.
    """

    kind = "new_hand"
    seed: int = 0

    def run(self, game, policy) -> None:  # pragma: no cover - see services.apply
        raise actions.IllegalAction("a new hand is dealt by the session")


#: The free actions, by stored kind.
BY_KIND = {action.kind: action for action in (FreePlay, FreeCommander, NextTurn, NewHand)}

#: What a free session may post: its own actions and a plain draw.
ALLOWED = frozenset(BY_KIND) | {actions.Draw.kind}


def from_row(kind: str, payload: dict) -> actions.Action:
    """A stored action, free or the engine's."""
    if kind in BY_KIND:
        return BY_KIND[kind](**payload)
    return actions.from_row(kind, payload)


# --- What happened, for the journal ----------------------------------------

DEAL, DRAW, LAND, PLAY, COMMANDER, TURN = "deal", "draw", "land", "play", "commander", "turn"


@dataclass(frozen=True)
class Step:
    """One action, as the journal reads it.

    ``cost`` is what the card would have cost (a commander with its tax),
    ``sources`` the lands and mana rocks out when it was played.
    """

    what: str
    cards: tuple[str, ...] = ()
    cost: int = 0
    sources: int = 0


def step(game, action) -> Step | None:
    """What ``action`` is about to do to ``game``, read before it runs.

    A draw or a new turn names its cards afterwards: `drew()` fills them in.
    """
    if isinstance(action, FreePlay):
        card = actions._at(game, actions.HAND, action.index)
        return Step(LAND if card.is_land else PLAY, (card.name,), card.mv, _sources(game))
    if isinstance(action, FreeCommander):
        commanders = game.deck.commanders
        commander = commanders[action.index]
        casts = (game.partner_casts if commander is game.deck.partner
                 else game.commander_casts)
        return Step(COMMANDER, (commander.name,), commander.mv + 2 * casts, _sources(game))
    if isinstance(action, NewHand):
        return Step(DEAL)
    if isinstance(action, NextTurn):
        return Step(TURN)
    if isinstance(action, actions.Draw):
        return Step(DRAW)
    return None


def drew(before: Step, game, hand_before: int) -> Step:
    """A draw or a new turn, with the cards it brought."""
    return Step(before.what, tuple(card.name for card in game.hand[hand_before:]))


def _sources(game) -> int:
    return len(game.lands) + len(game.rocks)


@dataclass
class Turn:
    """One turn of the journal: its number, whether it was guessed, its lines."""

    number: int
    guessed: bool = False
    lines: list = field(default_factory=list)


@dataclass
class Journal:
    """The journal of the hand being played: its turns, the unusual moves."""

    turns: list
    unusual: int = 0

    @property
    def now(self) -> Turn:
        return self.turns[-1]


def journal(steps, *, on_the_play: bool) -> Journal:
    """The turns the steps were most likely played in, and what was unusual.

    Until the player says "next turn", the turns are **guessed**: a second land,
    or a draw past the turn's one (none in the first turn on the play), means a
    new turn has begun. Once they have said it, they are trusted, and a second
    land is an unusual move instead. A card that costs more than the lands and
    rocks out is unusual either way. A new hand starts the journal again.
    """
    book = Journal([Turn(1, lines=[_line(gettext("Seven cards dealt."))])])
    told = False
    lands = draws = 0

    def new_turn(*, guessed: bool) -> None:
        nonlocal lands, draws
        book.turns.append(Turn(book.now.number + 1, guessed=guessed))
        lands = draws = 0

    for each in steps:
        if each.what == DEAL:
            book = Journal([Turn(1, lines=[_line(gettext("New hand: seven fresh cards."))])])
            told = False
            lands = draws = 0
        elif each.what == TURN:
            told = True
            new_turn(guessed=False)
            draws = 1
            _drew(book, each)
        elif each.what == DRAW:
            allowed = 0 if book.now.number == 1 and on_the_play else 1
            if not told and draws >= allowed:
                new_turn(guessed=True)
            draws += 1
            _drew(book, each)
        elif each.what == LAND:
            if lands and told:
                _unusual(book, gettext("A second land this turn."))
            elif lands:
                new_turn(guessed=True)
            lands += 1
            book.now.lines.append(_line(gettext("Land: %(card)s.") % {"card": each.cards[0]}))
        elif each.what in (PLAY, COMMANDER):
            text = (gettext("Commander: %(card)s.") if each.what == COMMANDER
                    else gettext("Played %(card)s.")) % {"card": each.cards[0]}
            book.now.lines.append(_line(text))
            if each.cost > each.sources:
                _unusual(book, ngettext(
                    "%(card)s costs %(cost)s, with %(count)s mana source out.",
                    "%(card)s costs %(cost)s, with %(count)s mana sources out.",
                    each.sources) % {"card": each.cards[0], "cost": each.cost,
                                     "count": each.sources})
    return book


def _line(text: str, *, unusual: bool = False) -> dict:
    return {"text": text, "unusual": unusual}


def _drew(book: Journal, each: Step) -> None:
    if each.cards:
        book.now.lines.append(_line(gettext("Drew %(cards)s.") % {"cards": ", ".join(each.cards)}))
    else:
        book.now.lines.append(_line(gettext("The library is empty.")))


def _unusual(book: Journal, text: str) -> None:
    book.now.lines.append(_line(text, unusual=True))
    book.unusual += 1
