"""The try-on selling script: five shots, one locked character, one bedroom.

Each shot only moves the scene variables the bible allows (camera, pose, outfit, expression, time,
lighting); identity and room come from `bible.compose`. Job ids are fixed so a rerun can never submit
the same shot twice (I7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from video.story import bible

ASPECT = "9:16"
MODEL = "veo-lite"
DURATION = 8
COUNT = 1

_CAMISOLE = "ivory lace-trimmed camisole and matching soft shorts, lingerie-catalogue styling"
_CREAM_DRESS = "cream lace slip dress with fine floral embroidery and a small ribbon at the neckline"


@dataclass(frozen=True)
class Shot:
    key: str
    beat: str
    caption: str
    scene: bible.Scene


SHOTS: tuple[Shot, ...] = (
    Shot(
        key="hook",
        beat="stop the scroll: the seller greets the viewer from her room",
        caption="Hôm nay thử đồ mới nha",
        scene=bible.Scene(
            camera="medium environmental portrait, 35mm equivalent, vertical framing, camera at eye level",
            pose="sitting on the edge of the bed, turning to the camera and giving a small wave",
            outfit=_CAMISOLE,
            expression="calm and friendly, a small smile with the cheek dimple",
        ),
    ),
    Shot(
        key="wardrobe",
        beat="show the stock: a full rail of pieces the viewer can buy",
        caption="Cả tủ đều có sẵn size",
        scene=bible.Scene(
            camera="wide shot from the corner of the room, 24mm equivalent, vertical framing",
            pose="standing in front of the open wardrobe, running one hand along the hanging clothes",
            outfit=_CAMISOLE,
            expression="relaxed, focused on the clothes",
        ),
    ),
    Shot(
        key="holdup",
        beat="present the product: one piece held up so the fabric reads on camera",
        caption="Váy ren kem, vải mềm mát",
        scene=bible.Scene(
            camera="medium shot from the front, 50mm equivalent, vertical framing, shallow but natural depth",
            pose="holding the dress on its hanger up against her body and tilting her head to look at it",
            outfit=f"{_CAMISOLE}, holding up a {_CREAM_DRESS}",
            expression="curious, considering the piece",
        ),
    ),
    Shot(
        key="tryon",
        beat="the try-on itself: the dress worn and turned so the cut and drape are visible",
        caption="Lên dáng đẹp lắm nè",
        scene=bible.Scene(
            camera="full-body mirror selfie, phone selfie perspective, vertical framing",
            pose="wearing the dress and turning slowly from front to three-quarter so the skirt moves",
            outfit=_CREAM_DRESS,
            expression="pleased, a soft smile",
        ),
    ),
    Shot(
        key="closing",
        beat="close the sale: she points at the folded pieces laid out for the viewer",
        caption="Inbox chốt đơn nha",
        scene=bible.Scene(
            camera="high-angle shot looking slightly downward, 35mm equivalent, vertical framing",
            pose="sitting on the bed beside neatly folded clothes, gesturing toward them with an open hand",
            outfit=f"{_CREAM_DRESS} with white knee-high socks",
            expression="warm and inviting, looking straight into the camera",
        ),
    ),
)


def job_id(index: int) -> str:
    return f"tryon-{index:02d}"


def plan(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data if data is not None else bible.load()
    return [
        {
            "key": shot.key,
            "job_id": job_id(index),
            "beat": shot.beat,
            "outfit": shot.scene.outfit,
            "prompt": bible.compose(shot.scene, data),
        }
        for index, shot in enumerate(SHOTS, start=1)
    ]


def captions() -> list[tuple[float, float, str]]:
    """Caption windows laid back to back, one per shot, in cut order."""
    return [
        (index * float(DURATION), (index + 1) * float(DURATION), shot.caption)
        for index, shot in enumerate(SHOTS)
    ]
