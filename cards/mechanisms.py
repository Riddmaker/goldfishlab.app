"""A card's mechanisms as one field (card model phase 4, PR 16).

Until PR 16 a `DerivedProfile` kept what the engine plays - a land search,
a tutor, triggers, a mana ability's rules - in some forty columns, one per
mechanism a P19 round added; each new mechanism meant a migration. PR 16
keeps them in one JSON field, ``blocks``, keyed by the ids corrections
already use (`simulations.patches`): a primitive's key where one covers the
column (``land_search``, ``triggers``; ``tutor`` holds its four columns),
else the column's own name.

PR 16a writes ``blocks`` beside the columns, so a rollback finds them as
they were; PR 16b drops the columns and reads ``blocks`` alone.

`pack` turns a profile's columns into the field, leaving defaults out;
`unpack` turns the field back into every column. No Django import beyond
the model's values: the functions take any object with the attributes.
"""

from typing import Any

#: The mechanism columns with their defaults, in the model's order. A list
#: default is written as ``list``: a fresh one each time.
COLUMNS: dict[str, Any] = {
    "enters_tapped": False,
    "mana_produces": None,
    "mana_activation": None,
    "mana_untaps": True,
    "mana_condition": None,
    "mana_rule": None,
    "mana_filter": None,
    "mana_spend_only": None,
    "mana_chosen_color": False,
    "treasures": 0,
    "landers": 0,
    "discard_cost": 0,
    "additional_cost": None,
    "sacrifice_mana": None,
    "mana_from_hand": False,
    "tap_mana": None,
    "mox": "",
    "mana_mills": 0,
    "mana_store": None,
    "growth": None,
    "combat": None,
    "triggers": list,
    "cost_reduction": None,
    "draws_cards": None,
    "discards_after": 0,
    "puts_back": 0,
    "draws_x": False,
    "extra_cost": "",
    "tutor_to": "",
    "tutor_count": None,
    "tutor_kind": "",
    "tutor_filter": None,
    "land_search": None,
    "tapped_unless": None,
    "skips_draw_step": False,
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
