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
from dataclasses import dataclass, field, replace

from django.utils.translation import gettext, gettext_noop, ngettext

from cards import categories as card_categories
from decks.models import Deck
from simulation import ENGINE_VERSION, agent, blocks
from simulation.cards import (
    CARD_TYPES,
    COMBAT_SEARCH,
    COMBAT_SEARCH_UNTAPPED,
    COUNTS,
    DISCARD_LAND,
    DOUBLE_SUBTYPE,
    DRAW_ENGINE,
    ENGINE_ROLES,
    EXTRA,
    FILTER,
    FLAT,
    GRANT,
    IMPRINT,
    INSTANT,
    LANDS_COULD_PRODUCE,
    MULTIPLY,
    PER_CONTROLLED,
    RITUAL,
    SORCERY,
    STORE_FREE,
    STORE_SCALES,
    STORE_TAP,
    TYPE_ADDING,
    AdditionalCost,
    Card,
    CombatTrigger,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    Growth,
    LandSearch,
    ManaAbility,
    SpellFilter,
    Store,
    TapMana,
    TappedUnless,
    Trigger,
    TutorSpec,
    UpkeepSpec,
)
from simulation.mana import land_color
from simulation.manacost import COLORLESS, COLORS, ManaCost, choice, mana_label, parse
from simulation.rules.mana import ritual_counts_of
from simulation.rules.selector import wanted_of
from simulations import gaps as gaps_module
from simulations import patches, primitives

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
    chosen = deck_creature_type(entries, deck)
    color = deck_color(entries, deck, colors)

    for entry in entries:
        card = _card_from(entry.oracle_card, annotations, gaps, colors, chosen, color)
        seen.add(entry.oracle_card_id)
        library.extend([card] * entry.quantity)

    # The commanders count toward the total, because gaps are recorded for
    # them like any other card. Leaving one out made `cards_with_gaps` able to
    # exceed `cards_total` - a two-card deck whose commander the engine could
    # not read reported **-50% coverage**, which is a worse answer than no
    # answer. They are not DeckCards (trap 7), so `seen` is the only thing that
    # knows whether one has already been counted. Two since P20 F2b.
    commander, partner = (
        _card_from(oracle, annotations, gaps, colors, chosen, color) if oracle else None
        for oracle in (deck.commander if deck.commander_id else None,
                       deck.partner if deck.partner_id else None))
    seen.update(oracle.pk for oracle in deck.commanders)

    if adding is not None:
        library.append(_card_from(adding, annotations, gaps, colors, chosen, color))
        seen.add(adding.pk)

    definition = DeckDefinition(
        name=deck.name,
        commander=commander,
        library=tuple(library),
        partner=partner,
    )
    copies = Counter(card.name for card in library)
    for each in definition.commanders:
        copies[each.name] += 1
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
    #: The owner's corrections whose block changed or is gone since they were
    #: written (`Check`) - "please check". Built-in ones are the operator's.
    checks: list = field(default_factory=list)
    #: The card's main role, one of `card.categories` (card model phase 3):
    #: what the deck page's slots count. Empty for a card without one - a
    #: land, a plan card - and for every card until its profile is derived
    #: again.
    primary: str = ""

    @property
    def names_type(self) -> bool:
        """Its mana pays for "the chosen type" - Cavern of Souls (P19 R17)."""
        profile = getattr(self.oracle_card, "profile", None)
        return _chosen_type(profile, {}, ("", 0)) is not None

    @property
    def names_color(self) -> bool:
        """It makes, or pays for, "the chosen color" - Heraldic Banner (P19 R18)."""
        profile = getattr(self.oracle_card, "profile", None)
        return _names_color(profile)

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
        if self.card.play.ways:
            # P19 R15: what else is paid, any one of the ways.
            ways = " / ".join(_way_text(way) for way in self.card.play.ways)
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
        """The roles the engine plays the card by, read off its categories
        (`engine_roles`)."""
        return sorted(self.card.engine_roles)

    @property
    def searches(self) -> str:
        """The library search the engine will actually perform.

        Spelled out in full because a tutor is the one derived value that can
        change a whole game on its own, and because the two halves come from
        different places - the zone from a community tag, the number from the
        printed text - so a person checking it needs to see both at once.
        """
        spec = self.card.play.tutor
        if spec is None and self.card.play.search is not None:
            return _land_search_text(self.card.play.search)
        if spec is None:
            return gettext("nothing")
        if spec.kind or spec.types:
            from cards.models import DerivedProfile

            kinds = dict(DerivedProfile.Kind.choices)
            named = [spec.kind] if spec.kind else sorted(spec.types)
            what = gettext("%(count)s × %(kind)s") % {
                "count": spec.count,
                "kind": " / ".join(str(kinds.get(kind, kind.capitalize())) for kind in named)}
        else:
            what = ngettext("%(count)s card", "%(count)s cards", spec.count) % {
                "count": spec.count}
        if spec.delirium_any:
            what = gettext("%(what)s, any card with delirium") % {"what": what}
        if spec.max_mv_x or spec.max_mv is not None:
            what = gettext("%(what)s with mana value %(limit)s or less") % {
                "what": what, "limit": "X" if spec.max_mv_x else spec.max_mv}
        if spec.to_battlefield:
            if spec.color:
                what = gettext("%(what)s (%(color)s)") % {"what": what,
                                                          "color": mana_label(spec.color)}
            if spec.max_mv_sacrificed is not None:
                what = gettext("%(what)s with mana value up to %(plus)s more than the "
                               "creature sacrificed") % {"what": what,
                                                         "plus": spec.max_mv_sacrificed}
            text = gettext("%(what)s onto the battlefield") % {"what": what}
        elif spec.to_top:
            text = gettext("%(what)s on top of the library, drawn next turn") % {"what": what}
        elif spec.gifts:
            text = gettext("%(found)s cards, %(count)s of them to hand and the rest to the "
                           "graveyard") % {"found": spec.gifts, "count": spec.count}
        elif spec.land_tapped:
            text = gettext("%(what)s to hand, a land onto the battlefield tapped") % {
                "what": what}
        else:
            text = (gettext("%(what)s to hand") if spec.to_hand
                    else gettext("%(what)s to the graveyard")) % {"what": what}
        if spec.random_discard:
            text = gettext("%(search)s, then a card discarded at random") % {"search": text}
        if spec.upkeep:
            text = gettext("in an upkeep, sacrificed once an opponent controls three "
                           "creatures: %(search)s") % {"search": text}
        if spec.life:
            text = gettext("%(search)s, paying %(life)s life") % {"search": text,
                                                                 "life": spec.life}
        return text

    @property
    def skips_draw_step(self) -> str:
        return gettext("yes") if self.card.turn.skips_draw_step else gettext("no")

    @property
    def triggers(self) -> str:
        """The triggers the engine plays (P19 R16), in combat too (P19 R22),
        in words."""
        return "; ".join([*(_trigger_text(trigger) for trigger in self.card.turn.triggers),
                          *(_combat_text(trigger) for trigger in self.card.turn.combat)])

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
        if not self.card.mana.entries:
            return gettext("nothing")
        text = "; ".join(_ability_text(source) for source in self.card.mana.entries)
        if any(gap.field == "assumed_mana" for gap in self.gaps):
            text = gettext("%(mana)s - assuming your opponents' lands make these colours") % {
                "mana": text}
        if not self.card.mana.untaps:
            text = gettext("%(mana)s - once, then it stays tapped") % {"mana": text}
        return text

    @property
    def activation(self) -> str:
        """What tapping it for mana costs beside the tap, in words."""
        cost = sum(
            ability.activation_generic
            for ability in self.card.mana.entries
            if ability.rule == FLAT
        )
        return f"{{{cost}}}" if cost else gettext("nothing beyond tapping")

    @property
    def untaps(self) -> bool:
        return self.card.mana.untaps

    @property
    def enters_tapped(self) -> bool:
        return self.card.play.enters_tapped


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
    if search.by_power:
        what = gettext("as many basic lands as the greatest power among your creatures")
    elif search.reveal:
        what = gettext("X lands off the top of the library")
    elif search.basic:
        what = ngettext("%(count)s basic land", "%(count)s basic lands",
                        search.battlefield) % {"count": search.battlefield}
    else:
        what = ngettext("%(count)s land", "%(count)s lands",
                        search.battlefield) % {"count": search.battlefield}
    if types:
        what = f"{what} ({types})"
    if search.also:
        what = gettext("%(what)s or Gates") % {"what": what}
    if search.tempting:
        what = gettext("%(what)s, and one more for each opponent who searches too") % {
            "what": what}
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
    if search.power_at:
        text = gettext("%(search)s; %(count)s beside a creature with power %(power)s or "
                       "greater") % {"search": text, "count": search.power_count,
                                     "power": search.power_at}
    if search.condition == "opponent_more_lands":
        text = gettext("%(search)s, if an opponent controls more lands") % {"search": text}
    if search.repeat:
        text = gettext("%(search)s, then once more") % {"search": text}
    if search.when == "opponents":
        text = gettext("in each opponent's end step: %(search)s") % {"search": text}
    if search.sacrifices_land and search.when in ("enters", "cast"):
        text = gettext("sacrifice a land: %(search)s") % {"search": text}
    return text


