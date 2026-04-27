"""Health check and system info endpoints.

P20 extended this router with ``/system/paths`` and ``/system/open_path``
so the Diagnostics → Paths sub-page can show "where is my data?" at a
glance and open those directories in the host file manager without the
tester having to remember the relative path prefix.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger
from tests.testbench.session_store import get_session_store

router = APIRouter(tags=["health"])

# 进程启动时生成一次. 前端用它判断"服务是否新启动了" — 例如 welcome 横幅
# 想每次重启都再提醒一次: 比较上次看过时存的 boot_id 和现在返回的, 不一致
# 就当作"新一轮启动"重置 LS 里的 seen flag. 格式用 UUID 避免任何"按时间
# 推测前后" 类错觉 (比如同秒内连重启不会误判为同一次).
BOOT_ID = uuid.uuid4().hex


@router.get("/healthz")
async def healthz() -> dict:
    """Basic liveness probe. Returns ``{"status": "ok", "boot_id": ...}``.

    ``boot_id`` 每次进程启动生成一次, 前端用来检测服务重启.
    """
    return {"status": "ok", "boot_id": BOOT_ID}


@router.get("/version")
async def version() -> dict:
    """Static version metadata for the testbench UI.

    Read the numeric version + phase from :mod:`tests.testbench.config`
    so that a single constant bump updates both FastAPI's OpenAPI spec
    (used by ``/api/docs``) and Settings → About. The About page
    renders ``"{name} {version}"`` and ``phase`` as-is, so keep these
    strings tester-friendly rather than machine-readable.
    """
    return {
        "name": "N.E.K.O. Testbench",
        "version": tb_config.TESTBENCH_VERSION,
        "phase": tb_config.TESTBENCH_PHASE,
        "last_updated": tb_config.TESTBENCH_LAST_UPDATED,
        "host": tb_config.DEFAULT_HOST,
        "port": tb_config.DEFAULT_PORT,
        "boot_id": BOOT_ID,
    }


# ── /system/paths ───────────────────────────────────────────────────
#
# Diagnostics → Paths 子页列出所有 "testbench 会在这里读/写数据" 的目录.
# 每项含 key (前端 i18n 查 label/tooltip), 绝对路径 (系统原生分隔符, 方
# 便 copy-to-clipboard), 存在标志, 字节大小 (`du -sb` 等价, 但纯 Python
# 实现避免外部依赖), 文件/子目录计数. 当前会话的沙盒和日志文件会被单
# 独列出 (指向 current session 的子路径, 不是所有 sandbox/log 的聚合),
# 因为 "我这次测试的数据在哪里" 是第一优先级.


def _safe_dir_size(path: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for ``path`` (0, 0) if unreadable.

    Walks recursively. Missing / permission-denied files are silently
    skipped — Paths 子页是诊断工具, 不能因为一个僵尸文件让整个列表爆
    炸 500.
    """
    if not path.exists() or not path.is_dir():
        return (0, 0)
    total_bytes = 0
    file_count = 0
    for sub in path.rglob("*"):
        try:
            if sub.is_file():
                total_bytes += sub.stat().st_size
                file_count += 1
        except OSError:
            continue
    return (total_bytes, file_count)


def _safe_file_size(path: Path) -> int:
    """Return size in bytes, or 0 if the file doesn't exist / is a dir."""
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _describe_path(
    *,
    key: str,
    path: Path,
    kind: str,                # "dir" | "file"
    session_scoped: bool = False,
) -> dict[str, Any]:
    """Build one path entry for ``/system/paths`` response.

    ``session_scoped`` signals to the UI that this entry points at a
    path that belongs to the **current active session** (sandbox dir /
    active log file) and would become stale after session switch.
    """
    exists = path.exists()
    if kind == "dir":
        total_bytes, file_count = _safe_dir_size(path)
    else:
        total_bytes = _safe_file_size(path)
        file_count = 1 if exists else 0
    return {
        "key": key,
        "kind": kind,
        "path": str(path),
        # POSIX form is handy for grep across OSs — we expose both so
        # the UI's [Copy path] can default to native while the tooltip
        # shows POSIX for documentation snippets.
        "path_posix": path.as_posix(),
        "exists": exists,
        "size_bytes": total_bytes,
        "file_count": file_count,
        "session_scoped": session_scoped,
    }


