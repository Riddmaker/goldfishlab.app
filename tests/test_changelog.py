"""C7: the changelog - a page, a feed, and a monthly mail nobody gets unasked.

What has to stay true:

1. **The page and the feed are public, in every language,** and list only
   what has happened: an entry dated tomorrow waits for tomorrow.
2. **The mail is off until a person asks for it,** by a click - never by a
   link opened, never by default. Without an account the click is kept until
   the account exists, and it subscribes even when the address is confirmed
   in another browser.
3. **Stopping it is one click wherever the mail is:** in the mail (the
   link's page and the mail program's own button), on the page, on "Your plan".
4. **One mail a month, only with news, once:** a batch sent twice sends
   nothing the second time, and nobody unconfirmed, guest or switched off
   gets one.
"""

import re
from datetime import date, timedelta
from xml.etree import ElementTree

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone, translation

from changelog import entries, services, tasks
from simulations import services as simulations
from tests.test_guests import PASSWORD, FakeRedis, upload

pytestmark = pytest.mark.django_db

User = get_user_model()
SENDER = "Goldfish Lab <noreply@goldfishlab.test>"


@pytest.fixture
def mail_on(settings):
    settings.CHANGELOG_FROM_EMAIL = SENDER


def account(email="reader@example.com", verified=True, **extra):
    user = User.objects.create_user(email=email, password=PASSWORD, **extra)
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return user


def confirmation_link(mailoutbox):
    return re.search(r"/accounts/confirm-email/[^/\s]+/", mailoutbox[-1].body).group(0)


# --- 1. the page and the feed ----------------------------------------------


def test_the_entries_are_newest_first_with_unique_anchors():
    days = [entry.day for entry in entries.ENTRIES]
    slugs = [entry.slug for entry in entries.ENTRIES]

    assert days == sorted(days, reverse=True)
    assert len(set(slugs)) == len(slugs)
    assert min(days) == entries.FIRST_DAY
    for entry in entries.ENTRIES:
        assert re.fullmatch(r"[a-z0-9-]+", entry.slug)
        if entry.url_name:
            assert entry.link  # resolves


def test_an_entry_dated_tomorrow_waits(monkeypatch):
    first = entries.ENTRIES[0]
    future = entries.Entry(first.day + timedelta(days=1), "future", "Soon", "Not yet.")
    monkeypatch.setattr(entries, "ENTRIES", (future, *entries.ENTRIES))

    assert future not in entries.published(first.day)
    assert future in entries.published(first.day + timedelta(days=1))


def test_the_page_shows_every_entry_and_offers_the_feed_first(client, mail_on):
    body = client.get(reverse("changelog")).content.decode()

    for entry in entries.published():
        assert f'id="{entry.slug}"' in body
    assert 'type="application/rss+xml"' in body
    assert body.index("RSS feed") < body.index("Subscribe by email") < body.index("September 2026")


def test_without_a_sender_the_page_offers_the_feed_alone(client, settings):
    settings.CHANGELOG_FROM_EMAIL = ""

    body = client.get(reverse("changelog")).content.decode()

    assert "RSS feed" in body
    assert "Subscribe by email" not in body
    assert client.post(reverse("changelog_subscribe")).status_code == 404


def test_the_page_is_public_and_in_the_sitemap(client, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]

    body = client.get("/de/changelog/").content.decode()
    sitemap = client.get(reverse("sitemap")).content.decode()

    assert '<html lang="de">' in body
    assert 'hreflang="en"' in body and "noindex" not in body
    assert "/changelog/</loc>" in sitemap and "/de/changelog/</loc>" in sitemap
    assert 'href="/changelog/"' in client.get(reverse("home")).content.decode()


def test_the_feed_is_rss_in_the_language_of_its_address(client, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]

    # Our own output, not user input.
    english = ElementTree.fromstring(client.get(reverse("changelog_feed")).content)  # noqa: S314
    german = ElementTree.fromstring(client.get("/de/changelog/feed/").content)  # noqa: S314

    items = english.findall("channel/item")
    assert english.tag == "rss"
    assert len(items) == len(entries.published())
    first = entries.published()[0]
    with translation.override("en"):
        assert items[0].findtext("title") == str(first.title)
    assert items[0].findtext("link").endswith(f"/changelog/#{first.slug}")
    assert german.findtext("channel/language") == "de"
    assert german.findall("channel/item")[0].findtext("link").endswith(
        f"/de/changelog/#{first.slug}")


