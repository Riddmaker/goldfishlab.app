"""Trying the site without an account (phase 9 G): the guest, its limits, its end.

**A guest is a real user row** - the "lazy sign-up" pattern - rather than a
deck with no owner. Every queryset in the application is already filtered by
`owner=request.user` (the pre-launch review checked for IDOR), so a guest's
deck, runs and playtests are private to the one browser signed in as that
guest without a single queryset changing. A nullable owner would have meant
touching every one of them, which is exactly where an IDOR hides.

Three things end a guest:

* `claim` - the person saves the deck; the rows move to the new account and
  the guest is deleted.
* `expire` - nobody saved within `LIFETIME`; the guest and everything it owns
  are deleted (hourly beat task, `guests.expire`).
* a new upload - a guest holds one deck; trying another replaces it (`reset`).
"""

import secrets
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.utils import timezone

from accounts import privacy
from billing.models import Plan, Subscription
from metrics import counts

#: The plan row every guest runs on (billing migration 0005).
GUEST_PLAN = "guest"

#: `.invalid` is reserved (RFC 2606): no mail can ever be delivered to it, so a
#: guest address can never reach a real inbox, whatever sends to it.
GUEST_DOMAIN = "guest.invalid"

#: How long a guest lives without saving. The privacy policy says the same.
LIFETIME = timedelta(hours=24)

#: The run a guest's upload starts by itself: the guest plan's ceiling, so the
#: first report is the best one a guest can have.
TRIAL_GAMES = 2_000
TRIAL_TURNS = 6

#: Guest runs queued or running at once, across ALL guests. A per-IP limit
#: stops one person; this stops many addresses together from filling the
#: short queue that every signed-in user's small runs share.
MAX_ACTIVE_GUEST_RUNS = 20

#: `login()` needs a backend when none authenticated the user. The plain
#: model backend: a guest never signs in through allauth.
BACKEND = "django.contrib.auth.backends.ModelBackend"


def is_guest(user) -> bool:
    """A signed-in guest - not a real account and not an anonymous visitor."""
    return bool(user.is_authenticated and getattr(user, "is_guest", False))


def create(request):
    """A new guest, signed in for this browser, on the guest plan."""
    user = get_user_model().objects.create_user(
        email=f"guest-{secrets.token_hex(12)}@{GUEST_DOMAIN}",
        # `set_password(None)` stores an unusable password: nobody can sign in
        # as a guest, the session is the only way to be one.
        password=None,
        is_guest=True,
    )
    # The signup signal gave it Free; a guest runs on the stricter plan.
    Subscription.objects.update_or_create(
        user=user, defaults={"plan": Plan.objects.get(slug=GUEST_PLAN)}
    )
    login(request, user, backend=BACKEND)
    counts.add(counts.Name.GUEST_STARTED)
    return user


def deck_of(guest):
    """The guest's one deck, or None."""
    return guest.decks.order_by("-updated_at").first()


def reset(guest) -> None:
    """Clear the way for another upload: a guest holds one deck at a time.

    The deck cascades to its runs and playtests. Unfinished column mappings go
    too - they would otherwise hold a second deck's file.
    """
    guest.decks.all().delete()
    guest.pending_imports.all().delete()


def busy() -> bool:
    """Are guests, all together, already using their share of the workers?"""
    from simulations.models import SimulationRun

    active = SimulationRun.objects.filter(
        owner__is_guest=True,
        status__in=[SimulationRun.Status.PENDING, SimulationRun.Status.RUNNING],
    ).count()
    return active >= MAX_ACTIVE_GUEST_RUNS


def _owned():
    """The models a guest's rows are moved in, and the field that owns them.

    Read from the privacy export's list, which a test keeps complete for every
    user-owned model, so a new model cannot be forgotten here either. Billing
    stays behind: the new account has its own subscription, and a guest's
    usage counters are not something to carry into it.
    """
    for _key, label, path in privacy.EXPORTED:
        if "__" in path or label.startswith("billing."):
            continue
        yield apps.get_model(label), path


@transaction.atomic
def claim(guest, user, *, deck_name: str = "") -> None:
    """Give everything the guest made to `user`, then delete the guest."""
    if not guest.is_guest:
        raise ValueError("only a guest can be claimed")
    if deck_name:
        guest.decks.update(name=deck_name)
    changed = []
    if guest.language and not user.language:
        # The language picked while trying the site (phase 12).
        user.language = guest.language
        changed.append("language")
    if guest.simulated_week and (not user.simulated_week
                                 or guest.simulated_week > user.simulated_week):
        # Counted as somebody who simulated this week already (P2).
        user.simulated_week = guest.simulated_week
        changed.append("simulated_week")
    if changed:
        user.save(update_fields=changed)
    for model, path in _owned():
        model.objects.filter(**{path: guest}).update(**{path: user})
    guest.delete()
    counts.add(counts.Name.GUEST_SAVED)


def expire(now=None) -> int:
    """Delete every guest older than `LIFETIME`, with all it owns."""
    cutoff = (now or timezone.now()) - LIFETIME
    stale = get_user_model().objects.filter(is_guest=True, date_joined__lt=cutoff)
    deleted = 0
    for guest in stale.iterator():
        privacy.delete(guest)
        deleted += 1
    return deleted
