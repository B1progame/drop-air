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


def draw_pixel_drop(size: int) -> Image.Image:
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)

    qr_size = int(size * 0.55)
    qr_left = (size - qr_size) // 2
    qr_top = int(size * 0.16)
    cell = qr_size // 13
    qr_size = cell * 13
    qr_left = (size - qr_size) // 2
    qr_right = qr_left + qr_size
    qr_bottom = qr_top + qr_size

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.rounded_rectangle(
        (qr_left - cell, qr_top - cell, qr_right + cell, qr_bottom + cell),
        radius=cell * 2,
        fill=(45, 212, 191, 78),
    )
    glyph.alpha_composite(glow.filter(ImageFilter.GaussianBlur(22)))

    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(plate)
    pdraw.rounded_rectangle(
        (qr_left - cell // 2, qr_top - cell // 2, qr_right + cell // 2, qr_bottom + cell // 2),
        radius=cell,
        fill=(5, 38, 52, 170),
        outline=(125, 249, 255, 95),
        width=max(2, cell // 8),
    )
    glyph.alpha_composite(plate)

    modules = {
        (6, 1), (8, 1), (10, 1),
        (5, 2), (6, 2), (9, 2), (11, 2),
        (6, 3), (8, 3), (10, 3), (12, 3),
        (1, 5), (3, 5), (5, 5), (7, 5), (11, 5),
        (2, 6), (5, 6), (6, 6), (8, 6), (10, 6), (12, 6),
        (1, 7), (4, 7), (7, 7), (9, 7), (11, 7),
        (5, 8), (6, 8), (8, 8), (12, 8),
        (1, 9), (3, 9), (5, 9), (9, 9), (10, 9),
        (6, 10), (8, 10), (11, 10), (12, 10),
        (5, 11), (7, 11), (9, 11), (10, 11),
        (6, 12), (8, 12), (12, 12),
    }

    def draw_module(col: int, row: int, fill: tuple[int, int, int, int]) -> None:
        x0 = qr_left + col * cell
        y0 = qr_top + row * cell
        inset = max(2, cell // 10)
        draw.rounded_rectangle(
            (x0 + inset, y0 + inset, x0 + cell - inset, y0 + cell - inset),
            radius=max(2, cell // 6),
            fill=fill,
        )

    def draw_finder(col: int, row: int) -> None:
        x = qr_left + col * cell
        y = qr_top + row * cell
        outer = (x, y, x + cell * 4, y + cell * 4)
        inner = (x + cell, y + cell, x + cell * 3, y + cell * 3)
        core = (x + cell + cell // 2, y + cell + cell // 2, x + cell * 3 - cell // 2, y + cell * 3 - cell // 2)
        draw.rounded_rectangle(outer, radius=max(3, cell // 4), fill=(190, 255, 255, 255))
        draw.rounded_rectangle(inner, radius=max(2, cell // 5), fill=(3, 28, 43, 255))
        draw.rounded_rectangle(core, radius=max(2, cell // 6), fill=(94, 234, 212, 255))

    def in_finder_zone(col: int, row: int) -> bool:
        return (
            0 <= col <= 3 and 0 <= row <= 3
        ) or (
            9 <= col <= 12 and 0 <= row <= 3
        ) or (
            0 <= col <= 3 and 9 <= row <= 12
        )

    for col, row in modules:
        if in_finder_zone(col, row):
            continue
        t = row / 12
        fill = (
            int(189 * (1 - t) + 34 * t),
            int(255 * (1 - t) + 211 * t),
            int(255 * (1 - t) + 238 * t),
            255,
        )
        draw_module(col, row, fill)

    draw_finder(0, 0)
    draw_finder(9, 0)
    draw_finder(0, 9)

    arc_color = (94, 234, 212, 245)
    arc_width = max(8, size // 46)
    draw.arc(
        (int(size * 0.28), int(size * 0.68), int(size * 0.72), int(size * 0.96)),
        start=202,
        end=338,
        fill=arc_color,
        width=arc_width,
    )
    draw.arc(
        (int(size * 0.38), int(size * 0.77), int(size * 0.62), int(size * 0.96)),
        start=207,
        end=333,
        fill=(207, 250, 254, 245),
        width=max(7, arc_width - 7),
    )

    dot = size // 20
    draw.ellipse(
        (size // 2 - dot // 2, int(size * 0.83), size // 2 + dot // 2, int(size * 0.83) + dot),
        fill=(125, 249, 255, 255),
    )
    return glyph


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
    elif style == "pixel-drop":
        base = make_base(size, (2, 10, 25), (4, 48, 63))
        bloom = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bloom)
        bdraw.ellipse((92, 76, size - 92, size - 82), fill=(20, 184, 166, 82))
        bdraw.ellipse((278, 230, size - 278, size - 126), fill=(125, 249, 255, 58))
        base.alpha_composite(bloom.filter(ImageFilter.GaussianBlur(52)))
        shine = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        shdraw = ImageDraw.Draw(shine)
        shdraw.rounded_rectangle((72, 58, size - 72, size - 70), radius=210, outline=(148, 255, 244, 38), width=7)
        base.alpha_composite(shine)
        fg = (207, 250, 254, 255)
        radius = 228
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
    if style == "pixel-drop":
        glyph_layer = draw_pixel_drop(size)
        glow = glyph_layer.filter(ImageFilter.GaussianBlur(18))
        canvas.alpha_composite(glow)
    else:
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
    parser.add_argument("--style", choices=["neon", "minimal", "retro", "pixel-drop"], default="pixel-drop")
    parser.add_argument("--output", default="assets/icon/drop_air.ico")
    args = parser.parse_args()

    out = Path(args.output)
    make_icon(args.style, out)
    print(f"Icon written to: {out}")


if __name__ == "__main__":
    main()
