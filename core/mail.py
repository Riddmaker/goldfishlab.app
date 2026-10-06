"""Production's mail backend: Django's SMTP one, with our own Message-ID (phase 12 J30).

Django writes `Message-ID: <...@{DNS_NAME}>` into every mail it sends, and
`DNS_NAME` is the machine's `socket.getfqdn()` (`django.core.mail.utils`). On
Jelastic that is the node's host name, so every confirmation, password reset
and alert said it came from a host that is not the From domain - one of the
small signals a spam filter adds up (the Hotmail Junk problem, J23).

This backend gives each message without a Message-ID one on the domain it is
sent from. A Message-ID the caller set is kept. `EmailMessage.message()` only
makes its own when `extra_headers` has none (Django 5.2), so nothing else
changes.
"""

from email.utils import make_msgid, parseaddr

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


def sender_domain(from_email: str) -> str:
    """`example.org` for `Name <someone@example.org>`, else ""."""
    return parseaddr(from_email)[1].rpartition("@")[2].lower()


class EmailBackend(SMTPEmailBackend):
    """SMTP, with a Message-ID on the sender's domain."""

    def send_messages(self, email_messages):
        for message in email_messages or ():
            if any(name.lower() == "message-id" for name in message.extra_headers):
                continue
            domain = sender_domain(message.from_email or settings.DEFAULT_FROM_EMAIL)
            if domain:
                # A new dict: the caller's headers may be shared between messages.
                message.extra_headers = {**message.extra_headers,
                                         "Message-ID": make_msgid(domain=domain)}
        return super().send_messages(email_messages)
