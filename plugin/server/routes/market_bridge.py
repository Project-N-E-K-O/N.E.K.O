"""Market Bridge — 本地客户端与插件市场的双向联动协议。

提供以下能力：
1. Market 前端探测本地客户端状态
2. Market 前端触发插件安装（从 URL 下载 → 校验 → 安装）
3. 查询本地已安装插件列表（供 Market 标记已安装状态）
4. 安装任务进度查询
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import secrets
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from plugin.logging_config import get_logger
from plugin.server.application.install_source import (
    InstallSourceError,
    InstallSourceManager,
    LockEntry,
    SourceDetailMarket,
    get_install_source_manager,
)
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.settings import MARKET_URL, USER_PLUGIN_CONFIG_ROOT

router = APIRouter(prefix="/market", tags=["market-bridge"])
logger = get_logger("server.routes.market_bridge")

_cli_service = PluginCliService()

# ─── Bridge Token（本地安全令牌）───────────────────────────────────
# 每次服务启动时生成，防止恶意网页未经授权调用本地 API。
# Market 前端需要通过 neko:// 协议或用户手动配对获取此 token。
_BRIDGE_TOKEN: str = secrets.token_urlsafe(32)
_BRIDGE_TOKEN_FILE: Path | None = None

# 安装任务存储（内存，重启清空）
_tasks: dict[str, dict[str, Any]] = {}

# 下载限制
_DOWNLOAD_MAX_BYTES = 200 * 1024 * 1024  # 200 MB
_DOWNLOAD_TIMEOUT = 120.0  # 秒
_ALLOWED_SUFFIXES = frozenset({".neko-plugin", ".neko-bundle"})


def get_bridge_token() -> str:
    """获取当前 bridge token（供 URI scheme handler 使用）。"""
    return _BRIDGE_TOKEN


def write_bridge_token_file(directory: Path) -> Path:
    """将 bridge token 写入文件，供外部进程读取。"""
    global _BRIDGE_TOKEN_FILE
    directory.mkdir(parents=True, exist_ok=True)
    token_file = directory / "bridge.json"
    import json
    token_file.write_text(
        json.dumps({"token": _BRIDGE_TOKEN, "port": 48911}, indent=2),
        encoding="utf-8",
    )
    _BRIDGE_TOKEN_FILE = token_file
    logger.info("Bridge token written to {}", token_file)
    return token_file


# ─── 请求/响应模型 ─────────────────────────────────────────────────


class MarketStatusResponse(BaseModel):
    online: bool = True
    version: str = "0.1.0"
    protocol_version: int = 1
    client_name: str = "N.E.K.O Plugin Server"
    installed_count: int = 0
    token_required: bool = True
    market_url: str = ""


class MarketInstallRequest(BaseModel):
    """从 Market 触发安装的请求。

    v2 (design §3.4.1) 在原有字段之上新增 ``mode`` / ``channel`` /
    ``published_at``，让客户端区分 install / upgrade / reinstall 三种
    语义并把 Market 已知的发布证据透传到 lock entry 上。
    """
    package_url: str = Field(..., description="插件包下载 URL")
    package_sha256: str | None = Field(
        default=None,
        description="包文件 SHA256（可选）。为空或全 0 时跳过校验（仅用于 Market 尚未生成 hash 的场景）。",
    )
    payload_hash: str | None = Field(None, description="可选的 payload hash 二次校验")
    plugin_id: str | None = Field(None, description="Market 侧的插件标识")
    version: str | None = Field(None, description="版本号")
    # v2: stable / beta channel 透传给客户端，让 lock entry 携带完整证据
    channel: str | None = Field(
        default=None,
        description="Market 上 latest_version.channel；None 时按 'stable' 处理",
    )
    published_at: str | None = Field(
        default=None,
        description="Market 上 latest_version.created_at；None 时由客户端兜底为当前时间",
    )
    # v2: install / upgrade / reinstall mode 选择；旧客户端不传 mode 则默认 install
    mode: Literal["install", "upgrade", "reinstall"] = Field(
        default="install",
        description="install=全新安装；upgrade=覆盖旧版本；reinstall=同版本重装",
    )
    # v2 (Option C): plugin 身份一致性校验 —— Market slug 透传给客户端，
    # 客户端 unpack 后比对包内 plugin.toml [plugin].id；不一致时附 warning。
    expected_plugin_toml_id: str | None = Field(
        default=None,
        description=(
            "Market 上的 plugin.slug；客户端 unpack 后会和包内 plugin.toml "
            "的 id 字段比对。不一致只 warn 不阻塞，给用户审视空间"
        ),
    )
    on_conflict: str = Field(default="rename", pattern=r"^(rename|fail)$")
    require_confirm: bool = Field(default=True, description="是否需要用户确认（预留）")


class MarketInstallResponse(BaseModel):
    task_id: str
    status: str  # "pending" | "downloading" | "installing" | "completed" | "failed"
    message: str = ""


class MarketTaskStatus(BaseModel):
    task_id: str
    status: str
    progress: float = 0.0  # 0.0 ~ 1.0
    message: str = ""
    result: dict[str, Any] | None = None
    # v2 (R10.1 / R10.2): error 字段保留 message 以便旧前端展示；新增 error_code
    # 让前端识别稳定错误码（upgrade_rollback_completed / version_already_at_target / ...）。
    error: str | None = None
    error_code: str | None = None
    created_at: float = 0.0
    completed_at: float | None = None
    install_source_warning: str | None = None


class MarketInstalledPlugin(BaseModel):
    plugin_id: str
    path: str
    # v2 (R6.1 / R6.6 / design §3.5): 让前端在不二次请求的前提下展示 yank /
    # channel / 版本对比信息。仅 channel="market" 的 entry 投影；非 market /
    # 没有 lock entry 时为 None。
    latest_install_source: dict[str, Any] | None = None


class MarketInstalledResponse(BaseModel):
    installed: list[MarketInstalledPlugin]
    count: int


class MarketTokenExchangeRequest(BaseModel):
    """用于 neko:// 回调后交换 token 的请求。"""
    one_time_code: str


