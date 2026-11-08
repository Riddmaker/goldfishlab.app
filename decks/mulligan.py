"""Keep or mulligan? (Themen-Doc, Paket 5, PR 25).

A seven from the player's own deck; the player keeps it or sends it back, and
the simulation says which was better. The comparison is the point: this hand
kept, against a fresh seven under the rule for a mulligan taken (the first
one is free in Commander, `Game.take_opening_hand(mulligans=1)`). A hand on
its own almost always looks worse than "a good hand".

- A round is `ROUND` hands from one seed (`hand_seed`); every third is a
  borderline hand (two lands or five), so not every hand is obvious.
- `measure` plays each side `GAMES` times and counts the games with a good
  start (`good_start`): by turn `START_TURN`, `START_MANA` mana and
  `START_PERMANENTS` permanents that are not lands. Every deck can be
  measured on it, and it punishes both too few lands and too many. The
  engines of the deck page (`decks.engine_watch`) are counted beside it, by
  turn six, but do not decide.
- `verdict` compares the two shares. Within the 95 % margin of their
  difference, or under `CLOSE_FLOOR` points, it is close and both answers
  count as right: a single simulated game is never shown as proof.
- The worker (`decks.tasks.read_mulligan`) plays the hand as soon as it is
  shown, so the answer is usually there when the player has decided. The
  answer waits in the cache, keyed like the hand tutor's.

What the player decided is stored (`decks.models.MulliganDecision`); the
privacy policy says what and why.
"""

import math
import random
import time
from dataclasses import dataclass

from django.core.cache import cache

from decks import engine_watch
from simulation import ENGINE_VERSION, analysis
from simulation.game import STARTING_HAND_SIZE, Game
from simulations.engine import adapter

#: Hands in a round.
ROUND = 10
#: Every this many hands, a borderline one.
BORDERLINE_EVERY = 3
#: The lands a borderline seven holds: the free seven's rule mulligans two
#: (unless accelerants stand in) and keeps five.
BORDERLINE_LANDS = (2, 5)
#: How many deals are tried to find a borderline hand before taking any.
BORDERLINE_TRIES = 60

#: Games per side. 1,000 put a share within about three points (95 %).
GAMES = 1000
TURNS = engine_watch.BY_TURN
#: A good start: by this turn, this much mana and this many permanents that
#: are not lands (the commander counts).
START_TURN = 4
START_MANA = 4
START_PERMANENTS = 2
#: A difference under this many points is close, however many games: not one
#: a player at the table would notice.
CLOSE_FLOOR = 3
#: 95 %.
Z = 1.96

KEEP, MULLIGAN, CLOSE = "keep", "mulligan", "close"

CACHE_TTL = 24 * 60 * 60
PENDING_SECONDS = 120


# --- The hands of a round ----------------------------------------------------


def deal(definition, seed: int) -> list:
    """The seven a seed deals (as the playtest and the hand tutor deal), as cards."""
    game = Game(random.Random(seed), deck=definition)
    game.draw(STARTING_HAND_SIZE)
    return list(game.hand)


def _candidate(round_seed: int, position: int, attempt: int) -> int:
    return random.Random(f"{round_seed}:{position}:{attempt}").getrandbits(62)


def borderline(cards) -> bool:
    return sum(1 for card in cards if card.is_land) in BORDERLINE_LANDS


def hand_seed(definition, round_seed: int, position: int) -> int:
    """The deal seed of a round's hand `position` (1 to `ROUND`): the same
    round always deals the same hands. Every `BORDERLINE_EVERY`-th is a
    borderline seven when one turns up in `BORDERLINE_TRIES` deals."""
    first = _candidate(round_seed, position, 0)
    if position % BORDERLINE_EVERY:
        return first
    for attempt in range(BORDERLINE_TRIES):
        seed = _candidate(round_seed, position, attempt)
        if borderline(deal(definition, seed)):
            return seed
    return first


def on_the_play(seed: int) -> bool:
    """On the play or on the draw, drawn by lot with the hand."""
    return not seed & 1


# --- Facts kept with a decision ---------------------------------------------


def mana_value(card) -> int:
    cost = card.mana_cost
    return (cost.generic + cost.colorless + sum(count for _color, count in cost.pips)
            + len(cost.hybrid) + len(cost.phyrexian))


