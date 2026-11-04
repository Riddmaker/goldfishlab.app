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

Five families, each strictly bound to a meaning. The stock Tailwind palette is disabled
(`--color-*: initial`) so no colour can enter by accident.

| Family | Anchor | Means | Never used for |
|---|---|---|---|
| **ink** | `#12100d` @950 | The page ground, and all body text on parchment. Warm near-black, never pure black. | Accent or state |
| **parchment** | `#f5ecd7` @100 | Surfaces — cards, report panels, the playtest table. Also light text on the dark ground. | Success or danger |
| **blood** | `#c43d33` @500 | Primary action, danger, destructive confirmation, "needs review". | Body text, large fills |
| **verdigris** | — | Success, and "successfully modelled" states. Aged copper, not a bright UI green. | Decoration |
| **water** | `#9cc4dc` @300 | The fill of a symbol: water in the flask, the wave, a card. Two steps only (Paket 4, PR 15). | Text, surfaces, state |

Rules that follow from this:

- **`blood` is an accent, never a surface.** A full-width red banner reads as an error page.
- **Success and danger must never be distinguishable by hue alone** — see accessibility above.
- **Symbols may use gold, water, blood and verdigris as fills** (see SYMBOLS). There a colour is
  decoration that brings the page to life, not a state; the word beside the symbol carries the
  meaning.
- **No colour outside these five.** A new meaning gets a new *step* in an existing family, or a
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
- **The changelog mail (C7)** wears the same frame (`templates/changelog/email/`) but has no
  button: a list of entries, each title linking its anchor on /changelog/, and at the foot, in
  small type, why it came and "Unsubscribe with one click". The subject names the month.

---

# PICTURES, MARKERS AND CHARTS (PHASE 9 C2-H)

The rule behind all of them: **a number that can be a picture is a picture, and no picture says
anything by colour alone** - a word or a count always stands beside it. All server-rendered;
where something toggles, it is a real `<input>` and CSS (`:checked`, `:has()`), never a script.

- **The status marker** (`decks/_status.html`, `.deck-status-open` / `-ready`): blood-700 "! 3
  cards need you" (a link into the card review) or green "✓ Ready". On the deck list, the deck
  page and the card page.
- **The deck fan** (`decks/_fan.html`, `.deck-fan*`): on "Your decks", left of the name, the
  commander small with the deck's three most expensive spells fanned out to the right behind it,
  each turned a little further about its bottom edge; a hover spreads them a little more (not
  under `prefers-reduced-motion`). No commander: the slot stays, dashed and empty, the fan
  behind it all the same. Scryfall's small pictures, lazily; hidden from screen readers, since
  the name beside it is the link and says the same.
- **Theme chips** (`decks/_themes.html`, `.deck-theme*`, Themen-Doc Paket 1): "Built around"
  at the top of "What is in it". A theme is a chip that opens, a `<details>` with no script. The
  level is always the word on the chip; the border only repeats it - thick and parchment-200
  for strong, solid for medium, dashed for hinted. Open, it takes the whole row: the sentence,
  each piece as "8, wants 6" with its cards, and the number folded under "Advanced". No theme
  is a sentence, never a guess.
- **Engines and synergies** (`decks/_engines.html`, `decks/_pair.html`, Themen-Doc Paket 1,
  PR 3): under the combos in one section, "Combos & engines". An engine is a row with a
  parchment-600 rule at its left, as a combo is: its cards joined by "+", its theme and level
  as words, and one sentence of what each card does. An engine a piece short gets a dashed
  ink-700 rule and says "2 of 3 pieces. Still missing:" with the piece in bold; the dash only
  repeats the words. Pairs show the five strongest open, the next ones in one fold, each with
  its three numbers under "Advanced". No engine is a sentence, never a guess.
- **Lock-in** (`decks/_mark.html`, `decks/_gone.html`, PR 4): only the owner's word is stored,
  by the cards. A suggestion has two small square buttons, a padlock and a cross, named in full
  for screen readers ("Lock this engine: it stays, whatever the list becomes"). A locked entry
  says "Locked" beside the padlock, comes first and stays even when the rules stop finding it.
  A dismissed one moves to a fold at the end ("1 dismissed") with "Bring back". An entry whose
  card left the deck turns ink-700 and says "Resting, a card is missing: …". The dashed rule
  keeps one meaning, a piece short; locking never changes the rule.
- **Engine timing** (`decks/_engines.html`, `simulations/_engines.html`, PR 5): under each
  complete engine, one sentence from the last finished run, "Running by turn 6 in **41%** of
  games, in your last run.", the share in bold as everywhere a run gives a number. A complete
  engine the run did not measure says the next simulation will, in ink-700; an engine a piece
  short or resting says nothing, since it cannot run. The report lists the same as one line per
  engine after the combos. "Running" is a plan, not the named cards: any card of each role out.
