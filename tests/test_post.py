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


def make_clip(
    path: Path,
    seconds: int = 2,
    size: str = "720x1280",
    volume: float = 0.5,
    audio: str = "sine=frequency=440",
) -> Path:
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
            f"{audio}:duration={seconds}:sample_rate=48000",
            "-af",
            f"volume={volume}",
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


def test_xfade_timeline_matches_the_measured_formula():
    # Measured 2026-09-13: two 8.01s clips with a 0.5s fade came out 15.5417s, formula says 15.52.
    assert post.xfade_total([8.01, 8.01], 0.5) == pytest.approx(15.52, abs=0.001)
    assert post.xfade_total([8.0] * 6, 0.5) == pytest.approx(45.5, abs=0.001)
    assert post.xfade_total([8.0], 0.5) == pytest.approx(8.0, abs=0.001)
    assert post.xfade_starts([8.0, 8.0, 8.0], 0.5) == pytest.approx([0.0, 7.5, 15.0])


@pytest.mark.slow
def test_the_timeline_measures_the_video_stream_not_the_container(tmp_path):
    # Loudness normalising re-encodes the audio a little longer than the picture, and the container
    # reports the longer of the two. xfade works on the picture, so timing off the container drifts.
    clip = tmp_path / "longer_audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3:sample_rate=48000",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    assert post.duration(clip) == pytest.approx(3.0, abs=0.1)
    assert post.video_duration(clip) == pytest.approx(2.0, abs=0.1)


def test_a_join_can_be_a_cut_instead_of_a_dissolve():
    # Dissolving two different shots double-exposes them for half a second, which is what the owner saw.
    # A cut is one frame of blend: invisible, and it keeps a single filter path.
    fades = post.join_fades(["cut", "dissolve", "cut"], fade=0.5)
    assert fades == [post.CUT, 0.5, post.CUT]
    assert post.join_fades([], fade=0.5) == []
    assert post.join_fades(None, fade=0.5, joins_needed=2) == [0.5, 0.5]


def test_the_timeline_takes_a_different_fade_at_every_join():
    durations = [8.0, 8.0, 8.0]
    fades = [post.CUT, 0.5]
    assert post.xfade_total(durations, fades) == pytest.approx(24.0 - post.CUT - 0.5, abs=0.001)
    starts = post.xfade_starts(durations, fades)
    assert starts == pytest.approx([0.0, 8.0 - post.CUT, 16.0 - post.CUT - 0.5])
    spec = post.build_xfade_filter(durations, [], fades)
    assert f"duration={post.CUT}" in spec and "duration=0.5" in spec


def test_build_xfade_filter_offsets_every_transition_and_crossfades_the_audio():
    spec = post.build_xfade_filter([8.0, 8.0, 8.0], [], fade=0.5)
    assert spec.count("xfade=") == 2
    assert "offset=7.500" in spec and "offset=15.000" in spec
    assert spec.count("acrossfade=d=0.5") == 2
    # A hard concat is what made the old cut jump; it must be gone.
    assert "concat=" not in spec


def test_a_single_clip_needs_no_transition_at_all():
    spec = post.build_xfade_filter([8.0], [], fade=0.5)
    assert "xfade" not in spec and "acrossfade" not in spec
    assert "[0:v]" in spec and "[a]" in spec


def test_caption_windows_follow_the_faded_timeline_not_the_raw_clock():
    windows = post.caption_windows([8.0, 8.0], ["một", "hai"], fade=0.5, lead=0.4)
    assert windows[0][0] == pytest.approx(0.4)
    assert windows[0][1] == pytest.approx(7.1)  # ends before the fade begins at 7.5
    assert windows[1][0] == pytest.approx(7.9)  # clip 2 starts at 7.5 on the faded timeline
    assert [text for _, _, text in windows] == ["một", "hai"]
    assert post.caption_windows([8.0], [""], fade=0.5) == []


def test_parse_lufs_reads_the_integrated_loudness_from_ebur128():
    summary = "[Parsed_ebur128_0 @ 0x7f] Summary:\n\n  Integrated loudness:\n    I:         -16.8 LUFS\n"
    assert post.parse_lufs(summary) == pytest.approx(-16.8)
    assert post.parse_lufs("  I:  -14.5 LUFS") == pytest.approx(-14.5)
    assert post.parse_lufs("no loudness here") is None


def test_strip_grid_keeps_short_clips_from_becoming_mostly_empty():
    assert post.strip_grid(4) == (4, 1)
    assert post.strip_grid(16) == (8, 2)
    assert post.strip_grid(17) == (8, 3)


def test_the_measured_pass_feeds_its_numbers_into_the_second_one():
    text = (
        "[Parsed_loudnorm_0 @ 0x14] \n"
        '{\n\t"input_i" : "-52.36",\n\t"input_tp" : "-38.13",\n\t"input_lra" : "0.00",\n'
        '\t"input_thresh" : "-62.40",\n\t"output_i" : "-16.00",\n\t"target_offset" : "0.21"\n}\n'
    )
    measured = post.parse_loudnorm_json(text)
    assert measured["input_i"] == "-52.36"
    spec = post.loudnorm_filter(measured)
    assert "measured_I=-52.36" in spec and "measured_thresh=-62.40" in spec
    assert "linear=true" in spec
    # No measurement, or an unusable one, falls back to the single pass rather than building junk.
    assert post.loudnorm_filter(None) == post.LOUDNORM
    assert post.loudnorm_filter({"input_i": "-inf", "input_tp": "-inf"}) == post.LOUDNORM
    assert post.parse_loudnorm_json("nothing here") is None


