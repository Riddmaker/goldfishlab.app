"""More about this card (B-22, PR 1): what a card is strong with, and its combos.

What these tests are for, in order of how much they matter:

1. **Nothing is guessed.** A partner comes from a pair, an engine or a theme
   the deck page found; a combo from Commander Spellbook, with its link.
2. **Commander Spellbook is asked only on the button, and politely**: once a
   week per card, not again right after a failure, never while the card page
   renders - and a failure keeps the older answer, dated.
3. **Only the owner sees it**, every name is escaped, and the page works
   without JavaScript.
"""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from cards.models import OracleCard
from combos import services, spellbook
from combos.models import CardComboLookup
from decks import insight
from decks import services as deck_services
from tests.support import Calls

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "pw-for-test-only"
DECK_LIST = (b"1 Exquisite Blood\n1 Carrion Feeder\n1 Mikaeus, the Unhallowed\n"
             b"1 Gravecrawler\n1 Blood Artist\n30 Swamp\n")


# --- 1. strong with ---------------------------------------------------------


def _pair(first, second, sentence, gone=()):
    return SimpleNamespace(cards=(first, second), sentence=sentence, gone=gone)


def _reading(pairs=(), engines=(), themes=()):
    return SimpleNamespace(pairs=list(pairs), more_pairs=[], engines=list(engines),
                           themes=list(themes))


def _theme(*parts, label="Aristocrats"):
    return SimpleNamespace(label=label, subject="", parts=[
        SimpleNamespace(label=part_label, count=len(cards) * 2, cards=tuple(cards))
        for part_label, cards in parts])


def test_a_pair_and_an_engine_name_the_partner_with_their_sentence():
    engine = SimpleNamespace(cards=("Viscera Seer", "Blood Artist", "Bitterblossom"),
                             sentence="the engine", gone=())
    reading = _reading(pairs=[_pair("Viscera Seer", "Blood Artist", "the pair")],
                       engines=[engine])

    strong = insight.strong_with(reading, "Blood Artist")

    assert [(p.name, p.kind, p.why) for p in strong.worked] == [
        ("Viscera Seer", insight.PAIR, "the pair"),
        ("Bitterblossom", insight.ENGINE, "the engine")]


def test_the_other_pieces_of_its_theme_are_the_kinds_it_is_strong_with():
    reading = _reading(themes=[_theme(("Sacrifice outlets", ["Viscera Seer"]),
                                      ("Death payoffs", ["Blood Artist"]),
                                      ("Fodder", []))])

    strong = insight.strong_with(reading, "Blood Artist")

    assert [(k.label, k.count, k.cards) for k in strong.kinds] == [
        ("Sacrifice outlets", 1, ("Viscera Seer",)), ("Fodder", 0, ())]  # cards, not copies
    assert [p.name for p in strong.partners] == ["Viscera Seer"]
    assert strong.worked == ()  # named under its kind, not twice


def test_a_card_outside_every_theme_is_strong_with_nothing():
    reading = _reading(pairs=[_pair("Viscera Seer", "Blood Artist", "x")],
                       themes=[_theme(("Sacrifice outlets", ["Viscera Seer"]))])

    assert not insight.strong_with(reading, "Sol Ring")


def test_a_resting_pair_names_no_partner_and_the_list_has_a_cap():
    many = [_pair("Hub", f"Card {i}", "x") for i in range(20)]
    reading = _reading(pairs=[_pair("Hub", "Gone", "x", gone=("Gone",)), *many])

    strong = insight.strong_with(reading, "Hub")

    assert len(strong.partners) == insight.PARTNERS_MAX
    assert "Gone" not in {p.name for p in strong.partners}
    assert "Hub" not in {p.name for p in strong.partners}


# --- 2. the Spellbook client ------------------------------------------------


def test_one_card_is_asked_for_by_its_whole_name(monkeypatch):
    asked = {}
    payload = json.loads((FIXTURES / "spellbook_find_my_combos.json").read_text())
    variants = payload["results"]["included"]

    def fake(url, params, **kwargs):
        asked.update(url=url, **params)
        return {"results": variants}
    monkeypatch.setattr(spellbook, "_get", fake)

    records = spellbook.combos_of_card('Bloodline Keeper // Lord "of" Lineage')

    assert asked["url"] == spellbook.VARIANTS
    assert asked["q"] == 'card="Bloodline Keeper // Lord of Lineage" legal:commander'
    assert asked["ordering"] == "-popularity"
    assert [r.spellbook_id for r in records] == [v["id"] for v in variants]
    assert spellbook.combos_of_card(' " ') == []


# --- the cache --------------------------------------------------------------


