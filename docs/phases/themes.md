# Themes, engines, synergies (Themen-Doc, Paket 1 «Deck verstehen»)

Concept and decisions: the Themen-Doc, section "Paket 1 «Deck verstehen»: Konzept v1"
(https://claude.ai/artifact/N5jbtPHJLqNNPxAnTyZ8X4). Research: `docs/research/synergy/`
(scene.html, terms.html, detection.html).

## Decisions (2026-11-03)

- Version 1 uses rules only, no AI. AI comes later and only explains.
- Strength is shown as three levels (strong / medium / hint) with a sentence. The number
  (fill, 0–1) goes under "Advanced".
- Detection is free for everyone: it is derived from Scryfall data, and Scryfall does not
  allow a paywall. Measuring an engine in a simulation counts as a simulation.
- We do not write to Scryfall or Wizards about the subscription. That was settled earlier.
- No user-made engines in version 1. Users can only lock or dismiss.
- 12–15 themes to start: 13 in the catalogue plus typal.
- No data from EDHREC, Archidekt or Moxfield (their terms forbid it).

## Plan

| PR | What | State |
|----|------|-------|
| 1 | `cards/themes.py` catalogue and `detect`; `decks/themes.py`; `manage.py themes_report`; `tests/test_themes.py` | done |
| 2 | Theme chips in "What is in it"; one "Building blocks" chart instead of strategy and mechanic | done |
| 3 | Synergy pairs and engines; the "Combos & engines" section | done |
| 4 | Lock-in: model, three states, buttons | done |
| 5 | Measure engines in the simulation | done |
| 6 | AI explains each engine, in the deck summary's call | done |

## How PR 1 works

- A **theme** is a set of **pieces**, with the core piece first. A piece matches on:
  - tag slugs, with the tag tree rolled up (`tags`);
  - tag slugs the card wears itself only (`direct`), for example `death-trigger`;
  - our block mechanics (`DerivedProfile.mechanics`);
  - type-line words, for example `Equipment`.
- **Weight:** a card counts once per copy and twice in the command zone. Basic lands count zero.
- **Fill:** the mean of `min(count/need, 1)` over the pieces, with the core piece weighted double.
- **Rules for finding a theme:**
  - The core piece needs at least `CORE_MIN` (4) cards.
  - If any piece is empty, the theme is a hint at most.
  - Level thresholds: strong 0.75, medium 0.5, hint 0.4.
- **Typal:** the most common creature type, with changelings in every type.
  - It needs at least 10 members.
  - Payoffs are cards tagged `typal-<type>` or `typal-choose`, and there must be at least 4.
- **Not a theme yet:**
  - Blink. The tags only know `flicker`, not the cards that want to be flickered.
  - "Big creatures" and stompy decks. Ghalta's deck correctly shows no theme.
- **Queries:** `decks.themes.theme_cards` uses two queries: one for the quantities, one for
  the cards with an `ArrayAgg` of the tags. Nothing is stored.
- **Calibration:** done on the 41 decks of `tests/fixtures/trace_decks.json` with the tags of
  `coverage_cards.json.gz`. The golden test holds five of them:
  - Chainer: theft, aristocrats, reanimator
  - Angels: typal Angel and lifegain
  - Keen Engineering: artifacts
  - Prismari: spellslinger
  - Limit Break: voltron
- **Slug drift:** a test checks every slug against the fixture. `manage.py themes_report`
  warns about slugs missing from a live catalogue.
  - The local development database holds only a 100-card extract, so it always warns.

## How PR 2 works

- **Deck page:** `DeckDetailView` puts `deck_themes(deck)` in the context (two queries, the
  count does not grow with the deck). `templates/decks/_themes.html` draws "Built around" at the
  top of "What is in it":
  - each theme is a `<details>` chip, the level as a word (strong, medium, hinted);
  - opened, it shows the sentence, each piece as "8, wants 6" with its cards, and the fill
    under "Advanced";
  - no theme is a sentence: "No theme we know of."
- **Simulation page:** `report.seen()` returns `blocks` (strategies and mechanics in one chart,
  sorted by the share on the last turn, at most `BLOCK_LINES` = 8 lines, as a chart has eight
  colours), `blocks_rest` (the labels left out, named under the chart) and `block_rows` (every
  one, for "The numbers"). Old runs work: the stored `role:<key>` groups did not change.
- **Words:** the methodology page has a paragraph for each; every string is in the six languages.

## How PR 3 works

- **Rules in the catalogue:** each `Piece` has a clause, `does` ("%(card)s sacrifices"), and
  each `Theme` has `rules`, (first, second, weight) in the order a sentence says them:
  Aristocrats has outlet → payoff (1.0) and fodder → payoff (0.7); Voltron gear → payoff (1.0)
  and protect → payoff (0.5); the others one rule at 1.0. `Theme.flow` is the pieces in that
  order. A test holds every piece to a rule and a clause.
- **Pairs** (`cards.synergy.pairs`): every card of the first piece with every card of the second,
  in each theme found (not typal). Strength = rule weight × rarity of the first card's tag ×
  rarity of the second's × the theme's fill. Rarity is an IDF, `log(N/n) / log(N/20)`, held to
  0.1–1; a card in a piece by type or our mechanics gets 0.5. Each pair of cards once, at its
  strongest; the list goes in rounds, so one card cannot fill the top rows.
- **Engines** (`cards.synergy.engines`): one per theme found, one card per piece, the commander
  first, then a card that repeats, then the rarest tag. A card repeats when one of its
  abilities is `repeatable` or `event` (`DerivedProfile.tempo`) or Tagger tags it
  `repeatable-…`. A complete engine without such a card is left out; one a piece short is kept
  and names what is missing. Its level is its theme's.
- **Page:** "Combos & engines" (`#combos`) holds the combo panel (an h3 now, still swapped alone
  by htmx), then `decks/_engines.html`: engines, then pairs (5 open, up to 30 folded, the rest
  counted). Sentences are joined in the page's language (`pgettext` "joins clauses", "ends a
  sentence"; Japanese ends in ます。).
- **Queries:** `decks.themes.deck_reading` reads the cards once (`profile__tempo` in the same
  query) and the tag sizes once a day from the cache: two queries per page, three on the first.
- **Calibration:** `scratchpad/synergy/calib.py` on the 41 fixture decks (tags only, so repeats
  come from `repeatable-…` there). The golden test holds four engines: Chainer's aristocrats
  (Ashnod's Altar, Grave Pact) and reanimator (Chainer), Prismari's spellslinger (Rootha),
  Limit Break's voltron (Cloud).


## How PR 4 works

- **Only the owner's word is stored** (`decks.models.DeckMark`): kind (engine or pair), theme,
  `cards` as `[[oracle id, piece key], ...]`, `key` (`cards.synergy.mark_key`: kind, theme,
  sorted oracle ids; unique per deck) and the state, locked or dismissed. A suggestion is no
  row: it is read live, so a new reading only adds suggestions and never deletes anything.
- **Merge** (`decks.themes.merge`): a locked entry comes first and stays; a dismissed one leaves
  the list for `Reading.dismissed`. An entry the deck still shows is the live one, numbers and
  all; one it no longer shows is made again from the row (`cards.synergy.rebuild`), without a
  level or numbers. Locked pairs are open above the five suggestions and take none of their
  places. `ThemeCard.oracle_id` gives every engine and pair its `parts` and so its key.
- **Resting:** a stored entry whose card is not in the deck (99 or command zone) names it in
  `gone` and is shown grey with "Resting, a card is missing: …"; it is active again by itself
  when the card is back. An entry whose theme or pieces the catalogue lost is not shown, but
  its row stays.
- **The button** (`decks.views.DeckMarkView`, `POST /decks/<id>/marks/`, `decks/_mark.html`):
  lock and dismiss send the key, and the server looks it up in the reading it makes again
  (`Reading.suggestion`), so nothing can be stored that the page did not show; undo sends the
  row's id, scoped to the deck. Owner only (404 otherwise), CSRF, 30 per minute. htmx gets
  `#engines` back; without scripting the form returns to `#engines`. A key the page no longer
  shows (the list changed in another tab) stores nothing and is not an error.
- **Queries:** one more for the rows, and one for the names of cards that left the deck, only
  when a row has such a card.


## How PR 5 works

- **Running, not the named cards:** an engine runs when every role (piece) of it has a card
  out at the end of a turn, *any* card of the deck that fills the role (decision A,
  2026-11-03). A permanent counts on the battlefield, an instant or sorcery in hand; the
  commander on the battlefield only. `simulation.combos.AnyOf` is a requirement any one of
  several cards meets, each with its own zone.
- **Which engines** (`decks.engine_watch.watches`): the ones the page shows, locked first, one
  per theme, at most 5; none a piece short or resting. Each chunk works this out again from
  the deck (`deck_reading(deck, cards)`, one read of the cards), like the combo plan.
- **Free:** the watches ride with the combos in the run's own games (`simulate_chunk`),
  key `engine:<theme>`, `Watch.combo=False`, so a run that watched only engines has no `wins`.
  A reading that fails is logged and the run goes on without engines. Engine pieces are not
  `key_cards`: a tutor going for them would change what is measured.
- **Stored:** `finalize_run` moves them out of `combos` into `run.result["engines"]`
  (`split`), `{theme: {games, by_turn}}`. No model.
- **Shown:** the deck page quotes the last finished run, by turn 6 or the last turn played
  (`running`, `engine_rows`, also in the lock button's htmx answer); the report lists them
  after the combos (`simulations/_engines.html`, `measured`). Methodology `#engines`.


## How PR 6 works

- **No extra call** (decision A, 2026-11-03): the deck summary's one Mistral call
  (`simulations.summary`, `PROMPT_VERSION` 6) also gets `engines` in its facts: the engines
  that can run (`decks.engine_watch.runnable`, the ones PR 5 measures, at most 5), each with
  its theme, its cards and, in English, what each card does. A reading that fails is logged and
  leaves them out; the summary goes on.
- **The answer** has `engines`: per engine `theme` and `how`, one or two sentences on which card
  first, what to protect, what stops it. `parse` (`_explained`) keeps only themes it offered,
  one each, `how` cleaned like every summary text and cut at `HOW_MAX` (300). Stored in
  `DeckSummary.content["engines"]` as `{theme, cards (sorted), how}`. Optional: an answer
  without it is still a summary. Free text is not checked for card names; the prompt says to
  name only the engine's own, and the page says it can be wrong.
- **Shown on the deck page only** (decision B): `summary.engine_notes(user, deck)` gives
  `{(theme, cards): {how, language}}` when summaries are on, Mistral is configured and the
  summary is written; `engine_watch.engine_rows` hands it to `decks/_engines.html` (also in
  the lock button's htmx answer). It shows under the rules' sentence, in its own `lang`, with
  "Written by Mistral AI. It can be wrong." An engine whose cards changed shows none.
- **Old summaries** (version 5) are not rewritten: nobody pays for our change. They show no
  explanation until the deck changes and the summary is written again.
- **Checked once for real** (decision C): one German call on the seeded deck "Cards that need an
  opponent", 3,175 prompt and 669 completion tokens. The text was usable but not exact (it
  called Chainer's Nightmares "revived tokens"), so the attribution line is needed.
