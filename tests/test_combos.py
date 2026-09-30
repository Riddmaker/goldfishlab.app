"""Phase 7 §1: looking a deck up against Commander Spellbook.

**No test in this file touches the network.** `spellbook.parse` is a pure
function over one response, and everything above it takes the parsed result -
so the committed fixture, captured from the live API on 2026-09-20, runs
through the identical code path a real lookup does.

`tests/fixtures/spellbook_find_my_combos.json` is assembled from two real
responses and **every record in it is verbatim**. Records were dropped, never
edited: a fixture trimmed *inside* a record lies about the shape of the thing
it stands in for, which is the one thing a fixture must not do.

What it covers, on purpose:

* an **included** combo whose two cards are one in our catalogue and one not
* an **almost included** combo that needs a **template** - `Persist Creature`,
  a Scryfall search rather than a card - which is the honesty trap of this
  whole feature
* a combo **outside the commander's colour identity**, which is counted and
  never stored
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from cards import ingest, profiles
from combos import services, spellbook
from combos.models import Combo, ComboCard, ComboLookup, ComboTemplate, DeckCombo
from decks import services as deck_services

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESPONSE = FIXTURES / "spellbook_find_my_combos.json"
VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
PASSWORD = "pw-for-test-only"

DECK_LIST = (
    b"1 Exquisite Blood\n"
    b"1 Carrion Feeder\n"
    b"1 Mikaeus, the Unhallowed\n"
    b"1 Gravecrawler\n"
    b"1 Blood Artist\n"
    b"30 Swamp\n"
)


@pytest.fixture
def catalogue():
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz", updated_at=VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz", updated_at=VERSION)
    profiles.rebuild()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="combo@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(owner=owner, raw=DECK_LIST, name="Combo test").deck


@pytest.fixture
def results():
    """The fixture, through the same parser a live response goes through."""
    return spellbook.parse(json.loads(RESPONSE.read_text(encoding="utf-8")))


@pytest.fixture
def answered(monkeypatch, results):
    """Make `refresh()` return the fixture instead of calling out.

    Returns a list the test can inspect to prove how many calls were made -
    because "did not call the API" is half of what the cooldown is for.
    """
    calls = []

    def fake(main, commanders=(), **kwargs):
        calls.append((list(main), list(commanders)))
        return results

    monkeypatch.setattr(spellbook, "find_my_combos", fake)
    return calls


# --- parsing, and what it throws away ---------------------------------------


def test_the_fixture_parses_into_the_shape_the_models_take(results):
    assert results.identity == "B"
    assert len(results.included) == 1
    assert len(results.almost) == 3
    assert results.out_of_identity == 1

    combo = results.included[0]
    assert combo.spellbook_id == "690-3966"
    assert {card.name for card in combo.cards} == {"Sanguine Bond", "Exquisite Blood"}
    assert "Infinite lifeloss" in combo.produces
    assert combo.legal_commander is True


def test_the_seventy_percent_that_is_image_urls_never_reaches_a_model(results):
    """The measurement that decided against a bulk mirror, asserted.

    3,350 of a record's 4,756 bytes are ten Scryfall image URLs per card, five
    of them `null`, for cards whose images are already in `OracleCard`. The one
    field worth keeping is `oracleId`, which joins to the catalogue.
    """
    fields = set(vars(results.included[0].cards[0]))

    assert fields == {"oracle_id", "name", "quantity", "must_be_commander", "zones"}
    assert not any("image" in field.lower() for field in fields)
    assert results.included[0].cards[0].oracle_id


def test_a_template_requirement_survives_parsing(results):
    """A combo is named cards *plus categories*, and the categories matter.

    Mikaeus + Carrion Feeder also needs a creature with persist - a Scryfall
    search, not a card anybody can name. Dropping it as detail would let the
    page claim a deck holding both named cards has the combo.
    """
    combo = next(c for c in results.almost if c.spellbook_id == "628-2438--5")

    assert {card.name for card in combo.cards} == {
        "Mikaeus, the Unhallowed", "Carrion Feeder"
    }
    assert [t.name for t in combo.templates] == ["Persist Creature"]
    assert combo.templates[0].scryfall_query.startswith("https://api.scryfall.com/")
    assert combo.needs_templates


def test_an_empty_response_parses_to_an_empty_result():
    """The commonest real answer. It must not raise and must not look broken."""
    results = spellbook.parse({"results": {"identity": "B", "included": [],
                                           "almostIncluded": []}})
    assert results.included == [] and results.almost == []
    assert results.total_seen == 0


# --- storing it -------------------------------------------------------------


def test_a_lookup_writes_the_combos_and_links_the_cards_it_knows(deck, answered):
    lookup = services.refresh(deck)

    assert isinstance(lookup, ComboLookup)
    assert lookup.ok
    assert lookup.entries.filter(kind=DeckCombo.Kind.INCLUDED).count() == 1
    assert lookup.entries.filter(kind=DeckCombo.Kind.ONE_AWAY).count() == 3

    exquisite = ComboCard.objects.get(name="Exquisite Blood")
    assert exquisite.oracle_card is not None, "a card in our catalogue is joined"

    sanguine = ComboCard.objects.get(name="Sanguine Bond")
    assert sanguine.oracle_card is None, "one that is not is still recorded by name"
    assert sanguine.oracle_id, "and keeps the id a later ingest could resolve"


def test_combos_outside_the_commanders_colours_are_counted_and_not_stored(deck, answered):
    """112 of them on the real reference deck. A list would be advice about a
    different deck; a number is a fact about this one."""
    lookup = services.refresh(deck)

    assert lookup.out_of_identity == 1
    assert not Combo.objects.filter(spellbook_id="513-5034--46").exists()


def test_the_missing_card_is_worked_out_from_our_own_record_of_the_deck(deck, answered):
    """Not taken from the API, so the shopping list cannot contradict the page."""
    services.refresh(deck)
    entry = DeckCombo.objects.get(combo_id="2577-4050")

    assert entry.missing == ["Phyrexian Altar"], "Gravecrawler is in the deck; the Altar is not"


def test_a_second_deck_reuses_the_same_combo_rows(owner, deck, answered):
    """The mirror accumulates and deduplicates. That is what replaces the
    656 MB download: the table fills from demand, not from a bulk file."""
    services.refresh(deck)
    before = Combo.objects.count()

    other = deck_services.import_deck(owner=owner, raw=DECK_LIST, name="Second").deck
    services.refresh(other)

    assert Combo.objects.count() == before, "no duplicate combos"
    assert ComboLookup.objects.count() == 2, "but two lookups"
    assert ComboCard.objects.filter(name="Exquisite Blood").count() == 1


def test_looking_up_again_replaces_rather_than_accumulates(deck, answered, results, monkeypatch):
    """An import is somebody saying 'this is the deck now', and so is a lookup.

    Merging would leave a combo on the page after the card that made it was
    cut - the same rule, and the same reason, as the deck importer's replace.
    """
    services.refresh(deck)
    assert DeckCombo.objects.count() == 4

    thinner = replace(results, included=[], almost=results.almost[:1], out_of_identity=0)
    monkeypatch.setattr(spellbook, "find_my_combos", lambda *a, **k: thinner)
    services.refresh(deck, force=True)

    assert DeckCombo.objects.count() == 1
    assert ComboTemplate.objects.filter(combo_id="628-2438--5").count() == 1


# --- knowing when the answer has gone off -----------------------------------


def test_adding_a_card_makes_the_answer_stale(deck, answered):
    services.refresh(deck)
    lookup = services.lookup_for(deck)
    assert not services.is_stale(lookup, deck)

    from cards.models import OracleCard
    from decks.models import DeckCard
    DeckCard.objects.create(
        deck=deck, oracle_card=OracleCard.objects.get(front_name="Necropotence"), quantity=1
    )

    assert services.is_stale(lookup, deck), "different cards, different answer"


def test_changing_only_the_commander_makes_the_answer_stale(deck, answered):
    """Easy to miss: the commander is not a `DeckCard` (trap 7), and it is the
    card that decides the colour identity the whole lookup is filtered by."""
    services.refresh(deck)
    lookup = services.lookup_for(deck)

    from cards.models import OracleCard
    deck.commander = OracleCard.objects.get(front_name="Chainer, Dementia Master")
    deck.save(update_fields=["commander"])

    assert services.is_stale(lookup, deck)


def test_an_old_answer_goes_stale_even_if_the_deck_did_not_change(deck, answered):
    services.refresh(deck)
    lookup = services.lookup_for(deck)

    ComboLookup.objects.filter(pk=lookup.pk).update(
        fetched_at=timezone.now() - services.MAX_AGE - timedelta(days=1)
    )
    lookup.refresh_from_db()

    assert services.is_stale(lookup, deck), "Spellbook publishes new combos continuously"


def test_pressing_the_button_twice_is_refused_politely_and_calls_nobody(deck, answered):
    services.refresh(deck)
    assert len(answered) == 1

    outcome = services.refresh(deck)

    assert isinstance(outcome, services.Refusal)
    assert "free service" in outcome.reason
    assert len(answered) == 1, "the cooldown is about not making the request"


def test_an_empty_deck_is_refused_before_anybody_is_called(owner, answered):
    from decks.models import Deck
    empty = Deck.objects.create(owner=owner, name="Nothing in it")

    outcome = services.refresh(empty)

    assert isinstance(outcome, services.Refusal)
    assert not answered


# --- when Spellbook is not there --------------------------------------------


def test_an_outage_costs_the_page_its_freshness_and_not_its_contents(deck, answered, monkeypatch):
    """Their downtime must not become our bug.

    The entries stay and the date beside them already says how old they are,
    which is worth more than a panel that empties itself.
    """
    services.refresh(deck)
    monkeypatch.setattr(spellbook, "find_my_combos", _raise)

    lookup = services.refresh(deck, force=True)

    assert not lookup.ok
    assert "unreachable" in lookup.message
    assert DeckCombo.objects.count() == 4, "what was found before is still there"


def test_an_outage_does_not_re_date_the_old_answer(deck, answered, monkeypatch):
    """The date beside the combos is the date they were true.

    `fetched_at` used to be `auto_now`, so saving the failure moved it to now:
    a list months old read as checked today.
    """
    services.refresh(deck)
    long_ago = timezone.now() - timedelta(days=40)
    ComboLookup.objects.filter(deck=deck).update(fetched_at=long_ago)
    monkeypatch.setattr(spellbook, "find_my_combos", _raise)

    lookup = services.refresh(deck, force=True)
    lookup.refresh_from_db()

    assert lookup.fetched_at == long_ago
    assert services.is_stale(lookup, deck)


def test_a_collection_sized_request_is_refused_before_it_is_sent():
    """Somebody else's free API should not receive a 214-row collection."""
    with pytest.raises(spellbook.SpellbookError) as excinfo:
        spellbook.find_my_combos([f"Card {n}" for n in range(spellbook.MAX_CARDS + 1)])
    assert "more than a deck" in str(excinfo.value)


