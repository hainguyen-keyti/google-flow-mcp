"""Pure parsers for the migrated host's batchexecute payloads (shapes measured 2026-09-12)."""

from __future__ import annotations

import re
from typing import Any

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


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


def characters(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise TypeError("WuwhI: expected a list")
    return [{"id": _str(_at(c, 0)), "name": _str(_at(c, 1)), "raw": c} for c in payload]


def characters_or_empty(
    frames: dict[str, list[Any]], *, character_page_rendered: bool
) -> list[dict[str, Any]]:
    """A project with no characters renders the New-character page and emits no list rpc (measured
    2026-09-12 on all 16 projects), so that page is the evidence for an empty list."""
    if frames.get("WuwhI"):
        return characters(frames["WuwhI"][0])
    if character_page_rendered:
        return []
    raise LookupError(f"no character list rpc and no character page; saw {sorted(frames)}")


def _record_model(record: list[Any]) -> str | None:
    arm = _at(record, 7, 0)
    if isinstance(arm, str) and " " not in arm:
        return arm
    return _str(_at(record, 5, 6, 1, 0, 0))


def media(payload: Any) -> list[dict[str, Any]]:
    descriptors, records = _at(payload, 1), _at(payload, 2)
    if not isinstance(descriptors, list) or not isinstance(records, list):
        raise TypeError("Zzl0ze: expected [_, [descriptor, ...], [record, ...], ...]")
    by_media: dict[str, list[Any]] = {}
    for record in records:
        if isinstance(record, list) and len(record) >= 7 and _uuid(_at(record, 2)):
            by_media[record[2]] = record
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
            details = _at(record, 5)
            status = _at(details, 8, 0)
            size = _at(details, 13)
            item["status"] = status if isinstance(status, int) else None
            item["size_bytes"] = size if isinstance(size, int) else None
            if len(record) >= 8:
                item["kind"] = "video"
                item["prompt"] = _str(_at(details, 1))
                item["model"] = _record_model(record)
                item["url"] = _url(_at(record, 7, 0, 8)) or _url(_at(details, 10)) or _url(_at(details, 5))
            else:
                item["kind"] = "image"
                item["prompt"] = _str(_at(details, 6, 2, 0, 0))
                item["url"] = _url(_at(details, 10)) or _url(_at(details, 5))
        out.append(item)
    return out


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
