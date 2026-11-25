# Package «The card shop» (B-16)

Goal: a small point-and-click adventure in a made-up card shop, hidden behind an Easter egg. Five to
ten minutes, pixel art, every room built as closely as possible after the interior of a real shop,
with no real people, no real logo and a made-up name. Planned 2026-11-26 while the owner asked to
work through the night («arbeite durch die nacht … weiter machen bei den features»); the plan
follows the card as filed (Ready), and the calls below are mine until the owner overrules them.

## How it is built (and why)

| Part | Choice | Why |
|---|---|---|
| Page | `/shop/`, public, no account; `noindex`, not in the sitemap, not in any menu | an Easter egg is found, not listed; nothing to sign in for |
| Way in | on the home page, the goldfish's third triple poke in one visit opens a small door beside the flask that links to `/shop/` (`static/js/fish.js` already counts the triples) | reuses the one playful thing the site has; keyboard pokes count too |
| Engine | plain JavaScript (`static/js/shop.js`), no library; rules kept pure for Node tests like `fish.js` | CSP is `script-src 'self'`; a game library is far more than a few rooms need (YAGNI) |
| Scenes | data, not code: a room is a picture plus hotspots (rectangles in the picture's own pixels), each with a look text, an action and a condition | rooms change when the real interior is known, without touching the engine |
| Pictures | SVG pixel art, drawn by a script from shapes and small sprite grids (`scripts/shop_art.py`), committed as files under `static/img/shop/` | crisp at any size, a few kB, no image editor needed; a real artist's PNGs can replace them later behind the same hotspots |
| «Real art» | close-ups (a card in the case, a poster) show real card art from Scryfall with the artist named, as the rest of the site does | my reading of «pixel art, but with real art»; owner to confirm |
| Hotspots | real `<button>`s over the picture, each with its name; Tab moves between them, Enter acts | playable with a keyboard and a screen reader, not only a mouse |
| Text | one `<dialog>`-like panel below the picture; every text through `gettext`, so all six languages | the site is translated, and the i18n tests expect it |
| State | what you carry and what happened, in `sessionStorage` only; nothing sent to the server | no account, nothing to store or delete (privacy), and nothing to migrate |
| Without JS | the shop front as a still picture and one sentence | as the fish: an enhancement only |
| Motion | no animation needed to play; `prefers-reduced-motion` turns the small ones off | DESIGN rule for motion |

## Story (draft, short)

You come in on a Friday to get a deck ready for the evening. Three things are missing: sleeves, one
card and a place at a table. The shopkeeper (a made-up person) helps if you help first. Three small
puzzles, one per thing; at the end you sit down, shuffle, and the shopkeeper says it plays well, as
far as one can tell. That is the only link to Goldfish Lab: «Goldfish it» opens the import.

## PRs

| PR | Content | State |
|---|---|---|
| 1 | Engine (scenes, hotspots, inventory, conditions, panel), the `/shop/` page, the way in from the fish, one room (the shop floor) in placeholder pixel art, tests (Node rules, view, a11y) | in progress |
| 2 | Rooms after the real interior (needs the owner's photos or a sketch of the floor plan), the three puzzles, close-ups with real card art | waits for the owner's material |
| 3 | Ending, translations of the whole text, polish; DESIGN note on the shop's palette | after PR 2 |

## Open for the owner (pushed to the back, as asked)

1. The interior: photos or a hand sketch (where the counter, the case of singles, the wall of
   boosters, the play tables and any back room are). Without it the rooms stay generic.
2. «Real art»: my reading above (pixel rooms, real card art in close-ups) - or did you mean real
   painted pictures instead of pixel art?
3. The made-up name of the shop. My placeholder: «The Brass Lantern», chosen to sound like nothing
   real nearby; change it freely, but not to anything close to the real shop's name.
4. The story above: keep, change, or give me yours.

## Not in

Real people, the real logo or a similar name; sound and music; saving across visits; scores; any
data sent to the server.
