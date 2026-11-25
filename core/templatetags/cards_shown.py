"""`{% card_tile name %}`: a card shown, not described (deck page v2, PR E).

docs/DESIGN.html, "Cards are shown": a combo, an engine or a synergy is its cards'
pictures in a row - `[card] + [card] = result` - with what each card does in
a line under it. The picture is the printing the deck's file named
(`Deck.card_images`, `decks.printing_art`), else the catalogue's; with a
pointer the card grows where it sits until it can be read.

The deck's pictures are read once per deck and render: every tile of the
page asks the same dictionary.
"""

from django import template

register = template.Library()


def _images(deck) -> dict[str, str]:
    if deck is None:
        return {}
    images = getattr(deck, "_shown_images", None)
    if images is None:
        images = deck._shown_images = deck.card_images()
    return images


def image_for(deck, name: str, fallback: str = "") -> str:
    """The picture of `name` in `deck`, by its full name or its front face."""
    images = _images(deck)
    return images.get(name) or images.get(name.split(" // ", 1)[0]) or fallback


@register.inclusion_tag("decks/_card_tile.html", takes_context=True)
def card_tile(context, name, effect="", repeats=False, size="", fallback="", missing=False):
    """One card's picture with `effect` under it; `size="sm"` for a row of
    many (the theme groups). `fallback` is a picture for a card the deck
    does not hold; `missing` marks it as the one a combo still needs (B-06)."""
    return {"name": name, "effect": effect, "repeats": repeats, "size": size,
            "missing": missing, "image": image_for(context.get("deck"), name, fallback)}


@register.filter
def is_missing(name: str, missing) -> bool:
    """`card.name|is_missing:entry.missing`: the card a one-away combo lacks."""
    return name in (missing or ())
