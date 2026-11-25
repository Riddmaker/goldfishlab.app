# Paket «Deckseite: weniger Text» (B-15)

Ziel: Die Deckseite liest sich zwischen zwei Spielen in einem Blick. Regel aus `DESIGN.html`
(«Number before sentence», 2026-11-24): ein Block beginnt mit Zahl, Prozent, Symbol oder Balken;
Worte nur, wo keine Zahl trägt, dann ein Satz; Erklärungen hinter «More» (`core/_why.html`, Link auf
die Methodik) oder in einem `<details>`. Gestartet 2026-11-25 (Owner: «dann das nächste paket»).

## Bestandsaufnahme (2026-11-25, Deck `five_colour`, nach einem Lauf)

Gezählt im Browser (`$S/browser/inventory.js`): Absätze, Listenpunkte und Links mit 5+ Wörtern.
Vorher: **552 sichtbare Wörter** in 61 Blöcken, 166 weitere zugeklappt.

| Block | Satz heute | Wörter | Entscheid |
|---|---|---|---|
| Kopf | drei Zeilen unter den Knöpfen («Hand tutor – which plan…», «Keep or mulligan? – ten hands…», «Deck in hand – shuffle it…») | 40 | weg; Symbol und Wort auf dem Knopf tragen es, die Startseite und die Methodik erklären |
| Kopf | Deck ohne Commander: «Goldfish Lab plays by the Commander rules - 40 life…» | 35 | ein Satz mit Zahl + «More» (#commander) |
| Kopf | «Your last run · Datum · 10,000 games · 6 turns» | 14 | bleibt (Zahlen) |
| Kacheln | Legalität: Detail bei Fehlern (Kartennamen) | 19+7 | bleibt (Daten, die man braucht) |
| Strategien | «Cards that fit your colours, are legal … The most played first.» | 20 | hinter «More» (#strategies) |
| Strategien | «At least one by turn 4: 95% now, 97% with 2 more.» | 12 je | Zahl zuerst: «95% → 97% by turn 4, +2 cards» |
| Strategien | «To make room: the cards a rule finds the easiest to cut …» | 34 | hinter «More» (#strategies) |
| Strategien | je Kandidat «Ramp: 14, the template has 10» | 6 je | «Ramp 14 / ~10» wie bei der Kurve |
| Strategien | «The odds are calculated from your list …» | 17 | hinter «More» |
| Kurve | «Spells by mana value.» | 4 | weg (Achse sagt es) |
| Kurve | «77% of your hands of seven hold 2 to 4 lands …» | 17 | bleibt (Zahl zuerst) |
| Kurve | «Keep or mulligan? Judge ten hands from this deck» | 9 | kürzer: «Keep or mulligan?» (Knopf oben ist derselbe) |
| Inhalt | «A theme is a plan made of roles …» | 21 | hinter «More» (#engines) |
| Inhalt | «No theme we know of. Plenty of good decks have no single plan.» | 13 | ein Satz |
| Inhalt | Legende «a common template, not a rule. Click a row…» | 13 | «template» + «More» |
| Inhalt | «A card counts in every category it fits …» | 27 | Zahl zuerst («27 without a role») + Rest hinter «More» |
| Combos | «Look this deck up in Commander Spellbook's curated list of combos.» | 11 | kürzer, Quelle bleibt genannt |
| Engines | «Cards that together make value again and again …» + More | 17 | nur Überschrift + «More» |
| Engines | «No engine: engines come from …» | 16 | kurz: «No engine: no theme found.» |
| Karten | «Choose a chip: a curve shows …» | 16 | bleibt (ein Satz, Leerzustand) |
| Karten | Methode des Explorers (zugeklappt bis Auswahl) | 45 | in ein `<details>` «How it is counted» |
| Versionen | «Take cards out, put cards in: …» | 16 | hinter «More» (#versions) |
| Unten | «What the engine cannot model», «Earlier runs» | – | schon zugeklappt, bleibt |

Ziel: **unter 300 sichtbare Wörter** nach einem Lauf; ein Test hält eine Obergrenze fest.

## PRs

| PR | Inhalt | Stand |
|---|---|---|
| 1 | Kopf, Kacheln, Strategien: drei Erklärzeilen weg, Nicht-Commander-Hinweis ein Satz, Strategien Zahl zuerst und Erklärungen hinter «More» | erledigt |
| 2 | Kurve, Inhalt, Combos und Engines, Karten, Versionen; Wortbudget-Test für die ganze Seite | erledigt |

Nicht drin: der Bericht (Simulation), die Startseite, neue Grafiken; nichts wird gelöscht, was nur
dort steht: jede Erklärung, die von der Deckseite geht, steht danach auf der Methodik-Seite.

Pro PR: ruff, djlint, Vollsuite, Browser-Check (`inventory.js`), Übersetzungen, push auf dev.

## PR 1, Befunde

- Sichtbare Wörter nach einem Lauf (`five_colour`): 552 → 438.
- Kopf: die drei Erklärzeilen unter den Knöpfen sind weg. Ein Deck ohne Commander: «60 cards, no
  commander: the numbers hold under the Commander rules.» + «More» (#commander); `! Not a Commander
  deck` im Status bleibt.
- Strategien: «95% → 97% by turn 4, +2 cards» (die Zahl gross); der ganze alte Satz bleibt für
  Screenreader (`sr-only`). Einleitung, «To make room…» und «The odds are calculated…» stehen in der
  Methodik unter #strategies; auf der Seite bleibt «The most played first.» + «More». Die
  Mistral-Hinweise bleiben (KI-Kennzeichnung).
- Streichkandidat: «Ramp 14 / ~10» statt «Ramp: 14, the template has 10» (`simulations/cuts.py`),
  wie die Zahlen neben der Kurve; die Methodik erklärt die drei Teile.

## PR 2, Befunde

- Sichtbare Wörter nach einem Lauf, gleich gezählt wie vorher: 438 → **317** (Ausgang 552). Die Zahl
  enthält noch die Screenreader-Sätze der Kurve und der Strategien (rund 120 Wörter), die man nicht
  sieht; was das Auge liest, liegt deutlich unter 300.
- Kurve: «Spells by mana value.» weg; der Link unter der Starthand heisst nur noch «Keep or mulligan?».
- Inhalt: «A theme is a plan…» weg (Methodik #drew erklärt Themen); kein Thema: «No known theme;
  plenty of good decks have none.» Die Legende heisst «template» + «More» (#drew). Statt des Satzes
  über Kategorien steht die Zahl: «27 cards without a role».
- Engines: Erklärung nur noch als «More» neben der Überschrift; Legende «↻ works again and again.»;
  ohne Thema «No engine: we found no theme.». «Your themes, piece by piece»: die Erklärung der Prozent
  steht neu in der Methodik unter #engines, auf der Seite nur «More».
- Karten: die Methode des Explorers in einem `<details>` «How it is counted».
- Versionen: ohne Version nur «More» (#versions); der Satz steht dort schon.
- Combos: «Look this deck up in Commander Spellbook's curated list of combos.» bleibt (ein Satz, nennt
  die Quelle).
- Test `tests/test_less_text.py`: Wortbudget 200 für Sätze (Absätze und Listenpunkte mit 5+ Wörtern,
  ohne Falten und Screenreader-Text); heute 185 für das Beispieldeck ohne Lauf.
- Nebenbei: Französisch siezt (`test_i18n_long_pages`), ein neuer Satz hatte «ta liste».
