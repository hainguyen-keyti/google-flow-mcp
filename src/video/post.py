"""ffmpeg post: stitch the shots into one vertical video, burn the selling captions, build a QC sheet.

Captions are burned here rather than asked of Veo: editing a line costs nothing, regenerating a clip
costs credits.
"""

from __future__ import annotations

import math
import re
import shlex
import subprocess
from pathlib import Path

WIDTH, HEIGHT, FPS = 720, 1280, 24
SHEET_CELL = 240
STRIP_CELL, STRIP_COLS = 180, 8
FADE = 0.5
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
LUFS_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
Caption = tuple[float, float, str]


def font() -> str:
    for candidate in FONTS:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError(f"no usable font found, looked for {FONTS}")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def build_filter(count: int, captions: list[Caption], clip_seconds: float) -> str:
    """One filter graph: normalise every clip, concat, then window each caption over the join."""
    parts = [
        f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v{i}]"
        for i in range(count)
    ]
    streams = "".join(f"[v{i}][{i}:a]" for i in range(count))
    parts.append(f"{streams}concat=n={count}:v=1:a=1[vcat][a]")
    if not captions:
        parts.append("[vcat]null[v]")
        return ";".join(parts)
    parts += _caption_chain("vcat", captions)
    return ";".join(parts)


def _run(args: list[str]) -> None:
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {shlex.join(args)[:200]}\n{done.stderr[-600:]}")


def concat_with_captions(
    clips: list[Path], captions: list[Caption], out: Path, clip_seconds: float = 8.0
) -> Path:
    if not clips:
        raise ValueError("no clips to stitch")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y"]
    for clip in clips:
        args += ["-i", str(clip)]
    args += [
        "-filter_complex",
        build_filter(len(clips), captions, clip_seconds),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    _run(args)
    return out


def duration(path: Path) -> float:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def last_frame(clip: Path, out: Path) -> Path:
    """The final frame of a clip, as the still that the next shot starts from.

    It has to be the END frame: starting the next shot from frame 0 would replay the same motion
    instead of continuing it.
    """
    clip = Path(clip)
    if not clip.is_file():
        raise FileNotFoundError(clip)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-sseof",
            "-0.15",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(out),
        ]
    )
    return out


def parse_lufs(text: str) -> float | None:
    """Integrated loudness out of an ebur128 summary."""
    match = LUFS_RE.search(text or "")
    return float(match.group(1)) if match else None


def lufs(path: Path) -> float | None:
    """Measure a clip's integrated loudness; None when it carries no audio."""
    done = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_lufs(done.stderr)


def normalise(clip: Path, out: Path, start: float | None = None, end: float | None = None) -> Path:
    """One clip at broadcast loudness, optionally trimmed.

    The clips come back from Veo 2.8 dB apart, which reads as the volume jumping at every cut; the trim
    is how a broken hand gets cut out without paying for the shot again.
    """
    clip, out = Path(clip), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(clip)]
    if end is not None:
        args += ["-t", f"{end - (start or 0.0):.3f}"]
    args += ["-af", LOUDNORM]
    # Trimming has to re-encode: an input seek with -c:v copy would start at the nearest keyframe.
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"] if start or end else ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    _run(args)
    return out


def xfade_total(durations: list[float], fade: float = FADE) -> float:
    """Every transition eats one fade out of the running time."""
    return sum(durations) - max(0, len(durations) - 1) * fade


def xfade_starts(durations: list[float], fade: float = FADE) -> list[float]:
    """Where each clip begins once the transitions overlap."""
    starts, clock = [], 0.0
    for index, seconds in enumerate(durations):
        starts.append(clock)
        clock += seconds - (fade if index < len(durations) - 1 else 0.0)
    return starts


def caption_windows(
    durations: list[float], texts: list[str], fade: float = FADE, lead: float = 0.4
) -> list[Caption]:
    """Caption windows on the faded timeline, clear of the dissolves at both ends."""
    starts = xfade_starts(durations, fade)
    windows: list[Caption] = []
    for index, (start, seconds, text) in enumerate(zip(starts, durations, texts, strict=False)):
        if not text:
            continue
        tail = fade if index < len(durations) - 1 else 0.0
        begin = start + lead
        end = start + seconds - tail - lead
        windows.append((round(begin, 2), round(max(begin + 0.5, end), 2), text))
    return windows


