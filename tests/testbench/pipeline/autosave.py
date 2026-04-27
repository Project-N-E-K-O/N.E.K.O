"""Debounced autosave + boot-time recovery for the active session.

PLAN §14 约定: 每次 session 状态变更触发 debounced autosave (默认 5s 防抖,
60s 强制); 启动时扫 ``_autosave/`` 最近 24h 条目给 UI 恢复. 本模块是**纯
工具层** — 不持有 FastAPI router, 不直接被前端调; router 层
(``session_router``) 负责调度, UI 层 (``session_restore_modal.js``) 负责展示.

设计三件套
==========

1. :class:`AutosaveConfig` — 进程级单例配置 (enabled / debounce_seconds /
   force_seconds / rolling_count / keep_window_hours), 默认值来自
   ``config.AUTOSAVE_*``, runtime 可通过
   ``POST /api/session/autosave/config`` 改.

2. :class:`AutosaveScheduler` — **每个 session 一个实例**, 挂在
   ``Session.autosave_scheduler``.  跑一个后台 ``asyncio.Task`` 做 debounce
   循环: 收到 :meth:`notify` 后, 等 ``debounce_seconds`` 没有新 notify 或
   自首次 notify 起已过 ``force_seconds`` 就 flush. flush 失败不抛异常,
   只记 ``_last_error`` + diagnostics, 下一轮 dirty 再重试.

3. 模块级 I/O 辅助函数 — :func:`write_autosave_slot` (rolling 3 份) /
   :func:`list_autosaves` (供前端列表 + boot banner) /
   :func:`read_autosave_archive_and_tarball` (单条加载用于 restore) /
   :func:`delete_autosave_entry` (删单条) / :func:`cleanup_old_autosaves`
   (启动清理 > 24h).

磁盘布局
========

``DATA_DIR/saved_sessions/_autosave/`` 下:

.. code-block:: text

    <session_id>.autosave.json              (slot 0, 最新)
    <session_id>.autosave.memory.tar.gz
    <session_id>.autosave.prev.json         (slot 1)
    <session_id>.autosave.prev.memory.tar.gz
    <session_id>.autosave.prev2.json        (slot 2, 最旧)
    <session_id>.autosave.prev2.memory.tar.gz
    pre_load_<session_id>_<ts>.json         (P21 已实装, 无伴生 tar)

滚动写入 (每次 flush):

1. 删 slot 2 (tar 先, JSON 后; 符合 §3A F6).
2. rename slot 1 → slot 2.
3. rename slot 0 → slot 1.
4. 写新 slot 0 (tar 先, JSON 后; 都用 fsync 版的原子写).

slot 0 的 JSON 半写崩溃时, list 扫描标为 ``InvalidArchive``, 用户可从
slot 1 / slot 2 恢复; 这是为什么要 3 份而不是 1 份.

为什么 autosave 强制 api_key 脱敏
----------------------------------

和手动 Save 不同, autosave 是**自动**动作, 用户没有机会看"包含 API keys"
的警告勾选框. 留 plaintext 的 autosave 文件在磁盘上易被意外分享 (备份
到云 / 截图给别人看 / 压缩整个 ``testbench_data/`` 发邮件). PLAN §12
明确 "autosave 永远脱敏"; 本模块强制 ``redact_api_keys=True``.

锁策略 (与 ``session.lock`` 的交互)
------------------------------------

``session_store.session_operation`` 若发现 ``session.lock.locked()`` 就立
刻 409 (**不等**), 所以 scheduler 在 flush 时不能长时间占住 session.lock,
否则用户所有动作都会频繁 409.  我们把 flush 拆成两段:

* **快**段 (~几十 ms, 持 session.lock): ``serialize_session`` 做内存
  deepcopy, 捕获 session 的字典状态到 :class:`persistence.SessionArchive`.
* **慢**段 (百 ms 级, **不持** session.lock): ``pack_memory_tarball``
  遍历 sandbox 文件 + ``os.replace`` 落盘. 期间用户若修改 memory 文件,
  得到的 tar 可能"某些文件早, 某些文件晚"——这对 crash-recovery 类
  autosave 是可接受的折衷; 比用"用户点击 → 409"更友好.

单机内并发 flush (例如 flush_now 和 debounce 到期同时触发) 由模块内部
``_flush_lock`` 串行化, 避免两份 flush 互相踩到 rolling rename.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger
from tests.testbench.pipeline import persistence

if TYPE_CHECKING:  # pragma: no cover
    from tests.testbench.session_store import Session


# ── Magic markers ────────────────────────────────────────────────────

#: Archive kind written into autosave JSON files so the load path can
#: distinguish "named archive" (``testbench_session``) vs "autosave slot".
#: UI may show a different icon / disable "Save as" etc. for autosaves.
AUTOSAVE_ARCHIVE_KIND: str = "testbench_session_autosave"


# ── Slot naming ──────────────────────────────────────────────────────


#: Mapping ``slot index → filename suffix``. Slot 0 is the most recent
#: write, 1 is the prior one, 2 is the one before that. We don't go
#: beyond 3 — historical rollback past that is the snapshot timeline's
#: job (see P18), not autosave's.
_SLOT_SUFFIXES: tuple[str, ...] = ("", ".prev", ".prev2")

#: Session id validation for autosave entry IDs. Session IDs are
#: ``uuid4().hex[:12]`` → 12 lowercase hex chars. We allow a little
#: slack for future format changes (4..64 chars, hex + dash + dot +
#: underscore).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{4,64}$")


def _slot_json_path(session_id: str, slot: int) -> Path:
    """Absolute path to the JSON file for a given session + slot index."""
    suffix = _SLOT_SUFFIXES[slot]
    return tb_config.AUTOSAVE_DIR / f"{session_id}.autosave{suffix}.json"


def _slot_tar_path(session_id: str, slot: int) -> Path:
    """Absolute path to the tarball file for a given session + slot."""
    suffix = _SLOT_SUFFIXES[slot]
    return tb_config.AUTOSAVE_DIR / f"{session_id}.autosave{suffix}.memory.tar.gz"


def _parse_entry_id(entry_id: str) -> tuple[str, int]:
    """Parse ``<session_id>:<slot>`` into its two parts.

    Raises :class:`persistence.PersistenceError` with ``InvalidName``
    when the format is off; the router maps it to HTTP 400.
    """
    if not isinstance(entry_id, str) or ":" not in entry_id:
        raise persistence.PersistenceError(
            "InvalidName",
            f"autosave entry_id must be '<session_id>:<slot>', got {entry_id!r}",
        )
    sid, slot_raw = entry_id.rsplit(":", 1)
    if not _SESSION_ID_RE.match(sid):
        raise persistence.PersistenceError(
            "InvalidName",
            f"autosave entry_id session_id part invalid: {sid!r}",
        )
    try:
        slot = int(slot_raw)
    except ValueError as exc:
        raise persistence.PersistenceError(
            "InvalidName",
            f"autosave entry_id slot part must be an int, got {slot_raw!r}",
        ) from exc
    if slot < 0 or slot >= len(_SLOT_SUFFIXES):
        raise persistence.PersistenceError(
            "InvalidName",
            f"autosave slot out of range; must be 0..{len(_SLOT_SUFFIXES)-1}, got {slot}",
        )
    return sid, slot


def _compose_entry_id(session_id: str, slot: int) -> str:
    return f"{session_id}:{slot}"


def _session_id_from_json_name(filename: str) -> tuple[str, int] | None:
    """Given a raw filename like ``abc123.autosave.prev.json`` return
    ``(session_id, slot)`` or ``None`` if the name doesn't match any
    known autosave slot pattern.
    """
    if filename.endswith(".autosave.prev2.json"):
        slot = 2
        core_end = len(filename) - len(".autosave.prev2.json")
    elif filename.endswith(".autosave.prev.json"):
        slot = 1
        core_end = len(filename) - len(".autosave.prev.json")
    elif filename.endswith(".autosave.json"):
        slot = 0
        core_end = len(filename) - len(".autosave.json")
    else:
        return None
    sid = filename[:core_end]
    if not _SESSION_ID_RE.match(sid):
        return None
    return sid, slot


# ── Config singleton ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AutosaveConfig:
    """Runtime-adjustable autosave settings (process-wide).

    Immutable so swapping instances is atomic — callers that want to
    update config build a replacement via :func:`dataclasses.replace`
    (or just instantiate a fresh one) and hand it to
    :func:`set_autosave_config`. Existing scheduler instances read
    :func:`get_autosave_config` on every loop iteration so config
    changes take effect within one debounce cycle without needing to
    restart background tasks.
    """
    enabled: bool = True
    debounce_seconds: float = 5.0
    force_seconds: float = 60.0
    rolling_count: int = 3
    keep_window_hours: float = 24.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "debounce_seconds": self.debounce_seconds,
            "force_seconds": self.force_seconds,
            "rolling_count": self.rolling_count,
            "keep_window_hours": self.keep_window_hours,
        }

    @classmethod
    def defaults(cls) -> "AutosaveConfig":
        return cls(
            enabled=True,
            debounce_seconds=float(tb_config.AUTOSAVE_DEBOUNCE_SECONDS),
            force_seconds=float(tb_config.AUTOSAVE_FORCE_SECONDS),
            rolling_count=int(tb_config.AUTOSAVE_ROLLING_COUNT),
            keep_window_hours=float(tb_config.AUTOSAVE_KEEP_WINDOW_HOURS),
        )


# Module-level singleton. Read via :func:`get_autosave_config`; written
# (Python assignment is atomic for single references) via
# :func:`set_autosave_config`.
_config: AutosaveConfig = AutosaveConfig.defaults()


def get_autosave_config() -> AutosaveConfig:
    """Return the live autosave config (safe to call from any task)."""
    return _config


def validate_autosave_config(cfg: AutosaveConfig) -> None:
    """Bound-check an :class:`AutosaveConfig`. Raises :class:`ValueError`."""
    if cfg.debounce_seconds < 0.5 or cfg.debounce_seconds > 300:
        raise ValueError(
            f"debounce_seconds must be in [0.5, 300], got {cfg.debounce_seconds}",
        )
    if cfg.force_seconds < cfg.debounce_seconds or cfg.force_seconds > 3600:
        raise ValueError(
            f"force_seconds must be in [debounce_seconds, 3600], "
            f"got {cfg.force_seconds} (debounce={cfg.debounce_seconds})",
        )
    if cfg.rolling_count < 1 or cfg.rolling_count > len(_SLOT_SUFFIXES):
        raise ValueError(
            f"rolling_count must be in [1, {len(_SLOT_SUFFIXES)}], "
            f"got {cfg.rolling_count}",
        )
    if cfg.keep_window_hours < 1.0 or cfg.keep_window_hours > 720.0:
        raise ValueError(
            f"keep_window_hours must be in [1, 720] (30 days), "
            f"got {cfg.keep_window_hours}",
        )


def set_autosave_config(cfg: AutosaveConfig) -> AutosaveConfig:
    """Replace the autosave config; validates bounds first.

    Raises :class:`ValueError` on out-of-range values so the router can
    return HTTP 422. See :func:`validate_autosave_config` for the bounds.
    """
    validate_autosave_config(cfg)
    global _config
    _config = cfg
    python_logger().info(
        "autosave: config updated enabled=%s debounce=%.2fs force=%.2fs "
        "rolling=%d window=%.1fh",
        cfg.enabled, cfg.debounce_seconds, cfg.force_seconds,
        cfg.rolling_count, cfg.keep_window_hours,
    )
    return cfg


# ── Low-level I/O: write / list / read / delete ──────────────────────


def _finalise_slot_write(
    session_id: str,
    archive: persistence.SessionArchive,
    tar_bytes: bytes,
    *,
    rolling_count: int,
    autosave_at: str,
) -> dict[str, Any]:
    """Sync helper: do the rolling rename + atomic writes.

    Runs inside ``asyncio.to_thread`` so the event loop isn't blocked
    during ``os.fsync`` round-trips. Not held by any asyncio lock;
    :class:`AutosaveScheduler` uses its own ``_flush_lock`` to ensure
    only one flush per session is in the roll-rename dance at a time.
    """
    tb_config.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)

    # P21.1 G3/G10: pin hash of exactly-the-bytes-we-write so a future
    # restore can spot bit-rot / manual tar swap. Must happen BEFORE
    # ``archive.to_json_dict()`` so the serialized JSON carries the
    # hash. See :func:`persistence.verify_memory_hash` for the reader.
    archive.memory_sha256 = persistence.compute_memory_sha256(tar_bytes)

    on_disk = archive.to_json_dict()
    on_disk["archive_kind"] = AUTOSAVE_ARCHIVE_KIND
    on_disk["autosave_slot"] = 0
    on_disk["autosave_at"] = autosave_at

    # Roll slots.
    effective_count = max(1, min(rolling_count, len(_SLOT_SUFFIXES)))
    # From highest slot downward: delete target, rename src → target.
    for k in range(effective_count - 1, 0, -1):
        cur_json = _slot_json_path(session_id, k)
        cur_tar = _slot_tar_path(session_id, k)
        src_json = _slot_json_path(session_id, k - 1)
        src_tar = _slot_tar_path(session_id, k - 1)
        # Delete target slot (tar first per §3A F6).
        for doomed in (cur_tar, cur_json):
            try:
                if doomed.exists():
                    doomed.unlink()
            except OSError as exc:
                python_logger().warning(
                    "autosave: failed to delete %s during roll: %s", doomed, exc,
                )
        # Rename source → target; each os.replace is atomic.
        for src, dst in ((src_tar, cur_tar), (src_json, cur_json)):
            if src.exists():
                try:
                    os.replace(src, dst)
                except OSError as exc:
                    python_logger().warning(
                        "autosave: rename %s → %s failed: %s", src, dst, exc,
                    )

    slot0_tar = _slot_tar_path(session_id, 0)
    slot0_json = _slot_json_path(session_id, 0)
    # Tar first so a mid-write crash leaves "tar without JSON" (harmless
    # orphan visible to cleanup) rather than "JSON without tar" (broken
    # listing row). §3A F6.
    persistence._atomic_write_bytes(slot0_tar, tar_bytes)  # noqa: SLF001
    persistence._atomic_write_json(slot0_json, on_disk)  # noqa: SLF001

    json_bytes = slot0_json.stat().st_size
    tar_stat_size = slot0_tar.stat().st_size
    return {
        "slot": 0,
        "session_id": session_id,
        "autosave_at": autosave_at,
        "json_bytes": json_bytes,
        "tar_bytes": tar_stat_size,
    }


def write_autosave_slot(session: "Session") -> dict[str, Any]:
    """Synchronous one-shot autosave writer. Useful in unit tests.

    Unlike :meth:`AutosaveScheduler.flush_now` this does NOT take
    ``session.lock`` — the caller is responsible. Produces the same
    rolling slot result as the scheduler path.
    """
    cfg = get_autosave_config()
    archive = persistence.serialize_session(
        session,
        name=f"autosave_{session.id}",
        redact_api_keys=True,
    )
    sandbox_root = session.sandbox._app_docs  # noqa: SLF001
    tar_bytes = persistence.pack_memory_tarball(sandbox_root)
    autosave_at = datetime.now().isoformat(timespec="seconds")
    stats = _finalise_slot_write(
        session.id, archive, tar_bytes,
        rolling_count=cfg.rolling_count, autosave_at=autosave_at,
    )
    python_logger().info(
        "autosave: wrote slot 0 for session=%s (json=%d bytes, tar=%d bytes)",
        session.id, stats["json_bytes"], stats["tar_bytes"],
    )
    return stats


def _describe_slot_file(
    json_path: Path, tar_path: Path, slot: int,
) -> dict[str, Any] | None:
    """Cheap metadata read for one slot. Returns ``None`` if missing.

    Mirrors :func:`persistence.list_saved` style — corrupt files come
    back with an ``error`` string so the UI can show them greyed-out
    rather than disappearing (diagnosability > silent drop).
    """
    if not json_path.exists():
        return None
    parsed = _session_id_from_json_name(json_path.name)
    if parsed is None:
        return None
    session_id, _ = parsed
    tar_exists = tar_path.exists()
    json_bytes = json_path.stat().st_size
    tar_bytes = tar_path.stat().st_size if tar_exists else 0
    size_bytes = json_bytes + tar_bytes
    autosave_at = ""
    session_name = ""
    message_count = 0
    snapshot_count = 0
    eval_count = 0
    schema_version = 0
    error: str | None = None
    try:
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        kind = data.get("archive_kind")
        # Be lenient — we're the writers, so if kind is the "named archive"
        # magic, this was probably a rename screw-up; still show it but
        # mark the mismatch as an error.
        if kind not in (AUTOSAVE_ARCHIVE_KIND, persistence.ARCHIVE_KIND):
            raise ValueError(f"unknown archive_kind: {kind!r}")
        sess = data.get("session") or {}
        snaps = data.get("snapshots") or {}
        autosave_at = str(data.get("autosave_at") or data.get("saved_at") or "")
        session_name = str(sess.get("name") or "")
        message_count = len(sess.get("messages") or [])
        snapshot_count = (
            len(snaps.get("hot") or []) + len(snaps.get("cold_meta") or [])
        )
        eval_count = len(sess.get("eval_results") or [])
        schema_version = int(data.get("schema_version") or 0)
        if not tar_exists:
            error = f"TarballMissing: companion tarball {tar_path.name} missing"
    except Exception as exc:  # noqa: BLE001 — corrupt file → error column
        error = f"{type(exc).__name__}: {exc}"
        python_logger().warning(
            "autosave: list_autosaves failed to parse %s (%s)", json_path, exc,
        )
    return {
        "entry_id": _compose_entry_id(session_id, slot),
        "session_id": session_id,
        "slot": slot,
        "slot_label": _SLOT_SUFFIXES[slot] or "current",
        "autosave_at": autosave_at,
        "session_name": session_name,
        "message_count": message_count,
        "snapshot_count": snapshot_count,
        "eval_count": eval_count,
        "json_bytes": json_bytes,
        "tar_bytes": tar_bytes,
        "size_bytes": size_bytes,
        "schema_version": schema_version,
        "tar_missing": not tar_exists,
        "error": error,
    }


def list_autosaves(
    *, within_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Scan ``AUTOSAVE_DIR`` and return metadata for every slot.

    ``within_hours`` optionally filters out entries whose ``autosave_at``
    is older than ``now - within_hours``. A missing / unparseable
    timestamp is treated as "unknown" and **included** (so users can
    manually clean them up via the modal).

    Sort order: broken entries (with ``error`` set) sink to the end;
    the rest are newest first (by ``autosave_at`` descending).
    """
    out: list[dict[str, Any]] = []
    root = tb_config.AUTOSAVE_DIR
    if not root.exists():
        return out

    cutoff: datetime | None = None
    if within_hours is not None and within_hours > 0:
        cutoff = datetime.now() - timedelta(hours=within_hours)

    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        # Skip pre_load_*.json — those are P21's safety net, not
        # autosave slots. Surfaced separately if ever needed.
        if path.name.startswith("pre_load_"):
            continue
        parsed = _session_id_from_json_name(path.name)
        if parsed is None:
            # Unknown JSON — skip rather than error (user may have
            # dropped a random file into the dir).
            continue
        sid, slot = parsed
        tar_path = _slot_tar_path(sid, slot)
        meta = _describe_slot_file(path, tar_path, slot)
        if meta is None:
            continue
        if cutoff is not None and not meta.get("error"):
            ts = meta.get("autosave_at") or ""
            if ts:
                try:
                    parsed_ts = datetime.fromisoformat(ts)
                    if parsed_ts < cutoff:
                        continue
                except ValueError:
                    # Unparseable timestamp: include (user can delete).
                    pass
        out.append(meta)

    # Newest first by autosave_at; broken entries at the end.
    out.sort(key=lambda m: m.get("autosave_at") or "", reverse=True)
    out.sort(key=lambda m: m.get("error") is not None)
    return out


