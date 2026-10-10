"""The closed set of things that can happen in a game.

Until Phase 5 the agent both *decided* what to do and *did* it, inside
:func:`simulation.agent.take_turn`. That is fine for a simulation nobody
watches and useless for a playtest, where a human decides and the same
mechanics have to run.

This module is the seam. It holds a small, closed set of ``Action`` records and
one :func:`apply` that carries any of them out. The agent now chooses from
:func:`legal_actions` and calls :func:`apply`; the playtest UI will offer the
same list to a person and call the same function. **Both drive identical code**,
which is the whole reason for the split - two implementations of "may I cast
this" would have diverged by the second sprint.

An action names a card by its **index in a zone**, never by object. That is what
lets a stored action be a row in Postgres and a replayed game be the seeded
shuffle put back through N of them. It is also exactly equivalent to what the
engine did before: :class:`simulation.cards.Card` is a frozen dataclass, so two
Swamps compare equal, and ``hand.remove(card)`` already removed the first equal
one rather than a particular object.

**Legality here is advisory, on purpose.** :func:`legal_actions` reports what
the rules allow so the UI can highlight it; :func:`apply` will still carry out a
:class:`MoveCard` from anywhere to anywhere, because full manual control is what
a paper playtest gives you and the alternative is an infinite rules-engine
rabbit hole. The user is the judge.
"""

from dataclasses import asdict, dataclass

from simulation.mana import effective_mana_cost, reductions_from

# --- Phases ----------------------------------------------------------------
#
# A goldfish has no opponent, so combat and main2 carry no mechanics. They are
# here anyway, because a person reading their own board expects them and the
# playtest lets a card be moved by hand at any point in the turn.
#
# **Untap and upkeep are deliberately not phases.** The Phase 5 outline listed
# all seven, but the engine resolves untap, the turn's draw and the upkeep
# triggers together in ``Game.begin_turn`` - and in that order, which is not
# real Magic's. Every published number rests on it, so the turn starts at
# ``DRAW`` and two constants that no game state could ever hold do not get
# defined. Unreachable constants are how four of the engine's five land types
# stayed broken for six phases; see docs/phases/test-decks.md.

DRAW = "draw"
MAIN1 = "main1"
COMBAT = "combat"
MAIN2 = "main2"
END = "end"

#: The turn, in order. ``None`` means the game has not started.
PHASES = (DRAW, MAIN1, COMBAT, MAIN2, END)

# --- Zones -----------------------------------------------------------------

HAND = "hand"
LIBRARY = "library"
GRAVEYARD = "graveyard"
EXILED = "exiled"
LANDS = "lands"
ROCKS = "rocks"
CREATURES = "creatures"
OTHER = "other_permanents"

#: Every zone a card can be moved between. The command zone is not one of them:
#: the commander is a property of the deck, not a card in a list.
ZONES = (HAND, LIBRARY, GRAVEYARD, EXILED, LANDS, ROCKS, CREATURES, OTHER)


class IllegalAction(ValueError):
    """An action that could not be carried out at all.

    Not the same as an *unwise* one. This is raised for an index that is not in
    the zone or a zone that does not exist - a bug or a stale form post, never a
    judgement about how the deck should be played.
    """


def zone_of(game, name: str) -> list:
    """The list behind a zone name."""
    if name not in ZONES:
        raise IllegalAction(f"no such zone: {name!r}")
    return getattr(game, name)


# --- The actions -----------------------------------------------------------
#
# Every action is frozen, carries a ``kind`` that is stable across releases
# (it is written to the database), and knows how to carry itself out. The
# module-level ``apply`` is what callers use.


@dataclass(frozen=True)
class Action:
    """Base class. Subclasses implement ``run``."""

    #: Stable identifier, stored in ``PlaytestAction.kind``. Never renamed.
    kind = "action"

    def run(self, game, policy) -> None:
        raise NotImplementedError

    def payload(self) -> dict:
        """The action's fields, as something JSON can hold."""
        return asdict(self)


