"""The card page's primitives: what the engine plays, block by block (editor E2, E3).

Each block is one primitive of :mod:`simulations.primitives` - a land search,
a search, an altar - with what the engine makes of it in words, who decided
it (the card, you, a built-in default, or "off"), and a form where the
primitive has one. The form is built from the primitive's `Field`s, so the
registry is the only place a primitive's boxes are described.

A form edits one primitive and never the whole: what it does not show of a
reading (a fetch land's `untap_at`) is carried through from the reading or
the stored override, so editing the count of a search cannot lose its
condition. A primitive that is a list - triggers, triggers in combat, the
ways to pay an additional cost - is edited one entry at a time (E3), and the
other entries stay as they are.

Some primitives have boxes that depend on one of their fields (card model
phase 2, PR 7; `primitives.VARIANTS`): which condition lets a land enter
untapped, which rule a mana rule follows. That field is picked before the
form - the *variant* - and is never one of its boxes.
"""

import re
from dataclasses import dataclass, field
from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext, ngettext

from cards import categories as card_categories
from simulation.cards import AdditionalCost
from simulation.manacost import COLORLESS
from simulation.rules.selector import wanted_of
from simulations import primitives
from simulations.annotations import (
    CATEGORY_CHOICES,
    CATEGORY_KEYS,
    ROLE_NAMES,
    ManaTextError,
    format_mana,
    parse_mana,
)
from simulations.engine import adapter

#: The primitives edited one entry at a time.
LISTS = frozenset(primitives.ITEM_FIELDS)


@dataclass(frozen=True)
class Block:
    """One primitive of one card, as the card page shows it."""

    key: str
    label: str
    help: str
    text: str
    #: ``card`` (the reader), ``deck``, ``user``, ``builtin`` or ``off``.
    status: str
    editable: bool
    #: The scope that set it, when one did: ``deck``, ``user`` or ``builtin``.
    scope: str = ""
    #: "Back to the card data" changes something in the page's scope: only
    #: the scope that set it can take it back.
    resettable: bool = False
    #: A list's entries, in words, in their stored order (E3).
    entries: tuple[str, ...] = field(default_factory=tuple)
    #: Card model phase 2, "please check": ``changed`` when the reading moved
    #: under the correction, ``gone`` when it no longer has the block (or, in
    #: a list, an entry that was corrected); empty when nothing is to check.
    check: str = ""
    #: The scope of the correction to check. Only that scope's page can keep
    #: it or take it back, as with `resettable`.
    check_scope: str = ""
    #: The page's scope is the check's: its buttons are shown here.
    check_here: bool = False

    @property
    def is_list(self) -> bool:
        return self.key in LISTS

    @property
    def status_label(self) -> str:
        return {
            "card": gettext("From the card"),
            "deck": gettext("Yours, this deck"),
            "user": gettext("Yours, all decks"),
            "builtin": gettext("Built in"),
            "off": gettext("Off"),
        }[self.status]


def blocks(reading, overrides: dict, scopes: dict,
           scope: str = "deck") -> tuple[list[Block], list[Block]]:
    """The card's primitives, and the ones that can be added.

    A primitive is on the page when the reader read it or anybody set it;
    the others are offered under "Add" where a form can describe them.
    """
    profile = getattr(reading.oracle_card, "profile", None)
    checks = _checks_by_key(reading)
    shown, addable = [], []
    for primitive in primitives.PRIMITIVES:
        key = primitive.key
        check = checks.get(key)
        said = key in overrides
        read = primitives.reading_of(key, profile)
        if said and _is_off(key, overrides[key]):
            status = "off"
        elif said:
            status = scopes.get(key, "deck")
        else:
            status = "card"
        value = overrides[key] if said else read
        # Switched off, the form starts from the reading (`current`).
        editable = (can_edit(key, value) if status != "off"
                    else can_add(key) or can_edit(key, read)) or key in LISTS
        block = Block(
            key=key, label=str(primitive.label), help=str(primitive.help),
            text=(gettext("switched off") if status == "off"
                  else "" if key in LISTS else _text(key, reading)),
            status=status, editable=editable,
            scope=scopes.get(key, "deck") if said else "",
            resettable=said and scopes.get(key, "deck") == scope,
            entries=(tuple(_entry_text(key, entry, reading.oracle_card)
                           for entry in entries_of(key, value))
                     if key in LISTS and status != "off" else ()),
            check=check.status if check and (said or key in LISTS) else "",
            check_scope=check.scope if check else "",
            check_here=bool(check) and check.scope == scope,
        )
        if said or read is not None:
            shown.append(block)
        elif can_add(key) or key in LISTS:
            addable.append(block)
    return shown, addable


