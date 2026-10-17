"""Montage mode: take several short vertical clips, chop them into fast cuts and recombine them
into N different vertical montages (each a different ordering of shots). The clips keep their own
sound — the audio follows the cuts, which gives a raw club / FOMO feel."""
import random
from pathlib import Path

from .render import _run, has_audio
from .subtitles import OUT_H, OUT_W

# Fast cuts: every shot is a fraction of a second, so the montage feels synced to an up-tempo track.
MIN_SEG = 0.7
MAX_SEG = 1.4
MAX_SEGMENTS = 60  # safety cap so one montage never explodes into hundreds of tiny cuts
COUNTS = (2, 3, 5)      # how many different montages to return
LENGTHS = (15, 30)      # seconds per montage


def montage_segments(durations: list[float], target: int, seed: int) -> list[tuple[int, float, float]]:
    """Pick (clip_index, start, duration) shots until they fill ~target seconds.

    The same clip is not used twice in a row (when there is more than one), and `seed` makes every
    montage a different arrangement."""
    rng = random.Random(seed)
    segs: list[tuple[int, float, float]] = []
    total = 0.0
    while total < target and len(segs) < MAX_SEGMENTS:
        i = rng.randrange(len(durations))
        if segs and segs[-1][0] == i and len(durations) > 1:
            continue
        dur = durations[i]
        seg = min(rng.uniform(MIN_SEG, MAX_SEG), dur)
        start = 0.0 if dur <= seg else rng.uniform(0.0, dur - seg)
        segs.append((i, start, seg))
        total += seg
    return segs


def render_montage(srcs: list[Path], segments: list[tuple[int, float, float]], out: Path) -> None:
    """Cut every shot from its source, scale it to a 1080×1920 vertical frame and concatenate them
    all into one montage. Each shot keeps its own audio; the audio is concatenated with the video."""
    inputs: list[str] = []
    for s in srcs:
        inputs += ["-i", str(s.resolve())]
    all_audio = all(has_audio(s) for s in srcs)
    chains: list[str] = []
    labels: list[str] = []
    for j, (i, start, dur) in enumerate(segments):
        chains.append(
            f"[{i}:v]trim=start={start:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS,"
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},setsar=1,fps=30,format=yuv420p[v{j}]"
        )
        if all_audio:
            chains.append(
                f"[{i}:a]atrim=start={start:.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo[a{j}]"
            )
            labels.append(f"[v{j}][a{j}]")
        else:
            labels.append(f"[v{j}]")
    n = len(segments)
    if all_audio:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[v][a]")
        amap = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "44100"]
    else:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[v]")
        amap = ["-map", "[v]"]
    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(chains),
        *amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
        "-movflags", "+faststart",
        str(out.resolve()),
    ])
