"""Versions of a deck's list (B-01, package "Workshop", PR 2): `decks.versions`.

A version is a snapshot; the deck's own rows always hold the version it is in.
"""
import pytest
from django.contrib.auth import get_user_model

from accounts import privacy
from cards.models import OracleCard
from decks import services, versions
from decks.models import DeckVersion
from simulations.summary import list_fingerprint

User = get_user_model()

LIST = b"1 Chainer, Dementia Master *CMDR*\n10 Swamp\n1 Dark Ritual\n1 Sol Ring\n"


@pytest.fixture
def owner():
    return User.objects.create_user(email="versions@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(catalogue, owner):
    return services.import_deck(owner=owner, raw=LIST, name="Versioned").deck


def card(name):
    return str(OracleCard.objects.get(front_name=name).pk)


def listed(deck):
    return dict(deck.entries.values_list("oracle_card__front_name", "quantity"))


def test_a_deck_nobody_versions_has_no_version(deck):
    assert not deck.versions.exists()
    assert deck.current_version is None


def test_the_first_new_version_keeps_the_list_before_it_as_version_one(deck):
    before = listed(deck)

    made = versions.make(deck, {card("Sol Ring"): -1, card("Necropotence"): +1}, "less artifacts")

    one, two = deck.versions.all()
    assert (one.number, one.origin, two.number, two.origin) == (1, "first", 2, "new")
    assert made == two and two.comment == "less artifacts"
    deck.refresh_from_db()
    assert deck.current_version == two
    after = listed(deck)
    assert "Sol Ring" not in after and after["Necropotence"] == 1
    assert one.card_count == sum(before.values())
    assert two.list_print == list_fingerprint(deck) != one.list_print


def test_a_version_that_changes_nothing_is_refused(deck):
    with pytest.raises(versions.VersionError):
        versions.make(deck, {card("Swamp"): 0})
    with pytest.raises(versions.VersionError):
        versions.make(deck, {})


def test_choosing_a_version_puts_its_list_and_commander_back(deck):
    before = listed(deck)
    commander = deck.commander_id
    versions.make(deck, {card("Swamp"): -2, card("Island"): +2})
    one = deck.versions.get(number=1)

    versions.choose(deck, one)

    deck.refresh_from_db()
    assert listed(deck) == before
    assert deck.commander_id == commander
    assert deck.current_version == one
    assert list_fingerprint(deck) == one.list_print


def test_removing_the_version_the_deck_is_in_moves_to_the_one_before(deck):
    versions.make(deck, {card("Swamp"): -1})
    three = versions.make(deck, {card("Swamp"): -1})

    versions.remove(three)

    deck.refresh_from_db()
    assert deck.current_version.number == 2
    assert listed(deck)["Swamp"] == 9


def test_removing_one_in_between_joins_the_changes_of_its_neighbours(deck):
    versions.make(deck, {card("Swamp"): -1, card("Island"): +1})
    versions.make(deck, {card("Sol Ring"): -1})
    two = deck.versions.get(number=2)

    versions.remove(two)

    one, three = deck.versions.all()
    assert three.number == 3  # numbers are never reused
    assert versions.diff(one, three) == {
        card("Swamp"): -1, card("Island"): +1, card("Sol Ring"): -1}


def test_the_last_version_stays(deck):
    versions.make(deck, {card("Swamp"): -1})
    one, two = deck.versions.all()
    versions.remove(one)

    with pytest.raises(versions.VersionError):
        versions.remove(two)


def test_a_version_of_another_deck_is_not_chosen(deck, owner):
    other = services.import_deck(owner=owner, raw=b"1 Sol Ring\n", name="Other").deck
    versions.make(other, {card("Swamp"): +1})

    with pytest.raises(versions.VersionError):
        versions.choose(deck, other.versions.first())


def test_a_reimport_of_a_versioned_deck_is_a_version_and_loses_nothing(deck, owner):
    versions.make(deck, {card("Swamp"): -1})

    services.import_deck(owner=owner, raw=b"1 Necropotence\n", deck=deck)

    numbers = list(deck.versions.values_list("number", "origin"))
    assert numbers == [(1, "first"), (2, "new"), (3, "import")]
    assert deck.versions.get(number=2).card_count == 11  # 12 less one Swamp


def test_a_reimport_of_an_unversioned_deck_makes_no_version(deck, owner):
    services.import_deck(owner=owner, raw=b"1 Necropotence\n", deck=deck)

    assert not deck.versions.exists()


def test_a_commander_picked_by_hand_corrects_the_version_the_deck_is_in(deck):
    versions.make(deck, {card("Carrion Feeder"): +1})
    feeder = OracleCard.objects.get(front_name="Carrion Feeder")

    services.set_commander(deck, feeder)

    two = DeckVersion.objects.get(deck=deck, number=2)
    assert two.commander_id == feeder.pk
    assert two.list_print == list_fingerprint(deck)
    assert deck.versions.get(number=1).commander_id != feeder.pk


def test_diff_counts_the_commander_too(deck):
    versions.make(deck, {card("Swamp"): -1})
    one, two = deck.versions.all()

    assert versions.diff(None, one)[card("Chainer, Dementia Master")] == 1
    assert versions.diff(one, two) == {card("Swamp"): -1}


def test_versions_are_in_the_export(deck, owner):
    versions.make(deck, {card("Swamp"): -1})

    exported = privacy.export(owner)

    assert len(exported["deck_versions"]) == 2
