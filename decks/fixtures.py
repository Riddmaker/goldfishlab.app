"""The test decks, as data.

Everything the application does was proved for six phases against exactly one
deck - the mono-black Chainer reference deck. That deck is a fixed point and a
good one, but it is also mono-coloured, has a commander, runs a sane land count
and was hand-annotated card by card. A great many ways of being wrong cannot
show up against it, because it does not have the shape that would reveal them.

These are the missing shapes. Each one exists to catch a **specific class of
bug**, named in `catches`, and that field is not decoration: a deck nobody can
say what it would catch is a deck nobody will maintain.

**Why this module holds no Django.** It is imported by three things that do not
agree about what is loaded:

* `scripts/build_card_fixtures.py`, a plain script that must know which cards to
  put in the offline sample, and which runs with no settings module;
* `decks/seeding.py`, which writes them into the database;
* the test suite.

One definition, three readers. If the list of cards lived in the seed command,
the fixture builder could not see it, and the sample would drift out of step
with the decks that need it - silently, because a missing card looks exactly
like a card the catalogue does not have.

**These are shapes, not legal decks.** They repeat cards past the singleton
rule and most are well under 100 cards, because a test deck's job is to be the
smallest thing that still has the property under test. Nothing here is a deck
anybody should play.

**There is deliberately no legal 100-card deck in this file.** The reference
Chainer deck already is one - 99 cards, a commander, hand-annotated - and it is
seeded by `manage.py seed_reference_deck`. A second legal deck would be a second
thing to keep current in exchange for nothing these shapes do not already cover.
What the reference deck cannot be is *odd*, and odd is what this file is for.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Deck-scoped annotation overrides, keyed by card name.
#:
#: **Deck-scoped, never built-in.** A built-in annotation applies to every deck
#: of every user, so seeding "Cabal Coffers scales per swamp" there would be
#: defensible, but "this land is a forest for Yavimaya" would not - and the two
#: are written the same way. `decks/seeding.py` writes every one of these with
#: an explicit deck, so the question never arises.
Overrides = Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class DeckShape:
    """One deck, and the reason it is worth having."""

    key: str
    name: str
    #: The class of bug this shape exists to catch. Prose, aimed at whoever
    #: finds this file in a year wondering whether they may delete a deck.
    catches: str
    commander: str | None
    #: (quantity, card name), in the order they should be written.
    cards: tuple[tuple[int, str], ...]
    #: Judgements this deck carries, as deck-scoped annotations. Only the decks
    #: whose point *is* an annotation have any; the rest are deliberately bare,
    #: because "nobody has said" is the state most decks are really in.
    annotations: Overrides = field(default_factory=lambda: MappingProxyType({}))

    @property
    def card_count(self) -> int:
        """Cards in the 99. The commander is not one of them."""
        return sum(quantity for quantity, _ in self.cards)

    @property
    def land_count(self) -> int:
        """Only correct for the shapes below, whose lands are all named here."""
        return sum(quantity for quantity, name in self.cards if name in LAND_NAMES)

    @property
    def names(self) -> frozenset[str]:
        """Every distinct card this deck needs, commander included."""
        found = {name for _, name in self.cards}
        if self.commander:
            found.add(self.commander)
        return frozenset(found)


#: The lands used by these shapes. Written out rather than derived from a type
#: line, because this module may not touch the database - and a land count that
#: quietly stopped counting would make the land-light and land-heavy decks pass
#: while testing nothing.
LAND_NAMES = frozenset({
    "Swamp", "Plains", "Island", "Mountain", "Forest",
    "Command Tower", "Exotic Orchard", "Maze of Ith",
    "Bojuka Bog", "Phyrexian Tower", "Blood Crypt", "Thriving Moor",
    "Cabal Coffers", "Urborg, Tomb of Yawgmoth", "Yavimaya, Cradle of Growth",
    "Nykthos, Shrine to Nyx", "Gaea's Cradle",
})


# --- the shapes -------------------------------------------------------------

FIVE_COLOUR = DeckShape(
    key="five_colour",
    name="Five colours, one of each",
    catches=(
        "Anything that still assumes one colour. The per-colour report columns "
        "have nothing to show unless a deck actually makes more than black, and "
        "the five basic land subtypes map to five different colours - a bug "
        "that read a Forest as a Swamp would be invisible in every other deck "
        "here."
    ),
    commander="Kenrith, the Returned King",
    cards=(
        (5, "Plains"), (5, "Island"), (5, "Swamp"), (5, "Mountain"), (5, "Forest"),
        # Reads "one mana of any colour in your commander's colour identity".
        # The pool counts mana rather than holding sources, so the adapter has
        # to pick one and report the choice as a gap. With a five-colour
        # commander there is no defensible pick, which is the point.
        (2, "Command Tower"),
        (1, "Sol Ring"), (1, "Arcane Signet"),
        (3, "Swords to Plowshares"), (3, "Wrath of God"),
        (3, "Counterspell"), (3, "Brainstorm"),
        (3, "Dark Ritual"), (3, "Demonic Tutor"),
        (3, "Lightning Bolt"), (3, "Rite of Flame"),
        (3, "Llanowar Elves"), (3, "Cultivate"),
    ),
)

NO_COMMANDER = DeckShape(
    key="no_commander",
    name="Five colours, no commander",
    catches=(
        "The colour-identity fallback in `adapter._deck_colors`. With no "
        "commander there is no colour identity to read, and the deck's own "
        "coloured pips are the only evidence of what colours it wants. Every "
        "other deck in the suite has a commander, so the fallback branch was "
        "never once executed."
    ),
    commander=None,
    cards=FIVE_COLOUR.cards,
)

LAND_LIGHT = DeckShape(
    key="land_light",
    name="Twenty lands",
    catches=(
        "Mulligan and keep rates at the bottom of the range, and the Karsten "
        "verdict with them. A land count this low should produce visibly worse "
        "opening hands; if it does not, the opening-hand model is not reading "
        "the deck it was given."
    ),
    commander="Chainer, Dementia Master",
    cards=(
        (18, "Swamp"), (1, "Bojuka Bog"), (1, "Phyrexian Tower"),
        (4, "Dark Ritual"), (4, "Night's Whisper"), (4, "Demonic Tutor"),
        (4, "Gray Merchant of Asphodel"), (4, "Vampire Nighthawk"),
        (4, "Blood Artist"), (4, "Carrion Feeder"), (4, "Gravecrawler"),
        (4, "Reassembling Skeleton"), (4, "Infernal Grasp"),
    ),
)

LAND_HEAVY = DeckShape(
    key="land_heavy",
    name="Forty-five lands",
    catches=(
        "The same machinery at the other extreme. A deck this land-heavy should "
        "almost never mulligan for lands and should flood instead - and flooding "
        "is the failure mode a goldfish simulation is least likely to notice, "
        "because a goldfish never loses to it."
    ),
    commander="Chainer, Dementia Master",
    cards=(
        (43, "Swamp"), (1, "Bojuka Bog"), (1, "Phyrexian Tower"),
        (3, "Dark Ritual"), (3, "Night's Whisper"),
        (3, "Gray Merchant of Asphodel"), (3, "Blood Artist"),
        (3, "Infernal Grasp"),
    ),
)

SCALING_RAMP = DeckShape(
    key="scaling_ramp",
    name="Scaling mana, through the database",
    catches=(
        "`PER_CONTROLLED`, `TYPE_ADDING` and `DOUBLE_SUBTYPE` driven by "
        "annotation rows rather than by the hand-written Python fixture. Until "
        "this deck existed, the only proof those three rules worked was a deck "
        "defined in `simulation/fixtures/chainer.py` - which never travels "
        "through `CardAnnotation`, the adapter's merge, or the JSON column. "
        "Yavimaya is here so the subtype is not always 'swamp': with both "
        "type-adders out, every land is a swamp *and* a forest, which is the "
        "documented ambiguity in `mana.land_color`."
    ),
    commander="Chainer, Dementia Master",
    cards=(
        (20, "Swamp"), (8, "Forest"),
        (1, "Cabal Coffers"),
        (1, "Urborg, Tomb of Yawgmoth"),
        (1, "Yavimaya, Cradle of Growth"),
        (2, "Crypt Ghast"),
        (4, "Gray Merchant of Asphodel"), (4, "Necropotence"),
        (4, "Dark Ritual"), (4, "Demonic Tutor"),
    ),
    annotations=MappingProxyType({
        "Cabal Coffers": MappingProxyType({
            "kind": "land",
            "scaling_rule": "per_controlled",
            "scaling_subtype": "swamp",
            "scaling_activation": 2,
            "scaling_color": "B",
        }),
        "Urborg, Tomb of Yawgmoth": MappingProxyType({
            "kind": "land",
            "scaling_rule": "type_adding",
            "scaling_subtype": "swamp",
        }),
        "Yavimaya, Cradle of Growth": MappingProxyType({
            "kind": "land",
            "scaling_rule": "type_adding",
            "scaling_subtype": "forest",
        }),
        "Crypt Ghast": MappingProxyType({
            "kind": "creature",
            "scaling_rule": "double_subtype",
            "scaling_subtype": "swamp",
            "scaling_color": "B",
            "priority": 20,
            "accelerant": True,
        }),
    }),
)

OPPONENT_DEPENDENT = DeckShape(
    key="opponent_dependent",
    name="Cards that need an opponent",
    catches=(
        "The blind-spot detector on the case it was written for. Every card "
        "here resolves perfectly well in a goldfish and then does nothing, "
        "because the thing it is waiting for is an opponent. This is the deck "
        "the simulation flatters most and understands least, and the panel has "
        "to say so loudly."
    ),
    commander="Chainer, Dementia Master",
    cards=(
        (20, "Swamp"), (2, "Command Tower"),
        (4, "Rhystic Study"), (4, "Smothering Tithe"),
        (4, "Esper Sentinel"), (4, "Mystic Remora"),
        (4, "Painful Quandary"), (4, "Liliana's Caress"),
        (4, "Bottomless Pit"),
    ),
)

UNMODELLABLE = DeckShape(
    key="unmodellable",
    name="Cards the engine cannot read",
    catches=(
        "**The gap Phase 4 left open.** Its verification list asked that a deck "
        "full of unmodelled cards shows a loud, unmissable warning; what was "
        "actually tested was the empty case and the reference deck, neither of "
        "which has the problem. Every card here is one the deriver refuses to "
        "guess at - a land whose mana depends on what an opponent controls, a "
        "land that taps for nothing, devotion and creature-count scaling the "
        "pool cannot hold - and none of them is annotated. Coverage should be "
        "bad and the page should say so."
    ),
    commander=None,
    cards=(
        # "Add one mana of any colour a land an opponent controls could
        # produce." In a goldfish that is no colours at all, and it opens with
        # the same words as Arcane Signet - trap 12, as a deck.
        (4, "Exotic Orchard"),
        # A land with no mana ability whatsoever.
        (4, "Maze of Ith"),
        # Scales on devotion and on creature count. The engine's PER_CONTROLLED
        # counts lands carrying a subtype, so neither is expressible - and
        # nobody has annotated them into something that is.
        (4, "Nykthos, Shrine to Nyx"), (4, "Gaea's Cradle"),
        # Scales per swamp, and here nobody has said so.
        (4, "Cabal Coffers"),
        (4, "Rhystic Study"), (4, "Smothering Tithe"),
        (4, "Esper Sentinel"), (4, "Mystic Remora"),
        (4, "Ashnod's Altar"),
        (4, "Blood Crypt"),
        (4, "Gleemax"),
    ),
)

#: Every shape, in the order a person should meet them.
SHAPES: tuple[DeckShape, ...] = (
    FIVE_COLOUR,
    NO_COMMANDER,
    LAND_LIGHT,
    LAND_HEAVY,
    SCALING_RAMP,
    OPPONENT_DEPENDENT,
    UNMODELLABLE,
)

BY_KEY: Mapping[str, DeckShape] = MappingProxyType({shape.key: shape for shape in SHAPES})


def card_names() -> frozenset[str]:
    """Every card any shape needs.

    `scripts/build_card_fixtures.py` calls this to decide what goes into the
    offline sample. A name added to a deck above therefore reaches the fixture
    on the next rebuild without anybody having to remember a second list.
    """
    return frozenset().union(*(shape.names for shape in SHAPES))
