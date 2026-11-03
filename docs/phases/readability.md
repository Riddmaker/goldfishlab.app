# Readability (Themen-Doc, Paket 3)

Started 2026-11-03. The four points of the Themen-Doc that make the pages easier to read:
rows 39 (one sentence why instead of a "Why?" to follow), 32 ("In short"), 35 (the stress
test) and 31 (Share and Compare). Plan approved by the owner on 2026-11-03, every decision A.

| PR | Doc row | What | Status |
|---|---|---|---|
| 10 | 39 | "Why?" becomes "More"; each section's sentence says first why it matters | **Done** |
| 11 | 32 | "In short" compares the run with the precons; the "±" sentence moves to Advanced | **Done** |
| 12 | 35 | The stress test as two cards side by side, one number each, the table folded | Planned |
| 13 | 31 | Share invites ("Show your friends"); Compare becomes "Better than yesterday", this run against the deck's last one | Planned |

Decisions (all A): the link says "More" ("Mehr dazu"); "In short" is compared with the precons,
and leaves when fewer than five precons have a finished run; the Compare fake door is replaced,
its earlier clicks stay counted; comparing with a friend comes later, private through a share
link, with no public ranking; order 10, 11, 12, 13.

## How PR 10 works

- `core/_why.html` keeps its name and its anchors, and now says `More` in the context
  `explanation link`, so the plain "More" elsewhere keeps its own translation. All 22 uses changed
  with it; `tests/test_text_pass.py` still checks that every anchor is on the methodology page.
- The sentences before the link that only said what was measured now say why it matters first:
  What you drew, Milestones, Stress test, What the mana was made of, Mana and lands (Advanced),
  the measured Engines and Combos. Beside "The engine read N of M cards": "The more it reads,
  the more the numbers tell." (the run page and the shared report).
- The sentences that already said why (the blind spots, the deck page's engines, the brackets)
  stayed.
- `tests/test_readability.py`: no template asks "Why?"; the run page and a shared report link
  only with "More"; German says "Mehr dazu".

## How PR 11 works

- `simulations.numbers.precons()` reads every published precon's newest finished, shared run
  (what `datapages.services.report_of` shows) through the related names `deck__precon` and
  `share`, so `simulations` still does not import `datapages`; kept 10 minutes, like a report.
- `numbers.standings(numbers, deck_id)` sets on-curve, kept-seven and mana on turn 4 against
  them: how many precons each is strictly ahead of, their median, and each one's place on a
  strip. Fewer than five precons (`PRECONS_AT_LEAST`) gives nothing; `deck_id` leaves a
  precon's own page out. A run shorter than four turns compares only what it has.
- `report.build` gives `standings` and `exact` (the old sentences). `#glance` keeps its id: the
  names are the precon table's columns, the strip is `.peer-*` in `input.css`, the words beside
  it say the same. A run on the play says the precons were on the draw. A link goes to the
  precon table; "More" to `#precons-compared` on the methodology page.
- The "±" sentences are in Advanced under "How exact the numbers are" (`#exact`).
- Tests: `tests/test_readability.py` (PR 11 part), `tests/test_report_numbers.py` updated.
