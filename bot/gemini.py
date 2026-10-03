"""Picking the most engaging fragments of a transcript with Gemini."""
import asyncio
import json
import logging

import aiohttp

from .clips import length_bounds
from .transcribe import Segment

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ATTEMPTS_PER_MODEL = 2
RETRY_DELAY = 5
# Overloaded / rate limited / server errors: worth retrying or trying the next model.
TRANSIENT = {429, 500, 502, 503, 504}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "title": {"type": "STRING"},
                },
                "required": ["start", "end", "title"],
            },
        }
    },
    "required": ["clips"],
}


class GeminiError(Exception):
    pass


def build_prompt(segments: list[Segment], duration: float, target: int, language: str) -> str:
    min_len, max_len = length_bounds(target)
    max_clips = max(1, int(duration // target))
    transcript = "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segments)
    return f"""You are an experienced TikTok editor. Below is a timestamped transcript of a vlog / interactive video
(language: {language}, duration: {duration:.0f} s).

Pick the most engaging fragments that work as standalone TikTok videos:
- each fragment is {min_len:.0f}-{max_len:.0f} seconds long, ideally about {target} s;
- it starts with a strong hook (question, intrigue, emotion, a bold statement) and ends on a finished thought;
- start and end exactly on phrase boundaries from the transcript;
- fragments must not overlap;
- return from 1 to {max_clips} fragments, quality over quantity, in chronological order;
- title: a short catchy caption for the post (up to 60 characters) in the video's language.

Transcript:
{transcript}"""


def parse_response(data: dict) -> list[dict]:
    candidates = data.get("candidates") or []
    if not candidates or not candidates[0].get("content"):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or (
            candidates[0].get("finishReason") if candidates else "пустой ответ"
        )
        raise GeminiError(f"Gemini: {reason}")
    text = "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []))
    try:
        clips = json.loads(text).get("clips")
    except (json.JSONDecodeError, AttributeError) as e:
        raise GeminiError(f"не удалось разобрать ответ Gemini: {e}") from e
    return clips if isinstance(clips, list) else []


async def choose_fragments(
    api_key: str, models: list[str], segments: list[Segment], duration: float, target: int, language: str
) -> list[dict]:
    """Tries each model in turn, retrying transient errors; raises GeminiError with the last reason."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": build_prompt(segments, duration, target, language)}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    headers = {"x-goog-api-key": api_key}
    last_error = "нет моделей"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=240)) as session:
        for model in models:
            for attempt in range(ATTEMPTS_PER_MODEL):
                try:
                    async with session.post(API_URL.format(model=model), json=body, headers=headers) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status == 200:
                            return parse_response(data)
                        message = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
                        last_error = f"Gemini ({model}): {resp.status} {message}".strip()
                        log.warning(last_error)
                        if resp.status not in TRANSIENT:
                            break  # e.g. bad key or unknown model: retrying will not help
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = f"Gemini ({model}): {type(e).__name__} {e}"
                    log.warning(last_error)
                if attempt + 1 < ATTEMPTS_PER_MODEL:
                    await asyncio.sleep(RETRY_DELAY)
    raise GeminiError(last_error)
