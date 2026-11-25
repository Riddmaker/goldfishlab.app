# Paket «Startseite» (B-12, B-13, B-06)

Ziel: Neue sehen auf der Startseite sofort, was alles geht, und was andere gerade tun.
Freigegeben vom Owner am 2026-11-25.

## PRs

| PR | Inhalt | Stand |
|---|---|---|
| 1 | B-12 kompakte Importbox nur auf der Startseite (`compact=True` in `templates/decks/_import_form.html`): Dropzone einzeilig, eine Hinweiszeile statt zwei Absätzen, «More» neben dem Knopf; auf niedrigen Laptop-Bildschirmen (Höhe ≤ 840 px) kleinere Flasche. Vier neue Spasszeilen im Ticker, je mit eigenem Symbol | erledigt |
| 2 | B-13 unter den zwei grossen Kacheln eine kleine pro weitere Funktion der Deckseite (`core.views.FEATURES`): Keep or mulligan, Hand-Tutor, Combos, Strategien, Bracket, Versionen. Jede Kachel (auch die zwei grossen) verlinkt «How it works» auf ihren Abschnitt der Methodik-Seite; dort neu: Hand-Tutor, Strategien, Versionen (mit den bestehenden Sätzen der App). Neues Symbol `version` | erledigt |
| 3 | B-06 Combos, denen eine Karte fehlt, als Kartenreihe wie die Combos im Deck: `[Karte] + [Karte] = Ergebnis`, die fehlende Karte gestrichelt und blass mit «Not in your deck» darunter (`{% card_tile … missing=True %}`). Der eingeklappte Rest in kleinen Karten. Auf dem Handy kleinere Karten und das Ergebnis als eigene Zeile, das gilt auch für die Combos im Deck | erledigt |

Pro PR: ruff, djlint, Vollsuite, Browser-Check (Desktop 1280×800 und 1440×900, angemeldet und nicht, Handy).

## PR 1, Befunde

- Vorher: Box 409 px hoch, erste Tickerzeile endete bei 1025 px, auf keinem Laptop sichtbar.
- Nachher: Box 258 px; erste Tickerzeile endet bei 858 px (1440×900) und 786 px (1280×800).
- Die Importseite und die Gast-Seite behalten die ausführliche Box (Test).
- Die neuen Spasszeilen: zwei Zitate bleiben englisch («The cake is a lie.», «Have you tried turning it off and on again?»), zwei eigene sind übersetzt.

## PR 2, Befunde

- Drei Funktionen hatten keinen Abschnitt auf «How this works»: Hand-Tutor, Strategien, Versionen. Die neuen Abschnitte verwenden nur Sätze, die die App schon sagt (und übersetzt hat).
- Die Deck-Zusammenfassung (KI) und die Legalität haben keine eigene Kachel: die Zusammenfassung läuft nur, wenn sie eingeschaltet ist; die Legalität ist eine Prüfung, keine Funktion.
- Neue Funktion der Deckseite: eine Zeile in `FEATURES`, ein Test prüft, dass ihr Anker auf der Methodik-Seite existiert.

## PR 3, Befunde

- Deck `land_light` mit echter Spellbook-Antwort: 7 Combos mit einer fehlenden Karte, 3 oben, 4 eingeklappt. Jede Reihe markiert genau die fehlende Karte; mit Tastatur-Fokus wächst sie wie jede andere Karte.
- Handy (390 px): vorher standen das «=» allein und unter den Karten eine Lücke, bei drei Karten rutschte die dritte in eine neue Zeile. Das betraf auch die Combos im Deck. Jetzt passen bis drei Karten in eine Zeile, das Ergebnis folgt darunter.
