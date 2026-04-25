"""Session save / load / import / export (P21).

PLAN §P21 约定: 人工命名的 session 存档, 让测试床从 "临时沙盒" 变成
"可交付的持久资产". 本模块只负责纯序列化 (Session ↔ SessionArchive ↔
disk files), **不持有 asyncio.Lock / FastAPI / session_store 引用** —
router 层 (``session_router``) 负责加锁 + 调度 + HTTP 映射; 本模块做的事
逐项都是同步 I/O, 可以在任何上下文里复用 (包括 P22 autosave 和 P23
export).

存档布局
--------

一份 "存档" = **两个伴生文件** 放在 ``DATA_DIR/saved_sessions/``:

    <name>.json            (UTF-8 JSON; 见下方 schema)
    <name>.memory.tar.gz   (gzip tar; sandbox ``_app_docs/`` 下所有文件)

``<name>.json`` 的顶层 schema (``schema_version=1``):

.. code-block:: json

    {
        "schema_version": 1,
        "archive_kind": "testbench_session",
        "name": "...",
        "saved_at": "2026-04-21T10:00:00",
        "redact_api_keys": true,
        "session": {
            "id": "...",
            "name": "...",
            "created_at": "ISO",
            "messages": [...],
            "persona": {...},
            "model_config": {...},    # api_key 已脱敏
            "stage_state": {...},
            "eval_results": [...],
            "clock": {...}
        },
        "snapshots": {
            "hot": [ Snapshot.to_json_dict() ... ],
            "cold_meta": [ {id, label, trigger, ...} ... ]
            // cold 的 payload 本身在 tar.gz 内部的
            // ``.snapshots/<id>.json.gz`` 文件里
        }
    }

**为什么把 memory 放到单独的 tar.gz 而不是塞进 JSON**:

(a) Memory 可能含二进制 (SQLite ``time_indexed.db`` / FTS5 index / 图片
    附件等), base64 到 JSON 里把体积 ×1.33 且不可 stream.
(b) `tarfile + gzip` 是 stdlib, 不引入第三方依赖.
(c) P18 冷存快照 ``.snapshots/<id>.json.gz`` 已经是独立文件, 和 memory
    一起打包恰好是 "整个 sandbox app_docs 目录照搬".

**Load 的还原顺序** (见 :func:`apply_to_session`):

1. 调用方负责拿 ``session_operation(LOADING)`` 锁 + 对当前 session 建
   pre_load_backup JSON (落在 autosave dir).
2. 调用方负责 ``_dispose_all_sqlalchemy_caches()`` + ``gc.collect`` 释
   放老会话的 SQLite 句柄 (F4).
3. 调用方销毁旧 session (``store.destroy(purge_sandbox=True)``).
4. 调用方 ``store.create(name=archive.session.name)`` 开新会话, 走完
   ``sandbox.apply()`` + 初始 ``snapshot_store``.
5. **本模块** 的 :func:`restore_memory_tarball` 把 tar.gz 解到 sandbox
   ``_app_docs`` 下 (覆盖 create 创建的空骨架).
6. **本模块** 的 :func:`apply_to_session` 把 archive 内的 messages /
   persona / model_config / stage / clock / eval_results / snapshot
   timeline 灌回新 session.
7. 调用方 (UI) 收到新 session describe 后 ``window.location.reload()``
   让前端彻底重载 (§3A B13 "状态彻底替换类操作默认走 reload").

API key 脱敏
------------

默认 ``redact_api_keys=True``. 对 ``session.model_config`` 里每个 group
(chat/simuser/judge/memory), 把 ``api_key`` 替换成 sentinel 值
:data:`REDACTED_SECRET`. Load 回来时保持 sentinel (不会把 sentinel 当真 key
发请求), UI 会在 Settings → Models 提示用户重新填. 如果用户确定要明文存
(临时排障归档给自己看), 调用方传 ``redact_api_keys=False`` — 前端 Save
对话框会 **默认勾选 "脱敏"** + 额外二次确认才允许取消.
"""
from __future__ import annotations

import base64
import copy
import gc
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger
from tests.testbench.pipeline.snapshot_store import (
    Snapshot,
    SnapshotStore,
    _dispose_all_sqlalchemy_caches,
)
from tests.testbench.virtual_clock import VirtualClock

if TYPE_CHECKING:  # pragma: no cover
    from tests.testbench.session_store import Session


# ── constants ───────────────────────────────────────────────────────

#: Current archive schema version. Bump this whenever the on-disk
#: JSON shape changes in a breaking way; migration ladder lives in
#: :func:`_migrate_archive_dict`.
ARCHIVE_SCHEMA_VERSION: int = 1

#: Magic kind marker at the top of the JSON file. Guards against
#: the user accidentally loading an autosave / export / scoring schema
#: JSON as a session archive (all four live under ``testbench_data/``).
ARCHIVE_KIND: str = "testbench_session"

#: Sentinel used for redacted secrets. Chosen so that any downstream
#: code that forgets to re-prompt the user can't silently mistake it
#: for a real key — it has no resemblance to any provider's format.
REDACTED_SECRET: str = "<redacted>"

#: Save name validation: letters / digits / underscore / dash / dot,
#: first char non-symbol, 1..64 chars. Mirrors dialog_templates /
#: scoring_schemas naming for consistency.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{0,63}$")

#: Hard cap on a single memory file we'll pack into the tarball. Same
#: limit as :data:`snapshot_store._MAX_MEMORY_FILE_BYTES` so a rogue
#: huge file in the sandbox doesn't explode the archive size.
_MAX_FILE_BYTES: int = 10 * 1024 * 1024

#: Hard cap on the **decompressed** tarball size we'll accept on import.
#: 250 MiB is enough for any realistic sandbox (three-layer memory +
#: thirty-odd snapshots) while guarding against a malicious or broken
#: tar from the filesystem owner's perspective.
_MAX_TAR_UNCOMPRESSED_BYTES: int = 250 * 1024 * 1024

#: Tar member path prefix allow-list (relative to sandbox ``_app_docs``).
#: Extras like `_cache/` or absolute paths are rejected as tampered.
_ALLOWED_TAR_PREFIXES: tuple[str, ...] = (
    "memory/", "config/", "character_cards/", ".snapshots/", "plugins/",
    "live2d/", "vrm/", "mmd/", "workshop/",
)


# ── errors ──────────────────────────────────────────────────────────


