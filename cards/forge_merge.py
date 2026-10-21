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

from django.utils.translation import gettext_noop

from cards import forge
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


# --- the families --------------------------------------------------------------

#: Kinds whose mana is the spell's own, as it resolves: not a mana ability.
_ONE_SHOT = frozenset({"instant", "sorcery", "ritual"})
_LETTERS = frozenset("WUBRGC")
#: Reasons a mana ability read whole answers.
_MANA_UNREAD = (
    "makes mana, but how much could not be read",
    "produces mana, but no readable 'Add' clause",
    "an 'Add' clause whose amount could not be read",
)


def mana(profile, abilities, card) -> None:
    """A mana ability where the reader read none: Bramble Familiar's {G}.

    Taken only when the card has exactly one ability that makes mana for {T}
    (and generic mana, a Signet's {1}), a fixed amount, of colours the text
    names or of any colour, for any spell. A painland's missing {C} is not
    taken: beside a choice of two colours it changes nothing the engine plays.
    """
    if profile.kind in _ONE_SHOT or profile.mana_rule or (
            profile.produces_mana and profile.mana_amount is not None):
        return
    found = [ability for ability in abilities if ability.kind == b.MANA]
    if len(found) != 1 or not complete(found[0]):
        return
    ability = found[0]
    atoms = [cost.atom for cost in ability.costs]
    if "tap" not in atoms or not set(atoms) <= {"tap", "mana"}:
        return
    activation = 0
    for cost in ability.costs:
        if cost.atom == "mana":
            paid = cost.mana
            if paid.pips or paid.colorless or paid.hybrid or paid.phyrexian or paid.has_x:
                return
            activation = paid.generic
    if len(ability.effects) != 1 or ability.effects[0].verb != "add_mana":
        return
    effect = ability.effects[0]
    n, color = effect.arg("n", 1), effect.arg("color", "")
    if not isinstance(n, int) or effect.arg("restrict"):
        return
    if color == "any":
        produces, colors = None, list("WUBRG")
    elif color and set(color) <= _LETTERS and (effect.arg("all") or len(color) == 1):
        produces = {letter: n for letter in color}
        n *= len(color)
        colors = sorted(set(color) - {"C"})
    elif color and set(color) <= _LETTERS:
        produces, colors = None, sorted(set(color) - {"C"})
    else:
        return
    if activation >= n:
        return
    profile.produces_mana = True
    take(profile, "mana_amount", n, answers=_MANA_UNREAD)
    take(profile, "mana_produces", produces)
    take(profile, "mana_activation", activation or None)
    if colors and not profile.mana_colors:
        take(profile, "mana_colors", colors)


_TAPPED_UNREAD = ("text mentions entering tapped, but the condition was not readable",)
_SELF_ENTERS = b.Event("enters", who="self")


def _unless(cost: str) -> dict | None:
    """Forge's "unless" cost as a condition the engine checks: a card of a
    type revealed from the hand, or life paid."""
    parts = forge.cost_parts(cost)
    if len(parts) != 1:
        return None
    name, _, inner = parts[0].partition("<")
    fields = inner.rstrip(">").split("/")
    if name == "Reveal" and len(fields) == 2 and fields[0] == "1" and fields[1].isalpha():
        return {"kind": "reveal", "types": [fields[1].casefold()]}
    if name == "PayLife" and fields[0].isdigit():
        return {"kind": "pay_life", "life": int(fields[0])}
    return None


def enters_tapped(profile, abilities, card) -> None:
    """The card enters tapped, where the reader did not read it.

    "Boseiju enters tapped" (the short name), and "If you don't, this land
    enters tapped" after a reveal or life the engine can check. A condition
    it cannot check is played as tapped, and the reason stays: pessimistic,
    not wrong. "You may have it enter tapped" is not taken.
    """
    if profile.enters_tapped:
        return
    found = [ability for ability in abilities if ability.kind == b.REPLACEMENT
             and ability.event == _SELF_ENTERS
             and any(effect.verb == "enters_tapped" for effect in ability.effects)]
    if len(found) != 1 or any(effect.verb == b.UNMODELED or effect.arg("optional")
                              for effect in found[0].effects):
        return
    ability = found[0]
    if complete(ability):
        take(profile, "enters_tapped", True, answers=_TAPPED_UNREAD)
        return
    unless = next(effect.arg("unless") for effect in ability.effects
                  if effect.verb == "enters_tapped")
    condition = _unless(unless) if unless and not ability.unread else None
    if condition is not None:
        take(profile, "enters_tapped", True, answers=_TAPPED_UNREAD)
        take(profile, "tapped_unless", condition)
        return
    take(profile, "enters_tapped", True)


