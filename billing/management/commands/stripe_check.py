"""Ask Stripe, read-only, whether payments are set up as the code expects (B-18).

    manage.py stripe_check

One line per finding (`billing.readiness`); fails when any finding would make
a payment go wrong. Prints no key and no secret, so it is safe to run in the
host's web console and paste the output.
"""

from django.core.management.base import BaseCommand, CommandError

from billing import readiness

MARKS = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


class Command(BaseCommand):
    help = "Check the Stripe setup (prices, tax codes, webhook) without changing anything."

    def handle(self, *args, **options):
        found = readiness.findings()
        for finding in found:
            self.stdout.write(f"{MARKS[finding.level]} {finding.text}")
        failed = sum(finding.level == "fail" for finding in found)
        if failed:
            raise CommandError(f"{failed} finding(s) would make a payment go wrong")
