"""The data pages: every Commander precon, simulated (P11).

* MTGJSON's list is read for store precons since 2025: no Collector's
  Edition, no Secret Lair, no older deck, no other deck type.
* Cards are matched by oracle id; a list with an unknown card is held and
  gets no deck, run or page.
* A precon is the system account's deck, simulated with the same settings
  every time, on the long queue, with no quota, summary or count touched;
  an unchanged list is not simulated again, a changed one is.
* The table and each precon's page answer anybody in every language, are in
  the sitemap with their hreflang links, and only show a finished run; the
  precon's /r/ link sends to its page; unpublished is 404.
* The system account is nobody on /admin/stats/; a page opened is counted,
  a robot's visit is not.
* The land sweep rebuilds the best-read precons at every land count by the
  stated rules, once per list; the article shows it when every run is done.
"""

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import translation

from billing.models import UsageRecord
from cards.models import OracleCard
from datapages import mtgjson, numbers, services, sweep
from datapages import tasks as datapage_tasks
from datapages.models import LandSweep, Precon
from metrics import report as metrics_report
from metrics.models import DailyCount
from sharing.models import SharedReport
from simulations import services as run_services
from simulations import tasks
from simulations.models import SimulationRun
from tests.test_i18n import FakeRedis

pytestmark = pytest.mark.django_db

User = get_user_model()
SOURCE = Path(__file__).resolve().parent / "fixtures" / "mtgjson"
SITE = "https://goldfishlab.app"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
ALL_LANGUAGES = [(code, name) for code, name in django_settings.LANGUAGE_NAMES.items()]
GAMES = 40


@pytest.fixture(autouse=True)
def small(monkeypatch, settings):
    """A few games, not ten thousand; the same code path otherwise."""
    monkeypatch.setattr(services, "GAMES", GAMES)
    monkeypatch.setattr(run_services, "_redis", FakeRedis)
    settings.SITE_URL = SITE