- **Engine explanation** (`decks/_engines.html`, PR 6): one or two sentences from the deck
  summary on how the engine plays, under the rules' sentence, in the summary's language
  (`lang`), followed by "Written by Mistral AI. It can be wrong." in ink-700, the way every AI
  text on the site owns up to it. Only for the engine with the same theme and cards it was
  written about, and only with summaries switched on. The rules find the engine; AI only
  explains it.
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
- **"More"** (`core/_why.html`, Paket 3 PR 10; "Why?" until then): the one small link beside a
  section, sending its explanation to a methodology anchor. `dark=True` on dark grounds. The
  sentence before it says **why the section matters** first, then what it measures: "How soon
  your deck gets going: the share of games ...". The page has to be understood without the
  link - reading on is an offer, never a must. A question ("Why?") made readers feel they had
  to answer it before going on; "More" (DE "Mehr dazu") does not.
- **"In short" next to the precons** (`simulations/_report.html` `#glance`, `.peer-*`, Paket 3
  PR 11): a bare "81% of games" says little; where the deck stands among the precons, each
  played the same way, says a lot. Each of the three numbers is a row: its name, the value,
  a strip with every precon as a faint ink tick (where many stand it darkens) and the deck as
  one tall verdigris mark, then the words - "Ahead of 34 of 40 precons. The precons' middle:
  76%." The strip is hidden from screen readers, the sentence says it. Fewer than five
  precons and the block is gone; a precon's own page leaves the precon out. The "±"
  sentences moved to Advanced ("How exact the numbers are").
- **The stress test as two cards** (`simulations/_report.html` `#stress`, Paket 3 PR 12): a
  table of with/without rows made the reader do the comparing. Each blow is a card, side by
  side from 640 px: its title, one big number - how fast the deck comes back (the share of
  games the commander was back by the last turn; the mana on the last turn after the wipe) - the
  words under it, one sentence more and the assumption small at the bottom. The rows are folded
  under "The numbers". A blow the run could not play says why, without a number.
- **Compared with your last run** (`simulations/_compare.html` `#compare`, Paket 3 PR 13): in
  place of the "Compare two versions" fake door. The precon table's three numbers of the deck's
  run before, then this run's ("80% → 85%"), each with one word: **better** (verdigris),
  **worse** (blood), or "about the same" (ink) when the difference is within what chance makes
  it at 95% - the word carries the meaning, never the colour alone. One line says whether the
  deck changed in between (runs keep a fingerprint, not the list). A deck's first run invites to
  change a card and run again; a guest gets no invitation.
- **Show your friends** (`simulations/_share.html`): the share panel invites, then says what
  becomes public before anything does. Three small stars twinkle at the button's corners three
  times after load and rest (aria-hidden, none under `prefers-reduced-motion`). "Share…" opens
  the device's share sheet and only appears where the browser has one (Web Share API).
- **Resources** (`core/resources.html`, data in `core/resources.py`): a short list, not a link
  farm. Each entry is its name as a display-font link with "↗", then one sentence of ours on why
  it is there; a video adds channel, year and length. Titles stay in English (`lang="en"`), the
  sentences are translated. Every link opens in a new tab with `rel="noopener noreferrer"`;
  nothing is embedded, so the page loads nothing from YouTube.

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
- **"Deck summary"** (`simulations/_summary.html`, `.summary-*`) and **"Your strategies"**
  (`simulations/_strategies.html`) are the deck's, not one run's: they stand on the deck page
  right under the four tiles (Paket 2, PR 7), before the numbers. A deck with no run yet shows
  the block anyway, with one ink-700 line "Run a simulation to fill this." (a link to the
  Simulate button) - the empty block advertises what fills it and sells nothing else. The
  report keeps what its run measured, "Combos & engines" (`simulations/_measured.html`), whose
  first line links to the summary on the deck page; and it does not wait for Mistral: the
  report shows as soon as the games are done. Each part's title has its colour: parchment-700 for what the deck is (feel,
  mechanisms), verdigris-700 for strengths, blood-700 for weaknesses, ink-700 for the closing
  tactics - and each point under strengths and weaknesses starts with **+** or **−**, so the
  colour is never the only sign. The mechanisms are chips that are not switches: a parchment-50
  frame with rounded corners (a long one wraps on a phone), ink-900 words "Removal · 9 cards ·
  drawn by turn 4 in 78% of games", and the swatch of the same category's line in "What you
  drew" in front. A mechanism the run does not count has no swatch. The written parts (phase 10
  H) are plain ink-900 text under their titles; a strength or weakness has its sign hanging in
  front in its colour (`.summary-point`, `.summary-sign-*`), so a wrapped line lines up with the
  words. While Mistral writes, one italic ink-700 line "Writing your deck summary…". Under the
  text, small ink-700: "Written by Mistral AI. It can be wrong. Its numbers are calculated from
  your list, not measured in a run.",
  then the "Write a summary" offer when there is one. "Were the summary and the picks
  helpful?" stands once, at the end of "Your strategies" (one Mistral call writes both). There
  is no "Hide summaries" on a report: the switch is an account setting, under Settings on
  "Your plan" (`.switch`: a real button with `role="switch"`, dark track when on, the knob
  slides without motion under `prefers-reduced-motion`), with one sentence on why it exists.
