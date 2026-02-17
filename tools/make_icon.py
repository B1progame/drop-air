from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def make_base(size: int, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))
    return img


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def draw_glyph(draw: ImageDraw.ImageDraw, size: int, color: tuple[int, int, int, int]) -> None:
    # Stylized share/air-drop arrow + signal arcs.
    w = max(10, size // 14)
    cx = size // 2
    top = int(size * 0.22)
    bottom = int(size * 0.72)

    draw.line([(cx, bottom), (cx, top + w)], fill=color, width=w)
    draw.polygon(
        [(cx, top), (cx - int(size * 0.12), top + int(size * 0.14)), (cx + int(size * 0.12), top + int(size * 0.14))],
        fill=color,
    )
    draw.arc(
        (int(size * 0.22), int(size * 0.45), int(size * 0.78), int(size * 0.94)),
        start=206,
        end=334,
        fill=color,
        width=max(6, w - 2),
    )
    draw.arc(
        (int(size * 0.30), int(size * 0.54), int(size * 0.70), int(size * 0.94)),
        start=210,
        end=330,
        fill=color,
        width=max(5, w - 3),
    )


def make_icon(style: str, out: Path) -> None:
    size = 1024
    if style == "neon":
        base = make_base(size, (5, 25, 46), (8, 70, 83))
        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.ellipse((130, 130, size - 130, size - 130), fill=(45, 212, 191, 80))
        glow = glow.filter(ImageFilter.GaussianBlur(42))
        base.alpha_composite(glow)
        fg = (207, 250, 254, 255)
        radius = 230
    elif style == "minimal":
        base = make_base(size, (226, 232, 240), (148, 163, 184))
        fg = (17, 24, 39, 255)
        radius = 210
    else:  # retro
        base = make_base(size, (51, 65, 85), (15, 23, 42))
        stripes = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(stripes)
        for i in range(-size, size, 42):
            sdraw.line([(i, 0), (i + size, size)], fill=(94, 234, 212, 48), width=16)
        base.alpha_composite(stripes)
        fg = (250, 250, 250, 255)
        radius = 210

    mask = rounded_rect_mask(size, radius=radius)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(base, (0, 0), mask=mask)

    glyph_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glyph_layer)
    draw_glyph(gdraw, size, fg)
    if style == "neon":
        glow = glyph_layer.filter(ImageFilter.GaussianBlur(16))
        canvas.alpha_composite(glow)
    canvas.alpha_composite(glyph_layer)

    ensure_parent(out)
    canvas.save(out, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Drop Air icon (.ico)")
    parser.add_argument("--style", choices=["neon", "minimal", "retro"], default="neon")
    parser.add_argument("--output", default="assets/icon/drop_air.ico")
    args = parser.parse_args()

    out = Path(args.output)
    make_icon(args.style, out)
    print(f"Icon written to: {out}")


if __name__ == "__main__":
    main()
