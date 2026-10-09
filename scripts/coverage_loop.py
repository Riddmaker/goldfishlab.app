"""One turn of the P19 loop: a deck of hard cards, uploaded like a player would.

    .venv/bin/python manage.py runserver 127.0.0.1:8000      # in another shell
    .venv/bin/python scripts/coverage_loop.py --colours URG [--skip 0] [--out DIR]

Issue #36 teaches the engine one class of cards at a time. This script finds
the cards it cannot read yet and puts them in front of the site exactly as a
player would:

1. **A test deck.** The most-played Commander cards in the colours asked for
   that the engine alone cannot read (`adapter.engine_gaps`), up to 62 spells
   and 37 lands, around the most-played commander of exactly those colours,
   topped up with basics. `--skip` moves down the list for the next turn. It
   is written as an Archidekt CSV, the format the first user test used.
2. **The upload**, signed in as a local test account
   (`coverage@goldfishlab.invalid`, created here with a fresh random password
   that is never printed), through /decks/import/ in a real browser.
3. **What the player is shown**: the review walk ("1 of n", Skip, Skip ...)
   is followed to the end, and every card it names is listed - then checked
   against the engine's own reading of the same deck. They must agree.

The deck, the walk and the comparison go to `--out` as `deck.csv` and
`report.json`. **Only against a local server**: the script refuses any other
address, so it can never touch production's activity log or counters.
"""

import argparse
import csv
import json
import os
import secrets
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "goldfishlab.settings.dev")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from cards.models import OracleCard  # noqa: E402
from decks.models import Deck  # noqa: E402
from simulations import unread  # noqa: E402
from simulations.engine import adapter  # noqa: E402

EMAIL = "coverage@goldfishlab.invalid"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SPELLS, LANDS = 62, 37
#: How far down the most-played list to look for unreadable cards.
POOL = 6_000
BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
CSV_HEADER = ["Quantity", "Name", "Finish", "Condition", "Date Added", "Language",
              "Purchase Price", "Tags", "Edition Name", "Edition Code", "Multiverse Id",
              "Scryfall ID", "Collector Number", "Mana Value", "Price (Card Kingdom)",
              "Price (Card Market)"]


def _legal(colours: set[str]):
    return (OracleCard.objects.filter(legalities__commander="legal", edhrec_rank__isnull=False)
            .exclude(type_line__startswith="Basic Land")
            .select_related("profile").order_by("edhrec_rank"))


def _within(card, colours: set[str]) -> bool:
    return set(card.color_identity) <= colours


def build(colours: set[str], skip: int) -> tuple[OracleCard, list[tuple[int, OracleCard]], dict]:
    """The commander, the 99 and, per card, why the engine cannot read it."""
    commander = next(card for card in _legal(colours).filter(type_line__contains="Legendary")
                     .filter(type_line__contains="Creature")
                     if set(card.color_identity) == colours)
    pool = [card for card in _legal(colours)[:POOL] if _within(card, colours)
            and card.pk != commander.pk]
    gaps = adapter.engine_gaps(pool)
    hard = [card for card in pool if unread.reading_reasons(gaps[card.pk])][skip:]

    def is_land(card):
        return "Land" in card.type_line.split("//")[0]

    lands = [card for card in hard if is_land(card)][:LANDS]
    spells = [card for card in hard if not is_land(card)][:SPELLS]
    # Short of hard spells: the most-played readable ones, so it plays as a deck.
    for card in pool:
        if len(spells) >= SPELLS:
            break
        if not is_land(card) and card not in spells:
            spells.append(card)

    chosen = [(1, card) for card in spells + lands]
    missing = SPELLS + LANDS - len(chosen)
    basics = [OracleCard.objects.get(name=BASICS[colour]) for colour in sorted(colours)]
    for index, basic in enumerate(basics):
        count = missing // len(basics) + (1 if index < missing % len(basics) else 0)
        if count:
            chosen.append((count, basic))
    why = {card.name: unread.reading_reasons(gaps[card.pk]) for _count, card in chosen
           if card.pk in gaps and unread.reading_reasons(gaps[card.pk])}
    return commander, chosen, why


