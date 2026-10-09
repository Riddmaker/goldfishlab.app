"""Phase 2 definition of done: a deck row in Postgres is the same deck.

`simulations/engine/adapter.py` is the only module that sees both Django and
the simulation engine. These tests are the argument that it carries a deck
across that boundary without losing or inventing anything:

1. the card list matches the hand-written fixture, card for card;
2. every simulation-relevant field of every card matches;
3. running both decks produces **identical** aggregates.

Three is the one that matters. One and two exist so that when three fails, the
failure names the field rather than just the number.

Library order is normalised with `canonical()` before comparing: a database
returns rows in its own order, and `rng.shuffle` gives different results for
differently ordered lists. Same cards in a different order is the same deck,
and the comparison should say so.
"""


import pytest
from django.core.management import call_command

from cards.names import normalise
from decks.models import Deck
from simulation import analysis
from simulation.fixtures import chainer
from simulations.engine import adapter
from simulations.models import CardAnnotation
from tests.support import forget_module_rows, load_catalogue

pytestmark = pytest.mark.django_db


#: Everything the engine reads off a card. `tags` is included: the seeded
#: annotations carry the deck author's own roles, which is what its numbers
#: were researched with.
#
# `mana_cost` rather than `cost`: the fixture describes a mono-black cost with
# `pips` and `generic` and leaves `cost` at None, the database fills it in from
# the printed cost, and the engine reads neither - it reads `mana_cost`, which
# is the same object either way. Comparing the raw field would report a
# difference in how the cost is written down as if it were a different cost.
ENGINE_FIELDS = [
    "mv", "pips", "generic", "mana_cost", "kind", "tags", "land_type",
    "enters_tapped", "goldfish_castable", "needs_creature_in_yard",
    "needs_creature_on_bf", "mana_abilities", "ritual_gain", "ritual_color",
    "cost_reduction", "draw_on_cast", "life_on_cast", "tutor", "upkeep",
    "end_step", "skips_draw_step", "priority", "accelerant", "subtypes",
]

# The one name that differs, and the database is the one that is right.
#
# Scryfall spells it `Lim-Dul the Necromancer` with a circumflex; the deck
# research wrote it without one, which is also how every deck list and every
# CSV export writes it. The import ladder folds accents precisely so the two
# meet (that is rung 5, doing its job), and the catalogue then keeps the
# correct spelling. Comparing on the folded name is the honest test.
KNOWN_SPELLING_DIFFERENCES = {"lim dul the necromancer"}


@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker):
    """Ingest the catalogue and seed the reference deck, once.

    Written outside any test's transaction, so it is deleted again after the
    module - it used to stay, and every later module found its user, deck and
    70 annotations (phase 9 I).
    """
    with django_db_blocker.unblock():
        load_catalogue()
        call_command("seed_reference_deck", verbosity=0)
        yield Deck.objects.get(name="Chainer, Dementia Master")
        forget_module_rows()


@pytest.fixture(scope="module")
def converted(seeded, django_db_blocker):
    with django_db_blocker.unblock():
        return adapter.convert(seeded)


# --- composition ------------------------------------------------------------


def test_the_database_deck_has_the_same_cards(converted):
    """99 cards, same names, same quantities."""
    from collections import Counter

    built = Counter(normalise(card.name) for card in converted.definition.library)
    expected = Counter(normalise(card.name) for card in chainer.build_deck())
    assert built == expected


def test_the_commander_survives_the_round_trip(converted):
    assert normalise(converted.definition.commander.name) == normalise(chainer.COMMANDER.name)


def test_only_the_known_spelling_differs(converted):
    """Accents are the only thing the round trip changes about a name."""
    built = {card.name for card in converted.definition.library}
    expected = {card.name for card in chainer.build_deck()}

    differing = {normalise(name) for name in built ^ expected}
    assert differing <= KNOWN_SPELLING_DIFFERENCES


def test_no_model_instance_crosses_the_boundary(converted):
    """The engine must be runnable without Django.

    A `Card` holding a queryset or a model would make `simulation/` depend on
    the database through the back door, and the four vendored test files would
    stop meaning anything.
    """
    for card in converted.definition.library:
        for value in vars(card).values():
            assert not hasattr(value, "_meta"), f"{card.name} carries a model instance"
            assert not hasattr(value, "query"), f"{card.name} carries a queryset"


# --- field-by-field ---------------------------------------------------------