class PersistenceError(RuntimeError):
    """Raised for any recoverable save/load/import failure.

    Carries a ``code`` string that the router maps to HTTP status:

      - ``InvalidName``          → 400
      - ``ArchiveNotFound``      → 404
      - ``ArchiveExists``        → 409 (save_as refusing to overwrite)
      - ``InvalidArchive``       → 400 (wrong magic / bad schema_version /
                                        corrupt JSON / tar escape)
      - ``SchemaVersionTooNew``  → 400 (future format)
      - ``NameTaken``            → 409 (alt wording of ArchiveExists)
      - ``WriteFailed``          → 500 (tmp + replace I/O error)
      - ``TarballMissing``       → 400 (json found but .memory.tar.gz gone)
    """

    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.detail = detail or {}
        super().__init__(f"[{code}] {message}")


# ── helpers ─────────────────────────────────────────────────────────


def validate_name(name: str) -> str:
    """Reject names that would escape the save dir or confuse the OS.

    Returns the trimmed name on success; raises :class:`PersistenceError`
    with ``code='InvalidName'`` otherwise.
    """
    if not isinstance(name, str):
        raise PersistenceError("InvalidName", "name must be a string")
    stripped = name.strip()
    if not stripped:
        raise PersistenceError("InvalidName", "name cannot be blank")
    if not _SAFE_NAME_RE.match(stripped):
        raise PersistenceError(
            "InvalidName",
            "name must be 1-64 chars of letters/digits/_/-/., "
            "starting with a letter or digit",
        )
    # Defense in depth: even if the regex is ever loosened, reject
    # obvious traversal attempts.
    if ".." in stripped or stripped.startswith((".", "-")):
        raise PersistenceError("InvalidName", "name cannot start with '.' or '-' or contain '..'")
    return stripped


# ── H2 archive schema lint (P24 §3.2) ────────────────────────────────


def lint_archive_json(name: str) -> dict[str, Any]:
    """Validate a saved-archive JSON against the current schema.

    Reads ``<SAVED_SESSIONS_DIR>/<name>.json`` (does **not** touch the
    tarball — that's out of scope, covered by ``verify_memory_hash``
    which is a separate load-time operation), runs a strict check, and
    returns a structured lint report:

    * ``errors``: list of ``{path, code, message}`` field-level issues
      that would break :meth:`SessionArchive.from_json_dict`.
    * ``warnings``: list of ``{path, code, message}`` advisory issues —
      unknown top-level fields (future-version forward compat), empty
      ``memory_sha256`` (legacy archive, integrity check will skip), etc.
    * ``schema_version``: the version found in the file (or ``None``).
    * ``schema_version_supported``: bool — ``False`` if the archive was
      written by a newer testbench version than this build understands.
    * ``name``: echo back.

    Does **not** raise on a malformed archive — returns a report with
    errors populated. Only :class:`PersistenceError` is raised when the
    file is missing / unreadable (callers map to 404).
    """
    path = tb_config.SAVED_SESSIONS_DIR / f"{name}.json"
    if not path.exists():
        raise PersistenceError("ArchiveMissing", f"no archive found named {name!r}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersistenceError(
            "ArchiveReadFailed",
            f"failed to read archive JSON: {exc}",
        ) from exc

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append({
            "path": "$",
            "code": "JSONDecodeError",
            "message": (
                f"存档 JSON 无法解析: {exc.msg} (第 {exc.lineno} 行第 "
                f"{exc.colno} 列). 文件可能被手动修改后写入了非法语法, "
                f"或者写盘过程被中断."
            ),
        })
        return {
            "name": name,
            "schema_version": None,
            "schema_version_supported": False,
            "errors": errors,
            "warnings": warnings,
        }

    if not isinstance(data, dict):
        errors.append({
            "path": "$",
            "code": "RootNotObject",
            "message": (
                f"存档根节点必须是 JSON 对象, 实际是 {type(data).__name__}. "
                f"文件格式从根本上就不对, 大概率不是 testbench 存档."
            ),
        })
        return {
            "name": name,
            "schema_version": None,
            "schema_version_supported": False,
            "errors": errors,
            "warnings": warnings,
        }

    # schema_version check
    version = data.get("schema_version")
    if version is None:
        errors.append({
            "path": "schema_version",
            "code": "Missing",
            "message": "缺少必填字段 schema_version — 无法判断存档版本兼容性.",
        })
    elif not isinstance(version, int):
        errors.append({
            "path": "schema_version",
            "code": "WrongType",
            "message": (
                f"schema_version 应为整数, 实际是 "
                f"{type(version).__name__} ({version!r})."
            ),
        })
    supported = isinstance(version, int) and version <= ARCHIVE_SCHEMA_VERSION

    # archive_kind sentinel
    kind = data.get("archive_kind")
    if kind != ARCHIVE_KIND:
        errors.append({
            "path": "archive_kind",
            "code": "Mismatch",
            "message": (
                f"archive_kind 应为 {ARCHIVE_KIND!r}, 实际是 {kind!r}. "
                f"这个文件可能根本不是 testbench 会话存档."
            ),
        })

    # Required top-level fields
    _type_names_cn = {str: "字符串", dict: "对象", list: "数组", int: "整数"}
    for required_path, required_type in [
        ("name", str),
        ("saved_at", str),
        ("session", dict),
        ("snapshots", dict),
    ]:
        val = data.get(required_path)
        if val is None:
            errors.append({
                "path": required_path,
                "code": "Missing",
                "message": "缺少必填字段, 存档无法载入.",
            })
        elif not isinstance(val, required_type):
            expected_cn = _type_names_cn.get(required_type, required_type.__name__)
            got_cn = _type_names_cn.get(type(val), type(val).__name__)
            errors.append({
                "path": required_path,
                "code": "WrongType",
                "message": f"类型错误: 期望{expected_cn}, 实际是{got_cn}.",
            })

    # session.* required subfields
    session_block = data.get("session") or {}
    if isinstance(session_block, dict):
        for sub in ("id", "name", "created_at"):
            if session_block.get(sub) is None:
                errors.append({
                    "path": f"session.{sub}",
                    "code": "Missing",
                    "message": f"session.{sub} 是必填字段.",
                })

    # memory_sha256 advisory
    if not data.get("memory_sha256"):
        warnings.append({
            "path": "memory_sha256",
            "code": "LegacyArchive",
            "message": (
                "memory_sha256 为空 — 这是 P22.1 (2026-04 前) 保存的旧存档. "
                "载入时会跳过 memory tarball 完整性校验 (向后兼容), "
                "但加载仍正常工作."
            ),
        })

    # Unknown top-level fields → warnings (forward-compat friendly)
    known_top = {"schema_version", "archive_kind", "name", "saved_at",
                 "redact_api_keys", "memory_sha256", "session", "snapshots"}
    for key in data.keys():
        if key not in known_top:
            warnings.append({
                "path": key,
                "code": "UnknownField",
                "message": (
                    f"未知字段 {key!r} — 可能来自更新版本的 testbench. "
                    f"载入时会被忽略, 不影响使用."
                ),
            })

    return {
        "name": name,
        "schema_version": version if isinstance(version, int) else None,
        "schema_version_supported": supported,
        "errors": errors,
        "warnings": warnings,
    }


