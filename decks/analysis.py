"""What a deck page says about a deck, without simulating anything.

Phase 1 ships a useful deck viewer and no simulation at all, so everything here
is arithmetic over the card list. That is a feature: these numbers are exact,
and the page can say so. When the simulator arrives in Phase 3 its output sits
*beside* these, clearly labelled as estimates, never blended into them.

The one judgement this module makes is the land-count verdict, and it cites its
source (Karsten) rather than inventing a threshold.
"""

from collections import Counter
from dataclasses import dataclass, field

from django.utils.translation import gettext, gettext_noop, ngettext

from cards import commanders
from decks.models import COMMANDER_DECK_SIZE

# Frank Karsten's published Commander guidance: 35-38 lands for a typical
# 99-card deck. Quoted, not invented - and shown to the user with his name on
# it, because a bare "too few lands" is an opinion pretending to be a fact.
KARSTEN_MIN = 35
KARSTEN_MAX = 38
KARSTEN_SOURCE = gettext_noop("Frank Karsten's mana-base guidance for Commander")

#: Bracket 3 ("upgraded") allows at most three cards from the Game Changer list;
#: Brackets 1 and 2 expect none, 4 and 5 have no limit (Wizards' bracket rules,
#: checked again for P20 F4).
BRACKET_3_GAME_CHANGERS = 3

#: The curve histogram stops here and buckets the rest, so one Eldrazi cannot
#: stretch the chart to thirty columns.
CURVE_CEILING = 7


@dataclass
class Verdict:
    """A judgement with its reasoning attached.

    Never just a boolean: the deck page has to be able to explain itself, and a
    red badge with no sentence next to it is how users learn to ignore badges.
    """

    ok: bool
    message: str
    detail: str = ""


@dataclass
class DeckAnalysis:
    """Everything the deck page renders."""

    total_cards: int = 0
    land_count: int = 0
    nonland_count: int = 0
    average_mv: float = 0.0
    curve: dict[int, int] = field(default_factory=dict)
    color_identity: list[str] = field(default_factory=list)
    game_changers: list[str] = field(default_factory=list)
    #: Cards counted as spells that have a land on their back (P20 F4, E1.5):
    #: the tile says how many, so the land count reads right either way.
    land_backs: int = 0
    #: The lowest bracket the Game Changers allow (P20 F4, E1.30).
    bracket_floor: int = 1

    lands_verdict: Verdict | None = None
    bracket_verdict: Verdict | None = None
    legality: list[Verdict] = field(default_factory=list)

    @property
    def is_legal(self) -> bool:
        return all(verdict.ok for verdict in self.legality)


def analyse(deck) -> DeckAnalysis:
    """Describe a deck from its entries. One query, no simulation."""
    entries = list(
        deck.entries.select_related("oracle_card").all()
    )

    analysis = DeckAnalysis()
    curve: Counter[int] = Counter()
    colors: set[str] = set()
    mv_total = 0

    for entry in entries:
        card = entry.oracle_card
        quantity = entry.quantity

        analysis.total_cards += quantity
        colors.update(card.color_identity)

        if card.is_land:
            analysis.land_count += quantity
        else:
            analysis.nonland_count += quantity
            if card.has_land_back:
                analysis.land_backs += quantity
            bucket = min(int(card.cmc), CURVE_CEILING)
            curve[bucket] += quantity
            mv_total += int(card.cmc) * quantity

        if card.game_changer:
            analysis.game_changers.append(card.name)

    for commander in deck.commanders:
        colors.update(commander.color_identity)
        # A commander off the list counts like any card of the 99 (P20 F4).
        if commander.game_changer:
            analysis.game_changers.append(commander.name)

    analysis.curve = {bucket: curve.get(bucket, 0) for bucket in range(CURVE_CEILING + 1)}
    analysis.average_mv = (
        round(mv_total / analysis.nonland_count, 2) if analysis.nonland_count else 0.0
    )
    analysis.color_identity = sorted(colors)

    analysis.lands_verdict = _judge_lands(analysis.land_count)
    analysis.bracket_floor = bracket_floor(len(analysis.game_changers))
    analysis.bracket_verdict = _judge_bracket(analysis.game_changers)
    analysis.legality = _check_legality(deck, entries, analysis)
    return analysis


