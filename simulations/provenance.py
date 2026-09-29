"""Where every number about a card came from.

The whole product rests on one claim: the simulation says how much of a deck it
could actually model, and does not pretend to more. Phase 3 made that claim at
the level of a deck - "57 of 183 cards described in full". This module makes it
at the level of a field, which is where somebody who does not believe a result
can actually check it.

Five sources, in descending confidence:

===========  ================================================================
`scryfall`   Structured data off the card. Mana cost, type line. Ground truth.
`tags`       The Scryfall Tagger community. Broad, and sometimes opinionated.
`regex`      A pattern over English prose. The weakest claim here.
`builtin`    A default this application ships, applying to every deck.
`you`        A judgement the user recorded - `user` scope or `deck` scope.
===========  ================================================================

Plus one that is not a source at all and has to be labelled as such: where
nobody said anything, the engine's own fallback rule applies. "Priority 38"
looks like a decision until it is shown as `engine`, at which point it reads
correctly as "nobody decided, so cheapest first".

Nothing here recomputes what the engine will do. The values come from
`adapter.readings`, which is `adapter._card_from` - the same function the
simulation uses - so a panel and a run cannot drift apart.
"""

from dataclasses import dataclass

from simulations import gaps as gaps_module
from simulations.annotations import BY_KEY, format_mana
from simulations.engine import adapter

#: Source keys, in descending confidence, with the words the panel shows.
SOURCES = {
    "scryfall": (
        "Scryfall field",
        "Structured data straight off the card. As close to ground truth as "
        "this application gets.",
    ),
    "tags": (
        "Community tagger",
        "Rolled up from the Scryfall Tagger project. Community-maintained, "
        "deliberately broad, and occasionally an opinion rather than a fact.",
    ),
    "regex": (
        "Read from the card text",
        "A pattern matched against English prose. The weakest reading this "
        "application makes, and the one most worth checking.",
    ),
    "builtin": (
        "Built in",
        "A default that ships with the application and applies to every deck.",
    ),
    "user": ("You, for all your decks", "A judgement you recorded."),
    "deck": ("You, for this deck", "A judgement you recorded for this deck only."),
    "engine": (
        "Nobody — the engine's own rule",
        "No one said, so the engine's fallback applies. Worth a look if the "
        "card matters to how the deck plays.",
    ),
}

@dataclass(frozen=True)
class FieldSpec:
    """One row of the panel, and how to find out who is responsible for it.

    `derived_keys` are tried against `DerivedProfile.source_map` in order, so
    that "taps for" can say *regex* when a pattern read the amount and
    *Scryfall field* when the card simply makes no mana.

    `fallback` is what to say when none of them speaks, and getting it right
    per field is the difference between honest and merely plausible. For
    `enters_tapped` the answer is `regex`: the pattern ran and said no, which
    is a weak claim either way. For `accelerant` it is `engine`: nobody has
    ever expressed an opinion and the engine assumed one.
    """

    attribute: str
    label: str
    derived_keys: tuple[str, ...] = ()
    fallback: str = "engine"
    #: Whether the row says anything about a land. Two of them do not: the
    #: engine never casts a land, so its cast priority and whether it could be
    #: cast against nobody are both answers to a question that never comes up.
    #: A row of those on every Swamp is noise, and noise is what teaches
    #: people to stop reading the panel that matters.
    applies_to_lands: bool = True
    #: The mirror of it: a row that only a land has an answer to. Land types
    #: are what a basic land taps for, so they are the difference between a
    #: Swamp and a blank card - and meaningless on a Dark Ritual.
    lands_only: bool = False
    #: A row only a mana source has an answer to. "Costs nothing to tap for
    #: mana" on every creature in the deck is the noise that teaches people to
    #: stop reading the panel.
    mana_only: bool = False


#: The rows, in the order the panel shows them. Every one has a label, so a
#: value cannot appear on screen with nothing saying what it is; a test walks
#: this table against the editable vocabulary in both directions.
FIELDS = (
    FieldSpec("cost", "Mana cost", ("pips", "generic"), "scryfall"),
    FieldSpec("kind", "Engine treats it as", ("kind",), "scryfall"),
    FieldSpec(
        "taps_for",
        "Taps for",
        # The amount comes from a regex when one could read it; the *colours*
        # are always a Scryfall field, which is what answers for a card that
        # makes no mana at all.
        ("mana_amount", "produces_mana", "mana_colors"),
        "scryfall",
    ),
    FieldSpec("activation", "Costs to tap for mana", ("mana_activation",), "regex",
              mana_only=True),
    FieldSpec("untaps", "Untaps every turn", ("mana_untaps",), "regex", mana_only=True),
    FieldSpec("subtypes", "Land types", ("is_basic_swamp",), "scryfall",
              lands_only=True),
    FieldSpec("enters_tapped", "Enters tapped", ("enters_tapped",), "regex"),
    FieldSpec("roles", "Roles", ("role_tags",), "scryfall"),
    # Two sources on one row, and the row says so: the zone is a community tag,
    # the number is a pattern over the printed text. `source_of` takes the
    # weakest of the two, which is the honest summary of a value that is only
    # as good as its worst half.
    FieldSpec("searches", "Searches your library for",
              ("tutor_count", "tutor_to"), "engine", applies_to_lands=False),
    FieldSpec("skips_draw_step", "Skips the draw step", ("skips_draw_step",),
              "regex", applies_to_lands=False),
    FieldSpec("effective_priority", "Cast priority", applies_to_lands=False),
    FieldSpec("accelerant", "Counts as acceleration"),
    FieldSpec("goldfish_castable", "Can be cast against nobody",
              applies_to_lands=False),
)

