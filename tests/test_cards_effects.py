"""Phase 5b: the effect catalogue, read off tags the application already had.

Scryfall's community tag DAG has been in the database since Phase 1 - 4,544
tags over 35,568 cards - and `cards/profiles.py` mapped sixteen of them. These
tests cover what the mapping was widened to, and they are mostly about what it
still refuses to claim.

The division of labour is the thing to keep. **A tag is a category and never a
number.** `tutor-to-graveyard` knows which zone Buried Alive searches to and
cannot know that it finds three cards; `Search your library for up to three
creature cards` knows the three and nothing about what the card is for. So the
zone comes from the tag, the amount comes from the text, and a tutor missing
either half is a gap rather than a `TutorSpec` with a plausible 1 in it.

The strongest evidence here is `test_the_three_hand_annotated_tutors_*`: the
reference deck was annotated by somebody who plays it, and the deriver now
reproduces all three of their tutors without being shown the answer.
"""


import pytest

from cards import profiles
from cards.models import OracleCard
from simulation.fixtures import chainer

pytestmark = pytest.mark.django_db



@pytest.fixture
def catalogue(catalogue):
    """The shared sample (conftest), as `{name: profile}`."""
    return {card.front_name: card.profile for card in
            OracleCard.objects.select_related("profile")}


def _tutor(text, *tags, name="Test Card"):
    """Read a tutor off one card's text and tag set, with no database row."""
    return profiles._tutor(OracleCard(front_name=name, oracle_text=text), set(tags))


# --- the tag says which zone, the text says how many -----------------------

def test_the_zone_comes_from_the_tag_and_the_amount_from_the_text():
    found = _tutor(
        "Search your library for up to three creature cards, put them into "
        "your graveyard, then shuffle.",
        "tutor", "tutor-to-graveyard", "tutor-creature",
    )
    assert (found.zone, found.count, found.kind) == ("graveyard", 3, "creature")
    assert found.reason == ""


def test_a_tutor_with_no_zone_tag_is_a_gap_not_a_guess():
    """Vampiric Tutor's shape: the count is right there, the zone is not ours.

    It searches to the *top of the library*, which the engine has no move for.
    Reading it as a tutor to hand would turn a card that costs you a draw step
    into a strictly better Demonic Tutor.
    """
    found = _tutor("Search your library for a card, then shuffle and put that "
                   "card on top. You lose 2 life.",
                   "tutor", "tutor-card", "tutor-to-top")
    assert found.zone == ""
    assert found.count == 1
    assert "zone" in found.reason


def test_the_count_is_never_defaulted_to_one():
    """The single most tempting wrong answer in the module.

    A tag says a card tutors. It never says how many cards it finds. Defaulting
    to 1 is right on Demonic Tutor and wrong on every Buried Alive, and wrong in
    a way that looks completely correct on the page.
    """
    found = _tutor("Target player searches their library for a card.",
                   "tutor", "tutor-to-hand")
    assert found.count is None
    assert found.zone == ""
    assert "readable" in found.reason


def test_somebody_elses_search_is_not_ours():
    """The pronoun check, which this module already applies to life loss."""
    found = _tutor("Target opponent searches their library for a creature card.",
                   "tutor", "tutor-to-hand", "tutor-creature")
    assert found.count is None


def test_a_card_nobody_tagged_a_tutor_is_not_one():
    found = _tutor("Search your library for a basic land card.", "ramp")
    assert (found.zone, found.count, found.reason) == ("", None, "")


def test_tutoring_onto_the_battlefield_is_reported_not_redirected():
    """The engine puts a found card in hand or in the graveyard. There is no
    third move, and a tutor quietly redirected to the graveyard would be a
    Natural Order that reanimates nothing."""
    found = _tutor("Search your library for a creature card and put it onto "
                   "the battlefield, then shuffle.",
                   "tutor", "tutor-to-battlefield", "tutor-creature")
    assert found.zone == "battlefield"
    assert "cannot do" in found.reason


def test_a_restriction_the_engine_cannot_express_is_not_widened_to_any_card():
    """`tutor-mv` and `tutors-by-name` restrict a search in ways `TutorSpec`
    has no field for. Dropping the restriction makes every one of them a
    Demonic Tutor, which is the most valuable card in the format."""
    found = _tutor("Search your library for a card named Forest.",
                   "tutor", "tutor-to-hand", "tutors-by-name")
    assert "cannot describe" in found.reason


