"""Try-on script v2: two continuous blocks instead of six unrelated clips.

Why it is shaped like this, all measured:

- The face and the garment cannot be locked in the same generation. A character chip keeps the face but
  lets Veo invent the clothes; r2v with the product photo keeps the clothes but returns a different woman.
  So each shot takes the route that fits its job: `character` for shots where the face matters, `product`
  for the close-up with nobody in it, and one paid Omni edit on `reveal` which keeps the face and changes
  only the clothing.
- Frames mode pins the first frame from a project image, so `wardrobe` starts from the last frame of the
  hook and inherits the room, the outfit, the face and the position.
- Every take with a person in it is generated in her own everyday clothes and dressed afterwards by an Omni
  edit. Asking Veo for the set in words is refused: Flow takes the submit, creates no job, charges nothing
  and says nothing (measured 2026-09-13 on `reveal` and three times on `pose`). The same shot worded with
  the everyday outfit generated first time. So the start frame is cut from the clip BEFORE its edit, and
  each dressed take pays its own edit.
- Two real cuts remain, both cuts a real edit would make anyway: into the product close-up and into the
  reveal.
- Veo mangles hands that manipulate fabric (Plan 2: tryon-03 lost a hand, tryon-05 fused fingers), so
  poses keep hands still or out of frame, and the two shots that must touch something are marked
  `hands_risk` and get a second take to choose from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from video.story import bible, product

ASPECT = "9:16"
MODEL = "veo-lite"
PRICE = 10
FADE = 0.5

_BEFORE = "a plain oversized white cotton t-shirt and soft shorts, her own everyday clothes"
_PRODUCT = product.DESCRIPTION


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
    edit_to_product: bool = False


SHOTS: tuple[Shot, ...] = (
    Shot(
        key="hook",
        beat="stop the scroll: she greets the viewer from her own room",
        caption="Hôm nay thử đồ mới nha",
        mode="character",
        start_from=None,
        scene=bible.Scene(
            camera="medium environmental portrait, 35mm equivalent, vertical framing, camera locked off",
            pose="sitting on the edge of the bed, both hands resting still on her lap, turning her head to"
            " the camera",
            outfit=_BEFORE,
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
            outfit=_BEFORE,
            expression="relaxed, looking at the clothes",
        ),
    ),
    Shot(
        key="fabric",
        beat="prove the quality: the lace read close up so the buyer can see the fabric",
        caption="Ren mềm, vải mát",
        mode="product",
        start_from=None,
        product=True,
        scene=bible.Scene(
            camera="slow close-up push along the garment on its hanger, 50mm equivalent, vertical framing",
            pose="no hands in frame, only the set hanging still on the rail as the camera drifts across it",
            outfit=f"{_PRODUCT}, hanging on a wooden hanger, seen in close detail",
            expression="not visible, she is out of frame",
        ),
    ),
    Shot(
        key="reveal",
        beat="the reveal: the same set now worn, the moment the video is selling",
        caption="Lên dáng đẹp lắm nè",
        mode="character",
        start_from=None,
        edit_to_product=True,
        scene=bible.Scene(
            camera="full-length mirror shot, phone held low and still, vertical framing",
            pose="standing in front of the mirror, arms hanging naturally at her sides",
            outfit=_BEFORE,
            expression="pleased, a soft smile",
        ),
    ),
    Shot(
        key="pose",
        edit_to_product=True,
        beat="show the fit: one slow turn so the cut and the drape read on camera",
        caption="Xoay một vòng cho dễ hình dung",
        hands_risk=True,
        start_from="reveal",
        scene=bible.Scene(
            camera="the same mirror shot continues, camera stays where it is, vertical framing",
            pose="turning slowly from front to three-quarter, one hand settling on her hip, the other still",
            outfit=_BEFORE,
            expression="calm, looking at her own reflection",
        ),
    ),
    Shot(
        key="closing",
        edit_to_product=True,
        beat="close the sale: she looks back at the viewer and invites the order",
        caption="Inbox chốt đơn nha",
        start_from="pose",
        scene=bible.Scene(
            camera="the same mirror shot, camera holds, vertical framing",
            pose="turning back to face the camera and standing still, both hands relaxed at her sides",
            outfit=_BEFORE,
            expression="warm and inviting, looking straight into the camera",
        ),
    ),
)

EXTRA_TAKES = sum(1 for shot in SHOTS if shot.hands_risk)
EDIT_PRICE = 20


def by_key(key: str) -> Shot:
    for shot in SHOTS:
        if shot.key == key:
            return shot
    raise KeyError(key)


def job_id(index: int) -> str:
    return f"tryon2-{index:02d}"


def total_seconds() -> int:
    return sum(shot.duration for shot in SHOTS)


def takes(shot: Shot) -> int:
    return 2 if shot.hands_risk else 1


def expected_credits() -> int:
    """Every take is generated, and every take of a shot that shows the set is also dressed by an edit."""
    generated = sum(takes(shot) for shot in SHOTS)
    edits = sum(takes(shot) for shot in SHOTS if shot.edit_to_product)
    return generated * PRICE + edits * EDIT_PRICE


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
            "edit_to_product": shot.edit_to_product,
            "hands_risk": shot.hands_risk,
            "duration": shot.duration,
            "prompt": bible.compose(shot.scene, data),
        }
        for index, shot in enumerate(SHOTS, start=1)
    ]
