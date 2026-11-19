# Plan: Startseite und Deck-Seite v2 (Testbericht des Owners, 2026-11-19)

Status: **FREIGEGEBEN 2026-11-19** (alles, D1–D4 nach Empfehlung). Fortschritt unten. Grundlage: Testbericht vom
19.11., Screenshots (Startseite, Deck-Seite mit Chainer-Vampir-Liste) und ein Blick in den Code.

## Befunde aus dem Code (warum es heute so aussieht)

- **Drei Begriffssysteme nebeneinander**, daher die Verwirrung:
  1. Kategorien (`simulations/report.py` `SEEN_ROLES`): Ramp, Card draw, Removal, Board wipe, Tutor,
     Counterspell, Protection, Recursion. Stehen in «What is in it» und bei «Cards».
  2. Strategie-Rollen (`SEEN_STRATEGY_ROLES`, `SEEN_MECHANIC_ROLES`): Sacrifice outlet, Reanimate,
     Drain, Theft … Stehen in «Your strategies, and what would feed them».
  3. Themen (`cards/themes.py`, 13 Stück): Aristocrats, Reanimator, Theft, Graveyard … Stehen bei
     «Built around» und bei den Engines. Jedes Thema besteht aus **Pieces** (Aristocrats = Payoff +
     Sacrifice outlet + Fodder). Das sind genau die «Engine-Gruppen» aus dem Testbericht.
- **Symbole:** In `core/icons/` gibt es 27. Für Board wipe, Tutor, Counterspell, Protection,
  Recursion, für alle Strategie-Rollen, alle Themen und alle Kartentypen fehlt ein Symbol.
- **KI und Commander:** Der Prompt (`simulations/summary.py` `SYSTEM_PROMPT`) sagt nirgends, dass der
  Commander in der Command Zone beginnt und von dort gewirkt wird. Das Modell rät deshalb.
- **Bloodghast:** Die Karte ist korrekt als «Fodder» erkannt (Tag `recursion-self`). Der Text
  «brings more bodies» klingt aber so, als würde sie andere Kreaturen zurückholen.
- **Hand-Hover:** `.hand-slot` schiebt die Karte zur Mitte des Fächers (`translateX(--to-middle)`,
  `scale(1.8)`). Das war ein früherer Wunsch und wird jetzt umgedreht.

## Die PRs, in dieser Reihenfolge

### PR A: Startseite und Aktionen (schnelle Fixes)
1. **Startseite:** Die Import-Box wird vertikal schlanker (weniger Innenabstand, niedrigere
   Drop-Zone), damit man sieht, dass unten noch mehr kommt. «Where do I get my list?» und «More
   options» werden zu **einem** Aufklapper «Mehr». Die Kacheln «Simulate» und «Draw a hand» werden
   gleich hoch (Grid stretch, das Bild füllt den Rest). Die Links «How it works», «Every Commander
   precon» und «Resources» über dem Footer fallen weg, sie stehen schon im Footer.
2. **Deck-Seite:** «Hand tutor» und «Keep or mulligan?» bekommen eigene Aktions-Buttons wie
   «Simulate again» und «Draw a hand», mit zwei neuen Symbolen (Tutor-Hand mit Lupe, zwei Karten
   mit Pfeil). Auf dem Handy brechen die Buttons um.
3. **«What the engine cannot model»** bekommt denselben Look wie «Earlier runs and games»
   (Pergament, standardmässig zugeklappt).
4. **Hand-Hover:** Die Karte wächst an ihrem eigenen Platz und schiebt sich nach oben
   (`transform-origin` unten, `translateY` statt `translateX`). Fährt die Maus durch die Hand,
   wächst eine Karte nach der anderen und schrumpft wieder, mit weichen Übergängen. Die Karten am
   Rand wachsen nach innen, damit sie nicht aus dem Bild ragen. Mit `prefers-reduced-motion` gibt es
   keine Animation, nur die Grösse.

### PR B: Die KI versteht den Commander
1. Der Prompt bekommt eine feste Regel: Der Commander beginnt in der Command Zone. Er wird von dort
   gewirkt, jedes Mal um 2 teurer, und ist nie in Bibliothek oder Hand, wird also nie gezogen, nie
   gesucht und nie abgeworfen. Dazu kommt ein Fakt `commander_zone` in den Deck-Daten.
