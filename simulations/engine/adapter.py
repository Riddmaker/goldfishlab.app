"""Deck (ORM) -> DeckDefinition (frozen, Django-free).

This is the only module that sees both worlds, and the rule it enforces is
simple: **no model instance ever crosses the boundary.** What comes out the far
side is plain frozen dataclasses that the engine could run without a database,
a settings module or an installed Django.

Merge precedence, narrowest last:

    DerivedProfile  ->  built-in annotation  ->  user annotation  ->  deck annotation

`DerivedProfile` answers what can be read off the card. The annotations carry
what cannot: how early to cast it, whether it makes a one-land hand keepable,
whether its draw is a cast trigger or an upkeep one. Phase 1's deriver refuses
to guess those, so something has to supply them, and a human is the only honest
source.

**What this module will not do:** infer a judgement because it would make a
deck look better. If nothing supplies a card's priority, the engine's default
rule applies and `coverage()` reports the gap. A simulation that quietly
invented the missing half of its input would be worse than no simulation.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from django.utils.translation import gettext, gettext_noop, ngettext

from decks.models import Deck
from simulation import ENGINE_VERSION, agent
from simulation.cards import (
    CARD_TYPES,
    COUNTS,
    DOUBLE_SUBTYPE,
    EXTRA,
    FILTER,
    FLAT,
    GRANT,
    LANDS_COULD_PRODUCE,
    MULTIPLY,
    PER_CONTROLLED,
    TYPE_ADDING,
    AdditionalCost,
    Card,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    LandSearch,
    ManaAbility,
    TappedUnless,
    TutorSpec,
    UpkeepSpec,
)
from simulation.mana import land_color
from simulation.manacost import COLORLESS, COLORS, ManaCost, choice, mana_label, parse
from simulations import gaps as gaps_module

#: Engine kinds that are one-shot spells. Only these may take `draw_on_cast`
#: from the derived `draws_cards`: a permanent's "draw a card" is almost always
#: a triggered ability, and reading Phyrexian Arena as a cast-trigger would
#: hand the deck a free card every game it is drawn rather than every upkeep.
ONE_SHOT_KINDS = frozenset({"instant", "sorcery", "ritual"})

#: Vesuva, Thespian's Stage: a land that becomes a copy of another makes
#: that land's mana, which the engine cannot know. Not "makes none".
_COPIES = re.compile(r"\bcopy of\b", re.IGNORECASE)
#: Exotic Orchard, Fellwar Stone: read as the deck's colours since engine
#: version 9, which is an assumption about the opponents (P19 R5).
_OPPONENTS_LANDS = re.compile(r"that a land an opponent controls could produce", re.IGNORECASE)

#: Scaling rules an annotation may name, mapped to the engine's constants.
SCALING_RULES = {
    "per_controlled": PER_CONTROLLED,
    "double_subtype": DOUBLE_SUBTYPE,
    "type_adding": TYPE_ADDING,
    "lands_could_produce": LANDS_COULD_PRODUCE,
    "counts": COUNTS,
    "extra": EXTRA,
    "multiply": MULTIPLY,
    "grant": GRANT,
}

#: Fields a human has to supply, because nothing in the card text implies them.
#: The canonical list lives in `simulations.gaps`, which is also what classifies
#: the gaps already stored on finished runs. Re-exported here because this is
#: where every reader of it already looks.
JUDGEMENT_FIELDS = tuple(sorted(gaps_module.JUDGEMENT_FIELDS))


@dataclass
class Gap:
    """One thing the adapter could not establish about one card."""

    card: str
    field: str
    #: In English, as every stored run and session keeps it. Marked with
    #: `gettext_noop` where it is written, and translated by `text`.
    reason: str
    #: For a reason with values in it (phase 12): the sentence with its
    #: placeholders, and the values. `reason` is the same sentence filled in.
    template: str = ""
    params: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """The reason in the page's language."""
        if self.template:
            return gettext(self.template) % self.params
        return gettext(self.reason)

    @property
    def kind(self) -> str:
        """`reading` or `judgement` - see `simulations.gaps`.

        Derived from the field rather than stored, so that `asdict(gap)` keeps
        the shape every stored run and open session already has on disk, and so
        that there is exactly one place that decides which question a gap asks.
        """
        return gaps_module.kind_of(self.field)


@dataclass
class Conversion:
    """The deck, plus an honest account of what is missing from it."""

    definition: DeckDefinition
    gaps: list[Gap] = field(default_factory=list)
    cards_total: int = 0
    #: Copies of each card, by the name its gaps carry - the commander and a
    #: hypothetical card included. What the coverage counts since phase 10
    #: (T5.9, K2): thirty Swamps are thirty of a hundred cards, not one.
    copies: dict[str, int] = field(default_factory=dict)
    #: Which engine produced this reading. Stored with every run so a result
    #: can later say "computed with v2, current is v5 - re-run to compare".
    engine_version: int = ENGINE_VERSION

    @property
    def cards_with_gaps(self) -> int:
        return len(gaps_module.cards_with(self.gaps))

    @property
    def cards_unreadable(self) -> int:
        """Cards the engine could not read. The application's own limit."""
        return len(gaps_module.cards_with(self.gaps, gaps_module.READING))

    @property
    def readable(self) -> float:
        """Share of cards the engine read in full - the score every page shows.

        The cast-priority gaps are recorded too (`cards_with_gaps` counts
        them) but not scored: since phase 9 C the default rule is an answer
        nobody has to give - see `simulations.gaps`.
        """
        return gaps_module.share(self.cards_total, self.cards_unreadable)

    @property
    def copies_total(self) -> int:
        """Every card in the deck, counting each copy - "of 100"."""
        return sum(self.copies.values())

    @property
    def copies_unreadable(self) -> int:
        """Copies of the cards the engine could not read."""
        names = gaps_module.cards_with(self.gaps, gaps_module.READING)
        return sum(count for name, count in self.copies.items() if name in names)


def deck_definition(deck: Deck, *, adding=None) -> DeckDefinition:
    """The engine's view of a database deck.

    The signature the rest of the application uses. `convert()` is the same
    work with the coverage report attached.
    """
    return convert(deck, adding=adding).definition


def convert(deck: Deck, *, adding=None) -> Conversion:
    """Build the engine deck and record everything that had to be left out.

    Args:
        adding: One `OracleCard` to put into the library that the deck has not
            got - the hypothetical behind "add this card and the combo
            assembles by turn six in X% of games". The deck is one card
            **larger**, never one card swapped: choosing which card somebody's
            deck can spare is not this application's judgement to make, and the
            page says so wherever a hypothetical number appears.

            The colours are still read off the real deck. A single added card
            does not change what a ritual in the other ninety-nine makes.
    """
    entries = list(
        deck.entries.select_related("oracle_card", "oracle_card__profile").all()
    )
    annotations = annotations_for(deck)

    gaps: list[Gap] = []
    library: list[Card] = []
    seen = set()

    colors = _deck_colors(entries, deck)

    for entry in entries:
        card = _card_from(entry.oracle_card, annotations, gaps, colors)
        seen.add(entry.oracle_card_id)
        library.extend([card] * entry.quantity)

    commander = None
    if deck.commander_id:
        commander = _card_from(deck.commander, annotations, gaps, colors)
        # The commander counts toward the total, because gaps are recorded for
        # it like any other card. Leaving it out made `cards_with_gaps` able to
        # exceed `cards_total` - a two-card deck whose commander the engine
        # could not read reported **-50% coverage**, which is a worse answer
        # than no answer. It is not a DeckCard (trap 7), so `seen` is the only
        # thing that knows whether it has already been counted.
        seen.add(deck.commander_id)

    if adding is not None:
        library.append(_card_from(adding, annotations, gaps, colors))
        seen.add(adding.pk)

    definition = DeckDefinition(
        name=deck.name,
        commander=commander,
        library=tuple(library),
    )
    copies = Counter(card.name for card in library)
    if commander is not None:
        copies[commander.name] += 1
    return Conversion(definition=definition, gaps=gaps, cards_total=len(seen),
                      copies=dict(copies))


