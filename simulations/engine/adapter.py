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

from collections import Counter
from dataclasses import dataclass, field

from django.utils.translation import gettext, gettext_noop, ngettext

from decks.models import Deck
from simulation import ENGINE_VERSION, agent
from simulation.cards import (
    CARD_TYPES,
    DOUBLE_SUBTYPE,
    FLAT,
    PER_CONTROLLED,
    TYPE_ADDING,
    Card,
    CostReduction,
    DeckDefinition,
    EndStepSpec,
    LandSearch,
    ManaAbility,
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

#: Scaling rules an annotation may name, mapped to the engine's constants.
SCALING_RULES = {
    "per_controlled": PER_CONTROLLED,
    "double_subtype": DOUBLE_SUBTYPE,
    "type_adding": TYPE_ADDING,
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
        return str(self.card.mana_cost)

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
        if spec.kind:
            from cards.models import DerivedProfile

            kinds = dict(DerivedProfile.Kind.choices)
            what = gettext("%(count)s × %(kind)s") % {
                "count": spec.count, "kind": kinds.get(spec.kind, spec.kind)}
        else:
            what = ngettext("%(count)s card", "%(count)s cards", spec.count) % {
                "count": spec.count}
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
    text = (gettext("%(what)s onto the battlefield, tapped") if search.tapped
            else gettext("%(what)s onto the battlefield")) % {"what": what}
    if search.hand:
        text = gettext("%(search)s, and %(count)s to hand") % {"search": text,
                                                              "count": search.hand}
    if search.life:
        text = gettext("%(search)s, paying %(life)s life") % {"search": text,
                                                             "life": search.life}
    return text


def _ability_text(ability) -> str:
    """One mana ability, in words."""
    if ability.rule == FLAT:
        produced = " + ".join(
            f"{amount} {mana_label(color)}" for color, amount in ability.produces
        ) or gettext("nothing")
        if ability.activation_generic:
            return _for_cost(ability.activation_generic, produced)
        return produced

    if ability.rule in RULE_TEXT:
        text = gettext(RULE_TEXT[ability.rule]) % {
            "color": ability.scaling_color, "subtype": ability.subtype or "land"}
    else:
        text = str(ability.rule)
    if ability.activation_generic:
        return _for_cost(ability.activation_generic, text)
    return text


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
        ritual_color=_ritual_color(profile, overrides, deck_colors),
        cost_reduction=_cost_reduction(profile, overrides),
        draw_on_cast=_draw_on_cast(profile, overrides, kind),
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
    )

    _record_gaps(card, profile, overrides, gaps)
    return card


def _mana_abilities(profile, overrides: dict, kind: str, name: str,
                    gaps: list[Gap], deck_colors: frozenset[str]) -> tuple[ManaAbility, ...]:
    """The card's mana abilities, from an annotation or from the profile.

    A scaling rule can only come from an annotation: Scryfall's `produced_mana`
    gives colour and nothing else, so nothing in the derived data can tell
    Cabal Coffers from a Swamp.
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

    # The text named every symbol - a Signet's {U}{B} is one of EACH, not a
    # choice between them, so there is no colour to pick and nothing to report.
    exact = getattr(profile, "mana_produces", None)
    if exact:
        return (ManaAbility(FLAT, exact, activation_generic=activation),)

    # `mana_colors` is Scryfall's `produced_mana`: it lists every colour the
    # card can make, without saying whether that is a choice or all of them at
    # once. Nearly always it is a choice - a dual land, a Talisman, Command
    # Tower - and since engine version 5 the pool holds one (P19 R1), settled
    # when the mana is spent. Only the deck's own colours are offered: Command
    # Tower and Arcane Signet name all five, and make only the commander's.
    # Several mana of a choice are each chosen on their own, which is right
    # for "any combination of colours" and generous for "any one colour".
    colors = [color for color in profile.mana_colors or [] if color in COLORS]
    amount = profile.mana_amount
    if not colors:
        return (ManaAbility(FLAT, {COLORLESS: amount}, activation_generic=activation),)
    offered = [color for color in colors if color in deck_colors] or colors
    return (ManaAbility(FLAT, {choice(offered): amount}, activation_generic=activation),)


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
    return parse(oracle_card.mana_cost or "")


def _ritual_color(profile, overrides: dict, deck_colors: frozenset[str]) -> str:
    """Which colour a ritual adds. Dark Ritual black, Rite of Flame red."""
    if overrides.get("ritual_color"):
        return str(overrides["ritual_color"]).upper()
    colors = [color for color in profile.mana_colors or [] if color in COLORS]
    return _pick_color(colors, deck_colors) or _pick_color(deck_colors, deck_colors) or "B"


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


#: The two zones a `TutorSpec` can search to. `battlefield` is deliberately
#: absent: the engine puts a found card in the hand or in the graveyard and has
#: no third move, so a tutor that fetches onto the battlefield is a gap rather
#: than a tutor quietly redirected somewhere it does not go.
TUTOR_ZONES = {"hand": True, "graveyard": False}


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
    )


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

    return TutorSpec(
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


def _record_gaps(card: Card, profile, overrides: dict, gaps: list[Gap]) -> None:
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

    if profile.produces_mana and profile.mana_amount is None and not card.mana_abilities:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("makes mana, but how much could not be read")))

    fetches = card.land_search is not None and card.land_search.when == "play"
    if card.is_land and not card.mana_abilities and not fetches:
        gaps.append(Gap(card.name, "mana_abilities",
                        gettext_noop("a land that taps for nothing the engine can see")))