- **Deck page order** (`decks/detail.html`, Paket 2): the head (commander, buttons, bin), the
  four tiles, the deck summary and "Your strategies", the mana curve beside "What is in it",
  "Combos & engines", the cards, then folded at the bottom "What the engine cannot model" and
  "Earlier runs and games". Words before numbers, the deck a person already knows (its cards)
  last. A fold stays only where it hides something long that every deck says alike or a form.
- **Mana curve** (`decks/detail.html`, `.deck-curve*`, Paket 2 PR 8): eight small ink columns
  on one scale, the count above and the mana value below, each column a sentence for a screen
  reader. Beside it three numbers: ramp and card draw as "What is in it" counts them (with the
  template), and "Commander out by turn N" in half the games of the last run (link to the
  report's milestones), or "simulate to see". The average mana value stays a tile.
- **Deck list values** (`decks/list.html`, `data-deck-values`, Paket 2 PR 9): under each deck's
  name one quiet row of three: the colour identity as `.pip`s in WUBRG order (colourless: C), each
  named for a screen reader; "Bracket N+" exactly as the deck page's tile says it (one include,
  `decks/_bracket_number.html`); "simulated <date>" linking to the last finished run, or "not
  simulated yet". The row never costs a query per deck.
- **"Advanced"** (`simulations/_advanced.html`, `.report-advanced*`): a closed `<details>` on the
  dark page; the summary is a display heading with the fold's contents named beside it and a
  turning ▸ (no turn under `prefers-reduced-motion`). Inside: the mana table, the blind spots,
  the engine version and seed.
- **"The numbers"** under a chart: one sentence says what the rows are, each group has its unit
  as a row heading, a count reads "3.2 ± 1.1", and the table scrolls sideways inside its fold on
  a phone - the page never does.

---

# SYMBOLS (PAKET 4, PR 15)

The page stays quiet; the symbols bring the life. Sixteen of our own drawings, and ten for the
ticker's fun lines (`core/icons/*.svg`,
set with `{% icon "speed" %}` from `core/templatetags/icons.py`), designed on the
Erlebnis canvas and approved there.

- **Two kinds, never confused.** *Info symbols* say what a thing is: in colour, always beside
  their word, never a target of their own, no pointer cursor. *Functional icons* say what a
  click does: one colour (the text's), drawn as today, sitting in something that clearly is a
  button or link. The pencil (rename), the bin (delete) and the padlock (lock-in) are
  functional and keep their own look. An info symbol inside a button (Simulate, Draw a hand)
  is part of that button's label; the button is what is clickable.
- **One line, one or two fills.** The line in the colour of the text (`currentColor`), round
  ends; the fill from gold (`parchment-300`), water (`water-300`), blood (`blood-400`) or
  verdigris (`verdigris-300`). On parchment the fills step darker (`parchment-500`,
  `water-500`, `blood-500`, `verdigris-400`) so they hold on the light ground.
- **One size of ink.** Every drawing sits in about 22 of the 32 units of its box, so none
  shouts (the old hourglass, the flask) and none whispers (the hand, the rings, the seal).
- **Two sizes.** Beside text the symbol is 1.25em with a 2.2 line; large (`size="lg"`, 2.5rem,
  1.8 line) only where a card or a tile leads with it.
- **Each moves once.** On hover or keyboard focus of its host (`.icon-host`: the button, tile or
  heading row it belongs to) each symbol plays one short move, under a second: the sand turns,
  the fan opens, the seal stamps, the sprout grows. Nothing moves under reduced motion; on a
  phone there is no hover, so nothing moves there either - by design.
- **Sparingly.** A symbol only where its word already stands: the Simulate and Draw a hand
  buttons, the Bracket and legality tiles, ramp, draw and removal in "What is in it", the
  headings Combos & engines, Engines, Stress test and Speed, the import heading. Never in body
  text, never in a table.
- **Hidden from screen readers** (`aria-hidden`); the word says it.

## The ticker (PR 16)

- **A band of its own** on the home page, "What others did recently": ink-900 ground, a
  parchment-800 rule above and below. Eight lines at a time from a loop of the last forty
  events; every five seconds a line comes in at the top and one goes at the bottom. The lower
  lines step back in colour (parchment-100, -200, ink-300, ink-400; never under 5:1).
- **Each line its symbol**: simulation, hand, import, new user. A line that arrives fades in and
  its symbol moves once (`.icon-now`, the same move as on hover).
- **Fun lines, never an event.** At most every eighth line - after seven events, so a quiet site
  has none. Own ground (ink-800), parchment-600 rules, italic parchment-300, its own drawing
  (`fun-*`), and "The fish says:" for screen readers. Pop culture stays English (`lang="en"`
  in another language); our own lines are translated. The overlord talks as long as he is there.
- **It holds still when asked.** Pause stops the loop, the poll and the overlord (WCAG 2.2.2); a
  pointer on the band or the focus in it holds the loop. Under reduced motion nothing loops or
  moves: the newest eight, replaced every 30 s. Without script, the same.

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
