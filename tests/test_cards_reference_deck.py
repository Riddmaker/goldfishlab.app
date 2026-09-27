"""Phase 1 acceptance: the deriver reproduces the hand-annotated reference deck.

`simulation/cards.py` holds 70 cards annotated **by hand** during the deck
research - mana value, black pips, card kind, roles, enters-tapped. It is the
only ground truth in this repository that was not produced by the code being
tested, which makes it the one test that can catch the deriver being
confidently wrong.

Two kinds of assertion live here, and the difference matters:

* **Mechanical facts** - cost, pips, enters-tapped, basic-swamp-ness. These are
  read off the card. Any disagreement is a bug, so they are asserted exactly.
* **Judgement calls** - is Ashnod's Altar a "mana rock" or a "sacrifice
  engine"? A community tag DAG and one player's deck shorthand will differ, and
  neither is wrong. Those are pinned as a *known* divergence set, so the suite
  reports when the disagreement changes rather than pretending it does not
  exist. Patching them with a name list would be lying to the test.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cards import ingest, profiles
from cards.models import DerivedProfile, OracleCard
from cards.names import normalise
from simulation.cards import COMMANDER, SPELLS, SWAMP, UTILITY_LANDS

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)

HAND_ANNOTATED = [COMMANDER, SWAMP, *UTILITY_LANDS, *SPELLS]

# Judgement calls, not bugs. Each entry is (card, what the deriver says, what
# the player says) and each has a reason it can never be fixed by more regex.
KNOWN_KIND_DIVERGENCES = {
    # Tagged `mana-rock` upstream because it does add mana. A player files it
    # under sacrifice engines, because that is why it is in the deck.
    "Ashnod's Altar": ("rock", "artifact"),
    # Adds no mana at all - it reduces costs. A player still calls it ramp.
    "Jet Medallion": ("artifact", "rock"),
}


@pytest.fixture(scope="module")
def _loaded(django_db_setup, django_db_blocker):
    """Load the catalogue once for the whole module; these tests only read."""
    with django_db_blocker.unblock():
        ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz", updated_at=VERSION)
        ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz", updated_at=VERSION)
        profiles.rebuild()
        yield


@pytest.fixture(scope="module")
def deck(_loaded, django_db_blocker):
    """Every hand-annotated card paired with its derived profile."""
    with django_db_blocker.unblock():
        pairs = []
        for card in HAND_ANNOTATED:
            profile = _resolve(card.name)
            assert profile is not None, f"{card.name} did not resolve"
            pairs.append((card, profile))
        return pairs


def _resolve(name: str) -> DerivedProfile | None:
    """The import resolution ladder, in miniature: exact name, then normalised.

    `Lim-Dul the Necromancer` is really `Lim-Dul the Necromancer` with a
    circumflex, and no deck list ever writes it that way. Rung 5 is not a
    nicety.
    """
    card = OracleCard.objects.filter(front_name=name).first()
    if card is None:
        card = OracleCard.objects.filter(search_name=normalise(name)).first()
    return getattr(card, "profile", None) if card else None


def test_every_hand_annotated_card_resolves(deck):
    """70 of 70. One of them needs accent folding to get there."""
    assert len(deck) == len(HAND_ANNOTATED)


# --- mechanical facts: exact agreement required -----------------------------


def test_mana_values_agree(deck):
    wrong = [(c.name, p.mv, c.mv) for c, p in deck if p.mv != c.mv]
    assert not wrong


def test_black_pips_agree(deck):
    """The mono-black engine pays every pip with black mana; miscounting one
    silently changes what is castable on turn three."""
    wrong = [(c.name, p.black_pips, c.pips) for c, p in deck if p.black_pips != c.pips]
    assert not wrong


def test_generic_costs_agree(deck):
    wrong = [(c.name, p.generic, c.generic) for c, p in deck if p.generic != c.generic]
    assert not wrong


def test_basic_swamps_are_identified(deck):
    wrong = [c.name for c, p in deck if p.is_basic_swamp != c.is_swamp]
    assert not wrong


def test_enters_tapped_matches_exactly(deck):
    """The verified 2/2: Bojuka Bog and Charcoal Diamond, and nothing else."""
    derived = {c.name for c, p in deck if p.enters_tapped}
    hand = {c.name for c in HAND_ANNOTATED if c.enters_tapped}
    assert derived == hand == {"Bojuka Bog", "Charcoal Diamond"}


# --- roles: the three categories verified during the research ---------------


def test_recursive_creatures_match_exactly(deck):
    """The verified 4/4. `recursion-self` is the tag that means 'comes back'."""
    derived = {c.name for c, p in deck if "recursive" in p.role_tags}
    hand = {c.name for c in HAND_ANNOTATED if "recursive" in c.tags}
    assert derived == hand
    assert len(hand) == 4


def test_every_hand_marked_sac_outlet_is_found(deck):
    """The verified 5/5 recall.

    The deriver additionally finds Braids, Arisen Nightmare, which sacrifices a
    permanent every end step and genuinely is a repeatable outlet - the player
    left it out because it is not free and not instant-speed. Recall is the
    assertion that matters; the extra is named so it cannot grow unnoticed.
    """
    derived = {c.name for c, p in deck if "sac_outlet" in p.role_tags}
    hand = {c.name for c in HAND_ANNOTATED if "sac_outlet" in c.tags}

    assert hand <= derived, f"missed hand-annotated sac outlets: {hand - derived}"
    assert len(hand) == 5
    assert derived - hand == {"Braids, Arisen Nightmare"}


def test_rituals_match_exactly(deck):
    derived = {c.name for c, p in deck if "ritual" in p.role_tags}
    hand = {c.name for c in HAND_ANNOTATED if "ritual" in c.tags}
    assert derived == hand == {"Dark Ritual", "Cabal Ritual"}


# --- judgement calls: pinned, not patched -----------------------------------


def test_card_kinds_diverge_only_where_judgement_differs(deck):
    """Everything except two cards, and both of those are opinions.

    If this set ever changes, the deriver has either improved or regressed -
    and either way somebody should look, which is what pinning it achieves.
    """
    divergences = {c.name: (p.kind, c.kind) for c, p in deck if p.kind != c.kind}
    assert divergences == KNOWN_KIND_DIVERGENCES


def test_the_official_game_changer_list_overrides_the_hand_annotation(deck):
    """Here the deriver is right and the hand annotation is out of date.

    Demonic Tutor is on Wizards' published Game Changer list, which Scryfall
    exposes as a structured boolean. The research predates its addition. This
    is recorded rather than "fixed" because bracket legality must follow the
    published list, never a local opinion about it.
    """
    derived = {c.name for c, p in deck if "gamechanger" in p.role_tags}
    hand = {c.name for c in HAND_ANNOTATED if "gamechanger" in c.tags}

    assert hand <= derived
    assert derived - hand == {"Demonic Tutor"}
    assert OracleCard.objects.get(front_name="Demonic Tutor").game_changer


def test_community_tags_are_broader_than_one_players_shorthand(deck):
    """Documented, deliberate over-reporting - and bounded.

    `removal` is the clearest case: Scryfall tags Grave Pact and Tainted Aether
    as removal because they do remove creatures, while the player reserves the
    word for cards whose whole job is killing something. The deriver must stay
    a superset of the hand annotation (never miss one) without exploding.
    """
    for role in ("removal", "reanimate", "tutor", "draw"):
        derived = {c.name for c, p in deck if role in p.role_tags}
        hand = {c.name for c in HAND_ANNOTATED if role in c.tags}
        assert hand <= derived, f"{role}: deriver missed {hand - derived}"
        assert len(derived) <= 3 * max(len(hand), 5), f"{role}: over-reporting ran away"


def test_damage_is_not_silently_treated_as_life_loss(deck):
    """Syr Konrad deals damage; the player's shorthand calls it a drain payoff.

    The deriver keeps them apart, because teaching the regex layer to equate
    damage with life loss is the first step toward parsing rules text - which
    is explicitly Phase 4's job, done by a human who is recorded as having done
    it.
    """
    konrad = _resolve("Syr Konrad, the Grim")
    assert konrad.opponent_life_loss is None
    assert "drain_payoff" not in konrad.role_tags
    assert "deals 1 damage to each opponent" in konrad.oracle_card.oracle_text
