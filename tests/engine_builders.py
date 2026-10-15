"""One way for a test to set up a game board (card model, phase 4).

About twenty test modules carry a `game_with` of their own; they differ in
which zones they take and in the deck behind the game. New tests use this one.
Writing a zone directly (`game.lands = [...]`) stays valid: the game turns
whatever list it is given into its own zone, so the older helpers keep
working as they are.
"""

import random

from simulation.cards import CREATURE, Card, DeckDefinition
from simulation.game import Game

#: The card a game's deck holds when a test does not care about the deck.
FILLER = Card("Filler", 2, 0, 2, CREATURE, types=frozenset({"creature"}))

#: Zones `game_with` takes, by its keyword and the game's attribute.
ZONES = {"lands": "lands", "rocks": "rocks", "creatures": "creatures",
         "others": "other_permanents", "hand": "hand", "library": "library",
         "graveyard": "graveyard", "exiled": "exiled"}


def game_with(*, commander=None, partner=None, tapped_lands: int = 0, tapped_rocks: int = 0,
              turn: int = 0, life: int | None = None, seed: int = 1, deck=None,
              **zones) -> Game:
    """A game whose zones hold exactly the cards given, and nothing else.

    ``deck`` is the game's deck; left out, one of ``FILLER`` with the given
    commanders. The library is empty unless given.
    """
    unknown = set(zones) - set(ZONES)
    if unknown:
        raise TypeError(f"no such zone: {', '.join(sorted(unknown))}")
    if deck is None:
        deck = DeckDefinition("Test", commander, (FILLER,), partner=partner)
    game = Game(random.Random(seed), deck=deck)
    for keyword, attribute in ZONES.items():
        setattr(game, attribute, list(zones.get(keyword, ())))
    game.tapped_lands, game.tapped_rocks = tapped_lands, tapped_rocks
    game.turn = turn
    if life is not None:
        game.life = life
    return game


def put(game: Game, zone: str, *cards) -> Game:
    """Add cards to the end of one of `game_with`'s zones."""
    getattr(game, ZONES[zone]).extend(cards)
    return game
