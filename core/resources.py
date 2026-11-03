"""The resources page: videos and sites worth knowing (Themen-Doc, Ressourcen).

Plain data, so the page is a loop and a change is one line here. Chosen and
checked in November 2026 (docs/research/resources/); the titles stay as their
channels wrote them, in English, and only the sentence beside each is ours.

Videos are links, never embedded players: the page loads nothing from YouTube
or Google, so the Content-Security-Policy keeps `frame-src 'none'` and the
privacy policy has no new processor to name.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Video:
    title: str
    channel: str
    year: int
    minutes: int
    url: str
    why: str


@dataclass(frozen=True)
class Link:
    name: str
    url: str
    why: str


@dataclass(frozen=True)
class Group:
    heading: str
    items: tuple


VIDEOS = (
    Group(_("What Commander is"), (
        Video("How To Play Commander in 27 Minutes", "Based Deck Department", 2024, 27,
              "https://www.youtube.com/watch?v=67fTegpCt9g",
              _("The whole format from the start: the command zone, the 100 cards, how a "
                "table of four plays.")),
        Video("A Beginner's Guide to Commander 2026", "Planeswalker Project", 2026, 11,
              "https://www.youtube.com/watch?v=eQmx0keygRM",
              _("Short and current: the format as it is played today.")),
    )),
    Group(_("The rules and the brackets"), (
        Video("WeeklyMTG: Commander Brackets", "Magic: The Gathering", 2025, 61,
              "https://www.youtube.com/watch?v=w67WFRCxQ6w",
              _("Wizards of the Coast introduce the brackets themselves. The Game "
                "Changers list has changed since: the current one is on the official "
                "Commander page below.")),
        Video("Game Changers & Brackets ft. Gavin Verhey", "EDHREC", 2025, 0,
              "https://www.youtube.com/watch?v=gDDhBXSRnjQ",
              _("A designer at Wizards on what the brackets are for and how to use "
                "them at a table.")),
    )),
    Group(_("Building a deck"), (
        Video("Commander Deckbuilding Template for the New Era", "The Command Zone", 2025, 107,
              "https://www.youtube.com/watch?v=OSNV6224cHg",
              _("How many lands, ramp, card draw and removal a deck wants - the "
                "template many players start from.")),
    )),
    Group(_("Learning to play"), (
        Video("Learn to Play Magic: The Gathering", "Magic: The Gathering", 2023, 46,
              "https://www.youtube.com/watch?v=pISs64CG6Tg",
              _("Turns, phases and combat, for anybody new to Magic itself. Not about "
                "Commander, but everything in it applies.")),
    )),
)

LINKS = (
    Group(_("Official"), (
        Link("Commander - Wizards of the Coast", "https://magic.wizards.com/en/formats/commander",
             _("The rules of the format in writing - commander tax, commander damage, "
               "colour identity - with the brackets and the current Game Changers.")),
        Link("Magic rules", "https://magic.wizards.com/en/rules",
             _("The Comprehensive Rules, for when a question goes deep.")),
    )),
    Group(_("Building and keeping decks"), (
        Link("Archidekt", "https://archidekt.com",
             _("A visual deck builder; its CSV export imports here as it is.")),
        Link("Moxfield", "https://moxfield.com",
             _("A popular deck builder with a playtest mode.")),
        Link("ManaBox", "https://manabox.app",
             _("An app that scans your cards and keeps your collection and decks.")),
    )),
    Group(_("Looking things up"), (
        Link("Scryfall", "https://scryfall.com",
             _("Every card, with its full rules text and rulings. Our card data comes "
               "from here.")),
        Link("EDHREC", "https://edhrec.com",
             _("Which cards other players run with your commander.")),
        Link("Commander Spellbook", "https://commanderspellbook.com",
             _("Combos, searchable by the cards in your deck. The combos in a report "
               "come from here.")),
    )),
    Group(_("Players and news"), (
        Link("Magic: The Gathering Official", "https://discord.com/invite/wizards-magic",
             _("The official Discord server of Wizards of the Coast.")),
        Link("r/EDH", "https://www.reddit.com/r/EDH/",
             _("The largest Commander community on Reddit.")),
        Link("r/magicTCG", "https://www.reddit.com/r/magicTCG/",
             _("News and talk about all of Magic.")),
        Link("MTGGoldfish", "https://www.mtggoldfish.com/articles",
             _("News, budget decks and strategy articles.")),
    )),
)
