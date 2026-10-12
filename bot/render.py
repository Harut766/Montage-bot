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

    With `ad`, it is laid EDGE TO EDGE (full screen width, no side crop), centred, in the middle of
    the clip, covering ~30% of the screen (contest rule 3.2). A chromakey background is removed only
    when the ad defines one. The clip keeps its own audio at full volume so the voice-over stays
    clearly audible (rule 2.3); the banner's own audio is mixed in softly during its window, and it
    carries the sound on its own when the source clip has none — so the result is never silent.
    The ASS file is referenced by a bare name relative to its directory to avoid filter-escaping issues.
    """
    from .ads import full_width_size

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
        info = probe(ad.file)
        t0 = ad_start(duration, info.duration)
        aw, ah = full_width_size(info.width, info.height)
        key = f"colorkey={ad.key_color}:{ad.similarity}:{ad.blend},despill=type=blue," if ad.key_color else ""
        chains += [
            f"[1:v]{key}scale={aw}:{ah},setsar=1,setpts=PTS-STARTPTS+{t0:.3f}/TB[ad]",
            "[base][ad]overlay=(W-w)/2:(H-h)/2:eof_action=pass[withad]",
        ]
        video = "[withad]"
        if has_audio(ad.file):
            delay = int(t0 * 1000)
            ad_end = t0 + info.duration
            if has_audio(src):
                # Voice stays near full volume; the banner's audio is mixed in softly in its window.
                chains += [
                    f"[0:a]volume=0.8:enable='between(t,{t0:.3f},{ad_end:.3f})'[sa]",
                    f"[1:a]volume=0.4,adelay={delay}|{delay}[ba]",
                    "[sa][ba]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]",
                ]
            else:
                # Source clip has no sound at all — carry the banner's audio so it is never silent.
                chains.append(f"[1:a]adelay={delay}|{delay}[aout]")
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
