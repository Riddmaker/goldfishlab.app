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


def tutor_branches() -> frozenset[str]:
    """The direct children of `tutor` in the tag tree, read from the database.

    Read rather than written down, because the tree is Scryfall's and grows: a
    list here would quietly file a card under a new branch as a land fetcher.
    """
    from cards.models import TagEdge

    return frozenset(TagEdge.objects.filter(parent__slug=TUTOR_TAG)
                     .values_list("child__slug", flat=True))


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
    r"(a|an|one|two|three|four|five|\d+) mana of any (?:one )?colou?r", re.IGNORECASE
)
# Exotic Orchard is "Add one mana of any color **that a land an opponent
# controls could produce**". Same opening words, and in a goldfish it makes
# nothing at all, because there is no opponent and therefore no lands to copy.
# Reading it as one mana would hand the deck a source it does not have.
_OPPONENT_SOURCE = re.compile(r"\ban opponent\b|\bopponents\b|\beach player\b", re.IGNORECASE)
# Quoted text is an ability this card GRANTS to something else: a token it
# creates, a land it enchants, creatures it pumps. The grantee is the mana
# source; this card is not. Curly quotes included because Oracle text uses both.
_GRANTED = re.compile(r"[\"“][^\"”]*[\"”]")
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
#: Mox Diamond: the card arrives only if a land card is discarded for it.
_ENTERS_BY_DISCARD = re.compile(
    r"If this [\w ]+? would enter, you may discard|As this [\w ]+? enters, you may discard",
    re.IGNORECASE,
)

_COST_REDUCTION = re.compile(
    r"spells? (?:you cast )?costs? \{(\d+)\} less to cast", re.IGNORECASE
)
_DRAW = re.compile(r"\bdraws? (a|one|two|three|four|five|\d+) cards?", re.IGNORECASE)
# The pronoun check the deck research asked for: "you lose 1 life" is a cost,
# "each opponent loses 1 life" is a payoff. Same verb, opposite meaning.
_SELF_LOSS = re.compile(r"\byou lose (\d+|a|one|two|three) life", re.IGNORECASE)
_OPPONENT_LOSS = re.compile(
    r"\b(?:each opponent|target opponent|that player|each other player) "
    r"loses (\d+|a|one|two|three) life",
    re.IGNORECASE,
)

_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5}

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

#: "As an additional cost to cast this spell, sacrifice a creature." The engine
#: pays mana and nothing else, so it casts Diabolic Intent for {2}{B} and gets a
#: free Demonic Tutor. 360 cards in the catalogue say this; deriving the tutors
#: is what made it matter, because before this phase most of them did nothing at
#: all and an unpaid cost on a card that does nothing costs nothing.
_ADDITIONAL_COST = re.compile(r"As an additional cost to cast", re.IGNORECASE)

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


def _enters_tapped(card: OracleCard) -> tuple[bool, str]:
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

    if re.search(pattern, text, re.IGNORECASE):
        if _UNLESS.search(text):
            return True, "enters tapped only conditionally ('unless')"
        return True, ""

    if _TAPPED_MENTION.search(text):
        return False, "text mentions entering tapped, but the condition was not readable"

    return False, ""


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
    problem: str = ""
    note: str = ""

    @property
    def net(self) -> int:
        return (self.amount or 0) - self.activation


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
    #: The mana comes from sacrificing the card itself (Lotus Petal) - once,
    #: which the engine models as a ritual cast when it unlocks something.
    one_shot: bool = False
    notes: list[str] = field(default_factory=list)


def _self_reference(card: OracleCard) -> str:
    """How a card names itself: "this artifact", the legacy "~", or its name."""
    return rf"(?:this [\w ]+?|~|{re.escape(card.front_name or '')})"


