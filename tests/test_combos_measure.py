"""Phase 7 §2: how long a combo takes to come together.

This is the feature the whole product rests on. Everyone can tell a player that
their deck contains a combo; only a simulator can tell them it assembles by
turn six in four percent of games, and only an honest one can say what it had
to assume to get there.

So these tests are mostly about the assumptions, in the order they can do
damage:

1. **A zone is not a synonym for "drawn".** A card that has to be on the
   battlefield is not assembled by sitting in hand. Getting this wrong would
   inflate every number on the page and nothing would notice.
2. **The hypothetical is stated and never hidden.** A combo the deck is one
   card short of is measured on the deck **plus** that card - one card larger,
   nothing cut - and the measurement carries the card's name.
3. **A combo that cannot be measured honestly gets no number**, and gets a
   sentence instead. A template ("any creature with persist") is a Scryfall
   search, not a card, and a silent zero beside it would be a lie.
4. **The cost is bounded and it is the run's own.** `billing/quotas.py`
   documents exactly four `check()` call sites and a fifth would be a bug, so
   the hypotheticals are rationed out of the run's own budget.
"""

from dataclasses import dataclass, field

import pytest
from django.contrib.auth import get_user_model

from billing.models import UsageRecord
from billing.quotas import period_start
from cards.models import OracleCard
from combos import measure, services
from combos.models import (
    Combo,
    ComboCard,
    ComboLookup,
    ComboMeasurement,
    ComboTemplate,
    DeckCombo,
)
from decks import services as deck_services
from simulation import analysis
from simulation import combos as engine_combos
from simulations import report, tasks
from simulations import services as sim_services
from simulations.engine import adapter
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "pw-for-test-only"

#: Small, cheap and real. Carrion Feeder and Gravecrawler both cost one mana,
#: so a deck this land-heavy assembles them often enough that a test does not
#: have to play ten thousand games to see one.
DECK_LIST = (
    b"1 Carrion Feeder\n"
    b"1 Gravecrawler\n"
    b"1 Blood Artist\n"
    b"1 Exquisite Blood\n"
    b"30 Swamp\n"
)


# --- a game, without playing one -------------------------------------------


@dataclass
class FakeCard:
    name: str


@dataclass
class FakeDeck:
    commander: object = None


@dataclass
class FakeGame:
    """Just enough game for the watcher, and no turn sequence at all.

    The watcher reads zones and nothing else, so a stub says exactly what a
    state is - which is what makes "in hand is not on the battlefield" a test
    of one rule rather than of the whole engine.
    """

    lands: list = field(default_factory=list)
    rocks: list = field(default_factory=list)
    creatures: list = field(default_factory=list)
    other_permanents: list = field(default_factory=list)
    graveyard: list = field(default_factory=list)
    hand: list = field(default_factory=list)
    exiled: list = field(default_factory=list)
    library: list = field(default_factory=list)
    deck: FakeDeck = field(default_factory=FakeDeck)

    @property
    def battlefield(self):
        return self.lands + self.rocks + self.creatures + self.other_permanents


def watch(key, *requirements):
    return engine_combos.Watch(key=key, requirements=tuple(requirements))


def require(name, zones=("B",), quantity=1, must_be_commander=False):
    return engine_combos.Requirement(
        name=name, zones=tuple(zones), quantity=quantity,
        must_be_commander=must_be_commander,
    )


# --- zones, which decide what "assembled" means ----------------------------


def test_a_card_in_hand_does_not_assemble_a_battlefield_combo():
    """The single assumption that would inflate every number on the page.

    Measuring "the deck has seen these cards" instead of "they are where they
    have to be" is both easier and wrong, and nothing downstream would catch
    it: the percentages would simply all be larger.
    """
    watcher = engine_combos.Watcher([watch("x", require("Carrion Feeder"))])
    game = FakeGame(hand=[FakeCard("Carrion Feeder")])

    watcher.look(game, 1)
    assert watcher.first["x"] == 0

    game.creatures.append(FakeCard("Carrion Feeder"))
    watcher.look(game, 2)
    assert watcher.first["x"] == 2


