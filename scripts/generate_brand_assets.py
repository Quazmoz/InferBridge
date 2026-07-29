"""Generate deterministic Windows icon assets for release packaging."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

_CANVAS = 512
_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _mix(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int, int]:
    return tuple(round(a + (b - a) * amount) for a, b in zip(left, right, strict=True)) + (255,)


def render_brand_icon(size: int = _CANVAS) -> Image.Image:
    """Render the application mark at ``size`` pixels using only Pillow primitives."""

    if size < 16:
        raise ValueError("Icon size must be at least 16 pixels.")

    image = Image.new("RGBA", (_CANVAS, _CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (18, 18, 494, 494),
        radius=108,
        fill=(5, 12, 24, 255),
        outline=(42, 60, 84, 255),
        width=8,
    )

    bubble_mask = Image.new("L", (_CANVAS, _CANVAS), 0)
    mask_draw = ImageDraw.Draw(bubble_mask)
    mask_draw.rounded_rectangle((92, 105, 420, 338), radius=68, fill=255)
    mask_draw.polygon(((180, 323), (180, 407), (268, 330)), fill=255)

    gradient = Image.new("RGBA", (_CANVAS, _CANVAS), (0, 0, 0, 0))
    gradient_pixels = gradient.load()
    start = (14, 165, 233)
    end = (99, 102, 241)
    for x in range(_CANVAS):
        amount = x / (_CANVAS - 1)
        color = _mix(start, end, amount)
        for y in range(_CANVAS):
            gradient_pixels[x, y] = color
    image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), bubble_mask))

    draw = ImageDraw.Draw(image)
    spark = (
        (256, 151),
        (277, 211),
        (337, 232),
        (277, 253),
        (256, 313),
        (235, 253),
        (175, 232),
        (235, 211),
    )
    draw.polygon(spark, fill=(255, 255, 255, 255))
    draw.ellipse((347, 155, 371, 179), fill=(255, 255, 255, 230))
    draw.ellipse((145, 288, 165, 308), fill=(255, 255, 255, 210))

    if size == _CANVAS:
        return image
    return image.resize((size, size), Image.Resampling.LANCZOS)


def generate_brand_assets(output_directory: Path) -> tuple[Path, Path]:
    """Write a 512px PNG and a multi-resolution Windows ICO file."""

    output_directory.mkdir(parents=True, exist_ok=True)
    png_path = output_directory / "OpenVINOWindowsLLM.png"
    ico_path = output_directory / "OpenVINOWindowsLLM.ico"

    icon = render_brand_icon()
    icon.save(png_path, format="PNG", optimize=True)
    icon.save(ico_path, format="ICO", sizes=[(size, size) for size in _ICON_SIZES])

    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError("PNG brand asset generation failed.")
    if not ico_path.is_file() or ico_path.stat().st_size == 0:
        raise RuntimeError("ICO brand asset generation failed.")
    return png_path, ico_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args(argv)
    png_path, ico_path = generate_brand_assets(args.output_directory.resolve())
    print(f"Generated {png_path}")
    print(f"Generated {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
