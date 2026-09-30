"""Phase 4: the honesty layer - provenance, judgements and named blind spots.

What these tests are really for, in order of how much they matter:

1. **A judgement is never invented.** A blank field removes an override rather
   than storing a zero, and the form is never pre-filled from the derived
   reading - otherwise saving a page once would turn a community tag into a
   human decision, and the provenance panel would then credit a user with an
   opinion they never had. That is the failure this whole phase exists to
   prevent, so it is the first thing asserted.
2. **Saving merges.** The editor shows eight of thirty-five keys. The
   reference deck's rows carry `scaling_rule`, `upkeep_draw` and `tutor_count`;
   editing a priority must not delete the per-Swamp scaling that makes Cabal
   Coffers that card.
3. **Editing an annotation changes the next run in the expected direction.**
   The end-to-end claim of the phase, asserted rather than assumed.
4. **Nobody can write a built-in.** `owner=None, deck=None` applies to every
   deck of every user, and no user-facing path may reach it.
5. **Ownership.** Somebody else's deck is a 404, and a card that is not in the
   deck is a 404, at the source.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from cards.models import OracleCard
from decks import services as deck_services
from simulations import annotations, blindspots, provenance, report, services
from simulations.annotations import (
    ManaTextError,
    apply,
    format_mana,
    parse_mana,
)
from simulations.engine import adapter
from simulations.forms import AnnotationForm
from simulations.models import ALLOWED_KEYS, CardAnnotation

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
PASSWORD = "pw-for-test-only"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    outcome = deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    )
    return outcome.deck


@pytest.fixture
def no_builtins(deck):
    """Clear the built-in judgements for this deck's cards.

    Needed by the handful of tests that assert what happens when **nobody**
    has said anything, because built-ins are a real production state: seeding
    the reference deck writes one per card, and another test module seeds it
    module-scoped and outside a transaction, so they are present for the rest
    of the session. Rolled back with the test like everything else.
    """
    CardAnnotation.objects.filter(
        adapter.cards_filter(deck), owner__isnull=True, deck__isnull=True
    ).delete()
    return deck


@pytest.fixture
def swamp(deck):
    return deck.entries.filter(oracle_card__front_name="Swamp").first().oracle_card


@pytest.fixture
def spell(deck):
    """A card the agent might cast, so that priority means something."""
    return (
        deck.entries.exclude(oracle_card__type_line__contains="Land")
        .first()
        .oracle_card
    )


# --- the vocabulary --------------------------------------------------------


def test_every_editable_key_is_one_the_engine_understands():
    """A key the adapter ignores would be a form field that does nothing."""
    assert annotations.EDITABLE_KEYS <= set(ALLOWED_KEYS)


def test_every_judgement_has_a_label_and_a_reason():
    """No field goes on screen without saying what it is and why it matters."""
    for judgement in annotations.JUDGEMENTS:
        assert judgement.label
        assert len(judgement.help) > 30


def test_every_provenance_row_can_be_acted_on_or_is_honest_about_not_being():
    """A row the user cannot change has to say so by having no editor."""
    for spec in provenance.FIELDS:
        key = provenance.ANNOTATION_KEY.get(spec.attribute)
        assert key is None or key in ALLOWED_KEYS


def test_every_source_a_row_can_claim_has_words_for_it():
    """`SOURCES[row.source]` is looked up unguarded, so it must not miss."""
    claimable = {spec.fallback for spec in provenance.FIELDS}
    claimable |= {"scryfall", "tags", "regex", "builtin", "user", "deck"}
    assert claimable <= set(provenance.SOURCES)


# --- reading the "taps for" box --------------------------------------------


def test_an_empty_box_is_no_opinion_and_an_explicit_nothing_is_a_statement():
    """The distinction Ashnod's Altar exists to make.

    `None` leaves the derived reading alone. `{}` overrides it with "this taps
    for nothing", which is a judgement somebody has to be able to record.
    """
    assert parse_mana("") is None
    assert parse_mana("   ") is None
    assert parse_mana("nothing") == {}
    assert parse_mana("none") == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("B", {"B": 1}),
        ("BB", {"B": 2}),
        ("2B", {"B": 2}),
        ("b", {"B": 1}),
        ("B C", {"B": 1, "C": 1}),
        ("2B, 1C", {"B": 2, "C": 1}),
        ("G G G", {"G": 3}),
    ],
)
def test_the_taps_for_box_reads_the_forms_people_write(text, expected):
    assert parse_mana(text) == expected


@pytest.mark.parametrize("text", ["X", "black", "2", "B X"])
def test_a_colour_the_engine_would_not_know_is_refused_at_the_form(text):
    """Refused here, or it surfaces days later inside a Celery worker."""
    with pytest.raises(ManaTextError):
        parse_mana(text)


def test_the_taps_for_box_round_trips():
    for text in ("B", "2B", "B C", "nothing"):
        assert format_mana(parse_mana(text)) == text or parse_mana(
            format_mana(parse_mana(text))
        ) == parse_mana(text)


# --- blank is not zero -----------------------------------------------------


def test_a_blank_field_removes_the_override_rather_than_storing_a_zero():
    """The defect that would have changed every number on the first save."""
    stored = {"priority": 55, "accelerant": True}

    result = apply(stored, {"priority": None, "accelerant": None})

    assert result == {}
    assert "priority" not in result, "a blank box must not become priority 0"


def test_saving_keeps_the_keys_the_editor_does_not_show():
    """Cabal Coffers' scaling must survive somebody editing its priority.

    The editor exposes eight of thirty-five keys. If a save replaced the whole
    dict, the per-Swamp scaling rule would vanish and the deck would quietly
    start simulating with a land that taps for nothing.
    """
    stored = {
        "scaling_rule": "per_controlled",
        "scaling_subtype": "swamp",
        "scaling_activation": 2,
        "priority": 10,
    }

    result = apply(stored, {"priority": 42})

    assert result["scaling_rule"] == "per_controlled"
    assert result["scaling_subtype"] == "swamp"
    assert result["scaling_activation"] == 2
    assert result["priority"] == 42


def test_an_unknown_key_cannot_be_written_through_the_editor():
    with pytest.raises(ValueError, match="not an editable judgement"):
        apply({}, {"skips_draw_step": True})


def test_an_empty_list_of_roles_is_a_real_answer():
    """"This card has no role at all" has to be sayable, and is not the same
    thing as having no opinion about its roles."""
    assert apply({}, {"tags": []}) == {"tags": []}
    assert apply({"tags": ["ramp"]}, {"tags": None}) == {}


# --- the form --------------------------------------------------------------


def test_an_untouched_form_records_nothing():
    """Submitting the page without changing anything must be a no-op.

    This is what makes it safe to show the derived reading beside the form
    rather than inside it.
    """
    form = AnnotationForm(data={"scope": "deck"})

    assert form.is_valid(), form.errors
    assert all(value is None for value in form.judgements().values())


def test_the_form_can_say_no_as_well_as_nothing():
    """"Nobody has said" and "no" are different, and both must be storable."""
    form = AnnotationForm(data={"scope": "deck", "goldfish_castable": "0"})

    assert form.is_valid(), form.errors
    judgements = form.judgements()
    assert judgements["goldfish_castable"] is False
    assert judgements["accelerant"] is None


def test_the_form_can_switch_off_what_the_mana_reader_derived():
    """Trap 31 again: a derived value ships with the field that overrides it.

    The 2026-09-25 review started reading a mana ability's cost and whether its
    card untaps. Both are regexes over English, so both must be correctable -
    including to zero, which says "it only has to tap".
    """
    form = AnnotationForm(data={"scope": "deck", "mana_activation": "0", "untaps": "0"})

    assert form.is_valid(), form.errors
    judgements = form.judgements()
    assert judgements["mana_activation"] == 0
    assert judgements["untaps"] is False


def test_the_mana_judgements_round_trip_through_the_form():
    from types import SimpleNamespace

    stored = SimpleNamespace(overrides={"mana_activation": 1, "untaps": False}, note="")
    initial = AnnotationForm.initial_for(stored)
    assert (initial["mana_activation"], initial["untaps"]) == (1, "0")


def test_the_form_is_only_ever_filled_from_an_annotation():
    assert AnnotationForm.initial_for(None) == {}


def test_the_form_refuses_a_scope_no_user_may_write():
    form = AnnotationForm(data={"scope": "builtin"})

    assert not form.is_valid()
    assert "scope" in form.errors


def test_a_bad_colour_is_a_field_error_and_not_a_crash():
    form = AnnotationForm(data={"scope": "deck", "mana_produces": "purple"})

    assert not form.is_valid()
    assert "mana_produces" in form.errors


# --- writing it down -------------------------------------------------------


def test_a_judgement_is_stored_at_the_scope_it_was_made_in(deck, spell):
    annotation = services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck",
        judgements={"priority": 77}, note="it is the whole plan",
    )

    assert annotation.deck_id == deck.pk
    assert annotation.owner_id == deck.owner_id
    assert annotation.scope == "deck"
    assert annotation.overrides == {"priority": 77}


def test_a_user_scoped_judgement_has_no_deck(deck, spell):
    annotation = services.save_annotation(
        deck=deck, oracle_card=spell, scope="user", judgements={"priority": 77}
    )

    assert annotation.deck_id is None
    assert annotation.scope == "user"


def test_saving_twice_edits_rather_than_raising(deck, spell):
    """The unique constraint counts NULLs as equal, so a second save must
    update the row it finds instead of colliding with it."""
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="user", judgements={"priority": 10}
    )
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="user", judgements={"priority": 20}
    )

    # Scoped to the owner: a built-in row for the same card also has a null
    # deck, and built-ins are a real production state rather than a fixture
    # accident - seeding the reference deck writes one per card.
    rows = CardAnnotation.objects.filter(
        oracle_card=spell, owner=deck.owner, deck__isnull=True
    )
    assert rows.count() == 1
    assert rows.first().overrides == {"priority": 20}


def test_no_user_facing_path_can_write_a_built_in(deck, spell):
    """A built-in applies to every deck of every user. It is not reachable."""
    with pytest.raises(ValueError, match="not a scope a user may write"):
        services.save_annotation(
            deck=deck, oracle_card=spell, scope="builtin", judgements={"priority": 1}
        )


def test_an_emptied_judgement_leaves_no_row_behind(deck, spell):
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 5}
    )

    result = services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": None}
    )

    assert result is None
    assert not CardAnnotation.objects.filter(oracle_card=spell, deck=deck).exists()


def test_a_note_on_its_own_is_worth_keeping(deck, spell):
    """Somebody recording *why* without changing a value is still saying
    something, and the provenance panel shows it."""
    annotation = services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck",
        judgements={"priority": None}, note="checked; the default is right",
    )

    assert annotation is not None
    assert annotation.overrides == {}


def test_the_model_still_refuses_a_colour_the_engine_would_not_know(deck, spell):
    """The form is the friendly first line; this is the actual boundary."""
    with pytest.raises(ValidationError):
        services.save_annotation(
            deck=deck, oracle_card=spell, scope="deck",
            judgements={"mana_produces": {"purple": 1}},
        )


def test_forgetting_a_judgement_goes_back_to_the_derived_reading(deck, swamp):
    services.save_annotation(
        deck=deck, oracle_card=swamp, scope="deck",
        judgements={"mana_produces": {}, "subtypes": []},
    )
    before = _reading(deck, swamp)

    services.delete_annotation(deck=deck, oracle_card=swamp, scope="deck")
    after = _reading(deck, swamp)

    assert before.taps_for == "nothing"
    assert after.taps_for != "nothing", "removing a judgement must restore the card"


# --- precedence ------------------------------------------------------------


def test_a_deck_judgement_beats_the_same_users_global_one(deck, spell):
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="user", judgements={"priority": 10}
    )
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 90}
    )

    merged = adapter.annotations_for(deck)

    assert merged.overrides[spell.pk]["priority"] == 90
    assert merged.scope_of(spell.pk, "priority") == "deck"


def test_a_narrower_scope_overrides_one_key_without_restating_the_rest(deck, spell):
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="user",
        judgements={"priority": 10, "accelerant": True},
    )
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 90}
    )

    merged = adapter.annotations_for(deck)

    # Per key rather than whole-dict, because a built-in row for the same card
    # legitimately contributes keys neither of these two scopes mentioned.
    assert merged.overrides[spell.pk]["priority"] == 90
    assert merged.overrides[spell.pk]["accelerant"] is True
    assert merged.scope_of(spell.pk, "priority") == "deck"
    assert merged.scope_of(spell.pk, "accelerant") == "user"


# --- provenance ------------------------------------------------------------


def test_a_shipped_default_is_credited_to_the_application_not_the_user(
    deck, spell
):
    """A built-in is a judgement this application ships. It is not the user's,
    and the panel must not let them confuse the two."""
    CardAnnotation.objects.update_or_create(
        oracle_card=spell, owner=None, deck=None,
        defaults={"overrides": {"priority": 44}},
    )

    row = _row(provenance.for_card(deck, spell), "effective_priority")

    assert row.value == "44"
    assert row.source == "builtin"
    assert not row.is_yours


def test_a_value_the_user_set_is_credited_to_the_user(deck, spell):
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 90}
    )

    entry = provenance.for_card(deck, spell)
    row = _row(entry, "effective_priority")

    assert row.value == "90"
    assert row.source == "deck"
    assert row.is_yours
    assert row.editable_as


def test_a_value_nobody_set_is_credited_to_nobody(no_builtins, deck, spell):
    """The row that keeps the panel honest: 38 looks like a decision until it
    says that the engine made it up out of the mana value."""
    entry = provenance.for_card(deck, spell)
    row = _row(entry, "effective_priority")

    assert row.source == "engine"
    # Credited to nobody, but not flagged: nobody is asked for a priority any
    # more (Phase 9 C), so "worth checking" would nag about a question the
    # interface no longer puts.
    assert not row.is_weak
    assert not row.is_yours


def test_the_panel_shows_exactly_what_the_engine_will_use(deck, swamp):
    """Not a second reconstruction of it. One function, two readers."""
    entry = provenance.for_card(deck, swamp)
    reading = _reading(deck, swamp)

    assert _row(entry, "taps_for").value == reading.taps_for
    assert _row(entry, "cost").value == reading.cost


def test_a_land_is_not_asked_about_things_a_land_never_does(deck, swamp):
    """A Swamp has no casting order. A row saying "40" would be noise, and
    noise is what teaches people to skip the panel that matters."""
    entry = provenance.for_card(deck, swamp)
    keys = {row.key for row in entry.rows}

    assert "effective_priority" not in keys
    assert "goldfish_castable" not in keys
    assert "taps_for" in keys


def test_a_card_from_another_deck_has_no_provenance_here(deck, catalogue):
    """`None`, which the view turns into a 404."""
    outsider = OracleCard.objects.exclude(
        pk__in=deck.entries.values_list("oracle_card_id", flat=True)
    ).first()

    assert provenance.for_card(deck, outsider) is None


# --- the named blind spots -------------------------------------------------


def test_cards_that_need_an_opponent_are_named(deck):
    spot = blindspots.opponent_dependent(adapter.readings(deck))

    assert spot.suspects
    assert spot.fix == "Can be cast against nobody"
    assert all(suspect.reason for suspect in spot.suspects)


def test_unresolved_mana_sources_come_from_the_gaps_the_adapter_recorded(deck):
    """So that this panel and the run's own gap table cannot disagree."""
    readings = adapter.readings(deck)
    spot = blindspots.unresolved_mana(readings)
    from_gaps = {
        reading.oracle_card.pk
        for reading in readings
        for gap in reading.gaps
        if gap.field == "mana_abilities"
    }

    assert {suspect.oracle_id for suspect in spot.suspects} == from_gaps


