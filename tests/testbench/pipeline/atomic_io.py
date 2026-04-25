"""Unified chokepoint for crash-safe file writes.

Background
----------
Before this module, ``_atomic_write_bytes`` / ``_atomic_write_json`` were
**duplicated across 6 locations**:

* ``pipeline/persistence.py``           (had fsync — P21.1 G1 fix)
* ``routers/memory_router.py``           (NO fsync)
* ``pipeline/memory_runner.py``          (NO fsync — comment even claimed "same convention as memory_router")
* ``pipeline/script_runner.py``          (NO fsync — comment claimed "防掉电" but code didn't deliver)
* ``pipeline/scoring_schema.py``         (NO fsync)
* ``pipeline/snapshot_store.py`` _spill  (NOT EVEN atomic — raw ``gzip.open + write``)

P21.1's G1 fix only covered the persistence.py copy. The other 5 were
silent ticking time bombs: a BSOD / power loss during a memory-editor
save / snapshot cold spill / schema save could leave the file "present
but zero bytes", which deserializes as empty and can silently destroy
user data.

P24 §4.1.2 collapses all 6 sites into this one module.

Public API
----------
* :func:`atomic_write_bytes(path, data)`          — binary, fsync'd
* :func:`atomic_write_json(path, data)`            — UTF-8 JSON, fsync'd
* :func:`atomic_write_gzip_json(path, data)`       — gzip'd JSON, fsync'd
  (was the ONLY non-atomic writer before this module — highest-risk fix)

All three follow the same contract:

1. Ensure parent directory exists
2. Write to ``<path>.tmp`` (same directory, same filesystem — guarantees
   ``os.replace`` is atomic at the POSIX / NTFS level)
3. ``fh.flush() + os.fsync(fh.fileno())`` — forces kernel pagecache to
   durable storage. This closes the "file exists but 0 bytes" crash
   window that NTFS / ext4-writeback leave open between ``write()``
   return and actual media flush.
4. ``os.replace(tmp, path)`` — atomic rename, old file (if any) dropped
5. On exception: cleanup ``<path>.tmp`` best-effort (``missing_ok=True``
   since the exception may be from write itself leaving nothing to clean)

Performance
-----------
``fsync`` costs one disk round-trip: typically <10ms on SSD, 30-50ms on
spinning disks / network FS. For **bulk writers** (many small files in
a loop), consider batching into a single tar/zip archive write rather
than calling this helper per-element. Per-element fsync in a loop
amplifies latency visibly.

Contract verification
---------------------
``smoke/p21_1_reliability_smoke.py`` asserts (via ``inspect.getsource``)
that all 3 functions contain ``fh.flush()`` + ``os.fsync(`` before the
``os.replace`` call. Any future refactor that drops fsync fails the
smoke, not production.

See also
--------
* ``P24_BLUEPRINT §4.1.2`` — migration matrix, 6-site sweep
* ``~/.cursor/skills/single-writer-choke-point/SKILL.md`` — general pattern
* ``.cursor/rules/atomic-io-only.mdc`` — pre-commit lint
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any

__all__ = [
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_gzip_json",
]


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Crash-safe ``bytes`` writer: tmp + fsync + os.replace.

    Use for binary payloads (tarballs, images, pickled blobs). Never
    use for log files (fsync per-line would kill log throughput).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        # Cleanup best-effort; the exception propagates either way
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    """Crash-safe JSON writer: tmp + fsync + os.replace.

    Uses ``ensure_ascii=False`` so CJK / emoji round-trip readably,
    and ``indent=2`` for human-friendly disk inspection (session
    archives, memory files, etc. are sometimes manually edited for
    debugging per §3A — human readable matters).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_gzip_json(path: Path, data: Any) -> None:
    """Crash-safe gzip+JSON writer: tmp + fsync + os.replace.

    Replaces the **non-atomic** raw ``gzip.open(path, "wb"); fh.write(...)``
    pattern in ``snapshot_store._spill_to_cold`` — the riskiest of the 6
    pre-P24 writers because it didn't even tmp+replace, let alone fsync.

    ``compresslevel=6`` matches the original snapshot_store choice
    (balance of compression ratio vs CPU, validated in P18).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with tmp.open("wb") as raw_fh:
            with gzip.GzipFile(fileobj=raw_fh, mode="wb", compresslevel=6) as gz:
                gz.write(payload)
            # gzip closed here; underlying file still open for fsync
            raw_fh.flush()
            os.fsync(raw_fh.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
