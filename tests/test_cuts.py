"""What could go (deck page v2, PR D, decision D2).

What these tests hold:

1. **A rule picks the candidates**: no role and no theme, or one too many of
   a role over its template band; never the commander, a land, a theme or
   engine card or a combo piece; the most expensive first.
2. **Mistral only gives reasons**: a reason for a card the rule did not name,
   or an empty one, is dropped.
3. **The page** shows the candidates with their art, and the rule's reason
   when Mistral wrote none.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import escape

from decks import services as deck_services
from decks.themes import deck_reading
from simulations import cuts, strategies, summary
from simulations.engine import adapter


def _reading(name, cmc=3, categories=(), *, land=False, commander=False, quantity=1):
    oracle = SimpleNamespace(name=name, front_name=name.split(" // ")[0], cmc=cmc,
                             scryfall_uri=f"https://scryfall.com/{name}",
                             art_uri=f"https://cards.scryfall.io/art/{name}.jpg",
                             image_uri=f"https://cards.scryfall.io/normal/{name}.jpg")
    card = SimpleNamespace(is_land=land, categories=frozenset(categories))
    return SimpleNamespace(oracle_card=oracle, card=card, is_commander=commander,
                           quantity=quantity)


def _names(found):
    return [cut.name for cut in found]


def test_a_card_without_a_role_could_go_the_most_expensive_first():
    readings = [_reading("Cheap Filler", 2), _reading("Big Filler", 6),
                _reading("Bear", 2, ["ramp"])]

    found = cuts.candidates(readings)

    assert _names(found) == ["Big Filler", "Cheap Filler"]
    assert {cut.kind for cut in found} == {cuts.NONE}


def test_never_the_commander_a_land_or_a_kept_card():
    readings = [_reading("Boss", 8, commander=True), _reading("Wastes", 0, land=True),
                _reading("Theme Piece", 7), _reading("Combo Piece // Back", 5),
                _reading("Filler", 1)]

    found = cuts.candidates(readings, keep={"Theme Piece", "Combo Piece"})

    assert _names(found) == ["Filler"]


def test_a_role_over_its_band_gives_up_only_what_it_is_over():
    high = cuts.BAND_HIGH["wipe"]
    readings = [_reading(f"Wipe {index}", 4 + index, ["wipe"]) for index in range(high + 2)]

    found = cuts.candidates(readings)

    assert len(found) == 2
    assert all(cut.kind == cuts.OVER and cut.counted == (("wipe", high + 2),) for cut in found)
    assert found[0].mana_value > found[1].mana_value


def test_a_card_with_a_role_in_its_band_stays():
    readings = [_reading("Wipe", 4, ["wipe"]), _reading("Wipe Tutor", 5, ["wipe", "tutor"])]

    assert cuts.candidates(readings) == []


def test_at_most_the_page_s_number():
    readings = [_reading(f"Filler {index}", index) for index in range(cuts.CUTS_MAX + 3)]

    assert len(cuts.candidates(readings)) == cuts.CUTS_MAX


def test_protected_holds_theme_engine_and_combo_cards_by_front_face_too():
    themes = [SimpleNamespace(cards=("Westvale Abbey // Ormendahl", "Blood Artist"))]
    engines = [SimpleNamespace(cards=("Viscera Seer",))]
    combos = [{"cards": ["Exquisite Blood", "Sanguine Bond"]}]

    kept = cuts.protected(themes, engines, combos)

    assert {"Westvale Abbey", "Blood Artist", "Viscera Seer", "Sanguine Bond"} <= kept


def test_the_prompt_gets_each_card_with_the_rule_s_reason_in_english():
    wipe = cuts.Cut(name="Damnation", mana_value=4, kind=cuts.OVER, counted=(("wipe", 6),))

    assert cuts.Cut(name="X", mana_value=1, kind=cuts.NONE).for_prompt()["why_it_could_go"] \
        == "no role and no part in any theme"
    assert wipe.for_prompt() == {"name": "Damnation", "mana_value": 4,
                                 "why_it_could_go": "Board wipe: 6 in the deck, template 4"}
    assert "4" in wipe.rule and "6" in wipe.rule


# --- Mistral's reasons ----------------------------------------------------------


def _answer(cuts_said):
    return json.dumps({"feel": "Fun.", "strengths": ["Fast."], "weaknesses": [],
                       "tactics": "Win.", "cuts": cuts_said})


def test_only_reasons_for_the_rule_s_cards_are_kept():
    offered = [{"name": "Damnation"}, {"name": "Filler"}]
    said = [{"name": "damnation ", "reason": "Six wipes is plenty."},
            {"name": "Sol Ring", "reason": "Not offered."},
            {"name": "Filler", "reason": ""},
            {"name": "Damnation", "reason": "Twice."},
            "nonsense"]

    content = summary.parse(_answer(said), cuts=offered)

    assert content["cuts"] == [{"name": "Damnation", "reason": "Six wipes is plenty."}]


def test_an_answer_without_cuts_still_parses():
    content = summary.parse(json.dumps({"feel": "Fun.", "strengths": ["Fast."],
                                        "weaknesses": [], "tactics": "Win."}))

    assert content["cuts"] == []


def test_the_prompt_asks_for_reasons_only():
    assert '"cuts"' in summary.SYSTEM_PROMPT
    assert "do not add, drop or argue for keeping one" in summary.SYSTEM_PROMPT


# --- the block ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_block_gives_each_cut_its_art_and_mistral_s_reason():
    readings = [_reading("Filler", 5), _reading("Other Filler", 4)]
    found = cuts.candidates(readings)
    content = {"cuts": [{"name": "Filler", "reason": "Does nothing here."}]}

    block = strategies.block(readings, content, found)

    first, second = block["cuts"]
    assert (first.name, first.reason) == ("Filler", "Does nothing here.")
    assert first.art.endswith("/art/Filler.jpg") and first.image.endswith("/normal/Filler.jpg")
    assert second.reason == ""
    assert second.rule


# --- the deck page -----------------------------------------------------------------

ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


@pytest.mark.django_db
def test_the_deck_page_lists_what_the_rule_names(client, catalogue):
    owner = get_user_model().objects.create_user(email="cuts@example.com",
                                                 password="pw-test-1234")
    deck = deck_services.import_deck(owner=owner, raw=ARCHIDEKT_CSV.read_bytes(),
                                     name="deck", filename="sample.csv").deck
    readings = adapter.readings(deck)
    expected = summary.cut_candidates(readings, deck_reading(deck), summary.combos_of(deck))
    client.force_login(owner)

    html = client.get(reverse("decks:detail", args=[deck.pk])).content.decode()

    assert expected, "the sample deck has cards that could go"
    assert "What could go" in html and "data-cuts" in html
    listed = html[html.index("data-cuts"):]
    positions = [listed.index(escape(cut.name)) for cut in expected]
    assert positions == sorted(positions)
    assert 'class="card-peek-full"' in listed
