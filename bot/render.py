"""FFmpeg helpers: probe, audio extraction and rendering of a vertical clip."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ads import Ad, ad_start
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


def has_audio(path: Path) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)])
    return bool(out.strip())


def extract_audio(src: Path, dst: Path) -> None:
    _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])


def render_clip(
    src: Path, start: float, end: float, ass_file: Path, layout: Layout, fonts_dir: Path, out: Path,
    ad: Ad | None = None,
) -> None:
    """Blurred copy of the frame as a 9:16 background, the original on top, subtitles burned in.

    With `ad`, its chroma-key background is removed and the ad plays in the centre of the frame,
    in the middle of the clip; the clip's own sound is turned down while the ad speaks.
    The ASS file is referenced by a bare name relative to its directory to avoid filter-escaping issues.
    """
    duration = end - start
    inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src.resolve())]
    chains = [
        "[0:v]split=2[a][b]",
        "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        f"gblur=sigma=10,eq=brightness=-0.08,scale={OUT_W}:{OUT_H},setsar=1[bg]",
        f"[b]scale={layout.fg_w}:{layout.fg_h},setsar=1[fg]",
        f"[bg][fg]overlay=(W-w)/2:{layout.fg_y}[base]",
    ]
    video = "[base]"
    audio_map = ["-map", "0:a?"]
    if ad:
        inputs += ["-i", str(ad.file.resolve())]
        ad_dur = probe(ad.file).duration
        t0 = ad_start(duration, ad_dur)
        chains += [
            f"[1:v]colorkey={ad.key_color}:{ad.similarity}:{ad.blend},despill=type=blue,"
            f"scale={ad.width}:-2,setpts=PTS-STARTPTS+{t0:.3f}/TB[ad]",
            "[base][ad]overlay=(W-w)/2:(H-h)/2:eof_action=pass[withad]",
        ]
        video = "[withad]"
        if has_audio(ad.file):
            delay = int(t0 * 1000)
            if has_audio(src):
                chains += [
                    f"[0:a]volume=0.3:enable='between(t,{t0:.3f},{t0 + ad_dur:.3f})'[ma]",
                    f"[1:a]adelay={delay}:all=1[aa]",
                    "[ma][aa]amix=inputs=2:duration=first:normalize=0[aout]",
                ]
            else:
                chains.append(f"[1:a]adelay={delay}:all=1,apad[aout]")
            audio_map = ["-map", "[aout]"]
    chains.append(f"{video}ass={ass_file.name}:fontsdir={fonts_dir.resolve()},fps=30,format=yuv420p[v]")
    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[v]", *audio_map,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=ass_file.parent)