def _finish(run):
    chunks = [tasks.simulate_chunk(str(run.pk), index, GAMES // 2) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()
    return run


@pytest.fixture
def imported(catalogue):
    return services.refresh(source=SOURCE)


@pytest.fixture
def precon(imported):
    precon = Precon.objects.get(name="Grim Knights")
    _finish(precon.deck.runs.get())
    return precon


def _get(client, url, **headers):
    headers.setdefault("HTTP_USER_AGENT", BROWSER)
    return client.get(url, **headers)


# --- reading MTGJSON ---------------------------------------------------------------


def test_only_store_precons_since_2025_are_read():
    names = [listing.name for listing in mtgjson.listings(SOURCE)]

    assert names == ["Grim Knights", "Odd One Out"]


def test_a_card_carries_its_oracle_id_and_the_commander_is_marked():
    listing = mtgjson.listings(SOURCE)[0]
    rows = mtgjson.rows(mtgjson.deck(listing, SOURCE))

    assert [row.name for row in rows if row.is_commander] == ["Syr Konrad, the Grim"]
    assert all(row.oracle_id for row in rows)
    assert sum(row.quantity for row in rows) == 100


def test_an_address_that_is_not_https_is_refused(settings):
    settings.MTGJSON_URL = "http://mtgjson.com/api/v5/"

    with pytest.raises(mtgjson.MTGJSONError, match="not https"):
        mtgjson.listings()


def test_a_missing_file_is_an_error_not_a_crash(tmp_path):
    with pytest.raises(mtgjson.MTGJSONError):
        mtgjson.listings(tmp_path)


# --- importing -----------------------------------------------------------------------


def test_a_precon_becomes_the_system_accounts_deck_and_is_simulated(imported):
    precon = Precon.objects.get(name="Grim Knights")
    deck = precon.deck

    assert ("created", str(precon)) in imported.lines
    assert precon.set_name == "Test Set Commander"
    assert precon.slug == "grim-knights-tst"
    assert precon.released == date(2026, 3, 6)
    assert deck.owner.is_system and not deck.owner.is_active
    assert not deck.owner.has_usable_password()
    assert deck.commander.name == "Syr Konrad, the Grim"
    assert deck.card_count == 99
    run = deck.runs.get()
    assert (run.games_total, run.turns, run.on_the_play) == (GAMES, services.TURNS, False)
    assert run.seed == services.seed_for(precon.slug)
    assert run_services.queue_of(run) == run_services.LONG_QUEUE
    assert SharedReport.objects.get(run=run).card_count == 99


def test_a_list_with_an_unknown_card_is_held(imported):
    held = Precon.objects.get(name="Odd One Out")

    assert ("held", str(held)) in imported.lines
    assert held.deck is None
    assert held.unmatched == ["Card From Next Week"]


def test_the_system_account_is_charged_nothing_and_counted_nowhere(imported):
    lab = services.lab_account()

    assert not UsageRecord.objects.filter(
        user=lab, metric=UsageRecord.Metric.RUNS_STARTED).exists()
    assert not DailyCount.objects.exists()
    assert lab.simulated_week is None


def test_a_lab_run_is_refused_to_anybody_else(catalogue):
    person = User.objects.create_user(email="person@example.com", password="pw-test-only")
    with pytest.raises(ValueError):
        run_services.start_lab_run(owner=person, deck=None, games=1, turns=1,
                                   on_the_play=False, seed=1)


def test_an_unchanged_list_is_not_simulated_again(imported):
    again = services.refresh(source=SOURCE)

    assert ("unchanged", "Grim Knights (TST)") in again.lines
    assert SimulationRun.objects.filter(owner__is_system=True).count() == 1


def test_rerun_simulates_every_precon_again(imported):
    again = services.refresh(source=SOURCE, rerun=True)

    assert ("rerun", "Grim Knights (TST)") in again.lines
    assert SimulationRun.objects.filter(owner__is_system=True).count() == 2


def test_a_changed_list_is_simulated_again(imported, tmp_path):
    source = tmp_path / "mtgjson"
    shutil.copytree(SOURCE, source)
    path = source / "decks" / "GrimKnights_TST.json"
    data = json.loads(path.read_text())
    swamps = next(card for card in data["data"]["mainBoard"] if card["name"] == "Swamp")
    swamps["count"] -= 1
    island = OracleCard.objects.get(name="Island")
    data["data"]["mainBoard"].append({"name": "Island", "count": 1, "identifiers": {
        "scryfallOracleId": str(island.oracle_id)}})
    path.write_text(json.dumps(data))

    again = services.refresh(source=source)

    assert ("changed", "Grim Knights (TST)") in again.lines
    deck = Precon.objects.get(name="Grim Knights").deck
    assert deck.entries.filter(oracle_card__name="Island").exists()
    assert deck.runs.count() == 2


def test_a_held_list_is_taken_once_its_cards_are_known(imported, tmp_path):
    source = tmp_path / "mtgjson"
    shutil.copytree(SOURCE, source)
    shutil.copy(source / "decks" / "GrimKnights_TST.json", source / "decks" / "OddOneOut_TST.json")
    data = json.loads((source / "decks" / "OddOneOut_TST.json").read_text())
    data["data"]["name"] = "Odd One Out"
    (source / "decks" / "OddOneOut_TST.json").write_text(json.dumps(data))

    again = services.refresh(source=source)

    assert ("created", "Odd One Out (TST)") in again.lines
    assert Precon.objects.get(name="Odd One Out").unmatched == []


def test_a_plain_list_makes_a_precon_before_mtgjson_has_it(catalogue):
    text = "// Commander\n1 Syr Konrad, the Grim\n// Deck\n1 Sol Ring\n98 Swamp\n"

    precon, what = services.from_text(text, name="Final Frontier", set_code="tst",
                                      released=date(2026, 11, 13))

    assert what == "created"
    assert precon.source == Precon.Source.TEXT
    assert precon.set_code == "TST"
    assert precon.deck.commander.name == "Syr Konrad, the Grim"
    assert precon.deck.card_count == 99


def test_the_command_dry_run_changes_nothing(catalogue, capsys):
    call_command("precons", "--dry-run", "--source", str(SOURCE))

    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "created  Grim Knights (TST)" in out
    assert not Precon.objects.exists()
    assert not SimulationRun.objects.exists()


def test_the_command_wants_all_of_a_plain_lists_facts(catalogue, tmp_path):
    from django.core.management.base import CommandError

    path = tmp_path / "deck.txt"
    path.write_text("1 Sol Ring\n")
    with pytest.raises(CommandError, match="--set, --released"):
        call_command("precons", "--from-text", str(path), "--name", "X")


def test_the_weekly_task_survives_mtgjson_being_away(monkeypatch):
    def away(**kwargs):
        raise mtgjson.MTGJSONError("down")

    monkeypatch.setattr(services, "refresh", away)

    assert datapage_tasks.refresh() == "mtgjson unreachable"


# --- the numbers ------------------------------------------------------------------------


def test_the_numbers_come_from_the_run(precon):
    found = numbers.of(services.report_of(precon).run)

    assert found.games == GAMES
    assert found.lands == services.report_of(precon).run.lands_total == 39
    assert 0 <= found.on_curve <= 100
    assert 0 <= found.kept_seven <= 100
    assert found.mana_turn_4 >= 0
    assert len(numbers.sentences(found)) == 3


# --- the pages ---------------------------------------------------------------------------


def test_the_table_lists_every_finished_precon(client, precon):
    response = _get(client, reverse("datapages:precons"))

    page = response.content.decode()
    assert response.status_code == 200
    assert precon.get_absolute_url() in page
    assert "Odd One Out" not in page
    assert "Test Set Commander" in page


def test_a_precon_whose_run_is_not_finished_has_no_page(client, imported):
    precon = Precon.objects.get(name="Grim Knights")

    assert _get(client, precon.get_absolute_url()).status_code == 404
    assert "Grim Knights" not in _get(client, reverse("datapages:precons")).content.decode()


def test_a_precons_page_shows_its_report_and_sentences(client, precon):
    response = _get(client, precon.get_absolute_url())

    page = response.content.decode()
    assert response.status_code == 200
    assert "Syr Konrad, the Grim" in page
    assert "of games it has four lands in play on turn 4" in page
    assert "MTGJSON" in page
    assert 'name="robots"' not in page or "noindex" not in page


def test_an_unpublished_precon_is_gone(client, precon):
    Precon.objects.filter(pk=precon.pk).update(published=False)

    assert _get(client, precon.get_absolute_url()).status_code == 404


@pytest.mark.parametrize("sort", ["lands", "on_curve", "kept_seven", "mana", "read", "name",
                                  "<script>", "-released"])
def test_any_sort_answers(client, precon, sort):
    response = _get(client, reverse("datapages:precons"), data={"sort": sort})

    assert response.status_code == 200
    assert "<script>" not in response.content.decode().split("<main", 1)[1].split("</main>")[0]


def test_the_precons_share_link_sends_to_its_page(client, precon):
    shared = services.report_of(precon)

    response = _get(client, shared.get_absolute_url())

    assert response.status_code == 301
    assert response["Location"] == precon.get_absolute_url()


@pytest.mark.parametrize("language", [code for code, _ in ALL_LANGUAGES])
def test_the_pages_answer_in_every_language(client, settings, precon, language):
    settings.LANGUAGES = ALL_LANGUAGES
    with translation.override(language):
        table, page = reverse("datapages:precons"), precon.get_absolute_url()

    for url in (table, page):
        response = _get(client, url)
        assert response.status_code == 200
        assert f'lang="{language}"' in response.content.decode()


def test_the_sitemap_lists_each_precon_in_each_language(client, settings, precon):
    settings.LANGUAGES = ALL_LANGUAGES

    sitemap = client.get("/sitemap.xml").content.decode()

    for code, _ in ALL_LANGUAGES:
        with translation.override(code):
            assert f"<loc>{SITE}{precon.get_absolute_url()}</loc>" in sitemap
    assert 'hreflang="x-default"' in sitemap


def test_opening_a_data_page_is_counted_but_not_by_a_robot(client, precon):
    _get(client, precon.get_absolute_url())
    _get(client, reverse("datapages:precons"))
    _get(client, reverse("datapages:precons"), HTTP_USER_AGENT="Googlebot/2.1")

    assert DailyCount.objects.get(name=DailyCount.Name.DATA_PAGE_OPENED).value == 2


def test_the_system_account_is_nobody_on_the_stats_page(precon):
    cohorts = metrics_report.cohorts()

    assert sum(cohort.people for cohort in cohorts) == 0


# --- the land sweep and the article (P11b) ------------------------------------------------


@dataclass(frozen=True)
class Card:
    name: str
    cmc: float = 0
    type_line: str = "Creature"

    @property
    def is_land(self) -> bool:
        return "Land" in self.type_line


SWAMP = Card("Swamp", type_line="Basic Land — Swamp")
MOUNTAIN = Card("Mountain", type_line="Basic Land — Mountain")
TOWER = Card("Command Tower", type_line="Land")


def _deck():
    """36 lands (24 Swamps, 11 Mountains, a Tower) and 63 spells, 0 to 6 mana."""
    counts = {SWAMP: 24, MOUNTAIN: 11, TOWER: 1}
    for index in range(63):
        counts[Card(f"Spell {index:02}", cmc=index % 7)] = 1
    return counts


def _lands(counts):
    return sum(copies for card, copies in counts.items() if card.is_land)


@pytest.mark.parametrize("lands", list(sweep.LANDS))
def test_a_rebuilt_deck_has_its_lands_and_still_99_cards(lands):
    rebuilt = sweep.rebuild(_deck(), lands)

    assert _lands(rebuilt) == lands
    assert sum(rebuilt.values()) == 99
    assert rebuilt[TOWER] == 1


def test_more_lands_cut_the_most_expensive_spells_and_add_basics_in_proportion():
    rebuilt = sweep.rebuild(_deck(), 39)

    cut = [card for card in _deck() if card not in rebuilt]
    assert {card.cmc for card in cut} == {6}
    assert (rebuilt[SWAMP], rebuilt[MOUNTAIN]) == (26, 12)


def test_fewer_lands_cut_basics_and_add_second_copies_of_cheap_spells():
    rebuilt = sweep.rebuild(_deck(), 33)

    assert rebuilt[SWAMP] + rebuilt[MOUNTAIN] == 32
    doubled = [card for card, copies in rebuilt.items() if copies == 2 and not card.is_land]
    assert len(doubled) == 3
    assert {card.cmc for card in doubled} <= set(sweep.FILLER_MANA)


def test_a_deck_without_basics_is_not_rebuilt():
    counts = {TOWER: 36, **{Card(f"Spell {i}", cmc=2): 1 for i in range(63)}}

    with pytest.raises(sweep.SweepError):
        sweep.rebuild(counts, 38)


@pytest.fixture
def swept(precon):
    lines = sweep.run()
    for variant in LandSweep.objects.all():
        _finish(variant.deck.runs.get())
    return lines


def test_the_sweep_builds_every_land_count_of_the_chosen_precon(swept, precon):
    assert sweep.chosen() == [precon]
    assert [what for what, _ in swept] == ["created"] * len(sweep.LANDS)
    for variant in LandSweep.objects.select_related("deck"):
        run = variant.deck.runs.get()
        assert run.lands_total == variant.lands
        assert variant.deck.card_count == 99
        assert variant.deck.commander == precon.deck.commander
        assert run_services.queue_of(run) == run_services.LONG_QUEUE
        assert not SharedReport.objects.filter(run=run).exists()


def test_the_sweep_is_built_once_until_its_precon_changes(swept, precon):
    assert {what for what, _ in sweep.run()} == {"unchanged"}

    Precon.objects.filter(pk=precon.pk).update(list_print="changed")
    assert {what for what, _ in sweep.run()} == {"changed"}
    assert LandSweep.objects.count() == len(sweep.LANDS)


def test_the_article_waits_for_its_decks(client, precon):
    sweep.run()

    page = _get(client, reverse("datapages:lands")).content.decode()

    assert "being simulated" in page
    assert "What the precons run" in page


def test_the_article_shows_the_sweep(client, swept):
    response = _get(client, reverse("datapages:lands"))

    page = response.content.decode()
    assert response.status_code == 200
    assert "points more likely to have four lands on turn 4" in page
    assert page.count('class="seen-chart') == 2
    assert "Grim Knights" in page


@pytest.mark.parametrize("language", [code for code, _ in ALL_LANGUAGES])
def test_the_article_answers_in_every_language(client, settings, swept, language):
    settings.LANGUAGES = ALL_LANGUAGES
    with translation.override(language):
        url = reverse("datapages:lands")

    response = _get(client, url)

    assert response.status_code == 200
    assert f'lang="{language}"' in response.content.decode()
