"""On-disk inventory of recoverable library entries and their schedule snapshots."""

import json
import re
from pathlib import Path


_BATCH_NAME = re.compile(r"([a-z0-9][a-z0-9-]{0,63})__(\d{8}-\d{6}(?:-\d{6})?)")


def batches(directory: Path) -> list[tuple[str, str, Path, Path]]:
    """List body/metadata pairs newest first, including interrupted or legacy moves."""
    # Derive identity from the filename. A corrupt sidecar must not select another file.
    pairs = {}
    if directory.is_symlink():
        raise OSError("Linked trash directory")
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
    if path.is_symlink():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def stats(directory: Path, limit: int) -> dict:
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
        items.append({
            "batch": body.stem,
            "theme_id": theme_id,
            "name": meta.get("name") or read_record(body).get("name") or theme_id,
            "deleted_at": meta.get("deleted_at") or stamp,
            "bytes": sizes.get(body, 0),
            "schedule_zones": sum(len(bucket) for bucket in refs if isinstance(bucket, dict)),
            "restorable": body in sizes,
        })
    return {"count": len(items), "files": files, "bytes": total, "limit": limit, "items": items}


def remove_batch(body: Path, sidecar: Path) -> tuple[int, int]:
    count = size = 0
    for path in (body, sidecar):
        if path.is_symlink() or not path.is_file():
            continue
        length = path.stat().st_size
        path.unlink()
        count += 1
        size += length
    return count, size