def test_a_deck_the_engine_can_model_gets_no_warnings():
    """An empty panel is the right output, and not a bug.

    A page listing three limitations that affect none of your cards is how a
    warning turns into furniture.
    """
    assert blindspots.find([]) == []


def test_every_blind_spot_names_the_field_that_answers_it(deck):
    """A warning with no way to act on it is a disclaimer, and people learn to
    skip disclaimers."""
    labels = {judgement.label for judgement in annotations.JUDGEMENTS}
    for spot in blindspots.find(adapter.readings(deck)):
        assert spot.fix in labels
        assert spot.detail
        assert spot.heading


# --- the end-to-end claim --------------------------------------------------


def test_a_judgement_changes_the_next_run_in_the_expected_direction(deck, owner):
    """The phase's headline verification, asserted rather than displayed.

    Telling the engine that every land taps for nothing has to make the deck
    produce less mana. It is a deliberately absurd judgement, because a subtle
    one would be within sampling error at a test-sized number of games.

    **Both keys, and that is the point of the "Land types" field.** A land
    carrying a coloured land type taps as a basic land and its own mana ability
    goes unused, so `mana_produces` alone silences nothing - which is exactly
    why the editor exposes the land types too instead of leaving somebody to
    wonder why the Swamp they muted kept making black mana.
    """
    from simulations.engine import runner

    def mana_on_last_turn():
        result = runner.read(
            runner.run_chunk(
                120, run_seed=7, index=0, turns=3, on_the_play=True,
                deck=adapter.deck_definition(deck),
            )
        )
        return result["turn_stats"][-1]["mana"].mean

    before = mana_on_last_turn()

    for entry in deck.entries.filter(oracle_card__type_line__contains="Land"):
        services.save_annotation(
            deck=deck, oracle_card=entry.oracle_card, scope="deck",
            judgements={"mana_produces": {}, "subtypes": []},
        )

    after = mana_on_last_turn()

    assert before > 0
    assert after < before, "an annotation that removes mana has to remove mana"


