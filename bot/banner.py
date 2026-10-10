"""Animated banner placement following the BUBA VPN contest rules (3.1–3.4)."""
import math
import subprocess
from pathlib import Path

from .subtitles import OUT_H, OUT_W

# Rule 3.2: banner covers at least 25% of the screen.
BANNER_AREA = 0.25
# A banner may be scaled up to the full screen width and this fraction of its height.
MAX_W_FRAC = 1.0
MAX_H_FRAC = 0.72
# How long a banner stays on screen when the source is a still image (animated files play their own length).
STILL_SECONDS = 5.0
BANNER_NAMES = ("banner.mp4", "banner.mov", "banner.webm", "banner.gif", "banner.png", "banner.jpg")


def find_banner(path: Path) -> Path | None:
    """`path` is either the banner file itself or a directory that may contain banner.*."""
    if path.is_file():
        return path
    if path.is_dir():
        for name in BANNER_NAMES:
            cand = path / name
            if cand.is_file():
                return cand
    return None


def schedule(duration: float) -> list[float]:
    """Banner start times for a clip of `duration` seconds.

    Rule 3.4: up to 2:00 → one banner exactly in the middle; 2:00–3:00 → 0:30, 1:30, 2:30;
    longer → one more every 60 s (i.e. 0:30, 1:30, 2:30, 3:30 …).
    """
    if duration <= 120:
        return [round(duration / 2, 2)]
    times = []
    t = 30.0
    while t < duration:
        times.append(t)
        t += 60.0
    return times


def key_color(banner: Path) -> str | None:
    """Sample the banner's top-left pixel as the chromakey colour (0xRRGGBB), or None on failure.

    The corner of a chromakey banner is always the background, so this works for blue or green.
    """
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(banner), "-frames:v", "1",
             "-vf", "crop=2:2:2:2,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if len(raw) < 3:
        return None
    r, g, b = raw[0], raw[1], raw[2]
    # Only treat a strongly saturated corner as a chromakey; a dark/neutral corner means no key.
    if max(r, g, b) - min(r, g, b) < 60:
        return None
    return f"0x{r:02X}{g:02X}{b:02X}"


def scaled_size(bw: int, bh: int) -> tuple[int, int]:
    """Scale the banner to at least BANNER_AREA of the screen, keeping aspect.

    A very wide banner cannot reach 25% within the screen width; then it is scaled as large as
    it fits (max coverage). `covers_enough` reports whether the 25% rule is actually met.
    """
    s_area = math.sqrt(BANNER_AREA * OUT_W * OUT_H / (bw * bh))
    s_fit = min(MAX_W_FRAC * OUT_W / bw, MAX_H_FRAC * OUT_H / bh)
    s = min(s_area, s_fit)
    w = max(2, int(round(bw * s)) // 2 * 2)
    h = max(2, int(round(bh * s)) // 2 * 2)
    return w, h


def covers_enough(bw: int, bh: int) -> bool:
    w, h = scaled_size(bw, bh)
    # 0.5% tolerance for even-size rounding.
    return w * h >= BANNER_AREA * OUT_W * OUT_H * 0.995
