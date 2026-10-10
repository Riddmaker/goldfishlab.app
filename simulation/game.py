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

from simulation.cards import (
    ANOTHER,
    ARTIFACT,
    CAST,
    COLORLESS_SPELL,
    COUNTS,
    CREATURE,
    DIES,
    ENCHANTMENT,
    ENTERS,
    EXTRA,
    FLAT,
    GRANT,
    INSTANT_OR_SORCERY,
    LANDER,
    LANDFALL,
    LANDS_COULD_PRODUCE,
    MAIN_PHASE,
    MULTIPLY,
    OPPONENT_TURNS,
    PER_LAND,
    PLANESWALKER,
    RESOLVES,
    RITUAL,
    ROCK,
    SECOND_SPELL,
    UPKEEP,
    AdditionalCost,
    ManaAbility,
)
from simulation.mana import (
    CHOSEN_SCOPES,
    ENCHANTED_SCOPES,
    Extra,
    ManaPool,
    applicable_reduction,
    available_mana,
    doublers,
    effective_mana_cost,
    granted_subtypes,
    land_color,
    land_colors,
    reductions_from,
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

#: The tokens the engine keeps as a count or a list rather than as cards,
#: when one is sacrificed (P19 R15).
TREASURE = "Treasure"
LANDER_FODDER = "Lander"

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
        #: Every card that came into the hand by being drawn - the kept opening
        #: hand and each draw since - and nothing else: not a tutored card, not
        #: a fetched land, not a card milled into the graveyard. What "What you
        #: drew" counts (phase 10 T5.1). A card drawn, put back and drawn again
        #: would count twice; nothing in the engine puts a card back today.
        #: Not part of a stored playtest state, which shows no draw statistics:
        #: a loaded game starts it empty.
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

    def discard(self, count: int) -> None:
        """Discard the weakest cards - the additional cost of Big Score (P19 R7)."""
        for _ in range(min(count, len(self.hand))):
            worst = self._worst_in_hand()
            self.hand.remove(worst)
            self.graveyard.append(worst)
            self.note(f"  -> discards {worst.name}")

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
        for trigger in card.triggers:
            if trigger.event == RESOLVES:
                self._apply(card, trigger)
        if not card.treasures:
            return
        self._add_treasures(card.treasure_mana, card.treasures)
        self.note(f"  -> {card.treasures} Treasure")

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
        sources = self._triggers(lambda c: any(t.event == event for t in c.triggers))
        if gone is not None and any(t.event == event for t in gone.triggers):
            sources.append(gone)
        for card in sources:
            for trigger in card.triggers:
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
            return subject is not card if wanted == ANOTHER else True
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
                made.append(f"{count} Treasure")
        if trigger.mana:
            if self.pool is not None:
                self.pool.add(trigger.mana_color, trigger.mana)
            else:
                self.pending_mana.extend([trigger.mana_color] * trigger.mana)
            made.append(f"+{trigger.mana}{trigger.mana_color}")
        if trigger.draw:
            drawn = self.draw(trigger.draw)
            made.append(f"{len(drawn)} card")
        if made:
            self.note(f"  -> {card.name}: {', '.join(made)}")

    def spell_cast(self, card) -> None:
        """A spell was cast: count it, and fire what that sets off. A card
        exiled from the hand for its mana (a Spirit Guide) was not cast."""
        if card.exiled_on_cast:
            return
        self.spells_this_turn += 1
        self.fire(CAST, card)

    def make_landers(self, card) -> None:
        """The Lander tokens a card makes as it resolves or enters (P19 R14)."""
        if card.landers:
            self.landers += card.landers
            self.note(f"  -> {card.landers} Lander")

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
        spec = card.land_search
        if spec is None or spec.when != "activate" or card not in self.battlefield:
            return None
        cost = spec.cost or ManaCost()
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
        if card not in self.lands[self.tapped_lands:]:
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
        return (card.name not in self.arrived and card.name not in self.tapped_creatures
                and not card.mana_abilities)

    def activation_fodder(self, card):
        """What else ``card``'s activated search costs - ``(land, other)``,
        either None when not asked - or None when it cannot be paid now."""
        spec = card.land_search
        land = other = None
        if spec.sacrifices_land:
            lands = [found for found in self.land_fodder() if found is not card
                     and (not spec.land_cost_types or found.subtypes & spec.land_cost_types)]
            if not lands:
                return None
            land = lands[0]
        if spec.sacrifice_other:
            others = [found for found in self.fodder(AdditionalCost(sacrifice=spec.sacrifice_other))
                      if found is not card and not isinstance(found, str)]
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
        self.treasures = list(pool.treasures)
        if card is None:
            self.landers -= 1
            self.note("Lander (activated)")
            return self.search_lands(None, choose, LANDER)
        land, other = self.activation_fodder(card)
        spec = card.land_search
        if spec.sacrifice:
            self._sacrifice(card)
        elif card in self.creatures:
            self.tapped_creatures.append(card.name)
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
            index = len(self.lands) - 1 - self.lands[::-1].index(card)
            if index < self.tapped_lands:
                self.tapped_lands -= 1
            self.lands.pop(index)
        elif card in self.rocks:
            index = len(self.rocks) - 1 - self.rocks[::-1].index(card)
            if index < self.tapped_rocks:
                self.tapped_rocks -= 1
            self.rocks.pop(index)
        elif card in self.creatures:
            self.creatures.remove(card)
            self.graveyard.append(card)
            self.fire(DIES, card, gone=card)
            return
        else:
            self.other_permanents.remove(card)
        self.graveyard.append(card)

    # --- Additional costs and sacrifices (P19 R15) ---------------------------

    def is_mana_source(self, card) -> bool:
        """Does the permanent make mana? Such a one is never sacrificed to
        a cost: a player keeps the ramp - a Lotus Cobra or a Storm-Kiln
        Artist too (P19 R16)."""
        return (card.kind == ROCK or bool(card.mana_abilities) or card.sacrifice_mana is not None
                or any(trigger.mana or trigger.treasures for trigger in card.triggers))

    @staticmethod
    def types_of(card) -> frozenset[str]:
        """The card types a sacrifice asks about; a fixture card has only a kind."""
        return card.types or frozenset({"artifact" if card.kind == ROCK else card.kind})

    def _fits(self, card, cost) -> bool:
        if not self.types_of(card) & cost.sacrifice:
            return False
        wanted = cost.sacrifice_filter
        if not wanted:
            return True
        if wanted == "legendary":
            return card.legendary
        if wanted in COLORS:
            return wanted in (card.colors or card.mana_cost.colors)
        return wanted in card.creature_types

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
            if pool.treasures if pool is not None else self.treasures:
                found.append(TREASURE)
            if self.landers:
                found.append(LANDER_FODDER)
        commander = self.deck.commander
        permanents = [card for card in self.creatures + self.rocks + self.other_permanents
                      if card is not commander and card is not also
                      and not self.is_mana_source(card) and self._fits(card, cost)]
        found += sorted(permanents, key=lambda card: ("recursive" not in card.tags,
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
        tapped = {id(land) for land in self.lands[:self.tapped_lands]}

        def loss(land):
            shared = min((counts[color] for color in land_colors(land)), default=0)
            return (id(land) not in tapped, not land.basic, -shared, land.name)

        return sorted(self.lands, key=loss)

    def payment_options(self, card, pool: ManaPool, x: int = 0) -> list[tuple]:
        """Every way ``card``'s additional cost can be paid now, as ``(way,
        fodder)``, the agent's choice first: nothing (X = 0 life), life the
        deck can spare, a sacrifice, a card from the graveyard, a discard,
        more mana, and last life below the floor."""
        base = effective_mana_cost(card, reductions_from(self.battlefield), x)
        others = [other for other in self.hand if other is not card]
        options = []
        for way in card.additional_costs:
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
            for fodder in self.fodder(way, pool):
                test = pool
                if fodder == TREASURE:
                    test = pool.copy()
                    test.treasures.pop()
                if test.can_pay_cost(cost, life=life, spell=card):
                    options.append((rank, way, fodder))
        options.sort(key=lambda option: option[0])
        return [(way, fodder) for _, way, fodder in options]

    def additional_payment(self, card, pool: ManaPool, x: int = 0, pick: int | None = None):
        """How ``card`` is paid for now: ``(way, fodder)`` - ``(None, None)``
        for a card without an additional cost - or None when it cannot be.
        ``pick`` chooses among :meth:`payment_options`; None takes the first."""
        if not card.additional_costs:
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
        if fodder is not None:
            self.sacrifice(fodder)

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
            return
        index = next((i for i, land in enumerate(self.lands) if land is fodder), None)
        if index is not None:
            # This land, not an equal one: the one that came in tapped.
            if index < self.tapped_lands:
                self.tapped_lands -= 1
            self.graveyard.append(self.lands.pop(index))
        else:
            self._sacrifice(fodder)
        self.note(f"  -> sacrifices {fodder.name}")

    # --- Altars: mana for a sacrifice (P19 R15) -------------------------------

    def altar_cost(self, card) -> ManaCost | None:
        """What using ``card``'s altar costs out of the pool: nothing, or for
        Phyrexian Tower the {C} its {T} already made. None: not now."""
        if card.sacrifice_mana is None or card not in self.battlefield:
            return None
        if not card.sacrifice_mana_taps:
            return ManaCost()
        if card not in self.lands:
            # A creature's or an artifact's {T} is not tracked: not used.
            return None
        return self._own_mana_as_cost(card)

    def altar_fodder(self, card, pool: ManaPool) -> list:
        """What ``card``'s altar may sacrifice, in :meth:`fodder` order; the
        altar itself last when it fits (Skirk Prospector is a Goblin)."""
        cost = self.altar_cost(card)
        if cost is None:
            return []
        options = []
        also = None if card.sacrifice_mana_other else card
        if card.sacrifice_mana.life and \
                self.life - card.sacrifice_mana.life < PHYREXIAN_LIFE_FLOOR:
            return []
        for fodder in self.fodder(card.sacrifice_mana, pool, also=also):
            test = pool.copy()
            if fodder == TREASURE:
                test.treasures.pop()
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
        fodder = options[index]
        if fodder == TREASURE:
            pool.treasures.pop()
        pool.pay_cost(self.altar_cost(card), life=self.life)
        self.treasures = list(pool.treasures)
        if card.sacrifice_mana_taps:
            # Its {T} went to this: the land counts as tapped from now on.
            index = next(i for i in range(self.tapped_lands, len(self.lands))
                         if self.lands[i] == card)
            self.lands.insert(self.tapped_lands, self.lands.pop(index))
            self.tapped_lands += 1
        self.note(f"{card.name} (sacrifice for mana)")
        if card.sacrifice_mana.life:
            self.life -= card.sacrifice_mana.life
        self.sacrifice(fodder)
        pool.add(card.sacrifice_mana_color, card.sacrifice_mana_amount)

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
        untapped_lands = self._untapped(self.lands[self.tapped_lands:])
        untapped_rocks = self._untapped(self.rocks[self.tapped_rocks:])
        dorks = self._untapped(
            [card for card in self.creatures if self.mana_ability(card) is not None]
        )
        return available_mana(self.lands, untapped_lands, untapped_rocks + dorks,
                              doublers(self.battlefield), ability_of=self.mana_ability,
                              extras=self.mana_extras(), otherwise_of=self.plain_mana_ability)

    def mana_extras(self, *, per_source: bool = False) -> list[Extra]:
        """The mana on top that the permanents in play add (P19 R13).

        ``per_source`` leaves out what an Aura gives its one land: a land put
        onto the battlefield in the middle of the turn is not that land.
        """
        found = []
        for permanent in self.battlefield:
            for ability in permanent.mana_abilities:
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
        return found

    def deck_colors(self) -> frozenset[str]:
        """The colours the deck's costs ask for: what a colour is chosen from."""
        cards = [*self.deck.library, *([self.deck.commander] if self.deck.commander else [])]
        found = frozenset().union(*(card.mana_cost.colors for card in cards))
        return found & frozenset(COLORS) or frozenset(COLORS)

    def chosen_color(self, card, ability) -> str:
        """The colour a permanent chose as it entered, chosen the first time asked.

        A bonus for lands that make the colour (Caged Sun) takes the colour
        most lands make; a bonus that fixes (Utopia Sprawl) the one fewest
        make. Ties go in WUBRG order.
        """
        if card.name not in self.chosen_colors:
            granted = granted_subtypes(self.lands)
            made = {color: sum(1 for land in self.lands if color in land_colors(land, granted))
                    for color in COLORS if color in self.deck_colors()}
            most = ability.subtype in CHOSEN_SCOPES
            self.chosen_colors[card.name] = min(
                made, key=lambda color: (-made[color] if most else made[color],
                                         COLORS.index(color)))
        return self.chosen_colors[card.name]

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
            + self._untapped([c for c in self.creatures if self.mana_ability(c) is not None])
        )
        pool = self.mana()
        self.fire(MAIN_PHASE)
        for key in self.pending_mana:
            pool.add(key)
        self.pending_mana = []
        pool.treasures = list(self.treasures)
        for card in sources:
            flat = self.mana_ability(card)
            if not card.untaps and flat is not None and not flat.activation_generic:
                self.stays_tapped.append(card)
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
        if card.discard_cost and len([c for c in self.hand if c is not card]) < card.discard_cost:
            return False
        if card.x_count:
            return self.max_x(card, pool) >= card.x_min and (
                not card.additional_costs
                or self.additional_payment(card, pool, card.x_min) is not None)
        return self.additional_payment(card, pool) is not None

    def _has_target(self, enchants: str) -> bool:
        """An Aura's land is there: any land, or one of a type (P19 R13)."""
        if enchants == "land":
            return bool(self.lands)
        granted = granted_subtypes(self.lands)
        return any(enchants in subtypes_of(land, granted) for land in self.lands)

    #: More X than this is never tried: the most mana a goldfish makes in a
    #: turn is far below it, and the search stays a loop of a few steps.
    MAX_X = 99

    def max_x(self, card, pool: ManaPool) -> int:
        """The largest X this pool pays for the card, -1 if not even X=0 (P19 R9).

        Forge's AI does the same (``ComputerUtilMana.determineLeftoverMana``):
        it tries X = 1, 2, 3 ... and stops at the first it cannot pay.
        """
        reductions = reductions_from(self.battlefield)
        best = -1
        for x in range(self.MAX_X + 1):
            if not pool.can_pay_cost(effective_mana_cost(card, reductions, x), life=self.life,
                                     spell=card):
                break
            best = x
        return best

    # --- Playing cards -----------------------------------------------------

    #: A Commander game: three opponents, for "unless you have two or more".
    OPPONENTS = 3

    def untaps_on_entering(self, land) -> bool:
        """Whether a land that would enter tapped meets its condition not to.

        Asked before the land is on the battlefield, so "other lands" are all
        of them. Pure: a shock land's life is paid by :meth:`_enters_tapped`.
        """
        condition = land.tapped_unless
        if condition is None:
            return False
        return self.holds(condition, land, entering=True)

    def holds(self, condition, card, *, entering: bool = False) -> bool:
        """Whether a condition about this board is met (P19 R3 and R4).

        ``entering`` means the card is asked about before it is on the
        battlefield - a land deciding whether it enters tapped, a land the
        agent thinks of playing - and so counts itself in. Otherwise it is
        already among the permanents: Temple of the False God is one of the
        five lands it asks for.
        """
        kind = condition.kind
        if kind == "control_type":
            return any(other.subtypes & condition.types for other in self.lands)
        if kind == "lands":
            def matches(other):
                return ((not condition.basic or other.basic)
                        and (not condition.type or condition.type in other.subtypes))

            counted = [other for other in self.lands if matches(other)]
            if entering:
                # Itself only when it is one of them: a battle land is not a
                # basic land, so it needs two others (fixed in P19 R12).
                have = len(counted) + (0 if condition.other or not matches(card) else 1)
            else:
                itself = any(other is card for other in counted)
                have = len(counted) - (1 if condition.other and itself else 0)
            return have >= condition.count if condition.at_least else have <= condition.count
        if kind == "artifacts":
            have = sum(1 for permanent in self.battlefield if ARTIFACT in permanent.types)
            if entering and ARTIFACT in card.types:
                have += 1
            return have >= condition.count
        if kind == "opponents":
            return self.OPPONENTS >= condition.count
        if kind == "permanent":
            return any(permanent.types & condition.types
                       and (permanent.legendary or not condition.legendary)
                       for permanent in self.battlefield if permanent is not card)
        if kind == "turn":
            return self.turn <= condition.count
        if kind == "opponent_lands":
            # An assumption, stated on the card page: each opponent has played
            # a land in each of their turns before this one of yours.
            return self.OPPONENTS * max(self.turn - 1, 0) >= condition.count
        if kind == "life_at_most":
            return self.life <= condition.count
        if kind == "power":
            return any(CREATURE in (permanent.types or {permanent.kind})
                       and permanent.power >= condition.count
                       for permanent in self.battlefield)
        if kind == "reveal":
            return any(other is not card and other.subtypes & condition.types
                       for other in self.hand)
        if kind == "pay_life":
            return self.life - condition.count >= PHYREXIAN_LIFE_FLOOR
        return False

    def mana_ability(self, card, *, entering: bool = False):
        """The ``FLAT`` ability this card taps for on this board, or None.

        A creature under Cryptolith Rite has its ability as well (P19 R13),
        and taps for its own when that makes more. Summoning sickness needs
        no check: the pool is opened before anything is cast.
        """
        own = self._own_mana_ability(card, entering=entering)
        if card.kind != CREATURE:
            return own
        granted = self._granted_to_creatures()
        if granted is None or (own is not None
                               and own.total - own.activation_generic > granted.total):
            return own
        return granted

    def plain_mana_ability(self, card):
        """What ``card`` taps for instead of its restricted ability, or None
        (P19 R17): Cavern of Souls' {C}, Shrine of the Forsaken Gods' {R}."""
        for ability in card.mana_abilities:
            if ability.rule == FLAT and not ability.spend_only and (
                    ability.only_if is None or self.holds(ability.only_if, card)):
                return ability
        return None

    def _granted_to_creatures(self):
        """The mana ability creatures have from a permanent in play, or None.

        The widest, when there are several: Cryptolith Rite's any colour over
        Citanul Hierophants' {G}.
        """
        best = None
        for permanent in self.battlefield:
            for ability in permanent.mana_abilities:
                if ability.rule == GRANT and ability.subtype == "creature" and (
                        best is None or len(ability.produces[0][0]) > len(best.produces[0][0])):
                    best = ability
        return None if best is None else ManaAbility(FLAT, best.produces)

    def _own_mana_ability(self, card, *, entering: bool = False):
        """The card's own ``FLAT`` ability on this board, or None.

        The first one whose condition holds: a card lists its conditional
        ability before its plain one (P19 R4), so a Tainted Wood without a
        Swamp falls back to {C}, and Temple of the False God with four lands
        to nothing.
        """
        counted = None
        for ability in card.mana_abilities:
            if ability.rule == COUNTS and ability.subtype == "colors_among":
                # Bloom Tender: one of each colour among your permanents.
                colors = frozenset().union(*(each.colors for each in self.battlefield))
                if counted is None and colors:
                    counted = ManaAbility(FLAT, {color: 1 for color in colors})
                continue
            if ability.rule == COUNTS:
                # The best of a counting ability and the plain one after it:
                # Cabal Stronghold with three basic Swamps nets nothing, and
                # taps for {C} instead (P19 R11).
                amount, color = self.board_count(ability, card)
                if counted is None and amount - ability.activation_generic > 0:
                    counted = ManaAbility(FLAT, {color: amount},
                                          activation_generic=ability.activation_generic)
                continue
            if ability.rule == LANDS_COULD_PRODUCE:
                colors = self.lands_could_produce(card)
                if not colors:
                    continue
                return ManaAbility(FLAT, {colors: 1},
                                   activation_generic=ability.activation_generic)
            if ability.rule != FLAT:
                continue
            if ability.only_if is None or self.holds(ability.only_if, card, entering=entering):
                if counted is not None and (counted.total - counted.activation_generic
                                            >= ability.total - ability.activation_generic):
                    return counted
                return ability
        return counted

    def board_count(self, ability, card) -> tuple[int, str]:
        """How much a counting ability makes on this board, and of what (P19 R11)."""
        what, color = ability.subtype, ability.color
        if what == "creature":
            return len(self.creatures), color
        if what.startswith("basic:"):
            kind = what.split(":", 1)[1]
            return sum(1 for land in self.lands
                       if (land.basic and kind in land.subtypes)
                       or (kind == "swamp" and land.is_swamp)), color
        if what.startswith("graveyard:"):
            wanted = what.split(":", 1)[1]
            return sum(1 for found in self.graveyard
                       if CREATURE in (found.types or {found.kind})
                       and wanted in found.mana_cost.colors), color
        if what == "devotion":
            devotion = {each: self.devotion(each) for each in COLORS}
            best = max(COLORS, key=lambda each: (devotion[each], each == color))
            return devotion[best], best
        if what == "enchantment":
            return (sum(1 for each in self.battlefield if ENCHANTMENT in each.types),
                    color or self.wanted_color(card))
        if what == "creature:defender":
            return sum(1 for creature in self.creatures if creature.defender), color
        if what.startswith("names:"):
            needed = set(what.split(":", 1)[1].split("|"))
            here = {land.name for land in self.lands if land is not card}
            full = sum(amount for _, amount in ability.produces)
            return (full if needed <= here else 0), COLORLESS
        # An Elf, or the deck's chosen type (Three Tree City, P19 R18): of
        # one colour, the one the hand wants most when the text lets it choose.
        return (sum(1 for creature in self.creatures if what in creature.creature_types),
                color or self.wanted_color(card))

    def devotion(self, color: str) -> int:
        """Mana symbols of a colour among the costs of your permanents (Nykthos)."""
        total = 0
        for permanent in self.battlefield:
            if permanent.is_land:
                continue
            cost = permanent.mana_cost
            total += cost.colored.get(color, 0)
            total += sum(1 for symbol in cost.hybrid if color in symbol.colors)
            total += sum(1 for symbol in cost.phyrexian if symbol == color)
        return total

    def lands_could_produce(self, card) -> str:
        """The mana the other lands could make, as one pool key (P19 R5).

        A choice of their colours - or {C} when they make only colourless -
        and "" when there is nothing to copy. Another Reflecting Pool adds
        nothing: two of them alone make no mana, as on a table.
        """
        granted = granted_subtypes(self.lands)
        found: set[str] = set()
        for land in self.lands:
            if land is card or any(a.rule == LANDS_COULD_PRODUCE for a in land.mana_abilities):
                continue
            basic = land_color(land, granted)
            if basic is not None:
                found.update(basic)
                continue
            for ability in land.mana_abilities:
                if ability.rule == FLAT and (ability.only_if is None
                                             or self.holds(ability.only_if, land)):
                    for source, _amount in ability.produces:
                        found.update(source)
        colors = [color for color in COLORS if color in found]
        if colors:
            return choice(colors)
        return COLORLESS if COLORLESS in found else ""

    def _enters_tapped(self, land) -> bool:
        """Whether this land enters tapped, paying a shock land's life if not."""
        if not land.enters_tapped:
            return False
        if not self.untaps_on_entering(land):
            return True
        if land.tapped_unless.kind == "pay_life":
            self.life -= land.tapped_unless.count
        return False

    def play_land(self, card) -> None:
        """Play a land from hand."""
        self.hand.remove(card)
        tapped = self._enters_tapped(card)
        self.lands.append(card)
        self.land_drop_used = True
        if tapped:
            # Entered tapped: not a mana source this turn.
            self.lands.remove(card)
            self.lands.insert(0, card)
            self.tapped_lands += 1
        self.note(f"Land: {card.name}" + (" (tapped)" if tapped else ""))
        self.fire(LANDFALL, card)

    def play_fetch(self, card, choose=None) -> None:
        """A fetch land played this turn: sacrificed at once for what it finds.

        A goldfish has no reason to hold a fetch land back, and "at once" is
        also when its land is most use - untapped, it is in this turn's pool.
        """
        index = self.lands.index(card)
        if index < self.tapped_lands:
            self.tapped_lands -= 1
        self.lands.pop(index)
        self.graveyard.append(card)
        self.search_lands(card, choose)

    def search_lands(self, card, choose=None, spec=None) -> int:
        """Carry out ``card.land_search`` (or ``spec``, a Lander's). Returns
        how many lands it found.

        ``choose(game, options)`` picks each land; without it, the one that
        adds the most colours the lands in play cannot make yet
        (:func:`best_land`). The library is not shuffled afterwards: its order
        is random already, and taking cards out of it does not change that.
        """
        spec = spec or card.land_search
        if spec.condition == "opponent_more_lands" and not self.opponent_has_more_lands():
            self.note("  -> no opponent has more lands")
            return 0
        if spec.sacrifices_land and spec.when == "enters":
            # Springbloom Druid's "you may": only when more lands come back
            # than the one it costs (P19 R15).
            there = sum(1 for land in self.library if land.is_land
                        and (land.basic or not spec.basic)
                        and (not spec.types or land.subtypes & spec.types))
            fodder = self.land_fodder()
            if not fodder or min(there, spec.battlefield + spec.hand) < 2:
                self.note("  -> keeps its lands")
                return 0
            self.sacrifice(fodder[0])
        self.life -= spec.life
        found = 0
        each = sorted(spec.types) if spec.each else ()
        shared = None
        for index in range(spec.battlefield + spec.hand):
            options = [land for land in self.library if land.is_land
                       and (land.basic or not spec.basic)
                       and (not spec.types or land.subtypes & spec.types)]
            if each:
                if index >= len(each):
                    break
                options = [land for land in options if each[index] in land.subtypes]
            if shared is not None:
                options = [land for land in options if land.subtypes & shared]
            elif spec.share_type:
                # Myriad Landscape: a type there is enough of, where there is one.
                total = spec.battlefield + spec.hand
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
            if index >= spec.battlefield:
                self.hand.append(land)
                self.note(f"  -> {land.name} to hand")
                continue
            tapped = spec.tapped
            if spec.untap_at and len(self.lands) + 1 >= spec.untap_at:
                tapped = False
            # `tapped` first: a land the search puts in tapped pays no life.
            self._enter_land(land, tapped or self._enters_tapped(land))
        return found

    def _enter_land(self, land, tapped: bool) -> None:
        """A land put onto the battlefield, not played: no land drop used.

        Tapped ones join the tapped lands at the front of the list. An
        untapped one in a main phase whose pool is already open is tapped for
        its mana straight away - the pool is opened once a turn, and would
        otherwise never see it.
        """
        if not tapped and self.pool is not None:
            made = available_mana(self.lands + [land], [land], [], doublers(self.battlefield),
                                  extras=self.mana_extras(per_source=True),
                                  otherwise_of=self.plain_mana_ability)
            for source, amount in made.by_color().items():
                self.pool.add(source, amount)
            self.pool.restricted.extend(made.restricted)
            tapped = True
        if tapped:
            self.lands.insert(0, land)
            self.tapped_lands += 1
        else:
            self.lands.append(land)
        self.note(f"  -> {land.name} onto the battlefield" + (" (tapped)" if tapped else ""))
        self.fire(LANDFALL, land)

    def cast(self, card, pool: ManaPool, x: int = 0, pick: int | None = None) -> None:
        """Cast a card and take the cost out of the pool, ``x`` for each {X};
        ``pick`` is how its additional cost is paid (:meth:`payment_options`),
        the agent's choice when None."""
        cost = effective_mana_cost(card, reductions_from(self.battlefield), x)
        way, fodder = self.additional_payment(card, pool, x, pick) or (None, None)
        if way is not None and way.mana is not None:
            cost = cost.plus(way.mana)
        if fodder == TREASURE:
            # Sacrificed rather than tapped: its mana leaves the pool first.
            pool.treasures.pop()
        payment = pool.pay_cost(cost, life=self.life - (way.life if way else 0), spell=card)
        if payment is None:
            raise ValueError(f"{card.name} ({cost}) cannot be paid from {pool}")
        # Phyrexian mana: whatever was not paid with mana is paid with life.
        self.life -= payment.life
        self.treasures = list(pool.treasures)
        if card in self.hand:
            self.hand.remove(card)
        if card.discard_cost:
            self.discard(card.discard_cost)
        self._pay_additional(way, fodder)
        self.spell_cast(card)
        self._resolve(card, pool)
        if card.x_count:
            self.note(f"  -> X = {x}")

    def _resolve(self, card, pool: ManaPool) -> None:
        """Put the card in the right zone and apply its immediate effects."""
        if card.kind == RITUAL:
            color, gain = self.ritual_mana(card)
            pool.add(color, gain)
            (self.exiled if card.exiled_on_cast else self.graveyard).append(card)
            self.note(f"{card.name} -> +{gain}{color} mana")
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
            self.arrived.append(card.name)
            self.note(f"{card.name}")
            self.fire(ENTERS, card)
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

    def ritual_mana(self, card) -> tuple[str, int]:
        """What a ritual adds if it resolves now: a colour and an amount.

        Battle Hymn counts the creatures (P19 R13). High Tide counts the
        Islands in this turn's pool, less one: the pool is opened at once, and
        a player casts High Tide first, with one of them.
        """
        what = card.ritual_counts
        if what == "creature":
            return card.ritual_color, len(self.creatures)
        if what.startswith("tapped:"):
            wanted = what.split(":", 1)[1]
            granted = granted_subtypes(self.lands)
            lands = self._untapped(self.lands[self.tapped_lands:])
            found = sum(1 for land in lands if wanted in subtypes_of(land, granted))
            return card.ritual_color, max(found - 1, 0)
        if what.startswith("named:"):
            # Rite of Flame: one more for each copy already resolved.
            named = what.split(":", 1)[1]
            return card.ritual_color, card.ritual_gain + sum(
                1 for found in self.graveyard if found.name == named)
        if what.startswith("threshold:"):
            # Cabal Ritual: more instead, once the graveyard holds enough.
            _, needed, more = what.split(":")
            if len(self.graveyard) >= int(needed):
                return card.ritual_color, int(more)
        return card.ritual_color, card.ritual_gain

    def cast_commander(self, pool: ManaPool) -> None:
        """Cast the commander from the command zone, commander tax included."""
        commander = self.deck.commander
        cost = self._commander_cost()
        payment = pool.pay_cost(cost, life=self.life, spell=commander)
        if payment is None:
            raise ValueError(f"{commander.name} ({cost}) cannot be paid from {pool}")
        self.life -= payment.life
        self.treasures = list(pool.treasures)
        self.commander_casts += 1
        self.spell_cast(commander)
        self.creatures.append(commander)
        self.arrived.append(commander.name)
        self.note(f"{commander.name} (commander)")
        self.fire(ENTERS, commander)

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
        return pool.can_pay_cost(self._commander_cost(), life=self.life, spell=commander)

    # --- The turn ----------------------------------------------------------

    def begin_turn(self) -> None:
        """Untap, upkeep triggers, draw."""
        self.turn += 1
        self.tapped_lands = 0      # untap
        self.tapped_rocks = 0
        self.land_drop_used = False
        self.arrived = []
        self.tapped_creatures = []
        self.pending_mana = []
        self.spells_this_turn = 0
        self.note(f"--- Turn {self.turn} ---")
        if self.turn > 1:
            # The opponents' turns since the last one of yours (P19 R16).
            self.fire(OPPONENT_TURNS)

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
        self.fire(UPKEEP)

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


def best_land(game, options):
    """The land a search takes when nobody says which: the most new colours.

    New against the colours the lands in play can already make; then the most
    colours at all; then one that can enter untapped; then by name, so the same
    game always finds the same land.
    """
    have = frozenset().union(*(land_colors(land) for land in game.lands))

    def score(land):
        colors = land_colors(land)
        return (-len(colors - have), -len(colors), land.enters_tapped, land.name)

    return min(options, key=score)