def _json_path_for(name: str) -> Path:
    return tb_config.SAVED_SESSIONS_DIR / f"{name}.json"


def _tar_path_for(name: str) -> Path:
    return tb_config.SAVED_SESSIONS_DIR / f"{name}.memory.tar.gz"


# P24 §4.1.2 (2026-04-21): the fsync-correct implementations moved to
# ``pipeline/atomic_io.py`` so all 6 former write-copies (persistence /
# memory_router / memory_runner / script_runner / scoring_schema /
# snapshot_store) share one chokepoint. The original P21.1 G1 fsync
# guards are preserved in atomic_io; these module-level aliases stay
# for ``autosave.py`` and historical call sites ``save_archive_and_tarball``
# / ``import_from_payload`` which use them as "persistence-private helpers"
# via ``noqa: SLF001``. No behavior diff.
from tests.testbench.pipeline.atomic_io import (  # noqa: E402
    atomic_write_bytes as _atomic_write_bytes,
    atomic_write_json as _atomic_write_json,
)


def redact_model_config(
    model_config: dict[str, Any] | None, *, redacted: str = REDACTED_SECRET,
) -> dict[str, Any]:
    """Deep-copy ``model_config`` with every ``api_key`` field replaced.

    Accepts any shape (raw dict from ``session.model_config`` or a
    ``ModelConfigBundle.model_dump()``). Walks recursively so future
    nested bundles (``overrides`` / ``per_schema_overrides``) also get
    their keys masked without needing an update here.
    """
    if not isinstance(model_config, dict):
        return {}

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "api_key" and isinstance(v, str) and v:
                    out[k] = redacted
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(copy.deepcopy(model_config))


# ── archive dataclass ──────────────────────────────────────────────


@dataclass
class SessionArchiveMetadata:
    """Summary info for the list subpage + delete/load buttons."""

    name: str
    saved_at: str                       # ISO string
    session_name: str
    session_id: str
    message_count: int
    snapshot_count: int
    eval_count: int
    size_bytes: int                     # json + tarball combined
    schema_version: int
    redacted: bool
    # Non-empty iff the file could not be loaded as a valid archive.
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionArchive:
    """Python-side mirror of ``<name>.json``.

    Snapshots live in two tiers: ``snapshots_hot`` carries the full
    payload (messages/memory_files/etc.) and is ready to be re-inserted
    into :class:`SnapshotStore._hot`. ``snapshots_cold_meta`` holds only
    the metadata — the payload files are expected to be re-extracted
    from the tarball into ``<sandbox>/.snapshots/<id>.json.gz`` as part
    of :func:`restore_memory_tarball`.
    """

    schema_version: int
    archive_kind: str
    name: str
    saved_at: str
    redact_api_keys: bool

    # Session state (everything the UI / pipeline can read back).
    session_id: str
    session_name: str
    session_created_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    model_config: dict[str, Any] = field(default_factory=dict)
    stage_state: dict[str, Any] = field(default_factory=dict)
    eval_results: list[dict[str, Any]] = field(default_factory=list)
    clock: dict[str, Any] = field(default_factory=dict)

    # Snapshot timeline. ``snapshots_hot`` is ready-to-insert Snapshots,
    # ``snapshots_cold_meta`` are metadata dicts whose actual payload
    # sits in the tarball at ``.snapshots/<id>.json.gz``.
    snapshots_hot: list[Snapshot] = field(default_factory=list)
    snapshots_cold_meta: list[dict[str, Any]] = field(default_factory=list)

    # Hex sha256 of the companion ``<name>.memory.tar.gz`` bytes. Written
    # at save time by :func:`save_archive_and_tarball` /
    # :func:`_finalise_slot_write` (autosave) / :func:`save_session` so
    # that :func:`verify_memory_hash` can compare bytes-on-disk with the
    # bytes-as-written value. Empty string for archives produced before
    # P21.1 G3/G10 hardening was deployed — the verifier treats missing
    # hash as "legacy archive, skip verification" (backward compatible).
    memory_sha256: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archive_kind": self.archive_kind,
            "name": self.name,
            "saved_at": self.saved_at,
            "redact_api_keys": self.redact_api_keys,
            "memory_sha256": self.memory_sha256,
            "session": {
                "id": self.session_id,
                "name": self.session_name,
                "created_at": self.session_created_at,
                "messages": self.messages,
                "persona": self.persona,
                "model_config": self.model_config,
                "stage_state": self.stage_state,
                "eval_results": self.eval_results,
                "clock": self.clock,
            },
            "snapshots": {
                "hot": [s.to_json_dict() for s in self.snapshots_hot],
                "cold_meta": self.snapshots_cold_meta,
            },
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "SessionArchive":
        """Inverse of :meth:`to_json_dict` with lenient defaults.

        Raises :class:`PersistenceError` with ``InvalidArchive`` when
        required top-level fields are missing or the magic doesn't
        match. Passes unknown fields through silently (forward compat).
        """
        if not isinstance(data, dict):
            raise PersistenceError("InvalidArchive", "archive root must be a dict")
        kind = data.get("archive_kind")
        if kind != ARCHIVE_KIND:
            raise PersistenceError(
                "InvalidArchive",
                f"archive_kind mismatch: got {kind!r}, expected {ARCHIVE_KIND!r} "
                f"(is this actually a testbench session archive?)",
            )
        version = data.get("schema_version")
        if not isinstance(version, int):
            raise PersistenceError(
                "InvalidArchive", f"schema_version must be an int, got {type(version).__name__}",
            )
        if version > ARCHIVE_SCHEMA_VERSION:
            raise PersistenceError(
                "SchemaVersionTooNew",
                f"archive schema_version={version} is newer than this build "
                f"(supports up to {ARCHIVE_SCHEMA_VERSION}). Upgrade testbench.",
                detail={"got": version, "max_supported": ARCHIVE_SCHEMA_VERSION},
            )
        data = _migrate_archive_dict(data, from_version=version)

        sess = data.get("session") or {}
        snaps = data.get("snapshots") or {}
        hot_raw = snaps.get("hot") or []
        cold_meta = snaps.get("cold_meta") or []

        hot: list[Snapshot] = []
        for item in hot_raw:
            if not isinstance(item, dict):
                continue
            try:
                hot.append(Snapshot.from_json_dict(item))
            except Exception as exc:  # noqa: BLE001
                python_logger().warning(
                    "persistence: failed to parse hot snapshot (%s); skipping", exc,
                )

        return cls(
            schema_version=ARCHIVE_SCHEMA_VERSION,
            archive_kind=ARCHIVE_KIND,
            name=str(data.get("name") or ""),
            saved_at=str(data.get("saved_at") or ""),
            redact_api_keys=bool(data.get("redact_api_keys", True)),
            # ``memory_sha256`` defaults to empty string so legacy
            # archives (pre P21.1 G3/G10) deserialize cleanly; the
            # verifier treats empty as "no expectation, skip check".
            memory_sha256=str(data.get("memory_sha256") or ""),
            session_id=str(sess.get("id") or ""),
            session_name=str(sess.get("name") or ""),
            session_created_at=str(sess.get("created_at") or ""),
            messages=list(sess.get("messages") or []),
            persona=dict(sess.get("persona") or {}),
            model_config=dict(sess.get("model_config") or {}),
            stage_state=dict(sess.get("stage_state") or {}),
            eval_results=list(sess.get("eval_results") or []),
            clock=dict(sess.get("clock") or {}),
            snapshots_hot=hot,
            snapshots_cold_meta=[dict(m) for m in cold_meta if isinstance(m, dict)],
        )


