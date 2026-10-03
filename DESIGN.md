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
pairing drops below 4.5:1. **19 pairings are currently covered and all pass.**

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
- **The mark, inline** (`core/_logo.html`, `.logo-*`, phase 10 E): the same shapes with a class
  on each part and the colours as tokens, so it can move; a test keeps it equal to the favicon.
  With `logo-animated` the fish swims a circle in the flask (5 s, turning round through a scaleX)
  and two bubbles rise (2.6 s). It swims only while a run plays.
- **The fish at home** (`static/js/fish.js`, phase 10 F): on the home page, and only there, the
  header mark is the inline logo and a link marked `aria-current="page"`. Idle, the fish makes
  one move under 2 s now and then - first after 20-30 s without input, then every 25-60 s, at
  most six a visit: a glance (head turn, eye wide) about 60 %, a turn round about 30 %, a jump
  out of the flask about 10 %, never two jumps running, always the glance first. A click tilts
  the flask, sloshes the water and the fish glances; three clicks within 1.5 s and it jumps.
  Reduced motion: no idle moves, and a click only widens the eye.
- **Where a file goes, the whole area is the target.** A dashed parchment-600 box, blood-700 on
  hover and while a file is dragged over it, solid once a file is chosen, with the file's name in
  it. The real `<input type="file">` covers the box invisibly, so clicking and dropping are the
  browser's own and work without JavaScript.
- **Tabs** are radio buttons and CSS: the chosen tab is a parchment-100 face joined to its
  panel.

---

# THE MAILS (PHASE 9 A2)

Every account mail is a pair under `templates/account/email/`: plain text, and HTML on one frame
(`base_message.html`). A mail client is not a browser, so the frame keeps to what survives one:

- **Inline styles and tables only** - clients drop `<style>` and external CSS. The tokens are
  written out as hex: parchment-50 card on ink-100, an ink-950 band with "Goldfish Lab" in
  parchment-100 as the wordmark (text, never an image - clients block remote images), ink-800
  body text, blood-700 button with white text.
- **One button per mail, and its address written out under it** for clients that strip
  buttons. Codes stand alone in a large monospace box.
- **Light only.** `color-scheme: light only` for the clients that honour it; the rest may
  invert, and dark text on a light card stays legible when they do.
- **Words:** what happened and what to do, in one or two sentences, and what to do if it was not
  you. No "user x@y", no bracketed domain in the subject. `manage.py preview_mails` writes them
  all to files for a look.

---

# PICTURES, MARKERS AND CHARTS (PHASE 9 C2-H)

The rule behind all of them: **a number that can be a picture is a picture, and no picture says
anything by colour alone** - a word or a count always stands beside it. All server-rendered;
where something toggles, it is a real `<input>` and CSS (`:checked`, `:has()`), never a script.

- **The status marker** (`decks/_status.html`, `.deck-status-open` / `-ready`): blood-700 "! 3
  cards need you" (a link into the card review) or green "✓ Ready". On the deck list, the deck
  page and the card page.
- **The card grid** (`decks/_card_grid.html`, `.card-grid*`): card pictures, three across on a
  phone; the same marker words on a card ("! needs you", "✓ answered"), "×2" for a quantity.
  Filter chips reuse the chart chips. Every picture has its card name as `alt` and loads lazily.
- **Bars** (`decks/_bar.html`, `.deck-bar-*`, the usage bars on the plans page): an ink-800 fill
  in a parchment frame with the count as text beside it; blood-700 only when a limit is full.
  A common template shows as a faint band behind the bar, labelled "a common template, not a
  rule". A progress-type bar carries `role="progressbar"` with its values.
