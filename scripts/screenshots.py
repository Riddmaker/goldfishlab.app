"""Capture Playwright screenshots of the running app for visual review.

Usage:
    docker compose up -d
    py -3.13 scripts/screenshots.py [--out DIR] [--base URL]
    py -3.13 manage.py seed_demo_deck            # then, for the deck screens:
    py -3.13 scripts/screenshots.py --email demo@goldfishlab.test --password ...
    py -3.13 scripts/screenshots.py --guest      # also the trial, as a guest

Screenshots go to a scratch directory, NEVER into the repository (see
.gitignore). They exist to be looked at by a human or an agent once, then
thrown away.

Why this script exists at all: Phase 0 shipped three bugs that every view test
passed. The CSS 404'd, the allauth pages lost their layout, and a template tag
sat in the wrong place - and all three answered HTTP 200 with a broken page.
A screenshot pass is now part of finishing any phase that touches the UI.

The deck screens need a session, so they are captured only when credentials are
given. Without them the public pages are still captured and the run is a pass.
`--guest` walks the trial instead (phase 9 G): it uploads a small deck list
on /try/, which makes a real guest in the local database, and photographs
what a guest sees. The guest expires after a day like any other.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

#: (slug, path, viewport) - reachable without signing in.
PUBLIC_SHOTS = [
    ("home-desktop", "/", DESKTOP),
    ("home-phone", "/", PHONE),
    ("styleguide-desktop", "/styleguide/", DESKTOP),
    ("styleguide-phone", "/styleguide/", PHONE),
    ("signup-desktop", "/accounts/signup/", DESKTOP),
    # Phase 9 G: the guest trial's way in. Uploading from it makes a guest,
    # so the script photographs the page and never posts to it.
    ("try-desktop", "/try/", DESKTOP),
    ("try-phone", "/try/", PHONE),
    ("login-desktop", "/accounts/login/", DESKTOP),
    # Phase 8. All three are public on purpose: somebody has to be able to read
    # what is stored about them and how the numbers are made BEFORE deciding to
    # sign up. Both widths for the methodology page because it is the longest
    # prose page in the application and the one most likely to be read on a
    # phone, having been linked to from somewhere else.
    ("methodology-desktop", "/about/methodology/", DESKTOP),
    ("methodology-phone", "/about/methodology/", PHONE),
    ("terms-desktop", "/terms/", DESKTOP),
    ("privacy-desktop", "/privacy/", DESKTOP),
    # The widest table on any prose page - what is stored, why, and for how
    # long - and a table is the thing that breaks at 390px.
    ("privacy-phone", "/privacy/", PHONE),
    # C7: what changed; the feed's box and its address field at phone width.
    ("changelog-desktop", "/changelog/", DESKTOP),
    ("changelog-phone", "/changelog/", PHONE),
    # P3: the tiers for a visitor; three cards side by side become a column.
    ("pricing-desktop", "/pricing/", DESKTOP),
    ("pricing-phone", "/pricing/", PHONE),
]

#: (slug, path-template, viewport). `{deck}`, `{review}` and `{pending}` are filled in from
#: whatever the signed-in account actually has, so the script cannot drift out
#: of step with the seed command.
PRIVATE_SHOTS = [
    ("decks-list-desktop", "/decks/", DESKTOP),
    ("decks-list-phone", "/decks/", PHONE),
    ("decks-import-desktop", "/decks/import/", DESKTOP),
    ("decks-import-phone", "/decks/import/", PHONE),
    ("deck-detail-desktop", "{deck}", DESKTOP),
    ("deck-detail-phone", "{deck}", PHONE),
    ("import-review-desktop", "{review}", DESKTOP),
    ("import-review-phone", "{review}", PHONE),
    # Phase 6 §2. A state rather than a URL - `demo_screens.py` parks an upload
    # on it. Worth both widths: it is a form, a sample table and a live preview
    # stacked, which is the layout most likely to fall apart on a phone.
    ("import-map-desktop", "{pending}", DESKTOP),
    ("import-map-phone", "{pending}", PHONE),
    # Phase 6. With no Stripe keys - which is every environment but production
    # - this page says so and offers nothing to buy, and that is exactly the
    # state worth photographing: it is what a stranger running this repository
    # sees, and a broken upgrade button would be invisible to every test.
    ("plans-desktop", "/billing/", DESKTOP),
    ("plans-phone", "/billing/", PHONE),
    # Phase 8. The one screen in the application with an irreversible button on
    # it, so it is photographed at both widths: the confirmation field and the
    # warning above it have to stay together on a phone, or the warning becomes
    # something you scroll past.
    ("account-data-desktop", "/account/data/", DESKTOP),
    ("account-data-phone", "/account/data/", PHONE),
]

#: The honesty layer's card page needs a card id as well as a deck id, and
#: which card is worth photographing is a property of the data - the one with
#: something unresolved, because that is the screen the phase exists for. It is
#: discovered from the tune page rather than hardcoded.
CARD_SHOTS = [
    ("annotate-desktop", DESKTOP),
    ("annotate-phone", PHONE),
]

#: How many simulation runs to capture from the deck page. Every run linked
#: there is photographed, whatever state it is in, because the states worth
#: looking at - a progress bar mid-flight, a finished report, a cancelled run -
#: cannot be reached by navigating to a URL. They have to exist already, which
#: is what `scripts/demo_screens.py` arranges.
MAX_RUN_SHOTS = 4

#: What a guest sees after the trial upload (phase 9 G, added in I). The run
#: page is where the upload lands; `{deck}` is the guest's one deck.
#: A list every row of which resolves against the full catalogue, so the trial
#: starts its run instead of stopping on the import review.
GUEST_LIST = "\n".join([
    "1 Sol Ring", "1 Arcane Signet", "1 Dark Ritual", "1 Night's Whisper",
    "1 Sign in Blood", "1 Doom Blade", "1 Murder", "1 Read the Bones",
    "1 Phyrexian Arena", "1 Gravecrawler", "1 Vampire Nighthawk", "30 Swamp",
])
GUEST_SHOTS = [
    ("guest-run-desktop", "{run}", DESKTOP),
    ("guest-run-phone", "{run}", PHONE),
    ("guest-deck-desktop", "{deck}", DESKTOP),
    ("guest-deck-phone", "{deck}", PHONE),
    ("guest-save-desktop", "/try/save/", DESKTOP),
    ("guest-save-phone", "/try/save/", PHONE),
]

#: The playtest board, which only exists once a game has been dealt. Rather
#: than seeding one, the script presses the button on the deck page - so the
#: shot is evidence that the entry point works, not only that the template
#: renders.
PLAYTEST_SHOTS = [
    ("playtest-desktop", DESKTOP),
    ("playtest-phone", PHONE),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--out", default="")
    parser.add_argument("--email", default="", help="Sign in to capture the deck screens.")
    parser.add_argument("--password", default="")
    # The column-mapping screen is reachable only from the redirect that
    # creates it, so it cannot be found by clicking. `demo_screens.py` parks an
    # upload and passes the URL here; without it that shot falls back to the
    # deck page, which is what happened the first time this was wired up.
    parser.add_argument("--pending-url", default="")
    parser.add_argument("--guest", action="store_true",
                        help="Also upload through /try/ and capture the guest's pages.")
    args = parser.parse_args()

    out = Path(args.out) if args.out else Path.cwd() / "screenshots"
    out.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    captured = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        for slug, path, viewport in PUBLIC_SHOTS:
            page = browser.new_page(viewport=viewport)
            captured += _capture(page, args.base, slug, path, out, failures)
            page.close()

        if args.email:
            context = browser.new_context(viewport=DESKTOP)
            try:
                shots = _sign_in(
                    context, args.base, args.email, args.password, args.pending_url
                )
            except RuntimeError as exc:
                failures.append(str(exc))
                shots = []

            for slug, path, viewport in shots:
                page = context.new_page()
                page.set_viewport_size(viewport)
                captured += _capture(page, args.base, slug, path, out, failures)
                page.close()
            context.close()
        else:
            print("\nno --email given: skipping the deck screens")

        if args.guest:
            context = browser.new_context(viewport=DESKTOP)
            try:
                shots = _as_a_guest(context, args.base)
            except RuntimeError as exc:
                failures.append(str(exc))
                shots = []
            for slug, path, viewport in shots:
                page = context.new_page()
                page.set_viewport_size(viewport)
                captured += _capture(page, args.base, slug, path, out, failures)
                page.close()
            context.close()

        browser.close()

    if failures:
        print("\nProblems:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"\nAll {captured} pages captured cleanly.")
    return 0


def _capture(page, base: str, slug: str, path: str, out: Path, failures: list[str]) -> int:
    """Screenshot one page, recording a bad status or a console error."""
    errors: list[str] = []

    # Bind the list as a default argument: a bare closure over `errors` would
    # capture the *variable*, which is rebound on every iteration, so a
    # late-firing console event could append to the wrong page's list.
    def collect(message, sink=errors):
        if message.type == "error":
            sink.append(message.text)

    page.on("console", collect)
    response = page.goto(f"{base}{path}", wait_until="networkidle")
    status = response.status if response else 0
    if status != 200:
        failures.append(f"{path} returned HTTP {status}")

    page.screenshot(path=out / f"{slug}.png", full_page=True)
    print(f"{slug:26} HTTP {status}  {out / f'{slug}.png'}")
    if errors:
        failures.append(f"{path} console errors: {errors}")
    return 1


def _sign_in(context, base: str, email: str, password: str,
             pending_url: str = "") -> list[tuple]:
    """Sign in, then find the first deck and its most recent import.

    The URLs are discovered from the pages rather than hardcoded, so the script
    stays in step with whatever `seed_demo_deck` produced - and says so plainly
    when it produced nothing.
    """
    page = context.new_page()
    page.goto(f"{base}/accounts/login/", wait_until="networkidle")
    page.fill("input[name=login]", email)
    page.fill("input[name=password]", password)
    page.click("form button[type=submit], form input[type=submit]")
    page.wait_for_load_state("networkidle")

    page.goto(f"{base}/decks/", wait_until="networkidle")
    link = page.query_selector("li a[href^='/decks/']")
    if link is None:
        page.close()
        raise RuntimeError(
            "signed in, but no deck was listed - run `manage.py seed_demo_deck` first"
        )

    deck_url = link.get_attribute("href")
    deck_id = deck_url.strip("/").split("/")[-1]
    review_url = _find_review_url(page, base, deck_url)
    run_urls = _find_run_urls(page, base, deck_url)
    card_url = _find_card_url(page, base, deck_id)
    playtest_url = _deal_a_playtest(page, base, deck_url)
    page.close()

    resolved = []
    for slug, template, viewport in PRIVATE_SHOTS:
        path = (
            template.replace("{deck}", deck_url)
            .replace("{deck_id}", deck_id)
            .replace("{review}", review_url or deck_url)
            .replace("{pending}", pending_url or deck_url)
        )
        resolved.append((slug, path, viewport))

    if card_url:
        for slug, viewport in CARD_SHOTS:
            resolved.append((slug, card_url, viewport))

    # Both widths for every run. Which run is the finished one - and so which
    # page carries the report, the densest layout in the application - depends
    # on what the workers happened to have got through, so photographing only
    # the newest caught a progress bar twice and the report never.
    for index, run_url in enumerate(run_urls, start=1):
        resolved.append((f"run-{index}-desktop", run_url, DESKTOP))
        resolved.append((f"run-{index}-phone", run_url, PHONE))

    if playtest_url:
        for slug, viewport in PLAYTEST_SHOTS:
            resolved.append((slug, playtest_url, viewport))
    return resolved


def _as_a_guest(context, base: str) -> list[tuple]:
    """Upload the sample deck on /try/ and find the guest's run and deck.

    The upload starts the trial run by itself and lands on it, so the page it
    lands on is the run; the deck list holds its one deck.
    """
    page = context.new_page()
    page.goto(f"{base}/try/", wait_until="networkidle")
    page.set_input_files("input[type=file]", files=[{
        "name": "guest-deck.txt", "mimeType": "text/plain", "buffer": GUEST_LIST.encode(),
    }])
    page.click("form.import-form button[type=submit]")
    page.wait_for_load_state("networkidle")
    run_url = page.url[len(base):]
    if "/runs/" not in run_url:
        page.close()
        raise RuntimeError(f"the trial upload landed on {run_url}, not on a run")
    # "Your deck" in a guest's header is the deck list, which holds the one deck.
    page.goto(f"{base}/decks/", wait_until="networkidle")
    link = page.query_selector("li a[href^='/decks/']")
    deck_url = link.get_attribute("href") if link else ""
    page.close()
    if not deck_url:
        raise RuntimeError("a guest's header shows no link to the deck")
    return [(slug, path.replace("{run}", run_url).replace("{deck}", deck_url), viewport)
            for slug, path, viewport in GUEST_SHOTS]


def _deal_a_playtest(page, base: str, deck_url: str) -> str:
    """Open a playtest from the deck page and return where it landed.

    A few turns are played before the shot is taken, because an opening hand
    photographs a page with every zone empty - and empty zones are exactly
    where a layout looks fine and a real board does not.
    """
    page.goto(f"{base}{deck_url}", wait_until="networkidle")
    # Since phase 9 D the button sits in the Simulate row and names its form
    # (`form="deal-hand"`); looking for a button inside the form found nothing
    # and skipped the board silently until I.
    button = page.query_selector("button[form=deal-hand]")
    if button is None:
        raise RuntimeError(f"no Draw a hand button on {deck_url}")
    button.click()
    page.wait_for_load_state("networkidle")
    if "/playtest/" not in page.url:
        return ""

    # Locators rather than element handles: every click swaps the board out
    # from under the page, so a handle taken before the click is detached by
    # the time Playwright gets to it. A locator is re-resolved each attempt.
    advance = page.locator(
        "form:has(input[value=advance_phase]) button[type=submit]").first
    for _ in range(6):
        if not advance.count():
            break
        advance.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(150)

    return page.url[len(base):]


def _find_review_url(page, base: str, deck_url: str) -> str:
    """The deck's own import-review page, if it has one."""
    page.goto(f"{base}{deck_url}", wait_until="networkidle")
    link = page.query_selector("a[href*='/decks/imports/']")
    return link.get_attribute("href") if link else ""


def _find_card_url(page, base: str, deck_id: str) -> str:
    """The first card of the deck page's grid, which is one with an open value.

    The grid sorts those first on purpose, so taking the first link
    photographs the screen doing its job rather than a Swamp about which
    everything is already known.
    """
    page.goto(f"{base}/decks/{deck_id}/", wait_until="networkidle")
    link = page.query_selector(f"#card-grid a[href^='/decks/{deck_id}/tune/'][href$='/']")
    href = link.get_attribute("href") if link else ""
    return href if href and href.rstrip("/").split("/")[-1] != "tune" else ""


def _find_run_urls(page, base: str, deck_url: str) -> list[str]:
    """Every simulation linked from the deck page, newest first."""
    page.goto(f"{base}{deck_url}", wait_until="networkidle")
    links = page.query_selector_all("a[href^='/runs/']")
    urls = []
    for link in links:
        href = link.get_attribute("href")
        if href and href not in urls:
            urls.append(href)
    return urls[:MAX_RUN_SHOTS]


if __name__ == "__main__":
    raise SystemExit(main())
