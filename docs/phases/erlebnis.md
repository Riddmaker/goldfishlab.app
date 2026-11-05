# Erlebnis (Themen-Doc, Paket 4)

Started 2026-11-03. The points of the Themen-Doc that make the site feel alive: rows 1, 3, 41,
42 and 43 - our own symbols, the ticker with a fun line now and then, the home page and the
playtest board. Plan approved by the owner on 2026-11-03; the drafts (PR 14) on the Erlebnis
canvas, round 2 approved on 2026-11-04.

| PR | What | Status |
|---|---|---|
| 14 | Drafts on a design canvas: symbols, fun lines, home A/B, board A/B; two research notes | **Done** |
| 15 | Sixteen symbols of our own, in colour, each moving once on hover | **Done** |
| 16 | The ticker: the last 40 events in a loop, "What others did recently", fun lines with their ten icons and the talking overlord | **Done** |
| 17 | The home page (B): classic header kept still, the big flask with the fish's hello jump and click moves, the ticker as a band | **Done** |
| 18 | Free draw: a Free/Strict switch at the start (`PlaytestSession.mode`), the engine unchanged | Planned |
| 19 | The board (A) with the journal of B | Planned |

Decisions: our own single-line SVG symbols, no emoji and no Wizards glyphs (1A); a fun line at
most every eighth line, pop culture in English, our own lines translated, set apart by colour,
symbol and ground and never read as an event (2A); the ticker loops the 40 newest events (3A);
Free draw is a switch at the start (4A). Home B and board A with B's journal were chosen on the
canvas. The fish's click moves leave the header for the big flask on the home page (PR 17).

## How PR 15 works

- The drawings are `core/icons/<name>.svg`, shapes only. `{% icon "speed" %}` (or
  `size="lg"`) from `core/templatetags/icons.py` wraps one in an inline `<svg class="icon">`,
  hidden from screen readers. Inline, not `<img>` or `<use>`: CSS has to reach the parts to
  colour and move them. An unknown name renders nothing and logs a warning.
- Colour by class, never a hex in a drawing: `i-gold`, `i-water`, `i-blood`, `i-green`, `i-ink`
  fill, `i-line-*` stroke, through `--i-*` variables that step darker inside
  `.surface-parchment` (or `.icon-on-light`). `water` is a new, documented colour family of two
  steps, for symbol fills only.
- Motion: the block between `icons:start` and `icons:end` in `assets/css/input.css`. A symbol
  moves while its `.icon-host` (button, tile, heading, bar row) is hovered or has keyboard focus,
  inside `prefers-reduced-motion: no-preference` only. The styleguide builder copies that block
  and reads the same drawings, so STYLEGUIDE.html shows exactly what the site does.
- Where: Simulate and Draw a hand (deck page), the Bracket and legality tiles, ramp and draw
  beside the curve, ramp, draw and removal in "What is in it" (`Bar.icon`), Combos & engines
  (deck page and report), Engines, Stress test, Speed, the import heading. New user, The fish
  says, New hand and Unusual move are drawn now and used in PR 16 and PR 18.
- Info symbols and functional icons stay apart (DESIGN.md, SYMBOLS): the pencil, the bin and the
  padlock keep their own one-colour look.
- `tests/test_icons.py`: every drawing renders, every name a template uses is drawn, the
  styleguide lists them all, every class has a rule, no drawing carries a hex, every move waits
  for no motion preference. The headless check measures each drawing's extent (one size of ink).

## How PR 16 works

- `core.ticker.lines_for` now returns every cached event that is not the viewer's own (up to
  `KEEP` = 40), with `with_fun` putting a fun line after every seven: at most every eighth line,
  none on a quiet site, each one different, picked anew per answer. `FUN_LINES` holds the ten;
  pop culture (`pop=True`) stays English and carries `lang="en"` in another language, ours go
  through gettext. Each kind of event has its symbol (`ICONS`); each fun line its drawing
  `core/icons/fun-<name>.svg`, in the styleguide with the others.
- The page: a band of its own ("What others did recently") above the two pictures. The
  stylesheet shows the first eight lines, the lower ones a step dimmer; a fun line has its own
  ground, gold rules and italic gold, and "The fish says:" for screen readers.
- `static/js/ticker.js`: every 5 s the last line moves to the top (`ticker-fresh` fade,
  `icon-now` plays the symbol's move). The 30 s poll is merged (`merge`, pure, tested under
  Node in tests/test_ticker.py): new events to the top, gone ones out, fun lines kept unless one
  would follow fewer than seven events. Pause stops loop, poll and overlord; pointer or focus on
  the band holds loop and poll. Reduced motion or no script: the newest eight, no loop.
- The overlord's jaw, eye and antenna run always (inside the no-preference block); the other
  fun drawings play twice on arrival or hover.
- The symbol CSS hosts now include `.icon-now` beside hover and focus; new fills `i-night`,
  `i-steel`, `i-ash`, `i-bone`, `i-pip` (ink and parchment tokens) and `i-fine` (1.8 line).

## How PR 17 works

- The header mark is the still `favicon.svg` on every page; `base.html` no longer has a home
  page branch (only `aria-current="page"` on the home link there).
- `core/home.html` is Home B: `.home-hero` (centred, the glow) holds `<span class="home-flask"
  data-fish data-label="...">` with `core/_logo.html` (`logo-home`, 8.5rem / 6rem), the
  heading, the tank line, the importer and one line for a stranger. `guests/_steps.html` is
  left to the trial page. Below the ticker band two `.home-tile` sections on parchment
  (`{% icon "simulation"|"hand" size="lg" %}`, a sentence, the WebP), not links; the link row
  gained Resources.
- `static/js/fish.js` turns the span into a `<button type="button">` with the translated name,
  moving the svg into it, then runs the same moves as before on it: hello after 2 s, idle
  moves, `pointerenter` of a mouse jumps, a click (also Enter and Space) tilts, `poke` - pure,
  tested under Node like `nextWait` and `nextMove` - tells when three pokes within 1.5 s make a
  jump. The bubbles rise during `is-jump` and `is-tilt` only.
- Tests: `tests/test_home.py` (order, tiles, stranger, member, guest, German),
  `tests/test_fish.py` (where it lives, the poke rule), `tests/test_logo.py` (the bubbles under
  reduced motion). Five new strings in six languages.
