import shutil
import subprocess
from pathlib import Path

import pytest

from bot.ads import ADS, ad_start
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



def test_ad_is_centred_in_time():
    assert ad_start(60, 5) == 27.5
    assert ad_start(3, 5) == 0.0


def test_ad_full_width_no_crop():
    from bot.ads import MIN_AREA, full_width_size
    from bot.subtitles import OUT_H, OUT_W

    # The BubaVPN banner (1282x552) is laid full width, no side crop, stretched to clear 25%.
    w, h = full_width_size(1282, 552)
    assert w == OUT_W                                  # edge to edge, no gaps / no crop
    assert w * h / (OUT_W * OUT_H) >= MIN_AREA          # rule 3.2, with margin
    assert w * h / (OUT_W * OUT_H) >= 0.29              # comfortably above the 25% border
    # A naturally tall banner keeps its own (bigger) height instead of being shrunk to the target.
    assert full_width_size(1000, 1000)[1] == 1080


def test_outfit_compose_geometry(monkeypatch):
    import io
    import sys
    import types

    from PIL import Image

    # Stub rembg so the layout is tested without downloading the 170 MB model.
    stub = types.ModuleType("rembg")
    stub.new_session = lambda name: object()
    stub.remove = lambda data, session=None: data  # already a PNG with alpha
    monkeypatch.setitem(sys.modules, "rembg", stub)

    from bot import outfit

    s = outfit.LookSession()
    s.background = Image.new("RGB", (800, 600), (200, 180, 160))
    s.items["top"] = Image.new("RGB", (400, 500), (50, 80, 200))
    s.items["shoes"] = Image.new("RGB", (300, 200), (200, 50, 50))
    data = outfit.compose_look(s)
    out = Image.open(io.BytesIO(data))
    assert out.size == (outfit.CANVAS_W, outfit.CANVAS_H)
    assert out.mode == "RGB"
    # Every category has a position and a draw slot.
    assert set(outfit.CATEGORIES) == set(outfit.LAYOUT) == set(outfit.DRAW_ORDER)


def test_pick_percent_range():
    from bot.live import MAX_PERCENT, MIN_PERCENT, pick_percent

    values = {pick_percent() for _ in range(2000)}
    assert min(values) > 105 and max(values) <= 130
    assert MIN_PERCENT in values and MAX_PERCENT in values


def test_live_fg_size_always_leaves_background():
    from bot.live import MAX_FG_FRAC, live_fg_size
    from bot.subtitles import OUT_H, OUT_W

    # A vertical reel must NOT fill the whole height, so the background stays visible.
    w, h = live_fg_size(720, 1280)
    assert h <= int(MAX_FG_FRAC * OUT_H) and h < OUT_H
    assert w <= OUT_W
    # A horizontal source scales to full width with big bands, unchanged.
    assert live_fg_size(1920, 1080) == (OUT_W, 608)


def test_capped_duration_limits_long_lives():
    from bot.live import capped_duration

    # A 3-hour live capped at 1 hour yields 6 parts of 10 min, not 18.
    assert capped_duration(10800, 3600) == 3600
    assert len(split_by_time(capped_duration(10800, 3600), 600)) == 6
    # No cap (0 → None) or a cap longer than the video keeps the whole thing.
    assert capped_duration(1200, None) == 1200
    assert capped_duration(1200, 3600) == 1200


def _mean_db(p: Path) -> float:
    r = subprocess.run(["ffmpeg", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(r.stderr.split("mean_volume:")[1].split("dB")[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("with_bg", [True, False])
def test_live_part_vertical_louder_with_background(tmp_path, with_bg):
    from bot.live import find_backgrounds, has_audio, render_live_part

    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-af", "volume=0.3",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)],
        check=True, capture_output=True,
    )
    bg_dir = tmp_path / "backgrounds"
    bg_dir.mkdir()
    if with_bg:
        # Shorter than the part: it has to loop.
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "mandelbrot=size=320x240:rate=30",
                        "-t", "1.5", str(bg_dir / "fire.mp4")], check=True, capture_output=True)
    bgs = find_backgrounds(bg_dir)
    assert len(bgs) == (1 if with_bg else 0)
    assert has_audio(src)
    out = tmp_path / "out.mp4"
    render_live_part(src, 1.0, 5.0, 640, 360, bgs[0] if bgs else None, 130, True, out)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration - 4.0) < 0.2
    # +30% amplitude is about +2.3 dB.
    assert 1.8 < _mean_db(out) - _mean_db(src) < 2.8


def test_banner_times_one_in_the_middle_short_clip():
    from bot.ads import ad_start, banner_times

    # Up to 2:00 → one banner exactly in the middle (rule 3.4).
    assert banner_times(60, 7.2) == [ad_start(60, 7.2)]
    assert banner_times(119, 7.2) == [ad_start(119, 7.2)]
    # Its centre is the clip's centre.
    (t,) = banner_times(100, 7.2)
    assert abs((t + 7.2 / 2) - 50) < 0.01


def test_banner_times_one_per_minute_for_long_clip():
    from bot.ads import banner_times

    # From 2:00 → one banner in the middle of each full minute (rules 3.3–3.4): 0:30, 1:30, …
    centres = lambda d: [round(t + 7.2 / 2) for t in banner_times(d, 7.2)]
    assert centres(120) == [30, 90]
    assert centres(180) == [30, 90, 150]
    assert centres(300) == [30, 90, 150, 210, 270]
    # Every banner stays fully inside the clip.
    for t in banner_times(185, 7.2):
        assert 0 <= t and t + 7.2 <= 185


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_with_ad_full_width(tmp_path: Path):
    src = tmp_path / "src.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=8",
        "-f", "lavfi", "-i", "sine=f=440:d=8", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src),
    ], check=True)
    layout = make_layout(640, 360)
    ass = tmp_path / "p.ass"
    ass.write_text(build_ass([], 0, 8, layout, 1, "ru", True), encoding="utf-8")
    out = tmp_path / "out.mp4"
    render_clip(src, 0, 8, ass, layout, FONTS, out, ADS["natriavpn"])
    res = probe(out)
    assert (res.width, res.height) == (1080, 1920)
    assert abs(res.duration - 8) < 0.3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_long_clip_has_two_banners(tmp_path: Path):
    # A clip ≥2:00 gets two banners (0:30 and 1:30); render must succeed end to end.
    src = tmp_path / "src.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=640x360:r=30:d=130",
        "-f", "lavfi", "-i", "sine=f=440:d=130", "-shortest", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(src),
    ], check=True)
    layout = make_layout(640, 360)
    ass = tmp_path / "p.ass"
    ass.write_text(build_ass([], 0, 130, layout, 1, "ru", True), encoding="utf-8")
    out = tmp_path / "out.mp4"
    render_clip(src, 0, 130, ass, layout, FONTS, out, ADS["natriavpn"])
    res = probe(out)
    assert (res.width, res.height) == (1080, 1920)
    assert abs(res.duration - 130) < 0.5
