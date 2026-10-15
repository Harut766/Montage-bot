"""Live mode (Facebook lives and other long videos): plain parts of 10/15/20 minutes, vertical 9:16 with an
ambient background video (campfire, leaves, …) above and below, and the sound a random 106–130% louder.
No subtitles, labels or banner."""
import random
from pathlib import Path

from .render import _run
from .subtitles import OUT_H, OUT_W, make_layout

LIVE_LENGTHS = (600, 900, 1200)
# Original loudness is 100%; every part gets a random level above 105% and up to 130%.
MIN_PERCENT = 106
MAX_PERCENT = 130
BG_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".gif")


def pick_percent() -> int:
    return random.randint(MIN_PERCENT, MAX_PERCENT)


def has_audio(src: Path) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(src)])
    return bool(out.strip())


def find_backgrounds(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in BG_EXTS)


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
    """The original video centred on a 1080×1920 frame; the background video is looped to fill the rest.
    Without a background, a blurred copy of the frame is used instead."""
    lay = make_layout(src_w, src_h)
    dur = end - start
    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    fg = f"[0:v]scale={lay.fg_w}:{lay.fg_h},setsar=1[fg]"
    if background:
        inputs += ["-stream_loop", "-1", "-i", str(background)]
        bg = (
            f"[1:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},setsar=1,fps=30[bg]"
        )
        graph = f"{bg};{fg};[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=30,format=yuv420p[v]"
    else:
        graph = (
            "[0:v]split=2[a][b];"
            "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            f"gblur=sigma=10,eq=brightness=-0.08,scale={OUT_W}:{OUT_H},setsar=1[bg];"
            f"[b]scale={lay.fg_w}:{lay.fg_h},setsar=1[fg];"
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
        # Parts are 10–20 minutes long: a faster preset keeps hour-long lives from taking all day.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
        "-movflags", "+faststart",
        str(out),
    ])