#: When a trigger fires, in words (P19 R16).
_TRIGGER_WHEN = {
    ("landfall", ""): gettext_noop("whenever a land of yours enters"),
    ("cast", ""): gettext_noop("whenever you cast a spell"),
    ("cast", "instant_sorcery"): gettext_noop("whenever you cast an instant or sorcery"),
    ("cast", "colorless"): gettext_noop("whenever you cast a colourless spell"),
    ("cast", "second"): gettext_noop("on your second spell each turn"),
    ("upkeep", ""): gettext_noop("each upkeep"),
    ("main", ""): gettext_noop("at each first main phase"),
    ("dies", "another"): gettext_noop("whenever another creature of yours dies"),
    ("dies", ""): gettext_noop("whenever it or another creature of yours dies"),
    ("resolves", "per_land"): gettext_noop("as it resolves, for each land you control"),
    # A permanent's "When it enters, draw a card" (card model phase 1).
    ("resolves", ""): gettext_noop("as it enters"),
    ("opponents", ""): gettext_noop("once a round, when one opponent does not pay"),
    ("sacrificed", "artifact"): gettext_noop(
        "whenever you sacrifice an artifact, a Treasure spent included"),
}


def _trigger_text(trigger: Trigger) -> str:
    """One trigger, in words: when, then what."""
    if trigger.event == "enters":
        when = gettext("whenever it or another %(type)s of yours enters") % {
            "type": trigger.filter.capitalize()}
    else:
        when = gettext(_TRIGGER_WHEN.get((trigger.event, trigger.filter), trigger.event))
    made = []
    if trigger.treasures:
        if trigger.treasure_mana == COLORLESS:
            made.append(ngettext("%(count)s Eldrazi Spawn (one {C})",
                                 "%(count)s Eldrazi Spawn (one {C} each)",
                                 trigger.treasures) % {"count": trigger.treasures})
        else:
            made.append(ngettext("%(count)s Treasure", "%(count)s Treasures",
                                 trigger.treasures) % {"count": trigger.treasures})
    if trigger.mana:
        made.append(gettext("%(count)s × %(color)s mana") % {
            "count": trigger.mana, "color": mana_label(trigger.mana_color)})
    if trigger.draw:
        made.append(ngettext("draws %(count)s card", "draws %(count)s cards",
                             trigger.draw) % {"count": trigger.draw})
    if trigger.life:
        made.append(gettext("costs %(life)s life") % {"life": trigger.life})
    if trigger.counter:
        made.append(gettext("a +1/+1 counter on it"))
    if trigger.connive:
        made.append(gettext("connives"))
    return gettext("%(when)s: %(what)s") % {"when": when, "what": ", ".join(made)}


#: When a trigger in combat fires, in words (P19 R22).
_COMBAT_WHEN = {
    ("attacks", "self"): gettext_noop("whenever it attacks"),
    ("attacks", "equipped"): gettext_noop("whenever the creature it equips attacks"),
    ("attacks", "enchanted"): gettext_noop("whenever the enchanted creature attacks"),
    ("attacks", "commander"): gettext_noop("whenever your commander attacks"),
    ("attacks", "curse"): gettext_noop("whenever you attack the enchanted player"),
    ("damage", "self"): gettext_noop("whenever it deals combat damage to a player"),
    ("damage", "equipped"): gettext_noop(
        "whenever the creature it equips deals combat damage to a player"),
    ("damage", "enchanted"): gettext_noop(
        "whenever the enchanted creature deals combat damage to a player"),
    ("damage", "any"): gettext_noop(
        "once a combat, when creatures of yours deal combat damage to a player"),
    ("damage", "modified"): gettext_noop(
        "for each modified creature of yours that deals combat damage to a player"),
    ("damage", "total"): gettext_noop(
        "in your second main phase, for each life your opponents lost in combat"),
}


def _combat_text(trigger: CombatTrigger) -> str:
    """One trigger in combat, in words: when, then what (P19 R22)."""
    when = gettext(_COMBAT_WHEN.get((trigger.event, trigger.who), trigger.event))
    if trigger.types:
        when = f"{when} ({' / '.join(sorted(kind.capitalize() for kind in trigger.types))})"
    if trigger.search:
        what = _land_search_text(COMBAT_SEARCH_UNTAPPED if trigger.untapped else COMBAT_SEARCH)
    elif trigger.who == "total":
        what = "".join(f"{{{key}}}" for key in trigger.mana)
    elif trigger.mana:
        what = gettext("%(mana)s, kept to the second main phase") % {
            "mana": "".join(f"{{{key}}}" for key in trigger.mana)}
    else:
        what = ngettext("%(count)s Treasure", "%(count)s Treasures",
                        trigger.treasures) % {"count": trigger.treasures}
    return gettext("%(when)s: %(what)s") % {"when": when, "what": what}


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
        if way.sacrifice_count > 1:
            parts.append(gettext("sacrifice %(count)s × %(what)s") % {
                "count": way.sacrifice_count, "what": what})
        else:
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
    """One mana ability as the engine plays it (`rules.mana.Source`), in words."""
    if ability.rule == FLAT:
        produced = " + ".join(
            f"{amount} {mana_label(color)}" for color, amount in ability.produces
        ) or gettext("nothing")
        if ability.activation_generic:
            produced = _for_cost(ability.activation_generic, produced)
        if ability.only_if is not None:
            produced = gettext("%(mana)s if you control %(what)s") % {
                "mana": produced, "what": _condition_text(ability.only_if)}
        if ability.spend_only:
            produced = gettext("%(mana)s, only for %(spells)s") % {
                "mana": produced, "spells": spells_text(ability.spend_only)}
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


#: The words of a spell filter (P19 R17), translated one by one.
_SPELL_WORDS = {
    "legendary": gettext_noop("legendary"), "colorless": gettext_noop("colourless"),
    "multicolored": gettext_noop("multicoloured"), "noncreature": gettext_noop("noncreature"),
    "artifact": gettext_noop("artifact"), "creature": gettext_noop("creature"),
    "enchantment": gettext_noop("enchantment"), "instant": gettext_noop("instant"),
    "sorcery": gettext_noop("sorcery"), "planeswalker": gettext_noop("planeswalker"),
}


def spells_text(filters) -> str:
    """The spells restricted mana pays for (selectors), in words: "legendary
    spells or Dragon creature spells". Only spells: the engine pays no ability
    with it."""
    parts = []
    for wanted in filters:
        words = [gettext(_SPELL_WORDS[flag]) for flag in
                 ("legendary", "colorless", "multicolored", "noncreature")
                 if getattr(wanted, flag)]
        if wanted.named_types:
            words.append("/".join(sorted(subtype.title() for subtype in wanted.named_types)))
        if wanted.types:
            words.append("/".join(gettext(_SPELL_WORDS[kind]) for kind in sorted(wanted.types)))
        if wanted.only_color:
            # Throne of Eldraine (P19 R18).
            words.append(gettext("monocoloured %(color)s") % {
                "color": mana_label(wanted.only_color)})
        parts.append(gettext("%(what)s spells") % {"what": " ".join(words)})
    text = parts[0] if parts else ""
    for part in parts[1:]:
        text = gettext("%(one)s or %(other)s") % {"one": text, "other": part}
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
        if ability.subtype == "creature:counter":
            return gettext("your creatures with a counter tap for %(mana)s") % {"mana": mana}
        if ability.subtype == "equipped":
            return gettext("the creature it equips taps for %(mana)s") % {"mana": mana}
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


#: P19 R21: mana by power, toughness or counters, in words.
_STAT_TEXT = {
    "power": gettext_noop("%(color)s equal to its power"),
    "power:greatest": gettext_noop("%(color)s equal to the greatest power among your creatures"),
    "toughness:greatest_other": gettext_noop(
        "%(color)s equal to the greatest toughness among your other creatures"),
    "counters": gettext_noop("one %(color)s for each +1/+1 counter on it"),
}


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
    if what.startswith("devotion:"):
        return gettext("as much %(color)s as your devotion to it") % {"color": color}
    if what in _STAT_TEXT:
        if not color:
            color = gettext("mana of one colour")
        return gettext(_STAT_TEXT[what]) % {"color": color}
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
    if condition.kind == "power":
        return gettext("a creature with power %(count)s or greater") % {
            "count": condition.count}
    if condition.kind == "tap_fodder":
        return _fodder_label(condition.types, condition.legendary)
    types = " / ".join(sorted(subtype.capitalize() for subtype in condition.types))
    return gettext("a land of type %(types)s") % {"types": types}


