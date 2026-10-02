"""Looking a deck up, and knowing when the answer has gone off.

The whole of the network policy is here and in `spellbook.py`: **nothing
fetches during a page render.** A deck page reads whatever was cached, says how
old it is, and offers a button. That button is the only thing in the
application that can cause an outbound request to Commander Spellbook.

Two rules that follow from it, both deliberate:

1. **A stale answer is shown, not hidden.** The alternative is a page that goes
   blank because somebody else's API is down, which converts their outage into
   our bug. An old answer with a date on it is worth more than no answer.
2. **A refusal to refresh is not an error.** `COOLDOWN` stops somebody holding
   a key down and turning this application into a load generator against a free
   service. It returns the existing lookup and says why.

Not metered. `billing/quotas.py` documents every `check()` call site (five
since phase 10 H) and another one here would be a bug; the cache and the
cooldown are what bound this, and they bound it per deck rather than per
person, which is the right axis for a cost that is somebody else's bandwidth.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from cards.models import OracleCard
from combos import spellbook
from combos.models import (
    Combo,
    ComboCard,
    ComboLookup,
    ComboMeasurement,
    ComboTemplate,
    DeckCombo,
)

#: How long an answer stays believable without re-asking. Spellbook publishes
#: new combos continuously but a fortnight of them rarely changes what one deck
#: contains, and this is the number that decides how often this application
#: talks to somebody else's free API.
MAX_AGE = timedelta(days=14)

#: The floor between two refreshes of the same deck. Not a quota - a courtesy,
#: and a guard against a button somebody can hold down.
COOLDOWN = timedelta(minutes=2)


@dataclass(frozen=True)
class Refusal:
    """A refresh that did not happen, and why. Never an exception.

    A person pressing a button twice has done nothing wrong, so they get a
    sentence rather than an error page.
    """

    reason: str


def fingerprint(deck) -> str:
    """A hash of what this deck contains, for deciding staleness.

    The commander is in it, and it is easy to leave out: it is not a `DeckCard`
    (trap 7), so a deck whose only change was its commander would look
    unchanged - and the commander is the card that decides the colour identity
    the whole lookup is filtered by.
    """
    oracle_ids = sorted(
        str(pk) for pk in deck.entries.values_list("oracle_card_id", flat=True)
    )
    if deck.commander_id:
        oracle_ids.append(f"commander:{deck.commander_id}")
    return hashlib.sha256("|".join(oracle_ids).encode()).hexdigest()


def lookup_for(deck) -> ComboLookup | None:
    """This deck's cached answer, or None if nobody has ever asked."""
    try:
        return deck.combo_lookup
    except ComboLookup.DoesNotExist:
        return None


def is_stale(lookup: ComboLookup | None, deck) -> bool:
    """Whether the cached answer is about a deck, or a date, that has moved on."""
    if lookup is None:
        return True
    if lookup.fingerprint != fingerprint(deck) or lookup.fetched_at is None:
        return True
    return timezone.now() - lookup.fetched_at > MAX_AGE


def _names(deck) -> tuple[list[str], list[str]]:
    """The deck's card names as Scryfall spells them, split from the commander.

    `front_name` rather than `name`, because Spellbook's card names are front
    faces and `Tergrid, God of Fright // Tergrid's Lantern` matches nothing.
    Trap 3 from Phase 1, arriving from a new direction.
    """
    main = list(
        deck.entries.select_related("oracle_card")
        .values_list("oracle_card__front_name", flat=True)
        .distinct()
    )
    commanders = [deck.commander.front_name] if deck.commander_id else []
    return [name for name in main if name and name not in commanders], commanders


def refresh(deck, *, force: bool = False) -> ComboLookup | Refusal:
    """Ask Commander Spellbook about this deck and keep the answer.

    The only outbound call in the application's combo path. Returns a `Refusal`
    rather than raising when the cooldown blocks it, because a person pressing
    a button twice has not made a mistake.
    """
    existing = lookup_for(deck)
    if existing is not None and not force:
        since = timezone.now() - existing.attempted_at
        if since < COOLDOWN:
            wait = int((COOLDOWN - since).total_seconds())
            return Refusal(
                f"Checked {int(since.total_seconds())} seconds ago. "
                f"Try again in about {wait} seconds - Commander Spellbook is a free "
                "service and this is the polite thing to do."
            )

    main, commanders = _names(deck)
    if not main and not commanders:
        return Refusal("There are no cards in this deck to look up.")

    try:
        results = spellbook.find_my_combos(main, commanders)
    except spellbook.SpellbookError as exc:
        return _store_failure(deck, str(exc))

    return _store(deck, results, held={*main, *commanders})


