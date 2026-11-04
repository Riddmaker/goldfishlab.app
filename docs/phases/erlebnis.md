# Erlebnis (Themen-Doc, Paket 4)

Started 2026-11-03. The points of the Themen-Doc that make the site feel alive: rows 1, 3, 41,
42 and 43 - our own symbols, the ticker with a fun line now and then, the home page and the
playtest board. Plan approved by the owner on 2026-11-03; the drafts (PR 14) on the Erlebnis
canvas, round 2 approved on 2026-11-04.

| PR | What | Status |
|---|---|---|
| 14 | Drafts on a design canvas: symbols, fun lines, home A/B, board A/B; two research notes | **Done** |
| 15 | Sixteen symbols of our own, in colour, each moving once on hover | **Done** |
| 16 | The ticker: the last 40 events in a loop, "What others did recently", fun lines with their ten icons and the talking overlord | Planned |
| 17 | The home page (B): classic header kept still, the big flask with the fish's hello jump and click moves, the ticker as a band | Planned |
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