def _fodder_label(types, legendary: bool) -> str:
    """What a card taps besides itself for its mana, in words (P19 R19)."""
    if "permanent" in types:
        return gettext("an untapped permanent to tap for it")
    if {"artifact", "creature"} <= set(types):
        return gettext("an untapped artifact or creature to tap for it")
    if "artifact" in types:
        return gettext("an untapped artifact to tap for it")
    if legendary:
        return gettext("an untapped legendary creature to tap for it")
    return gettext("an untapped creature to tap for it")


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
    chosen = deck_creature_type(entries, deck)
    color = deck_color(entries, deck, colors)

    found: list[Reading] = []
    seen: set = set()

    for entry in entries:
        gaps: list[Gap] = []
        card = _card_from(entry.oracle_card, annotations, gaps, colors, chosen, color)
        seen.add(entry.oracle_card_id)
        found.append(
            Reading(
                oracle_card=entry.oracle_card,
                card=card,
                gaps=gaps,
                quantity=entry.quantity,
                checks=annotations.checks_of(entry.oracle_card_id),
                primary=primary_role(entry.oracle_card, card.categories,
                                     annotations.for_card(entry.oracle_card_id)),
            )
        )

    for oracle in deck.commanders:
        if oracle.pk in seen:
            continue
        gaps = []
        card = _card_from(oracle, annotations, gaps, colors, chosen, color)
        found.append(
            Reading(
                oracle_card=oracle,
                card=card,
                gaps=gaps,
                is_commander=True,
                checks=annotations.checks_of(oracle.pk),
                primary=primary_role(oracle, card.categories, annotations.for_card(oracle.pk)),
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
    ).values_list("oracle_card_id", "patches") if builtin else ()
    profiles = {card.pk: getattr(card, "profile", None) for card in cards}
    for oracle_id, document in rows:
        said, _notes = patches.effective(document, profiles.get(oracle_id))
        merged.setdefault(oracle_id, {}).update(said)
    # Said as built in: a built-in roles list is the engine's alone, and the
    # card counts in its derived categories (`_categories`).
    annotations = Annotations(overrides=merged, scopes={
        oracle_id: dict.fromkeys(said, BUILTIN_SCOPE) for oracle_id, said in merged.items()})

    found = {}
    for oracle_card in cards:
        gaps: list[Gap] = []
        card = _card_from(oracle_card, annotations, gaps)
        found[oracle_card.pk] = (card, gaps)
    return found


def card_with(oracle_card, overrides: dict,
              scopes: dict | None = None) -> tuple[Card, list[Gap]]:
    """One card with these overrides and no others, outside any deck.

    What a stored correction makes of the card - for comparing it with what
    later code makes of the same correction (`simulations.parity`). `scopes`
    says which scope set each key, where that matters (a built-in ``tags``
    list counts for the engine only); without it, nobody's in particular.
    """
    gaps: list[Gap] = []
    annotations = Annotations(overrides={oracle_card.pk: overrides},
                              scopes={oracle_card.pk: scopes or {}})
    return _card_from(oracle_card, annotations, gaps), gaps


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
    identity = frozenset(
        color for commander in deck.commanders
        for color in (getattr(commander, "color_identity", None) or [])
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


def deck_creature_type(entries, deck: Deck) -> tuple[str, int]:
    """The creature type Cavern of Souls names in this deck, and how many cards have it.

    "As this land enters, choose a creature type" is the player's choice;
    the engine's answer is the type the most cards in the deck share,
    commander included, and the commander's own type on a tie (P19 R17).
    An assumption, stated beside every card that makes it on the deck page,
    and one an annotation replaces. ``("", 0)`` for a deck without a
    creature type.
    """
    counts: Counter = Counter()
    for entry in entries:
        for found in creature_types(entry.oracle_card):
            counts[found] += entry.quantity
    own = frozenset().union(*(creature_types(each) for each in deck.commanders))
    for found in own:
        counts[found] += 1
    if not counts:
        return "", 0
    most = max(counts.values())
    tied = sorted(found for found, count in counts.items() if count == most)
    return next((found for found in tied if found in own), tied[0]), most


def deck_color(entries, deck: Deck, deck_colors: frozenset[str]) -> tuple[str, int]:
    """The colour Heraldic Banner chooses in this deck, and how many symbols ask for it.

    "As this artifact enters, choose a color" is the player's choice; the
    engine's answer is the colour the most coloured mana symbols in the
    deck's costs ask for, commander included, among the deck's colours, and
    the first of the commander's colours on a tie (P19 R18). An assumption,
    stated beside every card that makes it on the deck page, and one an
    annotation replaces. ``("", 0)`` for a deck without a coloured cost.
    """
    counts: Counter = Counter()
    for entry in entries:
        for found, amount in _cost_colors(entry.oracle_card).items():
            counts[found] += amount * entry.quantity
    own = deck.commander if deck.commander_id else None
    for each in deck.commanders:
        counts.update(_cost_colors(each))
    usable = {found: count for found, count in counts.items()
              if count and (not deck_colors or found in deck_colors)}
    if not usable:
        return "", 0
    most = max(usable.values())
    first = [found for found in COLORS if found in set(getattr(own, "colors", None) or ())]
    tied = [found for found in COLORS if usable.get(found) == most]
    return next((found for found in first if found in tied), tied[0]), most


def _cost_colors(oracle_card) -> Counter:
    """The coloured mana symbols of a card's printed cost, by colour.

    A hybrid symbol counts toward each colour it offers, a Phyrexian one
    toward its own.
    """
    cost = parse(oracle_card.mana_cost or "")
    found = Counter(dict(cost.pips))
    for symbol in cost.hybrid:
        found.update(color for color in symbol.colors if color in COLORS)
    found.update(color for color in cost.phyrexian if color in COLORS)
    return found


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
class Check:
    """A correction to look at again: its block changed or is gone since it
    was written (`simulations.patches.Note`), and whose correction it is."""

    key: str
    block: str
    #: ``changed`` (played on a reading that moved) or ``gone`` (not played).
    status: str
    #: `DECK_SCOPE`, `USER_SCOPE` or `BUILTIN_SCOPE`.
    scope: str

    @property
    def is_yours(self) -> bool:
        return self.scope in {DECK_SCOPE, USER_SCOPE}


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
    #: Phase 2: by oracle id, every patch whose block changed or is gone
    #: since it was written (`Check`) - "please check".
    notes: dict = field(default_factory=dict)

    def for_card(self, oracle_id) -> dict:
        """The winning overrides for one card."""
        return self.overrides.get(oracle_id, {})

    def checks_of(self, oracle_id) -> list:
        """The owner's own corrections to check on one card; the built-in
        ones are not a question to them."""
        return [check for check in self.notes.get(oracle_id, ()) if check.is_yours]

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
    for each in deck.commanders:
        relevant |= Q(oracle_card_id=each.pk)
    return relevant


def annotations_for(deck: Deck) -> Annotations:
    """All annotations that apply, already merged narrowest-wins.

    One query. The ordering is what does the merging: built-in rows first, then
    the owner's, then this deck's, each overwriting the keys before it.
    """
    from simulations.models import CardAnnotation

    relevant = cards_filter(deck)
    rows = list(
        CardAnnotation.objects.filter(relevant)
        .filter(scope_filter(deck))
        .order_by("owner_id", "deck_id")
        .values_list("oracle_card_id", "owner_id", "deck_id", "patches")
        .distinct()
    )
    profiles = _profiles_of(oracle_id for oracle_id, *_rest, document in rows
                            if patches.is_current(document))

    merged: dict = {}
    scopes: dict = {}
    notes: dict = {}
    # Built-in rows first, then the owner's, then this deck's. Each pass
    # overwrites only the keys it sets, so a deck-scoped annotation can change
    # one field without restating the rest.
    ordered = sorted(rows, key=lambda row: (row[1] is not None, row[2] is not None))
    for oracle_id, owner_id, deck_id, document in ordered:
        scope = _scope_name(owner_id, deck_id)
        said, found = patches.effective(document, profiles.get(oracle_id))
        merged.setdefault(oracle_id, {}).update(said)
        scopes.setdefault(oracle_id, {}).update(dict.fromkeys(said, scope))
        _keep_notes(notes, oracle_id, said, [
            Check(note.key, note.block, note.status, scope) for note in found])
    return Annotations(overrides=merged, scopes=scopes, notes=notes)


def _profiles_of(oracle_ids) -> dict:
    """The derived profiles of these cards, by oracle id - what a patch is
    played against."""
    from cards.models import DerivedProfile

    wanted = set(oracle_ids)
    if not wanted:
        return {}
    return {profile.oracle_card_id: profile
            for profile in DerivedProfile.objects.filter(oracle_card_id__in=wanted)}


def _keep_notes(notes: dict, oracle_id, said: dict, found: list) -> None:
    """A narrower row that says a key replaces the notes of a wider one about it."""
    kept = [note for note in notes.get(oracle_id, ()) if note.key not in said]
    kept.extend(found)
    if kept:
        notes[oracle_id] = kept
    else:
        notes.pop(oracle_id, None)


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


def check_overrides(oracle_card, overrides: dict) -> str:
    """Build the card with these overrides once, as a run would, and describe
    it (the editor rebuild). A primitive the engine cannot play raises here,
    at the form, rather than inside a worker.

    Returns:
        str: What the card searches, triggers and taps for, in words.

    Raises:
        ValueError: With the reason, for the form to show.
    """
    annotations = Annotations(overrides={oracle_card.pk: overrides}, scopes={})
    try:
        card = _card_from(oracle_card, annotations, [], frozenset("WUBRG"))
        reading = Reading(oracle_card=oracle_card, card=card)
        return " / ".join((reading.searches, reading.triggers, reading.taps_for))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"the engine cannot play this: {exc}") from exc


def _card_from(oracle_card, annotations: Annotations, gaps: list[Gap],
               deck_colors: frozenset[str] = frozenset(),
               deck_type: tuple[str, int] | None = None,
               deck_chosen_color: tuple[str, int] | None = None) -> Card:
    """One database card, as the engine sees it.

    ``deck_type`` is :func:`deck_creature_type`; None outside a deck, where
    no type is chosen and mana for "the chosen type" pays for nothing.
    ``deck_chosen_color`` is :func:`deck_color` the same way: outside a deck
    "the chosen color" is a choice of the card's colours.
    """
    overrides = annotations.for_card(oracle_card.pk)
    profile = effective(getattr(oracle_card, "profile", None), overrides)
    name = oracle_card.front_name

    if profile is None:
        gaps.append(Gap(name, "profile", "no derived profile; run cards.profiles.rebuild()"))
        return Card(name=name, mv=int(oracle_card.cmc or 0), pips=0,
                    generic=int(oracle_card.cmc or 0), kind="artifact",
                    types=card_types(oracle_card))

    kind = overrides.get("kind", profile.kind)
    chosen = _chosen_type(profile, overrides, deck_type)
    color = _chosen_color(profile, overrides, deck_chosen_color)
    mana_abilities = _mana_abilities(profile, overrides, kind, name, gaps, deck_colors, chosen,
                                     color)

    categories = _categories(profile, overrides)
    card = Card(
        name=name,
        mv=profile.mv,
        pips=int(overrides.get("pips", profile.black_pips)),
        generic=int(overrides.get("generic", profile.generic)),
        kind=kind,
        # Read off the categories, corrections included (card model PR 17).
        engine_roles=engine_roles(categories, profile),
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
        x_max=int((getattr(profile, "land_search", None) or {}).get("x_max", 0)),
        draws_x=(kind in ONE_SHOT_KINDS and "draw_on_cast" not in overrides
                 and bool(getattr(profile, "draws_x", False))),
        creature_types=creature_types(oracle_card),
        legendary="Legendary" in (oracle_card.type_line or "").split("//", 1)[0],
        colors=frozenset(oracle_card.colors or ()) & frozenset(COLORS),
        defender="Defender" in (oracle_card.keywords or []),
        enchants=("creature" if (getattr(profile, "combat", None) or {}).get("aura")
                  else _read_rule(profile, "enchants")),
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
        categories=categories,
        land_search=_land_search(profile),
        basic=oracle_card.type_line.startswith("Basic"),
        tapped_unless=_tapped_unless(profile, overrides),
        treasures=int(getattr(profile, "treasures", 0) or 0),
        treasure_mana=_any_colour(deck_colors) if getattr(profile, "treasures", 0) else "",
        landers=int(getattr(profile, "landers", 0) or 0),
        discard_cost=int(getattr(profile, "discard_cost", 0) or 0),
        additional_costs=_additional_costs(profile),
        exiled_on_cast=bool(getattr(profile, "mana_from_hand", False)),
        **_sacrifice_mana(profile, deck_colors),
        triggers=_triggers(profile, overrides, deck_colors),
        printed_subtypes=printed_subtypes(oracle_card),
        power=_power(oracle_card),
        toughness=_power(oracle_card, "toughness"),
        growth=_growth(profile),
        flip=0 if _overrides_mana(overrides) else int(_read_rule(profile, "flip") or 0),
        **_combat(oracle_card, profile, deck_colors),
        tap_mana=_tap_mana(profile, deck_colors, chosen, color),
        mox=str(getattr(profile, "mox", "") or ""),
        mana_mills=int(getattr(profile, "mana_mills", 0) or 0),
        store=_store(profile, deck_colors),
    )

    _record_gaps(card, profile, overrides, gaps, oracle_card.oracle_text or "")
    if chosen is not None and "chosen_type" not in overrides and deck_type is not None \
            and not _overrides_mana(overrides):
        _record_chosen_type(name, deck_type, gaps)
    if color is not None and "chosen_color" not in overrides \
            and deck_chosen_color is not None and not _overrides_mana(overrides):
        _record_chosen_color(name, deck_chosen_color, gaps)
    return card


#: P19 R19: what a card that taps itself and one more permanent assumes, by
#: what it taps - whole sentences, so each language words them its own way.
_TAPS_ONE = {
    "creature": gettext_noop(
        "taps a creature of yours that makes no mana and is not used otherwise this turn, "
        "before combat: that creature does not attack that turn"),
    "legendary creature": gettext_noop(
        "taps a legendary creature of yours that makes no mana and is not used otherwise "
        "this turn, before combat: that creature does not attack that turn"),
    "permanent": gettext_noop(
        "taps a permanent of yours that makes no mana and is not used otherwise this turn "
        "- never a land"),
    "artifact or creature": gettext_noop(
        "taps an artifact or creature of yours that makes no mana and is not used "
        "otherwise this turn, before combat: a creature tapped so does not attack that turn"),
}
#: ... and what a card that taps every permanent that fits assumes.
_TAPS_EACH = {
    "legendary creature": gettext_noop(
        "taps each legendary creature of yours that makes no mana, your commander "
        "included, for one mana each, before combat: those creatures do not attack that "
        "turn"),
    "artifact": gettext_noop(
        "taps each artifact of yours that makes no mana, for its mana; a Treasure is "
        "sacrificed for its own mana instead"),
    "creature": gettext_noop(
        "taps each creature of yours of the kind it names that makes no mana, itself "
        "included when it is one, before combat: those creatures do not attack that turn"),
    "permanent": gettext_noop(
        "taps each permanent of yours that makes no mana, for its mana - never a land"),
}


def _counts_ritual(card: Card) -> bool:
    """A ritual whose mana is counted (Cabal Ritual's threshold, Priest of
    Titania's Elves), read off its block."""
    return any(ability.form == "ritual" and ritual_counts_of(ability.effects[0].arg("amount"))
               for ability in card.abilities)


def _record_tapping(card: Card, gaps: list[Gap]) -> None:
    """P19 R19: what the engine assumes about tapping other permanents, the
    Moxen and a mill cost - stated on the card list, beside the card."""
    tapping = next((ability.only_if for ability in card.mana.entries
                    if ability.only_if is not None and ability.only_if.kind == "tap_fodder"),
                   None)
    if tapping is not None:
        kind = ("permanent" if "permanent" in tapping.types
                else "artifact or creature" if "artifact" in tapping.types
                else "legendary creature" if tapping.legendary else "creature")
        gaps.append(Gap(card.name, "assumed_cost", _TAPS_ONE[kind]))
    tap = card.mana.tap_other
    if tap is not None:
        types, wanted = wanted_of(tap.selector)
        kind = ("permanent" if "permanent" in types else "artifact" if "artifact" in types
                else "legendary creature" if wanted == "legendary"
                else "creature")
        gaps.append(Gap(card.name, "assumed_cost", _TAPS_EACH[kind]))
        if tap.tokens:
            gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
                "taps the artifact token it makes as it enters too")))
    if card.mana.mox == DISCARD_LAND:
        gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
            "is cast only with a land card left in hand after the turn's land drop, and "
            "discards it - one that would enter tapped first")))
    elif card.mana.mox == IMPRINT:
        gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
            "is cast only with a coloured nonartifact, nonland card in hand; it exiles the "
            "most expensive one, the card a discard would take, and makes its colours")))
    if card.mana.mills:
        gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
            "is tapped for mana every turn, and mills a card each time")))


