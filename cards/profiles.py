"""Turning a Scryfall card into something the simulator can reason about.

Read the honesty rule first, because it is the point of this module: **a value
this code cannot establish stays null and sets `needs_review`.** It is never
guessed, never defaulted to 1, never quietly dropped. A simulator that assumes
Cabal Coffers taps for one black mana produces a number that is wrong in a way
nobody can see. One that says "I could not read this card" produces a number
with a hole in it, which is honest and fixable.

Three layers, in descending confidence:

1. **Structured** (`scryfall`) - mana cost, type line, `game_changer`. Parsed,
   not interpreted.
2. **Tags** (`tags`) - the rolled-up Oracle tag DAG. Note that four of the role
   tags carry no direct taggings at all, so this layer only works on top of the
   rollup in `cards.ingest`.
3. **Regex** (`regex`) - a small, closed set of patterns. Deliberately small:
   every pattern is a promise to be right about English card text, and card
   text is adversarial. Anything outside the set is a `needs_review`.

Where this stops, on purpose: nothing here parses rules text into behaviour.
Triggered abilities, replacement effects and everything conditional belong to
the role editor in Phase 4, where a human makes the call and the application
records that a human made it.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from django.utils.translation import gettext_noop

from cards.models import DerivedProfile, OracleCard

# --- source labels, as they appear in DerivedProfile.source_map --------------
SCRYFALL = "scryfall"
TAGS = "tags"
REGEX = "regex"

# --- the tag vocabulary ------------------------------------------------------
# Left: the Oracle tag slug, verified to exist in the live tag DAG. Right: the
# role name the engine uses. Only mechanically grounded roles appear here.
# Judgement calls the tagger cannot make - "engine", "enabler", "lock" - are
# left to the Phase 4 role editor rather than faked with a keyword.
ROLE_FROM_TAG = {
    "ramp": "ramp",
    "ritual": "ritual",
    "removal": "removal",
    "sweeper": "wipe",
    # NOT plain "sacrifice-outlet": that tag covers any card that sacrifices
    # something, including one-shot edicts like Innocent Blood, and over-reports
    # by 6 cards on the reference deck. The repeatable variant is what a player
    # means by "a sac outlet" - an engine they can use again next turn.
    "repeatable-sacrifice-outlet": "sac_outlet",
    "recursion": "recursion",
    "recursion-self": "recursive",
    "reanimate": "reanimate",
    "tutor": "tutor",
    "draw": "draw",
    "evasion": "evasion",
    "cost-reducer": "cost_reducer",
    "drain-life": "drain_payoff",
    "theft": "steal",
    "discard": "discard",
    "mana-rock": "mana_rock",
    # Phase 9 E: two of the categories a Commander deck is sorted into
    # (Archidekt and Moxfield both offer them). No rule reads either - a
    # goldfish has nothing to counter and nothing to protect against - and
    # they exist for the draw statistics, which count how early a player sees
    # one. 561 and 1356 cards in the full catalogue.
    "counterspell": "counterspell",
    "protection": "protection",
    # The one role the *analysis* reads that no tag supplied, and therefore the
    # one milestone that could only ever fire for the hand-written fixture deck:
    # `simulation/analysis.py` counts `draw_engine` and the report prints "A
    # card-advantage engine in play", and until this line no card that came out
    # of the database could carry it. Measured against the reference deck, whose
    # author tagged eight cards by hand, `draw-engine` agrees on six and claims
    # nothing they did not - six of six precision, no over-reach.
    #
    # NOT `repeatable-card-advantage`, which finds all eight and adds Phyrexian
    # Reclamation, a graveyard engine that draws no cards. Recall is cheap here
    # and precision is not: a missed engine under-reports a deck, an invented
    # one puts a milestone on a report that never happened.
    "draw-engine": "draw_engine",
}

#: Which zone a tutor searches to. The engine models two of these; see
#: `DerivedProfile.tutor_to` for why the third is stored rather than dropped.
TUTOR_ZONE_FROM_TAG = {
    "tutor-to-hand": "hand",
    "tutor-to-graveyard": "graveyard",
    "tutor-to-battlefield": "battlefield",
}

#: What a tutor is allowed to find, in the engine's own card kinds. A tag with
#: no engine kind behind it - `tutors-by-name`, `tutor-mv`, `tutor-copy` - is
#: deliberately absent: the engine can restrict a search by kind and by nothing
#: else, and a restriction it cannot express must not be quietly widened to
#: "any card", which would make Entomb into Demonic Tutor.
TUTOR_KIND_FROM_TAG = {
    "tutor-creature": "creature",
    "tutor-land": "land",
    "tutor-artifact": "artifact",
    "tutor-enchantment": "enchantment",
    "tutor-instant": "instant",
    "tutor-sorcery": "sorcery",
    "tutor-planeswalker": "planeswalker",
}

#: Tag restrictions the engine cannot express at all. Present here so that a
#: card wearing one is reported rather than silently read as an unrestricted
#: tutor - the failure that would make every Gamble look like a Demonic Tutor.
TUTOR_UNEXPRESSIBLE_TAGS = frozenset({
    "tutors-by-name", "tutor-mv", "tutor-copy", "tutor-card-type",
})

#: The parent tag. A card wearing it is a tutor even when no child resolves,
#: which is what makes "tutors, but we could not read what it finds" sayable.
TUTOR_TAG = "tutor"

#: The two branches of the tutor tree that say nothing about finding a spell:
#: `tutor-land` finds lands, `tutor-to` says where a card goes. A card whose
#: only tutor branches are these two is a land fetcher - Cultivate, Farseek,
#: Evolving Wilds - and is not filed under the category "Tutor" (phase 10 N2:
#: 587 of the catalogue's 1,220 "tutors" were exactly that, and the category
#: read as if a deck could find its combo pieces when it could find a Forest).
#: Its tutor *behaviour* is unchanged: `tutor_to`/`tutor_kind` still let the
#: engine fetch the land. And it keeps "Ramp" when the tags say ramp.
LAND_TUTOR_TAG = "tutor-land"
TUTOR_DESTINATION_TAG = "tutor-to"


#: Tagger's `reanimate` is anything that comes back from a graveyard onto the
#: battlefield: lands (Crucible of Worlds), artifacts (Goblin Welder), a card
#: returning itself (Bloodghast) - 26 branches. Our category says "Puts creature
#: cards from a graveyard onto the battlefield", and a player who builds
#: around Reanimate means exactly that (phase 12 J29: Crucible of Worlds was
#: offered as a Reanimate card). So the role needs one of these branches, which
#: can bring a creature back. A list of the creature branches rather than of
#: the others, as at `draw-engine`: a branch nobody has read yet does not get
#: to put cards into the category. 1,102 cards carried the role, 683 do.
REANIMATE_TAG = "reanimate"
REANIMATE_CREATURE_TAGS = frozenset({
    "reanimate-creature", "reanimate-artifact-creature", "reanimate-permanent",
    "reanimate-from-opponent", "reanimate-from-any", "reanimate-face-down",
    "reanimate-nonland",
})
#: Casting from the graveyard, not putting onto the battlefield. Next to it,
#: `reanimate-nonland` describes what is cast: Underworld Breach, Six.
REANIMATE_CAST_TAG = "reanimate-cast"


def tag_children(parent: str) -> frozenset[str]:
    """The direct children of `parent` in the tag tree, read from the database."""
    from cards.models import TagEdge

    return frozenset(TagEdge.objects.filter(parent__slug=parent)
                     .values_list("child__slug", flat=True))


def tutor_branches() -> frozenset[str]:
    """The direct children of `tutor` in the tag tree, read from the database.

    Read rather than written down, because the tree is Scryfall's and grows: a
    list here would quietly file a card under a new branch as a land fetcher.
    """
    return tag_children(TUTOR_TAG)


def reanimate_branches() -> frozenset[str]:
    """The direct children of `reanimate`: whether a card wears any at all."""
    return tag_children(REANIMATE_TAG)


def _returns_no_creature(tags: set[str], branches: frozenset[str] | None) -> bool:
    """Whether the card's reanimation can bring back no creature.

    A card wearing `reanimate` and no branch at all keeps the role: the tagger
    said reanimate and nothing more, which is not a reason to doubt it (twelve
    cards, as at `TUTOR_TAG`).
    """
    if branches is None:
        branches = reanimate_branches()
    worn = tags & branches
    if not worn:
        return False
    creature = worn & REANIMATE_CREATURE_TAGS
    if REANIMATE_CAST_TAG in worn:
        creature -= {"reanimate-nonland"}
    return not creature


def _finds_only_lands(tags: set[str], branches: frozenset[str] | None) -> bool:
    """Whether every tutor branch the card wears is the land or the destination one.

    The tags are rolled up, so a card tagged `tutor-land-basic` also wears
    `tutor-land`, and one tagged `tutor-creature-elf` wears `tutor-creature`.
    The tree is only read for a card that wears `tutor-land` at all.
    """
    if LAND_TUTOR_TAG not in tags:
        return False
    if branches is None:
        branches = tutor_branches()
    return not tags & (branches - {LAND_TUTOR_TAG, TUTOR_DESTINATION_TAG})

# Artifacts wearing this tag are mana rocks rather than plain artifacts.
MANA_ROCK_TAG = "mana-rock"
RITUAL_TAG = "ritual"

#: 539 cards the community says net more than one mana. Never used as a value -
#: "more than one" is not a number - only as a **contradiction check** against
#: what the regexes read. A card the taggers call multi-mana and this module
#: reads as exactly one is a card this module probably read wrong, and saying so
#: is the only honest move available: the alternative is to believe the tag and
#: invent an amount, or to believe the regex and stay quiet about the
#: disagreement.
MULTIPLE_MANA_TAG = "adds-multiple-mana"

_SYMBOL = re.compile(r"\{([^}]+)\}")
_COLORS = frozenset("WUBRG")

# A card describes only itself as entering tapped when the subject is its own
# name, "This <type>", or the legacy "~". Without that anchor, "Creatures your
# opponents control enter tapped" would mark the card itself as entering tapped.
# `[.\n]\s*` rather than `[.\n] `: Oracle text separates abilities with a bare
# newline and no space, so requiring one missed every card whose sentence begins
# a line - "Flash\nFlying\nEbondeath enters tapped." among them.
_ENTERS_TAPPED = r"(?:^|[.\n]\s*)(?:{name}|This [\w ]+?|It|~) enters(?: the battlefield)? tapped"
_UNLESS = re.compile(r"enters(?: the battlefield)? tapped unless", re.IGNORECASE)
# The bare phrase, wherever it appears and whatever its subject. Used only to
# notice that a card is *about* entering tapped when the anchored rule missed.
_TAPPED_MENTION = re.compile(r"enters(?: the battlefield)? tapped", re.IGNORECASE)

# --- mana abilities, read clause by clause ------------------------------------
#
# A mana ability is `<cost>: ... Add <mana>`, and until the 2026-09-25 review
# only the `<mana>` half was read. That made every cost free and every source
# permanent: a Signet ("{1}, {T}: Add {U}{B}") tapped for two of one colour at no
# cost, Mana Vault and Grim Monolith made three every turn though they never
# untap, Lotus Petal sat on the battlefield as a rock, and a card with two
# differently sized abilities took the larger one - Cabal Ritual read as five.
# Trap 49. So each `Add` is now read with its cost, its colours and its
# conditions, and only what the engine can actually do counts.

#: The one capitalised word every mana ability's effect starts with. Case
#: matters: "instead add one mana of any color" (Gemstone Caverns) and "then add
#: {R} for each" (Rite of Flame) are riders on an ability, not abilities.
_ADD_WORD = re.compile(r"\bAdd\b")
_SYMBOL_RUN = re.compile(r"(?:\{[^}]+\})+")
#: What the pool can hold. A clause adding anything else is not a mana source
#: this engine models.
_MANA_SOURCES = frozenset("WUBRGC")
# Arcane Signet and Command Tower write their mana out in words rather than
# symbols: "Add one mana of any color in your commander's color identity". The
# *amount* is right there and is what this reader derives; which colour the
# card actually makes is a property of the deck it sits in, and `produced_mana`
# already lists the candidates.
_ANY_COLOR = re.compile(
    r"(a|an|one|two|three|four|five|\d+) mana of any (?:one )?(?:colou?r|type)", re.IGNORECASE
)
# Mana that depends on what an opponent has or does: "each player", "an
# opponent". A goldfish has no opponent, so none of it can be read - with one
# exception below.
_OPPONENT_SOURCE = re.compile(r"\ban opponent\b|\bopponents\b|\beach player\b", re.IGNORECASE)
# Since engine version 9 (P19 R5) Exotic Orchard and Fellwar Stone make the
# deck's colours: three opponents at a Commander table nearly always have the
# lands for them. An assumption, shown on the card page, and a player who
# knows their table better says what it taps for instead.
_OPPONENT_LANDS = re.compile(r"\s*that a land an opponent controls could produce\s*$",
                             re.IGNORECASE)
# Quoted text is an ability this card GRANTS to something else: a token it
# creates, a land it enchants, creatures it pumps. The grantee is the mana
# source; this card is not. Curly quotes included because Oracle text uses both.
_GRANTED = re.compile(r"[\"“][^\"”]*[\"”]")
#: P19 R12: mana made for another player, or by a target turned into a land
#: or a Treasure: removal, not a mana source.
_FOR_SOMEONE_ELSE = re.compile(
    r"Its controller creates (?:\w+ )?Treasure|Target [^.:]*becomes a Treasure"
    r"|Enchanted permanent is a colorless land", re.IGNORECASE)
#: A land with a basic land type taps for its colour by that type alone.
_BASIC_TYPE_LINE = re.compile(r"\bLand\b.*\b(?:Plains|Island|Swamp|Mountain|Forest)\b")
# Anything that makes an amount depend on the board state is unresolvable here.
_SCALING = re.compile(r"\bfor each\b|\bequal to\b|\btimes\b|\bX\b")
_SCALES_UP_FRONT = re.compile(r"(?:an amount of|X mana|mana equal to)", re.IGNORECASE)
#: "Threshold — Add ..." and "Vivid — {T}: ...": an ability word, not a cost.
_ABILITY_WORD = re.compile(r"^[^—–:]*[—–]\s*")
#: Mana somebody may only spend on some things, or only make at some moments.
#: "Activate only as an instant" is not here: a goldfish has no instant-speed
#: window that differs from its main phase.
_RESTRICTED = re.compile(r"Spend this mana only|Activate only (?:if|during)", re.IGNORECASE)
_LIFE_COST = re.compile(r"Pay (\d+|a|one|two|three) life", re.IGNORECASE)
#: One mana of input to a filter: "{W/B}", or a single colour "{G}" (P19 R6).
_ONE_MANA_INPUT = re.compile(r"\{([WUBRG]/[WUBRG]|[WUBRG])\}", re.IGNORECASE)
#: Mox Diamond: the card arrives only if a land card is discarded for it.
_ENTERS_BY_DISCARD = re.compile(
    r"If this [\w ]+? would enter, you may discard|As this [\w ]+? enters, you may discard",
    re.IGNORECASE,
)

_COST_REDUCTION = re.compile(
    r"spells? (?:you cast )?costs? \{(\d+)\} less to cast", re.IGNORECASE
)
_DRAW = re.compile(r"\bdraws? (a|one|two|three|four|five|\d+) cards?", re.IGNORECASE)
#: P19 R8: the discard a draw comes with - Faithless Looting, Frantic Search.
#: Read as "draw two" alone, such a card played better than it does.
_LOOT = re.compile(r"\bdraws? (a|one|two|three|four|five|\d+|X) cards?,? then discards? "
                   r"(a|one|two|three|four|five|\d+|X) cards?", re.IGNORECASE)
#: P19 R9: "Draw X cards" - as many as the X the spell was cast for. Not
#: "half X", which only creatures say and the engine does not read off them.
_DRAW_X = re.compile(r"\bdraws? X cards?\b", re.IGNORECASE)
#: An X that puts lands into play (Animist's Awakening, Open the Way, Genesis
#: Wave, Awaken the Woods): ramp the engine cannot play yet.
_X_LANDS = re.compile(r"(?:land|permanent) cards?[^.]*onto the battlefield|land creature tokens",
                      re.IGNORECASE)
#: Brainstorm: the cards go back on top of the library rather than away.
_PUT_BACK = re.compile(r"\bdraws? (a|one|two|three|four|five|\d+) cards?,? then put "
                       r"(a|one|two|three|four|five|\d+) cards? from your hand on top of "
                       r"your library", re.IGNORECASE)
#: Mystic Confluence: a modal spell whose draw mode may be chosen every time.
#: In a goldfish nothing else on it has a target, so all of them draw.
_REPEATED_MODES = re.compile(
    r"^Choose (two|three|four)\. You may choose the same mode more than once\.",
    re.IGNORECASE | re.MULTILINE)
_DRAW_MODE = re.compile(r"^• Draw (a|one|two|three) cards?\.$", re.IGNORECASE | re.MULTILINE)
#: A spree mode: "+ {B}{B} — Target player draws three cards". The mode's cost
#: is paid on top of the card's own when the engine plays that mode.
_SPREE_MODE = re.compile(r"^\+ ((?:\{[^}]+\})+) — .*$", re.MULTILINE)
# The pronoun check the deck research asked for: "you lose 1 life" is a cost,
# "each opponent loses 1 life" is a payoff. Same verb, opposite meaning.
_SELF_LOSS = re.compile(r"\byou lose (\d+|a|one|two|three) life", re.IGNORECASE)
_OPPONENT_LOSS = re.compile(
    r"\b(?:each opponent|target opponent|that player|each other player) "
    r"loses (\d+|a|one|two|three) life",
    re.IGNORECASE,
)

_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10}

#: "Search **your** library for up to three creature cards". The pronoun is the
#: whole check: "target player searches their library" is a card that makes an
#: opponent tutor, and in a goldfish it does nothing at all.
_SEARCH_YOUR_LIBRARY = re.compile(
    r"Search your library for (?:up to )?(a|an|one|two|three|four|five|\d+)\b",
    re.IGNORECASE,
)
#: Every search clause, counted. Two of them on one card - "search your library
#: for a creature card and a land card" - is a shape `TutorSpec` cannot hold,
#: and reading only the first would import half a card.
_SEARCH_CLAUSE = re.compile(r"Search your library for", re.IGNORECASE)
#: P19 R10: one creature or artifact straight onto the battlefield - Green
#: Sun's Zenith, Chord of Calling, Finale of Devastation (its graveyard is not
#: searched: a goldfish's graveyard rarely holds the card, and the library
#: alone can only under-read it), Whir of Invention, Natural Order.
_TO_BATTLEFIELD = re.compile(
    r"Search your library(?: and/or graveyard)? for an? "
    r"(?:(?P<color>white|blue|black|red|green) )?(?P<what>creature|artifact) card"
    r"(?: with mana value (?P<mv>X|\d+) or less)?"
    r"(?:,| and) put (?:it|that card) onto the battlefield",
    re.IGNORECASE)
#: P19 R15: Eldritch Evolution - "a creature card with mana value X or less,
#: where X is 2 plus the sacrificed creature's mana value. Put that card onto
#: the battlefield".
_PLUS_SACRIFICED = re.compile(
    r"Search your library for a creature card with mana value X or less, where X is "
    r"(?P<n>\w+) plus the sacrificed creature's mana value\. Put that card onto the battlefield",
    re.IGNORECASE)
_COLOR_LETTER = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
#: P19 R12: Vampiric Tutor, Mystical Tutor, Worldly Tutor: "Search your library
#: for a[n instant or sorcery] card, [reveal it, ]then shuffle and put that
#: card on top." Its zone is in the text, not in a tag.
_TO_TOP = re.compile(
    r"Search your library for an? (?:(?P<what>[a-z]+(?: or [a-z]+)?) )?card, "
    r"(?:reveal it, )?then shuffle and put (?:that|the) card on top\.",
    re.IGNORECASE)
_TOP_TYPES = frozenset({"artifact", "creature", "enchantment", "instant", "sorcery",
                        "planeswalker", "land"})

#: "As an additional cost to cast this spell, sacrifice a creature." The engine
#: pays mana and nothing else, so it casts Diabolic Intent for {2}{B} and gets a
#: free Demonic Tutor. 360 cards in the catalogue say this; deriving the tutors
#: is what made it matter, because before this phase most of them did nothing at
#: all and an unpaid cost on a card that does nothing costs nothing.
_ADDITIONAL_COST = re.compile(r"As an additional cost to cast", re.IGNORECASE)
#: The one additional cost the engine pays since version 11 (P19 R7):
#: "As an additional cost to cast this spell, discard a card."
_DISCARD_COST = re.compile(
    r"^As an additional cost to cast this spell, discard (a|one|two|three) cards?\.$",
    re.IGNORECASE | re.MULTILINE)
#: P19 R15: every other additional cost of the card's own, one sentence.
_OWN_ADDITIONAL_COST = re.compile(
    r"(?:^|\()As an additional cost to cast this spell, (?P<body>[^.]+)\.", re.MULTILINE)
#: The parts such a cost is made of, any of them joined by " or ".
_COST_PARTS = re.compile(
    r"sacrifice an? (?:(?P<filter>white|blue|black|red|green|legendary) )?"
    r"(?P<t1>artifact|creature|land|enchantment)"
    r"(?: or (?:an? )?(?P<t2>artifact|creature|land|enchantment)(?![\w ]*life))?"
    r"|sacrifice an? (?P<subtype>[A-Z][a-z]+)"
    r"|pay (?P<life>\d+|X) life"
    r"|discard (?P<discard>a|one|two) cards?"
    r"|pay (?P<mana>(?:\{[\dWUBRGC]\})+)"
    r"|exile an? (?P<exile>creature|artifact|land) card from your graveyard")
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
#: An additional cost on *other* spells (Defiler of Vigor): not the card's own.
_OTHER_SPELLS_COST = re.compile(
    r"As an additional cost to cast (?!this spell)[\w ]+ spells", re.IGNORECASE)
#: A sentence that pays off the optional cost: read only as not paid.
_PAID_PAYOFF = re.compile(r"\bthis way\b|additional cost was paid|\bwhen you do\b",
                          re.IGNORECASE)
#: Treasure a card makes as it resolves (P19 R7): a spell's own sentence
#: ("Draw two cards and create two Treasure tokens.") or a permanent's arrival
#: ("When this creature enters, create two Treasure tokens."). Not a trigger
#: on anything else, not "for each", not a tapped one.
_TREASURE_ON_ENTER = re.compile(
    r"^When (?:this [\w ]+?|~) enters, create (a|one|two|three|four) Treasure tokens?\.",
    re.IGNORECASE | re.MULTILINE)
_TREASURE_IN_SPELL = re.compile(r"\bcreate (a|one|two|three|four) Treasure tokens?\.$",
                                re.IGNORECASE)
#: Lander tokens (P19 R14), read like Treasure: "When this creature enters,
#: create a Lander token." or a spell's "Create a Lander token."
_LANDER_ON_ENTER = re.compile(
    r"^When (?:this [\w ]+?|~) enters, create (a|one|two|three) Lander tokens?\.",
    re.IGNORECASE | re.MULTILINE)
_LANDER_IN_SPELL = re.compile(r"\bcreate (a|one|two|three) Lander tokens?\.$", re.IGNORECASE)
_LANDER_SACRIFICED = re.compile(r"sacrifice (?:that|those) tokens?", re.IGNORECASE)
#: A Lander's reminder text: its search is the token's, not the card's.
_LANDER_REMINDER = re.compile(
    r"\((?:It's an artifact|A Lander token is an artifact) with \"\{2\}, \{T\}, "
    r"Sacrifice this token: Search your library for a basic land card, put it onto the "
    r"battlefield tapped, then shuffle\.\"\)")
_LANDER = re.compile(r"\bLander tokens?\b")
#: A sentence that makes it under a condition, or an ability's cost before it
#: (Magma Opus: "Discard this card: Create a Treasure token.").
_CONDITIONAL_SENTENCE = re.compile(r"^(?:For each|When|Whenever|If|At|Until)\b", re.IGNORECASE)
#: A Treasure's reminder text, which quotes its mana ability.
_TREASURE_REMINDER = re.compile(r"\((?:It's an artifact|They're artifacts) with \"\{T\}, "
                                r"Sacrifice this (?:token|artifact): Add one mana of any "
                                r"color\.\"\)")

#: Gamble's tax. Same first sentence as Demonic Tutor, and the difference is the
#: whole card - the engine would search up the best card in the library and then
#: not discard it.
_RANDOM_DISCARD = re.compile(r"discard a card at random", re.IGNORECASE)

#: The static, permanent form, as its own sentence and about *you*. Necropotence
#: and Recycle match; Fatigue ("Target player skips their next draw step") does
#: not, and neither does Ivory Gargoyle, which skips exactly one. The engine's
#: `skips_draw_step` is forever, so only forever may set it.
_SKIPS_DRAW_STEP = re.compile(r"(?:^|[.\n]\s*)Skip your draw step\.", re.IGNORECASE)


@dataclass
class ManaCost:
    """A parsed mana cost. Purely structural - no interpretation."""

    mv: int = 0
    pips: dict[str, int] = field(default_factory=dict)
    generic: int = 0
    colorless: int = 0
    has_x: bool = False
    hybrid: int = 0
    phyrexian: int = 0


def parse_mana_cost(cost: str) -> ManaCost:
    """Split `{3}{B}{B}` into its parts.

    Hybrid and Phyrexian symbols are counted toward every colour they can be
    paid with, and also counted separately - because "this pip is black" and
    "this pip may be paid with black" are different claims, and only the caller
    knows which one it needs.
    """
    parsed = ManaCost()
    for raw in _SYMBOL.findall(cost or ""):
        symbol = raw.upper()

        if symbol == "X":
            parsed.has_x = True
        elif symbol.isdigit():
            parsed.generic += int(symbol)
            parsed.mv += int(symbol)
        elif symbol == "C":
            parsed.colorless += 1
            parsed.mv += 1
        elif symbol in _COLORS:
            parsed.pips[symbol] = parsed.pips.get(symbol, 0) + 1
            parsed.mv += 1
        elif "/" in symbol:
            parts = symbol.split("/")
            if "P" in parts:
                parsed.phyrexian += 1
            else:
                parsed.hybrid += 1
            for part in parts:
                if part in _COLORS:
                    parsed.pips[part] = parsed.pips.get(part, 0) + 1
            parsed.mv += 1
        else:
            parsed.mv += 1

    return parsed


def derive_kind(card: OracleCard, tags: set[str]) -> str:
    """Map a type line onto one of the engine's nine card kinds.

    The ladder is ordered, and the order is the whole content: an artifact
    creature is a creature, a land that taps for mana is still a land.

    Two categories are *judgement*, not derivation, and this function is
    documented as getting them differently from a human:
    `Ashnod's Altar` is tagged `mana-rock` and lands here as a rock, though a
    player calls it a sacrifice engine; `Jet Medallion` taps for nothing and
    lands here as an artifact, though a player calls it ramp. Neither is a bug
    to be patched with a name list - it is what the Phase 4 role editor exists
    for.
    """
    type_line = card.type_line
    if "Land" in type_line:
        return DerivedProfile.Kind.LAND
    if "Creature" in type_line:
        return DerivedProfile.Kind.CREATURE
    if "Planeswalker" in type_line:
        return DerivedProfile.Kind.PLANESWALKER
    if "Instant" in type_line:
        return DerivedProfile.Kind.RITUAL if RITUAL_TAG in tags else DerivedProfile.Kind.INSTANT
    if "Sorcery" in type_line:
        return DerivedProfile.Kind.RITUAL if RITUAL_TAG in tags else DerivedProfile.Kind.SORCERY
    if "Artifact" in type_line:
        return DerivedProfile.Kind.ROCK if MANA_ROCK_TAG in tags else DerivedProfile.Kind.ARTIFACT
    if "Enchantment" in type_line:
        return DerivedProfile.Kind.ENCHANTMENT
    return DerivedProfile.Kind.ARTIFACT


# --- the conditions under which a land enters untapped (P19 R3) ---------------

_NUMBER_WORD = r"(?P<n>one|two|three|four|five|\d+)"
_BASIC_TYPE = r"(?:Plains|Island|Swamp|Mountain|Forest)"
_TYPE_LIST = (rf"(?:an? )?(?P<types>{_BASIC_TYPE}"
              rf"(?:,? (?:or )?(?:an? )?{_BASIC_TYPE})*)")
#: "This land enters tapped unless you control a Mountain or a Plains."
_UNLESS_CONTROL_TYPE = re.compile(
    rf"enters(?: the battlefield)? tapped unless you control {_TYPE_LIST}\.", re.IGNORECASE)
#: "... unless you control two or fewer other lands" (fast), "two or more other
#: lands" (slow), "two or more basic lands" (battle), "three or more other Islands".
_UNLESS_LANDS = re.compile(
    rf"enters(?: the battlefield)? tapped unless you control {_NUMBER_WORD} or "
    r"(?P<dir>more|fewer) (?P<other>other )?(?P<basic>basic )?"
    r"(?P<what>lands|Plains|Islands|Swamps|Mountains|Forests)\.",
    re.IGNORECASE)
#: Battlebond: "unless you have two or more opponents" - a Commander table has three.
_UNLESS_OPPONENTS = re.compile(
    rf"enters(?: the battlefield)? tapped unless you have {_NUMBER_WORD} or more opponents\.",
    re.IGNORECASE)
#: P19 R12: "unless you control a legendary creature" (Minas Tirith, Rivendell),
#: "a planeswalker" (Dedicated Commons), "a basic land" (Ba Sing Se).
_UNLESS_PERMANENT = re.compile(
    r"enters(?: the battlefield)? tapped unless you control an? "
    r"(?P<what>legendary creature|planeswalker|basic land)\.", re.IGNORECASE)
#: P19 R12: Starting Town.
_UNLESS_EARLY = re.compile(
    r"enters(?: the battlefield)? tapped unless it's your first, second, or third turn "
    r"of the game\.", re.IGNORECASE)
#: P19 R12: the Turbulent lands. A goldfish has no opponents' lands to count:
#: the engine assumes each of three opponents plays one a turn.
_UNLESS_OPPONENT_LANDS = re.compile(
    r"enters(?: the battlefield)? tapped unless your opponents control (?P<n>\w+) or "
    r"more lands\.", re.IGNORECASE)
#: P19 R12: "unless a player has 13 or less life" (Abandoned Campground). Only
#: your own life is known, so it enters tapped more often than at a real table.
_UNLESS_LOW_LIFE = re.compile(
    r"enters(?: the battlefield)? tapped unless a player has (?P<n>\d+) or less life\.",
    re.IGNORECASE)
#: Snarls and Shadowmoor's reveal lands.
_REVEAL = re.compile(
    rf"As (?:this land|~) enters(?: the battlefield)?, you may reveal {_TYPE_LIST} card from "
    r"your hand\. If you don't, (?:this land|it|~) enters(?: the battlefield)? tapped\.",
    re.IGNORECASE)
#: Shock lands.
_PAY_LIFE = re.compile(
    rf"As (?:this land|~) enters(?: the battlefield)?, you may pay {_NUMBER_WORD} life\. "
    r"If you don't, (?:this land|it|~) enters(?: the battlefield)? tapped\.",
    re.IGNORECASE)
_PLURAL_TYPES = {"plains": "plains", "islands": "island", "swamps": "swamp",
                 "mountains": "mountain", "forests": "forest"}

#: P19 R4: "Activate only if you control five or more lands" (Temple of the
#: False God), "an artifact" (Spire of Industry), "three or more artifacts"
#: (Mox Opal), "a Swamp" (Tainted Wood), "an Island or a Mountain" (Verges).
#: The board questions R3 already asks, plus how many artifacts.
_ACTIVATE_IF = re.compile(
    rf"Activate only if you control (?:{_NUMBER_WORD} or more (?P<what>lands|artifacts)"
    rf"|(?P<artifact>an artifact)|{_TYPE_LIST})\.",
    re.IGNORECASE)
_SPEND_ONLY = re.compile(r"Spend this mana only", re.IGNORECASE)

#: P19 R4: the three mana rules the engine has played since Phase 2, until now
#: only through a built-in annotation. "Each land is a Swamp in addition to
#: its other land types." (Urborg, Yavimaya)
_TYPE_ADDING = re.compile(
    rf"^Each land is an? (?P<type>{_BASIC_TYPE}) in addition to its other land types\.$",
    re.MULTILINE)
#: "Whenever you tap a Swamp for mana, add an additional {B}." (Crypt Ghast,
#: Nirkana Revenant) and "Whenever a Forest is tapped for mana, its controller
#: adds an additional {G}." (Vernal Bloom).
_DOUBLER = re.compile(
    rf"^Whenever (?:you tap an? (?P<type>{_BASIC_TYPE})|an? (?P<other>{_BASIC_TYPE}) is tapped)"
    r" for mana, (?:its controller )?adds? an additional \{(?P<color>[WUBRG])\}\.$",
    re.MULTILINE)
#: "{2}, {T}: Add {B} for each Swamp you control." - the whole of Cabal
#: Coffers. Only a land whose sole ability this is: the engine taps such a land
#: for nothing else.
_PER_CONTROLLED = re.compile(
    rf"\{{(?P<cost>\d+)\}}, \{{T\}}: Add \{{(?P<color>[WUBRG])\}} for each "
    rf"(?P<type>{_BASIC_TYPE}) you control\.")
#: "{T}: Add one mana of any type that a land you control could produce."
#: (Reflecting Pool, Incubation Druid; Horizon of Progress pays 1 life.)
_LANDS_COULD_PRODUCE = re.compile(
    r"^\{T\}(?:, Pay 1 life)?: Add one mana of any (?:type|colou?r) that a land you "
    r"control could produce\.", re.MULTILINE | re.IGNORECASE)
#: P19 R11: mana that counts the board. "{T}: Add {G} for each creature you
#: control." (Gaea's Cradle, Circle of Dreams Druid), "... for each Elf you
#: control / on the battlefield" (Elvish Archdruid, Priest of Titania), "{3},
#: {T}: Add {B} for each basic Swamp you control." (Cabal Stronghold), "{2},
#: {T}: Add {B} for each black creature card in your graveyard." (Crypt of
#: Agadeem). On a goldfish's table every Elf is yours.
_COUNTS = re.compile(
    r"^(?:\{(?P<cost>\d+)\}, )?\{T\}: Add \{(?P<color>[WUBRG])\} for each "
    r"(?:(?P<creature>creature) you control|(?P<elf>Elf) (?:you control|on the battlefield)"
    r"|(?P<defender>creature you control with defender)"
    rf"|basic (?P<basic>{_BASIC_TYPE}) you control"
    r"|(?P<dead>white|blue|black|red|green) creature card in your graveyard)\.$",
    re.MULTILINE)
#: Nykthos: "{2}, {T}: Choose a color. Add an amount of mana of that color equal
#: to your devotion to that color."
_DEVOTION = re.compile(
    r"^\{(?P<cost>\d+)\}, \{T\}: Choose a color\. Add an amount of mana of that color "
    r"equal to your devotion to that color\.", re.MULTILINE)
#: Tron: "{T}: Add {C}. If you control an Urza's Power-Plant and an Urza's
#: Tower, add {C}{C} instead."
_TRON = re.compile(
    r"^\{T\}: Add \{C\}\. If you control an Urza's (?P<a>[\w-]+) and an Urza's "
    r"(?P<b>[\w-]+), add (?P<more>(?:\{C\}){2,3}) instead\.$", re.MULTILINE)
#: The cards the Urza land types are printed on.
_URZA_NAMES = {"Mine": "Urza's Mine", "Power-Plant": "Urza's Power Plant",
               "Tower": "Urza's Tower"}
#: P19 R13: mana on top of what a source makes. "Whenever enchanted land is
#: tapped for mana, its controller adds an additional {G}." (Wild Growth,
#: Overgrowth, Fertile Ground, Utopia Sprawl's chosen colour, Market
#: Festival's two in any combination.)
_ENCHANTED_EXTRA = re.compile(
    r"^Whenever enchanted (?P<what>land|Forest) is tapped for mana, its controller adds an "
    r"additional (?:(?P<symbols>(?:\{[WUBRG]\})+)|(?P<n>one|two) mana (?:of any color|in any "
    r"combination of colors)|(?P<chosen>one mana of the chosen color))\.$", re.MULTILINE)
#: "Enchant land" / "Enchant Forest": what the Aura needs to be cast.
_ENCHANT = re.compile(r"^Enchant (?P<what>land|Forest)$", re.MULTILINE)
#: "Whenever you tap a land for mana, add one mana of any type that land
#: produced." (Mirari's Wake, Vorinclex, Zendikar Resurgent; "a player taps"
#: for Mana Flare and Heartbeat of Spring - in a goldfish, only you do;
#: "a nonland permanent" for Kinnan.)
_SAME_TYPE_EXTRA = re.compile(
    r"^Whenever (?:you tap|a player taps) an? (?P<what>land|nonland permanent|permanent) for "
    r"mana, (?:that player )?adds? one mana of any type that (?:land|permanent) produced\.",
    re.MULTILINE)
#: "Whenever you tap a creature for mana, add an additional {G}." (Badgermole
#: Cub, Leyline of Abundance) and "Whenever you tap a permanent for {C}, add an
#: additional {C}." (Forsaken Monument).
_SOURCE_EXTRA = re.compile(
    r"^Whenever you tap a (?P<what>creature for mana|permanent for \{C\}), add an additional "
    r"\{(?P<color>[WUBRGC])\}\.$", re.MULTILINE)
#: Caged Sun and Gauntlet of Power: a bonus in the colour chosen as it enters.
_CHOSEN_EXTRA = re.compile(
    r"^Whenever (?:a land's ability causes you to add one or more mana of the chosen color, add"
    r"|(?P<basic>a basic land is tapped for mana of the chosen color, its controller adds)) "
    r"an additional one mana of that color\.$", re.MULTILINE)
#: "If you tap a permanent for mana, it produces twice as much of that mana
#: instead." (Mana Reflection; Nyxbloom Ancient three times.)
_MULTIPLY = re.compile(
    r"^If you tap a permanent for mana, it produces (?P<times>twice|three times) as much of "
    r"that mana instead\.$", re.MULTILINE)
#: "Creatures you control have "{T}: Add one mana of any color."" (Cryptolith
#: Rite, Enduring Vitality, Elven Chorus; Citanul Hierophants' {G}) and
#: "Enchanted land has "{T}: Add one mana of any color."" (Abundant Growth).
_GRANT = re.compile(
    r"^(?P<who>Creatures you control have|Enchanted land has) \"\{T\}: Add "
    r"(?:(?P<any>one mana of any color)|\{(?P<color>[WUBRG])\})\.\"$", re.MULTILINE)
#: "{T}: For each color among permanents you control, add one mana of that
#: color." (Bloom Tender - after its "Vivid -" - and Faeburrow Elder.)
_COLORS_AMONG = re.compile(
    r"^(?:[^\n:]* — )?\{T\}: For each color among permanents you control, add one mana of "
    r"that color\.$", re.MULTILINE)
#: "{T}: Add X mana of any one color, where X is the number of enchantments
#: you control." (Sanctum Weaver) and "{T}: Add X mana in any combination of
#: colors, where X is the number of creatures you control with defender."
#: (Axebane Guardian).
_COUNTS_X = re.compile(
    r"^\{T\}: Add X mana (?:(?P<one>of any one color)|in any combination of colors), where X "
    r"is the number of (?:(?P<enchantments>enchantments you control)"
    r"|creatures you control with defender)\.$", re.MULTILINE)
#: Rituals that count the board as they resolve: "Add {R} for each creature
#: you control." (Battle Hymn) and "Until end of turn, whenever a player taps
#: an Island for mana, that player adds an additional {U}." (High Tide).
_RITUAL_CREATURES = re.compile(
    r"^Add \{(?P<color>[WUBRG])\} for each creature you control\.$", re.MULTILINE)
_RITUAL_TAPPED = re.compile(
    rf"^Until end of turn, whenever (?:a player taps|you tap) an? (?P<type>{_BASIC_TYPE}) for "
    r"mana, (?:that player )?adds? an additional \{(?P<color>[WUBRG])\}\.$", re.MULTILINE)
_WORD_TIMES = {"twice": 2, "three times": 3}
#: What a read mana rule explains, and so is no longer a reason to review.
_RULE_EXPLAINS = frozenset({
    "produces mana, but no readable 'Add' clause",
    "only grants a mana ability to another permanent",
    "mana amount scales with the board",
    "makes mana only as part of another effect",
    "a conditional replacement ('instead') is not counted",
})
#: Somebody else's search: "Its controller may search their library" (Path to
#: Exile, Ghost Quarter), "Each player searches their library" (Field of Ruin,
#: whose target is an opponent's land a goldfish does not have).
#: "Target player searches their library" stays a gap: the target may be you.
_THEIR_LIBRARY = re.compile(
    r"\b(?:Its controller|Each player) (?:may )?search(?:es)? their library", re.IGNORECASE)


def _land_types(listed: str) -> list[str]:
    return sorted({word.lower() for word in re.findall(
        r"Plains|Island|Swamp|Mountain|Forest", listed, re.IGNORECASE)})


def _activation_condition(text: str) -> dict | None:
    """"Activate only if you control ..." as a condition the game checks, or None.

    The same shapes as `_tapped_unless`, so the engine asks one question of
    its board for both (P19 R4). Anything else - a creature with power 4 or
    greater, a legendary creature - stays a gap.
    """
    found = _ACTIVATE_IF.search(text)
    if found is None:
        return None
    if found.group("artifact"):
        return {"kind": "artifacts", "count": 1}
    if found.group("what"):
        count = _word_number(found.group("n"))
        if count is None:
            return None
        return {"kind": found.group("what").lower(), "count": count}
    return {"kind": "control_type", "types": _land_types(found.group("types"))}


@dataclass
class AdditionalCost:
    """A card's own additional cost, read (P19 R15).

    `ways` are the alternatives, any one of which pays it - stored as
    `DerivedProfile.additional_cost`. `optional` is a "you may" cost, never
    paid; `reason` says why the cost could not be read.
    """

    ways: list[dict] | None = None
    optional: bool = False
    reason: str = ""


def _additional_cost(card: OracleCard) -> AdditionalCost:
    """Read "As an additional cost to cast this spell, ..." (P19 R15).

    "Discard a card" alone is `discard_cost`, read since engine version 11.
    """
    text = card.oracle_text or ""
    found = _OWN_ADDITIONAL_COST.search(text)
    if found is None:
        return AdditionalCost()
    body = found.group("body").strip()
    if body.lower().startswith("you may "):
        # Not paying is always allowed, and the card is played as it is
        # without it - unless something else on it counts on the payment.
        rest = text[:found.start()] + text[found.end():]
        if _PAID_PAYOFF.search(rest) and _COST_REDUCTION.search(rest):
            return AdditionalCost(reason=gettext_noop(
                "has an additional casting cost the engine does not pay"))
        return AdditionalCost(optional=True)
    ways, position = [], 0
    while True:
        part = _COST_PARTS.match(body, position)
        if part is None:
            return AdditionalCost(reason=gettext_noop(
                "has an additional casting cost the engine does not pay"))
        ways.append(_cost_way(part))
        position = part.end()
        if position == len(body):
            return AdditionalCost(ways=ways)
        if not body.startswith(" or ", position):
            return AdditionalCost(reason=gettext_noop(
                "has an additional casting cost the engine does not pay"))
        position += len(" or ")


def _cost_way(part: re.Match) -> dict:
    """One alternative of an additional cost, as the profile stores it."""
    way = {"sacrifice": [], "filter": "", "life": 0, "life_x": False, "discard": 0,
           "mana": "", "exile_from_graveyard": ""}
    if part.group("t1"):
        way["sacrifice"] = sorted({part.group("t1"), part.group("t2") or part.group("t1")})
        wanted = part.group("filter") or ""
        way["filter"] = _COLOR_WORDS.get(wanted, wanted)
    elif part.group("subtype"):
        way["sacrifice"], way["filter"] = ["creature"], part.group("subtype").lower()
    elif part.group("life"):
        if part.group("life") == "X":
            way["life_x"] = True
        else:
            way["life"] = int(part.group("life"))
    elif part.group("discard"):
        way["discard"] = _word_number(part.group("discard")) or 1
    elif part.group("mana"):
        way["mana"] = part.group("mana")
    else:
        way["exile_from_graveyard"] = part.group("exile")
    return way


def _treasures(card: OracleCard, kind: str) -> int:
    """How many Treasure tokens the card makes as it resolves, or 0 (P19 R7)."""
    return _made_tokens(card, kind, _TREASURE_ON_ENTER, _TREASURE_IN_SPELL)


def _landers(card: OracleCard, kind: str) -> int:
    """How many Lander tokens the card makes as it resolves, or 0 (P19 R14).

    Not one sacrificed again at a later end step (Kav Landseeker).
    """
    if _LANDER_SACRIFICED.search(card.oracle_text or ""):
        return 0
    return _made_tokens(card, kind, _LANDER_ON_ENTER, _LANDER_IN_SPELL)


def _made_tokens(card: OracleCard, kind: str, on_enter: re.Pattern,
                 in_spell: re.Pattern) -> int:
    """Tokens a spell's own sentence or a permanent's arrival makes."""
    text = _GRANTED.sub("", _TREASURE_REMINDER.sub("", _LANDER_REMINDER.sub(
        "", card.oracle_text or "")))
    if kind not in (DerivedProfile.Kind.INSTANT, DerivedProfile.Kind.SORCERY):
        found = on_enter.search(text)
        return (_word_number(found.group(1)) or 0) if found else 0
    for sentence in re.split(r"(?<=\.)\s+|\n", text):
        sentence = sentence.strip()
        if _CONDITIONAL_SENTENCE.match(sentence) or ":" in sentence:
            continue
        if (found := in_spell.search(sentence)) is not None:
            return _word_number(found.group(1)) or 0
    return 0


def _mana_rule(card: OracleCard, kind: str) -> dict | None:
    """A mana rule the engine plays, read off the text, or None (P19 R4).

    Urborg and Yavimaya make every land a Swamp or Forest, Crypt Ghast makes
    each Swamp tap for one more {B}, Cabal Coffers makes {B} for each Swamp.
    Until engine version 8 only a built-in annotation could say so.
    """
    text = (card.oracle_text or "").strip()
    if (found := _TYPE_ADDING.search(text)) is not None:
        return {"rule": "type_adding", "subtype": found.group("type").lower(),
                "activation": 0, "color": ""}
    if (found := _DOUBLER.search(text)) is not None:
        subtype = (found.group("type") or found.group("other")).lower()
        return {"rule": "double_subtype", "subtype": subtype, "activation": 0,
                "color": found.group("color")}
    if _LANDS_COULD_PRODUCE.search(text):
        return {"rule": "lands_could_produce", "subtype": "", "activation": 0, "color": ""}
    if (found := _extra_rule(text, kind)) is not None:
        return found
    if kind == DerivedProfile.Kind.LAND and (found := _PER_CONTROLLED.fullmatch(text)):
        return {"rule": "per_controlled", "subtype": found.group("type").lower(),
                "activation": int(found.group("cost")), "color": found.group("color")}
    if "//" in (card.name or ""):
        # Itlimoc counts creatures on its back face, which this engine never
        # reaches: the front is an enchantment that makes no mana.
        return None
    return _counting_rule(text)


def _extra_rule(text: str, kind: str) -> dict | None:
    """Mana on top, a granted ability or a counting ritual (P19 R13), or None."""
    if kind == DerivedProfile.Kind.RITUAL:
        if (found := _RITUAL_CREATURES.search(text)) is not None:
            return {"rule": "ritual", "subtype": "creature", "color": found.group("color"),
                    "activation": 0}
        if (found := _RITUAL_TAPPED.search(text)) is not None:
            return {"rule": "ritual", "subtype": "tapped:" + found.group("type").lower(),
                    "color": found.group("color"), "activation": 0}
        return None
    if (found := _ENCHANTED_EXTRA.search(text)) is not None:
        enchant = _ENCHANT.search(text)
        if enchant is None or enchant.group("what") != found.group("what"):
            return None
        what = found.group("what").lower()
        rule = {"rule": "extra", "activation": 0, "color": "", "enchants": what,
                "subtype": "enchanted" if what == "land" else "enchanted:" + what}
        if found.group("symbols"):
            symbols = re.findall(r"[WUBRG]", found.group("symbols"))
            rule["produces"] = {symbols[0]: len(symbols)} if len(set(symbols)) == 1 else None
            if rule["produces"] is None:
                return None
        elif found.group("n"):
            rule["produces"] = {"WUBRG": _word_number(found.group("n"))}
        else:
            rule["color"] = "chosen"
        return rule
    if (found := _SAME_TYPE_EXTRA.search(text)) is not None:
        scope = {"land": "land", "nonland permanent": "nonland",
                 "permanent": "permanent"}[found.group("what")]
        return {"rule": "extra", "subtype": scope, "activation": 0, "color": ""}
    if (found := _SOURCE_EXTRA.search(text)) is not None:
        scope = "creature" if found.group("what").startswith("creature") else "colorless"
        return {"rule": "extra", "subtype": scope, "activation": 0, "color": "",
                "produces": {found.group("color"): 1}}
    if (found := _CHOSEN_EXTRA.search(text)) is not None:
        return {"rule": "extra", "subtype": "chosen_basic" if found.group("basic") else
                "chosen_land", "activation": 0, "color": "chosen"}
    if (found := _MULTIPLY.search(text)) is not None:
        return {"rule": "multiply", "subtype": "permanent", "activation": 0, "color": "",
                "times": _WORD_TIMES[found.group("times")]}
    if (found := _GRANT.search(text)) is not None:
        if found.group("who").startswith("Enchanted"):
            if not found.group("any") or (enchant := _ENCHANT.search(text)) is None \
                    or enchant.group("what") != "land":
                return None
            return {"rule": "grant", "subtype": "enchanted", "activation": 0, "color": "",
                    "enchants": "land", "produces": {"WUBRG": 1}}
        return {"rule": "grant", "subtype": "creature", "activation": 0, "color": "",
                "produces": {"WUBRG" if found.group("any") else found.group("color"): 1}}
    return None


def _counting_rule(text: str) -> dict | None:
    """Mana that counts the board, as a ``counts`` rule (P19 R11), or None."""
    if _COLORS_AMONG.search(text):
        # P19 R13: Bloom Tender. The colours are the board's to say.
        return {"rule": "counts", "subtype": "colors_among", "color": "", "activation": 0}
    if (found := _COUNTS_X.search(text)) is not None:
        if found.group("enchantments"):
            return {"rule": "counts", "subtype": "enchantment", "activation": 0,
                    "color": "" if found.group("one") else "WUBRG"}
        return {"rule": "counts", "subtype": "creature:defender", "activation": 0,
                "color": "" if found.group("one") else "WUBRG"}
    if (found := _COUNTS.search(text)) is not None:
        if found.group("creature"):
            subtype = "creature"
        elif found.group("defender"):
            subtype = "creature:defender"
        elif found.group("elf"):
            subtype = "elf"
        elif found.group("basic"):
            subtype = "basic:" + found.group("basic").lower()
        else:
            subtype = "graveyard:" + {"white": "W", "blue": "U", "black": "B", "red": "R",
                                      "green": "G"}[found.group("dead").lower()]
        return {"rule": "counts", "subtype": subtype, "color": found.group("color"),
                "activation": int(found.group("cost") or 0)}
    if (found := _DEVOTION.search(text)) is not None:
        return {"rule": "counts", "subtype": "devotion", "color": "",
                "activation": int(found.group("cost"))}
    if (found := _TRON.search(text)) is not None:
        names = [_URZA_NAMES.get(found.group(key)) for key in ("a", "b")]
        if None in names:
            return None
        return {"rule": "counts", "subtype": "names:" + "|".join(names), "color": "C",
                "activation": 0, "produces": {"C": found.group("more").count("{C}")}}
    return None


def _tapped_unless(text: str) -> dict | None:
    """The condition under which a land that would enter tapped does not, or None.

    One of five shapes the engine can check against its own board (P19 R3):
    a land type you control, how many lands you control, how many opponents
    you have, a card you can reveal from your hand, life you can pay.
    """
    if (found := _UNLESS_CONTROL_TYPE.search(text)) is not None:
        return {"kind": "control_type", "types": _land_types(found.group("types"))}
    if (found := _UNLESS_LANDS.search(text)) is not None:
        count = _word_number(found.group("n"))
        what = found.group("what").lower()
        return {"kind": "lands", "count": count, "at_least": found.group("dir").lower() == "more",
                "other": bool(found.group("other")), "basic": bool(found.group("basic")),
                "type": "" if what == "lands" else _PLURAL_TYPES[what]}
    if (found := _UNLESS_OPPONENTS.search(text)) is not None:
        return {"kind": "opponents", "count": _word_number(found.group("n"))}
    if (found := _REVEAL.search(text)) is not None:
        return {"kind": "reveal", "types": _land_types(found.group("types"))}
    if (found := _PAY_LIFE.search(text)) is not None:
        return {"kind": "pay_life", "life": _word_number(found.group("n"))}
    if (found := _UNLESS_PERMANENT.search(text)) is not None:
        what = found.group("what").lower()
        if what == "basic land":
            return {"kind": "lands", "count": 1, "basic": True}
        return {"kind": "permanent", "types": [what.split()[-1]],
                "legendary": what.startswith("legendary")}
    if _UNLESS_EARLY.search(text):
        return {"kind": "turn", "count": 3}
    if (found := _UNLESS_OPPONENT_LANDS.search(text)) is not None:
        return {"kind": "opponent_lands", "count": _word_number(found.group("n"))}
    if (found := _UNLESS_LOW_LIFE.search(text)) is not None:
        return {"kind": "life_at_most", "count": int(found.group("n"))}
    return None


def _enters_tapped(card: OracleCard) -> tuple[bool, str, dict | None]:
    """Does the card itself enter tapped, and is that claim conditional?

    Three outcomes, and the third is the one that matters:

    * the card says plainly that it enters tapped -> True
    * it says so with a rider ("unless you control two or fewer other lands")
      -> True, and flagged, because the engine models no board state
    * the words appear but no anchored rule matched -> **False, and flagged.**

    The third case is Blood Crypt: "As Blood Crypt enters, you may pay 2 life.
    If you don't, it enters tapped." No pattern here can tell whether the
    player paid. Returning a flat False without a flag is how a shock land
    quietly becomes a strictly better land than it is.
    """
    text = card.oracle_text or ""
    pattern = _ENTERS_TAPPED.format(name=re.escape(card.front_name))
    condition = _tapped_unless(text)
    if condition is not None and all(value is not None for value in condition.values()):
        # Since engine version 7 the condition itself is read (P19 R3): the
        # land enters tapped unless the engine finds it met.
        return True, "", condition

    if re.search(pattern, text, re.IGNORECASE):
        if _UNLESS.search(text):
            return True, gettext_noop("enters tapped only conditionally ('unless')"), None
        return True, "", None

    if _TAPPED_MENTION.search(text):
        return False, gettext_noop(
            "text mentions entering tapped, but the condition was not readable"), None

    return False, "", None


@dataclass
class ManaClause:
    """One `Add` in a card's text, with the cost and the conditions around it.

    `problem` is the sentence saying why this clause is *not* a mana source the
    engine can use - an empty string means it is one. `note` is a rider the
    clause survives with but that is not counted (Rite of Flame's growth).
    """

    is_ability: bool
    amount: int | None = None
    #: Exactly what one activation adds, when the text names every symbol:
    #: `{U}{B}` is `{"U": 1, "B": 1}`. None when it is a choice ("{U} or {B}",
    #: "one mana of any color") - which colour a choice makes is the deck's.
    produces: dict | None = None
    #: Generic mana in the cost, beside `{T}`: the `{1}` on a Signet.
    activation: int = 0
    sacrifices_self: bool = False
    #: P19 R15: "Sacrifice a creature" - another permanent goes as the cost
    #: (Ashnod's Altar): {"sacrifice": types, "filter": ...}. None: no such.
    sacrifices: dict | None = None
    #: P19 R15: {T} is part of the cost.
    taps: bool = False
    #: P19 R15: "Exile this card from your hand" is the cost (Elvish Spirit
    #: Guide): mana once, out of the hand.
    from_hand: bool = False
    #: "Activate only if you control ..." read as a condition (P19 R4).
    condition: dict | None = None
    #: A filter's coloured input, "WB" for {W/B} (P19 R6). Empty: none.
    pays_with: str = ""
    #: The colours a choice among symbols offers: "WB" for "Add {W}{W},
    #: {W}{B}, or {B}{B}". Empty when the clause names no symbols.
    offers: str = ""
    problem: str = ""
    note: str = ""

    @property
    def net(self) -> int:
        return (self.amount or 0) - self.activation - (1 if self.pays_with else 0)

    @property
    def converts(self) -> bool:
        """A filter or a converter: one mana in, the same or one more out, other colours."""
        return bool(self.pays_with) or (self.activation == 1 and self.amount == 1)


@dataclass
class ManaReading:
    """What a card's mana abilities come to, as far as the engine can use them."""

    produces_mana: bool = False
    #: Mana one use makes, before any activation cost is paid. None means it
    #: could not be read, and is a gap rather than a guess.
    amount: int | None = None
    produces: dict | None = None
    activation: int = 0
    #: False for "This artifact doesn't untap during your untap step."
    untaps: bool = True
    #: P19 R15: "Exile this card from your hand: Add {G}" - Elvish Spirit
    #: Guide makes its mana from the hand, once, for nothing.
    from_hand: bool = False
    #: P19 R15: an ability that sacrifices another permanent for mana:
    #: {"sacrifice", "filter", "amount", "produces", "taps"}. None: none.
    sacrifice: dict | None = None
    #: The mana comes from sacrificing the card itself (Lotus Petal) - once,
    #: which the engine models as a ritual cast when it unlocks something.
    one_shot: bool = False
    #: The ability can be used only under a condition: {"if": ..., "otherwise":
    #: what the card taps for without it, or None} (P19 R4).
    condition: dict | None = None
    #: A filter or converter beside the plain ability (P19 R6): {"pays_with":
    #: "WB" or "" (any mana), "amount", "offers": colours or "" (any)}.
    filter: dict | None = None
    notes: list[str] = field(default_factory=list)


def _self_reference(card: OracleCard) -> str:
    """How a card names itself: "this artifact", the legacy "~", or its name."""
    return rf"(?:this [\w ]+?|~|{re.escape(card.front_name or '')})"


#: P19 R15: the cost of an altar - "Sacrifice a creature", "Sacrifice an
#: artifact", "Sacrifice a Goblin". Not "X Goats", not "five Treasures".
_SACRIFICE_ANOTHER = re.compile(
    r"Sacrifice (?:an?|another) (?:(?P<filter>white|blue|black|red|green|legendary) )?"
    r"(?:(?P<t1>artifact|creature)|(?P<subtype>(?!Food|Treasure|Clue|Blood|Desert|land)"
    r"[A-Z][a-z]+))", re.IGNORECASE)


def _read_cost(clause: ManaClause, cost_text: str, card: OracleCard) -> None:
    """Split `{1}, {T}, Sacrifice this artifact` into what the engine can pay."""
    for part in (piece.strip() for piece in cost_text.split(",")):
        if part == "{T}":
            clause.taps = True
            continue
        if not part:
            continue
        if re.fullmatch(r"Exile this card from your hand", part, re.IGNORECASE):
            clause.from_hand = True
            continue
        if (found := _SACRIFICE_ANOTHER.fullmatch(part)) is not None:
            # Ashnod's Altar, Phyrexian Tower (P19 R15).
            if found.group("t1"):
                wanted = found.group("filter") or ""
                clause.sacrifices = {"sacrifice": [found.group("t1").lower()],
                                     "filter": _COLOR_WORDS.get(wanted, wanted)}
            else:
                clause.sacrifices = {"sacrifice": ["creature"],
                                     "filter": found.group("subtype").lower()}
            continue
        if re.fullmatch(r"(?:\{\d+\})+", part):
            clause.activation += sum(int(n) for n in _SYMBOL.findall(part))
            continue
        # Life is paid at no mana cost and a goldfish has life to spare; the
        # engine does not deduct it, which is the same simplification as
        # Talisman's point of damage.
        if _LIFE_COST.fullmatch(part):
            continue
        if re.fullmatch(rf"Sacrifice {_self_reference(card)}", part, re.IGNORECASE):
            clause.sacrifices_self = True
            continue
        if (found := _ONE_MANA_INPUT.fullmatch(part)) is not None:
            # {W/B} or {G}: one mana of these colours goes in (P19 R6).
            clause.pays_with = "".join(sorted(set(found.group(1).upper()) - {"/"},
                                              key="WUBRG".index))
            continue
        if _SYMBOL_RUN.fullmatch(part):
            clause.problem = gettext_noop(
                "a mana ability with a coloured cost (a filter) is not modelled")
        else:
            clause.problem = f"a mana ability that costs '{part}' is not modelled"[:120]
        return


def _read_produced(clause: ManaClause, after: str) -> None:
    """What follows `Add`: symbols, "N mana of any color", or something that scales."""
    after = after.lstrip()
    run = _SYMBOL_RUN.match(after)
    if run:
        symbols = [symbol.upper() for symbol in _SYMBOL.findall(run.group())]
        rest = after[run.end():]
        rest_sentence = rest.split(".", 1)[0]
        if any(symbol not in _MANA_SOURCES for symbol in symbols):
            clause.problem = gettext_noop("makes a kind of mana the engine does not model")
            return
        if re.match(r"\s*,\s*then\b", rest):
            # Rite of Flame: "Add {R}{R}, then add {R} for each ...". The base
            # is fixed; only the rider grows, and it is not counted.
            clause.note = gettext_noop(
                "the part of its mana that grows with the board is not counted")
        elif _SCALING.search(rest_sentence):
            clause.problem = gettext_noop("mana amount scales with the board")
            return
        if "instead" in rest_sentence:
            # Cabal Ritual's threshold: a replacement that needs a full
            # graveyard, which the early turns being measured do not have.
            clause.problem = gettext_noop("a conditional replacement ('instead') is not counted")
            return
        choice = bool(re.match(r"\s*(?:,\s*)?(?:or\s+)?\{", rest))
        clause.amount = len(symbols)
        clause.produces = None if choice else dict(Counter(symbols))
        if choice:
            offered = {symbol.upper() for symbol in _SYMBOL.findall(after.split(".", 1)[0])}
            clause.offers = "".join(color for color in "WUBRG" if color in offered)
        return

    words = _ANY_COLOR.match(after)
    if words:
        token = words.group(1).lower()
        clause.amount = int(token) if token.isdigit() else _WORD_NUMBERS[token]
        tail = after[words.end():].split(".", 1)[0]
        if _OPPONENT_LANDS.match(tail):
            return
        if _OPPONENT_SOURCE.search(tail):
            clause.problem = gettext_noop("needs an opponent's lands; a goldfish has none")
        elif _SCALING.search(tail):
            clause.problem = gettext_noop("mana amount scales with the board")
        return

    if _SCALES_UP_FRONT.match(after):
        clause.problem = gettext_noop("mana amount scales with the board")
    else:
        clause.problem = gettext_noop("an 'Add' clause whose amount could not be read")


def _mana_clauses(card: OracleCard, *, is_spell: bool) -> list[ManaClause]:
    """Every `Add` in the card's own text - quoted, granted abilities removed."""
    found = []
    own = _GRANTED.sub("", card.oracle_text or "")
    for raw in own.split("\n"):
        line = raw.strip()
        # Reminder text: a Watery Grave's mana ability is "({T}: Add {U} or {B}.)".
        if line.startswith("(") and line.endswith(")"):
            line = line[1:-1]
        for match in _ADD_WORD.finditer(line):
            before, after = line[: match.start()], line[match.end():]
            if ":" in before:
                cost_text, _, prefix = before.rpartition(":")
                clause = ManaClause(is_ability=True)
                _read_cost(clause, _ABILITY_WORD.sub("", cost_text), card)
            else:
                prefix = _ABILITY_WORD.sub("", before)
                clause = ManaClause(is_ability=False)
                if not is_spell:
                    clause.problem = gettext_noop(
                        "makes mana only from a triggered or static ability")
            if prefix.strip() and not clause.problem:
                # Deathrite Shaman: "{T}: Exile target land card from a
                # graveyard. Add ..." - the mana needs a target first.
                clause.problem = gettext_noop("makes mana only as part of another effect")
            if not clause.problem:
                _read_produced(clause, after)
            if not clause.problem and _RESTRICTED.search(after):
                condition = _activation_condition(after)
                if (condition is not None and clause.is_ability
                        and not clause.sacrifices_self and not _SPEND_ONLY.search(after)):
                    # Since engine version 8 the game checks it (P19 R4).
                    clause.condition = condition
                else:
                    clause.problem = gettext_noop(
                        "its mana is restricted to certain spells or moments")
            found.append(clause)
    return found


def _strongest(clauses: list[ManaClause]) -> tuple[ManaClause, dict | None, list[ManaClause]]:
    """The clause a player would tap for, what it makes, and the ones tied with it.

    The most net mana wins, a free one a tie. Two free abilities making
    different colours (a Talisman's {C} or {U}/{B}, a Verge's {R} or {U}) are
    a choice between them, which the deck's colours settle: `produces` None.
    """
    best = max(clause.net for clause in clauses)
    top = [clause for clause in clauses if clause.net == best]
    top = [clause for clause in top if not clause.activation] or top
    chosen = top[0]
    same = all(clause.produces == chosen.produces for clause in top)
    return chosen, (chosen.produces if same else None), top


def _mana_production(card: OracleCard) -> ManaReading:
    """How much mana the card adds, at what cost, and how often.

    This is the function that exists because `produced_mana` does not answer
    the question. It reads `Add {C}{C}` and returns 2. It reads `Add {B} for
    each Swamp you control` and returns None, loudly. It reads `{1}, {T}: Add
    {U}{B}` as two mana, one of each colour, for {1} - which is what a Signet
    is and what the engine now plays.

    Only the card's *own* abilities count. Quoted text is an ability the card
    hands to something else - a Treasure token, an enchanted land - and that
    something else is the mana source, not this card. `produced_mana` lists the
    colour either way, which is why 385 cards in the catalogue read as mana
    sources they are not until the quotes come out.

    When a card has several abilities, the one a player would tap it for is the
    one that nets the most; a free one wins a tie. Abilities that cannot be
    used at all are named in `notes` - which become review reasons - so a
    Shrine of the Forsaken Gods reads as its `{T}: Add {C}` and says why the
    other ability does not count.
    """
    type_line = card.type_line or ""
    # A modal double-faced "Sorcery // Land" is played as its land, and the
    # land face's `{T}: Add` is the mana it makes. Read as a spell, all 41 of
    # them tapped for nothing.
    is_spell = ("Instant" in type_line or "Sorcery" in type_line) and "Land" not in type_line
    reading = ManaReading()
    clauses = _mana_clauses(card, is_spell=is_spell)
    reading.produces_mana = bool(clauses or card.produced_mana)
    if not reading.produces_mana:
        return reading

    text = card.oracle_text or ""
    reading.untaps = not re.search(
        rf"(?:^|[.\n]\s*){_self_reference(card)} doesn't untap during your untap step",
        text,
        re.IGNORECASE,
    )

    usable = [clause for clause in clauses if not clause.problem]
    notes = {clause.problem for clause in clauses if clause.problem}
    notes |= {clause.note for clause in usable if clause.note}
    if _ENTERS_BY_DISCARD.search(text):
        notes.add(gettext_noop(
            "enters only by discarding a land card, which the engine does not do"))

    if not clauses and (_FOR_SOMEONE_ELSE.search(text) or _BASIC_TYPE_LINE.search(type_line)):
        # P19 R12: the mana is somebody else's - An Offer You Can't Refuse
        # gives its Treasures to the spell's controller, Imprisoned in the Moon
        # and Vraska turn a target into a land or a Treasure - or it is a land
        # type's own: Dryad Arbor's "{T}: Add {G}" is reminder text for being
        # a Forest, which the engine plays as one.
        reading.produces_mana = False
        return reading

    if not clauses:
        # Scryfall says it makes mana but no `Add` clause of its own was found:
        # either every one is inside quotes, or it is a replacement effect or a
        # face this text does not cover.
        reading.notes = [
            gettext_noop("only grants a mana ability to another permanent")
            if _ADD_WORD.search(text)
            else gettext_noop("produces mana, but no readable 'Add' clause")
        ]
        return reading

    if is_spell:
        spells = [clause for clause in usable if not clause.is_ability]
        if spells:
            reading.amount, reading.produces = spells[0].amount, spells[0].produces
        reading.notes = sorted(notes)
        return reading

    altars = [clause for clause in usable if clause.sacrifices is not None]
    usable = [clause for clause in usable if clause.sacrifices is None]
    if altars:
        # P19 R15: mana for another permanent, used when it unlocks a spell.
        first = altars[0]
        if len(altars) > 1 or first.amount is None or first.activation or first.condition:
            notes.add(gettext_noop("an ability that sacrifices for mana is not modelled"))
        else:
            reading.sacrifice = {**first.sacrifices, "amount": first.amount,
                                 "produces": first.produces, "taps": first.taps}
    from_hand = [clause for clause in usable if clause.from_hand]
    usable = [clause for clause in usable if not clause.from_hand]
    if from_hand:
        first = from_hand[0]
        if usable or len(from_hand) > 1 or first.amount is None or first.activation \
                or first.taps or first.condition:
            notes.add(gettext_noop("a mana ability that exiles the card from hand is not modelled"))
        else:
            # P19 R15: a ritual that costs nothing - see `derive`.
            reading.amount, reading.produces, reading.from_hand = (
                first.amount, first.produces, True)
            reading.notes = sorted(notes)
            return reading
    tapping = [clause for clause in usable if not clause.sacrifices_self]
    one_shots = [clause for clause in usable if clause.sacrifices_self]
    # Filters and converters ride beside the plain ability (P19 R6): Fetid
    # Heath taps for {C}, or filters a white or black mana into two.
    converting = [clause for clause in tapping if clause.converts and not clause.condition]
    tapping = [clause for clause in tapping if clause not in converting]
    worth_it = [clause for clause in tapping if clause.net > 0]
    if converting and worth_it and not any(clause.condition for clause in worth_it):
        kinds = {(clause.pays_with, clause.amount, clause.offers,
                  repr(clause.produces)) for clause in converting}
        if len(kinds) == 1:
            first = converting[0]
            reading.filter = {"pays_with": first.pays_with, "amount": first.amount,
                              "offers": first.offers, "produces": first.produces}
        else:
            notes.add(gettext_noop("an ability that only converts mana is not modelled"))
    elif converting:
        notes.add(gettext_noop("an ability that only converts mana is not modelled"))

    if worth_it:
        chosen, produces, top = _strongest(worth_it)
        conditions = {repr(sorted(clause.condition.items())) for clause in top
                      if clause.condition}
        plain = [clause for clause in worth_it if not clause.condition]
        if len(conditions) > 1:
            # Two abilities under two different conditions: one ability with
            # a condition is what the engine holds.
            notes.add(gettext_noop("its mana is restricted to certain spells or moments"))
            chosen, produces, top = _strongest(plain) if plain else (None, None, [])
            conditions = set()
        if chosen is not None:
            reading.amount, reading.activation = chosen.amount, chosen.activation
            reading.produces = produces
        if conditions:
            condition = next(clause.condition for clause in top if clause.condition)
            otherwise = None
            if plain:
                fallback, fallback_produces, _ = _strongest(plain)
                otherwise = {"amount": fallback.amount, "produces": fallback_produces,
                             "activation": fallback.activation}
            if otherwise is not None and otherwise["produces"] is None:
                # Without the condition it would still make a choice of
                # colours, and which ones `produced_mana` cannot say apart
                # from the conditional ability's. Kept a gap.
                notes.add(gettext_noop("its mana is restricted to certain spells or moments"))
                reading.amount = None
            else:
                reading.condition = {"if": condition, "otherwise": otherwise}
        if any(clause.net <= 0 for clause in tapping):
            notes.add(gettext_noop("an ability that only converts mana is not modelled"))
    elif one_shots and "Creature" not in type_line:
        chosen = one_shots[0]
        reading.amount, reading.produces = chosen.amount, chosen.produces
        reading.one_shot = True
    else:
        if one_shots:
            notes.add(gettext_noop(
                "sacrifices itself for mana, which is not modelled on a creature"))
        if tapping:
            notes.add(gettext_noop("its mana ability costs as much as it makes"))

    reading.notes = sorted(notes)
    return reading


# Card text is adversarial and these columns are small integers. Anything above
# this is either an Un-card or a misread, and both deserve the same treatment:
# store the ceiling, and say so in review_reasons.
_NUMBER_CEILING = 32_767


@dataclass
class Tutor:
    """What a search-your-library card does, as far as anything can establish.

    Three fields and a reason, because the interesting answer is usually
    partial: the tags know Buried Alive searches to the graveyard for creatures
    and the text knows it finds three of them, and neither knows both.
    """

    zone: str = ""
    count: int | None = None
    kind: str = ""
    reason: str = ""
    #: P19 R10: {"color", "max_mv"} of a search onto the battlefield, or None.
    filter: dict | None = None


def _tutor(card: OracleCard, tags: set[str]) -> Tutor:
    """Read a tutor off the tags and the printed text, or report why not.

    The division of labour is the point. A community tag is a *category* - it
    can say "this searches to the graveyard" and can never say "for three
    cards". The printed text is the opposite: `Search your library for up to
    three creature cards` carries the number in plain sight and says nothing
    about what the tagger thought the card was for.

    So the zone comes from the tag, the amount comes from the text, and the
    card is a gap unless both arrive. The tempting shortcut - default the
    count to 1 - is wrong on every Buried Alive and right often enough that
    nobody would notice.
    """
    if TUTOR_TAG not in tags:
        return Tutor()

    zones = {TUTOR_ZONE_FROM_TAG[slug] for slug in tags if slug in TUTOR_ZONE_FROM_TAG}
    kinds = {TUTOR_KIND_FROM_TAG[slug] for slug in tags if slug in TUTOR_KIND_FROM_TAG}
    text = card.oracle_text or ""

    if len(_SEARCH_CLAUSE.findall(text)) > 1:
        return Tutor(reason=gettext_noop("searches the library more than once; not modelled"))
    found = _TO_BATTLEFIELD.search(text)
    line_start = text.rfind("\n", 0, found.start()) + 1 if found else 0
    if found is not None and ":" not in text[line_start:found.start()] and (
            (found.group("mv") or "").upper() != "X" or "{X}" in (card.mana_cost or "")):
        # Read whole off the text, so the tags' "battlefield" and "a mana value
        # limit" (`tutor-mv`) are both answered, not unexpressible (P19 R10).
        # Not an ability's search (Tezzeret's "-X:"): the engine plays what a
        # card does as it is cast or enters, and X is the X of its own cost.
        color = (found.group("color") or "").lower()
        limit = found.group("mv")
        return Tutor(zone="battlefield", count=1, kind=found.group("what").lower(),
                     filter={"color": _COLOR_LETTER.get(color, ""),
                             "max_mv": None if limit is None else
                             ("X" if limit.upper() == "X" else int(limit))})
    found = _PLUS_SACRIFICED.search(text)
    if found is not None and (plus := _word_number(found.group("n"))) is not None:
        return Tutor(zone="battlefield", count=1, kind="creature",
                     filter={"color": "", "max_mv": None, "plus_sacrificed": plus})
    found = _TO_TOP.search(text)
    line_start = text.rfind("\n", 0, found.start()) + 1 if found else 0
    if found is not None and ":" not in text[line_start:found.start()] \
            and not text[line_start:].startswith("+"):
        # On top of the library, to be drawn next turn (P19 R12). Not Sterling
        # Grove's "{1}, Sacrifice: Search ...", an ability the engine does not
        # use, and not Insatiable Avarice's spree mode, whose {2} nobody pays.
        types = (found.group("what") or "").lower().split(" or ") if found.group("what") else []
        if all(kind in _TOP_TYPES for kind in types):
            return Tutor(zone="top", count=1, filter={"types": sorted(types)})
    if not _SEARCH_YOUR_LIBRARY.search(text) and _THEIR_LIBRARY.search(text):
        # Path to Exile: the search is the target's controller's, and the
        # target is an opponent's creature or land a goldfish does not have.
        # Not a tutor this deck plays, so nothing is missing (P19 R4).
        return Tutor()
    if not _SEARCH_YOUR_LIBRARY.search(text):
        # Either the search is somebody else's ("target player searches their
        # library") or it is worded in a way this pattern does not cover. The
        # tag says the card is a tutor, so the honest answer is to say we could
        # not read it rather than to drop it silently.
        return Tutor(reason=gettext_noop("tutors, but no readable 'search your library' clause"))

    count = _first_number(_SEARCH_YOUR_LIBRARY, text)
    if not zones:
        return Tutor(count=count,
                     reason=gettext_noop("tutors, but to which zone could not be read"))
    if len(zones) > 1:
        return Tutor(count=count,
                     reason=gettext_noop("tutors to more than one zone; not modelled"))

    zone = zones.pop()
    if zone == "battlefield":
        return Tutor(zone=zone, count=count,
                     reason=gettext_noop(
                         "tutors onto the battlefield, which the engine cannot do"))
    if tags & TUTOR_UNEXPRESSIBLE_TAGS:
        return Tutor(zone=zone, count=count,
                     reason=gettext_noop("tutors for something the engine cannot describe"))
    if len(kinds) > 1:
        return Tutor(zone=zone, count=count,
                     reason=gettext_noop("tutors for more than one card kind; not modelled"))

    if _RANDOM_DISCARD.search(text):
        return Tutor(zone=zone, count=count,
                     reason=gettext_noop(
                         "searches, then discards at random; the discard is not modelled"))

    return Tutor(zone=zone, count=count, kind=kinds.pop() if kinds else "")


# --- searching for lands (P19 R2) --------------------------------------------

#: One whole land search, as ramp spells, fetch lands and Wood Elves print it:
#: "<lead>Search your library for <n> <what> card(s), [reveal ...,] put <put>,
#: then shuffle[. Then if you control four or more lands, untap that land]".
#: Everything outside these words stays a gap - a search for a Gate, a snow
#: land, "a Forest card and a Plains card" - because each is one more promise
#: about English the reader would have to keep.
_LAND_SEARCH = re.compile(
    r"(?P<lead>[^.\n]*?)Search your library for (?:up to )?(?P<n>a|an|one|two|three|\d+) "
    r"(?P<what>[A-Za-z ,]+?) cards?(?P<share> that share a land type)?, "
    r"(?:reveal (?:those cards|them|it), )?(?:and )?put (?P<put>[^.]+?)"
    r"(?:, then shuffle|\. Shuffle)"
    r"(?:\. Then if you control (?P<untap>\w+) or more lands, untap that land)?",
    re.IGNORECASE,
)
_LAND_TYPES = {"plains", "island", "swamp", "mountain", "forest"}
_ALL_TO_BATTLEFIELD = re.compile(
    r"^(?:it|that card|them|those cards) onto the battlefield(?P<tapped> tapped)?$")
_ONE_EACH = re.compile(r"^one onto the battlefield tapped and the other into your hand$")
_CAST = ""
_ENTERS = re.compile(
    r"^when (?:this|~) (?:creature|artifact|enchantment|permanent) enters, (?:you may )?$")
#: The New Capenna lands: "When this land enters, sacrifice it. When you do,
#: search ..." - a fetch land in two sentences.
_SACRIFICED_ON_ENTERING = re.compile(
    r"When this land enters, sacrifice it\. When you do, search your library", re.IGNORECASE)
_FETCH = re.compile(r"^\{t\}, (?:pay (?P<life>\w+) life, )?sacrifice this land: $")
_SACRIFICE_SELF = re.compile(r"^sacrifice this creature: $")
#: P19 R14: "{2}, {T}, Sacrifice this land: " - Wayfarer's Bauble, Myriad
#: Landscape, Burnished Hart (no {T}), the Panoramas.
_ACTIVATED = re.compile(
    r"^(?P<cost>(?:\{(?:\d+|[wubrg])\})+), (?P<tap>\{t\}, )?"
    r"sacrifice this (?P<what>land|artifact|creature|enchantment): $")
#: P19 R15: a creature's own {T} ability - Wight of the Reliquary, Knight of
#: the Reliquary, Elvish Reclaimer, Frontier Guide: "{2}, {T}, Sacrifice a
#: land: ". The parts after the {T}, each read or the whole left unread.
_CREATURE_TAP = re.compile(r"^(?:(?P<cost>(?:\{(?:\d+|[wubrg])\})+), )?\{t\}, ?(?P<rest>.*): $")
_TAP_PART = re.compile(
    r"sacrifice this creature|sacrifice a land|sacrifice another creature|discard a card"
    r"|sacrifice an? (?P<a>forest|plains|island|swamp|mountain)"
    r"(?: or (?P<b>forest|plains|island|swamp|mountain))?")
#: P19 R14: "When this creature enters, if an opponent controls more lands
#: than you, (you may) search ..." - Knight of the White Orchid.
_ENTERS_IF_BEHIND = re.compile(
    r"^(?:[\w' ]+ — )?when (?:this|~) (?:creature|artifact|enchantment|permanent) enters, "
    r"if an opponent controls more lands than you, (?:you may )?$")
#: P19 R14: a Saga's first chapter, which happens as it enters.
_FIRST_CHAPTER = re.compile(r"^i — (?:[^—]+ — )?$")
#: P19 R14: a search in combat - the engine plays none.
_IN_COMBAT = re.compile(r"\battacks\b|\bcombat damage\b")
#: P19 R15: Springbloom Druid - "When this creature enters, you may sacrifice
#: a land. If you do, search ...".
_SACRIFICE_LAND_FIRST = re.compile(
    r"When (?:this creature|~) enters, you may sacrifice a land\. If you do, search your library")
#: Krosan Verge: "a Forest card and a Plains card".
_ONE_OF_EACH = re.compile(r"^(\w+) card and an? (\w+)$")


@dataclass
class LandSearch:
    """A land search read whole (`spec`), or the reason it could not be."""

    spec: dict | None = None
    reason: str = ""


def _word_number(token: str) -> int | None:
    token = token.lower()
    return int(token) if token.isdigit() else _WORD_NUMBERS.get(token)


@dataclass
class Draw:
    """What a card's first "draw" reads as (P19 R8).

    ``extra_cost`` is the spree mode the draw sits in ("{B}{B}" on Insatiable
    Avarice), which the engine pays on top of the printed cost.
    """

    cards: int | None = None
    discards: int = 0
    puts_back: int = 0
    #: P19 R9: it draws X cards; ``x_unread``: by X in a way not modelled
    #: (Occult Epiphany discards X as well).
    x: bool = False
    x_unread: bool = False
    extra_cost: str = ""


def _draw(text: str) -> Draw:
    """Cards drawn, cards discarded or put back right after, a spree mode's cost."""
    numbered, by_x = _DRAW.search(text), _DRAW_X.search(text)
    first = min((found for found in (numbered, by_x) if found is not None),
                key=lambda found: found.start(), default=None)
    if first is None:
        return Draw()
    if first is by_x:
        draw = Draw(x=True)
    else:
        draw = Draw(cards=_first_number(_DRAW, text))
    if (loot := _LOOT.search(text)) is not None and loot.start() == first.start():
        if loot.group(2).upper() == "X":
            draw.x, draw.x_unread = False, True
        else:
            draw.discards = min(_word_number(loot.group(2)) or 0, _NUMBER_CEILING)
    if (back := _PUT_BACK.search(text)) is not None and back.start() == first.start():
        draw.puts_back = min(_word_number(back.group(2)) or 0, _NUMBER_CEILING)
    mode = _DRAW_MODE.search(text)
    repeated = _REPEATED_MODES.search(text)
    if repeated and mode and mode.start() <= first.start() < mode.end():
        draw.cards = (_word_number(mode.group(1)) or 1) * (_word_number(repeated.group(1)) or 1)
    for spree in _SPREE_MODE.finditer(text):
        if spree.start() <= first.start() < spree.end():
            draw.extra_cost = spree.group(1)
    return draw


def _land_search(card: OracleCard, kind: str) -> LandSearch:
    """Read a search that puts lands onto the battlefield, or say why not.

    Not a card with no such search: that is an empty `LandSearch`, and the
    general tutor reading (`_tutor`) goes on as before.
    """
    text = _LANDER_REMINDER.sub("", card.oracle_text or "")
    if len(_SEARCH_CLAUSE.findall(text)) != 1:
        return LandSearch()
    match = _LAND_SEARCH.search(text)
    if match is None or "battlefield" not in match.group("put").lower():
        return LandSearch()

    count = _word_number(match.group("n"))
    what = match.group("what").strip().lower()
    basic = what.startswith("basic ")
    names = what.removeprefix("basic ")
    each = _ONE_OF_EACH.match(names)
    if names == "land":
        types: list[str] = []
    elif each is not None and {each.group(1), each.group(2)} <= _LAND_TYPES and count == 1:
        types, count = [each.group(1), each.group(2)], 2
    else:
        types = [name for name in re.split(r",? or |, ", names) if name]
        if types and "land" not in names and not set(types) & _LAND_TYPES:
            # Natural Order's "green creature card": not a land search at
            # all, and the tutor reading takes it (P19 R15).
            return LandSearch()
        if not types or not set(types) <= _LAND_TYPES:
            return LandSearch(reason=gettext_noop(
                "searches for lands the engine cannot describe"))

    put = match.group("put").strip().lower()
    if (found := _ALL_TO_BATTLEFIELD.match(put)) is not None:
        battlefield, hand, tapped = count, 0, bool(found.group("tapped"))
    elif _ONE_EACH.match(put) and count == 2:
        battlefield, hand, tapped = 1, 1, True
    else:
        return LandSearch(reason=gettext_noop(
            "searches for lands, but where they go could not be read"))

    lead = match.group("lead").strip().lower()
    lead = f"{lead} " if lead else ""
    life, sacrifice = 0, False
    cost, taps, condition = "", False, ""
    sacrifices_land, land_cost_types, sacrifice_other, discard = False, [], [], 0
    tapping = _creature_tap(lead) if kind == DerivedProfile.Kind.CREATURE else None
    spell = kind in (DerivedProfile.Kind.SORCERY, DerivedProfile.Kind.INSTANT)
    activated = _ACTIVATED.match(lead)
    if not lead and spell:
        when = "cast"
    elif _ENTERS.match(lead) or _FIRST_CHAPTER.match(lead):
        when = "enters"
    elif _ENTERS_IF_BEHIND.match(lead):
        when, condition = "enters", "opponent_more_lands"
    elif lead == "if you do, " and _SACRIFICE_LAND_FIRST.search(text):
        when, sacrifices_land = "enters", True
    elif activated is not None and (
            (activated.group("what") == "land") == (kind == DerivedProfile.Kind.LAND)):
        when, sacrifice = "activate", True
        cost, taps = activated.group("cost").upper(), bool(activated.group("tap"))
    elif tapping is not None:
        # P19 R15: once a turn, and not the turn it arrived.
        when, taps = "activate", True
        cost, sacrifice = tapping["cost"], tapping["self"]
        sacrifices_land, land_cost_types = tapping["land"], tapping["land_types"]
        sacrifice_other, discard = tapping["other"], tapping["discard"]
    elif (lead == "when you do, " and kind == DerivedProfile.Kind.LAND
          and _SACRIFICED_ON_ENTERING.search(text)):
        when, sacrifice = "play", True
    elif (fetch := _FETCH.match(lead)) is not None and kind == DerivedProfile.Kind.LAND:
        when, sacrifice = "play", True
        if fetch.group("life"):
            life = _word_number(fetch.group("life"))
            if life is None:
                return LandSearch(reason=gettext_noop(
                    "searches for lands at a cost the engine does not pay"))
    elif _SACRIFICE_SELF.match(lead) and kind == DerivedProfile.Kind.CREATURE:
        when, sacrifice = "enters", True
    elif "{" in lead:
        return LandSearch(reason=gettext_noop(
            "searches for lands at a cost the engine does not pay"))
    elif _IN_COMBAT.search(lead):
        return LandSearch(reason=gettext_noop(
            "searches for lands in combat, which the engine does not play"))
    else:
        return LandSearch(reason=gettext_noop(
            "searches for lands under a condition the engine cannot read"))

    untap_at = 0
    if match.group("untap"):
        untap_at = _word_number(match.group("untap")) or 0
        if not untap_at:
            return LandSearch(reason=gettext_noop(
                "searches for lands under a condition the engine cannot read"))

    return LandSearch(spec={
        "battlefield": battlefield, "hand": hand, "tapped": tapped, "basic": basic,
        "types": sorted(types), "life": life, "when": when, "sacrifice": sacrifice,
        "untap_at": untap_at, "cost": cost, "taps": taps,
        "share_type": bool(match.group("share")), "each": each is not None and bool(types),
        "condition": condition, "sacrifices_land": sacrifices_land,
        "land_cost_types": land_cost_types, "sacrifice_other": sacrifice_other,
        "discard": discard,
    })


def _creature_tap(lead: str) -> dict | None:
    """A creature's "{2}, {T}, Sacrifice a land: " read part by part (P19
    R15), or None when one part is something else."""
    found = _CREATURE_TAP.match(lead)
    if found is None:
        return None
    read = {"cost": (found.group("cost") or "").upper(), "self": False, "land": False,
            "land_types": [], "other": [], "discard": 0}
    for part in filter(None, found.group("rest").split(", ")):
        piece = _TAP_PART.fullmatch(part)
        if piece is None:
            return None
        if part == "sacrifice this creature":
            read["self"] = True
        elif part == "sacrifice another creature":
            read["other"] = ["creature"]
        elif part == "discard a card":
            read["discard"] = 1
        else:
            read["land"] = True
            read["land_types"] = sorted(filter(None, (piece.group("a"), piece.group("b"))))
    return read


def _first_number(pattern: re.Pattern, text: str) -> int | None:
    match = pattern.search(text or "")
    if not match:
        return None
    token = match.group(1)
    value = int(token) if token.isdigit() else _WORD_NUMBERS.get(token.lower())
    if value is None:
        return None
    return min(value, _NUMBER_CEILING)


def derive(card: OracleCard, tag_slugs: set[str] | None = None, *,
           branches: frozenset[str] | None = None,
           revivals: frozenset[str] | None = None) -> DerivedProfile:
    """Build (not save) the profile for one card.

    `branches` is `tutor_branches()` and `revivals` is `reanimate_branches()`,
    passed in by `rebuild` so that a pass over the catalogue reads the tag tree
    once rather than once per land fetcher or reanimator.
    """
    tags = tag_slugs if tag_slugs is not None else set(card.tags.values_list("slug", flat=True))
    text = card.oracle_text or ""

    cost = parse_mana_cost(card.mana_cost)
    kind = derive_kind(card, tags)
    roles = sorted({ROLE_FROM_TAG[slug] for slug in tags if slug in ROLE_FROM_TAG})
    if "tutor" in roles and _finds_only_lands(tags, branches):
        roles.remove("tutor")
    if "reanimate" in roles and _returns_no_creature(tags, revivals):
        roles.remove("reanimate")
    if card.game_changer:
        roles.append("gamechanger")
    if kind == DerivedProfile.Kind.CREATURE and "creature" not in roles:
        roles.append("creature")
    if kind == DerivedProfile.Kind.LAND and "land" not in roles:
        roles.append("land")

    tapped, tapped_note, tapped_unless = _enters_tapped(card)
    mana = _mana_production(card)
    mana_rule = _mana_rule(card, kind)
    if mana_rule is not None:
        mana.notes = [note for note in mana.notes if note not in _RULE_EXPLAINS]
    treasures = _treasures(card, kind)
    if treasures and not _GRANTED.search(_TREASURE_REMINDER.sub("", text)):
        # The quoted ability was only the Treasure's own reminder text, and
        # the Treasure is read now (P19 R7).
        mana.notes = [note for note in mana.notes
                      if note != "only grants a mana ability to another permanent"]
    discard = _DISCARD_COST.search(text)
    additional = AdditionalCost() if discard else _additional_cost(card)
    draw = _draw(text)
    amount = mana.amount
    if mana.one_shot and kind in (DerivedProfile.Kind.ARTIFACT, DerivedProfile.Kind.ROCK):
        # Lotus Petal: "{T}, Sacrifice this artifact: Add one mana of any
        # color." One use, then the graveyard - which is exactly what the
        # engine's ritual is, and the agent casts a ritual precisely when the
        # mana unlocks something worth casting. Read as a rock it made a mana
        # every turn for the rest of the game.
        kind = DerivedProfile.Kind.RITUAL
    if mana.from_hand:
        # Elvish Spirit Guide (P19 R15): a ritual for {0} that goes to exile.
        # Its body is never cast, which can only under-read it.
        kind = DerivedProfile.Kind.RITUAL
    tutor = _tutor(card, tags)
    land = _land_search(card, kind)
    if land.spec is not None or land.reason:
        # A land search read whole, or the reason it could not be: either way
        # more specific than what the general tutor reading says about it
        # ("tutors onto the battlefield, which the engine cannot do").
        tutor = Tutor(reason=land.reason)
    landers = _landers(card, kind)
    if _LANDER.search(text) and not _SEARCH_CLAUSE.search(_LANDER_REMINDER.sub("", text)):
        # The only search is the Lander's own (P19 R14): played as a token
        # when the card makes it as it resolves, a gap when it is made later.
        tutor = Tutor(reason="" if landers else gettext_noop(
            "makes a Lander token at a moment the engine does not play"))

    opponent_loss = _first_number(_OPPONENT_LOSS, text)
    if opponent_loss and "drain_payoff" not in roles:
        # The pronoun check earns its keep here: Liliana's Caress and Syr Konrad
        # drain opponents without carrying the community `drain-life` tag.
        roles.append("drain_payoff")

    reasons: list[str] = []
    for note in (tapped_note, *mana.notes, tutor.reason):
        if note:
            reasons.append(note)
    if tutor.zone and tutor.count is None:
        reasons.append(gettext_noop("tutors, but how many cards it finds could not be read"))
    # The community says this makes more than one mana and we read exactly one.
    # Not a value - a contradiction, and the honest response to a contradiction
    # is to report it rather than to pick a side. Sol Ring is the shape: the
    # `Add {C}{C}` clause is readable, but the cards this catches are the ones
    # where it is not and a single mana slipped through looking correct.
    counts = mana_rule is not None and mana_rule["rule"] == "counts"
    altar = mana.sacrifice is not None and (mana.sacrifice["amount"] or 0) > 1
    if MULTIPLE_MANA_TAG in tags and amount == 1 and not counts and not altar:
        reasons.append(gettext_noop("tagged as adding more than one mana; only one was read"))
    if additional.reason:
        reasons.append(additional.reason)
    elif _OTHER_SPELLS_COST.search(text):
        reasons.append(gettext_noop(
            "changes what other spells cost, which the engine does not model"))
    elif (_ADDITIONAL_COST.search(text) and not discard and additional.ways is None
          and not additional.optional):
        reasons.append(gettext_noop("has an additional casting cost the engine does not pay"))
    if cost.hybrid:
        reasons.append(gettext_noop("hybrid pips: payment flexibility is not modelled"))
    # Since engine version 13 an X is paid (P19 R9): all that is left, last
    # in the main phase. What X then does is a gap only where it feeds
    # something the engine plays and cannot read yet.
    if cost.has_x and draw.x_unread:
        reasons.append(gettext_noop("draws or discards by X in a way that is not modelled"))
    if cost.has_x and _X_LANDS.search(text):
        reasons.append(gettext_noop("puts lands onto the battlefield by X; not modelled"))

    profile = DerivedProfile(
        oracle_card=card,
        mv=int(card.cmc or cost.mv),
        pips=cost.pips,
        generic=cost.generic,
        colorless=cost.colorless,
        has_x=cost.has_x,
        kind=kind,
        is_basic_swamp=card.type_line.startswith("Basic Land") and "Swamp" in card.type_line,
        role_tags=sorted(set(roles)),
        enters_tapped=tapped,
        produces_mana=mana.produces_mana,
        mana_colors=list(card.produced_mana or []),
        mana_amount=amount,
        mana_produces=mana.produces,
        mana_activation=mana.activation or None,
        mana_untaps=mana.untaps,
        mana_condition=mana.condition,
        mana_rule=mana_rule,
        mana_filter=mana.filter,
        treasures=treasures,
        landers=landers,
        discard_cost=_word_number(discard.group(1)) if discard else 0,
        additional_cost=additional.ways,
        sacrifice_mana=mana.sacrifice,
        mana_from_hand=mana.from_hand,
        cost_reduction=_first_number(_COST_REDUCTION, text),
        draws_cards=draw.cards,
        discards_after=draw.discards,
        puts_back=draw.puts_back,
        # Only a cost with X says what X is; Painful Truths counts colours.
        draws_x=draw.x and cost.has_x,
        extra_cost=draw.extra_cost,
        self_life_loss=_first_number(_SELF_LOSS, text),
        opponent_life_loss=opponent_loss,
        tutor_to=tutor.zone,
        tutor_count=tutor.count,
        tutor_kind=tutor.kind,
        tutor_filter=tutor.filter,
        land_search=land.spec,
        tapped_unless=tapped_unless,
        skips_draw_step=bool(_SKIPS_DRAW_STEP.search(text)),
        needs_review=bool(reasons),
        review_reasons=[reason[:120] for reason in reasons],
        source_map=_source_map(tags, tapped, mana, tutor),
    )
    return profile


def _source_map(tags: set[str], tapped: bool, mana: ManaReading,
                tutor: "Tutor") -> dict[str, str]:
    """Which layer is responsible for which field.

    Phase 4 renders this as a provenance badge, so a user can see that "ramp"
    came from a community tag while "enters tapped" came from a regex over
    English prose - and weigh them accordingly.
    """
    mapping = {
        "mv": SCRYFALL,
        "pips": SCRYFALL,
        "generic": SCRYFALL,
        "colorless": SCRYFALL,
        "kind": SCRYFALL,
        "is_basic_swamp": SCRYFALL,
        "mana_colors": SCRYFALL,
        "role_tags": TAGS if tags else SCRYFALL,
        "cost_reduction": REGEX,
        "draws_cards": REGEX,
        "discards_after": REGEX,
        "puts_back": REGEX,
        "draws_x": REGEX,
        "self_life_loss": REGEX,
        "opponent_life_loss": REGEX,
    }
    if tapped:
        mapping["enters_tapped"] = REGEX
    if mana.produces_mana:
        mapping["produces_mana"] = SCRYFALL
        mapping["mana_amount"] = REGEX
        # Both read off the ability's cost and the card's text by the clause
        # reader, which is a regex over prose like everything else here.
        mapping["mana_activation"] = REGEX
        mapping["mana_untaps"] = REGEX
        if mana.produces is not None:
            mapping["mana_produces"] = REGEX
    if tutor.zone:
        # Two sources on one card, which is the whole shape of the tutor work:
        # the community tagged the zone, a pattern over the printed text read
        # the amount. The provenance panel shows them separately so that
        # disagreeing with one does not mean disbelieving the other.
        mapping["tutor_to"] = TAGS
        mapping["tutor_kind"] = TAGS
    if tutor.count is not None:
        mapping["tutor_count"] = REGEX
    return mapping


def rebuild(queryset=None, *, batch_size: int = 1_000) -> int:
    """Derive profiles for a whole queryset, in one pass over the tag table.

    Loading the tags per card would be 35,568 queries. Loading them all at once
    would be 429,263 rows in memory. The middle path: iterate cards in chunks
    and fetch the tags for each chunk in one query.
    """
    from django.db import reset_queries

    from cards.models import OracleCardTag

    cards = queryset if queryset is not None else OracleCard.objects.all()
    branches = tutor_branches()
    revivals = reanimate_branches()
    written = 0
    batch: list[OracleCard] = []

    for card in cards.iterator(chunk_size=batch_size):
        batch.append(card)
        if len(batch) >= batch_size:
            written += _flush_profiles(batch, OracleCardTag, branches, revivals)
            batch = []
            reset_queries()

    written += _flush_profiles(batch, OracleCardTag, branches, revivals)
    return written


def _flush_profiles(batch: list[OracleCard], link_model, branches: frozenset[str],
                    revivals: frozenset[str]) -> int:
    if not batch:
        return 0

    slugs_by_card: dict[str, set[str]] = {}
    links = link_model.objects.filter(oracle_card__in=batch).values_list(
        "oracle_card_id", "tag__slug"
    )
    for card_id, slug in links:
        slugs_by_card.setdefault(str(card_id), set()).add(slug)

    profiles = [derive(card, slugs_by_card.get(str(card.pk), set()), branches=branches,
                       revivals=revivals)
                for card in batch]
    DerivedProfile.objects.bulk_create(
        profiles,
        batch_size=len(profiles),
        update_conflicts=True,
        update_fields=[
            "mv", "pips", "generic", "colorless", "has_x", "kind", "is_basic_swamp",
            "role_tags", "enters_tapped", "produces_mana", "mana_colors", "mana_amount",
            "cost_reduction", "draws_cards", "self_life_loss", "opponent_life_loss",
            "tutor_to", "tutor_count", "tutor_kind", "tutor_filter", "land_search",
            "tapped_unless",
            "skips_draw_step",
            "mana_produces", "mana_activation", "mana_untaps", "mana_condition", "mana_rule",
            "mana_filter", "treasures", "landers", "discard_cost", "additional_cost",
            "sacrifice_mana", "mana_from_hand",
            "discards_after", "puts_back",
            "draws_x",
            "extra_cost",
            "needs_review", "review_reasons", "source_map", "derived_at",
        ],
        unique_fields=["oracle_card"],
    )
    return len(profiles)