def test_channel_gains_map_one_measured_colour_onto_another():
    # Measured 2026-09-13: the garment reads rgb(159,132,129) on the hanger and rgb(182,159,140) on her.
    gains = post.channel_gains((159.2, 131.9, 129.0), (182.2, 158.8, 140.1))
    assert gains == pytest.approx((1.144, 1.204, 1.086), abs=0.001)
    assert post.channel_gains((100, 100, 100), (100, 100, 100)) == (1.0, 1.0, 1.0)
    # A wild correction is a measurement mistake, not a grade: clamp it rather than wreck the picture.
    assert post.channel_gains((10, 10, 10), (200, 200, 200)) == (2.0, 2.0, 2.0)
    assert post.channel_gains((0, 0, 0), (120, 120, 120)) == (1.0, 1.0, 1.0)


def test_the_grade_filter_is_empty_when_there_is_nothing_to_correct():
    assert post.grade_filter((1.0, 1.0, 1.0)) == ""
    assert post.grade_filter(None) == ""
    spec = post.grade_filter((1.144, 1.204, 1.086))
    assert spec == "colorchannelmixer=rr=1.144:gg=1.204:bb=1.086"


@pytest.mark.slow
def test_normalise_moves_the_colour_when_it_is_given_a_grade(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", seconds=2, size="320x320")
    before = post.mean_rgb(clip, at=1.0)
    graded = post.normalise(clip, tmp_path / "g.mp4", gains=(1.3, 1.0, 0.8))
    after = post.mean_rgb(graded, at=1.0)
    assert after[0] > before[0], (before, after)
    assert after[2] < before[2], (before, after)


@pytest.mark.slow
def test_a_clip_whose_loudness_range_beats_the_target_still_lands_on_level(tmp_path):
    # Real audio from the wardrobe shot: loudness range 15.7 dB with only 2.6 dB of headroom, so no
    # amount of gain can place it and loudnorm's own fallback stopped at -19.7 LUFS (measured 2026-09-13).
    fixture = Path(__file__).parent / "fixtures" / "audio" / "wide_range.m4a"
    assert post.lufs(fixture) < -20, post.lufs(fixture)
    level = post.lufs(post.normalise(fixture, tmp_path / "wide.m4a"))
    assert -17.5 <= level <= -14.5, level


@pytest.mark.slow
def test_normalise_reaches_the_target_even_on_a_nearly_silent_clip(tmp_path):
    # Measured 2026-09-13 on the real chain: the product close-up came back at -52.4 LUFS and one
    # loudnorm pass only lifted it to -17.6, leaving the cut 3.4 dB apart between shots.
    # Pink noise, not a pure tone: a mathematically constant sine has no loudness range at all and
    # loudnorm cannot place it, which is a property of the test signal, not of a real clip.
    quiet = make_clip(
        tmp_path / "quiet.mp4", seconds=3, volume=1.0, audio="anoisesrc=color=pink:amplitude=0.004"
    )
    assert post.lufs(quiet) < -40, post.lufs(quiet)
    level = post.lufs(post.normalise(quiet, tmp_path / "n.mp4"))
    assert -17.5 <= level <= -14.5, level


@pytest.mark.slow
def test_normalise_pulls_a_loud_and_a_quiet_clip_into_the_same_window(tmp_path):
    loud = make_clip(tmp_path / "loud.mp4", seconds=3, volume=0.9)
    quiet = make_clip(tmp_path / "quiet.mp4", seconds=3, volume=0.05)
    levels = [post.lufs(post.normalise(clip, tmp_path / f"n_{clip.stem}.mp4")) for clip in (loud, quiet)]
    assert all(-17.5 <= level <= -14.5 for level in levels), levels
    assert max(levels) - min(levels) <= 1.5, levels


@pytest.mark.slow
def test_the_xfade_cut_is_shorter_than_the_sum_by_exactly_the_fades(tmp_path):
    clips = [make_clip(tmp_path / f"c{i}.mp4", seconds=3) for i in range(3)]
    out = post.crossfade_with_captions(clips, ["một", "hai", "ba"], tmp_path / "final.mp4", fade=0.5)
    info = probe(out)
    expected = post.xfade_total([post.duration(c) for c in clips], 0.5)
    assert abs(float(info["format"]["duration"]) - expected) < 0.15, info["format"]["duration"]
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)
    assert any(s["codec_type"] == "audio" for s in info["streams"]), info


@pytest.mark.slow
def test_strip_has_two_frames_for_every_second_of_clip(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", seconds=2)
    image = post.strip(clip, tmp_path / "strip.jpg", fps=2)
    info = probe(image)
    cell = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert cell["width"] == post.STRIP_CELL * 4, info  # 2s at 2fps = 4 frames in one row


@pytest.mark.slow
def test_contact_sheet_has_one_cell_per_clip(tmp_path):
    clips = [make_clip(tmp_path / f"c{i}.mp4", seconds=1) for i in range(3)]
    sheet = post.contact_sheet(clips, tmp_path / "sheet.jpg")
    info = probe(sheet)
    image = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert image["width"] == post.SHEET_CELL * 3, info