def can_add(key: str) -> bool:
    """Whether a person may write this primitive where the card has none."""
    variants = primitives.VARIANTS.get(key)
    if variants is not None:
        return bool(variants.defaults)
    return key in primitives.FIELDS


def can_edit(key: str, value) -> bool:
    """Whether a form describes this value of the primitive."""
    variants = primitives.VARIANTS.get(key)
    if variants is not None:
        return bool(variants.specs.get(variants.of(value)))
    return key in primitives.FIELDS


def variant_choices(key: str) -> list[tuple[str, str]]:
    """The variants a person may pick for `key`: (value, label)."""
    variants = primitives.VARIANTS.get(key)
    if variants is None:
        return []
    return [(name, str(variants.labels.get(name, name))) for name in variants.defaults]


def starting_value(key: str, value, variant: str = ""):
    """What a form for `key` edits: `value`, or - where a variant other than
    its own is picked, or it has none - that variant as it starts.

    Returns `None` when a variant has to be picked first.
    """
    variants = primitives.VARIANTS.get(key)
    if variants is None:
        return value
    own = variants.of(value)
    if variant in variants.defaults and variant != own:
        return dict(variants.defaults[variant])
    return value if variants.specs.get(own) else None


@dataclass(frozen=True)
class Gone:
    """A correction of one block the card data no longer has: not played,
    and not on the page as a block, so it gets a line of its own."""

    key: str
    label: str
    scope: str
    #: The page's scope is the correction's: "Discard it" is shown here.
    here: bool
    #: A form can describe the block: "Set it again" is offered.
    editable: bool


def gone(reading, overrides: dict, scope: str = "deck") -> list[Gone]:
    """The owner's corrections whose single block is gone (`blocks` shows
    the lists, whose other entries are still there)."""
    return [
        Gone(key=key, label=str(primitives.BY_KEY[key].label), scope=check.scope,
             here=check.scope == scope, editable=can_add(key))
        for key, check in _checks_by_key(reading).items()
        if check.status == "gone" and key not in LISTS and key not in overrides
    ]


@dataclass(frozen=True)
class Category:
    """One category of one card, as the "Categories" block shows it."""

    key: str
    label: str
    #: ``card`` (derived), ``deck``, ``user`` or ``builtin``: who put it there,
    #: or for a removed one who took it away.
    status: str
    is_role: bool
    main: bool = False
    #: Derived, but not counted: offered back.
    removed: bool = False

    @property
    def status_label(self) -> str:
        if self.removed:
            return {"deck": gettext("Removed by you, this deck"),
                    "user": gettext("Removed by you, all decks"),
                    "builtin": gettext("Removed, built in")}.get(self.status, "")
        return Block(key=self.key, label="", help="", text="", status=self.status,
                     editable=False).status_label


@dataclass(frozen=True)
class CategoryBlock:
    """What a card counts as in the statistics (card model phase 3, PR 10)."""

    lines: tuple[Category, ...]
    #: ``(key, label)`` of every category it could be added to.
    addable: tuple[tuple[str, str], ...]
    #: The page's scope holds a correction: "Back to the card data" is shown.
    resettable: bool
    #: The profile is derived into categories; until then there is no main role.
    derived: bool


