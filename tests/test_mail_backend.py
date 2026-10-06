"""Production's mail backend names our domain in the Message-ID (phase 12 J30),
gives a Reply-To that is a mailbox and marks every mail as automatic (P6).

Django would name the machine (`DNS_NAME`, the node's host name on Jelastic).
Nothing is sent here: the backend's connection is a stand-in, and `_send`
only records the message as it would go out.
"""

import re
from urllib.parse import urlsplit

import pytest
from django.core.mail import EmailMessage
from django.test import RequestFactory

from accounts import mail_samples
from core import mail

HELLO = "Goldfish Lab <hello@goldfishlab.app>"


@pytest.fixture
def sent(monkeypatch):
    """Messages as they would go out, header by header."""
    messages = []
    monkeypatch.setattr(mail.EmailBackend, "_send",
                        lambda self, message: messages.append(message.message()) or True)
    return messages


def backend():
    instance = mail.EmailBackend(host="smtp.invalid")
    instance.connection = object()  # open() then reuses it and connects nowhere
    return instance


@pytest.fixture(autouse=True)
def reply_to(settings):
    settings.EMAIL_REPLY_TO = HELLO


def test_the_message_id_names_the_from_domain(sent, settings):
    settings.DEFAULT_FROM_EMAIL = "Goldfish Lab <noreply@goldfishlab.app>"

    count = backend().send_messages([EmailMessage("Hi", "Body", None, ["a@example.org"])])

    assert count == 1
    assert sent[0]["Message-ID"].endswith("@goldfishlab.app>")


def test_a_message_sent_from_elsewhere_names_its_own_domain(sent):
    message = EmailMessage("Hi", "Body", "Alerts <alerts@Example.org>", ["a@example.org"])

    backend().send_messages([message])

    assert sent[0]["Message-ID"].endswith("@example.org>")


def test_a_message_id_the_caller_set_is_kept(sent):
    message = EmailMessage("Hi", "Body", "noreply@goldfishlab.app", ["a@example.org"],
                           headers={"message-id": "<thread-1@goldfishlab.app>"})

    backend().send_messages([message])

    assert sent[0]["Message-ID"] == "<thread-1@goldfishlab.app>"


def test_shared_headers_are_not_changed_for_the_next_message(sent):
    shared = {"X-Kind": "alert"}
    messages = [EmailMessage("Hi", "Body", "noreply@goldfishlab.app", ["a@example.org"],
                             headers=shared) for _ in range(2)]

    backend().send_messages(messages)

    assert shared == {"X-Kind": "alert"}
    assert sent[0]["Message-ID"] != sent[1]["Message-ID"]


def test_production_sends_through_it():
    from pathlib import Path

    prod = (Path(__file__).resolve().parent.parent / "goldfishlab/settings/prod.py").read_text(
        encoding="utf-8")
    assert 'default="core.mail.EmailBackend"' in prod


# --- P6: Reply-To and Auto-Submitted ----------------------------------------------


def test_a_reply_goes_to_a_mailbox_and_the_mail_says_it_is_automatic(sent):
    backend().send_messages([EmailMessage("Hi", "Body", "noreply@goldfishlab.app",
                                          ["a@example.org"])])

    assert sent[0]["Reply-To"] == HELLO
    assert sent[0]["Auto-Submitted"] == "auto-generated"


def test_a_reply_to_and_auto_submitted_the_caller_set_are_kept(sent):
    messages = [
        EmailMessage("Hi", "Body", "noreply@goldfishlab.app", ["a@example.org"],
                     reply_to=["support@example.org"]),
        EmailMessage("Hi", "Body", "noreply@goldfishlab.app", ["a@example.org"],
                     headers={"Reply-To": "other@example.org",
                              "auto-submitted": "auto-replied"}),
    ]

    backend().send_messages(messages)

    assert sent[0]["Reply-To"] == "support@example.org"
    assert sent[1]["Reply-To"] == "other@example.org"
    assert sent[1]["Auto-Submitted"] == "auto-replied"
    assert len(sent[1].get_all("Auto-Submitted")) == 1


def test_without_a_reply_to_address_there_is_no_reply_to(sent, settings):
    settings.EMAIL_REPLY_TO = ""

    backend().send_messages([EmailMessage("Hi", "Body", "noreply@goldfishlab.app",
                                          ["a@example.org"])])

    assert sent[0]["Reply-To"] is None


def test_production_answers_from_hello():
    from pathlib import Path

    prod = (Path(__file__).resolve().parent.parent / "goldfishlab/settings/prod.py").read_text(
        encoding="utf-8")
    assert f'default="{HELLO}"' in prod


#: Every address a mail carries in its text or its HTML.
_LINK = re.compile(r"(?:https?://|mailto:)[^\s\"<>]+")


@pytest.mark.parametrize("prefix", list(mail_samples.SAMPLES))
def test_every_account_mail_goes_out_as_production_sends_it(prefix, sent, settings, db):
    """Our domain in the Message-ID, a mailbox to reply to, the automatic
    mark, and links only to the site the mail came from: a link to another
    host beside the From domain is one more thing a spam filter counts."""
    settings.DEFAULT_FROM_EMAIL = "Goldfish Lab <noreply@goldfishlab.app>"
    settings.ALLOWED_HOSTS = ["goldfishlab.app"]
    request = RequestFactory().get("/", HTTP_HOST="goldfishlab.app", secure=True)
    message = mail_samples.render(prefix, request)

    backend().send_messages([message])

    out = sent[0]
    assert out["From"] == "Goldfish Lab <noreply@goldfishlab.app>"
    assert out["Message-ID"].endswith("@goldfishlab.app>")
    assert out["Reply-To"] == HELLO
    assert out["Auto-Submitted"] == "auto-generated"
    links = _LINK.findall(f"{message.body} {message.alternatives[0][0]}")
    assert {urlsplit(link).netloc for link in links if not link.startswith("mailto:")} \
        <= {"goldfishlab.app"}
