"""The things this simulation is known to get wrong, named out loud.

A goldfish game has no opponent, no interaction and no board to read. Most of
what that costs is already reported: the adapter records a gap for every card
it could not describe, and every report carries the coverage score. What it
does *not* catch is the card the engine reads perfectly well and still
mismeasures, because the thing that makes the card good is not in the game
being simulated.

Three of those, and they are the three the design pass named:

1. **Opponent-dependent cards.** Rhystic Study, Smothering Tithe, Esper
   Sentinel, Mystic Remora. The engine casts them, they resolve, and nothing
   happens - because nothing an opponent does ever happens. A deck built
   around them will simulate far worse than it plays.
2. **Unresolved mana sources.** Cabal Coffers, Nykthos and Gaea's Cradle read
   as nothing or as one mana until somebody says otherwise. Already a gap; it
   earns a warning of its own because it is the one that most changes the
   numbers.
3. **Tag false positives.** "Destroy target creature **you control**" is a
   sacrifice outlet, not removal, and the community tag DAG files both under
   `removal`.

**Every function here reports; none decides.** A detector that quietly set
`goldfish_castable=False` on everything mentioning an opponent would be making
a judgement on the user's behalf and hiding it inside a number - which is the
exact failure this whole phase exists to prevent. What these produce is a
question, a named card, and a link to the field that answers it.
"""

import re
from dataclasses import dataclass, field

from django.utils.translation import gettext

#: A card whose *trigger* is something an opponent does. In a goldfish the
#: trigger never fires, so the card is a permanent that cost mana.
_OPPONENT_TRIGGER = re.compile(
    r"whenever an opponent\b"
    r"|whenever another player\b"
    r"|whenever a player\b"
    r"|whenever one or more opponents\b"
    r"|whenever an? (?:opponent|player) (?:casts|draws|plays)\b",
    re.IGNORECASE,
)

#: A card whose *effect* needs an opponent to point at. Drain payoffs live
#: here: "each opponent loses 1 life" is a real effect and measures nothing.
_OPPONENT_SUBJECT = re.compile(
    r"\beach opponent\b"
    r"|\btarget opponent\b"
    r"|\bopponents? controls?\b"
    r"|\ban opponent\b"
    r"|\beach other player\b",
    re.IGNORECASE,
)

#: The tag false positive worth catching, because the reference deck has three
#: of them: a card that destroys or sacrifices something **you** control is an
#: engine piece, and calling it removal overstates what the deck can answer.
_OWN_PERMANENT = re.compile(
    r"(?:destroy|sacrifice)s?\s+(?:target\s+|a\s+|an\s+|another\s+)?"
    r"[\w,\- ]{0,40}?you control",
    re.IGNORECASE,
)

#: …unless the same card also points at an opponent, in which case the tag is
#: fair and this detector must keep quiet.
_ALSO_THEIRS = re.compile(
    r"\btarget opponent\b|\bopponents? controls?\b|\beach opponent\b",
    re.IGNORECASE,
)

#: The role the tagger assigns that the detector above second-guesses.
REMOVAL_ROLE = "removal"


@dataclass(frozen=True)
class Suspect:
    """One card a blind spot applies to."""

    oracle_id: object
    name: str
    reason: str


@dataclass
class BlindSpot:
    """One named thing the simulation is known to get wrong.

    `detail` is shown whether or not there are any suspects, because the point
    is that the limitation is *named*; `fix` says which judgement resolves it,
    so the panel can send somebody straight to the field rather than leaving
    them with a complaint.
    """

    key: str
    heading: str
    detail: str
    fix: str
    suspects: list[Suspect] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.suspects)


def find(readings) -> list[BlindSpot]:
    """Every blind spot that applies to this deck, worst first.

    Only the ones with something to say. An empty panel is the correct output
    for a deck the engine can model, and a page listing three limitations that
    affect none of your cards teaches people to skip the panel.
    """
    readings = list(readings)
    found = [
        unresolved_mana(readings),
        opponent_dependent(readings),
        mislabelled_removal(readings),
    ]
    return [spot for spot in found if spot.suspects]


