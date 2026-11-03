"""Measuring the deck's engines in its own games (Themen-Doc, Paket 1, PR 5).

The engines on the deck page (`cards.synergy.engines`, PR 3) ride along with
the combos the simulator already watches (`simulation.combos`): no extra
games, so no extra cost. The question is the one a player asks of a plan:
**by which turn is it running?**

An engine is running when every role of it is filled by *any* card of the
deck that can fill it, not only by the cards the page names: an aristocrats
deck runs with any sacrifice outlet and any payoff out. A permanent counts on
the battlefield, an instant or sorcery in hand, where it is ready to cast.
The commander counts on the battlefield only: in the command zone it does
nothing yet.

Which engines: the ones the page shows, locked first, at most `MAX_ENGINES`,
one per theme. An engine a role short or resting (a card left the deck) is
not measured: it cannot run. Every chunk of a run works this out again, like
the combo plan, and the answer only depends on the deck and its marks.

The engines' keys start with `PREFIX` inside the run's ``combos``;
`split` moves them to ``engines`` when the run is finished, keyed by theme.
Engine pieces are not `key_cards`: a tutor going for them would change the
games being measured.
"""

from django.utils.translation import gettext

from cards import themes
from cards.names import front_face
from core.l10n import percent
from decks.themes import deck_reading, theme_cards
from simulations.engine import runner

PREFIX = "engine:"

#: The most engines one run measures. The page shows one per theme found,
#: and a deck with more than five themes is rare; past that the numbers are
#: for engines nobody reads down to.
MAX_ENGINES = 5

#: The turn the page quotes: "running by turn 6". A run of fewer turns
#: quotes its last one.
BY_TURN = 6

#: Never on the battlefield: these count in hand.
SPELL_TYPES = frozenset({"Instant", "Sorcery"})


def watches(deck) -> tuple:
    """The `runner.Watch` for each engine to measure, in page order."""
    cards = theme_cards(deck)
    reading = deck_reading(deck, cards)
    made, seen = [], set()
    for engine in reading.engines:
        if engine.missing or engine.gone or engine.theme in seen:
            continue
        watch = _watch_for(engine, cards)
        if watch is None:
            continue
        seen.add(engine.theme)
        made.append(watch)
        if len(made) == MAX_ENGINES:
            break
    return tuple(made)


def _watch_for(engine, cards):
    """Every role of the engine as one `AnyOf` of the cards that fill it."""
    theme = themes.BY_KEY.get(engine.theme)
    if theme is None:
        return None
    pieces = {piece.key: piece for piece in theme.pieces}
    roles = []
    for _oracle_id, piece_key in engine.parts:
        piece = pieces.get(piece_key)
        if piece is None:
            return None
        options = tuple(
            runner.Requirement(name=front_face(card.name), zones=(_zone(card),))
            for card in sorted(cards, key=lambda card: card.name)
            if card.weight and piece.holds(card))
        if not options:
            return None
        roles.append(runner.AnyOf(options))
    return runner.Watch(key=PREFIX + engine.theme, requirements=tuple(roles), combo=False)


def _zone(card) -> str:
    if card.types & SPELL_TYPES and not card.commander:
        return runner.HAND
    return runner.BATTLEFIELD


def split(result: dict) -> dict:
    """Move the engines out of a merged result's ``combos`` into ``engines``.

    ``{theme: {"games": n, "by_turn": [...]}}``. A result with no combo
    left loses its ``combos`` key, which then means what it always meant:
    nothing was watched.
    """
    watched = result.get("combos") or {}
    engines = {key[len(PREFIX):]: entry for key, entry in watched.items()
               if key.startswith(PREFIX)}
    if not engines:
        return result
    rest = {key: entry for key, entry in watched.items() if not key.startswith(PREFIX)}
    if rest:
        result["combos"] = rest
    else:
        result.pop("combos", None)
    result["engines"] = engines
    return result


def running(run, theme: str) -> dict | None:
    """What the run says of one theme's engine: ``{"turn", "share"}``, the
    share as the page's language writes a whole percent; None when the run
    did not measure it."""
    if run is None or not run.result:
        return None
    entry = (run.result.get("engines") or {}).get(theme)
    if not entry or not entry.get("games") or not entry.get("by_turn"):
        return None
    turn = min(BY_TURN, len(entry["by_turn"]))
    return {"turn": turn, "share": percent(100 * entry["by_turn"][turn - 1] / entry["games"])}


def engine_rows(reading, run) -> list[tuple]:
    """``(engine, running)`` for each engine on the deck page, `running`
    from the deck's last finished run (None: not measured there)."""
    return [(engine, running(run, engine.theme)) for engine in reading.engines]


def measured(run) -> list[dict]:
    """The report's lines: each engine the run measured, by its theme, the
    most often running first."""
    rows = []
    for key in (run.result or {}).get("engines") or {}:
        theme = themes.BY_KEY.get(key)
        found = running(run, key)
        if theme is None or found is None:
            continue
        rows.append({"theme": key, "label": gettext(theme.label), **found})
    return sorted(rows, key=lambda row: (-_count(run, row["theme"]), row["theme"]))


def _count(run, theme: str) -> float:
    entry = run.result["engines"][theme]
    return entry["by_turn"][min(BY_TURN, len(entry["by_turn"])) - 1] / entry["games"]
