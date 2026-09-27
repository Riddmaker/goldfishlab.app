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

# Frank Karsten's published Commander guidance: 35-38 lands for a typical
# 99-card deck. Quoted, not invented - and shown to the user with his name on
# it, because a bare "too few lands" is an opinion pretending to be a fact.
KARSTEN_MIN = 35
KARSTEN_MAX = 38
KARSTEN_SOURCE = "Frank Karsten's mana-base guidance for Commander"

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
    kind_counts: dict[str, int] = field(default_factory=dict)
    role_counts: dict[str, int] = field(default_factory=dict)

    lands_verdict: Verdict | None = None
    bracket_verdict: Verdict | None = None
    legality: list[Verdict] = field(default_factory=list)

    # How much of the deck the app could actually read. Phase 4 turns this into
    # the coverage panel; it is recorded from the start so no page can imply
    # full understanding it does not have.
    cards_needing_review: int = 0

    @property
    def coverage(self) -> float:
        if not self.total_cards:
            return 0.0
        return 1 - self.cards_needing_review / self.total_cards

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
    kinds: Counter[str] = Counter()
    roles: Counter[str] = Counter()
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

        if profile is not None:
            kinds[profile.kind] += quantity
            for role in profile.role_tags:
                roles[role] += quantity
            if profile.needs_review:
                analysis.cards_needing_review += quantity

    if deck.commander_id and deck.commander:
        colors.update(deck.commander.color_identity)

    analysis.curve = {bucket: curve.get(bucket, 0) for bucket in range(CURVE_CEILING + 1)}
    analysis.average_mv = (
        round(mv_total / analysis.nonland_count, 2) if analysis.nonland_count else 0.0
    )
    analysis.color_identity = sorted(colors)
    analysis.kind_counts = dict(kinds)
    analysis.role_counts = dict(roles.most_common())

    analysis.lands_verdict = _judge_lands(analysis.land_count)
    analysis.bracket_verdict = _judge_bracket(analysis.game_changers)
    analysis.legality = _check_legality(deck, entries, analysis)
    return analysis


def _judge_lands(count: int) -> Verdict:
    if KARSTEN_MIN <= count <= KARSTEN_MAX:
        return Verdict(
            True,
            f"{count} lands",
            f"Within the {KARSTEN_MIN}-{KARSTEN_MAX} band of {KARSTEN_SOURCE}.",
        )
    direction = "below" if count < KARSTEN_MIN else "above"
    return Verdict(
        False,
        f"{count} lands",
        f"{direction.capitalize()} the {KARSTEN_MIN}-{KARSTEN_MAX} band of {KARSTEN_SOURCE}. "
        "Rituals and mana rocks can justify the low end; this count does not know about them yet.",
    )


def _judge_bracket(game_changers: list[str]) -> Verdict:
    count = len(game_changers)
    if count <= BRACKET_3_GAME_CHANGERS:
        return Verdict(
            True,
            f"{count} Game Changers",
            f"Bracket 3 allows up to {BRACKET_3_GAME_CHANGERS}.",
        )
    return Verdict(
        False,
        f"{count} Game Changers",
        f"Bracket 3 allows {BRACKET_3_GAME_CHANGERS}. This deck reads as Bracket 4: "
        + ", ".join(sorted(game_changers)),
    )


def _check_legality(deck, entries, analysis: DeckAnalysis) -> list[Verdict]:
    """The five Commander rules a deck list can be checked against offline."""
    verdicts = []

    total = analysis.total_cards + (1 if deck.commander_id else 0)
    verdicts.append(
        Verdict(
            total == COMMANDER_DECK_SIZE,
            f"{total} cards",
            f"Commander decks are exactly {COMMANDER_DECK_SIZE}, commander included.",
        )
    )

    offenders = [
        entry.oracle_card.name
        for entry in entries
        if entry.quantity > 1 and not _unlimited(entry.oracle_card)
    ]
    verdicts.append(
        Verdict(
            not offenders,
            "Singleton" if not offenders else f"{len(offenders)} cards over the limit",
            "Basic lands and cards that say otherwise are exempt."
            + ("" if not offenders else " Over the limit: " + ", ".join(sorted(offenders)[:5])),
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
        verdicts.append(
            Verdict(
                not outside,
                "Colour identity" if not outside else f"{len(outside)} cards outside the identity",
                f"The commander's identity is {''.join(sorted(allowed)) or 'colourless'}."
                + ("" if not outside else " Outside it: " + ", ".join(outside[:5])),
            )
        )

        verdicts.append(
            Verdict(
                deck.commander.legalities.get("commander") == "legal",
                "Commander is legal"
                if deck.commander.is_commander_legal
                else "Commander is banned",
                deck.commander.name,
            )
        )
    else:
        verdicts.append(Verdict(False, "No commander set", "A Commander deck needs one."))

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
        f"{label}: {_listed(names)}"
        for label, names in (("Banned", banned), ("Not legal in Commander", not_legal))
        if names
    ]
    return Verdict(
        not problems,
        "Every card is legal" if not problems
        else f"{len(banned) + len(not_legal)} cards not allowed",
        "Checked against Scryfall's Commander legality for each card."
        + ("" if not problems else " " + ". ".join(problems) + "."),
    )


def _listed(names: list[str], shown: int = 5) -> str:
    return ", ".join(names[:shown]) + (" and more" if len(names) > shown else "")


def _unlimited(card) -> bool:
    """Cards allowed in any number: basics, and the handful that say so."""
    if card.type_line.startswith("Basic Land"):
        return True
    return "A deck can have any number of cards named" in (card.oracle_text or "")


def role_summary(deck) -> dict[str, int]:
    """Role counts, for the deck page's role strip.

    Reads `DerivedProfile.role_tags`, which is the rolled-up tag vocabulary -
    so "removal" here means what the Scryfall tag DAG means by it, which is
    broader than what a player usually means. The page says so.
    """
    counts: Counter[str] = Counter()
    for entry in deck.entries.select_related("oracle_card__profile"):
        profile = getattr(entry.oracle_card, "profile", None)
        if profile is None:
            continue
        for role in profile.role_tags:
            counts[role] += entry.quantity
    return dict(counts.most_common())


def review_cards(deck) -> list[tuple[str, list[str]]]:
    """Cards whose profile the deriver was not sure about, with the reasons."""
    rows = []
    for entry in deck.entries.select_related("oracle_card__profile"):
        profile = getattr(entry.oracle_card, "profile", None)
        if profile is not None and profile.needs_review:
            rows.append((entry.oracle_card.name, profile.review_reasons))
    return sorted(rows)


__all__ = [
    "BRACKET_3_GAME_CHANGERS",
    "COMMANDER_DECK_SIZE",
    "DeckAnalysis",
    "KARSTEN_MAX",
    "KARSTEN_MIN",
    "Verdict",
    "analyse",
    "review_cards",
    "role_summary",
]