@dataclass
class Reading:
    """One database card as the engine will play it, and what is missing.

    What the provenance panel renders. It exists so that the panel and the
    simulation cannot disagree: both are `_card_from` on the same inputs, so a
    screen saying "the engine treats this as a ritual that adds two mana" is
    describing the object the engine is actually going to use, not a second
    reconstruction of it that might have drifted.
    """

    oracle_card: object
    card: Card
    gaps: list[Gap] = field(default_factory=list)
    quantity: int = 1
    is_commander: bool = False

    @property
    def unreadable(self) -> bool:
        """The engine could not read something off this card."""
        return any(gap.kind == gaps_module.READING for gap in self.gaps)

    # --- the engine's reading, in words --------------------------------------
    #
    # These live here rather than in `simulations/provenance.py` because
    # phrasing them needs the engine's own rule constants, and this module is
    # the only one allowed to know them. The panel gets plain strings.

    @property
    def cost(self) -> str:
        """The cost the engine will make the agent pay.

        A land is spelled out as costing nothing rather than shown as `{0}`,
        which is what an Ornithopter costs and is a different statement.
        """
        if self.card.is_land:
            return gettext("no cost")
        if self.card.additional_costs:
            # P19 R15: what else is paid, any one of the ways.
            ways = " / ".join(_way_text(way) for way in self.card.additional_costs)
            return gettext("%(cost)s, and %(extra)s") % {"cost": self.card.mana_cost,
                                                         "extra": ways}
        return str(self.card.mana_cost)

    @property
    def x_rule(self) -> str:
        """How the engine picks X (P19 R9), in words; empty without an X."""
        if not self.card.x_count:
            return ""
        return gettext("everything left once nothing else can be cast, at least "
                       "%(min)s") % {"min": self.card.x_min}

    @property
    def roles(self) -> list[str]:
        return sorted(self.card.tags)

    @property
    def searches(self) -> str:
        """The library search the engine will actually perform.

        Spelled out in full because a tutor is the one derived value that can
        change a whole game on its own, and because the two halves come from
        different places - the zone from a community tag, the number from the
        printed text - so a person checking it needs to see both at once.
        """
        spec = self.card.tutor
        if spec is None and self.card.land_search is not None:
            return _land_search_text(self.card.land_search)
        if spec is None:
            return gettext("nothing")
        if spec.kind or spec.types:
            from cards.models import DerivedProfile

            kinds = dict(DerivedProfile.Kind.choices)
            named = [spec.kind] if spec.kind else sorted(spec.types)
            what = gettext("%(count)s × %(kind)s") % {
                "count": spec.count,
                "kind": " / ".join(str(kinds.get(kind, kind)) for kind in named)}
        else:
            what = ngettext("%(count)s card", "%(count)s cards", spec.count) % {
                "count": spec.count}
        if spec.to_battlefield:
            if spec.color:
                what = gettext("%(what)s (%(color)s)") % {"what": what,
                                                          "color": mana_label(spec.color)}
            if spec.max_mv_x or spec.max_mv is not None:
                what = gettext("%(what)s with mana value %(limit)s or less") % {
                    "what": what, "limit": "X" if spec.max_mv_x else spec.max_mv}
            if spec.max_mv_sacrificed is not None:
                what = gettext("%(what)s with mana value up to %(plus)s more than the "
                               "creature sacrificed") % {"what": what,
                                                         "plus": spec.max_mv_sacrificed}
            text = gettext("%(what)s onto the battlefield") % {"what": what}
        elif spec.to_top:
            text = gettext("%(what)s on top of the library, drawn next turn") % {"what": what}
        else:
            text = (gettext("%(what)s to hand") if spec.to_hand
                    else gettext("%(what)s to the graveyard")) % {"what": what}
        if spec.life:
            text = gettext("%(search)s, paying %(life)s life") % {"search": text,
                                                                 "life": spec.life}
        return text

    @property
    def skips_draw_step(self) -> str:
        return gettext("yes") if self.card.skips_draw_step else gettext("no")

    @property
    def effective_priority(self) -> int:
        """What the agent will actually use, default rule included."""
        return agent.priority(self.card)

    @property
    def taps_for(self) -> str:
        """What the engine believes this card adds to the pool.

        The string a person can disagree with. "nothing" is a real answer and
        the most common cause of a deck simulating badly, so it is spelled out
        rather than left blank.

        **A land with a coloured land type taps as a basic land, and its own
        flat ability then goes unused** - that is the engine's rule, in
        `mana.available_mana`. Reporting only the flat ability would make this
        row say "nothing" for a Swamp whose mana somebody had annotated away
        while the simulation happily went on making black mana off the land
        type. Read as the card stands alone: no Urborg on the battlefield,
        because a panel about one card cannot know what else is in play.
        """
        basic = land_color(self.card, frozenset()) if self.card.is_land else None
        if basic is not None:
            return gettext("1 %(color)s (as a basic land)") % {"color": mana_label(basic)}
        if not self.card.mana_abilities:
            return gettext("nothing")
        text = "; ".join(_ability_text(ability) for ability in self.card.mana_abilities)
        if any(gap.field == "assumed_mana" for gap in self.gaps):
            text = gettext("%(mana)s - assuming your opponents' lands make these colours") % {
                "mana": text}
        if not self.card.untaps:
            text = gettext("%(mana)s - once, then it stays tapped") % {"mana": text}
        return text

    @property
    def activation(self) -> str:
        """What tapping it for mana costs beside the tap, in words."""
        cost = sum(
            ability.activation_generic
            for ability in self.card.mana_abilities
            if ability.rule == FLAT
        )
        return f"{{{cost}}}" if cost else gettext("nothing beyond tapping")

    @property
    def untaps(self) -> bool:
        return self.card.untaps


#: How each scaling rule reads on the provenance panel. The engine's constants
#: are not words a user should have to learn.
RULE_TEXT = {
    PER_CONTROLLED: gettext_noop("one %(color)s for each %(subtype)s you control"),
    DOUBLE_SUBTYPE: gettext_noop("one extra %(color)s whenever a %(subtype)s is tapped"),
    TYPE_ADDING: gettext_noop("makes every land a %(subtype)s"),
    LANDS_COULD_PRODUCE: gettext_noop("one mana of a colour your other lands could make"),
}