2. **Prüfung nach der Antwort**, wie die bestehenden Checks: Ein Satz, der den Commander zusammen
   mit «draw/hand/tutor/search» nennt, wird verworfen und einmal neu angefragt.
3. Eine neue Prompt-Version. Bestehende Texte werden beim **nächsten Lauf** neu geschrieben, es gibt
   keine Massen-Neuberechnung (keine Zusatzkosten bei Mistral).
4. Bloodghast-Text: Das Piece «Fodder» sagt neu «%(card)s gives you bodies to sacrifice». Das
   stimmt für Token-Macher und für Karten, die sich selbst zurückholen.

### PR C: Ein Begriffssystem mit Symbolen (braucht Entscheid D1)
- Zwei Ebenen, überall gleich benannt:
  - **Rollen**, also was eine Karte tut: die 8 Kategorien plus die Strategie-Rollen.
  - **Themen**, also worum das Deck gebaut ist: Ein Thema besteht aus Rollen und zeigt das auch an
    («Aristocrats = Sacrifice outlet + Payoff + Fodder»).
- In «Built around» erklärt ein Klick auf ein Thema dessen Rollen.
- **Symbole für alles:** Rollen, Themen und Kartentypen (Creature, Planeswalker, Artifact,
  Enchantment, Instant, Sorcery, Land, Battle), etwa 35 neue Zeichnungen im bestehenden
  Strichstil. Zuerst kommen sie in den Styleguide, du gibst sie frei, dann werden sie eingebaut.
  Damit bekommen auch die Kapitel in «Your strategies» (Tutor, Sacrifice outlet, Recursion) ihre
  Symbole.

### PR D: Vorschläge zum Rauswerfen und Artwork (braucht Entscheid D2)
1. «Your strategies» zeigt pro vorgeschlagener Karte das **Artwork** (Scryfall art crop,
   lazy-loaded) mit Hover-Zoom.
2. **Neu «Was raus könnte»:** Eine Regel liefert die Kandidaten, die KI wählt nur aus dieser Liste.
   Kandidaten sind Karten ohne Rolle und ohne Thema (heute 48 «have none», ohne Länder) und Karten
   in Rollen, die über dem Richtwert liegen. Die teuersten kommen zuerst. Jede Wahl braucht einen
   Grund, und der Commander ist nie ein Kandidat.

### PR E: Combos, Engines und Synergien visuell
1. **Neue Darstellungsregel** (kommt in DESIGN.md und in den Styleguide): Karten werden gezeigt
   statt beschrieben. Eine Combo sieht so aus: `[Karte] + [Karte] = Ergebnis`. Unter jeder Karte
   steht ihr entscheidender Effekt in einer Zeile. Gezeigt wird die Version, die im Deck ist
   (`decks/printing_art`), sonst die Standardversion. Beim Hover springt die Karte nach vorne und
   wird lesbar.
2. Combos bekommen weniger Text: der Schritt-Text von Commander Spellbook kommt in einen Aufklapper,
   sichtbar bleibt nur das Ergebnis.
3. Für Engines und Synergien gilt dieselbe Regel. Die Aufklapper «Advanced» fallen weg.
4. **Engine-Gruppen:** Unter den Engines steht pro Thema eine Gruppe, zum Beispiel
   «Füllen den Friedhof» / «Holen zurück» / «Opfern», jeweils mit ihren Karten. Dazu kommt pro
   Gruppe die **kombinierte Chance**, dass das Thema bis Zug N steht (aus jedem Piece mindestens
   eine Karte). Berechnet wird sie aus der Deckliste mit einem Monte-Carlo-Mischen, das
   Überschneidungen sauber behandelt, mit festem Seed, damit gleiche Liste gleiche Zahl ergibt.

### PR F: Karten-Explorer (braucht Entscheid D3)
1. «Cards» bekommt Filter-Chips für Rollen, Themen-Rollen und Kartentypen. Ein Klick wählt einen
   Chip, **Ctrl/Cmd-Klick** fügt weitere dazu. Auf dem Handy sind die Chips einfache Schalter, weil
   es dort kein Ctrl gibt.
