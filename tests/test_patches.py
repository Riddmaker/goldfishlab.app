"""Card model phase 2: corrections as patches on the blocks of a reading.

A correction says only what a person said; what they did not say stays the
reader's, and a reading that moved under a correction is noted. Converting a
stored correction never changes what the engine plays (gate C).
"""

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from cards import profiles
from cards.models import DerivedProfile, OracleCard
from decks.models import Deck, DeckCard
from playtest import services as playtest
from simulation import ENGINE_VERSION
from simulations import annotations, patches, services
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE
from simulations.models import CardAnnotation

#: A reading with one primitive of each shape the patches know: an object
#: (land search), a list (triggers), a scalar (treasures).
SEARCH = {"battlefield": 1, "tapped": True, "basic": True}
LANDFALL = {"event": "landfall", "filter": "", "mana": 1, "color": "any"}
CAST = {"event": "cast", "filter": "", "draw": 1}


def _profile(**fields):
    base = {"land_search": None, "triggers": [], "treasures": 0, "tutor_to": "",
            "tutor_count": None, "tutor_kind": "", "tutor_filter": None}
    return SimpleNamespace(**(base | fields))


def _round_trip(overrides, profile):
    document = patches.from_overrides(overrides, profile)
    said, notes = patches.effective(document, profile)
    return document, said, notes


def test_a_field_said_is_a_set_and_the_rest_stays_the_reading():
    read = _profile(land_search=SEARCH)
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "priority": 70}
    document, said, notes = _round_trip(overrides, read)
    assert said == overrides and notes == []
    assert document["face"] == {"priority": 70}
    assert document["blocks"] == [{"id": "land_search", "op": "set",
                                   "base": patches.digest(SEARCH),
                                   "fields": {"battlefield": 2}}]

    # The reader learns the land enters untapped: the correction keeps its
    # count, takes the fix, and says the reading under it moved.
    later = _profile(land_search=SEARCH | {"tapped": False})
    now, notes = patches.effective(document, later)
    assert now["land_search"] == {"battlefield": 2, "tapped": False, "basic": True}
    assert notes == [patches.Note("land_search", "land_search", "changed")]


def test_a_block_that_is_gone_is_not_set_and_is_noted():
    overrides = {"land_search": SEARCH | {"battlefield": 2}}
    document = patches.from_overrides(overrides, _profile(land_search=SEARCH))
    now, notes = patches.effective(document, _profile())
    assert "land_search" not in now
    assert notes == [patches.Note("land_search", "land_search", "gone")]


def test_a_dropped_field_stays_dropped():
    overrides = {"land_search": {"battlefield": 1, "basic": True}}
    document, said, _notes = _round_trip(overrides, _profile(land_search=SEARCH))
    assert document["blocks"][0]["drop"] == ["tapped"]
    assert said == overrides


def test_the_reading_said_again_is_a_keep_that_follows_the_reader():
    overrides = {"land_search": dict(SEARCH)}
    document, said, _notes = _round_trip(overrides, _profile(land_search=SEARCH))
    assert document["blocks"][0]["op"] == "keep"
    now, notes = patches.effective(document,
                                   _profile(land_search=SEARCH | {"battlefield": 2}))
    assert now["land_search"]["battlefield"] == 2
    assert [note.status for note in notes] == ["changed"]


@pytest.mark.parametrize("overrides", [
    {"treasures": 2},                         # a scalar
    {"land_search": None},                    # "it does not do that"
    {"triggers": []},                         # no triggers at all
    {"land_search": dict(SEARCH)},            # a block the reader has not got
])
def test_what_no_diff_can_say_is_said_whole(overrides):
    read = _profile(triggers=[LANDFALL]) if "triggers" in overrides else _profile()
    document, said, notes = _round_trip(overrides, read)
    assert [patch["op"] for patch in document["blocks"]] == ["replace"]
    assert said == overrides and notes == []


def test_one_entry_of_a_list_is_patched_and_the_others_kept():
    read = _profile(triggers=[LANDFALL, CAST])
    overrides = {"triggers": [LANDFALL, CAST | {"draw": 2}]}
    document, said, _notes = _round_trip(overrides, read)
    assert said == overrides
    assert [(patch["id"], patch["op"]) for patch in document["blocks"]] == [
        ("triggers:0", "keep"), ("triggers:1", "set")]

    # A new trigger read in front: the corrected one is found by what it was.
    later = _profile(triggers=[{"event": "dies", "treasures": 1}, LANDFALL, CAST])
    now, notes = patches.effective(document, later)
    assert now["triggers"] == [{"event": "dies", "treasures": 1}, LANDFALL,
                               CAST | {"draw": 2}]
    assert notes == []