def _land_search_text(search: LandSearch) -> str:
    """A land search, in words: what, how many, where, at what price."""
    types = " / ".join(sorted(subtype.capitalize() for subtype in search.types))
    if search.basic:
        what = ngettext("%(count)s basic land", "%(count)s basic lands",
                        search.battlefield) % {"count": search.battlefield}
    else:
        what = ngettext("%(count)s land", "%(count)s lands",
                        search.battlefield) % {"count": search.battlefield}
    if types:
        what = f"{what} ({types})"
    if search.share_type:
        what = gettext("%(what)s of one land type") % {"what": what}
    elif search.each:
        what = gettext("%(what)s, one of each") % {"what": what}
    text = (gettext("%(what)s onto the battlefield, tapped") if search.tapped
            else gettext("%(what)s onto the battlefield")) % {"what": what}
    if search.hand:
        text = gettext("%(search)s, and %(count)s to hand") % {"search": text,
                                                              "count": search.hand}
    if search.life:
        text = gettext("%(search)s, paying %(life)s life") % {"search": text,
                                                             "life": search.life}
    if search.when == "activate" and search.sacrifice:
        cost = str(search.cost or "") + (", {T}" if search.taps else "")
        text = gettext("%(cost)s, sacrifice it: %(search)s") % {
            "cost": cost.lstrip(", "), "search": text}
    elif search.when == "activate":
        # P19 R15: a creature's {T}, once a turn.
        parts = [str(search.cost)] if search.cost else []
        parts.append("{T}")
        if search.sacrifices_land:
            parts.append(gettext("sacrifice a land") if not search.land_cost_types else
                         gettext("sacrifice a %(types)s") % {"types": " / ".join(
                             sorted(kind.capitalize() for kind in search.land_cost_types))})
        if search.sacrifice_other:
            parts.append(gettext("sacrifice another creature"))
        if search.discard:
            parts.append(gettext("discard a card"))
        text = gettext("%(cost)s: %(search)s, once a turn") % {"cost": ", ".join(parts),
                                                               "search": text}
    if search.condition == "opponent_more_lands":
        text = gettext("%(search)s, if an opponent controls more lands") % {"search": text}
    if search.sacrifices_land and search.when == "enters":
        text = gettext("sacrifice a land: %(search)s") % {"search": text}
    return text


def _way_text(way: AdditionalCost) -> str:
    """One way to pay an additional cost, in words (P19 R15)."""
    from cards.models import DerivedProfile

    kinds = dict(DerivedProfile.Kind.choices)
    parts = []
    if way.sacrifice:
        what = " / ".join(str(kinds.get(kind, kind)) for kind in sorted(way.sacrifice))
        if way.sacrifice_filter in COLORS:
            what = gettext("%(what)s (%(color)s)") % {
                "what": what, "color": mana_label(way.sacrifice_filter)}
        elif way.sacrifice_filter:
            what = f"{what} ({way.sacrifice_filter.capitalize()})"
        parts.append(gettext("sacrifice a %(what)s") % {"what": what})
    if way.life:
        parts.append(gettext("%(life)s life") % {"life": way.life})
    if way.life_x:
        parts.append(gettext("X life, paid as 0"))
    if way.discard:
        parts.append(ngettext("discard %(count)s card", "discard %(count)s cards",
                              way.discard) % {"count": way.discard})
    if way.mana is not None:
        parts.append(str(way.mana))
    if way.exile_from_graveyard:
        parts.append(gettext("exile a %(what)s card from the graveyard") % {
            "what": kinds.get(way.exile_from_graveyard, way.exile_from_graveyard)})
    return ", ".join(str(part) for part in parts) or gettext("nothing")


def _ability_text(ability) -> str:
    """One mana ability, in words."""
    if ability.rule == FLAT:
        produced = " + ".join(
            f"{amount} {mana_label(color)}" for color, amount in ability.produces
        ) or gettext("nothing")
        if ability.activation_generic:
            produced = _for_cost(ability.activation_generic, produced)
        if ability.only_if is not None:
            produced = gettext("%(mana)s if you control %(what)s") % {
                "mana": produced, "what": _condition_text(ability.only_if)}
        return produced

    if ability.rule == FILTER:
        produced = " + ".join(
            f"{amount} {mana_label(color)}" for color, amount in ability.produces)
        cost = "{" + "/".join(ability.pays_with) + "}" if ability.pays_with else "{1}"
        return gettext("for %(cost)s, %(mana)s") % {"cost": cost, "mana": produced}

    if ability.rule in (EXTRA, MULTIPLY, GRANT):
        return _extra_text(ability)
    if ability.rule == COUNTS:
        text = _counts_text(ability)
    elif ability.rule in RULE_TEXT:
        text = gettext(RULE_TEXT[ability.rule]) % {
            "color": ability.scaling_color, "subtype": ability.subtype or "land"}
    else:
        text = str(ability.rule)
    if ability.activation_generic:
        return _for_cost(ability.activation_generic, text)
    return text


def _extra_text(ability) -> str:
    """Mana on top, or a granted ability, in words (P19 R13)."""
    mana = " + ".join(f"{amount} {mana_label(source)}" for source, amount in ability.produces)
    if ability.color == "chosen":
        mana = gettext("1 of the chosen colour")
    if ability.rule == MULTIPLY:
        return gettext("a permanent tapped for mana makes %(times)s times as much") % {
            "times": ability.times}
    if ability.rule == GRANT:
        if ability.subtype == "enchanted":
            return gettext("the enchanted land taps for one mana of any colour")
        return gettext("your creatures tap for %(mana)s") % {"mana": mana}
    texts = {
        "enchanted": gettext_noop("%(mana)s more when the enchanted land is tapped for mana"),
        "enchanted:forest": gettext_noop(
            "%(mana)s more when the enchanted Forest is tapped for mana"),
        "land": gettext_noop("one more mana of a type it made, whenever a land is tapped "
                             "for mana"),
        "nonland": gettext_noop("one more mana of a type it made, whenever a nonland "
                                "permanent is tapped for mana"),
        "permanent": gettext_noop("one more mana of a type it made, whenever a permanent "
                                  "is tapped for mana"),
        "creature": gettext_noop("%(mana)s more whenever a creature is tapped for mana"),
        "colorless": gettext_noop("%(mana)s more whenever a permanent is tapped for {C}"),
        "chosen_land": gettext_noop("one more mana of the chosen colour, whenever a land "
                                    "makes that colour"),
        "chosen_basic": gettext_noop("one more mana of the chosen colour, whenever a basic "
                                     "land makes that colour"),
    }
    text = texts.get(ability.subtype)
    return gettext(text) % {"mana": mana} if text else str(ability.rule)


def _counts_text(ability) -> str:
    """What a counting ability makes, in words (P19 R11)."""
    what, color = ability.subtype, mana_label(ability.color) if ability.color else ""
    if what == "colors_among":
        return gettext("one of each colour among your permanents")
    if what == "enchantment":
        if not color:
            return gettext("one mana of one colour for each enchantment you control")
        return gettext("one %(color)s for each enchantment you control") % {"color": color}
    if what == "creature:defender":
        return gettext("one %(color)s for each creature you control with defender") % {
            "color": color}
    if what == "creature":
        return gettext("one %(color)s for each creature you control") % {"color": color}
    if what.startswith("basic:"):
        return gettext("one %(color)s for each basic %(type)s you control") % {
            "color": color, "type": what.split(":", 1)[1].title()}
    if what.startswith("graveyard:"):
        return gettext("one %(color)s for each %(dead)s creature card in your graveyard") % {
            "color": color, "dead": mana_label(what.split(":", 1)[1])}
    if what == "devotion":
        return gettext("as much as your devotion to your best colour, in that colour")
    if what.startswith("names:"):
        produced = " + ".join(f"{amount} {mana_label(source)}"
                              for source, amount in ability.produces)
        return gettext("%(mana)s while you control %(names)s") % {
            "mana": produced, "names": " + ".join(what.split(":", 1)[1].split("|"))}
    return gettext("one %(color)s for each %(type)s you control") % {
        "color": color, "type": what.title()}


