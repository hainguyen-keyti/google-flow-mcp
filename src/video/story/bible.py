"""Compose Flow prompts from the owner's visual bible.

Every lock sentence is read out of `assets/character/character_room_design_system_v1.json` rather than
retyped here: if the owner edits the bible, the prompts follow. Only the scene variables listed in the
bible's own `agent_prompt_template.scene_variables` may change between shots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "character"
BIBLE_JSON = ASSETS / "character_room_design_system_v1.json"
REFERENCES = {
    "portrait": "01_portrait_closeup.png",
    "angles": "04_character_multi_angle.png",
    "wardrobe": "07_wardrobe_room_montage.png",
}


def reference_images() -> dict[str, Path]:
    return {name: ASSETS / filename for name, filename in REFERENCES.items()}


def load(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else BIBLE_JSON
    return json.loads(target.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Scene:
    camera: str
    pose: str
    outfit: str
    expression: str
    time_of_day: str = "evening"
    lighting: str = "warm practical lamps mixed with soft ambient room light"


def _join(parts: list[str]) -> str:
    return "; ".join(p.strip().rstrip(".") for p in parts if p)


def character_lock(data: dict[str, Any]) -> str:
    character = data["character"]
    face, hair, tattoos = character["face"], character["hair"], character["tattoos"]
    return _join(
        [
            f"same fictional adult woman, {character['visual_archetype']}",
            f"face: {face['overall']}",
            *face["identity_landmarks"],
            f"skin: {face['skin']['texture']}, {face['skin']['finish']}",
            f"hair: {hair['color']}, {hair['length']}, {hair['density']}, {hair['texture']}",
            f"chest tattoo: {tattoos['chest']['motif']}, {tattoos['chest']['placement']}",
            f"upper arm tattoo: {tattoos['upper_arm']['style']}",
            f"necklace: {character['jewelry']['necklace']}, {character['jewelry']['rule']}",
            f"body: {character['body']['silhouette']}",
        ]
    )


def room_lock(data: dict[str, Any]) -> str:
    room = data["room"]
    return _join(
        [
            f"same {room['type']}, {room['mood']}",
            *room["layout"].values(),
            f"photo wall: {room['decor']['photo_wall']}",
            f"bedding: {room['decor']['bed']}",
            f"walls and floor: {room['architecture']['walls']}, {room['architecture']['floor']}",
        ]
    )


def negatives(data: dict[str, Any]) -> str:
    return _join(data["negative_constraints"])


def compose(scene: Scene, data: dict[str, Any] | None = None) -> str:
    for field in ("camera", "pose", "outfit", "expression", "time_of_day", "lighting"):
        if not getattr(scene, field).strip():
            raise ValueError(f"scene.{field} is empty; every scene variable must be spelled out")
    data = data if data is not None else load()
    photography = data["photography"]
    return (
        f"SHOT: {scene.camera}. {scene.pose}. Wearing {scene.outfit}. "
        f"Expression: {scene.expression}. {scene.time_of_day}, {scene.lighting}.\n"
        f"CHARACTER (locked, do not redesign): {character_lock(data)}.\n"
        f"ROOM (locked, do not rearrange): {room_lock(data)}.\n"
        f"STYLE: {photography['style']}, {photography['realism']}.\n"
        f"AVOID: {negatives(data)}."
    )
