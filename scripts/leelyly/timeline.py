"""The edit as arithmetic: what plays when, how long each shot lasts, and where the voice sits.

Numbers come from the research in docs/leelyly/story.md: hook inside 1.5 s, a cut every 2 to 4 s, total near
120 s. Keeping them here, away from ffmpeg, means the edit can be judged before a single frame is rendered.
"""

from __future__ import annotations

import random
from itertools import pairwise
from typing import NamedTuple

SEGMENT_MIN_S = 1.4
SEGMENT_MAX_S = 4.5
AVERAGE_MAX_S = 3.6
HOOK_MAX_S = 1.5
GAP_MIN_S = 0.18
GAP_MAX_S = 0.55
TARGET_S = 120.0
TARGET_SLACK_S = 5.0
PILOT_ACTS = (1, 2)


class Segment(NamedTuple):
    id: str
    kind: str  # "clip" (generated video), "still" (a photograph given motion)
    source: str
    start_in_source: float
    seconds: float
    fade_in: float = 0.0
    act: int = 1
    line: str | None = None
    note: str = ""
    # A beat of held picture after this line, on purpose: the pauses are what keep the voice from marching.
    pause_after: float = 0.0


class Line(NamedTuple):
    id: str
    start: float
    seconds: float


class Track(NamedTuple):
    lines: list[Line]
    seconds: float


class Plan(NamedTuple):
    segments: list[Segment]
    starts: list[float]
    seconds: float
    hook_ok: bool


def plan(segments: list[Segment], *, hook_seconds: float) -> Plan:
    """Where each shot starts once the crossfades have eaten their overlap."""
    starts: list[float] = []
    head = 0.0
    for index, segment in enumerate(segments):
        if index:
            head -= segment.fade_in
        starts.append(head)
        head += segment.seconds
    return Plan(segments, starts, head, hook_seconds <= HOOK_MAX_S)


def narration(durations: list[float], *, seed: int = 0) -> Track:
    """The voice track: every line in order, separated by gaps that are deliberately uneven."""
    rng = random.Random(seed)
    lines: list[Line] = []
    head = 0.0
    for index, seconds in enumerate(durations):
        lines.append(Line(f"L{index + 1:02d}", round(head, 3), seconds))
        head += seconds
        if index < len(durations) - 1:
            head += round(rng.uniform(GAP_MIN_S, GAP_MAX_S), 3)
    return Track(lines, round(head, 3))


def pilot(segments: list[Segment]) -> Plan:
    """The opening only: the part the owner judges before the rest is paid for."""
    opening = [segment for segment in segments if segment.act in PILOT_ACTS]
    return plan(opening, hook_seconds=0.0)


def problems(segments: list[Segment], *, narration_total: float, target: float | None = None) -> list[str]:
    """Everything a viewer would notice, said in numbers."""
    found: list[str] = []
    if not segments:
        return ["the edit has no segments at all"]
    for segment in segments:
        if segment.seconds > SEGMENT_MAX_S:
            found.append(
                f"segment {segment.id} runs {segment.seconds} s, over the {SEGMENT_MAX_S} s a cut is allowed; "
                "a generated clip laid down whole is what makes an edit look machine made"
            )
        if segment.seconds < SEGMENT_MIN_S:
            found.append(
                f"segment {segment.id} runs {segment.seconds} s, under {SEGMENT_MIN_S} s, too fast to read"
            )
    average = sum(segment.seconds for segment in segments) / len(segments)
    if average > AVERAGE_MAX_S:
        found.append(f"the average shot is {average:.2f} s, over {AVERAGE_MAX_S} s, so the edit drags")
    laid = plan(segments, hook_seconds=0.0).seconds
    if laid + 0.01 < narration_total:
        found.append(f"the picture is {laid:.1f} s but the voice needs {narration_total:.1f} s")
    if target is not None and abs(laid - target) > TARGET_SLACK_S:
        found.append(f"the film is {laid:.1f} s, outside {target} s give or take {TARGET_SLACK_S}")
    return found


def voice_problems(segments: list[Segment], durations: dict[str, float]) -> list[str]:
    """Where the voice would talk over itself, and which lines the cut forgot.

    The picture is allowed to cut under a line, so a line only has to finish before the NEXT line starts, not
    before its own segment ends.
    """
    starts = plan(segments, hook_seconds=0.0).starts
    spoken = [
        (segment.line, start)
        for segment, start in zip(segments, starts)
        if segment.line and segment.kind != "clip_onscreen"
    ]
    found: list[str] = []
    for (line_id, start), (next_id, next_start) in pairwise(spoken):
        ends = start + durations.get(line_id, 0.0)
        if ends > next_start + 0.01:
            found.append(
                f"{line_id} is still talking {ends - next_start:.1f} s after {next_id} starts; give it more "
                "picture or shorten the line"
            )
    placed = {line_id for line_id, _ in spoken}
    for line_id in durations:
        if line_id not in placed:
            found.append(f"{line_id} has audio but no segment in the cut plays it")
    return found


def fit(segments: list[Segment], durations: dict[str, float]) -> list[Segment]:
    """Hold each picture exactly long enough for the line under it, inside the 1.4 to 4.5 s a cut is allowed.

    The shot list decides WHICH picture; this decides how long, because doing that by hand is how a cut ends
    up with two lines talking at once or a shot sitting there for eight seconds.
    """
    fitted = list(segments)
    spoken = [index for index, segment in enumerate(fitted) if segment.line]
    for position, index in enumerate(spoken):
        end = spoken[position + 1] if position + 1 < len(spoken) else len(fitted)
        group = list(range(index, end))
        # A crossfade starts the next shot early, so the line under it starts early too: buy that time back.
        overlap = fitted[end].fade_in if end < len(fitted) else 0.0
        needed = durations.get(fitted[index].line, 0.0) + GAP_MIN_S + fitted[index].pause_after + overlap
        if len(group) * SEGMENT_MAX_S < needed:
            raise ValueError(
                f"{fitted[index].line} needs {needed:.1f} s but only {len(group)} shot(s) follow it, at most "
                f"{len(group) * SEGMENT_MAX_S:.1f} s; add a shot to the list"
            )
        each = max(SEGMENT_MIN_S, min(SEGMENT_MAX_S, needed / len(group)))
        for slot in group:
            fitted[slot] = fitted[slot]._replace(seconds=round(each, 2))
    return fitted
