"""Categories from blocks (card model phase 3).

A card's categories come in four facets (the rebuild doc, "Categories from
blocks"): its printed **type**, one primary and at most one secondary
**role**, any number of **mechanics**, and a **tempo** per ability. This
module derives the last three from the card's blocks
(`simulation.blocks`, translated from Forge's script) and the roles the
community tags give it (`DerivedProfile.role_tags`):

* every effect yields **labels**, each *defining* (what the ability is for)
  or *incidental* (a rider beside something else, or an effect for an
  opponent). A cost yields none: Deadly Dispute's sacrifice makes no
  sacrifice outlet;
* for the broad roles the **tags stay the default** - the blocks find only
  about half of what the ramp tag holds - and a defining label adds a role
  the tags miss. A tag role the blocks hold only as incidental is dropped
  when the card is read whole (Path to Exile is no ramp), and kept
  otherwise; either way the card goes on the review list;
* the **primary** role is the strongest, a role both the tags and a block
  say before one only one of them says, ties broken by `ROLE_ORDER`; the
  **secondary** is the next. A card may have neither: Blood Artist is a
  plan card, not a role;
* **mechanics** are the strategy roles of the tags plus every mechanic label
  of a block, incidental ones included - Deadly Dispute's Treasure is a
  mechanic - but never one of an effect for an opponent.

A card without blocks (no script, or one of an older format) is read off
the fields our own reader filled in. Nothing here imports the database:
`derive` takes plain values, so the worked examples test it directly.
"""

import re
from dataclasses import dataclass, field

from simulation import blocks as b

#: Stored beside the categories (`DerivedProfile.categories_version`); 0 there
#: means "not derived yet", and every reader falls back to `role_tags`.
#: 2: a tempo entry says whether its ability draws (``draws``), and a card's
#: own cycling or leaving happens once (card model PR 17). 3: so does a trigger
#: on casting the card itself (Hydroid Krasis; card model PR 19).
VERSION = 3

#: The roles: the eight the report counts (`simulation.analysis.SEEN_CATEGORIES`).
ROLES = ("ramp", "draw", "removal", "wipe", "tutor", "counterspell", "protection",
         "recursion")

#: Which role is primary when two are equally strong: the specific before the
#: broad (decided 2026-10-14). A wipe that also draws is a wipe.
ROLE_ORDER = ("wipe", "counterspell", "removal", "tutor", "ramp", "draw", "recursion",
              "protection")

#: The strategy roles the tags give today, kept under their names.
TAG_MECHANICS = ("sac_outlet", "reanimate", "drain_payoff", "steal", "discard",
                 "cost_reducer", "evasion", "recursive")

#: The mechanics phase 3 adds, read off the blocks alone.
NEW_MECHANICS = ("lifegain", "land_search", "treasure", "tokens", "death_trigger", "tax")

MECHANICS = TAG_MECHANICS + NEW_MECHANICS

# --- tempo ------------------------------------------------------------------------

ONE_SHOT = "one_shot"
REPEATABLE = "repeatable"
EVENT = "event"
#: What counts as "again and again" when asking whether a card is an engine:
#: an ability that waits for an event fires again too (decided 2026-10-14).
REPEATS = frozenset({REPEATABLE, EVENT})

NEEDS_NOTHING = "none"
NEEDS_BOARD = "board"
NEEDS_OPPONENT = "opponent"

#: Cheap, in the tempo facet: worked out from the mana value, never stored.
CHEAP_MV = 2


def cheap(mv: int) -> bool:
    return mv <= CHEAP_MV


# --- what a card's categories are -------------------------------------------------