def test_muting_a_lands_mana_needs_its_land_types_too(deck, swamp):
    """The trap the field above exists to close, pinned so it stays closed.

    `mana_produces: {}` on a Swamp leaves the land type intact, and the engine
    taps a coloured land type as a basic land. The panel must not claim the
    land makes nothing while the simulation goes on making black mana off it.
    """
    services.save_annotation(
        deck=deck, oracle_card=swamp, scope="deck", judgements={"mana_produces": {}}
    )
    half = _reading(deck, swamp)

    services.save_annotation(
        deck=deck, oracle_card=swamp, scope="deck",
        judgements={"mana_produces": {}, "subtypes": []},
    )
    muted = _reading(deck, swamp)

    assert "B" in half.taps_for, "the land type still makes mana, and must say so"
    assert muted.taps_for == "nothing"


def test_a_stored_result_is_not_rewritten_when_a_judgement_changes(deck, owner, spell):
    """A run is a record of what the engine saw. The report says the inputs
    have moved on; it never restates the numbers to match them."""
    from django.utils import timezone

    from simulations.models import SimulationRun

    run = SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=1, turns=1, seed=1,
        status=SimulationRun.Status.DONE, finished_at=timezone.now(),
    )

    assert report.annotations_changed_since(run) is False

    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 99}
    )

    assert report.annotations_changed_since(run) is True


