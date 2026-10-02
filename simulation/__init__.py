"""Monte Carlo simulation for Commander decks.

Modules:
    cards: the card model, the mana-ability rules and ``DeckDefinition``.
    fixtures: hand-written deck lists (the reference deck and the test fixture).
    mana: the mana model, with colour and the four scaling rules.
    game: game state, mulligans (the first one free), the turn sequence.
    agent: the priority-driven player.
    analysis: Monte Carlo aggregation and the metrics.

**This package never imports Django.** It runs and tests without a database,
without a settings module and without Django installed at all. The only module
that sees both worlds is ``simulations/engine/adapter.py`` in the application.
If a change wants to soften that boundary, the boundary has leaked - repair the
boundary, not the symptom.
"""

#: Version of the simulation's behaviour.
#:
#: **Every behaviour change increments this number**, including one that fixes a
#: bug. Stored runs record which version computed them, so the interface can
#: later say "computed with engine v2, current is v5 - re-run to compare".
#: Without this number, an old result and a new one are indistinguishable, which
#: makes both of them worthless.
#:
#: 1: The original engine, hardwired to the Chainer deck (Phase 1).
#: 2: Phase 2 - arbitrary decks, mana abilities as rules, priorities on the
#:    card. Aggregates identical to v1 but for one correction: the
#:    ``draw_engine`` metric had been missing Liliana, Dreadhorde General.
#: 3: The 2026-09-25 pre-launch review. Creatures with a flat mana ability tap
#:    for mana; a flat ability may cost generic mana to use (a Signet nets one,
#:    in both of its colours); a permanent that does not untap is tapped for
#:    mana once and stays tapped (``Card.untaps``, ``Game.stays_tapped``);
#:    Phyrexian symbols are paid with life when the mana is needed for the
#:    generic part. The reference deck has none of these cards, so the golden
#:    snapshot is unchanged - the evidence that nothing else moved.
#: 4: Phase 10, after the first user test. A tutor in a deck with no priority
#:    list takes a spell before a land - a key card (a combo piece or an
#:    engine) first, then the biggest spell castable by next turn - where it
#:    used to take the cheapest card, which was a land. "What you drew" counts
#:    only the opening hand and the draws (``Game.drawn``), no longer the cards
#:    a tutor found, and carries the sum of squares for a spread. The reference
#:    deck has a priority list, so the golden snapshot is unchanged.
ENGINE_VERSION = 4

__all__ = ["ENGINE_VERSION", "agent", "analysis", "cards", "fixtures", "game", "mana"]