def test_two_searches_on_one_card_are_not_modelled():
    found = _tutor("Search your library for a creature card, then search your "
                   "library for a land card.",
                   "tutor", "tutor-to-hand")
    assert found.count is None
    assert "more than once" in found.reason


def test_a_tutor_that_taxes_you_says_so():
    """Gamble opens with Demonic Tutor's exact sentence and is a different card."""
    found = _tutor("Search your library for a card, put that card into your "
                   "hand, discard a card at random, then shuffle.",
                   "tutor", "tutor-to-hand", "tutor-card")
    assert (found.zone, found.count) == ("hand", 1)
    assert "random" in found.reason


# --- the ground truth we actually have -------------------------------------

def test_the_three_hand_annotated_tutors_are_reproduced_exactly(catalogue):
    """The reference deck's author wrote these three by hand. The deriver was
    not shown their answers and now arrives at the same ones.

    This is the only ground truth in the project for what a tutor "should"
    read as, so it is worth asserting field by field rather than in aggregate.
    """
    by_name = {card.name: card for card in chainer.DECK.library}

    for name in ("Demonic Tutor", "Buried Alive", "Grim Tutor"):
        hand_written = by_name[name].tutor
        derived = catalogue[name]

        assert derived.tutor_count == hand_written.count, name
        assert (derived.tutor_to == "hand") == hand_written.to_hand, name
        assert derived.tutor_kind == hand_written.kind, name


def test_grim_tutors_life_was_already_being_derived(catalogue):
    """Three life, read by the same pronoun check that separates a cost from a
    payoff. The tutor work reuses it rather than reading the text twice."""
    assert catalogue["Grim Tutor"].self_life_loss == 3
    assert catalogue["Demonic Tutor"].self_life_loss is None


# --- skipping the draw step ------------------------------------------------

@pytest.mark.parametrize(
    ("text", "skips"),
    [
        ("Skip your draw step. Whenever you discard a card, exile that card.", True),
        ("Flying\nSkip your draw step.", True),
        ("Target player skips their next draw step.", False),
        ("When this creature dies, return it to the battlefield and you skip "
         "your next draw step.", False),
        ("Draw a card.", False),
    ],
)
def test_only_a_permanent_skip_of_your_own_draw_step_counts(text, skips):
    """Three cards wear the `skip-draw-step` tag and mean three different
    things. Fatigue makes somebody *else* skip one; Ivory Gargoyle skips
    exactly one; Necropotence skips every one. The engine's flag means the
    third, so only the third may set it - which is why this is read off the
    printed sentence and not off the tag.
    """
    card = OracleCard(front_name="Test Card", oracle_text=text)
    assert bool(profiles._SKIPS_DRAW_STEP.search(card.oracle_text)) is skips


def test_necropotence_is_read_off_the_card_at_last(catalogue):
    """It is in the reference deck, where somebody annotated it by hand. Now
    the catalogue knows it too, for every other deck that plays it."""
    assert catalogue["Necropotence"].skips_draw_step


# --- contradictions are reported, never resolved ---------------------------

def test_a_card_the_taggers_call_multi_mana_and_we_read_as_one_is_flagged():
    """Not a value. "More than one" is not a number, and the honest response to
    a disagreement is to say there is one."""
    card = OracleCard(front_name="Test Rock", oracle_text="{T}: Add {C}.",
                      produced_mana=["C"])
    profile = profiles.derive(card, {"adds-multiple-mana", "mana-rock"})
    assert profile.needs_review
    assert any("more than one mana" in reason for reason in profile.review_reasons)


def test_no_contradiction_when_the_reading_agrees_with_the_tag():
    card = OracleCard(front_name="Test Rock", oracle_text="{T}: Add {C}{C}.",
                      produced_mana=["C"])
    profile = profiles.derive(card, {"adds-multiple-mana", "mana-rock"})
    assert not any("more than one mana" in reason
                   for reason in profile.review_reasons)


def test_an_additional_casting_cost_is_a_cost_the_engine_never_pays():
    """Diabolic Intent is Demonic Tutor plus a sacrificed creature. The engine
    pays mana and nothing else, so it would get the tutor for free - and the
    tutor derivation is what turned that from harmless into a live overstatement.
    """
    card = OracleCard(
        front_name="Test Tutor",
        oracle_text="As an additional cost to cast this spell, sacrifice a "
                    "creature.\nSearch your library for a card, put that card "
                    "into your hand, then shuffle.",
    )
    profile = profiles.derive(card, {"tutor", "tutor-to-hand", "tutor-card"})
    assert profile.tutor_count == 1, "the tutor itself still reads"
    assert any("does not pay" in reason for reason in profile.review_reasons)


