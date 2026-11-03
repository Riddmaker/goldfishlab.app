"""Paket 3, PR 10 (Themen-Doc row 39): one sentence why, then "More"; PR 11 below.

What matters:

1. **No "Why?" any more.** The link into the methodology page is a quiet
   "More" - an offer to read on, not a question the reader has to answer.
2. **The sentence before it says why the section matters**, so the page can
   be understood without following the link.
3. **In every language**: "Mehr dazu" in German, through its own context, so
   the plain "More" elsewhere keeps its own translation.
"""

import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from datapages import services as datapages
from datapages.models import Precon
from decks.models import Deck
from sharing import services as sharing
from simulation import analysis
from simulations import numbers
from simulations.models import SimulationRun
from tests.test_i18n import GERMAN, deck, finished_run, machine_number_faults, owner  # noqa: F401

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
METHODOLOGY_LINK = re.compile(r'<a href="/[^"]*methodology/#[\w-]+"[^>]*>([^<]+)</a>')


def _links(body: str) -> set[str]:
    return {text.strip() for text in METHODOLOGY_LINK.findall(body)}


def test_no_template_asks_why_any_more():
    asking = [str(path.relative_to(TEMPLATES)) for path in TEMPLATES.rglob("*.html")
              if re.search(r'translate "Why\?"', path.read_text(encoding="utf-8"))]
    assert asking == []


def test_the_run_page_says_why_first_and_offers_more(client, owner, finished_run):  # noqa: F811
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()

    assert _links(body) == {"More"}, "every methodology link reads More"
    assert "How soon your deck gets going" in body
    assert "What your games start with" in body
    assert "The more it reads, the more the numbers tell." in body


def test_in_german_the_link_says_mehr_dazu(client, owner, finished_run, settings):  # noqa: F811
    settings.LANGUAGES = GERMAN
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()

    assert _links(body) == {"Mehr dazu"}
    assert "Wie schnell dein Deck in Gang kommt" in body
    assert "Warum?" not in body


def test_a_shared_report_offers_more_too(client, finished_run):  # noqa: F811
    shared = sharing.share(finished_run)

    body = client.get(shared.get_absolute_url()).content.decode()

    assert _links(body) == {"More"}
    assert "The more it reads, the more the numbers tell." in body


# --- PR 11 (Themen-Doc row 32): "In short" next to the precons ---------------
#
# 1. **Each number against the precons'**: how many it is ahead of, and their
#    middle; higher is better in all three.
# 2. **Five precons at least**, else no block: "ahead of 2 of 3" says nothing.
# 3. **A precon's own page leaves it out**, so it is not compared with itself.
# 4. **The "±" sentences stay**, in Advanced under "How exact the numbers are".


def _numbers(on_curve=80.0, kept_seven=50.0, mana=4.0):
    return numbers.Numbers(games=100, lands=36, on_curve=on_curve, kept_seven=kept_seven,
                           mana_turn_4=mana, spells_seen=10.0)


@pytest.fixture
def five(monkeypatch):
    peers = [(uuid.uuid4(), _numbers(on_curve, 40.0 + on_curve / 10, 3.0 + on_curve / 100))
             for on_curve in (60.0, 70.0, 75.0, 85.0, 90.0)]
    monkeypatch.setattr(numbers, "precons", lambda: peers)
    return peers


def test_each_number_says_how_many_precons_it_is_ahead_of(five):
    on_curve, kept, mana = numbers.standings(_numbers(on_curve=80.0, kept_seven=50.0, mana=3.5))

    assert (on_curve.ahead_of, on_curve.total) == (3, 5)
    assert on_curve.middle == "75%" and on_curve.value == "80%"
    assert kept.ahead_of == 5, "50% kept beats 46-49%"
    assert mana.ahead_of == 0
    assert on_curve.mark == pytest.approx(66.7) and on_curve.ticks[0] == 0.0
    assert (on_curve.low, on_curve.high) == ("60%", "90%")


def test_a_level_number_is_not_ahead(five):
    assert numbers.standings(_numbers(on_curve=75.0))[0].ahead_of == 2


def test_fewer_than_five_precons_compare_with_nothing(monkeypatch, five):
    monkeypatch.setattr(numbers, "precons", lambda: five[:4])

    assert numbers.standings(_numbers()) == []