@dataclass(frozen=True)
class BeginTurn(Action):
    """Untap, draw for the turn, then the upkeep triggers - all at once.

    **The draw happens before the upkeep triggers**, which is not the order
    real Magic uses. That is how the engine has always worked and every
    published number rests on it: with Phyrexian Arena and Dark Confidant both
    drawing, the order decides which card goes where and moves everything
    downstream. Changing it is a deliberate decision with its own snapshot
    diff, not something to slip into a refactor - which is why the turn simply
    starts at :data:`DRAW` rather than pretending to step through two phases
    whose work has already happened.
    """

    kind = "begin_turn"

    def run(self, game, policy) -> None:
        game.begin_turn()
        # Mana does not survive the turn it was made in. The agent never
        # noticed, because it reopens the pool at every main phase - but a
        # human who taps out on turn one and spends nothing would otherwise
        # still be holding that mana on turn three. Found by looking at a
        # screenshot, which is the only place it was visible.
        game.pool = None
        game.phase = DRAW


@dataclass(frozen=True)
class PlayLand(Action):
    """Play the land at ``index`` in hand."""

    kind = "play_land"
    index: int = 0

    def run(self, game, policy) -> None:
        card = _at(game, HAND, self.index)
        game.play_land(card)
        if card.land_search is not None and card.land_search.when == "play":
            game.play_fetch(card, _land_chooser(policy))


@dataclass(frozen=True)
class OpenMainPhase(Action):
    """Tap everything and record what the turn had to spend.

    The pool is opened once and then floats for the rest of the turn, which is
    how the engine has always modelled mana: it does not tap lands one at a
    time. ``mana_available`` is recorded here, before anything is cast, or a
    Crypt Ghast cast this turn would count himself.
    """

    kind = "open_main"

    def run(self, game, policy) -> None:
        pool = game.open_pool()
        game.pool = pool
        game.mana_available = pool.total
        game.mana_by_color = pool.reach()
        game.phase = MAIN1


@dataclass(frozen=True)
class CastSpell(Action):
    """Cast the card at ``index`` in hand, paying out of the floating pool."""

    kind = "cast_spell"
    index: int = 0
    #: What X is paid, for a card with {X} in its cost (P19 R9). None is a
    #: game recorded before X was paid at all, and replays as it was played:
    #: X = 0. The agent and the board both say what X is.
    x: int | None = None
    #: How its additional cost is paid, an index into
    #: ``Game.payment_options`` (P19 R15): what to sacrifice, or life rather
    #: than a discard. None: the agent's choice, the first.
    payment: int | None = None

    def run(self, game, policy) -> None:
        card = _at(game, HAND, self.index)
        pool = _pool(game)
        x = (self.x or 0) if card.x_count else 0
        if x < 0:
            raise IllegalAction(f"X cannot be {x}")
        # Payment is the one thing checked here, and it is mechanics rather
        # than legality: a cast nobody can pay for is not possible on a kitchen
        # table either. Everything else - no target, a missing creature - stays
        # the player's call. Without this the board, which offers a Cast button
        # on every card, answered an unaffordable one with a ValueError and an
        # HTTP 500 that htmx then swallowed in silence.
        cost = castable_cost(game, card, x)
        if game.additional_payment(card, pool, x, self.payment) is None:
            if card.additional_costs and pool.can_pay_cost(cost, life=game.life):
                raise IllegalAction(f"{card.name}'s additional cost cannot be paid now")
            raise IllegalAction(f"{card.name} costs {cost}; the floating mana is {pool}")
        game.cast(card, pool, x, self.payment)
        _apply_cast_effect(game, card, policy, x)


@dataclass(frozen=True)
class CastCommander(Action):
    """Cast the commander out of the command zone, tax included."""

    kind = "cast_commander"

    def run(self, game, policy) -> None:
        pool = _pool(game)
        commander = game.deck.commander
        if commander is None:
            raise IllegalAction("this deck has no commander")
        if not game.can_cast_commander(pool):
            if game.has(commander.name):
                raise IllegalAction(f"{commander.name} is already on the battlefield")
            raise IllegalAction(
                f"{commander.name} costs {game._commander_cost()} with tax; "
                f"the floating mana is {pool}"
            )
        game.cast_commander(pool)


@dataclass(frozen=True)
class ActivateSearch(Action):
    """Activate the land search of the permanent at ``index`` in ``zone``:
    Wayfarer's Bauble, Myriad Landscape (P19 R14). Paid out of the pool."""

    kind = "activate_search"
    zone: str = LANDS
    index: int = 0

    def run(self, game, policy) -> None:
        card = _at(game, self.zone, self.index)
        pool = _pool(game)
        if not game.can_activate(card, pool):
            raise IllegalAction(f"{card.name} cannot be activated now; the floating mana is {pool}")
        game.activate(card, pool, _land_chooser(policy))


