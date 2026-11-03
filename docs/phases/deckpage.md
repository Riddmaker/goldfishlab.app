# The deck page anew (Themen-Doc, Paket 2 «Deckseite neu»)

Concept: the Themen-Doc, sections "Deckseite: weniger Fly-outs" and "Deckübersicht: welche
Stats pro Deck" (https://claude.ai/artifact/N5jbtPHJLqNNPxAnTyZ8X4), rows 28, 15, 21 and 13
of its progress table.

## Decisions (2026-11-03)

- The AI leaves the simulation page entirely and lives on the deck page. No run values go
  into its prompt for now: the summary already works from the list, and run values would
  mean a new prompt version and more tokens. That is a PR of its own, later.
- Beside the mana curve: ramp cards, card draw cards, and the turn the commander is out in
  half the games (last run).
- The deck list shows three values per deck: colour identity, bracket (as the tile), date of
  the last simulation.

## Plan

| PR | What | State |
|----|------|-------|
| 7 | Deck summary and "Your strategies" on the deck page, new order (rows 28, 15) | done |
| 8 | Smaller mana curve with three values beside it (row 21) | open |
| 9 | Three values per deck in the deck list (row 13) | open |

## How PR 7 works

- **Deck page** (`decks/detail.html`): head, tiles, then `simulations/_summary.html` (only for
  somebody with summaries on; `summary.state`) and `simulations/_strategies.html`
  (`strategies.block` from the readings the page already has), then the curve and "What is in
  it", "Combos & engines", the cards, "What the engine cannot model", "Earlier runs and games".
- **Empty:** a deck with no run and no summary shows "Run a simulation to fill this." (a link
  to `#simulate`) and offers nothing to buy. With a run but no current summary, the old
  "Write a summary" offer and its messages (failed, paused, short) stay.
- **Endpoints keyed by the deck** (`simulations/urls.py`, under `/decks/<id>/summary/`): the
  poll (`DeckSummaryView`), "Write a summary" (`SummaryWriteView`, 10/min) and "Was this
  helpful?" (`SummaryFeedbackView`, 20/min). All go through `DeckScopedView.get_deck`, owner
  only, so another user's deck is a 404; the POSTs keep CSRF. Same names as before, so the
  templates only changed `run.id` to `deck.id`.
- **Run page:** no summary and no strategies. `simulations/_measured.html` ("Combos &
  engines", `#measured`) keeps the run's measured combos and engines and links to the deck
  page's `#summary`. The progress bar no longer waits for Mistral (`summary.writing`,
  `WRITING_SECONDS`, `HOLD_AT_MOST` and the `holding` state are gone): the report shows as
  soon as the games are done. The summary is still started with a run and charged as before.
- **Shared reports** keep their own copy of the summary text (`_summary_text.html`); unchanged.
- **Privacy page:** Mistral "writes the deck summary on the deck page" (was "on a simulation
  report"). What is sent did not change.
- **Fly-outs:** after rounds 2 and 3 four are left, each for a reason: "Options" (a form), the
  theme chips (detail on demand), "What the engine cannot model" (the same for every deck) and
  "Earlier runs and games" (history). Row 15 was mostly the order.