@dataclass(frozen=True)
class Categories:
    """The derived categories of one card.

    ``review`` holds the contradictions between the tags and the blocks:
    ``dropped:<role>`` (the tags say it, the blocks hold it only as a rider,
    the card is read whole, so it went) and ``incidental:<role>`` (the same,
    on a card read only in part, so it stayed).
    """

    primary: str = ""
    secondary: str = ""
    mechanics: tuple[str, ...] = ()
    tempo: dict = field(default_factory=dict)
    review: tuple[str, ...] = ()

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(role for role in (self.primary, self.secondary) if role)

    @property
    def keys(self) -> frozenset[str]:
        """Every category the card counts in: its roles and its mechanics."""
        return frozenset(self.roles) | frozenset(self.mechanics)


@dataclass(frozen=True)
class Label:
    name: str
    defining: bool
    #: Of an effect for an opponent: a role it would contradict, never a
    #: mechanic of the card.
    others: bool = False
    #: A role the block can add where the tags miss it. Not when the block
    #: cannot say whom it hits (`CONFIRMS`): then it only backs a tag up.
    adds: bool = True


# --- labels -------------------------------------------------------------------------

#: Effects that ride along: incidental beside another effect of the ability,
#: defining when they are all it does (Smothering Tithe's Treasure).
RIDERS = frozenset({"create_token", "gain_life", "scry", "surveil", "investigate"})

#: Effects whose benefit goes to whoever ``player`` or ``owner`` names: given
#: to an opponent (Path to Exile's land, Beast Within's token), they are
#: incidental and no mechanic of the card.
_BENEFITS = frozenset({"draw", "gain_life", "create_token", "move", "add_mana",
                       "scry", "surveil", "investigate"})

#: Tokens that are mana: ramp. A colourless artifact token's script starts
#: ``c_a_`` (``c_a_powerstone``); a Treasure's is shortened to its name.
_MANA_TOKENS = frozenset({"treasure", "powerstone", "gold"})
#: A creature token's script: colours, power, toughness, then its kind
#: (``b_2_2_zombie``, ``c_1_1_a_thopter_flying``, ``gu_0_0_fractal``, ``g_x_x_ooze``).
_CREATURE_TOKEN = re.compile(r"^[wubrgc]+_(?:\d+|x|y)_(?:\d+|x|y)_")
#: Creature tokens that sacrifice for mana.
_SPAWN = re.compile(r"eldrazi_(?:spawn|scion)")

#: The other land types a search names on its own: "a basic land or a Desert".
_LAND_TYPES = frozenset({"desert", "gate", "town", "cave", "lair", "locus", "sphere", "tower",
                         "mine", "power-plant", "urza's"})
_LIFELINK = "Lifelink"
#: What a wipe can sweep; a filter naming none of these (Chainer's
#: Nightmares) is no wipe.
_SWEPT = frozenset({"creature", "permanent", "artifact", "enchantment", "planeswalker", "land"})

#: A ``player`` or ``owner`` who is somebody else (`forge_import._player`):
#: "its controller" of a target (Path to Exile, Beast Within), an opponent,
#: the attacking player. "Target player", "each player" and "that player" on
#: your own trigger (``any``) may be you, and count as yours.
_SOMEBODY_ELSE = frozenset({"opponent", "other"})


def _for_others(effect: b.Effect) -> bool:
    """Whether the effect's benefit goes to someone else: "its controller
    searches", "each opponent draws", "its controller creates"."""
    return any(effect.arg(name) in _SOMEBODY_ELSE for name in ("player", "owner"))


def _what(effect: b.Effect) -> tuple[b.Which, ...]:
    """The cards the effect is about, one `Which` per alternative."""
    return effect.arg("what") or ()


def _is_self(what: tuple[b.Which, ...]) -> bool:
    return any(which.itself for which in what)


def _is_yours(what: tuple[b.Which, ...]) -> bool:
    """A filter that only finds your own: Ephemerate's blink is no removal."""
    return any(which.whose == "you" for which in what)


def _found(what: tuple[b.Which, ...]) -> bool:
    """A card the ability already found, not a search (`Which.found`)."""
    return any(which.found for which in what)


