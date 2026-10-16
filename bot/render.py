"""FFmpeg helpers: probe, audio extraction and rendering of a vertical clip."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ads import Ad
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

    With `ad`, banners are laid EDGE TO EDGE (full screen width, no side crop), centred, covering
    ~30% of the screen (contest rule 3.2). A short clip gets one banner in the middle; a clip from
    2:00 gets one in the middle of each full minute (rules 3.3–3.4). A chromakey background is
    removed only when the ad defines one. The clip keeps its own audio at full volume so the
    voice-over stays clearly audible (rule 2.3); each banner's own audio is mixed in softly during
    its window, and the banners carry the sound on their own when the source clip has none — so the
    result is never silent. The ASS file is referenced by a bare name relative to its directory to
    avoid filter-escaping issues.
    """
    from .ads import banner_times, full_width_size

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
        info = probe(ad.file)
        times = banner_times(duration, info.duration)
        aw, ah = full_width_size(info.width, info.height)
        key = (
            f"colorkey={ad.key_color}:{ad.similarity}:{ad.blend},despill=type={ad.despill_type},"
            if ad.key_color else ""
        )
        ad_has_audio = has_audio(ad.file)
        src_has_audio = has_audio(src)
        prev = "[base]"
        ad_audio: list[str] = []
        # Each banner is a fresh input so the same file can be overlaid at several times.
        for i, t0 in enumerate(times):
            idx = i + 1  # input 0 is the source
            inputs += ["-i", str(ad.file.resolve())]
            vlabel, outlabel = f"[ad{i}]", f"[v{i}]"
            chains.append(
                f"[{idx}:v]{key}scale={aw}:{ah},setsar=1,setpts=PTS-STARTPTS+{t0:.3f}/TB{vlabel}"
            )
            chains.append(f"{prev}{vlabel}overlay=(W-w)/2:(H-h)/2:eof_action=pass{outlabel}")
            prev = outlabel
            if ad_has_audio:
                delay = int(t0 * 1000)
                # The banner plays at full volume so its own sound is heard clearly while it is on.
                chains.append(f"[{idx}:a]adelay={delay}|{delay}[ba{i}]")
                ad_audio.append(f"[ba{i}]")
        video = prev
        if ad_audio:
            if src_has_audio:
                # While a banner is on screen the clip's own sound DUCKS right down so the banner
                # is heard; the rest of the time the clip plays at full volume.
                windows = "+".join(f"between(t,{t:.3f},{t + info.duration:.3f})" for t in times)
                chains.append(f"[0:a]volume=0.2:enable='{windows}'[sa]")
                parts = ["[sa]", *ad_audio]
            else:
                # Source clip has no sound at all — the banners carry it.
                parts = ad_audio
            if len(parts) == 1:
                chains.append(f"{parts[0]}alimiter=limit=0.95[aout]")
            else:
                chains.append(
                    f"{''.join(parts)}amix=inputs={len(parts)}:duration=first:normalize=0,"
                    "alimiter=limit=0.95[aout]"
                )
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