# --- the role the report could never fire ----------------------------------

def test_the_draw_engine_role_can_now_come_out_of_the_database(catalogue):
    """`simulation/analysis.py` counts `draw_engine` and the report prints "A
    card-advantage engine in play". Before this mapping the only cards that
    could carry it were in the hand-written fixture, so for every deck a user
    imported that row was structurally always zero.
    """
    engines = [name for name, profile in catalogue.items()
               if "draw_engine" in profile.role_tags]
    assert engines, "no card in the sample carries the role the report reads"
    assert "Phyrexian Arena" in engines


def test_the_mapping_does_not_claim_cards_the_deck_author_did_not(catalogue):
    """Precision over recall, measured against the one hand-curated deck.

    The broader `repeatable-card-advantage` tag would have found all eight of
    the author's engines and added Phyrexian Reclamation, which draws no cards.
    A missed engine under-reports a deck; an invented one puts a milestone on a
    report that never happened.
    """
    truth = {card.name for card in chainer.DECK.library
             if "draw_engine" in card.tags}
    claimed = {name for name, profile in catalogue.items()
               if "draw_engine" in profile.role_tags and name in
               {card.name for card in chainer.DECK.library}}

    assert claimed <= truth, f"claimed cards the author did not: {claimed - truth}"


# --- the mapping and the catalogue cannot drift apart ----------------------

#: Mapped tags that no card in the offline sample happens to carry, with the
#: number of cards each covers in the live DAG, measured 2026-09-18. They are
#: listed rather than asserted because the sample is 359 tag rows out of 4,544
#: and growing it to cover a planeswalker tutor would mean carrying a
#: planeswalker tutor. Anything NOT on this list must be in the sample, so a
#: typo in a tag the application actually uses still fails here.
VERIFIED_UPSTREAM_ONLY = {
    "tutor-artifact": 76,
    "tutor-enchantment": 37,
    "tutor-instant": 26,
    "tutor-planeswalker": 46,
    "tutor-sorcery": 19,
}


def test_every_mapped_tag_exists_in_the_tag_catalogue(catalogue):
    """Both directions have drifted before.

    A slug that no longer exists upstream is a mapping line that silently does
    nothing, and nothing else in the application would ever say so - the line
    keeps parsing, the tag keeps not matching, and the field keeps being empty
    for a reason no page can explain.
    """
    from cards.models import Tag

    known = set(Tag.objects.values_list("slug", flat=True))
    mapped = (set(profiles.ROLE_FROM_TAG)
              | set(profiles.TUTOR_ZONE_FROM_TAG)
              | set(profiles.TUTOR_KIND_FROM_TAG)
              | {profiles.TUTOR_TAG, profiles.MANA_ROCK_TAG,
                 profiles.RITUAL_TAG, profiles.MULTIPLE_MANA_TAG})

    missing = mapped - known - set(VERIFIED_UPSTREAM_ONLY)
    assert not missing, (
        f"mapped tags that are not in the sample catalogue: {sorted(missing)}. "
        "Either the slug is wrong, or the fixture needs the card that carries it."
    )


def test_the_upstream_only_list_does_not_outlive_its_reason(catalogue):
    """The other direction: a tag excused here that the sample now *does* have
    is an excuse nobody removed, and the excuses are what rot."""
    from cards.models import Tag

    known = set(Tag.objects.values_list("slug", flat=True))
    stale = set(VERIFIED_UPSTREAM_ONLY) & known
    assert not stale, (
        f"these are in the sample now and no longer need excusing: {sorted(stale)}"
    )


def test_nothing_is_mapped_that_the_mapping_cannot_spell():
    """A community tag slug is lowercase words joined by hyphens. Every typo
    this catches is a line that would otherwise sit there doing nothing."""
    import re

    mapped = (set(profiles.ROLE_FROM_TAG) | set(profiles.TUTOR_ZONE_FROM_TAG)
              | set(profiles.TUTOR_KIND_FROM_TAG)
              | set(profiles.TUTOR_UNEXPRESSIBLE_TAGS))
    bad = [slug for slug in mapped if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug)]
    assert not bad, bad


def test_every_tutor_kind_is_a_kind_the_engine_has():
    """`TutorSpec.kind` is compared against `Card.kind`, so a value the engine
    never assigns is a search that silently finds nothing."""
    from cards.models import DerivedProfile

    engine_kinds = {value for value, _ in DerivedProfile.Kind.choices}
    assert set(profiles.TUTOR_KIND_FROM_TAG.values()) <= engine_kinds