def _store(profile, deck_colors: frozenset[str]) -> Store | None:
    """Mana kept as counters, energy or a Food, as the engine plays it
    (P19 R20): "any color" the deck's; "X mana of any one color" one colour
    the hand asks for, which the game settles."""
    found = getattr(profile, "mana_store", None)
    if not found or not found.get("amount"):
        return None
    produces = found.get("produces")
    if produces is None:
        mana = "" if found["use"] == STORE_SCALES else _any_colour(deck_colors)
    elif len(produces) == 1:
        mana = next(iter(produces))
    else:
        return None
    return Store(use=found["use"], mana=mana, amount=int(found["amount"]),
                 per_use=int(found.get("per_use") or 1), energy=found.get("kind") == "energy",
                 start=int(found.get("start") or 0), start_from=found.get("start_from", ""),
                 grows=found.get("grows", ""), empty=found.get("empty", ""))


#: P19 R20: how a store spends its counters, as said beside the card.
_STORE_USE = {
    STORE_FREE: gettext_noop(
        "keeps its counters as mana and spends them only when a spell needs them, as a "
        "Treasure is spent"),
    STORE_TAP: gettext_noop(
        "spends a counter each turn it is tapped for this mana, whether or not that colour "
        "is needed; with none left it makes only its other mana, if it has any"),
}
_STORE_START = {
    "x": gettext_noop(
        "enters with X counters, X being all the mana left when it is cast, last in the "
        "main phase"),
    "kicks": gettext_noop(
        "is kicked as often as the mana left pays for, at least once - cast last in the "
        "main phase, as a spell with X is"),
    "colors": gettext_noop(
        "enters with a counter for each colour the open mana can make when it is cast, at "
        "most one for each mana it costs"),
}
_STORE_GROWS = {
    "tap": gettext_noop(
        "taps each turn from the turn after it enters to put one more counter on itself"),
    "use": gettext_noop(
        "puts a counter on itself each time it is tapped, so it makes one more mana each "
        "turn"),
    "red_spell": gettext_noop(
        "gets a counter for each red spell you cast and turns each three into {R}{R}{R} at "
        "once, kept as a Treasure is until a spell needs it"),
}
_STORE_EMPTY = {
    "sacrifice": gettext_noop(
        "is sacrificed as your next turn begins once its last counter is gone"),
    "dies": gettext_noop(
        "makes its mana once for each point of toughness, then dies as your next turn begins"),
    "hand": gettext_noop(
        "returns to your hand as your next turn begins once its last counter is gone, and "
        "may be cast again"),
}


