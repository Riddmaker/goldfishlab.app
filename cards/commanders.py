"""Which cards may lead a Commander deck, and which two may lead it together.

One place for the rule (P20 F2), because four screens ask it: the deck page's
choice, the server behind it, the importer and the legality panel. Before
this every card in the list could be picked - a land among them, and the
first one in the list was picked for anybody who only clicked "Save".

Read from the card's **front face**: a modal double-faced card whose back is a
creature does not make a commander (Esika's front is the creature, so she
does). The partner abilities are read from the rules text, not from
`OracleCard.keywords`: Scryfall lists "Partner" for plain partner, for
"Partner with X" and for every "Partner—<group>" alike, and the three pair
with different cards.

The pairs follow the Comprehensive Rules, 702.124 (checked against the
catalogue's own wording, 2026-10-13):

* two cards with plain **Partner**;
* two cards with the same **Partner—<group>** (Friends forever, Survivors,
  Character select, Father & son ...);
* **Partner with <name>**, each naming the other;
* a creature that says **Choose a Background** and a legendary Background;
* a creature with **Doctor's companion** and a legendary Time Lord Doctor
  creature with no other creature types.
"""

import re
from dataclasses import dataclass

from cards.names import normalise

#: Scryfall writes a double-faced card's type line and text as both faces.
_FACES = " // "
_TEXT_FACES = "\n//\n"

_PARTNER = re.compile(
    r"^Partner(?: with (?P<named>[^(\n]+?)|—(?P<group>[^(\n]+?))?\s*(?:\(|$)", re.M)
_FRIENDS = re.compile(r"^Friends forever\b", re.M)
_BACKGROUND_CHOOSER = re.compile(r"^Choose a Background\b", re.M | re.I)
_COMPANION = re.compile(r"^Doctor's companion\b", re.M)
#: Grist: "As long as Grist isn't on the battlefield, it's a 1/1 Insect
#: creature" - a creature everywhere a commander is chosen.
_CREATURE_OFF_FIELD = re.compile(r"isn't on the battlefield, it's a [^.\n]*creature")
_OWN_PERMISSION = "can be your commander"
_DOCTOR = "Time Lord Doctor"


def _front_type(card) -> str:
    return (card.type_line or "").split(_FACES)[0]


def _front_text(card) -> str:
    return (card.oracle_text or "").split(_TEXT_FACES)[0]


def _types(card) -> tuple[str, str]:
    """`(card types, subtypes)` of the front face, either side of the dash."""
    head, _, tail = _front_type(card).partition("—")
    return head, tail.strip()


@dataclass(frozen=True)
class Pairing:
    """The abilities a card has for sharing the command zone."""

    partner: bool = False
    group: str = ""
    named: str = ""
    chooses_background: bool = False
    doctors_companion: bool = False

    def __bool__(self) -> bool:
        return any((self.partner, self.group, self.named, self.chooses_background,
                    self.doctors_companion))


def pairing(card) -> Pairing:
    """What `card`'s rules text says about a second commander."""
    text = _front_text(card)
    partner, group, named = False, "", ""
    for found in _PARTNER.finditer(text):
        if found["named"]:
            named = found["named"].strip()
        elif found["group"]:
            group = found["group"].strip()
        else:
            partner = True
    if not group and _FRIENDS.search(text):
        # The printed wording before the 2025 update, kept for an older catalogue.
        group = "Friends forever"
    return Pairing(
        partner=partner,
        group=group,
        named=named,
        chooses_background=bool(_BACKGROUND_CHOOSER.search(text)),
        doctors_companion=bool(_COMPANION.search(text)
                               or "Doctor's companion" in (card.keywords or [])),
    )


def stands_alone(card) -> bool:
    """`card` may be a deck's commander, on its own or with a partner."""
    types, _ = _types(card)
    legendary = "Legendary" in types
    if legendary and "Creature" in types:
        return True
    text = _front_text(card)
    if _OWN_PERMISSION in text:
        return True
    return legendary and bool(_CREATURE_OFF_FIELD.search(text))


def is_background(card) -> bool:
    """A legendary Background: a commander only beside a creature that chooses one."""
    types, subtypes = _types(card)
    return "Legendary" in types and "Background" in subtypes.split()


def can_be_commander(card) -> bool:
    """`card` may sit in a command zone - alone, or as half of a pair."""
    return stands_alone(card) or is_background(card)


def _is_doctor(card) -> bool:
    types, subtypes = _types(card)
    return "Legendary" in types and "Creature" in types and subtypes == _DOCTOR


def _names(named: str, card) -> bool:
    return bool(named) and normalise(named) == normalise(card.name)


def _one_way(first, second, a: Pairing, b: Pairing) -> bool:
    """The pairs in which the two halves play different parts."""
    if a.chooses_background and is_background(second) and stands_alone(first):
        return True
    return a.doctors_companion and _is_doctor(second) and stands_alone(first)


def pair_ok(first, second) -> bool:
    """`first` and `second` may share the command zone, in either order."""
    if first is None or second is None or first.pk == second.pk:
        return False
    a, b = pairing(first), pairing(second)
    if _one_way(first, second, a, b) or _one_way(second, first, b, a):
        return True
    if not (stands_alone(first) and stands_alone(second)):
        return False
    if a.partner and b.partner:
        return True
    if a.group and a.group.casefold() == b.group.casefold():
        return True
    return _names(a.named, second) and _names(b.named, first)


def can_pair(card) -> bool:
    """`card` could share the command zone with *some* card."""
    return bool(pairing(card)) or is_background(card)


def partners_for(card, cards) -> list:
    """The cards among `cards` that may share the command zone with `card`."""
    return [other for other in cards if pair_ok(card, other)]


__all__ = [
    "Pairing",
    "can_be_commander",
    "can_pair",
    "is_background",
    "pair_ok",
    "pairing",
    "partners_for",
    "stands_alone",
]
