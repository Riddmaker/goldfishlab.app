"""Create a demo account with a deck already imported, for screenshots.

    py -3.13 manage.py seed_demo_deck
    py -3.13 manage.py seed_demo_deck --email me@example.com --password ...

Development only, and it refuses to run with DEBUG off. Its whole purpose is to
give `scripts/screenshots.py` something to photograph; a command that mints
accounts has no business anywhere near production.

The deck it imports is the reference Chainer list, read from the sibling
magic-project repository when that is present, and from the committed test
fixture otherwise - so this works on a fresh checkout.
"""

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from decks import services
from decks.models import Deck

ROOT = Path(settings.BASE_DIR)
REFERENCE_CSV = ROOT.parent / "magic-project" / "archidekt-collection-export-2026-09-15.csv"
FIXTURE_CSV = ROOT / "tests" / "fixtures" / "archidekt_sample.csv"

DEFAULT_EMAIL = "demo@goldfishlab.test"


class Command(BaseCommand):
    help = "Create a demo user with an imported deck (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument(
            "--password",
            default="",
            help="Defaults to $GOLDFISH_DEMO_PASSWORD, else a fresh random one that is printed.",
        )
        parser.add_argument("--name", default="Chainer, Dementia Master")

    @staticmethod
    def _password(given: str) -> tuple[str, bool]:
        """Never a literal in the source.

        A demo account is still an account, and a password committed to a
        repository is a password on every machine that repository reaches.
        Generated and printed once is both safer and no less convenient.
        """
        if given:
            return given, False
        from os import environ

        if environ.get("GOLDFISH_DEMO_PASSWORD"):
            return environ["GOLDFISH_DEMO_PASSWORD"], False

        import secrets

        return secrets.token_urlsafe(12), True

    def _verify_email(self, user) -> None:
        """Mark the demo address confirmed.

        Email verification is mandatory, so without this the account can be
        created but never signed into - allauth sends it straight to
        /accounts/confirm-email/ and the screenshot run finds an empty deck
        list with no explanation.
        """
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_deck refuses to run with DEBUG off")

        source = REFERENCE_CSV if REFERENCE_CSV.exists() else FIXTURE_CSV
        if not source.exists():
            raise CommandError(f"no deck list to import; looked for {source}")

        password, generated = self._password(options["password"])

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(email=options["email"])
        user.set_password(password)
        user.save()
        self.stdout.write(f"{'created' if created else 'updated'} {user.email}")
        if generated:
            self.stdout.write(self.style.WARNING(f"generated password: {password}"))

        self._verify_email(user)

        # Replace rather than pile up: this command is expected to be run again
        # and again while iterating on the page it exists to photograph.
        Deck.objects.filter(owner=user).delete()

        outcome = services.import_deck(
            owner=user,
            raw=source.read_bytes(),
            name=options["name"],
            filename=source.name,
        )

        record = outcome.record
        self.stdout.write(
            self.style.SUCCESS(
                f"imported {record.rows_resolved}/{record.rows_total} rows from {source.name} "
                f"({record.rows_unresolved} unresolved)"
            )
        )
        self.stdout.write(f"deck: {outcome.deck.get_absolute_url()}")
