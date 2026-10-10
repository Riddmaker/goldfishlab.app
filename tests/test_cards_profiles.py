"""Phase 1: deriving a simulator-ready profile from a Scryfall card.

The rule these tests defend is the honesty rule: a value the deriver cannot
establish stays **null** and sets `needs_review`. It is never guessed and never
defaulted. Most of the assertions below are therefore about what the code
refuses to claim.
"""


import pytest

from cards import profiles
from cards.models import DerivedProfile, OracleCard

pytestmark = pytest.mark.django_db



@pytest.fixture
def catalogue(catalogue):
    """The shared sample (conftest), as `{name: profile}`."""
    return {card.front_name: card.profile for card in OracleCard.objects.all()}


# --- mana costs, parsed rather than interpreted -----------------------------


@pytest.mark.parametrize(
    ("cost", "mv", "pips", "generic"),
    [
        ("{3}{B}{B}", 5, {"B": 2}, 3),
        ("{B}", 1, {"B": 1}, 0),
        ("{1}", 1, {}, 1),
        ("", 0, {}, 0),
        ("{2}{W}{U}", 4, {"W": 1, "U": 1}, 2),
    ],
)
def test_mana_costs_split_into_pips_and_generic(cost, mv, pips, generic):
    parsed = profiles.parse_mana_cost(cost)
    assert (parsed.mv, parsed.pips, parsed.generic) == (mv, pips, generic)


def test_colorless_pips_are_not_generic():
    """{C} cannot be paid with generic mana, so it may not be counted as such."""
    parsed = profiles.parse_mana_cost("{2}{C}")
    assert parsed.colorless == 1
    assert parsed.generic == 2
    assert parsed.mv == 3


def test_hybrid_and_phyrexian_count_for_every_colour_they_can_pay():
    hybrid = profiles.parse_mana_cost("{B/R}")
    assert hybrid.pips == {"B": 1, "R": 1}
    assert hybrid.hybrid == 1

    phyrexian = profiles.parse_mana_cost("{B/P}")
    assert phyrexian.phyrexian == 1
    assert phyrexian.mv == 1


def test_x_is_recorded_but_contributes_nothing():
    parsed = profiles.parse_mana_cost("{X}{B}")
    assert parsed.has_x
    assert parsed.mv == 1


# --- the honesty rule -------------------------------------------------------


def test_sol_ring_resolves_the_amount_scryfall_does_not_give(catalogue):
    """`produced_mana` says ['C']. The answer the simulator needs is 2.

    This single card is why the regex layer exists at all.
    """
    sol_ring = catalogue["Sol Ring"]
    assert sol_ring.produces_mana
    assert sol_ring.mana_colors == ["C"]
    assert sol_ring.mana_amount == 2


def test_cabal_coffers_is_a_rule_not_a_number(catalogue):
    """'Add {B} for each Swamp you control' has no fixed answer.

    The wrong behaviour here is not crashing - it is quietly storing 1. Since
    engine version 8 the sentence is read as the rule the engine plays it by
    (P19 R4), and the amount stays None.
    """
    coffers = catalogue["Cabal Coffers"]
    assert coffers.produces_mana
    assert coffers.mana_amount is None
    assert coffers.mana_rule == {"rule": "per_controlled", "subtype": "swamp",
                                 "activation": 2, "color": "B"}
    assert not coffers.needs_review


def test_urborg_and_crypt_ghast_are_read_as_rules(catalogue):
    assert catalogue["Urborg, Tomb of Yawgmoth"].mana_rule["rule"] == "type_adding"
    assert catalogue["Yavimaya, Cradle of Growth"].mana_rule["subtype"] == "forest"
    ghast = catalogue["Crypt Ghast"]
    assert ghast.mana_rule == {"rule": "double_subtype", "subtype": "swamp",
                               "activation": 0, "color": "B"}
    assert not ghast.needs_review


def test_every_unresolved_mana_source_is_flagged(catalogue):
    """No silent holes: unresolved amount implies needs_review - unless a rule
    says how the mana is made, an altar's sacrifice does (P19 R15) or a
    trigger does (P19 R16: Smothering Tithe)."""
    unresolved = DerivedProfile.objects.filter(produces_mana=True, mana_amount=None,
                                               mana_rule__isnull=True,
                                               sacrifice_mana__isnull=True, triggers=[])
    assert unresolved.exists()
    assert not unresolved.filter(needs_review=False).exists()


