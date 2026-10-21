"""The corpus' card digests (card model PR 20): a card is digested by the
fields it is made with, as blocks."""


from simulation import build
from simulation.cards import CREATURE, Trigger
from simulations import games
from tests.engine_builders import with_rules


def test_a_card_digest_reads_the_fields_it_is_made_with():
    spy = build.card("Spy", 0, 0, 2, CREATURE, triggers=(Trigger("resolves", connive=1),))
    same = build.card("Spy", 0, 0, 2, CREATURE, triggers=(Trigger("resolves", connive=1),))
    other = with_rules(spy, triggers=(Trigger("resolves", draw=1),))
    digest = games._digest(games._stable(spy))
    assert digest == games._digest(games._stable(same))
    assert digest != games._digest(games._stable(other))
    assert "form:trigger" in games.features(spy)