def categories_block(reading, overrides: dict, scopes: dict,
                     scope: str = "deck") -> CategoryBlock:
    """The card's categories, each with who decided it, and the ones it
    could be added to. The main role first, then the vocabulary's order.

    A category the card data gives but a correction took away is shown too,
    to be put back.
    """
    profile = getattr(reading.oracle_card, "profile", None)
    derived = adapter.derived_categories(profile) if profile is not None else frozenset()
    current = reading.card.categories
    added = set(overrides.get("category_add") or ())
    order = card_categories.ROLES + card_categories.MECHANICS

    def status(key: str) -> str:
        if key in added:
            return scopes.get("category_add", adapter.DECK_SCOPE)
        if key not in derived:
            return scopes.get("category_primary", adapter.DECK_SCOPE)
        return "card"

    def label(key: str) -> str:
        return gettext(ROLE_NAMES[key]) if key in ROLE_NAMES else key.replace("_", " ")

    def rank(key: str) -> tuple:
        return (key != reading.primary, order.index(key) if key in order else len(order), key)

    lines = [Category(key=key, label=label(key), status=status(key),
                      is_role=key in card_categories.ROLES, main=key == reading.primary)
             for key in sorted(current, key=rank)]
    lines += [Category(key=key, label=label(key),
                       status=scopes.get("category_remove", adapter.DECK_SCOPE),
                       is_role=key in card_categories.ROLES, removed=True)
              for key in sorted(derived - current, key=rank)]
    return CategoryBlock(
        lines=tuple(lines),
        addable=tuple((key, str(name)) for key, name in CATEGORY_CHOICES
                      if key not in current | derived),
        resettable=any(scopes.get(key) == scope for key in CATEGORY_KEYS if key in overrides),
        derived=bool(getattr(profile, "categories_version", 0)),
    )


def _checks_by_key(reading) -> dict:
    """One check per primitive: ``changed`` wins over ``gone`` for a list
    that has both, since the block is still on the page."""
    found: dict = {}
    for check in getattr(reading, "checks", ()):
        if check.key not in found or check.status == "changed":
            found[check.key] = check
    return found


def _is_off(key: str, value) -> bool:
    return value is None or value == primitives.BY_KEY[key].off


def current(key: str, overrides: dict, profile):
    """What a form for `key` starts from: the stored override, the reading,
    or the primitive's default when neither has one."""
    if key in overrides and not _is_off(key, overrides[key]):
        return overrides[key]
    read = primitives.reading_of(key, profile)
    if read is not None:
        return read
    return primitives.DEFAULTS.get(key)


# --- the form ------------------------------------------------------------------------


def specs_of(key: str, item: bool, value=None) -> tuple:
    """A form's boxes: for a variant primitive, those of `value`'s variant."""
    if item:
        return primitives.ITEM_FIELDS[key]
    variants = primitives.VARIANTS.get(key)
    if variants is not None:
        return variants.specs.get(variants.of(value), ())
    return primitives.FIELDS[key]


def split(form, specs) -> tuple[list, list]:
    """The form's boxes shown at once, and the ones under "More"."""
    more = {spec.name for spec in specs if spec.more}
    return ([bound for bound in form if bound.name not in more],
            [bound for bound in form if bound.name in more])


def form_class(specs):
    """A Django form built from `Field`s."""
    return type("PrimitiveForm", (forms.Form,),
                {spec.name: _form_field(spec) for spec in specs})


def _form_field(spec: primitives.Field):
    common = {"label": spec.label, "help_text": spec.help, "required": False}
    if spec.kind == "int":
        return forms.IntegerField(min_value=spec.low, max_value=spec.high, **common)
    if spec.kind == "optint":
        return forms.IntegerField(min_value=0, max_value=spec.high, **common)
    if spec.kind == "bool":
        return forms.BooleanField(**common)
    if spec.kind == "choice":
        return forms.ChoiceField(choices=spec.choices, **common)
    if spec.kind == "multi":
        return forms.MultipleChoiceField(choices=spec.choices,
                                         widget=forms.CheckboxSelectMultiple, **common)
    if spec.kind == "colour":
        return forms.ChoiceField(choices=spec.choices, **common)
    return forms.CharField(max_length=40, **common)