def test_a_card_that_has_to_be_in_the_graveyard_is_not_assembled_from_the_library():
    """Gravecrawler's combos want it dead, not drawn."""
    watcher = engine_combos.Watcher([watch("x", require("Gravecrawler", zones=("G",)))])
    game = FakeGame(library=[FakeCard("Gravecrawler")])

    watcher.look(game, 1)
    assert watcher.first["x"] == 0

    game.graveyard.append(FakeCard("Gravecrawler"))
    watcher.look(game, 2)
    assert watcher.first["x"] == 2


def test_two_zones_mean_either_one():
    """Spellbook's list of zones is alternatives, not a conjunction."""
    watcher = engine_combos.Watcher([watch("x", require("Bloodghast", zones=("B", "G")))])

    watcher.look(FakeGame(graveyard=[FakeCard("Bloodghast")]), 1)
    assert watcher.first["x"] == 1


def test_the_commander_is_in_the_command_zone_until_it_is_cast():
    general = FakeCard("Chainer, Dementia Master")
    watcher = engine_combos.Watcher([watch("x", require(general.name, zones=("C",)))])
    game = FakeGame(deck=FakeDeck(commander=general))

    watcher.look(game, 1)
    assert watcher.first["x"] == 1, "a commander nobody has cast is in the command zone"

    later = engine_combos.Watcher([watch("x", require(general.name, zones=("C",)))])
    later.look(FakeGame(deck=FakeDeck(commander=general), creatures=[general]), 1)
    assert later.first["x"] == 0, "once it is out it is not in the command zone"


def test_a_combo_that_needs_a_particular_card_as_commander_says_so():
    general = FakeCard("Chainer, Dementia Master")
    requirement = require("Gravecrawler", must_be_commander=True)
    watcher = engine_combos.Watcher([watch("x", requirement)])

    watcher.look(FakeGame(deck=FakeDeck(commander=general),
                          creatures=[FakeCard("Gravecrawler")]), 1)

    assert watcher.first["x"] == 0


def test_two_copies_are_two_copies():
    watcher = engine_combos.Watcher([watch("x", require("Swamp", quantity=2))])
    game = FakeGame(lands=[FakeCard("Swamp")])

    watcher.look(game, 1)
    assert watcher.first["x"] == 0

    game.lands.append(FakeCard("Swamp"))
    watcher.look(game, 2)
    assert watcher.first["x"] == 2


def test_a_zone_this_engine_has_not_got_is_refused_rather_than_guessed_at():
    """The stack is real in Magic and meaningless in an end-of-turn snapshot.

    Refusing is what turns it into a sentence on the page. Guessing "call it
    the battlefield" would produce a number that is confidently wrong, which is
    the one output this application is built not to produce.
    """
    with pytest.raises(engine_combos.Unmeasurable):
        require("Underworld Breach", zones=("S",))

    with pytest.raises(engine_combos.Unmeasurable):
        require("Underworld Breach", zones=())


def test_the_first_turn_it_came_together_is_the_one_kept():
    """A combo broken up on turn five still assembled on turn four."""
    watcher = engine_combos.Watcher([watch("x", require("Carrion Feeder"))])
    game = FakeGame(creatures=[FakeCard("Carrion Feeder")])

    watcher.look(game, 3)
    game.creatures.clear()
    watcher.look(game, 4)

    assert watcher.first["x"] == 3


# --- counting across games -------------------------------------------------


def test_watching_nothing_leaves_the_result_exactly_as_it_was():
    """The golden parity snapshot depends on this, and so does every old run.

    A result that grew a key whenever the feature was compiled in would make
    the one piece of evidence that the engine still behaves as it used to
    into a thing that needs regenerating.
    """
    assert "combos" not in analysis.as_json(analysis.run(5, turns=2))


def test_the_counts_are_cumulative_and_merge_by_addition():
    watched = watch("x", require("Swamp"))
    first = analysis.as_json(analysis.run(40, turns=3, seed=1, watch=[watched]))
    second = analysis.as_json(analysis.run(40, turns=3, seed=2, watch=[watched]))

    merged = analysis.merge([first, second])["combos"]["x"]

    assert merged["games"] == 80
    assert merged["by_turn"] == [
        a + b for a, b in zip(first["combos"]["x"]["by_turn"],
                              second["combos"]["x"]["by_turn"], strict=True)
    ]
    assert merged["by_turn"] == sorted(merged["by_turn"]), "cumulative never falls"