@router.get("/system/paths")
async def system_paths() -> dict[str, Any]:
    """List all filesystem locations the testbench uses at runtime.

    The response groups entries into:

    * **data_root**: the gitignored parent (``tests/testbench_data``) —
      everything else is under this.
    * **session**: current session's sandbox + today's JSONL log (may
      be empty if no session is active or the log file hasn't been
      created yet).
    * **shared**: cross-session directories (saved sessions, autosave,
      exports, user schemas, user dialog templates, all-sessions log
      directory, all-sandboxes directory).
    * **code**: read-only code-side directories surfaced so the tester
      can find builtin assets (docs / templates / static / builtin
      schemas / builtin dialog templates). These are NOT whitelisted
      for ``/system/open_path`` because opening code directories is
      out of scope for diagnostics.

    All size/count values use lazy rglob with OSError-tolerance so a
    single broken file never 500s the whole endpoint. Cost is O(entries
    under DATA_DIR); on a healthy dev machine this is well under 20 ms.
    """
    store = get_session_store()
    session = store.get()

    data_root = _describe_path(
        key="data_root", path=tb_config.DATA_DIR, kind="dir",
    )
    data_root["gitignored"] = True

    entries: list[dict[str, Any]] = []

    # Current session scoped paths — only if a session is active and its
    # paths actually exist. Testers care most about these.
    session_entries: list[dict[str, Any]] = []
    if session is not None:
        sandbox_dir = tb_config.SANDBOXES_DIR / session.id
        session_entries.append(_describe_path(
            key="current_sandbox",
            path=sandbox_dir,
            kind="dir",
            session_scoped=True,
        ))
        # Today's log file — the per-day JSONL path, even if not yet
        # written (exists=False). Lets the UI show "not yet created"
        # instead of hiding the row entirely so testers learn it exists.
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y%m%d")
        log_file = tb_config.session_log_path(session.id, today)
        session_entries.append(_describe_path(
            key="current_session_log",
            path=log_file,
            kind="file",
            session_scoped=True,
        ))

    shared_entries = [
        _describe_path(key="sandboxes_all",       path=tb_config.SANDBOXES_DIR,            kind="dir"),
        _describe_path(key="logs_all",            path=tb_config.LOGS_DIR,                 kind="dir"),
        _describe_path(key="saved_sessions",      path=tb_config.SAVED_SESSIONS_DIR,       kind="dir"),
        _describe_path(key="autosave",            path=tb_config.AUTOSAVE_DIR,             kind="dir"),
        _describe_path(key="exports",             path=tb_config.EXPORTS_DIR,              kind="dir"),
        _describe_path(key="user_schemas",        path=tb_config.USER_SCHEMAS_DIR,         kind="dir"),
        _describe_path(key="user_dialog_templates", path=tb_config.USER_DIALOG_TEMPLATES_DIR, kind="dir"),
    ]

    code_entries = [
        _describe_path(key="code_dir",            path=tb_config.CODE_DIR,                 kind="dir"),
        _describe_path(key="builtin_schemas",     path=tb_config.BUILTIN_SCHEMAS_DIR,      kind="dir"),
        _describe_path(key="builtin_dialog_templates", path=tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, kind="dir"),
        _describe_path(key="docs",                path=tb_config.DOCS_DIR,                 kind="dir"),
    ]

    entries.extend(session_entries)
    entries.extend(shared_entries)
    entries.extend(code_entries)

    return {
        "data_root": data_root,
        "entries": entries,
        "platform": platform.system(),   # "Windows" | "Darwin" | "Linux"
    }


# ── /system/open_path ───────────────────────────────────────────────
#
# The OS command used to pop the native file manager:
#   Windows  → os.startfile(path) — actual shell action, honors user's
#              file-association preferences (opens Explorer for a dir).
#   macOS    → subprocess.Popen(["open", path])
#   Linux/*  → subprocess.Popen(["xdg-open", path]) — delegates to the
#              desktop environment (GNOME/KDE/XFCE all wire this up).
#
# **Security constraint**: only paths that are *strictly* inside
# ``DATA_DIR`` are allowed. We resolve the incoming path, then check
# ``resolved.is_relative_to(DATA_DIR.resolve())``. Symlink escapes are
# blocked by ``Path.resolve()`` (follows symlinks). Anything else — code
# dir / C:\Windows / user home / relative ``..`` — gets a 403.


class OpenPathRequest(BaseModel):
    """Body for ``POST /system/open_path``.

    Two accepted shapes:

    * ``{"path": "<absolute>"}`` — legacy, used by Diagnostics → Paths's
      per-row [打开] button. The client had already fetched
      ``/system/paths`` so it knows the absolute path.
    * ``{"key": "<whitelisted_key>"}`` — P24 §12.3.E #13, used by the
      shared ``openFolderButton`` helper across Setup/Evaluation pages.
      The caller doesn't need to fetch ``/system/paths`` first — the
      server resolves ``key`` against the same whitelist
      ``/system/paths`` enumerates. This avoids every button paying a
      network round-trip just to learn the path it's whitelisted to
      open anyway.

    Exactly one of ``key`` or ``path`` must be set (validator below).
    """

    path: str | None = Field(
        default=None,
        description="Absolute path to open. Must be inside testbench_data/",
    )
    key: str | None = Field(
        default=None,
        description=(
            "Whitelisted path key (current_sandbox, user_schemas, "
            "user_dialog_templates, saved_sessions, autosave, exports, "
            "sandboxes_all, logs_all, current_session_log). Server "
            "resolves to path, so this avoids a /system/paths round-trip."
        ),
    )


#: Whitelisted ``key`` → ``Path`` factory for :func:`system_open_path`.
#: Each value is a **callable** (not a path) because some keys (like
#: ``current_sandbox`` / ``current_session_log``) depend on the active
#: session and can't be evaluated at module import time. Keep in sync
#: with :func:`system_paths` entry list.
def _resolve_open_path_key(key: str) -> Path | None:
    """Resolve a whitelisted ``key`` to an absolute ``Path``.

    Returns ``None`` if:

    * ``key`` is not in the whitelist.
    * ``key`` is session-scoped but no active session exists.

    The caller (``system_open_path``) turns ``None`` into a 404 with a
    friendly "no current session, activate one first" message instead
    of a generic 400, because the most common cause is exactly that.
    """
    store = get_session_store()
    try:
        session = store.get()
    except Exception:
        session = None

    if key == "current_sandbox":
        if session is None:
            return None
        return tb_config.SANDBOXES_DIR / session.id
    if key == "current_session_log":
        if session is None:
            return None
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y%m%d")
        return tb_config.session_log_path(session.id, today)
    # Shared paths — no session dependency.
    _shared: dict[str, Path] = {
        "sandboxes_all": tb_config.SANDBOXES_DIR,
        "logs_all": tb_config.LOGS_DIR,
        "saved_sessions": tb_config.SAVED_SESSIONS_DIR,
        "autosave": tb_config.AUTOSAVE_DIR,
        "exports": tb_config.EXPORTS_DIR,
        "user_schemas": tb_config.USER_SCHEMAS_DIR,
        "user_dialog_templates": tb_config.USER_DIALOG_TEMPLATES_DIR,
    }
    return _shared.get(key)


