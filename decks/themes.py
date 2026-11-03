"""A deck's themes, read from the catalogue (Themen-Doc, Paket 1, PR 1).

The rules live in `cards.themes`, which knows nothing of the database; this
module only builds its `ThemeCard`s from a deck. Two queries, whatever the
deck's size: the card list with its quantities, then every card's tags and
mechanics at once (an `ArrayAgg` per card, the direct tags filtered apart).

Nothing is stored. The themes follow from the list as it is now, so a
changed list needs no rewrite; what a user confirms or dismisses (the
lock-in) will be stored beside the deck later, keyed by the cards.
"""

from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Q

from cards import themes
from cards.models import OracleCard


def theme_cards(deck) -> list[themes.ThemeCard]:
    """The deck's cards, the command zone included, as the themes read them."""
    quantities = dict(deck.entries.values_list("oracle_card_id", "quantity"))
    commanders = {pk for pk in (deck.commander_id, deck.partner_id) if pk}
    rows = (OracleCard.objects.filter(pk__in=set(quantities) | commanders)
            .annotate(tag_slugs=ArrayAgg("card_tags__tag__slug", distinct=True, default=[]),
                      direct_slugs=ArrayAgg("card_tags__tag__slug", distinct=True, default=[],
                                            filter=Q(card_tags__is_direct=True)))
            .values_list("pk", "name", "type_line", "keywords", "profile__mechanics",
                         "tag_slugs", "direct_slugs"))
    cards = []
    for pk, name, type_line, keywords, mechanics, tags, direct in rows:
        cards.append(themes.ThemeCard(
            name=name, type_line=type_line,
            tags=frozenset(slug for slug in tags if slug),
            direct=frozenset(slug for slug in direct if slug),
            mechanics=frozenset(mechanics or ()),
            quantity=quantities.get(pk, 1), commander=pk in commanders,
            every_type="Changeling" in (keywords or ())))
    return cards


def deck_themes(deck) -> list[themes.Found]:
    """What the deck is built around, the strongest first; empty for none."""
    return themes.detect(theme_cards(deck))
