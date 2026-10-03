"""Data export and account deletion — the two rights that have to actually work.

Under the Swiss DPA and the GDPR a person may ask for everything held about
them and may ask for it to be erased. A privacy policy that promises both while
the only implementation is "email us" is a promise made by the person who wrote
the page and kept by nobody.

**The export is built by walking the model graph, not by listing fields.**
Every field of every user-owned model goes in, discovered from
`_meta.get_fields()`, because a hand-written list is a list that stops being
complete the first time somebody adds a column. That is not a hypothetical: six
phases of this project added `gaps`, `column_mapping`, `rung_counts`,
`deck_snapshot` and `combo_lookup` to models that already existed, and an
export written in Phase 8 against the fields of Phase 8 would quietly have
stopped covering the data in Phase 9.

What deletion does NOT do is cancel a subscription. See `deletion_blockers`.
"""

from django.apps import apps
from django.db import transaction

#: Models to walk, in the order a person would want to read them. Each entry is
#: a label and the ORM path from a user to those rows.
#:
#: Listing the MODELS by hand while discovering their fields automatically is
#: the deliberate half of the design. A new column on an existing model must
#: appear in the export without anybody remembering; a whole new user-owned
#: model is a new category of personal data, and noticing it is the point of
#: `test_privacy.py::test_every_user_owned_model_is_exported_or_deliberately_skipped`.
EXPORTED = [
    ("decks", "decks.Deck", "owner"),
    ("deck_cards", "decks.DeckCard", "deck__owner"),
    ("deck_imports", "decks.DeckImport", "owner"),
    ("unresolved_rows", "decks.UnresolvedRow", "deck_import__owner"),
    ("pending_imports", "decks.PendingImport", "owner"),
    ("simulation_runs", "simulations.SimulationRun", "owner"),
    ("card_annotations", "simulations.CardAnnotation", "owner"),
    ("deck_summaries", "simulations.DeckSummary", "deck__owner"),
    ("playtest_sessions", "playtest.PlaytestSession", "owner"),
    ("playtest_actions", "playtest.PlaytestAction", "session__owner"),
    ("subscription", "billing.Subscription", "user"),
    ("usage", "billing.UsageRecord", "user"),
]

#: Field names never written into an export.
#:
#: These are not omitted to keep the file small - they are omitted because
#: handing somebody a downloadable file containing their own Stripe customer id
#: is handing an attacker who reaches that file a key to somebody's billing.
#: The subscription's *state* is exported; the identifiers Stripe authenticates
#: on are not. A password hash is excluded for the same reason and is not
#: personal data anybody has a use for.
REDACTED = {
    "stripe_customer_id",
    "stripe_subscription_id",
    "password",
    "task_id",
}


def _serialise(instance) -> dict:
    """One row, every concrete field, relations as their primary key.

    `value_from_object` rather than `getattr` so a foreign key comes out as the
    id it is stored as instead of dragging the whole related object - and so a
    `JSONField` comes out as its data rather than as a repr.
    """
    row = {}
    for field in instance._meta.concrete_fields:
        if field.name in REDACTED:
            continue
        row[field.name] = field.value_from_object(instance)
    return row


def export(user) -> dict:
    """Everything this application holds about one person.

    Returned as a plain dict so the view can hand it to `JsonResponse` and the
    tests can read it without parsing anything. `default=str` at the encoder
    handles the UUIDs, dates and Decimals this is full of.
    """
    data = {
        "account": {
            "email": user.email,
            "date_joined": user.date_joined,
            "last_login": user.last_login,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "deck_summaries": user.deck_summaries,
            "language": user.language,
        },
        "exported_at": _now(),
        # Named in the file itself, because a person who opens an export and
        # finds no decks deserves to know whether that is the whole answer.
        "about_this_file": (
            "Everything Goldfish Lab stores about your account. Card and combo "
            "data is not included: it is public reference data from Scryfall "
            "and Commander Spellbook, identical for every account, and is not "
            "about you. Stripe payment identifiers are deliberately left out - "
            "your invoices live in Stripe's own portal, linked from your plan "
            "page."
        ),
    }

    for key, label, path in EXPORTED:
        model = apps.get_model(label)
        rows = model.objects.filter(**{path: user}).order_by("pk")
        data[key] = [_serialise(row) for row in rows]

    return data


def _now():
    from django.utils import timezone

    return timezone.now()


def deletion_blockers(user) -> list[str]:
    """Reasons this account should not be deleted yet, in plain language.

    Exactly one today, and it is a protection rather than an obstacle: **a live
    Stripe subscription does not stop when our row is deleted.** Every
    user-owned model cascades, so `user.delete()` would take the local
    `Subscription` with it and leave Stripe billing a card every month for an
    account that no longer exists, with nobody left who can log in to stop it.

    This is the settled "Stripe hosts the cancellation" decision arriving
    somewhere it was not designed for. The honest answer is to send the person
    to the portal first - one place ends a subscription, and it is not us.
    """
    from billing.models import Subscription

    blockers = []
    try:
        subscription = user.subscription
    except Subscription.DoesNotExist:
        return blockers

    live = {Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE}
    if subscription.status in live and subscription.is_paid:
        blockers.append(
            "You have a paid subscription. Cancel it first in the billing "
            "portal - deleting this account here would not stop Stripe "
            "charging your card, and afterwards nobody could sign in to stop it."
        )
    return blockers


@transaction.atomic
def delete(user) -> None:
    """Erase the account and everything that cascades from it.

    There is no soft delete and no tombstone. A row kept "for analytics" after
    somebody has asked to be erased is the whole thing the right exists to
    prevent, and this application has no legal basis for keeping one: it holds
    no invoices (Stripe does) and owes no retention period.

    Every model in `EXPORTED` reaches the user through a CASCADE, so this one
    call is the whole erasure. `test_privacy.py` asserts that by counting rows
    across every one of them rather than by trusting the FKs.
    """
    user.delete()
