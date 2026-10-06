"""Production's mail backend: Django's SMTP one, with our own Message-ID (phase 12 J30),
Reply-To and Auto-Submitted (P6).

Django writes `Message-ID: <...@{DNS_NAME}>` into every mail it sends, and
`DNS_NAME` is the machine's `socket.getfqdn()` (`django.core.mail.utils`). On
Jelastic that is the node's host name, so every confirmation, password reset
and alert said it came from a host that is not the From domain - one of the
small signals a spam filter adds up (the Hotmail Junk problem, J23).

This backend gives each message without a Message-ID one on the domain it is
sent from. A Message-ID the caller set is kept. `EmailMessage.message()` only
makes its own when `extra_headers` has none (Django 5.2), so nothing else
changes.

P6 adds two more, each only where the caller set none:
* `Reply-To: EMAIL_REPLY_TO`. The From address (noreply@) is no mailbox, so
  a reply to it was lost; a sender nobody can answer is another small signal.
* `Auto-Submitted: auto-generated` (RFC 3834): every mail the application
  sends is automatic, and an auto-responder must not answer it.
"""

from email.utils import make_msgid, parseaddr

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


def sender_domain(from_email: str) -> str:
    """`example.org` for `Name <someone@example.org>`, else ""."""
    return parseaddr(from_email)[1].rpartition("@")[2].lower()


#: Headers set on every message that has none of its own name.
AUTO_SUBMITTED = ("Auto-Submitted", "auto-generated")


def _has(headers: dict, name: str) -> bool:
    return any(key.lower() == name.lower() for key in headers)


class EmailBackend(SMTPEmailBackend):
    """SMTP, with a Message-ID on the sender's domain, a Reply-To that is a
    mailbox, and the mark of an automatic mail."""

    def send_messages(self, email_messages):
        for message in email_messages or ():
            # A new dict: the caller's headers may be shared between messages.
            headers = dict(message.extra_headers)
            if not _has(headers, "Message-ID"):
                domain = sender_domain(message.from_email or settings.DEFAULT_FROM_EMAIL)
                if domain:
                    headers["Message-ID"] = make_msgid(domain=domain)
            if not _has(headers, AUTO_SUBMITTED[0]):
                headers[AUTO_SUBMITTED[0]] = AUTO_SUBMITTED[1]
            message.extra_headers = headers
            # Django reads a Reply-To header only under this exact name and
            # drops it otherwise (`_set_list_header_if_not_empty`, 5.2).
            if settings.EMAIL_REPLY_TO and not message.reply_to and "Reply-To" not in headers:
                message.reply_to = [settings.EMAIL_REPLY_TO]
        return super().send_messages(email_messages)
