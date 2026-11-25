"""The deck in your hand (B-11): the library, shuffled, as on a table.

No game and no rules: only the order of the cards. The page without
JavaScript shuffles here, from the seed in its address, so the same address
shows the same order (back button, a shared link). With JavaScript the
browser keeps the order itself (static/js/deck-hand.js), and the two need
not agree: nothing is stored.
"""

import random

#: The most cards the page turns face up at once.
CUT_MAX = 15


def library(deck) -> list[str]:
    """The deck's cards one per copy, commanders left out, in name order:
    the order before any shuffle."""
    commanders = {card.pk for card in deck.commanders}
    names = []
    for oracle_id, name, quantity in deck.entries.order_by("oracle_card__front_name").values_list(
            "oracle_card_id", "oracle_card__front_name", "quantity"):
        if oracle_id not in commanders:
            names += [name] * quantity
    return names


def shuffled(names: list[str], seed: int) -> list[str]:
    """`names` in the order this seed shuffles them to; `names` stays as it was."""
    order = list(names)
    random.Random(seed).shuffle(order)  # noqa: S311 - a shuffle to look at, no secret
    return order