def test_a_chunk_that_watched_a_combo_the_others_did_not_is_kept():
    """A lookup refreshed mid-run leaves chunks watching different things.

    Both answers are true about the games that were played, and `games` says
    how many those were - so the union is the honest merge and dropping either
    would throw away work somebody paid for.
    """
    one = analysis.as_json(analysis.run(20, turns=2, seed=1, watch=[watch("a", require("Swamp"))]))
    two = analysis.as_json(analysis.run(20, turns=2, seed=2, watch=[watch("b", require("Swamp"))]))

    merged = analysis.merge([one, two])["combos"]

    assert set(merged) == {"a", "b"}
    assert merged["a"]["games"] == 20
    assert merged["b"]["games"] == 20


def test_two_hypotheticals_in_one_chunk_do_not_play_the_same_games():
    """Otherwise their percentages would agree for a reason nobody asked about."""
    seeds = {analysis.sample_seed(7, 0, key) for key in ("a", "b", "c")}

    assert len(seeds) == 3
    assert analysis.sample_seed(7, 0, "a") == analysis.sample_seed(7, 0, "a")
    assert analysis.sample_seed(7, 0, "a") != analysis.sample_seed(7, 1, "a")


# --- the database side -----------------------------------------------------


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="measure@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(owner=owner, raw=DECK_LIST, name="Measured").deck


@pytest.fixture
def lookup(deck):
    return ComboLookup.objects.create(
        deck=deck, fingerprint=services.fingerprint(deck),
        status=ComboLookup.Status.OK, identity="B",
    )


def make_combo(spellbook_id, names, *, zones=("B",), templates=(), popularity=1):
    combo = Combo.objects.create(spellbook_id=spellbook_id, popularity=popularity)
    for name in names:
        card = OracleCard.objects.filter(front_name=name).first()
        ComboCard.objects.create(
            combo=combo, name=name, oracle_card=card,
            oracle_id=card.oracle_id if card else "", zones=list(zones),
        )
    for template in templates:
        ComboTemplate.objects.create(combo=combo, name=template)
    return combo


def link(lookup, combo, kind, missing=()):
    return DeckCombo.objects.create(
        lookup=lookup, combo=combo, kind=kind, missing=list(missing)
    )


@pytest.fixture
def contained(lookup):
    """A combo this deck holds outright."""
    combo = make_combo("held-1", ["Carrion Feeder", "Gravecrawler"], popularity=50)
    link(lookup, combo, DeckCombo.Kind.INCLUDED)
    return combo


@pytest.fixture
def one_away(lookup):
    """A combo this deck is one card short of, and the card is one we have."""
    combo = make_combo("away-1", ["Carrion Feeder", "Ashnod's Altar"], popularity=40)
    link(lookup, combo, DeckCombo.Kind.ONE_AWAY, missing=["Ashnod's Altar"])
    return combo


@pytest.fixture
def run(owner, deck):
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, turns=2, seed=99, chunks_total=2
    )


# --- what gets measured, and what gets a sentence --------------------------


def test_a_combo_the_deck_holds_is_watched_in_the_runs_own_games(run, contained):
    plan = measure.plan_for(run)

    assert [item.entry.combo_id for item in plan.watched] == ["held-1"]
    assert plan.extra_games == 0, "a combo the deck holds costs no extra games at all"


def test_the_pieces_of_a_held_combo_are_the_decks_key_cards(run, contained):
    """Phase 10 N1: what a tutor with no priority list goes for first."""
    plan = measure.plan_for(run)
    pieces = {requirement.name for requirement in plan.watched[0].watch.requirements}

    assert pieces and plan.key_cards == pieces
    deck = measure.with_key_cards(adapter.deck_definition(run.deck), plan)
    assert deck.key_cards == pieces


def test_a_hypothetical_deck_knows_the_pieces_of_its_combo(run, one_away, monkeypatch):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)

    samples = measure.samples_for(run, measure.plan_for(run), index=0, chunks=2)

    assert "Ashnod's Altar" in samples[0].deck.key_cards