def list_autosaves_boot_orphans(
    *, active_session_id: str | None, within_hours: float,
) -> list[dict[str, Any]]:
    """Return autosaves that look like "unfinished work from a previous
    run of testbench" — slot 0 entries for sessions other than the
    currently active one, within the recovery window, and parse-error free.

    This is what the topbar banner consumes on mount; a non-empty list
    means we should invite the user to review/restore via the modal.
    """
    items = list_autosaves(within_hours=within_hours)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in items:
        if m.get("error"):
            continue
        if m.get("slot") != 0:
            # Only surface the freshest slot per session — older slots
            # aren't "another session" for banner purposes; user reaches
            # them through the restore modal anyway.
            continue
        sid = m.get("session_id")
        if not sid or sid == active_session_id or sid in seen:
            continue
        seen.add(sid)
        out.append(m)
    return out


def read_autosave_archive_and_tarball(
    entry_id: str,
) -> tuple[persistence.SessionArchive, bytes]:
    """Load one autosave slot into in-memory archive + raw tarball bytes.

    Raises :class:`persistence.PersistenceError` with an appropriate
    code the router maps to an HTTP status:

    * ``InvalidName``       — entry_id malformed.
    * ``ArchiveNotFound``   — JSON file missing for that slot.
    * ``InvalidArchive``    — JSON corrupt / archive_kind mismatch.
    * ``TarballMissing``    — companion tar.gz missing.
    """
    sid, slot = _parse_entry_id(entry_id)
    json_path = _slot_json_path(sid, slot)
    tar_path = _slot_tar_path(sid, slot)
    if not json_path.exists():
        raise persistence.PersistenceError(
            "ArchiveNotFound",
            f"autosave {entry_id!r} has no JSON at {json_path}",
            detail={"entry_id": entry_id, "path": str(json_path)},
        )
    try:
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise persistence.PersistenceError(
            "InvalidArchive",
            f"autosave {entry_id!r} JSON could not be parsed: {exc}",
            detail={"entry_id": entry_id},
        ) from exc

    # Normalise ``archive_kind`` so :meth:`SessionArchive.from_json_dict`
    # accepts it. Swap our autosave-kind magic for the named-archive kind
    # before handing to the persistence layer — structure is otherwise
    # identical (the autosave kind is just a discriminator for `list`).
    data_for_parse = dict(data)
    if data_for_parse.get("archive_kind") == AUTOSAVE_ARCHIVE_KIND:
        data_for_parse["archive_kind"] = persistence.ARCHIVE_KIND
    archive = persistence.SessionArchive.from_json_dict(data_for_parse)

    if not tar_path.exists():
        raise persistence.PersistenceError(
            "TarballMissing",
            f"autosave {entry_id!r} companion tarball missing at {tar_path}",
            detail={"entry_id": entry_id, "path": str(tar_path)},
        )
    try:
        tar_bytes = tar_path.read_bytes()
    except OSError as exc:
        raise persistence.PersistenceError(
            "WriteFailed",
            f"failed to read autosave tarball {tar_path}: {exc}",
            detail={"entry_id": entry_id},
        ) from exc
    return archive, tar_bytes