def test_an_entry_taken_out_is_off_and_one_put_in_is_added():
    read = _profile(triggers=[LANDFALL, CAST])
    extra = {"event": "upkeep", "draw": 1}
    overrides = {"triggers": [CAST, extra]}
    document, said, _notes = _round_trip(overrides, read)
    assert said == overrides
    assert sorted((patch["id"], patch["op"]) for patch in document["blocks"]) == [
        ("triggers", "add"), ("triggers:0", "off"), ("triggers:1", "keep")]


def test_an_entry_that_changed_is_found_by_its_kind_and_noted():
    read = _profile(triggers=[LANDFALL])
    overrides = {"triggers": [LANDFALL | {"mana": 2}]}
    document = patches.from_overrides(overrides, read)
    later = _profile(triggers=[LANDFALL | {"filter": "basic"}])
    now, notes = patches.effective(document, later)
    assert now["triggers"] == [LANDFALL | {"filter": "basic", "mana": 2}]
    assert notes == [patches.Note("triggers", "triggers:0", "changed")]


def test_an_entry_whose_kind_is_gone_is_noted_and_the_rest_plays():
    read = _profile(triggers=[LANDFALL])
    overrides = {"triggers": [LANDFALL | {"mana": 2}]}
    document = patches.from_overrides(overrides, read)
    now, notes = patches.effective(document, _profile(triggers=[CAST]))
    assert now["triggers"] == [CAST]
    assert notes == [patches.Note("triggers", "triggers:0", "gone")]


def test_an_added_entry_the_reader_learns_to_read_is_not_played_twice():
    extra = {"event": "upkeep", "draw": 1}
    overrides = {"triggers": [extra]}
    document = patches.from_overrides(overrides, _profile())
    assert document["blocks"] == [{"id": "triggers", "op": "add", "value": extra}]

    # The reader reads the same entry now: it is the reader's, said once.
    now, notes = patches.effective(document, _profile(triggers=[LANDFALL, extra]))
    assert now["triggers"] == [LANDFALL, extra] and notes == []

    # It reads one of the same kind: the added one takes its place, to check.
    read = {"event": "upkeep", "draw": 2}
    now, notes = patches.effective(document, _profile(triggers=[read]))
    assert now["triggers"] == [extra]
    assert notes == [patches.Note("triggers", "triggers", "changed")]


def test_the_tutor_is_one_block_of_four_fields():
    read = _profile(tutor_to="hand", tutor_count=1, tutor_kind="", tutor_filter=None)
    overrides = {"tutor": {"to": "battlefield", "count": 1}}
    document, said, _notes = _round_trip(overrides, read)
    assert said == overrides
    assert document["blocks"][0]["fields"] == {"to": "battlefield"}


def test_a_document_states_its_overrides_without_a_reading():
    read = _profile(land_search=SEARCH)
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "priority": 40}
    document = patches.from_overrides(overrides, read)
    assert patches.overrides_of(document) == overrides
    assert patches.overrides_of({}) == {} and patches.effective({}, read) == ({}, [])


def test_an_unchanged_primitive_keeps_its_patches_through_a_save():
    read = _profile(land_search=SEARCH)
    first = patches.from_overrides({"land_search": SEARCH | {"battlefield": 2}}, read)
    later = _profile(land_search=SEARCH | {"tapped": False})
    current, _notes = patches.effective(first, later)
    second = patches.from_overrides(current | {"priority": 60}, later, previous=first)
    assert second["blocks"] == first["blocks"]
    assert patches.effective(second, later)[1] == [
        patches.Note("land_search", "land_search", "changed")]


def test_a_gone_correction_stays_through_a_save_of_another_block():
    said = {"land_search": SEARCH | {"battlefield": 2}}
    first = patches.from_overrides(said, _profile(land_search=SEARCH))
    assert first["said"] == said
    later = _profile()

    # Not played, but still said: a save of another block writes it again.
    carried = patches.stated(first, later)
    assert carried == said
    assert "land_search" not in patches.effective(first, later)[0]
    second = patches.from_overrides(carried | {"treasures": 1}, later, previous=first)
    assert [patch for patch in second["blocks"] if patch["id"] == "land_search"] \
        == first["blocks"]
    assert patches.effective(second, later)[1] == [
        patches.Note("land_search", "land_search", "gone")]