def _record_store(card: Card, found: dict, gaps: list[Gap]) -> None:
    """P19 R20: what the engine assumes about mana kept as counters, energy
    or a Food - stated on the card list, beside the card."""
    store = card.mana.store
    if store is None:
        return
    said = []
    if store.energy:
        said.append(gettext_noop(
            "spends one energy each turn it is tapped for any colour, whether or not that "
            "colour is needed; energy comes only from cards that give it as they enter, and "
            "without it the card makes only its other mana, if it has any"))
    elif found.get("kind") == "food":
        said.append(gettext_noop(
            "sacrifices the Food it makes as it enters for one mana, once; it never pays "
            "{1}{G} for another Food, which costs more than it gives"))
    elif store.use in _STORE_USE and store.grows != "red_spell" and store.empty != "dies":
        said.append(_STORE_USE[store.use])
    if found.get("once"):
        said.append(gettext_noop(
            "is used once each turn from the turn after it enters, as if it tapped"))
    if found.get("target"):
        said.append(gettext_noop(
            "puts the counters it enters with on itself - on another creature it would make "
            "no mana"))
    if store.start_from in _STORE_START:
        said.append(_STORE_START[store.start_from])
    if store.grows in _STORE_GROWS:
        said.append(_STORE_GROWS[store.grows])
    if store.empty in _STORE_EMPTY:
        said.append(_STORE_EMPTY[store.empty])
    if store.use == STORE_SCALES and not store.mana:
        said.append(gettext_noop(
            "makes all its mana in one colour: the one the cards in hand ask for most"))
    gaps.extend(Gap(card.name, "assumed_cost", text) for text in said)


def _power(oracle_card, stat: str = "power") -> int:
    """The power (or toughness, P19 R21) printed on the front face, 0 for none
    or "*" (P19 R18)."""
    printed = str(getattr(oracle_card, stat, "") or "").split("//", 1)[0].strip()
    return int(printed) if printed.isdigit() else 0


def _growth(profile) -> Growth | None:
    """+1/+1 counters and what grows with them, as the engine plays them (P19 R21)."""
    found = getattr(profile, "growth", None)
    if not found:
        return None
    adapt = found.get("adapt") or {}
    return Growth(enters=int(found.get("enters") or 0), enters_x=bool(found.get("enters_x")),
                  grows=tuple(found.get("grows") or ()), adapt=int(adapt.get("n") or 0),
                  adapt_cost=parse(adapt["cost"]) if adapt else None,
                  bonus=int(found.get("bonus") or 0), puts=int(found.get("puts") or 0),
                  draws_on_greatest=bool(found.get("draws_on_greatest")),
                  look=int(found.get("look") or 0))


#: P19 R21: what the engine assumes about mana by power and +1/+1 counters,
#: as said beside the card.
_STAT_SAID = {
    "power": gettext_noop(
        "counts its power as the turn's mana is opened: the power printed on it and its "
        "+1/+1 counters, no pumps or anthems; counters it gets later in the turn count from "
        "the next turn"),
    "power:greatest": gettext_noop(
        "counts the greatest power among your creatures as the turn's mana is opened: "
        "printed power and +1/+1 counters, no pumps or anthems"),
    "toughness:greatest_other": gettext_noop(
        "counts the greatest toughness among your other creatures as the turn's mana is "
        "opened: printed toughness and +1/+1 counters, no pumps or anthems"),
    "counters": gettext_noop(
        "counts its +1/+1 counters as the turn's mana is opened; counters it gets later in "
        "the turn count from the next turn"),
}
_GROWS_SAID = {
    "cast:noncreature": gettext_noop(
        "gets a +1/+1 counter for each noncreature spell you cast; the damage to the "
        "opponents is not counted"),
    "evolve": gettext_noop(
        "evolves when a creature enters with greater printed power or toughness plus "
        "counters than it has"),
    "increment": gettext_noop(
        "gets a counter from Increment when a spell's mana value is greater than its power "
        "or toughness; X is not counted"),
}


def _record_growth(card: Card, rule: dict, gaps: list[Gap]) -> None:
    """P19 R21: what the engine assumes about mana by power, toughness and
    +1/+1 counters - stated on the card list, beside the card."""
    said = []
    counted = rule.get("rule") == "counts"
    if counted and rule.get("subtype") in _STAT_SAID:
        said.append(_STAT_SAID[rule["subtype"]])
    if counted and rule.get("once"):
        said.append(gettext_noop(
            "is used once each turn from the turn after it enters, as if it tapped"))
    if counted and rule.get("pays"):
        said.append(gettext_noop(
            "pays the coloured mana its ability costs with any mana, as generic"))
    if rule.get("rule") == "grant" and rule.get("subtype") == "creature:counter":
        said.append(gettext_noop(
            "gives its {T}: Add {G} only to creatures with a +1/+1 counter; other kinds of "
            "counters are not counted"))
    if rule.get("flip"):
        said.append(gettext_noop(
            "turns into its land at your end step once you control enough creatures, and makes "
            "mana from your next turn on; it never counts as a land drop"))
    growth = card.turn.growth
    if growth is not None:
        if growth.puts:
            said.append(gettext_noop(
                "puts its counters as it enters on the creatures they make the most mana "
                "with - Incubation Druid first, then creatures without a mana ability - and "
                "on creatures that were there before this turn rather than new ones"))
        if growth.adapt:
            said.append(gettext_noop(
                "adapts as soon as the mana left after the turn's spells and land searches "
                "pays for it, before a spell with X takes the rest"))
        if growth.bonus:
            said.append(gettext_noop(
                "adds one more counter each time the engine puts +1/+1 counters on one of "
                "your creatures"))
        if growth.draws_on_greatest:
            said.append(gettext_noop(
                "draws a card when one of your creatures enters with more power than each "
                "other creature; a goldfish's opponents have no creatures"))
        if growth.look:
            said.append(gettext_noop(
                "takes the creature of highest mana value among the cards it looks at, and "
                "puts the rest on the bottom"))
        said.extend(_GROWS_SAID[grows] for grows in growth.grows if grows in _GROWS_SAID)
    gaps.extend(Gap(card.name, "assumed_cost", text) for text in said)


def _combat(oracle_card, profile, deck_colors: frozenset[str]) -> dict:
    """What the card does in combat, as `Card` fields (P19 R22)."""
    found = getattr(profile, "combat", None) or {}
    triggers = tuple(
        CombatTrigger(event=read["event"], who=read["who"],
                      types=frozenset(read.get("types") or ()),
                      treasures=int(read.get("treasures") or 0),
                      treasure_mana=_any_colour(deck_colors) if read.get("treasures") else "",
                      mana=str(read.get("mana") or ""), search=bool(read.get("search")),
                      untapped=bool(read.get("untapped")))
        for read in found.get("triggers") or ())
    return {
        "haste": "Haste" in (oracle_card.keywords or []),
        "combat": triggers,
        "equip": parse(found["equip"]) if found.get("equip") else None,
        "germ": bool(found.get("germ")),
        "treasure_bonus": int(found.get("treasure_bonus") or 0),
    }


