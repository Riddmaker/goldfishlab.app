"""Your strategies, and what would feed them (phase 11 E, Z5.3, K16, F14).

A block on the report: the deck's strategies, the chance of drawing one of
each by turn four, and cards that would feed them. Three parts, with
different sources, and the page says which is which (Principle 2):

* **The strategies** are the deck's categories - the eight core ones and the
  seven of "By strategy" - with at least `MIN_CARDS` cards (P5). One card of a
  kind is an accident, two are a plan.
* **The odds** are *calculated* over the list, now and with `MORE` more cards
  of the kind (the deck's size unchanged). What the run *measured* is in "By
  strategy", just above.
* **The candidates** are catalogue cards: the strategy's role, inside the
  deck's colour identity, legal in Commander, not in the deck, not a land
  (except for Ramp), and no Game Changer unless the deck already plays one -
  a suggestion must not push a casual deck into a higher bracket. The most
  played first (`edhrec_rank`).

Mistral picks from these lists in the summary's own call (P6, see
`simulations.summary`); `parse` there drops every card it was not offered.
Without Mistral - no key, a failure, summaries switched off, a version 1
summary - the block shows the `FALLBACK_STRATEGIES` biggest strategies and
their first `FALLBACK_CARDS` candidates, without reasons. It never disappears
because the written part is missing.
"""

from dataclasses import dataclass, field

from django.db.models import F
from django.utils.translation import gettext

from core.l10n import percent
from simulations.report import SEEN_ROLES, SEEN_STRATEGY_ROLES
from simulations.summary import BY_TURN, SEEN_BY_TURN, at_least_one

#: A category is a strategy from this many cards in the library.
MIN_CARDS = 2
#: Candidates per strategy offered to Mistral.
CANDIDATES = 6
#: "with two more": the odds if this many more cards of the kind were played.
MORE = 2
#: Without Mistral: this many strategies, the biggest, with this many cards.
FALLBACK_STRATEGIES = 2
FALLBACK_CARDS = 3

LABELS = dict(SEEN_ROLES + SEEN_STRATEGY_ROLES)
#: The one strategy a land can feed.
LANDS_FEED = "ramp"


@dataclass(frozen=True)
class Strategy:
    """One of the deck's strategies, with its calculated odds by `BY_TURN`."""

    key: str
    label: str
    count: int
    now: float
    more: float

    @property
    def name(self) -> str:
        """`label` in the page's language; `label` stays English for Mistral."""
        return gettext(self.label)

    @property
    def now_label(self) -> str:
        return _percent(self.now)

    @property
    def more_label(self) -> str:
        return _percent(self.more)


def _percent(value: float) -> str:
    """A whole percent, but never 100% or 0% for something that is not."""
    if 99 < value < 100:
        return gettext("over %(percent)s") % {"percent": percent(99)}
    if 0 < value < 1:
        return gettext("under %(percent)s") % {"percent": percent(1)}
    return percent(value)


@dataclass(frozen=True)
class Pick:
    """A card shown under a strategy; `reason` only when Mistral picked it."""

    name: str
    reason: str = ""
    url: str = ""


@dataclass
class Shown:
    strategy: Strategy
    why: str = ""
    cards: list[Pick] = field(default_factory=list)


def _library(readings) -> list:
    return [reading for reading in readings if not reading.is_commander]


def found(readings) -> list[Strategy]:
    """The deck's strategies, the most cards first (ties in list order).

    Args:
        readings: `simulations.engine.adapter.readings(deck)`.
    """
    library = _library(readings)
    population = sum(reading.quantity for reading in library)
    counts = dict.fromkeys(LABELS, 0)
    for reading in library:
        for key in counts:
            if key in reading.card.categories:
                counts[key] += reading.quantity
    strategies = [
        Strategy(
            key=key, label=LABELS[key], count=count,
            now=round(at_least_one(count, population, SEEN_BY_TURN), 1),
            more=round(at_least_one(min(count + MORE, population), population,
                                    SEEN_BY_TURN), 1),
        )
        for key, count in counts.items() if count >= MIN_CARDS
    ]
    return sorted(strategies, key=lambda strategy: -strategy.count)


