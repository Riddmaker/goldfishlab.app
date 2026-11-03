"""What a deck is built around: its themes (Themen-Doc, Paket 1, PR 1).

A theme is a plan many cards serve together - Theft, Aristocrats, Tokens -
not a card type or a category. Each theme here is a short list of **pieces**,
the jobs a card does in that plan (the Aristocrats' fodder, outlets and
payoffs), and each piece is a set of Scryfall Tagger tags, with the
mechanics our own blocks read (`DerivedProfile.mechanics`) and type-line
words beside them where a tag is missing or too narrow.

Rules, not a model (decided 2026-11-03, the Themen-Doc "Entscheidungen"):
every theme found can say which cards made it and why, and the same list
gives the same answer. Nothing here imports the database: `detect` takes
plain `ThemeCard`s, so the worked examples and the fixture decks test it
directly (`decks.themes` builds them from a deck).

How a theme is weighed:

* a card counts once per piece and copy, and twice when it is in the
  command zone - a commander is drawn every game;
* basic lands count for nothing;
* each piece has a **need**, the cards a deck built around the theme runs
  of it. A piece is full at its need; the theme's **fill** is the mean over
  its pieces, the **core** piece weighing double;
* a theme is only found when its core piece holds at least `CORE_MIN`
  cards: three lifegain cards and no payoff are no lifegain deck, and
  every deck gains some life;
* the fill gives the level: `STRONG`, `MEDIUM` or `HINT` (shown as words,
  never as the number; the number is for "Advanced"). A theme with an
  empty piece is a `HINT` at most: payoffs with nothing to pay off are a
  leaning, not a plan.

The tags are matched by slug. A slug Scryfall renames stops matching
silently, so `tests/test_themes.py` checks every slug of the catalogue
against the committed tag fixture, and `manage.py themes_report` names any
slug a live catalogue no longer has.

Blink is not a theme yet: the tags have `flicker`, but nothing for the
cards that want to be flickered, so a Blink deck could not be told from a
deck with two flicker spells.
"""

import re
from dataclasses import dataclass, field

from django.utils.translation import gettext_noop

STRONG = "strong"
MEDIUM = "medium"
HINT = "hint"

#: The fill each level starts at. Calibrated on the 41 decks of
#: `tests/fixtures/trace_decks.json`; `tests/test_themes.py` holds five of
#: them to what their precons are built around.
LEVELS = ((STRONG, 0.75), (MEDIUM, 0.5), (HINT, 0.4))

#: Cards a theme's core piece needs before the theme is found at all.
CORE_MIN = 4

#: A creature type the deck must hold this many of to be typal.
TYPAL_MEMBERS_MIN = 10


@dataclass(frozen=True)
class Piece:
    """One job in a theme, and how a card is known to do it.

    ``tags`` match a card's tags with the tag tree rolled up (a card tagged
    `theft-cast` also matches `theft`); ``direct`` match only tags the card
    wears itself - `death-trigger` rolled up would take in every card that
    triggers on its *own* death, which is fodder, not a payoff.
    """

    key: str
    label: str
    need: int
    tags: frozenset[str] = frozenset()
    direct: frozenset[str] = frozenset()
    mechanics: frozenset[str] = frozenset()
    #: Words of the type line, matched whole: "Equipment", "Aura".
    types: frozenset[str] = frozenset()

    def holds(self, card: "ThemeCard") -> bool:
        return bool(self.tags & card.tags or self.direct & card.direct
                    or self.mechanics & card.mechanics or self.types & card.types)


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    #: What the theme is, in one sentence, for the page (PR 2).
    info: str
    #: The core piece first.
    pieces: tuple[Piece, ...]

    @property
    def core(self) -> Piece:
        return self.pieces[0]


def _p(key, label, need, *, tags=(), direct=(), mechanics=(), types=()) -> Piece:
    return Piece(key, label, need, frozenset(tags), frozenset(direct), frozenset(mechanics),
                 frozenset(types))


# --- the pieces many themes share -------------------------------------------------

_SAC_OUTLET = _p("outlet", gettext_noop("Sacrifice outlet"), 3,
                 tags=("repeatable-sacrifice-outlet", "free-sacrifice-outlet"),
                 mechanics=("sac_outlet",))

