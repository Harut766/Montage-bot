import shutil
import subprocess
from pathlib import Path

import pytest

from bot.clips import split_by_time, split_evenly
from bot.render import probe, render_clip
from bot.subtitles import build_ass, group_words, make_layout
from bot.transcribe import Segment, Word

FONTS = Path(__file__).resolve().parent.parent / "fonts"


def fake_segments(total: float, seg_len: float = 6.0) -> list[Segment]:
    segments, t = [], 0.0
    while t + seg_len <= total:
        words = [Word(t + i, t + i + 0.8, w) for i, w in enumerate(["Привет", "это", "тестовый", "влог", "друзья."])]
        segments.append(Segment(t, t + 5.0, " ".join(w.text for w in words), words))
        t += seg_len
    return segments


def test_split_evenly_respects_bounds():
    segs = fake_segments(600)
    clips = split_evenly(segs, 600, 60)
    assert len(clips) >= 8
    for c in clips:
        assert 45 <= c.duration <= 76
    assert all(a.end <= b.start for a, b in zip(clips, clips[1:]))


def test_split_by_time_fixed_parts():
    clips = split_by_time(130, 60)
    assert [(round(c.start), round(c.end)) for c in clips] == [(0, 60), (60, 120), (120, 130)]
    # A tiny trailing part is folded into the previous one.
    clips = split_by_time(63, 60)
    assert len(clips) == 1 and round(clips[0].end) == 63


def test_layout_horizontal_keeps_text_off_video():
    lay = make_layout(1920, 1080)
    assert (lay.fg_w, lay.fg_h) == (1080, 608)
    assert lay.label_y < lay.fg_y
    assert lay.subs_y > lay.fg_y + lay.fg_h


def test_group_words_limits():
    words = [Word(i * 0.3, i * 0.3 + 0.25, "слово") for i in range(10)]
    for g in group_words(words):
        assert len(g) <= 3


def test_ass_contains_part_label_and_relative_times():
    segs = fake_segments(120)
    ass = build_ass(segs, 60.0, 120.0, make_layout(1920, 1080), 2, "ru")
    assert "Часть 2" in ass
    assert "Dialogue: 0,0:00:00.00" in ass


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_end_to_end(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-shortest",
         "-c:v", "libx264", "-c:a", "aac", str(src)],
        check=True, capture_output=True,
    )
    info = probe(src)
    layout = make_layout(info.width, info.height)
    segs = fake_segments(20)
    ass = tmp_path / "part_01.ass"
    ass.write_text(build_ass(segs, 2.0, 14.0, layout, 1, "ru"), encoding="utf-8")
    out = tmp_path / "part_01.mp4"
    render_clip(src, 2.0, 14.0, ass, layout, FONTS, out)
    res = probe(out)
    assert (res.width, res.height) == (1080, 1920)
    assert 11.5 <= res.duration <= 12.5


def test_sweep_tg_storage_keeps_db_and_fresh_files(tmp_path):
    import os
    import time

    from bot.cleanup import sweep_tg_storage

    bot_dir = tmp_path / "123:token"
    (bot_dir / "videos").mkdir(parents=True)
    old_video = bot_dir / "videos" / "file_1.mp4"
    new_video = bot_dir / "videos" / "file_2.mp4"
    db = bot_dir / "td.binlog"
    for f in (old_video, new_video, db):
        f.write_bytes(b"x")
    old = time.time() - 10 * 3600
    os.utime(old_video, (old, old))
    os.utime(db, (old, old))

    assert sweep_tg_storage(tmp_path, 6) == 1
    assert not old_video.exists()
    assert new_video.exists() and db.exists()



def test_banner_schedule_matches_rules():
    from bot.banner import schedule, scaled_size
    from bot.subtitles import OUT_H, OUT_W

    assert schedule(60) == [30.0]          # ≤2:00 → one in the middle
    assert schedule(120) == [60.0]
    assert schedule(180) == [30.0, 90.0, 150.0]   # 2:00–3:00 → three
    assert schedule(300) == [30.0, 90.0, 150.0, 210.0, 270.0]
    for bw, bh in [(600, 600), (1920, 1080), (1080, 1920)]:
        w, h = scaled_size(bw, bh)
        assert w * h / (OUT_W * OUT_H) >= 0.25   # rule 3.2: at least 25% of the screen
        assert w <= OUT_W and h <= OUT_H
