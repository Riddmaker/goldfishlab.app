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
"""

import re
from dataclasses import dataclass, field
from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext, ngettext

from simulations import primitives
from simulations.annotations import ManaTextError, format_mana, parse_mana
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
        editable = key in primitives.EDITABLE or key in LISTS
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
        elif editable:
            addable.append(block)
    return shown, addable


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
             here=check.scope == scope, editable=key in primitives.EDITABLE)
        for key, check in _checks_by_key(reading).items()
        if check.status == "gone" and key not in LISTS and key not in overrides
    ]


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


def specs_of(key: str, item: bool) -> tuple:
    return primitives.ITEM_FIELDS[key] if item else primitives.FIELDS[key]


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
    return forms.CharField(max_length=40, **common)


def initial(key: str, value) -> dict:
    """A stored value as the form's boxes."""
    if key == "treasures":
        return {"count": value}
    if key == "mox":
        return {"kind": value}
    return _initial(primitives.FIELDS[key], value or {})


def _initial(specs, value: dict) -> dict:
    found = {}
    for spec in specs:
        item = value.get(spec.name)
        if spec.kind == "words":
            item = ", ".join(item or ())
        elif spec.kind == "mana":
            item = format_mana(item) if item else ""
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
    value = _apply(primitives.FIELDS[key], dict(base or {}), cleaned)
    if key == "tutor":
        # Kind and types are one restriction; empty ones are "any card".
        for name in ("kind", "types", "color"):
            if not value.get(name):
                value.pop(name, None)
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
            if item is None:
                value.pop(spec.name, None)
            else:
                value[spec.name] = item
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
    if key == "land_search":
        return adapter._land_search_text(card.land_search) if card.land_search else ""
    if key == "tutor":
        return reading.searches if card.tutor else ""
    if key == "tapped_unless":
        return adapter._condition_text(card.tapped_unless) if card.tapped_unless else ""
    if key == "treasures":
        return ngettext("%(count)s Treasure as it resolves", "%(count)s Treasures as it resolves",
                        card.treasures) % {"count": card.treasures}
    if key == "sacrifice_mana" and card.sacrifice_mana is not None:
        return gettext("%(cost)s: %(amount)s mana") % {
            "cost": adapter._way_text(card.sacrifice_mana), "amount": card.sacrifice_mana_amount}
    if key == "tap_mana" and card.tap_mana is not None:
        return gettext("tap %(what)s: %(amount)s mana each") % {
            "what": " / ".join(sorted(card.tap_mana.types)), "amount": card.tap_mana.amount}
    if key == "mox":
        return {"discard_land": gettext("discards a land card to enter"),
                "imprint": gettext("exiles a coloured card for its colours")}.get(card.mox, "")
    if key == "mana_rule":
        return reading.taps_for
    return gettext("read from the card")


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