def _land(which: b.Which) -> bool:
    cards = which.cards
    return "land" in cards.types or bool(cards.subtypes) or bool(
        cards.named_types & _LAND_TYPES)


def _lands_only(what: tuple[b.Which, ...]) -> bool:
    """``Land.Basic``, ``Card.Plains``, ``EACH Forest & Plains``: lands only."""
    return bool(what) and all(_land(which) for which in what)


def _negative(value) -> bool:
    if isinstance(value, int):
        return value < 0
    return str(value or "").startswith("-")


def _token(script: str) -> list[str]:
    """What a token script makes; several, comma-separated, at once."""
    found: list[str] = []
    for name in (part.strip() for part in script.split(",")):
        kind = name.removeprefix("c_a_").split("_")[0]
        if kind in _MANA_TOKENS:
            found += ["treasure", "ramp"] if kind == "treasure" else ["ramp"]
        elif "lander" in name:
            found.append("land_search")
        elif _CREATURE_TOKEN.match(name):
            found += ["tokens", "ramp"] if _SPAWN.search(name) else ["tokens"]
    return list(dict.fromkeys(found))


def _itself(what: tuple[b.Which, ...], ability: b.Ability) -> bool:
    """The card moves itself: "return this card", "return it" on its own
    death (the Enduring cycle), or no card named at all (Bloodghast)."""
    if _is_self(what) or not what:
        return True
    return not what[0].base and "triggered" in what[0].found and ability.event is not None \
        and ability.event.who == "self"


def _swept(what: tuple[b.Which, ...]) -> bool:
    """Every card of a type, not one the ability already found (Coffin
    Queen's remembered creature)."""
    return any(which.base in _SWEPT and which.whose != "you" and not which.found
               for which in what)


def _flickers(ability: b.Ability) -> bool:
    """The ability returns what it exiles (Eldrazi Displacer, Astral Slide,
    Flicker): a blink, no removal."""
    return any(effect.verb in ("move", "move_all")
               and str(effect.arg("from") or "") in ("exile", "all")
               and str(effect.arg("to") or "") == "battlefield"
               and _found(_what(effect))
               for effect in ability.effects)


def _move(effect: b.Effect, ability: b.Ability, *, all_: bool, swap: bool) -> list[str]:
    origin, destination = str(effect.arg("from") or ""), str(effect.arg("to") or "")
    what = _what(effect)
    if "graveyard" in origin and destination in ("hand", "battlefield") \
            and _itself(what, ability):
        return ["recursive"]
    if _is_self(what):
        return []
    if origin == "library" and destination in ("battlefield", "hand"):
        if _lands_only(what):
            # Fetching a land for one given up is no ramp: a fetch land,
            # Crop Rotation.
            return ["land_search", "ramp"] if destination == "battlefield" and not swap \
                else ["land_search"]
        if all_ or not what or _found(what):
            return []
        return ["tutor"]
    if "graveyard" in origin and destination == "hand":
        return ["recursion"]
    if "graveyard" in origin and destination == "battlefield":
        if _lands_only(what):
            return ["recursion"]
        return ["reanimate", "recursion"]
    if origin == "battlefield" and destination in ("exile", "hand", "library", "graveyard"):
        if _is_yours(what) or (destination == "exile" and _flickers(ability)):
            return []
        if all_:
            return ["wipe"] if _swept(what) else []
        return ["removal"] if effect.arg("target") else []
    return []


