"""Forge's card scripts as blocks: the importer (card model rebuild, phase 1).

Each script of the pinned commit (`cards.forge.PACK`) is translated into the
blocks of :mod:`simulation.blocks` and stored per card as a `ForgeScript`.
The translation knows a fixed **vocabulary** - the API of an ability, the
mode of a trigger or static ability, the event of a replacement, a cost atom,
a keyword - of 207 entries. It holds each of the 250 constructs the most
Commander-legal cards use (an API counts once for an ability, a spell and a
sub-ability), and 85 % of those cards use nothing else. A construct outside
it becomes an ``unmodeled`` effect naming it, and a condition or a count
the blocks cannot hold yet is listed in the ability's ``unread``: nothing is
dropped silently.

The blocks are free of any deck: a choice of colour stays a choice
(``any``, ``identity``, ``WU``) until a deck is known.

Phase 1 stores the blocks and compares them with our reader
(`manage.py forge_disagreements`); a later step takes over the readings of
the families checked by hand. The engine does not read them yet.

Scripts are data: parsed as text, never executed.
"""

import re
from datetime import datetime
from pathlib import Path

from cards import forge
from simulation import blocks as b

#: Raised with every change to how a script is translated, so the next run
#: translates every script again although the pin did not move.
TRANSLATION = 4

#: More scripts than this share failing to translate stops the run, and the
#: blocks of the last good run stay.
MAX_FAILURES = 0.01

_BATCH = 1_000

# --- the vocabulary ---------------------------------------------------------------

#: The API of an ability (``AB$ Draw``, ``SP$ Draw``, ``DB$ Draw``), and the
#: verb it becomes. ``None``: bookkeeping that does nothing in a game.
VERBS = {
    "AddPhase": "add_phase", "AlterAttribute": "alter_attribute", "Amass": "amass",
    "Animate": "animate", "AnimateAll": "animate_all", "Attach": "attach",
    "BecomeMonarch": "become_monarch", "Branch": "branch", "ChangeZone": "move",
    "ChangeZoneAll": "move_all", "Charm": b.CHOOSE, "ChooseCard": "choose_card",
    "ChooseColor": "choose_color",
    "ChooseNumber": "choose_number", "ChoosePlayer": "choose_player",
    "ChooseSource": "choose_source", "ChooseType": "choose_type", "Cleanup": None,
    "Clone": "clone", "Connive": "connive", "CopyPermanent": "copy_permanent",
    "CopySpellAbility": "copy_spell", "Counter": "counter_spell", "DamageAll": "damage_all",
    "DamageResolve": None, "DealDamage": "damage", "DelayedTrigger": "delayed_trigger",
    "Destroy": "destroy", "DestroyAll": "destroy_all", "Dig": "dig", "DigUntil": "dig_until",
    "Discard": "discard", "Draw": "draw", "Effect": "effect", "Fight": "fight",
    "FlipCoin": "flip_coin", "GainControl": "gain_control", "GainLife": "gain_life",
    "GenericChoice": "choice", "Goad": "goad", "ImmediateTrigger": "immediate_trigger",
    "Investigate": "investigate", "LoseLife": "lose_life", "Mana": "add_mana",
    "ManaReflected": "add_mana", "Mill": "mill", "MultiplyCounter": "multiply_counter",
    "PeekAndReveal": "peek", "Play": "play", "PreventDamage": "prevent_damage",
    "Proliferate": "proliferate", "Pump": "pump", "PumpAll": "pump_all",
    "PutCounter": "put_counter", "PutCounterAll": "put_counter_all",
    "Regenerate": "regenerate", "RemoveCounter": "remove_counter", "RepeatEach": "repeat_each",
    "ReplaceEffect": "replace_effect", "RevealHand": "reveal_hand",
    "RingTemptsYou": "ring_tempts", "RollDice": "roll_dice", "Sacrifice": "sacrifice",
    "SacrificeAll": "sacrifice_all", "Scry": "scry", "SetState": "set_state",
    "Shuffle": "shuffle", "StoreSVar": None, "Surveil": "surveil", "Tap": "tap",
    "TapAll": "tap_all", "Token": "create_token", "Untap": "untap", "UntapAll": "untap_all",
    "WinsGame": "win_game",
}