# --- enters tapped ----------------------------------------------------------


def test_enters_tapped_is_read_from_the_card_text(catalogue):
    assert catalogue["Bojuka Bog"].enters_tapped
    assert catalogue["Charcoal Diamond"].enters_tapped
    assert not catalogue["Swamp"].enters_tapped


def test_a_shock_lands_condition_is_read_rather_than_guessed(catalogue):
    """'As Blood Crypt enters, you may pay 2 life. If you don't, it enters tapped.'

    Until engine version 7 nothing could know whether the player paid, so the
    deriver flagged it and claimed neither answer. Now the condition itself is
    read (P19 R3) and the engine decides in the game, where the life is.
    """
    crypt = catalogue.get("Blood Crypt")
    if crypt is None:
        pytest.skip("Blood Crypt not in fixture")

    assert crypt.enters_tapped
    assert crypt.tapped_unless == {"kind": "pay_life", "life": 2}
    assert not any("tapped" in reason for reason in crypt.review_reasons)


def test_a_card_that_taps_other_permanents_does_not_enter_tapped_itself(catalogue):
    """The regex is anchored to the card's own name for exactly this reason."""
    for profile in DerivedProfile.objects.filter(enters_tapped=True):
        text = profile.oracle_card.oracle_text.lower()
        assert "enters" in text and "tapped" in text


# --- kinds and roles --------------------------------------------------------


def test_kind_follows_the_type_line_ladder(catalogue):
    assert catalogue["Swamp"].kind == DerivedProfile.Kind.LAND
    assert catalogue["Carrion Feeder"].kind == DerivedProfile.Kind.CREATURE
    assert catalogue["Dark Ritual"].kind == DerivedProfile.Kind.RITUAL
    assert catalogue["Sol Ring"].kind == DerivedProfile.Kind.ROCK
    assert catalogue["Necropotence"].kind == DerivedProfile.Kind.ENCHANTMENT


def test_game_changer_comes_from_scryfall_not_from_opinion(catalogue):
    """Bracket enforcement must rest on the published list, not a guess."""
    assert "gamechanger" in catalogue["Necropotence"].role_tags
    assert catalogue["Necropotence"].oracle_card.game_changer


def test_the_pronoun_check_separates_a_cost_from_a_payoff(catalogue):
    """'You lose 1 life' is what Phyrexian Arena charges you.

    'Each opponent loses 1 life' is what Gray Merchant does to them. Same verb,
    opposite meaning, and a simulator that conflates them scores a drain deck
    as a self-burn deck.
    """
    arena = catalogue["Phyrexian Arena"]
    assert arena.self_life_loss == 1
    assert arena.opponent_life_loss is None

    gary = catalogue["Gray Merchant of Asphodel"]
    assert "drain_payoff" in gary.role_tags


def test_sacrifice_outlets_mean_repeatable_ones(catalogue):
    """The plain `sacrifice-outlet` tag over-reports by six on the reference deck.

    Innocent Blood sacrifices a creature once. Carrion Feeder is an engine you
    can use every turn. Only the second is what a player means.
    """
    assert "sac_outlet" in catalogue["Carrion Feeder"].role_tags
    assert "sac_outlet" not in catalogue["Innocent Blood"].role_tags


# --- provenance -------------------------------------------------------------


def test_every_profile_records_where_its_claims_came_from(catalogue):
    """Phase 4 renders this; an empty source_map would render a confident blank."""
    for name in ("Sol Ring", "Bojuka Bog", "Phyrexian Arena"):
        source_map = catalogue[name].source_map
        assert source_map["mv"] == profiles.SCRYFALL
        assert set(source_map.values()) <= {profiles.SCRYFALL, profiles.TAGS, profiles.REGEX}


def test_regex_derived_claims_are_labelled_as_the_weakest_source(catalogue):
    assert catalogue["Bojuka Bog"].source_map["enters_tapped"] == profiles.REGEX
    assert catalogue["Sol Ring"].source_map["mana_amount"] == profiles.REGEX