def _path_is_inside_data_dir(target: Path) -> bool:
    """Return True if ``target`` resolves to a path under ``DATA_DIR``.

    Uses :meth:`Path.resolve` so symlinks + ``..`` segments don't sneak
    out. ``Path.is_relative_to`` is strictly lexical — resolving first
    is crucial so something like
    ``tests/testbench_data/../../etc/passwd`` gets caught.
    """
    try:
        data_root = tb_config.DATA_DIR.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        return False
    # On Python 3.12+ is_relative_to never raises. Guard for edge cases
    # (different drives on Windows raise ValueError).
    try:
        return resolved.is_relative_to(data_root)
    except ValueError:
        return False


def _spawn_file_manager(path: Path) -> None:
    """Dispatch the OS-specific open command.

    Never raises on a missing desktop helper (xdg-open not installed
    on a headless Linux box) — we log a warning and propagate as
    :class:`HTTPException` 500 so the UI sees a clear error toast.
    """
    system = platform.system()
    if system == "Windows":
        # ``os.startfile`` is the canonical Shell ``ShellExecute`` bridge.
        # It opens a dir in Explorer, a file with the default handler.
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        return
    if system == "Darwin":
        # Popen + no-wait so the HTTP handler returns immediately. The
        # ``open`` CLI on macOS returns fast after spawning Finder.
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        return
    # Assume Linux/BSD — ``xdg-open`` is almost universal; fall back to
    # an informative error if missing rather than silently hanging.
    if shutil.which("xdg-open") is None:
        raise RuntimeError(
            "xdg-open not found — install xdg-utils or open the path "
            "manually",
        )
    subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


@router.post("/system/open_path")
async def system_open_path(body: OpenPathRequest) -> dict[str, Any]:
    """Open a whitelisted path in the host OS file manager.

    Two accepted request shapes (see :class:`OpenPathRequest`):

    * ``{"key": "<whitelisted_key>"}`` — resolved via
      :func:`_resolve_open_path_key`; bypasses ``path_is_inside_data_dir``
      because the whitelist already guarantees containment.
    * ``{"path": "<absolute>"}`` — legacy; checked against DATA_DIR.

    Returns ``{"ok": true, "path": ...}`` on success. Raises:

      * 400 if neither / both of ``key`` and ``path`` are set, or
        either is malformed.
      * 403 if a path-mode request resolves outside ``testbench_data/``.
      * 404 if the path doesn't exist on disk (opening a ghost path
        would silently spawn an empty Explorer window on Windows and
        an error dialog on macOS/Linux; surface the mistake early),
        or if a key-mode request is session-scoped with no active
        session.
      * 500 if the OS dispatcher itself raises (missing xdg-open /
        Explorer crashed / etc.).
    """
    raw_path = (body.path or "").strip()
    raw_key = (body.key or "").strip()
    if not raw_path and not raw_key:
        raise HTTPException(
            status_code=400, detail="either `key` or `path` is required",
        )
    if raw_path and raw_key:
        raise HTTPException(
            status_code=400, detail="send only one of `key` / `path`",
        )

    if raw_key:
        resolved_by_key = _resolve_open_path_key(raw_key)
        if resolved_by_key is None:
            # Distinguish "unknown key" (400) vs "valid key but needs
            # session" (404) — helps testers understand why the click
            # didn't open anything without them having to guess.
            known_session_scoped = {"current_sandbox", "current_session_log"}
            if raw_key in known_session_scoped:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"key={raw_key} is session-scoped but no active "
                        "session. Create a session first, then retry."
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail=f"unknown key: {raw_key}",
            )
        target = resolved_by_key
    else:
        try:
            target = Path(raw_path)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid path syntax: {exc}",
            ) from exc

        if not _path_is_inside_data_dir(target):
            python_logger().warning(
                "system_open_path: rejecting path %r outside DATA_DIR",
                raw_path,
            )
            raise HTTPException(
                status_code=403,
                detail="path must be inside tests/testbench_data/",
            )

    resolved = target.resolve()
    if not resolved.exists():
        raise HTTPException(
            status_code=404, detail=f"path does not exist: {resolved}",
        )

    try:
        _spawn_file_manager(resolved)
    except Exception as exc:  # noqa: BLE001 — surfaces as 500 with detail
        python_logger().warning(
            "system_open_path: OS dispatcher failed on %s (%s)",
            resolved, exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"failed to open file manager: {type(exc).__name__}: {exc}",
        ) from exc

    return {
        "ok": True,
        "path": str(resolved),
        "platform": platform.system(),
    }


# ── /system/orphans (P24 §15.2 A / P-A) ─────────────────────────────
#
# Orphan sandbox directory triage. Scans ``SANDBOXES_DIR`` for subdirs
# without an active session mapping and reports them to the user;
# deletion is a separate explicit endpoint. See
# :mod:`pipeline.boot_self_check` for the full rationale.


# ── /system/health (P24 §3.1 / H1 最小版) ──────────────────────────
#
# Aggregated read-only health snapshot. No new pipelines or background
# tasks — composes existing data sources (diagnostics_store ring buffer,
# logger.collect_logs_usage, autosave scheduler state, orphan scanner).
# Callers: Diagnostics Paths top card ("System Health: healthy/warning/
# critical"), optional external monitors via curl.

