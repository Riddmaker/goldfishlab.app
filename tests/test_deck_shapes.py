"""The test decks, and the things only they can prove.

Six phases were built and verified against exactly one deck. The mono-black
Chainer reference deck is a good fixed point - it is hand-annotated, it is real,
and its numbers are known - but it is also one shape, and a whole class of bug
can only be seen from another one. These tests are that other shape.

What each deck is for is written next to it in `decks/fixtures.py`. What matters
here, in order:

1. **The gap Phase 4 left open.** Its verification list asked that a deck full of
   unmodelled cards shows a loud, unmissable warning. What was actually tested
   was `blindspots.find([]) == []` and the reference deck - the empty case and a
   deck that does not have the problem. `test_a_deck_the_engine_cannot_read_*`
   is the missing half, and it asserts against the rendered page rather than the
   function, because "unmissable" is a claim about what a person sees.
2. **The shapes and the offline sample cannot drift apart.** Tests never hit the
   network, so a deck may only name cards that are in
   `tests/fixtures/oracle_cards_sample.jsonl.gz`. When they disagree the failure
   reads exactly like "Scryfall does not have this card", which sends you
   upstream after a problem that is two files away. The first test here says so
   in as many words.
3. **Scaling mana, driven through the database.** `PER_CONTROLLED`,
   `TYPE_ADDING` and `DOUBLE_SUBTYPE` were only ever proved through
   `simulation/fixtures/chainer.py`, which is Python and never travels through
   a `CardAnnotation` row, the adapter's three-scope merge, or a JSON column.
4. **Two regressions these decks found.** A basic Forest reaching the engine
   with no land types at all, and a coverage score that could go negative. Both
   are pinned below, because a bug a test deck caught once is a bug the test
   deck should keep catching.
"""

import html

import pytest
from django.contrib.auth import get_user_model

from cards.models import OracleCard
from decks import seeding
from decks.fixtures import (
    BY_KEY,
    FIVE_COLOUR,
    LAND_HEAVY,
    LAND_LIGHT,
    NO_COMMANDER,
    OPPONENT_DEPENDENT,
    SCALING_RAMP,
    SHAPES,
    UNMODELLABLE,
)
from decks.models import Deck, DeckCard
from simulation import analysis, mana
from simulation.cards import DOUBLE_SUBTYPE, PER_CONTROLLED, TYPE_ADDING
from simulations import blindspots, gaps
from simulations.engine import adapter
from simulations.models import CardAnnotation

pytestmark = pytest.mark.django_db

User = get_user_model()

#: Small enough to stay in the fast loop, large enough for the two land counts
#: to separate. At ~110 microseconds per game per turn this is a few hundred
#: milliseconds, and the difference it measures is enormous.
GAMES = 300
TURNS = 6


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="shapes@example.com", password="pw-for-test-only")


@pytest.fixture
def build(owner):
    """Build one shape, by key or by object."""

    def _build(shape):
        return seeding.build(BY_KEY[shape] if isinstance(shape, str) else shape, owner).deck

    return _build


def _colors_of(deck: Deck) -> frozenset[str]:
    entries = list(deck.entries.select_related("oracle_card", "oracle_card__profile"))
    return adapter._deck_colors(entries, deck)


def _by_name(deck: Deck) -> dict:
    """Every reading of this deck, keyed by card name."""
    return {reading.oracle_card.front_name: reading for reading in adapter.readings(deck)}


# --- the shapes and the sample must agree -----------------------------------


@pytest.mark.parametrize("shape", SHAPES, ids=lambda shape: shape.key)
def test_every_shape_builds_from_the_committed_sample(owner, shape):
    """A deck may only name cards the offline fixture actually has.

    This is the test that fails first when somebody adds a card to a shape and
    forgets to rebuild the sample, and it fails saying which card - rather than
    leaving a `KeyError` in whichever test happened to run next.
    """
    result = seeding.build(shape, owner)

    assert result.deck.card_count == shape.card_count
    assert DeckCard.objects.filter(deck=result.deck).count() == len(shape.cards)
    if shape.commander:
        assert result.deck.commander is not None
    else:
        assert result.deck.commander_id is None


