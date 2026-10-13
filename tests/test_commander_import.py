"""The command zone from import to deck page (P20 F2a).

Lauf 1: a Moxfield file naming Krark and Thrasios under `Commander` imported
with "No commander set - the file did not mark one"; the choice offered every
card, Bayou first and pre-selected; a commander once saved could not be
changed; and the legality panel said "✓ Commander is legal - Bayou".
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards import profiles
from cards.models import OracleCard
from decks import analysis, services
from decks.models import CommanderIssue
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

PARTNER = "Partner (You can have two commanders if both have partner.)"


@pytest.fixture
def pairs(catalogue):
    """Partners and a Background, which the offline sample does not hold."""

    def made(name, type_line, text, identity, keywords=()):
        card = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, color_identity=identity,
            keywords=list(keywords), legalities={"commander": "legal"}, mana_cost="{2}",
            cmc=2)
        profiles.rebuild(OracleCard.objects.filter(pk=card.pk))
        return card

    return {
        "krark": made("Krark, the Thumbless", "Legendary Creature — Goblin Wizard", PARTNER,
                      ["R"], ["Partner"]),
        "thrasios": made("Thrasios, Triton Hero", "Legendary Creature — Merfolk Wizard",
                         "{4}: Scry 1.\n" + PARTNER, ["G", "U"], ["Partner"]),
        "wilson": made("Wilson, Refined Grizzly", "Legendary Creature — Bear Warrior",
                       "Choose a Background (You can have a Background as a second "
                       "commander.)\nReach, trample", ["G"], ["Choose a background"]),
        "acolyte": made("Acolyte of Bahamut", "Legendary Enchantment — Background",
                        "Commander creatures you own have ward {1}.", ["G"]),
    }


@pytest.fixture
def owner(catalogue):
    return get_user_model().objects.create_user(email="lead@example.com",
                                                password="pw-for-test-only")


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _import(owner, text: str):
    return services.import_deck(owner=owner, raw=text.encode(), name="")


# --- what the file marks ---------------------------------------------------


def test_two_marked_partners_both_lead_the_deck(pairs, owner):
    outcome = _import(owner, "Commander\n1 Krark, the Thumbless\n1 Thrasios, Triton Hero\n\n"
                             "Deck\n1 Sol Ring\n1 Chainer, Dementia Master\n")
    deck = outcome.deck

    assert [card.front_name for card in deck.commanders] == ["Krark, the Thumbless",
                                                            "Thrasios, Triton Hero"]
    assert outcome.record.commander_issue == ""
    assert deck.card_count == 2, "both left the 99 for the command zone"
    assert deck.total_with_commander == 4
    assert deck.name == "Krark, the Thumbless + Thrasios, Triton Hero"


def test_a_background_goes_second_whatever_the_file_order(pairs, owner):
    deck = _import(owner, "// Commander\n1 Acolyte of Bahamut\n1 Wilson, Refined Grizzly\n"
                          "// Deck\n1 Sol Ring\n").deck

    assert [card.front_name for card in deck.commanders] == ["Wilson, Refined Grizzly",
                                                            "Acolyte of Bahamut"]


@pytest.mark.parametrize(("text", "issue", "sentence"), [
    ("1 Sol Ring\n1 Chainer, Dementia Master\n", CommanderIssue.UNMARKED,
     "The file does not name a commander."),
    ("// Commander\n1 Nosuchcard the Imaginary\n// Deck\n1 Sol Ring\n", CommanderIssue.NOT_FOUND,
     "The file names Nosuchcard the Imaginary as commander, but that card was not found."),
    ("// Commander\n1 Gaea's Cradle\n// Deck\n1 Sol Ring\n", CommanderIssue.NOT_ALLOWED,
     "The file names Gaea's Cradle as commander, but that card cannot lead a Commander deck."),
    ("// Commander\n1 Krark, the Thumbless\n1 Chainer, Dementia Master\n// Deck\n1 Sol Ring\n",
     CommanderIssue.NOT_PAIR,
     "The file names Krark, the Thumbless + Chainer, Dementia Master as commanders, but they "
     "cannot share the command zone."),
])
def test_no_commander_says_which_of_four_reasons(pairs, owner, text, issue, sentence):
    """E1.54: all four used to read "the file did not mark one"."""
    outcome = _import(owner, text)

    assert outcome.deck.commander is None
    assert outcome.record.commander_issue == issue
    assert outcome.record.commander_issue_shown == sentence


def test_a_marked_card_that_may_not_lead_stays_in_the_99(pairs, owner):
    deck = _import(owner, "// Commander\n1 Gaea's Cradle (LEG) 249 *F*\n// Deck\n1 Sol Ring\n").deck

    entry = deck.entries.get(oracle_card__front_name="Gaea's Cradle")
    assert entry.printings, "its printing stays with the card, not in an empty command zone"
    assert deck.commander_printings == {}


# --- what the file's order suggests (E4) -----------------------------------


def test_the_first_lines_out_of_order_are_suggested_never_saved(pairs, owner):
    """Moxfield's text export: the commanders first, then the list A to Z."""
    outcome = _import(owner, "1 Krark, the Thumbless\n1 Thrasios, Triton Hero\n"
                             "1 Chainer, Dementia Master\n1 Sol Ring\n")

    assert outcome.deck.commander is None
    assert outcome.record.suggested_commanders == [str(pairs["krark"].pk),
                                                   str(pairs["thrasios"].pk)]


def test_a_last_block_after_a_blank_line_is_suggested(pairs, owner):
    """Moxfield's MTGO export: the list, a blank line, the commanders."""
    outcome = _import(owner, "1 Chainer, Dementia Master\n1 Sol Ring\n1 Gaea's Cradle\n\n"
                             "1 Krark, the Thumbless\n1 Thrasios, Triton Hero\n")

    assert outcome.record.suggested_commanders == [str(pairs["krark"].pk),
                                                   str(pairs["thrasios"].pk)]