#: APIs that make mana: an ``AB$`` of them is a mana ability.
MANA_APIS = frozenset({"Mana", "ManaReflected"})

#: A trigger's mode, and the event it becomes (zone changes are told apart
#: in `_zone_event`).
EVENTS = {
    "AbilityCast": "ability_cast", "Always": "always", "AttackerBlocked": "blocked",
    "AttackerBlockedByCreature": "blocked", "AttackerUnblocked": "unblocked",
    "AttackersDeclared": "attackers_declared",
    "AttackersDeclaredOneTarget": "attackers_declared", "Attacks": "attacks",
    "BecomesTarget": "becomes_target", "Blocks": "blocks", "ChangesZone": "zone_change",
    "ChangesZoneAll": "zone_change_all", "CounterAddedOnce": "counter_added",
    "Cycled": "cycled", "DamageDealtOnce": "damage", "DamageDone": "damage",
    "DamageDoneOnce": "damage", "Discarded": "discarded", "Drawn": "draws",
    "LandPlayed": "land_played", "LifeGained": "life_gained", "Phase": "phase",
    "Sacrificed": "sacrificed", "SpellCast": "cast", "Taps": "taps",
    "TapsForMana": "tapped_for_mana", "TurnFaceUp": "turned_face_up",
}

#: A trigger's ``Phase$``, and the event it becomes.
PHASES = {"Upkeep": "upkeep", "End of Turn": "end_step", "Draw": "draw_step",
          "Main1": "main", "Main2": "second_main", "BeginCombat": "combat",
          "Cleanup": "cleanup"}

#: A static ability's mode, and its verb.
STATICS = {
    "AlternativeCost": "alternative_cost", "CantAttack": "cant_attack",
    "CantAttack,CantBlock": "cant_attack_or_block", "CantBeActivated": "cant_be_activated",
    "CantBeCast": "cant_be_cast", "CantBlock": "cant_block", "CantBlockBy": "evasion",
    "CastWithFlash": "cast_with_flash", "Continuous": "continuous",
    "MinMaxBlocker": "min_max_blockers", "MustAttack": "must_attack",
    "OptionalCost": "optional_cost", "Panharmonicon": "extra_trigger",
    "RaiseCost": "raise_cost", "ReduceCost": "reduce_cost",
}

#: A replacement's event, and its verb.
REPLACEMENTS = {"Counter": "replace_counters", "DamageDone": "replace_damage",
                "Draw": "replace_draw", "Moved": "replace_move", "Untap": "replace_untap"}

#: A cost atom as Forge names it, and ours.
COSTS = {
    "tap": "tap", "untap": "untap", "mana": "mana", "Sac": "sacrifice",
    "SubCounter": "remove_counter", "AddCounter": "add_counter", "Discard": "discard",
    "tapXType": "tap_other", "PayLife": "life", "ExileFromGrave": "exile_from_graveyard",
    "Exile": "exile", "Return": "return_to_hand", "PayEnergy": "energy",
    "ExileFromHand": "exile_from_hand", "Draw": "draw", "Exert": "exert",
    "RemoveAnyCounter": "remove_counter", "Reveal": "reveal",
}

#: Keywords, by the name before their first ``:`` and without ``from ...``.
KEYWORDS = frozenset({
    "Affinity", "AlternateAdditionalCost", "Bestow", "Bushido", "Buyback", "Cascade",
    "Changeling", "Chapter", "Choose a Background", "Class", "Convoke", "Crew",
    "Cumulative upkeep", "Cycling", "Daybound", "Deathtouch", "Defender", "Devoid",
    "Disguise", "Double Strike", "ETBReplacement", "Echo", "Enchant", "Entwine", "Equip",
    "Evoke", "Exalted", "Fear", "First Strike", "Flash", "Flashback", "Flying", "Foretell",
    "Haste", "Hexproof", "Indestructible", "Infect", "Kicker", "Landwalk", "Lifelink",
    "Madness", "Menace", "Morph", "Mutate", "Nightbound", "Ninjutsu", "Partner",
    "Partner with", "Plot", "Protection", "Prowess", "Reach", "Rebound", "Shadow",
    "Shroud", "Start your engines", "StickerSheet", "Storm", "Suspend", "Toxic", "Trample",
    "TypeCycling", "Unearth", "Vigilance", "Ward", "etbCounter",
    "You may choose not to untap CARDNAME during your untap step.",
})


