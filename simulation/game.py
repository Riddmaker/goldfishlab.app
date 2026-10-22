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
from dataclasses import replace

from simulation.cards import (
    ANOTHER,
    ARTIFACT,
    CAST,
    COLORLESS_SPELL,
    COMBAT_SEARCH,
    COMBAT_SEARCH_TO_HAND,
    COMBAT_SEARCH_UNTAPPED,
    COUNTS,
    CREATURE,
    DIES,
    DISCARD_LAND,
    ENCHANTMENT,
    ENTERS,
    EXTRA,
    FLAT,
    GERM,
    GRANT,
    IMPRINT,
    INSTANT_OR_SORCERY,
    LANDER,
    LANDFALL,
    LANDS_COULD_PRODUCE,
    MAIN_PHASE,
    MULTIPLY,
    OPPONENT_CREATURES,
    OPPONENT_TURNS,
    PER_LAND,
    PLANESWALKER,
    RECURSIVE,
    RESOLVES,
    RITUAL,
    ROCK,
    SACRIFICED,
    SECOND_SPELL,
    STORE_FREE,
    STORE_SCALES,
    STORE_TAP,
    UPKEEP,
    AdditionalCost,
    Card,
)
from simulation.mana import (
    CHOSEN_SCOPES,
    ENCHANTED_SCOPES,
    Extra,
    ManaPool,
    RestrictedMana,
    Stored,
    applicable_reduction,
    available_mana,
    doublers,
    effective_mana_cost,
    granted_subtypes,
    has_treasure,
    land_color,
    land_colors,
    reductions_from,
    sacrifice_treasure,
    subtypes_of,
)
from simulation.manacost import (
    COLORLESS,
    COLORS,
    PHYREXIAN_LIFE_FLOOR,
    ManaCost,
    choice,
    is_choice,
)
from simulation.permanent import Marks, ZoneField, ident, same
from simulation.rules.amount import compile_amount, devotion
from simulation.rules.condition import check, tap_wanted
from simulation.rules.mana import flat
from simulation.rules.selector import PERMANENT, selects, wanted, wanted_of

#: The tokens the engine keeps as a count or a list rather than as cards,
#: when one is sacrificed (P19 R15).
TREASURE = "Treasure"
LANDER_FODDER = "Lander"

STARTING_LIFE = 40


def _token_name(mana: str) -> str:
    """What the log calls a token sacrificed for mana: an Eldrazi Spawn's is {C}."""
    return "Eldrazi Spawn" if mana == COLORLESS else "Treasure"

#: Grants to the creature a card is attached to: Paradise Mantle's equipped
#: one (P19 R22), Karametra's Favor's enchanted one (engine v33).
HOSTED_GRANTS = frozenset({"equipped", "enchanted_creature"})

#: The card types delirium counts (P19 R23).
DELIRIUM_TYPES = frozenset({"artifact", "battle", "creature", "enchantment", "instant",
                            "kindred", "land", "planeswalker", "sorcery"})


def _taps_another(card) -> bool:
    """Does the card's mana ability tap another permanent besides itself -
    Springleaf Drum, Survivors' Encampment (P19 R19)?"""
    return card.mana.taps_another


#: What a counting mana ability makes when it names no colour: the one the
#: hand wants most, for these amounts; for the others, what it names.
def _wants_color(amount) -> bool:
    if amount.kind == "stat":
        return True
    if amount.kind != "count" or amount.selector is None:
        return False
    return amount.zone == "battlefield" or bool(amount.selector.creature_types)
STARTING_HAND_SIZE = 7
MAX_MULLIGANS = 3          # down to five cards