#: Reasons a land search read whole answers: each says the reader found a
#: search, or a tutor, and could not read it.
_SEARCH_UNREAD = (
    "searches for lands under a condition the engine cannot read",
    "tutors onto the battlefield, which the engine cannot do",
    "tutors, but no readable 'search your library' clause",
    "searches for lands the engine cannot describe",
    "searches for lands, but where they go could not be read",
    "tutors, but how many cards it finds could not be read",
    "searches the library more than once; not modelled",
    "tutors to more than one zone; not modelled",
    "searches for lands at a cost the engine does not pay",
)
_BASIC_TYPES = ("Plains", "Island", "Swamp", "Mountain", "Forest")
#: What may stand beside the search in its ability: nothing the engine skips.
_SEARCH_COMPANIONS = frozenset({"shuffle"})


def _lands(what: str) -> tuple[bool, list[str]] | None:
    """Forge's ``ChangeType`` of a land search as (basic only, land types),
    or None for one the engine cannot describe: "Land.Basic" -> (True, []),
    "Forest,Plains" -> (False, ["forest", "plains"])."""
    basics, types = set(), set()
    for part in what.split(","):
        base, _, rest = part.strip().partition(".")
        props = set(rest.split("+")) - {""}
        if base in _BASIC_TYPES:
            types.add(base.casefold())
        elif base in ("Land", "Card"):
            named = props & set(_BASIC_TYPES)
            if len(named) > 1 or (base == "Card" and not named):
                return None
            types |= {name.casefold() for name in named}
            props -= named
        else:
            return None
        if props - {"Basic"}:
            return None
        basics.add("Basic" in props)
    if len(basics) != 1 or (types and len(types) != len(what.split(","))):
        return None
    return basics.pop(), sorted(types)


def _search_when(ability: b.Ability, profile) -> dict | None:
    """When the search is played, and what activating it costs, or None."""
    if ability.kind == b.RESOLVES and profile.kind in _ONE_SHOT:
        return {"when": "cast"}
    if ability.kind == b.TRIGGERED and ability.event == _SELF_ENTERS \
            and profile.kind not in _ONE_SHOT:
        return {"when": "enters"}
    if ability.kind != b.ACTIVATED:
        return None
    found = {"when": "activate"}
    for cost in ability.costs:
        if cost.atom == "tap":
            found["taps"] = True
        elif cost.atom == "sacrifice_self":
            found["sacrifice"] = True
        elif cost.atom == "mana" and not (cost.mana.pips or cost.mana.colorless
                                          or cost.mana.hybrid or cost.mana.phyrexian
                                          or cost.mana.has_x):
            if cost.mana.generic:
                found["cost"] = f"{{{cost.mana.generic}}}"
        else:
            return None
    return found


def land_search(profile, abilities, card) -> None:
    """A land search the reader could not read: Reshape the Earth's ten
    lands, Molten Man's basic Mountain as it enters, Frontier Guide's
    activated one.

    Taken when it is the card's only search of its library, read whole and
    not a mode, the lands go onto the battlefield, a number of them, of a
    kind the engine describes (any, basic, of basic land types), and it is
    played as a spell resolves, as the card itself enters, or for generic
    mana, {T} and the card itself. A search in combat, as something dies or
    for another player is not.
    """
    if profile.land_search is not None:
        return
    searches = [ability for ability in abilities
                if any(effect.arg("from") == "library" for effect in ability.effects)]
    if len(searches) != 1 or not complete(searches[0]) or searches[0].mode:
        return
    ability = searches[0]
    moves = [effect for effect in ability.effects if effect.verb == "move"]
    rest = [effect for effect in ability.effects if effect.verb != "move"]
    if len(moves) != 1 or any(effect.verb not in _SEARCH_COMPANIONS for effect in rest):
        return
    move = moves[0]
    n = move.arg("n", 1)
    lands = _lands(str(move.arg("what", "")))
    when = _search_when(ability, profile)
    if move.arg("to") != "battlefield" or move.arg("player") or move.arg("target") \
            or not isinstance(n, int) or lands is None or when is None:
        return
    from cards.profiles import _search_spec

    basic, types = lands
    take(profile, "land_search", _search_spec(battlefield=n, tapped=bool(move.arg("tapped")),
                                              basic=basic, types=types, **when),
         answers=_SEARCH_UNREAD)
    # The search is the land search now, not a tutor the engine cannot play,
    # as `profiles.derive` leaves it when it reads one.
    profile.tutor_to, profile.tutor_count, profile.tutor_kind = "", None, ""
    profile.tutor_filter = None
    profile.source_map = {field: source for field, source in profile.source_map.items()
                          if not field.startswith("tutor_")}


