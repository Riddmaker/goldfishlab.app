"""A card's mechanisms as one field (card model phase 4, PR 16).

Until PR 16 a `DerivedProfile` kept what the engine plays - a land search,
a tutor, triggers, a mana ability's rules - in some forty columns, one per
mechanism a P19 round added; each new mechanism meant a migration. PR 16
keeps them in one JSON field, ``blocks``, keyed by the ids corrections
already use (`simulations.patches`): a primitive's key where one covers the
column (``land_search``, ``triggers``; ``tutor`` holds its four columns),
else the column's own name.

PR 16a wrote ``blocks`` beside the columns; since PR 16b the columns are
gone and each is a property of the same name over ``blocks``
(`column_property`), so the reader, the adapter, the editor and
`simulations.primitives` read and write a profile as before.

`pack` turns an object with the columns' attributes into the field, leaving
defaults out; `unpack` turns the field back into every column. The
migrations use both. No Django import.
"""

from typing import Any

#: The mechanism columns with their defaults, in the model's order. A list
#: default is written as ``list``: a fresh one each time.
COLUMNS: dict[str, Any] = {
    "enters_tapped": False,
    #: Exactly what one use of the mana ability adds, when the text names every
    #: symbol: a Signet's `{U}{B}` is `{"U": 1, "B": 1}` - both colours, not a
    #: choice between them. Null when it IS a choice ("{U} or {B}", "one mana
    #: of any color"), which the deck's colours settle in the adapter.
    "mana_produces": None,
    #: Generic mana the ability costs beside `{T}`: 1 for a Signet. Null means
    #: none. Until the 2026-09-25 review this was not read at all, so every
    #: Signet tapped for two at no cost (trap 49).
    "mana_activation": None,
    #: False for "This artifact doesn't untap during your untap step" - Mana
    #: Vault and the Monoliths make their mana once, not every turn.
    "mana_untaps": True,
    #: P19 R4: "Activate only if you control ..." on the ability above -
    #: {"if": {"kind": control_type | lands | artifacts, ...}, "otherwise":
    #: {"amount", "produces", "activation"} | null}. `otherwise` is what the
    #: card taps for while the condition does not hold: a Tainted land's {C},
    #: nothing for Temple of the False God. P19 R18: kind "power" (a creature
    #: with printed power `count` or more), and "also": [{"if", "amount",
    #: "produces", "activation"}, ...] for further conditional abilities
    #: (Nimbus Maze). See `cards.profiles._mana_production`.
    "mana_condition": None,
    #: P19 R4: a mana rule the engine already plays, read off the text -
    #: {"rule": type_adding | double_subtype | per_controlled, "subtype",
    #: "activation", "color"}. Urborg, Crypt Ghast, Cabal Coffers. Until R4
    #: only a built-in annotation could set one. See `cards.profiles._mana_rule`.
    "mana_rule": None,
    #: P19 R6: a filter or converter beside the plain ability - {"pays_with":
    #: "WB" for {W/B} or "" for {1}, "amount", "offers": the colours of a
    #: choice ("" = any), "produces": exact mana or null}. Fetid Heath, Study
    #: Hall. See `cards.profiles._mana_production`.
    "mana_filter": None,
    #: P19 R17: "Spend this mana only to cast ..." on the ability above -
    #: {"spells": [{"types", "subtypes", "legendary", "colorless",
    #: "multicolored", "noncreature", "chosen_type"}, ...] (any one of them),
    #: "otherwise": {"amount", "produces", "activation"} | null}. Cavern of
    #: Souls; `otherwise` is its {C}. See `cards.profiles._spend_only`.
    "mana_spend_only": None,
    #: P19 R18: the ability above makes the colour chosen as the card entered
    #: ("one mana of the chosen color": Heraldic Banner, Valgavoth's Lair).
    #: The deck settles which; a "chosen_color" spell filter in
    #: `mana_spend_only` means that colour too (Throne of Eldraine).
    "mana_chosen_color": False,
    #: P19 R7: Treasure tokens it makes as it resolves (a spell's sentence, a
    #: permanent's "When this creature enters"). See `cards.profiles._treasures`.
    "treasures": 0,
    #: Engine v33: the tokens `treasures` counts are Eldrazi Spawn or Scion,
    #: each sacrificed for {C} (Kozilek's Predator, Skittering Invasion).
    "spawn": False,
    #: P19 R14: Lander tokens it makes the same way. See `cards.profiles._landers`.
    "landers": 0,
    #: P19 R7: "As an additional cost to cast this spell, discard a card."
    "discard_cost": 0,
    #: P19 R15: every other additional cost of the card's own, as the ways it
    #: can be paid: [{"sacrifice": ["creature"], "filter": "", "life": 0,
    #: "life_x": false, "discard": 0, "mana": "", "exile_from_graveyard": ""}].
    #: Null: none, or none read. See `cards.profiles._additional_cost`.
    "additional_cost": None,
    #: P19 R15: a mana ability whose cost sacrifices another permanent -
    #: Ashnod's Altar, Phyrexian Tower: {"sacrifice": ["creature"], "filter": "",
    #: "amount": 2, "produces": {"C": 2} or null for a choice, "taps": false}.
    "sacrifice_mana": None,
    #: P19 R15: "Exile this card from your hand: Add {G}" - played as a ritual
    #: that costs nothing and goes to exile (Elvish Spirit Guide).
    "mana_from_hand": False,
    #: P19 R19: mana for tapping other permanents, without the card's own {T}:
    #: Relic of Legends, Urza - {"types": ["artifact"], "filter": "legendary",
    #: a colour letter or "", "amount": 1, "produces": {"U": 1} or null for a
    #: choice, "spend_only": spell filters or null, "token": artifact tokens it
    #: makes as it enters}. Null: none.
    "tap_mana": None,
    #: P19 R19: "discard_land" (Mox Diamond enters only so), "imprint" (Chrome
    #: Mox makes the exiled card's colours) or "".
    "mox": "",
    #: P19 R19: cards milled each time it is tapped for mana (Millikin).
    "mana_mills": 0,
    #: P19 R20: mana kept as counters, energy or a Food - Pentad Prism,
    #: Aether Hub, Everflowing Chalice: {"use", "kind", "per_use", "amount",
    #: "produces", "start", "start_from", "kick", "grows", "empty", "target",
    #: "once"}, see `cards.profiles._read_store`. Null: none.
    "mana_store": None,
    #: P19 R21: +1/+1 counters and what grows with them - Gyre Sage, Rishkar,
    #: Kami of Whispered Hopes, Growing Rites of Itlimoc: {"enters",
    #: "enters_x", "grows", "adapt", "bonus", "puts", "draws_on_greatest",
    #: "look"}, see `cards.profiles._growth`. Null: none.
    "growth": None,
    #: P19 R22: what happens in combat - Sword of the Animist, Goldspan
    #: Dragon, Ragavan: {"triggers": [{"event", "who", "types", "treasures",
    #: "mana", "search", "most_life", "extra"}], "equip", "aura", "germ",
    #: "treasure_bonus", "vehicle"}, see `cards.profiles._combat`. Null: none.
    "combat": None,
    #: P19 R16: the triggers that make Treasure, mana or cards - Lotus Cobra,
    #: Storm-Kiln Artist, Smothering Tithe: a list of {"event", "filter",
    #: "treasures", "spawn", "mana", "color", "draw", "life", "tax"}.
    "triggers": list,
    "cost_reduction": None,
    "draws_cards": None,
    #: P19 R8: cards discarded right after that draw - "Draw two cards, then
    #: discard two cards" (Faithless Looting).
    "discards_after": 0,
    #: P19 R8: cards put back on top of the library after that draw -
    #: Brainstorm draws three, then puts two back.
    "puts_back": 0,
    #: P19 R9: "Draw X cards" - Stroke of Genius, Blue Sun's Zenith.
    "draws_x": False,
    #: P19 R8: the spree mode the draw sits in, paid on top of the printed
    #: cost: "{B}{B}" on Insatiable Avarice. Empty: none.
    "extra_cost": "",
    #:
    #: Split deliberately. The community DAG knows which zone a tutor searches
    #: to - `tutor-to-hand` covers 633 cards, `tutor-to-graveyard` 35 - and
    #: never how many cards it finds. So the tag establishes the zone and the
    #: regex establishes the number, and where the regex cannot, `tutor_count`
    #: stays null and the card is a gap. Guessing 1 here would have been the
    #: single most tempting wrong answer in the whole module: it is right often
    #: enough to look fine and silently wrong on every Buried Alive.
    #:
    #: `battlefield` is stored even though the engine has no such move, because
    #: the honest report is "this tutors onto the battlefield and we cannot do
    #: that", not a tutor quietly redirected to the graveyard.
    #: hand, graveyard, battlefield or top. Blank means not a tutor.
    "tutor_to": "",
    #: Null means unresolved, never 'one'.
    "tutor_count": None,
    #: Engine card kind the search is restricted to. Blank means any.
    "tutor_kind": "",
    #: P19 R12: for `tutor_to` "top" (Vampiric Tutor), {"types": [...]}: the card
    #: types it may find, any of them; empty for any card.
    #: P19 R10: what else a search onto the battlefield is limited to - {"color":
    #: "G" for "a green creature card", "max_mv": "X" or a number for "with mana
    #: value X or less"}. Green Sun's Zenith, Chord of Calling. Null: nothing.
    "tutor_filter": None,
    #: P19 R2: a search for lands that puts them onto the battlefield - a ramp
    #: spell, a fetch land, Wood Elves - read whole off the text, or null.
    #: Keys: battlefield, hand (how many go where), tapped, basic, types (land
    #: subtypes it may find; empty = any), life, when (cast | enters | play),
    #: sacrifice (the card itself goes), untap_at (Fabled Passage: untapped
    #: once you control that many lands). See `cards.profiles._land_search`.
    "land_search": None,
    #: P19 R3: when `enters_tapped`, the condition under which it does not -
    #: {"kind": control_type | lands | opponents | reveal | pay_life | permanent |
    #: turn | opponent_lands | life_at_most, ...} (the last four since P19 R12).
    #: See `cards.profiles._tapped_unless`. Null: none, or none it could read.
    "tapped_unless": None,
    #: Necropotence. A static "Skip your draw step." on the card itself - not
    #: Fatigue, which makes somebody *else* skip one, and not Ivory Gargoyle,
    #: which skips exactly one. The distinction is why this is read off the
    #: printed sentence rather than off the `skip-draw-step` tag, which files
    #: all three together.
    "skips_draw_step": False,
    #: The pronoun check: a cost you pay, versus a payoff you inflict.
    "self_life_loss": None,
    "opponent_life_loss": None,
}

