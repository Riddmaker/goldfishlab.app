"""The corpus' card digests (card model PR 20): a card is digested by the
fields it is made with, read through `cards.value`, never off the card."""

from dataclasses import replace

from simulation.cards import CREATURE, Card, Trigger
from simulations import games


def test_a_card_digest_reads_the_fields_it_is_made_with():
    spy = Card("Spy", 0, 0, 2, CREATURE, triggers=(Trigger("resolves", connive=1),))
    same = Card("Spy", 0, 0, 2, CREATURE, triggers=(Trigger("resolves", connive=1),))
    other = replace(spy, triggers=(Trigger("resolves", draw=1),))
    digest = games._digest(games._stable(spy))
    assert digest == games._digest(games._stable(same))
    assert digest != games._digest(games._stable(other))
    assert "field:triggers" in games.features(spy)
