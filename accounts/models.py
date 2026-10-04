"""Custom user model.

AUTH_USER_MODEL is set from the very first migration on purpose. Swapping it
later is one of the genuinely painful migrations in Django, and this project
will grow user-owned decks, simulation runs and playtests.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Manager for a user identified by email rather than username."""

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Users must have an email address")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff and is_superuser set")
        return self._create(email, password, **extra)


class User(AbstractUser):
    """A Goldfish Lab account. Identified by email; no username."""

    username = None
    email = models.EmailField("email address", unique=True)
    #: Somebody trying the site without an account (phase 9 G): created on the
    #: first upload, signed in for that browser only, with a placeholder
    #: address and no usable password. Saving the deck turns the guest's rows
    #: into a real account's; otherwise `guests.services.expire` deletes it.
    is_guest = models.BooleanField(default=False)
    #: The written deck summary (phase 10 H, T6.5): on by default, switched off
    #: by "Hide summaries" on the run page and on again on "Your plan". Off,
    #: the summary block is not shown and nothing is written or charged.
    deck_summaries = models.BooleanField(default=True)
    #: The language this person picked (phase 12), a code from
    #: `settings.LANGUAGE_NAMES`. Blank: follow the cookie and the browser.
    #: No `choices`: a language switched off later must not make the row
    #: invalid; `AccountLanguageMiddleware` ignores a code that is not on.
    language = models.CharField(max_length=10, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.email