def test_every_engine_field_matches_the_fixture(converted):
    """The useful failure message: names the card and the field.

    Both sides are indexed by name, because the point is the card's content,
    not where it happens to sit in the list.
    """
    built = {normalise(card.name): card for card in converted.definition.library}
    expected = {normalise(card.name): card for card in chainer.build_deck()}

    assert set(built) == set(expected)

    mismatches = []
    for name, want in expected.items():
        got = built[name]
        for attribute in ENGINE_FIELDS:
            if getattr(got, attribute) != getattr(want, attribute):
                mismatches.append(
                    f"{name}.{attribute}: db={getattr(got, attribute)!r} "
                    f"fixture={getattr(want, attribute)!r}"
                )
    assert not mismatches, "\n".join(mismatches)


def test_the_commander_matches_field_for_field(converted):
    got, want = converted.definition.commander, chainer.COMMANDER
    for attribute in ENGINE_FIELDS:
        assert getattr(got, attribute) == getattr(want, attribute), attribute


def test_the_deck_definitions_are_equal(converted):
    """The plan's own acceptance line, once order and accents are normalised."""
    from dataclasses import replace

    def same(card):
        # Both sides get the cost spelled out, for the reason given above
        # ENGINE_FIELDS: `cost=None` and an equal explicit cost are the same
        # cost, and this comparison is about decks, not about spelling.
        # `types` and `categories` are left out: the hand-written deck never
        # had a type line or community tags, and both are read by the draw
        # statistics only, never by a rule (Phase 9 E) - the identical
        # aggregates below are the proof of that.
        return replace(card, name=normalise(card.name), cost=card.mana_cost,
                       types=frozenset(), categories=frozenset())

    def fold(definition):
        return tuple(
            sorted(
                (same(card) for card in definition.library),
                key=lambda card: (card.name, card.mv),
            )
        )

    assert fold(converted.definition) == fold(chainer.DECK)
    assert same(converted.definition.commander) == same(chainer.COMMANDER)


# --- the number that matters ------------------------------------------------


@pytest.mark.parametrize("seed", [11, 20260917])
def test_the_database_deck_produces_identical_aggregates(converted, seed):
    """A deck out of Postgres simulates exactly like the hand-written one.

    This is the definition of done for Phase 2.
    """
    from tests.test_engine_parity import summarise

    # Fold the accents before sorting. Otherwise the two decks differ by one
    # code point, and whether that changes the shuffled order depends on which
    # other cards happen to sort nearby - a test that passes by luck.
    from_db = analysis.run(400, turns=4, seed=seed, deck=_comparable(converted.definition))
    from_fixture = analysis.run(400, turns=4, seed=seed, deck=_comparable(chainer.DECK))

    assert summarise(from_db) == summarise(from_fixture)


def _comparable(definition):
    """The deck with folded names and a deterministic order."""
    from dataclasses import replace

    from simulation.cards import DeckDefinition

    return DeckDefinition(
        name=definition.name,
        commander=definition.commander,
        library=tuple(
            sorted(
                (replace(card, name=normalise(card.name)) for card in definition.library),
                key=lambda card: (card.name, card.mv),
            )
        ),
    )


# --- honesty ----------------------------------------------------------------


def test_the_conversion_reports_what_it_could_not_model(converted):
    """Coverage ships with the adapter, not as a later addition.

    Some cards legitimately have gaps - `Cabal Coffers` scales with the board,
    and the deriver says so. The requirement is that they are *reported*, not
    that there are none.
    """
    assert converted.cards_total > 0
    assert 0.0 <= converted.readable <= 1.0
    for gap in converted.gaps:
        assert gap.card and gap.field and gap.reason


def test_annotations_were_seeded_as_builtin_scope(seeded):
    """Built-in, not owned by the seeding user.

    They are the values the deck was researched with, not one account's
    opinion, and every user's copy of this deck should start from them.
    """
    annotations = CardAnnotation.objects.filter(oracle_card__in_decks__deck=seeded)
    assert annotations.exists()
    assert all(annotation.scope == "builtin" for annotation in annotations)


def test_a_deck_scoped_annotation_beats_a_builtin_one(seeded):
    """Narrowest scope wins - the rule the whole merge order rests on."""
    from cards.models import OracleCard

    sol_ring = OracleCard.objects.get(front_name="Sol Ring")
    CardAnnotation.objects.create(
        owner=seeded.owner, deck=seeded, oracle_card=sol_ring,
        overrides={"priority": 1}, note="test override",
    )

    rebuilt = adapter.deck_definition(seeded)
    card = next(c for c in rebuilt.library if c.name == "Sol Ring")
    assert card.priority == 1, "the deck-scoped annotation did not win"

    # Everything else about the card still comes from the built-in annotation.
    assert card.mana_abilities == chainer.SPELLS[0].mana_abilities or card.accelerant