THEMES: tuple[Theme, ...] = (
    Theme("theft", gettext_noop("Theft"),
          gettext_noop("Takes what opponents own - their creatures, their spells, the top of "
                       "their library - and plays it for you."),
          (_p("thief", gettext_noop("Takes control or casts their cards"), 8,
              tags=("theft", "threaten"), mechanics=("steal",)),
           _p("outlet", gettext_noop("Sacrifices what it took"), 2,
              tags=("sacrifice-outlet",), mechanics=("sac_outlet",)))),
    Theme("aristocrats", gettext_noop("Aristocrats"),
          gettext_noop("Sacrifices its own creatures on purpose, and is paid each time one "
                       "dies."),
          (_p("payoff", gettext_noop("Pays off when a creature dies"), 6,
              direct=("death-trigger", "your-sacrifice-matters", "sacrifice-matters"),
              mechanics=("death_trigger",)),
           _SAC_OUTLET,
           _p("fodder", gettext_noop("Makes bodies to sacrifice"), 6,
              tags=("repeatable-token-generator", "death-trigger-self", "recursion-self"),
              mechanics=("tokens",)))),
    Theme("tokens", gettext_noop("Tokens"),
          gettext_noop("Fills the table with creature tokens and makes them count."),
          (_p("maker", gettext_noop("Makes creature tokens"), 10,
              tags=("repeatable-token-generator",), mechanics=("tokens",)),
           _p("payoff", gettext_noop("Gets better with many tokens"), 5,
              tags=("synergy-token", "token-doubler", "token-increaser", "anthem",
                    "keyword-anthem")))),
    Theme("reanimator", gettext_noop("Reanimator"),
          gettext_noop("Puts big creatures into its graveyard on purpose, then brings them "
                       "back cheaply."),
          (_p("reanimate", gettext_noop("Brings creatures back from a graveyard"), 6,
              tags=("reanimate", "mass-reanimation"), mechanics=("reanimate",)),
           _p("fill", gettext_noop("Fills the graveyard"), 5,
              tags=("mill-self", "discard-outlet", "tutor-to-graveyard", "graveyard-fuel"),
              mechanics=("discard",)))),
    Theme("graveyard", gettext_noop("Graveyard"),
          gettext_noop("Uses its graveyard as a second hand: casts, activates and counts "
                       "what is in it."),
          (_p("payoff", gettext_noop("Plays from or counts the graveyard"), 6,
              tags=("castable-from-graveyard", "activate-from-graveyard",
                    "cards-in-graveyard-matter", "gives-castable-from-graveyard",
                    "leaves-graveyard-trigger", "synergy-graveyard-cast")),
           _p("fill", gettext_noop("Fills the graveyard"), 5,
              tags=("mill-self", "discard-outlet", "graveyard-fuel")))),
    Theme("spellslinger", gettext_noop("Spellslinger"),
          gettext_noop("Casts many instants and sorceries, and is rewarded for each one."),
          (_p("payoff", gettext_noop("Rewards casting spells"), 6,
              tags=("synergy-instant", "synergy-sorcery", "synergy-noncreature", "magecraft",
                    "copy-spell", "storm-count-matters", "second-spell-matters")),
           _p("spells", gettext_noop("Instants and sorceries"), 25,
              types=("Instant", "Sorcery")))),
    Theme("counters", gettext_noop("+1/+1 counters"),
          gettext_noop("Puts +1/+1 counters on its creatures and grows them turn after turn."),
          (_p("payoff", gettext_noop("Cares about counters or doubles them"), 5,
              tags=("pp-counters-matter", "counter-doubler", "counter-increaser",
                    "repeatable-proliferate")),
           _p("source", gettext_noop("Puts +1/+1 counters"), 10,
              tags=("repeatable-pp-counters", "gives-pp-counters", "gives-pp-counters-to-all")))),
    Theme("landfall", gettext_noop("Landfall"),
          gettext_noop("Plays extra lands and is paid every time one enters."),
          (_p("payoff", gettext_noop("Pays off when a land enters"), 5,
              tags=("landfall",)),
           _p("extra", gettext_noop("Puts extra lands into play"), 5,
              tags=("extra-land", "multi-land-ramp")))),
    Theme("voltron", gettext_noop("Voltron"),
          gettext_noop("Dresses up one creature - usually the commander - with Equipment and "
                       "Auras, and wins through commander damage."),
          (_p("gear", gettext_noop("Equipment and Auras for your own creatures"), 10,
              types=("Equipment", "Aura")),
           _p("payoff", gettext_noop("Gets better when equipped or enchanted"), 3,
              tags=("synergy-equipment", "synergy-aura", "auto-equip",
                    "alternate-equip-cost")),
           _p("protect", gettext_noop("Keeps that creature safe"), 5,
              tags=("protection", "gives-hexproof", "gives-indestructible")))),
    Theme("lifegain", gettext_noop("Lifegain"),
          gettext_noop("Gains life again and again, and turns each gain into something."),
          (_p("payoff", gettext_noop("Pays off when you gain life"), 5,
              tags=("lifegain-matters", "lifegain-increaser", "lifegain-to-damage")),
           _p("source", gettext_noop("Gains life again and again"), 10,
              tags=("repeatable-lifegain", "gives-lifelink"), mechanics=("lifegain",)))),
    Theme("artifacts", gettext_noop("Artifacts"),
          gettext_noop("Runs many artifacts and counts them."),
          (_p("payoff", gettext_noop("Counts or rewards artifacts"), 6,
              tags=("synergy-artifact", "artifact-count-matters", "affinity-for-artifacts",
                    "artifactfall")),
           _p("artifacts", gettext_noop("Artifacts"), 25, types=("Artifact",)))),
    Theme("enchantments", gettext_noop("Enchantments"),
          gettext_noop("Runs many enchantments and draws or grows from each one."),
          (_p("payoff", gettext_noop("Counts or rewards enchantments"), 5,
              tags=("synergy-enchantment", "synergy-aura", "synergy-saga")),
           _p("enchantments", gettext_noop("Enchantments"), 20, types=("Enchantment",)))),
    Theme("treasure", gettext_noop("Treasure"),
          gettext_noop("Makes Treasure tokens again and again, and spends or sacrifices them "
                       "for more."),
          (_p("maker", gettext_noop("Makes Treasure again and again"), 6,
              tags=("repeatable-treasures",), mechanics=("treasure",)),
           _p("payoff", gettext_noop("Rewards Treasure or artifacts leaving"), 3,
              tags=("synergy-treasure", "sacrifice-outlet-artifact")))),
)