# --- 2. asking for the mail --------------------------------------------------


def test_a_new_account_is_not_subscribed():
    assert not account().changelog_mail


def test_an_account_subscribes_with_one_click_and_is_told(client, mail_on):
    user = account()
    client.force_login(user)

    response = client.post(reverse("changelog_subscribe"), {"next": "/changelog/#follow"},
                           follow=True)

    user.refresh_from_db()
    assert user.changelog_mail
    assert user.changelog_mail_since is not None
    assert "subscribed: the changelog comes by mail" in response.content.decode()
    assert "You get it by mail" in response.content.decode()


def test_subscribing_is_a_post(client, mail_on):
    client.force_login(user := account())

    assert client.get(reverse("changelog_subscribe")).status_code == 405
    user.refresh_from_db()
    assert not user.changelog_mail


def test_subscribing_never_sends_anybody_to_another_site(client, mail_on):
    client.force_login(account())

    response = client.post(reverse("changelog_subscribe"), {"next": "https://evil.example/"})

    assert response.url == reverse("changelog")


def test_a_visitor_is_sent_to_sign_up_and_told_what_will_happen(client, mail_on):
    response = client.post(reverse("changelog_subscribe"), {"next": "/de/changelog/#follow"})

    assert response.url.startswith(reverse("account_signup"))
    assert "next=%2Fde%2Fchangelog%2F%23follow" in response.url
    body = client.get(response.url).content.decode()
    assert "data-changelog-wanted" in body


def test_signing_up_subscribes_and_the_link_comes_back_here(client, mail_on, mailoutbox):
    client.post(reverse("changelog_subscribe"), {"next": "/changelog/#follow"})
    client.post(f"{reverse('account_signup')}?next=/changelog/",
                {"email": "new@example.com", "password1": PASSWORD, "next": "/changelog/"})

    user = User.objects.get(email="new@example.com")
    assert user.changelog_mail, "kept on the account before the address is confirmed"

    response = client.post(confirmation_link(mailoutbox), follow=True)

    assert response.redirect_chain[-1][0] == "/changelog/"
    assert "subscribed: the changelog comes by mail" in response.content.decode()


def test_the_address_confirmed_in_another_browser_still_subscribes(client, mail_on,
                                                                  mailoutbox):
    client.post(reverse("changelog_subscribe"))
    client.post(reverse("account_signup"), {"email": "new@example.com", "password1": PASSWORD})

    other = client.__class__()
    other.post(confirmation_link(mailoutbox))

    user = User.objects.get(email="new@example.com")
    assert user.changelog_mail
    assert EmailAddress.objects.get(user=user).verified


def test_signing_in_subscribes_an_existing_account(client, mail_on):
    user = account()
    client.post(reverse("changelog_subscribe"))

    response = client.post(f"{reverse('account_login')}?next=/changelog/",
                           {"login": user.email, "password": PASSWORD, "next": "/changelog/"},
                           follow=True)

    user.refresh_from_db()
    assert user.changelog_mail
    assert "subscribed: the changelog comes by mail" in response.content.decode()


def test_a_wish_older_than_a_day_subscribes_nobody(client, mail_on):
    user = account()
    client.post(reverse("changelog_subscribe"))
    session = client.session
    session[services.INTENT] -= (services.INTENT_FOR + timedelta(minutes=1)).total_seconds()
    session.save()

    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})

    user.refresh_from_db()
    assert not user.changelog_mail


def test_signing_in_without_the_click_subscribes_nobody(client, mail_on):
    user = account()

    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})

    user.refresh_from_db()
    assert not user.changelog_mail


@pytest.fixture
def guest(client, catalogue, monkeypatch):
    monkeypatch.setattr(simulations, "_redis", FakeRedis)
    monkeypatch.setattr(simulations, "_dispatch", lambda run, tasks: None)
    upload(client)
    return User.objects.get(is_guest=True)


