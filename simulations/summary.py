"""The deck summary at the foot of a report (phase 10 G, T6.1 and T6.3).

This part is **computed, not written**: the "Mechanisms" are what the deck
plays, counted off the same readings the deck page and the engine use, and
how soon this run drew each of them. The numbers are always right and cost
nothing, which is why no language model is asked for them - batch H's text
gets them as facts instead of inventing its own.

Two sources, said plainly on purpose:

* **the card count** is the deck as it stands now, like the blind spots and
  "Cards that need your attention" beside it - it is a property of the cards;
* **the share** is this run's, read off the "What you drew" chart at turn
  four (or the run's last turn, if it is shorter), so a chip and its line on
  the chart cannot disagree.

A mechanism the run did not measure - sacrifice outlets, drain and the like,
or a category the deck gained after the run - has a count and no share.

**The written part** (phase 10 H, T6.1, T6.2, T6.4, T6.5) - feel, strengths,
weaknesses, tactics - is Mistral's, through `simulations.mistral`:

* **What goes there:** catalogue data and our own arithmetic only - the
  commander, card names with their type, mana value, categories and counts,
  the curve, the colours, the combos, and the exact opening-hand odds. No deck
  name, nothing about the person, no free text anybody typed, so there is
  next to nothing to inject a prompt through. No simulation numbers either:
  the text is written while the run still plays (P4); the measured numbers
  are the chips.
* **What comes back** is JSON, and `parse` checks it - the four keys, strings,
  at most four points each, every length capped, Markdown and links taken
  out. The template escapes it like any other text.
* **When it is written:** when a run starts and the deck has changed since the
  last one (`fingerprint`), or on the "Write a summary" button. It costs one
  run of the monthly allowance (a guest's one summary is free), and a failure
  gives that run back.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from django.db import transaction

from simulations import mistral
from simulations.report import SEEN_ROLES, SEEN_STRATEGY_ROLES, hypergeometric

#: The turn a chip reports the share for (P6), unless the run is shorter.
BY_TURN = 4


@dataclass(frozen=True)
class Mechanism:
    """One chip: what it is, how many cards, and - if measured - how soon."""

    key: str
    label: str
    cards: int
    #: Share of games that had drawn one by `turn`, in percent; `None` when
    #: the run did not measure it.
    share: float | None = None
    turn: int | None = None
    #: The line on the "What you drew" chart with the same colour, if any.
    line: int | None = None


def mechanisms(readings, report: dict) -> dict:
    """The "Mechanisms" chips, and the core categories the deck has none of.

    Args:
        readings: `simulations.engine.adapter.readings(deck)`.
        report: `simulations.report.build(run)`.

    Returns:
        ``{"chips": [Mechanism, ...], "missing": [label, ...]}``, the chips in
        the order a deck list sorts them.
    """
    counts: dict[str, int] = {}
    for reading in readings:
        if reading.is_commander:
            continue
        for category in reading.card.categories:
            counts[category] = counts.get(category, 0) + reading.quantity

    lines = {}
    if report.get("seen"):
        # Two charts, one palette: a chip's swatch is its line's colour on
        # "By category" or on "By strategy" (phase 11 B).
        lines = {line.key: line
                 for chart in (report["seen"]["roles"], report["seen"].get("strategies"))
                 if chart for line in chart.lines}
    turn = min(BY_TURN, report["turns"])

    chips = []
    for key, label in SEEN_ROLES + SEEN_STRATEGY_ROLES:
        if not counts.get(key):
            continue
        line = lines.get(key)
        chips.append(Mechanism(
            key=key, label=label, cards=counts[key],
            share=line.values[turn - 1] if line else None,
            turn=turn if line else None,
            line=line.index if line else None,
        ))
    missing = [label for key, label in SEEN_ROLES if not counts.get(key)]
    return {"chips": chips, "missing": missing}


# --- the written part (phase 10 H) ---------------------------------------------

#: Recorded on every summary, never compared: a new prompt does not rewrite
#: anybody's summary, because that would charge them for our change.
PROMPT_VERSION = 1

#: Room for the JSON answer. Each part is "not longer than a paragraph"
#: (T6.1); this is generous and still bounds the cost of a runaway answer.
MAX_TOKENS = 900

FEEL_MAX = 500
POINT_MAX = 200
POINTS_MAX = 4
TACTICS_MAX = 700

#: The opening hand the odds are worked out for.
HAND = 7

SYSTEM_PROMPT = (
    "You are an experienced Magic: The Gathering Commander (EDH) player helping "
    "someone understand their own deck. You get facts about one deck: its commander, "
    "its cards with their categories, its mana curve, its combos and exact "
    "opening-hand odds. Use only these facts and your knowledge of the named cards. "
    "Never invent numbers; quote only numbers that are in the facts. Judge counts "
    "against this deck's own size (cards_in_library) and the land band given for "
    "it, not against a fixed 99. If combos_in_deck is null, nobody has looked the "
    "deck up for combos: say nothing about combos at all. Do not suggest specific "
    "new cards by name - suggest changes as roles and counts (for example "
    '"two more card draw spells").\n\n'
    "Answer with one JSON object and nothing else, with exactly these keys:\n"
    '- "feel": two or three sentences on how the deck wants to play and what kind '
    "of game its owner probably enjoys, guessed from the cards.\n"
    '- "strengths": a list of at most four short sentences, each one strength.\n'
    '- "weaknesses": a list of at most four short sentences, each one weakness.\n'
    '- "tactics": two to four sentences on how to play the deck, and what to change '
    "if the owner wants it to carry out its plan better.\n"
    "Plain sentences only: no Markdown, no lists inside strings, no links, no "
    "leading + or -. Write in English."
)


def fingerprint(deck) -> str:
    """What a summary was written about: change any of it and it is out of date.

    The cards and how many of each, the commander, and every annotation the
    engine would merge for this deck - an owner's answer about a card changes
    what the deck does, which is T6.2's "or cards were annotated".
    """
    from simulations.engine.adapter import cards_filter, scope_filter
    from simulations.models import CardAnnotation

    cards = sorted(
        f"{pk}x{quantity}"
        for pk, quantity in deck.entries.values_list("oracle_card_id", "quantity")
    )
    rows = (
        CardAnnotation.objects.filter(cards_filter(deck)).filter(scope_filter(deck))
        .values_list("oracle_card_id", "owner_id", "deck_id", "overrides").distinct()
    )
    annotations = sorted(
        json.dumps([str(card), str(owner), str(deck_id), overrides], sort_keys=True,
                   default=str)
        for card, owner, deck_id, overrides in rows
    )
    text = "|".join([f"commander:{deck.commander_id}", *cards, "annotations", *annotations])
    return hashlib.sha256(text.encode()).hexdigest()


def _at_least_one(successes: int, population: int, drawn: int = HAND) -> float:
    return 100.0 - hypergeometric(0, population, successes, drawn)


def facts(deck, readings) -> dict:
    """Everything the prompt says about the deck - and nothing else."""
    from decks.analysis import KARSTEN_MAX, KARSTEN_MIN, analyse

    analysis = analyse(deck)
    library = [reading for reading in readings if not reading.is_commander]
    population = sum(reading.quantity for reading in library)
    lands = sum(reading.quantity for reading in library if reading.card.is_land)
    labels = dict(SEEN_ROLES + SEEN_STRATEGY_ROLES)
    counts = dict.fromkeys(labels, 0)
    cards = []
    for reading in sorted(library, key=lambda reading: reading.oracle_card.front_name):
        mine = [key for key in labels if key in reading.card.categories]
        for key in mine:
            counts[key] += reading.quantity
        oracle = reading.oracle_card
        cards.append({
            "name": oracle.front_name,
            "count": reading.quantity,
            "type": oracle.type_line,
            "mana_value": int(oracle.cmc),
            "categories": [labels[key] for key in mine],
        })
    commander = next((reading.oracle_card for reading in readings if reading.is_commander),
                     None)
    core = dict(SEEN_ROLES)
    return {
        "commander": (
            {"name": commander.front_name, "type": commander.type_line,
             "mana_cost": commander.mana_cost, "text": commander.oracle_text}
            if commander else None
        ),
        "colour_identity": "".join(analysis.color_identity) or "colourless",
        "cards_in_library": population,
        "lands": lands,
        # Karsten's 35-38 for 99 cards, scaled to this deck: the first F7 pass
        # called 24 lands in 48 cards "very low", measured against 99.
        "usual_lands_for_this_size": (
            f"{round(population * KARSTEN_MIN / 99)}-{round(population * KARSTEN_MAX / 99)}"
        ),
        "average_mana_value_of_spells": analysis.average_mv,
        "curve_of_spells": {("7+" if value == 7 else str(value)): count
                            for value, count in analysis.curve.items()},
        "cards_per_category": {labels[key]: count for key, count in counts.items() if count},
        "combos_in_deck": _combos(deck),
        "opening_hand_of_7": {
            "chance_of_exactly_n_lands_percent": {
                str(count): round(hypergeometric(count, population, lands, HAND), 1)
                for count in range(HAND + 1)
            },
            "chance_of_at_least_one_percent": {
                labels[key]: round(_at_least_one(count, population), 1)
                for key, count in counts.items() if count and key in core
            },
        },
        "cards": cards,
    }


#: At most this many combos go into the prompt, the most popular first.
COMBOS_MAX = 10


def _combos(deck) -> list[dict] | None:
    """The combos Commander Spellbook found complete in this deck.

    `None` when nobody has looked the deck up - "not looked for" is not "none",
    and the first F7 pass read an empty list as a weakness of every deck.
    """
    from combos.models import ComboLookup, DeckCombo

    if not ComboLookup.objects.filter(deck=deck, status=ComboLookup.Status.OK).exists():
        return None
    entries = (
        DeckCombo.objects.filter(lookup__deck=deck, kind=DeckCombo.Kind.INCLUDED)
        .select_related("combo").prefetch_related("combo__cards")[:COMBOS_MAX]
    )
    return [
        {"cards": [card.name for card in entry.combo.cards.all()],
         "produces": list(entry.combo.produces)[:3]}
        for entry in entries
    ]


def messages(deck_facts: dict) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Facts about the deck, as JSON:\n"
                                    + json.dumps(deck_facts, ensure_ascii=False)},
    ]


_LINK = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_MARKDOWN = re.compile(r"[*_#`>\[\]]")
#: A bullet, sign or number the model put in front of a point anyway: the page
#: draws its own + and −.
_LEADING = re.compile(r"^[\s+\-−•\d.)]+")


def _clean(value, limit: int) -> str:
    """One string from the model, as the page may show it.

    No links, no Markdown markers, one line, and cut at the last whole word
    under `limit`. The template escapes it as well; this is about what it
    says, not about whether it is safe to put in HTML.
    """
    if not isinstance(value, str):
        raise ValueError("not a string")
    text = " ".join(_MARKDOWN.sub("", _LINK.sub("", value)).split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _points(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("not a list")
    points = [_LEADING.sub("", _clean(item, POINT_MAX)) for item in value[:POINTS_MAX]]
    return [point for point in points if point]


def parse(content: str) -> dict:
    """Mistral's answer, checked. Raises ValueError for anything else."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    checked = {
        "feel": _clean(data.get("feel"), FEEL_MAX),
        "strengths": _points(data.get("strengths")),
        "weaknesses": _points(data.get("weaknesses")),
        "tactics": _clean(data.get("tactics"), TACTICS_MAX),
    }
    if not checked["feel"] or not (checked["strengths"] or checked["weaknesses"]):
        raise ValueError("empty summary")
    return checked


