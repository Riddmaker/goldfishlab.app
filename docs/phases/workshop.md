# Paket «Werkstatt» (B-09, B-01 V1)

Ziel: «We make you know your deck like you played it since childhood». Die Werkstatt ist das Wie:
messen, Vorschlag annehmen, neue Version, Zeitstrahl.
Freigegeben vom Owner am 2026-11-24 mit allen Empfehlungen.

## Entscheide (2026-11-24)

1. Knopf heisst «Neue Version».
2. Läufe einer entfernten Version bleiben beim Deck, markiert als «alte Version».
3. Adds kommen aus den Strategie-Picks (`simulations/strategies.py`, `simulations/cuts.py`) und einer neuen Kartensuche im Katalog; keine neue KI.
4. Versionen haben in V1 kein Limit.

## PRs

| PR | Inhalt | Stand |
|---|---|---|
| 1 | B-09 Hand: feste Trefferfläche pro Karte (Streifen), Bild statt Slot bewegt sich, Hand an der Matte, Hilfetext darunter, Schweben in Ruhe, Lang-Drücken am Handy (`static/js/hand-press.js`, auch Tutor und Quiz) | erledigt (88c9f86) |
| 2 | Modell `DeckVersion`: Schnappschuss der Liste + Kommentar; V1 lazy beim ersten Bearbeiten; Auswahl schreibt `DeckCard` über die Logik aus `decks/services.py:451`; Diffs berechnet, nicht gespeichert (`decks/versions.py`) | erledigt |
| 3 | Zeitstrahl der Versionen auf der Deckseite (`templates/decks/_versions.html`): neueste oben, +/- je Karte gegen die Version davor, Läufe je Version über `list_print` mit «vier Länder in Zug 4» des letzten Laufs, Wählen und Entfernen; Läufe entfernter Versionen unter «Läufe einer alten Liste» | erledigt |
| 4 | Delta-Maske (Cuts/Adds annehmen) und Kartensuche | offen |

Pro PR: ruff, djlint, Vollsuite, Browser-Check; Architektur-Doc nachführen bei PR 2.

## PR 1, Befunde

- Der gehobene Slot drehte und hob sich selbst, die Trefferfläche wanderte unter der Maus weg.
- Ein gehobener Slot lag mit voller Breite obenauf: ab 9 Karten übersprang die Maus nach rechts Karten.
- Gedrehte Trefferstreifen lassen unten Lücken: Streifen 1.5rem breiter und 1rem tiefer.
- Browser-Checks: echtes Brett (7 Karten, drei Höhen, beide Richtungen, Handy lang/kurz), synthetisch 7/10/15 Karten.