def test_a_shape_naming_an_unknown_card_says_so(owner):
    """The error names the card and the two files that could fix it."""
    from dataclasses import replace

    broken = replace(FIVE_COLOUR, cards=((1, "Not A Real Card"),), commander=None)
    with pytest.raises(seeding.MissingCards) as raised:
        seeding.build(broken, owner)

    assert raised.value.names == ["Not A Real Card"]
    assert "build_card_fixtures" in str(raised.value)


# --- the gap Phase 4 left open ----------------------------------------------


def test_a_deck_the_engine_cannot_read_has_bad_coverage(build):
    """The deck that has the problem, measured.

    Every card in it is one the deriver refused to guess at, and none of them is
    annotated. Anything close to a respectable coverage score here would mean
    the score is not measuring what it claims to.
    """
    conversion = adapter.convert(build(UNMODELLABLE))

    # The reading score - the one the pages show - is 4 of 12 here: a few cards
    # are unreadable only in their casting order, which no page counts since
    # phase 9 C. The mixed number this used to check was under 25%.
    assert conversion.readable < 0.5
    assert conversion.cards_with_gaps >= conversion.cards_total - 1


def test_a_deck_the_engine_cannot_read_shouts_about_it(client, owner, build):
    """**The verification item Phase 4 could not check.**

    Asserted against the rendered page, not against `blindspots.find`, because
    the claim is "loud and unmissable" and that is a claim about what somebody
    actually sees. The named cards have to be on the page, each linked to the
    field that answers it - a warning with no way to act on it is a disclaimer,
    and people learn to skip disclaimers.
    """
    deck = build(UNMODELLABLE)
    client.force_login(owner)

    response = client.get(deck.get_absolute_url())
    # Unescaped, because the assertions below are about what a person reads.
    # Django renders `Gaea's Cradle` as `Gaea&#x27;s Cradle`, and a test that
    # tripped over that would be testing the template engine.
    body = html.unescape(response.content.decode())

    assert response.status_code == 200
    assert "What this simulation does not model" in body
    # The two limitations this deck is built out of.
    assert "Mana sources nobody has pinned down" in body
    assert "Cards that need an opponent" in body
    # And the cards themselves, by name.
    for name in ("Exotic Orchard", "Gaea's Cradle", "Rhystic Study", "Mystic Remora"):
        assert name in body, f"{name} is a blind spot and is not on the page"


def test_a_deck_the_engine_can_read_stays_quiet_by_comparison(build):
    """The contrast that makes the test above mean something.

    Without it, a panel that shouted at every deck would pass.

    **The coverage score alone is the weak half of this.** With no annotations
    in the database - which is the state every deck starts in and most stay in -
    every deck scores badly, because "nobody has said how early to cast this" is
    a gap and saying so is the whole point. `land_heavy` scores poorly too.

    The strong half is the *named* limitation. `land_heavy` is made of cards the
    deriver reads: it raises no mana blind spot at all. `unmodellable` raises
    one covering most of the deck. That difference is what a person can act on,
    and it is the difference the panel is built to show.
    """
    loud_reads = adapter.readings(build(UNMODELLABLE))
    quiet_reads = adapter.readings(build(LAND_HEAVY))

    loud = {spot.key: spot.count for spot in blindspots.find(loud_reads)}
    quiet = {spot.key: spot.count for spot in blindspots.find(quiet_reads)}

    assert "unresolved_mana" not in quiet
    assert loud["unresolved_mana"] >= 5

    assert adapter.convert(build(UNMODELLABLE)).readable < adapter.convert(
        build(LAND_HEAVY)
    ).readable


def test_coverage_is_never_negative(owner):
    """A regression the `unmodellable` shape found on the way past.

    `convert` counted `cards_total` from deck entries but recorded gaps for the
    commander too, which is not a `DeckCard` (trap 7). A two-card deck whose
    commander the engine could not read therefore reported **-50% coverage**.
    "Always show a coverage score" is a settled decision, and a negative
    percentage is a worse answer than no answer at all.
    """
    deck = Deck.objects.create(owner=owner, name="two unreadable cards")
    DeckCard.objects.bulk_create(
        [
            DeckCard(deck=deck, oracle_card=card, quantity=1)
            for card in OracleCard.objects.filter(
                front_name__in=["Maze of Ith", "Exotic Orchard"]
            )
        ]
    )
    deck.commander = OracleCard.objects.get(front_name="Kenrith, the Returned King")
    deck.save(update_fields=["commander", "updated_at"])

    conversion = adapter.convert(deck)

    assert conversion.cards_total == 3, "the commander belongs in its own denominator"
    assert 0.0 <= conversion.readable <= 1.0