def initial(key: str, value) -> dict:
    """A stored value as the form's boxes."""
    if key == "treasures":
        return {"count": value}
    if key == "mox":
        return {"kind": value}
    return _initial(specs_of(key, False, value), value or {})


def _initial(specs, value: dict) -> dict:
    found = {}
    for spec in specs:
        item = value.get(spec.name, spec.default)
        if spec.kind == "optint" and not isinstance(item, int):
            item = None
        elif spec.kind == "words":
            item = ", ".join(item or ())
        elif spec.kind == "mana":
            item = format_mana(item) if item else ""
        elif spec.kind == "colour" and not isinstance(item, str):
            # One colour, the key of what it makes: {"G": 1} is G.
            item = next(iter(item)) if isinstance(item, dict) and len(item) == 1 else ""
        found[spec.name] = item
    return found


def value_from(key: str, base, cleaned: dict):
    """The primitive a submitted form describes, over `base`.

    Raises:
        forms.ValidationError: On a box the engine cannot read.
    """
    if key == "treasures":
        return cleaned.get("count") or 1
    if key == "mox":
        return cleaned.get("kind") or "discard_land"
    specs = specs_of(key, False, base)
    value = _unsaid_left_out(specs, base, _apply(specs, dict(base or {}), cleaned))
    if key == "tutor":
        # Kind and types are one restriction; empty ones are "any card".
        for name in ("kind", "types", "color"):
            if not value.get(name):
                value.pop(name, None)
    elif key == "mana_store":
        # What it makes is the colour's, as much as one use adds.
        value["produces"] = ({value["produces"]: value["amount"]}
                             if value.get("produces") else None)
    shown = {spec.name for spec in specs}
    if key == "tapped_unless" and "types" in shown and not value.get("types") \
            or key == "mana_rule" and "subtype" in shown and not value.get("subtype"):
        # A condition or a rule about no type at all: nothing to look for.
        raise forms.ValidationError(gettext("Name at least one type."))
    return value


def _unsaid_left_out(specs, base, value: dict) -> dict:
    """`value` without the boxes the stored value did not have and that were
    left at what the adapter reads for a missing one.

    So an untouched form gives back exactly what it was shown: "lands it
    finds enter tapped: no" written into a search that never said so would
    make the adapter read a search it does not play (a creature tutor to the
    battlefield with no filter) as one it does.
    """
    base = base if isinstance(base, dict) else {}
    for spec in specs:
        if spec.name in base or spec.name not in value:
            continue
        item = value[spec.name]
        if (item == spec.default) if spec.default is not None else not item:
            del value[spec.name]
    return value


# --- lists, one entry at a time (E3) ---------------------------------------------------


def entries_of(key: str, value) -> list:
    """A list primitive's entries: triggers, combat triggers, ways to pay."""
    if not value:
        return []
    if key == "combat":
        return list(value.get("triggers") or [])
    return list(value)


def entry_initial(key: str, value, index: int | None) -> dict:
    """The boxes for entry `index`, or for a new one (`None`)."""
    entries = entries_of(key, value)
    entry = (entries[index] if index is not None and 0 <= index < len(entries)
             else primitives.ITEM_DEFAULTS[key])
    found = _initial(primitives.ITEM_FIELDS[key], entry)
    if key == "combat":
        # The card's combat settings sit in every entry's form.
        found.update(_initial([spec for spec in primitives.ITEM_FIELDS[key]
                               if spec.name in primitives.COMBAT_CARD_FIELDS], value or {}))
    return found