def _migrate_archive_dict(data: dict[str, Any], *, from_version: int) -> dict[str, Any]:
    """Schema migration ladder. No-op for v1.

    Future shape:

    .. code-block:: python

        if from_version < 2:
            data = _migrate_v1_to_v2(data)
        if from_version < 3:
            ...

    Each step mutates-a-copy to keep migration steps composable. Kept
    here as a placeholder so new agents know *where* to add migrations.
    """
    return data


# ── serialize (Session → SessionArchive → disk) ─────────────────────


def serialize_session(
    session: "Session",
    *,
    name: str,
    redact_api_keys: bool = True,
) -> SessionArchive:
    """Build a :class:`SessionArchive` from the live session.

    Does **not** touch disk; the caller composes ``serialize_session``
    + :func:`save_archive_and_tarball` so tests can round-trip through
    memory without a sandbox on disk.
    """
    validate_name(name)
    now_iso = datetime.now().isoformat(timespec="seconds")
    model_cfg = dict(session.model_config or {})
    if redact_api_keys:
        model_cfg = redact_model_config(model_cfg)

    # Split hot vs cold from snapshot_store. Hot entries are already in
    # RAM with full payloads; cold meta points at on-disk .snapshots/
    # files that the tarball will ship alongside.
    snapshots_hot: list[Snapshot] = []
    snapshots_cold_meta: list[dict[str, Any]] = []
    if session.snapshot_store is not None:
        for snap in list(session.snapshot_store._hot):  # noqa: SLF001
            snapshots_hot.append(copy.deepcopy(snap))
        for meta in session.snapshot_store._cold_meta:  # noqa: SLF001
            snapshots_cold_meta.append(dict(meta))

    return SessionArchive(
        schema_version=ARCHIVE_SCHEMA_VERSION,
        archive_kind=ARCHIVE_KIND,
        name=name,
        saved_at=now_iso,
        redact_api_keys=redact_api_keys,
        session_id=session.id,
        session_name=session.name,
        session_created_at=session.created_at.isoformat(timespec="seconds"),
        messages=copy.deepcopy(session.messages or []),
        persona=copy.deepcopy(session.persona or {}),
        model_config=model_cfg,
        stage_state=copy.deepcopy(session.stage_state or {}),
        eval_results=copy.deepcopy(session.eval_results or []),
        clock=session.clock.to_dict() if session.clock else {},
        snapshots_hot=snapshots_hot,
        snapshots_cold_meta=snapshots_cold_meta,
    )


def pack_memory_tarball(sandbox_app_docs: Path) -> bytes:
    """Pack the sandbox ``_app_docs`` directory into gzip-tar bytes.

    Walks the entire subtree but only keeps files under the allow-list
    prefixes (memory / config / character_cards / .snapshots / …).
    Per-file size cap is :data:`_MAX_FILE_BYTES`; oversize files are
    skipped with a warning (matches snapshot_store behavior).

    Returns raw bytes so the caller can decide whether to write them
    to disk (save) or base64-encode them into a JSON body (export).
    """
    if not sandbox_app_docs.exists():
        # Valid case: sandbox was freshly created then destroyed, nothing
        # to pack. Emit an empty tar rather than raising; Load will just
        # restore zero files and the new session starts clean.
        python_logger().warning(
            "persistence: sandbox %s does not exist; producing empty tar",
            sandbox_app_docs,
        )
        sandbox_app_docs.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    # ``mode="w:gz"`` opens a gzip-compressed tar stream.
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(sandbox_app_docs.rglob("*")):
            if not path.is_file():
                continue
            try:
                relpath = path.relative_to(sandbox_app_docs).as_posix()
            except ValueError:
                continue
            # Keep only known subdirs; skip anything else (e.g. stray
            # tempfiles at the app_docs root).
            if not any(relpath.startswith(p) for p in _ALLOWED_TAR_PREFIXES):
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                python_logger().warning(
                    "persistence: stat failed on %s (%s); skipping", path, exc,
                )
                continue
            if size > _MAX_FILE_BYTES:
                python_logger().warning(
                    "persistence: file %s is %d bytes > cap %d; skipping to "
                    "keep archive size sane",
                    path, size, _MAX_FILE_BYTES,
                )
                continue
            try:
                tar.add(str(path), arcname=relpath, recursive=False)
            except OSError as exc:
                python_logger().warning(
                    "persistence: tar.add failed on %s (%s); skipping", path, exc,
                )
                continue
    return buf.getvalue()


