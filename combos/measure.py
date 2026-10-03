"""Deciding which combos a run measures, and what it costs.

Phase 7 §1 answered "does this deck contain a combo". This module answers the
question the simulator exists for: **how long does it take to come together**.

Two kinds of combo, and the difference decides the whole shape of this file:

* A combo the deck **contains** is measured in the deck's own games. It costs
  nothing at all - the games are played either way, and watching for a card in
  a zone is a dictionary lookup per turn. Every one of them is watched.
* A combo the deck is **one card away** from cannot be. The card that completes
  it is not in the library to be drawn, so the only honest way to measure it is
  to play a deck that has it: the real deck **plus** that one card. That is a
  different experiment, it needs games of its own, and games are the thing this
  application meters.

So the hypotheticals are rationed, and `budget()` is where that happens. The
rule, in one line: **a run spends at most half its own size again on
hypotheticals, across at most three of them, and never fewer than a thousand
games on any one.** A thousand games puts the sampling error on a 5% answer at
about half a percentage point, which is inside the whole-percent this
application reports; below that the number would be noise wearing a percent
sign, and it is better to say nothing.

That also means a small run measures nothing hypothetical, and says so. A
free-plan run of ten thousand games measures three; a run of a thousand
measures none. Telling somebody "run more games and I can price that card" is
a true sentence and a useful one.

**No fifth `quotas.check()` call site.** The games are the run's own, planned
where the run is planned and consumed where the run's games are consumed.
`billing/quotas.py` documents every place that decides whether somebody may do
something - five since phase 10 H added the deck summary's button - and this is
not one of them.
"""

from dataclasses import dataclass, field, replace

from django.utils.translation import gettext_noop

from combos.models import ComboLookup, ComboMeasurement, DeckCombo
from simulations.engine import adapter, runner

#: At most this many hypothetical decks per run. Three rather than eight: the
#: page needs one sentence somebody will act on, not a table of eight, and the
#: eighth-most-played combo in a list of 125 is not the one anybody buys a card
#: for. `combos.services.ONE_AWAY_SHOWN` still lists eight - listing is free.
MAX_HYPOTHETICALS = 3

#: How many combos the deck already holds may be watched at once. Watching is
#: nearly free - the games are played either way - but "nearly" is per game per
#: turn, and a cEDH deck holding sixty of them would put a real tax on every
#: run for a page nobody reads past the top of. Most-played first, like
#: everything else in this list.
MAX_WATCHED = 25

#: The smallest sample worth printing a percentage from. At 1,000 games a 5%
#: answer carries about half a point of sampling error; at 300 it carries one
#: and a half, which is wider than the differences the page would be inviting
#: somebody to read.
MIN_SAMPLE = 1_000

#: The largest. Past this the extra precision is below the whole percent the
#: report rounds to, and it is somebody's plan being spent on decimals nobody
#: is shown.
MAX_SAMPLE = 5_000

#: A run spends at most `games_total // BUDGET_DIVISOR` extra games on all its
#: hypotheticals together - half the run again, in the worst case.
BUDGET_DIVISOR = 2

#: Why a combo has no number beside it. These are printed, so they are
#: sentences rather than codes: a person who sees a gap deserves to know
#: whether it is their deck, our catalogue or the simulator that is the reason.
NEEDS_TEMPLATE = ("needs a kind of card rather than a named one, which is a search "
                  "and not a simulation")
NOT_IN_CATALOGUE = "names a card this application has not got in its catalogue"
UNWATCHABLE = "asks for a card to be somewhere this simulator does not model"
MISSING_SEVERAL = "is more than one card away once this deck is read our way"
NOT_REALLY_HELD = "is one this deck turns out not to hold, once it is read our way"
NOT_CHOSEN = "was not among the most-played few this run had room to measure"

#: What a page prints for each, as a whole sentence (phase 12): a fragment
#: after "it" cannot be translated on its own. English, translated where shown.
SENTENCES = {
    NEEDS_TEMPLATE: gettext_noop("No timing for this one: it needs a kind of card rather "
                                 "than a named one, which is a search and not a simulation."),
    NOT_IN_CATALOGUE: gettext_noop("No timing for this one: it names a card this "
                                   "application has not got in its catalogue."),
    UNWATCHABLE: gettext_noop("No timing for this one: it asks for a card to be somewhere "
                              "this simulator does not model."),
    MISSING_SEVERAL: gettext_noop("No timing for this one: it is more than one card away "
                                  "once this deck is read our way."),
    NOT_REALLY_HELD: gettext_noop("No timing for this one: it is one this deck turns out "
                                  "not to hold, once it is read our way."),
    NOT_CHOSEN: gettext_noop("No timing for this one: it was not among the most-played "
                             "few this run had room to measure."),
}


