"""Monte Carlo simulation for Commander decks.

Modules:
    cards: the card model, the mana-ability rules and ``DeckDefinition``.
    fixtures: hand-written deck lists (the reference deck and the test fixture).
    mana: the mana model, with colour and the four scaling rules.
    game: game state, mulligans (the first one free), the turn sequence.
    agent: the priority-driven player.
    analysis: Monte Carlo aggregation and the metrics.

**This package never imports Django.** It runs and tests without a database,
without a settings module and without Django installed at all. The only module
that sees both worlds is ``simulations/engine/adapter.py`` in the application.
If a change wants to soften that boundary, the boundary has leaked - repair the
boundary, not the symptom.
"""

#: Version of the simulation's behaviour.
#:
#: **Every behaviour change increments this number**, including one that fixes a
#: bug. Stored runs record which version computed them, so the interface can
#: later say "computed with engine v2, current is v5 - re-run to compare".
#: Without this number, an old result and a new one are indistinguishable, which
#: makes both of them worthless.
#:
#: 1: The original engine, hardwired to the Chainer deck (Phase 1).
#: 2: Phase 2 - arbitrary decks, mana abilities as rules, priorities on the
#:    card. Aggregates identical to v1 but for one correction: the
#:    ``draw_engine`` metric had been missing Liliana, Dreadhorde General.
#: 3: The 2026-09-25 pre-launch review. Creatures with a flat mana ability tap
#:    for mana; a flat ability may cost generic mana to use (a Signet nets one,
#:    in both of its colours); a permanent that does not untap is tapped for
#:    mana once and stays tapped (``Card.untaps``, ``Game.stays_tapped``);
#:    Phyrexian symbols are paid with life when the mana is needed for the
#:    generic part. The reference deck has none of these cards, so the golden
#:    snapshot is unchanged - the evidence that nothing else moved.
#: 4: Phase 10, after the first user test. A tutor in a deck with no priority
#:    list takes a spell before a land - a key card (a combo piece or an
#:    engine) first, then the biggest spell castable by next turn - where it
#:    used to take the cheapest card, which was a land. "What you drew" counts
#:    only the opening hand and the draws (``Game.drawn``), no longer the cards
#:    a tutor found, and carries the sum of squares for a spread. The reference
#:    deck has a priority list, so the golden snapshot is unchanged.
#: 5: P19 R1 (issue #36). A source that makes one mana of a choice of colours
#:    - dual and tri lands, Talismans, Command Tower, Arcane Signet - puts that
#:    choice into the pool, and paying a cost settles it (an exact search, like
#:    hybrid symbols). Until now the reader fixed one colour and reported a
#:    gap, and a land with two basic types (Blood Crypt) silently made its
#:    first colour in WUBRG order. Per-colour mana counts a choice toward each
#:    colour it offers. The mono-black reference deck has no such source, so
#:    the golden snapshot is unchanged.
#: 6: P19 R2 (issue #36). A search that puts lands onto the battlefield is
#:    played: ramp spells (Rampant Growth, Cultivate's one-to-play and
#:    one-to-hand), permanents that fetch on arrival (Wood Elves, Solemn
#:    Simulacrum, Sakura-Tribe Elder) and fetch lands, which are sacrificed
#:    the turn they are played for the land they find. The land picked is the
#:    one that adds the most colours the lands in play cannot make. Until now
#:    each was a gap and did nothing; a fetch land was a land with no mana.
#: 7: P19 R3 (issue #36). A land that enters tapped unless something holds is
#:    checked against the game: check lands (a land type you control), fast,
#:    slow and battle lands (how many lands), Battlebond lands (a Commander
#:    table has three opponents), snarls and reveal lands (a card in hand) and
#:    shock lands (2 life, paid while above the Phyrexian floor). Until now the
#:    first kinds always entered tapped and a shock land always untapped, for
#:    free.
#: 8: P19 R4. "Activate only if you control ..." is checked against the game:
#:    Temple of the False God taps for nothing before the fifth land, a
#:    Tainted land for {C} without its land type, Mox Opal only beside two
#:    more artifacts. Urborg, Yavimaya, Crypt Ghast and Cabal Coffers play by
#:    the rules that until now only a built-in annotation could give them.
#: 9: P19 R5. Reflecting Pool and Incubation Druid make a colour the other
#:    lands could make, worked out each turn. Exotic Orchard and Fellwar Stone
#:    make the deck's colours - an assumption that three opponents have the
#:    lands for them, stated on the card page - where they made nothing.
#: 10: P19 R6. Filter lands (Fetid Heath) turn their {C} and a mana of their
#:    colours into two of their colours, when that narrows nothing; converters
#:    (Study Hall, Prismatic Lens) turn their {C} and one more mana into a
#:    colour when a payment would otherwise fail.
#: 11: P19 R7. Treasure tokens: Big Score, Rapacious Dragon and the like make
#:    them as they resolve, and a payment sacrifices one only when it would
#:    fail without; the rest wait for a later turn. "As an additional cost,
#:    discard a card" is paid with the weakest card in hand.
#: 12: P19 R8. A draw that comes with a discard (Faithless Looting, Frantic
#:    Search) discards the weakest cards after it, Brainstorm puts two back
#:    on top; Mystic Confluence draws
#:    three; a spree mode's cost (Insatiable Avarice) is paid with its draw.
#: 13: P19 R9. X costs are paid: an X spell is cast last in the main phase
#:    with everything left as X (at least ``x_min``), and "draw X cards"
#:    draws them. Until now X was 0 and the spell was cast early, for
#:    nothing.
#: 14: P19 R10. Green Sun's Zenith, Chord of Calling, Finale of Devastation,
#:    Natural Order and Whir of Invention put the card they find onto the
#:    battlefield, mana value X or less where the card says so.
#: 15: P19 R11. Mana that counts the board: Gaea's Cradle and Circle of
#:    Dreams Druid per creature, Elvish Archdruid and Priest of Titania per
#:    Elf, Cabal Stronghold per basic Swamp, Crypt of Agadeem per black
#:    creature card in the graveyard, Nykthos by devotion, Tron together.
#: 16: P19 R12. Vampiric, Mystical, Enlightened and Worldly Tutor put their
#:    card on top of the library. Lands that enter tapped unless you control
#:    a legendary creature, a planeswalker or a basic land, Starting Town
#:    (first three turns), the Turbulent lands (assuming each opponent plays a
#:    land a turn) and "a player has 13 or less life" (your own life) are
#:    checked against the game. A battle land no longer counts itself among
#:    the basic lands it asks for, so it entered untapped beside one.
#: 17: P19 R13. Mana on top of what a source makes: Wild Growth, Utopia
#:    Sprawl, Mirari's Wake, Kinnan, Forsaken Monument, Caged Sun, Mana
#:    Reflection; Cryptolith Rite and Abundant Growth give a mana ability;
#:    Bloom Tender, Sanctum Weaver and Overgrown Battlement count the board,
#:    Battle Hymn and High Tide are rituals that do.
#: 18: P19 R14. Land searches that are activated - Wayfarer's Bauble,
#:    Burnished Hart, Myriad Landscape, Urza's Cave, the Panoramas, Lander
#:    tokens - with mana left once spells are cast, before an X spell; Knight
#:    of the White Orchid while an opponent has more lands (assumed: one a
#:    turn each); a Saga's first chapter as it enters. The report's "two
#:    scaling mana sources" no longer counts filter lands or Reflecting Pool.
#: 19: P19 R15. Additional costs are paid: a sacrifice (a Treasure, a Lander,
#:    a creature that comes back, then the cheapest - never the commander or
#:    a mana source; a land only to a land search worth it), life, a discard,
#:    mana; "pay X life" with X = 0. Altars and Phyrexian Tower sacrifice
#:    when their mana unlocks a spell, Spirit Guides are free rituals, and a
#:    creature's {T} search (Wight of the Reliquary) waits a turn.
#: 20: P19 R16. Triggers that make Treasure, mana or a card: landfall (Lotus
#:    Cobra, Tireless Provisioner), casting a spell (Birgi, Storm-Kiln Artist,
#:    Lotho on the second), a creature dying (Pitiless Plunderer), a Dragon
#:    entering (Ganax), upkeep and first main phase (Awakening Zone, Hulking
#:    Raptor), Brass's Bounty per land; an Eldrazi Spawn is a Treasure for
#:    {C}. Smothering Tithe, Rhystic Study and Esper Sentinel get one a round
#:    (assumed: one opponent does not pay). Warren Soultrader is an altar.
#: 21: P19 R17. Mana only some spells may spend: Cavern of Souls, Ancient
#:    Ziggurat, Plaza of Heroes, Eldrazi Temple, Power Depot and the rest
#:    pay only for the spells they name - a creature, legendary, colourless,
#:    artifact spell, a subtype - and spell their mana first; what such a land
#:    makes instead ({C}) is spent last. "The chosen type" is the deck's most
#:    common creature type (assumed, shown on the deck page) unless an
#:    annotation names one. Shrine of the Forsaken Gods counts seven lands.
#: 22: P19 R18. Small readings: "N mana in any combination of colours"
#:    (Relic of Sauron, Gwenna); the colour chosen as a card enters (Heraldic
#:    Banner, Valgavoth's Lair, Throne of Eldraine for monocoloured spells of
#:    it) is the one the deck's costs ask for most - assumed, shown on the
#:    deck page; Three Tree City counts the deck's creature type; "a creature
#:    with power 4 or greater" by printed power (Fanatic of Rhonas, Ilysian
#:    Caryatid); Nimbus Maze's two conditions; Rite of Flame and Cabal
#:    Ritual's threshold count the graveyard. Hybrid symbols are no gap: the
#:    payer has settled them since version 5.
#: 23: P19 R19. Tapping other permanents for mana: Springleaf Drum and the
#:    lands like it tap a creature that makes no mana, Relic of Legends a
#:    legendary one, Urza each artifact (his Construct included), Grand
#:    Architect each blue creature. Mox Diamond discards a spare land card,
#:    Chrome Mox exiles the most expensive coloured card and makes its
#:    colours; Millikin mills a card each time it taps.
#: 24: P19 R20. Mana kept as counters, energy or a Food: Pentad Prism and
#:    Crystalline Crawler spend theirs as a Treasure, only when a payment
#:    needs it; Vivid lands, storage lands, Gemstone Mine, Aether Hub,
#:    Servant of the Conduit, Wall of Roots, Mana Bloom and Gilded Goose one
#:    each turn they tap; Everflowing Chalice (kicked as an X), Astral
#:    Cornucopia and Empowered Autogenerator tap for one per counter;
#:    Runaway Steam-Kin turns three counters into {R}{R}{R}.
#: 25: P19 R21. Mana by power and +1/+1 counters: a creature's power is the
#:    printed one plus its counters (Kami of Whispered Hopes, Marwyn, Vivi
#:    Ornitier, Selvala's greatest power, Arbor Adherent's toughness, Gyre
#:    Sage's counters, "a creature with power 4 or greater"); counters come
#:    as creatures enter with them, from Rishkar, Evolve, Increment, Adapt
#:    (Incubation Druid's three) and "another Elf enters"; Growing Rites of
#:    Itlimoc turns into its land at four creatures.
#: 26: P19 R22. Combat: every creature that can attacks each turn and, a
#:    goldfish having no blockers, deals its damage to a player - assumed,
#:    shown on the deck page. Sword of the Animist and Kodama of the West
#:    Tree find lands; Goldspan Dragon (whose Treasures make two), Ragavan,
#:    Professional Face-Breaker, Curse of Opulence and the rest make
#:    Treasure; Savage Ventmaw's mana goes to a second main phase, where a
#:    spell with X waits for it. Equipment is equipped with spare mana, an
#:    Aura goes on a creature as it is cast, Living weapon makes a Germ;
#:    Paradise Mantle makes its creature a mana creature.
#: 27: P19 R23. Searches with a choice: Open the Armory, Solve the Equation,
#:    Spellseeker and Ranger-Captain of Eos find a type or subtype up to a
#:    mana value; Demonic Counsel any card with delirium; Archdruid's Charm a
#:    land onto the battlefield while the hand has none, else a creature;
#:    Gifts Ungiven keeps the two cards the opponent leaves; Gamble discards
#:    at random; Defense of the Heart from turn 4 (assumed). Land searches by
#:    power (Traverse the Outlands, Entish Restoration), with Gates, twice
#:    (Claim Jumper), in the opponents' end steps (Keeper of the Accord),
#:    tempted (one opponent accepts, assumed), off the top by X (Open the
#:    Way); Sword of Hearth and Home in combat. Neheb adds {R} per combat
#:    damage, Crime Novelist {R} per artifact sacrificed, a Treasure too.
#: 28: card model phase 1. Forge's card scripts, translated into blocks, fill
#:    in what the reader left open: cards drawn as a permanent enters
#:    (Baleful Strix, Mulldrifter), a land search, Treasure, entering tapped
#:    (Boseiju; a Lorwyn land revealing a creature type, The Black Gate's
#:    life), a mana ability. A draw in reminder text is no longer the spell's
#:    own (Cycling, a Clue, Learn, a gift), a token "tapped and attacking" no
#:    card entering tapped; Phyrexian Tribute sacrifices two creatures.
#: 29: P20 F2b. Two commanders: partners, a creature and its Background, the
#:    Doctor's companion and the Doctor. Each has its own tax, the cheaper one
#:    that can be paid is cast first, "your commander attacks" fires for each,
#:    and "commander on the battlefield" means at least one - with a count of
#:    the games that have both. A deck with one commander plays as in 28.
#: 30: card model phase 4, PR 17. Each permanent keeps its own state: two
#:    copies of one card, or two Germs, no longer share counters, equipment
#:    or being tapped. The engine's four roles (sacrifice outlet, recursive
#:    creature, drain, draw engine) are read off the card's categories, so a
#:    category correction changes play too; a draw engine is a card whose
#:    drawing ability happens again and again. Games are seeded per block of
#:    250, so how a run is cut into chunks decides no game.
#: 31: card model phase 5, PR 19. A card's "when you cast this spell" draw
#:    (Hydroid Krasis, the Kozileks) happens once, so it is no draw engine;
#:    neither is an opponent's spell (Esper Sentinel) the deck's own. A
#:    creature that connives as it enters (A.I.M. Scientists) draws, discards
#:    and grows when the discard was no land.
#: 32: card model phase 5. A creature that connives in combat (Ultron,
#:    Unlimited as it attacks, Norman Osborn as it deals combat damage)
#:    connives, and a combat that draws a card has a second main phase; Mob
#:    Lookout's "target creature you control connives" is itself.
#: 33: card model phase 5, PR 23. Eldrazi Spawn and Scion a card makes as
#:    it enters, resolves or is cast (a Treasure that adds {C}); mana from a
#:    trigger as a creature enters and at the main phase (Frontier Siege's
#:    first only); Priest of Forgotten Gods, an altar for two creatures that
#:    draws; mana abilities given to your lands, to creatures of one type, to
#:    the enchanted creature, and two of one colour to the enchanted land.
#:    Chromatic Lantern taps for its own beside its grant.
#: 34: searches onto the battlefield for more kinds of card (not legendary,
#:    any permanent, a creature type, a mana value one more than what was
#:    sacrificed), as a creature enters and as a {T} ability once a turn; a
#:    basic land to the top. A planeswalker's "+N: Add ..." each turn from the
#:    turn after it arrives; several creatures tapped at once for mana
#:    (Heritage Druid). Land searches on a Saga's second chapter, as a spell
#:    is cast, as a door unlocks, with four lands or fewer. Cards drawn by a
#:    count (Champion of Dusk's Vampires), and as much life lost.
#: 35: the commander is cast as soon as it can be paid, before every spell
#:    without a priority of its own and after only fast mana and a few
#:    staples (agent.COMMANDER_PRIORITY); a deck that ranks its commander
#:    keeps that rank.
ENGINE_VERSION = 35

__all__ = ["ENGINE_VERSION", "agent", "analysis", "cards", "fixtures", "game", "mana"]