def vocabulary() -> list[str]:
    """Every construct the translation knows, as `cards.forge.constructs` names them."""
    words = [f"{line}:{api}" for api in VERBS for line in ("AB", "SP", "DB")]
    words += [f"T:{mode}" for mode in EVENTS]
    words += [f"S:{mode}" for mode in STATICS]
    words += [f"R:{event}" for event in REPLACEMENTS]
    words += [f"cost:{atom}" for atom in COSTS]
    words += [f"K:{name}" for name in KEYWORDS]
    return sorted(words)


# --- parameters -------------------------------------------------------------------

#: Parameters that make an ability conditional in a way no block holds yet.
_CONDITIONS = ("Condition", "CheckSVar", "IsPresent", "ActivationLimit")
#: Parameters that say when, from where or by whom it is used, which no block
#: holds yet either. Not what the goldfish does anyway: act on its own turn,
#: from the battlefield, at sorcery speed ("Activate only as a sorcery").
_WHEN = ("OpponentTurn", "ActivationPhases", "ActivationFirstCombat", "Exhaust", "AtEOT")
_WHEN_DEFAULTS = {"ActivationZone": ("Battlefield",), "Activator": ("You",),
                  "PlayerTurn": ("True", "You")}

#: Where an amount stands, by API: the first found is taken.
_AMOUNTS = ("NumCards", "Amount", "NumDmg", "LifeAmount", "CounterNum", "TokenAmount",
            "ChangeNum", "ScryNum", "SacValid", "Num")

#: ``Produced$`` words for a choice that only a deck decides.
_CHOICES = {"Any": "any", "AnyColor": "any", "ColorIdentity": "identity",
            "Chosen": "chosen"}

_TOKEN_SCRIPT = re.compile(r"^c_a_([a-z]+)_sac$")
_ANY_TYPE = frozenset({"Card", "Permanent", "Any", "Self", "CARDNAME"})


class _Reading:
    """What one translation could not hold, gathered as it goes."""

    def __init__(self, svars: dict):
        self.svars = svars
        self.unread: list[str] = []

    def amount(self, value: str):
        """An amount: a number, ``count:<what>`` for a count, or ``x``."""
        if value.isdigit():
            return int(value)
        body = self.svars.get(value, "")
        if body.startswith("Count$"):
            head = re.match(r"Count\$([A-Za-z]+)", body)
            return f"count:{head.group(1) if head else body[6:]}"
        if value == "X" and not body:
            return "x"
        self.unread.append(f"amount:{value}")
        return value


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name.replace(" ", "")).lower()


def _types(valid: str) -> frozenset[str]:
    """``Creature.YouCtrl,Artifact`` -> {"creature", "artifact"}; ``Card`` and
    ``Permanent`` name no type."""
    words = {part.split(".")[0].split("+")[0].strip() for part in valid.split(",")}
    return frozenset(word.lower() for word in words if word and word not in _ANY_TYPE)


def _whose(valid: str) -> str:
    if "Card.Self" in valid or valid == "Self":
        return "self"
    if "OppCtrl" in valid or "OppOwn" in valid or valid.startswith("Opponent"):
        return "opponent"
    if "YouCtrl" in valid or "YouOwn" in valid or valid == "You":
        return "you"
    return ""


def _colors(produced: str) -> str:
    """``Combo W U`` -> "WU" (one of), ``G`` -> "G", ``Any`` -> "any"."""
    words = produced.replace(",", " ").split()
    if words[:1] == ["Combo"]:
        words = words[1:]
    for word in words:
        if word in _CHOICES:
            return _CHOICES[word]
    letters = [word for word in words if word in ("W", "U", "B", "R", "G", "C")]
    return "".join(letters) if letters else produced