def _judge_lands(count: int) -> Verdict:
    lands = ngettext("%(count)s land", "%(count)s lands", count) % {"count": count}
    band = {"low": KARSTEN_MIN, "high": KARSTEN_MAX, "source": gettext(KARSTEN_SOURCE)}
    if KARSTEN_MIN <= count <= KARSTEN_MAX:
        return Verdict(True, lands,
                       gettext("Within the %(low)s-%(high)s band of %(source)s.") % band)
    side = (gettext("Below the %(low)s-%(high)s band of %(source)s.") if count < KARSTEN_MIN
            else gettext("Above the %(low)s-%(high)s band of %(source)s.")) % band
    return Verdict(
        False,
        lands,
        side + " " + gettext("Rituals and mana rocks can justify the low end; this count "
                             "does not know about them yet."),
    )


def bracket_floor(game_changers: int) -> int:
    """The lowest bracket a deck with this many Game Changers can be in.

    A floor, never a rating (P20 F4, E1.30): Brackets 1 and 2 expect no Game
    Changers, so one is enough for 3, and Bracket 3 allows three. A deck with
    none is not held to 1 or 2 by them; speed, tutors and extra turns decide
    as well, which is the report's half.
    """
    if not game_changers:
        return 1
    return 3 if game_changers <= BRACKET_3_GAME_CHANGERS else 4


def _judge_bracket(game_changers: list[str]) -> Verdict:
    count = len(game_changers)
    floor = bracket_floor(count)
    cards = ", ".join(sorted(game_changers))
    if floor == 1:
        return Verdict(True, gettext("possible: no Game Changers"), gettext(
            "No Game Changers, so none of them keeps this deck out of Bracket 1 or 2. Speed, "
            "tutors and extra turns decide as well; the report checks the speed."))
    found = ngettext("at least, by %(count)s Game Changer",
                     "at least, by %(count)s Game Changers", count) % {"count": count}
    if floor == 3:
        return Verdict(True, found, gettext(
            "Brackets 1 and 2 expect no Game Changers and Bracket 3 allows up to %(limit)s, "
            "so this deck is at least Bracket 3: %(cards)s.") % {
                "limit": BRACKET_3_GAME_CHANGERS, "cards": cards})
    return Verdict(False, found, gettext(
        "Bracket 3 allows up to %(limit)s Game Changers, so this deck is at least "
        "Bracket 4: %(cards)s.") % {"limit": BRACKET_3_GAME_CHANGERS, "cards": cards})


def game_changers(deck) -> list[str]:
    """The Game Changers of a deck, commanders included, by name.

    For the report's bracket section (P20 F4), which needs the floor without
    the rest of `analyse`.
    """
    names = list(deck.entries.filter(oracle_card__game_changer=True)
                 .values_list("oracle_card__name", flat=True))
    return names + [card.name for card in deck.commanders if card.game_changer]


def _check_legality(deck, entries, analysis: DeckAnalysis) -> list[Verdict]:
    """The six Commander rules a deck list can be checked against offline."""
    verdicts = []
    leading = deck.commanders

    total = analysis.total_cards + len(leading)
    verdicts.append(
        Verdict(
            total == COMMANDER_DECK_SIZE,
            ngettext("%(count)s card", "%(count)s cards", total) % {"count": total},
            gettext("Commander decks are exactly %(size)s, commander included.") % {
                "size": COMMANDER_DECK_SIZE},
        )
    )

    offenders = [
        entry.oracle_card.name
        for entry in entries
        if entry.quantity > 1 and not _unlimited(entry.oracle_card)
    ]
    exempt = gettext("Basic lands and cards that say otherwise are exempt.")
    verdicts.append(
        Verdict(
            not offenders,
            gettext("Singleton") if not offenders
            else ngettext("%(count)s card over the limit", "%(count)s cards over the limit",
                          len(offenders)) % {"count": len(offenders)},
            exempt if not offenders
            else exempt + " " + gettext("Over the limit: %(cards)s") % {
                "cards": ", ".join(sorted(offenders)[:5])},
        )
    )

    if leading:
        allowed = {colour for commander in leading for colour in commander.color_identity}
        outside = sorted(
            {
                entry.oracle_card.name
                for entry in entries
                if not set(entry.oracle_card.color_identity) <= allowed
            }
        )
        identity = (gettext("The commanders' identity is %(colours)s.") if len(leading) > 1
                    else gettext("The commander's identity is %(colours)s.")) % {
            "colours": "".join(sorted(allowed)) or gettext("colourless")}
        verdicts.append(
            Verdict(
                not outside,
                gettext("Colour identity") if not outside
                else ngettext("%(count)s card outside the identity",
                              "%(count)s cards outside the identity",
                              len(outside)) % {"count": len(outside)},
                identity if not outside
                else identity + " " + gettext("Outside it: %(cards)s") % {
                    "cards": ", ".join(outside[:5])},
            )
        )
        verdicts.append(_may_lead(leading))
        banned = [card.name for card in leading if not card.is_commander_legal]
        verdicts.append(
            Verdict(
                not banned,
                gettext("Commander is legal") if not banned else gettext("Commander is banned"),
                ", ".join(banned or [card.name for card in leading]),
            )
        )
    else:
        verdicts.append(Verdict(False, gettext("No commander set"),
                                gettext("A Commander deck needs one.")))

    verdicts.append(_legal_cards(entries))
    return verdicts