def test_an_unknown_override_key_is_rejected(seeded):
    """A typo must be an error, not a silently ignored setting.

    A dropped key produces a simulation that is wrong with nothing in any log
    to say so.
    """
    from django.core.exceptions import ValidationError

    from cards.models import OracleCard

    with pytest.raises(ValidationError):
        CardAnnotation.objects.create(
            owner=seeded.owner,
            deck=seeded,
            oracle_card=OracleCard.objects.get(front_name="Swamp"),
            overrides={"pirority": 5},
        )


# --- reading colour off a card ----------------------------------------------
#
# These do not need the database: they are the rules the adapter applies once
# the rows are in hand, and they are the part that decides what colour a deck
# actually makes.


def _profile(**fields):
    """A stand-in for a DerivedProfile row, with the defaults it would have."""
    from types import SimpleNamespace

    defaults = dict(
        pips={}, generic=0, colorless=0, has_x=False, mv=0,
        produces_mana=False, mana_colors=[], mana_amount=None,
    )
    return SimpleNamespace(**{**defaults, **fields})


def test_a_rainbow_source_makes_the_deck_s_colour():
    """Arcane Signet taps for any colour. In a mono-black deck that is black.

    Reading it as white would put mana in the pool that no card in the deck
    can spend, and the deck would look a card short every turn it drew one.
    """
    gaps = []
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=1, mana_colors=list("WUBRG")),
        {}, "rock", "Arcane Signet", gaps, frozenset({"B"}),
    )

    assert abilities[0].produces == (("B", 1),)
    # One colour on offer is no choice, and since engine version 5 a choice
    # is no gap either: the pool holds it (P19 R1).
    assert gaps == []


def test_a_rainbow_source_offers_every_colour_of_the_deck():
    """In a Temur deck Arcane Signet makes one of U, R or G - not white or black."""
    gaps = []
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=1, mana_colors=list("WUBRG")),
        {}, "rock", "Arcane Signet", gaps, frozenset({"U", "R", "G"}),
    )
    assert abilities[0].produces == (("URG", 1),)
    assert gaps == []


def test_a_source_of_colours_the_deck_does_not_play_offers_all_of_them():
    gaps = []
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=1, mana_colors=["U", "W"]),
        {}, "land", "Tundra", gaps, frozenset({"B"}),
    )
    assert abilities[0].produces == (("WU", 1),)
    assert gaps == []


def test_one_colour_is_not_a_choice_and_reports_no_gap():
    gaps = []
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=1, mana_colors=["G"]),
        {}, "land", "Forest", gaps, frozenset({"G"}),
    )
    assert abilities[0].produces == (("G", 1),)
    assert gaps == []


def test_an_old_annotation_keeps_working():
    """Rows written before the engine knew about green are still valid.

    A stored judgement does not stop being true because the vocabulary grew.
    """
    assert adapter._annotated_production({"mana_black": 1}) == {"B": 1}
    assert adapter._annotated_production({"mana_colorless": 2}) == {"C": 2}
    assert adapter._annotated_production({"mana_black": 0, "mana_colorless": 0}) == {}
    assert adapter._annotated_production({}) is None, "no keys means no opinion"


def test_the_general_key_wins_over_the_old_pair():
    overrides = {"mana_produces": {"G": 1}, "mana_black": 1}
    assert adapter._annotated_production(overrides) == {"G": 1}


def test_the_cost_comes_from_the_printed_cost_not_the_flattened_pips():
    """The profile stores `{W/U}` as one white *and* one blue pip.

    That is right as "may be paid with" and wrong as "must be paid with", and
    only the engine's payer can tell the two apart - so the adapter parses the
    printed cost rather than reassembling the profile's numbers.
    """
    from types import SimpleNamespace

    card = SimpleNamespace(mana_cost="{W/U}")
    cost = adapter._cost(card, _profile(pips={"W": 1, "U": 1}), {})

    assert cost.colored == {}, "a hybrid pip is not a fixed requirement"
    assert len(cost.hybrid) == 1
    assert cost.mv == 1


def test_an_annotation_still_overrides_the_cost():
    from types import SimpleNamespace

    card = SimpleNamespace(mana_cost="{2}{B}")
    cost = adapter._cost(card, _profile(pips={"B": 1}, generic=2), {"generic": 0})

    assert (cost.colored, cost.generic) == ({"B": 1}, 0)