def _args(step: forge.Ability, reading: _Reading) -> tuple:
    params = dict(step.params)
    args: dict = {}
    for name in _AMOUNTS:
        if name in params and name != "SacValid":
            args["n"] = reading.amount(params[name])
            break
    if step.head in MANA_APIS:
        args["color"] = _colors(params.get("Produced", "")) if step.head == "Mana" \
            else "reflected"
        words = params.get("Produced", "").split()
        if step.head == "Mana" and len(words) > 1 and set(words) <= set("WUBRGC"):
            # "W U": each of them (a Signet), where "Combo W U" is one of them.
            args["all"] = True
        if params.get("RestrictValid"):
            # "Spend this mana only to cast a creature spell of the chosen type".
            args["restrict"] = params["RestrictValid"]
    elif step.head == "Token":
        script = params.get("TokenScript", "")
        match = _TOKEN_SCRIPT.match(script)
        args["token"] = match.group(1) if match else script
    elif step.head in ("ChangeZone", "ChangeZoneAll"):
        args["from"] = params.get("Origin", "").lower()
        args["to"] = params.get("Destination", "").lower()
        what = params.get("ChangeType") or params.get("ValidTgts") or params.get("Defined", "")
        if what:
            args["what"] = what
        if params.get("Tapped") == "True":
            args["tapped"] = True
        if params.get("DefinedPlayer"):
            args["player"] = params["DefinedPlayer"]
    elif step.head in ("PutCounter", "PutCounterAll", "RemoveCounter"):
        args["counter"] = params.get("CounterType", "")
    elif step.head in ("Pump", "PumpAll"):
        for name, key in (("NumAtt", "power"), ("NumDef", "toughness")):
            if name in params:
                args[key] = reading.amount(params[name].lstrip("+"))
    who = params.get("Defined", "")
    if who in ("You", "Self") and step.head not in ("ChangeZone", "ChangeZoneAll"):
        args["who"] = who.lower()
    if params.get("ValidTgts"):
        args["target"] = True
    if params.get("UnlessCost"):
        args["unless"] = params["UnlessCost"]
    if params.get("OptionalDecider") or params.get("Optional"):
        args["optional"] = True
    for name, value in params.items():
        if name.startswith(_CONDITIONS):
            reading.unread.append(f"cond:{name}")
        elif name in _WHEN or value not in _WHEN_DEFAULTS.get(name, (value,)):
            reading.unread.append(f"when:{name}")
    return tuple(sorted(args.items()))


def _effects(ability: forge.Ability, reading: _Reading) -> tuple[b.Effect, ...]:
    """The ability's chain of sub-abilities, each step one effect, in order."""
    effects = []

    def walk(step: forge.Ability):
        if step.line in ("A", "DB"):
            if step.head not in VERBS:
                effects.append(b.Effect(b.UNMODELED, (("construct", f"DB:{step.head}"),)))
            elif step.head == "Charm":
                # Modes inside a chain (a trigger that lets you choose): named,
                # not yet held as blocks.
                choices = [name for name in step.param("Choices").split(",") if name]
                effects.append(b.Effect(b.CHOOSE, (
                    ("n", reading.amount(step.param("CharmNum", "1"))), ("of", len(choices)))))
                reading.unread.append("modes")
                return
            elif VERBS[step.head] is not None:
                if step is not ability and step.param("Cost"):
                    # Paid as it resolves: Larval Scoutlander's "you may
                    # sacrifice a land. If you do, search ...".
                    reading.unread.append("cost:" + step.param("Cost"))
                verb = "enters_tapped" if step.head == "Tap" and \
                    step.param("ETB") == "True" else VERBS[step.head]
                effects.append(b.Effect(verb, _args(step, reading)))
        for sub in step.chain:
            walk(sub)

    if ability.line in ("A", "DB"):
        walk(ability)
    else:
        for sub in ability.chain:
            walk(sub)
    return tuple(effects)


# --- costs --------------------------------------------------------------------------

_ATOM = re.compile(r"^([A-Za-z]+)<([^>]*)>$")
_MANA = re.compile(r"^(?:\d+|[WUBRGCXS](?:/[WUBRGCP2])*)$")


