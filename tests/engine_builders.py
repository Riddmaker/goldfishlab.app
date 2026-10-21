"""One way for a test to set up a game board (card model, phase 4).

About twenty test modules carry a `game_with` of their own; they differ in
which zones they take and in the deck behind the game. New tests use this one.
Writing a zone directly (`game.lands = [...]`) stays valid: the game turns
whatever list it is given into its own zone, so the older helpers keep
working as they are. Marks are kept by permanent number since PR 17: `marks`
sets them by card name, and `simulation.trace.named` reads them so.
"""

import dataclasses
import random

from simulation import build
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
    tap(game, lands=tapped_lands, rocks=tapped_rocks)
    game.turn = turn
    if life is not None:
        game.life = life
    return game


def tap(game: Game, *, lands: int | None = None, rocks: int | None = None) -> Game:
    """Exactly the first ``lands`` lands and ``rocks`` rocks tapped, the rest
    untapped; a zone not named stays as it is."""
    for zone, count in ((game.lands, lands), (game.rocks, rocks)):
        if count is not None:
            for at, permanent in enumerate(zone.permanents):
                permanent.tapped = at < count
    return game


def hold(game: Game, *cards) -> Game:
    """These permanents stay tapped through the untap step (Mana Vault)."""
    for card in cards:
        game.where[id(card)].held = True
    return game


def put(game: Game, zone: str, *cards) -> Game:
    """Add cards to the end of one of `game_with`'s zones."""
    getattr(game, ZONES[zone]).extend(cards)
    return game


def marks(game: Game, **by_name) -> Game:
    """Set marks of permanents on the board by card name, as
    `simulation.trace.named` reads them: ``marks(game, arrived=["Bear"],
    attached={"Sword": "Bear"})``. A name is the first permanent of that
    name on the battlefield; a name of none is kept as a number nobody has,
    as for a permanent that left."""
    by_card = {}
    for zone in (game.lands, game.rocks, game.creatures, game.other_permanents):
        for permanent in zone.permanents:
            by_card.setdefault(permanent.card.name, permanent.pid)

    def pid(name: str) -> int:
        if name not in by_card:
            by_card[name] = game.next_pid()
            game.pid_names[by_card[name]] = name
        return by_card[name]

    for attribute, value in by_name.items():
        if isinstance(value, dict):
            setattr(game, attribute, {
                pid(name): pid(item) if attribute == "attached" else item
                for name, item in value.items()})
        else:
            setattr(game, attribute, [pid(name) for name in value])
    return game


def rules(card: Card, **fields) -> tuple[tuple, tuple]:
    """The card's blocks of the forms ``fields`` make, and the blocks they
    make (`simulation.build.blocks`): equal when the card was made with
    them. Name every field that shares a form - a ritual's gain and colour,
    an altar's cost and its mana. Nothing turns blocks back into fields
    since the decoder retired (card model phase 5), so a test compares
    blocks, or reads the views (`Card.play`, `Card.turn`)."""
    made = build.blocks(**fields)
    forms = {ability.form for ability in made}
    if not forms:
        raise ValueError("these fields make no block: ask the card's view")
    return tuple(each for each in card.abilities if each.form in forms), made


def with_rules(card: Card, **fields) -> Card:
    """The card with the blocks ``fields`` make in place of its blocks of the
    same forms (`simulation.build.blocks`). Name every field that shares a
    form: nothing reads the card's other ones back."""
    made = build.blocks(**fields)
    forms = {ability.form for ability in made}
    kept = tuple(each for each in card.abilities if each.form not in forms)
    return dataclasses.replace(card, abilities=kept + made)
