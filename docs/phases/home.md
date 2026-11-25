# Paket «Startseite» (B-12, B-13, B-06)

Ziel: Neue sehen auf der Startseite sofort, was alles geht, und was andere gerade tun.
Freigegeben vom Owner am 2026-11-25.

## PRs

| PR | Inhalt | Stand |
|---|---|---|
| 1 | B-12 kompakte Importbox nur auf der Startseite (`compact=True` in `templates/decks/_import_form.html`): Dropzone einzeilig, eine Hinweiszeile statt zwei Absätzen, «More» neben dem Knopf; auf niedrigen Laptop-Bildschirmen (Höhe ≤ 840 px) kleinere Flasche. Vier neue Spasszeilen im Ticker, je mit eigenem Symbol | erledigt |
| 2 | B-13 eine Kachel pro Funktion der Deckseite, auch Versionen | offen |
| 3 | B-06 Combos, denen eine Karte fehlt, als Kartenbilder | offen |

Pro PR: ruff, djlint, Vollsuite, Browser-Check (Desktop 1280×800 und 1440×900, angemeldet und nicht, Handy).

## PR 1, Befunde

- Vorher: Box 409 px hoch, erste Tickerzeile endete bei 1025 px, auf keinem Laptop sichtbar.
- Nachher: Box 258 px; erste Tickerzeile endet bei 858 px (1440×900) und 786 px (1280×800).
- Die Importseite und die Gast-Seite behalten die ausführliche Box (Test).
- Die neuen Spasszeilen: zwei Zitate bleiben englisch («The cake is a lie.», «Have you tried turning it off and on again?»), zwei eigene sind übersetzt.