# --- the screens -----------------------------------------------------------


def test_the_old_card_list_sends_its_links_to_the_deck_page(client, owner, deck):
    """Phase 9 I: `/decks/<id>/tune/` was the card list; the deck page's grid is.

    The sources it used to open with are on the methodology page."""
    client.force_login(owner)

    response = client.get(f"/decks/{deck.pk}/tune/")

    assert response.status_code == 302
    assert response.url == f"{deck.get_absolute_url()}#cards"
    assert "Swamp" in client.get(response.url).content.decode()
    assert "Scryfall field" in client.get(reverse("methodology")).content.decode()


def test_the_card_page_shows_the_reading_and_a_form(client, owner, deck, spell):
    client.force_login(owner)

    response = client.get(
        reverse("simulations:annotate", args=[deck.pk, spell.pk])
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert spell.front_name in body
    assert "What does this card do?" in body
    assert "Why does the engine think this?" in body


def test_saving_from_the_card_page_records_the_judgement(client, owner, deck, spell):
    client.force_login(owner)

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, spell.pk]),
        {"scope": "deck", "priority": "80"},
    )

    assert response.status_code == 302
    annotation = CardAnnotation.objects.get(oracle_card=spell, deck=deck)
    assert annotation.overrides == {"priority": 80}


def test_a_nonsense_priority_is_refused_with_the_form_shown_again(
    client, owner, deck, spell
):
    client.force_login(owner)

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, spell.pk]),
        {"scope": "deck", "priority": "9000"},
    )

    assert response.status_code == 400
    assert not CardAnnotation.objects.filter(
        oracle_card=spell, owner=owner
    ).exists()