def entry_value_from(key: str, value, index: int | None, cleaned: dict):
    """The whole primitive, with entry `index` (or a new one) as submitted.

    Raises:
        forms.ValidationError: On a box the engine cannot read.
    """
    entries = entries_of(key, value)
    known = index is not None and 0 <= index < len(entries)
    base = dict(entries[index] if known else primitives.ITEM_DEFAULTS[key])
    card_fields = primitives.COMBAT_CARD_FIELDS if key == "combat" else frozenset()
    entry = _apply([spec for spec in primitives.ITEM_FIELDS[key]
                    if spec.name not in card_fields], base, cleaned)
    if known:
        entries[index] = entry
    else:
        entries.append(entry)
    if key != "combat":
        return entries
    combat = dict(value or {})
    combat["triggers"] = entries
    settings = _apply([spec for spec in primitives.ITEM_FIELDS[key]
                       if spec.name in card_fields], {}, cleaned)
    for name, item in settings.items():
        if item:
            combat[name] = item
        else:
            combat.pop(name, None)
    return combat


def without_entry(key: str, value, index: int):
    """The primitive with entry `index` removed."""
    entries = entries_of(key, value)
    if 0 <= index < len(entries):
        del entries[index]
    if key != "combat":
        return entries
    return {**(value or {}), "triggers": entries}


# --- boxes into values ----------------------------------------------------------------


#: A word a "types" box may hold: a type line's word.
_TYPE_WORD = re.compile(r"^[a-z][a-z'-]{0,23}$")
#: One word a filter box may hold: a colour letter, `legendary`, `per_land`.
_WORD = re.compile(r"^[A-Za-z_]{0,24}$")
#: Mana kept to the second main phase: "RRRGGG".
_PIPS = re.compile(r"^[WUBRGC]{0,12}$")
#: A cost: generic and coloured symbols.
_COST = re.compile(r"^(?:\{(?:\d{1,2}|[WUBRGC])\})+$")


def _apply(specs, value: dict, cleaned: dict) -> dict:
    for spec in specs:
        item = cleaned.get(spec.name)
        if spec.kind == "int":
            value[spec.name] = spec.low if item is None else item
        elif spec.kind == "optint":
            if item is not None:
                value[spec.name] = item
            elif not isinstance(value.get(spec.name), str):
                value.pop(spec.name, None)
            # Else a word no box can hold - Chord of Calling's "X" - left
            # blank, so it stays as it is.
        elif spec.kind == "bool":
            value[spec.name] = bool(item)
        elif spec.kind == "choice":
            value[spec.name] = item or ""
        elif spec.kind == "multi":
            value[spec.name] = sorted(item or ())
        elif spec.kind == "words":
            value[spec.name] = _words(item)
        elif spec.kind == "word":
            value[spec.name] = _word(item)
        elif spec.kind == "pips":
            value[spec.name] = _pips(item)
        elif spec.kind == "mana":
            try:
                value[spec.name] = parse_mana(item) or None
            except ManaTextError as exc:
                raise forms.ValidationError(str(exc)) from exc
        elif spec.kind == "cost":
            value[spec.name] = _cost(item)
        elif spec.kind == "colors":
            value[spec.name] = _colors(item)
        elif spec.kind == "colour":
            # Blank where blank is no choice (a ritual's colour) keeps it.
            blank = any(name == "" for name, _label in spec.choices)
            value[spec.name] = item or ("" if blank else value.get(spec.name, ""))
    return value


def _words(text) -> list[str]:
    words = [word.strip().lower() for word in (text or "").split(",") if word.strip()]
    bad = [word for word in words if not _TYPE_WORD.match(word)]
    if bad:
        raise forms.ValidationError(
            gettext("“%(word)s” is not a type. Use words such as aura or elf.")
            % {"word": bad[0]})
    return words


def _word(text) -> str:
    text = (text or "").strip()
    if not _WORD.match(text):
        raise forms.ValidationError(
            gettext("“%(word)s” is not one word the engine knows.") % {"word": text})
    return text.upper() if len(text) == 1 else text.lower()


def _pips(text) -> str:
    text = (text or "").strip().upper()
    if not _PIPS.match(text):
        raise forms.ValidationError(
            gettext("“%(mana)s” is not mana. Use letters such as RRRGGG.") % {"mana": text})
    return text


