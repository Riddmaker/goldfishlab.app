"""Card model phase 1: Forge's blocks fill in what our reader left open.

`profiles.derive` reads a card's text into a `DerivedProfile`; `merge` then
looks at the blocks translated from the card's Forge script
(`cards.forge_import`) and takes a value from them, family by family, where

* our reader read nothing there, or less - Forge never overrides a reading;
* the ability it comes from is read whole: nothing unmodelled, no condition,
  no "unless", nothing optional (`complete`);
* the family's own rule, hand-checked on cards before it was taken, says
  the engine plays it as the card does.

A value taken is marked ``forge`` in the profile's ``source_map``, and a
review reason goes only where Forge's reading answers exactly what it said
could not be read. A user's annotation is applied by the adapter after all
of this, so it still overrides everything.
"""

from collections.abc import Callable

from simulation import blocks as b

#: The source label, as it appears in `DerivedProfile.source_map`.
FORGE = "forge"

#: Arguments that make an effect depend on something no block holds yet.
_OPEN_ARGS = ("unless", "optional")

#: What `derive` takes by default: the card's own blocks, from the database.
LOAD = object()


def blocks_of(card) -> tuple[b.Ability, ...]:
    """The card's blocks, or none: no script, or one of an older format."""
    from cards.models import ForgeScript

    try:
        script = card.forge
    except ForgeScript.DoesNotExist:
        return ()
    return _usable(script)


def blocks_by_card(cards) -> dict[str, tuple[b.Ability, ...]]:
    """The blocks of many cards in one query, by the card's id as a string."""
    from cards.models import ForgeScript

    rows = ForgeScript.objects.filter(oracle_card__in=cards).only(
        "oracle_card_id", "blocks", "blocks_version")
    return {str(row.oracle_card_id): _usable(row) for row in rows}


def _usable(script) -> tuple[b.Ability, ...]:
    if script.blocks_version != b.VERSION:
        return ()
    return b.from_json(script.blocks)


def complete(ability: b.Ability) -> bool:
    """Whether the ability is read whole, so a value may be taken from it."""
    if ability.unread or ability.conds or ability.options:
        return False
    if any(cost.atom == b.UNMODELED for cost in ability.costs):
        return False
    return not any(effect.verb in (b.UNMODELED, b.CHOOSE)
                   or any(effect.arg(name) for name in _OPEN_ARGS)
                   for effect in ability.effects)


def take(profile, field: str, value, *, answers: tuple[str, ...] = ()) -> None:
    """Set ``field`` from Forge, and drop the review reasons it ``answers``."""
    setattr(profile, field, value)
    profile.source_map = {**(profile.source_map or {}), field: FORGE}
    if answers:
        # Stored cut to 120 characters, as `profiles.derive` keeps them.
        cut = {answer[:120] for answer in answers}
        profile.review_reasons = [reason for reason in profile.review_reasons
                                  if reason not in cut]
        profile.needs_review = bool(profile.review_reasons)


#: The families, in order: each reads the blocks and the profile, and takes
#: what its rule allows. Filled in by the following steps of phase 1.
FAMILIES: list[Callable] = []


def merge(profile, abilities, card) -> None:
    """Take from Forge's ``abilities`` what our reader left open (in place)."""
    if not abilities:
        return
    for family in FAMILIES:
        family(profile, abilities, card)