def test_a_precon_is_not_compared_with_itself(five):
    assert numbers.standings(_numbers(), deck_id=five[0][0]) == []


def test_a_run_without_turn_4_compares_only_what_it_has(five):
    shown = numbers.standings(_numbers(on_curve=None, mana=None))

    assert [standing.total for standing in shown] == [5]


def test_the_precons_are_their_newest_finished_shared_runs():
    lab = datapages.lab_account()
    for index in range(numbers.PRECONS_AT_LEAST + 1):
        lab_deck = Deck.objects.create(owner=lab, name=f"Precon {index}")
        Precon.objects.create(slug=f"precon-{index}", name=f"Precon {index}", set_code="TST",
                              released=date(2025, 1, 1), source=Precon.Source.TEXT,
                              deck=lab_deck, published=index > 0)
        result = analysis.as_json(analysis.run(40, turns=6, seed=index))
        run = SimulationRun.objects.create(
            owner=lab, deck=lab_deck, games_total=40, games_done=40, turns=6, seed=index,
            status=SimulationRun.Status.DONE, finished_at=timezone.now(), result=result)
        sharing.share(run)
        SimulationRun.objects.create(owner=lab, deck=lab_deck, games_total=40, turns=6,
                                     seed=index, status=SimulationRun.Status.RUNNING)

    found = numbers.precons()

    assert len(found) == numbers.PRECONS_AT_LEAST, "the unpublished one is left out"
    assert all(said.games == 40 for _deck, said in found), "only finished runs"


def test_the_report_compares_and_keeps_the_exact_sentences(client, owner, finished_run, five):  # noqa: F811
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()
    glance = body[body.index('id="glance"'):body.index("</section>", body.index('id="glance"'))]

    assert "next to 5 Commander precons" in glance
    assert glance.count("data-standing") == 3
    assert "The precons&#x27; middle" in glance or "The precons' middle" in glance
    assert "±" not in glance, "the ± sentences are in Advanced"
    finished_run.refresh_from_db()
    assert 'id="exact"' in body and numbers.sentences(numbers.of(finished_run))[0] in body
    assert reverse("datapages:precons") in glance
    assert not machine_number_faults(glance)


def test_without_precons_the_report_has_no_in_short(client, owner, finished_run):  # noqa: F811
    client.force_login(owner)

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()

    assert 'id="glance"' not in body and 'id="exact"' in body


def test_in_german_the_comparison_is_german_and_its_numbers_machine_readable(
        client, owner, finished_run, five, settings):  # noqa: F811
    settings.LANGUAGES = GERMAN
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    body = client.get(reverse("simulations:detail", args=[finished_run.pk])).content.decode()
    glance = body[body.index('id="glance"'):body.index("</section>", body.index('id="glance"'))]

    assert "neben 5 Commander-Precons" in glance and "Mitte der Precons" in glance
    assert not machine_number_faults(glance)


# --- PR 12 (Themen-Doc row 35): the stress test as two cards -----------------
#
# 1. **One card per blow**, side by side, each with one big number: how fast
#    the deck comes back.
# 2. **The with/without table is folded** under "The numbers".
# 3. **A blow the run could not play** says why, without a number.


def _stress(body: str) -> str:
    start = body.index('id="stress"')
    return body[start:body.index("</section>", start)]


def test_the_stress_test_is_two_cards_with_the_table_folded(client, owner, finished_run):  # noqa: F811
    client.force_login(owner)

    shown = _stress(client.get(reverse("simulations:detail", args=[finished_run.pk]))
                    .content.decode())
    cards, folded = shown.split("data-stress-numbers") if "data-stress-numbers" in shown \
        else (shown, "")

    assert cards.count("data-stress-card=") == 2
    assert "<table" not in cards, "the table is folded, not beside the cards"
    assert cards.count("data-stress-figure") == 1, "a run of 4 turns has no wipe to number"
    assert "six turns or more" in cards
    assert "<table" in folded and "The numbers" in folded
    assert not machine_number_faults(shown)


def test_in_german_the_cards_are_german(client, owner, finished_run, settings):  # noqa: F811
    settings.LANGUAGES = GERMAN
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    shown = _stress(client.get(reverse("simulations:detail", args=[finished_run.pk]))
                    .content.decode())

    assert "bis Zug 4 wieder da" in shown and "Die Zahlen" in shown
    assert not machine_number_faults(shown)
