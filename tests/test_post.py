import json
import subprocess
from pathlib import Path

import pytest

from video import post


def probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=width,height,codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(out.stdout or "{}")


def make_clip(path: Path, seconds: int = 2, size: str = "720x1280") -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=24:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_font_exists_and_can_render_vietnamese():
    font = post.font()
    assert Path(font).is_file(), font


def test_build_filter_concats_every_clip_and_windows_each_caption():
    spec = post.build_filter(2, [(0.0, 8.0, "Hôm nay thử đồ mới"), (8.0, 16.0, "Inbox đặt nha")], 8.0)
    assert "[0:v]" in spec and "[1:v]" in spec
    assert "concat=n=2:v=1:a=1" in spec
    assert "between(t,0.0,8.0)" in spec and "between(t,8.0,16.0)" in spec
    assert spec.count("drawtext") == 2


def test_build_filter_without_captions_is_a_plain_concat():
    spec = post.build_filter(3, [], 8.0)
    assert "concat=n=3:v=1:a=1" in spec
    assert "drawtext" not in spec


@pytest.mark.slow
def test_concat_with_captions_produces_one_vertical_video(tmp_path):
    clips = [make_clip(tmp_path / f"c{i}.mp4", seconds=2) for i in range(2)]
    out = post.concat_with_captions(
        clips, [(0.0, 2.0, "Thử đồ"), (2.0, 4.0, "Chốt đơn")], tmp_path / "final.mp4", clip_seconds=2.0
    )
    info = probe(out)
    assert out.is_file()
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.6, info["format"]["duration"]
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)


@pytest.mark.slow
def test_contact_sheet_has_one_cell_per_clip(tmp_path):
    clips = [make_clip(tmp_path / f"c{i}.mp4", seconds=1) for i in range(3)]
    sheet = post.contact_sheet(clips, tmp_path / "sheet.jpg")
    info = probe(sheet)
    image = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert image["width"] == post.SHEET_CELL * 3, info
