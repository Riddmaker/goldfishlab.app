"""The card page's primitives: what the engine plays, block by block (editor E2).

Each block is one primitive of :mod:`simulations.primitives` - a land search,
a search, an altar - with what the engine makes of it in words, who decided
it (the card, you, a built-in default, or "off"), and a form where the
primitive has one. The form is built from the primitive's `Field`s, so the
registry is the only place a primitive's boxes are described.

A form edits one primitive and never the whole: what it does not show of a
reading (a fetch land's `untap_at`) is carried through from the reading or
the stored override, so editing the count of a search cannot lose its
condition.
"""

import re
from dataclasses import dataclass

from django import forms
from django.utils.translation import gettext, ngettext

from simulations import primitives
from simulations.annotations import ManaTextError, format_mana, parse_mana
from simulations.engine import adapter


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
    shown, addable = [], []
    for primitive in primitives.PRIMITIVES:
        key = primitive.key
        said = key in overrides
        read = primitives.reading_of(key, profile)
        if said and _is_off(key, overrides[key]):
            status = "off"
        elif said:
            status = scopes.get(key, "deck")
        else:
            status = "card"
        block = Block(key=key, label=str(primitive.label), help=str(primitive.help),
                      text=_text(key, reading) if status != "off" else gettext("switched off"),
                      status=status, editable=key in primitives.EDITABLE,
                      scope=scopes.get(key, "deck") if said else "",
                      resettable=said and scopes.get(key, "deck") == scope)
        if said or read is not None:
            shown.append(block)
        elif block.editable:
            addable.append(block)
    return shown, addable


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


def split(form, key: str) -> tuple[list, list]:
    """The form's boxes shown at once, and the ones under "More"."""
    more = {spec.name for spec in primitives.FIELDS[key] if spec.more}
    return ([bound for bound in form if bound.name not in more],
            [bound for bound in form if bound.name in more])


def form_class(key: str):
    """A Django form for one primitive, built from its `Field`s."""
    fields = {}
    for spec in primitives.FIELDS[key]:
        fields[spec.name] = _form_field(spec)
    return type(f"{key.title().replace('_', '')}Form", (forms.Form,), fields)


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
    found = {}
    for spec in primitives.FIELDS[key]:
        item = (value or {}).get(spec.name)
        if spec.kind == "words":
            item = ", ".join(item or ())
        elif spec.kind == "mana":
            item = format_mana(item) if item else ""
        found[spec.name] = item
    return found


#: A word a "types" box may hold: a type line's word.
_TYPE_WORD = re.compile(r"^[a-z][a-z'-]{0,23}$")


def value_from(key: str, base, cleaned: dict):
    """The primitive a submitted form describes, over `base`.

    Raises:
        forms.ValidationError: On a box the engine cannot read.
    """
    if key == "treasures":
        return cleaned.get("count") or 1
    if key == "mox":
        return cleaned.get("kind") or "discard_land"
    value = dict(base or {})
    for spec in primitives.FIELDS[key]:
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
            words = [word.strip().lower() for word in (item or "").split(",") if word.strip()]
            bad = [word for word in words if not _TYPE_WORD.match(word)]
            if bad:
                raise forms.ValidationError(
                    gettext("“%(word)s” is not a type. Use words such as aura or elf.")
                    % {"word": bad[0]})
            value[spec.name] = words
        elif spec.kind == "mana":
            try:
                value[spec.name] = parse_mana(item) or None
            except ManaTextError as exc:
                raise forms.ValidationError(str(exc)) from exc
        elif spec.kind == "cost":
            value[spec.name] = _cost(item)
    if key == "tutor":
        # Kind and types are one restriction; empty ones are "any card".
        for name in ("kind", "types", "color"):
            if not value.get(name):
                value.pop(name, None)
    return value


#: A cost a land search may be activated for: generic and coloured symbols.
_COST = re.compile(r"^(?:\{(?:\d{1,2}|[WUBRGC])\})+$")


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
    if key == "triggers":
        return "; ".join(adapter._trigger_text(trigger) for trigger in card.triggers)
    if key == "combat":
        return "; ".join(adapter._combat_text(trigger) for trigger in card.combat)
    if key == "additional_cost":
        return " / ".join(adapter._way_text(way) for way in card.additional_costs)
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
