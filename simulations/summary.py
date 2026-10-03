"""The deck summary at the foot of a report (phase 10 G and H, phase 11 D).

Version 2 (phase 11 D, Z5.2, K15) is the written part alone: a short title
and a tagline on top, then feel, strengths, weaknesses and tactics. The
"Mechanisms" chips of phase 10 G are gone - their numbers live in "By
strategy" now - and the core categories the deck has none of (`missing`) are
named under "By category", where their lines are missing. Version 3 (phase
11 E) adds "strategies": Mistral's picks from the cards that would feed the
deck's strategies, shown in that block (`simulations.strategies`).

The text is Mistral's, through `simulations.mistral`:

* **What goes there:** catalogue data and our own arithmetic only - the
  commander, card names with their type, mana value, categories and counts,
  the curve, the colours, the combos, and exact odds (the opening hand, and
  at least one of each category by turn four), and for each strategy the
  catalogue cards that would feed it (`simulations.strategies`, phase 11 E),
  which "strategies" in the answer picks from. No deck name, nothing about
  the person, no free text anybody typed, so there is next to nothing to
  inject a prompt through. No simulation numbers either: the text is written
  while the run still plays (P4 of phase 10), which is why the odds are
  *calculated* over the list rather than measured.
* **What comes back** is JSON, and `parse` checks it - the keys, strings, at
  most four points each, every length capped, Markdown and links taken out,
  and every card it names checked against the list it was offered.
  The template escapes it like any other text.
* **When it is written:** when a run starts and the deck has changed since the
  last one (`fingerprint`), or on the "Write a summary" button. It costs one
  run of the monthly allowance (a guest's one summary is free), and a failure
  gives that run back. A version 1 summary stays until the deck changes
  (F13): it shows without title and tagline, and our new prompt rewrites
  nothing and charges nobody.
* **In which language** (phase 12 G, Q3): the language of whoever starts it,
  kept on the row and never in the fingerprint - a language change alone
  rewrites nothing and charges nobody. Strategy and card names stay English,
  so `parse` checks them as strictly as before.
"""

import hashlib
import json
import re

from django.conf import settings
from django.db import transaction
from django.utils.translation import get_language

from simulations import mistral
from simulations.report import SEEN_ROLES, SEEN_STRATEGY_ROLES, hypergeometric

#: The turn the "at least one by turn N" odds in the facts are worked out for.
BY_TURN = 4


def missing(readings) -> list[str]:
    """The core categories the deck has no card of, in deck-list order.

    Args:
        readings: `simulations.engine.adapter.readings(deck)`.
    """
    present = {category for reading in readings if not reading.is_commander
               for category in reading.card.categories}
    return [label for key, label in SEEN_ROLES if key not in present]


# --- the written part (phase 10 H, phase 11 D) ------------------------------------

#: Recorded on every summary, never compared: a new prompt does not rewrite
#: anybody's summary, because that would charge them for our change.
#: 2 = phase 11 D: title and tagline, tactics about the deck's strategies.
#: 3 = phase 11 E: "strategies", cards picked from the lists we offer.
#: 4 = phase 11 F: the land verdict worked out, no card from the deck picked,
#: changes from the deck's own numbers; six shorter candidates a strategy.
#: 5 = phase 12 G: written in the starter's language, names kept in English.
PROMPT_VERSION = 5

#: Room for the JSON answer. Each part is "not longer than a paragraph"
#: (T6.1); this is generous and still bounds the cost of a runaway answer.
MAX_TOKENS = 1600

TITLE_MAX = 40
TAGLINE_MAX = 200
FEEL_MAX = 500
POINT_MAX = 200
POINTS_MAX = 4
TACTICS_MAX = 700
#: "strategies" (phase 11 E): at most three, each with at most three cards.
STRATEGIES_MAX = 3
CARDS_MAX = 3
WHY_MAX = 200
REASON_MAX = 120

#: The opening hand the odds are worked out for.
HAND = 7
#: Cards seen by the end of turn `BY_TURN`: the hand and one draw a turn,
#: none on turn one - a run's default (on the play), and no mulligans.
SEEN_BY_TURN = HAND + BY_TURN - 1

