#!/usr/bin/env python3
"""Generate SmatEconData raster brand assets.

Draws the same mark as ``packages/frontend/public/favicon.svg`` -- a warm
"Sunrise Research" tile with four resolving data columns and a query arc --
at each size the web app needs, so no artwork from any reference project
survives (spec sections 0G and 0Q).

Run:  python scripts/generate_brand_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "packages" / "frontend" / "public"

# Sunrise Research palette (spec 0G).
GROUND_TOP = (255, 242, 232)
GROUND_BOTTOM = (255, 217, 196)
CORAL = (242, 107, 79)
AMBER = (242, 184, 75)
TEAL = (42, 174, 155)
INK = (37, 49, 60)
WHITE = (255, 255, 255)

SS = 4  # supersampling factor for smooth edges


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rounded_gradient(size: int, radius_ratio: float = 0.26) -> Image.Image:
    """Warm diagonal-ish gradient tile with rounded corners."""
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            px[x, y] = _lerp(GROUND_TOP, GROUND_BOTTOM, (x + y) / (2 * size - 2))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=round(size * radius_ratio), fill=255
    )
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    return tile


def draw_mark(size: int) -> Image.Image:
    """Render the logomark at ``size`` px, supersampled then downscaled."""
    s = size * SS
    img = _rounded_gradient(s)
    d = ImageDraw.Draw(img)

    def sc(v: float) -> float:
        return v * s / 100.0

    # Four columns: the dataset assembling itself.
    columns = [
        (23, 58, 20, CORAL, 140),
        (38, 49, 29, CORAL, 190),
        (53, 38, 40, AMBER, 255),
        (68, 45, 33, TEAL, 255),
    ]
    for x, y, h, colour, alpha in columns:
        d.rounded_rectangle(
            [sc(x), sc(y), sc(x + 11), sc(y + h)],
            radius=sc(3),
            fill=colour + (alpha,),
        )

    # The middle column gets the coral->amber transition of the SVG gradient.
    d.rounded_rectangle(
        [sc(53), sc(38), sc(64), sc(58)], radius=sc(3), fill=CORAL + (255,)
    )

    # Query arc sweeping into the data.
    d.arc(
        [sc(16), sc(10), sc(84), sc(66)],
        start=203,
        end=331,
        fill=TEAL + (255,),
        width=round(sc(5)),
    )
    # Search node at the end of the arc.
    r = sc(7)
    d.ellipse([sc(78) - r, sc(27) - r, sc(78) + r, sc(27) + r],
              fill=WHITE + (255,), outline=TEAL + (255,), width=round(sc(5)))

    return img.resize((size, size), Image.LANCZOS)


def draw_og_image(width: int = 1200, height: int = 630) -> Image.Image:
    """Social preview card carrying the SmatEconData identity."""
    img = Image.new("RGB", (width, height), GROUND_TOP)
    px = img.load()
    for y in range(height):
        for x in range(0, width, 2):
            c = _lerp(GROUND_TOP, GROUND_BOTTOM, (x / width * 0.6 + y / height * 0.4))
            px[x, y] = c
            if x + 1 < width:
                px[x + 1, y] = c

    mark = draw_mark(220)
    img.paste(mark, (90, height // 2 - 150), mark)

    d = ImageDraw.Draw(img)

    def font(sz: int):
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, sz)
            except OSError:
                continue
        return ImageFont.load_default()

    def font_regular(sz: int):
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, sz)
            except OSError:
                continue
        return ImageFont.load_default()

    x0 = 360
    d.text((x0, height // 2 - 140), "SmatEconData", font=font(76), fill=INK)
    d.text((x0, height // 2 - 44),
           "Economic data, easier to find.",
           font=font_regular(38), fill=(102, 114, 125))
    d.text((x0, height // 2 + 20),
           "Search  ·  Build  ·  Understand  ·  Export",
           font=font_regular(30), fill=CORAL)
    d.text((x0, height // 2 + 96), "Dr Merwan Roudane",
           font=font_regular(28), fill=(102, 114, 125))

    d.rounded_rectangle([x0, height // 2 + 150, x0 + 250, height // 2 + 156],
                        radius=3, fill=TEAL)
    return img


def main() -> int:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    outputs = {
        "favicon-16x16.png": draw_mark(16),
        "favicon-32x32.png": draw_mark(32),
        "apple-touch-icon.png": draw_mark(180),
        "logo.png": draw_mark(512),
    }
    for name, img in outputs.items():
        path = PUBLIC / name
        img.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(REPO).as_posix()}  ({path.stat().st_size} bytes)")

    og = PUBLIC / "og-image.jpg"
    draw_og_image().save(og, "JPEG", quality=90, optimize=True)
    print(f"wrote {og.relative_to(REPO).as_posix()}  ({og.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