# --- five colours -----------------------------------------------------------


def test_the_five_colour_deck_is_read_as_five_colours(build):
    assert _colors_of(build(FIVE_COLOUR)) == frozenset("WUBRG")


def test_every_basic_land_taps_for_its_own_colour(build):
    """The bug this deck was built to catch, and did.

    `adapter._subtypes` only ever produced `{"swamp"}`, for a basic Swamp -
    a leftover from when the engine was mono-black. Every other basic reached
    the engine with no land types at all, which left the four non-swamp
    constants in `simulation/cards.py` unreachable from any database deck.

    The colour was not what broke: a Forest still taps for green through its own
    mana ability. What broke was everything that *counts* land types - Cabal
    Coffers' per-swamp scaling, Crypt Ghast's doubling, and the types Urborg and
    Yavimaya hand out.
    """
    readings = _by_name(build(FIVE_COLOUR))
    expected = {
        "Plains": ("plains", "W"), "Island": ("island", "U"),
        "Swamp": ("swamp", "B"), "Mountain": ("mountain", "R"),
        "Forest": ("forest", "G"),
    }

    for name, (subtype, color) in expected.items():
        card = readings[name].card
        assert card.subtypes == frozenset({subtype}), f"{name} lost its land type"
        assert mana.land_color(card, frozenset()) == color


def test_a_rainbow_land_records_the_choice_it_was_forced_to_make(build):
    """Command Tower makes one mana of any colour in the commander's identity.

    The pool counts mana rather than holding sources, so it cannot hold "either
    colour"; the adapter picks one and must report having done so. With a
    five-colour commander there is no defensible pick, which is what makes this
    the right deck to assert it on.
    """
    reading = _by_name(build(FIVE_COLOUR))["Command Tower"]

    reasons = [gap.reason for gap in reading.gaps if gap.field == "mana_abilities"]
    assert reasons, "the engine picked a colour and said nothing about it"
    assert "makes one of" in reasons[0]


def test_the_simulation_reports_more_than_one_colour(build):
    """The per-colour histograms, end to end.

    Phase 4 added a histogram per colour and a report section for them. Against
    a mono-black deck a bug that counted every colour as black would look
    exactly like a correct implementation.
    """
    definition = adapter.deck_definition(build(FIVE_COLOUR))
    result = analysis.run(GAMES, turns=TURNS, seed=20260918, deck=definition)

    final = result["turn_stats"][-1]
    made = {
        color: final[analysis.COLOR_FIELD[color]].total
        for color in "WUBRG"
        if final[analysis.COLOR_FIELD[color]].total
    }
    assert sorted(made) == list("BGRUW"), f"a five-colour deck made only {sorted(made)}"
    # And the mono-black shorthand still agrees with the colour it shadows, which
    # is what the golden snapshot is keyed on.
    assert final["black"].total == final[analysis.COLOR_FIELD["B"]].total


def test_a_deck_with_no_commander_falls_back_to_its_own_pips(build):
    """`_deck_colors` has two branches and only one had ever run.

    With a commander the answer is its colour identity. Without one there is no
    identity to read, and the deck's own coloured pips are the only evidence of
    what it wants. Every other deck in the suite has a commander.
    """
    deck = build(NO_COMMANDER)

    assert deck.commander_id is None
    assert _colors_of(deck) == frozenset("WUBRG")


# --- land counts ------------------------------------------------------------


def _mean_opening_lands(result) -> float:
    counter = result["opening_lands"]
    return sum(lands * n for lands, n in counter.items()) / sum(counter.values())


