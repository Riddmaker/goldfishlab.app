"""Changing a look and its images (B-10).

Every deck wears the account's look. A plan that allows more than one look
(`Plan.features["looks"]`) may give a deck a look of its own; that look counts
against the plan's number, the account's look included. This is a limit on
what you keep, not a metered quota (`billing.quotas` counts usage).
"""

from django.db import transaction

from billing.quotas import plan_for
from looks import images, presets
from looks.models import Look

#: Looks on a plan without a `looks` feature: one, for every deck.
LOOKS_DEFAULT = 1
#: Where a save goes: the account's look, or this deck's own.
ALL, DECK = "all", "deck"


class TooManyLooks(Exception):
    """The plan's number of looks is used up."""

    def __init__(self, limit: int):
        super().__init__(limit)
        self.limit = limit


def limit_for(user) -> int:
    return int(plan_for(user).features.get("looks", LOOKS_DEFAULT))


def per_deck(user) -> bool:
    """May this account give a deck a look of its own?"""
    return limit_for(user) > 1


def default_look(user) -> Look | None:
    """The account's look, or None: the standard look."""
    if not user.is_authenticated:
        return None
    return Look.objects.filter(owner=user, is_default=True).first()


def deck_look(user, deck) -> Look | None:
    """The deck's own look, or None."""
    return Look.objects.filter(owner=user, deck=deck).first()


def look_for(user, deck) -> Look | None:
    """The look a deck wears: its own while the plan allows it, else the
    account's. A deck's own look outlives a downgrade, unseen until the plan
    allows it again."""
    if not user.is_authenticated:
        return None
    if per_deck(user):
        return deck_look(user, deck) or default_look(user)
    return default_look(user)


def _store(look: Look, kind: str, data: bytes) -> str:
    """Write processed bytes and point the look at them; the old file goes
    after the commit, the new one at once if the row cannot be saved."""
    field = f"{kind}_image"
    old = getattr(look, field)
    new = images.write(look.pk, data)
    try:
        setattr(look, field, new)
        look.save()
    except Exception:
        images.remove(new)
        raise
    if old:
        transaction.on_commit(lambda: images.remove(old))
    return new


def set_image(look: Look, kind: str, upload) -> str:
    """Process an upload and make it the look's mat or sleeve.

    Raises `images.ImageRefused` with a message for the person; then nothing
    is written and the look is unchanged.
    """
    return _store(look, kind, images.process(upload, kind))


def clear_image(look: Look, kind: str) -> None:
    """Drop the uploaded mat or sleeve; the file goes after the commit."""
    field = f"{kind}_image"
    old = getattr(look, field)
    if not old:
        return
    setattr(look, field, "")
    look.save()
    transaction.on_commit(lambda: images.remove(old))


def _target(user, deck, scope) -> Look:
    """The row a save goes to. For the account: its look, and the deck
    follows it again (its own look goes). For the deck: its own look, a new
    one only while the plan has room (`TooManyLooks`)."""
    if scope != DECK:
        if deck is not None:
            Look.objects.filter(owner=user, deck=deck).delete()
        return default_look(user) or Look(owner=user, is_default=True)
    own = deck_look(user, deck)
    if own is not None:
        return own
    limit = limit_for(user)
    if limit <= 1 or Look.objects.filter(owner=user).count() >= limit:
        raise TooManyLooks(limit)
    return Look(owner=user, deck=deck)


@transaction.atomic
def save(user, data: dict, deck=None, scope=ALL) -> Look:
    """Store the Customisation form's `cleaned_data` as the account's look,
    or as `deck`'s own (`scope=DECK`).

    Both uploads are processed before anything is written, so a refused image
    (`images.ImageRefused`) or no room for a look (`TooManyLooks`) leaves the
    looks and the share as they were.
    """
    processed = {kind: images.process(data[f"{kind}_file"], kind)
                 for kind in images.KINDS if data.get(f"{kind}_file")}
    look = _target(user, deck, scope)
    look.mat_preset, look.sleeve_preset = data["mat"], data["sleeve"]
    look.inner_sleeve, look.inner_colour = data["inner_sleeve"], data["inner_colour"]
    look.seam, look.seam_colour = data["seam"], data["seam_colour"]
    look.save()
    for kind in images.KINDS:
        if kind in processed:
            _store(look, kind, processed[kind])
        elif data[kind] != presets.OWN:
            clear_image(look, kind)
    return look


@transaction.atomic
def reset(user, deck=None) -> None:
    """Back: a deck's own look goes (the deck wears the account's again);
    without one, the account's look goes (every deck wears the standard).
    The rows' files go with them."""
    if deck is not None and Look.objects.filter(owner=user, deck=deck).delete()[0]:
        return
    Look.objects.filter(owner=user, is_default=True).delete()
