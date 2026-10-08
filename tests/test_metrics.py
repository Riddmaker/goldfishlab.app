"""P2: how the site is used, in numbers that name nobody.

* A count is a day, a name and a number - no user, no address. It survives
  the guest it counted, and counting never costs a request.
* What is counted, where it happens: guests started and saved, accounts
  opened, simulations by guests and accounts, shared reports read, and each
  person once per week they simulate - the launch plan's lead metric.
* The stats page (staff only) and `manage.py stats` show the last weeks, the
  last full week against the next target, and how many accounts came back.
* The privacy policy says all of it, in every language.
"""

import html
from datetime import date, datetime, timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone, translation

from decks.models import Deck
from metrics import counts, report
from metrics.counts import Name
from metrics.models import DailyCount
from sharing import services as sharing_services
from simulations import services as simulations
from simulations.models import SimulationRun
from tests.test_guests import PASSWORD, FakeRedis, save, the_guest, upload
from tests.test_sharing import BROWSER, _finish, deck, owner, run  # noqa: F401

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _no_workers(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(simulations, "_redis", lambda: redis)
    monkeypatch.setattr(simulations, "_dispatch", lambda started, tasks: None)


def today_counts() -> dict[str, int]:
    return dict(DailyCount.objects.filter(day=timezone.localdate()).values_list("name", "value"))


def member_deck(email="member@example.com"):
    user = User.objects.create_user(email=email, password=PASSWORD)
    return user, Deck.objects.create(owner=user, name="Test deck")


# --- a count names nobody -------------------------------------------------------------


def test_a_count_points_at_nobody():
    relations = [field.name for field in DailyCount._meta.get_fields() if field.is_relation]

    assert relations == []


def test_counts_add_up_per_day_and_name():
    counts.add(Name.SIGNUP)
    counts.add(Name.SIGNUP, 2)
    counts.add(Name.SIGNUP, day=date(2026, 1, 5))

    assert today_counts() == {Name.SIGNUP: 3}
    assert DailyCount.objects.get(day=date(2026, 1, 5)).value == 1


def test_the_days_first_two_at_once_both_count(monkeypatch):
    counts.add(Name.SIGNUP)
    real = counts._increment
    missed = []

    def first_misses(day, name, amount):
        # The other request made the row just after this one looked.
        if not missed:
            missed.append(True)
            return 0
        return real(day, name, amount)

    monkeypatch.setattr(counts, "_increment", first_misses)
    counts.add(Name.SIGNUP)

    assert today_counts() == {Name.SIGNUP: 2}


def test_a_failed_count_costs_nothing(monkeypatch, caplog):
    def broken(*args):
        raise DatabaseError("disk full")

    monkeypatch.setattr(counts, "_increment", broken)
    counts.add(Name.SIGNUP)  # no exception

    assert "could not count signup" in caplog.text
    assert not DailyCount.objects.exists()


# --- what is counted -----------------------------------------------------------------


def test_a_guest_is_counted_once_however_many_decks_it_tries(client, catalogue):
    upload(client)
    upload(client)  # replaces the first deck, and its run

    assert today_counts() == {Name.GUEST_STARTED: 1, Name.RUN_GUEST: 2,
                              Name.SIMULATOR_GUEST: 1}


def test_a_saved_guest_is_one_person_that_week(client, catalogue):
    upload(client)
    save(client)
    user = User.objects.get(email="new@example.com")

    assert user.simulated_week == counts.week_start(timezone.localdate())
    simulations.start_run(owner=user, deck=user.decks.get(), games=1000, turns=3)

    assert today_counts() == {Name.GUEST_STARTED: 1, Name.RUN_GUEST: 1,
                              Name.SIMULATOR_GUEST: 1, Name.GUEST_SAVED: 1,
                              Name.SIGNUP: 1, Name.RUN_MEMBER: 1}


def test_a_guest_whose_address_has_an_account_is_not_saved(client, catalogue):
    User.objects.create_user(email="new@example.com", password=PASSWORD)
    upload(client)
    save(client)

    assert the_guest()
    assert Name.GUEST_SAVED not in today_counts()
    assert Name.SIGNUP not in today_counts()


def test_signing_up_counts_an_account(client):
    client.post(reverse("account_signup"), {"email": "flow@example.com",
                                            "password1": "a-long-enough-test-password"})

    assert today_counts() == {Name.SIGNUP: 1}


def test_an_account_is_counted_once_a_week():
    user, played = member_deck()

    for _ in range(2):
        started = simulations.start_run(owner=user, deck=played, games=1000, turns=3)
        simulations.finish_slot(started)

    assert today_counts() == {Name.RUN_MEMBER: 2, Name.SIMULATOR_MEMBER: 1}

    last_monday = counts.week_start(timezone.localdate()) - timedelta(weeks=1)
    User.objects.filter(pk=user.pk).update(simulated_week=last_monday)
    user.refresh_from_db()
    simulations.start_run(owner=user, deck=played, games=1000, turns=3)

    assert today_counts()[Name.SIMULATOR_MEMBER] == 2


def test_a_refused_run_is_not_counted(client, catalogue, monkeypatch):
    from guests import services as guests

    upload(client)
    guest = the_guest()
    before = today_counts()
    monkeypatch.setattr(guests, "MAX_ACTIVE_GUEST_RUNS", 1)

    with pytest.raises(simulations.SimulationRefused):
        simulations.start_run(owner=guest, deck=guest.decks.get(), games=1000, turns=3)

    assert today_counts() == before


def test_a_run_that_is_not_made_is_not_counted(monkeypatch):
    user, played = member_deck()

    def broken(**kwargs):
        raise DatabaseError("lost the connection")

    monkeypatch.setattr(SimulationRun.objects, "create", broken)
    with pytest.raises(DatabaseError):
        simulations.start_run(owner=user, deck=played, games=1000, turns=3)

    assert today_counts() == {}
    user.refresh_from_db()
    assert user.simulated_week is None


def test_a_reader_of_a_shared_report_is_counted(client, run):  # noqa: F811
    shared = sharing_services.share(run)

    client.get(shared.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
    client.get(shared.get_absolute_url(), HTTP_USER_AGENT="Slackbot-LinkExpanding 1.0")

    assert today_counts() == {Name.REPORT_OPENED: 1}


# --- the numbers ----------------------------------------------------------------------

#: A Wednesday.
TODAY = date(2026, 10, 7)


def test_weeks_run_monday_to_sunday_this_one_first():
    for day, name, value in [(date(2026, 10, 5), Name.SIMULATOR_GUEST, 3),
                             (date(2026, 10, 7), Name.SIMULATOR_MEMBER, 2),
                             (date(2026, 10, 4), Name.SIMULATOR_MEMBER, 5),  # a Sunday
                             (date(2026, 9, 28), Name.SIGNUP, 1),
                             (date(2026, 7, 1), Name.SIGNUP, 9)]:  # too old
        DailyCount.objects.create(day=day, name=name, value=value)

    weeks = report.weeks(TODAY, count=3)

    assert [week.start for week in weeks] == [date(2026, 10, 5), date(2026, 9, 28),
                                              date(2026, 9, 21)]
    assert [week.complete for week in weeks] == [False, True, True]
    assert [week.simulators for week in weeks] == [5, 5, 0]
    assert weeks[1].counts == {Name.SIMULATOR_MEMBER: 5, Name.SIGNUP: 1}
    assert weeks[1].end == date(2026, 10, 4)
    assert len(weeks[0].cells) == len(report.COLUMNS)


@pytest.mark.parametrize(("today", "people", "by"), [
    (date(2026, 10, 7), 250, date(2026, 12, 31)),
    (date(2026, 12, 31), 250, date(2026, 12, 31)),
    (date(2027, 1, 1), 400, date(2027, 3, 31)),
    (date(2027, 9, 30), 800, date(2027, 9, 30)),
])
def test_the_next_target_is_the_launch_plans(today, people, by):
    assert report.next_target(today) == report.Target(people, by)


def test_after_the_last_target_there_is_none():
    assert report.next_target(date(2027, 10, 1)) is None


def test_the_last_full_week_is_held_against_the_next_target():
    DailyCount.objects.create(day=date(2026, 9, 30), name=Name.SIMULATOR_GUEST, value=40)
    DailyCount.objects.create(day=date(2026, 10, 1), name=Name.SIMULATOR_MEMBER, value=10)
    DailyCount.objects.create(day=date(2026, 10, 6), name=Name.SIMULATOR_MEMBER, value=99)

    lead = report.lead(TODAY)

    assert lead.week.start == date(2026, 9, 28)
    assert lead.week.simulators == 50
    assert lead.percent == 20  # of 250


def _runs(user, *days):
    played = Deck.objects.create(owner=user, name="Deck")
    for day in days:
        made = SimulationRun.objects.create(owner=user, deck=played, games_total=10, seed=1)
        at = timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=12)))
        SimulationRun.objects.filter(pk=made.pk).update(created_at=at)


