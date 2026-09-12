import subprocess
from pathlib import Path

import pytest

from video import post


def make_clip(path: Path, seconds: int = 2) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=720x1280:rate=24:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_last_frame_writes_a_png_the_size_of_the_clip(tmp_path):
    clip = make_clip(tmp_path / "c.mp4")
    still = post.last_frame(clip, tmp_path / "last.png")
    assert still.is_file()
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(still)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.stdout.strip().startswith("720,1280")


def test_last_frame_takes_the_end_not_the_start(tmp_path):
    # testsrc counts up, so the final frame differs from the first; identical bytes would mean we
    # grabbed frame 0 and the chained shot would restart the motion instead of continuing it.
    clip = make_clip(tmp_path / "c.mp4", seconds=3)
    last = post.last_frame(clip, tmp_path / "last.png")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-frames:v", "1", str(tmp_path / "first.png")],
        check=True,
    )
    assert last.read_bytes() != (tmp_path / "first.png").read_bytes()


def test_last_frame_refuses_a_missing_clip(tmp_path):
    with pytest.raises(FileNotFoundError):
        post.last_frame(tmp_path / "gone.mp4", tmp_path / "x.png")