@dataclass(frozen=True)
class ActivateLander(Action):
    """Sacrifice a Lander token for a basic land, tapped (P19 R14)."""

    kind = "activate_lander"

    def run(self, game, policy) -> None:
        pool = _pool(game)
        if not game.can_activate(None, pool):
            raise IllegalAction(f"no Lander can be activated; the floating mana is {pool}")
        game.activate(None, pool, _land_chooser(policy))


@dataclass(frozen=True)
class SacrificeForMana(Action):
    """Sacrifice a permanent to the altar at ``index`` in ``zone`` for its
    mana (P19 R15): Ashnod's Altar, Phyrexian Tower. ``payment`` indexes
    ``Game.altar_fodder``; None is the agent's choice."""

    kind = "sacrifice_mana"
    zone: str = OTHER
    index: int = 0
    payment: int | None = None

    def run(self, game, policy) -> None:
        card = _at(game, self.zone, self.index)
        pool = _pool(game)
        options = game.altar_fodder(card, pool)
        if not options or not 0 <= (self.payment or 0) < len(options):
            raise IllegalAction(f"{card.name} has nothing to sacrifice now")
        game.use_altar(card, pool, self.payment)


@dataclass(frozen=True)
class EndStep(Action):
    """The end step triggers - Necropotence and friends."""

    kind = "end_step"

    def run(self, game, policy) -> None:
        game.end_step()
        game.phase = END


@dataclass(frozen=True)
class AdvancePhase(Action):
    """Step to the next phase, running whatever that phase does.

    This is what the playtest UI's "next" button posts. The agent does not use
    it: it drives the phases it cares about directly, because a goldfish turn
    has nothing to do in combat.
    """

    kind = "advance_phase"

    def run(self, game, policy) -> None:
        if game.phase is None or game.phase == END:
            BeginTurn().run(game, policy)
            return
        nxt = PHASES[PHASES.index(game.phase) + 1]
        if nxt == MAIN1:
            OpenMainPhase().run(game, policy)
        elif nxt == END:
            EndStep().run(game, policy)
        else:
            game.phase = nxt


@dataclass(frozen=True)
class Mulligan(Action):
    """Throw the hand back and draw a fresh seven.

    The London mulligan puts cards on the bottom **when the hand is kept**, so
    this action does not bottom anything; :class:`KeepHand` does.
    """

    kind = "mulligan"

    def run(self, game, policy) -> None:
        game.library = game.deck.shuffled(game.rng)
        game.hand = []
        game.drawn = []
        game.mulligans += 1
        game.draw(7)


@dataclass(frozen=True)
class KeepHand(Action):
    """Keep the current hand, bottoming what the mulligan count demands."""

    kind = "keep_hand"

    def run(self, game, policy) -> None:
        bottom = game.cards_to_bottom(game.mulligans)
        if bottom:
            # Private, and reached on purpose: which cards go to the bottom is
            # a mechanic of the London mulligan, not a decision this action is
            # entitled to make differently from `Game.take_opening_hand`.
            game._bottom_worst(bottom)


@dataclass(frozen=True)
class Draw(Action):
    """Draw ``count`` cards. A human action: the agent draws in its turn."""

    kind = "draw"
    count: int = 1

    def run(self, game, policy) -> None:
        game.draw(self.count)


@dataclass(frozen=True)
class SetLife(Action):
    """Set the life total outright, for a human correcting the board."""

    kind = "set_life"
    total: int = 40

    def run(self, game, policy) -> None:
        game.life = self.total


@dataclass(frozen=True)
class MoveCard(Action):
    """Move a card between any two zones.

    **Always permitted.** The rules do not allow most of these and that is the
    point: in paper you pick a card up and put it where the interaction you are
    testing needs it. The UI highlights what is legal and forbids nothing.
    """

    kind = "move_card"
    from_zone: str = HAND
    index: int = 0
    to_zone: str = GRAVEYARD

    def run(self, game, policy) -> None:
        card = _take(game, self.from_zone, self.index)
        zone_of(game, self.to_zone).append(card)


