"""Generate a refined vintage gramophone app icon (PNG).

Style: classic florabell-horn gramophone — flared flower-petal brass horn
opening upward-right, wooden cabinet base, side crank, small turntable.
Warm gold + deep brown palette on a cream rounded-square backdrop.

Uses Pillow only.
"""

from __future__ import annotations

import math
from PIL import Image, ImageDraw, ImageFilter

SIZE = 256

GOLD_DARK = (168, 116, 46, 255)
GOLD_MID = (214, 162, 74, 255)
GOLD_LIGHT = (244, 206, 120, 255)
RIM_COLOR = (232, 186, 104, 255)


def canvas():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def draw_florabell_horn(d):
    """Draw the flared brass horn opening toward upper area, neck down to base.

    The horn is built as stacked ellipses (a petal-flare silhouette) plus
    highlight bands, then a dark mouth ring for depth.
    """
    # Horn axis: from neck (lower-left, near base top) to bell mouth (upper-right).
    # Bell center & mouth ellipse.
    bcx, bcy = 158, 92          # bell mouth center
    mw, mh = 86, 78             # mouth half-extents (slightly wider than tall)

    # Outer petal bell: concentric ellipses, gold gradient from edge (dark) to center (light).
    gold_dark = (168, 116, 46, 255)
    gold_mid = (214, 162, 74, 255)
    gold_light = (244, 206, 120, 255)

    rings = 9
    for i in range(rings, 0, -1):
        t = i / rings
        ew = mw * t
        eh = mh * t
        col = lerp(GOLD_LIGHT, GOLD_DARK, t)
        d.ellipse([bcx - ew, bcy - eh, bcx + ew, bcy + eh], fill=col)

    # Petal scallops around the mouth rim (8 petals) to give the flower look.
    petal_n = 8
    petal_r = 13
    for k in range(petal_n):
        ang = (k / petal_n) * 2 * math.pi - math.pi / 2
        px = bcx + (mw + 4) * math.cos(ang)
        py = bcy + (mh + 4) * math.sin(ang)
        d.ellipse([px - petal_r, py - petal_r, px + petal_r, py + petal_r], fill=RIM_COLOR)

    # Re-cap the center after petals so they read as rim bumps.
    d.ellipse([bcx - mw, bcy - mh, bcx + mw, bcy + mh], fill=GOLD_MID)
    # Mouth opening (dark inside).
    d.ellipse([bcx - mw + 16, bcy - mh + 16, bcx + mw - 16, bcy + mh - 16], fill=(96, 60, 28, 255))
    d.ellipse([bcx - mw + 22, bcy - mh + 22, bcx + mw - 22, bcy + mh - 22], fill=(70, 42, 20, 255))

    # Neck: a tapered tube from bell down to the base (upper-left direction).
    # Polygon from bell bottom-left to base top.
    neck = [
        (bcx - 40, bcy + 30),
        (bcx - 18, bcy + 8),
        (96, 150),
        (84, 168),
    ]
    d.polygon(neck, fill=GOLD_MID)
    # Neck highlight.
    d.line([(bcx - 28, bcy + 18), (90, 158)], fill=GOLD_LIGHT, width=5)
    # Neck shadow edge.
    d.line([(bcx - 10, bcy + 6), (100, 150)], fill=GOLD_DARK, width=3)


def draw_base(d):
    """Wooden cabinet base with a small turntable and crank."""
    wood_dark = (96, 56, 28, 255)
    wood_mid = (130, 78, 40, 255)
    wood_light = (160, 100, 56, 255)

    # Cabinet body (trapezoid-ish, rounded).
    d.rounded_rectangle([44, 150, 212, 214], radius=16, fill=wood_mid)
    # Top plate (lighter).
    d.rounded_rectangle([48, 146, 208, 160], radius=8, fill=wood_light)
    # Wood grain lines.
    for gy in (172, 184, 196):
        d.line([60, gy, 196, gy], fill=wood_dark, width=2)
    # Feet.
    for fx in (58, 198):
        d.rounded_rectangle([fx - 8, 210, fx + 8, 222], radius=4, fill=wood_dark)

    # Turntable (platter) peeking on top.
    tcx, tcy, tr = 110, 150, 26
    d.ellipse([tcx - tr, tcy - tr, tcx + tr, tcy + tr], fill=(38, 38, 44, 255))
    for rr in (22, 17, 12):
        d.ellipse([tcx - rr, tcy - rr, tcx + rr, tcy + rr], outline=(54, 54, 62, 255), width=1)
    d.ellipse([tcx - 7, tcy - 7, tcx + 7, tcy + 7], fill=(214, 74, 58, 255))

    # Crank on the right side of the cabinet.
    d.line([200, 182, 222, 182], fill=wood_dark, width=6)
    d.ellipse([218, 176, 232, 190], fill=GOLD_MID, outline=wood_dark, width=2)


def make():
    img, d = canvas()
    # Cream rounded backdrop with subtle border.
    d.rounded_rectangle([6, 6, 250, 250], radius=46, fill=(245, 237, 218, 255))
    d.rounded_rectangle([6, 6, 250, 250], radius=46, outline=(214, 200, 168, 255), width=3)

    draw_base(d)
    draw_florabell_horn(d)

    # Soft overall AA by downscaling from 2x supersample.
    big = img.resize((SIZE * 2, SIZE * 2), Image.LANCZOS).filter(ImageFilter.SMOOTH)
    # Re-draw isn't needed; instead supersample up front for crispness on save.
    img.save("icon_gramophone.png")
    img.resize((128, 128), Image.LANCZOS).save("icon_gramophone_preview.png")
    return img


if __name__ == "__main__":
    make()
    print("Generated: icon_gramophone.png (+ preview)")