#: P19 R22: what the engine assumes about combat, as said beside the card.
#: The goldfish has no blockers: whole sentences, so each language words them
#: its own way.
_COMBAT_SAID = {
    "attacks": gettext_noop(
        "attacks each turn it can - from the turn after it arrives, at once with haste - and "
        "is assumed to survive combat; what it makes as it attacks comes even if it is "
        "blocked"),
    "damage": gettext_noop(
        "attacks each turn it can and is assumed to deal combat damage to a player: a "
        "goldfish has no blockers, so against opponents with many blockers this is "
        "optimistic"),
    "host_attacks": gettext_noop(
        "the creature it is on attacks each turn it can and is assumed to survive combat; "
        "what it makes as that creature attacks comes even if it is blocked"),
    "host_damage": gettext_noop(
        "the creature it is on attacks each turn it can and is assumed to deal combat damage "
        "to a player: a goldfish has no blockers, so against opponents with many blockers "
        "this is optimistic"),
    "any": gettext_noop(
        "makes its Treasure once each turn one of your creatures can attack, assumed to deal "
        "combat damage to a player: a goldfish has no blockers, so against opponents with "
        "many blockers this is optimistic"),
    "modified": gettext_noop(
        "finds a basic land for each creature with an Equipment, an Aura or a +1/+1 counter "
        "that attacks, each assumed to deal combat damage to a player: a goldfish has no "
        "blockers, so against opponents with many blockers this is optimistic"),
    "commander": gettext_noop(
        "makes its Treasure each turn your commander can attack, assuming it attacks the "
        "opponent with the most life"),
    "curse": gettext_noop(
        "makes a Gold token each turn one of your creatures attacks, assuming you attack the "
        "enchanted player; attacks by your opponents are not counted"),
    "total": gettext_noop(
        "counts the combat damage of every creature that can attack, each assumed to deal it "
        "to a player: a goldfish has no blockers, so against opponents with many blockers "
        "this is optimistic; life lost any other way is not counted"),
}


#: P19 R23: Sword of Hearth and Home.
_FLICKER_SAID = gettext_noop(
    "the creature it may exile and return with the land is not used")


#: P19 R23: what a search assumes, by the shape read - whole sentences.
_SEARCH_SAID = {
    "tempting": ("assumed_lands", gettext_noop(
        "assumes one opponent takes the tempting offer, so you find two lands")),
    "reveal": ("assumed_lands", gettext_noop(
        "assumes four players, so X is at most 4")),
    "land_tapped": ("assumed_trigger", gettext_noop(
        "looks for a land, put onto the battlefield tapped, while your hand holds no land, "
        "and for a creature otherwise; its other modes are not used")),
    "gifts": ("assumed_trigger", gettext_noop(
        "assumes the opponent puts the two best of the four cards into your graveyard; the "
        "other two go to your hand")),
    "upkeep": ("assumed_trigger", gettext_noop(
        "assumes an opponent controls three creatures from your fourth turn on; then it is "
        "sacrificed and puts your two best creatures onto the battlefield")),
    "exiled": ("assumed_trigger", gettext_noop(
        "the card it exiles counts as a card in your hand, since you may play it from exile")),
    "symmetry": ("assumed_trigger", gettext_noop(
        "you are one of the two players and find a card for the top of your library; the "
        "opponent's card is not counted")),
    "opponents": ("assumed_trigger", gettext_noop(
        "searches only an opponent's library, which a goldfish does not have: it does "
        "nothing here")),
}


def _record_search(card: Card, found: dict, gaps: list[Gap]) -> None:
    """P19 R23: what the engine assumes about a search - stated on the card
    list, beside the card."""
    keys = [key for key in ("land_tapped", "gifts", "upkeep", "exiled", "symmetry",
                            "opponents") if found.get(key)]
    search = card.play.search
    if search is not None and search.tempting:
        keys.append("tempting")
    if search is not None and search.reveal:
        keys.append("reveal")
    for key in keys:
        field_name, text = _SEARCH_SAID[key]
        gaps.append(Gap(card.name, field_name, text))


def _record_combat(card: Card, found: dict, gaps: list[Gap]) -> None:
    """P19 R22: what the engine assumes about combat - stated on the card
    list, beside the card."""
    said = []
    turn = card.turn
    for trigger in turn.combat:
        if trigger.who in ("equipped", "enchanted"):
            key = "host_" + trigger.event
        elif trigger.who in ("any", "modified", "commander", "curse", "total"):
            key = trigger.who
        else:
            key = trigger.event
        if _COMBAT_SAID[key] not in said:
            said.append(_COMBAT_SAID[key])
        if trigger.mana and trigger.who != "total":
            said.append(gettext_noop(
                "keeps the mana it adds as it attacks for your second main phase, where the "
                "engine casts what it pays for; a spell with X waits for it"))
        if any(read.get("flicker") for read in found.get("triggers") or ()) \
                and _FLICKER_SAID not in said:
            said.append(_FLICKER_SAID)
    if any(trigger.treasures for trigger in turn.combat):
        said.append(gettext_noop(
            "the Treasure made in combat can be spent in your second main phase"))
    if turn.equip is not None and turn.germ:
        said.append(gettext_noop(
            "its Germ token, a 0/0 the Equipment makes a 1/1, attacks from the turn after it "
            "enters; it is equipped again only if the Germ is gone"))
    elif turn.equip is not None and any(ability.rule == GRANT and ability.subtype == "equipped"
                                       for ability in card.mana.entries):
        said.append(gettext_noop(
            "is equipped, with mana left after the turn's spells, to the weakest creature "
            "without a mana ability or anything else on it; that creature taps for mana from "
            "the next turn and no longer attacks"))
    elif turn.equip is not None:
        said.append(gettext_noop(
            "is equipped, with mana left after the turn's spells, to a creature without a mana "
            "ability that can attack this turn - one with nothing else on it and no counters "
            "first; it stays there"))
    if card.enchants == "creature":
        said.append(gettext_noop(
            "goes on a creature without a mana ability that can attack this turn as it is "
            "cast - one with nothing else on it first; it needs a creature to be cast"))
    if turn.treasure_bonus:
        said.append(gettext_noop(
            "makes each of your Treasures add two mana while it is on the battlefield; the "
            "two may be of different colours"))
    if any(read.get("extra") for read in found.get("triggers") or ()):
        said.append(gettext_noop(
            "the rest of its trigger - a card exiled from an opponent's library - is not "
            "played"))
    if found.get("vehicle"):
        said.append(gettext_noop(
            "searches as it enters; the engine crews no Vehicle, so it never attacks"))
    gaps.extend(Gap(card.name, "assumed_trigger", text) for text in said)


def _names_color(profile) -> bool:
    """Its mana is "of the chosen color", or pays only for it (P19 R18)."""
    if profile is None:
        return False
    if getattr(profile, "mana_chosen_color", False):
        return True
    spend = getattr(profile, "mana_spend_only", None) or {}
    return any(wanted.get("chosen_color") for wanted in spend.get("spells", ()))


def _chosen_color(profile, overrides: dict,
                  deck_chosen_color: tuple[str, int] | None) -> str | None:
    """The colour this card chose as it entered (P19 R18).

    None when it chooses none; "" when it does but nothing says which -
    outside a deck, or in a deck without a coloured cost.
    """
    if not _names_color(profile):
        return None
    wanted = str(overrides.get("chosen_color") or "").strip().upper()
    if wanted in COLORS:
        return wanted
    return deck_chosen_color[0] if deck_chosen_color is not None else ""


def _record_chosen_color(name: str, deck_chosen_color: tuple[str, int],
                         gaps: list[Gap]) -> None:
    """The deck's colour, as the assumption it is (P19 R18)."""
    found, count = deck_chosen_color
    if not found:
        gaps.append(Gap(name, "assumed_color", gettext_noop(
            "chooses a colour, but no card in this deck has a coloured cost: it is played "
            "as a choice of its colours")))
        return
    template = gettext_noop("chooses %(color)s, the colour most mana symbols in this deck's "
                            "costs ask for (%(count)s)")
    params = {"color": "{" + found + "}", "count": count}
    gaps.append(Gap(name, "assumed_color", template % params, template, params))


def _chosen_type(profile, overrides: dict, deck_type: tuple[str, int] | None) -> str | None:
    """The creature type this card's restricted mana names (P19 R17).

    None when it names none; "" when it does but there is no type to name -
    outside a deck, or in a deck without creature types.
    """
    spend = getattr(profile, "mana_spend_only", None)
    rule = getattr(profile, "mana_rule", None) or {}
    counts = rule.get("rule") == "counts" and rule.get("subtype") == "chosen_type"
    if not counts and (not spend
                       or not any(wanted.get("chosen_type") for wanted in spend["spells"])):
        return None
    if overrides.get("chosen_type"):
        return str(overrides["chosen_type"]).strip().lower()
    return deck_type[0] if deck_type is not None else ""


def _record_chosen_type(name: str, deck_type: tuple[str, int], gaps: list[Gap]) -> None:
    """The deck's creature type, as the assumption it is (P19 R17)."""
    found, count = deck_type
    if not found:
        gaps.append(Gap(name, "assumed_type", gettext_noop(
            "names a creature type, but no card in this deck has one: only its other "
            "mana is played")))
        return
    template = gettext_noop("names %(type)s as its creature type, the most common one in "
                            "this deck (%(count)s cards)")
    params = {"type": found.title(), "count": count}
    gaps.append(Gap(name, "assumed_type", template % params, template, params))