def _condition_text(condition: TappedUnless) -> str:
    """What "Activate only if you control ..." asks for, in words (P19 R4)."""
    if condition.kind == "lands":
        return ngettext("%(count)s or more land", "%(count)s or more lands",
                        condition.count) % {"count": condition.count}
    if condition.kind == "artifacts":
        return ngettext("%(count)s or more artifact", "%(count)s or more artifacts",
                        condition.count) % {"count": condition.count}
    types = " / ".join(sorted(subtype.capitalize() for subtype in condition.types))
    return gettext("a land of type %(types)s") % {"types": types}


def _for_cost(generic: int, mana: str) -> str:
    """"for {1}, 2 B": a mana ability that costs mana beside the tap."""
    return gettext("for %(cost)s, %(mana)s") % {"cost": f"{{{generic}}}", "mana": mana}


def readings(deck: Deck) -> list[Reading]:
    """Every distinct card in the deck, commander included, as the engine reads it.

    One query set, one merge of the annotations, one pass. The deck's colours
    are worked out first for the same reason `convert` does it: a card that
    could make one of several colours is read as making the deck's colour, and
    that is only knowable from the whole deck.
    """
    entries = list(
        deck.entries.select_related("oracle_card", "oracle_card__profile").all()
    )
    annotations = annotations_for(deck)
    colors = _deck_colors(entries, deck)

    found: list[Reading] = []
    seen: set = set()

    for entry in entries:
        gaps: list[Gap] = []
        card = _card_from(entry.oracle_card, annotations, gaps, colors)
        seen.add(entry.oracle_card_id)
        found.append(
            Reading(
                oracle_card=entry.oracle_card,
                card=card,
                gaps=gaps,
                quantity=entry.quantity,
            )
        )

    if deck.commander_id and deck.commander_id not in seen:
        gaps = []
        card = _card_from(deck.commander, annotations, gaps, colors)
        found.append(
            Reading(
                oracle_card=deck.commander,
                card=card,
                gaps=gaps,
                is_commander=True,
            )
        )

    return found


def engine_readings(oracle_cards, *, builtin: bool = True) -> dict:
    """Each card as the engine alone reads it, by oracle id: `(Card, gaps)`.

    `builtin=False` leaves the built-in annotations out as well: the reader
    and the engine and nothing else, which is what `simulations.coverage`
    snapshots - built-in rows are data a database may or may not hold.

    Built-in annotations only: a player's own answer makes the card work on
    their deck, but the engine still could not read it, and that is what the
    operator's queue (`simulations.unread`) is about. No deck colours either -
    they only pick which colour a choice is read as, never whether the choice
    is a gap.
    """
    from simulations.models import CardAnnotation

    cards = list(oracle_cards)
    merged: dict = {}
    rows = CardAnnotation.objects.filter(
        owner__isnull=True, deck__isnull=True, oracle_card__in=cards
    ).values_list("oracle_card_id", "overrides") if builtin else ()
    for oracle_id, overrides in rows:
        merged.setdefault(oracle_id, {}).update(overrides or {})
    annotations = Annotations(overrides=merged, scopes={})

    found = {}
    for oracle_card in cards:
        gaps: list[Gap] = []
        card = _card_from(oracle_card, annotations, gaps)
        found[oracle_card.pk] = (card, gaps)
    return found


def engine_gaps(oracle_cards) -> dict:
    """What the engine alone cannot read about each card, by oracle id (P19a)."""
    return {pk: gaps for pk, (_card, gaps) in engine_readings(oracle_cards).items()}


def _deck_colors(entries, deck: Deck) -> frozenset[str]:
    """Which colours this deck's rainbow sources should make.

    Needed because Arcane Signet, Command Tower and most filter lands make
    "one mana of any colour", and the pool counts mana rather than holding
    sources, so it has to be told *which* colour. Reading "any colour" as white
    in a mono-black deck would put mana in the pool that no card can spend.

    **The commander's colour identity is the answer when there is a commander**,
    because that is literally what those cards say - "in your commander's color
    identity" - and in Commander it is also the rule that bounds the deck. The
    colours the deck's own spells demand are the fallback, for a deck with no
    commander or a colourless one.
    """
    commander = deck.commander if deck.commander_id else None
    identity = frozenset(
        color for color in (getattr(commander, "color_identity", None) or [])
        if color in COLORS
    )
    if identity:
        return identity

    found: set[str] = set()
    for entry in entries:
        profile = getattr(entry.oracle_card, "profile", None)
        if profile is not None:
            found.update(color for color in profile.pips or {} if color in COLORS)
    return frozenset(found)


def _pick_color(colors, deck_colors: frozenset[str]) -> str | None:
    """One colour out of several a ritual could make.

    The deck's own colours first, then WUBRG order, so the same deck always
    reads the same way. Only rituals still pick: a mana *source* offers its
    choice to the pool since engine version 5 (`_mana_abilities`), while a
    ritual's mana goes in when it resolves, as one colour.
    """
    usable = [color for color in COLORS if color in set(colors) & deck_colors]
    if usable:
        return usable[0]
    remaining = [color for color in COLORS if color in set(colors)]
    return remaining[0] if remaining else None


#: How an annotation row's (owner, deck) pair reads as a scope name. The same
#: three words `CardAnnotation.scope` uses, and the ones the provenance panel
#: puts on screen.
BUILTIN_SCOPE = "builtin"
USER_SCOPE = "user"
DECK_SCOPE = "deck"


@dataclass(frozen=True)
class Annotations:
    """Every annotation that applies to one deck, already merged.

    Two mappings over the same keys, both keyed by oracle id:

    * `overrides` - the value that won.
    * `scopes` - *which* of the three scopes won it.

    The second one exists for the provenance panel. "Priority is 55" and
    "priority is 55 because you set it on this deck, overriding the built-in
    50" are different statements, and only the second lets somebody decide
    whether to trust it.
    """

    overrides: dict
    scopes: dict

    def for_card(self, oracle_id) -> dict:
        """The winning overrides for one card."""
        return self.overrides.get(oracle_id, {})

    def scope_of(self, oracle_id, key: str) -> str | None:
        """Which scope set this key, or `None` when nobody did."""
        return self.scopes.get(oracle_id, {}).get(key)


def cards_filter(deck: Deck):
    """Annotation rows about a card this deck actually contains.

    The commander is NOT a `DeckCard` - it sits in the command zone. Filtering
    on deck membership alone silently dropped its annotations, which left it
    with no priority and meant the agent almost never cast it. Written once
    here because two other places now need exactly the same question answered.
    """
    from django.db.models import Q

    relevant = Q(oracle_card__in_decks__deck=deck)
    if deck.commander_id:
        relevant |= Q(oracle_card_id=deck.commander_id)
    return relevant


