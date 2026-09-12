"""Try-on script v2: two continuous blocks instead of six unrelated clips.

Why it is shaped like this, all measured:

- Frames mode pins the first frame from a project image but has NO ingredients button (T1), so the
  character entity can only be attached on a block opener. Every other shot starts from the last frame of
  the shot before it, which carries the room, the outfit, the face and the position across the join.
- The garment changes once, so there is exactly ONE real cut (fabric -> reveal). That cut is the reveal,
  which is what a try-on video wants anyway.
- Veo mangles hands that manipulate fabric (Plan 2: tryon-03 lost a hand, tryon-05 fused fingers), so
  poses keep hands still or out of frame, and the two shots that must touch something are marked
  `hands_risk` and get a second take to choose from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from video.story import bible

ASPECT = "9:16"
MODEL = "veo-lite"
PRICE = 10
FADE = 0.5

_CAMISOLE = "ivory lace-trimmed camisole and matching soft shorts, lingerie-catalogue styling"
_DRESS = "the cream lace slip dress from the reference image, worn"


@dataclass(frozen=True)
class Shot:
    key: str
    beat: str
    caption: str
    scene: bible.Scene
    duration: int = 8
    hands_risk: bool = False
    mode: str = "frames"
    start_from: str | None = None
    product: bool = False


SHOTS: tuple[Shot, ...] = (
    Shot(
        key="hook",
        beat="stop the scroll: she greets the viewer from her own room",
        caption="Hôm nay thử đồ mới nha",
        mode="ingredients",
        start_from=None,
        scene=bible.Scene(
            camera="medium environmental portrait, 35mm equivalent, vertical framing, camera locked off",
            pose="sitting on the edge of the bed, both hands resting still on her lap, turning her head to"
            " the camera",
            outfit=_CAMISOLE,
            expression="calm and friendly, a small smile with the cheek dimple",
        ),
    ),
    Shot(
        key="wardrobe",
        beat="show the stock: the rail the viewer is buying from",
        caption="Cả tủ đều có sẵn size",
        hands_risk=True,
        start_from="hook",
        scene=bible.Scene(
            camera="the same room, camera pans slowly left to the open wardrobe, 35mm equivalent, vertical",
            pose="standing at the wardrobe, one hand resting flat on the rail, the other arm hanging still",
            outfit=_CAMISOLE,
            expression="relaxed, looking at the clothes",
        ),
    ),
    Shot(
        key="fabric",
        beat="prove the quality: the lace read close up so the buyer can see the fabric",
        caption="Ren mềm, đứng phom",
        start_from="wardrobe",
        scene=bible.Scene(
            camera="slow close-up push along the hanging cream lace dress, 50mm equivalent, vertical",
            pose="no hands in frame, only the dress hanging still on the rail as the camera drifts across it",
            outfit="the cream lace slip dress hanging on its hanger, seen in close detail",
            expression="not visible, she is out of frame",
        ),
    ),
    Shot(
        key="reveal",
        beat="the reveal: the same dress now worn, the one deliberate cut of the video",
        caption="Lên dáng đẹp lắm nè",
        mode="ingredients",
        start_from=None,
        product=True,
        scene=bible.Scene(
            camera="full-length mirror shot, phone held low and still, vertical framing",
            pose="standing in front of the mirror wearing the dress, arms hanging naturally at her sides",
            outfit=_DRESS,
            expression="pleased, a soft smile",
        ),
    ),
    Shot(
        key="pose",
        beat="show the fit: one slow turn so the cut and the drape read on camera",
        caption="Xoay một vòng cho dễ hình dung",
        hands_risk=True,
        start_from="reveal",
        scene=bible.Scene(
            camera="the same mirror shot continues, camera stays where it is, vertical framing",
            pose="turning slowly from front to three-quarter, one hand settling on her hip, the other still",
            outfit=_DRESS,
            expression="calm, looking at her own reflection",
        ),
    ),
    Shot(
        key="closing",
        beat="close the sale: she looks back at the viewer and invites the order",
        caption="Inbox chốt đơn nha",
        start_from="pose",
        scene=bible.Scene(
            camera="the same mirror shot, camera holds, vertical framing",
            pose="turning back to face the camera and standing still, both hands relaxed at her sides",
            outfit=_DRESS,
            expression="warm and inviting, looking straight into the camera",
        ),
    ),
)

EXTRA_TAKES = sum(1 for shot in SHOTS if shot.hands_risk)


def by_key(key: str) -> Shot:
    for shot in SHOTS:
        if shot.key == key:
            return shot
    raise KeyError(key)


def job_id(index: int) -> str:
    return f"tryon2-{index:02d}"


def total_seconds() -> int:
    return sum(shot.duration for shot in SHOTS)


def expected_credits() -> int:
    return (len(SHOTS) + EXTRA_TAKES) * PRICE


def captions() -> list[tuple[float, float, str]]:
    windows, clock = [], 0.0
    for shot in SHOTS:
        windows.append((clock, clock + shot.duration, shot.caption))
        clock += shot.duration
    return windows


def plan(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data if data is not None else bible.load()
    return [
        {
            "key": shot.key,
            "job_id": job_id(index),
            "beat": shot.beat,
            "caption": shot.caption,
            "mode": shot.mode,
            "start_from": shot.start_from,
            "product": shot.product,
            "hands_risk": shot.hands_risk,
            "duration": shot.duration,
            "prompt": bible.compose(shot.scene, data),
        }
        for index, shot in enumerate(SHOTS, start=1)
    ]
