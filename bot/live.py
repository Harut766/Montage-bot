"""YouTube mode (Facebook lives and other long videos): plain parts of 10/15/20 minutes, HORIZONTAL
16:9 for a regular YouTube upload — the source (often a vertical phone clip) sits centred and a
background video fills the sides (a provided ambient clip, or a blurred copy of the source). The
sound is a random 106–130% louder. No subtitles, labels or banner."""
import random
from pathlib import Path

from .render import _run

# Regular YouTube videos are horizontal 16:9.
FRAME_W, FRAME_H = 1920, 1080
LIVE_LENGTHS = (600, 900, 1200)
# Original loudness is 100%; every part gets a random level above 105% and up to 130%.
MIN_PERCENT = 106
MAX_PERCENT = 130
BG_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".gif")
# The centred person fills this share of the width; the background shows on the sides. A vertical
# source is zoomed to this width (its top/bottom cropped), so the person is big and the side bands
# are narrow — the rest of the width is background.
FG_W_FRAC = 0.55
# When the zoom crops a vertical source, keep more of the top (head/face) than the bottom.
CROP_TOP_BIAS = 0.3


def live_fg_size(src_w: int, src_h: int) -> tuple[int, int]:
    """Size of the centred source box: a fixed share of the width, full height. The source is
    scaled to cover this box (keeping its aspect) and the overflow is cropped, so the person is
    wide with narrow side bands."""
    w = int(FG_W_FRAC * FRAME_W)
    return max(2, w // 2 * 2), FRAME_H


def pick_percent() -> int:
    return random.randint(MIN_PERCENT, MAX_PERCENT)


def capped_duration(duration: float, limit: int | None) -> float:
    """How much of the source to cut: the whole thing, or only the first `limit` seconds
    (lives can run for hours, so the user caps how much is processed)."""
    return min(duration, limit) if limit else duration


def has_audio(src: Path) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(src)])
    return bool(out.strip())


def find_backgrounds(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in BG_EXTS)


def extract_thumb(video: Path, out: Path) -> None:
    """A small preview frame so the user can see what each background looks like before picking."""
    _run(["ffmpeg", "-y", "-ss", "0.5", "-i", str(video), "-frames:v", "1", "-vf", "scale=480:-2", str(out)])


def render_live_part(
    src: Path,
    start: float,
    end: float,
    src_w: int,
    src_h: int,
    background: Path | None,
    percent: int,
    with_audio: bool,
    out: Path,
) -> None:
    """The original video centred on a 1920×1080 frame; the background video is looped to fill the
    sides. Without a background, a blurred copy of the frame is used instead."""
    fg_w, fg_h = live_fg_size(src_w, src_h)
    dur = end - start
    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    # Fill the centre box without distortion: scale to cover it, then crop the overflow (biased to
    # keep the top, so a talking head's face stays in frame).
    fg_scale = (
        f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase,"
        f"crop={fg_w}:{fg_h}:(iw-ow)/2:(ih-oh)*{CROP_TOP_BIAS},setsar=1"
    )
    fg = f"[0:v]{fg_scale}[fg]"
    if background:
        inputs += ["-stream_loop", "-1", "-i", str(background)]
        bg = (
            f"[1:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,"
            f"crop={FRAME_W}:{FRAME_H},setsar=1,fps=30[bg]"
        )
        graph = f"{bg};{fg};[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=30,format=yuv420p[v]"
    else:
        graph = (
            "[0:v]split=2[a][b];"
            "[a]scale=480:270:force_original_aspect_ratio=increase,crop=480:270,"
            f"gblur=sigma=10,eq=brightness=-0.08,scale={FRAME_W}:{FRAME_H},setsar=1[bg];"
            f"[b]{fg_scale}[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p[v]"
        )
    audio: list[str] = []
    if with_audio:
        # A limiter with auto-levelling off keeps loud peaks from clipping into crackle after the boost.
        graph += f";[0:a]volume={percent / 100:.2f},alimiter=limit=0.98:level=disabled[a]"
        audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", graph,
        "-map", "[v]", *audio,
        "-t", f"{dur:.3f}",
        # Parts are 10–20 minutes long and encoded on CPU: superfast keeps hour-long lives from taking
        # all day, and the bitrate cap keeps a part well under the 2 GB limit so the upload stays quick
        # and does not strain memory on a small machine.
        "-c:v", "libx264", "-preset", "superfast", "-crf", "21",
        "-maxrate", "7M", "-bufsize", "14M", "-profile:v", "high",
        "-movflags", "+faststart",
        str(out),
    ])
