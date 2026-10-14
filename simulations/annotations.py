"""What a user may say about a card, and how it gets written down.

`CardAnnotation.ALLOWED_KEYS` has thirty-four entries. Most of them exist so
that the hand-annotated reference deck can be rebuilt from the database -
`scaling_rule`, `end_step_life_floor`, `tutor_kind` - and an editor offering
all of them would be a form nobody could fill in. This module names the eleven
that a person actually has an opinion about, and they are not chosen at random:
eight are the fixes for the blind spots in :mod:`simulations.blindspots`, and
the other three are there because something started deriving them - tutors in
Phase 5b, a mana ability's cost and whether it untaps in the 2026-09-25 review -
and a derived value nobody can switch off is a judgement made on their behalf.
The tutors' count left this form in card model phase 2 (PR 7): the Search
block says it, beside the zone and what it may find (`tutor_as_block`).

Two rules shape everything here, and both are about not lying.

**Blank means "no opinion", never zero.** A form that wrote `priority: 0` for
an empty box would change every future simulation of that deck, silently, on
the first save. So a key that the form did not receive a value for is *absent*
from `overrides`, and the engine falls back to its own rule.

**Saving merges; it never replaces.** The editor owns the eight keys it shows
and nothing else. Cabal Coffers' annotation carries `scaling_rule`,
`scaling_subtype` and `scaling_activation`; someone editing its priority
through this form must not silently delete the per-Swamp scaling that makes it
the card it is. `apply` is the one function that enforces that, and
`tests/test_simulations_annotations.py` pins it.
"""

from dataclasses import dataclass

from django.utils.text import format_lazy
from django.utils.translation import gettext, gettext_lazy, gettext_noop

from cards.models import DerivedProfile
from cards.profiles import ROLE_FROM_TAG
from simulations.engine import runner
from simulations.models import MANA_SOURCES
from simulations.report import SEEN_MECHANIC_ROLES, SEEN_ROLES, SEEN_STRATEGY_ROLES

_ = gettext_lazy

#: Roles a card may be given. The community tag vocabulary, plus the three the
#: deriver adds itself, plus the ones the analysis reads for its milestone
#: table. `draw_engine` used to be described here as "a pure judgement that no
#: tag supplies"; that was written without looking. The DAG has `draw-engine`
#: on 1,667 cards, it agrees with the reference deck's author six times out of
#: six, and Phase 5b mapped it - which is what finally let that milestone fire
#: for a deck somebody imported rather than only for the fixture.
ROLE_KEYS = sorted(
    set(ROLE_FROM_TAG.values())
    | {"gamechanger", "creature", "land"}
    | set(runner.ENGINE_ROLE_TAGS)
)

#: What a page calls each role, in English (translated where shown). The
#: report's categories and strategies keep their names; the rest are the
#: engine's own.
ROLE_NAMES = {
    **dict(SEEN_ROLES + SEEN_STRATEGY_ROLES + SEEN_MECHANIC_ROLES),
    "creature": gettext_noop("Creature"),
    "draw_engine": gettext_noop("Draw engine"),
    "gamechanger": gettext_noop("Game Changer"),
    "land": gettext_noop("Land"),
    "mana_rock": gettext_noop("Mana rock"),
    "recursive": gettext_noop("Recursive creature"),
    "ritual": gettext_noop("Ritual"),
}

ROLE_CHOICES = tuple(
    (role, gettext_lazy(ROLE_NAMES[role]) if role in ROLE_NAMES else role.replace("_", " "))
    for role in ROLE_KEYS
)

#: Engine card kinds. The same nine values as `DerivedProfile.Kind`, which is
#: not a coincidence - the deriver writes that field and the engine reads it.
KIND_CHOICES = DerivedProfile.Kind.choices

#: Land types, with the colour each one taps for. Editable because in this
#: engine a land type is not decoration: **a land carrying a coloured land type
#: taps as a basic land and its own mana ability goes unused.** So "taps for"
#: alone cannot silence a Swamp, and a form offering only "taps for" would let
#: somebody set a land to make nothing and then watch it go on making black
#: mana. The same types are what Cabal Coffers counts and what Crypt Ghast
#: doubles, and the help text says so.
SUBTYPE_CHOICES = tuple(
    # The land type keeps its English name, like the type line it is read from.
    (subtype, format_lazy(_("{type} (taps for {color})"), type=subtype.title(), color=color))
    for subtype, color in sorted(runner.LAND_SUBTYPE_COLORS.items())
)