def test_an_oversized_response_is_refused_rather_than_read():
    """The body is gzip from a third party, so an unbounded read is a bomb."""
    class Fake:
        headers = {}

        def read(self, size):
            return b"x" * size

    with pytest.raises(spellbook.SpellbookError) as excinfo:
        spellbook._read(Fake())
    assert "refusing to read on" in str(excinfo.value)


def _raise(*args, **kwargs):
    raise spellbook.SpellbookError("Spellbook is unreachable")


# --- the screen -------------------------------------------------------------


def test_the_deck_page_shows_the_panel_without_ever_fetching(client, owner, deck, answered):
    client.force_login(owner)

    body = client.get(deck.get_absolute_url()).content.decode()

    assert "Find combos" in body
    assert not answered, "rendering a deck page must not call anybody"


def test_no_combos_is_an_answer_and_not_an_empty_list(client, owner, deck, results, monkeypatch):
    """The reference deck - 69 cards, hand-built, the deck this project was
    written around - contains zero Spellbook combos. A page that renders an
    empty list for that looks broken instead of informative."""
    monkeypatch.setattr(spellbook, "find_my_combos",
                        lambda *a, **k: replace(results, included=[], almost=[]))
    client.force_login(owner)

    client.post(reverse("combos:refresh", args=[deck.id]))
    body = client.get(deck.get_absolute_url()).content.decode()

    assert "No known combos in this deck" in body
    assert "normal result and not a fault" in body.lower()