def delete_autosave_entry(entry_id: str) -> dict[str, Any]:
    """Delete one slot's JSON + tarball pair. Idempotent on half-deleted.

    Order: tar first, JSON second (§3A F6 — leave a "broken but
    discoverable" row, not a silent orphan).
    """
    sid, slot = _parse_entry_id(entry_id)
    json_path = _slot_json_path(sid, slot)
    tar_path = _slot_tar_path(sid, slot)

    tar_removed = False
    json_removed = False
    try:
        if tar_path.exists():
            tar_path.unlink()
            tar_removed = True
    except OSError as exc:
        raise persistence.PersistenceError(
            "WriteFailed",
            f"failed to delete {tar_path}: {exc}",
            detail={"entry_id": entry_id},
        ) from exc
    try:
        if json_path.exists():
            json_path.unlink()
            json_removed = True
    except OSError as exc:
        raise persistence.PersistenceError(
            "WriteFailed",
            f"failed to delete {json_path}: {exc}",
            detail={"entry_id": entry_id},
        ) from exc
    if not json_removed and not tar_removed:
        raise persistence.PersistenceError(
            "ArchiveNotFound",
            f"no autosave files for {entry_id!r}",
            detail={"entry_id": entry_id},
        )
    return {
        "entry_id": entry_id,
        "session_id": sid,
        "slot": slot,
        "json_removed": json_removed,
        "tar_removed": tar_removed,
    }


