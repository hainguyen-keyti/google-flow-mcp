import subprocess
from pathlib import Path

import pytest

from video import framediff


def make_clip(path: Path, seconds: int = 2, size: str = "320x320", source: str = "testsrc") -> Path:
    """A silent clip built by ffmpeg, following the pattern of tests/test_post.py:31."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"{source}=size={size}:rate=24:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


CROPS = [(80, 80, 0, 0), (80, 80, 160, 160), (80, 80, 240, 40)]


def test_diff_is_zero_for_identical_buffers_and_maximal_for_opposite_ones():
    assert framediff.diff(bytes([7]) * 100, bytes([7]) * 100) == 0.0
    assert framediff.diff(bytes([0]) * 100, bytes([255]) * 100) == 255.0
    assert framediff.diff(bytes([0]) * 100, bytes([10]) * 100) == 10.0


def test_frame_fails_loudly_instead_of_returning_an_empty_buffer(tmp_path):
    # post.mean_rgb answers (0, 0, 0) when ffmpeg gives it nothing. A measuring harness must not do
    # that: a silent zero is indistinguishable from a perfect match, which is how a broken measure
    # gets believed.
    with pytest.raises(RuntimeError):
        framediff.frame(tmp_path / "nothing.mp4", 1.0)


def test_the_harness_measures_zero_when_it_grabs_the_same_frame_twice(tmp_path):
    clip = make_clip(tmp_path / "a.mp4")
    report = framediff.self_test(clip, CROPS)
    assert report["identity"] == 0.0


def test_a_grab_that_cannot_repeat_itself_is_refused(tmp_path):
    # The harness thrown away on 2026-09-13 (blend=all_mode=difference + scale=1:1) answered 25.67
    # when it compared one frame against itself. Two independent grabs of one timestamp must agree.
    clip = make_clip(tmp_path / "a.mp4")
    calls = {"n": 0}

    def drifting(path, at, crop=None, size=framediff.SIZE):
        calls["n"] += 1
        return bytes([calls["n"] % 256]) * (size * size)

    with pytest.raises(framediff.HarnessBroken, match="identity"):
        framediff.self_test(clip, CROPS, grab=drifting)


def test_a_grab_that_ignores_the_crop_is_refused(tmp_path):
    # This is the bug that produced three wrong conclusions on 2026-09-13: every crop returned the
    # same number, so a band of floral fabric and a band of bare skin measured identically.
    clip = make_clip(tmp_path / "a.mp4")

    def cropless(path, at, crop=None, size=framediff.SIZE):
        return bytes([42]) * (size * size)

    with pytest.raises(framediff.HarnessBroken, match="crop"):
        framediff.self_test(clip, CROPS, grab=cropless)


def test_the_measurement_changes_with_the_region_it_is_pointed_at(tmp_path):
    # Two clips that differ everywhere, measured on two regions: an honest harness gives the regions
    # different numbers, because the regions do not differ by the same amount.
    a = make_clip(tmp_path / "a.mp4", source="testsrc")
    b = make_clip(tmp_path / "b.mp4", source="smptebars")
    left = framediff.diff(framediff.frame(a, 1.0, CROPS[0]), framediff.frame(b, 1.0, CROPS[0]))
    right = framediff.diff(framediff.frame(a, 1.0, CROPS[1]), framediff.frame(b, 1.0, CROPS[1]))
    assert left != right, (left, right)


def test_frames_sweeps_the_whole_clip_at_the_rate_it_is_asked_for(tmp_path):
    clip = make_clip(tmp_path / "a.mp4", seconds=2)
    swept = framediff.frames(clip, fps=24)
    assert len(swept) == pytest.approx(48, abs=2), len(swept)
    assert all(len(buf) == framediff.SIZE * framediff.SIZE for buf in swept)


def test_frames_agrees_with_seeking_to_each_timestamp(tmp_path):
    # The sweep is an optimisation, so it has to answer what the per-frame seek answers. If these
    # drift apart, every window measured by the sweep is measured against the wrong baseline.
    clip = make_clip(tmp_path / "a.mp4", seconds=2)
    swept = framediff.frames(clip, fps=24, crop=CROPS[0])
    for index in (6, 12, 24):
        seeked = framediff.frame(clip, index / 24.0, CROPS[0])
        assert framediff.diff(swept[index], seeked) < 1.0, index


def test_frames_fails_loudly_on_a_clip_that_is_not_there(tmp_path):
    with pytest.raises(RuntimeError):
        framediff.frames(tmp_path / "nothing.mp4")


def test_a_crop_actually_selects_a_region_rather_than_the_whole_frame(tmp_path):
    clip = make_clip(tmp_path / "a.mp4")
    whole = framediff.frame(clip, 1.0)
    corner = framediff.frame(clip, 1.0, CROPS[0])
    assert len(whole) == len(corner) == framediff.SIZE * framediff.SIZE
    assert whole != corner