2. Darunter stehen die Karten nach Kategorie gruppiert. Beim Hover wird die Karte gross.
3. Pro Auswahl gibt es eine **Kurve statt Zahlen im Text** für die Züge 1–8:
   - «mindestens eine aus jeder gewählten Gruppe»
   - «mindestens eine aus irgendeiner gewählten Gruppe»
   - mit dem Schalter **«mit Tutoren»**: deine Tutoren zählen als Treffer, so entsteht eine zweite
     Kurve.
   Beim Hover über einen Zug erscheint die Verteilung, also in welchem Zug du die erste Karte
   siehst, mit Mittelwert ± Standardabweichung.
4. Die Rechnung läuft im Browser (Monte Carlo, etwa 10'000 Mal mischen, dauert unter 100 ms). Der
   Server liefert nur die Deckliste mit den Rollen als JSON. Die SVG-Kurve folgt dem Stil von
   `simulations/charts.py`.

### PR G: Platz unter der Mana-Kurve (braucht Entscheid D4)
Siehe D4.

## Entscheide, die du triffst (jeweils mit meiner Empfehlung)

- **D1 Begriffe:** Ich empfehle die zwei Ebenen Rollen und Themen, wie in PR C beschrieben. Die
  Alternative wären drei getrennte Listen, die beim Namen bleiben und nur Symbole bekommen.
- **D2 Rauswerfen:** Ich empfehle, dass die Regel die Kandidaten bestimmt und die KI nur Gründe
  dazu schreibt, wie heute bei den Zukäufen. Die KI allein würde Karten erfinden.
- **D3 Kurven:** Ich empfehle Monte Carlo im Browser. Die Alternative ist Rechnen auf dem Server
  pro Auswahl, das sind mehr Anfragen und es ist langsamer.
- **D4 Platz unter der Mana-Kurve:** Ich empfehle eine Mini-Box **«Deine Starthand»**: die Chance
  auf 2–4 Länder in 7 Karten plus ein Link zum Quiz «Keep or mulligan?». Das Quiz hat bisher 0 echte
  Nutzer, dort würde es gefunden. Die Alternative wären die drei wichtigsten Rollen als Mini-Kurve.

## Was ich unterwegs selbst entschieden habe (überstimmbar)

- Bestehende KI-Texte werden nur beim nächsten Lauf neu geschrieben, nicht alle auf einmal.
- Die Symbole für Kartentypen zeichne ich selbst und übernehme keine Wizards- oder Font-Symbole
  (Marke und Lizenz).
- Auf dem Handy werden die Filter-Chips zu einfachen Schaltern.

## Wie der Plan HABIT 1, 4, 5 und 7 abdeckt

- **HABIT 1:** Es gibt keine neuen Secrets. Kartennamen und KI-Texte sind Daten, sie werden im
  Template escaped und im JS mit `textContent` gesetzt, nie mit `innerHTML`. Filter aus der URL
  gelten nur gegen die bekannten Schlüssel. Bilder kommen nur von Scryfall-Hosts, die die CSP schon
  erlaubt.
- **HABIT 4:** Ich verwende `{% icon %}` und `core/icons/` wieder, ebenso die Themen-Pieces, die
  Chancen-Rechnung aus `strategies`/`report`, `printing_art`, `charts.py` und das bestehende
  Playtest-CSS. Es kommt keine neue Bibliothek dazu.
- **HABIT 5:** DESIGN.md (Regel «Karten zeigen statt beschreiben», Begriffe), STYLEGUIDE.html (alle
  neuen Symbole und die Combo-Darstellung), diese Datei als Bauplan, README nur falls nötig, die
  KPI-Datei (Quiz-Aufrufe über die Startbox).
- **HABIT 7:** Jeder PR bekommt Unit-Tests (Prompt-Regel und Prüfung, Kandidatenliste, Begriffe,
  Gruppen-Chance mit festem Seed, Icon-Namen) und View-Tests. Vor jedem Push laufen ruff, djlint
  und die volle Suite. Dazu kommt ein Headless-Browser-Test mit Screenshots auf Desktop und Handy,
  mit Hover-Modus und `prefers-reduced-motion`, in DE und EN.

## Was von dir kommt (ein Block)

1. Den Plan freigeben, ganz oder pro PR.
2. D1–D4 entscheiden.
3. Nach PR C die neuen Symbole im Styleguide freigeben.

## Fortschritt

- [x] PR A  - [ ] PR B  - [ ] PR C  - [ ] PR D  - [ ] PR E  - [ ] PR F  - [ ] PR G
