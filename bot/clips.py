"""Choosing clip boundaries: even split on phrase boundaries, or fixed-time cut."""
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


def _pad(start: float, end: float, duration: float) -> tuple[float, float]:
    return max(0.0, start - PAD_BEFORE), min(duration, end + PAD_AFTER)


def split_evenly(segments: list[Segment], duration: float, target: int) -> list[Clip]:
    """Consecutive parts of ~target seconds, cut between phrases so subtitles are not clipped."""
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


def split_by_time(duration: float, target: int) -> list[Clip]:
    """Cut mode: fixed parts of exactly `target` seconds, no transcript needed."""
    clips: list[Clip] = []
    start = 0.0
    while start < duration - 0.5:
        end = min(start + target, duration)
        clips.append(Clip(start, end))
        start = end
    # Fold a tiny trailing part into the previous one.
    if len(clips) > 1 and clips[-1].duration < min(target * 0.3, 10):
        clips[-2].end = clips[-1].end
        clips.pop()
    return clips