def test_a_combo_needing_a_template_gets_no_number_and_a_reason(run, lookup):
    """The honesty trap of the whole feature, from §1, arriving in §2.

    Mikaeus + Carrion Feeder also needs *a creature with persist*. A deck
    holding both named cards does not have the combo, and a percentage beside
    it would say that it does.
    """
    combo = make_combo("tmpl-1", ["Mikaeus, the Unhallowed", "Carrion Feeder"],
                       templates=["Persist Creature"])
    link(lookup, combo, DeckCombo.Kind.ONE_AWAY, missing=["Mikaeus, the Unhallowed"])

    plan = measure.plan_for(run)

    assert not plan.watched and not plan.hypotheticals
    assert plan.refusals["tmpl-1"] == measure.NEEDS_TEMPLATE


def test_a_missing_card_we_have_never_ingested_gets_a_reason_not_a_number(run, lookup):
    combo = make_combo("away-2", ["Carrion Feeder", "Basalt Monolith"])
    link(lookup, combo, DeckCombo.Kind.ONE_AWAY, missing=["Basalt Monolith"])

    plan = measure.plan_for(run)

    assert not plan.hypotheticals
    assert plan.refusals["away-2"] == measure.NOT_IN_CATALOGUE


def test_a_combo_two_cards_short_is_not_pretended_to_be_one(run, lookup):
    combo = make_combo("away-3", ["Ashnod's Altar", "Animate Dead", "Carrion Feeder"])
    link(lookup, combo, DeckCombo.Kind.ONE_AWAY,
         missing=["Ashnod's Altar", "Animate Dead"])

    plan = measure.plan_for(run)

    assert not plan.hypotheticals
    assert plan.refusals["away-3"] == measure.MISSING_SEVERAL


def test_spellbook_saying_the_deck_has_it_does_not_beat_our_own_record(run, lookup):
    """Our record of the deck is the one the page is about."""
    combo = make_combo("held-2", ["Carrion Feeder", "Sanguine Bond"])
    link(lookup, combo, DeckCombo.Kind.INCLUDED, missing=["Sanguine Bond"])

    plan = measure.plan_for(run)

    assert not plan.watched
    assert plan.refusals["held-2"] == measure.NOT_REALLY_HELD


def test_a_failed_lookup_is_not_measured_at_all(run, contained, lookup):
    lookup.status = ComboLookup.Status.FAILED
    lookup.save(update_fields=["status"])

    assert not measure.plan_for(run)


# --- the budget ------------------------------------------------------------


def test_a_run_too_small_to_price_a_card_prices_none(run, one_away):
    """And that is a real answer, not a failure.

    A thousand games puts half a point of sampling error on a 5% number; three
    hundred puts a point and a half on it, which is wider than the differences
    the page would be inviting somebody to read.
    """
    assert measure.budget(1_000, 5) == (0, 0)
    assert measure.plan_for(run).hypotheticals == ()
    assert measure.plan_for(run).refusals["away-1"] == measure.NOT_CHOSEN


def test_a_run_big_enough_measures_the_most_played_few():
    count, games = measure.budget(10_000, 8)

    assert count == measure.MAX_HYPOTHETICALS
    assert count * games <= 10_000 // measure.BUDGET_DIVISOR
    assert games >= measure.MIN_SAMPLE


def test_the_budget_never_spends_more_than_half_the_run_again():
    for games_total in (2_000, 10_000, 100_000, 1_000_000):
        count, games = measure.budget(games_total, 8)
        assert count * games <= games_total // 2, games_total
        assert games <= measure.MAX_SAMPLE


def test_the_chunks_of_a_sample_add_up_to_exactly_the_sample():
    """A sample that plays 997 of the 1,000 games it claims puts a wrong
    denominator under every percentage it produces."""
    item = measure.Hypothetical(entry=None, card=None, watch=None, games=1_000)

    for chunks in (1, 3, 7, 200):
        assert sum(item.share(chunks, i) for i in range(chunks)) == 1_000


def test_the_most_played_are_the_ones_measured(run, lookup, monkeypatch):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)
    for index, popularity in enumerate((10, 900, 500)):
        combo = make_combo(f"pop-{index}", ["Carrion Feeder", "Ashnod's Altar"],
                           popularity=popularity)
        link(lookup, combo, DeckCombo.Kind.ONE_AWAY, missing=["Ashnod's Altar"])

    chosen = [item.entry.combo_id for item in measure.plan_for(run).hypotheticals]

    assert chosen == ["pop-1", "pop-2", "pop-0"]


