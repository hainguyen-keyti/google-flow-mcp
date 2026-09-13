"""Frame difference measurement that refuses to report a number until it proves it can measure.

Two harnesses were thrown away on 2026-09-13 before this one. The first compared a frame against
itself and answered 25.67 instead of 0, and silently ignored its crop argument, so three different
regions of the same pair returned one number. Conclusions drawn from it were wrong in both
directions: it nearly produced "Omni edit redraws the room" and "the poses never align".

So `self_test` runs before any comparison is trusted, and it takes its frame grabber as an argument
so a deliberately broken one can be pointed at it (the pattern of scripts/acceptance/ledger_integrity.py).

The ffmpeg invocation follows video.post.mean_rgb. What it does NOT reuse is that function's
reduction: `mean_rgb` averages a whole frame down to three numbers, and two frames with identical
average colour can have completely different content, which is exactly the failure this must catch.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

SIZE = 128
Crop = tuple[int, int, int, int]


class HarnessBroken(RuntimeError):
    """The measure failed its own self-test, so no number it produces can be trusted."""


def frame(path: Path | str, at: float, crop: Crop | None = None, size: int = SIZE) -> bytes:
    """One frame at `at` seconds, optionally one region of it, as size x size grey bytes."""
    chain = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(path),
            "-vf",
            f"{chain}scale={size}:{size}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=False,
    ).stdout
    if len(raw) != size * size:
        raise RuntimeError(f"no frame from {Path(path).name} at {at}s crop={crop}: got {len(raw)} bytes")
    return raw


def frames(path: Path | str, fps: int = 24, crop: Crop | None = None, size: int = SIZE) -> list[bytes]:
    """Every frame of a clip at `fps`, in one ffmpeg call rather than one seek per frame.

    A whole-clip sweep through `frame` costs hundreds of process launches. This is the same filter
    chain in one pass, and `test_frames_agrees_with_seeking_to_each_timestamp` pins the two together.
    """
    chain = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},{chain}scale={size}:{size}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=False,
    ).stdout
    step = size * size
    if not raw or len(raw) % step:
        raise RuntimeError(f"no sweep from {Path(path).name} at {fps}fps crop={crop}: {len(raw)} bytes")
    return [raw[i * step : (i + 1) * step] for i in range(len(raw) // step)]


def diff(a: bytes, b: bytes) -> float:
    """Mean absolute difference per pixel, 0 for identical buffers and 255 for opposite ones."""
    if not a or len(a) != len(b):
        raise ValueError(f"cannot compare buffers of {len(a)} and {len(b)} bytes")
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def self_test(
    path: Path | str,
    crops: list[Crop],
    *,
    at: float = 1.0,
    grab: Callable[..., bytes] = frame,
) -> dict[str, object]:
    """Prove the measure works before it is believed, or raise HarnessBroken saying which check failed.

    Identity compares two INDEPENDENT grabs of one timestamp rather than a buffer against itself:
    comparing a buffer with itself is 0 for every implementation, including a broken one.
    """
    identity = diff(grab(path, at), grab(path, at))
    if identity != 0.0:
        raise HarnessBroken(
            f"identity: two grabs of {Path(path).name} at {at}s differ by {identity:.2f}, must be 0"
        )
    buffers = [grab(path, at, crop) for crop in crops]
    if len({bytes(buf) for buf in buffers}) == 1:
        raise HarnessBroken(
            f"crop: {len(crops)} different crops of {Path(path).name} all returned the same buffer"
        )
    return {"identity": identity, "crop_spread": [diff(buffers[0], buf) for buf in buffers[1:]]}
