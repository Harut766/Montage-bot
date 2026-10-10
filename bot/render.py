"""FFmpeg helpers: probe, audio extraction and rendering of a vertical clip."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .subtitles import OUT_H, OUT_W, Layout


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr[-2000:]}")
    return proc.stdout


def probe(path: Path) -> VideoInfo:
    out = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    stream = data["streams"][0]
    w, h = int(stream["width"]), int(stream["height"])
    rotation = stream.get("tags", {}).get("rotate")
    for side in stream.get("side_data_list", []):
        rotation = side.get("rotation", rotation)
    # Phone videos are often stored landscape with a rotation flag; ffmpeg auto-rotates on decode.
    if rotation is not None and abs(int(float(rotation))) % 180 == 90:
        w, h = h, w
    return VideoInfo(w, h, float(data["format"]["duration"]))


def extract_audio(src: Path, dst: Path) -> None:
    _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])


def render_clip(
    src: Path,
    start: float,
    end: float,
    ass_file: Path,
    layout: Layout,
    fonts_dir: Path,
    out: Path,
    banner: Path | None = None,
    banner_times: list[float] | None = None,
) -> None:
    """Blurred copy of the frame as a 9:16 background, the original on top, subtitles burned in,
    and (if given) an animated banner centred on screen at each time in `banner_times`.

    The ASS file is referenced by a bare name relative to its directory to avoid filter-escaping issues.
    """
    from .banner import STILL_SECONDS, key_color, scaled_size

    base = (
        "[0:v]split=2[a][b];"
        "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        f"gblur=sigma=10,eq=brightness=-0.08,scale={OUT_W}:{OUT_H},setsar=1[bg];"
        f"[b]scale={layout.fg_w}:{layout.fg_h},setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:{layout.fg_y},"
        f"ass={ass_file.name}:fontsdir={fonts_dir.resolve()},fps=30,format=yuv420p[v0]"
    )

    inputs = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src.resolve())]
    parts = [base]
    last = "[v0]"

    times = banner_times or []
    if banner and times:
        info = probe(banner)
        bw, bh = scaled_size(info.width, info.height)
        is_still = banner.suffix.lower() in (".png", ".jpg", ".jpeg")
        bdur = STILL_SECONDS if is_still else info.duration
        # Remove the chromakey background (rule 2.8) so only the banner card is overlaid.
        ck = key_color(banner)
        key = f"colorkey={ck}:0.20:0.10," if ck else ""
        for i, t0 in enumerate(times):
            idx = i + 1  # input 0 is the source clip
            if is_still:
                inputs += ["-loop", "1", "-t", f"{bdur:.3f}", "-i", str(banner.resolve())]
            else:
                inputs += ["-i", str(banner.resolve())]
            t1 = t0 + bdur
            parts.append(
                f"[{idx}:v]{key}scale={bw}:{bh},setsar=1,setpts=PTS-STARTPTS+{t0:.3f}/TB[bn{i}]"
            )
            nxt = f"[v{i + 1}]"
            parts.append(
                f"{last}[bn{i}]overlay=(W-w)/2:(H-h)/2:enable='between(t,{t0:.3f},{t1:.3f})':"
                f"eof_action=pass{nxt}"
            )
            last = nxt

    graph = ";".join(parts)
    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", graph,
        "-map", last, "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=ass_file.parent)