def _may_lead(leading) -> Verdict:
    """The commander is a card that may be one, and a pair may share the zone.

    Lauf 1 read "✓ Commander is legal - Bayou": a land's Commander legality
    is "legal", because it may be *in* the deck (E1.3). Whether it may lead
    the deck is the other question (`cards.commanders`). An old deck with a
    wrong commander needs no migration: this line says so, and the choice
    above it is open.
    """
    rule = gettext("A commander is a legendary creature, or a card that says it can be one; "
                   "two share the command zone only as partners or with a Background.")
    if len(leading) == 2:
        if commanders.pair_ok(*leading):
            return Verdict(True, gettext("May be commanders"), rule)
        return Verdict(False, gettext("%(first)s and %(second)s cannot share the command "
                                      "zone") % {"first": leading[0].front_name,
                                                 "second": leading[1].front_name}, rule)
    card = leading[0]
    if commanders.stands_alone(card):
        return Verdict(True, gettext("May be commander"), rule)
    return Verdict(False, gettext("%(card)s cannot be a commander") % {"card": card.front_name},
                   rule)


def _legal_cards(entries) -> Verdict:
    """The 99 against Scryfall's own Commander legality field.

    Planned in Phase 1 and found missing in the 2026-09-25 review: only the
    commander was ever checked, so a deck holding a banned card read as legal
    on every other line. Banned and merely not legal are said separately -
    "banned" is a decision about a card, "not legal" is usually an Un-set or a
    digital-only card.
    """
    def status(entry):
        return (entry.oracle_card.legalities or {}).get("commander")

    banned = sorted({e.oracle_card.name for e in entries if status(e) == "banned"})
    not_legal = sorted({
        e.oracle_card.name for e in entries if status(e) not in ("legal", "banned")
    })
    problems = [
        text % {"cards": _listed(names)}
        for text, names in ((gettext("Banned: %(cards)s"), banned),
                            (gettext("Not legal in Commander: %(cards)s"), not_legal))
        if names
    ]
    checked = gettext("Checked against Scryfall's Commander legality for each card.")
    count = len(banned) + len(not_legal)
    return Verdict(
        not problems,
        gettext("Every card is legal") if not problems
        else ngettext("%(count)s card not allowed", "%(count)s cards not allowed",
                      count) % {"count": count},
        checked if not problems else checked + " " + ". ".join(problems) + ".",
    )


def _listed(names: list[str], shown: int = 5) -> str:
    listed = ", ".join(names[:shown])
    return gettext("%(cards)s and more") % {"cards": listed} if len(names) > shown else listed


def _unlimited(card) -> bool:
    """Cards allowed in any number: basics, and the handful that say so."""
    if card.type_line.startswith("Basic Land"):
        return True
    return "A deck can have any number of cards named" in (card.oracle_text or "")


__all__ = [
    "BRACKET_3_GAME_CHANGERS",
    "COMMANDER_DECK_SIZE",
    "DeckAnalysis",
    "KARSTEN_MAX",
    "KARSTEN_MIN",
    "Verdict",
    "analyse",
    "bracket_floor",
    "game_changers",
]
