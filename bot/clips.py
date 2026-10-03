"""Choosing clip boundaries: clean up what Gemini returns, or split evenly as a fallback."""
from dataclasses import dataclass

from .transcribe import Segment

MAX_CLIP_SECONDS = 180.0
PAD_BEFORE = 0.15
PAD_AFTER = 0.35


@dataclass
class Clip:
    start: float
    end: float
    title: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def length_bounds(target: int) -> tuple[float, float]:
    return target * 0.8, min(target * 1.25, MAX_CLIP_SECONDS)


def _nearest(values: list[float], x: float) -> int:
    return min(range(len(values)), key=lambda i: abs(values[i] - x))


def _fit(segments: list[Segment], i: int, j: int, min_len: float, max_len: float) -> tuple[int, int]:
    """Grow or shrink the segment range [i, j] until its length is within bounds."""
    while segments[j].end - segments[i].start < min_len and j + 1 < len(segments):
        j += 1
    while segments[j].end - segments[i].start < min_len and i > 0:
        i -= 1
    while segments[j].end - segments[i].start > max_len and j > i:
        j -= 1
    return i, j


def _pad(start: float, end: float, duration: float) -> tuple[float, float]:
    return max(0.0, start - PAD_BEFORE), min(duration, end + PAD_AFTER)


def normalize_clips(raw: list[dict], segments: list[Segment], duration: float, target: int) -> list[Clip]:
    """Snap LLM-proposed ranges to phrase boundaries, enforce length, drop overlaps, sort chronologically."""
    if not segments:
        return []
    min_len, max_len = length_bounds(target)
    starts = [s.start for s in segments]
    ends = [s.end for s in segments]
    candidates = []
    for item in raw:
        try:
            a, b = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        i, j = _nearest(starts, a), _nearest(ends, b)
        if j < i:
            j = i
        i, j = _fit(segments, i, j, min_len, max_len)
        candidates.append(Clip(segments[i].start, segments[j].end, str(item.get("title") or "").strip()))

    candidates.sort(key=lambda c: c.start)
    result: list[Clip] = []
    for c in candidates:
        if result and c.start < result[-1].end:
            continue
        result.append(c)
    for c in result:
        c.start, c.end = _pad(c.start, c.end, duration)
    return result


def split_evenly(segments: list[Segment], duration: float, target: int) -> list[Clip]:
    """Fallback when Gemini is unavailable: consecutive parts of ~target seconds, cut between phrases."""
    if not segments:
        return []
    min_len, max_len = length_bounds(target)
    groups: list[list[Segment]] = [[]]
    for seg in segments:
        group = groups[-1]
        if group and seg.end - group[0].start > max_len:
            groups.append([seg])
            continue
        group.append(seg)
        if seg.end - group[0].start >= target:
            groups.append([])
    groups = [g for g in groups if g]
    if len(groups) > 1 and groups[-1][-1].end - groups[-1][0].start < min_len:
        merged = groups[-2] + groups[-1]
        if merged[-1].end - merged[0].start <= max_len:
            groups[-2:] = [merged]
    clips = []
    for g in groups:
        start, end = _pad(g[0].start, g[-1].end, duration)
        clips.append(Clip(start, end))
    return clips
