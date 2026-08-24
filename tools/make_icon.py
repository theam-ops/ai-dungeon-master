"""Draw the app icon: a d20, gold on ink, in the palette the web app already uses.

Run it to regenerate `static/favicon.ico` after changing the colours. Pillow is already
a dependency (the server uses it for uploaded images), so this needs nothing extra.

    python tools/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "static", "favicon.ico")

INK = (18, 16, 14, 255)
GOLD = (201, 162, 39, 255)
GOLD_DIM = (138, 114, 32, 255)
PARCHMENT = (232, 220, 200, 255)

SIZE = 512                      # drawn large, downsampled to each icon size
SIZES = [16, 24, 32, 48, 64, 128, 256]


def polygon(cx, cy, r, sides, rotation=0.0):
    return [(cx + r * math.cos(rotation + i * 2 * math.pi / sides),
             cy + r * math.sin(rotation + i * 2 * math.pi / sides))
            for i in range(sides)]


def draw():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = SIZE / 2

    # rounded ink tile, so the die reads against any wallpaper or tab colour
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=SIZE * 0.22, fill=INK)

    # the die: a pointy-top hexagon is the silhouette of an icosahedron
    outer = polygon(c, c, SIZE * 0.36, 6, rotation=-math.pi / 2)
    d.polygon(outer, fill=GOLD_DIM, outline=GOLD, width=int(SIZE * 0.022))

    # The top face, and the edges running from it to the lower corners: that inner
    # triangle is what makes a hexagon read as a d20 rather than a stop sign. It points
    # up and its corners run to the hexagon's alternating corners (top, lower-right,
    # lower-left) - line them up wrong and the whole thing reads as a pinwheel.
    inner = polygon(c, c, SIZE * 0.19, 3, rotation=-math.pi / 2)
    for i, (x, y) in enumerate(inner):
        ox, oy = outer[i * 2]
        d.line([(x, y), (ox, oy)], fill=INK, width=int(SIZE * 0.016))
    d.polygon(inner, fill=GOLD, outline=INK, width=int(SIZE * 0.016))

    return img


def main():
    img = draw()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    img.resize((256, 256), Image.LANCZOS).save(OUT.replace(".ico", ".png"))
    print(f"wrote {OUT} ({', '.join(str(s) for s in SIZES)})")


if __name__ == "__main__":
    main()