def test_the_refresh_is_a_post_and_belongs_to_its_owner(client, owner, deck, answered):
    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    client.force_login(stranger)

    assert client.post(reverse("combos:refresh", args=[deck.id])).status_code == 404
    assert not answered

    client.force_login(owner)
    assert client.get(reverse("combos:refresh", args=[deck.id])).status_code == 405


def test_htmx_gets_the_panel_back_and_a_browser_gets_a_redirect(client, owner, deck, answered):
    client.force_login(owner)

    fragment = client.post(reverse("combos:refresh", args=[deck.id]),
                           headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "Sanguine Bond" in fragment.content.decode()

    plain = client.post(reverse("combos:refresh", args=[deck.id]))
    assert plain.status_code == 302
    assert plain["Location"] == deck.get_absolute_url()


def test_a_long_one_away_list_is_folded_rather_than_flattened(deck, answered, results,
                                                              monkeypatch):
    """Measured, not imagined: the demo deck returns 125 of these.

    Rendered flat that is a deck page taller than the collection page was when
    it pushed its own answers off the screen. Folded away and not cut, because
    a list somebody might act on is not ours to truncate silently.
    """
    # Distinct ids, because `one_row_per_combo_per_deck` is a real constraint
    # and repeating one combo thirty times is not what a long list looks like.
    template = results.almost[0]
    many = replace(results, almost=[
        replace(template, spellbook_id=f"{template.spellbook_id}-{n}") for n in range(30)
    ])
    monkeypatch.setattr(spellbook, "find_my_combos", lambda *a, **k: many)
    services.refresh(deck)

    panel = services.panel_for(deck)

    assert len(panel.one_away) == 30
    assert len(panel.one_away_top) == services.ONE_AWAY_SHOWN
    assert len(panel.one_away_rest) == 30 - services.ONE_AWAY_SHOWN
    assert len(panel.one_away_top) + len(panel.one_away_rest) == len(panel.one_away)


def test_the_panel_names_the_template_a_combo_still_needs(client, owner, deck, answered):
    """So a deck holding both named cards is not told it has the combo."""
    client.force_login(owner)
    client.post(reverse("combos:refresh", args=[deck.id]))

    body = client.get(deck.get_absolute_url()).content.decode()

    assert "persist creature" in body.lower()


# --- a slow or unhappy Spellbook must not take the web workers with it --------


def _unavailable(retry_after):
    import email.message
    import urllib.error

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after

    def _urlopen(request, timeout):
        raise urllib.error.HTTPError(spellbook.ENDPOINT, 503, "busy", headers, None)

    return _urlopen


@pytest.mark.parametrize("retry_after", ["Wed, 21 Oct 2015 07:28:00 GMT", "3600", None])
def test_an_unhappy_spellbook_gives_up_inside_the_deadline(monkeypatch, retry_after):
    """An HTTP-date Retry-After used to be a ValueError, and a numeric one was
    obeyed however long it was - inside a web request gunicorn kills at 30 s."""
    import urllib.request

    slept = []
    monkeypatch.setattr(urllib.request, "urlopen", _unavailable(retry_after))
    monkeypatch.setattr(spellbook.time, "sleep", slept.append)

    with pytest.raises(spellbook.SpellbookError):
        spellbook.find_my_combos(["Sol Ring"])

    assert all(wait <= spellbook.MAX_RETRY_WAIT for wait in slept)
    assert sum(slept) < spellbook.DEADLINE_SECONDS


def test_refreshing_in_a_loop_is_refused(client, owner, deck, answered):
    client.force_login(owner)
    url = reverse("combos:refresh", args=[deck.id])
    response = None
    for _ in range(8):
        response = client.post(url)
    assert response.status_code == 429