def test_rebuild_is_idempotent(catalogue):
    before = DerivedProfile.objects.count()
    profiles.rebuild()
    assert DerivedProfile.objects.count() == before


# --- mana written out in words ----------------------------------------------
#
# "Add one mana of any color" is not a symbol clause, so the deriver used to
# read nothing at all and Arcane Signet - and Command Tower, which is in a very
# large share of Commander decks - tapped for nothing.


def _production(text, produced=("W", "U", "B", "R", "G"), name="Test Card", type_line=""):
    """Run the reader over one card's text, without a database row."""
    card = OracleCard(front_name=name, oracle_text=text, produced_mana=list(produced),
                      type_line=type_line)
    return profiles._mana_production(card)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{T}: Add one mana of any color in your commander's color identity.", 1),
        ("{T}: Add one mana of any color.", 1),
        ("{T}: Add two mana of any one color.", 2),
        ("{T}: Add {C}{C}.", 2),
    ],
)
def test_the_amount_is_read_whether_it_is_written_in_symbols_or_words(text, expected):
    reading = _production(text)
    assert (reading.produces_mana, reading.amount, reading.notes) == (True, expected, [])


def test_a_source_that_copies_an_opponents_lands_is_one_mana():
    """Exotic Orchard copies *an opponent's* lands. A goldfish has no opponent.

    Until engine version 9 it made nothing here. Since P19 R5 it is one mana
    of the deck's colours: three opponents nearly always have the lands. The
    adapter states that assumption on the card page; the reader only says how
    much. Mana that needs an opponent in any other way still makes nothing.
    """
    reading = _production(
        "{T}: Add one mana of any color that a land an opponent controls could produce."
    )
    assert (reading.produces_mana, reading.amount, reading.notes) == (True, 1, [])

    other = _production("{T}: Add one mana of any color an opponent chose this turn.")
    assert other.amount is None
    assert "opponent" in " ".join(other.notes)


def test_an_ability_the_card_grants_away_is_not_its_own():
    """Abundant Growth enchants a land. The *land* taps for the mana.

    `produced_mana` lists the colour either way, so without this the catalogue
    reads 385 cards as mana sources they are not.
    """
    reading = _production(
        'When this enchantment enters, draw a card.\n'
        'Enchanted land has "{T}: Add one mana of any color."'
    )
    assert (reading.produces_mana, reading.amount) == (True, None)
    assert "grants" in " ".join(reading.notes)


def test_a_card_with_both_keeps_its_own_ability():
    """Chromatic Lantern grants one and has one. The one it has is the answer."""
    reading = _production(
        'Lands you control have "{T}: Add one mana of any color."\n'
        "{T}: Add one mana of any color."
    )
    assert (reading.produces_mana, reading.amount, reading.notes) == (True, 1, [])


def test_the_signet_reads_as_one_mana_in_the_catalogue(catalogue):
    """End to end, against the real card row rather than a hand-written string."""
    assert catalogue["Arcane Signet"].mana_amount == 1
    assert catalogue["Arcane Signet"].review_reasons == []
    assert set(catalogue["Arcane Signet"].mana_colors) == set("WUBRG")


# --- mana abilities with a cost, a condition or a single use ---------------
#
# Trap 49. Until the 2026-09-25 review only the half of a mana ability after
# "Add" was read, so every cost was free and every source was permanent. The
# texts below are the cards' real Oracle text as Scryfall publishes it in 2026
# ("this artifact", not the card's name), copied rather than paraphrased,
# because a paraphrase is exactly how a reader passes its tests and fails on
# the real card.

