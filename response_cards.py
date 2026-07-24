"""Image-card rendering helpers used by Response Discord messages."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 900, 280
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
)


def parse_color(value: str, fallback: str = "#5865F2") -> tuple[int, int, int]:
    try:
        clean = value.lstrip("#")
        if len(clean) != 6:
            raise ValueError
        return tuple(int(clean[index : index + 2], 16) for index in (0, 2, 4))
    except (AttributeError, TypeError, ValueError):
        return parse_color(fallback, "#5865F2")


def font(size: int, configured: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            candidates.append(candidate)
    candidates.extend(FONT_CANDIDATES)
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _gradient(start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    for x in range(WIDTH):
        ratio = x / max(1, WIDTH - 1)
        mixed = tuple(round(a + (b - a) * ratio) for a, b in zip(start, end, strict=True))
        for y in range(HEIGHT):
            shade = 0.72 + 0.28 * (1 - y / HEIGHT)
            pixels[x, y] = tuple(round(channel * shade) for channel in mixed)
    return image


def render_card(
    *,
    title: str,
    subtitle: str,
    detail: str,
    avatar: bytes | None = None,
    background: bytes | None = None,
    start_color: str = "#5865F2",
    end_color: str = "#9B59B6",
    text_color: str = "#FFFFFF",
    progress: float | None = None,
    configured_font: str = "",
) -> io.BytesIO:
    """Render a 900×280 PNG without touching the filesystem."""

    start, end = parse_color(start_color), parse_color(end_color)
    if background:
        try:
            canvas = _cover(Image.open(io.BytesIO(background)), (WIDTH, HEIGHT))
        except (OSError, ValueError):
            canvas = _gradient(start, end)
    else:
        canvas = _gradient(start, end)

    overlay = Image.new("RGBA", canvas.size, (7, 9, 17, 115))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    foreground = parse_color(text_color, "#FFFFFF")

    avatar_box = (50, 50, 230, 230)
    if avatar:
        try:
            profile = _cover(Image.open(io.BytesIO(avatar)), (180, 180)).convert("RGBA")
            mask = Image.new("L", profile.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 180, 180), fill=255)
            draw.ellipse((43, 43, 237, 237), fill=(*foreground, 55))
            canvas.paste(profile, avatar_box[:2], mask)
        except (OSError, ValueError):
            draw.ellipse(avatar_box, fill=(*foreground, 45))
    else:
        draw.ellipse(avatar_box, fill=(*foreground, 45))

    title_font = font(42, configured_font)
    body_font = font(24, configured_font)
    small_font = font(19, configured_font)
    draw.text((275, 58), title[:32], fill=(*foreground, 255), font=title_font)
    draw.text((278, 121), subtitle[:58], fill=(*foreground, 220), font=body_font)
    draw.text((278, 164), detail[:78], fill=(*foreground, 180), font=small_font)

    if progress is not None:
        ratio = min(1.0, max(0.0, progress))
        draw.rounded_rectangle((278, 210, 840, 230), radius=10, fill=(255, 255, 255, 45))
        if ratio:
            draw.rounded_rectangle(
                (278, 210, 278 + round(562 * ratio), 230), radius=10, fill=(*foreground, 235)
            )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    output.seek(0)
    return output