@dataclass(frozen=True)
class Watched:
    """A combo the deck contains, measured in the deck's own games."""

    entry: DeckCombo
    watch: object


@dataclass(frozen=True)
class Hypothetical:
    """A combo the deck is one card away from, and the card that completes it.

    `games` is for the whole run; `share()` splits it across the chunks.
    """

    entry: DeckCombo
    card: object
    watch: object
    games: int = 0

    def share(self, chunks: int, index: int) -> int:
        """This chunk's slice of the sample.

        Split by remainder rather than rounded, so the slices add up to exactly
        `games` - a sample that quietly plays 997 of the 1,000 games it claims
        would put a wrong denominator under every percentage it produces.
        """
        if chunks < 1:
            return 0
        base, extra = divmod(self.games, chunks)
        return base + (1 if index < extra else 0)


@dataclass
class Plan:
    """What one run will measure, and what it will not.

    `refusals` is as much of the product as the measurements are. A combo with
    no number beside it and no reason is a page that looks broken; a combo with
    a reason is a page that is being honest about a limit.
    """

    watched: tuple = ()
    hypotheticals: tuple = ()
    refusals: dict = field(default_factory=dict)
    #: Every piece of every combo the deck holds, watched or not: what a tutor
    #: with no priority list goes for first (phase 10 N1).
    key_cards: frozenset = frozenset()

    @property
    def watches(self) -> tuple:
        return tuple(item.watch for item in self.watched)

    @property
    def extra_games(self) -> int:
        """Games this run will play beyond the ones that were asked for."""
        return sum(item.games for item in self.hypotheticals)

    def __bool__(self) -> bool:
        return bool(self.watched or self.hypotheticals)


def plan_for(run) -> Plan:
    """What this run should measure, decided the same way every time.

    Deterministic, because every chunk of the run recomputes it rather than
    being handed it through the broker: the chunks are separate tasks and only
    JSON travels between them. Recomputing costs a couple of queries per
    fifteen seconds of simulation, which is the same trade the cancellation
    check already makes.
    """
    entries = _entries(run.deck)
    if not entries:
        return Plan()

    watched, candidates, refusals = [], [], {}

    for entry in entries:
        watch = _watch_for(entry.combo)
        if isinstance(watch, str):
            refusals[entry.combo_id] = watch
            continue

        if entry.kind == DeckCombo.Kind.INCLUDED:
            if entry.missing:
                # Spellbook says the deck has it; our own record of the deck
                # says otherwise. Trust ours - it is the one the page is about,
                # and measuring a combo against a card the deck has not got
                # would report a flat zero for a reason nobody could see.
                refusals[entry.combo_id] = NOT_REALLY_HELD
                continue
            watched.append(Watched(entry=entry, watch=watch))
            continue

        card = _missing_card(entry)
        if card is None:
            refusals[entry.combo_id] = (
                MISSING_SEVERAL if len(entry.missing) != 1 else NOT_IN_CATALOGUE
            )
            continue
        candidates.append(Hypothetical(entry=entry, card=card, watch=watch))

    key_cards = _pieces(item.watch for item in watched)
    for item in watched[MAX_WATCHED:]:
        refusals[item.entry.combo_id] = NOT_CHOSEN
    watched = watched[:MAX_WATCHED]

    count, games = budget(run.games_total, len(candidates))
    chosen = tuple(
        Hypothetical(entry=item.entry, card=item.card, watch=item.watch, games=games)
        for item in candidates[:count]
    )
    for item in candidates[count:]:
        refusals[item.entry.combo_id] = NOT_CHOSEN

    return Plan(watched=tuple(watched), hypotheticals=chosen, refusals=refusals,
                key_cards=key_cards)


def _pieces(watches) -> frozenset:
    """The card names a set of combos needs."""
    return frozenset(requirement.name for watch in watches for requirement in watch.requirements)


def with_key_cards(definition, plan: Plan):
    """The deck as this run plays it: knowing which of its cards are combo pieces."""
    return replace(definition, key_cards=plan.key_cards)