def _engine_neutral(overrides, document, later, keys):
    """`settle` plays what was played, and leaves nothing to check on `keys`."""
    before, _notes = patches.effective(document, later)
    kept, settled = patches.settle(document, later, keys)
    after, notes = patches.effective(settled, later)
    assert after == before and patches.overrides_of(settled) == kept
    assert not [note for note in notes if note.key in keys]
    return kept, settled


def test_keeping_a_changed_correction_writes_it_against_today():
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "priority": 70}
    document = patches.from_overrides(overrides, _profile(land_search=SEARCH))
    later = _profile(land_search=SEARCH | {"tapped": False})
    kept, settled = _engine_neutral(overrides, document, later, {"land_search"})
    assert kept["land_search"] == {"battlefield": 2, "tapped": False, "basic": True}
    assert settled["blocks"][0]["base"] == patches.digest(SEARCH | {"tapped": False})


def test_discarding_a_gone_correction_lets_it_go():
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "treasures": 1}
    document = patches.from_overrides(overrides, _profile(land_search=SEARCH))
    kept, settled = _engine_neutral(overrides, document, _profile(), {"land_search"})
    assert kept == {"treasures": 1}
    assert [patch["id"] for patch in settled["blocks"]] == ["treasures"]


def test_settling_a_list_lets_a_gone_entry_go_and_keeps_the_rest():
    overrides = {"triggers": [LANDFALL | {"mana": 2}, CAST | {"draw": 3}]}
    document = patches.from_overrides(overrides, _profile(triggers=[LANDFALL, CAST]))
    later = _profile(triggers=[CAST | {"filter": "instant"}])
    assert {note.status for note in patches.effective(document, later)[1]} \
        == {"gone", "changed"}
    kept, _settled = _engine_neutral(overrides, document, later, {"triggers"})
    assert kept["triggers"] == [CAST | {"filter": "instant", "draw": 3}]


def test_settling_one_block_leaves_the_others_to_check():
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "triggers": [LANDFALL | {"mana": 2}]}
    document = patches.from_overrides(overrides, _profile(land_search=SEARCH,
                                                          triggers=[LANDFALL]))
    later = _profile(triggers=[LANDFALL | {"filter": "basic"}])
    kept, settled = _engine_neutral(overrides, document, later, {"triggers"})
    assert patches.effective(settled, later)[1] == [
        patches.Note("land_search", "land_search", "gone")]


