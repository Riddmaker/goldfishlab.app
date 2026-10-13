"""The engine's building blocks a person may correct, one override key each.

Until the editor rebuild only fourteen pre-P19 judgements were editable
(:mod:`simulations.annotations`). Everything P19 taught the reader - land
searches, triggers, combat, tutors with a filter, mana kept as counters - was
derived and could not be corrected, and "Taps for" switched every mana reading
of the card off at once.

Each building block (a *primitive*) is now one key of `CardAnnotation.
overrides`, and its value has **exactly the JSON shape the reader stores** on
`DerivedProfile` - so the adapter plays an override through the code that
already plays a reading (`adapter.effective`), and nothing about a primitive is
written twice. `None` is a statement: "this card does not do that".

Merging stays per key, narrowest scope wins: a deck can switch one primitive
off without restating the rest.

What arrives here comes from a form, so it is checked twice: structurally
(:func:`validate` - known keys, bounded numbers, short words, short lists) and
by building the card with it (`services.save_annotation` runs the adapter once
before anything is stored). A value the engine cannot play is refused at the
form, never discovered in a worker.
"""

import re
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Primitive:
    """One correctable building block."""

    key: str
    label: str
    help: str
    #: The `DerivedProfile` fields it replaces. One for most; the tutor's
    #: four are kept as one object (`to`, `count`, `kind` and the filter).
    fields: tuple[str, ...]
    #: What the field holds when the primitive is switched off.
    off: object = None
    #: The keys its value (or each item of a list) may carry.
    keys: frozenset[str] = frozenset()
    #: A list of items (triggers), rather than one object.
    many: bool = False


_LAND_SEARCH_KEYS = frozenset({
    "battlefield", "hand", "tapped", "basic", "types", "life", "when", "sacrifice",
    "untap_at", "cost", "taps", "share_type", "each", "condition", "sacrifices_land",
    "land_cost_types", "sacrifice_other", "discard", "also", "power_at", "power_count",
    "by_power", "repeat", "tempting", "by_x", "reveal", "x_max",
})
_TRIGGER_KEYS = frozenset({
    "event", "filter", "treasures", "spawn", "mana", "color", "draw", "life", "tax", "counter",
})
_COMBAT_KEYS = frozenset({
    "triggers", "equip", "aura", "germ", "treasure_bonus", "vehicle",
    # One trigger in combat.
    "event", "who", "types", "treasures", "mana", "search", "untapped", "flicker",
    "most_life", "extra",
})
_TUTOR_KEYS = frozenset({
    "to", "count", "kind", "types", "max_mv", "color", "plus_sacrificed", "land_tapped",
    "gifts", "random_discard", "delirium_any", "upkeep", "exiled", "symmetry", "opponents",
})
_CONDITION_KEYS = frozenset({
    "kind", "types", "count", "life", "at_least", "other", "basic", "type", "legendary",
})
_MANA_RULE_KEYS = frozenset({
    "rule", "subtype", "produces", "activation", "color", "times", "with_counter", "flip",
    "enchants", "stat", "of", "per", "once", "condition", "amount", "count", "spells",
    "types", "filter", "other", "greatest", "chosen", "subtypes",
})
_STORE_KEYS = frozenset({
    "use", "kind", "per_use", "amount", "produces", "start", "start_from", "kick", "grows",
    "empty", "target", "once",
})
_GROWTH_KEYS = frozenset({
    "enters", "enters_x", "grows", "adapt", "bonus", "puts", "draws_on_greatest", "look",
})
_TAP_MANA_KEYS = frozenset({
    "types", "filter", "amount", "produces", "spend_only", "token",
})
_ALTAR_KEYS = frozenset({
    "sacrifice", "filter", "amount", "produces", "taps", "life", "other",
})
_COST_KEYS = frozenset({
    "sacrifice", "filter", "life", "life_x", "discard", "mana", "exile_from_graveyard", "count",
})

