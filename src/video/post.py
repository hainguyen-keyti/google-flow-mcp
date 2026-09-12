"""ffmpeg post: stitch the shots into one vertical video, burn the selling captions, build a QC sheet.

Captions are burned here rather than asked of Veo: editing a line costs nothing, regenerating a clip
costs credits.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

WIDTH, HEIGHT, FPS = 720, 1280, 24
SHEET_CELL = 240
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
    stage = "vcat"
    for index, (start, end, text) in enumerate(captions):
        nxt = "v" if index == len(captions) - 1 else f"vt{index}"
        parts.append(
            f"[{stage}]drawtext=fontfile='{font()}':text='{_escape(text)}':"
            f"fontcolor=white:fontsize=44:box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y=h-260:enable='between(t,{start},{end})'[{nxt}]"
        )
        stage = nxt
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
