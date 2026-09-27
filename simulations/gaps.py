"""Two kinds of gap, because they are two different questions.

Until now everything the adapter could not establish went into one list and one
percentage. Measuring that list across the nine decks in the development
database gave a number worth knowing: **161 of 217 gaps were `priority`** - the
field that, by settled decision, nothing may ever derive. So "78.6% understood"
mostly meant "78.6% hand-annotated", and a deck of perfectly readable cards
nobody had annotated looked exactly like a deck of cards the engine could not
read at all. Two very different problems, one number, and the number pointed at
neither.

So a gap now says which question it is:

``reading``
    The engine could not read the card. Cabal Coffers' per-Swamp scaling, a
    land that taps for nothing we can see, an ``Add`` clause in a sentence the
    deriver will not parse. **This is a limit of the application**, and the
    honest response is to widen what it can read - which is what Phase 5b does.

``judgement``
    Nobody has said. How early to cast it, whether it makes a one-land hand
    keepable. No amount of card text implies these; they are deck-author
    opinions, and `simulation/` has always treated them that way. **This is not
    a limit of the application**, and closing it is one person's five minutes on
    the tune page.

The split is a **property of the field**, not of the row, so it applies to gaps
already stored on finished runs and open playtest sessions. That is deliberate:
stored gaps are a record of what the engine saw, they are never rewritten, and
a migration that back-filled a `kind` column would have been a migration that
edited history. `kind_of` reads the field name, which those rows already carry.
"""

#: The engine could not read the card. Ours to fix.
READING = "reading"

#: Nobody has said. The deck author's to answer, and nothing else can.
JUDGEMENT = "judgement"

#: Fields a human has to supply, because nothing in the card text implies them.
#: Deriving either one mechanically changed the keep rate and every number under
#: it - which is why both are fields rather than rules.
JUDGEMENT_FIELDS = frozenset({"priority", "accelerant"})


def kind_of(field: str) -> str:
    """Which question a gap in this field asks."""
    return JUDGEMENT if field in JUDGEMENT_FIELDS else READING


def field_of(gap) -> str:
    """The field a gap names, whether it is a `Gap` or a stored dict.

    Stored gaps are JSON - `asdict(gap)` at the moment the run finished - and
    live ones are `adapter.Gap`. Everything that counts gaps has to handle both,
    so it happens here once instead of at four call sites.
    """
    return gap["field"] if isinstance(gap, dict) else gap.field


def card_of(gap) -> str:
    """The card a gap is about, from either shape."""
    return gap["card"] if isinstance(gap, dict) else gap.card


def of_kind(gaps, kind: str) -> list:
    """Just the gaps asking one of the two questions."""
    return [gap for gap in gaps if kind_of(field_of(gap)) == kind]


def cards_with(gaps, kind: str | None = None) -> set[str]:
    """Distinct card names carrying a gap, optionally of one kind only.

    Names rather than ids because that is what a `Gap` carries, and a `Gap` is
    written to be read by a person.
    """
    return {card_of(gap) for gap in gaps
            if kind is None or kind_of(field_of(gap)) == kind}


def share(total: int, missing: int) -> float:
    """`missing` of `total` gone wrong, as the share that went right.

    Bounded on purpose: a score outside 0-1 is a bug, not a strong opinion. A
    two-card deck whose commander the engine could not read once reported -50%.
    """
    if not total:
        return 0.0
    return max(0, total - missing) / total
