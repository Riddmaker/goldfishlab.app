"""The player agent, driven by a priority list.

The method follows the EDHREC article "Simulating Available Mana": an agent
with clear priorities, **not random play**. Playing at random produces nonsense
and systematically understates every deck with ramp and draw in it.

The priorities encode how ``deck-v2.md`` says the deck should be played: mana
first, then the card-advantage engines, then the sacrifice motor.
"""

from simulation import actions
from simulation.cards import PER_CONTROLLED, RITUAL, TYPE_ADDING
from simulation.mana import ManaPool, effective_mana_cost, reductions_from
from simulation.manacost import SUBTYPE_COLORS

# From this priority upwards it is worth burning a ritual. A setting of the
# agent rather than a property of a card, which is why it stays here.
RITUAL_THRESHOLD = 74

#: Cards with no priority of their own: cheaper first.
DEFAULT_PRIORITY_BASE = 40


class _AgentPolicy:
    """The choices an action cannot make for itself.

    An action knows *what a tutor does*; it does not know which card this deck
    wants. The playtest asks a person; the agent asks its priority list, which
    is what it always did - this class only gives that answer a name and an
    address, so that :mod:`simulation.actions` never has to import the agent.
    """

    @staticmethod
    def choose_tutor_target(game, options):
        """The best card the search may take.

        A deck with a priority list is asked, exactly as before - which is what
        keeps the reference deck, and the golden snapshot, playing the same
        games. A deck without one (every user deck since the priorities left
        the page) used to fall through to "cheaper first", and the cheapest card
        in a library is a land: Demonic Tutor fetched a Forest (phase 10 N1).
        """
        if any(card.priority is not None for card in options):
            return max(options, key=priority)
        return _unprioritised_target(game, options)


#: The agent's answers, passed to every ``actions.apply`` it makes.
POLICY = _AgentPolicy()


#: Tags that make a card worth finding before a bigger spell: the engines the
#: milestones already count. Combo pieces come from the deck (`key_cards`).
ENGINE_TAGS = frozenset({"draw_engine", "sac_outlet", "drain_payoff"})


def _unprioritised_target(game, options):
    """A tutor's pick when nobody said what the deck wants.

    1. A spell before a land - a land is what the next draw brings anyway.
    2. A key card before any other spell: a piece of one of the deck's combos,
       or an engine.
    3. Among those, the biggest one castable by next turn, which is what a
       player tutors for when there is nothing more specific to find.
    4. Failing that, the cheapest: the one closest to being cast.

    Only lands to choose from (Expedition Map, Crop Rotation): the land the land
    rule would play. Ties go by name, so a run stays reproducible.
    """
    spells = [card for card in options if not card.is_land]
    if not spells:
        return max(options, key=lambda card: (_land_score(card, game), card.name))

    key_names = game.deck.key_cards
    key = [card for card in spells
           if card.name in key_names or card.tags & ENGINE_TAGS]
    pool = key or spells
    reach = game.mana_available + 1
    castable = [card for card in pool if card.mv <= reach]
    if castable:
        return max(castable, key=lambda card: (card.mv, card.name))
    return min(pool, key=lambda card: (card.mv, card.name))


def priority(card) -> int:
    """A card's priority.

    Until Phase 2 this was a table of 45 card names. The number now lives on
    the card itself, because it is a property of the *deck*: the same card is
    played differently in a different one. ``None`` does not mean "forgotten",
    it means "no opinion" - and then the default rule applies: cheaper first.
    """
    if card.priority is not None:
        return card.priority
    return DEFAULT_PRIORITY_BASE - card.mv


# --- Choosing a land -------------------------------------------------------