_GRANTS = "only grants a mana ability to another permanent"
#: Token owners that are you as the card enters or resolves: you, or the
#: controller of the card that triggered (`forge_import._player`).
_YOURS = frozenset({None, "controller"})


def treasures(profile, abilities, card) -> None:
    """Treasure the reader did not read: made as the card itself enters
    (Galazeth Prismari, Kalain) or as a spell resolves.

    Taken from an ability read whole, not a mode, that does nothing else but
    make other tokens; the Treasure yours, not "its controller's" (An Offer
    You Can't Refuse), and untapped: the engine spends a Treasure the turn it
    is made. Treasure made in combat, as a creature dies, at the end step or
    for an activation is not: the engine plays none of those moments, and a
    card with one of them beside (Patron of the Arts, as it enters or dies)
    is played with the one it does play.
    """
    from cards.profiles import _GRANTED, _TREASURE_REMINDER

    if profile.treasures:
        return
    made = 0
    for ability in abilities:
        tokens = [effect for effect in ability.effects
                  if effect.verb == "create_token" and effect.arg("token") == "treasure"]
        if not tokens:
            continue
        arrives = (ability.kind == b.RESOLVES and profile.kind in _ONE_SHOT) or (
            ability.kind == b.TRIGGERED and ability.event == _SELF_ENTERS
            and profile.kind not in _ONE_SHOT)
        n = tokens[0].arg("n", 1)
        others = [effect for effect in ability.effects if effect not in tokens]
        if not arrives or ability.mode or not complete(ability) or len(tokens) != 1 \
                or any(effect.verb != "create_token" for effect in others) \
                or tokens[0].arg("owner") not in _YOURS or tokens[0].arg("tapped") \
                or not isinstance(n, int):
            # Played at none of its moments, which only under-reads the card.
            continue
        made += n
    if not made:
        return
    text = (card.oracle_text or "") if card is not None else ""
    # The only "Add" was the Treasure's own reminder text, as `derive` says.
    answers = () if _GRANTED.search(_TREASURE_REMINDER.sub("", text)) else (_GRANTS,)
    take(profile, "treasures", made, answers=answers)


#: What may stand beside a draw: nothing that touches your hand or library,
#: so that the draw alone is what the engine plays of the ability. Leaving
#: out a scry or a token only under-reads it.
_DRAW_COMPANIONS = frozenset({
    "damage", "damage_all", "destroy", "destroy_all", "counter_spell", "gain_life", "pump",
    "pump_all", "tap", "tap_all", "untap", "untap_all", "put_counter", "put_counter_all",
    "scry", "surveil", "create_token", "goad", "prevent_damage", "regenerate", "shuffle",
    "proliferate", "gain_control", "fight", "become_monarch",
})


def _drawn(ability: b.Ability) -> tuple[int, int] | None:
    """Cards the ability draws for you, and life it costs you, or None for
    an ability whose draw the engine would play otherwise."""
    if not complete(ability) or ability.mode:
        return None
    draws = [effect for effect in ability.effects if effect.verb == "draw"]
    # Blade Juggler's "deals 1 damage to you" is life the draw costs too.
    losses = [effect for effect in ability.effects if effect.verb == "lose_life"
              or (effect.verb == "damage" and effect.arg("who") == "you")]
    rest = [effect for effect in ability.effects
            if effect not in draws and effect not in losses]
    if len(draws) != 1 or len(losses) > 1 or any(
            effect.verb not in _DRAW_COMPANIONS for effect in rest):
        return None
    n = draws[0].arg("n", 1)
    life = losses[0].arg("n", 1) if losses else 0
    if draws[0].arg("player") or not isinstance(n, int) or not isinstance(life, int) or (
            losses and losses[0].arg("player")):
        return None
    return n, life


