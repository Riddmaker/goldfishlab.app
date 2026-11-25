# Paket «Deck in der Hand» (B-05, B-11)

Ziel: Das eigene Deck kennenlernen, als hätte man es seit der Kindheit gespielt. Zuerst ein Fehler bei
den Streichkandidaten, dann eine eigene Ansicht, in der man das Deck wie am Tisch in die Hand nimmt.
Gestartet am 2026-11-25 (Owner: «du machst parallel dann das nächste paket»).

## PRs

| PR | Inhalt | Stand |
|---|---|---|
| 1 | B-05 Exquisite Blood bekommt eine Rolle, Combo-Teile werden nie als Streichkandidat vorgeschlagen (auch ohne Spellbook-Abfrage) | erledigt |
| 2 | B-11 Grundgerüst ohne JavaScript: eigene Seite `decks/<id>/hand/` mit Knopf oben auf der Deckseite. Der Stapel in 2.5D (CSS `perspective`, gestaffelte Kanten), vom Server mit einem Seed gemischt. GET-Formulare: «Shuffle» (neuer Seed), «Cut» (die obersten n Karten offen hinlegen). Rückseite und Matte über CSS-Variablen (`--sleeve`, `--mat`), damit B-10 sie später setzen kann | erledigt |
| 3 | B-11 in der Hand, mit `static/js/deck-hand.js`: aufheben und drehen, n Karten abheben und ansehen, wieder einmischen, in Häufchen mischen (n Häufchen austeilen, wieder stapeln), eine Karte antippen (hervorgehoben, «Draw» in die Hand oder «Put back» oben/unten), «Rebuild» (neu ordnen). Tastatur und `prefers-reduced-motion`. Die reinen Funktionen (Mischen, Häufchen, Abheben) unter Node getestet wie `hand-press.js` | offen |

Nicht drin: echtes 3D (WebGL), eine frei drehbare Kamera, Spielregeln (dafür gibt es das freie Spiel), eigene Matte und Sleeves (B-10, offene Fragen zu Speicher und Kosten).

Pro PR: ruff, djlint, Vollsuite, Browser-Check (`check.sh`), push auf dev; main nur auf Owner-Wort.

## Entscheide

- **Kein Serverzustand für die Hand.** Ohne Spielregeln braucht es keine Quelle der Wahrheit auf dem
  Server (anders als das freie Spiel, Phase 5). Ohne JavaScript bestimmt der Seed in der URL die
  Reihenfolge, mit JavaScript lebt der Zustand im Browser. Keine Tabelle, keine Migration.
- **Ein Seed, zwei Wege.** Server und Browser mischen nicht gleich; das ist auch nicht nötig. Der Seed
  macht nur die Seite ohne JavaScript wiederholbar (Zurück-Knopf, geteilter Link).

## PR 1, Befunde

- Exquisite Blood hatte keine Kategorie: Forge beschreibt den Auslöser mit `T:Mode$ LifeLost`, und
  der stand nicht im Vokabular des Importers. Neu wird er zum Ereignis `life_lost` mit dem Spieler,
  der das Leben verliert (`TRANSLATION` 11). Wer gewinnt, was ein Gegner verliert, gilt als
  `drain_payoff` (die andere Hälfte des Drain). Von 26 Karten mit diesem Auslöser betrifft das zwei:
  Exquisite Blood und Bloodthirsty Conqueror. Mindcrank (mahlt) bleibt ohne Drain.
- Auf prod kommt die neue Lesart mit dem nächsten nächtlichen Abgleich: eine neue `TRANSLATION`
  liest alle Forge-Skripte neu und leitet danach die Profile neu ab. Sofort geht es mit
  `manage.py ingest_scryfall --profiles` (lokal 1,5 min).
- Combo-Schutz ohne Abfrage: `combos.services.known_in` findet die Combos unseres Katalogs (die
  Spellbook-Antworten für andere Decks), deren genannte Karten alle im Deck sind. Mindestens zwei
  genannte Karten; eine Combo mit einer Karte, die der Katalog nicht kennt, zählt nicht. Es geht
  keine Anfrage an Spellbook hinaus.
- Browser: Deck `five_colour`, Arcane Signet war Kandidat; mit einer bekannten Combo
  «Arcane Signet + Brainstorm» fällt die Karte weg, Sol Ring rückt nach.

## PR 2, Befunde

- Seite `decks/<id>/hand/` (`DeckInHandView`, `decks/in_hand.py`): ohne Seed leitet sie auf einen
  neuen um; `cut` 1..15 deckt die obersten Karten auf. Falsche Adresse: 400; fremdes Deck: 404.
- Der Stapel ist so dick wie die Karten, die noch drin sind (`--left`, fünf gestaffelte Kanten).
  `--mat`, `--sleeve` und `--sleeve-edge` stehen auf `.in-hand-table`; B-10 setzt nur sie.
- Neues Symbol `deck` (Stapel, oberste Karte hebt sich) für den Knopf auf der Deckseite.
- Nebenbei: Der Test für das Combo-Rate-Limit fiel unter xdist zufällig durch. django-ratelimit zählt
  in festen Minutenfenstern; acht Posts über ein Fensterende zählten neu. Eine autouse-Fixture in
  `tests/conftest.py` hält die Uhr der Rate-Limits pro Test an (gilt auch für test_security, test_guests).
- Browser (`five_colour`, Desktop und Handy, ohne JavaScript): Knopf, Umleitung auf Seed, Abheben 5,
  gleicher Seed gleiche Karten, Zurücklegen, neues Mischen, kein seitliches Scrollen, keine Fehler.
