"""Speech-to-text with word-level timestamps (faster-whisper, runs locally)."""
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


@lru_cache(maxsize=1)
def _model(name: str, device: str):
    from faster_whisper import WhisperModel

    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe(
    audio: Path, language: str, model_name: str, device: str, should_stop: Callable[[], bool] = lambda: False
) -> list[Segment]:
    model = _model(model_name, device)
    raw_segments, _ = model.transcribe(
        str(audio),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    segments = []
    # Segments are decoded lazily, so checking between them lets /cancel stop a long transcription.
    for s in raw_segments:
        if should_stop():
            break
        words = [Word(w.start, w.end, w.word.strip()) for w in (s.words or []) if w.word.strip()]
        if words:
            segments.append(Segment(s.start, s.end, s.text.strip(), words))
    return segments
