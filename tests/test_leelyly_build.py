"""The rules of the LeeLyLy cut, as arithmetic, so the edit can be judged before anything is rendered.

The craft numbers come from the research written into docs/leelyly/story.md: a hook inside the first 1.5 s, a
cut every 2 to 4 s, and a total near 120 s. The repo has paid for the other half of this before: on 2026-09-13
an acceptance passed 7/7 while the delivered video was unusable, so these checks are about what a viewer sees,
not about whether ffmpeg exited zero.
"""

import importlib.util
from pathlib import Path

import pytest

_TIMELINE = Path(__file__).resolve().parents[1] / "scripts" / "leelyly" / "timeline.py"
_spec = importlib.util.spec_from_file_location("leelyly_timeline", _TIMELINE)
timeline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(timeline)


def _segments(durations):
    return [timeline.Segment(f"s{i:02d}", "clip", f"c{i}.mp4", 0.0, d) for i, d in enumerate(durations)]


def test_a_cut_every_two_to_four_seconds_passes():
    problems = timeline.problems(_segments([2.0, 3.0, 2.5, 4.0, 3.0]), narration_total=14.0)

    assert problems == []


def test_a_segment_left_at_its_full_eight_seconds_is_a_problem():
    """A generated clip is 8 s. Laying it down whole is the single easiest way to look like an AI video."""
    problems = timeline.problems(_segments([8.0, 3.0, 2.5]), narration_total=13.0)

    assert any("8.0" in p and "s00" in p for p in problems), problems


def test_an_edit_whose_average_shot_is_slow_is_a_problem():
    problems = timeline.problems(_segments([4.4, 4.4, 4.4, 4.4]), narration_total=17.0)

    assert any("average" in p for p in problems), problems


def test_the_hook_has_to_land_inside_the_first_second_and_a_half():
    segments = _segments([3.0, 3.0, 3.0])
    ontime = timeline.plan(segments, hook_seconds=1.2)
    late = timeline.plan(segments, hook_seconds=2.4)

    assert ontime.hook_ok is True
    assert late.hook_ok is False


def test_the_timeline_length_follows_the_fade_formula():
    """Two crossfades of half a second take a second off the sum, which is the arithmetic the acceptance and
    the human both read; getting it wrong makes every later duration claim wrong."""
    segments = _segments([3.0, 3.0, 3.0, 3.0])
    segments[1] = segments[1]._replace(fade_in=0.5)
    segments[3] = segments[3]._replace(fade_in=0.5)

    plan = timeline.plan(segments, hook_seconds=1.0)

    assert plan.seconds == pytest.approx(12.0 - 1.0)
    assert plan.starts[-1] == pytest.approx(3.0 + 2.5 + 3.0 - 0.5)


def test_narration_gaps_are_uneven_so_the_voice_does_not_march():
    """An even gap between every line is a metronome, and a metronome is the tell listeners notice first."""
    track = timeline.narration([4.0, 5.0, 4.5, 6.0], seed=7)

    gaps = [round(b.start - (a.start + a.seconds), 3) for a, b in zip(track.lines, track.lines[1:])]
    assert all(timeline.GAP_MIN_S <= g <= timeline.GAP_MAX_S for g in gaps), gaps
    assert len(set(gaps)) > 1, "every gap the same length is the metronome this exists to avoid"
    assert track.seconds == pytest.approx(19.5 + sum(gaps))


def test_the_pilot_is_only_the_opening_and_says_which_lines_it_covers():
    segments = _segments([3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0])
    for index in range(4, 10):
        segments[index] = segments[index]._replace(act=3)

    pilot = timeline.pilot(segments)

    assert pilot.seconds == pytest.approx(12.0)
    assert [segment.id for segment in pilot.segments] == ["s00", "s01", "s02", "s03"]


def test_a_line_that_would_still_be_talking_when_the_next_one_starts_is_a_problem():
    """The picture cuts faster than the voice on purpose, but two lines on top of each other is mud, and it is
    the kind of thing nobody notices in a shot list and everybody notices in the file."""
    segments = [
        timeline.Segment("s00", "still", "a.png", 0.0, 2.0, line="L01"),
        timeline.Segment("s01", "still", "b.png", 0.0, 2.0),
        timeline.Segment("s02", "still", "c.png", 0.0, 2.0, line="L02"),
    ]
    durations = {"L01": 3.5, "L02": 2.0}

    assert timeline.voice_problems(segments, durations) == []

    tight = durations | {"L01": 4.4}
    problems = timeline.voice_problems(segments, tight)
    assert any("L01" in p and "L02" in p for p in problems), problems


def test_every_line_has_to_be_somewhere_in_the_cut():
    segments = [timeline.Segment("s00", "still", "a.png", 0.0, 2.0, line="L01")]

    problems = timeline.voice_problems(segments, {"L01": 1.5, "L02": 1.5})

    assert any("L02" in p for p in problems), problems


def test_durations_are_stretched_so_every_line_finishes_before_the_next_one():
    """The shot list picks WHICH picture; how long each one holds is arithmetic, and doing it by hand is how a
    cut ends up with the voice talking over itself."""
    segments = [
        timeline.Segment("s00", "still", "a.png", 0.0, 2.0, line="L01"),
        timeline.Segment("s01", "still", "b.png", 0.0, 2.0),
        timeline.Segment("s02", "still", "c.png", 0.0, 2.0, line="L02"),
        timeline.Segment("s03", "still", "d.png", 0.0, 2.0),
    ]
    durations = {"L01": 6.4, "L02": 5.0}

    fitted = timeline.fit(segments, durations)

    assert timeline.voice_problems(fitted, durations) == []
    assert timeline.problems(fitted, narration_total=11.4) == []
    each = round((6.4 + timeline.GAP_MIN_S) / 2, 2)
    assert [round(s.seconds, 2) for s in fitted][:2] == [each, each]


def test_fitting_says_so_when_there_is_not_enough_picture_for_a_line():
    segments = [
        timeline.Segment("s00", "still", "a.png", 0.0, 2.0, line="L01"),
        timeline.Segment("s01", "still", "b.png", 0.0, 2.0, line="L02"),
    ]

    with pytest.raises(ValueError, match="L01"):
        timeline.fit(segments, {"L01": 12.0, "L02": 3.0})


def test_a_beat_of_silence_after_a_line_pushes_the_next_one_later():
    """ "Rồi thôi." needs the room to sit empty for a moment. Without a deliberate pause the fitter packs every
    line end to end, which is the even rhythm that reads as a machine reading a script."""
    segments = [
        timeline.Segment("s00", "still", "a.png", 0.0, 2.0, line="L01", pause_after=2.0),
        timeline.Segment("s01", "still", "b.png", 0.0, 2.0),
        timeline.Segment("s02", "still", "c.png", 0.0, 2.0, line="L02"),
    ]
    durations = {"L01": 4.0, "L02": 3.0}

    fitted = timeline.fit(segments, durations)
    starts = timeline.plan(fitted, hook_seconds=0.0).starts

    assert timeline.voice_problems(fitted, durations) == []
    assert starts[2] == pytest.approx(4.0 + timeline.GAP_MIN_S + 2.0, abs=0.05)