def test_more_lands_really_does_mean_more_lands_in_hand(build):
    """The two extremes, actually simulated.

    Twenty lands against forty-five. If the opening-hand model were reading
    anything other than the deck it was handed, these two would not separate -
    and a difference this large is the cheapest possible check that it is.
    """
    light = analysis.run(
        GAMES, turns=TURNS, seed=20260918, deck=adapter.deck_definition(build(LAND_LIGHT))
    )
    heavy = analysis.run(
        GAMES, turns=TURNS, seed=20260918, deck=adapter.deck_definition(build(LAND_HEAVY))
    )

    assert _mean_opening_lands(heavy) > _mean_opening_lands(light) + 1.0


def test_the_land_heavy_deck_mulligans_more_not_less(build):
    """Flooding, which is the interesting half.

    The intuition says more lands means fewer mulligans. The keep rule says
    otherwise: `game.keepable` throws back a hand of **0 or 6+ lands**, so a
    forty-five land deck mulligans into flood far more often than a twenty land
    deck mulligans into nothing.

    Asserted because it was guessed wrong once. It is also the failure mode a
    goldfish is least likely to report on its own - nobody loses to flooding
    against no opponent - so it is worth having a deck that makes it visible.
    """
    light = analysis.run(
        GAMES, turns=TURNS, seed=20260918, deck=adapter.deck_definition(build(LAND_LIGHT))
    )
    heavy = analysis.run(
        GAMES, turns=TURNS, seed=20260918, deck=adapter.deck_definition(build(LAND_HEAVY))
    )

    def mulliganed(result) -> int:
        return sum(n for taken, n in result["mulligans"].items() if taken)

    assert mulliganed(heavy) > mulliganed(light)


# --- scaling mana, through the database -------------------------------------


def test_the_scaling_rules_reach_the_engine_from_annotation_rows(build):
    """`PER_CONTROLLED`, `TYPE_ADDING` and `DOUBLE_SUBTYPE`, via Postgres.

    The reference deck proves these work when a Python fixture states them. It
    cannot prove they survive a `CardAnnotation` row: the JSON column, the
    three-scope merge and `SCALING_RULES` all sit between the two, and none of
    them was on the path the old proof took.
    """
    readings = _by_name(build(SCALING_RAMP))

    coffers = readings["Cabal Coffers"].card.ability(PER_CONTROLLED)
    assert coffers is not None
    assert coffers.subtype == "swamp"
    assert coffers.activation_generic == 2

    urborg = readings["Urborg, Tomb of Yawgmoth"].card.ability(TYPE_ADDING)
    assert urborg is not None and urborg.subtype == "swamp"

    yavimaya = readings["Yavimaya, Cradle of Growth"].card.ability(TYPE_ADDING)
    assert yavimaya is not None and yavimaya.subtype == "forest"

    ghast = readings["Crypt Ghast"].card.ability(DOUBLE_SUBTYPE)
    assert ghast is not None and ghast.subtype == "swamp"


def test_two_type_adders_make_every_land_both_types(build):
    """The documented ambiguity in `mana.land_color`, as a deck.

    With Urborg and Yavimaya both out, every land is a swamp *and* a forest, and
    the pool cannot hold "either colour". `land_color` resolves it in WUBRG
    order, which is deterministic and traceable rather than right - and that is
    a limitation worth having a deck for, because it is invisible in any deck
    with one type-adder.
    """
    readings = _by_name(build(SCALING_RAMP))
    forest = readings["Forest"].card
    urborg = readings["Urborg, Tomb of Yawgmoth"].card
    yavimaya = readings["Yavimaya, Cradle of Growth"].card

    granted = mana.granted_subtypes([urborg, yavimaya])
    assert granted == frozenset({"swamp", "forest"})

    assert mana.subtypes_of(forest, granted) == frozenset({"forest", "swamp"})
    # B before G in WUBRG order. The Forest is now also a Swamp, and the engine
    # picks the earlier colour.
    assert mana.land_color(forest, granted) == "B"


def test_cabal_coffers_counts_swamps_it_learned_about_from_the_database(build):
    """The end of the chain: an annotation row changing how much mana there is.

    Coffers costs {2} and adds one black per swamp controlled. Nothing in the
    derived data can tell it from a land that taps for nothing - Scryfall gives
    colour and never amount - so every mana it makes here arrived through a
    `CardAnnotation` row.
    """
    readings = _by_name(build(SCALING_RAMP))
    swamp = readings["Swamp"].card
    coffers = readings["Cabal Coffers"].card

    lands = [swamp] * 5 + [coffers]
    pool = mana.available_mana(lands, lands, [], 0)

    # Five swamps make five black. Coffers spends two of them and returns one
    # per swamp controlled - itself not among them, having no swamp type of its
    # own without Urborg - so 5 - 2 + 5.
    assert pool.black == 8

    # And without the judgement it is a land that taps for nothing: the same
    # board makes only what the swamps make.
    bare = mana.available_mana([swamp] * 5, [swamp] * 5, [], 0)
    assert bare.black == 5


