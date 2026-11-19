"""Which drawing stands beside a role, a theme or a card type (deck page v2, PR C).

One vocabulary on every part of the deck page: a card's **roles** (what it
does: the categories of `simulations.report.SEEN_ROLES` and the strategy and
mechanic roles beside them) and a deck's **themes** (what it is built
around, `cards.themes.THEMES`, each made of roles). The drawings live in
`core/icons/`; a theme that is one role at heart shares that role's drawing.

Every value here must be a drawing: tests/test_icons.py checks it.
"""

#: A role's key - a category, strategy or mechanic - to its drawing.
ROLE = {
    "ramp": "ramp", "draw": "draw", "removal": "removal", "wipe": "wipe",
    "tutor": "tutor", "counterspell": "counterspell", "protection": "protection",
    "recursion": "recursion",
    "sac_outlet": "sac-outlet", "reanimate": "reanimate", "drain_payoff": "drain",
    "steal": "steal", "discard": "discard", "cost_reducer": "cost-reducer",
    "evasion": "evasion",
    "lifegain": "lifegain", "land_search": "land-search", "treasure": "treasure",
    "tokens": "tokens", "death_trigger": "death-trigger", "tax": "tax",
}

#: A printed card type to its drawing.
TYPE = {kind: kind for kind in ("creature", "planeswalker", "battle", "artifact",
                                "enchantment", "instant", "sorcery", "land")}

#: A theme's key to its drawing. The typal theme ("Angel") takes the creature's.
THEME = {
    "theft": "steal", "aristocrats": "aristocrats", "tokens": "tokens",
    "reanimator": "reanimate", "graveyard": "graveyard", "spellslinger": "spellslinger",
    "counters": "counters", "landfall": "landfall", "voltron": "voltron",
    "lifegain": "lifegain", "artifacts": "artifact", "enchantments": "enchantment",
    "treasure": "treasure", "typal": "creature",
}


def for_role(key: str) -> str:
    return ROLE.get(key, "")


def for_type(kind: str) -> str:
    return TYPE.get(kind, "")


def for_theme(key: str) -> str:
    return THEME.get(key, "")