def budget(games_total: int, candidates: int) -> tuple[int, int]:
    """How many hypotheticals a run of this size measures, and with how many games.

    Returns `(0, 0)` when the run is too small to price a card honestly, which
    is a real answer and the one a thousand-game run gets.
    """
    room = games_total // BUDGET_DIVISOR
    count = min(MAX_HYPOTHETICALS, candidates, room // MIN_SAMPLE)
    if count < 1:
        return 0, 0
    return count, min(MAX_SAMPLE, room // count)


def samples_for(run, plan: Plan, index: int, chunks: int) -> list:
    """The hypothetical decks this chunk plays, built for the engine.

    One `adapter.convert` per hypothetical per chunk, which is the same work
    the chunk already does once for the deck itself.
    """
    samples = []
    for item in plan.hypotheticals:
        games = item.share(chunks, index)
        if games < 1:
            continue
        samples.append(runner.Sample(
            key=item.entry.combo_id,
            # The deck plus the card plays as a deck that holds this combo,
            # so its pieces are key cards as well.
            deck=replace(adapter.deck_definition(run.deck, adding=item.card),
                         key_cards=plan.key_cards | _pieces((item.watch,))),
            watch=item.watch,
            games=games,
        ))
    return samples


def store(run, payload: dict | None) -> int:
    """Write what the run measured, and return the extra games it played.

    The return value is what `finalize_run` adds to the run's own iterations
    when it records usage: the hypotheticals are real games on a real worker,
    and a usage record that left them out would be this application
    under-reporting its own costs to itself.
    """
    if not payload:
        return 0

    plan = plan_for(run)
    known = {item.entry.combo_id: (item.entry, None) for item in plan.watched}
    known.update(
        {item.entry.combo_id: (item.entry, item.card) for item in plan.hypotheticals}
    )

    rows = []
    for key, measured in payload.items():
        found = known.get(key)
        if found is None:
            # The lookup was refreshed while the run was in flight. The games
            # were played and the number is true, but nothing on this page
            # knows what to call it any more.
            continue
        entry, added = found
        rows.append(ComboMeasurement(
            run=run,
            deck=run.deck,
            combo_id=key,
            kind=entry.kind,
            added_card=added,
            added_name=added.front_name if added is not None else "",
            games=int(measured.get("games") or 0),
            turns=run.turns,
            by_turn=[int(count) for count in measured.get("by_turn") or []],
        ))

    ComboMeasurement.objects.bulk_create(rows, ignore_conflicts=True)
    return sum(row.games for row in rows if row.added_name)


# --- reading the database rows into something the engine understands -------


def _entries(deck) -> list:
    """This deck's combos, most-played first within each kind."""
    try:
        lookup = deck.combo_lookup
    except ComboLookup.DoesNotExist:
        return []
    if not lookup.ok:
        return []
    return list(
        lookup.entries.select_related("combo")
        .prefetch_related("combo__cards", "combo__cards__oracle_card", "combo__templates")
        # The id breaks ties explicitly. The model's own ordering stops at
        # popularity, and two equally-played combos could then come back in
        # either order - which would be harmless anywhere else and is not here:
        # every chunk of a run plans independently, so a wobble in this order
        # would have different chunks measuring different combos and the merge
        # would report each of them over a fraction of the sample it claims.
        .order_by("kind", "-combo__popularity", "combo_id")
    )


def _watch_for(combo):
    """A `runner.Watch` for this combo, or the sentence saying why not."""
    # `all()` rather than `exists()`: the caller prefetched these, and a query
    # per combo would be 125 queries on the demo deck to learn what is already
    # in memory.
    if list(combo.templates.all()):
        return NEEDS_TEMPLATE

    requirements = []
    for card in combo.cards.all():
        try:
            requirements.append(runner.Requirement(
                name=_engine_name(card),
                # An empty list means Spellbook did not say. The battlefield is
                # both the overwhelmingly common answer and the strictest one,
                # so a wrong guess costs a combo its number rather than
                # inventing one.
                zones=tuple(card.zones) or runner.DEFAULT_COMBO_ZONES,
                quantity=card.quantity,
                must_be_commander=card.must_be_commander,
            ))
        except runner.Unmeasurable:
            return UNWATCHABLE

    try:
        return runner.Watch(key=combo.spellbook_id, requirements=tuple(requirements))
    except runner.Unmeasurable:
        return UNWATCHABLE


def _engine_name(card) -> str:
    """The name the engine will know this card by.

    `front_name` where the catalogue has the card, because that is what
    `adapter._card_from` puts on the engine's `Card` - matching on Spellbook's
    spelling instead would work until the first double-faced card and then fail
    silently, which is trap 3 arriving from a third direction.
    """
    if card.oracle_card_id and card.oracle_card is not None:
        return card.oracle_card.front_name
    return card.name


def _missing_card(entry):
    """The one card this deck lacks, as the catalogue has it, or None.

    None whenever the honest answer is "no number": more than one card missing,
    or a card this application has never ingested. Either way the page says
    which, rather than showing an empty column.
    """
    if len(entry.missing) != 1:
        return None
    wanted = entry.missing[0]
    for card in entry.combo.cards.all():
        if card.name == wanted and card.oracle_card_id:
            return card.oracle_card
    return None
