"""Card model phase 0.7: splitting a card's text into clauses, and how much of
each the reader's matches cover."""

from simulations.management.commands.clause_audit import clauses, covered


def test_keywords_reminders_and_sentences_are_told_apart():
    text = ("Flying, vigilance\n"
            "When this creature enters, draw a card. Then discard a card.\n"
            "Ward {2} (Whenever it becomes the target of a spell, counter it unless "
            "its controller pays {2}.)")
    found = clauses(text, ["Flying", "Vigilance", "Ward"])
    assert ("Flying, vigilance", "keyword") in found
    assert ("When this creature enters, draw a card.", "") in found
    assert ("Then discard a card.", "") in found
    assert ("Ward {2}", "keyword") in found
    assert sum(kind == "reminder" for _clause, kind in found) == 1


def test_coverage_is_the_share_of_characters_matched():
    clause = "{T}: Add {G}."
    assert covered(clause, ["{t}: add {g}"]) > 0.8
    assert covered(clause, []) == 0.0
    assert 0 < covered("Draw three cards, then lose 3 life.", ["draw three cards"]) < 0.6