def test_coming_back_counts_accounts_by_the_week_they_started():
    today = date(2026, 11, 25)
    monday = date(2026, 10, 5)
    users = [User.objects.create_user(email=f"u{n}@example.com") for n in range(4)]
    _runs(users[0], monday, monday + timedelta(days=3))  # back within 7
    _runs(users[1], monday + timedelta(days=6), monday + timedelta(days=26))  # within 30
    _runs(users[2], monday, monday)  # twice on one day: not back
    _runs(users[3], monday - timedelta(days=1))  # the week before
    guest = User.objects.create_user(email="g@guest.invalid", is_guest=True)
    _runs(guest, monday, monday + timedelta(days=1))

    cohort = next(c for c in report.cohorts(today) if c.start == monday)

    assert cohort.people == 3
    assert (cohort.back_7, cohort.percent_7) == (1, 33)
    assert (cohort.back_30, cohort.percent_30) == (2, 67)


def test_a_week_says_nothing_until_everybody_had_the_whole_window():
    monday = date(2026, 10, 5)
    user = User.objects.create_user(email="u@example.com")
    _runs(user, monday, monday + timedelta(days=1))

    def cohort(today):
        return next(c for c in report.cohorts(today) if c.start == monday)

    assert cohort(date(2026, 10, 18)).percent_7 is None  # its Sunday + 7
    assert cohort(date(2026, 10, 19)).percent_7 == 100
    assert cohort(date(2026, 10, 19)).percent_30 is None
    assert cohort(date(2026, 11, 12)).percent_30 == 100


