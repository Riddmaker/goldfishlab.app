"""A deck's themes, read from the catalogue (Themen-Doc, Paket 1, PR 1).

The rules live in `cards.themes`, which knows nothing of the database; this
module only builds its `ThemeCard`s from a deck. Two queries, whatever the
deck's size: the card list with its quantities, then every card's tags and
mechanics at once (an `ArrayAgg` per card, the direct tags filtered apart).

PR 3 reads pairs and engines from the same cards (`cards.synergy`), with
one more query for how many cards wear each tag of the catalogue, kept a
day in the cache: a tag's rarity changes with a new set, not with a page.

Nothing is stored. The themes follow from the list as it is now, so a
changed list needs no rewrite; what a user confirms or dismisses (the
lock-in) will be stored beside the deck later, keyed by the cards.
"""

from dataclasses import dataclass, field

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.cache import cache
from django.db.models import Count, Q

from cards import synergy, themes
from cards.models import OracleCard, OracleCardTag

#: The pairs shown open, and the most the fold below them lists.
PAIRS_SHOWN = 5
PAIRS_MAX = 30

TAG_SIZES_KEY = "themes:tag-sizes:v1"
TAG_SIZES_TTL = 24 * 60 * 60


def theme_cards(deck) -> list[themes.ThemeCard]:
    """The deck's cards, the command zone included, as the themes read them."""
    quantities = dict(deck.entries.values_list("oracle_card_id", "quantity"))
    commanders = {pk for pk in (deck.commander_id, deck.partner_id) if pk}
    rows = (OracleCard.objects.filter(pk__in=set(quantities) | commanders)
            .annotate(tag_slugs=ArrayAgg("card_tags__tag__slug", distinct=True, default=[]),
                      direct_slugs=ArrayAgg("card_tags__tag__slug", distinct=True, default=[],
                                            filter=Q(card_tags__is_direct=True)))
            .values_list("pk", "name", "type_line", "keywords", "profile__mechanics",
                         "profile__tempo", "tag_slugs", "direct_slugs"))
    cards = []
    for pk, name, type_line, keywords, mechanics, tempo, tags, direct in rows:
        tags = frozenset(slug for slug in tags if slug)
        cards.append(themes.ThemeCard(
            name=name, type_line=type_line, tags=tags,
            direct=frozenset(slug for slug in direct if slug),
            mechanics=frozenset(mechanics or ()),
            quantity=quantities.get(pk, 1), commander=pk in commanders,
            every_type="Changeling" in (keywords or ()),
            repeats=themes.repeats(tempo, tags)))
    return cards


def tag_sizes() -> tuple[dict[str, int], int]:
    """How many cards wear each tag of the catalogue, and how many cards
    there are: the rarity of a tag (`cards.synergy.spec`)."""
    def build():
        sizes = dict(OracleCardTag.objects.filter(tag__slug__in=themes.SLUGS)
                     .values("tag__slug").annotate(cards=Count("oracle_card", distinct=True))
                     .values_list("tag__slug", "cards"))
        return sizes, OracleCard.objects.count()
    return cache.get_or_set(TAG_SIZES_KEY, build, TAG_SIZES_TTL)


@dataclass(frozen=True)
class Reading:
    """What the deck page says about a deck's plan."""

    themes: list = field(default_factory=list)
    engines: list = field(default_factory=list)
    #: The strongest pairs, shown open.
    pairs: list = field(default_factory=list)
    #: The next ones, folded.
    more_pairs: list = field(default_factory=list)
    #: Pairs beyond `PAIRS_MAX`, only counted.
    pairs_left_out: int = 0


def deck_themes(deck) -> list[themes.Found]:
    """What the deck is built around, the strongest first; empty for none."""
    return themes.detect(theme_cards(deck))


def deck_reading(deck) -> Reading:
    """The deck's themes, its engines and its pairs, from one read of its cards."""
    cards = theme_cards(deck)
    found = themes.detect(cards)
    if not found:
        return Reading()
    sizes, total = tag_sizes()
    every = synergy.pairs(found, cards, sizes, total)
    return Reading(themes=found, engines=synergy.engines(found, cards, sizes, total),
                   pairs=every[:PAIRS_SHOWN], more_pairs=every[PAIRS_SHOWN:PAIRS_MAX],
                   pairs_left_out=max(len(every) - PAIRS_MAX, 0))