class MarketTokenExchangeResponse(BaseModel):
    bridge_token: str
    expires_in: int | None = None  # None = 不过期（直到重启）


class MarketBridgeTokenResponse(BaseModel):
    """供同源前端（plugin-manager UI）直接获取 bridge token。"""
    bridge_token: str
    port: int = 48911


# ─── 端点 ──────────────────────────────────────────────────────────


@router.get("/status", response_model=MarketStatusResponse)
async def market_status():
    """探测本地客户端是否在线。

    此端点不需要 token，供 Market 前端快速探测。
    返回 market_url 供前端知道 Market 地址。
    """
    try:
        plugins_result = await _cli_service.list_local_plugins()
        count = plugins_result.get("count", 0)
    except Exception:
        count = 0

    return MarketStatusResponse(installed_count=count, market_url=MARKET_URL)


@router.post("/install", response_model=MarketInstallResponse)
async def market_install(
    payload: MarketInstallRequest,
    token: str = Query(..., description="Bridge token"),
):
    """从 Market 触发插件安装。

    流程：下载包 → 校验 SHA256 → 调用 install_package → 返回任务 ID。
    安装是异步的，前端通过 /market/tasks/{task_id} 轮询进度。

    v2 (design §3.4.2): mode 字段决定走 install / upgrade / reinstall 三条
    分支；upgrade / reinstall 在 bridge 内部协调 lifecycle stop → rename
    旧目录 → unpack → record → start，失败时按 rollback steps 逆序回滚。
    """
    _verify_token(token)

    # mode=upgrade 立即校验 lock entry 存在性（R5.5）；reinstall 同样需要
    # 已装才能"重装"，install 不要求。
    if payload.mode in ("upgrade", "reinstall"):
        mgr = get_install_source_manager()
        if mgr is None or mgr.find_active_market_entry(payload.plugin_id or "") is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "plugin_not_installed_for_upgrade",
                    "message": (
                        f"plugin {payload.plugin_id!r} has no active market lock "
                        "entry; cannot upgrade / reinstall"
                    ),
                },
            )

    task_id = secrets.token_urlsafe(16)
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0.0,
        "message": "任务已创建",
        "result": None,
        "error": None,
        "error_code": None,
        "created_at": time.time(),
        "completed_at": None,
    }

    # 异步执行安装
    asyncio.create_task(
        _execute_install(task_id, payload),
        name=f"market-install-{task_id}",
    )

    return MarketInstallResponse(
        task_id=task_id,
        status="pending",
        message="安装任务已创建，正在下载包...",
    )