def write_csv(path: Path, commander, chosen) -> None:
    def row(quantity, card, tags=""):
        return [quantity, card.name, "Normal", "NM", "", "EN", "", tags, "", "", "",
                str(card.scryfall_id or ""), "", int(card.cmc), "", ""]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerow(row(1, commander, "Commander"))
        for quantity, card in chosen:
            writer.writerow(row(quantity, card))


def account() -> str:
    """The local test account, with a new password that only this run knows."""
    from allauth.account.models import EmailAddress

    user, _created = get_user_model().objects.get_or_create(email=EMAIL)
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    EmailAddress.objects.update_or_create(user=user, email=EMAIL,
                                          defaults={"verified": True, "primary": True})
    return password


def upload_and_walk(base: str, password: str, csv_path: Path) -> tuple[str, list[str]]:
    """Upload the deck as the test account and follow the review walk to its end."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base}/accounts/login/", wait_until="networkidle")
        page.fill("input[name=login]", EMAIL)
        page.fill("input[name=password]", password)
        page.click("main form button[type=submit]")
        page.wait_for_load_state("networkidle")

        page.goto(f"{base}/decks/import/", wait_until="networkidle")
        page.set_input_files("input[name=file]", str(csv_path))
        page.click("form[enctype='multipart/form-data'] button[type=submit]")
        page.wait_for_load_state("networkidle")
        path = urlsplit(page.url).path
        if not path.startswith("/decks/") or path.count("/") != 3:
            # The import review (unmatched rows) comes first; its link leads on.
            page.click("a[href^='/decks/'][href$='/']:not([href='/decks/'])")
            page.wait_for_load_state("networkidle")
            path = urlsplit(page.url).path
        deck_id = path.strip("/").split("/")[-1]

        walked = []
        page.goto(f"{base}/decks/{deck_id}/review/", wait_until="networkidle")
        while "/tune/" in page.url and len(walked) < SPELLS + LANDS + 1:
            walked.append(page.inner_text("h1").strip())
            # By its address: "Skip to content" would match the word as well.
            nxt = page.get_by_role("link", name="Skip", exact=True).get_attribute("href")
            page.goto(f"{base}{nxt}", wait_until="networkidle")
        browser.close()
    return deck_id, walked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--colours", default="URG", help="colour identity, e.g. URG or WUBRG")
    parser.add_argument("--skip", type=int, default=0, help="unreadable cards to pass over")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "goldfishlab-coverage"))
    args = parser.parse_args()

    if urlsplit(args.base).hostname not in LOCAL_HOSTS:
        parser.error("only a local server: this must never touch production")
    colours = {colour for colour in args.colours.upper() if colour in BASICS}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    commander, chosen, why = build(colours, args.skip)
    csv_path = out / "deck.csv"
    write_csv(csv_path, commander, chosen)
    deck_id, walked = upload_and_walk(args.base.rstrip("/"), account(), csv_path)

    deck = Deck.objects.get(pk=deck_id)
    engine = sorted(reading.oracle_card.front_name
                    for reading in adapter.readings(deck) if reading.unreadable)
    reasons = Counter(reason if "cannot hold a choice" not in reason
                      else "makes one of several colours (a choice)"
                      for reasons_ in why.values() for reason in reasons_)
    report = {
        "deck": f"{args.base.rstrip('/')}/decks/{deck_id}/",
        "commander": commander.name,
        "cards": sum(quantity for quantity, _card in chosen) + 1,
        "shown_to_player": len(walked),
        "engine_unreadable": len(engine),
        "agree": sorted(walked) == engine,
        "only_shown": sorted(set(walked) - set(engine)),
        "only_engine": sorted(set(engine) - set(walked)),
        "reasons": reasons.most_common(),
        "cards_unread": why,
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False),
                                     encoding="utf-8")
    print(f"{report['deck']}  {commander.name}")
    print(f"the player is asked about {len(walked)} cards; the engine cannot read "
          f"{len(engine)}; {'they agree' if report['agree'] else 'THEY DISAGREE'}")
    for reason, count in reasons.most_common(12):
        print(f"  {count:3d}  {reason}")
    print(f"written: {csv_path}, {out / 'report.json'}")
    return 0 if report["agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