def test_forgetting_from_the_card_page_removes_the_row(client, owner, deck, spell):
    services.save_annotation(
        deck=deck, oracle_card=spell, scope="deck", judgements={"priority": 80}
    )
    client.force_login(owner)

    response = client.post(
        reverse("simulations:forget", args=[deck.pk, spell.pk]), {"scope": "deck"}
    )

    assert response.status_code == 302
    assert not CardAnnotation.objects.filter(oracle_card=spell, deck=deck).exists()


def test_the_casting_order_page_is_gone(client, owner, deck):
    """Phase 9 C (decision D6): nobody is asked for a casting order any more.

    The product is statistics about a deck, not steering a game, and the
    engine's own rule - cheapest first - is an answer nobody has to give.
    """
    client.force_login(owner)

    assert client.get(f"/decks/{deck.pk}/priority/").status_code == 404


def test_no_page_puts_the_casting_priority_to_anybody(
    no_builtins, client, owner, deck, spell
):
    """The priority gap is still recorded, but neither the card list nor the
    card page shows it as an open question."""
    assert any(gap.field == "priority"
               for gap in provenance.for_card(deck, spell).gaps), (
        "the fixture no longer carries a priority gap, so this test proves nothing"
    )
    client.force_login(owner)

    grid = client.get(deck.get_absolute_url()).content.decode()
    card = client.get(
        reverse("simulations:annotate", args=[deck.pk, spell.pk])
    ).content.decode()

    for body in (grid, card):
        assert "no one said how early to cast it" not in body
        assert "call only you can make" not in body
        assert "/priority/" not in body