PRIMITIVES = (
    Primitive("land_search", _("Land search"),
              _("Lands it puts onto the battlefield, and when."),
              ("land_search",), keys=_LAND_SEARCH_KEYS),
    Primitive("tutor", _("Search"),
              _("A card it finds in your library, and where that card goes."),
              ("tutor_to", "tutor_count", "tutor_kind", "tutor_filter"), keys=_TUTOR_KEYS),
    Primitive("tapped_unless", _("Enters tapped unless"),
              _("When a land that enters tapped comes in untapped."),
              ("tapped_unless",), keys=_CONDITION_KEYS),
    Primitive("mana_rule", _("Mana that counts"),
              _("Mana that grows with the board, adds to other sources or is granted to "
                "them: Gaea's Cradle, Caged Sun, Cryptolith Rite."),
              ("mana_rule",), keys=_MANA_RULE_KEYS),
    Primitive("triggers", _("Triggers"),
              _("Treasure, mana or cards it makes when something happens: a land enters, "
                "a spell is cast, a creature dies."),
              ("triggers",), off=[], keys=_TRIGGER_KEYS, many=True),
    Primitive("combat", _("Combat"),
              _("Treasure, mana or lands it makes as creatures attack or deal combat damage; "
                "an Equipment's cost."),
              ("combat",), keys=_COMBAT_KEYS),
    Primitive("mana_store", _("Mana kept as counters"),
              _("Counters, energy or a Food it spends for mana: Pentad Prism, storage lands."),
              ("mana_store",), keys=_STORE_KEYS),
    Primitive("growth", _("+1/+1 counters"),
              _("Counters it enters with or puts on creatures, and what grows with them."),
              ("growth",), keys=_GROWTH_KEYS),
    Primitive("tap_mana", _("Mana from tapping others"),
              _("Mana for tapping other permanents: Springleaf Drum, Urza, Relic of Legends."),
              ("tap_mana",), keys=_TAP_MANA_KEYS),
    Primitive("sacrifice_mana", _("Mana for a sacrifice"),
              _("Mana for sacrificing a permanent: Ashnod's Altar, Phyrexian Tower."),
              ("sacrifice_mana",), keys=_ALTAR_KEYS),
    Primitive("additional_cost", _("Additional cost"),
              _("What casting it costs beside mana: a sacrifice, life, a discard."),
              ("additional_cost",), keys=_COST_KEYS, many=True),
    Primitive("treasures", _("Treasure as it resolves"),
              _("Treasure tokens it makes as it resolves or enters."),
              ("treasures",), off=0),
    Primitive("mox", _("Mox"),
              _("Mox Diamond discards a land to enter; Chrome Mox exiles a card for its "
                "colours."),
              ("mox",), off=""),
)

BY_KEY = {primitive.key: primitive for primitive in PRIMITIVES}
KEYS = frozenset(BY_KEY)

#: Words a value may hold: lower-case names, colour letters, mana costs.
_WORD = re.compile(r"^[\w{}+\-/' ]{0,40}$")
#: The largest number any field takes; everything a card does fits under it.
_MOST = 99
#: The most entries a list holds; also what a patch may number (`simulations.patches`).
MAX_ITEMS = 10
#: Nesting a value may have: a combat object holds a list of triggers, a
#: trigger a list of types.
_DEPTH = 4


class PrimitiveError(ValueError):
    """A value the editor cannot store."""


