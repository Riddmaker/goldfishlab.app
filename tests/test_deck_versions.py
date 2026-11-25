"""Versions of a deck's list (B-01, package "Workshop", PR 2): `decks.versions`.

A version is a snapshot; the deck's own rows always hold the version it is in.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts import privacy
from cards.models import OracleCard
from decks import services, versions
from decks.models import DeckVersion
from simulations import services as sim_services
from simulations import tasks
from simulations.models import SimulationRun
from simulations.summary import list_fingerprint
from tests.test_i18n import FakeRedis

User = get_user_model()

LIST = b"1 Chainer, Dementia Master *CMDR*\n10 Swamp\n1 Dark Ritual\n1 Sol Ring\n"


@pytest.fixture
def owner():
    return User.objects.create_user(email="versions@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(catalogue, owner):
    return services.import_deck(owner=owner, raw=LIST, name="Versioned").deck


def card(name):
    return str(OracleCard.objects.get(front_name=name).pk)


def listed(deck):
    return dict(deck.entries.values_list("oracle_card__front_name", "quantity"))


def test_a_deck_nobody_versions_has_no_version(deck):
    assert not deck.versions.exists()
    assert deck.current_version is None


def test_the_first_new_version_keeps_the_list_before_it_as_version_one(deck):
    before = listed(deck)

    made = versions.make(deck, {card("Sol Ring"): -1, card("Necropotence"): +1}, "less artifacts")

    one, two = deck.versions.all()
    assert (one.number, one.origin, two.number, two.origin) == (1, "first", 2, "new")
    assert made == two and two.comment == "less artifacts"
    deck.refresh_from_db()
    assert deck.current_version == two
    after = listed(deck)
    assert "Sol Ring" not in after and after["Necropotence"] == 1
    assert one.card_count == sum(before.values())
    assert two.list_print == list_fingerprint(deck) != one.list_print


def test_a_version_that_changes_nothing_is_refused(deck):
    with pytest.raises(versions.VersionError):
        versions.make(deck, {card("Swamp"): 0})
    with pytest.raises(versions.VersionError):
        versions.make(deck, {})


def test_choosing_a_version_puts_its_list_and_commander_back(deck):
    before = listed(deck)
    commander = deck.commander_id
    versions.make(deck, {card("Swamp"): -2, card("Island"): +2})
    one = deck.versions.get(number=1)

    versions.choose(deck, one)

    deck.refresh_from_db()
    assert listed(deck) == before
    assert deck.commander_id == commander
    assert deck.current_version == one
    assert list_fingerprint(deck) == one.list_print


def test_removing_the_version_the_deck_is_in_moves_to_the_one_before(deck):
    versions.make(deck, {card("Swamp"): -1})
    three = versions.make(deck, {card("Swamp"): -1})

    versions.remove(three)

    deck.refresh_from_db()
    assert deck.current_version.number == 2
    assert listed(deck)["Swamp"] == 9


def test_removing_one_in_between_joins_the_changes_of_its_neighbours(deck):
    versions.make(deck, {card("Swamp"): -1, card("Island"): +1})
    versions.make(deck, {card("Sol Ring"): -1})
    two = deck.versions.get(number=2)

    versions.remove(two)

    one, three = deck.versions.all()
    assert three.number == 3  # numbers are never reused
    assert versions.diff(one, three) == {
        card("Swamp"): -1, card("Island"): +1, card("Sol Ring"): -1}


def test_the_last_version_stays(deck):
    versions.make(deck, {card("Swamp"): -1})
    one, two = deck.versions.all()
    versions.remove(one)

    with pytest.raises(versions.VersionError):
        versions.remove(two)


def test_a_version_of_another_deck_is_not_chosen(deck, owner):
    other = services.import_deck(owner=owner, raw=b"1 Sol Ring\n", name="Other").deck
    versions.make(other, {card("Swamp"): +1})

    with pytest.raises(versions.VersionError):
        versions.choose(deck, other.versions.first())


def test_a_reimport_of_a_versioned_deck_is_a_version_and_loses_nothing(deck, owner):
    versions.make(deck, {card("Swamp"): -1})

    services.import_deck(owner=owner, raw=b"1 Necropotence\n", deck=deck)

    numbers = list(deck.versions.values_list("number", "origin"))
    assert numbers == [(1, "first"), (2, "new"), (3, "import")]
    assert deck.versions.get(number=2).card_count == 11  # 12 less one Swamp


def test_a_reimport_of_an_unversioned_deck_makes_no_version(deck, owner):
    services.import_deck(owner=owner, raw=b"1 Necropotence\n", deck=deck)

    assert not deck.versions.exists()


def test_a_commander_picked_by_hand_corrects_the_version_the_deck_is_in(deck):
    versions.make(deck, {card("Carrion Feeder"): +1})
    feeder = OracleCard.objects.get(front_name="Carrion Feeder")

    services.set_commander(deck, feeder)

    two = DeckVersion.objects.get(deck=deck, number=2)
    assert two.commander_id == feeder.pk
    assert two.list_print == list_fingerprint(deck)
    assert deck.versions.get(number=1).commander_id != feeder.pk


def test_diff_counts_the_commander_too(deck):
    versions.make(deck, {card("Swamp"): -1})
    one, two = deck.versions.all()

    assert versions.diff(None, one)[card("Chainer, Dementia Master")] == 1
    assert versions.diff(one, two) == {card("Swamp"): -1}


def test_versions_are_in_the_export(deck, owner):
    versions.make(deck, {card("Swamp"): -1})

    exported = privacy.export(owner)

    assert len(exported["deck_versions"]) == 2


# --- PR 3: the timeline on the deck page ---------------------------------------


def run_of(deck, owner, **fields):
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, turns=4, seed=7,
        list_print=list_fingerprint(deck), **fields)


def test_the_timeline_is_newest_first_with_what_each_version_changed(deck):
    versions.make(deck, {card("Sol Ring"): -1, card("Island"): +2}, "fewer rocks")

    steps, old = versions.timeline(deck)

    assert [step.version.number for step in steps] == [2, 1]
    assert steps[0].current and not steps[1].current
    assert steps[0].added == [("Island", 2)] and steps[0].removed == [("Sol Ring", 1)]
    assert steps[1].added == steps[1].removed == []
    assert ("Land", 2) in steps[0].groups and ("Mana rock", -1) in steps[0].groups
    assert old == []


def test_a_run_belongs_to_the_version_of_its_list(deck, owner):
    first_run = run_of(deck, owner)
    versions.make(deck, {card("Swamp"): -1})
    second_run = run_of(deck, owner)

    two, one = versions.timeline(deck)[0]

    assert two.runs == [second_run] and one.runs == [first_run]


def test_a_removed_versions_runs_stay_as_an_old_list(deck, owner):
    versions.make(deck, {card("Swamp"): -1})
    run = run_of(deck, owner)
    versions.make(deck, {card("Swamp"): -1})
    versions.remove(deck.versions.get(number=2))

    steps, old = versions.timeline(deck)

    assert [step.version.number for step in steps] == [3, 1]
    assert old == [run]
    assert SimulationRun.objects.filter(pk=run.pk).exists()


def test_the_last_finished_run_says_its_share_on_curve(deck, owner, monkeypatch):
    monkeypatch.setattr(sim_services, "_redis", FakeRedis)
    versions.make(deck, {card("Swamp"): +20})
    run = run_of(deck, owner)
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))

    newest = versions.timeline(deck)[0][0]

    assert newest.last == run
    assert newest.on_curve and newest.on_curve.endswith("%")


def test_the_deck_page_shows_the_timeline_only_once_there_are_versions(client, deck, owner):
    client.force_login(owner)
    before = client.get(deck.get_absolute_url()).content.decode()
    assert "data-version-timeline" not in before
    assert reverse("decks:version-new", args=[deck.pk]) in before

    versions.make(deck, {card("Swamp"): -1}, "one swamp less")
    page = client.get(deck.get_absolute_url()).content.decode()

    assert "data-version-timeline" in page and "one swamp less" in page
    assert reverse("decks:version-choose", args=[deck.pk, 1]) in page
    assert reverse("decks:version-choose", args=[deck.pk, 2]) not in page  # the current one


def test_choose_and_remove_from_the_page(client, deck, owner):
    client.force_login(owner)
    versions.make(deck, {card("Swamp"): -1})

    client.post(reverse("decks:version-choose", args=[deck.pk, 1]))
    deck.refresh_from_db()
    assert deck.current_version.number == 1

    client.post(reverse("decks:version-remove", args=[deck.pk, 2]))
    assert list(deck.versions.values_list("number", flat=True)) == [1]

    response = client.post(reverse("decks:version-remove", args=[deck.pk, 1]), follow=True)
    assert deck.versions.count() == 1
    assert "at least one version" in response.content.decode()


def test_somebody_elses_versions_are_out_of_reach(client, deck):
    versions.make(deck, {card("Swamp"): -1})
    stranger = User.objects.create_user(email="other@example.com", password="pw-for-test-only")
    client.force_login(stranger)

    assert client.post(reverse("decks:version-choose", args=[deck.pk, 1])).status_code == 404
    assert client.post(reverse("decks:version-remove", args=[deck.pk, 1])).status_code == 404
    assert deck.versions.count() == 2


def test_the_timeline_speaks_german(deck):
    from django.template.loader import render_to_string
    from django.utils import translation

    versions.make(deck, {card("Swamp"): -1})
    steps, old = versions.timeline(deck)
    with translation.override("de"):
        html = render_to_string("decks/_versions.html",
                                {"versions": steps, "old_runs": old, "deck": deck})

    assert "Versionen" in html and "aktuell" in html and "v1 wählen" in html
    assert "Noch kein Lauf." in html


# --- PR 4: "New version" --------------------------------------------------------


def new_url(deck):
    return reverse("decks:version-new", args=[deck.pk])


def test_new_version_saves_ticked_cuts_and_adds_with_a_comment(client, deck, owner):
    client.force_login(owner)
    assert client.get(new_url(deck)).status_code == 200

    response = client.post(new_url(deck), {
        "action": "save", "cut": [card("Sol Ring")], "add": [card("Necropotence")],
        "comment": "lost to the turn-4 combo"})

    assert response.status_code == 302 and response.url.endswith("#versions")
    two = deck.versions.get(number=2)
    assert two.comment == "lost to the turn-4 combo"
    assert versions.diff(deck.versions.get(number=1), two) == {
        card("Sol Ring"): -1, card("Necropotence"): +1}


def test_a_search_keeps_the_ticks_and_finds_both_ways(client, deck, owner):
    client.force_login(owner)

    page = client.post(new_url(deck), {
        "action": "search", "q": "rit", "add": [card("Necropotence")]}).content.decode()

    assert not deck.versions.exists()  # a search saves nothing
    chosen = page[page.index("data-chosen"):page.index("search-heading")]
    assert f'name="add" value="{card("Necropotence")}" checked' in chosen
    mine = page[page.index("data-found-mine"):]
    assert "Dark Ritual" in mine.split("</ul>")[0]


def test_the_catalogue_search_stays_in_the_decks_colours(client, deck, owner):
    client.force_login(owner)

    page = client.post(new_url(deck), {"action": "search", "q": "counterspell"}).content.decode()

    assert "Counterspell" not in page  # blue, and the deck is black


def test_saving_with_nothing_ticked_says_so(client, deck, owner):
    client.force_login(owner)

    page = client.post(new_url(deck), {"action": "save"}).content.decode()

    assert "Tick at least one card" in page
    assert not deck.versions.exists()


def test_a_cut_of_a_card_not_in_the_deck_is_ignored(client, deck, owner):
    client.force_login(owner)

    client.post(new_url(deck), {"action": "save", "cut": [card("Counterspell")]})

    assert not deck.versions.exists()


def test_new_version_refuses_junk_and_strangers(client, deck, owner):
    client.force_login(owner)
    assert client.post(new_url(deck), {"action": "save", "add": ["not-a-uuid"]}).status_code == 400
    assert client.post(new_url(deck), {"action": "drop"}).status_code == 400
    many = [str(OracleCard.objects.first().pk)] * (61)
    assert client.post(new_url(deck), {"action": "save", "add": many}).status_code == 400

    stranger = User.objects.create_user(email="new@example.com", password="pw-for-test-only")
    client.force_login(stranger)
    assert client.get(new_url(deck)).status_code == 404
    assert client.post(new_url(deck), {"action": "save",
                                       "add": [card("Necropotence")]}).status_code == 404
    assert not deck.versions.exists()


def test_the_rules_cuts_are_offered_to_tick(client, deck, owner, monkeypatch):
    from simulations import cuts, summary

    monkeypatch.setattr(summary, "cut_candidates",
                        lambda *args: [cuts.Cut(name="Sol Ring", mana_value=1, kind=cuts.NONE)])
    client.force_login(owner)

    page = client.get(new_url(deck)).content.decode()

    offered = page[page.index("data-suggested-cuts"):].split("</ul>")[0]
    assert f'name="cut" value="{card("Sol Ring")}"' in offered
    assert "No role and no part in a theme." in offered