class Game(Marks):
    """One goldfish game: no opponent, no interaction.

    That is deliberate. Goldfishing answers "does this deck work and how is it
    played", not "how does it hold up in a matchup". See
    ``simulation-recherche.md``.
    """

    #: The battlefield: each a `Zone`, whatever list it is given (card model,
    #: phase 4). Reading one costs what reading a list costs.
    lands = ZoneField()
    rocks = ZoneField()
    creatures = ZoneField()
    other_permanents = ZoneField()

    def __init__(self, rng: random.Random, on_the_play: bool = True, *,
                 deck=None):
        #: The number the next permanent gets: one per game, never reused.
        self.pids = 0
        #: Each card's permanent, by the card object: the one it is, or the
        #: last one it was (`Marks.pid_of`). Kept by the zones.
        self.where = {}
        #: Each permanent's card name, by number: what the trace writes.
        self.pid_names = {}
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
        #: Every card that came into the hand by being drawn - the kept opening
        #: hand and each draw since - and nothing else: not a tutored card, not
        #: a fetched land, not a card milled into the graveyard. What "What you
        #: drew" counts (phase 10 T5.1). A card drawn, put back and drawn again
        #: would count twice; nothing in the engine puts a card back today.
        #: Part of a stored playtest state since the card model's phase 4, so a
        #: restored game traces as one never saved; an older state starts it
        #: empty.
        self.drawn = []
        self.lands = []            # lands on the battlefield
        self.rocks = []            # mana artifacts on the battlefield
        self.creatures = []
        self.other_permanents = []
        self.graveyard = []
        self.exiled = []
        self.life = STARTING_LIFE
        self.turn = 0
        self.mulligans = 0
        self.land_drop_used = False
        self.commander_casts = 0
        #: The second commander's casts, for its own tax (P20 F2b).
        self.partner_casts = 0
        # Mana available at the start of the main phase. It has to be recorded
        # before anything is cast, or Crypt Ghast would count himself in the
        # very turn he arrives.
        self.mana_available = 0
        #: The same mana, by colour - ``{"B": 3, "C": 2}`` - as much of each
        #: as the pool could pay (`ManaPool.reach`): a dual land's mana counts
        #: for both of its colours, so the colours may add up to more. Recorded
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
        #: Treasure tokens on the battlefield, by the mana each makes (P19 R7).
        #: Kept from turn to turn; the pool borrows them and gives back the
        #: ones a payment did not sacrifice.
        self.treasures: list[str] = []
        #: Lander tokens on the battlefield (P19 R14): each a ``LANDER``
        #: search, activated when the mana is there.
        self.landers = 0
        #: P19 R15: what the last additional cost sacrificed - Eldritch
        #: Evolution's X counts from it. None: nothing yet.
        self.last_sacrificed = None
        #: P19 R15: the creatures that arrived this turn, and those whose {T}
        #: was used - by name, so that a saved playtest keeps them. Neither
        #: can pay {T} until the next turn.
        self.arrived: list[str] = []
        self.tapped_creatures: list[str] = []
        #: The colour each permanent chose as it entered, by name (P19 R13):
        #: Caged Sun, Utopia Sprawl. Kept, because the choice is made once.
        self.chosen_colors: dict[str, str] = {}
        #: P19 R16: mana a trigger added before the main phase opened - Lotus
        #: Cobra's for the land drop - waiting for this turn's pool.
        self.pending_mana: list[str] = []
        #: P19 R16: spells cast this turn, the commander included (Lotho).
        self.spells_this_turn = 0
        #: P19 R19: the creatures tapped for another card's mana this turn,
        #: by name - in ``tapped_creatures`` too, yet still that card's.
        self.fodder_used: list[str] = []
        #: P19 R19: the colours of the card each Chrome Mox exiled, by name.
        self.imprinted: dict[str, str] = {}
        #: P19 R20: the counters each permanent keeps for its mana, by name,
        #: and the player's energy. A free store's counters are in
        #: ``treasures`` instead, as ``Stored`` mana.
        self.counters: dict[str, int] = {}
        self.energy = 0
        #: P19 R20: permanents whose last counter went this turn - sacrificed,
        #: dead or back in hand as the next turn begins.
        self.emptied: list[str] = []
        #: P19 R21: the +1/+1 counters on each creature, by name - apart from
        #: the counters a store keeps for its mana.
        self.plus_counters: dict[str, int] = {}
        #: P19 R21: the cards that turned into their land (Itlimoc), by name.
        self.flipped: list[str] = []
        #: P19 R22: each Equipment and Aura on a creature, by name: the
        #: creature's name. Kept from turn to turn.
        self.attached: dict[str, str] = {}
        #: P19 R22: this turn's combat has been fought - there is one.
        self.combat_done = False
        #: P19 R22: during combat, mana a trigger adds is gone before the
        #: second main phase - but for what a card keeps (Savage Ventmaw).
        self.in_combat = False
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

    def next_pid(self) -> int:
        """A new permanent's number."""
        self.pids += 1
        return self.pids

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
            self.drawn.append(card)
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

    def _worst_in_hand(self):
        """The card in hand worth least: a surplus land (beyond 4), else the
        most expensive card, which does nothing in the early turns."""
        lands = [card for card in self.hand if card.is_land]
        if len(lands) > 4:
            return lands[0]
        return max(self.hand, key=lambda card: (card.mv, card.name))

    def _bottom_worst(self, count: int) -> None:
        """Put the weakest cards on the bottom of the library."""
        for _ in range(count):
            worst = self._worst_in_hand()
            self.hand.remove(worst)
            if worst in self.drawn:
                self.drawn.remove(worst)
            self.library.append(worst)

    def discard(self, count: int) -> list:
        """Discard the weakest cards - the additional cost of Big Score (P19 R7);
        the cards discarded."""
        discarded = []
        for _ in range(min(count, len(self.hand))):
            worst = self._worst_in_hand()
            self.hand.remove(worst)
            self.graveyard.append(worst)
            discarded.append(worst)
            self.note(f"  -> discards {worst.name}")
        return discarded

    def enter_battlefield(self, card) -> None:
        """Put a permanent onto the battlefield without casting it - what a
        tutor such as Green Sun's Zenith does (P19 R10)."""
        self._resolve(card, self.pool)

    def put_back(self, count: int) -> None:
        """Put the weakest cards from hand on top of the library - Brainstorm
        (P19 R8). They are drawn again, so the draw itself was only a look."""
        for _ in range(min(count, len(self.hand))):
            worst = self._worst_in_hand()
            self.hand.remove(worst)
            self.library.insert(0, worst)
            self.note(f"  -> puts {worst.name} back on top")

    def make_treasures(self, card) -> None:
        """The Treasure tokens a card makes as it resolves (P19 R7), counted
        ones included: Brass's Bounty's one for each land (P19 R16)."""
        for trigger in card.turn.triggers:
            if trigger.event == RESOLVES:
                self._apply(card, trigger)
        if not card.turn.treasures:
            return
        self._add_treasures(card.turn.treasure_mana, card.turn.treasures)
        self.note(f"  -> {card.turn.treasures} {_token_name(card.turn.treasure_mana)}")

    def _spend_treasures(self, pool: ManaPool) -> None:
        """The Treasures left once a payment is made. Each one gone was
        sacrificed, which Crime Novelist sees (P19 R23)."""
        spent = len(self.treasures) - len(pool.treasures)
        self.treasures = list(pool.treasures)
        for _ in range(spent):
            self.fire(SACRIFICED, TREASURE)

    def _add_treasures(self, mana: str, count: int) -> None:
        """Treasure tokens onto the battlefield, and into an open pool."""
        made = [mana or "WUBRG"] * count
        self.treasures.extend(made)
        if self.pool is not None:
            self.pool.treasures.extend(made)

    # --- Triggers (P19 R16) --------------------------------------------------

    def fire(self, event: str, subject=None, *, gone=None) -> None:
        """Every trigger on the battlefield that ``event`` sets off.

        ``subject`` is what happened: the land that entered, the spell cast,
        the creature that died. ``gone`` is a permanent that has just left and
        still sees its own leaving (Pawn of Ulamog). The order is
        :meth:`_triggers`', so the same game always plays the same way.
        """
        sources = self._triggers(lambda c: any(t.event == event for t in c.turn.triggers))
        if gone is not None and any(t.event == event for t in gone.turn.triggers):
            sources.append(gone)
        for card in sources:
            for trigger in card.turn.triggers:
                if trigger.event == event and self._applies(trigger, card, subject):
                    self._apply(card, trigger)

    def _applies(self, trigger, card, subject) -> bool:
        """Whether ``subject`` passes the trigger's filter."""
        wanted = trigger.filter
        if trigger.event == CAST:
            if wanted == INSTANT_OR_SORCERY:
                return bool(self.types_of(subject) & {"instant", "sorcery"})
            if wanted == COLORLESS_SPELL:
                return not (subject.colors or subject.mana_cost.colors)
            if wanted == SECOND_SPELL:
                return self.spells_this_turn == 2
            return True
        if trigger.event == DIES:
            return not same(subject, card) if wanted == ANOTHER else True
        if trigger.event == ENTERS:
            return not wanted or wanted in subject.creature_types
        return True

    def _apply(self, card, trigger) -> None:
        """What one trigger does."""
        if trigger.life:
            self.life -= trigger.life
        made = []
        if trigger.treasures:
            count = trigger.treasures * (len(self.lands) if trigger.filter == PER_LAND else 1)
            if count:
                self._add_treasures(trigger.treasure_mana, count)
                made.append(f"{count} {_token_name(trigger.treasure_mana)}")
        if trigger.mana and self.in_combat:
            # Lotus Cobra's mana for a land found in combat empties before the
            # second main phase (P19 R22).
            made.append(f"+{trigger.mana}{trigger.mana_color} (lost in combat)")
        elif trigger.mana:
            if self.pool is not None:
                self.pool.add(trigger.mana_color, trigger.mana)
            else:
                self.pending_mana.extend([trigger.mana_color] * trigger.mana)
            made.append(f"+{trigger.mana}{trigger.mana_color}")
        if trigger.draw:
            drawn = self.draw(trigger.draw)
            made.append(f"{len(drawn)} card")
        if trigger.draw_by is not None:
            # v34: Champion of Dusk - a card for each Vampire, a life each.
            count = compile_amount(trigger.draw_by)(self, card)
            drawn = self.draw(count)
            if trigger.lose_drawn:
                self.life -= count
            made.append(f"{len(drawn)} card" + (f", {count} life" if trigger.lose_drawn else ""))
        if trigger.counter:
            # Crime Novelist grows with each artifact it sees go (P19 R23).
            self.add_counters(card, trigger.counter)
        made.extend(["connives"] * self.connive(card, trigger.connive))
        if made:
            self.note(f"  -> {card.name}: {', '.join(made)}")

    def connive(self, card, times: int) -> int:
        """``card`` connives ``times`` times: a loot that grows it when the
        discard is no land (card model PR 19). How many times it did."""
        for _ in range(times):
            self.draw(1)
            if any(not discarded.is_land for discarded in self.discard(1)):
                self.add_counters(card, 1)
        return times

    def spell_cast(self, card) -> None:
        """A spell was cast: count it, and fire what that sets off. A card
        exiled from the hand for its mana (a Spirit Guide) was not cast."""
        if card.play.exiled:
            return
        self.spells_this_turn += 1
        if "R" in self._colors_of(card):
            for permanent in self.battlefield:
                store = permanent.mana.store
                if store is not None and store.grows == "red_spell":
                    self.add_kept_counter(permanent)
                    self._bank(permanent)
        self._grow_on_cast(card)
        self.fire(CAST, card)

    def make_landers(self, card) -> None:
        """The Lander tokens a card makes as it resolves or enters (P19 R14)."""
        if card.play.landers:
            self.landers += card.play.landers
            self.note(f"  -> {card.play.landers} Lander")

    # --- Activated land searches (P19 R14) ---------------------------------

    def activation_cost(self, card) -> ManaCost | None:
        """What activating ``card``'s land search costs out of the open pool,
        or None when it cannot be activated now. ``None`` for ``card`` is a
        Lander token.

        A land whose cost holds {T} gives up the mana it made: the pool was
        opened with every untapped land in it, so that mana is paid back as
        part of the cost - {C} as generic, a colour as itself, the mana on
        top included. A land that entered tapped this turn cannot pay {T}.
        """
        if card is None:
            return LANDER.cost if self.landers else None
        spec = self.activated(card)
        if spec is None or card not in self.battlefield:
            return None
        cost = spec.cost or ManaCost()
        if spec is card.play.tutor:
            # v34: Moggcatcher, Planar Bridge - {T}, once a turn; a creature
            # not the turn it arrived.
            if card in self.creatures:
                return cost if self.creature_can_tap(card) else None
            return None if self.creature_tapped(card) or card.mana.entries else cost
        if not spec.taps or card not in self.lands:
            if spec.taps and card in self.creatures and not self.creature_can_tap(card):
                return None
            return cost
        own = self._own_mana_as_cost(card)
        return None if own is None else cost.plus(own)

    def _own_mana_as_cost(self, card) -> ManaCost | None:
        """The mana an untapped land put into the pool, as a cost: paid back
        when its {T} goes to an ability instead - {C} as generic, a colour as
        itself, the mana on top included. None when it cannot be: it came in
        tapped, or it made a choice of colours."""
        if not any(each is card for each in self.lands.untapped()):
            return None
        pool = available_mana(self.lands, [card], [], doublers(self.battlefield),
                              ability_of=self.mana_ability,
                              extras=self.mana_extras(per_source=True),
                              otherwise_of=self.plain_mana_ability)
        made = pool.by_color()
        if pool.restricted or any(is_choice(source) for source in made):
            return None
        return ManaCost(pips=tuple(sorted((source, amount) for source, amount in made.items()
                                          if source != COLORLESS)),
                        generic=made.get(COLORLESS, 0))

    def creature_can_tap(self, card) -> bool:
        """A creature's {T} for something other than mana (P19 R15): not the
        turn it arrived, once a turn, and not a mana creature, whose {T} is
        in the pool already."""
        return (not self.arrived_now(card) and not self.creature_tapped(card)
                and not card.mana.entries)

    @staticmethod
    def activated(card):
        """``card``'s search played as an ability - its activated land search,
        or (v34) its activated tutor - or None."""
        search = card.play.search
        if search is not None and search.when == "activate":
            return search
        tutor = card.play.tutor
        return tutor if tutor is not None and tutor.activate else None

    def activation_fodder(self, card):
        """What else ``card``'s activated search costs - ``(land, other)``,
        either None when not asked - or None when it cannot be paid now."""
        spec = card.play.search
        if self.activated(card) is not spec:
            return None, None
        land = other = None
        if spec.sacrifices_land:
            lands = [found for found in self.land_fodder() if not same(found, card)
                     and (not spec.land_cost_types or found.subtypes & spec.land_cost_types)]
            if not lands:
                return None
            land = lands[0]
        if spec.sacrifice_other:
            others = [found for found in self.fodder(AdditionalCost(sacrifice=spec.sacrifice_other))
                      if not same(found, card) and not isinstance(found, str)]
            if not others:
                return None
            other = others[0]
        if spec.discard and len(self.hand) < spec.discard:
            return None
        return land, other

    def can_activate(self, card, pool: ManaPool) -> bool:
        """Can ``card``'s land search (None: a Lander) be paid for now?"""
        cost = self.activation_cost(card)
        if cost is None or not pool.can_pay_cost(cost, life=self.life):
            return False
        return card is None or self.activation_fodder(card) is not None

    def activate(self, card, pool: ManaPool, choose=None) -> int:
        """Pay for a land search, sacrifice its source, search. Returns how
        many lands it found. ``card`` None is a Lander token."""
        cost = self.activation_cost(card)
        payment = None if cost is None else pool.pay_cost(cost, life=self.life)
        if payment is None:
            raise ValueError(f"{card.name if card else 'Lander'} cannot be activated from {pool}")
        self.life -= payment.life
        self._spend_treasures(pool)
        if card is None:
            self.landers -= 1
            self.note("Lander (activated)")
            self.fire(SACRIFICED, LANDER_FODDER)
            return self.search_lands(None, choose, LANDER)
        land, other = self.activation_fodder(card)
        spec = card.play.search
        if self.activated(card) is not spec:
            # v34: an activated tutor; the caller searches (`actions.run_tutor`).
            self.tap_creature(card)
            self.note(f"{card.name} (activated)")
            return 0
        if spec.sacrifice:
            self._sacrifice(card)
        elif card in self.creatures:
            self.tap_creature(card)
        self.note(f"{card.name} (activated)")
        for fodder in (land, other):
            if fodder is not None:
                self.sacrifice(fodder)
        if spec.discard:
            self.discard(spec.discard)
        return self.search_lands(card, choose)

    def _sacrifice(self, card) -> None:
        """Put a permanent into the graveyard; of two equal lands, the
        untapped one."""
        if card in self.lands:
            self.lands.pop(len(self.lands) - 1 - self.lands[::-1].index(card))
        elif card in self.rocks:
            self.rocks.pop(len(self.rocks) - 1 - self.rocks[::-1].index(card))
        elif card in self.creatures:
            self.creatures.remove(card)
            if not card.token:
                self.graveyard.append(card)
            self._detach(card)
            self.fire(DIES, card, gone=card)
            if "artifact" in card.types:
                self.fire(SACRIFICED, card)
            return
        else:
            self.other_permanents.remove(card)
            self._detach(card)
        self.graveyard.append(card)
        if card.kind in (ROCK, ARTIFACT) or "artifact" in card.types:
            self.fire(SACRIFICED, card)

    # --- Additional costs and sacrifices (P19 R15) ---------------------------

    def is_mana_source(self, card) -> bool:
        """Does the permanent make mana? Such a one is never sacrificed to
        a cost: a player keeps the ramp - a Lotus Cobra or a Storm-Kiln
        Artist too (P19 R16)."""
        view = card.mana
        return (card.kind == ROCK or bool(view.entries)
                or (view.altar is not None and view.altar.sacrifices)
                or view.tap_other is not None or view.store is not None
                or any(trigger.mana or trigger.treasures for trigger in card.turn.triggers))

    @staticmethod
    def types_of(card) -> frozenset[str]:
        """The card types a sacrifice asks about; a fixture card has only a kind."""
        return card.types or frozenset({"artifact" if card.kind == ROCK else card.kind})

    @staticmethod
    def _fits(card, cost) -> bool:
        """Whether ``card`` may be sacrificed to ``cost``: its types and its
        filter, as one selector."""
        return selects(wanted(cost.sacrifice, cost.sacrifice_filter))(card)

    def fodder(self, cost, pool: ManaPool | None = None, *, also=None) -> list:
        """What may be sacrificed to ``cost``, in the order it is given up.

        A Treasure, then a Lander token; then a permanent that comes back
        (tagged ``recursive`` - its return is not played); then the
        cheapest. Never the commander and never a permanent that makes mana
        - the Treasure aside, a token and first by the user's decision. A
        land only to a cost that asks for one, see :meth:`land_fodder`.
        ``also``: an altar that may sacrifice itself (Skirk Prospector),
        last of all.
        """
        if not cost.sacrifice:
            return []
        found: list = []
        if "artifact" in cost.sacrifice and not cost.sacrifice_filter:
            if has_treasure(pool.treasures if pool is not None else self.treasures):
                found.append(TREASURE)
            if self.landers:
                found.append(LANDER_FODDER)
        commanders = self.deck.commanders
        permanents = [card for card in self.creatures + self.rocks + self.other_permanents
                      if not any(card is each for each in commanders) and not same(card, also)
                      and not self.is_mana_source(card) and self._fits(card, cost)]
        found += sorted(permanents, key=lambda card: (RECURSIVE not in card.engine_roles,
                                                      card.mv, card.name))
        if also is not None and self._fits(also, cost):
            found.append(also)
        if "land" in cost.sacrifice:
            found += self.land_fodder()
        return found

    def land_fodder(self) -> list:
        """The lands a cost may take, the cheapest loss first: one that came
        in tapped (it made nothing this turn), a basic, one whose colours the
        other lands make too. An untapped one is tapped for its mana first,
        so its mana stays in the pool."""
        counts = Counter(color for land in self.lands for color in land_colors(land))
        tapped = {ident(land) for land, permanent
                  in zip(self.lands, self.lands.permanents, strict=True) if permanent.tapped}

        def loss(land):
            shared = min((counts[color] for color in land_colors(land)), default=0)
            return (ident(land) not in tapped, not land.basic, -shared, land.name)

        return sorted(self.lands, key=loss)

    def payment_options(self, card, pool: ManaPool, x: int = 0) -> list[tuple]:
        """Every way ``card``'s additional cost can be paid now, as ``(way,
        fodder)``, the agent's choice first: nothing (X = 0 life), life the
        deck can spare, a sacrifice, a card from the graveyard, a discard,
        more mana, and last life below the floor."""
        base = effective_mana_cost(card, reductions_from(self.battlefield), x)
        others = [other for other in self.hand if other is not card]
        options = []
        for way in card.play.ways:
            cost = base.plus(way.mana) if way.mana is not None else base
            if self.life < way.life or len(others) < way.discard:
                continue
            if way.exile_from_graveyard and not any(
                    way.exile_from_graveyard in self.types_of(other) for other in self.graveyard):
                continue
            life_rank = 0
            if way.life:
                life_rank = 1 if self.life - way.life >= PHYREXIAN_LIFE_FLOOR else 6
            rank = max(life_rank, 2 if way.sacrifice else 0,
                       3 if way.exile_from_graveyard else 0, 4 if way.discard else 0,
                       5 if way.mana is not None else 0)
            life = self.life - way.life
            if not way.sacrifice:
                if pool.can_pay_cost(cost, life=life, spell=card):
                    options.append((rank, way, None))
                continue
            if way.sacrifice_count > 1:
                # Phyrexian Tribute's two creatures, the first in the fodder
                # order: permanents, so no Treasure leaves the pool.
                picked = [found for found in self.fodder(way, pool)
                          if found not in (TREASURE, LANDER_FODDER)][:way.sacrifice_count]
                if len(picked) == way.sacrifice_count and pool.can_pay_cost(
                        cost, life=life, spell=card):
                    options.append((rank, way, tuple(picked)))
                continue
            for fodder in self.fodder(way, pool):
                test = pool
                if fodder == TREASURE:
                    test = pool.copy()
                    sacrifice_treasure(test.treasures)
                if test.can_pay_cost(cost, life=life, spell=card):
                    options.append((rank, way, fodder))
        options.sort(key=lambda option: option[0])
        return [(way, fodder) for _, way, fodder in options]

    def additional_payment(self, card, pool: ManaPool, x: int = 0, pick: int | None = None):
        """How ``card`` is paid for now: ``(way, fodder)`` - ``(None, None)``
        for a card without an additional cost - or None when it cannot be.
        ``pick`` chooses among :meth:`payment_options`; None takes the first."""
        if not card.play.ways:
            cost = effective_mana_cost(card, reductions_from(self.battlefield), x)
            return (None, None) if pool.can_pay_cost(cost, life=self.life, spell=card) else None
        options = self.payment_options(card, pool, x)
        index = 0 if pick is None else pick
        return options[index] if 0 <= index < len(options) else None

    def _pay_additional(self, way, fodder) -> None:
        """Pay what an additional cost asks beyond mana. A Treasure it
        sacrificed has left the pool already."""
        if way is None:
            return
        if way.life:
            self.life -= way.life
            self.note(f"  -> pays {way.life} life")
        if way.life_x:
            self.note("  -> X = 0: nothing in a goldfish for it to hit")
        if way.discard:
            self.discard(way.discard)
        if way.exile_from_graveyard:
            gone = min((other for other in self.graveyard
                        if way.exile_from_graveyard in self.types_of(other)),
                       key=lambda other: (other.mv, other.name))
            self.graveyard.remove(gone)
            self.exiled.append(gone)
            self.note(f"  -> exiles {gone.name} from the graveyard")
        for each in fodder if isinstance(fodder, tuple) else (fodder,):
            if each is not None:
                self.sacrifice(each)

    def sacrifice(self, fodder) -> None:
        """Sacrifice a permanent or a token to a cost. A Treasure is taken
        out of the pool by whoever pays with it."""
        self.last_sacrificed = fodder
        if fodder == TREASURE:
            self.note("  -> sacrifices a Treasure")
            return
        if fodder == LANDER_FODDER:
            self.landers -= 1
            self.note("  -> sacrifices a Lander")
            self.fire(SACRIFICED, LANDER_FODDER)
            return
        index = next((i for i, land in enumerate(self.lands) if same(land, fodder)), None)
        if index is not None:
            # This land, not an equal one: the one that came in tapped.
            self.graveyard.append(self.lands.pop(index))
        else:
            self._sacrifice(fodder)
        self.note(f"  -> sacrifices {fodder.name}")

    # --- Altars: mana for a sacrifice (P19 R15) -------------------------------

    def altar_cost(self, card) -> ManaCost | None:
        """What using ``card``'s altar costs out of the pool: nothing, or for
        Phyrexian Tower the {C} its {T} already made. None: not now."""
        altar = card.mana.altar
        if altar is None or not altar.sacrifices or card not in self.battlefield:
            return None
        if not altar.taps:
            return ManaCost()
        if card in self.creatures:
            # Engine v33: Priest of Forgotten Gods taps as a creature does.
            return ManaCost() if self.creature_can_tap(card) else None
        if card not in self.lands:
            # An artifact's {T} is not tracked: not used.
            return None
        return self._own_mana_as_cost(card)

    def altar_fodder(self, card, pool: ManaPool) -> list:
        """What ``card``'s altar may sacrifice, in :meth:`fodder` order; the
        altar itself last when it fits (Skirk Prospector is a Goblin)."""
        cost = self.altar_cost(card)
        if cost is None:
            return []
        options = []
        altar = card.mana.altar
        also = None if altar.other else card
        if altar.life and self.life - altar.life < PHYREXIAN_LIFE_FLOOR:
            return []
        found = self.fodder(altar, pool, also=also)
        if len(found) < altar.count:
            return []
        for fodder in found:
            test = pool.copy()
            if fodder == TREASURE:
                sacrifice_treasure(test.treasures)
            if test.can_pay_cost(cost, life=self.life):
                options.append(fodder)
        return options

    def use_altar(self, card, pool: ManaPool, pick: int | None = None) -> None:
        """Sacrifice to ``card``'s altar for its mana; ``pick`` indexes
        :meth:`altar_fodder`, the first when None."""
        options = self.altar_fodder(card, pool)
        index = 0 if pick is None else pick
        if not 0 <= index < len(options):
            raise ValueError(f"{card.name} has nothing to sacrifice")
        altar = card.mana.altar
        # Engine v33: an altar that takes more than one (Priest of Forgotten
        # Gods) takes the picked one first, then the rest in fodder order.
        rest = [other for other in self.fodder(altar, pool, also=None if altar.other else card)
                if other is not options[index]]
        fodders = [options[index], *rest[:altar.count - 1]]
        for fodder in fodders:
            if fodder == TREASURE:
                sacrifice_treasure(pool.treasures)
        pool.pay_cost(self.altar_cost(card), life=self.life)
        self._spend_treasures(pool)
        if altar.taps and card in self.creatures:
            self.tap_creature(card)
        elif altar.taps:
            # Its {T} went to this: the land counts as tapped from now on.
            index = next(i for i, (land, permanent)
                         in enumerate(zip(self.lands, self.lands.permanents, strict=True))
                         if not permanent.tapped and land == card)
            self.lands.tap(index)
        self.note(f"{card.name} (sacrifice for mana)")
        if altar.life:
            self.life -= altar.life
        for fodder in fodders:
            self.sacrifice(fodder)
        pool.add(altar.color, altar.amount)
        if altar.draw:
            self.draw(altar.draw)

    def opponent_has_more_lands(self) -> bool:
        """Does an opponent control more lands than you? On the assumption
        the card page states: each opponent has played a land in each of
        their turns before this one of yours (P19 R14, as in R12)."""
        return max(self.turn - 1, 0) > len(self.lands)

    def take_opening_hand(self) -> None:
        """Draw the opening hand, mulligans included."""
        first = True
        while True:
            self.library = self.deck.shuffled(self.rng)
            self.hand = []
            self.drawn = []
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
        untapped_lands = self._untapped(self.lands.untapped())
        untapped_rocks = self._untapped(self.rocks.untapped())
        dorks = self._untapped(
            [card for card in self.creatures if self.mana_ability(card) is not None]
        ) + self._stored_others()
        pool = available_mana(self.lands, untapped_lands, untapped_rocks + dorks,
                              doublers(self.battlefield), ability_of=self.mana_ability,
                              extras=self.mana_extras(), otherwise_of=self.plain_mana_ability)
        self._add_tap_mana(pool)
        return pool

    def _stored_others(self) -> list:
        """Enchantments that tap for kept counters (Mana Bloom, P19 R20), and
        (v34) a planeswalker's "+1: Add {R}{R}", used once each turn."""
        return self._untapped([card for card in self.other_permanents
                               if (card.mana.store is not None or card.mana.flip
                                   or card.kind == PLANESWALKER)
                               and self.mana_ability(card) is not None])

    def mana_extras(self, *, per_source: bool = False) -> list[Extra]:
        """The mana on top that the permanents in play add (P19 R13).

        ``per_source`` leaves out what an Aura gives its one land: a land put
        onto the battlefield in the middle of the turn is not that land.
        """
        found = []
        for permanent in self.battlefield:
            for ability in permanent.mana.entries:
                if ability.rule == EXTRA:
                    if per_source and ability.subtype in ENCHANTED_SCOPES:
                        continue
                    if ability.color == "chosen":
                        mana, amount = self.chosen_color(permanent, ability), 1
                    else:
                        mana, amount = ability.produces[0] if ability.produces else ("", 1)
                    found.append(Extra(ability.subtype, mana, amount))
                elif ability.rule == MULTIPLY:
                    found.append(Extra("permanent", times=ability.times))
                elif ability.rule == GRANT and ability.subtype == "enchanted" \
                        and not per_source:
                    found.append(Extra("enchanted", fixes=True))
                    if ability.produces[0][1] > 1:
                        # Engine v33: Gift of Paradise's two, played as the
                        # land's one of any colour and one more of any.
                        found.append(Extra("enchanted", choice(COLORS),
                                           ability.produces[0][1] - 1))
                elif ability.rule == GRANT and ability.subtype == "land" and not per_source:
                    # Engine v33: Wrenn and Realmbreaker - every land fixes.
                    found.append(Extra("lands", fixes=True))
        return found

    def deck_colors(self) -> frozenset[str]:
        """The colours the deck's costs ask for: what a colour is chosen from."""
        cards = [*self.deck.library, *self.deck.commanders]
        found = frozenset().union(*(card.mana_cost.colors for card in cards))
        return found & frozenset(COLORS) or frozenset(COLORS)

    def chosen_color(self, card, ability) -> str:
        """The colour a permanent chose as it entered, chosen the first time asked.

        A bonus for lands that make the colour (Caged Sun) takes the colour
        most lands make; a bonus that fixes (Utopia Sprawl) the one fewest
        make. Ties go in WUBRG order.
        """
        if self.chosen_color_of(card) is None:
            granted = granted_subtypes(self.lands)
            made = {color: sum(1 for land in self.lands if color in land_colors(land, granted))
                    for color in COLORS if color in self.deck_colors()}
            most = ability.subtype in CHOSEN_SCOPES
            self.choose_color(card, min(
                made, key=lambda color: (-made[color] if most else made[color],
                                         COLORS.index(color))))
        return self.chosen_color_of(card)

    def wanted_color(self, card) -> str:
        """One colour for "X mana of any one color": the one the hand asks most.

        Sanctum Weaver (P19 R13). The pool cannot hold X mana that must all be
        one colour, chosen later, and X separate choices would be more than
        the card makes. Ties go to the card's own colour, then WUBRG.
        """
        wanted = self.deck_colors()
        asked = {color: sum(other.mana_cost.colored.get(color, 0) for other in self.hand)
                 for color in COLORS if color in wanted}
        return min(asked, key=lambda color: (-asked[color], color not in card.colors,
                                             COLORS.index(color)))

    def _untapped(self, cards) -> list:
        """These permanents, without the ones that stayed tapped from before."""
        return [card for card in cards if not self._held(card)]

    def _held(self, card) -> bool:
        permanent = self.where.get(id(card))
        return permanent is not None and permanent.card is card and permanent.held

    @property
    def tapped_lands(self) -> int:
        """How many lands are tapped."""
        return self.lands.tapped_count

    @property
    def tapped_rocks(self) -> int:
        """How many mana rocks are tapped."""
        return self.rocks.tapped_count

    @property
    def stays_tapped(self) -> list:
        """The permanents that "don't untap during your untap step" and were
        already tapped for mana - Mana Vault after its first turn."""
        return [card for zone in (self.lands, self.rocks, self.creatures,
                                  self.other_permanents)
                for card, permanent in zip(zone, zone.permanents, strict=True)
                if permanent.held]

    def open_pool(self) -> ManaPool:
        """Tap everything for this turn's pool, and remember what stays tapped.

        The engine taps every source once, when the main phase opens. A
        source that does not untap is used then and never again - which is
        the whole of Mana Vault in a goldfish: its upkeep untap costs {4} for
        {C}{C}{C}, and nobody pays that.
        """
        sources = (
            self._untapped(self.lands.untapped())
            + self._untapped(self.rocks.untapped())
            + self._untapped([c for c in self.creatures if self.mana_ability(c) is not None])
            + self._stored_others()
        )
        pool = self.mana()
        self._spend_stores(sources)
        tapped, _ = self.tap_fodder()
        for found in tapped.values():
            for other in found:
                if any(same(other, creature) for creature in self.creatures) \
                        and not self.fodder_tapped(other):
                    # Its {T} went to another card's mana (P19 R19).
                    self.tap_as_fodder(other)
        for card in sources:
            if card.mana.mills and self.mana_ability(card) is not None:
                self.mill(card.mana.mills, card)
        self.fire(MAIN_PHASE)
        for mana in self.pending_mana:
            pool.add(mana)
        self.pending_mana = []
        pool.treasures = list(self.treasures)
        pool.treasure_bonus = self.treasure_bonus()
        for card in sources:
            own = self.mana_ability(card)
            if not card.mana.untaps and own is not None and not own.activation_generic:
                self.where[id(card)].held = True
        return pool

    def can_cast(self, card, pool: ManaPool) -> bool:
        """Is the card playable from this pool, conditions included?"""
        if card.is_land or not card.goldfish_castable:
            return False
        if card.enchants and not self._has_target(card.enchants):
            return False
        if card.needs_creature_in_yard and not any(
                c.kind == CREATURE for c in self.graveyard):
            return False
        if card.needs_creature_on_bf and not self.creatures:
            return False
        discards = card.play.discard_cost
        if discards and len([c for c in self.hand if c is not card]) < discards:
            return False
        if card.mana.mox == DISCARD_LAND and not any(other.is_land for other in self.hand):
            return False
        if card.mana.mox == IMPRINT and not self.imprint_options(card):
            return False
        if card.x_count:
            return self.max_x(card, pool) >= card.x_min and (
                not card.play.ways
                or self.additional_payment(card, pool, card.x_min) is not None)
        return self.additional_payment(card, pool) is not None

    def _has_target(self, enchants: str) -> bool:
        """An Aura's land is there: any land, or one of a type (P19 R13)."""
        if enchants == "land":
            return bool(self.lands)
        if enchants == "creature":
            # P19 R22: Shiny Impetus, Sticky Fingers.
            return bool(self.creatures)
        granted = granted_subtypes(self.lands)
        return any(enchants in subtypes_of(land, granted) for land in self.lands)

    #: More X than this is never tried: the most mana a goldfish makes in a
    #: turn is far below it, and the search stays a loop of a few steps.
    MAX_X = 99

    def max_x(self, card, pool: ManaPool) -> int:
        """The largest X this pool pays for the card, -1 if not even X=0 (P19 R9).

        It tries X = 1, 2, 3 ... and stops at the first it cannot pay.
        """
        reductions = reductions_from(self.battlefield)
        best = -1
        for x in range(min(self.MAX_X, card.x_max or self.MAX_X) + 1):
            if not pool.can_pay_cost(effective_mana_cost(card, reductions, x), life=self.life,
                                     spell=card):
                break
            best = x
        return best

    # --- Playing cards -----------------------------------------------------

    #: A Commander game: three opponents, for "unless you have two or more".
    OPPONENTS = 3
    #: P19 R23: the turn from which an opponent is assumed to control three
    #: creatures (Defense of the Heart): each plays one a turn from their second.
    OPPONENT_CREATURES_FROM = 4

    def untaps_on_entering(self, land) -> bool:
        """Whether a land that would enter tapped meets its condition not to.

        Asked before the land is on the battlefield, so "other lands" are all
        of them. Pure: a shock land's life is paid by :meth:`_enters_tapped`.
        """
        holds = land.play.untapped_holds
        return holds is not None and holds(self, land, True)

    def holds(self, condition, card, *, entering: bool = False) -> bool:
        """Whether a condition about this board is met (P19 R3 and R4).

        ``entering`` means the card is asked about before it is on the
        battlefield - a land deciding whether it enters tapped, a land the
        agent thinks of playing - and so counts itself in. Otherwise it is
        already among the permanents: Temple of the False God is one of the
        five lands it asks for.
        """
        return check(condition)(self, card, entering)

    # --- Tapping other permanents for mana (P19 R19) -------------------------

    def _self_tappers(self) -> list:
        """The untapped sources whose ability taps another permanent besides
        themselves: Springleaf Drum, Survivors' Encampment."""
        sources = (self._untapped(self.lands.untapped())
                   + self._untapped(self.rocks.untapped())
                   + self._untapped(self.creatures))
        return [card for card in sources if _taps_another(card)]

    def tap_fodder(self) -> tuple[dict, list]:
        """Which permanents each card taps for its mana, and what is left.

        Returns ``({ident(card): [permanents]}, left)``. Only a permanent that
        makes no mana of its own is tapped - a mana creature taps for its
        own - and never one whose {T} went to something else this turn. Each
        is tapped once: the narrowest ask goes first, a legendary creature
        before any creature and a creature before any permanent. A card that
        taps itself as well takes one; Relic of Legends or Urza all that fit.
        Grand Architect may tap itself.
        """
        if not any(card.mana.tap_other is not None or _taps_another(card)
                   for card in self.battlefield):
            return {}, []
        tappers = [card for card in self.battlefield if card.mana.tap_other is not None]
        own = self._self_tappers()
        busy = self.busy()
        left = []
        for card in self.creatures + self.rocks + self.other_permanents:
            if self.is_mana_source(card):
                continue
            if busy[self.pid_of(card)]:
                busy[self.pid_of(card)] -= 1
                continue
            left.append(card)
        asks = []
        for card in own:
            only_if = card.mana.tap_condition
            selector = wanted(only_if.types, tap_wanted(only_if))
            asks.append((card, selector, True))
        asks += [(card, card.mana.tap_other.selector, False) for card in tappers]

        def order(ask):
            types, wanted_ = wanted_of(ask[1])
            return (not wanted_, PERMANENT in types, not ask[2], ask[0].name)

        asks.sort(key=order)
        found: dict = {}
        for card, selector, one in asks:
            tappable = selects(selector)
            fits = [other for other in left if tappable(other)]
            if not one and tappable(card) and not busy[self.pid_of(card)]:
                # Grand Architect: a blue creature itself.
                fits.append(card)
            per = 1 if one else card.mana.tap_other.per
            # v34: Heritage Druid taps its Elves three at a time.
            taken = fits[:1] if one else fits[:len(fits) // per * per]
            found[ident(card)] = taken
            left = [other for other in left if not any(same(other, each) for each in taken)]
        return found, left

    def _add_tap_mana(self, pool: ManaPool) -> None:
        """Put the mana of Relic of Legends, Urza and the like into the pool."""
        tapped, _ = self.tap_fodder()
        for card in self.battlefield:
            spec = card.mana.tap_other
            if spec is None:
                continue
            count = len(tapped.get(ident(card), ())) // spec.per + spec.tokens
            if not count:
                continue
            if spec.spend:
                pool.restricted.append(RestrictedMana({spec.mana: spec.amount * count},
                                                      spec.spend, source=card.name))
            else:
                pool.add(spec.mana, spec.amount * count)

    def mana_ability(self, card, *, entering: bool = False):
        """The ``FLAT`` ability this card taps for on this board, or None.

        A creature under Cryptolith Rite has its ability as well (P19 R13),
        and taps for its own when that makes more. Summoning sickness needs
        no check: the pool is opened before anything is cast.
        """
        own = self._own_mana_ability(card, entering=entering)
        if card.kind != CREATURE:
            return own
        granted = self._granted_to_creatures(card)
        if granted is None or (own is not None
                               and own.total - own.activation_generic > granted.total):
            return own
        return granted

    def plain_mana_ability(self, card):
        """What ``card`` taps for instead of its restricted ability, or None
        (P19 R17): Cavern of Souls' {C}, Shrine of the Forsaken Gods' {R}."""
        for ability in card.mana.sources:
            if ability.rule == FLAT and not ability.spend_only and (
                    ability.holds is None or ability.holds(self, card)):
                return ability
        return None

    def _granted_to_creatures(self, card=None):
        """The mana ability creatures have from a permanent in play, or None.

        The widest, when there are several: Cryptolith Rite's any colour over
        Citanul Hierophants' {G}. Rishkar's goes only to a creature with a
        counter on it (P19 R21).
        """
        countered = card is not None and bool(self.plus(card))
        best = None
        for permanent in self.battlefield:
            for ability in permanent.mana.entries:
                if ability.rule != GRANT or not (
                        ability.subtype == "creature"
                        or (ability.subtype == "creature:counter" and countered)
                        or (ability.subtype in HOSTED_GRANTS and card is not None
                            and self.is_host_of(card, permanent))
                        or (card is not None and self._of_granted_type(
                            ability.subtype, card, permanent))):
                    continue
                if best is None or len(ability.produces[0][0]) > len(best.produces[0][0]):
                    best = ability
        return None if best is None else flat(best.produces)

    @staticmethod
    def _of_granted_type(subtype: str, card, permanent) -> bool:
        """Engine v33: a grant to creatures of one type - Tyvar Kell's Elves;
        "other" ones leave the granting creature out (Thranduil)."""
        scope, _, kind = subtype.partition(":")
        return (scope == "type" or (scope == "other_type" and not same(card, permanent))) \
            and kind in card.creature_types

    def _own_mana_ability(self, card, *, entering: bool = False):
        """The card's own ``FLAT`` ability on this board, or None.

        The first one whose condition holds: a card lists its conditional
        ability before its plain one (P19 R4), so a Tainted Wood without a
        Swamp falls back to {C}, and Temple of the False God with four lands
        to nothing.
        """
        view = card.mana
        if view.mox == IMPRINT:
            # Chrome Mox (P19 R19): the colours of the card it exiled, or none.
            colors = self.imprinted_on(card)
            return flat({colors: 1}) if colors else None
        if view.store is not None and (stored := self._store_ability(card)) is not None:
            return stored
        if view.flip and not self.has_flipped(card):
            # Growing Rites of Itlimoc: no mana before it turns (P19 R21).
            return None
        counted = None
        for ability in view.sources:
            if ability.rule == COUNTS and ability.amount is None:
                # Bloom Tender: one of each colour among your permanents.
                colors = frozenset().union(*(each.colors for each in self.battlefield))
                if counted is None and colors:
                    counted = flat({color: 1 for color in colors})
                continue
            if ability.rule == COUNTS:
                # The best of a counting ability and the plain one after it:
                # Cabal Stronghold with three basic Swamps nets nothing, and
                # taps for {C} instead (P19 R11).
                amount, color = self.board_count(ability, card)
                if counted is None and amount - ability.activation_generic > 0:
                    counted = flat({color: amount}, ability.activation_generic)
                continue
            if ability.rule == LANDS_COULD_PRODUCE:
                colors = self.lands_could_produce(card)
                if not colors:
                    continue
                # Incubation Druid: three with a +1/+1 counter (P19 R21).
                amount = ability.times if self.plus(card) else 1
                return flat({colors: amount}, ability.activation_generic)
            if ability.rule != FLAT:
                continue
            if ability.holds is None or ability.holds(self, card, entering):
                if counted is not None and (counted.total - counted.activation_generic
                                            >= ability.total - ability.activation_generic):
                    return counted
                return ability
        return counted

    def board_count(self, ability, card) -> tuple[int, str]:
        """How much a counting ability makes on this board, and of what (P19 R11):
        its compiled amount, and its colour - or the one the hand wants."""
        amount = ability.amount
        if amount.kind == "best_devotion":
            return ability.counts(self, card)
        if amount.kind == "all_named":
            return ability.counts(self, card), COLORLESS
        color = ability.color
        if not color and _wants_color(amount):
            color = self.wanted_color(card)
        return ability.counts(self, card), color

    def devotion(self, color: str) -> int:
        """Mana symbols of a colour among the costs of your permanents (Nykthos)."""
        return devotion(self, color)

    def lands_could_produce(self, card) -> str:
        """The mana the other lands could make, as one pool key (P19 R5).

        A choice of their colours - or {C} when they make only colourless -
        and "" when there is nothing to copy. Another Reflecting Pool adds
        nothing: two of them alone make no mana, as on a table.
        """
        granted = granted_subtypes(self.lands)
        found: set[str] = set()
        for land in self.lands:
            if same(land, card) or land.mana.source(LANDS_COULD_PRODUCE) is not None:
                continue
            basic = land_color(land, granted)
            if basic is not None:
                found.update(basic)
                continue
            for ability in land.mana.sources:
                if ability.rule == FLAT and (ability.holds is None
                                             or ability.holds(self, land)):
                    for source, _amount in ability.produces:
                        found.update(source)
        colors = [color for color in COLORS if color in found]
        if colors:
            return choice(colors)
        return COLORLESS if COLORLESS in found else ""

    def _enters_tapped(self, land) -> bool:
        """Whether this land enters tapped, paying a shock land's life if not."""
        if not land.play.enters_tapped:
            return False
        if not self.untaps_on_entering(land):
            return True
        condition = land.play.untapped_if
        if condition.kind == "pay_life":
            self.life -= condition.count
        return False

    def play_land(self, card) -> None:
        """Play a land from hand."""
        self.hand.remove(card)
        tapped = self._enters_tapped(card)
        self.lands.append(card)
        self.land_drop_used = True
        if tapped:
            # Entered tapped: not a mana source this turn.
            self.lands.tap(self.lands.index(card), first=True)
        self.note(f"Land: {card.name}" + (" (tapped)" if tapped else ""))
        if card.mana.store is not None:
            self._store_enters(card)
        self.fire(LANDFALL, card)

    def play_fetch(self, card, choose=None) -> None:
        """A fetch land played this turn: sacrificed at once for what it finds.

        A goldfish has no reason to hold a fetch land back, and "at once" is
        also when its land is most use - untapped, it is in this turn's pool.
        """
        self.lands.remove(card)
        self.graveyard.append(card)
        self.search_lands(card, choose)

    def search_lands(self, card, choose=None, spec=None, x: int = 0) -> int:
        """Carry out ``card.play.search`` (or ``spec``, a Lander's). Returns
        how many lands it found. ``x`` is what X was paid as (Open the Way).

        ``choose(game, options)`` picks each land; without it, the one that
        adds the most colours the lands in play cannot make yet
        (:func:`best_land`). The library is not shuffled afterwards: its order
        is random already, and taking cards out of it does not change that.
        """
        spec = spec or card.play.search
        if spec.condition == "opponent_more_lands" and not self.opponent_has_more_lands():
            self.note("  -> no opponent has more lands")
            return 0
        if spec.lands_at_most and len(self.lands) > spec.lands_at_most:
            # v34: Edge of Autumn.
            self.note(f"  -> more than {spec.lands_at_most} lands")
            return 0
        if spec.reveal:
            return self._reveal_lands(spec, x)
        battlefield = self._lands_searched(spec)
        if spec.sacrifices_land and spec.when == "enters":
            # Springbloom Druid's "you may": only when more lands come back
            # than the one it costs (P19 R15).
            there = sum(1 for land in self.library if self._searchable(land, spec))
            fodder = self.land_fodder()
            if not fodder or min(there, battlefield + spec.hand) < 2:
                self.note("  -> keeps its lands")
                return 0
            self.sacrifice(fodder[0])
        elif spec.sacrifices_land and (fodder := self.land_fodder()):
            # Entish Restoration: the land goes first, whatever comes back
            # (P19 R23).
            self.sacrifice(fodder[0])
        self.life -= spec.life
        found = 0
        each = sorted(spec.types) if spec.each else ()
        shared = None
        for index in range(battlefield + spec.hand):
            options = [land for land in self.library if self._searchable(land, spec)]
            if each:
                if index >= len(each):
                    break
                options = [land for land in options if each[index] in land.subtypes]
            if shared is not None:
                options = [land for land in options if land.subtypes & shared]
            elif spec.share_type:
                # Myriad Landscape: a type there is enough of, where there is one.
                total = battlefield + spec.hand
                plenty = [land for land in options
                          if sum(1 for other in options if other.subtypes & land.subtypes) >= total]
                options = plenty or options
            if not options:
                if each:
                    continue
                break
            land = (choose or best_land)(self, options)
            if spec.share_type and shared is None:
                shared = land.subtypes
            self.library.remove(land)
            found += 1
            if index >= battlefield:
                self.hand.append(land)
                self.note(f"  -> {land.name} to hand")
                continue
            tapped = spec.tapped
            if spec.untap_at and len(self.lands) + 1 >= spec.untap_at:
                tapped = False
            # `tapped` first: a land the search puts in tapped pays no life.
            self._enter_land(land, tapped or self._enters_tapped(land))
        if spec.repeat and found:
            # Claim Jumper: once more, if an opponent still has more lands.
            found += self.search_lands(card, choose, replace(spec, repeat=spec.repeat - 1))
        return found

    @staticmethod
    def _searchable(land, spec) -> bool:
        """Whether a library card is a land ``spec`` may find."""
        if not land.is_land:
            return False
        if spec.also and land.subtypes & spec.also:
            # Circuitous Route's Gates beside the basic lands (P19 R23).
            return True
        return (land.basic or not spec.basic) and (
            not spec.types or bool(land.subtypes & spec.types))

    def _lands_searched(self, spec) -> int:
        """How many lands a search puts onto the battlefield (P19 R23): the
        greatest power among your creatures (Traverse the Outlands), more
        beside a big one (Entish Restoration), one more for each opponent
        assumed to take the offer (Tempt with Discovery)."""
        if spec.by_power:
            return max((self.power(creature) for creature in self.creatures), default=0)
        if spec.power_at and any(self.power(creature) >= spec.power_at
                                 for creature in self.creatures):
            return spec.power_count
        return spec.battlefield + spec.tempting

    def _reveal_lands(self, spec, x: int) -> int:
        """Open the Way: cards off the top until X lands, those onto the
        battlefield, the rest to the bottom in a random order (P19 R23)."""
        lands, rest = [], []
        while self.library and len(lands) < x:
            card = self.library.pop(0)
            (lands if card.is_land else rest).append(card)
        self.rng.shuffle(rest)
        self.library.extend(rest)
        self.note(f"  -> reveals {len(lands) + len(rest)} cards")
        for land in lands:
            self._enter_land(land, spec.tapped or self._enters_tapped(land))
        return len(lands)

    def _enter_land(self, land, tapped: bool) -> None:
        """A land put onto the battlefield, not played: no land drop used.

        Tapped ones join the tapped lands at the front of the list. An
        untapped one in a main phase whose pool is already open is tapped for
        its mana straight away - the pool is opened once a turn, and would
        otherwise never see it.
        """
        # What the board adds is read before the land is on it, as it was.
        tapping = not tapped and self.pool is not None
        board = (doublers(self.battlefield), self.mana_extras(per_source=True)) \
            if tapping else None
        self.lands.append(land)
        if land.mana.store is not None:
            self._store_enters(land)
        if tapping:
            made = available_mana(list(self.lands), [land], [], board[0], extras=board[1],
                                  otherwise_of=self.plain_mana_ability)
            for source, amount in made.by_color().items():
                self.pool.add(source, amount)
            self.pool.restricted.extend(made.restricted)
            tapped = True
        if tapped:
            self.lands.tap(len(self.lands) - 1, first=True)
        self.note(f"  -> {land.name} onto the battlefield" + (" (tapped)" if tapped else ""))
        self.fire(LANDFALL, land)

    def cast(self, card, pool: ManaPool, x: int = 0, pick: int | None = None) -> None:
        """Cast a card and take the cost out of the pool, ``x`` for each {X};
        ``pick`` is how its additional cost is paid (:meth:`payment_options`),
        the agent's choice when None."""
        cost = effective_mana_cost(card, reductions_from(self.battlefield), x)
        colors = self._colors_open(card, pool)
        way, fodder = self.additional_payment(card, pool, x, pick) or (None, None)
        if way is not None and way.mana is not None:
            cost = cost.plus(way.mana)
        if fodder == TREASURE:
            # Sacrificed rather than tapped: its mana leaves the pool first.
            sacrifice_treasure(pool.treasures)
        payment = pool.pay_cost(cost, life=self.life - (way.life if way else 0), spell=card)
        if payment is None:
            raise ValueError(f"{card.name} ({cost}) cannot be paid from {pool}")
        # Phyrexian mana: whatever was not paid with mana is paid with life.
        self.life -= payment.life
        self._spend_treasures(pool)
        if card in self.hand:
            self.hand.remove(card)
        if card.play.discard_cost:
            self.discard(card.play.discard_cost)
        self._pay_additional(way, fodder)
        self.spell_cast(card)
        self._resolve(card, pool, x, colors)
        if card.x_count:
            self.note(f"  -> X = {x}")

    def _resolve(self, card, pool: ManaPool, x: int = 0, colors: int = 0) -> None:
        """Put the card in the right zone and apply its immediate effects.

        ``x`` is what X was paid as, ``colors`` how many colours were open
        for it - what a store's counters start from (P19 R20).
        """
        self._place(card, pool, x)
        if card.mana.store is not None and card.kind != RITUAL:
            self._store_enters(card, x, colors)

    def _place(self, card, pool: ManaPool, x: int = 0) -> None:
        """The card into its zone, a ritual's mana into the pool."""
        if card.kind == RITUAL:
            color, gain = self.ritual_mana(card)
            pool.add(color, gain)
            (self.exiled if card.play.exiled else self.graveyard).append(card)
            self.note(f"{card.name} -> +{gain}{color} mana")
            return
        if card.kind == ROCK:
            # On the battlefield first: what a Chrome Mox imprints is its own.
            self.rocks.append(card)
            self._mox(card)
            if card.play.enters_tapped:
                self.rocks.tap(self.rocks.index(card), first=True)
            self.note(f"{card.name}"
                      + (" (tapped)" if card.play.enters_tapped else ""))
            return
        if card.kind == CREATURE:
            self.creatures.append(card)
            self.mark_arrived(card)
            self.note(f"{card.name}")
            self._creature_enters(card, x)
            self.fire(ENTERS, card)
            if card.turn.treasure_bonus and self.pool is not None:
                self.pool.treasure_bonus = self.treasure_bonus()
            return
        if card.kind in (PLANESWALKER,):
            self.other_permanents.append(card)
            self.note(f"{card.name}")
            return
        if card.kind in (ENCHANTMENT, ARTIFACT):
            self.other_permanents.append(card)
            self.note(f"{card.name}")
            if card.play.search is not None and card.play.search.when == "chapter2":
                # v34: its second chapter comes next turn.
                self.mark_arrived(card)
            if card.turn.growth is not None and card.turn.growth.look:
                self._look_for_creature(card)
            if card.turn.germ:
                self._make_germ(card)
            elif card.enchants == "creature":
                targets = self.attach_targets(card) or list(self.creatures)
                if targets:
                    self.attach(card, targets[0])
                    self.note(f"  -> on {targets[0].name}")
            return
        # Sorcery / instant
        self.graveyard.append(card)
        self.note(f"{card.name}")

    # --- +1/+1 counters and power (P19 R21) ---------------------------------

    def power(self, card) -> int:
        """Its printed power and its +1/+1 counters - no pumps, no anthems."""
        return card.power + self.plus(card)

    def toughness(self, card) -> int:
        """Its printed toughness and its +1/+1 counters."""
        return card.toughness + self.plus(card)

    def _stat_count(self, what: str, card) -> int:
        """Power, toughness or counters, as a counting ability reads them."""
        if what == "power":
            return self.power(card)
        if what == "counters":
            return self.plus(card)
        if what == "power:greatest":
            return max((self.power(each) for each in self.creatures), default=0)
        return max((self.toughness(each) for each in self.creatures if not same(each, card)),
                   default=0)

    def add_counters(self, card, count: int) -> None:
        """+1/+1 counters on a creature - one more for each Kami of Whispered
        Hopes or Hardened Scales."""
        if count <= 0:
            return
        count += sum(each.turn.growth.bonus for each in self.battlefield
                     if each.turn.growth is not None)
        self.set_plus(card, self.plus(card) + count)
        self.note(f"  -> {card.name} gets {count} +1/+1 counter(s)")

    def _creature_enters(self, card, x: int = 0) -> None:
        """The counters a creature arrives with, and what its arrival grows."""
        growth = card.turn.growth
        if growth is not None:
            self.add_counters(card, x if growth.enters_x else growth.enters)
            if growth.puts:
                for target in self.counter_targets(growth.puts):
                    self.add_counters(target, 1)
        for other in list(self.creatures):
            spec = other.turn.growth
            if other is card or spec is None:
                continue
            for grows in spec.grows:
                if grows.startswith("enters:") and grows[7:] in card.creature_types:
                    self.add_counters(other, 1)
                elif grows == "evolve" and (self.power(card) > self.power(other)
                                            or self.toughness(card) > self.toughness(other)):
                    self.add_counters(other, 1)
            if spec.draws_on_greatest and all(
                    self.power(card) > self.power(each)
                    for each in self.creatures if not same(each, card)):
                drawn = self.draw(1)
                self.note(f"  -> {other.name}: {len(drawn)} card")

    def counter_targets(self, count: int) -> list:
        """Where Rishkar's counters go: on the creatures they make the most
        mana on - Incubation Druid's two more first - then the ones that were
        there before this turn."""
        def gain(creature) -> int:
            before = self.mana_ability(creature)
            had = self.plus(creature)
            self.set_plus(creature, had + 1)
            after = self.mana_ability(creature)
            self.set_plus(creature, had)
            return ((after.total if after is not None else 0)
                    - (before.total if before is not None else 0))
        ranked = sorted(self.creatures, key=lambda each: (
            -gain(each), self.arrived_now(each), each.name))
        return ranked[:count]

    def _grow_on_cast(self, spell) -> None:
        """Vivi Ornitier's noncreature spells, Topiary Lecturer's Increment."""
        noncreature = CREATURE not in self.types_of(spell)
        for creature in list(self.creatures):
            spec = creature.turn.growth
            if spec is None:
                continue
            for grows in spec.grows:
                if grows == "cast:noncreature" and noncreature:
                    self.add_counters(creature, 1)
                elif grows == "increment" and spell.mv > min(self.power(creature),
                                                             self.toughness(creature)):
                    self.add_counters(creature, 1)

    def _look_for_creature(self, card) -> None:
        """Growing Rites of Itlimoc: a creature from the top cards into the
        hand - the one of highest mana value - the rest to the bottom."""
        seen = self.library[:card.turn.growth.look]
        del self.library[:card.turn.growth.look]
        creatures = [each for each in seen if CREATURE in self.types_of(each)]
        if creatures:
            found = max(creatures, key=lambda each: (each.mv, each.name))
            seen.remove(found)
            self.hand.append(found)
            self.note(f"  -> {card.name}: {found.name} to hand")
        self.library.extend(seen)

    def can_adapt(self, card, pool: ManaPool) -> bool:
        """Can ``card`` adapt now - no counters yet, and the mana for it?"""
        growth = card.turn.growth
        return (growth is not None and growth.adapt > 0 and growth.adapt_cost is not None
                and any(same(each, card) for each in self.creatures)
                and not self.plus(card)
                and pool.can_pay_cost(growth.adapt_cost, life=self.life))

    def adapt(self, card, pool: ManaPool) -> None:
        """Pay for Adapt and put the counters on (Incubation Druid)."""
        if not self.can_adapt(card, pool):
            raise ValueError(f"{card.name} cannot adapt from {pool}")
        pool.pay_cost(card.turn.growth.adapt_cost, life=self.life)
        self.note(f"{card.name} adapts")
        self.add_counters(card, card.turn.growth.adapt)

    # --- Combat, Equipment and Auras (P19 R22) -------------------------------
    #
    # A goldfish has no blockers. Every creature that can attacks, each turn,
    # and deals its combat damage to a player - the assumption the card list
    # states beside each card it matters for. Only the triggers are played:
    # no life total of an opponent is kept.

    def treasure_bonus(self) -> int:
        """How much more each Treasure makes: Goldspan Dragon's one."""
        return max((card.turn.treasure_bonus for card in self.battlefield), default=0)

    def can_attack(self, card) -> bool:
        """Whether a creature attacks this turn: not the turn it arrived
        unless it has haste, not tapped for something else, no mana ability -
        its {T} is in the pool already - and no defender."""
        return ((not self.arrived_now(card) or card.haste)
                and not self.creature_tapped(card)
                and self.mana_ability(card) is None and not card.defender)

    def attackers(self) -> list:
        """The creatures that attack, in the order they are on the battlefield."""
        return [card for card in self.creatures if self.can_attack(card)]

    def host(self, card):
        """The creature an Equipment or Aura is on, or None."""
        host = self.host_pid(card)
        return None if host is None else self.permanent_by(host, (self.creatures,))

    def modified(self, creature) -> bool:
        """Kodama of the West Tree's "modified": an Equipment, an Aura or a
        +1/+1 counter on it."""
        return bool(self.plus(creature)) or self.carries(creature)

    def deals_damage(self, creature) -> bool:
        """Whether an attacker deals combat damage: it has power, or something
        on it to give it some (a Germ under its Equipment)."""
        return self.power(creature) > 0 or self.carries(creature)

    def combat_plan(self) -> list[tuple]:
        """What this turn's combat would set off: ``(card, trigger, times)``."""
        if self.combat_done:
            return []
        attackers = self.attackers()
        if not attackers:
            return []
        connected = [each for each in attackers if self.deals_damage(each)]
        plan = []
        for card in self._triggers(lambda each: bool(each.turn.combat)):
            for trigger in card.turn.combat:
                times = self._combat_times(card, trigger, attackers, connected)
                if times:
                    plan.append((card, trigger, times))
        return plan

    def _combat_times(self, card, trigger, attackers, connected) -> int:
        """How often ``trigger`` fires in a combat with these attackers."""
        group = attackers if trigger.event == "attacks" else connected
        who = trigger.who
        if who == "self":
            return int(any(same(each, card) for each in group))
        if who in ("equipped", "enchanted"):
            host = self.host(card)
            return int(host is not None and any(same(each, host) for each in group))
        if who == "any":
            return int(any(not trigger.types or each.creature_types & trigger.types
                           for each in group))
        if who == "modified":
            return sum(1 for each in group if self.modified(each))
        if who == "commander":
            # Each commander that attacks is "your commander" attacking.
            return sum(1 for commander in self.deck.commanders
                       if any(each is commander for each in group))
        if who == "curse":
            return int(bool(attackers))
        if who == "total":
            # Neheb: the life the opponents lost, all of it combat damage.
            return sum(self.power(each) for each in connected)
        return 0

    def combat_expected(self) -> bool:
        """Will this turn's combat bring mana or Treasure? A spell with X waits
        for it, to the second main phase."""
        return any(trigger.mana or trigger.treasures for _, trigger, _ in self.combat_plan())

    def combat(self, choose=None) -> bool:
        """Fight this turn's combat: every creature that can attacks. Returns
        whether it brought mana or Treasure for the second main phase.

        ``choose`` picks a land a search in combat finds, as for any search.
        """
        if self.combat_done:
            return False
        plan = self.combat_plan()
        attackers = self.attackers()
        self.combat_done = True
        if not plan:
            # Nothing rides on it: the log stays as it was before P19 R22.
            for each in attackers:
                self.tap_creature(each)
            return False
        self.note("Combat: " + ", ".join(each.name for each in attackers) + " attack")
        for each in attackers:
            self.tap_creature(each)
        treasures = len(self.treasures)
        gained = False
        self.in_combat = True
        try:
            for card, trigger, times in plan:
                if trigger.who == "total":
                    gained |= self._combat_effect(card, replace(trigger,
                                                                mana=trigger.mana * times))
                    continue
                for _ in range(times):
                    gained |= self._combat_effect(card, trigger, choose)
        finally:
            self.in_combat = False
        return gained or len(self.treasures) > treasures

    def _combat_effect(self, card, trigger, choose=None) -> bool:
        """What one trigger in combat does; whether it added mana."""
        if trigger.connive:
            self.connive(card, trigger.connive)
            self.note(f"  -> {card.name}: connives")
        if trigger.treasures:
            self._add_treasures(trigger.treasure_mana, trigger.treasures)
            self.note(f"  -> {card.name}: {trigger.treasures} Treasure")
        if trigger.search:
            self.note(f"  -> {card.name}: searches for a basic land")
            self.search_lands(card, choose,
                              COMBAT_SEARCH_UNTAPPED if trigger.untapped else COMBAT_SEARCH)
        if trigger.to_hand:
            self.note(f"  -> {card.name}: searches for a basic land to hand")
            self.search_lands(card, choose, COMBAT_SEARCH_TO_HAND)
        if trigger.mana and self.pool is not None:
            for mana in trigger.mana:
                self.pool.add(mana)
            if trigger.who == "total":
                self.note(f"  -> {card.name}: +{len(trigger.mana)}{trigger.mana[:1]} for the "
                          f"combat damage")
            else:
                self.note(f"  -> {card.name}: +{trigger.mana}, kept to the end of the turn")
            return True
        return False

    def _make_germ(self, card) -> None:
        """Living weapon: a 0/0 Germ token, the Equipment on it."""
        germ = Card(name=GERM, mv=0, pips=0, generic=0, kind=CREATURE,
                    types=frozenset({CREATURE}), creature_types=frozenset({"phyrexian", "germ"}),
                    colors=frozenset({"B"}), token=True)
        self.creatures.append(germ)
        self.mark_arrived(germ)
        self.attach(card, germ)
        self.note(f"  -> a {GERM} token, {card.name} on it")
        self._creature_enters(germ)
        self.fire(ENTERS, germ)

    def _detach(self, card) -> None:
        """A permanent left: what was on it falls off - an Aura goes to the
        graveyard - and it is on nothing any more."""
        self.unattach(card)
        for pid, host in list(self.attached.items()):
            if host != self.pid_of(card):
                continue
            del self.attached[pid]
            aura = self.permanent_by(pid, (self.other_permanents,))
            if aura is not None and aura.enchants == "creature":
                self.other_permanents.remove(aura)
                self.graveyard.append(aura)
                self.note(f"  -> {aura.name} goes to the graveyard")

    def _grants_mana(self, card) -> bool:
        """Paradise Mantle: what it is on taps for mana."""
        return any(ability.rule == GRANT and ability.subtype in HOSTED_GRANTS
                   for ability in card.mana.entries)

    def attach_targets(self, card) -> list:
        """The creatures an Equipment or Aura would go on, the best first.

        For a trigger in combat: one that makes no mana and can attack - this
        turn before the next, then one with nothing on it and no counters,
        which is one more modified creature for Kodama of the West Tree. For
        Paradise Mantle: one that makes no mana and has nothing on it, the
        weakest, which gives up the least by tapping for mana instead.
        """
        taken = set(self.attached.values())
        if self._grants_mana(card):
            found = [each for each in self.creatures if self.mana_ability(each) is None
                     and self.pid_of(each) not in taken and not each.turn.combat]
            return sorted(found, key=lambda each: (self.power(each), each.name))
        found = [each for each in self.creatures
                 if self.mana_ability(each) is None and not each.defender]
        return sorted(found, key=lambda each: (
            not ((not self.arrived_now(each) or each.haste)
                 and not self.creature_tapped(each)),
            self.modified(each), each.name))

    def can_equip(self, card, pool: ManaPool) -> bool:
        """Can ``card`` be equipped now: an Equipment on the battlefield on no
        creature, something to put it on, and the mana for it."""
        return (card.turn.equip is not None and any(each is card for each in self.other_permanents)
                and self.host(card) is None and bool(self.attach_targets(card))
                and pool.can_pay_cost(card.turn.equip, life=self.life))

    def equip(self, card, pool: ManaPool, pick: int | None = None) -> None:
        """Pay the equip cost and put ``card`` on a creature; ``pick`` indexes
        :meth:`attach_targets`, the best when None."""
        if not self.can_equip(card, pool):
            raise ValueError(f"{card.name} cannot be equipped from {pool}")
        targets = self.attach_targets(card)
        index = 0 if pick is None else pick
        if not 0 <= index < len(targets):
            raise ValueError(f"{card.name} has no creature {index} to go on")
        pool.pay_cost(card.turn.equip, life=self.life)
        self._spend_treasures(pool)
        self.attach(card, targets[index])
        self.note(f"{card.name} equipped to {targets[index].name}")

    # --- Mana kept as counters, energy or a Food (P19 R20) ------------------

    def _colors_open(self, card, pool: ManaPool) -> int:
        """How many colours Sunburst or Converge may count as ``card`` is cast:
        the colours the pool can make, at most one for each mana it costs."""
        if card.mana.store is None or card.mana.store.start_from != "colors":
            return 0
        made = pool.by_color()
        single = {key for key in made if key in COLORS}
        choices = sum(amount for key, amount in made.items()
                      if len(key) > 1 and set(key) & set(COLORS)) + len(pool.treasures)
        return min(card.mv, len(COLORS), len(single) + choices)

    def _store_enters(self, card, x: int = 0, colors: int = 0) -> None:
        """The counters, energy or Food a permanent arrives with."""
        store = card.mana.store
        count = {"x": x, "kicks": x, "colors": colors}.get(store.start_from, store.start)
        if store.energy:
            self.energy += count
            if count:
                self.note(f"  -> {count} energy")
            return
        self.keep_counters(card, count)
        if count and store.use != STORE_SCALES:
            self.note(f"  -> {card.name} enters with {count} counter(s)")
        self._bank(card)

    def _bank(self, card) -> None:
        """A free store's counters as mana kept for a payment, as a Treasure."""
        store = card.mana.store
        if store.use != STORE_FREE:
            return
        while self.kept_counters(card) >= store.per_use:
            self.keep_counters(card, self.kept_counters(card) - store.per_use)
            made = [Stored(store.mana)] * store.amount
            self.treasures.extend(made)
            if self.pool is not None:
                self.pool.treasures.extend(made)

    def _store_ready(self, card) -> bool:
        """Has a ``STORE_TAP`` permanent what one more use spends? Energy
        is shared: each source in turn takes its share from the player's."""
        store = card.mana.store
        if not store.energy:
            return self.kept_counters(card) >= store.per_use
        users = [each for each in self.battlefield
                 if each.mana.store is not None and each.mana.store.energy]
        index = next((i for i, each in enumerate(users) if same(each, card)), 0)
        return self.energy >= store.per_use * (index + 1)

    def _store_ability(self, card):
        """What the counters make this turn, or None: then the card taps
        for its plain ability, if it has one."""
        store = card.mana.store
        if store.use == STORE_TAP:
            return flat({store.mana: store.amount}) if self._store_ready(card) \
                else None
        if store.use == STORE_SCALES:
            count = self.kept_counters(card) + (1 if store.grows == "use" else 0)
            if count <= 0:
                return None
            return flat({store.mana or self.wanted_color(card): count * store.amount})
        return None

    def _spend_stores(self, sources) -> None:
        """The main phase opens: each store tapped spends its use, and a
        Crystalline Crawler taps to put one more counter on itself."""
        for card in self.battlefield:
            store = card.mana.store
            if store is not None and store.use == STORE_FREE and store.grows == "tap" \
                    and not self.arrived_now(card) and not self.creature_tapped(card):
                self.add_kept_counter(card)
                self._bank(card)
        for card in sources:
            store = card.mana.store
            if store is None:
                continue
            if store.use == STORE_SCALES and store.grows == "use":
                self.add_kept_counter(card)
            if store.use != STORE_TAP or not self._store_ready(card):
                continue
            if store.energy:
                self.energy -= store.per_use
                continue
            self.keep_counters(card, self.kept_counters(card) - store.per_use)
            if self.kept_counters(card) < store.per_use and store.empty:
                self.mark_emptied(card)

    def _clear_emptied(self) -> None:
        """Last turn's empty storage land is sacrificed, Wall of Roots dies,
        Mana Bloom goes back to the hand."""
        for pid in self.emptied:
            for zone in (self.lands, self.rocks, self.creatures, self.other_permanents):
                card = self.permanent_by(pid, (zone,))
                if card is None:
                    continue
                zone.remove(card)
                self._detach(card)
                self.counters.pop(pid, None)
                if card.mana.store.empty == "hand":
                    self.hand.append(card)
                    self.note(f"{card.name} returns to hand")
                else:
                    self.graveyard.append(card)
                    self.note(f"{card.name} " + ("dies" if card.mana.store.empty == "dies"
                                                 else "is sacrificed"))
                break
        self.emptied = []

    # --- Moxen and mill costs (P19 R19) -----------------------------------

    @staticmethod
    def _colors_of(card) -> frozenset[str]:
        """A card's colours; a hand-written fixture card has its cost's."""
        return frozenset(card.colors or card.mana_cost.colors) & frozenset(COLORS)

    def imprint_options(self, card) -> list:
        """What Chrome Mox may exile from the hand: a nonartifact, nonland
        card with a colour - one without would leave it tapping for nothing."""
        return [other for other in self.hand
                if other is not card and not other.is_land
                and ARTIFACT not in self.types_of(other) and self._colors_of(other)]

    def _mox(self, card) -> None:
        """Mox Diamond's land card, Chrome Mox's exiled card (P19 R19).

        Mox Diamond is cast only with a land card in hand, and the land drop
        comes first: it discards a land that would enter tapped if there is
        one. Chrome Mox exiles the card a discard would take - the most
        expensive - and makes its colours.
        """
        if card.mana.mox == DISCARD_LAND:
            lands = [other for other in self.hand if other.is_land]
            if lands:
                land = min(lands, key=lambda other: (not other.play.enters_tapped, other.name))
                self.hand.remove(land)
                self.graveyard.append(land)
                self.note(f"  -> discards {land.name}")
        elif card.mana.mox == IMPRINT:
            options = self.imprint_options(card)
            if options:
                exiled = max(options, key=lambda other: (other.mv, other.name))
                self.hand.remove(exiled)
                self.exiled.append(exiled)
                self.imprint(card, choice(self._colors_of(exiled)))
                self.note(f"  -> exiles {exiled.name}")

    def mill(self, count: int, source=None) -> None:
        """Put the top cards of the library into the graveyard - Millikin's
        cost, paid each time it is tapped for mana."""
        for _ in range(min(count, len(self.library))):
            milled = self.library.pop(0)
            self.graveyard.append(milled)
            self.note(f"  -> {source.name + ': ' if source else ''}mills {milled.name}")

    def ritual_mana(self, card) -> tuple[str, int]:
        """What a ritual adds if it resolves now: a colour and an amount.

        Battle Hymn counts the creatures (P19 R13). High Tide counts the
        Islands in this turn's pool, less one: the pool is opened at once, and
        a player casts High Tide first, with one of them.
        """
        ritual = card.mana.ritual
        if ritual is None:
            return "B", 0
        return ritual.color, ritual.amount(self, card)

    def cast_commander(self, pool: ManaPool, commander=None) -> None:
        """Cast a commander from the command zone, its own tax included.

        The first commander unless another is named: with two (P20 F2b) each
        pays the tax for its own casts, as the rules have it (903.8).
        """
        commander = commander or self.deck.commander
        cost = self._commander_cost(commander)
        payment = pool.pay_cost(cost, life=self.life, spell=commander)
        if payment is None:
            raise ValueError(f"{commander.name} ({cost}) cannot be paid from {pool}")
        self.life -= payment.life
        self._spend_treasures(pool)
        if commander is self.deck.partner:
            self.partner_casts += 1
        else:
            self.commander_casts += 1
        self.spell_cast(commander)
        self.creatures.append(commander)
        self.mark_arrived(commander)
        self.note(f"{commander.name} (commander)")
        self._creature_enters(commander)
        self.fire(ENTERS, commander)

    def _commander_cost(self, commander=None):
        """A commander's cost, its tax and cost reduction included.

        The tax is generic, so it is added to the generic portion *before* a
        cost reduction applies - a medallion takes {1} off the taxed commander
        just as it would off an untaxed one.
        """
        commander = commander or self.deck.commander
        casts = (self.partner_casts if commander is self.deck.partner
                 else self.commander_casts)
        cost = commander.mana_cost
        taxed = cost.with_generic(cost.generic + 2 * casts)
        return taxed.reduced(
            applicable_reduction(taxed, reductions_from(self.battlefield))
        )

    def can_cast_commander(self, pool: ManaPool, commander=None) -> bool:
        """Can a commander - the first, unless named - be paid for out of the command zone?"""
        commander = commander or self.deck.commander
        if commander is None or self.has(commander.name):
            return False
        return pool.can_pay_cost(self._commander_cost(commander), life=self.life,
                                 spell=commander)

    def castable_commanders(self, pool: ManaPool) -> list:
        """The commanders that could be cast now, the cheaper first."""
        found = [commander for commander in self.deck.commanders
                 if self.can_cast_commander(pool, commander)]
        return sorted(found, key=lambda commander: self._commander_cost(commander).mv)

    # --- The table strikes back (v36, the stress test) ------------------------
    #
    # Two named disruptions, played in the opponents' turns on a copy of a game
    # (`simulation.analysis`): a goldfish never meets them, so every number
    # outside the stress test stays what it was.

    def remove_commander(self) -> bool:
        """An opponent destroys your commander: it dies and goes back to the
        command zone, where its tax has grown by two with the cast that put it
        out. The first commander on the battlefield; False with none there."""
        names = {commander.name for commander in self.deck.commanders}
        commander = next((card for card in self.creatures
                          if card.name in names and not card.token), None)
        if commander is None:
            return False
        self._destroyed(commander, names)
        return True

    def board_wipe(self) -> int:
        """Every creature is destroyed at once, tokens and commanders too;
        artifacts, enchantments and lands stay. Returns how many died.

        The creatures with a "dies" trigger die last, so a Blood Artist sees
        the others go, as it would in a real wipe where all die together.
        """
        names = {commander.name for commander in self.deck.commanders}
        dying = sorted(self.creatures,
                       key=lambda card: any(t.event == DIES for t in card.turn.triggers))
        for card in dying:
            self._destroyed(card, names)
        return len(dying)

    def _destroyed(self, card, commanders) -> None:
        """A creature dies: to the graveyard, a token to nowhere, a commander
        to the command zone."""
        self.creatures.remove(card)
        if not card.token and card.name not in commanders:
            self.graveyard.append(card)
        self._detach(card)
        self.note(f"  -> {card.name} is destroyed")
        self.fire(DIES, card, gone=card)

    # --- The turn ----------------------------------------------------------

    def begin_turn(self) -> None:
        """Untap, upkeep triggers, draw."""
        self.turn += 1
        self.note(f"--- Turn {self.turn} ---")
        self._opponents_end_steps()
        self.lands.untap()
        self.rocks.untap()
        self.land_drop_used = False
        # v34: a Saga that arrived last turn reaches its second chapter now.
        chapters = [card for card in self.battlefield if card.play.search is not None
                    and card.play.search.when == "chapter2" and self.arrived_now(card)]
        self.arrived = []
        self.tapped_creatures = []
        self.fodder_used = []
        self.pending_mana = []
        self.spells_this_turn = 0
        self.combat_done = False
        self._clear_emptied()
        if self.turn > 1:
            # The opponents' turns since the last one of yours (P19 R16).
            self.fire(OPPONENT_TURNS)

        if self.turn > 1 or not self.on_the_play:
            skipper = self._first(lambda c: c.turn.skips_draw_step)
            if skipper is not None:
                self.note(f"{skipper.name}: draw step skipped")
            else:
                self.draw(1)
        for card in chapters:
            self.note(f"{card.name}: chapter II")
            self.search_lands(card)

        for card in self._triggers(lambda c: c.turn.upkeep is not None):
            spec = card.turn.upkeep
            drawn = self.draw(spec.draw)
            if spec.life_per_mv:
                self.life -= sum(c.mv for c in drawn)
            else:
                self.life -= spec.life
        self.fire(UPKEEP)

    def _opponents_end_steps(self) -> None:
        """Keeper of the Accord: a search in each opponent's end step since
        your last turn, while that opponent has more lands - assumed, one a
        turn each (P19 R23). Before the untap step: a land found tapped untaps
        with the rest."""
        if self.turn == 1:
            return
        for card in self._triggers(lambda c: c.play.search is not None
                                   and c.play.search.when == "opponents"):
            for _ in range(self.OPPONENTS):
                if not self.search_lands(card):
                    break

    def upkeep_tutors(self) -> list:
        """Defense of the Heart: the cards whose search is due this upkeep,
        once an opponent is assumed to have three creatures (P19 R23)."""
        if self.turn < self.OPPONENT_CREATURES_FROM:
            return []
        return self._triggers(lambda c: c.play.tutor is not None
                              and c.play.tutor.upkeep == OPPONENT_CREATURES)

    def delirium(self) -> bool:
        """Four or more card types among the cards in the graveyard."""
        kinds = set()
        for card in self.graveyard:
            kinds |= self.types_of(card) & DELIRIUM_TYPES
        return len(kinds) >= 4

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
        for card in self.other_permanents:
            if card.mana.flip and not self.has_flipped(card) \
                    and len(self.creatures) >= card.mana.flip:
                # Growing Rites of Itlimoc becomes Itlimoc (P19 R21).
                self.mark_flipped(card)
                self.note(f"{card.name} transforms ({len(self.creatures)} creatures)")
        for card in self._triggers(lambda c: c.turn.end_step is not None):
            spec = card.turn.end_step
            want = max(0, spec.max_hand - len(self.hand))
            affordable = max(0, self.life - spec.life_floor)
            pay = min(want, affordable)
            if pay:
                self.life -= pay
                self.draw(pay)
                self.note(f"{card.name}: {pay} life -> {pay} cards")


def best_land(game, options):
    """The land a search takes when nobody says which: the most new colours.

    New against the colours the lands in play can already make; then the most
    colours at all; then one that can enter untapped; then by name, so the same
    game always finds the same land.
    """
    have = frozenset().union(*(land_colors(land) for land in game.lands))

    def score(land):
        colors = land_colors(land)
        return (-len(colors - have), -len(colors), land.play.enters_tapped, land.name)

    return min(options, key=score)
