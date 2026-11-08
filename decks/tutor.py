"""The hand tutor: "Which plan does this hand have?" (Themen-Doc, Paket 5, PR 21).

A seven from the player's own deck, and what it plays. Rules, no model, the
same principle as Combos & engines: every sentence comes from the deck's
themes (`decks.themes`), the engines the deck page measures
(`decks.engine_watch`) and the cards in the hand, and the same hand always
gives the same answer.

- The hand is a deal seed, like the playtest's (`playtest.services`): the
  deck in the seed's shuffle, seven cards drawn. Nothing is stored.
- `measure` plays that hand `GAMES` times (`Game.take_given_hand`: kept, no
  mulligan, the rest of the deck shuffled anew each game, the games' seed
  taken from the deal's) and counts in how many games each engine runs by
  turn `engine_watch.BY_TURN`. About a second on the short worker
  (`decks.tasks.read_hand`); the answer waits in the cache (`cached`).
- `explain` turns the counts into the page: per theme the cards in hand, the
  ones playable early, the share with this hand beside the deck's usual one
  (its last simulation), and one plan. The deck's strongest theme leads.

The plan (`Reading.plan`) is the engine running most often with this hand.
Under `NO_PLAN` percent nothing counts as a plan. Within `CLOSE` points of
the best, an engine counts as the plan too: 300 games cannot tell them apart,
so a guess of either is right.
"""

import random
from dataclasses import dataclass, field

from django.core.cache import cache
from django.utils.translation import gettext, ngettext

from cards import themes
from cards.names import front_face
from core.l10n import percent
from decks import engine_watch
from decks.themes import deck_reading, theme_cards
from simulation import ENGINE_VERSION, analysis
from simulation.game import STARTING_HAND_SIZE, Game
from simulations.engine import adapter

#: How often one hand is played. 300 games put a share within about six
#: points (95 %): enough to say which plan a hand leans to.
GAMES = 300
TURNS = engine_watch.BY_TURN
#: Under this share by turn six an engine is not this hand's plan.
NO_PLAN = 15
#: Engines this close to the best (points) are the plan as well.
CLOSE = 5
#: A card is playable early when it costs at most this, and at most the
#: mana the hand's lands and accelerants make.
EARLY = 3

#: The answer a guess gives for "no plan yet".
NONE = "none"

CACHE_TTL = 24 * 60 * 60
PENDING_SECONDS = 60


def deal(definition, seed: int) -> list[str]:
    """The seven a seed deals, as the playtest deals it."""
    game = Game(random.Random(seed), deck=definition)
    game.draw(STARTING_HAND_SIZE)
    return [card.name for card in game.hand]


def _games_seed(seed: int) -> int:
    """The games' own seed: another shuffle than the deal's, the same for
    the same hand."""
    return seed ^ 0x5EED_7070


def play(definition, hand, watches, *, seed: int, on_the_play: bool = True,
         games: int = GAMES) -> dict[str, int]:
    """In how many of `games` each watched engine runs by turn `TURNS`,
    keyed by theme. Pure: no database."""
    rng = random.Random(_games_seed(seed))
    running = {watch.key[len(engine_watch.PREFIX):]: 0 for watch in watches}
    for _ in range(games):
        measured = analysis.simulate_game(rng, on_the_play=on_the_play, turns=TURNS,
                                          deck=definition, watch=watches, hand=hand)
        for key, turn in measured["combos"].items():
            if turn:
                running[key[len(engine_watch.PREFIX):]] += 1
    return running


def key(deck, seed: int, on_the_play: bool) -> str:
    """The cache key of one hand's answer: the deck as it is now (its cards,
    commander and corrections, `simulations.summary.fingerprint`), the
    engine that played it."""
    from simulations.summary import fingerprint

    return f"tutor:v1:{ENGINE_VERSION}:{deck.pk}:{fingerprint(deck)}:{seed}:{int(on_the_play)}"


def cached(deck, seed: int, on_the_play: bool) -> dict | None:
    return cache.get(key(deck, seed, on_the_play))


def ask(deck, seed: int, on_the_play: bool) -> None:
    """Have the worker play the hand, once: a second ask while the first is
    pending does nothing."""
    from decks.tasks import read_hand

    if cache.add(key(deck, seed, on_the_play) + ":pending", 1, PENDING_SECONDS):
        read_hand.delay(str(deck.pk), seed, on_the_play)


def measure(deck, seed: int, on_the_play: bool) -> dict:
    """Deal the hand, play it `GAMES` times, keep the answer in the cache."""
    definition = adapter.convert(deck).definition
    hand = deal(definition, seed)
    watches = engine_watch.watches(deck)
    keeper = Game(random.Random(0), deck=definition)
    cards = [next(card for card in definition.library if card.name == name) for name in hand]
    answer = {
        "hand": hand,
        "games": GAMES if watches else 0,
        "running": play(definition, hand, watches, seed=seed, on_the_play=on_the_play)
        if watches else {},
        "keeps": keeper.keeps_free_seven(cards),
        "lands": sum(1 for card in cards if card.is_land),
        "mana": sum(1 for card in cards if card.is_land or card.is_accelerant),
        "costs": {card.name: _mana_value(card) for card in cards if not card.is_land},
    }
    cache.set(key(deck, seed, on_the_play), answer, CACHE_TTL)
    return answer