# --- where to read them ---------------------------------------------------------------


def test_the_stats_page_is_for_staff(client):
    url = reverse("stats")
    member = User.objects.create_user(email="m@example.com", password=PASSWORD)

    assert client.get(url).status_code == 302
    client.force_login(member)
    assert client.get(url).status_code == 302
    assert reverse("admin:login") in client.get(url).url


def test_the_stats_page_shows_the_lead_metric_and_its_target(client):
    staff = User.objects.create_user(email="s@example.com", password=PASSWORD, is_staff=True)
    last_week = counts.week_start(timezone.localdate()) - timedelta(weeks=1)
    DailyCount.objects.create(day=last_week, name=Name.SIMULATOR_GUEST, value=17)
    client.force_login(staff)

    response = client.get(reverse("stats"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "<strong>17</strong>" in body
    target = report.next_target(timezone.localdate())
    assert f"Next target: <strong>{target.people}</strong>" in body
    assert "Coming back" in body
    assert reverse("stats") in client.get(reverse("admin:index")).content.decode()


def test_the_command_prints_the_same_numbers():
    last_week = counts.week_start(timezone.localdate()) - timedelta(weeks=1)
    DailyCount.objects.create(day=last_week, name=Name.SIMULATOR_MEMBER, value=12)
    out = StringIO()

    call_command("stats", "--weeks", "3", stdout=out)

    text = out.getvalue()
    assert f"People who simulated, week of {last_week}: 12" in text
    assert "Next target:" in text
    assert text.count("\n") > 8


# --- the privacy policy -----------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de", "fr", "it", "es", "pt-br", "ja"])
def test_the_privacy_policy_names_the_counts_in_every_language(client, settings, language):
    settings.LANGUAGES = [(code, code) for code in
                          ("en", "de", "fr", "it", "es", "pt-br", "ja")]
    with translation.override(language):
        url = reverse("privacy")
        expected = [translation.gettext(text) for text in (
            "The counts: as long as the site runs, as they are about nobody. The week of "
            "your last simulation: until you delete the account",
            "No analytics service, no ad network, no error-reporting service and no "
            "third-party scripts. The usage counts described above are our own and name "
            "nobody. If we ever add a service that processes anything about you, it is "
            "named here before it is switched on.",
        )]

    body = html.unescape(client.get(url).content.decode())

    for text in expected:
        assert text in body
    if language != "en":
        assert expected[0] != "The counts: as long as the site runs, as they are about " \
            "nobody. The week of your last simulation: until you delete the account"
    assert "there are no analytics or third-party trackers" not in body


# --- P9: the "Compare two versions" fake door --------------------------------------------


def test_a_report_shows_the_door_with_its_price(client, run):  # noqa: F811
    client.force_login(run.owner)

    body = client.get(run.get_absolute_url()).content.decode()

    assert 'id="compare"' in body
    assert reverse("metrics:compare", args=[run.pk]) in body
    assert "CHF 9 a month" in body


def test_a_report_view_counts_once_per_report_and_session(client, run):  # noqa: F811
    client.force_login(run.owner)

    client.get(run.get_absolute_url())
    client.get(run.get_absolute_url())  # a reload

    assert today_counts() == {Name.REPORT_VIEWED: 1}


def test_a_run_still_playing_is_no_report_view(client, owner, deck):  # noqa: F811
    playing = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, seed=1)
    client.force_login(owner)

    body = client.get(playing.get_absolute_url()).content.decode()

    assert 'id="compare"' not in body
    assert today_counts() == {}


def test_a_click_counts_once_and_says_it_is_not_built(client, run):  # noqa: F811
    client.force_login(run.owner)
    url = reverse("metrics:compare", args=[run.pk])

    response = client.post(url)
    client.post(url)

    assert response.status_code == 302
    assert response.url == f"{run.get_absolute_url()}#compare"
    assert today_counts()[Name.COMPARE_CLICKED] == 1
    body = client.get(run.get_absolute_url()).content.decode()
    assert "Not built yet" in body
    assert url not in body


def test_only_the_owner_can_click(client, run):  # noqa: F811
    url = reverse("metrics:compare", args=[run.pk])
    assert client.post(url).status_code == 302  # to the sign-in

    stranger = User.objects.create_user(email="x@example.com", password=PASSWORD)
    client.force_login(stranger)
    assert client.post(url).status_code == 404
    client.force_login(run.owner)
    assert client.get(url).status_code == 405
    assert Name.COMPARE_CLICKED not in today_counts()


def test_a_guest_sees_the_door_too(client, catalogue):
    upload(client)
    guest_run = _finish(SimulationRun.objects.get(owner=the_guest()))

    body = client.get(guest_run.get_absolute_url()).content.decode()

    assert 'id="compare"' in body
    assert today_counts()[Name.REPORT_VIEWED] == 1


@pytest.mark.parametrize(("views", "clicks", "judged", "passed"), [
    (0, 0, False, False),
    (199, 50, False, False),
    (200, 9, True, False),
    (200, 10, True, True),
])
def test_the_gate_wants_five_percent_of_two_hundred(views, clicks, judged, passed):
    DailyCount.objects.create(day=date(2026, 12, 1), name=Name.REPORT_VIEWED, value=views)
    DailyCount.objects.create(day=date(2026, 12, 2), name=Name.COMPARE_CLICKED, value=clicks)

    gate = report.gate()

    assert (gate.views, gate.clicks, gate.judged, gate.passed) == (views, clicks, judged, passed)


def test_the_stats_page_and_command_show_the_gate(client):
    DailyCount.objects.create(day=date(2026, 12, 1), name=Name.REPORT_VIEWED, value=40)
    DailyCount.objects.create(day=date(2026, 12, 1), name=Name.COMPARE_CLICKED, value=3)
    staff = User.objects.create_user(email="s@example.com", password=PASSWORD, is_staff=True)
    client.force_login(staff)
    out = StringIO()

    body = client.get(reverse("stats")).content.decode()
    call_command("stats", stdout=out)

    assert "<strong>3</strong> clicks on <strong>40</strong> reports viewed" in body
    assert "Too early to judge" in body
    assert "Gate G3: 3 clicks on 40 reports viewed = 7.5% (too early" in out.getvalue()