def annotations_for(deck: Deck) -> Annotations:
    """All annotations that apply, already merged narrowest-wins.

    One query. The ordering is what does the merging: built-in rows first, then
    the owner's, then this deck's, each overwriting the keys before it.
    """
    from simulations.models import CardAnnotation

    relevant = cards_filter(deck)
    rows = (
        CardAnnotation.objects.filter(relevant)
        .filter(scope_filter(deck))
        .order_by("owner_id", "deck_id")
        .values_list("oracle_card_id", "owner_id", "deck_id", "overrides")
        .distinct()
    )

    merged: dict = {}
    scopes: dict = {}
    # Built-in rows first, then the owner's, then this deck's. Each pass
    # overwrites only the keys it sets, so a deck-scoped annotation can change
    # one field without restating the rest.
    ordered = sorted(rows, key=lambda row: (row[1] is not None, row[2] is not None))
    for oracle_id, owner_id, deck_id, overrides in ordered:
        scope = _scope_name(owner_id, deck_id)
        merged.setdefault(oracle_id, {}).update(overrides or {})
        scopes.setdefault(oracle_id, {}).update(
            dict.fromkeys(overrides or {}, scope)
        )
    return Annotations(overrides=merged, scopes=scopes)


def _scope_name(owner_id, deck_id) -> str:
    if deck_id is not None:
        return DECK_SCOPE
    return USER_SCOPE if owner_id is not None else BUILTIN_SCOPE


def scope_filter(deck: Deck):
    """Built-in rows, the owner's rows, and this deck's rows - nothing else."""
    from django.db.models import Q

    return (
        Q(owner__isnull=True, deck__isnull=True)
        | Q(owner_id=deck.owner_id, deck__isnull=True)
        | Q(deck_id=deck.pk)
    )


def _card_from(oracle_card, annotations: Annotations, gaps: list[Gap],
               deck_colors: frozenset[str] = frozenset()) -> Card:
    """One database card, as the engine sees it."""
    profile = getattr(oracle_card, "profile", None)
    overrides = annotations.for_card(oracle_card.pk)
    name = oracle_card.front_name

    if profile is None:
        gaps.append(Gap(name, "profile", "no derived profile; run cards.profiles.rebuild()"))
        return Card(name=name, mv=int(oracle_card.cmc or 0), pips=0,
                    generic=int(oracle_card.cmc or 0), kind="artifact",
                    types=card_types(oracle_card))

    kind = overrides.get("kind", profile.kind)
    mana_abilities = _mana_abilities(profile, overrides, kind, name, gaps, deck_colors)

    card = Card(
        name=name,
        mv=profile.mv,
        pips=int(overrides.get("pips", profile.black_pips)),
        generic=int(overrides.get("generic", profile.generic)),
        kind=kind,
        # The community tag rollup is broad on purpose; a deck author who
        # disagrees says so here, and Phase 4's role editor writes it.
        tags=frozenset(overrides.get("tags", profile.role_tags)),
        land_type=overrides.get(
            "land_type", "basic_swamp" if profile.is_basic_swamp else ""
        ),
        enters_tapped=bool(overrides.get("enters_tapped", profile.enters_tapped)),
        goldfish_castable=bool(overrides.get("goldfish_castable", True)),
        needs_creature_in_yard=bool(overrides.get("needs_creature_in_yard", False)),
        needs_creature_on_bf=bool(overrides.get("needs_creature_on_bf", False)),
        cost=_cost(oracle_card, profile, overrides),
        mana_abilities=mana_abilities,
        ritual_gain=_ritual_gain(profile, overrides, kind),
        ritual_color=(_read_rule(profile, "color") if _ritual_counts(profile, overrides, kind)
                      else _ritual_color(profile, overrides, deck_colors)),
        cost_reduction=_cost_reduction(profile, overrides),
        draw_on_cast=_draw_on_cast(profile, overrides, kind),
        discard_on_cast=_after_draw(profile, overrides, kind, "discard_on_cast",
                                    "discards_after"),
        put_back_on_cast=_after_draw(profile, overrides, kind, "put_back_on_cast",
                                     "puts_back"),
        x_count=_x_count(oracle_card, profile),
        x_min=int(overrides.get("x_min", 1)),
        draws_x=(kind in ONE_SHOT_KINDS and "draw_on_cast" not in overrides
                 and bool(getattr(profile, "draws_x", False))),
        creature_types=creature_types(oracle_card),
        legendary="Legendary" in (oracle_card.type_line or "").split("//", 1)[0],
        colors=frozenset(oracle_card.colors or ()) & frozenset(COLORS),
        defender="Defender" in (oracle_card.keywords or []),
        enchants=_read_rule(profile, "enchants"),
        ritual_counts=_ritual_counts(profile, overrides, kind),
        life_on_cast=int(overrides.get("life_on_cast", 0)),
        tutor=_tutor(profile, overrides),
        upkeep=_upkeep(overrides),
        end_step=_end_step(overrides),
        skips_draw_step=bool(overrides.get("skips_draw_step",
                                           profile.skips_draw_step)),
        priority=overrides.get("priority"),
        accelerant=bool(overrides.get("accelerant", False)),
        subtypes=_subtypes(oracle_card, profile, overrides),
        untaps=bool(overrides.get("untaps", getattr(profile, "mana_untaps", True))),
        types=card_types(oracle_card),
        categories=_categories(profile, overrides,
                               annotations.scope_of(oracle_card.pk, "tags")),
        land_search=_land_search(profile),
        basic=oracle_card.type_line.startswith("Basic"),
        tapped_unless=_tapped_unless(profile, overrides),
        treasures=int(getattr(profile, "treasures", 0) or 0),
        treasure_mana=_any_colour(deck_colors) if getattr(profile, "treasures", 0) else "",
        landers=int(getattr(profile, "landers", 0) or 0),
        discard_cost=int(getattr(profile, "discard_cost", 0) or 0),
        additional_costs=_additional_costs(profile),
        exiled_on_cast=bool(getattr(profile, "mana_from_hand", False)),
        **({} if _overrides_mana(overrides) else _sacrifice_mana(profile, deck_colors)),
    )

    _record_gaps(card, profile, overrides, gaps, oracle_card.oracle_text or "")
    return card


def _mana_abilities(profile, overrides: dict, kind: str, name: str,
                    gaps: list[Gap], deck_colors: frozenset[str]) -> tuple[ManaAbility, ...]:
    """The card's mana abilities, from an annotation or from the profile.

    A scaling rule comes from an annotation or, since engine version 8, from
    the reader (P19 R4): `produced_mana` gives colour and nothing else, so
    it is the printed sentence that tells Cabal Coffers from a Swamp. An
    annotation still wins.
    """
    if overrides.get("scaling_rule"):
        rule = SCALING_RULES.get(overrides["scaling_rule"])
        if rule is not None:
            return (
                ManaAbility(
                    rule,
                    subtype=overrides.get("scaling_subtype", "swamp"),
                    activation_generic=int(overrides.get("scaling_activation", 0)),
                    color=overrides.get("scaling_color", ""),
                ),
            )
    read_rule = getattr(profile, "mana_rule", None)
    if read_rule and read_rule["rule"] in SCALING_RULES and not _overrides_mana(overrides):
        rule = ManaAbility(SCALING_RULES[read_rule["rule"]], read_rule.get("produces") or (),
                           subtype=read_rule["subtype"],
                           activation_generic=int(read_rule.get("activation") or 0),
                           color=read_rule.get("color", ""),
                           times=int(read_rule.get("times") or 1))
        if read_rule["rule"] != "counts" or not profile.mana_amount:
            return (rule,)
        # Cabal Stronghold, Urza's Mine: the counting ability and the plain
        # {C} beside it; the game taps for the better (P19 R11).
        return (rule, _flat(getattr(profile, "mana_produces", None), profile.mana_colors,
                            profile.mana_amount, 0, deck_colors, None))

    # What using the ability costs beside {T}: a Signet's {1}. Read off the
    # card by the deriver since the 2026-09-25 review, and an annotation can
    # say otherwise - including 0, "this one only has to tap".
    derived_activation = getattr(profile, "mana_activation", None) or 0
    activation = int(overrides.get("mana_activation", derived_activation) or 0)

    produces = _annotated_production(overrides)
    if produces is not None:
        # An empty mapping is a statement, not a missing value: Ashnod's Altar
        # is tagged `mana-rock` and does add mana, but only by sacrificing a
        # creature - which the engine does not model, so it taps for nothing.
        return (ManaAbility(FLAT, produces, activation_generic=activation),) if produces else ()

    # A ritual adds its mana when cast, not when tapped.
    if kind in ONE_SHOT_KINDS:
        return ()

    if not profile.produces_mana or profile.mana_amount is None:
        return ()

    # "Activate only if you control ..." (P19 R4): the ability carries the
    # condition, and what the card taps for without it follows as a second,
    # plain ability - the game takes the first one whose condition holds.
    condition = getattr(profile, "mana_condition", None)
    if condition and "mana_activation" not in overrides:
        only_if = _condition(condition["if"])
        main = _flat(profile.mana_produces, profile.mana_colors, profile.mana_amount,
                     activation, deck_colors, only_if)
        otherwise = condition.get("otherwise")
        if not otherwise:
            return (main,)
        return (main, _flat(otherwise["produces"], profile.mana_colors, otherwise["amount"],
                            int(otherwise.get("activation") or 0), deck_colors, None))

    return (_flat(getattr(profile, "mana_produces", None), profile.mana_colors,
                  profile.mana_amount, activation, deck_colors, None),
            *_filter(profile, deck_colors))