@pytest.fixture
def owner(catalogue):
    return get_user_model().objects.create_user(email="insight@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(owner=owner, raw=DECK_LIST, name="Insight").deck


@pytest.fixture
def records():
    payload = json.loads((FIXTURES / "spellbook_find_my_combos.json").read_text())
    return [*spellbook.parse(payload).included, *spellbook.parse(payload).almost]


@pytest.fixture
def asked(monkeypatch, records):
    """Count the calls to Commander Spellbook and answer from the fixture."""
    calls = Calls()

    def fake(name, **kwargs):
        calls.append(name)
        if calls.failure is not None:
            raise calls.failure
        return [r for r in records if name in {c.name for c in r.cards}]
    monkeypatch.setattr(spellbook, "combos_of_card", fake)
    return calls


def _card(name):
    return OracleCard.objects.get(name=name)


def _held(deck):
    from decks.themes import theme_cards
    return [card.oracle_id for card in theme_cards(deck)]


@pytest.mark.django_db
def test_the_combos_of_a_card_held_by_the_deck_come_first(deck, asked):
    combos = services.card_combos(_card("Exquisite Blood"), _held(deck))

    assert asked == ["Exquisite Blood"]
    assert combos.shown and combos.fetched_at is not None and not combos.failed
    assert [c.in_deck for c in combos.shown] == sorted(
        (c.in_deck for c in combos.shown), reverse=True)
    for combo in combos.shown:
        assert "Exquisite Blood" not in combo.missing
        assert combo.url.startswith("https://commanderspellbook.com/combo/")


@pytest.mark.django_db
def test_a_card_is_asked_for_once_a_week(deck, asked):
    card = _card("Exquisite Blood")
    services.card_combos(card, _held(deck))
    services.card_combos(card, _held(deck))
    assert len(asked) == 1

    CardComboLookup.objects.filter(oracle_card=card).update(
        fetched_at=timezone.now() - services.MAX_AGE_CARD - timedelta(hours=1),
        attempted_at=timezone.now() - services.COOLDOWN - timedelta(minutes=1))
    services.card_combos(card, _held(deck))
    assert len(asked) == 2


@pytest.mark.django_db
def test_a_failure_keeps_the_older_answer_and_waits_before_asking_again(deck, asked):
    card = _card("Exquisite Blood")
    first = services.card_combos(card, _held(deck))
    CardComboLookup.objects.filter(oracle_card=card).update(
        fetched_at=timezone.now() - timedelta(days=30),
        attempted_at=timezone.now() - timedelta(hours=1))
    asked.failure = spellbook.SpellbookError("down")

    later = services.card_combos(card, _held(deck))
    services.card_combos(card, _held(deck))

    assert later.failed and later.shown == first.shown
    assert later.fetched_at < timezone.now() - timedelta(days=29)
    assert len(asked) == 2  # the third call rests


@pytest.mark.django_db
def test_without_fetch_nothing_is_asked(deck, asked):
    combos = services.card_combos(_card("Exquisite Blood"), _held(deck), fetch=False)

    assert asked == [] and combos.shown == () and combos.fetched_at is None


# --- 3. on the page ---------------------------------------------------------


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _urls(deck, name):
    card = _card(name)
    return (reverse("simulations:insight", args=[deck.id, card.pk]),
            reverse("simulations:annotate", args=[deck.id, card.pk]))


@pytest.mark.django_db
def test_the_card_page_has_the_button_and_asks_nobody(signed_in, deck, asked):
    insight_url, page_url = _urls(deck, "Exquisite Blood")

    body = signed_in.get(page_url).content.decode()

    assert "data-insight-button" in body
    assert f'hx-get="{insight_url}"' in body and 'href="?insight=1#insight"' in body
    assert asked == []


@pytest.mark.django_db
def test_the_button_shows_the_combos_with_their_sources(signed_in, deck, asked):
    insight_url, page_url = _urls(deck, "Exquisite Blood")

    fragment = signed_in.get(insight_url).content.decode()
    in_place = signed_in.get(page_url + "?insight=1").content.decode()

    for body in (fragment, in_place):
        assert "data-insight-combo" in body
        assert "commanderspellbook.com/combo/" in body
        assert "Combos: Commander Spellbook · Card data: Scryfall" in body
    assert "<html" not in fragment


@pytest.mark.django_db
def test_somebody_elses_deck_and_a_basic_land_have_none(client, deck, asked):
    insight_url, _page = _urls(deck, "Exquisite Blood")
    stranger = get_user_model().objects.create_user(email="x@example.com", password=PASSWORD)
    client.force_login(stranger)
    assert client.get(insight_url).status_code == 404

    client.force_login(deck.owner)
    swamp_url, swamp_page = _urls(deck, "Swamp")
    assert client.get(swamp_url).status_code == 404
    assert "data-insight-button" not in client.get(swamp_page).content.decode()
    assert asked == []


def test_every_name_is_escaped():
    from django.template.loader import render_to_string

    evil = "<script>alert(1)</script>"
    strong = insight.StrongWith(
        partners=(insight.Partner(name=evil, kind=insight.PAIR, why=evil),),
        kinds=(insight.Kind(label="Fodder", theme="Aristocrats", subject="", count=1,
                            cards=(evil,)),))
    combos = services.CardCombos(shown=(services.CardCombo(
        spellbook_id="1-2", cards=(evil, "Blood Artist"), produces=(evil,), missing=(evil,),
        needs=(evil,), popularity=1),), total=1)

    html = render_to_string("simulations/_insight.html",
                            {"strong": strong, "card_combos": combos})

    assert "<script>alert" not in html
    assert html.count("&lt;script&gt;") == 7


def test_it_speaks_the_page_language():
    from django.template.loader import render_to_string
    from django.utils import translation

    with translation.override("de"):
        html = render_to_string("simulations/_insight.html", {
            "strong": insight.StrongWith(), "card_combos": services.CardCombos()})

    assert "Stark mit" in html and "Kartendaten: Scryfall" in html