def _mana_value(card) -> int:
    cost = card.mana_cost
    return (cost.generic + cost.colorless + sum(count for _color, count in cost.pips)
            + len(cost.hybrid) + len(cost.phyrexian))


# --- The page ------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One theme beside the hand."""

    theme: str
    label: str
    #: Its cards in the hand, and those of them playable early.
    cards: tuple[str, ...]
    early: tuple[str, ...]
    #: Whole percents, as the page writes them; None: not measured.
    share: str | None
    usual: str | None
    value: float | None


@dataclass(frozen=True)
class Reading:
    hand: tuple[str, ...]
    rows: tuple[Row, ...]
    #: The deck's strongest theme, or None for a deck without one.
    main: Row | None
    #: The themes that count as this hand's plan; empty: no plan yet.
    plan: tuple[str, ...]
    keeps: bool
    lands: int
    measured: bool
    sentences: tuple[str, ...] = field(default_factory=tuple)

    @property
    def answers(self) -> tuple[str, ...]:
        """What a right guess is."""
        return self.plan or (NONE,)


def explain(deck, answer: dict, last_run=None) -> Reading:
    """The page for one hand, from `measure`'s answer."""
    found = deck_reading(deck, theme_cards(deck)).themes
    hand = [front_face(name) for name in answer["hand"]]
    games = answer["games"]
    rows = []
    for theme in found:
        names = {front_face(name) for name in theme.cards}
        held = tuple(name for name in hand if name in names)
        early = tuple(name for name in held if _early(name, answer))
        count = answer["running"].get(theme.theme)
        usual = engine_watch.running(last_run, theme.theme)
        rows.append(Row(theme=theme.theme, label=_label(theme), cards=held, early=early,
                        share=None if count is None else percent(100 * count / games),
                        usual=usual["share"] if usual else None,
                        value=None if count is None else 100 * count / games))
    measured = [row for row in rows if row.value is not None]
    best = max((row.value for row in measured), default=0.0)
    plan = tuple(row.theme for row in sorted(measured, key=lambda row: -row.value)
                 if best >= NO_PLAN and row.value >= best - CLOSE)
    reading = Reading(hand=tuple(hand), rows=tuple(rows), main=rows[0] if rows else None,
                      plan=plan, keeps=answer["keeps"], lands=answer["lands"],
                      measured=bool(measured))
    return Reading(**{**reading.__dict__, "sentences": tuple(_sentences(reading))})


def _early(name: str, answer: dict) -> bool:
    costs = {front_face(card): value for card, value in answer["costs"].items()}
    return name in costs and costs[name] <= min(EARLY, answer["mana"])


def _label(found) -> str:
    """As the deck page writes it: "Typal Angel"."""
    if found.theme == themes.TYPAL and found.subject:
        return f"{gettext(found.label)} {found.subject}"
    return gettext(found.label)


def _sentences(reading: Reading) -> list[str]:
    """The tutor's words, in the order a player reads a hand."""
    out = []
    main = reading.main
    if main is None:
        return [gettext("The deck page finds no theme in this deck yet, so the tutor has no "
                         "plan to read. The lands and the mulligan rule below still count.")]
    out.append(gettext("Your deck is built mainly around %(theme)s.") % {"theme": main.label})
    if main.cards:
        out.append(ngettext("This hand holds %(count)s card for it.",
                            "This hand holds %(count)s cards for it.", len(main.cards))
                   % {"count": len(main.cards)})
    else:
        out.append(gettext("This hand holds no card for it."))
    if not reading.measured:
        out.append(gettext("None of its engines can run yet (the deck page says which piece "
                           "is missing), so the tutor cannot measure a plan."))
        return out
    by_key = {row.theme: row for row in reading.rows}
    if not reading.plan:
        out.append(gettext("No engine runs by turn %(turn)s in as many as %(share)s of the games "
                           "with this hand: it has no plan of its own yet. Play your lands and "
                           "draw into one.") % {"turn": TURNS, "share": percent(NO_PLAN)})
        return out
    # The deck's own theme leads when it is one of the plans.
    lead = main.theme if main.theme in reading.plan else reading.plan[0]
    first = by_key[lead]
    if main.theme in reading.plan:
        out.append(gettext("With this hand %(theme)s runs by turn %(turn)s in %(share)s of the "
                           "games.") % {"theme": main.label, "turn": TURNS, "share": main.share})
        out.append(gettext("Plan: %(theme)s.") % {"theme": main.label})
    elif main.share is None:
        out.append(gettext("With this hand %(theme)s runs by turn %(turn)s in %(share)s of the "
                           "games.") % {"theme": first.label, "turn": TURNS, "share": first.share})
    else:
        out.append(gettext("With this hand %(theme)s runs by turn %(turn)s in %(share)s of the "
                           "games, %(main)s in %(main_share)s.")
                   % {"theme": first.label, "turn": TURNS, "share": first.share,
                      "main": main.label, "main_share": main.share})
    if main.theme not in reading.plan:
        out.append(gettext("Plan: build %(theme)s first; %(main)s comes later.")
                   % {"theme": first.label, "main": main.label})
    if len(reading.plan) > 1:
        out.append(gettext("%(others)s is about as likely: %(games)s games cannot tell them "
                           "apart.")
                   % {"games": GAMES, "others": ", ".join(by_key[key].label for key in reading.plan
                                                          if key != lead)})
    return out
