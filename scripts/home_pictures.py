"""Re-capture the two pictures on the home page from the running stack.

Usage:
    docker compose up -d
    py -3.13 scripts/home_pictures.py DECK_FILE [--base URL]

Unlike `screenshots.py`, the output of this one goes INTO the repository:
`static/img/home-hand.webp` and `static/img/home-report.webp`. Both are 720 x 540
(4:3) so they sit in two equal columns (phase 10 T1.5).

It uploads DECK_FILE through /try/ like a visitor would, which makes a real
guest in the local database (it expires after a day), waits for the trial run,
clips the first chart of the report, then deals a hand on the playtest table.
Chromium encodes the WebP itself, through a canvas, so no image library is
needed. Look at both pictures before committing them.
"""

import argparse
import base64
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "img"
WIDTH, HEIGHT = 720, 540
QUALITY = 0.86

#: Scales a PNG data URL onto a canvas of the final size and returns WebP.
ENCODE = """async ([dataUrl, width, height, quality]) => {
  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.getContext('2d').drawImage(image, 0, 0, width, height);
  return canvas.toDataURL('image/webp', quality);
}"""

#: Everything inside the report's first section that comes after its first
#: chart: the picture shows one chart and plain parchment below it.
HIDE_AFTER_FIRST_CHART = """() => {
  const chart = document.querySelector('#seen svg');
  for (const element of document.querySelectorAll('#seen *')) {
    const after = chart.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING;
    if (after && !chart.contains(element)) element.style.visibility = 'hidden';
  }
}"""

HIDE_BELOW_THE_HAND = """() => {
  for (const element of document.querySelectorAll('footer, main details')) {
    element.style.visibility = 'hidden';
  }
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("deck", type=Path, help="A deck file a visitor could upload.")
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(device_scale_factor=1).new_page()
        run_url = _upload(page, args.base, args.deck)
        _report_picture(page, run_url)
        _hand_picture(page, args.base)
        browser.close()
    print(f"wrote {OUT / 'home-hand.webp'} and {OUT / 'home-report.webp'} - look at both")
    return 0


def _upload(page, base: str, deck: Path) -> str:
    page.set_viewport_size({"width": 1280, "height": 1000})
    page.goto(f"{base}/try/", wait_until="networkidle")
    page.set_input_files("input[type=file]", str(deck))
    page.click("form.import-form button[type=submit]")
    page.wait_for_load_state("networkidle")
    if "/runs/" not in page.url:
        raise SystemExit(f"the upload landed on {page.url}, not on a run")
    run_url = page.url
    for _ in range(120):
        if page.query_selector("#seen"):
            return run_url
        time.sleep(3)
        page.goto(run_url, wait_until="networkidle")
    raise SystemExit("the trial run did not finish in six minutes")


def _report_picture(page, run_url: str) -> None:
    """From "By category" down, the full width of the section at 800 px."""
    page.set_viewport_size({"width": 800, "height": 1000})
    page.goto(run_url, wait_until="networkidle")
    page.evaluate(HIDE_AFTER_FIRST_CHART)
    heading = page.locator("#seen h3", has_text="By category").bounding_box()
    section = page.locator("#seen").bounding_box()
    width = section["width"]
    clip = {"x": section["x"], "y": heading["y"] - 20,
            "width": width, "height": width * HEIGHT / WIDTH}
    _save(page, page.screenshot(clip=clip, full_page=True), "home-report.webp")


def _hand_picture(page, base: str) -> None:
    """The hand on its table: Mulligan and Keep, the empty board, the fan."""
    page.set_viewport_size({"width": 1280, "height": 1300})
    page.goto(f"{base}/decks/", wait_until="networkidle")
    page.click("li a[href^='/decks/']")
    page.wait_for_load_state("networkidle")
    page.click("button[form=deal-hand]")
    page.wait_for_load_state("networkidle")
    page.evaluate(HIDE_BELOW_THE_HAND)
    page.wait_for_timeout(1500)  # the card images are lazy
    table = (page.locator("button", has_text="Mulligan")
             .locator("xpath=ancestor::section[1] | ancestor::div[contains(@class,'surface')][1]")
             .first.bounding_box())
    width = table["width"] + 32
    clip = {"x": table["x"] - 16, "y": table["y"] - 14,
            "width": width, "height": width * HEIGHT / WIDTH}
    _save(page, page.screenshot(clip=clip, full_page=True), "home-hand.webp")


def _save(page, png: bytes, name: str) -> None:
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    webp = page.evaluate(ENCODE, [data_url, WIDTH, HEIGHT, QUALITY])
    if not webp.startswith("data:image/webp"):
        raise SystemExit("this Chromium cannot encode WebP")
    (OUT / name).write_bytes(base64.b64decode(webp.split(",", 1)[1]))


if __name__ == "__main__":
    sys.exit(main())
