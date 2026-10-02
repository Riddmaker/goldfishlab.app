"""Generate STYLEGUIDE.html from the design tokens.

Usage:  python scripts/build_styleguide.py

Why generated rather than hand-written: a styleguide that repeats hex values
and contrast ratios by hand starts lying the first time a token changes. This
reads `assets/css/input.css` for the palette and imports the role pairings
from `tests/test_design_tokens.py`, so the document can only ever show what is
actually defined and actually enforced.

The output is standalone - no Django, no build step, no external request. Open
it in a browser. Print CSS is included; a PDF is produced on demand and never
committed:

    msedge --headless --print-to-pdf=styleguide.pdf STYLEGUIDE.html
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_design_tokens import (  # noqa: E402
    AA_NORMAL,
    ROLE_PAIRINGS,
    contrast_ratio,
)

INPUT_CSS = ROOT / "assets" / "css" / "input.css"
OUTPUT = ROOT / "STYLEGUIDE.html"

FAMILY_MEANING = {
    "ink": ("The page ground, and all body text on parchment.",
            "Accent or state colour."),
    "parchment": ("Surfaces - cards, report panels, the playtest table. "
                  "Also light text on the dark ground.",
                  "Success or danger."),
    "blood": ("Primary action, danger, destructive confirmation, "
              "&ldquo;needs review&rdquo;.",
              "Body text, or large filled areas."),
    "verdigris": ("Success, and &ldquo;successfully modelled&rdquo; states.",
                  "Decoration."),
}
FAMILY_ORDER = ["ink", "parchment", "blood", "verdigris"]

DO = [
    "Set data plainly. Atmosphere belongs in the chrome, not behind a table of percentages.",
    "Right-align percentages, left-align labels, and use tabular figures so columns line up.",
    "Pair every state colour with a word. A hue alone is not a message.",
    "Keep <code>blood</code> as an accent - a button, a rule, a badge.",
    "Cap prose at about 70 characters. Tables may run full width.",
    "Say what the simulation could not model, in the same panel as the result.",
]
DONT = [
    "Six accent colours and a donut chart per metric. That is what every competitor looks like.",
    "A full-width <code>blood</code> banner - it reads as an error page.",
    "Parchment texture behind dense numeric data.",
    "Pure black (<code>#000</code>) or pure white. The ground is warm near-black.",
    "An inline hex value. A new meaning gets a step in an existing family, documented first.",
    "Zebra striping on report tables - it fights the parchment texture.",
]


def parse_scales() -> dict:
    css = INPUT_CSS.read_text(encoding="utf-8")
    found: dict = {}
    for family, step, value in re.findall(
        r"--color-([a-z]+)-(\d+):\s*(#[0-9a-fA-F]{6});", css
    ):
        found.setdefault(family, {})[int(step)] = value.lower()
    return found


def resolve(scales: dict, token) -> str:
    if isinstance(token, str):
        return {"white": "#ffffff", "black": "#000000"}[token]
    family, step = token
    return scales[family][step]


def label(token) -> str:
    if isinstance(token, str):
        return token
    return f"{token[0]}-{token[1]}"


def swatch_rows(scales: dict) -> str:
    out = []
    for family in FAMILY_ORDER:
        if family not in scales:
            continue
        means, never = FAMILY_MEANING.get(family, ("", ""))
        cells = "".join(
            f'<div class="sw"><div class="chip" style="background:{value}"></div>'
            f'<div class="step">{step}</div><div class="hex">{value}</div></div>'
            for step, value in sorted(scales[family].items())
        )
        out.append(
            f"""<section class="family">
  <h3>{family}</h3>
  <p class="rule"><strong>Means:</strong> {means}<br>
     <strong>Never:</strong> {never}</p>
  <div class="swatches">{cells}</div>
</section>"""
        )
    return "\n".join(out)


def pairing_rows(scales: dict) -> str:
    rows = []
    for description, fg, bg in ROLE_PAIRINGS:
        fg_hex, bg_hex = resolve(scales, fg), resolve(scales, bg)
        ratio = contrast_ratio(fg_hex, bg_hex)
        verdict = "pass" if ratio >= AA_NORMAL else "fail"
        rows.append(
            f"""<tr>
  <td>{description}</td>
  <td><code>{label(fg)}</code></td>
  <td><code>{label(bg)}</code></td>
  <td class="num">{ratio:.2f}:1</td>
  <td class="{verdict}">{"AA" if ratio >= AA_NORMAL else "FAIL"}</td>
  <td><span class="demo" style="color:{fg_hex};background:{bg_hex}">Aa 18.4%</span></td>
