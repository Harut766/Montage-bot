"""Ad inserts placed full-width, centred, on every rendered clip (BUBA VPN contest rules 3.1–3.4)."""
from dataclasses import dataclass
from pathlib import Path

from .subtitles import OUT_H, OUT_W

ADS_DIR = Path(__file__).resolve().parent.parent / "ads"

# Rule 3.2: the ad must cover at least 25% of the screen; aim higher (checker has ±2 p.p. error).
MIN_AREA = 0.25
TARGET_AREA = 0.27
# A too-wide ad is centre-cropped to this aspect so it fills TARGET_AREA at full screen width.
TARGET_ASPECT = OUT_W / (TARGET_AREA * OUT_H)
MAX_CROP_FRAC = 0.15
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


def crop_box(bw: int, bh: int) -> tuple[int, int, int, int] | None:
    """Horizontal centre-crop so the ad can fill TARGET_AREA at full width, or None if not needed."""
    if bw / bh <= TARGET_ASPECT + 1e-3:
        return None
    cw = max(bh * TARGET_ASPECT, bw * (1 - MAX_CROP_FRAC))
    cw = min(int(cw) // 2 * 2, bw)
    return cw, bh, (bw - cw) // 2, 0


def full_width_size(bw: int, bh: int) -> tuple[int, int]:
    """Scale the (already cropped) ad to the full screen width — edge to edge."""
    w = OUT_W
    h = round(w * bh / bw)
    max_h = int(MAX_H_FRAC * OUT_H)
    if h > max_h:
        h = max_h
        w = round(h * bw / bh)
    return max(2, w // 2 * 2), max(2, h // 2 * 2)
