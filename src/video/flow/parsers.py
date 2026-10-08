"""Pure parsers for the migrated host's batchexecute payloads (shapes measured 2026-09-12)."""

from __future__ import annotations

import re
from typing import Any

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def flat(text: str | None) -> str:
    """Whitespace collapsed, trimmed and casefolded: how a prompt is compared everywhere, since Flow's boxes
    collapse what was typed (a prompt with a leading space was typed twice into the editor, review 2026-10-08)."""
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _at(node: Any, *path: int) -> Any:
    for index in path:
        if not isinstance(node, list) or index >= len(node):
            return None
        node = node[index]
    return node


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID.match(value))


def _epoch(ts: Any) -> int | None:
    first = _at(ts, 0)
    return first if isinstance(first, int) else None


def _url(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def projects(payload: Any) -> list[dict[str, Any]]:
    # An account with no projects gets UpteDb as `[]` (measured 2026-09-28), which is an answer, not damage.
    if payload == []:
        return []
    cards = _at(payload, 0)
    if not isinstance(cards, list):
        raise TypeError("UpteDb: expected [[card, ...]]")
    out = []
    for card in cards:
        pid, meta = _at(card, 0), _at(card, 1)
        if not (isinstance(pid, str) and pid):
            continue
        out.append(
            {
                "id": pid,
                "title": _str(_at(meta, 0)),
                "created": _epoch(_at(meta, 2)),
                "thumbnail_url": _url(_at(meta, 3)),
                "cover_media_id": _str(_at(meta, 4)),
            }
        )
    return out


def project_meta(payload: Any) -> dict[str, Any]:
    pid = _at(payload, 0)
    if not _uuid(pid):
        raise TypeError("ngNC2: expected [project_id, [title, ...], ...]")
    return {"id": pid, "title": _str(_at(payload, 1, 0))}


def credits(payload: Any) -> dict[str, Any]:
    balance = _at(payload, 0)
    if not isinstance(balance, int):
        raise TypeError("nzlxg: expected [balance, ...]")
    return {"balance": balance, "raw": payload}


def video_models(payload: Any) -> list[str]:
    arms = _at(payload, 0)
    if not isinstance(arms, list):
        raise TypeError("yBhWQ: expected [[[model, flag], ...]]")
    return [arm[0] for arm in arms if isinstance(arm, list) and isinstance(_at(arm, 0), str)]


def characters_from_listing(payload: Any) -> list[dict[str, Any]]:
    """Characters ride inside the project listing: Zzl0ze[5] holds one entry per entity
    ([_, entity_id, _, [_, name, ...], portrait_workflow_id, ...]); None when the project has none.

    The entry names the portrait by its WORKFLOW id, which flow_download rejects (measured 2026-09-15), so the
    media id is looked up among the records of the same listing, and left None when that record is absent."""
    entries = _at(payload, 5)
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise TypeError("Zzl0ze[5]: expected a list of character entries")
    media_by_workflow = {record["workflow_id"]: record["id"] for record in records(payload)}
    out = []
    for entry in entries:
        entity_id = _at(entry, 1)
        if not _uuid(entity_id):
            continue
        portrait = _str(_at(entry, 4))
        out.append(
            {
                "entity_id": entity_id,
                "name": _str(_at(entry, 3, 1)),
                "portrait_media_id": media_by_workflow.get(portrait),
                "portrait_workflow_id": portrait,
            }
        )
    return out


def upload_record(payload: Any) -> dict[str, Any]:
    """maseQ answers an upload with [[workflow_id, project_id, media_id, version, _, details, ...]]: the same record
    shape the listing uses, where record[2] is the media id every other tool accepts (measured 2026-09-15). The
    version was "CAE" and is null since (measured 2026-10-07 on 6177057c: the listing and flow_download took
    record[2] as before); any other value is not an upload's answer."""
    record = _at(payload, 0)
    if not (isinstance(record, list) and _uuid(_at(record, 0)) and _at(record, 3) in ("CAE", None)):
        raise TypeError("maseQ: expected [[workflow_id, project_id, media_id, 'CAE' or None, ...]]")
    size = _at(record, 5, 13)
    return {
        "media_id": _str(_at(record, 2)),
        "project_id": _str(_at(record, 1)),
        "workflow_id": record[0],
        "size_bytes": size if isinstance(size, int) else None,
    }


def scenes_from_listing(payload: Any) -> list[dict[str, Any]]:
    """Scenes ride in Zzl0ze[4] as [scene_id, title, _, [created], [updated], status, _, flags]; a trashed
    scene stays listed with flags == [true] (measured 2026-09-12 after the tile's 'Move to trash')."""
    entries = _at(payload, 4)
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise TypeError("Zzl0ze[4]: expected a list of scene entries")
    out = []
    for entry in entries:
        scene_id = _at(entry, 0)
        if not _uuid(scene_id):
            continue
        flags = _at(entry, 7)
        out.append(
            {
                "scene_id": scene_id,
                "title": _str(_at(entry, 1)),
                "created": _epoch(_at(entry, 3)),
                "updated": _epoch(_at(entry, 4)),
                "trashed": isinstance(flags, list) and bool(flags) and flags[0] is True,
            }
        )
    return out


def voices_from_listing(payload: Any) -> list[dict[str, Any]]:
    """Preset voices ride in Zzl0ze[3] as [id, 3, name, ...] entries next to the project's assets."""
    assets = _at(payload, 3)
    if not isinstance(assets, list):
        return []
    return [
        {"id": asset[0], "name": asset[2]}
        for asset in assets
        if isinstance(asset, list)
        and _at(asset, 1) == 3
        and isinstance(_at(asset, 0), str)
        and isinstance(_at(asset, 2), str)
    ]


def custom_voices(payload: Any) -> list[dict[str, Any]]:
    """The voices saved on this account with character_make_voice that the project grid still lists.

    Measured 2026-10-01 on LilyVoice: such a voice is a record whose [10][0] holds the words it was made from, its
    description, its direction, the speech model, the audio type ('audio/wav') and [[the preset it is built on, its
    name]]; a request names it by its workflow id. It goes by the title of its grid entry, the name flow_media shows.
    """
    descriptors, records_ = _split_listing(payload)
    titles = {_at(descriptor, 0): _str(_at(descriptor, 3, 0)) for descriptor in descriptors}
    out = []
    for record in records_:
        arm = _at(record, 10, 0)
        mime = _at(arm, 10)
        if not (isinstance(mime, str) and mime.startswith("audio/")) or record[2] not in titles:
            continue
        names = _at(arm, 11, 0)
        out.append(
            {
                "workflow_id": _str(_at(record, 0)),
                "media_id": record[2],
                "name": titles[record[2]] or _str(_at(names, 1)),
                "base": _str(_at(names, 0)),
            }
        )
    return out


def _record_model(record: list[Any]) -> str | None:
    arm = _at(record, 7, 0)
    if isinstance(arm, str) and " " not in arm:
        return arm
    return _str(_at(record, 5, 6, 1, 0, 0))


def _record_fields(record: list[Any]) -> dict[str, Any]:
    details = _at(record, 5)
    status = _at(details, 8, 0)
    size = _at(details, 13)
    fields: dict[str, Any] = {
        "status": status if isinstance(status, int) else None,
        "size_bytes": size if isinstance(size, int) else None,
    }
    if len(record) >= 8:
        fields["kind"] = "video"
        fields["prompt"] = _str(_at(details, 1))
        fields["model"] = _record_model(record)
        fields["url"] = _url(_at(record, 7, 0, 8)) or _url(_at(details, 10)) or _url(_at(details, 5))
    else:
        fields["kind"] = "image"
        fields["prompt"] = _str(_at(details, 6, 2, 0, 0))
        fields["model"] = None
        fields["url"] = _url(_at(details, 10)) or _url(_at(details, 5))
    return fields


def _split_listing(payload: Any) -> tuple[list[Any], list[list[Any]]]:
    # An ABSENT section means an empty project: a project with nothing in it carries no descriptors and
    # no records, and `_at` answers None for a missing index. A section that is PRESENT but not a list is
    # a broken payload and still raises. Collapsing the two would silence real corruption, and the credits
    # reply `[840, 1, 2, 2, None, 840]` is exactly that second case (ints where the lists belong).
    # Measured 2026-09-13: flow_media raised against a fresh project, so an agent could create a project
    # and then not be able to look inside it.
    descriptors = _at(payload, 1)
    records = _at(payload, 2)
    descriptors = [] if descriptors is None else descriptors
    records = [] if records is None else records
    if not isinstance(descriptors, list) or not isinstance(records, list):
        raise TypeError("Zzl0ze: expected [_, [descriptor, ...], [record, ...], ...]")
    kept = [r for r in records if isinstance(r, list) and len(r) >= 7 and _uuid(_at(r, 2))]
    return descriptors, kept


def _current_version(records: list[list[Any]]) -> dict[str, list[Any]]:
    """One record per media id: the newest finished version (an Omni edit adds a 'CAI' record for the
    same media id), else the newest record."""
    by_media: dict[str, list[list[Any]]] = {}
    for record in records:
        by_media.setdefault(record[2], []).append(record)
    chosen: dict[str, list[Any]] = {}
    for media_id, versions in by_media.items():
        versions.sort(key=lambda r: _epoch(_at(r, 5, 0)) or 0, reverse=True)
        chosen[media_id] = next((v for v in versions if _record_fields(v)["url"]), versions[0])
    return chosen


def media(payload: Any) -> list[dict[str, Any]]:
    descriptors, records_ = _split_listing(payload)
    by_media = _current_version(records_)
    out = []
    for descriptor in descriptors:
        media_id, info, project_id = _at(descriptor, 0), _at(descriptor, 3), _at(descriptor, 4)
        if not _uuid(media_id):
            continue
        item: dict[str, Any] = {
            "id": media_id,
            "project_id": _str(project_id),
            "title": _str(_at(info, 0)),
            "created": _epoch(_at(info, 1)),
            "workflow_id": _str(_at(info, 4)),
            "kind": None,
            "status": None,
            "prompt": None,
            "model": None,
            "size_bytes": None,
            "url": None,
        }
        record = by_media.get(media_id)
        if record is not None:
            item.update(_record_fields(record))
        out.append(item)
    return out


def records(payload: Any) -> list[dict[str, Any]]:
    """Every generation record in Zzl0ze[2]: the ones without a grid descriptor (clips inside a scene,
    character portrait candidates) and every version of an edited media (type 'CAI' rows share the
    media id with the original 'CAE' row); `listed` tells whether the grid shows the media."""
    descriptors, records_ = _split_listing(payload)
    listed = {_at(d, 0) for d in descriptors}
    out = []
    for record in records_:
        out.append(
            {
                "id": record[2],
                "project_id": _str(_at(record, 1)),
                "workflow_id": _str(_at(record, 0)),
                "type": _str(_at(record, 3)),
                "created": _epoch(_at(record, 5, 0)),
                **_record_fields(record),
                "listed": record[2] in listed,
            }
        )
    return out


# Measured 2026-10-01 on the 169 records of one project: the family of a generation and the role of an input image.
_RECIPE_KINDS = {2: "frames", 3: "ingredients", 4: "derived", 5: "extend"}
_FRAME_SLOTS = {1: "start", 2: "end"}
_REFERENCE_ROLE = 4


def _named(items: Any) -> list[str]:
    return [item[0] for item in items or [] if isinstance(item, list) and item and isinstance(item[0], str)]


def _recipe(record: list[Any]) -> dict[str, Any] | None:
    arm = _at(record, 5, 6, 1)
    head = _at(arm, 0)
    key = _at(head, 0)
    if not isinstance(key, str):
        return None
    family = _at(head, 1)
    inputs = [
        each
        for each in _at(arm, 1) or []
        if isinstance(each, list) and isinstance(_at(each, 1), int) and isinstance(_at(each, 2), str)
    ]
    return {
        "model_key": key,
        "kind": _RECIPE_KINDS.get(family, "unknown") if isinstance(family, int) else "unknown",
        "frames": [
            {"slot": _FRAME_SLOTS[each[1]], "workflow_id": each[2]}
            for each in inputs
            if each[1] in _FRAME_SLOTS
        ],
        "reference_images": [each[2] for each in inputs if each[1] == _REFERENCE_ROLE],
        "source_workflow_id": _str(_at(arm, 2, 0, 3)),
        "voices": _named(_at(arm, 7)),
        "characters": _named(_at(arm, 8)),
    }


def recipes(payload: Any) -> dict[str, dict[str, Any]]:
    """What each generation was made from, by workflow id, as the listing keeps it at record[5][6][1].

    Measured 2026-10-01: [model key, family, [mode], ...], then the input images as [None, role, workflow id] (1 the
    start frame, 2 the end frame, 4 a reference), the source clip of an edit, an upscale or an extend at [2][0][3],
    the voices at [7] (a custom voice's workflow id or a preset's lowercase name) and the characters at [8] (entity
    ids). An image record keeps no recipe and is left out.
    """
    _, records_ = _split_listing(payload)
    found = ((record[0], _recipe(record)) for record in records_ if isinstance(_at(record, 0), str))
    return {workflow: recipe for workflow, recipe in found if recipe is not None}


def tools(payload: Any) -> list[dict[str, Any]]:
    entries = _at(payload, 0)
    if not isinstance(entries, list):
        raise TypeError("tRARke: expected [[tool, ...]]")
    out = []
    for entry in entries:
        tool_id, name = _at(entry, 0), _at(entry, 2)
        if not (isinstance(tool_id, str) and tool_id and isinstance(name, str)):
            continue
        tags = _at(entry, 22)
        out.append(
            {
                "id": tool_id,
                "name": name,
                "description": _str(_at(entry, 3)),
                "author": _str(_at(entry, 17)),
                "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
                "path": _str(_at(entry, 11)),
                "icon_url": _url(_at(entry, 15)),
                "thumbnail_url": _url(_at(entry, 21)),
            }
        )
    return out
