"""A deck's themes, read from the catalogue (Themen-Doc, Paket 1, PR 1).

The rules live in `cards.themes`, which knows nothing of the database; this
module only builds its `ThemeCard`s from a deck. Two queries, whatever the
deck's size: the card list with its quantities, then every card's tags and
mechanics at once (an `ArrayAgg` per card, the direct tags filtered apart).

PR 3 reads pairs and engines from the same cards (`cards.synergy`), with
one more query for how many cards wear each tag of the catalogue, kept a
day in the cache: a tag's rarity changes with a new set, not with a page.

The themes follow from the list as it is now, so a changed list needs no
rewrite. Only what the owner locks or dismisses is stored
(`decks.models.DeckMark`, PR 4), keyed by the cards: one query more, and
one for the names of cards that left the deck, when a stored entry has any.
"""

from dataclasses import dataclass, field, replace

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.cache import cache
from django.db.models import Count, Q

from cards import synergy, themes
from cards.models import OracleCard, OracleCardTag
from decks.models import DeckMark

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
            repeats=themes.repeats(tempo, tags), oracle_id=str(pk)))
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
    #: The engines and pairs the owner dismissed, to bring back.
    dismissed: list = field(default_factory=list)

    @property
    def engines_dismissed(self) -> bool:
        return any(item.kind == synergy.ENGINE for item in self.dismissed)

    def suggestion(self, key: str):
        """The engine or pair on the page under this key that the owner has
        not said anything of yet, or None."""
        return next((item for item in (*self.engines, *self.pairs, *self.more_pairs)
                     if item.key == key and not item.state), None)


def deck_themes(deck) -> list[themes.Found]:
    """What the deck is built around, the strongest first; empty for none."""
    return themes.detect(theme_cards(deck))


def deck_reading(deck) -> Reading:
    """The deck's themes, its engines and its pairs, from one read of its
    cards, with what the owner locked or dismissed (`merge`)."""
    marks = list(deck.marks.all())
    cards = theme_cards(deck)
    found = themes.detect(cards)
    engines, every = [], []
    if found:
        sizes, total = tag_sizes()
        engines = synergy.engines(found, cards, sizes, total)
        every = synergy.pairs(found, cards, sizes, total)
    if not marks:
        return Reading(themes=found, engines=engines, pairs=every[:PAIRS_SHOWN],
                       more_pairs=every[PAIRS_SHOWN:PAIRS_MAX],
                       pairs_left_out=max(len(every) - PAIRS_MAX, 0))
    names = {card.oracle_id: card.name for card in cards}
    away = {str(oracle_id) for mark in marks for oracle_id, _piece in mark.cards} - set(names)
    if away:
        names.update((str(pk), name) for pk, name in
                     OracleCard.objects.filter(pk__in=away).values_list("pk", "name"))
    return merge(found, engines, every, marks, names, present=names.keys() - away)


def merge(found, engines, every, marks, names, present) -> Reading:
    """The reading with the owner's word on it.

    A locked entry comes first and stays, found again or not; a dismissed one
    leaves the list and waits in `dismissed`. A stored entry the deck still
    shows is that one, numbers and all; one it no longer shows is made again
    from the row (`cards.synergy.rebuild`). Each says which of its cards are
    gone (`gone`): it rests until they are back. Locked pairs are shown open
    above the suggestions and do not take their places.
    """
    live = {item.key: item for item in (*engines, *every)}
    locked, dismissed = [], []
    for mark in marks:
        item = live.get(mark.key) or synergy.rebuild(mark.kind, mark.theme, mark.cards, names)
        if item is None:
            continue
        gone = tuple(names.get(str(oracle_id), "") for oracle_id, _piece in mark.cards
                     if str(oracle_id) not in present)
        item = replace(item, state=mark.state, mark=str(mark.pk), gone=gone)
        (locked if mark.state == synergy.LOCKED else dismissed).append(item)
    said = {mark.key for mark in marks}
    suggested = [pair for pair in every if pair.key not in said]
    return Reading(
        themes=found,
        engines=[item for item in locked if item.kind == synergy.ENGINE]
        + [engine for engine in engines if engine.key not in said],
        pairs=[item for item in locked if item.kind == synergy.PAIR] + suggested[:PAIRS_SHOWN],
        more_pairs=suggested[PAIRS_SHOWN:PAIRS_MAX],
        pairs_left_out=max(len(suggested) - PAIRS_MAX, 0), dismissed=dismissed)


def save_mark(deck, item, state: str):
    """Store what the owner said of a suggestion. Twice is once: a second
    click before the page came back finds the row there."""
    return DeckMark.objects.get_or_create(
        deck=deck, key=item.key,
        defaults={"kind": item.kind, "theme": item.theme, "state": state,
                  "cards": [list(part) for part in item.parts]})[0]