def identity(readings) -> set[str]:
    """The deck's colour identity: the commander's and every card's.

    For a legal deck that is the commander's own, and a partner's colours
    count as well, which the commander alone would leave out.
    """
    return {colour for reading in readings for colour in reading.oracle_card.color_identity}


def candidates(readings, key: str, limit: int = CANDIDATES) -> list:
    """Catalogue cards that would feed the strategy `key`, the most played first."""
    from cards.models import DerivedProfile, OracleCard

    in_deck = {reading.oracle_card.pk for reading in readings}
    cards = (
        OracleCard.objects
        .filter(profile__role_tags__contains=[key],
                color_identity__contained_by=sorted(identity(readings)),
                legalities__commander="legal")
        .exclude(pk__in=in_deck)
    )
    if key != LANDS_FEED:
        cards = cards.exclude(profile__kind=DerivedProfile.Kind.LAND)
    if not any(reading.oracle_card.game_changer for reading in readings):
        cards = cards.exclude(game_changer=True)
    return list(cards.order_by(F("edhrec_rank").asc(nulls_last=True), "name")[:limit])


def offered(readings) -> list[dict]:
    """The strategies and their candidates, as the summary's facts give them."""
    rows = []
    for strategy in found(readings):
        cards = candidates(readings, strategy.key)
        if not cards:
            continue
        rows.append({
            "strategy": strategy.label,
            "cards_in_deck": strategy.count,
            f"chance_by_turn_{BY_TURN}_now_percent": strategy.now,
            f"chance_by_turn_{BY_TURN}_with_{MORE}_more_percent": strategy.more,
            "candidates": [_card(card) for card in cards],
        })
    return rows


#: Oracle text offered to Mistral, cut here.
TEXT_MAX = 200


def _card(card) -> dict:
    text = " ".join(card.oracle_text.split())
    if len(text) > TEXT_MAX:
        text = text[:TEXT_MAX].rsplit(" ", 1)[0] + "…"
    return {"name": card.front_name, "type": card.type_line, "mana_cost": card.mana_cost,
            "text": text}


def block(readings, content: dict | None = None) -> dict:
    """What the block shows: Mistral's picks if it made any, else the fallback.

    `content` is the stored summary's (`DeckSummary.content`). Its picks are
    read against the deck as it is now: a strategy the deck no longer has, or
    a card it has since added, is left out. The odds are always worked out
    here, from the list as it stands.
    """
    from cards.models import OracleCard

    strategies = found(readings)
    by_key = {strategy.key: strategy for strategy in strategies}
    in_deck = {reading.oracle_card.front_name for reading in readings}
    shown = []
    for entry in (content or {}).get("strategies") or []:
        strategy = by_key.get(entry.get("key"))
        cards = [Pick(name=card["name"], reason=card.get("reason", ""))
                 for card in entry.get("cards") or [] if card.get("name") not in in_deck]
        if strategy is not None and cards:
            shown.append(Shown(strategy=strategy, why=entry.get("why", ""), cards=cards))
    picked = bool(shown)
    if not picked:
        shown = [
            Shown(strategy=strategy,
                  cards=[Pick(name=card.front_name) for card in
                         candidates(readings, strategy.key, FALLBACK_CARDS)])
            for strategy in strategies[:FALLBACK_STRATEGIES]
        ]
    names = {card.name for item in shown for card in item.cards}
    urls = dict(OracleCard.objects.filter(front_name__in=names)
                .values_list("front_name", "scryfall_uri")) if names else {}
    for item in shown:
        item.cards = [Pick(name=card.name, reason=card.reason, url=urls.get(card.name, ""))
                      for card in item.cards]
    return {"shown": shown, "picked": picked, "by_turn": BY_TURN, "more": MORE}


__all__ = ["MIN_CARDS", "MORE", "Pick", "Shown", "Strategy", "block", "candidates",
           "found", "identity", "offered"]
