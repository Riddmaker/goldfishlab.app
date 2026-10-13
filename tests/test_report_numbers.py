"""P20 F4: the numbers the deck page and the report state, and how exact they are.

Lauf 1 found them disagreeing with each other or missing:

* E1.5: the land tile counted a card with a land on its back (Fell the
  Profane // Fell Mire) as a land; the list "What is in it" did not.
* E1.30: the bracket tile said "3" for a deck with no Game Changers, the report
  "Bracket 1" by speed; now the tile is a floor and the report puts both
  together.
* E1.29: without looked-up combos the report had no bracket section at all.
* E1.19: "four lands on turn 4", the number of the article, was on the precon
  pages only.
* E1.22: "differences below one point are noise, whatever the number of
  games" - wrong; each share now says how exact it is.
* E1.35: "0 of 99 cards" beside "1 card needs you".
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone, translation

from cards import profiles
from cards.models import OracleCard
from combos.models import ComboLookup
from decks import analysis as deck_analysis
from decks import services as deck_services
from decks.models import DeckCard
from simulation import analysis
from simulation import combos as engine_combos
from simulations import deck_cards, numbers, report, review
from simulations.engine import adapter, runner
from simulations.models import SimulationRun

User = get_user_model()


def _oracle(name, type_line, **extra):
    """An unsaved card as Scryfall would send it."""
    return OracleCard.from_scryfall({"oracle_id": str(uuid.uuid4()), "name": name,
                                     "type_line": type_line, **extra})


# --- E1.5: lands by the front face -------------------------------------------------


@pytest.mark.parametrize(("name", "type_line", "land", "back"), [
    ("Fell the Profane // Fell Mire", "Sorcery // Land", False, True),
    ("Westvale Abbey // Ormendahl, Profane Prince",
     "Land // Legendary Creature — Eldrazi Demon", True, False),
    ("Dryad Arbor", "Land Creature — Forest Dryad", True, False),
    ("Fire // Ice", "Instant // Instant", False, False),
    ("Swamp", "Basic Land — Swamp", True, False),
])
def test_a_card_is_a_land_by_its_front_face(name, type_line, land, back):
    card = _oracle(name, type_line)

    assert card.is_land is land
    assert card.has_land_back is back


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="numbers@example.com", password="pw-test-only")


@pytest.fixture
def fell_deck(owner):
    """35 Swamps, Sol Ring and a sorcery with a land on its back."""
    deck = deck_services.import_deck(owner=owner, raw=b"35 Swamp\n1 Sol Ring\n",
                                     name="Fell").deck
    fell = _oracle("Fell the Profane // Fell Mire", "Sorcery // Land",
                   legalities={"commander": "legal"}, color_identity=["B"],
                   card_faces=[{"name": "Fell the Profane", "mana_cost": "{2}{B}{B}",
                                "type_line": "Sorcery",
                                "oracle_text": "Destroy target creature or planeswalker."},
                               {"name": "Fell Mire", "mana_cost": "",
                                "type_line": "Land",
                                "oracle_text": "As Fell Mire enters, you may pay 3 life. If "
                                               "you don't, it enters tapped.\n{T}: Add {B}."}])
    fell.save()
    profiles.rebuild(OracleCard.objects.filter(pk=fell.pk))
    DeckCard.objects.create(deck=deck, oracle_card=fell, quantity=1)
    return deck


@pytest.mark.django_db
def test_the_land_tile_counts_what_the_list_counts(fell_deck):
    found = deck_analysis.analyse(fell_deck)
    readings = adapter.readings(fell_deck)
    type_bars, _ = deck_cards.bars(deck_cards.cards(readings, review.queue(fell_deck, readings)))
    listed = next(bar for bar in type_bars if bar.value == "land")

    assert found.land_count == listed.count == 35
    assert found.land_backs == 1


@pytest.mark.django_db
def test_the_tile_names_the_cards_with_a_land_on_the_back(client, owner, fell_deck):
    client.force_login(owner)

    body = client.get(fell_deck.get_absolute_url()).content.decode()

    assert "+ 1 double-faced card with a land on the back" in body


# --- E1.30: the bracket tile is a floor --------------------------------------------


@pytest.mark.parametrize(("changers", "floor"), [(0, 1), (1, 3), (3, 3), (4, 4), (9, 4)])
def test_the_game_changers_set_a_floor(changers, floor):
    assert deck_analysis.bracket_floor(changers) == floor


@pytest.mark.django_db
def test_a_deck_without_game_changers_may_be_bracket_1_or_2(client, owner, fell_deck):
    client.force_login(owner)
    found = deck_analysis.analyse(fell_deck)

    body = client.get(fell_deck.get_absolute_url()).content.decode()

    assert found.bracket_floor == 1
    assert "1–2" in body and "possible: no Game Changers" in body


@pytest.mark.django_db
def test_the_why_names_the_game_changers_and_counts_the_commander(fell_deck):
    changers = list(OracleCard.objects.filter(game_changer=True).order_by("name")[:2])
    DeckCard.objects.create(deck=fell_deck, oracle_card=changers[0], quantity=1)
    fell_deck.commander = changers[1]
    fell_deck.save()

    found = deck_analysis.analyse(fell_deck)

    assert sorted(found.game_changers) == [card.name for card in changers]
    assert found.bracket_floor == 3
    assert "at least Bracket 3" in found.bracket_verdict.detail
    assert all(card.name in found.bracket_verdict.detail for card in changers)
    assert sorted(deck_analysis.game_changers(fell_deck)) == sorted(found.game_changers)


# --- E1.35: one count for the cards that need their owner ---------------------------


@pytest.mark.django_db
def test_the_sentence_and_the_marker_count_the_same_cards(client, owner):
    deck = deck_services.import_deck(
        owner=owner, raw=b"1 Mana Drain\n1 Sol Ring\n34 Swamp\n", name="Drain").deck
    client.force_login(owner)

    response = client.get(deck.get_absolute_url())
    body = response.content.decode()
    deck.refresh_from_db()

    assert response.context["unread_cards"] == len(review.queue(deck))
    if deck.open_questions:
        assert "of them still need" in body
    else:
        assert "The engine read all" in body or "You have looked at each" in body
    assert "card reader could not resolve" not in body


# --- the report ------------------------------------------------------------------------


def _finished(owner, deck, *, turns=9, watch=()):
    result = analysis.as_json(analysis.run(40, turns=turns, seed=3, watch=watch))
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, games_done=40, turns=turns, seed=3,
        status=SimulationRun.Status.DONE, finished_at=timezone.now(), result=result,
    )


@pytest.mark.django_db
def test_a_run_without_combos_still_has_a_bracket_section(client, owner, fell_deck):
    run = _finished(owner, fell_deck)
    client.force_login(owner)

    body = client.get(run.get_absolute_url()).content.decode()

    assert 'id="brackets"' in body
    assert "Without its combos looked up" in body
    assert f'{fell_deck.get_absolute_url()}#combos' in body
    assert "By Game Changers: none, so Bracket 1 or 2 is possible." in body
    assert "Together:" not in body, "speed said nothing, so there is nothing to add up"


@pytest.mark.django_db
def test_no_game_ending_combo_known_reads_as_bracket_1_by_speed(owner, fell_deck):
    ComboLookup.objects.create(deck=fell_deck, fetched_at=timezone.now())
    run = _finished(owner, fell_deck)

    brackets = report.build(run)["brackets"]

    assert brackets["no_combos"] and brackets["combos"] == report.NONE_KNOWN
    assert brackets["together"] == 1


@pytest.mark.django_db
def test_together_the_higher_floor_counts(owner, fell_deck):
    ComboLookup.objects.create(deck=fell_deck, fetched_at=timezone.now())
    for card in OracleCard.objects.filter(game_changer=True)[:4]:
        DeckCard.objects.create(deck=fell_deck, oracle_card=card, quantity=1)
    run = _finished(owner, fell_deck)

    brackets = report.build(run)["brackets"]

    assert brackets["floor"] == 4 and brackets["game_changers"] == 4
    assert brackets["together"] == 4


@pytest.mark.django_db
def test_a_fast_combo_and_no_game_changers_together_read_as_its_bracket(owner, fell_deck):
    swamp = engine_combos.Watch(key="x", requirements=(engine_combos.Requirement(name="Swamp"),),
                                wins=True)
    run = _finished(owner, fell_deck, watch=[swamp])

    brackets = report.brackets(run, runner.read(run.result), timed_combos=True)

    assert brackets["cedh"] and brackets["together"] == report.CEDH[0]


@pytest.mark.django_db
def test_the_report_says_four_lands_on_turn_4_as_the_precon_pages_do(client, owner, fell_deck):
    run = _finished(owner, fell_deck)
    client.force_login(owner)
    said = numbers.of(run)
    turn = runner.read(run.result)["turn_stats"][3]["lands"].counts

    body = client.get(run.get_absolute_url()).content.decode()

    assert said.on_curve == 100 * sum(n for lands, n in turn.items() if lands >= 4) / 40
    assert 'id="glance"' in body
    first = numbers.sentences(said)[0]
    assert "four lands in play on turn 4" in first and first in body


@pytest.mark.django_db
def test_a_run_of_three_turns_has_no_turn_4(owner, fell_deck):
    run = _finished(owner, fell_deck, turns=3)

    said = numbers.of(run)

    assert said.on_curve is None and said.mana_turn_4 is None
    assert len(numbers.sentences(said)) == 1


@pytest.mark.parametrize(("share", "games", "points"), [
    (50, 10_000, 0.98), (80, 10_000, 0.78), (50, 2_000, 2.19), (80, 2_000, 1.75),
    (100, 2_000, 0.0),
])
def test_a_share_says_how_exact_it_is(share, games, points):
    assert numbers.margin(share, games) == pytest.approx(points, abs=0.01)


def test_no_games_is_no_margin():
    assert numbers.margin(50, 0) == 0.0


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "de", "es", "fr", "it", "ja", "pt-br"])
def test_the_methodology_no_longer_calls_a_point_noise(client, settings, language):
    settings.LANGUAGES = [(code, code) for code in ("en", "de", "es", "fr", "it", "ja",
                                                     "pt-br")]
    with translation.override(language):
        url = reverse("methodology")

    body = client.get(url, HTTP_ACCEPT_LANGUAGE=language).content.decode()

    assert "±0.5" not in body and "±0,5" not in body
    assert 'id="on-curve"' in body
