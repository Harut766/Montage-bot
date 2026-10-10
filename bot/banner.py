"""Animated banner placement following the BUBA VPN contest rules (3.1–3.4)."""
import math
from pathlib import Path

from .subtitles import OUT_H, OUT_W

# Banner must cover at least 25% of the screen; aim a bit higher so rounding never drops below.
BANNER_AREA = 0.28
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


def scaled_size(bw: int, bh: int) -> tuple[int, int]:
    """Scale the banner to ~BANNER_AREA of the screen, keeping aspect, without covering it all."""
    s = math.sqrt(BANNER_AREA * OUT_W * OUT_H / (bw * bh))
    max_w, max_h = OUT_W * 0.95, OUT_H * 0.6
    s = min(s, max_w / bw, max_h / bh)
    w = max(2, int(round(bw * s)) // 2 * 2)
    h = max(2, int(round(bh * s)) // 2 * 2)
    return w, h