@transaction.atomic
def _store_failure(deck, message: str) -> ComboLookup:
    """Record that the lookup failed, without discarding what was found before.

    The entries stay. An outage at Spellbook should cost the page its freshness
    and not its contents - the date beside them already says how old they are.
    """
    lookup, _ = ComboLookup.objects.get_or_create(deck=deck)
    lookup.status = ComboLookup.Status.FAILED
    lookup.message = message[:256]
    # `attempted_at` and not `fetched_at`: the entries below are still the
    # old answer, and the date beside them has to stay the old answer's date.
    lookup.save(update_fields=["status", "message", "attempted_at"])
    return lookup


@transaction.atomic
def _store(deck, results: spellbook.Results, *, held: set[str]) -> ComboLookup:
    lookup, _ = ComboLookup.objects.get_or_create(deck=deck)
    lookup.fingerprint = fingerprint(deck)
    lookup.fetched_at = timezone.now()
    lookup.identity = results.identity
    lookup.status = ComboLookup.Status.OK
    lookup.message = ""
    lookup.out_of_identity = results.out_of_identity
    lookup.needs_other_commanders = results.needs_other_commanders
    lookup.save()

    # Replace, never merge. A lookup is Spellbook saying "this is what the deck
    # contains now"; merging would leave a combo on the page after the card
    # that made it was cut. Same rule as the deck importer.
    lookup.entries.all().delete()

    entries = []
    for kind, records in ((DeckCombo.Kind.INCLUDED, results.included),
                          (DeckCombo.Kind.ONE_AWAY, results.almost)):
        for record in records:
            combo = _upsert(record)
            entries.append(DeckCombo(
                lookup=lookup,
                combo=combo,
                kind=kind,
                # Computed against our own record of the deck rather than taken
                # from the API, so the shopping list and the deck page cannot
                # disagree about what is in it.
                missing=[c.name for c in record.cards if c.name not in held],
            ))
    DeckCombo.objects.bulk_create(entries, batch_size=200)
    return lookup


def _upsert(record: spellbook.ComboRecord) -> Combo:
    """Write one combo into the mirror, replacing what was there.

    This is where the mirror accumulates. Nothing downloaded 656 MB to fill it;
    it fills because somebody looked at a deck.
    """
    combo, _ = Combo.objects.update_or_create(
        spellbook_id=record.spellbook_id,
        defaults={
            "identity": record.identity,
            "status": record.status,
            "bracket_tag": record.bracket_tag,
            "mana_needed": record.mana_needed,
            "mana_value_needed": min(record.mana_value_needed, 32_767),
            "notable_prerequisites": record.notable_prerequisites,
            "description": record.description,
            "popularity": record.popularity,
            "legal_commander": record.legal_commander,
            "produces": list(record.produces),
        },
    )

    combo.cards.all().delete()
    combo.templates.all().delete()

    known = {
        str(pk): pk
        for pk in OracleCard.objects.filter(
            oracle_id__in=[c.oracle_id for c in record.cards if c.oracle_id]
        ).values_list("oracle_id", flat=True)
    }
    ComboCard.objects.bulk_create([
        ComboCard(
            combo=combo,
            # Null when Spellbook names a card this catalogue has not got. The
            # name beside it still says what it is.
            oracle_card_id=known.get(card.oracle_id),
            oracle_id=card.oracle_id,
            name=card.name,
            quantity=card.quantity,
            must_be_commander=card.must_be_commander,
            zones=list(card.zones),
        )
        for card in record.cards
    ])
    ComboTemplate.objects.bulk_create([
        ComboTemplate(
            combo=combo,
            name=template.name,
            quantity=template.quantity,
            scryfall_query=template.scryfall_query,
            zones=list(template.zones),
        )
        for template in record.templates
    ])
    return combo


#: How many "one card away" combos are shown before the rest are folded away.
#: Measured, not guessed: the demo deck returns **125** of them and the
#: reference deck 76. Rendered flat, that is a deck page thousands of pixels
#: tall - the detail pushes the thing somebody came for off the top of the
#: screen. They are
#: ordered by how many decks Spellbook has seen playing them, so the ones worth
#: reading are the ones that survive the cut.
ONE_AWAY_SHOWN = 8