BY_KEY = {theme.key: theme for theme in THEMES}

#: The typal theme is built per creature type, so it is no entry of `THEMES`.
TYPAL = "typal"
TYPAL_LABEL = gettext_noop("Typal")
TYPAL_INFO = gettext_noop("Runs many creatures of one type, and cards that reward that type.")
TYPAL_MEMBERS = gettext_noop("Creatures of the type")
TYPAL_PAYOFF = gettext_noop("Rewards that type")
#: Typal payoffs need this many; the members need `TYPAL_MEMBERS_FULL`.
TYPAL_PAYOFF_NEED = 6
TYPAL_MEMBERS_FULL = 20

#: Every slug the catalogue matches on, for the slug check.
SLUGS = frozenset(
    slug for theme in THEMES for piece in theme.pieces for slug in piece.tags | piece.direct)


# --- the input --------------------------------------------------------------------

_SUBTYPES = re.compile(r"—\s*(.+)$")


@dataclass(frozen=True)
class ThemeCard:
    """One card of a deck as the themes read it."""

    name: str
    type_line: str = ""
    #: Tag slugs with the tree rolled up (`OracleCardTag`, every row).
    tags: frozenset[str] = frozenset()
    #: Only the tags the card wears itself (`OracleCardTag.is_direct`).
    direct: frozenset[str] = frozenset()
    mechanics: frozenset[str] = frozenset()
    quantity: int = 1
    commander: bool = False
    #: Changeling: every creature type at once.
    every_type: bool = False
    types: frozenset[str] = field(init=False)

    def __post_init__(self):
        front = self.type_line.split(" // ")[0]
        object.__setattr__(self, "types", frozenset(front.replace("—", " ").split()))

    @property
    def weight(self) -> int:
        """How often it counts: per copy (Relentless Rats), twice in the command
        zone, never as a basic land."""
        if "Basic" in self.types and "Land" in self.types:
            return 0
        return 2 if self.commander else self.quantity

    @property
    def creature_types(self) -> frozenset[str]:
        front = self.type_line.split(" // ")[0]
        if "Creature" not in front:
            return frozenset()
        match = _SUBTYPES.search(front)
        return frozenset(match.group(1).split()) if match else frozenset()