SYSTEM_PROMPT = (
    "You are an experienced Magic: The Gathering Commander (EDH) player helping "
    "someone understand their own deck. You get facts about one deck: its commander, "
    "its cards with their categories, its mana curve, its combos, exact "
    "odds for the opening hand and for the first turns, and for each of its "
    "strategies a short list of catalogue cards that would feed it. Use only these "
    "facts and your knowledge of the named cards. Every card in \"cards\" is "
    "already in the deck; the candidates are not. "
    "Never invent numbers; quote only numbers that are in the facts. Judge counts "
    "against this deck's own size (cards_in_library) and the land band given for "
    "it, not against a fixed 99. If combos_in_deck is null, nobody has looked the "
    "deck up for combos: say nothing about combos at all. Outside \"strategies\", "
    "do not suggest specific new cards by name - suggest changes as roles and "
    "counts. For the land count, follow lands_compared_with_usual. Base every "
    "suggested change on this deck's own numbers, the strategies with the lowest "
    "chances first; never suggest more of a kind by habit.\n\n"
    "Answer with one JSON object and nothing else, with exactly these keys, in "
    "this order:\n"
    '- "title": a name for the deck in two to four words, the way players nickname '
    'a deck by its style, for example "Unconventional Dark" or "Patient Artifact '
    "Engine\". Not the commander's name, no quotes, at most 40 characters.\n"
    '- "tagline": one sentence that says what this deck is.\n'
    '- "feel": one string of two or three sentences on how the deck wants to play and what kind '
    "of game its owner probably enjoys, guessed from the cards.\n"
    '- "strengths": a list of at most four short sentences, each one strength.\n'
    '- "weaknesses": a list of at most four short sentences, each one weakness.\n'
    "- \"tactics\": one string of two to four sentences that name the deck's main strategies "
    "(from cards_per_category), say how they work together to win, and what to "
    "change if the owner wants the plan to come together more often.\n"
    '- "strategies": at most three entries from the facts\' "strategies", the ones '
    "that matter most for this deck's plan. Each is an object with \"strategy\" (its "
    'name exactly as given), "why" (one sentence on why it matters for this deck) '
    "and \"cards\": at most three cards from that strategy's own candidates, each an "
    'object with "name" (exactly as given) and "reason" (why it fits this deck, at '
    "most 120 characters). Never name a card that is not in that strategy's "
    "candidates, and never one from \"cards\": the deck already plays those. "
    "Prefer cards that fit the deck's theme and its commander over the "
    "merely popular.\n"
    "Write about this deck: a sentence that would fit any deck says nothing. "
    "Plain sentences only: no Markdown, no lists inside strings, no links, no "
    "leading + or -. "
)

#: How the last line of the prompt names each language, in English (the
#: prompt is English), with the tone the site takes in it (Q8).
WRITE_IN = {
    "en": "English",
    "de": "German, addressing the reader informally (du)",
    "fr": "French, addressing the reader formally (vous)",
    "it": "Italian, addressing the reader informally (tu)",
    "es": "Spanish, addressing the reader informally (tú)",
    "pt-br": "Brazilian Portuguese, addressing the reader as você",
    # Without these, shroud became a "shout" (シャウト) and Chainer チャイナー.
    # Tutors still turn into チュートリアル now and then: the category name in
    # the facts is English. Open until Japanese goes on (Q5).
    "ja": "Japanese, in the polite style (です・ます), with the terms printed on Japanese "
          "cards (速攻 haste, 呪禁 hexproof, 被覆 shroud; a tutor is a サーチ card) and every "
          "card name, the commander's too, in its English spelling",
}


def language() -> str:
    """The active language as a key of `settings.LANGUAGE_NAMES`, else English."""
    code = (get_language() or "en").lower()
    return code if code in settings.LANGUAGE_NAMES else "en"


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


def at_least_one(successes: int, population: int, drawn: int = HAND) -> float:
    """The chance, in percent, of at least one of `successes` in `drawn` cards."""
    # A deck smaller than the cards seen has shown all of itself.
    return 100.0 - hypergeometric(0, population, successes, min(drawn, population))


def facts(deck, readings) -> dict:
    """Everything the prompt says about the deck - and nothing else."""
    from decks.analysis import KARSTEN_MAX, KARSTEN_MIN, analyse
    from simulations.strategies import offered

    analysis = analyse(deck)
    library = [reading for reading in readings if not reading.is_commander]
    population = sum(reading.quantity for reading in library)
    lands = sum(reading.quantity for reading in library if reading.card.is_land)
    usual_min = round(population * KARSTEN_MIN / 99)
    usual_max = round(population * KARSTEN_MAX / 99)
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
        "usual_lands_for_this_size": f"{usual_min}-{usual_max}",
        # Batch F: worked out here - the model read "22 lands" as low against
        # a usual 18-19.
        "lands_compared_with_usual": (
            "below" if lands < usual_min else "above" if lands > usual_max else "within"
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
                labels[key]: round(at_least_one(count, population), 1)
                for key, count in counts.items() if count and key in core
            },
        },
        # Phase 11 D: the strategies' odds, calculated - the run's own numbers
        # do not exist yet when this is written.
        f"chance_of_at_least_one_by_turn_{BY_TURN}_percent": {
            "assumes": f"the opening hand of {HAND} and one draw a turn from turn 2, "
                       "no mulligan",
            "by_category": {
                labels[key]: round(at_least_one(count, population, SEEN_BY_TURN), 1)
                for key, count in counts.items() if count
            },
        },
        "cards": cards,
        # Phase 11 E: the strategies and the catalogue cards that would feed
        # them, for "strategies" to pick from - nothing else may be named.
        "strategies": offered(readings),
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


