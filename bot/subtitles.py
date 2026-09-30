"""Vertical 1080x1920 layout and ASS subtitles (white text, black outline, Montserrat ExtraBold)."""
from dataclasses import dataclass

from .transcribe import Segment, Word

OUT_W, OUT_H = 1080, 1920
FONT_NAME = "Montserrat ExtraBold"

MAX_WORDS = 3
MAX_CHARS = 18
PAUSE_BREAK = 0.5
HOLD_AFTER = 0.2

PART_LABEL = {"ru": "Часть {n}", "en": "Part {n}"}


@dataclass
class Layout:
    """Where the source video sits on the blurred 9:16 background, and where the text goes."""
    fg_w: int
    fg_h: int
    fg_y: int
    subs_y: int
    label_y: int


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def make_layout(src_w: int, src_h: int) -> Layout:
    fg_w, fg_h = OUT_W, _even(OUT_W * src_h / src_w)
    if fg_h > OUT_H:
        fg_w, fg_h = _even(OUT_H * src_w / src_h), OUT_H
    fg_y = (OUT_H - fg_h) // 2
    below = OUT_H - (fg_y + fg_h)
    # Keep both texts on the blurred area when there is room, and out of TikTok's top/bottom UI.
    subs_y = min(fg_y + fg_h + 110, 1480) if below >= 400 else 1380
    label_y = max(fg_y // 2, 230) if fg_y >= 250 else 230
    return Layout(fg_w, fg_h, fg_y, subs_y, label_y)


def _ts(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    return text.rstrip(",.;:…").strip()


def group_words(words: list[Word]) -> list[list[Word]]:
    """Short phrases of up to MAX_WORDS words, broken at pauses and sentence punctuation."""
    groups: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            chars = sum(len(x.text) + 1 for x in current) + len(w.text)
            if (
                len(current) >= MAX_WORDS
                or chars > MAX_CHARS
                or w.start - current[-1].end > PAUSE_BREAK
                or current[-1].text[-1:] in ".!?…"
            ):
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def build_ass(
    segments: list[Segment],
    clip_start: float,
    clip_end: float,
    layout: Layout,
    part: int,
    language: str,
) -> str:
    words = [w for s in segments for w in s.words if w.end > clip_start and w.start < clip_end]
    groups = group_words(words)
    clip_len = clip_end - clip_start

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {OUT_W}",
        f"PlayResY: {OUT_H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Subs,{FONT_NAME},78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,"
        "0,0,0,0,100,100,0,0,1,5,2,5,60,60,0,1",
        f"Style: Part,{FONT_NAME},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,"
        "0,0,0,0,100,100,1,0,1,4,2,5,60,60,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    label = PART_LABEL.get(language, PART_LABEL["en"]).format(n=part)
    lines.append(f"Dialogue: 1,{_ts(0)},{_ts(clip_len)},Part,,0,0,0,,{{\\pos(540,{layout.label_y})}}{label}")

    for k, group in enumerate(groups):
        start = max(group[0].start - clip_start, 0.0)
        end = group[-1].end - clip_start + HOLD_AFTER
        if k + 1 < len(groups):
            next_start = groups[k + 1][0].start - clip_start
            # Close small gaps so the text does not flicker between phrases.
            end = next_start if next_start - end < 0.4 else end
            end = min(end, next_start)
        end = min(end, clip_len)
        if end - start < 0.05:
            continue
        text = " ".join(t for t in (_clean(w.text) for w in group) if t)
        if not text:
            continue
        pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}"
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Subs,,0,0,0,,{{\\pos(540,{layout.subs_y})}}{pop}{text}"
        )
    return "\n".join(lines) + "\n"