def _mana_abilities(profile, overrides: dict, kind: str, name: str,
                    gaps: list[Gap], deck_colors: frozenset[str],
                    chosen: str | None = None,
                    color: str | None = None) -> tuple[ManaAbility, ...]:
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
        subtype = read_rule["subtype"]
        if subtype == "chosen_type":
            # Three Tree City (P19 R18): the deck's type, or only its {C}.
            if not chosen:
                return (_flat(getattr(profile, "mana_produces", None), profile.mana_colors,
                              profile.mana_amount, 0, deck_colors, None),)
            subtype = chosen
        rule = ManaAbility(SCALING_RULES[read_rule["rule"]], read_rule.get("produces") or (),
                           subtype=subtype,
                           activation_generic=int(read_rule.get("activation") or 0),
                           color=read_rule.get("color", ""),
                           times=int(read_rule.get("times")
                                     or read_rule.get("with_counter") or 1))
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
    spend = getattr(profile, "mana_spend_only", None)
    exact = _produced(profile, color)
    if spend and "mana_activation" not in overrides:
        return _restricted(profile, spend, condition, deck_colors, chosen, color)
    if condition and "mana_activation" not in overrides:
        only_if = _condition(condition["if"])
        main = _flat(exact, profile.mana_colors, profile.mana_amount,
                     activation, deck_colors, only_if)
        # Nimbus Maze (P19 R18): the further conditional abilities, in order.
        also = tuple(_flat(more["produces"], profile.mana_colors, more["amount"],
                           int(more.get("activation") or 0), deck_colors,
                           _condition(more["if"]))
                     for more in condition.get("also") or ())
        otherwise = condition.get("otherwise")
        if not otherwise:
            return (main, *also)
        return (main, *also, _flat(otherwise["produces"], profile.mana_colors,
                                   otherwise["amount"], int(otherwise.get("activation") or 0),
                                   deck_colors, None))

    return (_flat(exact, profile.mana_colors,
                  profile.mana_amount, activation, deck_colors, None),
            *_filter(profile, deck_colors))


def _produced(profile, color: str | None) -> dict | None:
    """The exact mana the profile names - or the chosen colour's (P19 R18)."""
    if color and getattr(profile, "mana_chosen_color", False) and profile.mana_amount:
        return {color: int(profile.mana_amount)}
    return getattr(profile, "mana_produces", None)


def _restricted(profile, spend: dict, condition: dict | None, deck_colors: frozenset[str],
                chosen: str | None, color: str | None = None) -> tuple[ManaAbility, ...]:
    """Mana only some spells may spend, then what the card makes instead (P19 R17).

    Cavern of Souls is two abilities: one mana of the deck's colours for a
    creature spell of the chosen type, and {C}. The pool keeps both open
    until the mana is spent. When no spell is left that may spend it - no
    creature type to name - only the plain ability is played.
    """
    otherwise = spend.get("otherwise")
    plain = () if not otherwise else (
        _flat(otherwise["produces"], profile.mana_colors, otherwise["amount"],
              int(otherwise.get("activation") or 0), deck_colors, None),)
    filters = spell_filters(spend["spells"], chosen, color)
    if not filters:
        return plain
    only_if = _condition(condition["if"]) if condition else None
    main = _flat(_produced(profile, color), profile.mana_colors,
                 profile.mana_amount, 0, deck_colors, only_if)
    return (replace(main, spend_only=filters), *plain)


def spell_filters(spells: list, chosen: str | None,
                  color: str | None = None) -> tuple[SpellFilter, ...]:
    """The reader's spell filters as the engine's; a "chosen type" one needs a
    type, a "chosen color" one a colour (P19 R18)."""
    found = []
    for wanted in spells:
        subtypes = set(wanted.get("subtypes", ()))
        if wanted.get("chosen_type"):
            if not chosen:
                continue
            subtypes.add(chosen)
        if wanted.get("chosen_color") and not color:
            continue
        found.append(SpellFilter(
            types=frozenset(wanted.get("types", ())), subtypes=frozenset(subtypes),
            legendary=bool(wanted.get("legendary")), colorless=bool(wanted.get("colorless")),
            multicolored=bool(wanted.get("multicolored")),
            noncreature=bool(wanted.get("noncreature")),
            only_color=color if wanted.get("chosen_color") else ""))
    return tuple(found)


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


class EffectiveProfile:
    """A derived profile with an annotation's primitives laid over it.

    The editor rebuild: every override key of :mod:`simulations.primitives`
    holds a primitive in the reader's own JSON shape, so the code that plays a
    reading plays an override too - nothing about a primitive is written twice.
    Every other attribute is the profile's.
    """

    def __init__(self, profile, overrides: dict):
        self._profile = profile
        self._fields: dict = {}
        for key in sorted(primitives.KEYS & set(overrides)):
            self._fields.update(primitives.profile_fields(key, overrides[key]))

    def __getattr__(self, name):
        fields = self.__dict__.get("_fields", {})
        if name in fields:
            return fields[name]
        return getattr(self.__dict__["_profile"], name)


def effective(profile, overrides: dict):
    """The profile the engine plays: the reading, or what an annotation says
    instead of a primitive of it."""
    if profile is None or not primitives.KEYS & set(overrides):
        return profile
    return EffectiveProfile(profile, overrides)


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
    """How many {X} the cost has (P19 R9): three on Astral Cornucopia.

    P19 R20: Everflowing Chalice's multikicker is an X too - {2} for each
    time, and X its counters.
    """
    store = getattr(profile, "mana_store", None) or {}
    if store.get("start_from") == "kicks":
        return int(store.get("kick") or 0)
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
        also=frozenset(found.get("also", ())),
        power_at=int(found.get("power_at", 0)), power_count=int(found.get("power_count", 0)),
        by_power=bool(found.get("by_power")), repeat=int(found.get("repeat", 0)),
        tempting=int(found.get("tempting", 0)), by_x=bool(found.get("by_x")),
        reveal=bool(found.get("reveal")),
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
                                             sacrifice_filter=found.get("filter", ""),
                                             life=int(found.get("life", 0))),
            "sacrifice_mana_amount": int(found["amount"]), "sacrifice_mana_color": color,
            "sacrifice_mana_taps": bool(found.get("taps")),
            "sacrifice_mana_other": bool(found.get("other"))}


def _tap_mana(profile, deck_colors: frozenset[str], chosen: str | None,
              color: str | None) -> TapMana | None:
    """Mana for tapping other permanents, as the engine plays it (P19 R19):
    "any color" the deck's, and spells only some may spend as R17 reads them."""
    found = getattr(profile, "tap_mana", None)
    if not found or not found.get("amount"):
        return None
    produces = found.get("produces")
    if produces is None:
        mana = _any_colour(deck_colors)
    elif len(produces) == 1:
        mana = next(iter(produces))
    else:
        return None
    spend_only = ()
    if found.get("spend_only"):
        spend_only = spell_filters(found["spend_only"], chosen, color)
        if not spend_only:
            return None
    return TapMana(types=frozenset(found["types"]), filter=found.get("filter", ""),
                   mana=mana, amount=int(found["amount"]), spend_only=spend_only,
                   tokens=int(found.get("token", 0)))


def _triggers(profile, overrides: dict, deck_colors: frozenset[str]) -> tuple[Trigger, ...]:
    """The triggers the reader read (P19 R16), as the engine plays them.

    A Treasure's "one mana of any color" is the deck's colours; an Eldrazi
    Spawn is a Treasure that makes {C}. Since the editor rebuild "Taps for"
    no longer switches a trigger's mana off: the `triggers` override does
    (`primitives.keep_taps_for_scope` keeps the annotations written before).
    """
    found = []
    for read in getattr(profile, "triggers", None) or []:
        mana = int(read.get("mana", 0))
        treasures = int(read.get("treasures", 0))
        color = read.get("color", "")
        trigger = Trigger(
            event=read["event"],
            filter=read.get("filter", ""),
            treasures=treasures,
            treasure_mana=(COLORLESS if read.get("spawn") else _any_colour(deck_colors))
            if treasures else "",
            mana=mana,
            mana_color=(_any_colour(deck_colors) if color == "any" else color) if mana else "",
            draw=int(read.get("draw", 0)),
            life=int(read.get("life", 0)),
            counter=int(read.get("counter", 0)),
            connive=int(read.get("connive", 0)),
        )
        if trigger.treasures or trigger.mana or trigger.draw or trigger.connive:
            found.append(trigger)
    return tuple(found)