@dataclass(frozen=True)
class TapPermanent(Action):
    """Tap one more land or rock, for a human tracking mana by hand.

    The engine models tapped permanents as a count at the front of the zone
    rather than a flag per card, so this moves the boundary rather than
    marking a particular permanent.
    """

    kind = "tap_permanent"
    zone: str = LANDS

    def run(self, game, policy) -> None:
        if self.zone == LANDS:
            game.tapped_lands = min(game.tapped_lands + 1, len(game.lands))
        elif self.zone == ROCKS:
            game.tapped_rocks = min(game.tapped_rocks + 1, len(game.rocks))
        else:
            raise IllegalAction(f"{self.zone} does not tap for mana")


# --- Carrying an action out ------------------------------------------------

#: Every action, by its stored ``kind``. Used to rebuild one from a database row.
BY_KIND = {
    action.kind: action
    for action in (
        BeginTurn, PlayLand, OpenMainPhase, CastSpell, CastCommander, EndStep,
        AdvancePhase, Mulligan, KeepHand, Draw, SetLife, MoveCard, TapPermanent,
        ActivateSearch, ActivateLander, SacrificeForMana,
    )
}


def apply(game, action: Action, policy=None) -> None:
    """Carry out one action against one game.

    ``policy`` supplies the choices an action cannot make for itself - which
    card a tutor finds, above all. The agent passes itself; a playtest passes
    the human's answer, or ``None`` while nothing has been asked yet.
    """
    action.run(game, policy)


def from_row(kind: str, payload: dict) -> Action:
    """Rebuild an action from what was stored."""
    try:
        cls = BY_KIND[kind]
    except KeyError as exc:
        raise IllegalAction(f"unknown action kind: {kind!r}") from exc
    return cls(**payload)


# --- What the rules allow --------------------------------------------------

def legal_actions(game) -> list[Action]:
    """The actions the rules permit right now, for the UI to highlight.

    Advisory. Nothing consults this before carrying an action out - it exists so
    that a person can see what is castable without the application deciding on
    their behalf. Ordered so that the list is stable between calls.
    """
    if game.phase is None:
        return [Mulligan(), KeepHand()]

    allowed: list[Action] = [AdvancePhase()]

    if not game.land_drop_used:
        allowed += [PlayLand(index=i) for i, card in enumerate(game.hand)
                    if card.is_land]

    pool = getattr(game, "pool", None)
    if pool is not None:
        allowed += [CastSpell(index=i) for i, card in enumerate(game.hand)
                    if game.can_cast(card, pool)]
        if game.can_cast_commander(pool):
            allowed.append(CastCommander())
        allowed += activations(game, pool)
        allowed += altars(game, pool)

    return allowed


def activations(game, pool) -> list[Action]:
    """The land searches that can be activated now (P19 R14)."""
    found: list[Action] = [
        ActivateSearch(zone=zone, index=index)
        for zone in (LANDS, ROCKS, CREATURES, OTHER)
        for index, card in enumerate(zone_of(game, zone))
        if card.land_search is not None and card.land_search.when == "activate"
        and game.can_activate(card, pool)
    ]
    if game.can_activate(None, pool):
        found.append(ActivateLander())
    return found


def altars(game, pool) -> list[Action]:
    """The altars that have something to sacrifice now (P19 R15)."""
    return [SacrificeForMana(zone=zone, index=index)
            for zone in (LANDS, ROCKS, CREATURES, OTHER)
            for index, card in enumerate(zone_of(game, zone))
            if card.sacrifice_mana is not None and game.altar_fodder(card, pool)]


def castable_cost(game, card, x: int = 0):
    """What a card would actually cost right now, reductions and X included.

    The UI needs this to explain *why* something is not highlighted, which is
    the honest version of greying a button out.
    """
    return effective_mana_cost(card, reductions_from(game.battlefield), x)


# --- Internals -------------------------------------------------------------

def _at(game, zone: str, index: int):
    """The card at ``index`` of ``zone``, left where it is.

    Used where the ``Game`` method does its own removal, so that the index and
    the object cannot disagree about which card is meant.
    """
    cards = zone_of(game, zone)
    if not 0 <= index < len(cards):
        raise IllegalAction(
            f"{zone} has {len(cards)} cards, so there is nothing at {index}")
    return cards[index]


