"""Write every account mail to files, for a look in a browser.

    py -3.13 manage.py preview_mails
    py -3.13 manage.py preview_mails --out screenshots/mails

One `<prefix>.html` and one `<prefix>.txt` per mail, each headed by its
subject, plus an index.html linking them all - and the changelog's monthly
mail (C7), which is not an account mail but wears the same frame. The samples live in
`accounts/mail_samples.py`, which tests/test_mails.py renders too. A mail
client is not a browser - this shows the layout and the words; the real test
is one sign-up read on a phone (docs/phases/phase-9-ux-overhaul.md, A2.5).
"""

from html import escape
from pathlib import Path

from django.core.management.base import BaseCommand
from django.test import RequestFactory

from accounts import mail_samples


class Command(BaseCommand):
    help = "Render every account mail to HTML and text files."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="screenshots/mails", help="Directory to write into.")

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        request = RequestFactory().get("/", SERVER_NAME="localhost", SERVER_PORT="8000")
        links = []
        mails = [(prefix, lambda prefix=prefix: mail_samples.render(prefix, request))
                 for prefix in mail_samples.SAMPLES]
        mails.append(("changelog_digest", mail_samples.changelog_digest))
        for prefix, build in mails:
            msg = build()
            html = next(body for body, mimetype in msg.alternatives if mimetype == "text/html")
            (out / f"{prefix}.html").write_text(html, encoding="utf-8")
            (out / f"{prefix}.txt").write_text(
                f"Subject: {msg.subject}\n\n{msg.body}\n", encoding="utf-8"
            )
            links.append(
                f'<li><a href="{prefix}.html">{escape(msg.subject)}</a> '
                f'(<a href="{prefix}.txt">text</a>)</li>'
            )
        index = (
            "<!DOCTYPE html><meta charset=utf-8><title>Mails</title><ul>" + "".join(links) + "</ul>"
        )
        (out / "index.html").write_text(index, encoding="utf-8")
        self.stdout.write(f"{len(links)} mails written to {out.resolve()}")
