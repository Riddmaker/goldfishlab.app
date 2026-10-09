"""The cards the engine still cannot read, as players bring them (P19a).

The player's side does not change: the deck's review page names every card
the engine could not read, and they answer it on their own deck. This is the
operator's side of the same moment. Every uploaded deck is read once more,
after the upload is saved, and each card the engine alone could not read is
counted on an `UnreadCard` row - the card, the reader's reasons, how often.
**No deck, no account, nothing about a person** is kept, which is also why the
row outlives the guest who uploaded it.

From there: the admin lists the queue by how often a card came up, with what
players answered for it (`answers`); a Monday mail to ALERT_EMAIL names the
cards new since the last one (`weekly_mail`); and every night `recheck` closes
the cards the engine reads now - after a deploy that taught it, or after the
nightly catalogue refresh re-derived a profile.
"""

import json
import logging
from collections import Counter

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from simulation import ENGINE_VERSION
from simulations import gaps as gaps_module
from simulations.engine import adapter
from simulations.models import CardAnnotation, UnreadCard

logger = logging.getLogger(__name__)

#: How many cards one mail lists; the count is always complete.
LISTED = 50

#: How many of the most common player answers the admin shows.
ANSWERS = 3


def reading_reasons(gaps) -> list[str]:
    """The `reading` gaps' reasons, in order, each once."""
    return list(dict.fromkeys(
        gap.reason for gap in gaps if gap.kind == gaps_module.READING
    ))


def deck_cards(deck):
    """Every distinct card of a deck, commander included."""
    cards = {entry.oracle_card_id: entry.oracle_card
             for entry in deck.entries.select_related("oracle_card", "oracle_card__profile")}
    if deck.commander_id and deck.commander_id not in cards:
        cards[deck.commander_id] = deck.commander
    return list(cards.values())


def record(deck) -> int:
    """Count every card of `deck` the engine could not read. Returns how many.

    A card that had been read since comes back open, to be mailed again: the
    engine lost it, which is worth knowing. One the operator set aside stays
    set aside, and is only counted.
    """
    now = timezone.now()
    found = adapter.engine_gaps(deck_cards(deck))
    unread = {oracle_id: reasons for oracle_id, gaps in found.items()
              if (reasons := reading_reasons(gaps))}
    with transaction.atomic():
        existing = set(
            UnreadCard.objects.select_for_update()
            .filter(oracle_card_id__in=unread).values_list("oracle_card_id", flat=True)
        )
        for oracle_id, reasons in unread.items():
            if oracle_id not in existing:
                UnreadCard.objects.create(
                    oracle_card_id=oracle_id, reasons=reasons, seen=1,
                    last_seen=now, engine_version=ENGINE_VERSION,
                )
                continue
            UnreadCard.objects.filter(oracle_card_id=oracle_id).update(
                reasons=reasons, seen=F("seen") + 1, last_seen=now,
                engine_version=ENGINE_VERSION,
            )
            UnreadCard.objects.filter(
                oracle_card_id=oracle_id, status=UnreadCard.Status.READ
            ).update(status=UnreadCard.Status.OPEN, read_since=None, mailed_at=None)
    return len(unread)


def recheck() -> int:
    """Close the open cards the engine reads now. Returns how many."""
    rows = list(UnreadCard.objects.filter(status=UnreadCard.Status.OPEN)
                .select_related("oracle_card", "oracle_card__profile"))
    found = adapter.engine_gaps(row.oracle_card for row in rows)
    closed = 0
    for row in rows:
        reasons = reading_reasons(found[row.oracle_card_id])
        if reasons:
            if reasons != row.reasons:
                UnreadCard.objects.filter(pk=row.pk).update(reasons=reasons)
            continue
        UnreadCard.objects.filter(pk=row.pk).update(
            status=UnreadCard.Status.READ, read_since=ENGINE_VERSION
        )
        closed += 1
    return closed


def answers(oracle_card) -> tuple[int, list[tuple[str, int]]]:
    """How many players answered the card themselves, and their commonest values.

    Read in aggregate from their saved annotations: what they told the engine
    is what the card should do. Counted by value, never listed by who.
    """
    rows = CardAnnotation.objects.filter(oracle_card=oracle_card, owner__isnull=False)
    players = rows.values("owner_id").distinct().count()
    values = Counter(
        json.dumps(overrides, sort_keys=True)
        for overrides in rows.values_list("overrides", flat=True) if overrides
    )
    return players, values.most_common(ANSWERS)


def _line(row: UnreadCard) -> str:
    card = row.oracle_card
    path = reverse("admin:simulations_unreadcard_change", args=[card.pk])
    return "\n".join([
        f"- {card.name} (seen {row.seen}x) {settings.SITE_URL}{path}",
        *(f"  {reason}" for reason in row.reasons),
    ])


def weekly_mail() -> int:
    """Mail the cards new since the last mail to ALERT_EMAIL. Returns how many.

    Nothing new, no mail. Without an address the cards wait, so the first
    mail after one is set names them all.
    """
    rows = list(UnreadCard.objects.filter(status=UnreadCard.Status.OPEN, mailed_at=None)
                .select_related("oracle_card").order_by("-seen"))
    if not rows:
        return 0
    recipient = settings.ALERT_EMAIL
    if not recipient:
        logger.warning("unread cards (ALERT_EMAIL unset): %s new", len(rows))
        return 0
    open_total = UnreadCard.objects.filter(status=UnreadCard.Status.OPEN).count()
    body = "\n".join([
        f"Goldfish Lab: {len(rows)} card(s) the engine could not read, new this week "
        f"(engine v{ENGINE_VERSION}; {open_total} open in all).",
        "",
        *(_line(row) for row in rows[:LISTED]),
        "",
        "Counted from uploaded decks, without who uploaded them (simulations/unread.py).",
    ])
    send_mail(f"[Goldfish Lab] {len(rows)} new card(s) the engine cannot read",
              body, None, [recipient])
    # Only the listed ones: the rest come first next week.
    listed = rows[:LISTED]
    UnreadCard.objects.filter(pk__in=[row.pk for row in listed]).update(mailed_at=timezone.now())
    return len(listed)
