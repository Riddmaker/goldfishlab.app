"""Turning an engine `Card` back into annotation overrides.

This is the inverse of `simulations.engine.adapter`, and it exists for exactly
one reason: to seed the reference deck into the database so that the round trip

    fixture Card -> overrides -> database -> adapter -> Card

can be checked for equality. If a field survives that loop unchanged, the
boundary really does carry it; if it does not, the golden test says which one.

Only the judgements go through here. Cost and type are read from the card by
`cards.profiles` and must **not** be written as annotations - otherwise
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
    if card.turn.skips_draw_step:
        overrides["skips_draw_step"] = True

    _mana(card, overrides)

    mana, play, turn = card.mana, card.play, card.turn
    if mana.ritual is not None and mana.ritual.gain:
        overrides["ritual_gain"] = mana.ritual.gain
    if mana.reduction is not None:
        overrides["cost_reduction"] = mana.reduction.amount
    if play.draw:
        overrides["draw_on_cast"] = play.draw
    if play.life:
        overrides["life_on_cast"] = play.life

    if play.tutor is not None:
        overrides["tutor_count"] = play.tutor.count
        overrides["tutor_to_hand"] = play.tutor.to_hand
        if play.tutor.life:
            overrides["tutor_life"] = play.tutor.life
        if play.tutor.kind:
            overrides["tutor_kind"] = play.tutor.kind

    if turn.upkeep is not None:
        overrides["upkeep_draw"] = turn.upkeep.draw
        if turn.upkeep.life:
            overrides["upkeep_life"] = turn.upkeep.life
        if turn.upkeep.life_per_mv:
            overrides["upkeep_life_per_mv"] = True

    if turn.end_step is not None:
        overrides["end_step_max_hand"] = turn.end_step.max_hand
        overrides["end_step_life_floor"] = turn.end_step.life_floor

    if card.subtypes:
        overrides["subtypes"] = sorted(card.subtypes)
    # The engine roles are a judgement too, but one said in the card's
    # categories, against its profile: `seed_reference_deck` does that
    # (`adapter.roles_as_categories`).

    return overrides


def _mana(card, overrides: dict) -> None:
    """Mana abilities, as either a flat amount or a scaling rule.

    A card with no mana ability exports an explicit ``0/0`` rather than
    nothing. The difference matters: leaving the keys out means "no opinion",
    and the adapter would then fall back to the derived reading - which for
    Ashnod's Altar says two colourless mana the engine cannot actually make.
    """
    scaling = [a for a in card.mana.entries if a.rule != FLAT]
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
    for ability in (a for a in card.mana.entries if a.rule == FLAT):
        for color, amount in ability.produces:
            produced[color] = produced.get(color, 0) + amount
    overrides["mana_produces"] = produced