def _costs(cost: str, reading: _Reading, *, printed: bool = False):
    """A Forge cost as atoms. ``printed``: the mana is the card's own cost
    (a spell's ``Cost$``), already on the face, and left out."""
    from simulation.manacost import parse

    atoms, mana = [], []
    for part in forge.cost_parts(cost):
        if part == "T":
            atoms.append(b.Cost("tap"))
        elif part == "Q":
            atoms.append(b.Cost("untap"))
        elif _MANA.match(part):
            mana.append("{" + part + "}")
        else:
            match = _ATOM.match(part)
            name = match.group(1) if match else part
            if name not in COSTS:
                atoms.append(b.Cost(b.UNMODELED, filter=name))
                continue
            fields = match.group(2).split("/") if match else []
            amount = reading.amount(fields[0]) if fields else 1
            what = fields[1] if len(fields) > 1 else ""
            atom = COSTS[name]
            if atom == "sacrifice" and what == "CARDNAME":
                atom, what = "sacrifice_self", ""
            elif atom == "exile" and what == "CARDNAME":
                atom, what = "exile_self", ""
            atoms.append(b.Cost(atom, amount=amount if isinstance(amount, int) else 0,
                                x=not isinstance(amount, int), types=_types(what),
                                other="Other" in what))
    if mana and not printed:
        atoms.insert(0, b.Cost("mana", mana=parse("".join(mana))))
    return tuple(atoms)


# --- abilities ----------------------------------------------------------------------


def _event(ability: forge.Ability) -> b.Event:
    params = dict(ability.params)
    mode = ability.head
    valid = params.get("ValidCard", "")
    if mode in ("ChangesZone", "ChangesZoneAll"):
        return _zone_event(params)
    if mode == "Phase":
        name = PHASES.get(params.get("Phase", ""), _snake(params.get("Phase", "phase")))
        return b.Event(name, who=_whose(params.get("ValidPlayer", "")) or "each")
    if mode in ("SpellCast", "AbilityCast"):
        return b.Event(EVENTS[mode], who=_whose(params.get("ValidActivatingPlayer", "")),
                       types=_types(valid))
    if mode == "DamageDone":
        return b.Event("damage", who=_whose(params.get("ValidSource", "")),
                       filter="combat" if params.get("CombatDamage") == "True" else "")
    return b.Event(EVENTS[mode], who=_whose(valid), types=_types(valid),
                   filter="another" if ".Other" in valid or "+Other" in valid else "")


def _zone_event(params: dict) -> b.Event:
    origin, destination = params.get("Origin", ""), params.get("Destination", "")
    valid = params.get("ValidCard", "")
    who, types = _whose(valid), _types(valid)
    other = "another" if "Other" in valid else ""
    if destination == "Battlefield":
        if types == {"land"} and who == "you":
            return b.Event("landfall")
        return b.Event("enters", filter=other, who=who, types=types)
    if origin == "Battlefield" and destination == "Graveyard":
        return b.Event("dies", filter=other, who=who, types=types)
    return b.Event("zone_change", filter=f"{origin}>{destination}".lower(), who=who,
                   types=types)


def _keyword(text: str, svars: dict, reading: _Reading):
    """A ``K:`` line as an ability."""
    name, _sep, rest = text.partition(":")
    name = name.strip()
    base = name.split(" from ")[0] if name.startswith("Protection") else name
    if base not in KEYWORDS:
        return b.Ability(b.STATIC, "", (b.Effect(b.UNMODELED, (("construct", f"K:{base}"),)),))
    if base == "ETBReplacement":
        kind, _sep, svar = rest.partition(":")
        body = svars.get(svar.split(":")[0], "")
        sub = forge._ability("DB", body, svars) if body else None
        effects = _effects(sub, reading) if sub else ()
        return b.Ability(b.REPLACEMENT, "", effects, event=b.Event("enters", who="self"))
    if base == "etbCounter":
        counter, _sep, amount = rest.partition(":")
        n = reading.amount(amount.split(":")[0] or "1")
        return b.Ability(b.REPLACEMENT, "", (b.Effect("put_counter", (
            ("counter", counter), ("n", n), ("who", "self"))),),
            event=b.Event("enters", who="self"))
    args = (("name", base),) + ((("args", rest),) if rest else ())
    return b.Ability(b.STATIC, "", (b.Effect(b.KEYWORD, args),))