def validate(key: str, value):
    """Check one primitive's value; return it unchanged, or raise.

    Structure only: which keys, which types, how large. Whether the engine
    can play it is the adapter's to say, by building the card.
    """
    primitive = BY_KEY.get(key)
    if primitive is None:
        raise PrimitiveError(f"{key!r} is not a primitive")
    if value is None:
        return None
    if key == "treasures":
        _number(value, key)
        return value
    if key == "mox":
        if value not in ("", "discard_land", "imprint"):
            raise PrimitiveError(f"mox: {value!r} is not a kind of Mox")
        return value
    if primitive.many:
        if not isinstance(value, list) or len(value) > MAX_ITEMS:
            raise PrimitiveError(f"{key}: a list of at most {MAX_ITEMS} entries")
        for item in value:
            _object(item, primitive.keys, key, 1)
        return value
    _object(value, primitive.keys, key, 1)
    if key == "tutor" and value.get("to") not in ("hand", "graveyard", "battlefield", "top"):
        raise PrimitiveError("tutor: 'to' must be hand, graveyard, battlefield or top")
    return value


def validate_fields(key: str, fields) -> dict:
    """Check some fields of one primitive's object - a patch's `set`
    (`simulations.patches`); return them unchanged, or raise."""
    primitive = BY_KEY.get(key)
    if primitive is None or not primitive.keys:
        raise PrimitiveError(f"{key!r} has no fields")
    _object(fields, primitive.keys, key, 1)
    return fields


def _object(value, keys: frozenset[str], where: str, depth: int) -> None:
    if not isinstance(value, dict):
        raise PrimitiveError(f"{where}: an object is expected")
    unknown = set(value) - keys
    if unknown:
        raise PrimitiveError(f"{where}: unknown keys {sorted(unknown)}")
    for name, item in value.items():
        _any(item, keys, f"{where}.{name}", depth + 1)


def _any(value, keys: frozenset[str], where: str, depth: int) -> None:
    if depth > _DEPTH:
        raise PrimitiveError(f"{where}: nested too deeply")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        _number(value, where)
    elif isinstance(value, str):
        if not _WORD.match(value):
            raise PrimitiveError(f"{where}: {value!r} is not a word the engine reads")
    elif isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise PrimitiveError(f"{where}: at most {MAX_ITEMS} entries")
        for item in value:
            _any(item, keys, where, depth + 1)
    elif isinstance(value, dict):
        # Nested objects: a combat trigger, a "produces" mapping, a condition.
        for name, item in value.items():
            if not isinstance(name, str) or not _WORD.match(name):
                raise PrimitiveError(f"{where}: {name!r} is not a key")
            _any(item, keys, f"{where}.{name}", depth + 1)
    else:
        raise PrimitiveError(f"{where}: {type(value).__name__} is not stored")