def _filter(profile, deck_colors: frozenset[str]) -> tuple[ManaAbility, ...]:
    """A filter or converter beside the plain ability, if the reader found one (P19 R6).

    Its output is a choice of the colours it offers - a filter land's two,
    "any color" the deck's own - or the exact mana the text names.
    """
    found = getattr(profile, "mana_filter", None)
    if not found:
        return ()
    if found.get("produces"):
        produces = found["produces"]
    else:
        colors = list(found.get("offers") or COLORS)
        offered = [color for color in colors if color in deck_colors] or colors
        produces = {choice(offered): int(found["amount"])}
    return (ManaAbility(FILTER, produces, pays_with=found.get("pays_with", "")),)


def _flat(exact: dict | None, mana_colors, amount: int, activation: int,
          deck_colors: frozenset[str], only_if: TappedUnless | None) -> ManaAbility:
    """One ``FLAT`` ability: the exact mana the text names, or a choice."""
    # The text named every symbol - a Signet's {U}{B} is one of EACH, not a
    # choice between them, so there is no colour to pick and nothing to report.
    if exact:
        return ManaAbility(FLAT, exact, activation_generic=activation, only_if=only_if)

    # `mana_colors` is Scryfall's `produced_mana`: it lists every colour the
    # card can make, without saying whether that is a choice or all of them at
    # once. Nearly always it is a choice - a dual land, a Talisman, Command
    # Tower - and since engine version 5 the pool holds one (P19 R1), settled
    # when the mana is spent. Only the deck's own colours are offered: Command
    # Tower and Arcane Signet name all five, and make only the commander's.
    # Several mana of a choice are each chosen on their own, which is right
    # for "any combination of colours" and generous for "any one colour".
    colors = [color for color in mana_colors or [] if color in COLORS]
    if not colors:
        return ManaAbility(FLAT, {COLORLESS: amount}, activation_generic=activation,
                           only_if=only_if)
    offered = [color for color in colors if color in deck_colors] or colors
    return ManaAbility(FLAT, {choice(offered): amount}, activation_generic=activation,
                       only_if=only_if)


def _any_colour(deck_colors: frozenset[str]) -> str:
    """"One mana of any color", as the deck spends it: a choice of its colours."""
    return choice([color for color in COLORS if color in deck_colors] or COLORS)


def _overrides_mana(overrides: dict) -> bool:
    """Whether an annotation says what this card taps for."""
    return _annotated_production(overrides) is not None


def _condition(found: dict) -> TappedUnless:
    """A condition the reader read, as the engine's (P19 R3, R4 and R12)."""
    return TappedUnless(
        kind=found["kind"], types=frozenset(found.get("types", ())),
        count=int(found.get("count") or found.get("life") or 0),
        at_least=bool(found.get("at_least", True)), other=bool(found.get("other", False)),
        basic=bool(found.get("basic", False)), type=found.get("type", ""),
        legendary=bool(found.get("legendary", False)),
    )


def _annotated_production(overrides: dict) -> dict | None:
    """What an annotation says the card taps for, or `None` for no opinion.

    `mana_produces` is the general form; `mana_black` and `mana_colorless` are
    the two-colour form that existing annotation rows were written with, and
    they keep working because a stored judgement is not invalidated by the
    engine learning about green.
    """
    if "mana_produces" in overrides:
        produces = overrides["mana_produces"] or {}
        return {color: int(amount) for color, amount in produces.items() if int(amount)}

    black = overrides.get("mana_black")
    colorless = overrides.get("mana_colorless")
    if black is None and colorless is None:
        return None
    produced = {"B": int(black or 0), COLORLESS: int(colorless or 0)}
    return {color: amount for color, amount in produced.items() if amount}


def _cost(oracle_card, profile, overrides: dict) -> ManaCost:
    """The card's full mana cost.

    Parsed from the printed cost rather than assembled from the profile,
    because the profile stores hybrid pips as a requirement in *every* colour
    they could be paid with - true as "may be paid with", wrong as "must be
    paid with", and the engine's payer is the one thing that can tell the
    difference. `DerivedProfile` keeps the flattened numbers for display.

    An annotation that sets `pips` or `generic` still wins: someone correcting
    a cost is correcting it for a reason.
    """
    if "pips" in overrides or "generic" in overrides:
        pips = overrides.get("pips", profile.pips)
        # An older annotation may say `"pips": 2`, meaning two black ones.
        colored = {"B": int(pips)} if isinstance(pips, (int, float)) else {
            color: int(amount) for color, amount in (pips or {}).items() if int(amount)
        }
        return ManaCost(
            pips=tuple(sorted(colored.items())),
            generic=int(overrides.get("generic", profile.generic)),
            colorless=int(profile.colorless),
            has_x=bool(profile.has_x),
        )
    if getattr(profile, "mana_from_hand", False):
        # Elvish Spirit Guide (P19 R15): exiled from the hand, not cast.
        return ManaCost()
    # A spree mode the engine plays costs its own mana on top (P19 R8) - but
    # only the derived draw's mode: a person who set the draw said what it is.
    extra = "" if "draw_on_cast" in overrides else getattr(profile, "extra_cost", "")
    return parse((oracle_card.mana_cost or "") + (extra or ""))


def _ritual_color(profile, overrides: dict, deck_colors: frozenset[str]) -> str:
    """Which colour a ritual adds. Dark Ritual black, Rite of Flame red."""
    if overrides.get("ritual_color"):
        return str(overrides["ritual_color"]).upper()
    colors = [color for color in profile.mana_colors or [] if color in COLORS]
    return _pick_color(colors, deck_colors) or _pick_color(deck_colors, deck_colors) or "B"