# --- when one is written, and what it costs -----------------------------------


def _stored(deck):
    from simulations.models import DeckSummary

    return DeckSummary.objects.filter(deck=deck).first()


def guest_has_had_one(owner) -> bool:
    """A guest gets exactly one summary (K8), and it is free (P7)."""
    from simulations.models import DeckSummary

    return (DeckSummary.objects.filter(deck__owner=owner)
            .exclude(status=DeckSummary.Status.FAILED).exists())


def due(owner, deck, print_: str | None = None) -> bool:
    """Should starting a run on this deck write a new summary?

    Not when Mistral is not configured or the owner switched summaries off;
    not while one is being written; not when the stored one is about this
    exact deck. A guest only ever gets one.
    """
    from simulations.models import DeckSummary

    if not mistral.is_configured() or not owner.deck_summaries:
        return False
    stored = _stored(deck)
    if stored is not None and stored.status == DeckSummary.Status.PENDING:
        return False
    if owner.is_guest and guest_has_had_one(owner):
        return False
    if stored is None or stored.status == DeckSummary.Status.FAILED:
        return True
    return stored.fingerprint != (print_ or fingerprint(deck))


def claim(owner, deck) -> bool:
    """`due`, asked again with the deck row locked - inside the transaction
    that charges for it, so two runs started together, or a double click,
    write and charge one summary rather than two."""
    from decks.models import Deck

    Deck.objects.select_for_update().filter(pk=deck.pk).first()
    return due(owner, deck)