#: The word that means "this taps for nothing at all", as opposed to leaving
#: the field empty, which means "no opinion". Ashnod's Altar is the case that
#: makes the distinction necessary: it is tagged `mana-rock` and does make
#: mana, but only by sacrificing a creature - which the engine cannot do, so it
#: taps for nothing, and that is a judgement somebody has to record.
NOTHING = "nothing"


@dataclass(frozen=True)
class Judgement:
    """One thing a user may decide about a card."""

    key: str
    label: str
    help: str


#: The editable subset, in the order the form shows it. Every entry's `key` is
#: a key of `CardAnnotation.ALLOWED_KEYS`, and a test asserts that.
JUDGEMENTS = (
    Judgement(
        "priority",
        _("How early to cast it"),
        _("Higher goes first. The engine's own rule is 40 minus the mana value, "
          "so a two-drop it has no opinion about sits at 38."),
    ),
    Judgement(
        "accelerant",
        _("Counts as acceleration"),
        _("Whether this card makes a one-land opening hand worth keeping. It "
          "changes the mulligan decision and therefore every number below it."),
    ),
    Judgement(
        "goldfish_castable",
        _("Can be cast against nobody"),
        _("Say no for a card with no legal target in an empty game - removal, a "
          "board wipe, anything that needs an opponent. The engine will hold it "
          "rather than pretend it did something."),
    ),
    Judgement(
        "kind",
        _("What the engine treats it as"),
        _("Rituals add their mana when cast; rocks add it when tapped. The type "
          "line cannot always tell the two apart."),
    ),
    Judgement(
        "mana_produces",
        _("Taps for"),
        # "nothing" is the word the box reads (`NOTHING`), in every language.
        _("What tapping the card itself makes. Letters for the colours, with a "
          "number where it makes more than one: B, 2B, B C. Write “nothing” for a "
          "card whose own tap makes no mana. Its other mana - an altar, a trigger, "
          "counters - is under “What the engine plays”."),
    ),
    Judgement(
        "subtypes",
        _("Land types"),
        _("What the land counts as. This is what a basic land taps for, what "
          "Cabal Coffers counts and what Crypt Ghast doubles — so a land with a "
          "type here taps for that colour whatever “Taps for” says above."),
    ),
    Judgement(
        "enters_tapped",
        _("Enters tapped"),
        _("Read off the card text by a regular expression, which is the weakest "
          "reading this application makes. Worth checking on anything unusual."),
    ),
    Judgement(
        "tags",
        _("Roles"),
        _("What the card does, in the vocabulary the report's milestone table "
          "counts. Replacing them overrides the community tags completely."),
    ),
    Judgement(
        "mana_activation",
        _("Costs to tap for mana"),
        _("Generic mana its mana ability costs on top of tapping - the {1} on a "
          "Signet. Write 0 for a card that only has to tap."),
    ),
    Judgement(
        "untaps",
        _("Untaps every turn"),
        _("Say no for Mana Vault, Grim Monolith and anything else that stays tapped "
          "once it has been used. The engine then takes its mana once, not every turn."),
    ),
    Judgement(
        "x_min",
        _("Smallest X worth casting it for"),
        _("The engine casts a spell with X last in your turn, with all the mana "
          "that is left as X - but not for less than this. It starts at 1."),
    ),
    Judgement(
        "chosen_type",
        _("Creature type it names"),
        _("For Cavern of Souls and the like: the creature type you choose as it "
          "enters, such as Elf. Left empty, the engine names the most common "
          "creature type in your deck."),
    ),
    Judgement(
        "chosen_color",
        _("Colour it chooses"),
        _("For Heraldic Banner, Throne of Eldraine and the like: the colour you "
          "choose as it enters. Left empty, the engine chooses the colour most "
          "mana symbols in your deck's costs ask for."),
    ),
)

#: Every key the editor owns. Anything else in `overrides` is left alone.
EDITABLE_KEYS = frozenset(judgement.key for judgement in JUDGEMENTS)

#: Keyed, for the templates and for the provenance panel.
BY_KEY = {judgement.key: judgement for judgement in JUDGEMENTS}


class ManaTextError(ValueError):
    """The "taps for" box did not say anything the engine could read."""


