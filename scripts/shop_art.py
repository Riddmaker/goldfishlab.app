"""Draw the pixel art of the card shop (B-16) as SVG files.

Usage:  .venv/bin/python scripts/shop_art.py

Writes `static/img/shop/*.svg`. A room is 320 x 180 pixels; each pixel is one
unit of the SVG's viewBox, drawn with `crispEdges`, so the picture stays sharp
at any size. Rooms are drawn from rectangles; people and small things from
character grids (`SPRITES`), one character a pixel, `.` see-through.

The art is a placeholder until the rooms are drawn after the real interior
(docs/phases/shop-adventure.md); the hotspots in `core/shop.py` name the
same rectangles, so a new picture only has to keep them in place.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "img" / "shop"
W, H = 320, 180

PALETTE = {
    "k": "#16110d",  # near black
    "d": "#2a1f17",  # dark wood
    "w": "#4a3524",  # wood
    "l": "#6b4c31",  # light wood
    "t": "#8a6a45",  # wood edge
    "p": "#e9dcc0",  # parchment
    "q": "#c9b48c",  # parchment shade
    "r": "#8e2a22",  # blood red
    "R": "#b0402f",  # red light
    "g": "#2f5d50",  # verdigris
    "G": "#4f8a76",  # verdigris light
    "b": "#2b3f5c",  # blue
    "B": "#4b6a92",  # blue light
    "y": "#d9a63a",  # gold
    "Y": "#f0cf72",  # gold light
    "s": "#e7b48f",  # skin
    "S": "#c98d6a",  # skin shade
    "h": "#3b2a1e",  # hair
    "n": "#5a5a66",  # grey
    "N": "#8d8d99",  # grey light
    "x": "#a7c7cf",  # glass
    "X": "#d6eaee",  # glass light
    "o": "#24323a",  # night window
    "f": "#e98a2c",  # goldfish orange
}

SPRITES = {
    # The shopkeeper behind the counter, from the waist up: a made-up person.
    "keeper": [
        "....hhhhh...",
        "...hhhhhhh..",
        "...hsssssh..",
        "...ssksksS..",
        "...sssssss..",
        "....sSSSs...",
        ".....sss....",
        "...gggggggg.",
        "..gggGgggggg",
        "..ggggpgggGg",
        "..gggppppggg",
        "..sgggppgggs",
        "..sgggggggggs",
        "..sggggggggs",
    ],
    # A deck box with a goldfish sticker, left under a chair.
    "deckbox": [
        "kkkkkkk",
        "kbbbbbk",
        "kbffbbk",
        "kbbffbk",
        "kbbbbbk",
        "kkkkkkk",
    ],
    "register": [
        "..nnnnnnn.",
        "..nXXXXXn.",
        "..nnnnnnn.",
        "nnnnnnnnnn",
        "nNyNyNyNnn",
        "nnnnnnnnnn",
    ],
    "plant": [
        "..G.G..",
        ".GgGgG.",
        "GgGgGgG",
        ".gGgGg.",
        "..rrr..",
        "..rRr..",
        "..rrr..",
    ],
}


class Picture:
    def __init__(self, width=W, height=H):
        self.width, self.height = width, height
        self.parts = []

    def rect(self, x, y, w, h, colour):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{PALETTE[colour]}"/>')

    def sprite(self, name, x, y, scale=1):
        for row, line in enumerate(SPRITES[name]):
            col = 0
            while col < len(line):
                char = line[col]
                run = 1
                while col + run < len(line) and line[col + run] == char:
                    run += 1
                if char != ".":
                    self.rect(x + col * scale, y + row * scale, run * scale, scale, char)
                col += run

    def svg(self) -> str:
        body = "".join(self.parts)
        size = f'width="{self.width}" height="{self.height}"'
        return (f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 {self.width} {self.height}" {size} '
                f'shape-rendering="crispEdges">{body}</svg>\n')


def _shelf_of_boosters(pic, x, y, w):
    """A shelf with booster boxes standing on it, in turns of colour."""
    pic.rect(x, y + 14, w, 3, "t")
    pic.rect(x, y + 17, w, 1, "k")
    colours = ["r", "b", "g", "y", "R", "B", "G", "Y"]
    left = x + 2
    turn = 0
    while left + 9 <= x + w - 1:
        colour = colours[turn % len(colours)]
        pic.rect(left, y + 2, 8, 12, colour)
        pic.rect(left, y + 2, 8, 1, "k")
        pic.rect(left + 1, y + 5, 6, 2, "p")
        left += 10
        turn += 3


def floor() -> Picture:
    """The shop floor: door, booster wall, notice board, card case, counter, tables."""
    pic = Picture()
    # Back wall: panels above, wainscot below.
    pic.rect(0, 0, W, 112, "w")
    for x in range(0, W, 32):
        pic.rect(x, 0, 1, 80, "d")
    pic.rect(0, 80, W, 4, "t")
    pic.rect(0, 84, W, 28, "d")
    for x in range(4, W, 16):
        pic.rect(x, 88, 10, 20, "w")
    # Floor: boards with a few seams.
    pic.rect(0, 112, W, 68, "l")
    for y in range(118, H, 8):
        pic.rect(0, y, W, 1, "t")
    for y, xs in ((118, (40, 130, 220, 300)), (126, (10, 90, 170, 260)), (134, (60, 150, 240)),
                  (142, (20, 110, 200, 290)), (150, (70, 160, 250)), (158, (30, 120, 210, 310)),
                  (166, (80, 180, 270)), (174, (50, 140, 230))):
        for x in xs:
            pic.rect(x, y - 7, 1, 7, "t")
    # The door to the street, left: glass with the evening outside.
    pic.rect(8, 20, 44, 92, "k")
    pic.rect(11, 23, 38, 89, "t")
    pic.rect(14, 26, 32, 50, "o")
    pic.rect(15, 27, 14, 6, "B")  # a lit window across the street
    pic.rect(33, 40, 10, 4, "y")
    pic.rect(14, 50, 32, 1, "t")
    pic.rect(14, 80, 32, 28, "w")
    pic.rect(42, 88, 3, 6, "y")  # handle
    # The booster wall: three shelves.
    pic.rect(62, 8, 112, 70, "d")
    for y in (10, 32, 54):
        _shelf_of_boosters(pic, 64, y, 108)
    # The notice board, between the wall and the counter.
    pic.rect(184, 18, 40, 34, "t")
    pic.rect(186, 20, 36, 30, "q")
    pic.rect(189, 23, 13, 11, "p")
    pic.rect(190, 25, 11, 1, "k")
    pic.rect(190, 28, 8, 1, "k")
    pic.rect(205, 24, 14, 9, "Y")
    pic.rect(206, 26, 10, 1, "k")
    pic.rect(191, 37, 12, 10, "X")
    pic.rect(207, 36, 12, 11, "p")
    pic.rect(208, 39, 9, 1, "r")
    for x, y in ((195, 23), (211, 24), (196, 37), (212, 36)):
        pic.rect(x, y, 1, 1, "r")
    # The counter, right, standing on the floor, with the shopkeeper behind it.
    pic.sprite("keeper", 262, 62, scale=2)
    pic.rect(232, 88, 88, 4, "t")
    pic.rect(232, 92, 88, 34, "d")
    pic.rect(236, 96, 80, 26, "w")
    pic.rect(232, 126, 88, 2, "k")
    pic.sprite("register", 298, 76, scale=2)
    pic.sprite("plant", 238, 74, scale=2)
    # The glass case of singles, in front.
    pic.rect(96, 104, 90, 4, "t")
    pic.rect(96, 108, 90, 30, "x")
    pic.rect(98, 110, 86, 10, "X")
    for x in range(100, 182, 7):
        colour = "rbgyBRG"[(x // 7) % 7]
        pic.rect(x, 112, 5, 7, colour)
        pic.rect(x + 1, 113, 3, 3, "p")
        pic.rect(x, 124, 5, 7, "ygRbrGB"[(x // 7) % 7])
        pic.rect(x + 1, 125, 3, 3, "p")
    pic.rect(96, 138, 90, 10, "d")
    # Two play tables with chairs, lower right.
    for tx, ty in ((196, 138), (258, 150)):
        pic.rect(tx, ty, 50, 6, "g")
        pic.rect(tx, ty + 6, 50, 2, "k")
        pic.rect(tx + 3, ty + 8, 3, 18, "d")
        pic.rect(tx + 44, ty + 8, 3, 18, "d")
        pic.rect(tx + 10, ty + 2, 6, 3, "p")  # cards on the mat
        pic.rect(tx + 30, ty + 1, 5, 3, "r")
    for cx, cy in ((188, 142), (244, 156), (310, 154)):
        pic.rect(cx, cy, 8, 3, "t")
        pic.rect(cx, cy + 3, 2, 14, "d")
        pic.rect(cx + 6, cy + 3, 2, 14, "d")
    return pic


def deckbox() -> Picture:
    """The deck box as a prop the page shows until it is picked up."""
    pic = Picture(7, 6)
    pic.sprite("deckbox", 0, 0)
    return pic


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, draw in (("floor", floor), ("deckbox", deckbox)):
        (OUT / f"{name}.svg").write_text(draw().svg())
        print(f"wrote static/img/shop/{name}.svg")


if __name__ == "__main__":
    main()