def _line(ability: forge.Ability, reading: _Reading) -> list[b.Ability]:
    """One ability line of the script as one or more abilities."""
    params = dict(ability.params)
    if ability.line == "A" and ability.spell:
        found = []
        costs = _costs(params.get("Cost", ""), reading, printed=True)
        if costs:
            found.append(b.Ability(b.CAST, "", costs=costs))
        if ability.head == "Charm":
            choices = [name.strip() for name in params.get("Choices", "").split(",") if name]
            n = reading.amount(params.get("CharmNum", "1"))
            found.append(b.Ability(b.RESOLVES, "", (b.Effect(b.CHOOSE, (
                ("n", n), ("of", len(choices)))),)))
            for mode, name in enumerate(choices, 1):
                body = reading.svars.get(name, "")
                sub = forge._ability("DB", body, reading.svars) if body else None
                found.append(b.Ability(b.RESOLVES, "", _effects(sub, reading) if sub else (),
                                       mode=mode))
            return found
        if ability.head not in VERBS:
            found.append(b.Ability(b.RESOLVES, "", (b.Effect(b.UNMODELED, (
                ("construct", f"SP:{ability.head}"),)),)))
        else:
            found.append(b.Ability(b.RESOLVES, "", _effects(ability, reading)))
        return found
    if ability.line == "A":
        kind = b.MANA if ability.head in MANA_APIS else b.ACTIVATED
        if ability.head not in VERBS:
            effects = (b.Effect(b.UNMODELED, (("construct", f"AB:{ability.head}"),)),)
        else:
            effects = _effects(ability, reading)
        return [b.Ability(kind, "", effects, costs=_costs(params.get("Cost", ""), reading))]
    if ability.line == "T":
        if ability.head not in EVENTS:
            return [b.Ability(b.TRIGGERED, "", (b.Effect(b.UNMODELED, (
                ("construct", f"T:{ability.head}"),)),))]
        for name in params:
            if name.startswith(_CONDITIONS):
                reading.unread.append(f"cond:{name}")
        return [b.Ability(b.TRIGGERED, "", _effects(ability, reading), event=_event(ability))]
    if ability.line == "S":
        verb = STATICS.get(ability.head)
        effect = b.Effect(verb) if verb else \
            b.Effect(b.UNMODELED, (("construct", f"S:{ability.head}"),))
        return [b.Ability(b.STATIC, "", (effect,))]
    verb = REPLACEMENTS.get(ability.head)
    if verb is None:
        return [b.Ability(b.REPLACEMENT, "", (b.Effect(b.UNMODELED, (
            ("construct", f"R:{ability.head}"),)),))]
    event = _zone_event(params) if ability.head == "Moved" else b.Event(_snake(ability.head))
    effects = _effects(ability, reading) or (b.Effect(verb),)
    return [b.Ability(b.REPLACEMENT, "", effects, event=event)]


#: ``AlternateMode`` values whose other faces are played as well as the front:
#: either face of a modal card, either half of a split card, the adventure.
_PLAYED_FACES = frozenset({"Modal", "Split", "Adventure", "Omen"})


def translate(script: forge.Script) -> tuple[b.Ability, ...]:
    """The card's abilities as blocks, numbered by kind (their stable ids).

    The front face, and the other faces where they are played as well. The
    back of a card that transforms waits for a later phase.
    """
    faces = script.faces if script.mode in _PLAYED_FACES else script.faces[:1]
    found = []
    for face in faces:
        svars = dict(face.svars)
        for ability in face.abilities:
            reading = _Reading(svars)
            for block in _line(ability, reading):
                found.append(_with_unread(block, reading))
        for keyword in face.keywords:
            reading = _Reading(svars)
            found.append(_with_unread(_keyword(keyword, svars, reading), reading))
    slots: dict[str, int] = {}
    numbered = []
    for ability in found:
        slot = slots.get(ability.kind, 0)
        slots[ability.kind] = slot + 1
        numbered.append(_replace(ability, slot=slot))
    return tuple(numbered)


def _with_unread(ability: b.Ability, reading: _Reading) -> b.Ability:
    if not reading.unread:
        return ability
    return _replace(ability, unread=tuple(sorted(set(reading.unread))))


def _replace(ability: b.Ability, **changes) -> b.Ability:
    import dataclasses

    return dataclasses.replace(ability, **changes)