def write_in(code: str) -> str:
    """The prompt's last line: the language, and the names that stay English."""
    if code not in WRITE_IN or code == "en":
        return "Write in English."
    return (f"Write every text value, the title too, in {WRITE_IN[code]}. Use the Magic "
            "terms players of that language use, the official ones where they exist. Keep "
            "the JSON keys, and every strategy name and card name exactly as given, in "
            "English.")


def messages(deck_facts: dict, code: str = "en") -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT + write_in(code)},
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
    under `limit`. Japanese has no spaces between words: there, a space is
    only one inside a card name, so a cut that would lose more than a third
    is made at the limit instead. The template escapes it as well; this is
    about what it says, not about whether it is safe to put in HTML.
    """
    if not isinstance(value, str):
        raise ValueError("not a string")
    text = " ".join(_MARKDOWN.sub("", _LINK.sub("", value)).split())
    if len(text) > limit:
        cut = text[:limit]
        head = cut.rsplit(" ", 1)[0]
        text = (head if len(head) * 3 >= limit * 2 else cut).rstrip(",;:、，") + "…"
    return text


def _prose(value, limit: int) -> str:
    """`_clean` for a paragraph. Its sentences sometimes come back as a list
    (phase 12 G: 2 of 8 Spanish and Portuguese answers); joined, they are the
    paragraph that was asked for."""
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        value = " ".join(value)
    return _clean(value, limit)


def _optional(value, limit: int) -> str:
    """`_clean`, but a missing or wrong-typed value is just empty."""
    return _clean(value, limit) if isinstance(value, str) else ""


def _points(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("not a list")
    points = [_LEADING.sub("", _clean(item, POINT_MAX)) for item in value[:POINTS_MAX]]
    return [point for point in points if point]


def _strategies(value, offered) -> list[dict]:
    """Mistral's picks, checked against what it was offered (Principle 3).

    An unknown strategy, a card not on that strategy's list (the list holds no
    card of the deck), a second mention - also under another strategy - each is
    dropped without a word, and so is a strategy left without a card. Names are
    stored as we spelled them.
    """
    if not isinstance(value, list):
        return []
    lists = {row["strategy"].casefold(): row for row in offered or []}
    chosen, seen, named = [], set(), set()
    for item in value:
        if len(chosen) == STRATEGIES_MAX:
            break
        if not isinstance(item, dict) or not isinstance(item.get("strategy"), str):
            continue
        label = item["strategy"].strip().casefold()
        row = lists.get(label)
        if row is None or label in seen:
            continue
        names = {card["name"].casefold(): card["name"] for card in row["candidates"]}
        cards = []
        for card in item.get("cards") if isinstance(item.get("cards"), list) else []:
            name = (names.get(card["name"].strip().casefold())
                    if isinstance(card, dict) and isinstance(card.get("name"), str) else None)
            if name is None or name in named:
                continue
            named.add(name)
            cards.append({"name": name, "reason": _optional(card.get("reason"), REASON_MAX)})
            if len(cards) == CARDS_MAX:
                break
        if cards:
            seen.add(label)
            chosen.append({"key": _KEY_BY_LABEL[label], "label": row["strategy"],
                           "why": _optional(item.get("why"), WHY_MAX), "cards": cards})
    return chosen


_KEY_BY_LABEL = {label.casefold(): key for key, label in SEEN_ROLES + SEEN_STRATEGY_ROLES}


def parse(content: str, offered: list[dict] | None = None) -> dict:
    """Mistral's answer, checked. Raises ValueError for anything else.

    `offered` is the facts' "strategies": the only cards it may name.
    """
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    checked = {
        # Optional (P4): without a title the block starts with "Feel".
        "title": _optional(data.get("title"), TITLE_MAX).strip(" .\"'“”"),
        "tagline": _optional(data.get("tagline"), TAGLINE_MAX),
        "feel": _prose(data.get("feel"), FEEL_MAX),
        "strengths": _points(data.get("strengths")),
        "weaknesses": _points(data.get("weaknesses")),
        "tactics": _prose(data.get("tactics"), TACTICS_MAX),
        # Optional too (P6): without picks the block shows its fallback.
        "strategies": _strategies(data.get("strategies"), offered),
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
    text stays in the row until a new one replaces it. It is written in the
    language active now - the request of whoever started it (Q3).
    """
    from simulations import tasks
    from simulations.models import DeckSummary

    summary, _ = DeckSummary.objects.update_or_create(
        deck=deck,
        defaults={
            "fingerprint": print_ or fingerprint(deck),
            "status": DeckSummary.Status.PENDING,
            "charged": charged,
            "language": language(),
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
        # The text's own language, for its `lang` (screen readers, fonts).
        "language": stored.language if stored is not None else "",
        "pending": pending,
        "current": current,
        "failed": stored is not None and stored.status == DeckSummary.Status.FAILED,
        "offer": offer,
        "short": short,
        "remaining": remaining,
    }


__all__ = ["BY_TURN", "PROMPT_VERSION", "SEEN_BY_TURN", "WRITE_IN", "at_least_one", "begin",
           "claim", "due", "facts", "fingerprint", "language", "messages", "missing", "parse",
           "state", "write_in"]