- **Line charts** (`simulations/_line_chart.html`, `.seen-*`): inline SVG, eight line colours, the
  last four dashed so a colour-blind reader can still tell them apart; checkbox chips hide a
  line. The numbers sit under a "The numbers" fold. Since phase 10 a line explains itself:
  pointing at it or its chip (or focusing or tapping it) fades the others to 0.2 and puts its
  sentence in the info line under the chart; a count line shows a neutral ink band of ±1
  spread; a share line's sentence names its typical turn (the dot with a dashed guide went
  in phase 11 - it said the same thing as the sentence).
- **The playtest board** (`playtest/_board.html`, `.playtest-*`, `.hand-*`, `.pip-*`): the hand
  as a fan of card buttons that straighten and grow under the pointer or keyboard focus (a
  straight sideways row up to 48rem); mana as round pips in the colour pie with the letter
  inside; `prefers-reduced-motion` drops the transitions.
- **"Why?"** (`core/_why.html`): the one small link beside a section that keeps a sentence and
  sends its explanation to a methodology anchor. `dark=True` on dark grounds.

---

# THE RUN PAGE (PHASE 10)

The order follows what a person can do first: keep the deck (a guest), answer the cards the
engine could not read, then the pictures, and the depth folded at the bottom.

- **"Keep this deck"** (`simulations/_keep_deck.html`, `.keep-card*`), for guests only: a Magic
  card made of tokens - an ink-950 border, a parchment frame, a name bar and a type line on
  parchment-100, the commander's art (Scryfall's art crop) or the flask, a parchment-50 text box
  with italic ink-700 flavour, and a blood-700 button. At most 18rem wide.
- **"Cards that need your attention"** (`simulations/_attention.html`): the status marker in its
  three states ("! 3 cards need you", "✓ All answered" with "Run the deck again", "✓ Nothing needs
  you"), and under it the coverage line with "See what it could not read".
- **While a run plays** (`simulations/_progress.html`, `_waiting.html`,
  `static/js/run-progress.js`): the moving logo beside the bar, one italic ink-700 loading line
  under it that fades (600 ms) to another every 3-5 s, and a bar that glides (900 ms) on a
  curve that slows towards 95% (under 10% while the run waits in the queue) and never shows less
  than the real share or 100 before the end. Beside the heading "Playing your deck" the line
  "2,000 games" ("2,000 games, waiting for a free table" while queued); the exact counts once it
  is over. Under `prefers-reduced-motion`: a still logo, one line, no glide.
- **"Deck summary"** (`simulations/_summary.html`, `.summary-*`), the last block before
  "Advanced". Each part's title has its colour: parchment-700 for what the deck is (feel,
  mechanisms), verdigris-700 for strengths, blood-700 for weaknesses, ink-700 for the closing
  tactics - and each point under strengths and weaknesses starts with **+** or **−**, so the
  colour is never the only sign. The mechanisms are chips that are not switches: a parchment-50
  frame with rounded corners (a long one wraps on a phone), ink-900 words "Removal · 9 cards ·
  drawn by turn 4 in 78% of games", and the swatch of the same category's line in "What you
  drew" in front. A mechanism the run does not count has no swatch. The written parts (phase 10
  H) are plain ink-900 text under their titles; a strength or weakness has its sign hanging in
  front in its colour (`.summary-point`, `.summary-sign-*`), so a wrapped line lines up with the
  words. While Mistral writes, one italic ink-700 line "Writing your deck summary…". Under the
  text, small ink-700: "Written by Mistral AI. It can be wrong. The numbers above are measured.",
  then the "Write a summary" offer when there is one, then "Hide summaries".
- **"Advanced"** (`simulations/_advanced.html`, `.report-advanced*`): a closed `<details>` on the
  dark page; the summary is a display heading with the fold's contents named beside it and a
  turning ▸ (no turn under `prefers-reduced-motion`). Inside: the mana table, the blind spots,
  the engine version and seed.
- **"The numbers"** under a chart: one sentence says what the rows are, each group has its unit
  as a row heading, a count reads "3.2 ± 1.1", and the table scrolls sideways inside its fold on
  a phone - the page never does.

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