def unmodeled(abilities) -> list[str]:
    """The constructs outside the vocabulary, as the blocks name them."""
    found = set()
    for ability in abilities:
        for effect in ability.effects:
            if effect.verb == b.UNMODELED:
                found.add(effect.arg("construct"))
        for cost in ability.costs:
            if cost.atom == b.UNMODELED:
                found.add(f"cost:{cost.filter}")
    return sorted(found)


# --- the run ------------------------------------------------------------------------


class TooManyFailures(RuntimeError):
    pass


def _version() -> str:
    return f"translation {TRANSLATION}, blocks v{b.VERSION}"


def stale(path: Path | None = None) -> bool:
    """Whether the stored blocks are of another pin or another translation."""
    from cards.models import BulkImport

    path = path or forge.PACK

    last = BulkImport.objects.filter(kind=BulkImport.Kind.FORGE_SCRIPTS,
                                      status=BulkImport.Status.OK).order_by("-started_at").first()
    commit = forge.header(path)["commit"]
    return last is None or last.forge_commit != commit or \
        not last.message.startswith(_version())


def run(path: Path | None = None, *, force: bool = False, measure: bool = False):
    """Translate every script of the packed copy and store each card's blocks.

    All or nothing, in one transaction: above `MAX_FAILURES` the run fails and
    the blocks of the last good run stay. Skipped when nothing changed - the
    pin, the translation - unless ``force``. A card that lost its script loses
    its row.
    """
    from django.db import transaction

    from cards import ingest
    from cards.models import BulkImport, ForgeScript, OracleCard

    path = path or forge.PACK
    head = forge.header(path)
    commit = head["commit"]
    kind = BulkImport.Kind.FORGE_SCRIPTS
    if not force and not stale(path) and \
            ForgeScript.objects.count() and not _new_cards():
        return ingest.IngestResult(kind=kind, status=BulkImport.Status.SKIPPED,
                                   message=f"{commit[:10]}, {_version()}: unchanged")

    by_name: dict[str, list] = {}
    for pk, name, front in OracleCard.objects.values_list("pk", "name", "front_name"):
        by_name.setdefault(front or name, []).append(pk)
        if front and name != front:
            by_name.setdefault(name, []).append(pk)

    with ingest._run(kind, datetime.fromisoformat(head["date"]), measure) as (record, result), \
            transaction.atomic():
        record.forge_commit = commit
        record.save(update_fields=["forge_commit"])
        seen, matched, failed, kept, batch = 0, 0, [], set(), []
        for script in forge.scripts(path):
            seen += 1
            pks = [pk for pk in by_name.get(script.name, ()) if pk not in kept]
            if not pks:
                continue
            try:
                abilities = translate(script)
                data = b.to_json(abilities)
            except Exception as error:  # noqa: BLE001 - counted, the run decides
                failed.append(f"{script.path}: {type(error).__name__}: {error}")
                continue
            matched += 1
            unread = sorted({name for ability in abilities for name in ability.unread})
            for pk in pks:
                kept.add(pk)
                batch.append(ForgeScript(
                    oracle_card_id=pk, commit=commit, path=script.path, blocks=data,
                    blocks_version=b.VERSION, unmodeled=unmodeled(abilities), unread=unread))
            if len(batch) >= _BATCH:
                result.rows_written += _write(batch)
                batch = []
        result.rows_written += _write(batch)
        result.rows_seen = seen
        if seen and len(failed) / seen > MAX_FAILURES:
            raise TooManyFailures(f"{len(failed)} of {seen} scripts failed, first: {failed[:3]}")
        gone, _ = ForgeScript.objects.exclude(pk__in=kept).delete()
        # Scripts of cards outside the catalogue: Alchemy, tokens, schemes.
        result.rows_skipped = seen - matched - len(failed)
        result.message = (f"{_version()}; {len(kept)} cards, {len(failed)} failed, "
                          f"{gone} removed" + (f"; first failures: {failed[:3]}" if failed else ""))
    return result


def _write(batch: list) -> int:
    from cards import ingest
    from cards.models import ForgeScript

    if not batch:
        return 0
    ForgeScript.objects.bulk_create(
        batch, update_conflicts=True, unique_fields=["oracle_card"],
        update_fields=["commit", "path", "blocks", "blocks_version", "unmodeled", "unread",
                       "imported_at"])
    ingest._forget_queries()
    return len(batch)