def _number(value, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= _MOST:
        raise PrimitiveError(f"{where}: a whole number from 0 to {_MOST}")


def profile_fields(key: str, value) -> dict:
    """What a primitive's override makes the `DerivedProfile` fields read."""
    primitive = BY_KEY[key]
    if key == "tutor":
        if value is None:
            return {"tutor_to": "", "tutor_count": None, "tutor_kind": "", "tutor_filter": None}
        limit = {name: item for name, item in value.items()
                 if name not in ("to", "count", "kind")}
        if value["to"] == "battlefield":
            limit = {"color": "", "max_mv": None} | limit
        return {"tutor_to": value["to"], "tutor_count": value.get("count", 1),
                "tutor_kind": value.get("kind", ""), "tutor_filter": limit or None}
    return {primitive.fields[0]: primitive.off if value is None else value}


def reading_of(key: str, profile):
    """The primitive as the reader read it, in the override's shape: what the
    editor starts from, and what "back to the card data" restores."""
    if profile is None:
        return None
    if key == "tutor":
        if not profile.tutor_to or profile.tutor_count is None:
            return None
        return {"to": profile.tutor_to, "count": profile.tutor_count,
                **({"kind": profile.tutor_kind} if profile.tutor_kind else {}),
                **(profile.tutor_filter or {})}
    value = getattr(profile, BY_KEY[key].fields[0], None)
    return value or None


def keep_taps_for_scope(overrides: dict, profile) -> dict:
    """An annotation written before the editor rebuild, read as it was then.

    "Taps for" used to switch every mana reading of the card off; since the
    rebuild it replaces the card's own tap ability only. An annotation that
    says "Taps for" gets the other mana readings switched off explicitly, so
    nothing it decided moves: an altar, tapping others, a Mox, counters, and
    the mana (not the Treasure or the card) of a trigger.
    """
    says = any(key in overrides for key in ("mana_produces", "mana_black", "mana_colorless"))
    if not says or profile is None:
        return overrides
    kept = dict(overrides)
    for key in ("sacrifice_mana", "tap_mana", "mana_store"):
        if getattr(profile, key, None) and key not in kept:
            kept[key] = None
    if getattr(profile, "mox", "") and "mox" not in kept:
        kept["mox"] = ""
    triggers = getattr(profile, "triggers", None) or []
    if any(read.get("mana") for read in triggers) and "triggers" not in kept:
        kept["triggers"] = [{name: item for name, item in read.items()
                             if name not in ("mana", "color")} for read in triggers]
    return kept


# --- the editor's fields (E2) ------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One box of a primitive's form.

    `kind`: ``int`` (from `low` to `high`), ``optint`` (blank is "no limit"),
    ``bool``, ``choice`` (one of `choices`), ``multi`` (any of `choices`),
    ``words`` (comma-separated lower-case words: types, subtypes), ``mana``
    ("2B G"; blank is "any colour of the deck") or ``cost`` ("{2}{B}").
    """

    name: str
    kind: str
    label: str
    help: str = ""
    choices: tuple = ()
    low: int = 0
    high: int = 10
    #: Shown under "More" rather than at once.
    more: bool = False


_LAND_TYPE_CHOICES = (("plains", _("Plains")), ("island", _("Island")),
                      ("swamp", _("Swamp")), ("mountain", _("Mountain")),
                      ("forest", _("Forest")))
_PERMANENT_CHOICES = (("creature", _("Creature")), ("artifact", _("Artifact")),
                      ("land", _("Land")), ("enchantment", _("Enchantment")))
_COLOR_CHOICES = (("", _("Any")), ("W", _("White")), ("U", _("Blue")), ("B", _("Black")),
                  ("R", _("Red")), ("G", _("Green")))
_KIND_CHOICES = (("", _("Any card")), ("creature", _("Creature")), ("land", _("Land")),
                 ("artifact", _("Artifact")), ("enchantment", _("Enchantment")),
                 ("instant", _("Instant")), ("sorcery", _("Sorcery")),
                 ("planeswalker", _("Planeswalker")))

FIELDS = {
    "land_search": (
        Field("when", "choice", _("When"), choices=(
            ("cast", _("As it is cast")), ("enters", _("As it enters")),
            ("play", _("As it is played (a fetch land)")),
            ("activate", _("When activated, with spare mana")),
            ("opponents", _("In each opponent's end step"))), high=0),
        Field("battlefield", "int", _("Lands onto the battlefield"), high=10),
        Field("hand", "int", _("Lands to hand after them"), high=5),
        Field("tapped", "bool", _("They enter tapped")),
        Field("basic", "bool", _("Basic lands only")),
        Field("types", "multi", _("Land types it may find"),
              _("None ticked: any land."), choices=_LAND_TYPE_CHOICES),
        Field("condition", "choice", _("Only if"), choices=(
            ("", _("Always")),
            ("opponent_more_lands", _("An opponent controls more lands (assumed: one a turn "
                                      "each)")))),
        Field("sacrifice", "bool", _("The card itself is sacrificed"), more=True),
        Field("sacrifices_land", "bool", _("A land of yours is sacrificed first"), more=True),
        Field("cost", "cost", _("Mana it costs to activate"), _("Such as {2}. Blank: none."),
              more=True),
        Field("taps", "bool", _("Activating it taps the card"), more=True),
        Field("life", "int", _("Life paid"), high=20, more=True),
        Field("by_power", "bool", _("As many as the greatest power among your creatures"),
              more=True),
        Field("repeat", "int", _("Searches again this often while the condition holds"),
              high=3, more=True),
        Field("tempting", "int", _("One more for each opponent assumed to search too"),
              high=3, more=True),
    ),
    "tutor": (
        Field("to", "choice", _("Where the card goes"), choices=(
            ("hand", _("To hand")), ("top", _("On top of the library")),
            ("battlefield", _("Onto the battlefield")), ("graveyard", _("To the graveyard")))),
        Field("count", "int", _("How many cards"), low=1, high=10),
        Field("kind", "choice", _("What it may find"), choices=_KIND_CHOICES),
        Field("types", "words", _("Or any of these types"),
              _("Card types, subtypes or creature types, comma-separated: aura, equipment, "
                "elf.")),
        Field("max_mv", "optint", _("Mana value at most"), _("Blank: no limit."), high=20),
        Field("color", "choice", _("Of this colour"), choices=_COLOR_CHOICES, more=True),
        Field("land_tapped", "bool", _("A land it finds goes onto the battlefield tapped"),
              more=True),
        Field("random_discard", "int", _("Cards discarded at random afterwards"), high=3,
              more=True),
        Field("delirium_any", "bool", _("Any card with delirium"), more=True),
    ),
    "sacrifice_mana": (
        Field("sacrifice", "multi", _("What it sacrifices"), choices=_PERMANENT_CHOICES),
        Field("amount", "int", _("Mana for each sacrifice"), low=1, high=10),
        Field("produces", "mana", _("Of these colours"),
              _("Such as C or BB. Blank: any colour of the deck.")),
        Field("taps", "bool", _("Using it taps the card")),
        Field("life", "int", _("Life paid each time"), high=10, more=True),
    ),
    "tap_mana": (
        Field("types", "multi", _("What it taps"), choices=_PERMANENT_CHOICES),
        Field("amount", "int", _("Mana for each one tapped"), low=1, high=10),
        Field("produces", "mana", _("Of these colours"),
              _("Such as U or C. Blank: any colour of the deck.")),
    ),
    "treasures": (
        Field("count", "int", _("Treasure tokens"), low=1, high=10),
    ),
    "mox": (
        Field("kind", "choice", _("How it enters"), choices=(
            ("discard_land", _("Discards a land card (Mox Diamond)")),
            ("imprint", _("Exiles a coloured card for its colours (Chrome Mox)")))),
    ),
}

#: What a primitive the reader did not find starts from when added by hand.
DEFAULTS = {
    "land_search": {"battlefield": 1, "hand": 0, "tapped": True, "basic": True, "types": [],
                    "life": 0, "when": "cast", "sacrifice": False, "untap_at": 0, "cost": "",
                    "taps": False, "share_type": False, "each": False, "condition": "",
                    "sacrifices_land": False, "land_cost_types": [], "sacrifice_other": [],
                    "discard": 0},
    "tutor": {"to": "hand", "count": 1},
    "sacrifice_mana": {"sacrifice": ["creature"], "filter": "", "amount": 1, "produces": None,
                       "taps": False},
    "tap_mana": {"types": ["creature"], "filter": "", "amount": 1, "produces": None,
                 "spend_only": None, "token": 0},
    "treasures": 1,
    "mox": "discard_land",
}

#: Primitives with a form; the rest can be switched off or reset only.
EDITABLE = frozenset(FIELDS)


# --- lists: one entry at a time (E3) ------------------------------------------------

#: The entries' boxes, for the primitives that are lists: a trigger, a
#: trigger in combat (with the card's combat settings beside it), one way to
#: pay an additional cost.
ITEM_FIELDS = {
    "triggers": (
        Field("event", "choice", _("When"), choices=(
            ("landfall", _("A land of yours enters")),
            ("cast", _("You cast a spell")),
            ("enters", _("It or another creature of a type enters")),
            ("dies", _("A creature of yours dies")),
            ("sacrificed", _("You sacrifice an artifact")),
            ("upkeep", _("Your upkeep")),
            ("main", _("Your first main phase")),
            ("resolves", _("As it resolves")),
            ("opponents", _("Once a round, an opponent does not pay")))),
        Field("filter", "word", _("Narrowed to"),
              _("Blank for none. For a spell: instant_sorcery, colorless or second; for a "
                "creature dying: another; as it resolves: per_land; for a creature entering: "
                "its type, such as dragon.")),
        Field("treasures", "int", _("Treasure tokens"), high=5),
        Field("mana", "int", _("Mana"), high=10),
        Field("color", "choice", _("Of colour"), choices=(
            ("", _("None")), ("any", _("Any colour of the deck")), ("W", _("White")),
            ("U", _("Blue")), ("B", _("Black")), ("R", _("Red")), ("G", _("Green")),
            ("C", _("Colourless")))),
        Field("draw", "int", _("Cards drawn"), high=3),
        Field("life", "int", _("Life it costs"), high=5, more=True),
        Field("counter", "int", _("+1/+1 counters on it"), high=3, more=True),
    ),
    "combat": (
        Field("event", "choice", _("When"), choices=(
            ("attacks", _("As it attacks")),
            ("damage", _("As it deals combat damage to a player")))),
        Field("who", "choice", _("Whose attack"), choices=(
            ("self", _("This creature")), ("equipped", _("The creature it equips")),
            ("enchanted", _("The creature it enchants")),
            ("any", _("Any of your creatures, once a combat")),
            ("modified", _("Each modified creature of yours")),
            ("commander", _("Your commander")), ("curse", _("You attack the enchanted player")),
            ("total", _("Every point of combat damage")))),
        Field("types", "words", _("Of these creature types"), _("Blank: any creature."),
              more=True),
        Field("treasures", "int", _("Treasure tokens"), high=5),
        Field("mana", "pips", _("Mana kept to the second main phase"),
              _("Letters, such as RRRGGG. Blank: none.")),
        Field("search", "bool", _("Finds a basic land")),
        Field("untapped", "bool", _("The land enters untapped"), more=True),
        Field("equip", "cost", _("Equip cost"), _("For an Equipment, such as {2}."), more=True),
        Field("aura", "bool", _("An Aura that goes on a creature"), more=True),
        Field("treasure_bonus", "int", _("Each Treasure makes this much more"), high=2,
              more=True),
    ),
    "additional_cost": (
        Field("sacrifice", "multi", _("Sacrifice one of"), choices=_PERMANENT_CHOICES),
        Field("life", "int", _("Life"), high=20),
        Field("life_x", "bool", _("Pay X life (paid with X = 0)")),
        Field("discard", "int", _("Cards discarded"), high=3),
        Field("mana", "cost", _("Mana on top"), _("Such as {2}. Blank: none.")),
        Field("exile_from_graveyard", "choice", _("Exile from your graveyard"), choices=(
            ("", _("Nothing")), ("creature", _("A creature card")),
            ("artifact", _("An artifact card")), ("land", _("A land card")))),
        Field("filter", "word", _("The sacrifice must also be"),
              _("Blank for anything. A colour letter, legendary, or a creature type."),
              more=True),
        Field("count", "optint", _("How many are sacrificed"), _("Blank or 1: one."), high=3,
              more=True),
    ),
}

#: The card's combat settings, not one trigger's: carried in the combat form.
COMBAT_CARD_FIELDS = frozenset({"equip", "aura", "treasure_bonus"})

#: What a new entry starts from.
ITEM_DEFAULTS = {
    "triggers": {"event": "landfall", "filter": "", "mana": 1, "color": "any"},
    "combat": {"event": "attacks", "who": "self", "treasures": 1},
    "additional_cost": {"sacrifice": ["creature"], "filter": "", "life": 0, "life_x": False,
                        "discard": 0, "mana": "", "exile_from_graveyard": ""},
}