def compute_memory_sha256(tarball_bytes: bytes) -> str:
    """Hex digest of the companion tarball bytes (G3/G10).

    Single-line helper kept close to :func:`save_archive_and_tarball` and
    :func:`verify_memory_hash` so callers don't accidentally use a
    different algorithm / encoding. ``tarball_bytes`` is expected to be
    the raw ``tarfile.open(mode="w:gz")`` output; hashing the bytes that
    actually hit the disk (rather than the pre-gzip tar stream) lets
    verification detect bit-rot in the on-disk ``.memory.tar.gz`` file
    directly without having to re-read the inner tar members.
    """
    return hashlib.sha256(tarball_bytes).hexdigest()


def verify_memory_hash(
    archive: SessionArchive, tarball_bytes: bytes,
) -> dict[str, Any]:
    """Compare ``archive.memory_sha256`` with the sha256 of ``tarball_bytes``.

    Returns a structured result dict the router can log / surface to the
    UI; **never raises**. Semantics:

    * ``{"legacy": True}`` — archive has no stored hash (pre-G3/G10
      deploy). The verifier has no expectation, so the caller should
      simply proceed.
    * ``{"legacy": False, "match": True, ...}`` — happy path.
    * ``{"legacy": False, "match": False, "expected": ..., "actual": ...}``
      — the bytes on disk don't match what was originally written.
      Callers currently **log a warning and continue** (P21.1 G3/G10 is
      diagnostic, not enforcement — matches §3A F3 "report, don't auto
      delete" discipline): we surface the mismatch so the user can
      investigate (disk corruption / manual edit / partial tar swap),
      but we don't refuse to load because the user may have a legit
      reason to accept the mismatched bytes (e.g. they manually
      repaired the tar to recover). A future ``?strict=1`` opt-in could
      elevate this to a refusal; not worth the UX cost today.
    """
    expected = (archive.memory_sha256 or "").strip().lower()
    if not expected:
        return {
            "legacy": True,
            "match": None,
            "expected": "",
            "actual": "",
            "message": "archive has no memory_sha256; skipping verification",
        }
    actual = compute_memory_sha256(tarball_bytes)
    match = expected == actual
    return {
        "legacy": False,
        "match": match,
        "expected": expected,
        "actual": actual,
        "message": (
            "memory tarball hash OK" if match
            else f"memory tarball hash mismatch (expected {expected[:12]}…, "
                 f"got {actual[:12]}…); archive may be corrupt or manually edited"
        ),
    }


def save_archive_and_tarball(
    archive: SessionArchive,
    tarball_bytes: bytes,
    *,
    overwrite: bool = False,
) -> Path:
    """Write the two files for ``archive.name`` atomically.

    Writes the tarball first, then the JSON index — if the process dies
    between them the orphan tarball is recoverable (just missing from the
    list), but a stale JSON pointing at a missing tar would be worse.

    Before writing, the archive's ``memory_sha256`` is populated from
    ``tarball_bytes`` (P21.1 G3/G10) so :func:`verify_memory_hash` can
    later detect bit-rot / silent tar swaps.

    Returns the absolute path of the main ``<name>.json`` file.
    """
    tb_config.SAVED_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _json_path_for(archive.name)
    tar_path = _tar_path_for(archive.name)

    if not overwrite and json_path.exists():
        raise PersistenceError(
            "ArchiveExists",
            f"archive {archive.name!r} already exists; pass overwrite=True to replace",
            detail={"name": archive.name},
        )

    # Pin the hash of the bytes we're about to write. Callers that
    # serialize + pack + save in separate steps (autosave) must do the
    # equivalent assignment themselves; the named-save path owns it here
    # so the hash always matches the bytes that actually land on disk.
    archive.memory_sha256 = compute_memory_sha256(tarball_bytes)

    try:
        _atomic_write_bytes(tar_path, tarball_bytes)
        _atomic_write_json(json_path, archive.to_json_dict())
    except OSError as exc:
        raise PersistenceError(
            "WriteFailed",
            f"failed to persist archive {archive.name!r}: {exc}",
            detail={"name": archive.name, "os_error": str(exc)},
        ) from exc

    python_logger().info(
        "persistence: saved %s (json=%d bytes, tar=%d bytes)",
        archive.name, json_path.stat().st_size, tar_path.stat().st_size,
    )
    return json_path


# ── list / load / delete ───────────────────────────────────────────