def test_a_guest_saves_the_deck_and_is_subscribed(client, mail_on, guest):
    response = client.post(reverse("changelog_subscribe"))
    assert response.url == reverse("guests:save")
    assert "data-changelog-wanted" in client.get(response.url).content.decode()

    client.post(reverse("guests:save"), {"deck_name": "Mine", "email": "saved@example.com",
                                         "password1": PASSWORD})

    assert User.objects.get(email="saved@example.com").changelog_mail


def test_a_guest_signing_in_keeps_the_wish(client, mail_on, guest):
    user = account()
    client.post(reverse("changelog_subscribe"))

    client.get(reverse("account_login"))  # ends the guest (guests.middleware)
    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})

    user.refresh_from_db()
    assert user.changelog_mail


# --- 3. stopping it -----------------------------------------------------------


@pytest.fixture
def subscribed(mail_on):
    user = account()
    services.subscribe(user)
    return user


def test_the_page_and_your_plan_both_unsubscribe(client, subscribed):
    client.force_login(subscribed)
    plans = client.get(reverse("billing:plans")).content.decode()
    assert 'id="changelog-mail"' in plans and "Unsubscribe" in plans

    response = client.post(reverse("changelog_unsubscribe_account"),
                           {"next": reverse("billing:plans") + "#settings"})

    subscribed.refresh_from_db()
    assert response.url == reverse("billing:plans") + "#settings"
    assert not subscribed.changelog_mail
    assert subscribed.changelog_mail_since is None
    assert "Subscribe by email" in client.get(reverse("billing:plans")).content.decode()