@router.get("/tasks/{task_id}", response_model=MarketTaskStatus)
async def market_task_status(
    task_id: str,
    token: str = Query(..., description="Bridge token"),
):
    """查询安装任务进度。"""
    _verify_token(token)

    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return MarketTaskStatus(**task)


@router.get("/installed", response_model=MarketInstalledResponse)
async def market_installed(
    token: str = Query(..., description="Bridge token"),
):
    """查询本地已安装的插件列表。

    v2 (design §3.5): 把 lock 上 ``channel="market"`` 的 entry 投影成
    ``latest_install_source`` 一并返回，前端不再需要二次请求即可拿到
    版本号 / channel / sha256 / payload_hash 用于 upgrade 与 yank 判定。
    """
    _verify_token(token)

    try:
        result = await _cli_service.list_local_plugins()
        plugins = result.get("plugins", [])
        # 一次性拿全量 lock 索引
        mgr = get_install_source_manager()
        snapshot = mgr.snapshot() if mgr is not None else None
        entries_by_pid: dict[str, LockEntry] = {}
        if snapshot is not None:
            entries_by_pid = {
                e.plugin_id: e
                for e in snapshot.entries
                if not e.removed and e.plugin_id
            }

        from plugin.settings import PLUGIN_CONFIG_ROOTS
        installed: list[MarketInstalledPlugin] = []
        for plugin_id in plugins:
            # 查找插件实际路径
            plugin_dir: Path | None = None
            for root in PLUGIN_CONFIG_ROOTS:
                candidate = root / plugin_id
                if candidate.is_dir():
                    plugin_dir = candidate
                    break
            if plugin_dir is None:
                continue
            installed.append(MarketInstalledPlugin(
                plugin_id=plugin_id,
                path=str(plugin_dir),
                latest_install_source=_project_market_source_detail(
                    entries_by_pid.get(plugin_id)
                ),
            ))
        return MarketInstalledResponse(installed=installed, count=len(installed))
    except Exception as exc:
        logger.warning("Failed to list installed plugins: {}", exc)
        return MarketInstalledResponse(installed=[], count=0)


def _project_market_source_detail(
    entry: LockEntry | None,
) -> dict[str, Any] | None:
    """Project a LockEntry's market source_detail to the API view (design §3.5).

    Returns None for entries that are missing, soft-removed, non-market,
    or carry a non-market source_detail (defensive — should not happen
    after parser validation but keeps the projection total).
    """

    if entry is None or entry.removed or entry.channel != "market":
        return None
    detail = entry.source_detail
    if not isinstance(detail, SourceDetailMarket):
        return None
    return {
        "channel": detail.channel,
        "version": detail.version,
        "package_sha256": detail.package_sha256,
        "payload_hash": detail.payload_hash,
        "package_url": detail.package_url,
        "published_at": detail.published_at,
    }


@router.post("/token-exchange", response_model=MarketTokenExchangeResponse)
async def market_token_exchange(payload: MarketTokenExchangeRequest):
    """通过一次性码交换 bridge token。

    流程：
    1. N.E.K.O 客户端生成 one-time code 并通过 neko:// URI 传给浏览器
    2. Market 前端用此 code 调用本端点换取 bridge_token
    3. 后续请求使用 bridge_token

    注意：此端点本身不需要 token（因为是用来获取 token 的）。
    """
    # 简化实现：one-time code 就是 bridge token 的前 8 位
    # 生产环境应该用独立的 OTP 存储
    if not secrets.compare_digest(payload.one_time_code, _BRIDGE_TOKEN[:8]):
        raise HTTPException(status_code=403, detail="无效的一次性码")

    return MarketTokenExchangeResponse(bridge_token=_BRIDGE_TOKEN)