@pytest.mark.parametrize("document", [
    {"v": 99, "said": {}, "face": {}, "blocks": []},
    {"v": 1, "of": "0" * 16, "said": {}, "face": {}, "blocks": []},
    {"v": 2, "said": {}, "face": {"triggers": []}, "blocks": []},
    {"v": 2, "said": {}, "face": {}, "blocks": [{"id": "nothing", "op": "keep"}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [{"id": "land_search:1", "op": "keep"}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [{"id": "triggers", "op": "set"}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [
        {"id": "land_search", "op": "set", "fields": {"evil": 1}}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [
        {"id": "land_search", "op": "set", "fields": {"battlefield": "<script>"}}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [
        {"id": "triggers", "op": "add", "value": {"event": "x", "evil": 1}}]},
    {"v": 2, "said": {}, "face": {}, "blocks": [
        {"id": "land_search", "op": "keep", "drop": ["evil"]}]},
    {"v": 2, "said": {"priority": 10}, "face": {}, "blocks": []},
    {"v": 2, "said": {"land_search": {"battlefield": "<script>"}}, "face": {}, "blocks": []},
    {"v": 2, "said": [], "face": {}, "blocks": []},
    {"v": 2, "face": {}, "blocks": []},
])
def test_a_document_the_engine_cannot_store_is_refused(document):
    with pytest.raises(patches.PatchError):
        patches.validate(document)


def test_a_written_document_is_valid():
    read = _profile(land_search=SEARCH, triggers=[LANDFALL, CAST])
    overrides = {"land_search": SEARCH | {"battlefield": 2}, "treasures": 1,
                 "triggers": [CAST, {"event": "upkeep", "draw": 1}], "priority": 50}
    assert patches.validate(patches.from_overrides(overrides, read))


# --- stored: every save writes them, the engine plays them ---------------------------


@pytest.fixture
def cobra_deck(db):
    owner = get_user_model().objects.create_user(email="patches@example.com",
                                                 password="pw-for-test-only")
    cobra = OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name="Lotus Cobra", front_name="Lotus Cobra",
        search_name="lotus cobra", type_line="Creature — Snake", mana_cost="{1}{G}",
        oracle_text="Landfall — Whenever a land you control enters, add one mana of any color.",
        produced_mana=[], keywords=[])
    profiles.rebuild(OracleCard.objects.filter(pk=cobra.pk))
    deck = Deck.objects.create(owner=owner, name="Patches")
    DeckCard.objects.create(deck=deck, oracle_card=cobra, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, cobra=cobra)


def _triggers(deck, card):
    return next(reading.card for reading in adapter.readings(deck)
                if reading.oracle_card.pk == card.pk).triggers


def _reread(card, **fields):
    """The reader reads the card differently now."""
    profile = DerivedProfile.objects.get(oracle_card=card)
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save()


def test_a_saved_correction_is_played_from_its_patches(cobra_deck):
    read = cobra_deck.cobra.profile.triggers
    assert len(read) == 1 and read[0]["mana"] == 1
    services.save_annotation(deck=cobra_deck.deck, oracle_card=cobra_deck.cobra,
                             scope=DECK_SCOPE,
                             primitive_changes={"triggers": [read[0] | {"mana": 2}]})
    row = CardAnnotation.objects.get(oracle_card=cobra_deck.cobra)
    assert row.patches["blocks"] == [{
        "id": "triggers:0", "op": "set", "base": patches.digest(read[0]), "at": 0,
        "kind": "landfall", "fields": {"mana": 2}}]
    assert _triggers(cobra_deck.deck, cobra_deck.cobra)[0].mana == 2

    # The reader learns more about the trigger: the correction keeps its mana,
    # takes the rest, and the deck is told to check it.
    _reread(cobra_deck.cobra, triggers=[read[0] | {"filter": "basic"}])
    found = adapter.annotations_for(cobra_deck.deck)
    assert found.for_card(cobra_deck.cobra.pk)["triggers"] == [
        read[0] | {"filter": "basic", "mana": 2}]
    assert found.notes[cobra_deck.cobra.pk] == [
        adapter.Check("triggers", "triggers:0", "changed", DECK_SCOPE)]

    # Saving another judgement leaves the trigger's patch - and its note - as it was.
    services.save_annotation(
        deck=cobra_deck.deck, oracle_card=OracleCard.objects.get(pk=cobra_deck.cobra.pk),
        scope=DECK_SCOPE,
        judgements=dict.fromkeys(annotations.EDITABLE_KEYS) | {"priority": 70})
    row.refresh_from_db()
    assert row.patches["blocks"][0]["base"] == patches.digest(read[0])
    assert row.overrides["priority"] == 70
    assert adapter.annotations_for(cobra_deck.deck).notes[cobra_deck.cobra.pk]


def test_a_gone_correction_survives_an_unrelated_save(cobra_deck):
    read = cobra_deck.cobra.profile.triggers
    services.save_annotation(deck=cobra_deck.deck, oracle_card=cobra_deck.cobra,
                             scope=DECK_SCOPE,
                             primitive_changes={"triggers": [read[0] | {"mana": 2}],
                                                "treasures": 1})
    _reread(cobra_deck.cobra, triggers=[])
    services.save_annotation(
        deck=cobra_deck.deck, oracle_card=OracleCard.objects.get(pk=cobra_deck.cobra.pk),
        scope=DECK_SCOPE, primitive_changes={"treasures": 2})
    row = CardAnnotation.objects.get(oracle_card=cobra_deck.cobra)
    assert [patch["id"] for patch in row.patches["blocks"]] == ["treasures", "triggers:0"]
    assert [note.status for note in
            adapter.annotations_for(cobra_deck.deck).notes[cobra_deck.cobra.pk]] == ["gone"]


def test_a_playtest_keeps_its_engine_version(cobra_deck):
    session = playtest.start(cobra_deck.deck, cobra_deck.owner, seed=1)
    assert session.engine_version == ENGINE_VERSION
    assert playtest.fork(session, 0).engine_version == ENGINE_VERSION
