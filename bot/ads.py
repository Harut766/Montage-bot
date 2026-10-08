"""Ad inserts on a chroma-key background, placed in the middle of every rendered clip."""
from dataclasses import dataclass
from pathlib import Path

ADS_DIR = Path(__file__).resolve().parent.parent / "ads"


@dataclass(frozen=True)
class Ad:
    title: str
    file: Path
    # Background colour that is keyed out (0xRRGGBB), and how close a pixel must be to it to vanish.
    key_color: str
    similarity: float = 0.30
    blend: float = 0.10
    # Width of the ad on the 1080x1920 frame.
    width: int = 960


ADS = {
    "bubavpn": Ad("🟦 BubaVPN", ADS_DIR / "bubavpn.mp4", key_color="0x0200F3"),
}


def ad_start(clip_duration: float, ad_duration: float) -> float:
    """The ad plays centred in time: its middle falls on the middle of the clip."""
    return max(0.0, (clip_duration - ad_duration) / 2)
