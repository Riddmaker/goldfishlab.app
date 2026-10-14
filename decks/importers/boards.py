"""Which part of a deck list a card is in, in every language a tool writes.

A deck list is more than the deck. Arena writes a `Sideboard`, Archidekt a
`Maybeboard` category, Moxfield a `Considering` board, ManaBox a `// COMMANDER`
block, and LigaMagic writes `Comandante:`. Until P20 Lauf 1 the importer knew
five English words and treated every other heading as a card, which put
fifteen sideboard cards into a Commander deck (E1.80) and made `[CRÉATURES]` a
row nobody could resolve (E1.18).

The unit here is the **board**, not the heading: every spelling below maps to
one of six values, and only `MAIN` and `COMMANDER` reach the deck. The others
are counted on the import (`DeckImport.left_out`) and said out loud, because
the import rule is "never lose a card in silence" - leaving one out is fine,
not saying so is not.

The words are the headings the tools actually write (Arena in its seven client
languages, Moxfield, Archidekt, ManaBox, LigaMagic), folded with `casefold`.
A heading nobody lists here is read as the main deck, which is what an
unknown category heading like `[CRÉATURES]` is.
"""

import re

MAIN = "main"
COMMANDER = "commander"
SIDE = "side"
MAYBE = "maybe"
COMPANION = "companion"
TOKENS = "tokens"

#: The boards whose cards are the deck.
IN_DECK = frozenset({MAIN, COMMANDER})

_WORDS = {
    COMMANDER: (
        "commander", "commanders", "command zone", "kommandeur", "kommandeure",
        "commandant", "commandants", "comandante", "comandantes", "統率者",
    ),
    MAIN: (
        "deck", "main", "mainboard", "main deck", "maindeck", "hauptdeck", "mazo",
        "mazo principal", "mazzo", "baralho", "deck principal", "デッキ",
    ),
    SIDE: (
        "sideboard", "side", "side board", "réserve", "reserve", "banquillo",
        "reserva", "サイドボード",
    ),
    MAYBE: ("maybeboard", "maybe", "maybe board", "considering"),
    COMPANION: (
        "companion", "gefährte", "begleiter", "compagnon", "compañero", "compagno",
        "companheiro", "相棒",
    ),
    TOKENS: ("tokens", "token", "spielsteine", "jetons", "fichas", "pedine", "トークン"),
}
_BY_WORD = {word: board for board, words in _WORDS.items() for word in words}

#: `Sideboard (15)`, `Commander:`, `{noDeck}` - decoration around a heading
#: word that carries no meaning of its own.
_DECORATION = re.compile(r"\{[^}]*\}|\(\s*\d+\s*\)|[:：]\s*$")


def classify(heading: str) -> str | None:
    """The board a heading names, or `None` when it names none we know."""
    word = _DECORATION.sub("", heading or "").strip().casefold()
    return _BY_WORD.get(word)


def from_tags(cell: str) -> str | None:
    """The board a tags or category cell puts a card in, or `None`.

    Archidekt writes categories as `Commander{top}` or
    `Maybeboard{noDeck}{noPrice},Creature`; a cell is a comma-separated list.
    The commander test stays a substring test, as it always was here: the
    real cell reads `Commander,Ramp`. A category Archidekt flags `{noDeck}`
    is outside the deck whatever it is called.
    """
    text = cell or ""
    folded = text.casefold()
    items = [item.strip() for item in text.split(",") if item.strip()]
    boards = {classify(item) for item in items}
    if "commander" in folded or COMMANDER in boards:
        return COMMANDER
    for board in (MAYBE, SIDE, COMPANION, TOKENS):
        if board in boards:
            return board
    if "{nodeck}" in folded:
        return SIDE
    return None