def begin(deck, *, charged: bool, print_: str | None = None):
    """Mark the deck's summary as being written, and queue the writing.

    Call inside the transaction that charged for it: the task is sent on
    commit, so a worker never looks for a row that is not there yet. The old
    text stays in the row until a new one replaces it.
    """
    from simulations import tasks
    from simulations.models import DeckSummary

    summary, _ = DeckSummary.objects.update_or_create(
        deck=deck,
        defaults={
            "fingerprint": print_ or fingerprint(deck),
            "status": DeckSummary.Status.PENDING,
            "charged": charged,
            "error": "",
        },
    )
    transaction.on_commit(lambda: tasks.write_summary.delay(str(summary.pk)))
    return summary


def state(user, deck) -> dict:
    """What the summary block shows about the written part, for this viewer.

    `offer` is the "Write a summary" button (P8): a member's deck whose summary
    is missing, out of date or failed, with a run left to pay for it. `short`
    is the same deck with no run left.
    """
    from billing import quotas
    from billing.models import UsageRecord
    from simulations.models import DeckSummary

    configured = mistral.is_configured()
    stored = _stored(deck) if configured else None
    pending = stored is not None and stored.status == DeckSummary.Status.PENDING
    current = (stored is not None and stored.status == DeckSummary.Status.DONE
               and stored.fingerprint == fingerprint(deck))
    remaining = None
    offer = short = False
    if configured and not pending and not current and not user.is_guest:
        decision = quotas.check(user, UsageRecord.Metric.RUNS_STARTED, raise_on_fail=False)
        remaining = decision.remaining
        offer = decision.allowed
        short = not decision.allowed
    return {
        "configured": configured,
        "content": (stored.content if stored is not None else None) or {},
        "pending": pending,
        "current": current,
        "failed": stored is not None and stored.status == DeckSummary.Status.FAILED,
        "offer": offer,
        "short": short,
        "remaining": remaining,
    }


__all__ = ["BY_TURN", "Mechanism", "PROMPT_VERSION", "begin", "claim", "due",
           "facts", "fingerprint", "mechanisms", "messages", "parse", "state"]