# --- the hypothetical deck -------------------------------------------------


def test_the_hypothetical_deck_is_one_card_larger_and_nothing_is_cut(deck):
    """Choosing which card a deck can spare is not this application's call."""
    real = adapter.deck_definition(deck)
    card = OracleCard.objects.get(front_name="Ashnod's Altar")

    hypothetical = adapter.deck_definition(deck, adding=card)

    assert len(hypothetical.library) == len(real.library) + 1
    assert [c.name for c in real.library] == [
        c.name for c in hypothetical.library[:len(real.library)]
    ]
    assert hypothetical.library[-1].name == "Ashnod's Altar"


def test_the_added_card_is_the_one_the_combo_was_missing(run, one_away, monkeypatch):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)

    samples = measure.samples_for(run, measure.plan_for(run), index=0, chunks=2)

    assert [sample.key for sample in samples] == ["away-1"]
    assert any(card.name == "Ashnod's Altar" for card in samples[0].deck.library)


# --- storing, and what the numbers mean ------------------------------------


def test_a_measurement_carries_its_own_sample_size(run, contained):
    measure.store(run, {"held-1": {"games": 40, "by_turn": [4, 10]}})

    row = ComboMeasurement.objects.get(combo_id="held-1")
    assert row.games == 40
    assert row.turns == 2
    assert row.share == pytest.approx(25.0)
    assert row.assembled == 10
    assert not row.hypothetical


def test_a_hypothetical_measurement_names_the_card_that_was_added(run, one_away,
                                                                  monkeypatch):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)

    extra = measure.store(run, {"away-1": {"games": 20, "by_turn": [1, 3]}})

    row = ComboMeasurement.objects.get(combo_id="away-1")
    assert row.hypothetical and row.added_name == "Ashnod's Altar"
    assert extra == 20, "the hypothetical's games are real games and are counted"


def test_a_combo_the_run_did_not_plan_for_is_dropped_rather_than_guessed_at(run):
    assert measure.store(run, {"never-heard-of-it": {"games": 5, "by_turn": [1]}}) == 0
    assert not ComboMeasurement.objects.exists()


def test_never_assembling_is_an_answer_and_not_a_missing_one(run, contained):
    measure.store(run, {"held-1": {"games": 40, "by_turn": [0, 0]}})

    row = ComboMeasurement.objects.get(combo_id="held-1")
    assert row.never and row.share == 0.0
    assert row.median_turn == 0


def test_a_share_below_one_percent_is_a_bound_and_not_a_rounded_zero(run, contained):
    """0.6% rounds to zero, and zero reads as *never* beside a combo that worked.

    Measured on the reference deck: Gravecrawler + Phyrexian Altar comes
    together in 0.6% of games by turn six. Printing "0%" there would contradict
    the row above it, which says it happened.
    """
    measure.store(run, {"held-1": {"games": 1_000, "by_turn": [0, 6]}})

    row = ComboMeasurement.objects.get(combo_id="held-1")
    assert not row.never
    assert row.share_label == "under 1%"


def test_a_share_worth_a_whole_number_gets_one(run, contained):
    measure.store(run, {"held-1": {"games": 100, "by_turn": [10, 25]}})

    assert ComboMeasurement.objects.get(combo_id="held-1").share_label == "25%"


def test_never_is_printed_as_the_word(run, contained):
    measure.store(run, {"held-1": {"games": 100, "by_turn": [0, 0]}})

    assert ComboMeasurement.objects.get(combo_id="held-1").share_label == "never"


def test_half_of_the_games_that_worked_is_read_off_the_curve(run, contained):
    measure.store(run, {"held-1": {"games": 100, "by_turn": [2, 9]}})

    assert ComboMeasurement.objects.get(combo_id="held-1").median_turn == 2


# --- end to end, through the tasks that a worker runs ----------------------


def test_a_run_measures_the_combo_it_has_and_the_one_it_is_short_of(
    run, contained, one_away, fake_redis, monkeypatch
):
    """The whole pass, through the two tasks a worker actually executes."""
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)

    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))

    rows = {row.combo_id: row for row in ComboMeasurement.objects.all()}
    assert set(rows) == {"held-1", "away-1"}
    assert rows["held-1"].games == 40, "measured in the run's own games"
    assert rows["away-1"].games == 20, "measured in a sample of its own"
    assert rows["away-1"].added_name == "Ashnod's Altar"
    assert rows["held-1"].assembled > 0, "one-mana cards in a 34-card deck do turn up"