def _health_status_for(
    value: int | float | None,
    *,
    warn_at: int | float | None = None,
    crit_at: int | float | None = None,
    reverse: bool = False,
) -> str:
    """Bucket a numeric value into ``healthy / warning / critical``.

    ``reverse=True`` means "smaller is worse" (e.g. disk_free_gb — low
    disk is bad). Default is "bigger is worse" (error count goes up).
    ``None`` value always maps to ``healthy`` (missing measurement
    shouldn't cause alarm bells).
    """
    if value is None:
        return "healthy"
    if reverse:
        if crit_at is not None and value <= crit_at:
            return "critical"
        if warn_at is not None and value <= warn_at:
            return "warning"
        return "healthy"
    if crit_at is not None and value >= crit_at:
        return "critical"
    if warn_at is not None and value >= warn_at:
        return "warning"
    return "healthy"


@router.get("/system/health")
async def system_health() -> dict[str, Any]:
    """Aggregated health snapshot across disk / logs / orphans / diagnostics.

    Overall ``status`` is the worst of all individual ``check`` statuses.
    Thresholds are intentionally loose — this is a user-facing "is
    anything obviously broken?" indicator, not a precise monitoring
    system.

    Checks:

    * **disk_free_gb**: available bytes on the DATA_DIR filesystem.
      critical < 0.5 GB, warn < 2 GB.
    * **log_dir_size_mb**: total bytes under LOGS_DIR. warn > 500 MB,
      critical > 2000 MB (the user should trigger log cleanup).
    * **orphan_sandboxes**: count from :func:`scan_orphan_sandboxes`.
      warn >= 3, critical >= 10 (indicates repeated hard-kill cycles).
    * **autosave_scheduler**: true if the active session's scheduler
      is running, null if no active session.
    * **diagnostics_errors**: count of ``level == "error"`` entries
      in the ring buffer (a coarse "recent problems" indicator).
      warn >= 5, critical >= 20.
    """
    import shutil
    from tests.testbench.logger import collect_logs_usage
    from tests.testbench.pipeline import diagnostics_store
    from tests.testbench.pipeline.boot_self_check import scan_orphan_sandboxes

    checks: dict[str, dict[str, Any]] = {}

    # Disk free on DATA_DIR's filesystem
    try:
        du = shutil.disk_usage(tb_config.DATA_DIR)
        free_gb = round(du.free / (1024 ** 3), 2)
    except OSError:
        free_gb = None
    checks["disk_free_gb"] = {
        "value": free_gb,
        "threshold_warn": 2.0,
        "threshold_critical": 0.5,
        "status": _health_status_for(
            free_gb, warn_at=2.0, crit_at=0.5, reverse=True,
        ),
    }

    # Log directory total size
    try:
        logs_usage = collect_logs_usage()
        log_mb = round(logs_usage.get("total_bytes", 0) / (1024 ** 2), 1)
    except Exception:  # noqa: BLE001
        log_mb = None
    checks["log_dir_size_mb"] = {
        "value": log_mb,
        "threshold_warn": 500,
        "threshold_critical": 2000,
        "status": _health_status_for(log_mb, warn_at=500, crit_at=2000),
    }

    # Orphan sandboxes (share scanner with /system/orphans endpoint)
    try:
        orphans_info = scan_orphan_sandboxes()
        orphan_count = len(orphans_info.get("orphans", []))
    except Exception:  # noqa: BLE001
        orphan_count = None
    checks["orphan_sandboxes"] = {
        "value": orphan_count,
        "threshold_warn": 3,
        "threshold_critical": 10,
        "status": _health_status_for(orphan_count, warn_at=3, crit_at=10),
    }

    # Autosave scheduler alive (null when no active session)
    autosave_alive: bool | None = None
    try:
        store = get_session_store()
        session = store.get()
        if session is not None:
            sched = session.autosave_scheduler
            autosave_alive = (
                bool(sched is not None and getattr(sched, "_task", None) is not None
                     and not sched._task.done())  # noqa: SLF001
            )
    except Exception:  # noqa: BLE001
        pass
    checks["autosave_scheduler"] = {
        "alive": autosave_alive,
        "status": "healthy" if autosave_alive or autosave_alive is None else "warning",
    }

    # Recent error count from diagnostics_store ring buffer
    try:
        errors_view = diagnostics_store.list_errors(limit=500, level="error")
        error_count = errors_view.get("matched", 0)
    except Exception:  # noqa: BLE001
        error_count = None
    checks["diagnostics_errors"] = {
        "value": error_count,
        "threshold_warn": 5,
        "threshold_critical": 20,
        "status": _health_status_for(error_count, warn_at=5, crit_at=20),
    }

    # Aggregate: worst wins
    order = {"healthy": 0, "warning": 1, "critical": 2}
    worst = "healthy"
    for c in checks.values():
        s = c.get("status", "healthy")
        if order.get(s, 0) > order.get(worst, 0):
            worst = s

    from datetime import datetime
    return {
        "status": worst,
        "checks": checks,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/system/orphans")
async def list_orphan_sandboxes() -> dict[str, Any]:
    """List sandbox directories without a corresponding active session.

    Read-only. Called by Diagnostics → Paths every time the Paths
    subpage mounts / the user clicks "Refresh". Per §3A F3 "report,
    don't silently delete" — this endpoint never mutates disk.

    Response shape::

        {
            "orphans": [
                {
                    "session_id": "a1b2c3d4e5f6",
                    "path": "E:/.../sandboxes/a1b2c3d4e5f6",
                    "size_bytes": 1234567,
                    "mtime": "2026-04-21T18:56:51"
                },
                ...
            ],
            "scanned_at": "2026-04-21T18:57:00",
            "total_bytes": 9876543
        }
    """
    from tests.testbench.pipeline.boot_self_check import scan_orphan_sandboxes
    return scan_orphan_sandboxes()


@router.post("/system/orphans/clear_empty")
async def clear_empty_orphan_sandboxes() -> dict[str, Any]:
    """One-shot clear all **empty** (0-byte) orphan sandboxes.

    Same safety tier as the per-item delete below — refuses to touch
    the active session and checks path-traversal — but bulk so users
    don't have to click 23+ times to clear a startup-test accumulation.
    "Empty" = recursive walk finds no files with ``size > 0``; dirs
    with even a single 0-byte file (e.g. ``.gitkeep``) are preserved.

    Returns ``{"cleared": N, "skipped_nonempty": M, "errors": [...]}``.
    """
    import shutil
    from tests.testbench.pipeline.boot_self_check import (
        scan_orphan_sandboxes,
    )

    scan = scan_orphan_sandboxes()
    cleared = 0
    skipped_nonempty = 0
    errors: list[dict[str, str]] = []
    for orphan in scan.get("orphans", []):
        if orphan.get("size_bytes", 0) > 0:
            skipped_nonempty += 1
            continue
        path = Path(orphan.get("path", ""))
        # Defensive: re-check path is inside SANDBOXES_DIR after resolve
        try:
            path.resolve(strict=False).relative_to(
                tb_config.SANDBOXES_DIR.resolve(strict=False),
            )
        except (OSError, ValueError):
            errors.append({
                "session_id": orphan.get("session_id", "?"),
                "message": "path resolves outside sandboxes root",
            })
            continue
        try:
            shutil.rmtree(path, ignore_errors=False)
            cleared += 1
        except OSError as exc:
            errors.append({
                "session_id": orphan.get("session_id", "?"),
                "message": f"rmtree failed: {exc}",
            })
    python_logger().info(
        "clear_empty_orphan_sandboxes: cleared=%d skipped=%d errors=%d",
        cleared, skipped_nonempty, len(errors),
    )
    return {
        "cleared": cleared,
        "skipped_nonempty": skipped_nonempty,
        "errors": errors,
    }


@router.delete("/system/orphans/{session_id}")
async def delete_orphan_sandbox_endpoint(session_id: str) -> dict[str, Any]:
    """Delete one orphan sandbox by session_id.

    Frontend MUST gate this behind an explicit confirm modal listing
    what's about to be removed. The backend refuses if ``session_id``
    matches the active session (would destroy live data) or if the
    resolved path escapes :data:`config.SANDBOXES_DIR`.

    Returns ``{session_id, deleted_bytes, remaining_bytes, fully_removed}``.
    ``fully_removed=false`` means the OS kept a locked handle open (common
    on Windows) — UI should hint "retry after restarting the server".
    """
    from tests.testbench.pipeline.boot_self_check import (
        OrphanSandboxError,
        delete_orphan_sandbox,
    )
    try:
        return delete_orphan_sandbox(session_id)
    except OrphanSandboxError as exc:
        # 404 for "not found" / 409 for "is active" / 400 for path traversal
        status = {
            "OrphanNotFound": 404,
            "OrphanIsActive": 409,
            "OrphanPathTraversal": 400,
        }.get(exc.code, 400)
        raise HTTPException(
            status_code=status,
            detail={"error_type": exc.code, "message": exc.message},
        ) from exc


# ── /docs/{doc_name} ────────────────────────────────────────────────
#
# Expose a small hand-picked set of tester-facing Markdown docs under
# ``DOCS_DIR`` so Settings → About can deep-link to them. We don't mount
# the whole ``docs/`` directory through StaticFiles because most entries
# there are internal (blueprint / progress / agent notes / lessons) and
# irrelevant to testers. The whitelist below is the "public" subset.
#
# Two response shapes, negotiated by ``Accept`` header:
#
#   * ``text/markdown`` — raw source (for curl / fetch-as-text clients).
#   * ``text/html`` (default, i.e. when opened from a browser) — rendered
#     with ``markdown-it-py`` + inline GitHub-ish styles so the About
#     page's [open manual] link shows a readable document instead of
#     dumping raw markdown at the user.
#
# Missing-on-disk whitelist entries return a 404 with a friendly
# explanation rather than a generic 'file not found' — some docs (the
# user manual, the architecture overview) are written across multiple
# commits and may be absent in intermediate states.

#: Public tester-facing docs. Key is the URL segment; value is the
#: filename under :data:`DOCS_DIR`. Kept tiny on purpose — adding a new
#: entry is a deliberate act, not "whoever drops a file into docs/".
_PUBLIC_DOCS: dict[str, str] = {
    "testbench_USER_MANUAL": "testbench_USER_MANUAL.md",
    "testbench_ARCHITECTURE_OVERVIEW": "testbench_ARCHITECTURE_OVERVIEW.md",
    "external_events_guide": "external_events_guide.md",
    "CHANGELOG": "CHANGELOG.md",
}


def _slugify_heading(text: str) -> str:
    """Convert heading text to a GitHub-style URL slug.

    GitHub's algorithm: lower-case, strip non-[\\w\\s\\-], collapse
    whitespace to ``-``, preserve CJK so Chinese headings like "准备
    事项" still get usable anchors (``#准备事项`` is what the docs
    actually link to). We intentionally keep it stable and de-accent-
    free so the same heading always slugs to the same anchor.
    """
    import re as _re
    # Strip HTML tags (rare but possible in inline anchor pre-emphasis)
    text = _re.sub(r"<[^>]+>", "", text)
    # Keep word chars (incl. CJK via Unicode \w), spaces and hyphens;
    # drop punctuation like . , / : ( ) etc. that break anchor lookups.
    text = _re.sub(r"[^\w\s\-]", "", text, flags=_re.UNICODE)
    # Collapse runs of whitespace into a single hyphen.
    text = _re.sub(r"\s+", "-", text.strip())
    return text.lower()


# _PUBLIC_DOCS is declared further up; reuse for link rewriting below.

def _rewrite_internal_doc_links(html: str) -> str:
    """Fix two classes of broken links in the rendered Markdown:

    1. **Cross-doc links with ``.md`` suffix** — e.g. the manual links
       to ``[ARCHITECTURE_OVERVIEW](testbench_ARCHITECTURE_OVERVIEW.md)``
       which the browser resolves relative to the current URL
       (``/docs/testbench_USER_MANUAL`` → ``/docs/testbench_ARCHITECTURE_
       OVERVIEW.md``). The ``.md`` suffix isn't a whitelist key so we
       404. Transparently strip the suffix so authors can keep the
       natural GitHub-style link form in the source.

       Only strips when the bare stem (sans ``.md``) is a whitelisted
       doc name; links to arbitrary external ``.md`` files are left
       alone.

    2. **Links to non-whitelisted internal docs** — e.g. LESSONS_LEARNED,
       PROGRESS, AGENT_NOTES are developer-only docs never exposed via
       ``/docs/`` by design. The manual sometimes references them for
       cross-reading; those references get downgraded to plain ``<span
       class="muted-link">…</span>`` so testers aren't led to a 404.

    Done as a post-render regex sweep rather than a markdown-it plugin
    because the rule is **doc-layer**, not markdown-layer (the author
    writing ``.md`` is grammatically correct, it's our endpoint routing
    that can't handle it).
    """
    import re as _re

    whitelist_stems = set(_PUBLIC_DOCS.keys())

    # Candidate internal doc filenames the manual / arch doc reference
    # but that are intentionally NOT whitelisted. These become plain
    # text so readers don't get a "unknown_doc" 404 when they click.
    internal_only_docs = {
        "LESSONS_LEARNED.md",
        "LESSONS_LEARNED",
        "PROGRESS.md",
        "PROGRESS",
        "AGENT_NOTES.md",
        "AGENT_NOTES",
        "PLAN.md",
        "PLAN",
        "P24_BLUEPRINT.md",
        "P25_BLUEPRINT.md",
        "P26_BLUEPRINT.md",
    }

    def _rewrite(match: _re.Match) -> str:
        prefix = match.group(1)  # ``<a href="``
        href = match.group(2)
        suffix = match.group(3)  # closing quote + any other attrs before >
        body = match.group(4)    # link text
        closing = match.group(5)  # ``</a>``

        # External / anchor-only / already-rooted links: leave alone.
        if (
            href.startswith(("http://", "https://", "mailto:", "/", "#"))
            or not href
        ):
            return match.group(0)

        # Split off ``#anchor`` suffix if present (preserved through).
        bare, sep, anchor = href.partition("#")

        # Rule 2: downgrade links to internal-only docs.
        if bare in internal_only_docs:
            return f'<span class="muted-link">{body}</span>'

        # Rule 1: strip ``.md`` if the stem is whitelisted.
        if bare.endswith(".md"):
            stem = bare[:-3]
            if stem in whitelist_stems:
                new_href = stem + (sep + anchor if anchor else "")
                return f"{prefix}{new_href}{suffix}{body}{closing}"

        # Fallback: leave untouched (could be a link to another file
        # in the same folder — not our concern at this layer).
        return match.group(0)

    return _re.sub(
        r"(<a\s+[^>]*href=\")([^\"]*)(\"[^>]*>)(.*?)(</a>)",
        _rewrite,
        html,
        flags=_re.DOTALL,
    )


def _render_markdown_html(md_source: str, title: str) -> str:
    """Render Markdown to a standalone HTML page with embedded styles.

    Using ``markdown-it-py`` (already a transitive dep via ``markdownify``)
    with ``commonmark`` + ``table`` + ``strikethrough`` so GitHub-flavored
    tables / task-lists render correctly. The embedded CSS is deliberately
    tiny (prose readability only, no interactive controls) — testers open
    the doc, read, close; we're not building a wiki app here.

    Two post-processing passes are applied to the rendered HTML:

    1. **Heading anchor ids** — every ``<h1>`` … ``<h6>`` gets an
       ``id`` derived from its text (GitHub-style slug). This makes
       the in-doc TOC links ``[§1.1](#11-testbench-...)`` actually
       jump.
    2. **Broken-link rewrite** — see :func:`_rewrite_internal_doc_links`.

    Defensive on import failure: if the lib can't load for some reason we
    fall back to ``<pre>`` of the raw source so the user still sees the
    content, just unstyled.
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        body_html = (
            "<pre style='white-space: pre-wrap; word-break: break-word;"
            "font-family: ui-monospace, monospace; font-size: 13.5px;'>"
            f"{_html_escape(md_source)}</pre>"
        )
    else:
        md = (
            MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": False})
            .enable(["table", "strikethrough"])
        )
        # Heading anchor ids: walk the token stream and stamp the
        # opening h-tag with an ``id`` attr computed from the following
        # inline text token. Done at token level so it survives any
        # later render customisation (vs a post-render regex that can
        # get confused by headings containing ``<code>`` etc.).
        tokens = md.parse(md_source)
        for i, tok in enumerate(tokens):
            if tok.type == "heading_open" and i + 1 < len(tokens):
                inline = tokens[i + 1]
                if inline.type == "inline" and inline.content:
                    slug = _slugify_heading(inline.content)
                    if slug:
                        tok.attrSet("id", slug)
        body_html = md.renderer.render(tokens, md.options, {})
        body_html = _rewrite_internal_doc_links(body_html)

    # Inline CSS — kept minimal. Layout width ~820px to match the prose
    # width tolerance recommended by most typography guides; anything
    # wider hurts readability on 1080p+ monitors.
    return (
        "<!doctype html><html lang='zh-CN'><head>"
        "<meta charset='utf-8'>"
        f"<title>{_html_escape(title)}</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>"
        "body{margin:0;padding:32px 24px;background:#f6f8fa;color:#1f2328;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "Helvetica,Arial,sans-serif,'PingFang SC','Microsoft YaHei';"
        "line-height:1.7;font-size:15px;}"
        "main{max-width:820px;margin:0 auto;background:#fff;padding:40px 48px;"
        "border:1px solid #d1d9e0;border-radius:8px;"
        "box-shadow:0 1px 3px rgba(31,35,40,0.04);}"
        "h1,h2,h3,h4{line-height:1.3;margin-top:1.6em;margin-bottom:0.6em;"
        "font-weight:600;color:#1f2328;}"
        "h1{font-size:2em;border-bottom:1px solid #d1d9e0;padding-bottom:0.3em;}"
        "h2{font-size:1.5em;border-bottom:1px solid #eaeef2;padding-bottom:0.3em;}"
        "h3{font-size:1.25em;}"
        "p{margin:0 0 1em 0;}"
        "ul,ol{padding-left:2em;margin:0 0 1em 0;}"
        "li{margin:0.2em 0;}"
        "code{background:#eff1f3;padding:0.2em 0.4em;border-radius:4px;"
        "font-family:ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace;"
        "font-size:85%;}"
        "pre{background:#f6f8fa;padding:16px;border-radius:6px;overflow:auto;"
        "line-height:1.45;font-size:85%;}"
        "pre code{background:transparent;padding:0;font-size:inherit;}"
        # Tables: force layout:fixed + word-break so long CJK/path/code
        # cells wrap instead of pushing the table off-screen. Wrap
        # the ``<table>`` in an overflow-x auto container via display
        # so super-wide tables (6+ cols) get a scrollbar rather than
        # overflowing the main card.
        "table{border-collapse:collapse;margin:1em 0;width:100%;"
        "table-layout:auto;display:block;overflow-x:auto;max-width:100%;}"
        "table th,table td{border:1px solid #d1d9e0;padding:6px 13px;"
        "word-break:break-word;overflow-wrap:anywhere;vertical-align:top;}"
        "table th{background:#f6f8fa;font-weight:600;}"
        "table code{white-space:normal;word-break:break-all;}"
        # Smooth-scroll + a bit of top padding so clicking an anchor
        # doesn't hide the target heading behind the page top.
        "html{scroll-behavior:smooth;}"
        ":target{scroll-margin-top:16px;background:#fff8c5;"
        "transition:background 1.2s ease-out;}"
        # Muted link = downgraded link to an internal-only doc. Keeps
        # the text visible but signals it's not clickable so the
        # tester doesn't keep trying to click it.
        ".muted-link{color:#636c76;border-bottom:1px dotted #afb8c1;"
        "cursor:help;}"
        "blockquote{border-left:0.25em solid #d1d9e0;padding:0 1em;color:#636c76;"
        "margin:0 0 1em 0;}"
        "hr{height:1px;border:0;background:#d1d9e0;margin:2em 0;}"
        "a{color:#0969da;text-decoration:none;}a:hover{text-decoration:underline;}"
        ".docs-backlink{display:inline-block;margin-bottom:24px;font-size:13px;"
        "color:#636c76;}"
        # Images: fit container width (screenshot source is up to 2560
        # wide, container inner ~724px), preserve aspect ratio. Narrow
        # images (e.g. small menu screenshots around 340–600 px wide)
        # render at their native size because max-width only caps the
        # upper bound. Tall portrait shots are readable at container
        # width; the lightbox covers "I need to see details" cases.
        "main img{display:block;max-width:100%;height:auto;"
        "margin:1em auto;border:1px solid #d1d9e0;"
        "border-radius:6px;cursor:zoom-in;"
        "background:#f6f8fa;}"
        # Lightbox overlay — hidden until JS toggles .open. Click anywhere
        # to dismiss (the <img> inside also cursor:zoom-out). Keeping the
        # overlay layout in pure CSS so the no-JS fallback at least shows
        # normal-size images; click just won't do anything without JS.
        "#docs-lightbox{position:fixed;inset:0;background:rgba(13,17,23,0.88);"
        "display:none;align-items:center;justify-content:center;"
        "z-index:9999;cursor:zoom-out;padding:24px;}"
        "#docs-lightbox.open{display:flex;}"
        "#docs-lightbox img{max-width:100%;max-height:100%;"
        "box-shadow:0 8px 32px rgba(0,0,0,0.4);border-radius:4px;}"
        "</style>"
        "</head><body><main>"
        "<a class='docs-backlink' href='/'>← 回到 testbench 主页</a>"
        f"{body_html}"
        "</main>"
        # Lightbox: single overlay reused for any clicked image. The JS
        # below attaches one delegated click handler on <main>; we don't
        # need per-image listeners. Esc / click-outside dismisses.
        "<div id='docs-lightbox' role='dialog' aria-hidden='true'>"
        "<img alt='' />"
        "</div>"
        "<script>"
        "(function(){"
        "var box=document.getElementById('docs-lightbox');"
        "var boxImg=box.querySelector('img');"
        "function open(src,alt){boxImg.src=src;boxImg.alt=alt||'';"
        "box.classList.add('open');box.setAttribute('aria-hidden','false');}"
        "function close(){box.classList.remove('open');"
        "box.setAttribute('aria-hidden','true');boxImg.src='';}"
        "document.querySelector('main').addEventListener('click',function(e){"
        "var t=e.target;if(t&&t.tagName==='IMG'&&t.closest('main')){"
        "e.preventDefault();open(t.currentSrc||t.src,t.alt);}});"
        "box.addEventListener('click',close);"
        "document.addEventListener('keydown',function(e){"
        "if(e.key==='Escape'&&box.classList.contains('open'))close();});"
        "})();"
        "</script>"
        "</body></html>"
    )


def _html_escape(text: str) -> str:
    """Minimal HTML escape for the fallback ``<pre>`` path.

    ``html.escape`` would work too, but importing it lazily here keeps
    the top-of-file import block focused on what's always-used. This
    helper is only hit when ``markdown-it-py`` fails to import, which
    should never happen in a healthy venv.
    """
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Allowed image extensions for docs screenshots. Kept to a short
# whitelist because ``/docs/images/`` is served off-disk from
# ``DOCS_DIR/images`` and we don't want to accidentally hand out
# arbitrary binary payloads if someone drops a stray ``foo.exe`` in
# that folder.
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

_IMAGE_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@router.get("/docs/images/{filename}")
async def serve_doc_image(filename: str):  # noqa: ANN201
    """Serve a screenshot referenced from a whitelisted markdown doc.

    The markdown docs (e.g. USER_MANUAL) embed images via relative
    paths ``![caption](images/09_eval_workflow.png)``. When the doc
    is rendered at ``/docs/testbench_USER_MANUAL``, the browser
    resolves those as ``/docs/images/09_eval_workflow.png``; this
    endpoint serves them off-disk from ``DOCS_DIR / images``.

    Security:

      * filename must be a bare basename (no ``/`` ``\\`` ``..``) —
        we reject anything else up front to kill path-traversal.
      * extension must be in :data:`_ALLOWED_IMAGE_EXTS` — the docs
        folder is developer-writable, but we still refuse to hand
        out e.g. ``.py`` / ``.exe`` / ``.zip`` over this endpoint.

    Returns 404 with ``error_type='image_missing'`` if the file
    isn't on disk; this mirrors the dual-semantics pattern in
    :func:`serve_public_doc`.
    """
    # Reject anything that looks like a traversal attempt or nested
    # path. We intentionally don't support subfolders under
    # ``docs/images/`` — flat layout is a testbench convention.
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or filename.startswith(".")
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "bad_filename",
                "message": f"image filename {filename!r} contains illegal characters.",
            },
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "bad_extension",
                "message": (
                    f"extension {suffix!r} not allowed. Allowed: "
                    f"{sorted(_ALLOWED_IMAGE_EXTS)}."
                ),
            },
        )

    images_dir = tb_config.DOCS_DIR / "images"
    path = images_dir / filename
    # Resolve + boundary check defends against corner cases where
    # the basename-only check above would let through (e.g. Windows
    # short names). After resolve, the parent must equal images_dir.
    try:
        resolved = path.resolve()
        if resolved.parent != images_dir.resolve():
            raise HTTPException(
                status_code=400,
                detail={
                    "error_type": "bad_filename",
                    "message": "resolved image path escapes docs/images/.",
                },
            )
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"failed to resolve image path: {exc}",
        ) from exc

    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "image_missing",
                "message": (
                    f"image {filename!r} is not on disk under "
                    f"{images_dir}. If the doc still references it, "
                    f"either commit the screenshot or remove the link."
                ),
            },
        )

    return FileResponse(
        path=resolved,
        media_type=_IMAGE_CONTENT_TYPE.get(suffix, "application/octet-stream"),
    )


@router.get("/docs/{doc_name}")
async def serve_public_doc(doc_name: str, request: Request):  # noqa: ANN201
    """Serve one of the whitelisted tester-facing Markdown docs.

    Accept header negotiation:

      * ``text/markdown`` in ``Accept`` → return raw markdown source
        with ``text/markdown; charset=utf-8`` content type.
      * Otherwise → render to a standalone HTML page (GitHub-ish styles)
        so the Settings → About page's "open manual" link just opens
        a readable document in a new tab.

    Errors:

      * 404 ``unknown_doc`` — ``doc_name`` not in the whitelist.
      * 404 ``file_missing`` — whitelist entry exists but the file
        hasn't been authored yet (e.g. we're between commits where
        the user manual is scheduled but not written).
    """
    filename = _PUBLIC_DOCS.get(doc_name)
    if filename is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "unknown_doc",
                "message": (
                    f"doc_name={doc_name!r} is not in the public docs "
                    f"whitelist. Known: {sorted(_PUBLIC_DOCS)}"
                ),
            },
        )

    path = tb_config.DOCS_DIR / filename
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "file_missing",
                "message": (
                    f"doc {doc_name!r} is on the whitelist but its file "
                    f"({filename}) hasn't been written yet. It will appear "
                    "in a subsequent commit of this release cycle."
                ),
            },
        )

    try:
        md_source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"failed to read {filename}: {exc}",
        ) from exc

    accept = request.headers.get("accept", "").lower()
    if "text/markdown" in accept:
        return PlainTextResponse(
            content=md_source,
            media_type="text/markdown; charset=utf-8",
        )

    html_body = _render_markdown_html(md_source, title=doc_name)
    return HTMLResponse(content=html_body)
