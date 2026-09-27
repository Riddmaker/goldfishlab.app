"""Game state, mulligans and the turn sequence.

The rules this rests on (see CLAUDE.md for the sources):

* Commander: 100 cards including the commander, 40 starting life, the commander
  sits in the command zone and is not one of the 99.
* London mulligan: draw a fresh 7, then put N cards on the bottom.
* **In multiplayer Commander the first mulligan is free.** That is why
  ``cards_to_bottom`` computes ``mulligans - 1`` and not ``mulligans``. Without
  that rule every keep rate would come out too pessimistic.
"""

import random
from collections import Counter

from simulation.cards import ARTIFACT, CREATURE, ENCHANTMENT, FLAT, PLANESWALKER, RITUAL, ROCK
from simulation.mana import (
    ManaPool,
    applicable_reduction,
    available_mana,
    doublers,
    effective_mana_cost,
    reductions_from,
)

STARTING_LIFE = 40
STARTING_HAND_SIZE = 7
MAX_MULLIGANS = 3          # down to five cards


class Game:
    """One goldfish game: no opponent, no interaction.

    That is deliberate. Goldfishing answers "does this deck work and how is it
    played", not "how does it hold up in a matchup". See
    ``simulation-recherche.md``.
    """

    def __init__(self, rng: random.Random, on_the_play: bool = True, *,
                 deck=None):
        self.rng = rng
        self.on_the_play = on_the_play
        # Keyword-only and defaulted: ``Game(rng)`` and
        # ``Game(rng, on_the_play=False)`` - every form the tests use - stay
        # valid unchanged.
        if deck is None:
            from simulation.fixtures import chainer
            deck = chainer.DECK
        self.deck = deck
        self.library = self.deck.shuffled(self.rng)
        self.hand = []
        self.lands = []            # lands on the battlefield
        self.rocks = []            # mana artifacts on the battlefield
        self.creatures = []
        self.other_permanents = []
        self.graveyard = []
        self.exiled = []
        self.life = STARTING_LIFE
        self.turn = 0
        self.mulligans = 0
        self.tapped_lands = 0
        self.tapped_rocks = 0
        #: Permanents that "don't untap during your untap step" and have
        #: already been tapped for mana - Mana Vault after its first turn.
        #: A list rather than a count at the front of a zone, because
        #: ``begin_turn`` resets those counts and these must survive it.
        self.stays_tapped: list = []
        self.land_drop_used = False
        self.commander_casts = 0
        # Mana available at the start of the main phase. It has to be recorded
        # before anything is cast, or Crypt Ghast would count himself in the
        # very turn he arrives.
        self.mana_available = 0
        #: The same mana, split by colour - ``{"B": 3, "C": 2}``. Recorded
        #: beside the total because the total on its own is the number that
        #: flatters a deck: five mana of which four is the wrong colour plays
        #: like one. Colours the pool did not hold are absent rather than
        #: zero; :mod:`simulation.analysis` fills the zeroes in when counting.
        self.mana_by_color: dict[str, int] = {}
        #: Lands in the very first seven, before any mulligan. Kept because it
        #: is the one quantity in the whole simulation with an exact
        #: closed-form answer - the report puts it beside the hypergeometric
        #: distribution, and the two agreeing is what makes the rest of the
        #: numbers worth believing. The kept hand cannot do that job: the
        #: mulligan rule throws the tails back and reshapes the distribution.
        self.first_hand_lands = 0
        #: The phase the turn is in, or ``None`` before it starts. Written by
        #: :mod:`simulation.actions`; the turn sequence itself does not consult
        #: it, so a simulation that never uses actions leaves it at ``None``.
        self.phase = None
        #: Mana floating this turn, opened once at the main phase. ``None``
        #: outside a main phase - and a cast with nothing floating is a bug,
        #: not a free spell.
        self.pool = None
        self.log = []

    @property
    def black_available(self) -> int:
        """Black mana available this turn.

        Read-only, and derived rather than stored: it was a field of its own
        until the engine learned about the other four colours, and a field that
        can be assigned is a field that can disagree with ``mana_by_color``.
        Kept under its old name because the reference deck is mono-black and
        every number published about it is expressed this way.
        """
        return self.mana_by_color.get("B", 0)

    # --- Zone helpers ------------------------------------------------------

    @property
    def battlefield(self):
        """Every permanent on the battlefield."""
        return self.lands + self.rocks + self.creatures + self.other_permanents

    @property
    def permanent_names(self):
        """The names of every permanent on the battlefield."""
        return {card.name for card in self.battlefield}

    def has(self, name: str) -> bool:
        """Is this particular card on the battlefield?"""
        return any(card.name == name for card in self.battlefield)

    def note(self, text: str) -> None:
        """Write one line into the game log."""
        self.log.append(text)

    # --- Drawing -----------------------------------------------------------

    def draw(self, count: int = 1) -> list:
        """Draw cards from the library into hand."""
        drawn = []
        for _ in range(count):
            if not self.library:
                break
            card = self.library.pop(0)
            self.hand.append(card)
            drawn.append(card)
        return drawn

    # --- Mulligan ----------------------------------------------------------

    def keepable(self, hand) -> bool:
        """The mulligan heuristic.

        This heuristic decides *every* number that follows, so it is kept
        deliberately explicit and simple:

        * 2 to 5 lands: keep.
        * 1 land: only with at least 2 cheap accelerants.
        * 0 or 6+ lands: mulligan.
        """
        lands = sum(1 for card in hand if card.is_land)
        if 2 <= lands <= 5:
            return True
        if lands == 1:
            accel = sum(1 for card in hand if card.is_accelerant)
            return accel >= 2
        return False

    @staticmethod
    def cards_to_bottom(mulligans: int) -> int:
        """How many cards go to the bottom under the London mulligan.

        The **first mulligan is free in multiplayer Commander**, hence
        ``mulligans - 1``.
        """
        return max(0, mulligans - 1)

    def _bottom_worst(self, count: int) -> None:
        """Put the weakest cards on the bottom of the library.

        In order: surplus lands (beyond 4) first, then the most expensive
        cards, because those do nothing in the early turns.
        """
        for _ in range(count):
            lands = [card for card in self.hand if card.is_land]
            if len(lands) > 4:
                worst = lands[0]
            else:
                worst = max(self.hand, key=lambda card: (card.mv, card.name))
            self.hand.remove(worst)
            self.library.append(worst)

    def take_opening_hand(self) -> None:
        """Draw the opening hand, mulligans included."""
        first = True
        while True:
            self.library = self.deck.shuffled(self.rng)
            self.hand = []
            self.draw(STARTING_HAND_SIZE)
            if first:
                self.first_hand_lands = sum(1 for card in self.hand if card.is_land)
                first = False
            if self.keepable(self.hand) or self.mulligans >= MAX_MULLIGANS:
                break
            self.mulligans += 1
        bottom = self.cards_to_bottom(self.mulligans)
        if bottom:
            self._bottom_worst(bottom)

    # --- Mana --------------------------------------------------------------

    def mana(self) -> ManaPool:
        """The most mana available this turn.

        Creatures with a flat mana ability count as sources since engine
        version 3 - until then a Llanowar Elves resolved, sat on the
        battlefield and made nothing, while the tune page said it tapped for
        green. Summoning sickness needs no rule of its own: the pool is opened
        once, at the start of the main phase, so every creature in it has been
        on the battlefield since the turn began.
        """
        untapped_lands = self._untapped(self.lands[self.tapped_lands:])
        untapped_rocks = self._untapped(self.rocks[self.tapped_rocks:])
        dorks = self._untapped(
            [card for card in self.creatures if card.ability(FLAT) is not None]
        )
        return available_mana(self.lands, untapped_lands, untapped_rocks + dorks,
                              doublers(self.battlefield))

    def _untapped(self, cards) -> list:
        """These permanents, without the ones that stayed tapped from before."""
        held = Counter(self.stays_tapped)
        free = []
        for card in cards:
            if held[card]:
                held[card] -= 1
                continue
            free.append(card)
        return free

    def open_pool(self) -> ManaPool:
        """Tap everything for this turn's pool, and remember what stays tapped.

        The engine taps every source once, when the main phase opens. A
        source that does not untap is used then and never again - which is
        the whole of Mana Vault in a goldfish: its upkeep untap costs {4} for
        {C}{C}{C}, and nobody pays that.
        """
        sources = (
            self._untapped(self.lands[self.tapped_lands:])
            + self._untapped(self.rocks[self.tapped_rocks:])
            + self._untapped([c for c in self.creatures if c.ability(FLAT) is not None])
        )
        pool = self.mana()
        for card in sources:
            flat = card.ability(FLAT)
            if not card.untaps and flat is not None and not flat.activation_generic:
                self.stays_tapped.append(card)
        return pool

    def can_cast(self, card, pool: ManaPool) -> bool:
        """Is the card playable from this pool, conditions included?"""
        if card.is_land or not card.goldfish_castable:
            return False
        if card.needs_creature_in_yard and not any(
                c.kind == CREATURE for c in self.graveyard):
            return False
        if card.needs_creature_on_bf and not self.creatures:
            return False
        return pool.can_pay_cost(
            effective_mana_cost(card, reductions_from(self.battlefield)), life=self.life
        )

    # --- Playing cards -----------------------------------------------------

    def play_land(self, card) -> None:
        """Play a land from hand."""
        self.hand.remove(card)
        self.lands.append(card)
        self.land_drop_used = True
        if card.enters_tapped:
            # Entered tapped: not a mana source this turn.
            self.lands.remove(card)
            self.lands.insert(0, card)
            self.tapped_lands += 1
        self.note(f"Land: {card.name}"
                  + (" (tapped)" if card.enters_tapped else ""))

    def cast(self, card, pool: ManaPool) -> None:
        """Cast a card and take the cost out of the pool."""
        cost = effective_mana_cost(card, reductions_from(self.battlefield))
        payment = pool.pay_cost(cost, life=self.life)
        if payment is None:
            raise ValueError(f"{card.name} ({cost}) cannot be paid from {pool}")
        # Phyrexian mana: whatever was not paid with mana is paid with life.
        self.life -= payment.life
        if card in self.hand:
            self.hand.remove(card)
        self._resolve(card, pool)

    def _resolve(self, card, pool: ManaPool) -> None:
        """Put the card in the right zone and apply its immediate effects."""
        if card.kind == RITUAL:
            pool.add(card.ritual_color, card.ritual_gain)
            self.graveyard.append(card)
            self.note(f"{card.name} -> +{card.ritual_gain}{card.ritual_color} mana")
            return
        if card.kind == ROCK:
            self.rocks.append(card)
            if card.enters_tapped:
                self.rocks.remove(card)
                self.rocks.insert(0, card)
                self.tapped_rocks += 1
            self.note(f"{card.name}"
                      + (" (tapped)" if card.enters_tapped else ""))
            return
        if card.kind == CREATURE:
            self.creatures.append(card)
            self.note(f"{card.name}")
            return
        if card.kind in (PLANESWALKER,):
            self.other_permanents.append(card)
            self.note(f"{card.name}")
            return
        if card.kind in (ENCHANTMENT, ARTIFACT):
            self.other_permanents.append(card)
            self.note(f"{card.name}")
            return
        # Sorcery / instant
        self.graveyard.append(card)
        self.note(f"{card.name}")

    def cast_commander(self, pool: ManaPool) -> None:
        """Cast the commander from the command zone, commander tax included."""
        commander = self.deck.commander
        cost = self._commander_cost()
        payment = pool.pay_cost(cost, life=self.life)
        if payment is None:
            raise ValueError(f"{commander.name} ({cost}) cannot be paid from {pool}")
        self.life -= payment.life
        self.commander_casts += 1
        self.creatures.append(commander)
        self.note(f"{commander.name} (commander)")

    def _commander_cost(self):
        """The commander's cost, tax and cost reduction included.

        The tax is generic, so it is added to the generic portion *before* a
        cost reduction applies - a medallion takes {1} off the taxed commander
        just as it would off an untaxed one.
        """
        commander = self.deck.commander
        cost = commander.mana_cost
        taxed = cost.with_generic(cost.generic + 2 * self.commander_casts)
        return taxed.reduced(
            applicable_reduction(taxed, reductions_from(self.battlefield))
        )

    def can_cast_commander(self, pool: ManaPool) -> bool:
        """Can the commander be paid for out of the command zone?"""
        commander = self.deck.commander
        if commander is None or self.has(commander.name):
            return False
        return pool.can_pay_cost(self._commander_cost(), life=self.life)

    # --- The turn ----------------------------------------------------------

    def begin_turn(self) -> None:
        """Untap, upkeep triggers, draw."""
        self.turn += 1
        self.tapped_lands = 0      # untap
        self.tapped_rocks = 0
        self.land_drop_used = False
        self.note(f"--- Turn {self.turn} ---")

        if self.turn > 1 or not self.on_the_play:
            skipper = self._first(lambda c: c.skips_draw_step)
            if skipper is not None:
                self.note(f"{skipper.name}: draw step skipped")
            else:
                self.draw(1)

        for card in self._triggers(lambda c: c.upkeep is not None):
            spec = card.upkeep
            drawn = self.draw(spec.draw)
            if spec.life_per_mv:
                self.life -= sum(c.mv for c in drawn)
            else:
                self.life -= spec.life

    def _first(self, predicate):
        """The first permanent the condition holds for."""
        for card in self.battlefield:
            if predicate(card):
                return card
        return None

    def _triggers(self, predicate):
        """The triggering permanents, in a fixed order.

        Descending mana value, ties broken by name. The order is **not**
        arbitrary: Phyrexian Arena and Dark Confidant both draw, so it decides
        which card goes to whom - and with it every number downstream.
        Descending mana value models what a player does (resolve the bigger
        engine first) and reproduces the order that was hardwired before the
        generalization.
        """
        return sorted(
            (card for card in self.battlefield if predicate(card)),
            key=lambda card: (-card.mv, card.name),
        )

    def end_step(self) -> None:
        """Card advantage in the end step, Necropotence style.

        The standard line: pay life in your own end step. The cards arrive in
        the next end step (an opponent's), so before your own next turn, which
        is why they are drawn straight away here. Capped conservatively: hand
        size 7, life never below 25.
        """
        for card in self._triggers(lambda c: c.end_step is not None):
            spec = card.end_step
            want = max(0, spec.max_hand - len(self.hand))
            affordable = max(0, self.life - spec.life_floor)
            pay = min(want, affordable)
            if pay:
                self.life -= pay
                self.draw(pay)
                self.note(f"{card.name}: {pay} life -> {pay} cards")