@router.get("/bridge-token", response_model=MarketBridgeTokenResponse)
async def market_bridge_token(request: Request):
    """供同源前端（plugin-manager UI）获取 bridge token。

    plugin-manager UI 由同一个 FastAPI 进程托管，跟 /market/* 同源，所以
    不需要走 one-time code 配对。只允许 127.0.0.1 / localhost 来源，避免
    被外部网页拿到 token。
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="仅允许本地同源访问")

    return MarketBridgeTokenResponse(bridge_token=_BRIDGE_TOKEN, port=48911)


# ─── 内部实现 ──────────────────────────────────────────────────────


def _verify_token(token: str) -> None:
    """验证 bridge token。"""
    if not secrets.compare_digest(token, _BRIDGE_TOKEN):
        raise HTTPException(status_code=403, detail="无效的 bridge token")


async def _execute_install(task_id: str, payload: MarketInstallRequest) -> None:
    """异步执行下载 + 校验 + 安装 / 升级流程（design §3.4）。

    根据 ``payload.mode`` 走 ``_do_install`` / ``_do_upgrade`` 之一；后者
    再细分为 ``upgrade`` (版本号必须前进) / ``reinstall`` (允许相同版本号)。
    所有结构化错误都收敛到 :class:`_TaskError`，最终落到 task dict 的
    ``error_code`` 字段供前端识别。
    """

    task = _tasks[task_id]
    started_at = time.monotonic()
    log_ctx: dict[str, Any] = {
        "task_id": task_id,
        "mode": payload.mode,
        "plugin_id": payload.plugin_id or "",
        "version": payload.version or "",
        "package_sha256_check": "skipped",
    }

    try:
        if payload.mode == "install":
            await _do_install(task, payload, log_ctx)
        elif payload.mode == "upgrade":
            await _do_upgrade(task, payload, log_ctx)
        elif payload.mode == "reinstall":
            await _do_upgrade(task, payload, log_ctx, allow_same_version=True)
        else:  # pragma: no cover — Pydantic Literal already enforces this
            raise _TaskError(
                code="invalid_mode",
                message=f"unknown mode: {payload.mode}",
            )
        _finalize_task_success(task, started_at, log_ctx)
    except _TaskError as exc:
        _finalize_task_failure(task, exc, started_at, log_ctx)
    except Exception as exc:
        logger.exception(
            "Market install task {} hit unexpected error: {}",
            task_id,
            exc,
        )
        _finalize_task_failure(
            task,
            _TaskError(code="internal_error", message=str(exc)),
            started_at,
            log_ctx,
        )


# ─── Task error / finalisers ─────────────────────────────────────────


@dataclasses.dataclass
class _TaskError(Exception):
    """Bridge-internal structured error.

    Carries a stable ``code`` so the front-end can reliably switch on
    error type (R10.1) plus a human-readable ``message`` to surface in
    Chinese UI. ``http_status`` is currently unused but kept for the
    rare case where a synchronous endpoint wants to translate the same
    error to an HTTP response.
    """

    code: str
    message: str
    http_status: int | None = None

    def __post_init__(self) -> None:
        super().__init__(self.code, self.message)


def _finalize_task_success(
    task: dict[str, Any],
    started_at: float,
    log_ctx: dict[str, Any],
) -> None:
    """Mark task completed and emit one structured info log line."""

    duration_ms = int((time.monotonic() - started_at) * 1000)
    task["status"] = "completed"
    task["progress"] = 1.0
    task["completed_at"] = time.time()
    if not task.get("message"):
        task["message"] = "完成"
    logger.info(
        "market_install_task outcome=success task_id={} mode={} plugin_id={} "
        "version={} duration_ms={} package_sha256_check={}",
        log_ctx.get("task_id", ""),
        log_ctx.get("mode", ""),
        log_ctx.get("plugin_id", ""),
        log_ctx.get("version", ""),
        duration_ms,
        log_ctx.get("package_sha256_check", "skipped"),
    )


def _finalize_task_failure(
    task: dict[str, Any],
    err: _TaskError,
    started_at: float,
    log_ctx: dict[str, Any],
) -> None:
    """Mark task failed and emit one structured error log line."""

    duration_ms = int((time.monotonic() - started_at) * 1000)
    task["status"] = "failed"
    task["progress"] = task.get("progress", 0.0)
    task["error"] = err.message
    task["error_code"] = err.code
    task["completed_at"] = time.time()
    task["message"] = _human_message_for(err.code) or err.message
    logger.error(
        "market_install_task outcome=failed task_id={} mode={} plugin_id={} "
        "version={} duration_ms={} error_code={} package_sha256_check={} message={}",
        log_ctx.get("task_id", ""),
        log_ctx.get("mode", ""),
        log_ctx.get("plugin_id", ""),
        log_ctx.get("version", ""),
        duration_ms,
        err.code,
        log_ctx.get("package_sha256_check", "skipped"),
        err.message,
    )


_HUMAN_MESSAGES: dict[str, str] = {
    "upgrade_rollback_completed": "升级失败，已回滚到旧版本",
    "plugin_not_installed_for_upgrade": "该插件未安装，无法升级",
    "version_already_at_target": "当前已是目标版本",
    "lock_write_failed": "安装记录写入失败",
    "market_list_fetch_failed": "无法连接到 Market",
}


def _human_message_for(code: str) -> str:
    return _HUMAN_MESSAGES.get(code, "")


# ─── install / upgrade flows ─────────────────────────────────────────


async def _do_install(
    task: dict[str, Any],
    payload: MarketInstallRequest,
    log_ctx: dict[str, Any],
) -> None:
    """Install a fresh market plugin (mode=install).

    Reuses the original download → verify → ``upload_and_install`` path
    but threads the v2 fields (``channel`` / ``published_at``) through
    to the lock record.
    """

    task["status"] = "downloading"
    task["progress"] = 0.1
    task["message"] = f"正在下载: {payload.package_url}"

    content = await _download_package(payload.package_url, task)

    sha_check = _verify_sha256(content, payload.package_sha256, task)
    log_ctx["package_sha256_check"] = sha_check

    task["status"] = "installing"
    task["progress"] = 0.8
    task["message"] = "正在安装插件..."

    filename = _extract_filename(payload.package_url)
    market_override = _build_market_override(payload, content, mode="install")

    try:
        result = await _cli_service.upload_and_install(
            filename=filename,
            content=content,
            on_conflict=payload.on_conflict,
            install_source_override=market_override,
        )
    except InstallSourceError as exc:
        if exc.code == "lock_write_failed":
            raise _TaskError(code="lock_write_failed", message=str(exc.message)) from exc
        raise _TaskError(code="internal_error", message=str(exc.message)) from exc

    _post_install_payload_check(payload, result)

    task["progress"] = 1.0
    task["message"] = "安装成功"
    task["result"] = result

    if isinstance(result, dict) and "install_source_warning" in result:
        task["install_source_warning"] = result["install_source_warning"]


async def _do_upgrade(
    task: dict[str, Any],
    payload: MarketInstallRequest,
    log_ctx: dict[str, Any],
    *,
    allow_same_version: bool = False,
) -> None:
    """Upgrade an installed market plugin (design §3.4.3).

    Steps (numbered to match design):
      1. find active market entry; reject if missing
      2. compare versions; reject if equal (unless reinstall)
      3. lifecycle stop (if running) — currently a no-op stub since the
         plugin loader does not expose a stable stop/start API at this
         layer. We keep the hook so downstream wiring can implement it
         without touching this control flow.
      4. rename existing dir → ``<dir>.bak.<utc_micro_ts>``
      5. download + verify sha256
      6. unpack to original directory + record_market_upgrade
      7. lifecycle start (if was running)
      8. async cleanup of backup dir
    """

    plugin_id = payload.plugin_id or ""
    target_version = payload.version or ""

    # Step 1: probe active lock entry.
    mgr = get_install_source_manager()
    if mgr is None:
        raise _TaskError(
            code="plugin_not_installed_for_upgrade",
            message="install source manager not initialised",
        )

    entry = mgr.find_active_market_entry(plugin_id)
    if entry is None:
        raise _TaskError(
            code="plugin_not_installed_for_upgrade",
            message=f"plugin {plugin_id!r} has no active market lock entry",
            http_status=400,
        )

    # Step 2: version-equality short-circuit (skipped for reinstall).
    current_version = ""
    if isinstance(entry.source_detail, SourceDetailMarket):
        current_version = entry.source_detail.version
    if not allow_same_version and current_version == target_version:
        raise _TaskError(
            code="version_already_at_target",
            message=(
                f"plugin {plugin_id!r} is already at version {target_version!r}"
            ),
        )

    plugin_dir = (USER_PLUGIN_CONFIG_ROOT / entry.directory_name).resolve()
    backup_dir = plugin_dir.with_name(
        f"{entry.directory_name}.bak.{_utc_micro_ts()}"
    )
    rollback_steps: list[Callable[[], Awaitable[None]]] = []
    was_running = await _safely_is_running(plugin_id)

    # Step 3: lifecycle stop.
    if was_running:
        task["message"] = "正在停止旧版本插件..."
        await _safely_stop(plugin_id)

    # Step 4: rename old dir → backup.
    try:
        await asyncio.to_thread(os.rename, plugin_dir, backup_dir)
    except OSError as exc:
        if was_running:
            await _safely_start(plugin_id)
        raise _TaskError(
            code="upgrade_rollback_completed",
            message=f"无法备份旧目录: {exc}",
        ) from exc
    rollback_steps.append(_make_restore_dir_step(backup_dir, plugin_dir))

    try:
        # Step 5: download + verify sha256.
        task["status"] = "downloading"
        task["progress"] = 0.1
        task["message"] = "正在下载新版本..."
        content = await _download_package(payload.package_url, task)
        sha_check = _verify_sha256(content, payload.package_sha256, task)
        log_ctx["package_sha256_check"] = sha_check

        # Step 6: unpack + record_market_upgrade (single atomic call).
        task["status"] = "installing"
        task["progress"] = 0.8
        task["message"] = "正在升级插件..."

        market_override = _build_market_override(
            payload,
            content,
            mode="reinstall" if allow_same_version else "upgrade",
        )

        try:
            result = await _cli_service.upload_and_install(
                filename=_extract_filename(payload.package_url),
                content=content,
                on_conflict="fail",  # backup already moved aside
                install_source_override=market_override,
            )
        except InstallSourceError as exc:
            if exc.code == "lock_write_failed":
                raise _TaskError(
                    code="lock_write_failed",
                    message=str(exc.message),
                ) from exc
            raise _TaskError(
                code="upgrade_rollback_completed",
                message=str(exc.message),
            ) from exc

        rollback_steps.append(_make_remove_dir_step(plugin_dir))

        # Step 7: lifecycle start.
        if was_running:
            task["message"] = "正在启动新版本..."
            await _safely_start(plugin_id)

        # Step 8: async cleanup of backup.
        asyncio.create_task(
            _async_remove_dir(backup_dir),
            name=f"market-upgrade-cleanup-{plugin_id}",
        )

        task["progress"] = 1.0
        task["message"] = "升级成功"
        task["result"] = result

        if isinstance(result, dict) and "install_source_warning" in result:
            task["install_source_warning"] = result["install_source_warning"]

    except _TaskError:
        # _TaskError already carries a stable code; just roll back fs.
        await _run_rollback(rollback_steps, was_running, plugin_id)
        raise
    except Exception as exc:
        # Other (network / sha256 / unpack) failures collapse into one code.
        await _run_rollback(rollback_steps, was_running, plugin_id)
        raise _TaskError(
            code="upgrade_rollback_completed",
            message=f"升级失败已回滚: {exc}",
        ) from exc


def _build_market_override(
    payload: MarketInstallRequest,
    content: bytes,
    *,
    mode: str,
) -> dict[str, Any]:
    """Construct the ``install_source_override`` dict for upload_and_install.

    Caller's ``package_sha256`` is passed through verbatim — the CLI
    service will re-hash and overwrite it with the actual value, but
    for v1 lock entries that legitimately omit the field we want the
    caller-provided value to win when present.
    """

    return {
        "channel": "market",
        "mode": mode,
        "market_detail": {
            "plugin_market_id": payload.plugin_id or "",
            "version": payload.version or "",
            "package_url": payload.package_url,
            "channel": payload.channel or "stable",
            "package_sha256": (payload.package_sha256 or "").lower(),
            "payload_hash": payload.payload_hash,
            "published_at": payload.published_at or _utc_iso_now(),
            # v2 (Option C): identity check — passed through to PluginCliService
            # which compares it against the unpacked plugin.toml id.
            "expected_plugin_toml_id": payload.expected_plugin_toml_id,
        },
    }


def _verify_sha256(
    content: bytes,
    expected_hash: str | None,
    task: dict[str, Any],
) -> Literal["passed", "skipped", "mismatch"]:
    """Verify sha256 if present; raise ValueError on mismatch.

    Returns the structured-log status string for ``log_ctx``.
    """

    raw = (expected_hash or "").strip().lower()
    skip = (
        not raw
        or raw == "0" * 64
        or len(raw) != 64
        or not all(c in "0123456789abcdef" for c in raw)
    )
    if skip:
        logger.warning(
            "Market install skipping SHA256 verification (no hash provided)"
        )
        task["message"] = "跳过 SHA256 校验（Market 未提供）"
        return "skipped"

    task["status"] = "verifying"
    task["progress"] = 0.7
    task["message"] = "正在校验文件完整性..."

    actual = hashlib.sha256(content).hexdigest().lower()
    if actual != raw:
        raise ValueError(
            f"SHA256 校验失败\n  期望: {raw}\n  实际: {actual}"
        )
    return "passed"


def _post_install_payload_check(
    payload: MarketInstallRequest,
    result: Any,
) -> None:
    """Best-effort payload_hash double-check after a successful install.

    Mismatch is logged but does not fail the install — Market's
    ``payload_hash`` may legitimately drift from the unpacked
    ``[payload].hash`` under archive normalisation.
    """

    if not payload.payload_hash or not isinstance(result, dict):
        return
    install_block = result.get("install") or {}
    installed_payload_hash = install_block.get("payload_hash") or ""
    if (
        installed_payload_hash
        and installed_payload_hash.lower() != payload.payload_hash.lower()
    ):
        logger.warning(
            "Payload hash mismatch after install: expected={}, got={}",
            payload.payload_hash,
            installed_payload_hash,
        )


# ─── lifecycle / rollback helpers ─────────────────────────────────────


async def _safely_is_running(plugin_id: str) -> bool:
    """Probe whether ``plugin_id`` is currently running.

    Reads the plugin host registry directly (lock-protected) instead of
    going through the lifecycle service — we just need a snapshot of
    the running set, not a heavy RPC. Failure modes (registry not yet
    initialized, weird plugin id) collapse to "not running" so the
    upgrade flow does not try to stop something that isn't there.
    """

    if not plugin_id:
        return False
    try:
        from plugin.server.application.plugins.lifecycle_service import (
            _plugin_is_running_sync,
        )
        return await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "lifecycle is_running probe failed for plugin_id={}: {}",
            plugin_id,
            exc,
        )
        return False


async def _safely_stop(plugin_id: str) -> None:
    """Best-effort lifecycle stop wrapping ``PluginLifecycleService.stop_plugin``.

    Bridge upgrade calls this **before** renaming the old plugin
    directory; failures here aren't necessarily fatal (Linux happily
    renames a dir even when the process holds open files, Windows
    won't). We surface any error to bridge so it can choose to abort
    rather than risk corruption.
    """

    if not plugin_id:
        return None
    from plugin.server.application.plugins import PluginLifecycleService
    from plugin.server.domain.errors import ServerDomainError

    service = PluginLifecycleService()
    try:
        await service.stop_plugin(plugin_id)
    except ServerDomainError as exc:
        # PLUGIN_NOT_RUNNING (404) is benign — the plugin was already
        # stopped between our is_running probe and the stop call.
        if getattr(exc, "code", None) == "PLUGIN_NOT_RUNNING":
            logger.debug(
                "lifecycle stop: plugin already stopped plugin_id={}",
                plugin_id,
            )
            return None
        logger.error(
            "lifecycle stop failed for plugin_id={}: {}",
            plugin_id,
            exc,
        )
        raise
    except Exception as exc:
        logger.error(
            "lifecycle stop unexpected error for plugin_id={}: {}",
            plugin_id,
            exc,
        )
        raise


async def _safely_start(plugin_id: str) -> None:
    """Best-effort lifecycle start; never raises (R5.4).

    Wraps the start hook in a try/except so that a failure during
    rollback does not shadow the original error. Logged at ERROR with
    the underlying cause so the operator can see why the old version
    didn't come back up.
    """

    if not plugin_id:
        return None
    from plugin.server.application.plugins import PluginLifecycleService

    service = PluginLifecycleService()
    try:
        await service.start_plugin(plugin_id)
    except Exception as exc:
        logger.error(
            "lifecycle start failed for plugin_id={}: {}",
            plugin_id,
            exc,
        )
        return None


def _make_restore_dir_step(
    backup_dir: Path,
    target_dir: Path,
) -> Callable[[], Awaitable[None]]:
    """Build a rollback step that renames ``backup_dir`` back to ``target_dir``."""

    async def _step() -> None:
        if not backup_dir.exists():
            return
        # Make sure target is clear before rename so we don't EEXIST.
        if target_dir.exists():
            await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        await asyncio.to_thread(os.rename, backup_dir, target_dir)

    return _step


def _make_remove_dir_step(target_dir: Path) -> Callable[[], Awaitable[None]]:
    """Build a rollback step that removes a directory, ignoring missing.

    Used for the *new* directory after upload_and_install succeeds; if a
    later step (lifecycle start) fails we rmtree the new dir to make room
    for the backup-restore step to rename the old one back.
    """

    async def _step() -> None:
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)

    return _step


async def _async_remove_dir(target_dir: Path) -> None:
    """Async best-effort rmtree for backup cleanup."""

    try:
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
    except Exception as exc:  # pragma: no cover — ignore_errors=True swallows
        logger.warning("backup cleanup failed for {}: {}", target_dir, exc)


async def _run_rollback(
    rollback_steps: list[Callable[[], Awaitable[None]]],
    was_running: bool,
    plugin_id: str,
) -> None:
    """Execute rollback steps in reverse order, then re-start old plugin.

    Each step is wrapped in try/except so one failure does not stop the
    rest from running. ``_safely_start`` itself is non-throwing.
    """

    for step in reversed(rollback_steps):
        try:
            await step()
        except Exception as exc:
            logger.error(
                "rollback step failed plugin_id={} err={}",
                plugin_id,
                exc,
            )
    if was_running:
        await _safely_start(plugin_id)


def _utc_micro_ts() -> str:
    """Generate a microsecond-precision UTC timestamp suitable for filenames.

    Format: ``YYYYMMDDTHHMMSS_uuuuuu`` (no colons / slashes so it works on
    every OS we support). Backup directory names are derived from this
    so concurrent upgrades on the same plugin can be distinguished —
    though the InstallSourceManager lock already serialises lock writes,
    so concurrent upgrades hitting the *same* timestamp are bounded by
    bridge-level scheduling.
    """

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")


def _utc_iso_now() -> str:
    """Current UTC time in ISO 8601 with microsecond precision and ``Z`` suffix."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _download_package(url: str, task: dict[str, Any]) -> bytes:
    """下载插件包，带进度更新。"""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                # 检查 content-length
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _DOWNLOAD_MAX_BYTES:
                    raise ValueError(
                        f"包文件过大: {int(content_length)} bytes "
                        f"(最大 {_DOWNLOAD_MAX_BYTES} bytes)"
                    )

                chunks: list[bytes] = []
                downloaded = 0

                async for chunk in response.aiter_bytes(chunk_size=65536):
                    chunks.append(chunk)
                    downloaded += len(chunk)

                    if downloaded > _DOWNLOAD_MAX_BYTES:
                        raise ValueError(
                            f"下载超过大小限制: {_DOWNLOAD_MAX_BYTES} bytes"
                        )

                    # 更新进度（下载占 0.1 ~ 0.7）
                    if content_length:
                        dl_progress = downloaded / int(content_length)
                        task["progress"] = 0.1 + dl_progress * 0.6
                        task["message"] = (
                            f"正在下载: {downloaded // 1024}KB"
                            f" / {int(content_length) // 1024}KB"
                        )

                return b"".join(chunks)

    except httpx.HTTPStatusError as exc:
        raise ValueError(f"下载失败: HTTP {exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        raise ValueError("下载超时") from exc
    except httpx.RequestError as exc:
        raise ValueError(f"下载网络错误: {exc}") from exc


def _extract_filename(url: str) -> str:
    """从 URL 提取文件名。"""
    from urllib.parse import urlparse, unquote
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if "/" in path else "package.neko-plugin"
    # 确保有合法后缀
    if not any(name.endswith(s) for s in _ALLOWED_SUFFIXES):
        name = name + ".neko-plugin"
    return name
