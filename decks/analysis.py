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

# Frank Karsten's published Commander guidance: 35-38 lands for a typical
# 99-card deck. Quoted, not invented - and shown to the user with his name on
# it, because a bare "too few lands" is an opinion pretending to be a fact.
KARSTEN_MIN = 35
KARSTEN_MAX = 38
KARSTEN_SOURCE = gettext_noop("Frank Karsten's mana-base guidance for Commander")

#: Bracket 3 ("upgraded") allows at most three cards from the Game Changer list.
BRACKET_3_GAME_CHANGERS = 3

#: Commander is a 100-card format, commander included.
COMMANDER_DECK_SIZE = 100

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

    lands_verdict: Verdict | None = None
    bracket_verdict: Verdict | None = None
    legality: list[Verdict] = field(default_factory=list)

    # How much of the deck the app could actually read. Phase 4 turns this into
    # the coverage panel; it is recorded from the start so no page can imply
    # full understanding it does not have.
    cards_needing_review: int = 0

    @property
    def is_legal(self) -> bool:
        return all(verdict.ok for verdict in self.legality)


def analyse(deck) -> DeckAnalysis:
    """Describe a deck from its entries. One query, no simulation."""
    entries = list(
        deck.entries.select_related("oracle_card", "oracle_card__profile").all()
    )

    analysis = DeckAnalysis()
    curve: Counter[int] = Counter()
    colors: set[str] = set()
    mv_total = 0

    for entry in entries:
        card = entry.oracle_card
        quantity = entry.quantity
        profile = getattr(card, "profile", None)

        analysis.total_cards += quantity
        colors.update(card.color_identity)

        if card.is_land:
            analysis.land_count += quantity
        else:
            analysis.nonland_count += quantity
            bucket = min(int(card.cmc), CURVE_CEILING)
            curve[bucket] += quantity
            mv_total += int(card.cmc) * quantity

        if card.game_changer:
            analysis.game_changers.append(card.name)

        if profile is not None and profile.needs_review:
            analysis.cards_needing_review += quantity

    if deck.commander_id and deck.commander:
        colors.update(deck.commander.color_identity)

    analysis.curve = {bucket: curve.get(bucket, 0) for bucket in range(CURVE_CEILING + 1)}
    analysis.average_mv = (
        round(mv_total / analysis.nonland_count, 2) if analysis.nonland_count else 0.0
    )
    analysis.color_identity = sorted(colors)

    analysis.lands_verdict = _judge_lands(analysis.land_count)
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


def _judge_bracket(game_changers: list[str]) -> Verdict:
    count = len(game_changers)
    found = ngettext("%(count)s Game Changer", "%(count)s Game Changers", count) % {
        "count": count}
    if count <= BRACKET_3_GAME_CHANGERS:
        return Verdict(True, found, gettext("Bracket 3 allows up to %(limit)s.") % {
            "limit": BRACKET_3_GAME_CHANGERS})
    return Verdict(
        False,
        found,
        gettext("Bracket 3 allows %(limit)s. This deck reads as Bracket 4: %(cards)s") % {
            "limit": BRACKET_3_GAME_CHANGERS, "cards": ", ".join(sorted(game_changers))},
    )


def _check_legality(deck, entries, analysis: DeckAnalysis) -> list[Verdict]:
    """The five Commander rules a deck list can be checked against offline."""
    verdicts = []

    total = analysis.total_cards + (1 if deck.commander_id else 0)
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

    if deck.commander_id and deck.commander:
        allowed = set(deck.commander.color_identity)
        outside = sorted(
            {
                entry.oracle_card.name
                for entry in entries
                if not set(entry.oracle_card.color_identity) <= allowed
            }
        )
        identity = gettext("The commander's identity is %(colours)s.") % {
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

        verdicts.append(
            Verdict(
                deck.commander.legalities.get("commander") == "legal",
                gettext("Commander is legal")
                if deck.commander.is_commander_legal
                else gettext("Commander is banned"),
                deck.commander.name,
            )
        )
    else:
        verdicts.append(Verdict(False, gettext("No commander set"),
                                gettext("A Commander deck needs one.")))

    verdicts.append(_legal_cards(entries))
    return verdicts


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
]
