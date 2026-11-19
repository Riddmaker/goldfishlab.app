"""PR 22: the weekly Commander news - a page, a feed and a mail.

What has to stay true:

1. **A week's file keeps the rules a program can check** (`news.rules`). It
   links only the listed sources over https and never Wizards, Scryfall or
   Reddit. Its text is ours, in every language, without markup. Every fact is
   in the English text, and with the texts it was written from, every fact
   stands in them and no eight words are copied.
2. **The page and the feed are public, in every language,** and show only
   weeks that are out. The label says how the text was made, and the links
   leave with nofollow.
3. **The mail is the changelog's:** off until asked for, one click to stop it
   in the mail, on the page and on "Your plan", and each list's link stops
   only its own list.
4. **Each week goes out once, while it is fresh,** to confirmed accounts
   that asked for it.
"""

import copy
import json
import re
from datetime import date, timedelta
from xml.etree import ElementTree

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from changelog import services as changelog_services
from changelog import tasks as changelog_tasks
from core import mailing, seo
from news import check, issues, rules, tasks
from news.services import LIST
from tests.test_guests import PASSWORD

User = get_user_model()
SENDER = "Goldfish Lab <noreply@goldfishlab.test>"
WEEK = "2026-W45"
MONDAY = date(2026, 11, 2)
URL = "https://www.wargamer.com/magic-the-gathering/sol-ring-banned"


def item(slug="sol-ring-banned", url=URL, **changes):
    text = {
        "headline": "Sol Ring leaves Commander on November 10",
        "summary": ("The Commander Format Panel announced that Sol Ring is banned from "
                    "November 10. Decks that play it have a week to swap it out."),
    }
    data = {
        "slug": slug,
        "headline": {code: text["headline"] for code in rules.LANGUAGES},
        "summary": {code: text["summary"] for code in rules.LANGUAGES},
        "facts": ["Sol Ring", "November 10"],
        "links": [{"source": "wargamer", "url": url, "title": "Sol Ring is banned & gone"}],
    }
    data["headline"]["de"] = "Sol Ring fliegt am 10. November aus Commander"
    data["summary"]["de"] = ("Das Commander Format Panel hat angekündigt, dass Sol Ring ab "
                             "dem 10. November gebannt ist. Decks haben eine Woche Zeit.")
    data.update(changes)
    return data


def week(name=WEEK, published=MONDAY, items=None):
    return {"week": name, "published": published.isoformat(),
            "items": items if items is not None else [item()]}


