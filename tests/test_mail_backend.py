"""Production's mail backend names our domain in the Message-ID (phase 12 J30).

Django would name the machine (`DNS_NAME`, the node's host name on Jelastic).
Nothing is sent here: the backend's connection is a stand-in, and `_send`
only records the message as it would go out.
"""

import pytest
from django.core.mail import EmailMessage

from core import mail


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
