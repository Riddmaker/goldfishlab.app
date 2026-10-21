"""Language-model drafts of a card's building blocks (card model phase 5, PR 20).

Decided on 13 October: drafts come as a review queue, and first today's
Mistral model is compared with a larger one on a sample of cards. This module
is that comparison. A model reads a card's printed text and answers with the
card's primitives (`simulations.primitives`) in the shape an override has;
on cards our reader reads, the reading is the answer key.

Nothing here is stored, shown to a player or counted against the summaries'
daily budget: `manage.py draft_compare` is run by hand and writes its report to
`.data/`. The prompt holds card text and the primitives' schema only - no
deck, no user.
"""

import json
import random
import time
from dataclasses import dataclass, field

from simulations import mistral, primitives

#: An answer is a handful of small objects, but a reasoning model (Mistral
#: Large 4) spends its thinking from the same budget before it answers.
MAX_TOKENS = 8_000
#: The schema and the examples are the prompt's bulk; the card is short.
TEMPERATURE = 0.0


def _schema() -> str:
    lines = []
    for primitive in primitives.PRIMITIVES:
        fields = primitives.ITEM_FIELDS.get(primitive.key) or primitives.FIELDS.get(
            primitive.key, ())
        described = "; ".join(
            f"{each.name} ({each.kind}"
            + (": " + ", ".join(str(choice) for choice, _label in each.choices)
               if each.choices else "")
            + f") {each.label}" for each in fields)
        shape = "a list of objects" if primitive.many else "an object"
        if primitive.key in ("treasures", "mox"):
            shape = "a number" if primitive.key == "treasures" else "a word"
        lines.append(f"- {primitive.key}: {primitive.help} As {shape}."
                     + (f" Keys: {described}." if described else ""))
    return "\n".join(lines)


SYSTEM = """You read Magic: The Gathering cards for a goldfish simulator (no opponent \
acts). Answer with one JSON object: the card's building blocks below, by key, in exactly \
the shapes shown. Leave a key out when the card does not do that. Answer {} when the card \
does none of them. Never invent keys.

Building blocks:
"""


def card_text(card) -> str:
    return f"{card.name}\n{card.type_line}\n{card.oracle_text}".strip()


def truth(profile) -> dict:
    """The card's primitives as our reader read them, empty ones left out."""
    found = {}
    for primitive in primitives.PRIMITIVES:
        value = primitives.reading_of(primitive.key, profile)
        if value not in (None, [], {}, 0, "") and value != primitive.off:
            found[primitive.key] = value
    return found


def messages(card, examples) -> list[dict]:
    """The prompt: schema, a few cards with their answers, then the card."""
    shown = []
    for example, answer in examples:
        shown += [{"role": "user", "content": card_text(example)},
                  {"role": "assistant", "content": json.dumps(answer, sort_keys=True)}]
    return [{"role": "system", "content": SYSTEM + _schema()}, *shown,
            {"role": "user", "content": card_text(card)}]


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True)


def parse(content: str) -> dict | None:
    """The model's answer, keys checked the way a form's are; None when it
    is no JSON object. A key whose value fails `primitives.validate` is kept
    out and counted as wrong."""
    try:
        answer = json.loads(content)
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    valid = {}
    for key, value in answer.items():
        if key not in primitives.BY_KEY or value in (None, [], {}, 0, ""):
            continue
        try:
            primitives.validate(key, value)
        except primitives.PrimitiveError:
            continue
        valid[key] = value
    return valid


@dataclass
class Score:
    """One model's answers against the answer key."""

    model: str
    cards: int = 0
    #: The answer was no JSON object, or the call failed.
    unusable: int = 0
    #: Why calls failed, by the error's words, so a failing model shows why.
    errors: dict = field(default_factory=dict)
    #: Every primitive of the card right, and nothing more.
    exact: int = 0
    #: Per primitive: right, wrong or missing (the key had it), extra (it did not).
    right: dict = field(default_factory=dict)
    missed: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    answers: list = field(default_factory=list)

    def add(self, name: str, expected: dict, answer: dict | None) -> None:
        self.cards += 1
        if answer is None:
            self.unusable += 1
            answer = {}
        if {key: _canonical(value) for key, value in answer.items()} == {
                key: _canonical(value) for key, value in expected.items()}:
            self.exact += 1
        for key in expected.keys() | answer.keys():
            if key not in answer:
                self.missed[key] = self.missed.get(key, 0) + 1
            elif key not in expected:
                self.extra[key] = self.extra.get(key, 0) + 1
            elif _canonical(answer[key]) == _canonical(expected[key]):
                self.right[key] = self.right.get(key, 0) + 1
            else:
                self.missed[key] = self.missed.get(key, 0) + 1
        self.answers.append({"card": name, "expected": expected, "answer": answer})

    def summary(self) -> dict:
        return {"model": self.model, "cards": self.cards, "exact": self.exact,
                "unusable": self.unusable, "errors": self.errors, "right": self.right,
                "missed": self.missed,
                "extra": self.extra, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "seconds": round(self.seconds, 1)}


def sample(profiles, size: int, seed: int) -> list:
    """``size`` profiles with at least one primitive read, drawn reproducibly."""
    chosen = sorted((profile for profile in profiles if truth(profile)),
                    key=lambda profile: profile.oracle_card.name)
    return random.Random(seed).sample(chosen, min(size, len(chosen)))  # noqa: S311  (a reproducible sample, not a secret)


def compare(models: list[str], cards: list, examples: list) -> list[Score]:
    """Every card through every model; the examples are never among the cards."""
    scores = []
    for name in models:
        score = Score(model=name)
        for profile in cards:
            card = profile.oracle_card
            started = time.monotonic()
            try:
                completion = mistral.complete(messages(card, examples), max_tokens=MAX_TOKENS,
                                              temperature=TEMPERATURE, model_name=name)
            except mistral.MistralError as exc:
                score.errors[str(exc)] = score.errors.get(str(exc), 0) + 1
                score.add(card.name, truth(profile), None)
                continue
            finally:
                score.seconds += time.monotonic() - started
            score.prompt_tokens += completion.prompt_tokens
            score.completion_tokens += completion.completion_tokens
            score.add(card.name, truth(profile), parse(completion.content))
        scores.append(score)
    return scores