def hand_facts(cards) -> dict:
    return {
        "lands": sum(1 for card in cards if card.is_land),
        "accelerants": sum(1 for card in cards if card.is_accelerant),
        "mana_values": sorted(mana_value(card) for card in cards if not card.is_land),
    }


def deck_facts(deck, definition) -> dict:
    """What the deck was, without its list: enough to read a decision
    later, after the deck changed or was deleted."""
    colors = sorted({color for card in deck.commanders for color in card.color_identity})
    return {
        "cards": definition.size,
        "lands": definition.land_count,
        "accelerants": sum(1 for card in definition.library if card.is_accelerant),
        "commanders": [card.name for card in definition.commanders],
        "commander_mana_values": [mana_value(card) for card in definition.commanders],
        "colors": colors,
    }


# --- Playing both sides ------------------------------------------------------


def _games_seed(seed: int) -> int:
    return seed ^ 0x6D17_A9A9


def good_start(measured) -> bool:
    """Did this game start well: `START_MANA` mana and `START_PERMANENTS`
    non-land permanents by turn `START_TURN`?"""
    turn = measured["per_turn"][START_TURN - 1]
    built = sum(1 for card in turn["battlefield"] if not card.is_land)
    return turn["mana"] >= START_MANA and built >= START_PERMANENTS


def play(definition, hand, watches, *, seed: int, on_the_play: bool = True,
         games: int = GAMES) -> dict:
    """Both sides, `games` each: the hand kept, and a fresh seven after it
    went back. Per side the games with a good start and the games with any
    of the deck's engines running by turn `TURNS`. Pure: no database."""
    rng = random.Random(_games_seed(seed))
    sides = {}
    for side, given in ((KEEP, {"hand": hand}), (MULLIGAN, {"mulligans": 1})):
        good = engines = 0
        for _ in range(games):
            measured = analysis.simulate_game(rng, on_the_play=on_the_play, turns=TURNS,
                                              deck=definition, watch=watches, **given)
            good += good_start(measured)
            engines += any(measured.get("combos", {}).values())
        sides[side] = {"good": good, "engines": engines}
    return sides


# --- The verdict -------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """The two shares (percent), their 95 % margins (points) and which side
    was better: `KEEP`, `MULLIGAN` or `CLOSE`."""

    keep: float
    keep_margin: float
    mulligan: float
    mulligan_margin: float
    best: str

    @property
    def difference(self) -> float:
        return abs(self.keep - self.mulligan)

    def right(self, choice: str) -> bool:
        """Close: either answer is right."""
        return self.best in (CLOSE, choice)


def _margin(share: float, games: int) -> float:
    return 100 * Z * math.sqrt(share * (1 - share) / games)


def verdict(keep: int, mulligan: int, games: int) -> Verdict:
    """From the games with a good start on each side."""
    kept, sent = keep / games, mulligan / games
    spread = 100 * Z * math.sqrt(kept * (1 - kept) / games + sent * (1 - sent) / games)
    difference = 100 * (kept - sent)
    if abs(difference) <= max(spread, CLOSE_FLOOR):
        best = CLOSE
    else:
        best = KEEP if difference > 0 else MULLIGAN
    return Verdict(keep=100 * kept, keep_margin=_margin(kept, games),
                   mulligan=100 * sent, mulligan_margin=_margin(sent, games), best=best)


def answer_verdict(answer: dict) -> Verdict:
    return verdict(answer["sides"][KEEP]["good"], answer["sides"][MULLIGAN]["good"],
                   answer["games"])


# --- Worker and cache ----------------------------------------------------------


def key(deck, seed: int) -> str:
    """The deck as it is now and the engine that played it, as the hand
    tutor keys its answers (`decks.tutor.key`)."""
    from simulations.summary import fingerprint

    return f"mulligan:v1:{ENGINE_VERSION}:{deck.pk}:{fingerprint(deck)}:{seed}"


def cached(deck, seed: int) -> dict | None:
    return cache.get(key(deck, seed))


def ask(deck, seed: int) -> None:
    """Have the worker play the hand, once."""
    from decks.tasks import read_mulligan

    if cache.add(key(deck, seed) + ":pending", 1, PENDING_SECONDS):
        read_mulligan.delay(str(deck.pk), seed)


def measure(deck, seed: int) -> dict:
    """Deal the hand, play both sides, keep the answer in the cache."""
    definition = adapter.convert(deck).definition
    hand = [card.name for card in deal(definition, seed)]
    watches = engine_watch.watches(deck)
    answer = {
        "hand": hand,
        "games": GAMES,
        "engines": bool(watches),
        "sides": play(definition, hand, watches, seed=seed, on_the_play=on_the_play(seed)),
    }
    cache.set(key(deck, seed), answer, CACHE_TTL)
    return answer


# --- Decisions ---------------------------------------------------------------


SALT = "decks.mulligan.shown"
#: A hand shown longer ago than this cannot be answered any more.
SHOWN_MAX_AGE = 24 * 60 * 60


def shown_note(round_seed: int, position: int, seed: int) -> str:
    """The signed note a hand's form carries: which hand, shown when."""
    from django.core import signing

    return signing.dumps({"r": round_seed, "n": position, "s": seed,
                          "t": int(time.time() * 1000)}, salt=SALT)