def _colors(text) -> str:
    """Colour letters, each once, in the order WUBRG then C."""
    letters = (text or "").strip().upper()
    if not _PIPS.match(letters) or len(set(letters)) != len(letters):
        raise forms.ValidationError(
            gettext("“%(mana)s” is not a set of colours. Use letters such as G or WUBRG.")
            % {"mana": letters})
    return "".join(sorted(letters, key="WUBRGC".index))


def _cost(text: str) -> str:
    text = (text or "").strip().upper()
    if text and not _COST.match(text):
        raise forms.ValidationError(
            gettext("“%(cost)s” is not a mana cost. Write it like {2} or {1}{G}.")
            % {"cost": text})
    return text


# --- what each block says ------------------------------------------------------------


def _text(key: str, reading) -> str:
    """What the engine makes of one primitive of the card, in words."""
    card = reading.card
    play, turn, mana = card.play, card.turn, card.mana
    if key == "land_search":
        return adapter._land_search_text(play.search) if play.search else ""
    if key == "tutor":
        return reading.searches if play.tutor else ""
    if key == "tapped_unless":
        return _unless_text(play.untapped_if) if play.untapped_if else ""
    if key == "mana_store":
        return _store_text(mana.store) if mana.store else ""
    if key == "growth":
        return _growth_text(turn.growth) if turn.growth else ""
    if key == "treasures" and turn.treasure_mana == COLORLESS:
        return ngettext("%(count)s Eldrazi Spawn as it resolves (one {C})",
                        "%(count)s Eldrazi Spawn as it resolves (one {C} each)",
                        turn.treasures) % {"count": turn.treasures}
    if key == "treasures":
        return ngettext("%(count)s Treasure as it resolves", "%(count)s Treasures as it resolves",
                        turn.treasures) % {"count": turn.treasures}
    altar = next((each for each in card.abilities if each.form == "sacrifice_mana"), None)
    if key == "sacrifice_mana" and altar is not None and mana.altar.sacrifices:
        said = {"cost": adapter._way_text(_sacrifice_of(altar)), "amount": mana.altar.amount}
        if mana.altar.draw:
            return ngettext("%(cost)s: %(amount)s mana and %(count)s card",
                            "%(cost)s: %(amount)s mana and %(count)s cards",
                            mana.altar.draw) % {**said, "count": mana.altar.draw}
        return gettext("%(cost)s: %(amount)s mana") % said
    if key == "tap_mana" and mana.tap_other is not None:
        return gettext("tap %(what)s: %(amount)s mana each") % {
            "what": " / ".join(sorted(wanted_of(mana.tap_other.selector)[0])),
            "amount": mana.tap_other.amount}
    if key == "mox":
        return {"discard_land": gettext("discards a land card to enter"),
                "imprint": gettext("exiles a coloured card for its colours")}.get(mana.mox, "")
    if key == "mana_rule":
        return reading.taps_for
    return gettext("read from the card")


def _sacrifice_of(ability) -> AdditionalCost:
    """What an altar's sacrifice asks, read off its cost atoms, in the shape
    `adapter._way_text` words."""
    atoms = {cost.atom: cost for cost in ability.costs}
    sacrifice = atoms["sacrifice"]
    types, wanted = wanted_of(sacrifice.selector)
    life, discard = atoms.get("life"), atoms.get("discard")
    mana, exile = atoms.get("mana"), atoms.get("exile_from_graveyard")
    return AdditionalCost(sacrifice=types, sacrifice_filter=wanted,
                          sacrifice_count=sacrifice.amount, life=life.amount if life else 0,
                          life_x=life.x if life else False,
                          discard=discard.amount if discard else 0,
                          mana=mana.mana if mana else None,
                          exile_from_graveyard=exile.filter if exile else "")