@dataclass
class Panel:
    """Everything the deck page needs to talk about combos, including silence.

    `nothing_found` is a real answer and the most common one. The reference
    deck - 69 distinct cards, hand-built, the deck this whole project was
    written around - contains **zero** Spellbook combos. A page that renders an
    empty list for that is a page that looks broken; it has to say so instead.
    """

    lookup: ComboLookup | None = None
    included: list = None
    one_away: list = None
    stale: bool = True
    #: The run whose measurements are on the entries, or None if this deck has
    #: never been simulated since its combos were looked up. The panel says
    #: which, because "no number yet" and "measured as never" are different
    #: answers and only one of them is about the deck.
    measured_run: object = None
    #: `spellbook_id` -> the sentence saying why that combo has no number.
    refusals: dict = None

    def __post_init__(self):
        self.included = self.included or []
        self.one_away = self.one_away or []
        self.refusals = self.refusals or {}

    @property
    def asked(self) -> bool:
        return self.lookup is not None

    @property
    def failed(self) -> bool:
        return self.lookup is not None and not self.lookup.ok

    @property
    def nothing_found(self) -> bool:
        return bool(self.lookup) and self.lookup.ok and not self.included

    @property
    def measured(self) -> bool:
        return self.measured_run is not None

    @property
    def measured_count(self) -> int:
        """How many of the combos below carry a number."""
        return sum(
            1 for entry in (*self.included, *self.one_away)
            if getattr(entry, "measurement", None) is not None
        )

    @property
    def min_measurable(self) -> int:
        """The smallest run that can price a missing card.

        Asked of `measure` rather than repeated here: a number the page quotes
        and a number the planner enforces have to be the same number, and two
        copies of it are one edit away from disagreeing.
        """
        from combos import measure

        return measure.MIN_SAMPLE * measure.BUDGET_DIVISOR

    @property
    def needs_a_run(self) -> bool:
        """Whether there is something to measure and nothing has measured it.

        This is the sentence that turns a list into a reason to press the
        button: the combos are there, and what they are worth is one simulation
        away.
        """
        return bool(self.lookup and self.lookup.ok
                    and (self.included or self.one_away)
                    and self.measured_run is None)

    @property
    def one_away_top(self) -> list:
        """The most-played of them, which is the only sane order for 125 rows."""
        return self.one_away[:ONE_AWAY_SHOWN]

    @property
    def one_away_rest(self) -> list:
        return self.one_away[ONE_AWAY_SHOWN:]


def panel_for(deck) -> Panel:
    """The cached answer, shaped for a template. Never fetches.

    Never simulates either, and for the same reason: a page render is not
    allowed to start work that costs anybody anything. The measurements it
    shows were made by a run somebody asked for.
    """
    lookup = lookup_for(deck)
    if lookup is None:
        return Panel()

    entries = list(
        lookup.entries.select_related("combo")
        .prefetch_related("combo__cards", "combo__templates")
    )
    run, measurements, refusals = _measurements_for(deck)
    for entry in entries:
        # Set on the row rather than passed as a parallel dict, so the two
        # templates that render these rows cannot disagree about which
        # measurement belongs to which combo - and because a Django template
        # cannot look a dictionary up by a variable key, which is exactly the
        # kind of limitation that ends in the reason being dropped.
        entry.measurement = measurements.get(entry.combo_id)
        entry.no_number = _worth_saying(refusals.get(entry.combo_id, ""))

    return Panel(
        lookup=lookup,
        included=[e for e in entries if e.kind == DeckCombo.Kind.INCLUDED],
        one_away=[e for e in entries if e.kind == DeckCombo.Kind.ONE_AWAY],
        stale=is_stale(lookup, deck),
        measured_run=run,
        refusals=refusals,
    )


def _worth_saying(reason: str) -> str:
    """A refusal belongs on a row only when it is about that combo.

    "This run had no room for it" is true of most of a list of 125 and printing
    it beside each one is six identical lines of noise where the eye is looking
    for a number. It is said once, in the summary, where it is a fact about the
    run rather than about the combo. What stays on the row is the kind of
    refusal a person could act on: a template nothing can resolve, a card this
    catalogue has not got, a zone this engine does not model.
    """
    from combos import measure

    return "" if reason == measure.NOT_CHOSEN else reason


def _measurements_for(deck) -> tuple:
    """The last run that measured this deck's combos, and what it found.

    One run rather than the best of several. Numbers from different runs have
    different game counts, different turn counts and a deck that may have
    changed between them; putting them in one column would invite a comparison
    that is not there to be made.
    """
    latest = (
        ComboMeasurement.objects.filter(deck=deck)
        .select_related("run")
        .order_by("-measured_at")
        .first()
    )
    if latest is None:
        return None, {}, {}

    rows = ComboMeasurement.objects.filter(deck=deck, run_id=latest.run_id)
    # Imported here rather than at module level: `measure` reads the engine
    # through `simulations.engine`, and the deck page should not pay for that
    # import on every request that never renders a combo.
    from combos import measure

    return (
        latest.run,
        {row.combo_id: row for row in rows},
        measure.plan_for(latest.run).refusals,
    )