def _caption_chain(stage: str, captions: list[Caption]) -> list[str]:
    parts = []
    for index, (start, end, text) in enumerate(captions):
        nxt = "v" if index == len(captions) - 1 else f"vt{index}"
        parts.append(
            f"[{stage}]drawtext=fontfile='{font()}':text='{_escape(text)}':"
            f"fontcolor=white:fontsize=44:box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y=h-260:enable='between(t,{start},{end})'[{nxt}]"
        )
        stage = nxt
    return parts


def build_xfade_filter(
    durations: list[float], captions: list[Caption], fade: float = FADE, transition: str = "fade"
) -> str:
    """Dissolve between shots instead of cutting: the hard concat is what made the old cut jump."""
    count = len(durations)
    parts = [
        f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p,settb=AVTB[v{i}]"
        for i in range(count)
    ]
    parts += [
        f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]" for i in range(count)
    ]
    stage, astage = "v0", "a0"
    for index in range(1, count):
        offset = sum(durations[:index]) - index * fade
        parts.append(
            f"[{stage}][v{index}]xfade=transition={transition}:duration={fade}:offset={offset:.3f}[x{index}]"
        )
        parts.append(f"[{astage}][a{index}]acrossfade=d={fade}:c1=tri:c2=tri[ax{index}]")
        stage, astage = f"x{index}", f"ax{index}"
    parts.append(f"[{astage}]anull[a]")
    if not captions:
        parts.append(f"[{stage}]null[v]")
        return ";".join(parts)
    parts += _caption_chain(stage, captions)
    return ";".join(parts)


def crossfade_with_captions(clips: list[Path], texts: list[str], out: Path, fade: float = FADE) -> Path:
    """Stitch the shots with dissolves and burn one caption per shot."""
    if not clips:
        raise ValueError("no clips to stitch")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    durations = [duration(Path(clip)) for clip in clips]
    captions = caption_windows(durations, texts, fade)
    args = ["ffmpeg", "-v", "error", "-y"]
    for clip in clips:
        args += ["-i", str(clip)]
    args += [
        "-filter_complex",
        build_xfade_filter(durations, captions, fade),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    _run(args)
    return out


def strip_grid(frames: int, cols: int = STRIP_COLS) -> tuple[int, int]:
    """Grid for a strip: a short clip gets a short row instead of a mostly empty sheet."""
    columns = max(1, min(cols, frames))
    return columns, max(1, math.ceil(frames / columns))


def strip(clip: Path, out: Path, fps: float = 2.0) -> Path:
    """Two frames per second of the clip, tiled, so a human can scrub the hands without a player."""
    clip, out = Path(clip), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(duration(clip) * fps))
    columns, rows = strip_grid(frames)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clip),
            "-vf",
            f"fps={fps},scale={STRIP_CELL}:-2,tile={columns}x{rows}",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out),
        ]
    )
    return out


def contact_sheet(clips: list[Path], out: Path, at_second: float = 2.0) -> Path:
    """One frame per clip, side by side, so a human can check the face across the whole cut."""
    if not clips:
        raise ValueError("no clips for a contact sheet")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y"]
    for clip in clips:
        # Seek inside the clip: a fixed 2s lands past the end of a short clip and yields no frame.
        seek = min(at_second, max(0.0, duration(clip) * 0.5))
        args += ["-ss", f"{seek:.2f}", "-i", str(clip)]
    scaled = "".join(f"[{i}:v]scale={SHEET_CELL}:-2,setsar=1[s{i}];" for i in range(len(clips)))
    inputs = "".join(f"[s{i}]" for i in range(len(clips)))
    args += [
        "-filter_complex",
        f"{scaled}{inputs}hstack=inputs={len(clips)}[out]",
        "-map",
        "[out]",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(out),
    ]
    _run(args)
    return out