def write(folder, *weeks):
    folder.mkdir(exist_ok=True)
    for data in weeks:
        (folder / f"{data['week']}.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """The site reads the weeks from here instead of news/issues/."""
    path = tmp_path / "issues"
    path.mkdir()
    monkeypatch.setattr(issues, "FOLDER", path)
    issues._all.cache_clear()
    yield path
    issues._all.cache_clear()


@pytest.fixture
def one_week(folder):
    write(folder, week())
    issues._all.cache_clear()
    return folder


# --- 1. the rules ------------------------------------------------------------


def test_a_good_week_passes():
    parsed = rules.parse(WEEK, week())

    assert parsed["published"] == MONDAY
    assert parsed["items"][0]["links"][0]["title"] == "Sol Ring is banned & gone"


def test_the_languages_are_the_sites():
    assert rules.LANGUAGES == tuple(django_settings.LANGUAGE_NAMES)


def test_every_source_is_read_over_https_on_its_own_host():
    for source in rules.SOURCES.values():
        assert source.feed.startswith(f"https://{source.host}/")
        assert not any(host in source.host for host in rules.FORBIDDEN)


def test_the_routines_steps_run_in_order_and_its_references_exist():
    routine = (django_settings.BASE_DIR / "news" / "ROUTINE.md").read_text(encoding="utf-8")
    steps = [int(n) for n in re.findall(r"^(\d+)\. \*\*", routine, re.MULTILINE)]
    assert steps == list(range(1, len(steps) + 1))
    assert {int(n) for n in re.findall(r"\(step (\d+)\)", routine)} <= set(steps)


def test_the_routine_never_writes_from_a_search_result():
    routine = (django_settings.BASE_DIR / "news" / "ROUTINE.md").read_text(encoding="utf-8")
    never = routine.split("## Steps")[0]
    assert "never open a search result" in never
    assert "Never write from a search result" in never
    assert "*Found, no allowed source*" in routine


def test_the_weeks_in_the_repository_keep_the_rules():
    rules.load(check.FOLDER)


def _broken(change):
    data = week()
    change(data)
    return data


@pytest.mark.parametrize(("change", "says"), [
    (lambda d: d["items"][0]["links"][0].update(url="http://www.wargamer.com/a"), "https"),
    (lambda d: d["items"][0]["links"][0].update(url="https://evil.example/a"), "https page on"),
    (lambda d: d["items"][0]["links"][0].update(url="javascript:alert(1)"), "https page on"),
    (lambda d: d["items"][0]["links"][0].update(source="scryfall"), "unknown source"),
    (lambda d: d["items"][0]["links"][0].update(title="<script>"), "original title"),
    (lambda d: d["items"][0]["summary"].update(en=d["items"][0]["summary"]["en"]
                                               + " See scryfall.com"), "scryfall.com"),
    (lambda d: d["items"][0]["summary"].update(de="Mehr <b>hier</b> und noch viel mehr Text "
                                               "damit es lang genug ist."), "no markup"),
    (lambda d: d["items"][0]["summary"].update(fr="Voir https://x.example pour la suite des "
                                               "nouvelles de la semaine."), "no markup"),
    (lambda d: d["items"][0]["summary"].pop("ja"), "exactly the languages"),
    (lambda d: d["items"][0]["summary"].update(en="x" * 481), "characters"),
    (lambda d: d["items"][0]["headline"].update(it="y" * 91), "characters"),
    (lambda d: d["items"][0].update(facts=["Mana Crypt", "November 10"]), "not in the English"),
    (lambda d: d["items"][0].update(facts=["Sol Ring"]), "facts"),
    (lambda d: d["items"][0].update(slug="Sol Ring"), "slug"),
    (lambda d: d["items"][0].update(extra=1), "an item is"),
    (lambda d: d["items"].append(copy.deepcopy(d["items"][0])), "same slug"),
    (lambda d: d.update(items=[]), "items"),
    (lambda d: d.update(published="2026-11-16"), "is not in week"),
    (lambda d: d.update(week="2026-W46"), "says it is week"),
])
def test_a_broken_week_is_refused_and_says_why(change, says):
    with pytest.raises(rules.Invalid, match=says):
        rules.parse(WEEK, _broken(change))


def test_japanese_may_be_shorter_but_not_empty():
    short = item()
    short["summary"]["ja"] = "パネルは11月10日からSol Ringを禁止すると発表しました。"
    assert rules.parse(WEEK, week(items=[short]))

    short["summary"]["ja"] = "禁止です。"
    with pytest.raises(rules.Invalid, match="ja: .* allowed 25 to 240"):
        rules.parse(WEEK, week(items=[short]))


@pytest.mark.parametrize("name", ["2026-46", "2026-W54", "week46"])
def test_a_file_must_be_named_for_its_week(name):
    with pytest.raises(rules.Invalid):
        rules.week_of(name)


def test_a_link_is_news_only_once(tmp_path):
    later = week("2026-W46", MONDAY + timedelta(days=7), [item("again")])
    write(tmp_path / "w", week(), later)

    with pytest.raises(rules.Invalid, match="already news in 2026-W45"):
        rules.load(tmp_path / "w")


def test_the_weeks_come_newest_first(tmp_path):
    later = week("2026-W46", MONDAY + timedelta(days=7),
                 [item("next", url=URL + "-again")])
    loaded = rules.load(write(tmp_path / "w", week(), later))

    assert [issue["week"] for issue in loaded] == ["2026-W46", WEEK]


def _texts(tmp_path, text):
    path = tmp_path / "texts"
    path.mkdir()
    (path / "sol-ring-banned-1.txt").write_text(text, encoding="utf-8")
    return path


ARTICLE = ("The Commander Format Panel has decided: from November 10, Sol Ring is no "
           "longer legal in Commander. Players should update their lists.")


def test_the_facts_stand_in_the_text_it_was_written_from(tmp_path):
    assert rules.against(rules.parse(WEEK, week()), _texts(tmp_path, ARTICLE)) == []


def test_a_fact_the_text_does_not_have_is_found(tmp_path):
    problems = rules.against(rules.parse(WEEK, week()),
                             _texts(tmp_path, "Sol Ring is banned, says the panel."))

    assert problems == ["sol-ring-banned: the fact 'November 10' is not in the article"]


def test_eight_copied_words_are_found_in_any_language(tmp_path):
    copied = item()
    copied["summary"]["es"] = ("Según el panel: from November 10, Sol Ring is no longer "
                               "legal in Commander. Así queda.")
    problems = rules.against(rules.parse(WEEK, week(items=[copied])),
                             _texts(tmp_path, ARTICLE))

    assert len(problems) == 1
    assert problems[0].startswith("sol-ring-banned.es: copied from the article")


def test_a_missing_text_is_a_problem(tmp_path):
    (tmp_path / "texts").mkdir()

    assert rules.against(rules.parse(WEEK, week()), tmp_path / "texts") == [
        "sol-ring-banned: no article text sol-ring-banned-1.txt"]


def test_the_check_says_ok_or_fail(tmp_path, capsys):
    folder = write(tmp_path / "w", week())
    texts = _texts(tmp_path, ARTICLE)

    assert check.main(["--folder", str(folder)]) == 0
    assert check.main(["--folder", str(folder), "--week", WEEK, "--sources", str(texts)]) == 0
    (texts / "sol-ring-banned-1.txt").write_text("Nothing about it.", encoding="utf-8")
    assert check.main(["--folder", str(folder), "--week", WEEK, "--sources", str(texts)]) == 1
    assert "FAIL sol-ring-banned: the fact 'Sol Ring'" in capsys.readouterr().out


# --- 2. the page and the feed -------------------------------------------------

@pytest.mark.django_db
def test_without_a_week_the_page_says_the_first_is_coming(client, folder):
    body = client.get(reverse("news")).content.decode()

    assert "data-news-empty" in body
    assert "data-news-label" in body
    assert reverse("news_feed") in body


@pytest.mark.django_db
def test_the_page_shows_the_week_with_its_label_and_links(client, one_week):
    body = client.get(reverse("news")).content.decode()

    assert 'id="2026-w45"' in body
    assert 'id="2026-w45-sol-ring-banned"' in body
    assert "Sol Ring leaves Commander on November 10" in body
    assert "Summarised with AI" in body
    assert f'href="{URL}" rel="nofollow noopener noreferrer"' in body
    # The original title is escaped like any text.
    assert "Sol Ring is banned &amp; gone" in body
    assert "(Wargamer)" in body


@pytest.mark.django_db
def test_a_week_dated_later_waits(client, folder):
    write(folder, week("2099-W01", date(2099, 1, 1), [item("later", url=URL + "-later")]))
    issues._all.cache_clear()

    assert "data-news-empty" in client.get(reverse("news")).content.decode()


@pytest.mark.django_db
def test_each_language_reads_its_own_text(client, one_week, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]

    body = client.get("/de/news/").content.decode()

    assert "Sol Ring fliegt am 10. November aus Commander" in body


@pytest.mark.django_db
def test_the_page_is_public_and_in_the_sitemap(client):
    assert "news" in seo.PUBLIC_PAGES
    sitemap = client.get(reverse("sitemap")).content.decode()
    assert "/news/</loc>" in sitemap


@pytest.mark.django_db
def test_the_footer_and_the_legal_notice_name_it(client):
    home = client.get(reverse("home"), follow=True).content.decode()
    imprint = client.get(reverse("imprint"), follow=True).content.decode()

    assert f'href="{reverse("news")}"' in home
    assert "editorially responsible for the" in imprint


@pytest.mark.django_db
def test_the_feed_has_every_item_and_stays_out_of_search(client, one_week):
    response = client.get(reverse("news_feed"))

    assert response["X-Robots-Tag"] == "noindex"
    channel = ElementTree.fromstring(response.content).find("channel")  # noqa: S314
    entries = channel.findall("item")
    assert [entry.findtext("title") for entry in entries] == [
        "Sol Ring leaves Commander on November 10"]
    assert entries[0].findtext("link").endswith("/news/#2026-w45-sol-ring-banned")
    assert "(Source: Wargamer)" in entries[0].findtext("description")


# --- 3. subscribing and stopping ------------------------------------------------


@pytest.fixture
def mail_on(settings):
    settings.CHANGELOG_FROM_EMAIL = SENDER


def account(email="reader@example.com", verified=True, **extra):
    user = User.objects.create_user(email=email, password=PASSWORD, **extra)
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return user


@pytest.fixture
def subscribed(mail_on, db):
    user = account()
    LIST.subscribe(user)
    return user


@pytest.mark.django_db
def test_an_account_subscribes_with_one_click_and_only_to_the_news(client, mail_on):
    user = account()
    client.force_login(user)

    response = client.post(reverse("news_subscribe"), {"next": "/news/#follow"}, follow=True)

    user.refresh_from_db()
    assert user.news_mail and user.news_mail_since
    assert not user.changelog_mail
    assert "the Commander news comes by mail once a week" in response.content.decode()


@pytest.mark.django_db
def test_a_visitor_is_sent_to_sign_up_and_told_what_will_happen(client, mail_on):
    response = client.post(reverse("news_subscribe"), {"next": "/news/#follow"})

    assert response.url.startswith(reverse("account_signup"))
    body = client.get(response.url).content.decode()
    assert "data-news-wanted" in body
    assert "data-changelog-wanted" not in body


@pytest.mark.django_db
def test_signing_in_subscribes_an_existing_account(client, mail_on):
    user = account()
    client.post(reverse("news_subscribe"))

    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})

    user.refresh_from_db()
    assert user.news_mail
    assert not user.changelog_mail


@pytest.mark.django_db
def test_both_wishes_outlive_the_guests_sign_out(client, mail_on, rf):
    request = rf.get("/")
    request.session = client.session
    LIST.want(request)
    changelog_services.want(request)
    request.session["other"] = 1

    mailing.keep_across(request, lambda r: r.session.clear())

    assert set(request.session.keys()) == {LIST.intent, changelog_services.INTENT}


@pytest.mark.django_db
def test_your_plan_has_both_switches_and_stops_the_news(client, subscribed):
    client.force_login(subscribed)
    body = client.get(reverse("billing:plans")).content.decode()

    assert "data-news-mail-switch" in body
    assert 'id="changelog-mail"' in body
    client.post(reverse("news_unsubscribe_account"),
                {"next": reverse("billing:plans") + "#settings"})
    subscribed.refresh_from_db()
    assert not subscribed.news_mail and subscribed.news_mail_since is None


@pytest.mark.django_db
def test_opening_the_mails_link_changes_nothing_and_the_button_stops_it(client, subscribed):
    path = LIST.unsubscribe_path(subscribed)

    assert "data-unsubscribe" in client.get(path).content.decode()
    subscribed.refresh_from_db()
    assert subscribed.news_mail

    body = client.post(path).content.decode()
    subscribed.refresh_from_db()
    assert not subscribed.news_mail
    assert "gets no more news mails" in body


@pytest.mark.django_db
def test_each_list_has_its_own_link(client, subscribed):
    changelog_services.subscribe(subscribed)

    assert client.post(reverse("news_unsubscribe",
                               args=[changelog_services.unsubscribe_token(subscribed)])
                       ).status_code == 404
    assert client.post(reverse("changelog_unsubscribe", args=[LIST.token(subscribed)])
                       ).status_code == 404
    subscribed.refresh_from_db()
    assert subscribed.news_mail and subscribed.changelog_mail


@pytest.mark.django_db
def test_the_export_names_the_switch(subscribed):
    from accounts import privacy

    data = privacy.export(subscribed)["account"]

    assert data["news_mail"] is True
    assert data["news_mail_since"] == subscribed.news_mail_since


# --- 4. the mail ----------------------------------------------------------------


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setattr(tasks.send, "delay",
                        lambda pks, name, published: tasks.send(pks, name, published))


def test_the_mail_is_looked_for_every_morning():
    assert django_settings.CELERY_BEAT_SCHEDULE["news-digest"]["task"] == "news.digest"


@pytest.mark.django_db
def test_one_mail_per_subscriber_in_their_language(subscribed, one_week, eager, mailoutbox,
                                                     settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    german = account("leser@example.com", language="de")
    LIST.subscribe(german)

    assert tasks.digest("2026-11-03") == 2

    by_address = {mail.to[0]: mail for mail in mailoutbox}
    english, deutsch = by_address[subscribed.email], by_address[german.email]
    assert english.subject == "Commander news, week 45"
    assert english.from_email == SENDER
    assert "Sol Ring leaves Commander on November 10" in english.body
    assert URL in english.body
    assert "Summarised with AI" in english.body
    assert "Sol Ring fliegt" in deutsch.body
    assert "/news/#2026-w45-sol-ring-banned" in english.alternatives[0][0]


@pytest.mark.django_db
def test_every_mail_can_be_stopped_and_links_all_settings(subscribed, one_week, eager,
                                                          mailoutbox):
    tasks.digest("2026-11-03")

    mail = mailoutbox[0]
    path = LIST.unsubscribe_path(subscribed)
    assert mail.extra_headers["List-Unsubscribe"].endswith(f"{path}>")
    assert mail.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert path in mail.body
    assert f"{reverse('billing:plans')}#settings" in mail.body


@pytest.mark.django_db
def test_the_changelog_mail_links_all_settings_too(mail_on, eager, mailoutbox, monkeypatch):
    user = account()
    changelog_services.subscribe(user)
    monkeypatch.setattr(changelog_tasks.send, "delay",
                        lambda pks, month: changelog_tasks.send(pks, month))

    changelog_tasks.digest("2026-11-01")

    assert f"{reverse('billing:plans')}#settings" in mailoutbox[0].body
    assert f"{reverse('billing:plans')}#settings" in mailoutbox[0].alternatives[0][0]


@pytest.mark.django_db
def test_nobody_gets_a_week_twice(subscribed, one_week, eager, mailoutbox):
    tasks.digest("2026-11-03")

    assert tasks.digest("2026-11-04") == 0
    assert tasks.send([subscribed.pk], WEEK, MONDAY.isoformat()) == 0
    assert len(mailoutbox) == 1
    subscribed.refresh_from_db()
    assert subscribed.news_mailed == MONDAY


@pytest.mark.django_db
def test_a_stale_week_is_not_sent(subscribed, one_week, eager, mailoutbox):
    assert tasks.digest((MONDAY + timedelta(days=8)).isoformat()) == 0
    assert tasks.digest("2026-11-01") == 0, "not out yet"
    assert mailoutbox == []


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["off", "unconfirmed", "guest", "inactive"])
def test_who_does_not_get_it(who, mail_on, one_week, eager, mailoutbox):
    user = account(verified=who != "unconfirmed", is_guest=who == "guest",
                   is_active=who != "inactive")
    if who != "off":
        User.objects.filter(pk=user.pk).update(news_mail=True)

    assert tasks.digest("2026-11-03") == 0
    assert mailoutbox == []


@pytest.mark.django_db
def test_without_a_sender_nothing_is_offered_or_sent(client, one_week, settings, mailoutbox):
    settings.CHANGELOG_FROM_EMAIL = ""
    user = account()
    User.objects.filter(pk=user.pk).update(news_mail=True)

    assert "data-news-mail" not in client.get(reverse("news")).content.decode()
    assert tasks.digest("2026-11-03") == 0
    assert mailoutbox == []
