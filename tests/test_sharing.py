"""Shareable reports (P4).

* Only the owner shares or stops sharing, and only a finished run of a deck
  that is still the one it played; a guest is asked to save first.
* /r/<token>/ answers anybody, shows the deck as it was shared and nothing
  that leads back to the owner, and may be indexed - but is not in the sitemap.
* A stopped link is gone for good; deleting the run, deck or account takes it.
* The copy-as-text and the link preview carry the same few numbers.
* Readers are counted, the owner and preview robots are not.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from accounts import privacy
from decks import services as deck_services
from decks.models import DeckCard
from sharing import services, text
from sharing.models import SharedReport
from simulations import report, summary, tasks
from simulations import services as run_services
from simulations.models import DeckSummary, SimulationRun
from tests.test_i18n import FakeRedis
from tests.test_search_basics import _head

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
SITE = "https://goldfishlab.app"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="sharer@example.com", password="pw-test-only")


@pytest.fixture
def stranger(db):
    return User.objects.create_user(email="stranger@example.com", password="pw-test-only")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer <b>Loops</b>",
        filename="sample.csv",
    ).deck


def _finish(run):
    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))
    run.refresh_from_db()
    return run


@pytest.fixture
def run(owner, deck, monkeypatch, settings):
    settings.SITE_URL = SITE
    monkeypatch.setattr(run_services, "_redis", FakeRedis)
    return _finish(SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, turns=4, seed=7,
        deck_print=summary.fingerprint(deck)))


@pytest.fixture
def shared(run):
    return services.share(run)


def _public(client, shared, **headers):
    return client.get(shared.get_absolute_url(), HTTP_USER_AGENT=BROWSER, **headers)


# --- making and stopping a link --------------------------------------------------


def test_the_owner_shares_a_finished_run(client, owner, run):
    client.force_login(owner)

    response = client.post(reverse("sharing:share", args=[run.pk]))

    assert response.status_code == 302
    shared = SharedReport.objects.get(run=run)
    assert len(shared.token) >= 22
    assert shared.deck_name == run.deck.name
    assert shared.commander == run.deck.commander.name
    assert shared.card_count == run.deck.card_count
    page = client.get(run.get_absolute_url()).content.decode()
    assert shared.token in page
    assert "Stop sharing" in page


def test_sharing_twice_keeps_one_link(owner, run):
    first = services.share(run)

    assert services.share(run) == first
    assert SharedReport.objects.filter(run=run).count() == 1


def test_somebody_else_cannot_share_or_stop_a_run(client, stranger, run, shared):
    client.force_login(stranger)

    assert client.post(reverse("sharing:share", args=[run.pk])).status_code == 404
    assert client.post(reverse("sharing:stop", args=[run.pk])).status_code == 404
    assert SharedReport.objects.filter(run=run).exists()


def test_signed_out_nobody_can_share(client, run):
    response = client.post(reverse("sharing:share", args=[run.pk]))

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]
    assert not SharedReport.objects.exists()


def test_an_unfinished_run_cannot_be_shared(client, owner, deck):
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=4,
                                       seed=7, deck_print=summary.fingerprint(deck))
    client.force_login(owner)

    client.post(reverse("sharing:share", args=[run.pk]))

    assert not SharedReport.objects.exists()


def test_a_changed_deck_cannot_be_shared_until_it_is_run_again(client, owner, run):
    DeckCard.objects.filter(deck=run.deck).first().delete()
    client.force_login(owner)

    response = client.post(reverse("sharing:share", args=[run.pk]), follow=True)

    assert not SharedReport.objects.exists()
    assert "Run it again to share it." in response.content.decode()


def test_a_run_from_before_the_fingerprint_cannot_be_shared(run):
    SimulationRun.objects.filter(pk=run.pk).update(deck_print="")
    run.refresh_from_db()

    assert services.refusal(run) == services.DECK_CHANGED


def test_a_guest_is_asked_to_save_first(client, run):
    guest = run.owner
    guest.is_guest = True
    guest.save()
    client.force_login(guest)

    page = client.get(run.get_absolute_url()).content.decode()
    client.post(reverse("sharing:share", args=[run.pk]))

    assert "Save your deck with a free account to share its report." in page
    assert reverse("guests:save") in page
    assert not SharedReport.objects.exists()


def test_stopping_kills_the_link_and_sharing_again_makes_a_new_one(client, owner, run, shared):
    old = shared.get_absolute_url()
    client.force_login(owner)

    client.post(reverse("sharing:stop", args=[run.pk]))
    client.logout()
    assert client.get(old).status_code == 404

    again = services.share(run)
    assert again.get_absolute_url() != old
    assert client.get(old).status_code == 404


@pytest.mark.parametrize("delete", ["run", "deck", "account"])
def test_deleting_the_run_deck_or_account_takes_the_link(client, run, shared, delete):
    url = shared.get_absolute_url()

    {"run": run, "deck": run.deck, "account": run.owner}[delete].delete()

    assert client.get(url).status_code == 404


def test_an_unknown_token_is_a_404_and_says_noindex(client, shared):
    response = client.get(reverse("sharing:report", args=["x" * 22]))

    assert response.status_code == 404
    head = _head(response)
    assert head.meta["robots"] == "noindex"
    assert "canonical" not in head.links


# --- the public page -----------------------------------------------------------------


def test_anybody_can_read_a_shared_report(client, run, shared):
    response = _public(client, shared)

    assert response.status_code == 200
    page = response.content.decode()
    assert "What you drew" in page
    assert "Mulligans" in page
    assert "Chainer &lt;b&gt;Loops&lt;/b&gt;" in page, "the deck's name, escaped"
    for card in shared.cards[:5]:
        assert card["name"] in page or card["name"].replace("'", "&#x27;") in page


def test_the_public_page_leads_nowhere_near_the_owner(client, run, shared):
    page = _public(client, shared).content.decode()

    assert run.owner.email not in page
    assert str(run.pk) not in page
    assert str(run.deck.pk) not in page
    assert "Stop sharing" not in page
    assert "Cards that need your attention" not in page
    assert "Hide summaries" not in page


def test_it_may_be_indexed_but_is_not_in_the_sitemap(client, shared):
    head = _head(_public(client, shared))

    assert head.links["canonical"] == SITE + shared.get_absolute_url()
    assert head.meta["og:url"] == SITE + shared.get_absolute_url()
    assert head.meta["og:type"] == "article"
    assert "robots" not in head.meta
    assert shared.token not in client.get("/sitemap.xml").content.decode()
    assert _head(client.get("/")).meta["og:type"] == "website"


def test_the_title_leads_with_the_commander_not_the_deck_name(client, run, shared):
    head = _head(_public(client, shared))

    # head.title would also collect the charts' SVG <title>s; og:title is the
    # same capture.
    assert head.meta["og:title"] == f"{shared.commander}: 40 games simulated — Goldfish Lab"
    assert "Loops" not in head.meta["og:title"]
    assert head.meta["og:description"].endswith(
        f"The engine read {run.coverage_read} of {run.coverage_total} cards in full.")


def test_later_changes_to_the_deck_do_not_change_the_shared_page(client, run, shared):
    before = [card["name"] for card in shared.cards]
    run.deck.name = "Renamed"
    run.deck.save()
    DeckCard.objects.filter(deck=run.deck).delete()
    cache.clear()

    page = _public(client, shared).content.decode()

    assert "Renamed" not in page
    assert "Chainer" in page
    assert sum(name in page for name in before) >= len(before) - 2


def test_the_cards_are_listed_by_type_in_deck_list_order(shared):
    order = ["creature", "planeswalker", "battle", "artifact", "enchantment",
             "instant", "sorcery", "land", ""]
    kinds = [card["type"] for card in shared.cards]

    assert kinds == sorted(kinds, key=order.index)
    assert "land" in kinds


def test_the_owner_sees_it_as_others_do_with_a_way_back(client, owner, run, shared):
    client.force_login(owner)

    page = _public(client, shared).content.decode()

    assert "This is how others see your shared report." in page
    assert run.get_absolute_url() in page


def test_a_guest_reading_a_shared_link_is_not_fenced(client, run, shared):
    guest = User.objects.create_user(email="g@guest.invalid", password=None)
    guest.is_guest = True
    guest.save()
    client.force_login(guest)

    assert _public(client, shared).status_code == 200


# --- the written summary --------------------------------------------------------------


def _summary(run, fingerprint):
    return DeckSummary.objects.create(
        deck=run.deck, fingerprint=fingerprint, status=DeckSummary.Status.DONE,
        content={"feel": "A grindy loop deck.", "strengths": ["Recursion"]},
        language="de")


def test_a_summary_of_this_exact_deck_is_shared_and_frozen(client, run):
    written = _summary(run, run.deck_print)

    shared = services.share(run)
    written.content = {"feel": "Something else."}
    written.save()

    assert shared.summary["feel"] == "A grindy loop deck."
    assert shared.summary_language == "de"
    page = _public(client, shared).content.decode()
    assert 'lang="de">A grindy loop deck.' in page
    assert "Written by Mistral AI about this exact deck." in page


def test_a_summary_of_another_version_of_the_deck_is_not_shared(run):
    _summary(run, "f" * 64)

    assert services.share(run).summary == {}


def test_a_summary_is_not_shared_when_its_owner_switched_summaries_off(run):
    _summary(run, run.deck_print)
    run.owner.deck_summaries = False
    run.owner.save()

    assert services.share(run).summary == {}


# --- the text ---------------------------------------------------------------------


def test_copy_as_text_has_the_heading_the_facts_and_the_link(client, owner, run, shared):
    built = report.build(run)

    plain = text.plain(shared, built, run, SITE + shared.get_absolute_url())

    lines = plain.splitlines()
    assert lines[0].startswith(f"{shared.commander}: 40 games simulated, 4 turns")
    assert lines[0].endswith("(Goldfish Lab)")
    assert any(line.startswith("- Kept the first seven: ") for line in lines)
    assert lines[-2].startswith("- The engine read ")
    assert lines[-1] == SITE + shared.get_absolute_url()
    client.force_login(owner)
    assert "Kept the first seven: " in client.get(run.get_absolute_url()).content.decode()


def test_the_commander_line_reads_turn_four_or_the_last_turn(run, shared):
    built = report.build(run)
    built["milestones"] = [{"key": "commander", "label": "Commander", "shares": [0, 10, 20]}]

    assert "Commander out by turn 3: 20%" in text.facts(built, run)


def test_a_deck_without_a_commander_gets_a_plain_title(run, shared):
    shared.commander = ""

    assert text.title(shared, run) == "A Commander deck: 40 games simulated"


# --- counting readers ----------------------------------------------------------------


def test_readers_are_counted_the_owner_and_robots_are_not(client, owner, run, shared):
    _public(client, shared)
    _public(client, shared)
    client.get(shared.get_absolute_url(), HTTP_USER_AGENT="Discordbot/2.0")
    client.get(shared.get_absolute_url(), HTTP_USER_AGENT="facebookexternalhit/1.1")
    client.get(shared.get_absolute_url())  # no user agent at all
    client.force_login(owner)
    _public(client, shared)

    shared.refresh_from_db()
    assert shared.views == 2
    assert "Opened 2 times by others" in client.get(run.get_absolute_url()).content.decode()


# --- what it touches elsewhere ------------------------------------------------------


def test_the_export_includes_shared_reports(owner, shared):
    exported = privacy.export(owner)

    assert [row["token"] for row in exported["shared_reports"]] == [shared.token]


def test_the_privacy_policy_and_terms_say_a_shared_report_is_public(client):
    policy = client.get(reverse("privacy")).content.decode()
    terms = client.get(reverse("terms")).content.decode()

    assert "Shared reports" in policy
    assert re.search(r"unless you share", terms)


def test_the_run_page_loads_the_copy_script_only_once_finished(client, owner, run):
    client.force_login(owner)

    assert "js/share" in client.get(run.get_absolute_url()).content.decode()