def _read_rule(profile, key: str) -> str:
    """One value of the profile's read mana rule, or ""."""
    rule = getattr(profile, "mana_rule", None) or {}
    return str(rule.get(key) or "")


def _ritual_counts(profile, overrides: dict, kind: str) -> str:
    """What a ritual counts as it resolves (P19 R13): Battle Hymn, High Tide."""
    if kind != "ritual" or "ritual_gain" in overrides or _read_rule(profile, "rule") != "ritual":
        return ""
    return _read_rule(profile, "subtype")


def _ritual_gain(profile, overrides: dict, kind: str) -> int:
    if "ritual_gain" in overrides:
        return int(overrides["ritual_gain"])
    if kind == "ritual" and profile.mana_amount:
        return int(profile.mana_amount)
    return 0


def _cost_reduction(profile, overrides: dict) -> CostReduction | None:
    amount = overrides.get("cost_reduction", profile.cost_reduction)
    return CostReduction(amount=int(amount)) if amount else None


def _draw_on_cast(profile, overrides: dict, kind: str) -> int:
    if "draw_on_cast" in overrides:
        return int(overrides["draw_on_cast"])
    if kind in ONE_SHOT_KINDS and profile.draws_cards:
        return int(profile.draws_cards)
    return 0


def _after_draw(profile, overrides: dict, kind: str, key: str, field_name: str) -> int:
    """What the derived draw gives back: a discard or a put-back (P19 R8).

    A person who set the draw has said what the spell does; the derived
    discard then no longer belongs to it unless they set that too.
    """
    if key in overrides:
        return int(overrides[key])
    if "draw_on_cast" in overrides or kind not in ONE_SHOT_KINDS:
        return 0
    if not (profile.draws_cards or getattr(profile, "draws_x", False)):
        return 0
    return int(getattr(profile, field_name, 0) or 0)


def _x_count(oracle_card, profile) -> int:
    """How many {X} the cost has (P19 R9): three on Astral Cornucopia."""
    if not profile.has_x:
        return 0
    return (oracle_card.mana_cost or "").upper().count("{X}") or 1


#: The zones a `TutorSpec` can search to, and whether that is the hand. The
#: battlefield joined in P19 R10, read whole off the text (`tutor_filter`); a
#: battlefield tutor the reader could not read stays a gap.
TUTOR_ZONES = {"hand": True, "graveyard": False, "battlefield": False, "top": False}


def _tapped_unless(profile, overrides: dict) -> TappedUnless | None:
    """When a land that enters tapped does not, as the reader read it (P19 R3).

    Only while nobody said otherwise: an annotation that sets `enters_tapped`
    is a person's answer about this land, and it stands as given.
    """
    found = getattr(profile, "tapped_unless", None)
    if not found or "enters_tapped" in overrides:
        return None
    return _condition(found)


def _land_search(profile) -> LandSearch | None:
    """The land search the reader read whole, or nothing (P19 R2).

    No annotation reaches it yet: the reader either has every value or none,
    and a card it could not read keeps its gap.
    """
    found = getattr(profile, "land_search", None)
    if not found:
        return None
    return LandSearch(
        battlefield=int(found["battlefield"]), hand=int(found["hand"]),
        tapped=bool(found["tapped"]), basic=bool(found["basic"]),
        types=frozenset(found["types"]), life=int(found["life"]), when=found["when"],
        sacrifice=bool(found["sacrifice"]), untap_at=int(found.get("untap_at", 0)),
        cost=parse(found["cost"]) if found.get("cost") else None,
        taps=bool(found.get("taps")), share_type=bool(found.get("share_type")),
        each=bool(found.get("each")), condition=found.get("condition", ""),
        sacrifices_land=bool(found.get("sacrifices_land")),
        land_cost_types=frozenset(found.get("land_cost_types", ())),
        sacrifice_other=frozenset(found.get("sacrifice_other", ())),
        discard=int(found.get("discard", 0)),
    )


def _sacrifice_mana(profile, deck_colors: frozenset[str]) -> dict:
    """An altar's mana, as `Card` fields (P19 R15): what it takes, what one
    sacrifice gives and in what colour - "any color" being the deck's. An
    annotation that says what the card taps for says this too: the reference
    deck's Ashnod's Altar is a sacrifice engine that makes no mana."""
    found = getattr(profile, "sacrifice_mana", None)
    if not found or not found.get("amount"):
        return {}
    produces = found.get("produces")
    if produces is None:
        color = _any_colour(deck_colors)
    elif len(produces) == 1:
        color = next(iter(produces))
    else:
        return {}
    return {"sacrifice_mana": AdditionalCost(sacrifice=frozenset(found["sacrifice"]),
                                             sacrifice_filter=found.get("filter", "")),
            "sacrifice_mana_amount": int(found["amount"]), "sacrifice_mana_color": color,
            "sacrifice_mana_taps": bool(found.get("taps"))}


def _additional_costs(profile) -> tuple[AdditionalCost, ...]:
    """The ways the reader read the card's additional cost (P19 R15)."""
    return tuple(
        AdditionalCost(
            sacrifice=frozenset(way.get("sacrifice", ())), sacrifice_filter=way.get("filter", ""),
            life=int(way.get("life", 0)), life_x=bool(way.get("life_x")),
            discard=int(way.get("discard", 0)),
            mana=parse(way["mana"]) if way.get("mana") else None,
            exile_from_graveyard=way.get("exile_from_graveyard", ""))
        for way in getattr(profile, "additional_cost", None) or ())


def _tutor(profile, overrides: dict) -> TutorSpec | None:
    """The search this card performs, from an annotation or from the reading.

    An annotation still wins outright, and a `tutor_count` of zero is how
    somebody says "this is not a tutor" - the same blank-means-no-opinion rule
    as everywhere else, because a derived tutor a user cannot switch off would
    be a judgement made on their behalf.

    Without one, the derived reading is used only when it is **complete**: a
    zone the engine can reach and an amount somebody could read off the card.
    A half-read tutor is a gap, never a `TutorSpec` with a plausible 1 in it.
    """
    if "tutor_count" in overrides:
        if not overrides["tutor_count"]:
            return None
        return TutorSpec(
            to_hand=bool(overrides.get("tutor_to_hand", True)),
            count=int(overrides["tutor_count"]),
            life=int(overrides.get("tutor_life", 0)),
            kind=overrides.get("tutor_kind", ""),
        )

    if profile.tutor_to not in TUTOR_ZONES or profile.tutor_count is None:
        return None
    limit = getattr(profile, "tutor_filter", None)
    if profile.tutor_to == "battlefield" and limit is None:
        return None

    battlefield = {}
    if profile.tutor_to == "top":
        # P19 R12: Vampiric Tutor. `tutor_filter` holds the card types it may find.
        battlefield = {"to_top": True,
                       "types": frozenset((limit or {}).get("types", ()))}
    elif limit is not None:
        max_mv = limit.get("max_mv")
        battlefield = {"to_battlefield": True, "color": limit.get("color") or "",
                       "max_mv_x": max_mv == "X",
                       "max_mv": max_mv if isinstance(max_mv, int) else None,
                       "max_mv_sacrificed": limit.get("plus_sacrificed")}
    return TutorSpec(
        **battlefield,
        to_hand=TUTOR_ZONES[profile.tutor_to],
        count=int(profile.tutor_count),
        # Grim Tutor's three life is already derived, by the same pronoun check
        # that separates a cost from a payoff. Reusing it here rather than
        # reading the text twice is the point of having derived it.
        life=int(overrides.get("tutor_life", profile.self_life_loss or 0)),
        kind=overrides.get("tutor_kind", profile.tutor_kind),
    )