def _effect_labels(effect: b.Effect, ability: b.Ability, kind: str,
                   swap: bool = False) -> list[str]:
    verb = effect.verb
    if verb == "add_mana":
        # A land's own mana is no ramp: the tags say it where it is (Ancient
        # Tomb). Nor is mana that only replaces what it costs (Barbed Sextant).
        if (kind == "land" and ability.kind == b.MANA) or not b.nets_mana(ability, effect):
            return []
        return ["ramp"]
    if verb == "draw":
        return ["draw"]
    if verb == "create_token":
        return _token(str(effect.arg("token") or ""))
    if verb == "amass":
        return ["tokens"]
    if verb == "gain_life":
        return ["lifegain"]
    if verb in ("move", "move_all"):
        return _move(effect, ability, all_=verb == "move_all", swap=swap)
    if verb == "destroy":
        return ["removal"] if effect.arg("target") and not _is_yours(_what(effect)) else []
    if verb == "destroy_all":
        # What it destroys is not in the block ("all Equipment attached to
        # it"): only backs up a tagged wipe (`CONFIRMS`).
        return ["wipe"]
    # Not `damage_all` or `sacrifice_all`: a block does not say whom they
    # hit - Snort's damage goes to the players, Animate Dead sacrifices its
    # own creature. The tags have the wipes they would find.
    if verb in ("damage", "fight"):
        return ["removal"] if effect.arg("target") else []
    if verb == "sacrifice":
        # A targeted edict: "target player sacrifices". One that names no
        # target (Grave Pact) has no player in its block; the tags have it.
        return ["removal"] if effect.arg("target") or _for_others(effect) else []
    if verb == "pump":
        return ["removal"] if effect.arg("target") and _negative(effect.arg("toughness")) \
            else []
    if verb == "pump_all":
        return ["wipe"] if _negative(effect.arg("toughness")) else []
    if verb == "put_counter":
        return ["removal"] if effect.arg("target") and effect.arg("counter") == "M1M1" \
            else []
    if verb == "put_counter_all":
        return ["wipe"] if effect.arg("counter") == "M1M1" else []
    if verb == "counter_spell":
        # Not a ward-like trigger on being targeted (Frost Titan), nor one on
        # a spell of yours (Blood Funnel, Brain Gorgers).
        event = ability.event
        if event is not None and (event.name == "becomes_target" or (
                event.name == "cast" and event.who != "opponent")):
            return []
        return ["counterspell"]
    if verb == "gain_control":
        return ["steal"] if effect.arg("target") else []
    if verb == "lose_life":
        return ["drain_payoff"] if effect.arg("who") != "you" and (
            effect.arg("target") or _for_others(effect)) else []
    if verb == "discard":
        return ["discard"] if effect.arg("target") or _for_others(effect) else []
    # Not `reduce_cost`: a block does not say whose spells get cheaper, and
    # most are the card's own (Blasphemous Act). The tags say the rest.
    if verb == "raise_cost":
        # On an instant or a sorcery the cost raised is its own (Aether Tide).
        return [] if kind in ("instant", "sorcery", "ritual") else ["tax"]
    if verb == b.KEYWORD:
        return ["lifegain"] if effect.arg("name") == _LIFELINK else []
    return []


#: Steps that only set up what the ability does: beside them a rider is
#: still the point (Ancient Copper Dragon rolls, then makes Treasure).
_BOOKKEEPING = frozenset({
    b.KEYWORD, b.CHOOSE, "choose_card", "choose_color", "choose_number", "choose_player",
    "choose_source", "choose_type", "choice", "roll_dice", "flip_coin", "repeat_each",
    "branch", "shuffle", "delayed_trigger", "immediate_trigger", "peek", "reveal_hand",
    "set_state",
})


#: Verbs whose block does not say whom they hit - only ``target`` - so their
#: role only backs up a tag and never adds one: damage to a player is no
#: removal (Lava Spike), -1/-1 counters on your own creature neither
#: (Channeler Initiate), nor destroying a permanent you own (Despotic
#: Scepter); a "destroy all" may mean Equipment (Eaten by Spiders).
CONFIRMS = frozenset({"destroy", "damage", "fight", "pump", "put_counter", "destroy_all",
                      "pump_all", "put_counter_all"})