REAL_CARDS = {
    # name: (type line, produced_mana, oracle text)
    "Dimir Signet": ("Artifact", "BU", "{1}, {T}: Add {U}{B}."),
    "Mana Vault": (
        "Artifact", "C",
        "This artifact doesn't untap during your untap step.\n"
        "At the beginning of your upkeep, you may pay {4}. If you do, untap this artifact.\n"
        "At the beginning of your draw step, if this artifact is tapped, it deals 1 damage "
        "to you.\n{T}: Add {C}{C}{C}.",
    ),
    "Grim Monolith": (
        "Artifact", "C",
        "This artifact doesn't untap during your untap step.\n{T}: Add {C}{C}{C}.\n"
        "{4}: Untap this artifact.",
    ),
    "Lotus Petal": ("Artifact", "WUBRG",
                    "{T}, Sacrifice this artifact: Add one mana of any color."),
    "Cabal Ritual": (
        "Instant", "B",
        "Add {B}{B}{B}.\nThreshold — Add {B}{B}{B}{B}{B} instead if there are seven or "
        "more cards in your graveyard.",
    ),
    "Sunken Ruins": ("Land", "BCU", "{T}: Add {C}.\n{U/B}, {T}: Add {U}{U}, {U}{B}, or {B}{B}."),
    "Shrine of the Forsaken Gods": (
        "Land", "C",
        "{T}: Add {C}.\n{T}: Add {C}{C}. Spend this mana only to cast colorless spells. "
        "Activate only if you control seven or more lands.",
    ),
    "Talisman of Dominance": (
        "Artifact", "BCU",
        "{T}: Add {C}.\n{T}: Add {U} or {B}. This artifact deals 1 damage to you.",
    ),
    "Sol Ring": ("Artifact", "C", "{T}: Add {C}{C}."),
    "Llanowar Elves": ("Creature — Elf Druid", "G", "{T}: Add {G}."),
    "Blood Pet": ("Creature — Thrull", "B", "Sacrifice this creature: Add {B}."),
    "Deathrite Shaman": (
        "Creature — Elf Shaman", "WUBRG",
        "{T}: Exile target land card from a graveyard. Add one mana of any color. "
        "(Activate only as an instant.)",
    ),
    "Rite of Flame": (
        "Sorcery", "R",
        "Add {R}{R}, then add {R} for each card named Rite of Flame in each graveyard.",
    ),
    "Prismatic Lens": ("Artifact", "WUBRGC", "{T}: Add {C}.\n{1}, {T}: Add one mana of any color."),
    "Watery Grave": (
        "Land — Island Swamp", "BU",
        "({T}: Add {U} or {B}.)\nAs this land enters, you may pay 2 life. If you don't, it "
        "enters tapped.",
    ),
    # A modal double-faced card is played as its land face; the sorcery face
    # must not turn the land's ability into "a triggered or static ability".
    "Agadeem's Awakening": (
        "Sorcery // Land", "B",
        "Return from your graveyard to the battlefield any number of target creature cards "
        "that each have a different mana value X or less.\n//\nAs this land enters, you may "
        "pay 3 life. If you don't, it enters tapped.\n{T}: Add {B}.",
    ),
    "Ishgard, the Holy See": (
        "Land — Town // Sorcery — Adventure", "W",
        "This land enters tapped.\n{T}: Add {W}.\n//\nReturn up to two target artifact and/or "
        "enchantment cards from your graveyard to your hand. (Then exile this card. You may "
        "play the land later from exile.)",
    ),
}


def _real(name):
    type_line, produced, text = REAL_CARDS[name]
    return _production(text, produced=tuple(produced), name=name, type_line=type_line)


def _real_card(name, mana_cost):
    type_line, produced, text = REAL_CARDS[name]
    return OracleCard(
        oracle_id=f"00000000-0000-0000-0000-0000000000{len(name) % 100:02d}",
        name=name, front_name=name, search_name=name.lower(), type_line=type_line,
        oracle_text=text, produced_mana=list(produced), mana_cost=mana_cost,
    )