def _new_cards() -> bool:
    """Whether cards came in after the last translation (a card ingest)."""
    from cards.models import BulkImport

    def newest(kind):
        return BulkImport.objects.filter(kind=kind, status=BulkImport.Status.OK) \
            .order_by("-started_at").values_list("finished_at", flat=True).first()

    cards = newest(BulkImport.Kind.ORACLE_CARDS)
    forge_run = newest(BulkImport.Kind.FORGE_SCRIPTS)
    return cards is not None and (forge_run is None or cards > forge_run)


# --- facts a goldfish plays, for the comparison with our reader --------------------

#: Mana of any colour, as our engine writes the choice.
ANY = "WUBRG"

_BASIC_TYPES = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
_LAND_WORDS = ("Land", *_BASIC_TYPES)
#: Colours a deck decides: read as any colour, as the engine plays them.
_ANY_COLOUR = frozenset({"any", "identity", "chosen", "reflected"})


def facts(abilities, type_line: str = "") -> dict:
    """What the card does that the goldfish plays, in five coarse facts.

    ``taps_for``: the colours its {T} mana abilities can make, ``C`` included,
    and those of its basic land types (``type_line``, which no ability spells
    out); ``draws``: cards drawn as it resolves or enters, ``x`` for a count;
    ``land_search``: a land from the library onto the battlefield;
    ``enters_tapped``; ``treasures``: it makes Treasure tokens.
    """
    taps_for = {_BASIC_TYPES[word] for word in type_line.replace("—", " ").split()
                if word in _BASIC_TYPES} if "Land" in type_line else set()
    draws: int | str = 0
    land_search = enters_tapped = treasures = False
    for ability in abilities:
        atoms = {cost.atom for cost in ability.costs}
        # What {T} makes, life aside (Hashep Oasis), and restricted or not:
        # the reader's flat mana. An ability that also takes mana counts only
        # when it makes more than it takes (a Signet), not when it converts.
        if ability.kind == b.MANA and "tap" in atoms and atoms <= {"tap", "life", "mana"}:
            for effect in ability.effects:
                if effect.verb == "add_mana" and _nets_mana(ability, effect):
                    color = effect.arg("color", "")
                    taps_for |= set(ANY) if color in _ANY_COLOUR else \
                        {letter for letter in color if letter in ANY + "C"}
        enters = ability.kind == b.TRIGGERED and ability.event == b.Event("enters", who="self")
        if (ability.kind == b.RESOLVES or enters) and draws != "x":
            for effect in ability.effects:
                if effect.verb == "draw" and ability.mode == 0 and draws != "x":
                    n = effect.arg("n", 1)
                    draws = draws + n if isinstance(n, int) else "x"
        for effect in ability.effects:
            if effect.verb == "move" and effect.arg("from") == "library" and \
                    effect.arg("to") == "battlefield" and not effect.arg("player") and \
                    any(word in str(effect.arg("what", "")) for word in _LAND_WORDS):
                land_search = True
            if effect.verb == "create_token" and effect.arg("token") == "treasure":
                treasures = True
            if ability.kind == b.REPLACEMENT and effect.verb == "enters_tapped" and \
                    ability.event == b.Event("enters", who="self"):
                enters_tapped = True
    return {"taps_for": "".join(sorted(taps_for)), "draws": draws,
            "land_search": land_search, "enters_tapped": enters_tapped,
            "treasures": treasures}


def _nets_mana(ability, effect) -> bool:
    """Whether ``effect`` makes more mana than ``ability``'s cost takes."""
    paid = sum(cost.mana.generic + cost.mana.colorless + sum(n for _, n in cost.mana.pips)
               + len(cost.mana.hybrid) + len(cost.mana.phyrexian)
               for cost in ability.costs if cost.atom == "mana" and cost.mana is not None)
    if not paid:
        return True
    n = effect.arg("n", 1)
    made = (n if isinstance(n, int) else 1) * (len(effect.arg("color", "")) if effect.arg(
        "all") else 1)
    return made > paid