def _additional_costs(profile) -> tuple[AdditionalCost, ...]:
    """The ways the reader read the card's additional cost (P19 R15)."""
    return tuple(
        AdditionalCost(
            sacrifice=frozenset(way.get("sacrifice", ())), sacrifice_filter=way.get("filter", ""),
            life=int(way.get("life", 0)), life_x=bool(way.get("life_x")),
            discard=int(way.get("discard", 0)),
            mana=parse(way["mana"]) if way.get("mana") else None,
            exile_from_graveyard=way.get("exile_from_graveyard", ""),
            sacrifice_count=max(int(way.get("count") or 1), 1))
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
    elif profile.tutor_to == "hand" and limit is not None:
        # P19 R23: types or subtypes, a mana value limit, and the shapes of
        # Archdruid's Charm, Gifts Ungiven, Gamble and Demonic Counsel.
        battlefield = {"types": frozenset(limit.get("types") or ()),
                       "max_mv": limit.get("max_mv"),
                       "land_tapped": bool(limit.get("land_tapped")),
                       "gifts": int(limit.get("gifts") or 0),
                       "random_discard": int(limit.get("random_discard") or 0),
                       "delirium_any": bool(limit.get("delirium_any"))}
    elif limit is not None:
        max_mv = limit.get("max_mv")
        battlefield = {"to_battlefield": True, "color": limit.get("color") or "",
                       "max_mv_x": max_mv == "X",
                       "max_mv": max_mv if isinstance(max_mv, int) else None,
                       "max_mv_sacrificed": limit.get("plus_sacrificed"),
                       "upkeep": limit.get("upkeep") or ""}
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


def _categories(profile, overrides: dict) -> frozenset[str]:
    """The categories of the card (`Card.categories`): what the draw
    statistics sort it by, and what the engine's roles are read off
    (`engine_roles`).

    The card's derived categories (`derived_categories`) and every
    correction of them, relative to them, so a later reading still reaches
    the card (card model phase 3, PR 10). A main role is counted, unless a
    narrower scope took it away.
    """
    removed = frozenset(overrides.get("category_remove") or ())
    found = (derived_categories(profile)
             | frozenset(overrides.get("category_add") or ())) - removed
    primary = overrides.get("category_primary")
    return found | {primary} if primary and primary not in removed else found


#: The kinds that are spells: one cast, one resolution.
ONE_SHOT_KINDS = frozenset({SORCERY, INSTANT, RITUAL})
#: The categories version whose tempo says which abilities draw.
DRAWS_VERSION = 2


def engine_roles(categories: frozenset[str], profile) -> frozenset[str]:
    """Which of the engine's four roles (`simulation.cards.ENGINE_ROLES`) the
    card plays by, read off its categories (card model PR 17).

    A sacrifice outlet, a recursive creature and a drain are categories of
    their own. A draw engine is a card that draws (``draw``) with an ability
    that happens again and again (a rate in `cards.categories.REPEATS`) -
    the ability that draws (``draws``), where the blocks tell which one: a
    card that draws once on entering and taps for mana is no draw engine.
    Where none of them draws in so many words (digging, a mode, a
    correction), any of them but a mana ability: a land that cycles is
    none. A card without blocks (or with keywords only), or derived before
    abilities said whether they draw (categories version 1), has no tempo
    to tell: there a permanent that draws is one, and a spell only when the
    community says so. Until the profile is derived (``categories_version``), its
    categories are the community roles, which name all four themselves.
    """
    roles = categories & ENGINE_ROLES
    version = getattr(profile, "categories_version", 0)
    if not version:
        return roles
    tempo = getattr(profile, "tempo", None) or {}
    if tempo and version >= DRAWS_VERSION:
        drawing = [each for each in tempo.values() if each.get("draws")] or [
            each for key, each in tempo.items() if not key.startswith(f"{blocks.MANA}:")]
        repeats = any(each.get("rate") in card_categories.REPEATS for each in drawing)
    else:
        repeats = profile.kind not in ONE_SHOT_KINDS or DRAW_ENGINE in profile.role_tags
    return (roles - {DRAW_ENGINE}) | ({DRAW_ENGINE} if "draw" in categories and repeats
                                      else frozenset())


def roles_as_categories(overrides: dict, profile, wanted) -> tuple[dict, frozenset[str]]:
    """``overrides`` with the category corrections under which the engine
    plays the card in exactly the roles ``wanted`` (`engine_roles`): how a
    list of engine roles from before card model PR 17 is said now.

    A role is added or taken away by its category; a draw engine by
    ``draw``. Returns the overrides, and the roles the categories cannot
    give - a draw engine on a card no ability of which repeats, or any role
    of a profile not derived yet.
    """
    said = dict(overrides)
    added = list(said.get("category_add") or ())
    removed = list(said.get("category_remove") or ())
    have = engine_roles(_categories(profile, said), profile)
    for role in sorted(ENGINE_ROLES):
        key = "draw" if role == DRAW_ENGINE else role
        if role in wanted and role not in have:
            if key in removed:
                removed.remove(key)
            elif key not in added:
                added.append(key)
        elif role in have and role not in wanted:
            if key in added:
                added.remove(key)
            elif key not in removed:
                removed.append(key)
    order = card_categories.ROLES + card_categories.MECHANICS
    for name, value in (("category_add", added), ("category_remove", removed)):
        if value:
            said[name] = sorted(value, key=order.index)
        else:
            said.pop(name, None)
    return said, frozenset(wanted) ^ engine_roles(_categories(profile, said), profile)


def derived_categories(profile) -> frozenset[str]:
    """A card's roles and mechanics (card model phase 3, `cards.categories`).

    Until the profile is derived that way (`categories_version`), the
    community roles as before: `role_tags`.
    """
    if not getattr(profile, "categories_version", 0):
        return frozenset(profile.role_tags)
    roles = {role for role in (profile.primary_role, profile.secondary_role) if role}
    return frozenset(roles) | frozenset(profile.mechanics)


def primary_role(oracle_card, categories: frozenset[str], overrides: dict | None = None) -> str:
    """The card's main role among its `categories`.

    The owner's (``category_primary``, PR 10) first; then the derived one
    while the categories hold it; otherwise - the owner replaced the roles -
    the first of theirs in `cards.categories.ROLE_ORDER`. None before the
    profile is derived again, the owner's included: today's roles name no
    main one, and one card with a main role would make the deck page count
    every other card as having none.
    """
    profile = getattr(oracle_card, "profile", None)
    if not getattr(profile, "categories_version", 0):
        return ""
    said = (overrides or {}).get("category_primary")
    if said and said in categories:
        return said
    if profile.primary_role in categories:
        return profile.primary_role
    return next((role for role in card_categories.ROLE_ORDER if role in categories), "")


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


def printed_subtypes(oracle_card) -> frozenset[str]:
    """Every subtype on the front face, lower case (P19 R17): "aura",
    "equipment", "dragon" - what restricted mana asks a spell about."""
    front = (oracle_card.type_line or "").split("//", 1)[0]
    for separator in TYPE_SEPARATORS:
        if separator in front:
            return frozenset(word.casefold() for word in front.split(separator, 1)[1].split())
    return frozenset()


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

    mana, turn, play = card.mana, card.turn, card.play
    if profile.produces_mana and profile.mana_amount is None and not mana.entries \
            and not turn.treasures and not _counts_ritual(card) \
            and not (mana.altar is not None and mana.altar.sacrifices) \
            and not turn.triggers and mana.tap_other is None and mana.store is None \
            and not turn.combat:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("makes mana, but how much could not be read")))

    if play.search is not None and play.search.condition == "opponent_more_lands":
        gaps.append(Gap(card.name, "assumed_lands", gettext_noop(
            "searches when you have fewer lands than turns gone by, assuming each opponent "
            "plays a land a turn")))

    for read in getattr(profile, "triggers", None) or []:
        if read["event"] != "opponents":
            continue
        # P19 R16: the opponents' round, assumed - stated on the card list.
        template = (gettext_noop("makes a Treasure once a round, assuming one opponent a round "
                                 "does not pay %(tax)s") if read.get("treasures")
                    else gettext_noop("draws a card once a round, assuming one opponent a round "
                                      "does not pay %(tax)s"))
        params = {"tax": read.get("tax", "")}
        gaps.append(Gap(card.name, "assumed_trigger", template % params, template, params))

    _record_search(card, getattr(profile, "tutor_filter", None) or {}, gaps)
    _record_tapping(card, gaps)
    _record_store(card, getattr(profile, "mana_store", None) or {}, gaps)
    if not _overrides_mana(overrides):
        _record_growth(card, getattr(profile, "mana_rule", None) or {}, gaps)
    _record_combat(card, getattr(profile, "combat", None) or {}, gaps)

    if any(way.life_x for way in play.ways):
        gaps.append(Gap(card.name, "assumed_cost", gettext_noop(
            "pays X life with X = 0: in a goldfish there is nothing for X to hit")))

    if play.untapped_if is not None and play.untapped_if.kind == "opponent_lands":
        gaps.append(Gap(card.name, "assumed_lands", gettext_noop(
            "enters untapped from your fourth turn, assuming each opponent plays a land "
            "a turn")))

    if mana.entries and _OPPONENTS_LANDS.search(card_text) \
            and not _overrides_mana(overrides):
        gaps.append(Gap(card.name, "assumed_mana", gettext_noop(
            "makes your deck's colours, assuming your opponents' lands make them")))

    fetches = play.search is not None and play.search.when == "play"
    # A land with a basic land type taps for its colour (Dryad Arbor), and one
    # whose text adds no mana and lists none really makes none: Maze of Ith,
    # Dark Depths. Neither is something the engine failed to read (P19 R4).
    typed = land_color(card, frozenset()) is not None
    makes_none = not profile.produces_mana and not _COPIES.search(card_text)
    if card.is_land and not mana.entries and not fetches and not typed \
            and not makes_none:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("a land that taps for nothing the engine can see")))
