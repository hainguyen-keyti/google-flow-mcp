"""Render the LeeLyLy cut: photographs given motion, generated clips trimmed to their best seconds, her voice
over the top, and the small imperfections that keep it from looking machine made.

    uv run python scripts/leelyly/build.py --pilot          # the opening only, what the owner judges first
    uv run python scripts/leelyly/build.py                  # the whole film

Every shot is cut to between 1.4 s and 4.5 s (docs/leelyly/story.md); a generated clip is never laid down
whole, because that even 8 s rhythm is the first thing that reads as AI.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shots import ONSCREEN, segments_for
from timeline import Segment, fit, plan, problems, voice_problems

W, H, FPS = 1080, 1920, 30
VOICE_DIR = Path("out/leelyly/voice")
WORK = Path("out/leelyly/work")
# Measured 2026-09-18 on the delivered Flow clips: they sit near -16 LUFS, so the voice is normalised to the
# same place and the ambient under it is pushed well down.
VOICE_LUFS = -16.0
AMBIENT_DB = -21.0


def run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"{shlex.join(args[:6])} ... failed: {proc.stderr.strip()[-600:]}")


def probe_seconds(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return float(proc.stdout.strip() or 0.0)


# A little shake and grain, applied to every shot: real phone footage is never this steady or this clean.
IMPERFECT = (
    f"scale={W + 40}:{H + 40}:force_original_aspect_ratio=increase,"
    f"crop={W}:{H}:'(iw-ow)/2+3*sin(2*PI*t*0.7)':'(ih-oh)/2+3*cos(2*PI*t*0.53)',"
    "noise=alls=5:allf=t+u,eq=saturation=1.02:contrast=1.03"
)


def render_still(segment: Segment, out: Path) -> Path:
    """A photograph with a slow push in, so it reads as a held shot rather than a slide."""
    frames = max(2, int(segment.seconds * FPS))
    drift = "+0.0012" if segment.id.endswith(("0", "2", "4", "6", "8")) else "-0.0012"
    zoom = f"min(max(zoom{drift},1.0),1.16)"
    chain = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
        f"zoompan=z='{zoom}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"{IMPERFECT},format=yuv420p"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{segment.seconds:.3f}",
            "-i",
            segment.source,
            "-f",
            "lavfi",
            "-t",
            f"{segment.seconds:.3f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            chain,
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
    )
    return out


def render_clip(segment: Segment, out: Path) -> Path:
    """The chosen seconds of a generated clip, never the whole thing."""
    chain = f"{IMPERFECT},format=yuv420p"
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{segment.start_in_source:.3f}",
            "-t",
            f"{segment.seconds:.3f}",
            "-i",
            segment.source,
            "-filter_complex",
            chain,
            "-r",
            str(FPS),
            "-s",
            f"{W}x{H}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-af",
            f"volume={0 if segment.kind == ONSCREEN else AMBIENT_DB}dB",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out),
        ]
    )
    return out


def render_segments(segments: list[Segment]) -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    for segment in segments:
        out = WORK / f"{segment.id}.mp4"
        paths.append(render_still(segment, out) if segment.kind == "still" else render_clip(segment, out))
    return paths


def render_voice(segments: list[Segment], out: Path) -> tuple[Path, float]:
    """Her voice laid where the picture asks for it, with uneven gaps and one normalisation pass."""
    pieces: list[str] = []
    filters: list[str] = []
    starts = plan(segments, hook_seconds=0.0).starts
    used = 0
    for segment, start in zip(segments, starts):
        if not segment.line or segment.kind == ONSCREEN:
            continue
        line = VOICE_DIR / f"{segment.line}.mp3"
        if not line.exists():
            raise FileNotFoundError(f"{segment.line} has no audio; run scripts/leelyly/say.py first")
        pieces += ["-i", str(line)]
        filters.append(f"[{used}:a]adelay={int(start * 1000)}|{int(start * 1000)}[v{used}]")
        used += 1
    if not used:
        raise RuntimeError("no narration lines in this cut")
    mix = "".join(f"[v{i}]" for i in range(used))
    chain = ";".join(filters) + f";{mix}amix=inputs={used}:dropout_transition=0:normalize=0[mixed];"
    chain += f"[mixed]loudnorm=I={VOICE_LUFS}:TP=-1.5:LRA=11[out]"
    run(["ffmpeg", "-y", *pieces, "-filter_complex", chain, "-map", "[out]", "-c:a", "aac", str(out)])
    return out, probe_seconds(out)


def assemble(segments: list[Segment], out: Path) -> Path:
    parts = render_segments(segments)
    listing = WORK / "concat.txt"
    listing.write_text("".join(f"file '{path.resolve()}'\n" for path in parts), encoding="utf-8")
    silent = WORK / "picture.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(silent)])
    voice, _ = render_voice(segments, WORK / "voice.m4a")
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(voice),
            "-filter_complex",
            "[0:a]volume=0dB[amb];[amb][1:a]amix=inputs=2:dropout_transition=0:normalize=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/leelyly")
    ap.add_argument("--pilot", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = segments_for(pilot=args.pilot)
    missing = [segment.source for segment in segments if not Path(segment.source).exists()]
    if missing:
        print("FAIL missing sources:", json.dumps(missing[:6], ensure_ascii=False))
        return 1
    durations = {
        segment.line: probe_seconds(VOICE_DIR / f"{segment.line}.mp3")
        for segment in segments
        if segment.line and segment.kind != ONSCREEN
    }
    silent = [line for line, seconds in durations.items() if seconds <= 0.0]
    if silent:
        print("FAIL these lines have no audio yet:", json.dumps(silent, ensure_ascii=False))
        return 1
    # The shot list says WHICH picture; how long each one holds is arithmetic over the real audio.
    segments = fit(segments, durations)
    narration_total = sum(durations.values())
    found = problems(segments, narration_total=narration_total, target=None if args.pilot else 120.0)
    found += voice_problems(segments, durations)
    for problem in found:
        print("FAIL", problem)
    if found:
        return 1
    target = out_dir / ("pilot.mp4" if args.pilot else "leelyly_story.mp4")
    assemble(segments, target)
    seconds = probe_seconds(target)
    print(json.dumps({"file": str(target), "seconds": round(seconds, 2), "segments": len(segments)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
