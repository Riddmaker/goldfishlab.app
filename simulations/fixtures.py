"""Turning an engine `Card` back into annotation overrides.

This is the inverse of `simulations.engine.adapter`, and it exists for exactly
one reason: to seed the reference deck into the database so that the round trip

    fixture Card -> overrides -> database -> adapter -> Card

can be checked for equality. If a field survives that loop unchanged, the
boundary really does carry it; if it does not, the golden test says which one.

Only the judgements go through here. Cost, type and role tags are read from the
card by `cards.profiles` and must **not** be written as annotations - otherwise
the golden test would be comparing the fixture with itself and proving nothing
about the derivation.
"""

from simulation.cards import DOUBLE_SUBTYPE, FLAT, PER_CONTROLLED, TYPE_ADDING

#: Engine rule constant -> the name an annotation uses.
RULE_NAMES = {
    PER_CONTROLLED: "per_controlled",
    DOUBLE_SUBTYPE: "double_subtype",
    TYPE_ADDING: "type_adding",
}


def annotation_overrides(card) -> dict:
    """Everything about `card` that no derivation could have produced."""
    overrides: dict = {}

    # Kind and land type are the deck author's reading of the card. They differ
    # from the derivation on exactly the cards Phase 1 documented as judgement
    # calls - Ashnod's Altar is tagged `mana-rock` but plays as a sacrifice
    # engine, Jet Medallion taps for nothing but plays as ramp.
    overrides["kind"] = card.kind
    if card.land_type:
        overrides["land_type"] = card.land_type

    if card.priority is not None:
        overrides["priority"] = card.priority
    if card.accelerant:
        overrides["accelerant"] = True
    if not card.goldfish_castable:
        overrides["goldfish_castable"] = False
    if card.needs_creature_in_yard:
        overrides["needs_creature_in_yard"] = True
    if card.needs_creature_on_bf:
        overrides["needs_creature_on_bf"] = True
    if card.skips_draw_step:
        overrides["skips_draw_step"] = True

    _mana(card, overrides)

    if card.ritual_gain:
        overrides["ritual_gain"] = card.ritual_gain
    if card.cost_reduction is not None:
        overrides["cost_reduction"] = card.cost_reduction.amount
    if card.draw_on_cast:
        overrides["draw_on_cast"] = card.draw_on_cast
    if card.life_on_cast:
        overrides["life_on_cast"] = card.life_on_cast

    if card.tutor is not None:
        overrides["tutor_count"] = card.tutor.count
        overrides["tutor_to_hand"] = card.tutor.to_hand
        if card.tutor.life:
            overrides["tutor_life"] = card.tutor.life
        if card.tutor.kind:
            overrides["tutor_kind"] = card.tutor.kind

    if card.upkeep is not None:
        overrides["upkeep_draw"] = card.upkeep.draw
        if card.upkeep.life:
            overrides["upkeep_life"] = card.upkeep.life
        if card.upkeep.life_per_mv:
            overrides["upkeep_life_per_mv"] = True

    if card.end_step is not None:
        overrides["end_step_max_hand"] = card.end_step.max_hand
        overrides["end_step_life_floor"] = card.end_step.life_floor

    if card.subtypes:
        overrides["subtypes"] = sorted(card.subtypes)
    if card.tags:
        # Roles are a judgement too: the community tag DAG calls Grave Pact
        # removal, the deck author calls it a lock piece. Both are defensible,
        # and the deck's own reading is what its numbers were researched with.
        overrides["tags"] = sorted(card.tags)

    return overrides


def _mana(card, overrides: dict) -> None:
    """Mana abilities, as either a flat amount or a scaling rule.

    A card with no mana ability exports an explicit ``0/0`` rather than
    nothing. The difference matters: leaving the keys out means "no opinion",
    and the adapter would then fall back to the derived reading - which for
    Ashnod's Altar says two colourless mana the engine cannot actually make.
    """
    scaling = [a for a in card.mana_abilities if a.rule != FLAT]
    if scaling:
        ability = scaling[0]
        overrides["scaling_rule"] = RULE_NAMES[ability.rule]
        if ability.subtype:
            overrides["scaling_subtype"] = ability.subtype
        if ability.activation_generic:
            overrides["scaling_activation"] = ability.activation_generic
        if ability.color:
            overrides["scaling_color"] = ability.color
        return

    produced: dict[str, int] = {}
    for ability in (a for a in card.mana_abilities if a.rule == FLAT):
        for color, amount in ability.produces:
            produced[color] = produced.get(color, 0) + amount
    overrides["mana_produces"] = produced