def test_a_bare_number_of_pips_still_means_black():
    from types import SimpleNamespace

    card = SimpleNamespace(mana_cost="{2}{B}")
    cost = adapter._cost(card, _profile(pips={"B": 1}, generic=2), {"pips": 2})

    assert cost.colored == {"B": 2}


@pytest.mark.parametrize("overrides", [
    {"mana_produces": {"Black": 1}},
    {"mana_produces": {"B": "one"}},
    {"mana_produces": [["B", 1]]},
    {"ritual_color": "black"},
    {"scaling_color": "Z"},
])
def test_a_colour_the_engine_does_not_know_is_rejected(seeded, overrides):
    """Caught at save time, not three screens away inside a worker.

    `normalised()` would raise on these anyway - but it would raise while a
    simulation was running, with nothing to connect the failure back to the
    form that accepted the value.
    """
    from django.core.exceptions import ValidationError

    from cards.models import OracleCard

    with pytest.raises(ValidationError):
        CardAnnotation.objects.create(
            owner=seeded.owner,
            deck=seeded,
            oracle_card=OracleCard.objects.get(front_name="Swamp"),
            overrides=overrides,
        )


def test_a_valid_colour_annotation_saves(seeded):
    from cards.models import OracleCard

    annotation = CardAnnotation.objects.create(
        owner=seeded.owner,
        deck=seeded,
        oracle_card=OracleCard.objects.get(front_name="Bojuka Bog"),
        overrides={"mana_produces": {"G": 1}, "ritual_color": "R"},
    )
    assert annotation.pk


def test_the_commander_s_colour_identity_decides_what_a_rainbow_source_makes(seeded):
    """Which is what the cards themselves say: "in your commander's color identity".

    The reference deck's commander is mono-black, so its Signet makes black -
    and that now comes from the deck rather than from a default that would
    follow the card into everyone else's decks.
    """
    colors = adapter._deck_colors([], seeded)
    assert colors == frozenset({"B"})


def test_the_signet_is_no_longer_black_by_decree(seeded):
    """The built-in annotation must not carry a colour any more.

    A built-in applies to every deck of every user. "Arcane Signet makes black"
    is true of this deck and of no other, so it has to be derived per deck.
    """
    from cards.models import OracleCard

    annotation = CardAnnotation.objects.get(
        owner=None, deck=None, oracle_card=OracleCard.objects.get(front_name="Arcane Signet")
    )
    assert "mana_produces" not in annotation.overrides
    # The judgements that really are judgements stay.
    assert annotation.overrides["priority"] == 92
    assert annotation.overrides["accelerant"] is True


def test_the_signet_still_makes_black_in_this_deck(converted):
    """Derived rather than decreed - and the same answer, which is the point."""
    from simulation.cards import FLAT, ManaAbility

    signet = next(c for c in converted.definition.library if c.name == "Arcane Signet")
    assert signet.mana_abilities == (ManaAbility(FLAT, {"B": 1}),)


def test_a_choice_of_colours_is_no_longer_a_gap(converted):
    """Until engine version 5 the reader picked a colour and had to say so."""
    signet_gaps = [g for g in converted.gaps if g.card == "Arcane Signet"]
    assert not any(g.field == "mana_abilities" for g in signet_gaps)


# --- a Signet's cost, and permanents that stay tapped (engine version 3) ----

def test_a_signet_reaches_the_engine_with_its_cost_and_both_colours():
    """Trap 49: `{1}, {T}: Add {U}{B}` is one of EACH for {1}, not a choice.

    Read the old way it was a choice between blue and black, picked as one of
    them and made twice, for nothing.
    """
    gaps = []
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=2, mana_colors=["B", "U"],
                 mana_produces={"U": 1, "B": 1}, mana_activation=1),
        {}, "rock", "Dimir Signet", gaps, frozenset({"U", "B"}),
    )
    assert abilities == (adapter.ManaAbility(adapter.FLAT, {"U": 1, "B": 1},
                                             activation_generic=1),)
    assert gaps == [], "both colours at once is not a choice, so there is no gap"


def test_an_annotation_can_say_a_source_only_has_to_tap():
    abilities = adapter._mana_abilities(
        _profile(produces_mana=True, mana_amount=2, mana_produces={"U": 1, "B": 1},
                 mana_activation=1),
        {"mana_activation": 0}, "rock", "Dimir Signet", [], frozenset(),
    )
    assert abilities[0].activation_generic == 0