def _land_score(card, game) -> int:
    """Score which land should be played this turn.

    Cabal Coffers makes *no* mana of its own without Urborg. Playing it early
    therefore costs a land drop outright, so it waits for 3 lands or for Urborg
    on the battlefield.

    Since Phase 2 these are rules rather than card names: any land with a
    ``PER_CONTROLLED`` ability behaves like Coffers, any with ``TYPE_ADDING``
    like Urborg. The numbers are unchanged.
    """
    lands_out = len(game.lands)
    scaling_out = any(land.ability(PER_CONTROLLED) is not None for land in game.lands)
    adder_out = any(land.ability(TYPE_ADDING) is not None for land in game.lands)

    if card.ability(PER_CONTROLLED) is not None:
        # Without the subtype giver it taps for nothing and is a dead land drop.
        if adder_out or lands_out >= 3:
            return 95
        return 5
    if card.ability(TYPE_ADDING) is not None:
        # With a scaling land already out it is the best land in the deck.
        return 96 if scaling_out else 90
    if any(subtype in SUBTYPE_COLORS for subtype in card.subtypes):
        return 80          # a basic land type: makes colour and can be counted
    search = card.land_search
    if search is not None and search.when == "play":
        # A fetch land: as good as the land it finds, a little less for the
        # life it may cost; a tapped one like any land that enters tapped,
        # with the colour it fixes on top (P19 R2).
        untapped = not search.tapped or (search.untap_at
                                         and lands_out + 1 >= search.untap_at)
        return 79 if untapped else 25
    if card.enters_tapped and not game.untaps_on_entering(card):
        return 20          # useless this turn
    # What it would tap for once played: Temple of the False God taps for
    # nothing before the fifth land, a Tainted land for {C} without its type.
    flat = game.mana_ability(card, entering=True)
    if flat is not None:
        # Colour beats colourless. This used to drop a coloured land with no
        # basic land type to 50, which put it *below* the colourless one - never
        # noticed in the mono-black deck, where every coloured land is either a
        # swamp or enters tapped.
        return 70 if flat.colored_total else 60
    return 50


def choose_land(game):
    """The best land in hand, or None."""
    lands = [card for card in game.hand if card.is_land]
    if not lands:
        return None
    return max(lands, key=lambda card: (_land_score(card, game), card.name))


# --- Spells with an extra effect ------------------------------------------

# --- Rituals ---------------------------------------------------------------

def _try_ritual_line(game, pool: ManaPool) -> bool:
    """Cast a ritual when it unlocks something worth unlocking.

    Without this check the agent would play Dark Ritual either never or
    blindly. Both distort the odds of a turn-one Necropotence.
    """
    rituals = [card for card in game.hand
               if card.kind == RITUAL and game.can_cast(card, pool)]
    if not rituals:
        return False

    castable_now = {card.name for card in game.hand if game.can_cast(card, pool)}
    for ritual in sorted(rituals, key=lambda c: c.mv):
        test = pool.copy()
        cost = effective_mana_cost(ritual, reductions_from(game.battlefield))
        if test.pay_cost(cost, life=game.life) is None:
            continue
        test.add(ritual.ritual_color, ritual.ritual_gain)
        unlocked = [
            card for card in game.hand
            if card is not ritual
            and card.kind != RITUAL
            and card.name not in castable_now
            and priority(card) >= RITUAL_THRESHOLD
            and game.can_cast(card, test)
        ]
        if game.can_cast_commander(test) and not game.can_cast_commander(pool):
            unlocked.append(None)
        if unlocked:
            _do(game, actions.CastSpell(index=game.hand.index(ritual)))
            return True
    return False


# --- Main phase ------------------------------------------------------------

def _cast_best(game, pool: ManaPool) -> bool:
    """Cast the best playable card. Returns True when something happened."""
    options = [card for card in game.hand
               if card.kind != RITUAL and game.can_cast(card, pool)]
    best_spell = max(options, key=priority) if options else None
    best_score = priority(best_spell) if best_spell else -1

    commander_ok = game.can_cast_commander(pool)
    commander_priority = priority(game.deck.commander) if game.deck.commander else -1
    if commander_ok and commander_priority > best_score:
        _do(game, actions.CastCommander())
        return True

    if best_spell is not None:
        _do(game, actions.CastSpell(index=game.hand.index(best_spell)))
        return True

    if commander_ok:
        _do(game, actions.CastCommander())
        return True

    return False


def _do(game, action) -> None:
    """Carry out one action with this agent's answers."""
    actions.apply(game, action, POLICY)


def take_turn(game) -> None:
    """Play a whole turn: land drop, main phase, end step.

    Since Phase 5 every step here is an :class:`~simulation.actions.Action`
    rather than a direct call into :class:`~simulation.game.Game`. The agent
    still *decides* - which land, which spell, whether a ritual is worth it -
    but the doing is the same code the playtest runs, so a human and the agent
    cannot drift apart about what casting a spell means.

    It does not use ``AdvancePhase``: a goldfish turn has nothing to do in
    combat, and stepping through empty phases would only make the log longer.
    """
    _do(game, actions.BeginTurn())

    land = choose_land(game)
    if land is not None:
        _do(game, actions.PlayLand(index=game.hand.index(land)))

    # Opens the pool and records the mana that was available *before* any
    # ritual is cast on top of it.
    _do(game, actions.OpenMainPhase())
    pool = game.pool

    while True:
        if _cast_best(game, pool):
            continue
        if _try_ritual_line(game, pool):
            continue
        break

    _do(game, actions.EndStep())
