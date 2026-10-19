"""The interpreter's vocabulary (card model, phase 4).

Since PR 13 the engine plays a card's blocks (:mod:`simulation.blocks`), built
from four atoms, each compiled once into a function and never interpreted
while a game is played:

* :mod:`~simulation.rules.selector` - which cards: one form for what restricted
  mana may pay for, what a cost taps or sacrifices, and what is counted;
* :mod:`~simulation.rules.amount` - how much: a number, a count of a selector,
  devotion, a creature's stats;
* :mod:`~simulation.rules.condition` - whether the board holds: every question
  `Game.holds` asked;
* cost atoms - :class:`simulation.blocks.Cost` with a selector.

:mod:`~simulation.rules.mana` is the first family on them: the encoder and
decoder of the sixteen mana fields, and the view the engine reads.
:mod:`~simulation.rules.play` is the second (PR 14): lands, costs and spells.
PR 15 adds triggers, turns and combat, with the atoms they need.

Nothing here imports Django, like the rest of :mod:`simulation`.
"""