</tr>"""
        )
    return "\n".join(rows)


def build() -> str:
    scales = parse_scales()
    ratios = [
        contrast_ratio(resolve(scales, fg), resolve(scales, bg))
        for _, fg, bg in ROLE_PAIRINGS
    ]
    worst = min(ratios)
    token_count = sum(len(steps) for steps in scales.values())

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goldfish Lab &mdash; Styleguide</title>
<style>
/* GENERATED by scripts/build_styleguide.py - do not edit by hand.
   Standalone on purpose: no Django, no Tailwind, no external request. */
@font-face {{
  font-family: "EB Garamond"; font-style: normal; font-weight: 400 700;
  font-display: swap;
  src: url("static/fonts/eb-garamond-var-normal-latin.woff2") format("woff2");
}}
@font-face {{
  font-family: "EB Garamond"; font-style: italic; font-weight: 400 700;
  font-display: swap;
  src: url("static/fonts/eb-garamond-var-italic-latin.woff2") format("woff2");
}}
:root {{
  --ink-950: {scales["ink"][950]}; --ink-900: {scales["ink"][900]};
  --ink-800: {scales["ink"][800]}; --ink-700: {scales["ink"][700]};
  --ink-300: {scales["ink"][300]}; --ink-200: {scales["ink"][200]};
  --parch-50: {scales["parchment"][50]}; --parch-100: {scales["parchment"][100]};
  --parch-200: {scales["parchment"][200]}; --parch-300: {scales["parchment"][300]};
  --parch-600: {scales["parchment"][600]};
  --blood-700: {scales["blood"][700]}; --blood-800: {scales["blood"][800]};
  --blood-100: {scales["blood"][100]};
  --verd-700: {scales["verdigris"][700]}; --verd-100: {scales["verdigris"][100]};
  --verd-800: {scales["verdigris"][800]};
  --serif: "EB Garamond", Georgia, "Times New Roman", serif;
  --mono: ui-monospace, "Cascadia Mono", Menlo, monospace;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0 16px 6rem;
  background: var(--ink-950);
  background-image:
    radial-gradient(ellipse at 20% 0%, #1e1b16 0%, transparent 55%),
    radial-gradient(ellipse at 80% 100%, #191610 0%, transparent 55%);
  background-attachment: fixed;
  color: var(--parch-100);
  font-family: var(--serif); font-size: 17px; line-height: 1.55;
}}
.wrap {{ max-width: 60rem; margin: 0 auto; }}
h1 {{ font-size: 2.6rem; margin: 2.5rem 0 .3rem; line-height: 1.1; }}
h2 {{ font-size: 1.9rem; margin: 3rem 0 .4rem;
      border-bottom: 1px solid var(--ink-800); padding-bottom: .3rem; }}
h3 {{ font-size: 1.3rem; margin: 1.6rem 0 .3rem; text-transform: lowercase;
      letter-spacing: .02em; }}
p {{ max-width: 42rem; }}
.lede {{ color: var(--parch-200); font-size: 1.12rem; }}
.meta {{ color: var(--ink-300); font-size: .88rem; }}
code {{ font-family: var(--mono); font-size: .85em; color: var(--parch-300); }}
.rule {{ color: var(--ink-300); font-size: .92rem; }}
.rule strong {{ color: var(--parch-200); }}

.swatches {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr));
             gap: 6px; margin-top: .5rem; }}
.chip {{ height: 54px; border: 1px solid var(--ink-700); }}
.step {{ font-family: var(--mono); font-size: 11px; color: var(--ink-300); margin-top: 3px; }}
.hex  {{ font-family: var(--mono); font-size: 10px; color: var(--ink-700); }}

table {{ width: 100%; border-collapse: collapse; margin-top: .6rem;
         font-variant-numeric: tabular-nums; }}
th, td {{ text-align: left; padding: .38rem .5rem; font-size: .9rem;
          border-bottom: 1px solid var(--ink-800); }}
th {{ color: var(--ink-300); font-weight: 600; font-size: .8rem;
      text-transform: uppercase; letter-spacing: .04em; }}
td.num, th.num {{ text-align: right; font-family: var(--mono); }}
.pass {{ color: {scales["verdigris"][300]}; font-weight: 600; }}
.fail {{ color: {scales["blood"][300]}; font-weight: 600; }}
.demo {{ padding: 2px 8px; font-size: .85rem; }}

.surface {{
  background-color: var(--parch-100);
  background-image:
    radial-gradient(ellipse at 15% 10%, var(--parch-50) 0%, transparent 60%),
    radial-gradient(ellipse at 85% 90%, var(--parch-200) 0%, transparent 60%);
  color: var(--ink-900); border: 1px solid var(--parch-600); border-radius: 2px;
  box-shadow: 0 1px 0 var(--parch-50) inset, 0 8px 24px rgb(0 0 0 / 45%);
  padding: 1.1rem 1.3rem;
}}
.panel-dark {{ background: var(--ink-900); border: 1px solid var(--ink-800); padding: 1.1rem
               1.3rem; }}
.grid2 {{ display: grid; gap: 1rem; grid-template-columns: 1fr; }}
@media (min-width: 720px) {{ .grid2 {{ grid-template-columns: 1fr 1fr; }} }}

button, .btn {{ font-family: var(--serif); font-size: 1.02rem; padding: .55rem 1.1rem;
                border-radius: 2px; cursor: pointer; }}
.btn-primary   {{ background: var(--blood-700); border: 1px solid var(--blood-800);
                  color: var(--parch-100); }}
.btn-secondary {{ background: var(--parch-300); border: 1px solid var(--parch-600);
                  color: var(--ink-900); }}
.btn-success   {{ background: var(--verd-700); border: 1px solid var(--verd-800);
                  color: var(--parch-100); }}
.btn-disabled  {{ background: var(--ink-800); border: 1px solid var(--ink-700);
                  color: var(--ink-400, #9c9382); cursor: not-allowed; }}
.btn-focus {{ outline: 2px solid var(--blood-700); outline-offset: 1px; }}

.field {{ display: flex; flex-direction: column; gap: .2rem; margin-bottom: .9rem; }}
.field label {{ font-size: .85rem; font-weight: 600; color: var(--ink-800); }}
.field input {{ padding: .5rem .6rem; background: var(--parch-50);
                border: 1px solid var(--parch-600); border-radius: 2px;
                color: var(--ink-900); font-family: var(--serif); font-size: 1rem; }}
.field .help {{ font-size: .8rem; color: var(--ink-700); }}
.field .err  {{ font-size: .85rem; font-weight: 600; color: var(--blood-700); }}
.badge {{ padding: 2px 8px; font-size: .82rem; }}
.b-modelled {{ background: var(--verd-100); color: var(--verd-800); }}
.b-vanilla  {{ background: var(--ink-200);  color: var(--ink-800); }}
.b-review   {{ background: var(--blood-100); color: var(--blood-800); }}
.callout {{ border-left: 3px solid var(--parch-600); padding-left: .9rem; }}
ul.dodont {{ padding-left: 1.1rem; max-width: 42rem; }}
ul.dodont li {{ margin-bottom: .4rem; font-size: .93rem; }}

/* PRINT. Inverts to a light ground: printing the dark theme would waste an
   ink cartridge and read badly. */
@media print {{
  body {{ background: #fff !important; background-image: none !important;
          color: #111 !important; font-size: 10.5pt; padding: 0; }}
  h1, h2, h3 {{ color: #111 !important; }}
  h2 {{ border-bottom-color: #bbb !important; page-break-after: avoid; }}
  .lede, .meta, .rule, .rule strong, code {{ color: #333 !important; }}
  .step, .hex {{ color: #555 !important; }}
  th {{ color: #333 !important; }}
  th, td {{ border-bottom-color: #ccc !important; }}
  .panel-dark {{ background: #f4f4f4 !important; border-color: #bbb !important;
                 color: #111 !important; }}
  /* The screen verdict colours are light-on-dark; on white they vanish. */
  .pass {{ color: #1a3d30 !important; }}
  .fail {{ color: #6a1c17 !important; }}
  .surface {{ box-shadow: none !important; }}
  section, table, .grid2 {{ page-break-inside: avoid; }}
  .chip {{ border-color: #999 !important; }}
  a[href]::after {{ content: " (" attr(href) ")"; font-size: 9pt; color: #555; }}
}}
/* Motion (phase 10 E): the logo while a run plays. input.css .logo-* */
.logo-ground {{ fill: var(--ink-950); }}
.logo-water {{ fill: var(--blood-700); }}
.logo-glass {{ fill: none; stroke: var(--parch-100); }}
.logo-fish-body {{ fill: var(--parch-300); }}
.logo-fish-eye {{ fill: var(--ink-950); }}
.logo-bubble {{ fill: none; stroke: var(--parch-300); }}
.logo-fish, .logo-bubble {{ transform-box: fill-box; transform-origin: center; }}
.logo-animated .logo-fish {{ animation: logo-swim 5s ease-in-out infinite; }}
.logo-animated .logo-bubble {{ opacity: 0; animation: logo-rise 2.6s ease-in infinite; }}
.logo-animated .logo-bubble-2 {{ animation-delay: 1.3s; }}
@keyframes logo-swim {{
  0% {{ transform: translate(-1px, 0) scaleX(1); }}
  38% {{ transform: translate(2.5px, -1.4px) scaleX(1); }}
  48% {{ transform: translate(3px, -0.4px) scaleX(-1); }}
  86% {{ transform: translate(-2.2px, 0.9px) scaleX(-1); }}
  96% {{ transform: translate(-2.4px, 0.2px) scaleX(1); }}
  100% {{ transform: translate(-1px, 0) scaleX(1); }}
}}
@keyframes logo-rise {{
  0% {{ transform: translateY(5px); opacity: 0; }}
  25%, 75% {{ opacity: 1; }}
  100% {{ transform: translateY(-4px); opacity: 0; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .logo-animated .logo-fish, .logo-animated .logo-bubble {{ animation: none; opacity: 1; }}
}}
</style>
</head>
<body>
<div class="wrap">

<h1>Goldfish Lab &mdash; Styleguide</h1>
<p class="lede">The binding visual reference. Normative rules live in
<code>DESIGN.md</code>; this document renders them.</p>
<p class="meta">Generated from <code>assets/css/input.css</code> by
<code>scripts/build_styleguide.py</code> &mdash; do not edit by hand.
{token_count} colour tokens, {len(ROLE_PAIRINGS)} enforced role pairings,
worst measured contrast {worst:.2f}:1 (AA needs {AA_NORMAL}:1).</p>

<h2>Grounding</h2>
<p>A <strong>measurement instrument dressed as an old grimoire.</strong> It must feel like the
Magic cards it simulates &mdash; pre-2003 dark fantasy, a tan scroll textbox on near-black
leather &mdash; and read like a tool whose numbers you would trust.</p>
<p>The atmosphere carries the <em>chrome</em>: header, panels, buttons, empty states.
<strong>Data itself is set plainly.</strong></p>

<h2>Wordmark &amp; header</h2>
<div class="panel-dark" style="display:flex;align-items:baseline;gap:1.2rem">
  <span style="font-size:1.35rem">Goldfish&nbsp;Lab</span>
  <span class="meta">it actually plays your deck</span>
</div>
<p class="rule">Wordmark in the display serif, no logotype image. The tagline is the product
claim and is never changed for decoration.</p>

<h2>Palette</h2>
<p class="rule">The stock Tailwind palette is disabled (<code>--color-*: initial</code>), so no
colour can enter the system by accident.</p>
{swatch_rows(scales)}

<h2>Contrast &mdash; every enforced pairing</h2>
<p class="rule">These are the exact pairings asserted by
<code>tests/test_design_tokens.py</code>. The ratios below are computed, not transcribed.</p>
<table>
<thead><tr><th>Role</th><th>Foreground</th><th>Background</th><th class="num">Ratio</th>
<th>Verdict</th><th>Sample</th></tr></thead>
<tbody>
{pairing_rows(scales)}
</tbody>
</table>

<h2>Typography</h2>
<div class="grid2">
  <div class="surface">
    <div style="font-size:2.2rem;line-height:1.1">Display &mdash; EB Garamond</div>
    <div style="font-size:1.4rem">Subheading</div>
    <p style="margin:.6rem 0 0">Body text. EB Garamond, self-hosted, SIL Open Font
    License. Old-style proportions match the type on a pre-2003 card.
    <em>Italic for emphasis.</em> <strong>Semibold for labels.</strong></p>
    <p class="meta" style="color:var(--ink-700)">Accented glyphs load from the
    <code>latin-ext</code> subset only when needed: Lim-D&ucirc;l, &AElig;ther.</p>
  </div>
  <div class="surface">
    <p style="margin-top:0"><strong>Numbers use tabular figures.</strong> A report is a wall of
    percentages; proportional digits make columns ragged.</p>
    <table style="margin-top:.2rem">
      <thead><tr><th>Milestone</th><th class="num">R3</th><th class="num">R5</th>
      <th class="num">R6</th></tr></thead>
      <tbody>
        <tr><td>Commander in play</td><td class="num">5.9&nbsp;%</td>
            <td class="num">67.9&nbsp;%</td><td class="num">84.4&nbsp;%</td></tr>
        <tr><td>Sacrifice engine</td><td class="num">4.1&nbsp;%</td>
            <td class="num">11.9&nbsp;%</td><td class="num">18.0&nbsp;%</td></tr>
        <tr><td>Coffers + Urborg</td><td class="num">1.0&nbsp;%</td>
            <td class="num">1.7&nbsp;%</td><td class="num">2.1&nbsp;%</td></tr>
      </tbody>
    </table>
    <p class="meta" style="color:var(--ink-700);margin-bottom:0">Percentages right-aligned,
    labels left-aligned, no zebra striping.</p>
  </div>
</div>

<h2>Buttons &mdash; all states</h2>
<div class="surface">
  <div style="display:flex;flex-wrap:wrap;gap:.6rem;align-items:center">
    <button class="btn-primary">Run simulation</button>
    <button class="btn-secondary">Cancel</button>
    <button class="btn-success">Import deck</button>
    <button class="btn-disabled" disabled>Quota reached</button>
    <button class="btn-primary btn-focus">Focus visible</button>
  </div>
  <p class="rule" style="color:var(--ink-700);margin-bottom:0">One primary action per view.
  Focus ring is 2px <code>blood-700</code> with offset, on every focusable element.</p>
</div>

<h2>Form fields</h2>
<div class="surface" style="max-width:26rem">
  <div class="field">
    <label for="sg-a">Email</label>
    <input id="sg-a" type="email" placeholder="you@example.com">
  </div>
  <div class="field">
    <label for="sg-b">Iterations</label>
    <input id="sg-b" value="10000" class="btn-focus">
    <span class="help">Your plan allows up to 10,000 games per run.</span>
  </div>
  <div class="field">
    <label for="sg-c">Deck name</label>
    <input id="sg-c" value="" style="border-color:var(--blood-700)">
    <span class="err">This field is required.</span>
  </div>
</div>

<h2>Panels &amp; badges</h2>
<div class="grid2">
  <div class="surface">
    <h3 style="margin-top:0">Parchment surface</h3>
    <p style="margin:0">Cards, report panels, the playtest table. The default surface for
    anything the user reads closely.</p>
  </div>
  <div class="panel-dark">
    <h3 style="margin-top:0;color:var(--parch-100)">Raised dark panel</h3>
    <p style="margin:0">Secondary information on the page ground. Used sparingly.</p>
  </div>
</div>
<p style="margin-top:1rem">
  <span class="badge b-modelled">Modelled</span>
  <span class="badge b-vanilla">Treated as vanilla</span>
  <span class="badge b-review">Needs review</span>
</p>
<p class="rule">Each badge carries a <strong>word</strong>. Colour is never the only carrier of
meaning &mdash; the honesty layer has to reach a colour-blind reader too.</p>

<h2>The honesty callout</h2>
<div class="surface">
  <div class="callout">
    <strong>What this simulation did not model</strong>
    <p style="margin:.3rem 0 0">17 of your 99 cards have effects the engine cannot represent.
    They are treated as vanilla permanents: they cost mana and do nothing else.</p>
    <p class="meta" style="color:var(--ink-700);margin:.4rem 0 0">Opponent-dependent cards such
    as Rhystic Study are worth exactly zero in a goldfish, and will simulate far worse than they
    play.</p>
  </div>
</div>
<p class="rule">This pattern appears beside <em>every</em> result. It is the product's core
differentiator, not a disclaimer to be tucked away.</p>

<h2>Markers, bars and &ldquo;Why?&rdquo; (Phase 9)</h2>
<div class="surface">
  <p style="margin:0">
    <span style="display:inline-block;border:1px solid var(--blood-700);color:var(--blood-700);
      background:var(--parch-100);padding:.1rem .5rem;font-size:.85rem">! 3 cards need you</span>
    <span style="display:inline-block;border:1px solid var(--verd-800);color:var(--verd-800);
      background:var(--parch-100);padding:.1rem .5rem;font-size:.85rem;margin-left:.5rem"
      >&check; Ready</span>
  </p>
  <p style="margin:.8rem 0 0;display:flex;align-items:center;gap:.6rem">
    <span style="flex:1;height:.75rem;border:1px solid var(--parch-600);
      background:var(--parch-200)">
      <span style="display:block;height:100%;width:60%;background:var(--ink-800)"></span></span>
    <span class="meta" style="color:var(--ink-900)">3 of 5 decks</span>
    <a href="#" style="color:var(--ink-700);font-size:.85rem">Why?</a>
  </p>
</div>
<p class="rule">The deck marker, a usage bar and the small &ldquo;Why?&rdquo; link. Each says it
in words as well: the marker is a sentence, the bar has its count beside it, and blood-700 fills
a bar only when the limit is reached. The card grid, the line charts and the playtest hand are
described in <code>DESIGN.md</code> (&ldquo;Pictures, markers and charts&rdquo;) and live at
their templates; this file shows the tokens they are built from.</p>

<h2>Motion (Phase 10)</h2>
<div class="surface" style="display:flex;align-items:center;gap:1.2rem">
  <svg viewBox="0 0 32 32" width="72" height="72" class="logo-animated" aria-hidden="true">
    <defs><clipPath id="sg-flask">
      <path d="M13 4.5h6v7l7.6 12.4A2.7 2.7 0 0 1 24.3 28H7.7a2.7 2.7 0 0 1-2.3-4.1L13 11.5z"/>
    </clipPath></defs>
    <rect width="32" height="32" rx="6" class="logo-ground"/>
    <rect y="17" width="32" height="15" class="logo-water" clip-path="url(#sg-flask)"/>
    <path d="M13 4.5h6v7l7.6 12.4A2.7 2.7 0 0 1 24.3 28H7.7a2.7 2.7 0 0 1-2.3-4.1L13 11.5z"
          class="logo-glass" stroke-width="1.8" stroke-linejoin="round"/>
    <path d="M11.5 3.5h9" class="logo-glass" stroke-width="1.8" stroke-linecap="round"/>
    <g clip-path="url(#sg-flask)"><g class="logo-fish">
      <ellipse cx="17" cy="22.4" rx="4.6" ry="2.9" class="logo-fish-body"/>
      <path d="M12.9 22.4l-3.6-2.7v5.4z" class="logo-fish-body"/>
      <circle cx="19.2" cy="21.7" r="0.8" class="logo-fish-eye"/>
    </g></g>
    <circle cx="17.2" cy="14.6" r="1" class="logo-bubble" stroke-width="0.8"/>
    <circle cx="15.4" cy="10.2" r="0.7" class="logo-bubble logo-bubble-2" stroke-width="0.7"/>
  </svg>
  <p style="margin:0;font-style:italic;color:var(--ink-700)">Keeping a two-lander. Living
  dangerously.</p>
</div>
<p class="rule">While a run plays, and only then: the fish swims a circle in the flask, bubbles
rise, and one loading line beside it changes every three to five seconds with a 600&nbsp;ms fade.
The progress bar glides to each new value in 900&nbsp;ms. Under
<code>prefers-reduced-motion</code> all of it stands still: a still logo, one line, a bar that
steps. The shapes are the favicon's; only the colours moved into tokens.</p>

<h2>Do &amp; Don't</h2>
<div class="grid2">
  <div class="panel-dark">
    <h3 style="margin-top:0;color:{scales["verdigris"][300]}">Do</h3>
    <ul class="dodont">{"".join(f"<li>{item}</li>" for item in DO)}</ul>
  </div>
  <div class="panel-dark">
    <h3 style="margin-top:0;color:{scales["blood"][300]}">Don't</h3>
    <ul class="dodont">{"".join(f"<li>{item}</li>" for item in DONT)}</ul>
  </div>
</div>

<h2>Sync rule</h2>
<p><code>DESIGN.md</code> &rarr; <code>STYLEGUIDE.html</code> &rarr;
<code>assets/css/input.css</code> &rarr; <code>tests/test_design_tokens.py</code></p>
<p class="rule">Change the rule first, regenerate this file, then move the tokens, then extend
the contrast test. Never edit a token first and document it afterwards.</p>

</div>
</body>
</html>
"""


def main() -> int:
    OUTPUT.write_text(build(), encoding="utf-8")
    scales = parse_scales()
    ratios = [
        contrast_ratio(resolve(scales, fg), resolve(scales, bg))
        for _, fg, bg in ROLE_PAIRINGS
    ]
    failing = [d for (d, fg, bg), r in zip(ROLE_PAIRINGS, ratios, strict=True)
               if r < AA_NORMAL]
    print(f"Wrote {OUTPUT.name}: {sum(len(v) for v in scales.values())} tokens, "
          f"{len(ROLE_PAIRINGS)} pairings, worst {min(ratios):.2f}:1")
    if failing:
        print("FAILING pairings:", failing)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