def test_an_alphabetical_list_suggests_nothing(pairs, owner):
    """Archidekt sorts everything: its first line is not a statement."""
    outcome = _import(owner, "1 Chainer, Dementia Master\n1 Kenrith, the Returned King\n"
                             "1 Sol Ring\n")

    assert outcome.record.suggested_commanders == []


def test_the_deck_page_fills_in_the_suggestion_and_asks(pairs, owner, signed_in):
    deck = _import(owner, "1 Krark, the Thumbless\n1 Thrasios, Triton Hero\n"
                          "1 Chainer, Dementia Master\n1 Sol Ring\n").deck

    body = signed_in.get(deck.get_absolute_url()).content.decode()

    assert ("The file lists <strong>Krark, the Thumbless + Thrasios, Triton Hero</strong> first "
            "- are those the commanders?") in body
    assert f'<option value="{pairs["krark"].pk}" selected>' in body
    assert f'value="{pairs["thrasios"].pk}" data-leads="{pairs["krark"].pk}" selected>' in body
    deck.refresh_from_db()
    assert deck.commander is None, "a suggestion is never saved without a click"


# --- choosing by hand ------------------------------------------------------


def test_a_pair_is_chosen_by_hand_and_undone(pairs, owner, signed_in):
    deck = _import(owner, "1 Chainer, Dementia Master\n1 Krark, the Thumbless\n"
                          "1 Sol Ring\n1 Thrasios, Triton Hero\n").deck
    url = reverse("decks:set-commander", args=[deck.id])

    signed_in.post(url, {"commander": str(pairs["krark"].pk),
                         "partner": str(pairs["thrasios"].pk)})
    deck.refresh_from_db()
    assert [card.pk for card in deck.commanders] == [pairs["krark"].pk, pairs["thrasios"].pk]
    assert deck.card_count == 2

    chainer = OracleCard.objects.get(front_name="Chainer, Dementia Master")
    signed_in.post(url, {"commander": str(chainer.pk)})
    deck.refresh_from_db()
    assert deck.commanders == [chainer]
    assert deck.card_count == 3, "both partners went back into the 99"


def test_a_pair_that_may_not_lead_together_is_refused(pairs, owner, signed_in):
    deck = _import(owner, "1 Chainer, Dementia Master\n1 Krark, the Thumbless\n"
                          "1 Sol Ring\n1 Acolyte of Bahamut\n1 Wilson, Refined Grizzly\n").deck
    # Wilson makes the Background a second the page offers - beside Wilson only.

    response = signed_in.post(reverse("decks:set-commander", args=[deck.id]),
                              {"commander": str(pairs["krark"].pk),
                               "partner": str(pairs["acolyte"].pk)}, follow=True)

    deck.refresh_from_db()
    assert deck.commander is None
    assert "Krark, the Thumbless and Acolyte of Bahamut cannot share the command zone." \
        in response.content.decode()


# --- the legality panel ----------------------------------------------------


def _verdict(deck, label_start):
    return next(verdict for verdict in analysis.analyse(deck).legality
                if verdict.message.startswith(label_start))


def test_a_land_as_commander_is_a_legality_problem(pairs, owner):
    """E1.3: "✓ Commander is legal - Bayou". An old deck needs no migration."""
    deck = _import(owner, "1 Sol Ring\n1 Chainer, Dementia Master\n").deck
    deck.commander = OracleCard.objects.get(front_name="Gaea's Cradle")
    deck.save()

    assert not _verdict(deck, "Gaea's Cradle cannot be a commander").ok


def test_a_pair_shares_one_identity(pairs, owner):
    deck = _import(owner, "Commander\n1 Krark, the Thumbless\n1 Thrasios, Triton Hero\n\n"
                          "Deck\n1 Sol Ring\n").deck

    result = analysis.analyse(deck)
    assert result.color_identity == ["G", "R", "U"]
    assert _verdict(deck, "May be commanders").ok
    assert _verdict(deck, "Colour identity").ok


# --- a run without a commander, a deck that is not a Commander deck --------


def test_a_run_without_a_commander_says_so_and_an_old_run_says_nothing(owner, signed_in):
    """E1.4: the report of a run with an empty command zone looked like any other."""
    deck = _import(owner, "1 Sol Ring\n1 Chainer, Dementia Master\n").deck
    without = SimulationRun.objects.create(owner=owner, deck=deck, games_total=1000, seed=1,
                                           commander_names=[], library_size=2)
    old = SimulationRun.objects.create(owner=owner, deck=deck, games_total=1000, seed=1)

    body = signed_in.get(without.get_absolute_url()).content.decode()
    assert "Played without a commander" in body
    assert "this deck had 2 cards" in body
    assert "Casting the commander" not in body

    body = signed_in.get(old.get_absolute_url()).content.decode()
    assert "Played without a commander" not in body
    assert "Casting the commander" in body


def test_a_deck_without_commander_and_100_cards_says_whose_rules(owner, signed_in):
    """E1.81: a 75-card list ran as if it were a Commander deck, without a word."""
    deck = _import(owner, "1 Sol Ring\n1 Chainer, Dementia Master\n").deck

    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "! Not a Commander deck" in body
    assert "This deck has 2 cards and no commander" in body
    assert "Simulate without a commander" in body

    chainer = OracleCard.objects.get(front_name="Chainer, Dementia Master")
    signed_in.post(reverse("decks:set-commander", args=[deck.id]), {"commander": str(chainer.pk)})
    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "Not a Commander deck" not in body
