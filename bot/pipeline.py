"""Long video -> transcript -> clip selection -> rendered vertical clips."""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .clips import Clip, length_bounds, normalize_clips, split_evenly
from .config import Config
from .render import extract_audio, probe, render_clip
from .subtitles import build_ass, make_layout
from .transcribe import Segment, transcribe

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]


@dataclass
class RenderedClip:
    path: Path
    part: int
    total: int
    clip: Clip


async def ask_n8n(cfg: Config, segments: list[Segment], duration: float, target: int, language: str) -> list[dict]:
    min_len, max_len = length_bounds(target)
    payload = {
        "language": language,
        "target_seconds": target,
        "min_seconds": round(min_len),
        "max_seconds": round(max_len),
        "video_duration": round(duration, 2),
        "transcript": [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text} for s in segments],
    }
    headers = {"X-Montage-Secret": cfg.n8n_secret} if cfg.n8n_secret else {}
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(cfg.n8n_webhook_url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    if isinstance(data, list):  # n8n may wrap the response in a list
        data = data[0] if data else {}
    return list(data.get("clips", []))


async def choose_clips(
    cfg: Config, segments: list[Segment], duration: float, target: int, language: str, progress: Progress
) -> list[Clip]:
    if cfg.n8n_webhook_url:
        await progress("🧠 Gemini выбирает интересные моменты…")
        try:
            raw = await ask_n8n(cfg, segments, duration, target, language)
            clips = normalize_clips(raw, segments, duration, target)
            if clips:
                return clips
            log.warning("n8n returned no usable clips: %s", raw)
        except Exception:
            log.exception("n8n request failed")
        await progress("⚠️ n8n/Gemini не ответил, режу видео на равные части.")
    return split_evenly(segments, duration, target)


async def run(
    cfg: Config, src: Path, work: Path, target: int, language: str, progress: Progress
) -> AsyncIterator[RenderedClip]:
    info = await asyncio.to_thread(probe, src)
    layout = make_layout(info.width, info.height)

    await progress("🎧 Расшифровываю речь…")
    audio = work / "audio.wav"
    await asyncio.to_thread(extract_audio, src, audio)
    segments = await asyncio.to_thread(transcribe, audio, language, cfg.whisper_model, cfg.whisper_device)
    audio.unlink(missing_ok=True)
    if not segments:
        raise RuntimeError("В видео не найдена речь")

    clips = await choose_clips(cfg, segments, info.duration, target, language, progress)
    total = len(clips)
    for n, clip in enumerate(clips, start=1):
        await progress(f"🎬 Рендерю часть {n}/{total}…")
        ass = work / f"part_{n:02d}.ass"
        ass.write_text(build_ass(segments, clip.start, clip.end, layout, n, language), encoding="utf-8")
        out = work / f"part_{n:02d}.mp4"
        await asyncio.to_thread(render_clip, src, clip.start, clip.end, ass, layout, cfg.fonts_dir, out)
        yield RenderedClip(out, n, total, clip)
