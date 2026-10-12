"""Volume boost: the original video with its sound made a random 5–30% louder, picture untouched."""
import random
from pathlib import Path

from .render import _run

# Original loudness is 100%; every video gets a random level above 105% and up to 130%.
MIN_PERCENT = 106
MAX_PERCENT = 130


def pick_percent() -> int:
    return random.randint(MIN_PERCENT, MAX_PERCENT)


def has_audio(src: Path) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(src)])
    return bool(out.strip())


def boost_volume(src: Path, dst: Path, percent: int) -> None:
    """Video stream is copied as is (no re-encode, no quality loss); only the audio is re-encoded.
    A limiter with auto-levelling off keeps loud peaks from clipping into crackle after the boost."""
    if not has_audio(src):
        raise RuntimeError("В видео нет звука — нечего делать громче")
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy",
        "-af", f"volume={percent / 100:.2f},alimiter=limit=0.98:level=disabled",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ])