def read_note(note: str, round_seed: int, position: int, seed: int) -> int | None:
    """The milliseconds since the hand was shown, or None for a note that
    is forged, too old or about another hand."""
    from django.core import signing

    try:
        data = signing.loads(note, salt=SALT, max_age=SHOWN_MAX_AGE)
    except signing.BadSignature:
        return None
    if (data.get("r"), data.get("n"), data.get("s")) != (round_seed, position, seed):
        return None
    return max(0, int(time.time() * 1000) - int(data.get("t", 0)))


def decide(user, deck, definition, *, round_seed: int, position: int, seed: int,
           choice: str, sure: int | None, waited_ms: int):
    """Store the answer to one hand; a hand answered before keeps its first
    answer. Returns the decision."""
    from django.db import IntegrityError, transaction

    from decks.models import MulliganDecision
    from simulations.summary import fingerprint

    cards = deal(definition, seed)
    keeper = Game(random.Random(0), deck=definition)
    fields = {
        "deck": deck, "seed": seed, "on_the_play": on_the_play(seed),
        "engine_version": ENGINE_VERSION, "deck_print": fingerprint(deck),
        "hand": [card.name for card in cards], "hand_facts": hand_facts(cards),
        "deck_facts": deck_facts(deck, definition),
        "rule_keeps": keeper.keeps_free_seven(cards), "choice": choice, "sure": sure,
        "decision_ms": min(waited_ms, MulliganDecision.SLOWEST_MS),
    }
    try:
        with transaction.atomic():
            decision, _made = MulliganDecision.objects.get_or_create(
                user=user, round_seed=round_seed, position=position, defaults=fields)
    except IntegrityError:
        decision = MulliganDecision.objects.get(user=user, round_seed=round_seed,
                                                position=position)
    return decision


def settle(decision, answer: dict) -> Verdict | None:
    """Write the simulation's answer onto a decision, once. None when the
    answer is about another hand: the deck changed after the decision."""
    if answer["hand"] != decision.hand:
        return None
    judged = answer_verdict(answer)
    if not decision.verdict:
        decision.result = {"games": answer["games"], "sides": answer["sides"],
                           "engines": answer["engines"]}
        decision.verdict = judged.best
        decision.right = judged.right(decision.choice)
        decision.save(update_fields=["result", "verdict", "right"])
    return judged


@dataclass(frozen=True)
class Tally:
    decided: int
    right: int
    close: int

    @property
    def share(self) -> float | None:
        return 100 * self.right / self.decided if self.decided else None


def tally(decisions) -> Tally:
    """Count the judged decisions of a queryset."""
    from django.db.models import Count, Q

    counts = decisions.exclude(verdict="").aggregate(
        decided=Count("pk"), right=Count("pk", filter=Q(right=True)),
        close=Count("pk", filter=Q(verdict=CLOSE)))
    return Tally(**counts)


def record(user) -> dict:
    """The player's own record: every judged decision, and by how sure they
    said they were - the calibration."""
    from decks.models import MulliganDecision

    decisions = user.mulligan_decisions.all()

    return {
        "all": tally(decisions),
        "by_sure": {value: tally(decisions.filter(sure=value))
                    for value in MulliganDecision.Sure.values},
    }