@pytest.mark.parametrize(
    ("name", "amount", "produces", "activation", "untaps", "one_shot"),
    [
        # Both colours, for {1}: one mana net, and no colour to choose.
        ("Dimir Signet", 2, {"U": 1, "B": 1}, 1, True, False),
        ("Mana Vault", 3, {"C": 3}, 0, False, False),
        ("Grim Monolith", 3, {"C": 3}, 0, False, False),
        # One use: the engine's ritual.
        ("Lotus Petal", 1, None, 0, True, True),
        # The threshold replacement is not counted: three, not five.
        ("Cabal Ritual", 3, {"B": 3}, 0, True, False),
        # The filter is not modelled; what is left is the {T}: Add {C}.
        ("Sunken Ruins", 1, {"C": 1}, 0, True, False),
        # Its {C}{C} for colourless spells, with seven lands (P19 R17).
        ("Shrine of the Forsaken Gods", 2, {"C": 2}, 0, True, False),
        # {C} or {U}/{B}: a choice, which the deck's colours settle.
        ("Talisman of Dominance", 1, None, 0, True, False),
        ("Sol Ring", 2, {"C": 2}, 0, True, False),
        ("Llanowar Elves", 1, {"G": 1}, 0, True, False),
        ("Rite of Flame", 2, {"R": 2}, 0, True, False),
        ("Prismatic Lens", 1, {"C": 1}, 0, True, False),
        ("Watery Grave", 1, None, 0, True, False),
        ("Agadeem's Awakening", 1, {"B": 1}, 0, True, False),
        ("Ishgard, the Holy See", 1, {"W": 1}, 0, True, False),
    ],
)
def test_a_mana_ability_is_read_with_its_cost(name, amount, produces, activation, untaps,
                                               one_shot):
    reading = _real(name)
    assert reading.produces_mana
    assert (reading.amount, reading.produces, reading.activation, reading.untaps,
            reading.one_shot) == (amount, produces, activation, untaps, one_shot)


@pytest.mark.parametrize("name", ["Blood Pet", "Deathrite Shaman"])
def test_mana_the_engine_cannot_make_is_a_gap_and_not_a_guess(name):
    """A creature that sacrifices itself; a mana ability that needs a target."""
    reading = _real(name)
    assert reading.produces_mana
    assert reading.amount is None
    assert reading.notes


@pytest.mark.parametrize(
    "name",
    ["Cabal Ritual", "Rite of Flame"],
)
def test_an_ability_that_is_not_counted_says_so(name):
    """A reading that dropped something has to say what, or it is a guess."""
    assert _real(name).notes


@pytest.mark.parametrize("name", ["Sunken Ruins", "Prismatic Lens"])
def test_a_filter_and_a_converter_are_counted_since_r6(name):
    """Until engine version 10 these were notes; now the filter is read (P19 R6)."""
    reading = _real(name)
    assert reading.filter is not None and not reading.notes


def test_the_petal_becomes_a_ritual():
    """One use, then the graveyard - the engine's ritual, cast when it pays off."""
    profile = profiles.derive(_real_card("Lotus Petal", "{0}"), {"mana-rock"})
    assert profile.kind == DerivedProfile.Kind.RITUAL
    assert profile.mana_amount == 1


def test_the_signet_profile_carries_its_cost_and_both_colours():
    profile = profiles.derive(_real_card("Dimir Signet", "{2}"), {"mana-rock"})
    assert (profile.mana_amount, profile.mana_produces, profile.mana_activation) == (
        2, {"U": 1, "B": 1}, 1)
    assert profile.mana_untaps
    assert profile.review_reasons == []


def test_the_vault_profile_says_it_stays_tapped():
    profile = profiles.derive(_real_card("Mana Vault", "{1}"), {"mana-rock"})
    assert (profile.mana_amount, profile.mana_untaps) == (3, False)


# --- phase 10 N2: a land fetcher is not a "Tutor" -------------------------------

CULTIVATE = OracleCard(
    name="Cultivate", front_name="Cultivate", search_name="cultivate",
    type_line="Sorcery", mana_cost="{2}{G}",
    oracle_text="Search your library for up to two basic land cards, reveal those cards, "
                "put one onto the battlefield tapped and the other into your hand, then shuffle.",
)
BRANCHES = frozenset({"tutor-land", "tutor-to", "tutor-card", "tutor-creature"})
LAND_FETCHER_TAGS = {"ramp", "tutor", "tutor-land", "tutor-land-basic",
                     "tutor-to", "tutor-to-hand", "tutor-land-to-battlefield"}


def test_a_land_fetcher_is_ramp_and_not_a_tutor():
    profile = profiles.derive(CULTIVATE, LAND_FETCHER_TAGS, branches=BRANCHES)

    assert "tutor" not in profile.role_tags
    assert "ramp" in profile.role_tags