def opponent_dependent(readings) -> BlindSpot:
    """Cards whose whole point is something an opponent does.

    Deliberately not restricted to cards the engine finds uncastable: the
    dangerous case is the opposite one. Rhystic Study costs {3}, resolves, sits
    on the battlefield and is counted as a permanent the deck successfully
    deployed - which is exactly how a simulation flatters a deck it does not
    understand.
    """
    spot = BlindSpot(
        key="opponent_dependent",
        heading=gettext("Cards that need an opponent"),
        detail=gettext(
            "A goldfish game has no opponents, so nothing these cards react to "
            "ever happens. They cost mana and then do nothing else. A deck "
            "built around them plays considerably better than it simulates — "
            "and no number on this page can tell you by how much."
        ),
        fix=gettext("Can be cast against nobody"),
    )
    for reading in readings:
        text = reading.oracle_card.oracle_text or ""
        if _OPPONENT_TRIGGER.search(text):
            reason = gettext("triggers on something an opponent does")
        elif _OPPONENT_SUBJECT.search(text):
            # Deliberately hedged: the pattern proves the card *mentions* an
            # opponent, not that the whole card is dead without one. Bloodghast
            # is a fine creature whose enters-with-a-counter clause never
            # fires. Claiming more than the pattern shows would be the same
            # overreach this module exists to warn about.
            reason = gettext("part of what it does needs an opponent")
        else:
            continue
        spot.suspects.append(
            Suspect(reading.oracle_card.pk, reading.oracle_card.front_name, reason)
        )
    return spot


def unresolved_mana(readings) -> BlindSpot:
    """Mana sources the deriver refused to guess at.

    Read off the gaps the adapter already recorded rather than re-derived, so
    that this panel and the run's own gap table can never disagree about which
    cards they are.
    """
    spot = BlindSpot(
        key="unresolved_mana",
        heading=gettext("Mana sources nobody has pinned down"),
        detail=gettext(
            "Scryfall records which colours a card can make, never how much or "
            "at what cost. Where a pattern could not read the amount off the "
            "card text, the engine was told nothing rather than guessing at "
            "one — so these cards are currently making less mana in the "
            "simulation than they do on a table."
        ),
        fix=gettext("Taps for"),
    )
    for reading in readings:
        reasons = [gap.text for gap in reading.gaps if gap.field == "mana_abilities"]
        if reasons:
            spot.suspects.append(
                Suspect(
                    reading.oracle_card.pk,
                    reading.oracle_card.front_name,
                    reasons[0],
                )
            )
    return spot


def mislabelled_removal(readings) -> BlindSpot:
    """Cards tagged as removal that destroy something of yours.

    One narrow rule, and narrow on purpose: it fires only when the card both
    carries the `removal` role and describes destroying or sacrificing a
    permanent *you* control, without also pointing at an opponent anywhere.
    A broader pattern would start second-guessing the tagger on cards it is
    right about, which is worse than saying nothing.
    """
    spot = BlindSpot(
        key="mislabelled_removal",
        heading=gettext("Roles worth a second look"),
        detail=gettext(
            "The role vocabulary comes from the Scryfall Tagger community, "
            "which is broad by design: “destroy target creature you control” "
            "is filed under removal alongside “destroy target creature”. The "
            "first is an engine piece and the second answers a threat, and the "
            "report's milestone table counts them the same way."
        ),
        fix=gettext("Roles"),
    )
    for reading in readings:
        if REMOVAL_ROLE not in reading.card.tags:
            continue
        text = reading.oracle_card.oracle_text or ""
        if _OWN_PERMANENT.search(text) and not _ALSO_THEIRS.search(text):
            spot.suspects.append(
                Suspect(
                    reading.oracle_card.pk,
                    reading.oracle_card.front_name,
                    gettext("tagged as removal, but it destroys something you control"),
                )
            )
    return spot