def parse_mana(text: str) -> dict[str, int] | None:
    """Read the "taps for" box.

    Three outcomes, and the difference between the last two is the whole
    reason this function exists:

    * `None` - the box was empty. No opinion; the derived reading stands.
    * `{}` - the box said "nothing". A statement that this card taps for no
      mana, which beats the derived reading.
    * `{"B": 2}` - this much of this colour.

    Raises:
        ManaTextError: On anything else, with the input quoted back. A colour
            the engine does not know has to be an error here and not a saved
            row, or it surfaces days later inside a worker.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() in {NOTHING, "none", "no mana", "0"}:
        return {}

    produced: dict[str, int] = {}
    for token in text.replace(",", " ").split():
        count, letters = _split_count(token)
        for letter in letters:
            if letter not in MANA_SOURCES:
                raise ManaTextError(gettext(
                    "%(letter)s is not a mana colour. Use letters from %(letters)s, "
                    "or “%(nothing)s”."
                ) % {"letter": repr(letter), "letters": ", ".join(sorted(MANA_SOURCES)),
                     "nothing": NOTHING})
            produced[letter] = produced.get(letter, 0) + count
    return produced


def _split_count(token: str) -> tuple[int, str]:
    """`"2B"` is two black; `"BB"` is also two black; `"B"` is one."""
    digits = ""
    while token and token[0].isdigit():
        digits, token = digits + token[0], token[1:]
    letters = token.upper()
    if not letters:
        raise ManaTextError(gettext(
            "“%(amount)s” names an amount but no colour. Write 2B rather than 2."
        ) % {"amount": digits})
    return int(digits or 1), letters


def format_mana(produced) -> str:
    """The inverse of :func:`parse_mana`, for filling the form back in."""
    if produced is None:
        return ""
    if not produced:
        return NOTHING
    return " ".join(
        f"{amount}{color}" if amount != 1 else color
        for color, amount in sorted(produced.items())
    )


def apply(overrides: dict, values: dict) -> dict:
    """Merge a **whole form's** answers into an existing `overrides` dict.

    The editor owns every editable key, so `values` has to carry all of them:
    one that is absent is removed from the result, because a user clearing a
    box means "I have no opinion after all" and leaving the old value would
    make the box a lie. Every non-editable key is carried through untouched -
    see the module docstring for why that matters more than it looks.

    Never call it with a partial dict: it would silently throw away judgements
    the caller never asked about, which is how the casting-order screen
    (removed in Phase 9 C) nearly deleted every role somebody had set. (Its
    one-key sibling `patch` went in phase 9 I, with the last screen that used
    it.)

    Args:
        overrides: What is stored now.
        values: `{key: value}` for every editable key, `None` where nobody
            expressed an opinion.

    Returns:
        dict: The overrides to store. Never the same object as `overrides`.
    """
    kept = {
        key: value for key, value in (overrides or {}).items()
        if key not in EDITABLE_KEYS
    }
    return _merged(kept, values)


def _merged(kept: dict, values: dict) -> dict:
    for key, value in values.items():
        if key not in EDITABLE_KEYS:
            raise ValueError(f"{key!r} is not an editable judgement")
        if value is not None:
            kept[key] = value
    return kept


#: The judgement keys a search was said with before the Search block.
LEGACY_TUTOR_KEYS = ("tutor_count", "tutor_to_hand")


def tutor_as_block(overrides: dict) -> dict | None:
    """A correction that says a search with `tutor_count`, said as the
    Search block instead (card model phase 2, PR 7), or None when it says
    none.

    A count of 0 is "not a tutor": the block switched off. `tutor_life` and
    `tutor_kind` stay as they are - the adapter reads both beside the block.
    Whether the engine plays the result the same is for the caller to check
    (migration 0016 builds the card both ways).
    """
    if "tutor_count" not in (overrides or {}):
        return None
    count = overrides["tutor_count"]
    kept = {key: value for key, value in overrides.items() if key not in LEGACY_TUTOR_KEYS}
    if not count:
        return kept | {"tutor": None}
    to = "hand" if overrides.get("tutor_to_hand", True) else "graveyard"
    return kept | {"tutor": {"to": to, "count": int(count)}}


#: The editor rebuild: "back to the card data" for one primitive - the key is
#: removed, so the reader's reading stands again. Not `None`, which is a
#: statement ("the card does not do this").
RESET = object()


def apply_primitives(overrides: dict, changes: dict) -> dict:
    """Merge changes to some primitives into an existing `overrides` dict.

    Unlike :func:`apply`, which owns every judgement and takes the whole
    form, a primitive is edited one block at a time: keys not in `changes`
    stay as they are. A value of :data:`RESET` removes the key; `None`
    switches the primitive off.

    Returns:
        dict: The overrides to store. Never the same object as `overrides`.
    """
    from simulations import primitives

    kept = dict(overrides or {})
    for key, value in changes.items():
        if key not in primitives.KEYS:
            raise ValueError(f"{key!r} is not a primitive")
        if value is RESET:
            kept.pop(key, None)
        else:
            kept[key] = primitives.validate(key, value)
    return kept