def _read_cost(clause: ManaClause, cost_text: str, card: OracleCard) -> None:
    """Split `{1}, {T}, Sacrifice this artifact` into what the engine can pay."""
    for part in (piece.strip() for piece in cost_text.split(",")):
        if not part or part == "{T}":
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
        if _SYMBOL_RUN.fullmatch(part):
            clause.problem = "a mana ability with a coloured cost (a filter) is not modelled"
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
            clause.problem = "makes a kind of mana the engine does not model"
            return
        if re.match(r"\s*,\s*then\b", rest):
            # Rite of Flame: "Add {R}{R}, then add {R} for each ...". The base
            # is fixed; only the rider grows, and it is not counted.
            clause.note = "the part of its mana that grows with the board is not counted"
        elif _SCALING.search(rest_sentence):
            clause.problem = "mana amount scales with the board"
            return
        if "instead" in rest_sentence:
            # Cabal Ritual's threshold: a replacement that needs a full
            # graveyard, which the early turns being measured do not have.
            clause.problem = "a conditional replacement ('instead') is not counted"
            return
        choice = bool(re.match(r"\s*(?:,\s*)?(?:or\s+)?\{", rest))
        clause.amount = len(symbols)
        clause.produces = None if choice else dict(Counter(symbols))
        return

    words = _ANY_COLOR.match(after)
    if words:
        token = words.group(1).lower()
        clause.amount = int(token) if token.isdigit() else _WORD_NUMBERS[token]
        tail = after[words.end():].split(".", 1)[0]
        if _OPPONENT_SOURCE.search(tail):
            clause.problem = "needs an opponent's lands; a goldfish has none"
        elif _SCALING.search(tail):
            clause.problem = "mana amount scales with the board"
        return

    if _SCALES_UP_FRONT.match(after):
        clause.problem = "mana amount scales with the board"
    else:
        clause.problem = "an 'Add' clause whose amount could not be read"


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
                    clause.problem = "makes mana only from a triggered or static ability"
            if prefix.strip() and not clause.problem:
                # Deathrite Shaman: "{T}: Exile target land card from a
                # graveyard. Add ..." - the mana needs a target first.
                clause.problem = "makes mana only as part of another effect"
            if not clause.problem:
                _read_produced(clause, after)
            if not clause.problem and _RESTRICTED.search(after):
                clause.problem = "its mana is restricted to certain spells or moments"
            found.append(clause)
    return found


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
        notes.add("enters only by discarding a land card, which the engine does not do")

    if not clauses:
        # Scryfall says it makes mana but no `Add` clause of its own was found:
        # either every one is inside quotes, or it is a replacement effect or a
        # face this text does not cover.
        reading.notes = [
            "only grants a mana ability to another permanent"
            if _ADD_WORD.search(text)
            else "produces mana, but no readable 'Add' clause"
        ]
        return reading

    if is_spell:
        spells = [clause for clause in usable if not clause.is_ability]
        if spells:
            reading.amount, reading.produces = spells[0].amount, spells[0].produces
        reading.notes = sorted(notes)
        return reading

    tapping = [clause for clause in usable if not clause.sacrifices_self]
    one_shots = [clause for clause in usable if clause.sacrifices_self]
    worth_it = [clause for clause in tapping if clause.net > 0]

    if worth_it:
        best = max(clause.net for clause in worth_it)
        top = [clause for clause in worth_it if clause.net == best]
        top = [clause for clause in top if not clause.activation] or top
        chosen = top[0]
        reading.amount, reading.activation = chosen.amount, chosen.activation
        # Two free abilities making different colours (a Talisman's {C} or
        # {U}/{B}) are a choice between them, which the deck's colours settle.
        same = all(clause.produces == chosen.produces for clause in top)
        reading.produces = chosen.produces if same else None
        if any(clause.net <= 0 for clause in tapping):
            notes.add("an ability that only converts mana is not modelled")
    elif one_shots and "Creature" not in type_line:
        chosen = one_shots[0]
        reading.amount, reading.produces = chosen.amount, chosen.produces
        reading.one_shot = True
    else:
        if one_shots:
            notes.add("sacrifices itself for mana, which is not modelled on a creature")
        if tapping:
            notes.add("its mana ability costs as much as it makes")

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
        return Tutor(reason="searches the library more than once; not modelled")
    if not _SEARCH_YOUR_LIBRARY.search(text):
        # Either the search is somebody else's ("target player searches their
        # library") or it is worded in a way this pattern does not cover. The
        # tag says the card is a tutor, so the honest answer is to say we could
        # not read it rather than to drop it silently.
        return Tutor(reason="tutors, but no readable 'search your library' clause")

    count = _first_number(_SEARCH_YOUR_LIBRARY, text)
    if not zones:
        return Tutor(count=count, reason="tutors, but to which zone could not be read")
    if len(zones) > 1:
        return Tutor(count=count, reason="tutors to more than one zone; not modelled")

    zone = zones.pop()
    if zone == "battlefield":
        return Tutor(zone=zone, count=count,
                     reason="tutors onto the battlefield, which the engine cannot do")
    if tags & TUTOR_UNEXPRESSIBLE_TAGS:
        return Tutor(zone=zone, count=count,
                     reason="tutors for something the engine cannot describe")
    if len(kinds) > 1:
        return Tutor(zone=zone, count=count,
                     reason="tutors for more than one card kind; not modelled")

    if _RANDOM_DISCARD.search(text):
        return Tutor(zone=zone, count=count,
                     reason="searches, then discards at random; the discard is not modelled")

    return Tutor(zone=zone, count=count, kind=kinds.pop() if kinds else "")


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
           branches: frozenset[str] | None = None) -> DerivedProfile:
    """Build (not save) the profile for one card.

    `branches` is `tutor_branches()`, passed in by `rebuild` so that a pass over
    the catalogue reads the tag tree once rather than once per land fetcher.
    """
    tags = tag_slugs if tag_slugs is not None else set(card.tags.values_list("slug", flat=True))
    text = card.oracle_text or ""

    cost = parse_mana_cost(card.mana_cost)
    kind = derive_kind(card, tags)
    roles = sorted({ROLE_FROM_TAG[slug] for slug in tags if slug in ROLE_FROM_TAG})
    if "tutor" in roles and _finds_only_lands(tags, branches):
        roles.remove("tutor")
    if card.game_changer:
        roles.append("gamechanger")
    if kind == DerivedProfile.Kind.CREATURE and "creature" not in roles:
        roles.append("creature")
    if kind == DerivedProfile.Kind.LAND and "land" not in roles:
        roles.append("land")

    tapped, tapped_note = _enters_tapped(card)
    mana = _mana_production(card)
    amount = mana.amount
    if mana.one_shot and kind in (DerivedProfile.Kind.ARTIFACT, DerivedProfile.Kind.ROCK):
        # Lotus Petal: "{T}, Sacrifice this artifact: Add one mana of any
        # color." One use, then the graveyard - which is exactly what the
        # engine's ritual is, and the agent casts a ritual precisely when the
        # mana unlocks something worth casting. Read as a rock it made a mana
        # every turn for the rest of the game.
        kind = DerivedProfile.Kind.RITUAL
    tutor = _tutor(card, tags)

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
        reasons.append("tutors, but how many cards it finds could not be read")
    # The community says this makes more than one mana and we read exactly one.
    # Not a value - a contradiction, and the honest response to a contradiction
    # is to report it rather than to pick a side. Sol Ring is the shape: the
    # `Add {C}{C}` clause is readable, but the cards this catches are the ones
    # where it is not and a single mana slipped through looking correct.
    if MULTIPLE_MANA_TAG in tags and amount == 1:
        reasons.append("tagged as adding more than one mana; only one was read")
    if _ADDITIONAL_COST.search(text):
        reasons.append("has an additional casting cost the engine does not pay")
    if cost.hybrid:
        reasons.append("hybrid pips: payment flexibility is not modelled")
    if cost.has_x:
        reasons.append("cost contains X")

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
        cost_reduction=_first_number(_COST_REDUCTION, text),
        draws_cards=_first_number(_DRAW, text),
        self_life_loss=_first_number(_SELF_LOSS, text),
        opponent_life_loss=opponent_loss,
        tutor_to=tutor.zone,
        tutor_count=tutor.count,
        tutor_kind=tutor.kind,
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
    written = 0
    batch: list[OracleCard] = []

    for card in cards.iterator(chunk_size=batch_size):
        batch.append(card)
        if len(batch) >= batch_size:
            written += _flush_profiles(batch, OracleCardTag, branches)
            batch = []
            reset_queries()

    written += _flush_profiles(batch, OracleCardTag, branches)
    return written


def _flush_profiles(batch: list[OracleCard], link_model, branches: frozenset[str]) -> int:
    if not batch:
        return 0

    slugs_by_card: dict[str, set[str]] = {}
    links = link_model.objects.filter(oracle_card__in=batch).values_list(
        "oracle_card_id", "tag__slug"
    )
    for card_id, slug in links:
        slugs_by_card.setdefault(str(card_id), set()).add(slug)

    profiles = [derive(card, slugs_by_card.get(str(card.pk), set()), branches=branches)
                for card in batch]
    DerivedProfile.objects.bulk_create(
        profiles,
        batch_size=len(profiles),
        update_conflicts=True,
        update_fields=[
            "mv", "pips", "generic", "colorless", "has_x", "kind", "is_basic_swamp",
            "role_tags", "enters_tapped", "produces_mana", "mana_colors", "mana_amount",
            "cost_reduction", "draws_cards", "self_life_loss", "opponent_life_loss",
            "tutor_to", "tutor_count", "tutor_kind", "skips_draw_step",
            "mana_produces", "mana_activation", "mana_untaps",
            "needs_review", "review_reasons", "source_map", "derived_at",
        ],
        unique_fields=["oracle_card"],
    )
    return len(profiles)
