"""On-disk inventory of the theme library's recycle bin.

A deleted library entry is moved to ``<data_dir>/trash/<theme_id>__<stamp>.json``
next to a ``<theme_id>__<stamp>.meta.json`` sidecar that records its display
name, deletion time and the schedule zones that referenced it. These helpers
only do synchronous file IO; callers run them in a worker thread (every entry
is dispatched through ``asyncio.to_thread``) under ``_WRITE_LOCK``.
"""

import json
import re
from pathlib import Path


_BATCH_NAME = re.compile(r"([a-z0-9][a-z0-9-]{0,63})__(\d{8}-\d{6}(?:-\d{6})?)")


def batches(directory: Path) -> list[tuple[str, str, Path, Path]]:
    """List ``(stamp, theme_id, body, sidecar)`` tuples, newest first.

    A batch is listed when either its body or its sidecar is present, so an
    interrupted move (sidecar written, body not moved yet) is still found and
    can be pruned or cleared. Identity comes from the file name only: a corrupt
    sidecar must never make a restore pick up another file.
    """
    pairs = {}
    if directory.is_symlink():
        raise OSError("Linked trash directory")
    if not directory.is_dir():
        return []
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        base = path.name.removesuffix(".json").removesuffix(".meta")
        match = _BATCH_NAME.fullmatch(base)
        if match:
            theme_id, stamp = match.groups()
            pairs[base] = (stamp, theme_id, directory / (base + ".json"), directory / (base + ".meta.json"))
    return sorted(pairs.values(), reverse=True)


def read_record(path: Path) -> dict:
    """Read a JSON object; anything missing, linked, unreadable or not an object yields ``{}``."""
    if path.is_symlink():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def stats(directory: Path, limit: int) -> dict:
    """Summarize the recycle bin for the panel: totals plus one item per batch, newest first."""
    items = []
    total = files = 0
    for stamp, theme_id, body, sidecar in batches(directory):
        sizes = {}
        for path in (body, sidecar):
            if not path.is_symlink() and path.is_file():
                try:
                    sizes[path] = path.stat().st_size
                except FileNotFoundError:
                    continue
        total += sum(sizes.values())
        files += len(sizes)
        meta = read_record(sidecar)
        schedule = meta.get("schedule")
        refs = [schedule.get(key) for key in ("zones", "weekend_zones")] if isinstance(schedule, dict) else []
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            # Missing sidecar (interrupted move or manual cleanup): fall back to the body's own name.
            name = read_record(body).get("name") if body in sizes else None
        deleted_at = meta.get("deleted_at")
        items.append({
            "batch": body.stem,
            "theme_id": theme_id,
            "name": name if isinstance(name, str) and name else theme_id,
            "deleted_at": deleted_at if isinstance(deleted_at, str) and deleted_at else stamp,
            "bytes": sizes.get(body, 0),
            "schedule_zones": sum(len(bucket) for bucket in refs if isinstance(bucket, dict)),
            "restorable": body in sizes,
        })
    return {"count": len(items), "files": files, "bytes": total, "limit": limit, "items": items}


def remove_batch(body: Path, sidecar: Path) -> tuple[int, int]:
    """Permanently delete one batch; return ``(files_removed, bytes_removed)``. Links are left alone."""
    count = size = 0
    for path in (body, sidecar):
        if path.is_symlink() or not path.is_file():
            continue
        length = path.stat().st_size
        path.unlink()
        count += 1
        size += length
    return count, size
