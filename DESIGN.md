# DESIGN & UI (GOLDFISH LAB)

The product is a **measurement instrument dressed as an old grimoire.** Two things have to be
true at once: it must feel like the Magic cards it simulates — pre-2003 dark fantasy, a tan
scroll textbox on near-black leather — and it must read like a tool whose numbers you would
trust. The tension between those is the design.

**The failure mode to avoid is the dashboard rainbow.** Six accent colours, gradient cards, a
donut chart per metric. That is what every competitor looks like, and it signals "we generated
this" precisely when the one thing being sold is rigour.

**The second failure mode is theme over function.** Parchment textures behind a table of
percentages, a serif so decorative the digits misalign. The atmosphere carries the chrome —
header, panels, buttons, empty states. **Data itself is set plainly.**

---

# ACCESSIBILITY — A HARD FLOOR, NOT A GOAL

WCAG 2.1 AA is the minimum, and it is enforced by test, not by intention:
`tests/test_design_tokens.py` parses `assets/css/input.css` and fails CI if any declared role
pairing drops below 4.5:1. **18 pairings are currently covered and all pass.**

- **Colour is never the only carrier of meaning.** A card marked "not modelled" gets the word,
  not just a hue. This matters more here than in most products: the honesty layer (Phase 4) exists
  to tell users what the engine could not simulate, and a colour-blind user must get that warning
  too.
- Every focusable element has a visible focus ring — 2px, `blood-700`, with offset.
- Full keyboard operability. The manual playtest table is the hard case and is server-driven
  precisely so it stays reachable without a mouse.
- A dark ground with low-chroma warm tones is exactly where contrast quietly fails. Never adjust
  a token without re-running the test.

# MOBILE-FIRST

Every layout is designed at phone width first, then widened. 16px side gutters, no horizontal
page scroll, ever.

The **playtest card table** is the one genuinely hard case: a board state does not fold into a
390px column. Its rule is explicit — on phone, zones stack vertically and scroll *within* their
own row; the board never forces the page sideways.

---

# TYPOGRAPHY

One serif carries almost everything, because the old-frame look is a serif look.

- **`--font-display` / `--font-body`: EB Garamond** (SIL Open Font License), a Garamond revival —
  the closest widely-available match to the type on a pre-2003 Magic card. **Self-hosted** from
  `static/fonts/`; the browser never contacts Google, which keeps visitor IPs out of a third
  party's logs and lets the dev container work offline. One variable woff2 per style covers
  400–700. `latin-ext` is split out by `unicode-range` so its 114 KB only loads when an accented
  glyph actually appears — which for this product means card names like *Lim-Dûl the Necromancer*.
  Georgia is the fallback during load.
- **`--font-mono`: system monospace stack.** Seeds, card counts, hex values, file paths — anything
  the user might copy or compare character by character.

**Numbers are a first-class case.** A simulation report is a wall of percentages, and
proportional digits make columns ragged and hard to scan down. `font-variant-numeric:
tabular-nums` applies to all table cells and to anything marked `.tabular`. Percentages are
right-aligned; labels are left-aligned.

**Measure.** Prose caps at ~70 characters (`max-w-3xl`). Tables may run full width.

---

# COLOUR SEMANTICS

Four families, each strictly bound to a meaning. The stock Tailwind palette is disabled
(`--color-*: initial`) so no colour can enter by accident.

| Family | Anchor | Means | Never used for |
|---|---|---|---|
| **ink** | `#12100d` @950 | The page ground, and all body text on parchment. Warm near-black, never pure black. | Accent or state |
| **parchment** | `#f5ecd7` @100 | Surfaces — cards, report panels, the playtest table. Also light text on the dark ground. | Success or danger |
| **blood** | `#c43d33` @500 | Primary action, danger, destructive confirmation, "needs review". | Body text, large fills |
| **verdigris** | — | Success, and "successfully modelled" states. Aged copper, not a bright UI green. | Decoration |

Rules that follow from this:

- **`blood` is an accent, never a surface.** A full-width red banner reads as an error page.
- **Success and danger must never be distinguishable by hue alone** — see accessibility above.
- **No colour outside these four.** A new meaning gets a new *step* in an existing family, or a
  new family added to `DESIGN.md` first — never an inline hex.
- Texture is CSS gradients only. No image assets for paper or leather: nothing extra to download,
  and it scales to any viewport.

---

# DENSITY

This is a data product for someone reading probabilities, not a marketing page.

- Report tables are compact: tight row height, a hairline rule between rows, no zebra striping
  (it fights the parchment texture).
- Panels get generous padding; the *content inside them* does not.
- One idea per panel. A report is several panels, not one long scroll.

---

# THE MARK AND THE UPLOAD (PHASE 9 A)

- **The mark** is `static/img/favicon.svg`: a goldfish in a flask, drawn only in tokens - ink-950
  tile, parchment-100 flask, blood-700 liquid, parchment-300 fish. It is the tab icon and sits
  beside the wordmark in the header. SVG only: every current browser takes an SVG tab icon, and
  the repository's push path is text-only. A PNG `apple-touch-icon` is the one thing missing.
- **Where a file goes, the whole area is the target.** A dashed parchment-600 box, blood-700 on
  hover and while a file is dragged over it, solid once a file is chosen, with the file's name in
  it. The real `<input type="file">` covers the box invisibly, so clicking and dropping are the
  browser's own and work without JavaScript.
- **Tabs** are radio buttons and CSS: the chosen tab is a parchment-100 face joined to its
  panel.

---

# STYLEGUIDE (VISUAL REFERENCE)

The binding, rendered implementation of this document is **`STYLEGUIDE.html`** (repo root):
wordmark and header, the full palette with hex values *and the measured AA ratio of every role
pairing*, type specimen, buttons and form fields in every state, panel patterns, the data
patterns the product actually needs (probability table, per-turn milestone row, coverage
callout), and a Do/Don't list.

It is a **standalone file** — no Django, no build step. Open it in a browser.

**A PDF export is deliberately NOT versioned.** Print CSS is included in the HTML, so one can be
produced at any time via browser print or
`msedge --headless --print-to-pdf=styleguide.pdf STYLEGUIDE.html`. The print stylesheet inverts
to a light ground; printing the dark theme would waste an ink cartridge and read badly.

There is also a live `/styleguide/` page in the Django app. That one is a **development tool** — it
dumps tokens straight out of `input.css` so a token edit is visible on reload. It is not the spec.

---

# SYNC RULE (HABIT 5)

Design decisions flow in exactly one direction:

```
DESIGN.md  ->  STYLEGUIDE.html  ->  assets/css/input.css  ->  tests/test_design_tokens.py
```

Change the rule here first, bring the styleguide in line, then move the tokens, then extend the
contrast test if a new role pairing was declared. **Never edit a token first and document it
afterwards** — that is how a design system turns into a pile of hex values nobody can justify.