def _take(game, zone: str, index: int):
    """Remove and return the card at ``index`` of ``zone``."""
    _at(game, zone, index)
    return zone_of(game, zone).pop(index)


def _pool(game):
    """The floating pool, or a complaint that the main phase has not opened."""
    pool = getattr(game, "pool", None)
    if pool is None:
        raise IllegalAction("nothing can be cast before the main phase opens")
    return pool


def _apply_cast_effect(game, card, policy, x: int = 0) -> None:
    """Cast-time extras: tutors and draw-on-cast.

    Moved here from the agent, because *what a card does* is a mechanic and
    only *which card the tutor finds* is a decision. The decision is delegated
    to ``policy``; without one, the search takes the most expensive card, which
    is a placeholder for the human being asked and never used by the agent.
    """
    if card.tutor is not None:
        spec = card.tutor
        game.life -= spec.life
        if spec.to_battlefield:
            predicate = _battlefield_predicate(spec, x, game)
        elif spec.to_top:
            predicate = (lambda c: bool(c.types & spec.types)) if spec.types else None
        else:
            predicate = (lambda c: c.kind == spec.kind) if spec.kind else None
        found = 0
        for _ in range(spec.count):
            target = _search(game, policy, predicate)
            if target is None:
                break
            game.library.remove(target)
            if spec.to_battlefield:
                game.note(f"  -> puts {target.name} onto the battlefield")
                game.enter_battlefield(target)
                _arrival(game, target, policy)
            elif spec.to_top:
                game.library.insert(0, target)
                game.note(f"  -> puts {target.name} on top of the library")
            elif spec.to_hand:
                game.hand.append(target)
                game.note(f"  -> searches up {target.name}")
            else:
                game.graveyard.append(target)
            found += 1
        if not (spec.to_hand or spec.to_battlefield or spec.to_top) and found:
            game.note(f"  -> {found} cards to the graveyard")

    _arrival(game, card, policy)

    drawn = card.draw_on_cast + (x if card.draws_x else 0)
    if drawn:
        game.draw(drawn)
        game.life -= card.life_on_cast
        game.note(f"  -> {drawn} cards, {card.life_on_cast} life")
    if card.discard_on_cast:
        game.discard(card.discard_on_cast)
    if card.put_back_on_cast:
        game.put_back(card.put_back_on_cast)


def _arrival(game, card, policy) -> None:
    """What a card does as it resolves or enters: its land search, its Treasure.

    Shared by a cast card and a card a tutor put onto the battlefield (P19
    R10): Wood Elves found by Green Sun's Zenith still fetch their Forest.
    """
    search = card.land_search
    if search is not None and search.when in ("cast", "enters"):
        game.search_lands(card, _land_chooser(policy))
        if search.sacrifice and card in game.creatures:
            # Sakura-Tribe Elder: sacrificed for its land as soon as it is in
            # play - nothing in a goldfish is worth keeping it around for.
            game.creatures.remove(card)
            game.graveyard.append(card)

    game.make_treasures(card)
    game.make_landers(card)


def _battlefield_predicate(spec, x: int, game=None):
    """Which library cards a search onto the battlefield may find (P19 R10)."""
    limit = x if spec.max_mv_x else spec.max_mv
    if spec.max_mv_sacrificed is not None:
        # Eldritch Evolution: counted from what its cost sacrificed (P19 R15).
        gone = getattr(game, "last_sacrificed", None)
        limit = spec.max_mv_sacrificed + getattr(gone, "mv", 0)

    def matches(card) -> bool:
        types = card.types or frozenset({card.kind})
        if spec.kind and spec.kind not in types:
            return False
        if spec.color and spec.color not in card.mana_cost.colors:
            return False
        return limit is None or card.mv <= limit

    return matches


def _land_chooser(policy):
    """The policy's pick for a land search, if it makes one (a human does)."""
    return getattr(policy, "choose_fetched_land", None)


def _search(game, policy, predicate):
    """The card a tutor finds."""
    options = [card for card in game.library
               if predicate is None or predicate(card)]
    if not options:
        return None
    if policy is not None:
        return policy.choose_tutor_target(game, options)
    return max(options, key=lambda card: (card.mv, card.name))