def _unless_text(condition) -> str:
    """When a land that enters tapped comes in untapped, in words (PR 7)."""
    count = condition.count
    types = " / ".join(sorted(word.capitalize() for word in condition.types))
    kind = condition.kind
    if kind == "lands":
        text = (ngettext("untapped if you control %(count)s or more land",
                         "untapped if you control %(count)s or more lands", count)
                if condition.at_least else
                ngettext("untapped if you control %(count)s or fewer land",
                         "untapped if you control %(count)s or fewer lands", count))
        text %= {"count": count}
        said = [gettext("not counting this one") if condition.other else "",
                gettext("basic ones") if condition.basic else "",
                condition.type.capitalize()]
        said = [part for part in said if part]
        return f"{text} ({', '.join(said)})" if said else text
    if kind == "control_type":
        return gettext("untapped if you control a land of type %(types)s") % {"types": types}
    if kind == "reveal":
        return gettext("untapped if you reveal a card of type %(types)s from your hand") % {
            "types": types}
    if kind == "permanent":
        return (gettext("untapped if you control a legendary permanent of type %(types)s")
                if condition.legendary
                else gettext("untapped if you control a permanent of type %(types)s")) % {
            "types": types}
    if kind == "opponents":
        return ngettext("untapped if you have %(count)s or more opponent",
                        "untapped if you have %(count)s or more opponents", count) % {
            "count": count}
    if kind == "pay_life":
        return gettext("untapped if you pay %(count)s life") % {"count": count}
    if kind == "turn":
        return gettext("untapped up to your turn %(count)s") % {"count": count}
    if kind == "opponent_lands":
        return ngettext("untapped if an opponent controls %(count)s or more land",
                        "untapped if an opponent controls %(count)s or more lands", count) % {
            "count": count}
    if kind == "life_at_most":
        return gettext("untapped if you have %(count)s or less life") % {"count": count}
    return adapter._condition_text(condition)


def _store_text(store) -> str:
    """Mana kept as counters, in words (PR 7)."""
    each = ngettext("%(count)s mana each use", "%(count)s mana each use",
                    store.amount) % {"count": store.amount}
    if store.start_from:
        return each
    return "; ".join([each, ngettext("enters with %(count)s counter",
                                     "enters with %(count)s counters",
                                     store.start) % {"count": store.start}])


def _growth_text(growth) -> str:
    """+1/+1 counters, in words (PR 7)."""
    said = []
    if growth.enters_x:
        said.append(gettext("enters with X +1/+1 counters"))
    elif growth.enters:
        said.append(ngettext("enters with %(count)s +1/+1 counter",
                             "enters with %(count)s +1/+1 counters",
                             growth.enters) % {"count": growth.enters})
    if growth.puts:
        said.append(ngettext("puts a counter on %(count)s creature as it enters",
                             "puts a counter on %(count)s creatures as it enters",
                             growth.puts) % {"count": growth.puts})
    if growth.bonus:
        said.append(ngettext("%(count)s more counter on each placement",
                             "%(count)s more counters on each placement",
                             growth.bonus) % {"count": growth.bonus})
    if growth.draws_on_greatest:
        said.append(gettext("draws when a creature with the greatest power enters"))
    if growth.look:
        said.append(ngettext("looks at %(count)s card for a creature",
                             "looks at %(count)s cards for a creature",
                             growth.look) % {"count": growth.look})
    return "; ".join(said) or gettext("read from the card")


#: Every colour, for describing an entry outside a deck.
_ALL = frozenset("WUBRG")


def _entry_text(key: str, entry: dict, oracle_card) -> str:
    """One entry of a list primitive, in words, as the engine would play it."""
    try:
        if key == "triggers":
            built = adapter._triggers(SimpleNamespace(triggers=[entry]), {}, _ALL)
            return adapter._trigger_text(built[0]) if built else gettext("does nothing")
        if key == "combat":
            built = adapter._combat(oracle_card, SimpleNamespace(combat={"triggers": [entry]}),
                                    _ALL)["combat"]
            return adapter._combat_text(built[0]) if built else gettext("does nothing")
        built = adapter._additional_costs(SimpleNamespace(additional_cost=[entry]))
        return adapter._way_text(built[0]) if built else gettext("does nothing")
    except (KeyError, TypeError, ValueError):
        return gettext("not readable")
