"""What changed on Goldfish Lab, newest first (C7).

The entries are code, not rows: each title and text is a string for the
translation catalogues like every other one on the site, so an entry is in all
seven languages the moment its catalogues are, and a change that a person
would notice brings its entry in the same pull request.

Written for the person using the site - what they can now do or will see -
never in the words of the code. An entry dated in the future is held back
until its day, so one can be written before the release it belongs to.
"""

from dataclasses import dataclass
from datetime import date

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

#: The first entry's day: the launch, 2026-09-28.
FIRST_DAY = date(2026, 9, 28)


@dataclass(frozen=True)
class Entry:
    day: date
    #: The entry's anchor on the page and the last part of its feed id. Never
    #: changed once published: a feed reader would show it again as new.
    slug: str
    title: str
    text: str
    #: Where to see it: a view name, and an anchor on that page.
    url_name: str = ""
    anchor: str = ""

    @property
    def link(self) -> str:
        if not self.url_name:
            return ""
        url = reverse(self.url_name)
        return f"{url}#{self.anchor}" if self.anchor else url


ENTRIES = (
    Entry(
        date(2026, 10, 10), "triggers",
        _("Smothering Tithe, Lotus Cobra and Storm-Kiln Artist"),
        _("Cards that make Treasure, mana or a card when something happens now do it in a "
          "simulated game: Lotus Cobra and Tireless Provisioner when a land enters, Birgi, "
          "Storm-Kiln Artist and Lotho when you cast a spell, Pitiless Plunderer and Pawn of "
          "Ulamog when a creature dies, Ganax when a Dragon arrives, Awakening Zone and Hulking "
          "Raptor every turn, Brass's Bounty once for each land. Smothering Tithe, Rhystic Study "
          "and Esper Sentinel wait on opponents a goldfish does not have, so they give one a "
          "round, assuming one opponent does not pay - and the card list on your deck page now "
          "says beside every card what the engine assumes to play it."),
    ),
    Entry(
        date(2026, 10, 10), "sacrifice-costs",
        _("Village Rites, Harrow, Natural Order and Ashnod's Altar"),
        _("Spells that sacrifice, pay life or discard as an additional cost now pay it: a "
          "simulated game gives up a Treasure first, then a creature that comes back, then its "
          "cheapest - never your commander or a mana creature - and a land only to Harrow or a "
          "search worth it. In the playtest you choose what to sacrifice. Ashnod's Altar, "
          "Phyrexian Tower and Krark-Clan Ironworks sacrifice when their mana lets a spell be "
          "cast, the Spirit Guides add their mana from your hand, and Wight of the Reliquary "
          "and Elvish Reclaimer search from the turn after they arrive. Toxic Deluge pays X = 0 "
          "life, as nothing in a goldfish is there for it."),
    ),
    Entry(
        date(2026, 10, 9), "activated-land-search",
        _("Wayfarer's Bauble, Myriad Landscape and Lander tokens"),
        _("Wayfarer's Bauble, Burnished Hart, Myriad Landscape, Urza's Cave, the Panoramas and "
          "Lander tokens now find their lands: a simulated game activates them with the mana "
          "left once its spells are cast, and in the playtest a button does. Knight of the "
          "White Orchid searches while an opponent has more lands, assuming each plays one a "
          "turn. The report no longer counts filter lands or Reflecting Pool among the mana "
          "sources that grow with your board."),
    ),
    Entry(
        date(2026, 10, 9), "mana-on-top",
        _("Wild Growth, Mirari's Wake and Cryptolith Rite"),
        _("Auras such as Wild Growth, Fertile Ground and Utopia Sprawl now add their mana "
          "when the land they enchant is tapped, and Mirari's Wake, Vorinclex, Kinnan, "
          "Forsaken Monument, Caged Sun, Mana Reflection and Nyxbloom Ancient add theirs to "
          "what you tap. With Cryptolith Rite or Enduring Vitality your creatures tap for any "
          "colour from the turn after they arrive. Bloom Tender, Sanctum Weaver and Overgrown "
          "Battlement count your board, and Battle Hymn and High Tide make their mana."),
    ),
    Entry(
        date(2026, 10, 9), "tutor-to-top",
        _("Vampiric Tutor, Minas Tirith and the battle lands"),
        _("Vampiric, Mystical, Enlightened and Worldly Tutor now put the card they find on "
          "top of your library, where you draw it next turn. Lands that enter tapped unless "
          "you control a legendary creature, a planeswalker or a basic land, Starting Town "
          "and the Turbulent lands now enter untapped when that holds; for the Turbulent "
          "lands we assume each opponent plays a land a turn. Battle lands such as Prairie "
          "Stream entered untapped beside a single basic land; now they wait for two."),
    ),
    Entry(
        date(2026, 10, 9), "board-counts",
        _("Gaea's Cradle, Elvish Archdruid, Nykthos and Tron"),
        _("Mana that counts your board now makes what it counts: Gaea's Cradle and Circle of "
          "Dreams Druid one green for each creature, Elvish Archdruid and Priest of Titania "
          "one for each Elf, Cabal Stronghold one black for each basic Swamp, Crypt of "
          "Agadeem one for each black creature card in your graveyard, Nykthos your devotion, "
          "and the Urza lands seven together."),
    ),
    Entry(
        date(2026, 10, 9), "tutor-onto-battlefield",
        _("Green Sun's Zenith and Chord of Calling find their creature"),
        _("Green Sun's Zenith, Chord of Calling, Finale of Devastation, Nature's Rhythm and "
          "Whir of Invention now put the card they find onto the battlefield, with mana value "
          "X or less. Before, they found nothing."),
    ),
    Entry(
        date(2026, 10, 9), "x-costs",
        _("Spells with X are paid with everything left"),
        _("Stroke of Genius, Blue Sun's Zenith, Walking Ballista and every other card with X "
          "in its cost were cast for X = 0 and as early as possible. Now they wait until "
          "nothing else can be cast and take all the mana that is left as X, and draw-X "
          "spells draw that many. On a card's page you can set the smallest X worth casting "
          "it for, and when you play a deck by hand the board asks you for X."),
    ),
    Entry(
        date(2026, 10, 9), "draw-then-discard",
        _("Faithless Looting discards, Brainstorm puts back"),
        _("Spells that draw and then discard, such as Faithless Looting, Frantic Search and "
          "Izzet Charm, now discard your weakest cards, and Brainstorm puts two back on top. "
          "Before, they only drew. Mystic Confluence now draws three, and Insatiable "
          "Avarice pays for the mode that draws."),
    ),
    Entry(
        date(2026, 10, 9), "treasure",
        _("Treasure, and discarding a card to cast"),
        _("Big Score, Seize the Spoils, Rapacious Dragon and the like now make their Treasure, "
          "which pays for a later spell when your lands fall short. Spells that ask you to "
          "discard a card to cast them discard your weakest one."),
    ),
    Entry(
        date(2026, 10, 9), "filter-lands",
        _("Filter lands and Study Hall fix your colours"),
        _("Filter lands such as Fetid Heath and Rugged Prairie turn one mana of their colours "
          "into two. Study Hall, Opal Palace and Prismatic Lens turn a mana into a colour you "
          "are missing when a spell needs it."),
    ),
    Entry(
        date(2026, 10, 9), "other-lands",
        _("Exotic Orchard and Reflecting Pool make mana"),
        _("Reflecting Pool and Incubation Druid make a colour your other lands make. Exotic "
          "Orchard and Fellwar Stone make your deck's colours, assuming your opponents' lands "
          "make them; the card page says so, and you can say what they make at your table."),
    ),
    Entry(
        date(2026, 10, 9), "activate-only-if",
        _("Temple of the False God, Urborg and Cabal Coffers"),
        _("Lands that make mana only when you control enough lands or a land type, such as "
          "Temple of the False God, the Tainted lands and the Verges, now make it once that "
          "holds. Urborg, Yavimaya, Crypt Ghast and Cabal Coffers now play by their rules, "
          "and Path to Exile and Maze of Ith no longer need you."),
    ),
    Entry(
        date(2026, 10, 9), "tapped-unless",
        _("Lands that enter tapped unless ..."),
        _("Check lands, fast and slow lands, snarls and shock lands now enter untapped "
          "whenever their condition holds in the game, and shock lands pay their 2 life "
          "for it."),
    ),
    Entry(
        date(2026, 10, 9), "land-search",
        _("Ramp spells and fetch lands find their lands"),
        _("Rampant Growth, Cultivate, Wood Elves, Solemn Simulacrum and fetch lands now "
          "put the land they find onto the battlefield, picking the colour your lands "
          "lack. Green ramp decks reach their commander sooner."),
    ),
    Entry(
        date(2026, 10, 9), "dual-lands",
        _("Dual lands make either colour"),
        _("Dual lands, Talismans, Command Tower and Arcane Signet now make any "
          "of their colours, chosen when you pay. Three-colour commanders come "
          "down much more often, and these cards no longer need you."),
        "methodology", "report-colour",
    ),
    Entry(
        date(2026, 10, 8), "whats-new",
        _("What's new, by feed or by mail"),
        _("This page. Follow it in your feed reader, or get one mail a month "
          "when there is something new."),
    ),
    Entry(
        date(2026, 10, 8), "bracket-speed",
        _("Speed and the Commander Brackets"),
        _("The report times every combo in your deck that ends the game and "
          "says which Commander Brackets that speed fits."),
        "methodology", "report-brackets",
    ),
    Entry(
        date(2026, 10, 8), "plans",
        _("Three plans: Goldfish, Koi and Kraken"),
        _("Goldfish is free. Koi and Kraken simulate more turns and more "
          "games, by the month or by the year."),
        "pricing",
    ),
    Entry(
        date(2026, 10, 7), "lands",
        _("How many lands in Commander?"),
        _("We rebuilt precons with eight different land counts and played "
          "each version thousands of times: what one land more or less changes."),
        "datapages:lands",
    ),
    Entry(
        date(2026, 10, 7), "precons",
        _("Every Commander precon, simulated"),
        _("Each Commander precon since 2025 has its own page, played thousands "
          "of times - how fast it gets going and what it draws."),
        "datapages:precons",
    ),
    Entry(
        date(2026, 10, 6), "share",
        _("Share a report"),
        _("A finished run can be shared by link, and copied as text for a "
          "forum or a chat. Only you decide which runs are shared."),
    ),
    Entry(
        date(2026, 10, 6), "queue",
        _("Your place in the queue"),
        _("When many runs come in at once, the run page shows how many are "
          "ahead of yours instead of a bar that does not move."),
    ),
    Entry(
        date(2026, 10, 6), "small-fixes",
        _("Small fixes"),
        _("An icon for your phone's home screen. A double click in the "
          "playtest no longer ends in an error. \"Reanimate\" now means a "
          "creature can come back."),
    ),
    Entry(
        date(2026, 10, 4), "new-cards",
        _("New cards every night"),
        _("The card data is refreshed every night, so a new set can be "
          "simulated as soon as its cards are out."),
    ),
    Entry(
        date(2026, 10, 4), "languages",
        _("Goldfish Lab in seven languages"),
        _("German, French, Italian, Spanish, Portuguese (Brazil) and Japanese "
          "join English. Pick yours at the bottom of any page."),
    ),
    Entry(
        date(2026, 10, 2), "deck-summary",
        _("A written deck summary"),
        _("A short text says what your deck does and how it wins, read from "
          "the simulation. Don't want it? Switch it off on the run page."),
    ),
    Entry(
        date(2026, 10, 2), "by-strategy",
        _("What you drew, by strategy"),
        _("\"What you drew\" also groups your cards by the strategy they "
          "serve, so you see whether a plan shows up in time."),
    ),
    Entry(
        date(2026, 10, 2), "clearer-report",
        _("A clearer report"),
        _("The report is in a new order, its line charts explain themselves, "
          "and the last run is on the deck page."),
    ),
    Entry(
        date(2026, 10, 2), "engine-4",
        _("Numbers that are right"),
        _("A new version of the simulation counts a few things more "
          "carefully. Run a deck again to get the new numbers."),
        "methodology",
    ),
    Entry(
        date(2026, 9, 30), "deck-page",
        _("A new deck page"),
        _("What you can do with the deck comes first, then its numbers and "
          "a card grid you can filter."),
    ),
    Entry(
        date(2026, 9, 30), "playtest-table",
        _("The playtest as a card table"),
        _("Your hand is a fan, the battlefield a table, and the mana a card "
          "needs is shown on it."),
    ),
    Entry(
        date(2026, 9, 30), "try",
        _("Try it without an account"),
        _("Paste a deck list and run it straight away. Save the deck when you "
          "want to keep it."),
        "guests:try",
    ),
    Entry(
        date(2026, 9, 29), "card-roles",
        _("Check what your cards do"),
        _("Each card's role - ramp, removal, draw and the rest - can be "
          "checked and confirmed, and the deck page counts what is still open."),
    ),
    Entry(
        date(2026, 9, 29), "what-you-drew",
        _("What you drew"),
        _("The report shows what the deck drew by each turn: by card type, "
          "by role and by mana value."),
    ),
    Entry(
        date(2026, 9, 29), "no-questions",
        _("A run starts without questions"),
        _("Goldfish Lab no longer asks which spell to cast first. It plays "
          "the deck the way the methodology describes and reports one score."),
        "methodology",
    ),
    Entry(
        date(2026, 9, 28), "sign-up",
        _("An account in two fields"),
        _("An email address and one password. The confirmation link signs "
          "you in, and every mail we send is in plain words."),
    ),
)


def published(today: date | None = None) -> list[Entry]:
    """Every entry up to and including `today`, newest first."""
    from django.utils import timezone

    today = today or timezone.localdate()
    return [entry for entry in ENTRIES if entry.day <= today]


def of_month(month: date) -> list[Entry]:
    """The entries of the month `month` falls in, newest first."""
    return [entry for entry in ENTRIES
            if (entry.day.year, entry.day.month) == (month.year, month.month)]