def test_the_hypotheticals_games_are_recorded_as_usage(run, contained, one_away,
                                                       fake_redis, monkeypatch, owner):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)

    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))

    used = UsageRecord.objects.get(
        user=owner, metric=UsageRecord.Metric.GAMES_SIMULATED,
        period_start=period_start(),
    )
    assert used.amount == 40 + 20


def test_a_measurement_that_cannot_be_stored_never_fails_the_run(run, contained,
                                                                 fake_redis, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("the lookup moved")

    monkeypatch.setattr(measure, "store", explode)

    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))

    run.refresh_from_db()
    assert run.status == SimulationRun.Status.DONE
    assert run.result


# --- what a person sees ----------------------------------------------------


@pytest.fixture
def fake_redis(monkeypatch):
    """The concurrency counter, without a broker."""
    values = {}

    class FakeRedis:
        def incr(self, key):
            values[key] = values.get(key, 0) + 1
            return values[key]

        def decr(self, key):
            values[key] = values.get(key, 0) - 1
            return values[key]

        def expire(self, key, seconds):
            return True

        def set(self, key, value, ex=None):
            values[key] = value
            return True

    monkeypatch.setattr(sim_services, "_redis", lambda: FakeRedis())
    return values


def test_the_panel_asks_for_a_run_when_nothing_has_timed_anything(client, owner, deck,
                                                                  contained):
    client.login(email=owner.email, password=PASSWORD)

    page = client.get(deck.get_absolute_url())

    assert page.status_code == 200
    assert services.panel_for(deck).needs_a_run
    assert b"Nothing has timed these yet" in page.content


def test_the_panel_prints_the_number_and_names_the_added_card(client, owner, deck, run,
                                                              one_away, monkeypatch):
    monkeypatch.setattr(measure, "MIN_SAMPLE", 5)
    measure.store(run, {"away-1": {"games": 20, "by_turn": [1, 5]}})
    client.login(email=owner.email, password=PASSWORD)

    page = client.get(deck.get_absolute_url()).content.decode()

    assert "Ashnod&#x27;s Altar" in page
    assert "25%" in page
    assert "nothing was cut to make room" in page


def test_a_combo_with_no_number_says_why_rather_than_showing_a_blank(client, owner,
                                                                     deck, run, lookup):
    combo = make_combo("tmpl-2", ["Mikaeus, the Unhallowed", "Carrion Feeder"],
                       templates=["Persist Creature"])
    link(lookup, combo, DeckCombo.Kind.ONE_AWAY, missing=["Mikaeus, the Unhallowed"])
    # One measured row, so the panel has a run to hang the reasons off.
    make_combo("held-3", ["Carrion Feeder", "Gravecrawler"])
    link(lookup, Combo.objects.get(pk="held-3"), DeckCombo.Kind.INCLUDED)
    measure.store(run, {"held-3": {"games": 40, "by_turn": [1, 2]}})
    client.login(email=owner.email, password=PASSWORD)

    page = client.get(deck.get_absolute_url()).content.decode()

    assert "No timing for this one" in page
    assert measure.NEEDS_TEMPLATE in page


def test_the_panel_shows_one_runs_numbers_and_not_a_mixture(deck, run, owner, contained):
    """Numbers from two runs have two game counts and possibly two decks."""
    measure.store(run, {"held-1": {"games": 40, "by_turn": [1, 2]}})
    later = SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=80, turns=2, seed=7, chunks_total=1
    )
    measure.store(later, {"held-1": {"games": 80, "by_turn": [8, 20]}})

    panel = services.panel_for(deck)

    assert panel.measured_run.pk == later.pk
    assert panel.included[0].measurement.games == 80


def test_the_report_lists_what_the_run_measured(run, contained):
    measure.store(run, {"held-1": {"games": 40, "by_turn": [1, 2]}})

    measured = report.combo_measurements(run)

    assert [row.combo_id for row in measured] == ["held-1"]
