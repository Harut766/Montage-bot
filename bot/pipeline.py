"""Long video -> transcript -> clip selection -> rendered vertical clips."""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .clips import Clip, length_bounds, normalize_clips, split_by_time, split_evenly
from .config import Config
from .gemini import GeminiError, choose_fragments
from .render import extract_audio, probe, render_clip
from .subtitles import build_ass, make_layout
from .transcribe import Segment, transcribe

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]


class Cancelled(Exception):
    """The user asked to stop processing (/cancel)."""


@dataclass
class RenderedClip:
    path: Path
    part: int
    total: int
    clip: Clip


async def choose_clips(
    cfg: Config, segments: list[Segment], duration: float, target: int, language: str, progress: Progress
) -> list[Clip]:
    if cfg.gemini_api_key:
        await progress("🧠 Gemini выбирает интересные моменты…")
        try:
            raw = await choose_fragments(cfg.gemini_api_key, cfg.gemini_models, segments, duration, target, language)
            clips = normalize_clips(raw, segments, duration, target)
            if clips:
                return clips
            log.warning("Gemini returned no usable clips: %s", raw)
            reason = "Gemini не вернул фрагменты"
        except GeminiError as e:
            reason = str(e)
        except Exception as e:
            log.exception("Gemini request failed")
            reason = f"{type(e).__name__}: {e}"
        await progress(f"⚠️ {reason}. Режу видео на равные части.")
    return split_evenly(segments, duration, target)


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
    """mode: 'clips' (Gemini moments + subtitles), 'cut' (even split, no subtitles),
    'full' (whole video + subtitles, no cutting)."""
    info = await asyncio.to_thread(probe, src)
    layout = make_layout(info.width, info.height)

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
            clips = await choose_clips(cfg, segments, info.duration, target, language, progress)

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
        await asyncio.to_thread(render_clip, src, clip.start, clip.end, ass, layout, cfg.fonts_dir, out)
        yield RenderedClip(out, n, total, clip)