# --- the output -------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One piece of a found theme: how many cards hold it, and which."""

    key: str
    label: str
    count: int
    need: int
    cards: tuple[str, ...]


@dataclass(frozen=True)
class Found:
    theme: str
    label: str
    info: str
    level: str
    fill: float
    parts: tuple[Part, ...]
    #: The creature type of a typal theme ("Angel"); empty for the others.
    subject: str = ""

    @property
    def cards(self) -> tuple[str, ...]:
        """Every card behind the theme once, in the order of its pieces."""
        return tuple(dict.fromkeys(name for part in self.parts for name in part.cards))


# --- detection --------------------------------------------------------------------


def _level(fill: float) -> str:
    return next((level for level, start in LEVELS if fill >= start), "")


def _capped(level: str, parts: list[Part]) -> str:
    """At most a hint while one of the pieces holds no card."""
    if level and any(part.count == 0 for part in parts):
        return HINT
    return level


def _fill(parts: list[Part]) -> float:
    """The mean fill of the pieces, the core (the first) weighing double."""
    shares = [min(part.count / part.need, 1.0) for part in parts]
    return (2 * shares[0] + sum(shares[1:])) / (len(shares) + 1)


def _part(key: str, label: str, need: int, cards: list[ThemeCard]) -> Part:
    return Part(key, label, sum(card.weight for card in cards), need,
                tuple(sorted({card.name for card in cards})))


def _theme(theme: Theme, cards: list[ThemeCard]) -> Found | None:
    parts = [_part(piece.key, piece.label, piece.need,
                   [card for card in cards if card.weight and piece.holds(card)])
             for piece in theme.pieces]
    if parts[0].count < CORE_MIN:
        return None
    fill = _fill(parts)
    level = _capped(_level(fill), parts)
    if not level:
        return None
    return Found(theme.key, theme.label, theme.info, level, round(fill, 3), tuple(parts))


def _typal(cards: list[ThemeCard]) -> Found | None:
    """The creature type most of the deck shares, when it is a plan.

    A payoff is a card tagged for that type (`typal-angel`), or for a type
    its controller chooses (`typal-choose`): Kindred Discovery serves any
    tribe. Changelings are members of every type.
    """
    counted: dict[str, list[ThemeCard]] = {}
    for card in cards:
        if card.weight:
            for kind in card.creature_types:
                counted.setdefault(kind, []).append(card)
    if not counted:
        return None
    kind = max(sorted(counted), key=lambda name: sum(card.weight for card in counted[name]))
    changelings = [card for card in cards if card.weight and card.every_type
                   and card not in counted[kind]]
    members = _part("members", TYPAL_MEMBERS, TYPAL_MEMBERS_FULL, counted[kind] + changelings)
    if members.count < TYPAL_MEMBERS_MIN:
        return None
    wanted = {f"typal-{kind.lower()}", "typal-choose"}
    payoff = _part("payoff", TYPAL_PAYOFF, TYPAL_PAYOFF_NEED,
                   [card for card in cards if card.weight and wanted & card.tags])
    if payoff.count < CORE_MIN:
        return None
    parts = [payoff, members]
    fill = _fill(parts)
    level = _level(fill)
    if not level:
        return None
    return Found(TYPAL, TYPAL_LABEL, TYPAL_INFO, level, round(fill, 3), tuple(parts),
                 subject=kind)


def detect(cards) -> list[Found]:
    """Every theme the deck is built around, the strongest first.

    An empty list is an answer: plenty of good decks are "good stuff" with
    no single plan.
    """
    cards = list(cards)
    found = [result for theme in THEMES if (result := _theme(theme, cards))]
    if typal := _typal(cards):
        found.append(typal)
    return sorted(found, key=lambda result: (-result.fill, result.theme))
