"""The account mails and the confirmation link (phase 9 A2).

allauth's own mails went out as plain text that said "user <your address> has
given your email address to register an account", under a "[goldfishlab.app]"
subject, and the link led to a page asking to "confirm that x is an email
address for user x". Both read like phishing. These tests hold the
replacement: every mail allauth can send has our words and a branded HTML
version, and the link confirms without a question.
"""

import re
from pathlib import Path

import allauth
import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse

from accounts import mail_samples

User = get_user_model()

ALLAUTH_MAILS = Path(allauth.__file__).parent / "templates" / "account" / "email"
OUR_MAILS = Path(settings.BASE_DIR) / "templates" / "account" / "email"
PASSWORD = "a-long-enough-test-password"


def allauth_mails():
    """Every mail allauth can send - each one has a subject; the bases do not."""
    return sorted(p.name.removesuffix("_subject.txt") for p in ALLAUTH_MAILS.glob("*_subject.txt"))


# --- every mail has our words ---------------------------------------------------


def test_every_allauth_mail_has_an_override():
    """An allauth upgrade that adds a mail must not ship it in allauth's words.

    allauth sends `<prefix>_message.txt` and, if it exists, the `.html` next to
    it - so a mail without both overrides would go out plain and unbranded.
    """
    missing = [
        f"{prefix}_{part}"
        for prefix in allauth_mails()
        for part in ("subject.txt", "message.txt", "message.html")
        if not (OUR_MAILS / f"{prefix}_{part}").exists()
    ]
    assert missing == []


def test_every_mail_has_a_sample():
    """The samples are what the tests below and `preview_mails` render."""
    assert set(allauth_mails()) == set(mail_samples.SAMPLES)


@pytest.fixture
def mail_request():
    return RequestFactory().get("/")


@pytest.mark.parametrize("prefix", list(mail_samples.SAMPLES))
def test_every_mail_renders_in_our_words(prefix, mail_request):
    msg = mail_samples.render(prefix, mail_request)
    html = dict((mimetype, body) for body, mimetype in msg.alternatives)["text/html"]

    assert msg.subject
    assert "Goldfish Lab" in msg.subject
    assert not msg.subject.startswith("[")
    for body in (msg.body, html):
        assert f"user {mail_samples.EMAIL}" not in body
        assert "Hello from" not in body
        assert "example.com" not in body
        assert "Goldfish Lab" in body
    # Mail clients drop style sheets and block remote images and scripts.
    assert not re.search(r"<(style|link|img|script)\b", html)


@pytest.mark.parametrize(
    "prefix, key",
    [
        ("email_confirmation_signup", "activate_url"),
        ("email_confirmation", "activate_url"),
        ("password_reset_key", "password_reset_url"),
        ("unknown_account", "signup_url"),
        ("account_already_exists", "password_reset_url"),
    ],
)
def test_a_mail_with_a_link_has_a_button_and_the_plain_link(prefix, key, mail_request):
    """The button, and the address written out for clients that strip it."""
    url = mail_samples.SAMPLES[prefix](mail_request)[key]
    msg = mail_samples.render(prefix, mail_request)
    html = msg.alternatives[0][0]
    assert url in msg.body
    assert html.count(f'href="{url}"') == 2
    assert f">{url}</a>" in html


@pytest.mark.parametrize("prefix", ["login_code", "password_reset_code"])
def test_a_mail_with_a_code_shows_it(prefix, mail_request):
    msg = mail_samples.render(prefix, mail_request)
    assert mail_samples.CODE in msg.body
    assert mail_samples.CODE in msg.alternatives[0][0]


def test_a_notification_says_when_and_from_where(mail_request):
    msg = mail_samples.render("password_changed", mail_request)
    for body in (msg.body, msg.alternatives[0][0]):
        assert "203.0.113.7" in body
        assert "iPhone" in body
        assert reverse("account_reset_password") in body


