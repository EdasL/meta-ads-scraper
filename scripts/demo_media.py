"""Tiny media generator for the demo seeder (scripts/seed_demo.py only).

Produces REAL bytes for creatives so the local UI shows actual ad media via the
Go media proxy (which streams objects straight from MinIO by their s3_key):

  - image creatives  -> a branded PNG tile (Pillow): per-advertiser background
                        color + the app name + the creative title text.
  - video creatives  -> a ~3s MP4 (ffmpeg) of that tile, IF ffmpeg is on PATH;
                        otherwise the caller falls back to a PNG poster.

Nothing here touches the DB or S3 directly — it just returns bytes. Upload and
bucket handling live in the seeder via agf.storage.s3.put_bytes / get_s3.
"""
from __future__ import annotations

import colorsys
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TILE = (1080, 1080)
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def ffmpeg_available() -> bool:
    """True if a usable ffmpeg binary is on PATH (used to gate real MP4 output)."""
    return shutil.which("ffmpeg") is not None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _bg_color(seed: str) -> tuple[int, int, int]:
    """Stable, distinct, reasonably dark background color derived from a seed
    (so each advertiser gets its own brand-ish tile)."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    hue = (h % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.32, 0.62)  # darkish, saturated
    return int(r * 255), int(g * 255), int(b * 255)


def _wrap(draw, text_value: str, font, max_width: int) -> list[str]:
    words = text_value.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_tile_png(seed: str, app_name: str, title: str) -> bytes:
    """Render a branded PNG tile: per-seed background + bold app name + wrapped
    title text. Returns PNG bytes."""
    img = Image.new("RGB", TILE, _bg_color(seed))
    draw = ImageDraw.Draw(img)
    margin = 90
    max_w = TILE[0] - 2 * margin

    name_sz, title_sz = 96, 58
    name_font = _font(name_sz)
    title_font = _font(title_sz)

    # app name near the top
    name_lines = _wrap(draw, app_name or "Field Desk", name_font, max_w)
    y = margin
    for line in name_lines:
        draw.text((margin, y), line, font=name_font, fill=(255, 255, 255))
        y += int(name_sz * 1.15)

    # rule
    y += 30
    draw.line([(margin, y), (TILE[0] - margin, y)], fill=(255, 255, 255), width=4)
    y += 50

    # creative title, wrapped, slightly translucent white
    for line in _wrap(draw, title or "", title_font, max_w):
        draw.text((margin, y), line, font=title_font, fill=(235, 235, 235))
        y += int(title_sz * 1.3)

    # small footer mark
    foot = _font(40)
    draw.text((margin, TILE[1] - margin - 40), "Field Desk demo",
              font=foot, fill=(210, 210, 210))

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_clip_mp4(seed: str, app_name: str, title: str, *, seconds: int = 3) -> bytes:
    """Loop the branded tile into a ~`seconds` MP4 via ffmpeg. Raises if ffmpeg
    is missing/fails — caller decides the PNG fallback."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not on PATH")
    png = render_tile_png(seed, app_name, title)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "poster.png"
        out = Path(td) / "clip.mp4"
        src.write_bytes(png)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(src),
            "-t", str(seconds),
            "-r", "24",
            # yuv420p + even dims keep the clip playable in every browser
            "-vf", "scale=720:720,format=yuv420p",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return out.read_bytes()
