"""The card shop (B-16): the rooms, what is in them, and what happens.

Data, not code: `static/js/shop.js` plays whatever this describes, and the
page hands it over with `json_script`. Every text goes through gettext, so
the shop speaks the page's language.

A room is a picture (`static/img/shop/<room>.svg`, drawn by
`scripts/shop_art.py`) and its spots: rectangles in the picture's own pixels
(320 x 180), each a button on the page. A spot has steps; the first step
whose condition holds is the one that happens. A condition is any of `has`
(an item carried), `lacks`, `flag` (something that happened) and `unflag`.
A step says its line, and may `give` or `take` an item, `set` a flag, `go`
to another room or `end` the game.

A prop is a small picture shown on top of the room while its condition holds
(the deck box under the chair, until it is picked up).

The shop, its name and its shopkeeper are made up; the rooms follow the real
interior once it is known (docs/phases/shop-adventure.md).
"""

from django.templatetags.static import static
from django.utils.translation import gettext as _

#: The made-up name, a placeholder until the owner picks one.
SHOP_NAME = "The Brass Lantern"
WIDTH, HEIGHT = 320, 180


def items() -> dict:
    return {
        "deckbox": _("A deck box with a goldfish sticker"),
        "sleeves": _("A pack of sleeves"),
    }


def rooms() -> dict:
    return {
        "floor": {
            "name": _("The shop floor"),
            "picture": static("img/shop/floor.svg"),
            "props": [
                {"id": "deckbox", "picture": static("img/shop/deckbox.svg"),
                 "x": 236, "y": 168, "w": 7, "h": 6, "if": {"unflag": "found"}},
            ],
            "spots": [
                {"id": "door", "name": _("The door"), "x": 8, "y": 20, "w": 44, "h": 92, "steps": [
                    {"if": {"flag": "seated"},
                     "say": _("Outside it is dark already. Inside, table three is waiting.")},
                    {"say": _("The street, and the evening coming. You came in for a reason: "
                              "Friday Commander starts at seven, and your deck is not ready.")},
                ]},
                {"id": "boosters", "name": _("The wall of boosters"),
                 "x": 62, "y": 8, "w": 112, "h": 70, "steps": [
                    {"say": _("Boosters up to the ceiling, every set of the last years. "
                              "Not tonight: you came for sleeves.")},
                ]},
                {"id": "board", "name": _("The notice board"),
                 "x": 184, "y": 18, "w": 40, "h": 34, "steps": [
                    {"say": _("“Friday Commander, 7 pm. Bring a deck, bring snacks, "
                              "bring a friend.” Below it, someone looking for a fourth "
                              "player since March.")},
                ]},
                {"id": "case", "name": _("The glass case of singles"),
                 "x": 96, "y": 104, "w": 90, "h": 44, "steps": [
                    {"say": _("Singles under glass, sorted by colour. Your deck needs nothing "
                              "from here tonight, which is a first.")},
                ]},
                {"id": "counter", "name": _("The shopkeeper at the counter"),
                 "x": 232, "y": 60, "w": 88, "h": 68, "steps": [
                    {"if": {"has": "deckbox"}, "take": "deckbox", "give": "sleeves",
                     "set": "helped",
                     "say": _("“That's Sam's deck box! He has been looking for it all week. "
                              "Here - the last pack of sleeves, from the back. On the house.”")},
                    {"if": {"flag": "helped"},
                     "say": _("“Table three is free. Have a good game.”")},
                    {"say": _("“Evening! Sleeves? The shelf is empty, sorry. I might have a pack "
                              "in the back… Oh, and if you see a deck box lying about at the "
                              "tables: someone is missing one.”")},
                ]},
                {"id": "tables", "name": _("The play tables"),
                 "x": 186, "y": 136, "w": 134, "h": 44, "steps": [
                    {"if": {"has": "sleeves", "unflag": "seated"}, "set": "seated",
                     "take": "sleeves", "end": True,
                     "say": _("You sit down at table three, sleeve the deck and shuffle. "
                              "It plays well - as far as anyone can tell before playing it.")},
                    {"if": {"unflag": "found"}, "give": "deckbox", "set": "found",
                     "say": _("Under a chair: a deck box with a goldfish sticker on it. "
                              "You pick it up.")},
                    {"if": {"flag": "seated"},
                     "say": _("Table three. Your deck, sleeved, shuffled.")},
                    {"say": _("The tables fill up at seven. Table three is free, but not with "
                              "a deck in bare cards.")},
                ]},
            ],
        },
    }


def game() -> dict:
    """Everything `shop.js` needs, in the page's language."""
    return {"start": "floor", "width": WIDTH, "height": HEIGHT,
            "items": items(), "rooms": rooms()}