def _adds(effect: b.Effect, ability: b.Ability) -> bool:
    """Whether the effect's role may add one the tags miss (`Label.adds`).
    A targeted draw may be an opponent's: "target opponent draws two"; one
    that replaces a draw gains no card (Chains of Mephistopheles)."""
    if effect.verb in CONFIRMS:
        return False
    if effect.verb == "draw":
        return not ((effect.arg("target") and not effect.arg("player"))
                    or ability.kind == b.REPLACEMENT)
    return True


def _main(effect: b.Effect) -> bool:
    """An effect that makes a rider beside it incidental."""
    return effect.verb not in RIDERS and effect.verb not in _BOOKKEEPING \
        and not (effect.verb in _BENEFITS and _for_others(effect))


def ability_labels(ability: b.Ability, kind: str, *, swap: bool = False) -> list[Label]:
    """The labels of one ability, each defining or incidental.

    ``swap``: the card gives up a land for the one it fetches (`swaps_land`).
    """
    if ability.kind == b.CAST:
        # Costs paid to cast: incidental, and no mechanic either.
        return []
    found: list[Label] = []
    atoms = {cost.atom for cost in ability.costs}
    if ability.kind in (b.ACTIVATED, b.MANA) and "sacrifice" in atoms and "tap" not in atoms:
        # Free and repeatable: what a player means by a sacrifice outlet.
        found.append(Label("sac_outlet", defining=False))
    event = ability.event
    if ability.kind == b.TRIGGERED and event is not None:
        if event.name in ("dies", "sacrificed") and (event.who != "self" or event.filter):
            found.append(Label("death_trigger", defining=False))
        if event.name == "life_gained":
            found.append(Label("lifegain", defining=False))
        if event.who == "opponent" and any(effect.arg("unless") for effect in ability.effects):
            found.append(Label("tax", defining=False))
    has_main = any(_main(effect) for effect in ability.effects)
    for effect in ability.effects:
        others = effect.verb in _BENEFITS and _for_others(effect)
        defining = not others and not (effect.verb in RIDERS and has_main)
        found += [Label(name, defining=defining, others=others, adds=_adds(effect, ability))
                  for name in _effect_labels(effect, ability, kind, swap)]
    return found


def swaps_land(abilities, kind: str) -> bool:
    """A land, or a card that sacrifices one: a land it fetches only
    replaces one. A land's mana is no ramp either (`_effect_labels`)."""
    return kind == "land" or any(
        cost.atom == "sacrifice_land" or (cost.atom == "sacrifice" and "land" in cost.types)
        for ability in abilities for cost in ability.costs)


def read_whole(abilities) -> bool:
    """Nothing on the card is outside the vocabulary or left unread."""
    return not any(
        ability.unread
        or any(cost.atom == b.UNMODELED for cost in ability.costs)
        or any(effect.verb == b.UNMODELED for effect in ability.effects)
        for ability in abilities)


# --- tempo --------------------------------------------------------------------------


def _rate(ability: b.Ability) -> str:
    event = ability.event
    if ability.kind == b.RESOLVES:
        return ONE_SHOT
    if ability.kind in (b.ACTIVATED, b.MANA):
        atoms = {cost.atom for cost in ability.costs}
        return ONE_SHOT if atoms & {"sacrifice_self", "exile_self", "exile_from_hand"} \
            else REPEATABLE
    if ability.kind in (b.TRIGGERED, b.REPLACEMENT):
        if event is not None and event.who == "self" and not event.filter \
                and event.name in ("enters", "dies"):
            return ONE_SHOT
        # Cycled, cast (Kozilek), or gone from where it was: once per card.
        if event is not None and event.who == "self" and event.name in ("cycled", "cast",
                                                                      "zone_change"):
            return ONE_SHOT
        return EVENT
    return REPEATABLE


def _needs(ability: b.Ability) -> str:
    # "Unless" asks an opponent - except a land's own "enters tapped unless
    # you pay 2 life" (Watery Grave).
    if (ability.event is not None and ability.event.who == "opponent") or (
            ability.kind != b.REPLACEMENT
            and any(effect.arg("unless") for effect in ability.effects)):
        return NEEDS_OPPONENT
    if {cost.atom for cost in ability.costs} & {"sacrifice", "tap_other"}:
        return NEEDS_BOARD
    return NEEDS_NOTHING


