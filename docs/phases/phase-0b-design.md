# Phase 0b — Design system documentation

**Duration:** ~2 days
**Ships:** `DESIGN.md` + `STYLEGUIDE.html` at the repository root.

**Why this comes before feature UI:** Phase 0 produced working *tokens*, but no written design
language. Without one, every later phase re-decides typography, spacing and button hierarchy from
scratch, and the app drifts into looking assembled rather than designed. Locking it once, in
writing, is cheaper than negotiating it seven more times.

---

## The convention (from `wiemeinsch.ch`)

Two artifacts with distinct jobs, plus one rule:

| File | Role |
|---|---|
| `DESIGN.md` | **Normative prose.** The rules, and the reasoning behind them. Short. |
| `STYLEGUIDE.html` | **Binding rendered reference.** Every rule with a live example beside it. |
| `/styleguide/` (Django) | Live token dump, read straight from `input.css`. A dev tool, not the spec. |

**PDF is generated on demand and never committed** — browser print, or
`msedge --print-to-pdf`. Print CSS lives inside `STYLEGUIDE.html` so the output is usable.

**Sync rule (HABIT 5):** a design decision changes `DESIGN.md` **first**, then `STYLEGUIDE.html`
is brought in line, then the tokens in `assets/css/input.css`. Never the other way round.

---

## Contents of `DESIGN.md`

1. **Grounding** — what the product should feel like, and what it must not feel like.
   Old-school dark fantasy: a pre-2003 Magic card's tan scroll textbox on near-black leather.
   The failure mode to avoid is "dashboard rainbow".
2. **Accessibility as a hard floor** — WCAG 2.1 AA minimum, colour never the sole carrier of
   meaning (a "not modelled" badge needs a label, not just a hue), visible focus rings, full
   keyboard operability. Already enforced by `tests/test_design_tokens.py`.
3. **Mobile-first** — every layout designed at phone width first. The playtest card table is the
   hard case and gets an explicit rule.
4. **Typography** — the role of each family, and which one carries body text.
5. **Colour semantics** — each family strictly bound to a meaning, and explicitly what it is
   *not* used for.
6. **Density** — this is a data product. Numbers need tabular figures and restrained spacing;
   prose needs measure limits.

## Contents of `STYLEGUIDE.html`

Each section is a normative rule plus a rendered example:

- Wordmark and header
- Full palette with hex values and the AA ratio of every role pairing
- Type specimen: display, body, numeric/tabular, monospace
- Buttons: primary, secondary, success, disabled, focus-visible
- Form fields: default, focused, error, help text
- Panels: parchment surface, raised dark panel, badge row
- **Data patterns** — the ones the product actually needs: a probability table, a per-turn
  milestone row, a coverage/honesty callout
- **Do / Don't list** — the most useful part, and the one that prevents drift

## Also in this phase

**Vendor EB Garamond (OFL) into `static/fonts/`** and put it in front of Georgia. This is the one
visible gap left from Phase 0: the CSS named a self-hosted face that was never added. Self-host
it — never a Google Fonts request, for privacy and so the dev container works offline.

**Tabular figures.** A simulation report is a wall of percentages; proportional digits make
columns ragged. Needs `font-variant-numeric: tabular-nums` on numeric contexts.

---

## Verification

- `tests/test_design_tokens.py` still green; extend it if new role pairings are declared.
- A new test asserting `DESIGN.md` and `STYLEGUIDE.html` both exist and that every colour family
  named in `DESIGN.md` is present in `input.css` — cheap protection for the sync rule.
- `STYLEGUIDE.html` prints to a sane PDF (check page breaks, and that dark backgrounds do not
  waste a whole ink cartridge — print CSS should invert to a light ground).
- Playwright screenshots at 1440px and 390px, plus the printed PDF, reviewed by eye.

## Definition of done

The visual language is written down, rendered, and printable — and a later phase can answer
"what should this button look like?" by reading, not by asking.