def test_another_users_deck_is_a_404_not_a_permission_error(client, deck):
    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    client.force_login(stranger)

    spell = deck.entries.first().oracle_card
    assert client.get(
        reverse("simulations:annotate", args=[deck.pk, spell.pk])
    ).status_code == 404


def test_a_card_that_is_not_in_the_deck_cannot_be_annotated(client, owner, deck):
    """Filtered at the source: a crafted POST has nowhere to land."""
    client.force_login(owner)
    outsider = OracleCard.objects.exclude(
        pk__in=deck.entries.values_list("oracle_card_id", flat=True)
    ).first()

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, outsider.pk]),
        {"scope": "deck", "priority": "50"},
    )

    assert response.status_code == 404
    assert not CardAnnotation.objects.filter(
        oracle_card=outsider, owner=owner
    ).exists()


def test_signing_out_hides_the_honesty_layer(client, deck):
    spell = deck.entries.first().oracle_card
    response = client.get(reverse("simulations:annotate", args=[deck.pk, spell.pk]))

    assert response.status_code == 302
    assert "login" in response.url


def test_the_commander_can_be_annotated(client, owner, deck):
    """It is not a `DeckCard` - it sits in the command zone - and filtering on
    deck membership alone once cost it its priority entirely."""
    commander = deck.commander
    if commander is None:
        pytest.skip("the sample deck has no commander")
    client.force_login(owner)

    response = client.post(
        reverse("simulations:annotate", args=[deck.pk, commander.pk]),
        {"scope": "deck", "priority": "95"},
    )

    assert response.status_code == 302
    assert CardAnnotation.objects.get(
        oracle_card=commander, deck=deck
    ).overrides == {"priority": 95}


# --- helpers ---------------------------------------------------------------


def _reading(deck, oracle_card):
    for reading in adapter.readings(deck):
        if reading.oracle_card.pk == oracle_card.pk:
            return reading
    raise AssertionError(f"{oracle_card} is not in {deck}")


def _row(entry, key):
    for row in entry.rows:
        if row.key == key:
            return row
    raise AssertionError(f"no {key!r} row; got {[row.key for row in entry.rows]}")