def tempo(abilities, kind: str = "") -> dict[str, dict]:
    """Per ability id: how often it happens and what it needs, and
    ``draws`` on an ability that draws cards (the engine's draw engine is
    one that happens again and again).

    A cast cost and a static ability that is only a keyword or only
    unmodelled say nothing about tempo and are left out.
    """
    found = {}
    for ability in abilities:
        if ability.kind == b.CAST:
            continue
        if ability.kind == b.STATIC and all(
                effect.verb in (b.KEYWORD, b.UNMODELED) for effect in ability.effects):
            continue
        found[ability.id] = {"rate": _rate(ability), "needs": _needs(ability)}
        if any(label.name == "draw" and label.defining
               for label in ability_labels(ability, kind)):
            found[ability.id]["draws"] = True
    return found


# --- without blocks -----------------------------------------------------------------


def reader_labels(facts, kind: str) -> list[Label]:
    """Labels off our own reader's fields, for a card without blocks.

    ``facts`` is anything with `DerivedProfile`'s field names. All defining:
    the reader says what a card does, not how much it matters.
    """
    names: list[str] = []
    if getattr(facts, "draws_cards", None):
        names.append("draw")
    treasures = getattr(facts, "treasures", 0) or any(
        (trigger or {}).get("treasures") for trigger in getattr(facts, "triggers", None) or ()
        if isinstance(trigger, dict))
    if treasures:
        names += ["treasure", "ramp"]
    land = getattr(facts, "land_search", None)
    if land:
        names.append("land_search")
        if land.get("battlefield"):
            names.append("ramp")
    if getattr(facts, "opponent_life_loss", None):
        names.append("drain_payoff")
    if getattr(facts, "produces_mana", False) and kind != "land":
        names.append("ramp")
    return [Label(name, defining=True) for name in names]


# --- the card -----------------------------------------------------------------------


def derive(kind: str, abilities, role_tags, facts=None) -> Categories:
    """A card's roles, mechanics, tempo and review notes.

    Args:
        kind: `DerivedProfile.kind`.
        abilities: the card's blocks; empty for a card without them.
        role_tags: `DerivedProfile.role_tags`, the community's roles.
        facts: the card's profile, read when it has no blocks.
    """
    abilities = tuple(abilities or ())
    if abilities:
        swap = swaps_land(abilities, kind)
        labels = [label for ability in abilities
                  for label in ability_labels(ability, kind, swap=swap)]
    else:
        labels = reader_labels(facts, kind) if facts is not None else []

    tagged = {role for role in role_tags if role in ROLES}
    defining = {label.name for label in labels if label.defining and label.name in ROLES}
    incidental = {label.name for label in labels
                  if not label.defining and label.name in ROLES} - defining
    adding = {label.name for label in labels
              if label.defining and label.adds and label.name in ROLES}

    roles = tagged | adding
    review = []
    whole = read_whole(abilities)
    for role in sorted(tagged & incidental):
        if abilities and whole:
            roles.discard(role)
            review.append(f"dropped:{role}")
        else:
            review.append(f"incidental:{role}")

    ranked = sorted(roles, key=lambda role: (-((role in tagged) + (role in defining)),
                                             ROLE_ORDER.index(role)))
    mechanics = {tag for tag in role_tags if tag in TAG_MECHANICS} | {
        label.name for label in labels if label.name in MECHANICS and not label.others}
    return Categories(
        primary=ranked[0] if ranked else "",
        secondary=ranked[1] if len(ranked) > 1 else "",
        mechanics=tuple(name for name in MECHANICS if name in mechanics),
        tempo=tempo(abilities, kind),
        review=tuple(review),
    )