def draws(profile, abilities, card) -> None:
    """Cards drawn the reader did not read: as a spell resolves, or as the
    card itself enters - Baleful Strix, Mulldrifter, Abundant Growth - which
    the engine plays as a trigger as the card arrives, with the life it costs
    (``resolves``, as Brass's Bounty's Treasure).

    Taken from an ability read whole, not a mode, that draws you a fixed
    number of cards beside nothing that touches your hand or library.
    """
    found = []
    for ability in abilities:
        arrives = (ability.kind == b.RESOLVES and profile.kind in _ONE_SHOT) or (
            ability.kind == b.TRIGGERED and ability.event == _SELF_ENTERS
            and profile.kind not in _ONE_SHOT)
        if arrives and any(effect.verb == "draw" for effect in ability.effects):
            found.append(ability)
    if len(found) != 1 or (drawn := _drawn(found[0])) is None:
        return
    n, life = drawn
    if profile.kind in _ONE_SHOT:
        # A spell's life is no field the engine reads from the reader.
        if profile.draws_cards is None and not profile.draws_x and not life:
            take(profile, "draws_cards", n)
        return
    if any(trigger.get("draw") for trigger in profile.triggers or ()):
        return
    entry = {"event": "resolves", "draw": n, **({"life": life} if life else {})}
    take(profile, "triggers", [*(profile.triggers or ()), entry])


def connives(profile, abilities, card) -> None:
    """A permanent that connives as it enters - A.I.M. Scientists: it draws
    a card, discards one, and grows when that was no land (card model PR 19).
    Our reader reads connive nowhere.

    Taken from an ability read whole that does nothing else and makes the
    card itself connive; Mob Lookout's "target creature connives" is another
    creature's.
    """
    if profile.kind in _ONE_SHOT:
        return
    found = [ability for ability in abilities
             if ability.kind == b.TRIGGERED and ability.event == _SELF_ENTERS
             and any(effect.verb == "connive" for effect in ability.effects)]
    if len(found) != 1 or not complete(found[0]) or found[0].mode:
        return
    effects = found[0].effects
    if len(effects) != 1 or effects[0].arg("target") or effects[0].arg("n", 1) != 1:
        return
    if any(trigger.get("connive") for trigger in profile.triggers or ()):
        return
    take(profile, "triggers", [*(profile.triggers or ()), {"event": "resolves", "connive": 1}])


#: What a card does that the goldfish plays, when Forge reads it and the
#: engine does not: said, where it said nothing (card model phase 1).
_SILENT = {
    "draw": gettext_noop("draws cards in a way that is not read yet"),
    "treasure": gettext_noop("makes Treasure in a way that is not read yet"),
    "land_search": gettext_noop("searches for lands in a way that is not read yet"),
}


def _family(ability: b.Ability) -> str | None:
    for effect in ability.effects:
        if effect.verb == "draw" and not effect.arg("player"):
            return "draw"
        if effect.verb == "create_token" and effect.arg("token") == "treasure" \
                and effect.arg("owner") in _YOURS:
            return "treasure"
        if effect.verb == "move" and effect.arg("from") == "library" \
                and effect.arg("to") == "battlefield" and not effect.arg("player") \
                and "Land" in str(effect.arg("what", "")):
            return "land_search"
    return None


def _played(profile, family: str) -> bool:
    if family == "draw":
        return bool(profile.draws_cards or profile.draws_x
                    or any(trigger.get("draw") for trigger in profile.triggers or ()))
    if family == "treasure":
        return bool(profile.treasures or any(
            trigger.get("treasures") for trigger in profile.triggers or ()))
    return profile.land_search is not None


def unread(profile, abilities, card) -> None:
    """Say what the card does that neither the reader nor Forge's blocks gave
    the engine: a draw, a Treasure or a land search as the spell resolves or
    the card enters - under a condition or by a count no block holds yet, or
    beside something the engine would leave out (a loot's discard).
    Without it the card would read as complete and play without it.

    Only where nothing else is said about the card: a reason already given
    covers the reading the user looks at.
    """
    if profile.review_reasons:
        return
    for ability in abilities:
        arrives = (ability.kind == b.RESOLVES and profile.kind in _ONE_SHOT) or (
            ability.kind == b.TRIGGERED and ability.event == _SELF_ENTERS)
        family = _family(ability) if arrives and not ability.mode else None
        if family is None or _played(profile, family):
            continue
        profile.review_reasons = [_SILENT[family]]
        profile.needs_review = True
        return


#: The families, in order: each reads the blocks and the profile, and takes
#: what its rule allows. `unread` comes last: it says what none of them took.
FAMILIES: list[Callable] = [mana, enters_tapped, land_search, treasures, draws, connives,
                            unread]


def merge(profile, abilities, card) -> None:
    """Take from Forge's ``abilities`` what our reader left open (in place)."""
    if not abilities:
        return
    for family in FAMILIES:
        family(profile, abilities, card)