def list_saved() -> list[SessionArchiveMetadata]:
    """Return summary metadata for every ``*.json`` in saved_sessions/.

    Corrupt or unreadable files come back with ``error`` set so the UI
    can show them greyed-out with a tooltip rather than disappearing.
    Sort order: most-recently-saved first.
    """
    out: list[SessionArchiveMetadata] = []
    root = tb_config.SAVED_SESSIONS_DIR
    if not root.exists():
        return out

    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        # Skip autosave dir's children (autosave has its own listing
        # endpoint in P22). This method only walks the top level.
        name = path.stem
        try:
            validate_name(name)
        except PersistenceError:
            # Odd filename (e.g. starts with `_`) — skip without warning.
            continue

        tar_path = _tar_path_for(name)
        tar_exists = tar_path.exists()
        size_bytes = path.stat().st_size
        if tar_exists:
            size_bytes += tar_path.stat().st_size

        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            # Don't fully reconstruct Snapshots — metadata listing
            # should be cheap and tolerant.
            kind = data.get("archive_kind")
            if kind != ARCHIVE_KIND:
                raise PersistenceError(
                    "InvalidArchive",
                    f"archive_kind mismatch: {kind!r}",
                )
            sess = data.get("session") or {}
            snaps = data.get("snapshots") or {}
            # Missing companion tarball = broken archive. We keep the
            # JSON-level fields populated (saved_at / message_count /…)
            # so the user can still tell which session this was, but
            # ``error`` flips the row to grey'd-out + disables Load so
            # they don't accidentally restore with empty memory. Without
            # this guard the UI would happily load the session with zero
            # files in sandbox/_app_docs (confusing, data-loss-looking).
            tar_error = None
            if not tar_exists:
                tar_error = (
                    f"TarballMissing: {tar_path.name} not found on disk"
                )
                python_logger().warning(
                    "persistence: list_saved marks %s as broken "
                    "(companion tarball missing)", name,
                )
            meta = SessionArchiveMetadata(
                name=name,
                saved_at=str(data.get("saved_at") or ""),
                session_name=str(sess.get("name") or ""),
                session_id=str(sess.get("id") or ""),
                message_count=len(sess.get("messages") or []),
                snapshot_count=(
                    len(snaps.get("hot") or [])
                    + len(snaps.get("cold_meta") or [])
                ),
                eval_count=len(sess.get("eval_results") or []),
                size_bytes=size_bytes,
                schema_version=int(data.get("schema_version") or 0),
                redacted=bool(data.get("redact_api_keys", True)),
                error=tar_error,
            )
        except Exception as exc:  # noqa: BLE001 — corrupt archive UI path
            python_logger().warning(
                "persistence: list_saved failed to parse %s (%s)", path, exc,
            )
            meta = SessionArchiveMetadata(
                name=name,
                saved_at="",
                session_name="",
                session_id="",
                message_count=0,
                snapshot_count=0,
                eval_count=0,
                size_bytes=size_bytes,
                schema_version=0,
                redacted=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        out.append(meta)

    # Sort: newest saved_at first; broken entries go last.
    def _sort_key(m: SessionArchiveMetadata) -> tuple[int, str]:
        return (0 if m.error is None else 1, m.saved_at)

    out.sort(key=_sort_key, reverse=True)
    # Broken ones back to the end after the reverse.
    out.sort(key=lambda m: m.error is not None)
    return out


def load_archive(name: str) -> SessionArchive:
    """Read + parse ``<name>.json``. Does **not** touch the tarball.

    Raises :class:`PersistenceError` with a code the router can map to
    HTTP status.
    """
    validate_name(name)
    json_path = _json_path_for(name)
    if not json_path.exists():
        raise PersistenceError(
            "ArchiveNotFound",
            f"archive {name!r} does not exist",
            detail={"name": name, "path": str(json_path)},
        )
    try:
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PersistenceError(
            "InvalidArchive",
            f"archive {name!r} is not valid JSON: {exc}",
            detail={"name": name},
        ) from exc
    archive = SessionArchive.from_json_dict(data)
    # Sanity: the tarball companion should exist. We don't fail load
    # here (a missing tar just means the new sandbox will have empty
    # memory), but the router surfaces a warning toast.
    tar_path = _tar_path_for(name)
    if not tar_path.exists():
        python_logger().warning(
            "persistence: archive %s has no companion tarball at %s",
            name, tar_path,
        )
    return archive


def delete_saved(name: str) -> dict[str, Any]:
    """Remove the JSON + tarball pair. Idempotent on partial state.

    Returns ``{name, json_removed, tar_removed}`` for the UI toast.

    Deletion order (P21.1 G8) — **tar first, JSON second**:

    * ``list_saved`` keys off ``*.json`` (not ``*.tar.gz``), and as of
      P21 surfaces ``TarballMissing`` when the companion tarball is
      absent. If we're interrupted mid-delete, we want the leftover
      state to be "JSON present, tarball gone" — that renders as a
      broken row the user can retry deletion on (idempotent closure).
    * The reverse order ("JSON first, tar second") creates an invisible
      orphan ``.memory.tar.gz`` that ``list_saved`` can never surface,
      because there's no JSON anchor left → silent disk leak.
    """
    validate_name(name)
    json_path = _json_path_for(name)
    tar_path = _tar_path_for(name)

    json_removed = False
    tar_removed = False
    try:
        if tar_path.exists():
            tar_path.unlink()
            tar_removed = True
    except OSError as exc:
        raise PersistenceError(
            "WriteFailed",
            f"failed to delete {tar_path}: {exc}",
            detail={"name": name},
        ) from exc
    try:
        if json_path.exists():
            json_path.unlink()
            json_removed = True
    except OSError as exc:
        raise PersistenceError(
            "WriteFailed",
            f"failed to delete {json_path}: {exc}",
            detail={"name": name},
        ) from exc

    if not json_removed and not tar_removed:
        raise PersistenceError(
            "ArchiveNotFound", f"no archive files for {name!r}",
        )
    return {"name": name, "json_removed": json_removed, "tar_removed": tar_removed}


# ── tarball extract + session apply ────────────────────────────────


def read_tarball_bytes(name: str) -> bytes:
    """Return raw tar.gz bytes for ``<name>.memory.tar.gz``.

    Raises :class:`PersistenceError` (``TarballMissing``) when the file
    is absent so the router can distinguish "archive fully gone" from
    "json present but tar gone" (the latter still lets us restore an
    empty-memory session if the user is OK with it).
    """
    validate_name(name)
    tar_path = _tar_path_for(name)
    if not tar_path.exists():
        raise PersistenceError(
            "TarballMissing",
            f"companion tarball for archive {name!r} is missing at {tar_path}",
            detail={"name": name, "path": str(tar_path)},
        )
    try:
        return tar_path.read_bytes()
    except OSError as exc:
        raise PersistenceError(
            "WriteFailed",
            f"failed to read {tar_path}: {exc}",
            detail={"name": name},
        ) from exc


def restore_memory_tarball(
    sandbox_app_docs: Path, tarball_bytes: bytes,
) -> dict[str, Any]:
    """Extract ``tarball_bytes`` into ``sandbox_app_docs``.

    Strategy:
      * Clear the existing ``_app_docs`` subtree first so leftover files
        from the freshly-created empty skeleton don't linger and confuse
        Memory/Character loaders.
      * Iterate tar members, reject anything that would escape the
        sandbox (absolute paths / `..` components / symlinks / device
        files). Skip members whose prefix isn't in the allow-list.
      * Enforce :data:`_MAX_TAR_UNCOMPRESSED_BYTES` cumulative size cap.

    Returns a small stats dict for the router to log.
    """
    if not tarball_bytes:
        python_logger().info(
            "persistence: tarball is empty; leaving sandbox %s untouched",
            sandbox_app_docs,
        )
        return {"files_restored": 0, "bytes_restored": 0, "empty_tar": True}

    sandbox_app_docs.mkdir(parents=True, exist_ok=True)

    # Wipe the existing tree except the top-level skeleton markers. We
    # use robust_rmtree-style best effort: the caller already disposed
    # SQLite engines, so normal rmtree should succeed here. If a leftover
    # handle sneaks through we log and continue (the missing cleanup
    # shows up as "extra files after extract" which is visible in UI).
    for child in list(sandbox_app_docs.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=False)
            else:
                child.unlink()
        except OSError as exc:
            python_logger().warning(
                "persistence: pre-extract cleanup failed on %s (%s); continuing",
                child, exc,
            )

    files_restored = 0
    bytes_restored = 0
    resolved_root = sandbox_app_docs.resolve()

    try:
        with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
            for member in tar:
                if member.isdir():
                    continue
                # Reject non-regular files (symlinks, device nodes, etc).
                if not member.isfile():
                    python_logger().warning(
                        "persistence: rejecting non-regular tar member %s (type=%d)",
                        member.name, member.type,
                    )
                    continue
                # Reject absolute / traversal paths.
                member_name = member.name.replace("\\", "/")
                if member_name.startswith("/") or ".." in member_name.split("/"):
                    python_logger().warning(
                        "persistence: rejecting traversal tar member %r", member_name,
                    )
                    continue
                # Check allow-list prefix.
                if not any(member_name.startswith(p) for p in _ALLOWED_TAR_PREFIXES):
                    python_logger().warning(
                        "persistence: rejecting out-of-allowlist member %r",
                        member_name,
                    )
                    continue
                # Size cap.
                if bytes_restored + int(member.size or 0) > _MAX_TAR_UNCOMPRESSED_BYTES:
                    raise PersistenceError(
                        "InvalidArchive",
                        f"tarball exceeds {_MAX_TAR_UNCOMPRESSED_BYTES} bytes "
                        f"uncompressed; refusing to extract further",
                    )
                target = (sandbox_app_docs / member_name).resolve()
                # Final safety check: resolved path must stay inside
                # the sandbox root (catches symlink-based escapes that
                # the earlier `..` check might have missed).
                try:
                    target.relative_to(resolved_root)
                except ValueError:
                    python_logger().warning(
                        "persistence: rejecting escape path %r -> %s",
                        member_name, target,
                    )
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # Manually copy the file contents to avoid tarfile's
                # legacy permission/owner replay on Windows.
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                content = extracted.read()
                target.write_bytes(content)
                files_restored += 1
                bytes_restored += len(content)
    except (OSError, tarfile.TarError) as exc:
        raise PersistenceError(
            "InvalidArchive", f"failed to extract tarball: {exc}",
        ) from exc

    python_logger().info(
        "persistence: restored %d files (%d bytes) into %s",
        files_restored, bytes_restored, sandbox_app_docs,
    )
    return {
        "files_restored": files_restored,
        "bytes_restored": bytes_restored,
        "empty_tar": False,
    }


def apply_to_session(session: "Session", archive: SessionArchive) -> dict[str, Any]:
    """Replace ``session``'s state with ``archive`` in-place.

    **Preconditions** (the caller is responsible for all of these):
      * ``session`` is a freshly-created Session (post ``store.create``).
      * Its ``snapshot_store`` is initialised and ``list_metadata`` is
        empty except for the initial ``t0:init`` (which this function
        **replaces** with the archive's timeline).
      * The companion tarball has already been extracted via
        :func:`restore_memory_tarball` — this function only patches
        the Python-level fields.

    Replaces in place rather than returning a new Session because the
    ``Session`` dataclass owns the ``asyncio.Lock`` + ``busy_op`` markers
    that the rest of the system references by identity.
    """
    # Scalar/collection fields — deepcopy the archive payloads so a
    # later mutation of the archive object can't leak into the session.
    session.messages = copy.deepcopy(archive.messages)
    session.persona = copy.deepcopy(archive.persona)
    session.model_config = copy.deepcopy(archive.model_config)
    session.stage_state = copy.deepcopy(archive.stage_state)
    session.eval_results = copy.deepcopy(archive.eval_results)

    # Virtual clock.
    if archive.clock:
        session.clock = VirtualClock.from_dict(archive.clock)
    else:
        session.clock = VirtualClock()

    # Transient runtime state is NOT carried in archives.
    session.memory_previews = {}
    session.script_state = None
    session.auto_state = None

    # Snapshot timeline: wipe whatever the freshly-created session
    # inserted (the t0:init anchor) and repopulate from the archive.
    store = session.snapshot_store
    hot_count = 0
    cold_count = 0
    if store is not None:
        # Drop the empty-session init snapshot(s). .clear accepts
        # ``keep_backups=False`` which removes everything.
        store.clear(keep_backups=False)

        # Re-insert hot snapshots (deep-copied from archive).
        for snap in archive.snapshots_hot:
            store._hot.append(copy.deepcopy(snap))  # noqa: SLF001
            hot_count += 1

        # Re-insert cold meta. The matching ``.snapshots/<id>.json.gz``
        # files should already be on disk from the tarball extraction;
        # if any are missing we log but don't raise — rewind to that id
        # will simply fail gracefully with "cold entry has no disk file".
        cold_dir = store._cold_dir  # noqa: SLF001
        for meta in archive.snapshots_cold_meta:
            store._cold_meta.append(dict(meta))  # noqa: SLF001
            cold_count += 1
            sid = meta.get("id")
            if sid:
                expected = cold_dir / f"{sid}.json.gz"
                if not expected.exists():
                    python_logger().warning(
                        "persistence: cold snapshot %s missing from tarball (%s)",
                        sid, expected,
                    )

    python_logger().info(
        "persistence: applied archive %r to session %s (messages=%d, "
        "hot_snapshots=%d, cold_snapshots=%d, evals=%d)",
        archive.name, session.id, len(session.messages),
        hot_count, cold_count, len(session.eval_results),
    )
    return {
        "messages": len(session.messages),
        "eval_results": len(session.eval_results),
        "snapshots_hot": hot_count,
        "snapshots_cold": cold_count,
        "persona_has_character_name": bool(
            (session.persona or {}).get("character_name"),
        ),
        "redacted_api_keys": bool(archive.redact_api_keys),
    }


# ── import / export for transport ──────────────────────────────────


def export_to_payload(
    archive: SessionArchive, tarball_bytes: bytes,
) -> dict[str, Any]:
    """Bundle an archive + tarball into a JSON-safe transport payload.

    Used by ``POST /api/session/export`` (P21) for one-shot inline
    transfer (e.g. copy to clipboard / send over chat). The tarball is
    base64-encoded. Large tarballs push the payload size past
    comfortable JSON limits — for very large sessions the dedicated
    "download the two files" path (Diagnostics → Paths [Open Saved
    Sessions Dir]) stays the recommended workflow.
    """
    return {
        "kind": "testbench_session_export",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "archive": archive.to_json_dict(),
        "tarball_b64": base64.b64encode(tarball_bytes).decode("ascii"),
    }


def import_from_payload(
    payload: dict[str, Any], *, name: str | None = None, overwrite: bool = False,
) -> Path:
    """Persist an exported payload to ``saved_sessions/``.

    * ``payload['archive']`` is a JSON-dict archive (must pass
      :meth:`SessionArchive.from_json_dict`).
    * ``payload['tarball_b64']`` is the base64-encoded gzip-tar.
    * ``name`` optional override; defaults to ``archive.name``.
    """
    if not isinstance(payload, dict):
        raise PersistenceError("InvalidArchive", "payload must be a dict")

    archive_data = payload.get("archive")
    if not isinstance(archive_data, dict):
        raise PersistenceError(
            "InvalidArchive", "payload.archive must be a dict",
        )
    tar_b64 = payload.get("tarball_b64")
    if not isinstance(tar_b64, str):
        raise PersistenceError(
            "InvalidArchive", "payload.tarball_b64 must be a base64 string",
        )
    try:
        tar_bytes = base64.b64decode(tar_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise PersistenceError(
            "InvalidArchive", f"payload.tarball_b64 decode failed: {exc}",
        ) from exc

    archive = SessionArchive.from_json_dict(archive_data)
    if name is not None:
        archive.name = validate_name(name)
    else:
        validate_name(archive.name)

    # P21.1 G3/G10: import is an explicit "trust boundary crossed" op
    # (user received this blob from elsewhere, base64 roundtrip + JSON
    # chat channels all have quiet corruption modes). If the incoming
    # archive carries ``memory_sha256``, the base64-decoded tar bytes
    # **must** match — any mismatch is transport corruption and we
    # refuse the import (InvalidArchive → 400) so the user knows.
    # Legacy exports without the field are accepted (backward compat);
    # :func:`save_archive_and_tarball` below will backfill the hash
    # from the bytes we actually persist.
    verify = verify_memory_hash(archive, tar_bytes)
    if verify.get("match") is False:
        raise PersistenceError(
            "InvalidArchive",
            f"import payload tarball hash does not match archive metadata: "
            f"expected {verify['expected'][:12]}…, got {verify['actual'][:12]}…. "
            f"Most likely transport corruption — re-export and try again.",
            detail={
                "name": archive.name,
                "expected_memory_sha256": verify["expected"],
                "actual_memory_sha256": verify["actual"],
            },
        )

    return save_archive_and_tarball(archive, tar_bytes, overwrite=overwrite)


# ── pre-load safety backup ─────────────────────────────────────────


def write_pre_load_backup(session: "Session") -> Path | None:
    """Dump the current session's small JSON state before a Load wipes it.

    Rationale: the user might realise mid-Load they picked the wrong
    archive. The snapshot timeline has a ``pre_rewind_backup`` safety
    net for in-session rewinds, but **crossing sessions** (the Load
    endpoint destroys the current sandbox) nukes the snapshot_store
    itself. So before the destroy, we write a lightweight dump to
    ``saved_sessions/_autosave/pre_load_<session_id>_<ts>.json`` that
    the user can Load back manually if something went wrong.

    We do NOT pack memory here — pre-load backup is intentionally
    lightweight and JSON-only. The full sandbox is still under
    ``testbench_data/sandboxes/<session_id>/`` until the caller's
    ``store.destroy(purge_sandbox=True)`` step; crash-recovery (P22)
    is the right layer to handle "rebuild a session from a surviving
    sandbox dir".

    Returns the path of the backup file (or ``None`` if the write
    failed — this function never raises to keep Load resilient).
    """
    try:
        # Take a single ``datetime.now()`` and reuse it for both the
        # archive ``name`` field (epoch-int form) and the on-disk
        # filename suffix (human-friendly YYYYMMDD_HHMMSS form). Two
        # separate ``now()`` calls would tick across a second boundary
        # ~once-in-a-million-runs and produce an archive whose internal
        # ``name`` field doesn't match the surrounding filename — making
        # crash-recovery scripts that match name↔file by parsing the
        # suffix silently fall back to a less-precise heuristic (GH
        # AI-review issue #11).
        now_dt = datetime.now()
        archive = serialize_session(
            session,
            name=f"pre_load_{session.id}_{int(now_dt.timestamp())}",
            redact_api_keys=True,
        )
        target = (
            tb_config.AUTOSAVE_DIR / f"pre_load_{session.id}_"
            f"{now_dt.strftime('%Y%m%d_%H%M%S')}.json"
        )
        _atomic_write_json(target, archive.to_json_dict())
        python_logger().info(
            "persistence: pre_load_backup written to %s", target,
        )
        return target
    except Exception as exc:  # noqa: BLE001 — best-effort
        python_logger().warning(
            "persistence: pre_load_backup failed (%s); Load will proceed anyway",
            exc,
        )
        return None


# ── convenience: one-shot save from Session ────────────────────────


def save_session(
    session: "Session",
    *,
    name: str,
    redact_api_keys: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """High-level save: serialize + pack + write in one call.

    Returns a dict describing what ended up on disk (used by the
    router to build the success toast).
    """
    name = validate_name(name)
    archive = serialize_session(session, name=name, redact_api_keys=redact_api_keys)
    sandbox_app_docs = session.sandbox._app_docs  # noqa: SLF001
    tar_bytes = pack_memory_tarball(sandbox_app_docs)
    json_path = save_archive_and_tarball(archive, tar_bytes, overwrite=overwrite)
    tar_path = _tar_path_for(name)
    return {
        "name": name,
        "json_path": str(json_path),
        "tar_path": str(tar_path) if tar_path.exists() else None,
        "json_bytes": json_path.stat().st_size,
        "tar_bytes": tar_path.stat().st_size if tar_path.exists() else 0,
        "saved_at": archive.saved_at,
        "redacted": redact_api_keys,
        "message_count": len(archive.messages),
        "snapshot_count": len(archive.snapshots_hot) + len(archive.snapshots_cold_meta),
        "eval_count": len(archive.eval_results),
    }


__all__ = [
    "ARCHIVE_KIND",
    "ARCHIVE_SCHEMA_VERSION",
    "PersistenceError",
    "REDACTED_SECRET",
    "SessionArchive",
    "SessionArchiveMetadata",
    "apply_to_session",
    "compute_memory_sha256",
    "delete_saved",
    "export_to_payload",
    "import_from_payload",
    "list_saved",
    "load_archive",
    "pack_memory_tarball",
    "read_tarball_bytes",
    "redact_model_config",
    "restore_memory_tarball",
    "save_archive_and_tarball",
    "save_session",
    "serialize_session",
    "validate_name",
    "verify_memory_hash",
    "write_pre_load_backup",
]
