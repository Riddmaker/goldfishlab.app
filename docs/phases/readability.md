# Readability (Themen-Doc, Paket 3)

Started 2026-11-03. The four points of the Themen-Doc that make the pages easier to read:
rows 39 (one sentence why instead of a "Why?" to follow), 32 ("In short"), 35 (the stress
test) and 31 (Share and Compare). Plan approved by the owner on 2026-11-03, every decision A.

| PR | Doc row | What | Status |
|---|---|---|---|
| 10 | 39 | "Why?" becomes "More"; each section's sentence says first why it matters | **Done** |
| 11 | 32 | "In short" compares the run with the precons; the "±" sentence moves to Advanced | **Done** |
| 12 | 35 | The stress test as two cards side by side, one number each, the table folded | **Done** |
| 13 | 31 | Share invites ("Show your friends"); Compare becomes "Compared with your last run", this run against the deck's last one | **Done** |

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

## How PR 12 works

- `report.stress` gives each scenario a `figure`, the words under it (`caption`) and one
  `sentence` more (`_stress_said`), plus `measured` when any scenario has rows. The commander's
  card shows the share of games it was back by the last turn, the next turn in the sentence
  (the next turn alone is rare when the commander costs two more); the wipe's card the mana on
  the last turn, "Without the wipe" in the sentence.
- `#stress` keeps its id: a short sentence why, then two cards (`data-stress-card`, side by
  side from 640 px), the with/without tables folded under "The numbers"
  (`data-stress-numbers`). A blow the run could not play keeps its card, with the reason and no
  number.
- The methodology's stress test says what the cards show.
- Tests: `tests/test_v36_stress.py` (the figures), `tests/test_readability.py` (PR 12 part).

## How PR 13 works

- **Share**: the panel's heading is "Show your friends"; the button that makes the link has
  three stars (`.share-star`, aria-hidden) that twinkle three times after load, none under
  `prefers-reduced-motion`. Once shared, "Share…" (`data-web-share`) opens the device's share
  sheet through `navigator.share`; `static/js/share.js` shows it only where the browser has one.
- **Compare**: `simulations/compare.with_last(run, numbers)` finds the deck's newest finished run
  that finished before this one (`last_before`) and sets the three numbers of the precon table
  side by side (`numbers.changes`). A difference counts as better or worse only beyond
  1.96 * sqrt(se1² + se2²): the share's margin for the two shares, the mean's standard error
  (`Numbers.mana_spread`, the turn-4 mana histogram's spread) for the mana, and only when the
  shown numbers differ (the browser check found "100% → 100% worse"). `list_print` says
  whether the deck changed in between; a different draw rule is said too.
- A deck's first run invites to change a card and run again (`data-compare-first`); a guest
  sees the panel only with a run before.
- **The fake door is closed**: `metrics.views.CompareView`, `metrics/urls.py` and
  `doors.ask/asked/COMPARE_PRICE_CHF` are gone. Reports viewed keep counting, the clicks so far
  stay in the stats page's gate G3.
- The methodology explains it under `#last-run`, the panel's "More".
- Tests: `tests/test_readability.py` (PR 13 part), `tests/test_metrics.py` (the door is gone).