def test_a_card_that_finds_lands_and_creatures_stays_a_tutor():
    tags = LAND_FETCHER_TAGS | {"tutor-creature"}

    assert "tutor" in profiles.derive(CULTIVATE, tags, branches=BRANCHES).role_tags


def test_a_tutor_that_says_only_where_the_card_goes_stays_a_tutor():
    """Unmarked Grave wears only `tutor-to-graveyard`: it finds a nonlegendary
    card, not a land, and the tagger has no kind branch for that."""
    tags = {"tutor", "tutor-to", "tutor-to-graveyard"}

    assert "tutor" in profiles.derive(CULTIVATE, tags, branches=BRANCHES).role_tags


def test_the_branches_are_read_from_the_tag_tree():
    """Without `branches`, the tree in the database decides - Scryfall's tree
    grows, and a card under a branch nobody listed must not lose its role."""
    from uuid import uuid4

    from cards.models import Tag, TagEdge

    def tag(slug):
        return Tag.objects.create(id=uuid4(), slug=slug, label=slug)

    root = tag("tutor")
    for slug in ("tutor-land", "tutor-to", "tutor-brand-new-branch"):
        TagEdge.objects.create(parent=root, child=tag(slug))

    assert profiles.tutor_branches() == {"tutor-land", "tutor-to", "tutor-brand-new-branch"}
    assert "tutor" not in profiles.derive(CULTIVATE, LAND_FETCHER_TAGS).role_tags
    assert "tutor" in profiles.derive(
        CULTIVATE, LAND_FETCHER_TAGS | {"tutor-brand-new-branch"}).role_tags


# --- phase 12 J29: "Reanimate" brings back creatures ----------------------------

CRUCIBLE = OracleCard(
    name="Crucible of Worlds", front_name="Crucible of Worlds",
    search_name="crucible of worlds", type_line="Artifact", mana_cost="{3}",
    oracle_text="You may play lands from your graveyard.",
)
REVIVALS = frozenset({"reanimate-creature", "reanimate-land", "reanimate-self",
                      "reanimate-cast", "reanimate-nonland", "mass-reanimation",
                      "reanimate-permanent"})
REANIMATE = {"recursion", "reanimate"}


@pytest.mark.parametrize(
    ("branches", "reanimates"),
    [
        # Crucible of Worlds: lands only.
        ({"reanimate-land", "crucible-of-worlds"}, False),
        # Bloodghast: only itself.
        ({"reanimate-self"}, False),
        # Splendid Reclamation: all at once, but lands.
        ({"mass-reanimation", "reanimate-land"}, False),
        # Underworld Breach: casts nonland cards, puts nothing onto the battlefield.
        ({"reanimate-cast", "reanimate-nonland"}, False),
        # Animate Dead.
        ({"reanimate-creature"}, True),
        # Living Death: all at once, creatures.
        ({"mass-reanimation", "reanimate-creature"}, True),
        # Restoration Seminar: any nonland permanent, creatures included.
        ({"reanimate-nonland"}, True),
        # Muldrotha: casts from the graveyard, permanents included.
        ({"reanimate-cast", "reanimate-land", "reanimate-permanent"}, True),
        # Tagged reanimate and nothing more: the tagger is not doubted.
        (set(), True),
    ],
)
def test_reanimate_means_a_creature_can_come_back(branches, reanimates):
    roles = profiles.derive(CRUCIBLE, REANIMATE | branches, revivals=REVIVALS).role_tags

    assert ("reanimate" in roles) is reanimates
    assert "recursion" in roles


def test_the_reanimate_branches_are_read_from_the_tag_tree():
    from uuid import uuid4

    from cards.models import Tag, TagEdge

    def tag(slug):
        return Tag.objects.create(id=uuid4(), slug=slug, label=slug)

    root = tag("reanimate")
    for slug in ("reanimate-land", "reanimate-creature"):
        TagEdge.objects.create(parent=root, child=tag(slug))

    assert profiles.reanimate_branches() == {"reanimate-land", "reanimate-creature"}
    assert "reanimate" not in profiles.derive(CRUCIBLE, REANIMATE | {"reanimate-land"}).role_tags
    assert "reanimate" in profiles.derive(
        CRUCIBLE, REANIMATE | {"reanimate-land", "reanimate-creature"}).role_tags
