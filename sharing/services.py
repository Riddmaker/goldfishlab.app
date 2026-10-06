"""Sharing a run and stopping it, and counting who reads it (P4)."""

import re

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.translation import gettext, gettext_noop

from metrics import counts
from sharing.models import SharedReport
from simulation.cards import CARD_TYPES
from simulations import summary
from simulations.models import DeckSummary, SimulationRun

#: Why a run cannot be shared, in English (translated where shown).
NOT_FINISHED = gettext_noop("Only a finished report can be shared.")
GUEST = gettext_noop("Save your deck with a free account to share its report.")
DECK_CHANGED = gettext_noop(
    "The deck has changed since this run, so its card list is no longer the one "
    "these numbers came from. Run it again to share it.")

#: User agents that fetch a page only to draw a link preview or to index it.
#: Their visits are not readers; a missed one only makes the count a little high.
ROBOTS = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|embedly|preview|whatsapp|"
    r"telegram|skype|vkshare|pinterest|bitly|curl|wget|python-requests|headless",
    re.IGNORECASE,
)


def panel(request, run: SimulationRun, built: dict) -> dict:
    """What the share panel on the run page needs (`simulations/_share.html`).

    `built` is the run's `simulations.report.build`, which the page has already.
    """
    from core import seo
    from sharing import text

    shared = SharedReport.objects.filter(run=run).first()
    if shared is None:
        reason = refusal(run)
        return {"shared": None, "refusal": gettext(reason) if reason else ""}
    url = seo.absolute(request, shared.get_absolute_url())
    return {"shared": shared, "url": url, "text": text.plain(shared, built, run, url)}


def refusal(run: SimulationRun) -> str | None:
    """Why this run cannot be shared now, or None when it can."""
    if run.status != SimulationRun.Status.DONE or not run.result:
        return NOT_FINISHED
    if getattr(run.owner, "is_guest", False):
        return GUEST
    if not run.deck_print or run.deck_print != summary.fingerprint(run.deck):
        return DECK_CHANGED
    return None


def _type_of(type_line: str) -> str:
    """The first printed type of the front face, in deck-list order."""
    front = type_line.split("//")[0].lower()
    return next((kind for kind in CARD_TYPES if kind in front), "")


def _cards(deck) -> list[dict]:
    entries = deck.entries.select_related("oracle_card")
    cards = [
        {"name": entry.oracle_card.name, "quantity": entry.quantity,
         "type": _type_of(entry.oracle_card.type_line)}
        for entry in entries
    ]
    order = {kind: index for index, kind in enumerate(CARD_TYPES)}
    return sorted(cards, key=lambda card: (order.get(card["type"], len(order)), card["name"]))


def _summary(run: SimulationRun) -> tuple[dict, str]:
    """The written summary, if it is about exactly the deck this run played and
    its owner reads summaries at all."""
    if not run.owner.deck_summaries:
        return {}, ""
    written = DeckSummary.objects.filter(
        deck=run.deck, status=DeckSummary.Status.DONE, fingerprint=run.deck_print).first()
    if written is None or not written.content:
        return {}, ""
    return written.content, written.language


def share(run: SimulationRun) -> SharedReport:
    """The run's share, made now if it has none. Call `refusal` first."""
    existing = SharedReport.objects.filter(run=run).first()
    if existing:
        return existing
    deck = run.deck
    content, language = _summary(run)
    try:
        with transaction.atomic():
            return SharedReport.objects.create(
                run=run,
                deck_name=deck.name,
                commander=deck.commander.name if deck.commander_id else "",
                cards=_cards(deck),
                summary=content,
                summary_language=language,
            )
    except IntegrityError:
        # Two clicks at once: the other one made it.
        return SharedReport.objects.get(run=run)


def stop(run: SimulationRun) -> bool:
    """Stop sharing. The link answers 404 from now on."""
    deleted, _ = SharedReport.objects.filter(run=run).delete()
    return bool(deleted)


def count_view(shared: SharedReport, request) -> None:
    """One more reader, unless it is the owner or a robot."""
    if request.user.is_authenticated and request.user.pk == shared.run.owner_id:
        return
    agent = request.headers.get("User-Agent", "")
    if not agent or ROBOTS.search(agent):
        return
    SharedReport.objects.filter(pk=shared.pk).update(views=F("views") + 1)
    counts.add(counts.Name.REPORT_OPENED)
