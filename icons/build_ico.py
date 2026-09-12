"""Process the downloaded gramophone PNG: remove white background to alpha,
auto-crop to the subject, and build a multi-size .ico with padding."""

from pathlib import Path
from PIL import Image, ImageChops

HERE = Path(__file__).parent
SRC = HERE / "source_gramophone.png"
ICO = HERE.parent / "app_icon.ico"
PREVIEW = HERE / "icon_from_source_preview.png"

SIZES = [16, 24, 32, 48, 64, 128, 256]
TARGET = 256  # working size


def remove_white(img: Image.Image) -> Image.Image:
    """Treat near-white pixels as background -> transparent, with a soft edge."""
    rgba = img.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    # Classify white-ish (all channels >= 240) as background.
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if r >= 240 and g >= 240 and b >= 240:
                px[x, y] = (255, 255, 255, 0)
            elif r >= 210 and g >= 210 and b >= 210:
                # Semi-transparent for near-white anti-aliasing edges.
                a = int(255 * (1 - (255 - min(r, g, b)) / 60))
                px[x, y] = (r, g, b, max(0, min(255, a)))
    return rgba


def autocrop(img: Image.Image, pad_ratio: float = 0.06) -> Image.Image:
    bbox = img.getbbox()
    if not bbox:
        return img
    cropped = img.crop(bbox)
    # Re-center into a square canvas with padding.
    side = max(cropped.size)
    pad = int(side * pad_ratio)
    canvas_side = side + pad * 2
    out = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    ox = (canvas_side - cropped.size[0]) // 2
    oy = (canvas_side - cropped.size[1]) // 2
    out.paste(cropped, (ox, oy), cropped)
    return out


def main():
    src = Image.open(SRC)
    print(f"source: {src.size} {src.mode}")
    no_bg = remove_white(src)
    cropped = autocrop(no_bg)
    master = cropped.resize((TARGET, TARGET), Image.LANCZOS)
    master.save(PREVIEW)
    master.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"saved preview {PREVIEW.name} and ico {ICO.name} sizes={SIZES}")


if __name__ == "__main__":
    main()