#: Columns kept together under one key: the tutor primitive's four.
GROUPS: dict[str, tuple[str, ...]] = {
    "tutor": ("tutor_to", "tutor_count", "tutor_kind", "tutor_filter"),
}

_GROUP_OF = {column: key for key, columns in GROUPS.items() for column in columns}


def default(column: str):
    """A column's default value; a fresh list where the default is one."""
    value = COLUMNS[column]
    return value() if value is list else value


def pack(profile) -> dict:
    """The profile's mechanism columns as the ``blocks`` field."""
    found: dict[str, Any] = {}
    for column in COLUMNS:
        value = getattr(profile, column)
        if value == default(column):
            continue
        key = _GROUP_OF.get(column)
        if key is None:
            found[column] = value
        else:
            found.setdefault(key, {})[column] = value
    return found


def unpack(blocks: dict) -> dict:
    """The ``blocks`` field as every mechanism column, defaults included."""
    out = {column: default(column) for column in COLUMNS}
    for key, value in blocks.items():
        if key in GROUPS:
            out.update(value)
        else:
            out[key] = value
    return out


def column_property(column: str) -> property:
    """The column as a property of the profile over its ``blocks``: reading
    gives the default where the field says nothing, writing the default
    takes the key out."""
    key = _GROUP_OF.get(column)

    def read(profile):
        holder = profile.blocks.get(key, {}) if key else profile.blocks
        return holder[column] if column in holder else default(column)

    def write(profile, value):
        holder = profile.blocks.setdefault(key, {}) if key else profile.blocks
        if value == default(column):
            holder.pop(column, None)
            if key and not holder:
                profile.blocks.pop(key)
        else:
            holder[column] = value

    return property(read, write, doc=f"``{column}``, kept in ``blocks`` (card model PR 16).")
