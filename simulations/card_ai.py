"""What else a card is good for, written by the AI (B-22, PR 2).

The third part of "More about this card", after what the rules found
(`decks.insight`) and Commander Spellbook's combos. Mistral (EU) gets the
card's Oracle text and those of at most `PARTNERS_MAX` cards of the deck it
was found to work with - nothing else, no person's data - and answers in the
page's language as JSON. What it may say is checked here, not trusted:

* a partner it names must be one it was given, exactly; any other item is
  dropped, so it cannot invent a card;
* a partner that shares a Commander Spellbook combo with the card gets that
  combo as its proof (`spellbook`); everything else stays an idea, and the
  page says the whole section is the AI's and unchecked;
* at most `ITEMS_MAX` items of at most `TEXT_MAX` characters each.

One answer per card, partners and language (`CardInsight`): the next person
asking the same gets it free. Only a fresh answer is counted against the
plan (`billing.quotas`, call site 6, in `simulations.views`).
"""

import hashlib
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import get_language_info

from cards.models import OracleCard
from simulations import mistral
from simulations.models import CardInsight

#: The cards of the deck it is shown beside the card itself.
PARTNERS_MAX = 8
ITEMS_MAX = 4
TEXT_MAX = 300
#: An Oracle text longer than this is cut: the longest real ones are sagas
#: and planeswalkers, and the end of them adds little.
ORACLE_MAX = 900
MAX_TOKENS = 700

SYSTEM = (
    "You explain Magic: The Gathering cards to Commander players. You get one card and "
    "up to eight other cards from the same deck, each with its Oracle text. Say what "
    "else the card is good for: uses a player may miss on a first reading, and which of "
    "the other cards it works especially well with, and why.\n"
    "Rules:\n"
    "- Use only what the given Oracle texts say. Make no claim about rulings, the stack, "
    "layers or timing beyond the texts, and name no card that is not given.\n"
    "- A partner is one of the given other cards, its name copied exactly, or null.\n"
    "- Never call anything an infinite combo or a win.\n"
    "- At most {items_max} items, each one or two short sentences.\n"
    "- Write every text in {language}.\n"
    'Answer only with JSON: {{"items": [{{"partner": "<name>" or null, "text": "<text>"}}]}}'
)


def model() -> str:
    return getattr(settings, "MISTRAL_INSIGHT_MODEL", "") or "mistral-small-latest"


def candidates(strong) -> list[str]:
    """The partners the AI is shown: those the rules found, strongest first."""
    return [partner.name for partner in strong.partners][:PARTNERS_MAX]


def partners_hash(names) -> str:
    return hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()


def cached(card: OracleCard, names, language: str) -> CardInsight | None:
    return CardInsight.objects.filter(oracle_card=card, partners_hash=partners_hash(names),
                                      language=language).first()


def write(card: OracleCard, names, language: str, combos=()) -> CardInsight:
    """Ask Mistral, check its answer, keep what survives.

    `combos` are the card's Spellbook combos (`combos.services.CardCombo`),
    the proof a partner may carry. Raises `mistral.MistralError`.
    """
    names = list(names)[:PARTNERS_MAX]
    others = {one.name: one for one in OracleCard.objects.filter(name__in=names)}
    shown = [others[name] for name in names if name in others]
    completion = mistral.complete(messages(card, shown, language), max_tokens=MAX_TOKENS,
                                  temperature=0.2, model_name=model(), effort="",
                                  timeout=mistral.QUICK_TIMEOUT_SECONDS, retry=False)
    items = parse(completion.content, [one.name for one in shown], combos)
    try:
        with transaction.atomic():
            return CardInsight.objects.create(
                oracle_card=card, partners_hash=partners_hash(names), language=language,
                items=items, model=completion.model[:64])
    except IntegrityError:
        # Somebody asked the same a moment earlier: theirs stands.
        return cached(card, names, language)


def messages(card: OracleCard, others, language: str) -> list[dict]:
    def shown(one):
        return {"name": one.name, "type": one.type_line,
                "text": (one.oracle_text or "")[:ORACLE_MAX]}
    name = get_language_info(language)["name"] if language else "English"
    return [
        {"role": "system", "content": SYSTEM.format(language=name, items_max=ITEMS_MAX)},
        {"role": "user", "content": json.dumps(
            {"card": shown(card), "other_cards": [shown(one) for one in others]},
            ensure_ascii=False)},
    ]


def parse(content: str, names, combos=()) -> list[dict]:
    """The items worth keeping from the AI's answer; [] for anything else."""
    try:
        raw = json.loads(content).get("items")
    except (ValueError, AttributeError):
        return []
    if not isinstance(raw, list):
        return []
    names = set(names)
    items = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = " ".join(item["text"].split())[:TEXT_MAX]
        partner = item.get("partner") or ""
        if not text or not isinstance(partner, str) or (partner and partner not in names):
            continue
        proof = next((combo.url for combo in combos
                      if partner and partner in combo.cards), "") if partner else ""
        items.append({"partner": partner, "text": text, "proof": proof})
        if len(items) == ITEMS_MAX:
            break
    return items
