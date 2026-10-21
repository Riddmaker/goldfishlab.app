"""Card model phase 5, PR 20: comparing language models drafting a card's
building blocks. No test talks to Mistral."""

import json

import pytest
from django.core.management import CommandError, call_command

from cards.models import DerivedProfile, OracleCard
from simulations import drafts, mistral


def profile(name: str, **fields) -> DerivedProfile:
    card = OracleCard(name=name, type_line="Creature", oracle_text=f"{name} text.")
    return DerivedProfile(oracle_card=card, triggers=fields.pop("triggers", []), **fields)


def test_the_answer_key_leaves_out_what_the_card_does_not_do():
    read = profile("Spy", triggers=[{"event": "resolves", "connive": 1}], treasures=0)
    assert drafts.truth(read) == {"triggers": [{"event": "resolves", "connive": 1}]}


def test_an_answer_is_checked_like_a_form():
    assert drafts.parse("not json") is None
    assert drafts.parse("[1]") is None
    # An unknown key and a value the editor would refuse are left out.
    assert drafts.parse(json.dumps({"treasures": 2, "flying": True,
                                    "triggers": [{"event": "x" * 500}]})) == {"treasures": 2}


def test_a_score_counts_right_missed_and_extra():
    score = drafts.Score(model="m")
    score.add("A", {"treasures": 1}, {"treasures": 1})
    score.add("B", {"treasures": 1, "mox": "discard_land"}, {"treasures": 2, "growth": {}})
    score.add("C", {"treasures": 1}, None)
    assert (score.cards, score.exact, score.unusable) == (3, 1, 1)
    assert score.right == {"treasures": 1}
    assert score.missed == {"treasures": 2, "mox": 1}
    assert score.extra == {"growth": 1}


def test_the_prompt_holds_the_schema_the_examples_and_the_card():
    found = drafts.messages(OracleCard(name="Card", type_line="Artifact", oracle_text="T"),
                            [(OracleCard(name="Example", type_line="Land"), {"treasures": 1})])
    assert [message["role"] for message in found] == ["system", "user", "assistant", "user"]
    assert "triggers:" in found[0]["content"] and "connive" in found[0]["content"]
    assert found[2]["content"] == '{"treasures": 1}' and found[3]["content"].startswith("Card")


def test_every_model_gets_every_card(monkeypatch):
    calls = []

    def complete(messages, *, max_tokens, temperature, model_name):
        calls.append(model_name)
        return mistral.Completion(content='{"treasures": 1}', model=model_name,
                                  prompt_tokens=10, completion_tokens=2)

    monkeypatch.setattr(mistral, "complete", complete)
    cards = [profile("A", treasures=1), profile("B", treasures=2)]
    scores = drafts.compare(["small", "large"], cards, [])
    assert calls == ["small", "small", "large", "large"]
    assert [(score.exact, score.prompt_tokens) for score in scores] == [(1, 20), (1, 20)]


def test_a_failed_call_is_unusable_not_fatal(monkeypatch):
    def complete(*args, **kwargs):
        raise mistral.MistralError("Mistral answered HTTP 500")

    monkeypatch.setattr(mistral, "complete", complete)
    assert drafts.compare(["m"], [profile("A", treasures=1)], [])[0].unusable == 1


@pytest.mark.django_db
def test_the_command_names_only_models_that_exist(monkeypatch):
    monkeypatch.setattr(mistral, "available_models",
                        lambda: {"mistral-medium-2604", "mistral-large-latest"})
    with pytest.raises(CommandError, match="no such model: guessed-model"):
        call_command("draft_compare", "--models", "mistral-medium-2604", "guessed-model")