def test_an_email_change_does_not_point_at_the_password_reset(mail_request):
    """It goes to the old address; a reset link would mail the new one."""
    msg = mail_samples.render("email_changed", mail_request)
    assert reverse("account_reset_password") not in msg.body
    assert "write to us" in msg.body


# --- sign-up, the mail it sends, and the link in it ----------------------------


def sign_up(client, email="newcomer@mail.test"):
    response = client.post(reverse("account_signup"), {"email": email, "password1": PASSWORD})
    assert response.status_code == 302
    return mail.outbox[-1]


def activate_url(message):
    return re.search(r"http://testserver(/accounts/confirm-email/[^/\s]+/)", message.body).group(1)


def test_the_sign_up_page_asks_for_one_password(client):
    page = client.get(reverse("account_signup")).content.decode()
    assert "Create a free account" in page
    assert "Just an email and a password" in page
    assert 'name="password1"' in page
    assert 'name="password2"' not in page


@pytest.mark.django_db
def test_sign_up_sends_the_branded_confirmation(client):
    message = sign_up(client)
    assert message.subject == "Confirm your email for Goldfish Lab"
    assert message.alternatives[0][1] == "text/html"
    assert activate_url(message) in message.alternatives[0][0]


@pytest.mark.django_db
def test_the_page_after_sign_up_names_the_address(client):
    response = client.post(
        reverse("account_signup"),
        {"email": "newcomer@mail.test", "password1": PASSWORD},
        follow=True,
    )
    page = response.content.decode()
    assert response.redirect_chain[-1][0] == reverse("account_email_verification_sent")
    assert "Check your inbox" in page
    assert "We sent a link to newcomer@mail.test" in page


@pytest.mark.django_db
def test_opening_the_link_does_not_confirm_by_itself(client):
    """A GET only shows the page; mail link scanners must not confirm."""
    url = activate_url(sign_up(client))
    page = client.get(url).content.decode()

    assert "Confirming your email" in page
    assert "data-autosubmit" in page
    assert "js/confirm-email.js" in page
    assert "for user" not in page
    assert not EmailAddress.objects.get(email="newcomer@mail.test").verified


@pytest.mark.django_db
def test_the_link_confirms_and_signs_in_the_browser_that_signed_up(client):
    url = activate_url(sign_up(client))
    response = client.post(url, follow=True)

    assert EmailAddress.objects.get(email="newcomer@mail.test").verified
    assert response.context["user"].email == "newcomer@mail.test"
    assert "✓ Email confirmed" in response.content.decode()


@pytest.mark.django_db
def test_the_link_in_another_browser_confirms_but_does_not_sign_in(client):
    """The phone's mail app: confirmed, then the sign-in page."""
    url = activate_url(sign_up(client))
    response = Client().post(url, follow=True)

    assert EmailAddress.objects.get(email="newcomer@mail.test").verified
    assert not response.context["user"].is_authenticated
    assert response.redirect_chain[-1][0] == reverse("account_login")
    assert "✓ Email confirmed" in response.content.decode()


@pytest.mark.django_db
def test_a_used_link_says_so_in_plain_words(client):
    url = activate_url(sign_up(client))
    client.post(url)
    page = Client().get(url).content.decode()
    assert "This link doesn't work any more" in page
    assert "data-autosubmit" not in page


def test_the_confirm_script_is_a_static_file():
    """The strict CSP (script-src 'self') only runs scripts served by us."""
    script = Path(settings.BASE_DIR) / "static" / "js" / "confirm-email.js"
    assert "form[data-autosubmit]" in script.read_text(encoding="utf-8")


# --- security notifications -----------------------------------------------------


@pytest.mark.django_db
def test_changing_the_password_sends_a_notification(client):
    user = User.objects.create_user(email="owner@mail.test", password=PASSWORD)
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    client.force_login(user)
    new = PASSWORD + "-changed"

    client.post(
        reverse("account_change_password"),
        {"oldpassword": PASSWORD, "password1": new, "password2": new},
    )

    assert [m.subject for m in mail.outbox] == ["Your Goldfish Lab password was changed"]
    assert mail.outbox[0].to == ["owner@mail.test"]
