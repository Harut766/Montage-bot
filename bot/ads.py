"""Ad banners placed full-width, centred, on every rendered clip (VPN clipping contest rules 3.1–3.4)."""
from dataclasses import dataclass
from pathlib import Path

from .subtitles import OUT_H, OUT_W

ADS_DIR = Path(__file__).resolve().parent.parent / "ads"

# Rule 3.2: the banner must cover at least 25% of the screen. Aim well above the floor — the
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
    # Spill-suppression colour for the chromakey ("green" or "blue").
    despill_type: str = "green"


ADS = {
    # NATRIA VPN clipping programme (replaces the closed BubaVPN contest).
    # The banner is delivered on a GREEN screen (0,254,0): the green is keyed out so only the
    # card floats over the clip (rule 2.8 — no visible chromakey allowed).
    "natriavpn": Ad(
        "🟩 NATRIA VPN", ADS_DIR / "natriavpn.mp4",
        key_color="0x00FE00", similarity=0.22, blend=0.10,
    ),
}


def ad_start(clip_duration: float, ad_duration: float) -> float:
    """The banner plays centred in time: its middle falls on the middle of the clip."""
    return max(0.0, (clip_duration - ad_duration) / 2)


def banner_times(clip_duration: float, ad_duration: float) -> list[float]:
    """Start offsets for the banner(s) on a clip (NATRIA banner rules, 16 Oct 2026):

    * a clip up to 2:00 — ONE banner exactly in the middle;
    * a clip from 2:00 — TWO banners, at 0:30 and 1:30.

    Each start is clamped so the whole banner stays inside the clip."""
    if clip_duration < 120:
        return [ad_start(clip_duration, ad_duration)]
    times: list[float] = []
    for centre in (30.0, 90.0):
        t0 = centre - ad_duration / 2
        times.append(max(0.0, min(t0, clip_duration - ad_duration)))
    return times


def full_width_size(bw: int, bh: int) -> tuple[int, int]:
    """Scale the ad to the FULL screen width — edge to edge, no side crop (so the checker sees no
    gaps and no 'обрезка'). If its natural height at full width falls under TARGET_AREA, it is
    stretched vertically up to the target; a naturally taller banner keeps its own height."""
    w = OUT_W
    natural_h = round(w * bh / bw)
    target_h = round(TARGET_AREA * OUT_H)
    h = min(max(natural_h, target_h), int(MAX_H_FRAC * OUT_H))
    return max(2, w // 2 * 2), max(2, h // 2 * 2)
