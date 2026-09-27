"""Write the test-deck shapes into the database, for looking at.

    py -3.13 manage.py seed_test_decks
    py -3.13 manage.py seed_test_decks --only five_colour --only unmodellable
    py -3.13 manage.py seed_test_decks --email me@example.com

Development only. The shapes themselves live in `decks/fixtures.py` and are
what the test suite asserts against; this command exists so the same decks can
be opened in a browser, simulated, and photographed.

Why that matters more than it sounds: the screenshot pass has found three
defects a fully green suite missed - invisible form inputs, a page 106,000
pixels tall, and a report missing an entire table. A deck that only ever exists
inside a transaction cannot be looked at, and looking is the part that works.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from decks import seeding
from decks.fixtures import BY_KEY, SHAPES

DEFAULT_EMAIL = "testdecks@goldfishlab.test"


class Command(BaseCommand):
    help = "Write the test-deck shapes into the database (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument(
            "--only",
            action="append",
            choices=sorted(BY_KEY),
            help="Seed just this shape. Repeatable. Default: all of them.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            # It mints an account, like `seed_demo_deck`, and for the same
            # reason that is a development-only thing to do.
            raise CommandError("seed_test_decks refuses to run with DEBUG off")

        shapes = [BY_KEY[key] for key in options["only"]] if options["only"] else list(SHAPES)
        owner = self._owner(options["email"])

        try:
            seeded = seeding.build_all(owner, shapes)
        except seeding.MissingCards as exc:
            raise CommandError(str(exc)) from exc

        for result in seeded:
            self.stdout.write(f"  {result}")
            self.stdout.write(f"    {result.deck.get_absolute_url()}")

        self.stdout.write(
            self.style.SUCCESS(f"{len(seeded)} test decks for {owner.email}")
        )

    def _owner(self, email: str):
        user_model = get_user_model()
        owner, created = user_model.objects.get_or_create(email=email)
        if created:
            # Never signed into. It exists to own rows, not to be used, so it
            # gets no password rather than a weak one.
            owner.set_unusable_password()
            owner.save(update_fields=["password"])
        self._verify_email(owner)
        return owner

    @staticmethod
    def _verify_email(user) -> None:
        """Mark the address confirmed, so the decks can actually be opened.

        Email verification is mandatory; without this the account exists but
        every sign-in lands on the confirm-email page instead of the deck list.
        """
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user, email=user.email, defaults={"verified": True, "primary": True}
        )
