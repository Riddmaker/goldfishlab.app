"""Priorities for the remaining cards.

Kept apart from the card definitions, because this is an opinion about how to
play and not a property of a card: the same card has a different priority in a
different deck. The database adapter fills this field from the user's
annotations later on.
"""

PRIORITIES = {
    "Grave Pact": 83,
    "Tergrid, God of Fright": 82,
    "Braids, Arisen Nightmare": 80,
    "Carrion Feeder": 79,
    "Blood Artist": 78,
    "Dread Presence": 74,
    "Ashnod's Altar": 72,
    "Greed": 70,
    "Morbid Opportunist": 68,
    "Bottomless Pit": 66,
    "Hypnotic Specter": 65,
    "Reassembling Skeleton": 64,
    "Gravecrawler": 63,
    "Bloodghast": 62,
    "Nether Traitor": 61,
    "Dauthi Voidwalker": 60,
    "Hymn to Tourach": 59,
    "Syr Konrad, the Grim": 57,
    "Gray Merchant of Asphodel": 56,
    "Mikaeus, the Unhallowed": 55,
    "Tainted Aether": 54,
    "Painful Quandary": 53,
    "Liliana's Caress": 52,
    "Exquisite Blood": 51,
    "Cao Cao, Lord of Wei": 50,
    "Gate to Phyrexia": 49,
    "Phyrexian Reclamation": 48,
    "Dauthi Embrace": 45,
    "Helm of Possession": 44,
    "Liliana, Dreadhorde General": 43,
    "Lim-Dul the Necromancer": 30,
}
