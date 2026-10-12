"""Ad inserts placed full-width, centred, on every rendered clip (BUBA VPN contest rules 3.1–3.4)."""
from dataclasses import dataclass
from pathlib import Path

from .subtitles import OUT_H, OUT_W

ADS_DIR = Path(__file__).resolve().parent.parent / "ads"

# Rule 3.2: the ad must cover at least 25% of the screen. Aim well above the floor — the BUBA
# checker rounds/estimates and warns when the area is "близко к границе", so 30% leaves room.
MIN_AREA = 0.25
TARGET_AREA = 0.30
# Never let the banner grow taller than this share of the screen.
MAX_H_FRAC = 0.72


@dataclass(frozen=True)
class Ad:
    title: str
    file: Path
    # Chromakey colour to remove (0xRRGGBB); None for a solid design that is overlaid as is.
    key_color: str | None = None
    similarity: float = 0.30
    blend: float = 0.10


ADS = {
    # Solid full-frame design — no chromakey, laid edge to edge.
    "bubavpn": Ad("🟦 BubaVPN", ADS_DIR / "bubavpn.webm"),
}


def ad_start(clip_duration: float, ad_duration: float) -> float:
    """The ad plays centred in time: its middle falls on the middle of the clip."""
    return max(0.0, (clip_duration - ad_duration) / 2)


def full_width_size(bw: int, bh: int) -> tuple[int, int]:
    """Scale the ad to the FULL screen width — edge to edge, no side crop (so the checker sees no
    gaps and no 'обрезка'). If its natural height at full width falls under TARGET_AREA, it is
    stretched vertically up to the target; a naturally taller banner keeps its own height."""
    w = OUT_W
    natural_h = round(w * bh / bw)
    target_h = round(TARGET_AREA * OUT_H)
    h = min(max(natural_h, target_h), int(MAX_H_FRAC * OUT_H))
    return max(2, w // 2 * 2), max(2, h // 2 * 2)