def test_a_shapes_judgements_are_scoped_to_its_own_deck(owner):
    """Never a built-in.

    `owner=None, deck=None` applies to every deck of every user, so a fixture
    that seeded one would change the reference deck's numbers from another test
    file - and the reference deck is the fixed point everything else is measured
    against.
    """
    before = CardAnnotation.objects.filter(owner__isnull=True, deck__isnull=True).count()
    deck = seeding.build(SCALING_RAMP, owner).deck

    written = CardAnnotation.objects.filter(deck=deck)
    assert written.count() == len(SCALING_RAMP.annotations)
    assert all(row.owner_id == owner.pk for row in written)
    assert CardAnnotation.objects.filter(owner__isnull=True, deck__isnull=True).count() == before


def test_rebuilding_a_shape_does_not_pile_up(owner):
    """Both callers run repeatedly - the command while iterating on a
    screenshot, the tests once per test. A build that added rather than
    replaced would make the second run mean something different from the first.
    """
    first = seeding.build(SCALING_RAMP, owner).deck
    second = seeding.build(SCALING_RAMP, owner).deck

    assert first.pk == second.pk
    assert DeckCard.objects.filter(deck=second).count() == len(SCALING_RAMP.cards)
    assert CardAnnotation.objects.filter(deck=second).count() == len(SCALING_RAMP.annotations)


# --- cards that need an opponent --------------------------------------------


def test_the_detector_names_the_cards_it_was_written_for(build):
    """Rhystic Study, Smothering Tithe, Esper Sentinel, Mystic Remora.

    The four cards named in `blindspots`' own docstring as the case it exists
    for. Until this deck there was nowhere to point it at them: the reference
    deck contains one of the four.
    """
    spot = blindspots.opponent_dependent(adapter.readings(build(OPPONENT_DEPENDENT)))
    named = {suspect.name for suspect in spot.suspects}

    assert {"Rhystic Study", "Smothering Tithe", "Esper Sentinel", "Mystic Remora"} <= named


def test_a_deck_of_opponent_dependent_cards_still_simulates(build):
    """It has to produce a number, and the number has to be honest.

    The failure this guards against is the opposite of a crash: these cards cost
    mana, resolve, sit on the battlefield and get counted as permanents the deck
    deployed successfully. That is how a simulation flatters a deck it does not
    understand, and the only defence is the panel saying so.
    """
    deck = build(OPPONENT_DEPENDENT)
    result = analysis.run(
        GAMES, turns=TURNS, seed=20260918, deck=adapter.deck_definition(deck)
    )

    assert result["iterations"] == GAMES
    assert blindspots.find(adapter.readings(deck)), "this deck of all decks must warn"


def test_coverage_counts_every_copy(owner):
    """Phase 10 T5.9 (K2): thirty Swamps are thirty of the deck, not one."""
    deck = Deck.objects.create(owner=owner, name="thirty swamps")
    swamp = OracleCard.objects.get(front_name="Swamp")
    maze = OracleCard.objects.get(front_name="Maze of Ith")
    DeckCard.objects.bulk_create([
        DeckCard(deck=deck, oracle_card=swamp, quantity=30),
        DeckCard(deck=deck, oracle_card=maze, quantity=1),
    ])
    deck.commander = OracleCard.objects.get(front_name="Kenrith, the Returned King")
    deck.save(update_fields=["commander", "updated_at"])

    conversion = adapter.convert(deck)
    unreadable = gaps.cards_with(conversion.gaps, gaps.READING)

    assert conversion.cards_total == 3
    assert conversion.copies_total == 32, "30 Swamps, the Maze and the commander"
    assert "Swamp" not in unreadable
    assert conversion.copies_unreadable == len(unreadable) <= 2