def delete_all_autosaves() -> dict[str, Any]:
    """Nuke every autosave slot (used by "Clear autosaves" button).

    Returns stats for the toast. Does NOT remove ``pre_load_*.json``
    safety backups from P21.
    """
    root = tb_config.AUTOSAVE_DIR
    if not root.exists():
        return {"deleted_entries": 0, "json_removed": 0, "tar_removed": 0}
    deleted_entries = 0
    json_removed = 0
    tar_removed = 0
    for path in list(root.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("pre_load_"):
            continue
        # Any .autosave*.json or .autosave*.memory.tar.gz counts.
        name = path.name
        if ".autosave" not in name:
            continue
        try:
            path.unlink()
            if name.endswith(".json"):
                json_removed += 1
                deleted_entries += 1
            elif name.endswith(".memory.tar.gz"):
                tar_removed += 1
        except OSError as exc:
            python_logger().warning(
                "autosave: delete_all failed on %s: %s", path, exc,
            )
    python_logger().info(
        "autosave: delete_all removed %d entries (json=%d, tar=%d)",
        deleted_entries, json_removed, tar_removed,
    )
    return {
        "deleted_entries": deleted_entries,
        "json_removed": json_removed,
        "tar_removed": tar_removed,
    }


def cleanup_old_autosaves(
    *, keep_window_hours: float | None = None,
) -> dict[str, Any]:
    """Remove autosave files older than ``keep_window_hours``.

    Intended to be called once on server startup (``server.py`` hooks
    this from ``@app.on_event("startup")``). Also reachable manually via
    a future Diagnostics "clean stale autosaves" button, but P22 MVP
    only wires the boot call.

    Returns stats for logging: ``{deleted_entries, json_removed,
    tar_removed, retention_hours}``.
    """
    cfg = get_autosave_config()
    window = (
        float(keep_window_hours)
        if keep_window_hours is not None
        else cfg.keep_window_hours
    )
    cutoff = datetime.now() - timedelta(hours=window)
    root = tb_config.AUTOSAVE_DIR
    if not root.exists():
        return {
            "deleted_entries": 0, "json_removed": 0, "tar_removed": 0,
            "retention_hours": window,
        }

    deleted_entries = 0
    json_removed = 0
    tar_removed = 0

    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        # Don't ever delete pre_load_*.json here; it's a manual safety
        # net from P21 with different semantics.
        if path.name.startswith("pre_load_"):
            continue
        if path.suffix != ".json":
            continue
        parsed = _session_id_from_json_name(path.name)
        if parsed is None:
            continue
        sid, slot = parsed
        # Determine age: prefer ``autosave_at`` inside JSON, fall back to
        # file mtime if parse fails.
        ts_str = ""
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            ts_str = str(data.get("autosave_at") or data.get("saved_at") or "")
        except Exception:  # noqa: BLE001 — unreadable → fall back to mtime
            ts_str = ""
        parsed_ts: datetime | None = None
        if ts_str:
            try:
                parsed_ts = datetime.fromisoformat(ts_str)
            except ValueError:
                parsed_ts = None
        if parsed_ts is None:
            try:
                parsed_ts = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
        if parsed_ts >= cutoff:
            continue
        # Delete tar first (F6), then JSON.
        tar_path = _slot_tar_path(sid, slot)
        try:
            if tar_path.exists():
                tar_path.unlink()
                tar_removed += 1
        except OSError as exc:
            python_logger().warning(
                "autosave: cleanup failed to delete %s: %s", tar_path, exc,
            )
        try:
            path.unlink()
            json_removed += 1
        except OSError as exc:
            python_logger().warning(
                "autosave: cleanup failed to delete %s: %s", path, exc,
            )
            continue
        deleted_entries += 1

    if deleted_entries:
        python_logger().info(
            "autosave: cleanup removed %d entries "
            "(json=%d, tar=%d, retention=%.1fh)",
            deleted_entries, json_removed, tar_removed, window,
        )
    return {
        "deleted_entries": deleted_entries,
        "json_removed": json_removed,
        "tar_removed": tar_removed,
        "retention_hours": window,
    }


# ── Scheduler ────────────────────────────────────────────────────────


#: On lock-contention inside the scheduler we retry after this many
#: seconds — too short and we hammer a busy session, too long and the
#: autosave lags visibly. Half the default debounce is a reasonable pick.
_LOCK_CONTENTION_RETRY_SECONDS: float = 2.5

#: After a failed flush (disk error / unexpected exception), back off
#: this long before the next retry. Keeps us from spinning on a full
#: disk while still recovering within a minute when space frees up.
_FLUSH_ERROR_BACKOFF_SECONDS: float = 10.0

#: Max time we'll wait trying to acquire the session lock during a
#: single flush attempt's fast stage. Scheduler yields to live ops.
_FAST_STAGE_ACQUIRE_TIMEOUT_SECONDS: float = 0.5


class AutosaveScheduler:
    """Per-session debounced autosave loop.

    Lifecycle: :meth:`start` on session create (``SessionStore.create``)
    → indefinite background task consuming :meth:`notify` events →
    :meth:`close` on session destroy (``SessionStore._destroy_locked``).

    Thread-safety: all public methods assume the asyncio event loop.
    Don't call from threads; use :func:`asyncio.run_coroutine_threadsafe`
    if you must.
    """

    def __init__(self, session: "Session") -> None:
        self._session = session
        self._dirty: bool = False
        self._first_dirty_at: float | None = None  # monotonic seconds
        self._last_notify_at: float | None = None  # monotonic seconds
        self._last_flush_at: float | None = None   # monotonic seconds
        self._last_flush_wall: str | None = None   # ISO timestamp for UI
        self._last_error: str | None = None
        self._last_source: str | None = None
        self._task: asyncio.Task[None] | None = None
        # ``_stopping`` blocks new ``notify()`` and tells the background
        # loop to bail; ``_closed`` is the *terminal* state set only
        # **after** the last-gasp flush (see :meth:`close`). Splitting
        # them is what lets ``_do_flush`` distinguish "scheduler is
        # winding down — final flush is allowed" from "scheduler is
        # gone — drop the flush". Conflating them caused the GH AI-
        # review issue #1: ``close()`` flipped a single ``_closed`` flag
        # before awaiting the final flush, and ``_do_flush`` then short-
        # circuited the very flush ``close()`` was trying to perform.
        self._stopping: bool = False
        self._closed: bool = False
        self._wakeup: asyncio.Event = asyncio.Event()
        # Serialises concurrent flushes (e.g. ``flush_now`` racing with
        # a debounce-timer-driven flush).
        self._flush_lock: asyncio.Lock = asyncio.Lock()
        self._stats: dict[str, int] = {
            "notifies": 0,
            "flushes": 0,
            "errors": 0,
            "skipped_disabled": 0,
            "skipped_lock_busy": 0,
        }

    # ── public API ──────────────────────────────────────────────────

    def start(self) -> None:
        """Kick off the background loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name=f"autosave-{self._session.id}",
        )

    def notify(self, source: str = "unknown") -> None:
        """Mark session dirty + wake the loop.

        Safe to call from any coroutine (incl. inside ``session.lock``'s
        async-with block — we don't block on the lock here). Never raises;
        the caller (``session_operation.__aexit__``) must not be broken
        by a stray exception in bookkeeping.
        """
        # Reject as soon as ``close()`` started winding the scheduler
        # down, even before the final flush completes — otherwise late
        # notifies could push ``_dirty=True`` *after* the last-gasp
        # flush cleared it, leaking that change forever.
        if self._stopping or self._closed:
            return
        try:
            now = time.monotonic()
            self._stats["notifies"] += 1
            self._dirty = True
            self._last_source = source
            self._last_notify_at = now
            if self._first_dirty_at is None:
                self._first_dirty_at = now
            self._wakeup.set()
        except Exception:  # noqa: BLE001 — must not break caller op
            python_logger().exception("autosave: notify() failed")

    async def flush_now(self) -> dict[str, Any]:
        """Force an immediate flush (bypasses debounce).

        Returns ``{wrote: bool, stats?: dict, reason?: str}``. Waits for
        any in-flight flush to finish before starting a new one.
        """
        if self._closed:
            return {"wrote": False, "reason": "SchedulerClosed"}
        cfg = get_autosave_config()
        if not cfg.enabled:
            return {"wrote": False, "reason": "AutosaveDisabled"}
        # ``flush_now`` does NOT check ``_dirty`` — user explicitly
        # asked, so we write even if nothing changed since last flush
        # ("snapshot before I tweak things" mentality).
        try:
            stats = await self._do_flush(wait_for_lock=True)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._stats["errors"] += 1
            python_logger().warning("autosave: flush_now failed (%s)", exc)
            return {"wrote": False, "reason": f"FlushFailed: {exc}"}
        return {"wrote": True, "stats": stats}

    async def close(self) -> None:
        """Cancel the background task; last-gasp flush if still dirty.

        Called from ``SessionStore._destroy_locked`` **before** the
        session's per-op lock is taken for sandbox teardown, so we won't
        deadlock if our last flush blocks on the same lock.

        Two-phase shutdown so the last-gasp flush actually runs:

        1. ``_stopping = True`` — :meth:`notify` rejects new dirty
           signals and the background loop will return on its next
           iteration. This prevents the loop from racing the final
           flush we're about to do here.
        2. ``await _do_flush(wait_for_lock=True)`` — at this point
           ``_closed`` is still ``False``, so :meth:`_do_flush`'s
           internal "scheduler closed during lock wait" guard does NOT
           trip, and the final flush completes.
        3. ``_closed = True`` + cancel loop task — terminal state.

        Conflating ``_stopping`` and ``_closed`` previously meant the
        guard at line ~1115 of :meth:`_do_flush` raised
        :class:`_LockContention` as soon as we acquired ``session.lock``,
        and the trailing dirty state was silently dropped (GH AI-review
        issue #1).
        """
        if self._closed:
            return
        self._stopping = True
        # Best-effort final flush so graceful shutdown captures trailing
        # dirty state. We try with a bounded timeout — if the session is
        # genuinely busy, skip (user just asked to destroy, so losing
        # a few seconds' worth of changes is acceptable).
        cfg = get_autosave_config()
        if self._dirty and cfg.enabled:
            try:
                await asyncio.wait_for(
                    self._do_flush(wait_for_lock=True),
                    timeout=max(cfg.force_seconds, 5.0),
                )
            except (asyncio.TimeoutError, TimeoutError):
                python_logger().warning(
                    "autosave: close() final flush timed out for session=%s",
                    self._session.id,
                )
            except Exception as exc:  # noqa: BLE001
                python_logger().warning(
                    "autosave: close() final flush failed: %s", exc,
                )
        self._closed = True
        # Wake the loop so the ``while not self._closed`` check exits
        # immediately (otherwise it stays parked on ``_wakeup.wait()``
        # until ``_task.cancel()`` raises ``CancelledError`` inside
        # ``wait()`` — works, but generates a noisy CancelledError
        # exception per close).
        self._wakeup.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    def get_status(self) -> dict[str, Any]:
        """Snapshot for ``GET /api/session/autosave/status``."""
        now = time.monotonic()
        return {
            "session_id": self._session.id,
            "dirty": self._dirty,
            "closed": self._closed,
            "first_dirty_elapsed_seconds": (
                (now - self._first_dirty_at) if self._first_dirty_at else None
            ),
            "last_flush_elapsed_seconds": (
                (now - self._last_flush_at) if self._last_flush_at else None
            ),
            "last_flush_at": self._last_flush_wall,
            "last_error": self._last_error,
            "last_source": self._last_source,
            "stats": dict(self._stats),
            "config": get_autosave_config().to_dict(),
        }

    # ── internal loop ──────────────────────────────────────────────

    async def _run(self) -> None:
        """Background loop: wait → debounce → flush → back-off on error.

        Happy path is intentionally shallow — the deeply-nested async
        flow this entails is the 'reactive' pattern we want *here*
        (event-driven, bursty), not a polling ``while True: sleep``.
        """
        try:
            while not (self._stopping or self._closed):
                # Wait for first dirty signal. No CPU when idle.
                await self._wakeup.wait()
                self._wakeup.clear()
                if self._stopping or self._closed:
                    return
                cfg = get_autosave_config()
                if not cfg.enabled:
                    # Config disabled between notifies; reset state and
                    # go back to waiting. Count notifies that came in
                    # during disabled as "skipped".
                    if self._dirty:
                        self._stats["skipped_disabled"] += 1
                    self._dirty = False
                    self._first_dirty_at = None
                    continue
                if not self._dirty:
                    continue

                # Debounce: sleep in short chunks so new notifies can
                # extend the window, but cap total wait at force_seconds
                # from the first notify.
                await self._debounce_wait(cfg)
                if self._stopping or self._closed:
                    return
                # Re-check enabled in case user toggled mid-debounce.
                cfg = get_autosave_config()
                if not cfg.enabled:
                    self._dirty = False
                    self._first_dirty_at = None
                    self._stats["skipped_disabled"] += 1
                    continue

                try:
                    await self._do_flush(wait_for_lock=False)
                except _LockContention:
                    self._stats["skipped_lock_busy"] += 1
                    # Don't clear _dirty; retry after a short sleep.
                    try:
                        await asyncio.sleep(_LOCK_CONTENTION_RETRY_SECONDS)
                    except asyncio.CancelledError:
                        raise
                    self._wakeup.set()
                    continue
                except Exception as exc:  # noqa: BLE001 — never die
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._stats["errors"] += 1
                    python_logger().warning(
                        "autosave: flush failed for session=%s (%s)",
                        self._session.id, exc,
                    )
                    try:
                        await asyncio.sleep(_FLUSH_ERROR_BACKOFF_SECONDS)
                    except asyncio.CancelledError:
                        raise
                    # Re-arm wakeup so the loop retries the still-dirty
                    # state immediately after backoff. Without this the
                    # next iteration would park on ``_wakeup.wait()`` and
                    # only resume on the next user notify, leaving
                    # ``_dirty=True`` stuck on disk-full / permission-
                    # denied / serialization-error scenarios where no
                    # further user activity is expected (GH AI-review
                    # issue #2).
                    self._wakeup.set()
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — last-ditch; log + return
            python_logger().exception(
                "autosave: loop for session=%s crashed; background task exits",
                self._session.id,
            )

    async def _debounce_wait(self, cfg: AutosaveConfig) -> None:
        """Sleep until either debounce expired with no new notifies
        OR force_seconds elapsed since first_dirty_at, whichever first.
        """
        while not (self._stopping or self._closed):
            now = time.monotonic()
            first = self._first_dirty_at or now
            last_notify = self._last_notify_at or first
            elapsed_total = now - first
            if elapsed_total >= cfg.force_seconds:
                return
            elapsed_since_notify = now - last_notify
            remaining_debounce = cfg.debounce_seconds - elapsed_since_notify
            remaining_force = cfg.force_seconds - elapsed_total
            # Wake on the earlier of "debounce expires" or "force expires"
            # or a new notify arriving.
            sleep_for = max(0.05, min(remaining_debounce, remaining_force))
            self._wakeup.clear()
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                # Debounce window has closed with no new notifies.
                return
            if self._stopping or self._closed:
                return
            # New notify came in; loop extends the window (self._last_notify_at
            # was updated inside notify()).

    async def _do_flush(self, *, wait_for_lock: bool) -> dict[str, Any]:
        """Two-stage flush keeping session.lock held only briefly.

        Stage 1 (fast, under ``session.lock``): :func:`serialize_session`
        — in-memory dict deep-copies, <50 ms even for large sessions.
        Stage 2 (slow, outside lock): :func:`pack_memory_tarball` +
        atomic disk writes — hundreds of ms for large sandboxes but
        does not block concurrent user operations.

        ``wait_for_lock=False`` raises :class:`_LockContention` if the
        fast stage can't grab the lock within
        ``_FAST_STAGE_ACQUIRE_TIMEOUT_SECONDS`` — the caller (loop)
        retries after a short sleep. ``True`` blocks indefinitely; used
        by :meth:`flush_now` and the shutdown last-gasp path.

        On success clears ``_dirty`` + updates ``_last_flush_*``.
        """
        # Serialise with other flushes on this scheduler (flush_now ∧
        # debounce-loop race).
        async with self._flush_lock:
            session = self._session
            cfg = get_autosave_config()

            # ── Stage 1: fast snapshot under session.lock. ─────────
            if wait_for_lock:
                await session.lock.acquire()
            else:
                try:
                    await asyncio.wait_for(
                        session.lock.acquire(),
                        timeout=_FAST_STAGE_ACQUIRE_TIMEOUT_SECONDS,
                    )
                except (asyncio.TimeoutError, TimeoutError) as exc:
                    raise _LockContention(
                        "session lock busy; deferring autosave flush",
                    ) from exc
            try:
                # Check session identity: between notify() and now, a
                # Load/Reset op could have destroyed this session and
                # spun up a new one. The scheduler should only autosave
                # the session it was constructed for.
                if self._closed:
                    raise _LockContention("scheduler closed during lock wait")
                archive = persistence.serialize_session(
                    session,
                    name=f"autosave_{session.id}",
                    redact_api_keys=True,
                )
                sandbox_root: Path = session.sandbox._app_docs  # noqa: SLF001
            finally:
                session.lock.release()

            # ── Race-snapshot for Stage 2. ─────────────────────────
            # Stage 2's two ``await asyncio.to_thread(...)`` calls are
            # real suspension points: while we are packing the tarball
            # (potentially hundreds of ms for a large sandbox), other
            # coroutines on the loop can run user ops + call
            # ``notify()``, which atomically bumps ``_dirty=True`` and
            # ``_last_notify_at=now`` (and ``_wakeup.set()``). If we
            # blindly clear ``_dirty=False`` after to_thread returns,
            # **that mid-flight notify is silently dropped** and the
            # loop won't schedule another flush — the tester's most
            # recent change leaks until the next user op happens to
            # touch ``notify()`` again. (GH AI-review issue, 2nd batch
            # #1.) Snapshot the notify timestamp now (after release of
            # session.lock — Stage 1 ran with lock held so notifies
            # couldn't sneak in there) and compare in the bookkeeping
            # block to decide whether the clear is safe.
            notify_at_start = self._last_notify_at

            # ── Stage 2: slow I/O outside lock. ────────────────────
            # Tarball packing reads sandbox files without mutating them;
            # concurrent user writes can produce a slightly-inconsistent
            # tar (some files from before the write, some after). That's
            # acceptable for crash-recovery autosaves — see module docstring.
            autosave_at = datetime.now().isoformat(timespec="seconds")
            tar_bytes = await asyncio.to_thread(
                persistence.pack_memory_tarball, sandbox_root,
            )
            stats = await asyncio.to_thread(
                _finalise_slot_write,
                session.id, archive, tar_bytes,
                rolling_count=cfg.rolling_count,
                autosave_at=autosave_at,
            )

            # ── Bookkeeping. ───────────────────────────────────────
            # The flush itself succeeded regardless — record the wall
            # clock unconditionally so ``get_status`` reflects "we did
            # write a slot just now" even when we're about to leave
            # ``_dirty=True`` for a follow-up flush.
            self._last_flush_at = time.monotonic()
            self._last_flush_wall = stats.get("autosave_at") or autosave_at
            self._last_error = None
            self._stats["flushes"] += 1
            if self._last_notify_at == notify_at_start:
                # No mid-flight notify — safe to clear the dirty latch.
                self._dirty = False
                self._first_dirty_at = None
            else:
                # A notify arrived during Stage 2's to_thread window.
                # Keep ``_dirty`` True and slide ``_first_dirty_at``
                # forward to the newer notify so debounce / force
                # accounting in the loop measures *that* notify's age
                # rather than the original (already-flushed) one.
                # ``notify()`` already called ``self._wakeup.set()`` so
                # the loop will pick this up on its next iteration; we
                # do **not** ``set()`` here to avoid spurious wakeups
                # on the borderline "notify happened to land at the
                # exact same monotonic tick" path (caught by the
                # equality test above).
                self._first_dirty_at = self._last_notify_at
            python_logger().info(
                "autosave: wrote slot 0 for session=%s (json=%d bytes, "
                "tar=%d bytes, source=%s)",
                session.id, stats["json_bytes"], stats["tar_bytes"],
                self._last_source or "-",
            )
            return stats


class _LockContention(RuntimeError):
    """Internal sentinel: scheduler couldn't grab session lock in time."""


__all__ = [
    "AUTOSAVE_ARCHIVE_KIND",
    "AutosaveConfig",
    "AutosaveScheduler",
    "cleanup_old_autosaves",
    "delete_all_autosaves",
    "delete_autosave_entry",
    "get_autosave_config",
    "list_autosaves",
    "list_autosaves_boot_orphans",
    "read_autosave_archive_and_tarball",
    "set_autosave_config",
    "validate_autosave_config",
    "write_autosave_slot",
]
