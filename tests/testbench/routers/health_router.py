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

from fastapi import APIRouter, HTTPException
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
    """Static version metadata for the testbench UI."""
    return {
        "name": "N.E.K.O. Testbench",
        "version": "0.1.0",
        "phase": "P20",
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
    """Body for ``POST /system/open_path``."""

    path: str = Field(
        ...,
        description="Absolute path to open. Must be inside testbench_data/",
    )


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

    Returns ``{"ok": true, "path": ...}`` on success. Raises:

      * 400 if ``body.path`` is empty or ``Path()`` parsing fails.
      * 403 if the resolved path is outside ``testbench_data/``.
      * 404 if the path doesn't exist on disk (opening a ghost path
        would silently spawn an empty Explorer window on Windows and
        an error dialog on macOS/Linux; surface the mistake early).
      * 500 if the OS dispatcher itself raises (missing xdg-open /
        Explorer crashed / etc.).
    """
    raw = (body.path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path must be non-empty")

    try:
        target = Path(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid path syntax: {exc}",
        ) from exc

    if not _path_is_inside_data_dir(target):
        python_logger().warning(
            "system_open_path: rejecting path %r outside DATA_DIR", raw,
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