def test_your_plan_has_the_language_beside_it(client, subscribed, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    client.force_login(subscribed)

    body = client.get(reverse("billing:plans")).content.decode()

    assert 'id="settings-language"' in body
    assert body.index('id="settings-language"') < body.index('id="changelog-mail"')


def test_opening_the_mails_link_changes_nothing(client, subscribed):
    url = services.unsubscribe_path(subscribed)

    body = client.get(url).content.decode()

    subscribed.refresh_from_db()
    assert subscribed.changelog_mail
    assert "data-unsubscribe" in body and "<form method=\"post\"" in body
    assert "noindex" in body


def test_the_button_on_it_unsubscribes(client, subscribed):
    url = services.unsubscribe_path(subscribed)

    body = client.post(url).content.decode()

    subscribed.refresh_from_db()
    assert not subscribed.changelog_mail
    assert "Unsubscribed." in body


def test_the_mail_programs_own_button_needs_no_csrf_token(subscribed):
    from django.test import Client

    Client(enforce_csrf_checks=True).post(
        services.unsubscribe_path(subscribed), {"List-Unsubscribe": "One-Click"})

    subscribed.refresh_from_db()
    assert not subscribed.changelog_mail


def test_the_link_page_is_in_the_accounts_language(client, subscribed, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    subscribed.language = "de"
    subscribed.save()

    body = client.get(services.unsubscribe_path(subscribed)).content.decode()

    assert '<html lang="de">' in body


def test_the_link_is_the_same_in_every_mail(subscribed):
    """No timestamp in it: a mail sent a second later carries the same link."""
    assert services.unsubscribe_token(subscribed) == services.unsubscribe_token(subscribed)


def test_a_forged_link_does_nothing(client, subscribed):
    token = services.unsubscribe_token(subscribed)

    assert client.post(f"/changelog/unsubscribe/{token}x/").status_code == 404
    assert client.post("/changelog/unsubscribe/1/").status_code == 404
    subscribed.refresh_from_db()
    assert subscribed.changelog_mail


def test_the_link_opens_before_the_privacy_ok(client, subscribed):
    """A pending privacy OK (accounts.consent) must not stand between a
    person and stopping a mail."""
    User.objects.filter(pk=subscribed.pk).update(privacy_accepted=None)
    client.force_login(subscribed)

    assert client.get(services.unsubscribe_path(subscribed)).status_code == 200


def test_the_export_names_the_switch(subscribed):
    from accounts import privacy

    data = privacy.export(subscribed)["account"]

    assert data["changelog_mail"] is True
    assert data["changelog_mail_since"] == subscribed.changelog_mail_since


# --- 4. the monthly mail -------------------------------------------------------

OCTOBER = date(2026, 10, 1)


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setattr(tasks.send, "delay", lambda pks, month: tasks.send(pks, month))


def test_last_month():
    assert tasks.last_month(date(2026, 11, 1)) == OCTOBER
    assert tasks.last_month(date(2027, 1, 15)) == date(2026, 12, 1)


def test_the_mail_goes_out_on_the_first_from_beat(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["changelog-digest"]

    assert entry["task"] == "changelog.digest"
    assert entry["schedule"].day_of_month == {1}


def test_one_mail_per_subscriber_in_their_language(subscribed, eager, mailoutbox, settings):
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    german = account("leser@example.com", language="de")
    services.subscribe(german)

    assert tasks.digest("2026-11-01") == 2

    by_address = {mail.to[0]: mail for mail in mailoutbox}
    english, deutsch = by_address[subscribed.email], by_address[german.email]
    assert english.subject == "What's new on Goldfish Lab: October 2026"
    assert "Oktober 2026" in deutsch.subject
    assert english.from_email == SENDER
    for entry in entries.of_month(OCTOBER):
        assert str(entry.title) in english.body
    html = english.alternatives[0][0]
    assert f"/changelog/#{entries.of_month(OCTOBER)[0].slug}" in html


def test_every_mail_can_be_stopped_by_the_mail_program(subscribed, eager, mailoutbox):
    tasks.digest("2026-11-01")

    mail = mailoutbox[0]
    path = services.unsubscribe_path(subscribed)
    assert mail.extra_headers["List-Unsubscribe"].endswith(f"{path}>")
    assert mail.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert path in mail.body


def test_a_month_without_news_sends_nothing(subscribed, eager, mailoutbox):
    assert not entries.of_month(date(2026, 8, 1))
    assert tasks.digest("2026-09-01") == 0
    assert mailoutbox == []


def test_nobody_gets_it_twice(subscribed, eager, mailoutbox):
    tasks.digest("2026-11-01")
    sent = len(mailoutbox)

    assert tasks.send([subscribed.pk], OCTOBER.isoformat()) == 0
    assert tasks.digest("2026-11-01") == 0
    assert len(mailoutbox) == sent == 1
    subscribed.refresh_from_db()
    assert subscribed.changelog_mailed == OCTOBER


@pytest.mark.parametrize("who", ["off", "unconfirmed", "guest", "inactive"])
def test_who_does_not_get_it(who, mail_on, eager, mailoutbox):
    user = account(verified=who != "unconfirmed", is_guest=who == "guest",
                   is_active=who != "inactive")
    if who != "off":
        User.objects.filter(pk=user.pk).update(changelog_mail=True)

    assert tasks.digest("2026-11-01") == 0
    assert mailoutbox == []


def test_without_a_sender_nothing_is_sent(subscribed, eager, mailoutbox, settings):
    settings.CHANGELOG_FROM_EMAIL = ""

    assert tasks.digest("2026-11-01") == 0
    assert mailoutbox == []


def test_its_own_login_when_it_has_one(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(tasks, "get_connection", lambda **kwargs: seen.update(kwargs))
    settings.CHANGELOG_EMAIL_HOST_USER = "changelog@goldfishlab.test"
    settings.CHANGELOG_EMAIL_HOST_PASSWORD = "test-only"

    tasks.connection()

    assert seen == {"username": "changelog@goldfishlab.test", "password": "test-only"}

    seen.clear()
    settings.CHANGELOG_EMAIL_HOST_USER = ""
    tasks.connection()
    assert seen == {}


def test_one_refused_address_does_not_stop_the_rest(subscribed, eager, mailoutbox,
                                                     monkeypatch):
    import smtplib

    other = account("second@example.com")
    services.subscribe(other)
    real_send = tasks.EmailMultiAlternatives.send

    def send(mail, *args, **kwargs):
        if mail.to == [subscribed.email]:
            raise smtplib.SMTPRecipientsRefused({subscribed.email: (550, b"no")})
        return real_send(mail, *args, **kwargs)

    monkeypatch.setattr(tasks.EmailMultiAlternatives, "send", send)

    tasks.digest("2026-11-01")

    assert [mail.to for mail in mailoutbox] == [[other.email]]
    subscribed.refresh_from_db()
    assert subscribed.changelog_mailed is None, "tried again next time"


def test_the_since_date_is_the_consent_record(subscribed):
    assert timezone.now() - subscribed.changelog_mail_since < timedelta(minutes=1)
