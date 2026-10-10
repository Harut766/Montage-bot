"""Long video -> transcript -> clip selection -> rendered vertical clips."""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .banner import find_banner, schedule
from .clips import Clip, split_by_time, split_evenly
from .config import Config
from .render import extract_audio, probe, render_clip
from .subtitles import build_ass, make_layout
from .transcribe import Segment, transcribe

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]

# Contest rule 1.4: a clip accepted for payout is 10–300 seconds.
MIN_CLIP_SECONDS = 10.0
MAX_CLIP_SECONDS = 300.0


class Cancelled(Exception):
    """The user asked to stop processing (/cancel)."""


@dataclass
class RenderedClip:
    path: Path
    part: int
    total: int
    clip: Clip


async def run(
    cfg: Config,
    src: Path,
    work: Path,
    mode: str,
    target: int | None,
    language: str,
    progress: Progress,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> AsyncIterator[RenderedClip]:
    """mode: 'clips' (even split on phrase boundaries + subtitles), 'cut' (even split, no subtitles),
    'full' (whole video + subtitles, no cutting)."""
    info = await asyncio.to_thread(probe, src)
    layout = make_layout(info.width, info.height)

    # Contest rule 1.4: each clip must be 10–300 seconds.
    if info.duration < MIN_CLIP_SECONDS:
        raise RuntimeError("Видео короче 10 секунд — такое не принимается на выплату")
    if mode == "full" and info.duration > MAX_CLIP_SECONDS:
        m, s = divmod(int(info.duration), 60)
        raise RuntimeError(
            f"Видео длиннее 5 минут ({m}:{s:02d}), а для субтитров на всё видео максимум 5 минут.\n"
            "Выбери режим нарезки — тогда ролик разобьётся на части по правилам."
        )

    if mode == "cut":
        # No speech-to-text, no subtitles — just fixed parts with a "Часть N" label.
        segments: list[Segment] = []
        clips = split_by_time(info.duration, target)
        label_lang = "ru"
    else:
        await progress("🎧 Расшифровываю речь…")
        audio = work / "audio.wav"
        await asyncio.to_thread(extract_audio, src, audio)
        segments = await asyncio.to_thread(
            transcribe, audio, language, cfg.whisper_model, cfg.whisper_device, is_cancelled
        )
        audio.unlink(missing_ok=True)
        if is_cancelled():
            raise Cancelled
        if not segments:
            raise RuntimeError("В видео не найдена речь")
        label_lang = language
        if mode == "full":
            # Subtitles over the whole video, one file, no splitting and no "Часть N".
            clips = [Clip(0.0, info.duration)]
        else:
            # Even split on phrase boundaries, each part gets subtitles and a "Часть N" label.
            clips = split_evenly(segments, info.duration, target)

    total = len(clips)
    show_label = mode != "full"
    for n, clip in enumerate(clips, start=1):
        if is_cancelled():
            raise Cancelled
        step = "🎬 Рендерю видео…" if mode == "full" else f"🎬 Рендерю часть {n}/{total}…"
        await progress(step)
        ass = work / f"part_{n:02d}.ass"
        ass.write_text(
            build_ass(segments, clip.start, clip.end, layout, n, label_lang, show_label), encoding="utf-8"
        )
        out = work / f"part_{n:02d}.mp4"
        # Animated banner centred on each clip, at the times the contest rules require.
        banner = find_banner(cfg.banner_path)
        banner_times = schedule(clip.duration) if banner else None
        await asyncio.to_thread(
            render_clip, src, clip.start, clip.end, ass, layout, cfg.fonts_dir, out, banner, banner_times
        )
        yield RenderedClip(out, n, total, clip)