#: Which annotation key overrides each row, where one does. Read off the
#: editable vocabulary so the two cannot fall out of step.
ANNOTATION_KEY = {
    "cost": "pips",
    "kind": "kind",
    "taps_for": "mana_produces",
    "activation": "mana_activation",
    "untaps": "untaps",
    "subtypes": "subtypes",
    "enters_tapped": "enters_tapped",
    "roles": "tags",
    "searches": "tutor_count",
    "effective_priority": "priority",
    "accelerant": "accelerant",
    "goldfish_castable": "goldfish_castable",
}

#: Rows whose engine default is a fine answer rather than a weakness. The cast
#: priority is the one: nobody is asked for it since Phase 9 C (decision D6 -
#: the product is statistics about a deck, not steering a game), so flagging
#: "the engine's own rule" as worth checking would nag about a question the
#: interface no longer asks. The row still says who decided.
SETTLED_BY_DEFAULT = frozenset({"effective_priority"})


@dataclass(frozen=True)
class Row:
    """One field of one card, with its value and where it came from."""

    key: str
    label: str
    value: str
    source: str

    @property
    def source_label(self) -> str:
        return SOURCES[self.source][0]

    @property
    def source_detail(self) -> str:
        return SOURCES[self.source][1]

    @property
    def is_yours(self) -> bool:
        return self.source in {"user", "deck"}

    @property
    def is_weak(self) -> bool:
        """Worth a second look: a prose pattern, or nobody's decision at all."""
        return (self.source in {"regex", "engine"}
                and self.key not in SETTLED_BY_DEFAULT)

    @property
    def editable_as(self) -> str:
        """The label of the form field that changes this, if there is one."""
        key = ANNOTATION_KEY.get(self.key)
        judgement = BY_KEY.get(key)
        return judgement.label if judgement else ""


@dataclass
class CardProvenance:
    """One card: the engine's reading, field by field, with sources."""

    reading: adapter.Reading
    rows: list[Row]

    @property
    def oracle_card(self):
        return self.reading.oracle_card

    @property
    def name(self) -> str:
        return self.reading.oracle_card.front_name

    @property
    def gaps(self) -> list:
        return self.reading.gaps

    @property
    def unreadable(self) -> bool:
        """The engine could not read something off this card."""
        return self.reading.unreadable

    @property
    def reading_gaps(self) -> list:
        """The gaps the engine could not read. The only ones the pages show.

        The other kind - "nobody said how early to cast it" - is still recorded
        on every run, but it is no longer a question put to anybody (Phase 9 C).
        """
        return gaps_module.of_kind(self.gaps, gaps_module.READING)

    @property
    def yours(self) -> list[Row]:
        """The rows the user is responsible for."""
        return [row for row in self.rows if row.is_yours]

    @property
    def weak(self) -> list[Row]:
        """The rows nobody has confirmed and a regex or a default supplied."""
        return [row for row in self.rows if row.is_weak]


def for_deck(deck) -> list[CardProvenance]:
    """Every card in the deck, with a provenance row per field.

    One pass: the readings and the annotation scopes are each one query set,
    and a 200-card deck is one page rather than 200 of them.
    """
    scopes = adapter.annotations_for(deck).scopes
    return [
        CardProvenance(reading=reading, rows=_rows(reading, scopes))
        for reading in adapter.readings(deck)
    ]


def for_card(deck, oracle_card) -> CardProvenance | None:
    """One card of one deck, or `None` when it is not in that deck.

    `None` rather than a fabricated reading: the caller turns it into a 404,
    which is the right answer to a card the user cannot see.
    """
    for entry in for_deck(deck):
        if entry.oracle_card.pk == oracle_card.pk:
            return entry
    return None


def _rows(reading: adapter.Reading, scopes: dict) -> list[Row]:
    card_scopes = scopes.get(reading.oracle_card.pk, {})
    profile = getattr(reading.oracle_card, "profile", None)
    source_map = (profile.source_map or {}) if profile is not None else None

    is_land = reading.card.is_land
    return [
        Row(
            key=spec.attribute,
            label=spec.label,
            value=_display(_value_of(reading, spec.attribute)),
            source=_source_for(spec, card_scopes, source_map),
        )
        for spec in FIELDS
        if (spec.applies_to_lands or not is_land) and (is_land or not spec.lands_only)
        and (reading.card.mana_abilities or not spec.mana_only)
    ]


def _value_of(reading: adapter.Reading, attribute: str):
    """The `Reading`'s own wording where it has one, else the engine card's."""
    if hasattr(reading, attribute):
        return getattr(reading, attribute)
    return getattr(reading.card, attribute, None)


def _source_for(spec: FieldSpec, card_scopes: dict, source_map: dict | None) -> str:
    """Who gets the credit for one field.

    A user annotation wins, because it is the last word in the merge. Failing
    that, whichever layer of the deriver wrote it. Failing that the field's own
    fallback - which is never "somebody decided this".

    `source_map` of `None` means the card has no derived profile at all. The
    adapter already records that as a gap; claiming a Scryfall field for a card
    nothing has ever read would be the one dishonest answer available.
    """
    annotation_key = ANNOTATION_KEY.get(spec.attribute)
    if annotation_key and annotation_key in card_scopes:
        return card_scopes[annotation_key]
    if source_map is None:
        return "engine"
    for key in spec.derived_keys:
        if key in source_map:
            return source_map[key]
    return spec.fallback


def _display(value) -> str:
    """One field's value as a short piece of text.

    Booleans become yes/no rather than True/False, and an empty list becomes a
    word: a blank cell in a provenance table reads as a missing measurement
    when what it means is "none of them".
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return format_mana(value)
    if isinstance(value, (list, tuple, frozenset, set)):
        return ", ".join(str(item) for item in sorted(value)) or "none"
    if value is None:
        return "not set"
    return str(value)