def _upkeep(overrides: dict) -> UpkeepSpec | None:
    if not overrides.get("upkeep_draw"):
        return None
    return UpkeepSpec(
        draw=int(overrides["upkeep_draw"]),
        life=int(overrides.get("upkeep_life", 0)),
        life_per_mv=bool(overrides.get("upkeep_life_per_mv", False)),
    )


def _end_step(overrides: dict) -> EndStepSpec | None:
    if not overrides.get("end_step_max_hand"):
        return None
    return EndStepSpec(
        max_hand=int(overrides["end_step_max_hand"]),
        life_floor=int(overrides.get("end_step_life_floor", 25)),
    )


#: The five basic land types, as the engine spells them.
#: `manacost.SUBTYPE_COLORS` turns each into the colour that land taps for, and
#: `simulation/cards.py` has had a constant for each since Phase 2.
BASIC_LAND_SUBTYPES = frozenset({"plains", "island", "swamp", "mountain", "forest"})

#: How Scryfall separates a type line's types from its subtypes. An em dash in
#: practice; the other two are accepted because a hand-written fixture or a
#: re-typed line is not worth losing a land's colour over.
TYPE_SEPARATORS = ("—", "–", " - ")


def _printed_subtypes(oracle_card) -> frozenset[str]:
    """The basic land types printed on this card's type line.

    Read off the type line rather than from `DerivedProfile`, which records only
    `is_basic_swamp` - a leftover from when the engine was mono-black. Without
    this, a basic Forest reaches the engine with no subtypes at all, and the
    four non-swamp constants in `simulation/cards.py` are unreachable from any
    deck stored in the database.

    What it costs to get wrong is not the colour - a Forest still taps for green
    through its own mana ability - but every rule that *counts* subtypes:
    Cabal Coffers' per-swamp scaling, Crypt Ghast's doubling, and the land types
    Urborg and Yavimaya hand out. Those quietly saw one basic type in the world.

    Only the basic five are returned. `Land — Urza's Mine` is a real subtype and
    means nothing to the engine, and a set full of types no rule reads would
    make `Card.subtypes` look more informative than it is.
    """
    type_line = oracle_card.type_line or ""
    if "Land" not in type_line:
        return frozenset()

    tail = ""
    for separator in TYPE_SEPARATORS:
        if separator in type_line:
            tail = type_line.split(separator, 1)[1]
            break

    printed = {word.strip().casefold() for word in tail.replace("//", " ").split()}
    return frozenset(printed & BASIC_LAND_SUBTYPES)


def _categories(profile, overrides: dict, tags_scope: str | None) -> frozenset[str]:
    """The roles the draw statistics sort this card by (`Card.categories`).

    The community's roles, unless the **user** replaced them - on this deck or
    on all of theirs - in which case their list is the answer, an empty one
    included. A **built-in** replacement is ignored here: the built-in
    annotations exist so that the reference deck plays exactly as its original
    hand-written list did, which is why Phyrexian Arena is only `draw_engine`
    there. That keeps a game metric right and would make every Phyrexian Arena
    on the site vanish from "Card draw" - it is a statement about how the
    engine plays the card, not about what the card is.
    """
    if "tags" in overrides and tags_scope != BUILTIN_SCOPE:
        return frozenset(overrides["tags"])
    return frozenset(profile.role_tags)


def card_types(oracle_card) -> frozenset[str]:
    """The card types printed on the front face, as `Card.types` holds them.

    **The front face only.** A modal double-faced card such as "Sorcery // Land"
    is counted as what its front says, which is how a deck list sorts it and
    how a player names it; counting both faces would put one card in two type
    columns and make the type chart add up to more cards than were drawn.
    An artifact creature is still both - those are two types on one face.

    Not overridable by an annotation: the type line is printed, not judged.
    """
    front = (oracle_card.type_line or "").split("//", 1)[0]
    for separator in TYPE_SEPARATORS:
        front = front.split(separator, 1)[0]
    words = {word.casefold() for word in front.split()}
    return frozenset(kind for kind in CARD_TYPES if kind in words)


def creature_types(oracle_card) -> frozenset[str]:
    """The creature types on the front face, lower case (P19 R11): what a
    "for each Elf" counts. Empty for a card that is not a creature."""
    front = (oracle_card.type_line or "").split("//", 1)[0]
    if "Creature" not in front:
        return frozenset()
    for separator in TYPE_SEPARATORS:
        if separator in front:
            return frozenset(word.casefold() for word in front.split(separator, 1)[1].split())
    return frozenset()


def _subtypes(oracle_card, profile, overrides: dict) -> frozenset[str]:
    if "subtypes" in overrides:
        return frozenset(overrides["subtypes"])
    printed = _printed_subtypes(oracle_card)
    if printed:
        return printed
    # A basic Swamp always prints its type, so this is belt and braces - kept
    # because `is_basic_swamp` is what the mono-black path was built on and a
    # card reaching here without a type line should still behave as before.
    return frozenset({"swamp"}) if profile.is_basic_swamp else frozenset()


def _record_gaps(card: Card, profile, overrides: dict, gaps: list[Gap],
                 card_text: str = "") -> None:
    """Note everything a human would still have to decide about this card."""
    if card.priority is None and card.goldfish_castable and not card.is_land:
        # Only worth reporting for cards the agent might actually cast. Removal
        # and wipes have no legal target against no opponent, so their casting
        # order is not a gap in what we know - it never comes up.
        gaps.append(Gap(card.name, "priority", gettext_noop(
            "no one said how early to cast it; the default rule applies")))

    if profile.needs_review:
        for reason in profile.review_reasons:
            gaps.append(Gap(card.name, "profile", reason))

    if profile.produces_mana and profile.mana_amount is None and not card.mana_abilities \
            and not card.treasures and not card.ritual_counts and card.sacrifice_mana is None:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("makes mana, but how much could not be read")))

    if card.land_search is not None and card.land_search.condition == "opponent_more_lands":
        gaps.append(Gap(card.name, "assumed_lands", gettext_noop(
            "searches when you have fewer lands than turns gone by, assuming each opponent "
            "plays a land a turn")))

    if any(way.life_x for way in card.additional_costs):
        gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
            "pays X life with X = 0: in a goldfish there is nothing for X to hit")))

    if card.tapped_unless is not None and card.tapped_unless.kind == "opponent_lands":
        gaps.append(Gap(card.name, "assumed_lands", gettext_noop(
            "enters untapped from your fourth turn, assuming each opponent plays a land "
            "a turn")))

    if card.mana_abilities and _OPPONENTS_LANDS.search(card_text) \
            and not _overrides_mana(overrides):
        gaps.append(Gap(card.name, "assumed_mana", gettext_noop(
            "makes your deck's colours, assuming your opponents' lands make them")))

    fetches = card.land_search is not None and card.land_search.when == "play"
    # A land with a basic land type taps for its colour (Dryad Arbor), and one
    # whose text adds no mana and lists none really makes none: Maze of Ith,
    # Dark Depths. Neither is something the engine failed to read (P19 R4).
    typed = land_color(card, frozenset()) is not None
    makes_none = not profile.produces_mana and not _COPIES.search(card_text)
    if card.is_land and not card.mana_abilities and not fetches and not typed \
            and not makes_none:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("a land that taps for nothing the engine can see")))
