"""Draw static/img/og-default.png, the link preview every page shares (P3).

Usage:  python scripts/og_image.py

1200 x 630 is the size Open Graph and Twitter cards show without cropping.
A PNG, because not every chat app shows WebP in a preview. Drawn from the
logo and the self-hosted EB Garamond with the colours of assets/css/input.css,
so a redesign means running this again rather than opening an image editor.
English only: one image serves every language until reports get their own (P4).
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "static/fonts/eb-garamond-var-normal-latin.woff2"
LOGO = ROOT / "static/img/favicon.svg"
OUT = ROOT / "static/img/og-default.png"

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{
  font-family: "EB Garamond"; font-weight: 400 700; src: url("{font}") format("woff2");
}}
html, body {{ margin: 0; width: 1200px; height: 630px; }}
body {{
  background: #12100d; color: #f5ecd7; font-family: "EB Garamond", serif;
  display: flex; align-items: center; gap: 64px; padding: 0 96px; box-sizing: border-box;
  border-bottom: 18px solid #86231d;
}}
svg {{ width: 260px; height: 260px; flex: none; }}
h1 {{ font-size: 104px; font-weight: 600; margin: 0; line-height: 1; letter-spacing: 1px; }}
p {{ margin: 0; }}
.tag {{ font-size: 42px; margin-top: 22px; color: #ebdcbd; }}
.sub {{ font-size: 32px; margin-top: 28px; color: #c2baa9; }}
</style></head><body>
{logo}
<div>
  <h1>Goldfish Lab</h1>
  <p class="tag">It actually plays your Commander deck.</p>
  <p class="sub">Thousands of games. Free to try, no account.</p>
</div>
</body></html>
"""


def main() -> None:
    # Inline, not file:// - a page set from a string is about:blank, which
    # may not load local files, and the screenshot came out without both.
    font = "data:font/woff2;base64," + base64.b64encode(FONT.read_bytes()).decode()
    html = PAGE.format(font=font, logo=LOGO.read_text(encoding="utf-8"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(OUT), type="png")
        browser.close()
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
