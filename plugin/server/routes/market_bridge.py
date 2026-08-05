"""Market Bridge — 本地客户端与插件市场的双向联动协议。

提供以下能力：
1. Market 前端探测本地客户端状态
2. Market 前端触发插件安装（从 URL 下载 → 校验 → 安装）
3. 查询本地已安装插件列表（供 Market 标记已安装状态）
4. 安装任务进度查询
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import os
import secrets
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, get_args
from urllib.parse import quote, urlparse, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from plugin.logging_config import get_logger
from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.neko_plugin_cli.public import inspect_package
from plugin.server.application.install_source import (
    InstallSourceError,
    LockEntry,
    SourceDetailMarket,
    classify_plugin_path,
    get_install_source_manager,
)
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugins.upgrade_support import (
    ReplacePluginError,
    plugin_is_running,
    remove_directory,
    replace_plugin,
    start_plugin_after_upgrade,
    stop_plugin_for_upgrade,
)
from plugin.settings import (
    MARKET_API_URL,
    MARKET_WEB_URL,
    NEKO_AUTH_CLIENT_ID,
    NEKO_AUTH_URL,
)

router = APIRouter(prefix="/market", tags=["market-bridge"])
logger = get_logger("server.routes.market_bridge")

_cli_service = PluginCliService()

# ─── Bridge Token（本地安全令牌）───────────────────────────────────
# 每次服务启动时生成，防止恶意网页未经授权调用本地 API。
# Market 前端需要通过 neko:// 协议或用户手动配对获取此 token。
_BRIDGE_TOKEN: str = secrets.token_urlsafe(32)

# 安装任务存储（内存，重启清空）
_tasks: dict[str, dict[str, Any]] = {}
_TASK_TTL_SECONDS = 60 * 60
_TASK_MAX_ENTRIES = 200

# 短期一次性配对码；成功交换后立即消费。
_ONE_TIME_CODES: dict[str, float] = {}
_ONE_TIME_CODE_TTL_SECONDS = 5 * 60

# OAuth 登录状态存储在本机用户目录，仅供本地插件面板使用。
_OAUTH_CLIENT_ID = NEKO_AUTH_CLIENT_ID
_OAUTH_SCOPE = "openid email profile offline"
_OAUTH_SCOPE_ALIASES = {"offline": "offline_access"}
_OAUTH_REDIRECT_PATH = "/market/oauth/callback"
_OAUTH_SESSION_TTL_SECONDS = 5 * 60
_OAUTH_EXPIRE_SKEW_SECONDS = 60
_MARKET_USER_STATUS_TTL_SECONDS = 60
_ACCOUNT_SUMMARY_TTL_SECONDS = 30
_NEKO_STATE_DIR = Path.home() / ".neko"
_OAUTH_PENDING_FILE = _NEKO_STATE_DIR / "market_oauth_pending.json"
_OAUTH_CALLBACK_FILE = _NEKO_STATE_DIR / "oauth_callback.json"
_OAUTH_TOKEN_FILE = _NEKO_STATE_DIR / "market_auth.json"
_OAUTH_REFRESH_LOCK = asyncio.Lock()
_ACCOUNT_SUMMARY_LOCK = asyncio.Lock()
_ACCOUNT_SUMMARY_CACHE: dict[str, Any] | None = None

# 下载限制
_DOWNLOAD_MAX_BYTES = 200 * 1024 * 1024  # 200 MB
_DOWNLOAD_TIMEOUT = 120.0  # 秒
_ALLOWED_SUFFIXES = frozenset({".neko-plugin", ".neko-bundle"})


def _normalize_required_sha256(value: str | None) -> str:
    """Normalize Market package hash; Market installs must never skip it."""

    raw = (value or "").strip().lower()
    if (
        not raw
        or raw == "0" * 64
        or len(raw) != 64
        or not all(c in "0123456789abcdef" for c in raw)
    ):
        raise ValueError(
            "package_sha256 is required for Market install and must be a "
            "64-character lowercase/uppercase hex SHA256 digest"
        )
    return raw


def get_bridge_token() -> str:
    """获取当前 bridge token（供 URI scheme handler 使用）。"""
    return _BRIDGE_TOKEN


def _main_server_port() -> int:
    """返回主服务的运行时端口；按 config.MAIN_SERVER_PORT 动态读取。

    launcher 会在端口冲突时把 ``config.MAIN_SERVER_PORT`` 改成 fallback 端口，
    所以这里始终通过 ``import config`` 拿最新值，而不是在模块加载时锁死。
    """

    try:
        import config

        return int(config.MAIN_SERVER_PORT)
    except Exception:  # pragma: no cover - 兜底，避免 bridge 写文件因配置异常崩溃
        return 48911


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    return normalized in {"localhost", "127.0.0.1", "::1"}


def _is_local_bridge_origin(origin: str, expected_port: int) -> bool:
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme != "http" or not parsed.hostname or not _is_loopback_host(parsed.hostname):
        return False
    if parsed.username or parsed.password or parsed.path not in ("", "/"):
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    return (parsed.port or 80) == expected_port


def _require_local_bridge_token_access(request: Request) -> None:
    """Allow bridge-token only to the local plugin-manager origin.

    Remote Market origins are intentionally excluded here even when CORS trusts
    them; remote pages must pair through /token-exchange instead.
    """

    host_header = request.headers.get("host", "")
    try:
        host = urlparse(f"//{host_header}").hostname or ""
    except ValueError:
        host = ""
    client_host = request.client.host if request.client else ""
    if not _is_loopback_host(client_host) or not _is_loopback_host(host):
        raise HTTPException(status_code=403, detail="仅允许本地同源访问")

    origin = request.headers.get("origin")
    if origin and not _is_local_bridge_origin(origin, _main_server_port()):
        raise HTTPException(status_code=403, detail="仅允许本地同源访问")


def write_bridge_token_file(directory: Path) -> Path:
    """将 bridge token 写入文件，供外部进程读取。"""
    directory.mkdir(parents=True, exist_ok=True)
    token_file = directory / "bridge.json"
    one_time_code = _issue_one_time_code()
    token_file.write_text(
        json.dumps(
            {
                "token": _BRIDGE_TOKEN,
                "port": _main_server_port(),
                "one_time_code": one_time_code,
                "one_time_code_expires_in": _ONE_TIME_CODE_TTL_SECONDS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        token_file.chmod(0o600)
    except OSError as exc:
        logger.warning("Failed to tighten bridge token file permissions: {}", exc)
    logger.info("Bridge token written to {}", token_file)
    return token_file


def _issue_one_time_code() -> str:
    _cleanup_one_time_codes()
    code = secrets.token_urlsafe(18)
    _ONE_TIME_CODES[code] = time.time() + _ONE_TIME_CODE_TTL_SECONDS
    return code


def _cleanup_one_time_codes(now: float | None = None) -> None:
    current = time.time() if now is None else now
    expired = [code for code, expires_at in _ONE_TIME_CODES.items() if expires_at <= current]
    for code in expired:
        _ONE_TIME_CODES.pop(code, None)


def _consume_one_time_code(code: str) -> bool:
    now = time.time()
    _cleanup_one_time_codes(now)
    for stored_code, expires_at in list(_ONE_TIME_CODES.items()):
        if expires_at > now and secrets.compare_digest(stored_code, code):
            _ONE_TIME_CODES.pop(stored_code, None)
            return True
    return False


def _cleanup_tasks() -> None:
    now = time.time()
    expired = [
        task_id
        for task_id, task in _tasks.items()
        if task.get("completed_at") is not None
        and now - float(task.get("completed_at") or 0) > _TASK_TTL_SECONDS
    ]
    for task_id in expired:
        _tasks.pop(task_id, None)

    if len(_tasks) <= _TASK_MAX_ENTRIES:
        return
    overflow = len(_tasks) - _TASK_MAX_ENTRIES
    ordered = sorted(
        _tasks.items(),
        key=lambda item: float(item[1].get("created_at") or 0),
    )
    for task_id, _task in ordered[:overflow]:
        _tasks.pop(task_id, None)


def _plugin_config_roots() -> tuple[Path, ...]:
    policy = PluginCliPathPolicy.from_settings()
    roots: list[Path] = []
    for root in (policy.builtin_plugins_root, policy.user_plugins_root):
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _read_plugin_toml_id(manifest: Path) -> str | None:
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to read plugin manifest {}: {}", manifest, exc)
        return None

    plugin_table = data.get("plugin")
    if not isinstance(plugin_table, dict):
        return None
    plugin_id = plugin_table.get("id")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        return None
    return plugin_id.strip()


# ─── 请求/响应模型 ─────────────────────────────────────────────────


class MarketStatusResponse(BaseModel):
    online: bool = True
    version: str = "0.1.0"
    protocol_version: int = 1
    client_name: str = "N.E.K.O Plugin Server"
    installed_count: int = 0
    token_required: bool = True
    market_url: str = ""
    market_web_url: str = ""


class MarketInstallRequest(BaseModel):
    """从 Market 触发安装的请求。

    v2 (design §3.4.1) 在原有字段之上新增 ``mode`` / ``channel`` /
    ``published_at``，让客户端区分 install / upgrade / reinstall 三种
    语义并把 Market 已知的发布证据透传到 lock entry 上。
    """
    package_url: str = Field(..., description="插件包下载 URL")
    package_sha256: str = Field(
        ...,
        description="包文件 SHA256。Market 一键安装必须提供合法 64 位 hex，客户端会强制校验。",
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
    # 客户端 unpack 后比对包内 plugin.toml [plugin].id；install 不一致时
    # 附 warning，upgrade/reinstall 不一致时拒绝并回滚。
    expected_plugin_toml_id: str | None = Field(
        default=None,
        description=(
            "Market 上的 plugin.slug；客户端 unpack 后会和包内 plugin.toml "
            "的 id 字段比对。install 不一致只 warn；upgrade/reinstall "
            "不一致会拒绝并回滚"
        ),
    )
    on_conflict: str = Field(default="fail", pattern=r"^(rename|fail)$")
    require_confirm: bool = Field(default=True, description="是否需要用户确认（预留）")

    @field_validator("package_sha256", mode="before")
    @classmethod
    def _validate_package_sha256(cls, value: object) -> str:
        return _normalize_required_sha256(str(value) if value is not None else None)


class MarketInstallResponse(BaseModel):
    task_id: str
    status: str  # "pending" | "downloading" | "installing" | "completed" | "failed"
    message: str = ""


class MarketTaskStatus(BaseModel):
    task_id: str
    status: str
    stage: str = "pending"
    progress: float = 0.0  # 0.0 ~ 1.0
    message: str = ""
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    result: dict[str, Any] | None = None
    # v2 (R10.1 / R10.2): error 字段保留 message 以便旧前端展示；新增 error_code
    # 让前端识别稳定错误码（upgrade_rollback_completed / package_id_change / ...）。
    error: str | None = None
    error_code: str | None = None
    created_at: float = 0.0
    completed_at: float | None = None
    install_source_warning: str | None = None
    rollback: dict[str, Any] | None = None


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


class MarketOAuthStartResponse(BaseModel):
    auth_url: str
    state: str
    expires_in: int = _OAUTH_SESSION_TTL_SECONDS


MarketOAuthState = Literal[
    "ready",
    "token_rejected",
    "forbidden",
    "identity_conflict",
    "unavailable",
    "invalid_response",
]
AuthOAuthState = Literal["ready", "pending"]


class MarketOAuthStatusResponse(BaseModel):
    authenticated: bool
    auth_state: AuthOAuthState | None = None
    market_state: MarketOAuthState | None = None
    retryable: bool = False
    user: dict[str, Any] | None = None
    expires_at: float | None = None
    market_web_url: str = ""


class MarketOAuthCompleteResponse(BaseModel):
    completed: bool
    authenticated: bool
    auth_state: AuthOAuthState | None = None
    market_state: MarketOAuthState | None = None
    retryable: bool = False
    user: dict[str, Any] | None = None
    message: str = ""


class MarketOAuthLogoutResponse(BaseModel):
    message: str


class MarketOAuthAccountSource(BaseModel):
    """A deliberately small availability projection for one account source."""

    status: Literal["ready", "unavailable"]


class MarketOAuthAccountProfile(BaseModel):
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    login_method: str | None = None


class MarketOAuthAccountMarket(BaseModel):
    member_days: int | None = None
    published_plugins: int | None = None
    installed_plugins: int | None = None
    total_downloads: int | None = None


class MarketOAuthAccountSummaryResponse(BaseModel):
    """Safe desktop-facing account summary.

    This intentionally excludes bearer tokens, OAuth subjects, email and
    permissions.  Community profile data is server-to-server only and must
    never make the desktop client a bearer of a Market service token.
    """

    authenticated: bool
    profile: MarketOAuthAccountProfile | None = None
    market: MarketOAuthAccountMarket | None = None
    sources: dict[str, MarketOAuthAccountSource]
    expires_at: float | None = None


# ─── 端点 ──────────────────────────────────────────────────────────


_CATALOG_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "cache-control",
        "etag",
        "last-modified",
        "x-request-id",
    }
)


async def _proxy_market_catalog(request: Request, upstream_path: str) -> Response:
    """Proxy a fixed public catalog path through the local same-origin API."""

    base_url = _normalized_base_url(MARKET_API_URL)
    if not base_url:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Market catalog is not configured",
                "code": "market_catalog_not_configured",
            },
        )

    upstream_url = f"{base_url}/api/v1{upstream_path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=False,
        ) as client:
            upstream = await client.get(upstream_url)
    except httpx.HTTPError as exc:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-catalog] request failed "
            "category={} status=unavailable request_id=unavailable "
            "elapsed_ms={} origin={}",
            _market_auth_network_failure_category(exc),
            elapsed_ms,
            _market_api_log_origin(),
        )
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Market catalog is temporarily unavailable",
                "code": "market_catalog_unavailable",
            },
        )

    if 300 <= upstream.status_code < 400:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-catalog] request failed "
            "category=redirect_rejected status={} request_id={} "
            "elapsed_ms={} origin={}",
            upstream.status_code,
            _safe_market_request_id(upstream.headers.get("x-request-id")),
            elapsed_ms,
            _market_api_log_origin(),
        )
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Market catalog returned an unsafe redirect",
                "code": "market_catalog_redirect_rejected",
            },
        )

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in _CATALOG_RESPONSE_HEADERS
    }
    if upstream.status_code >= 400:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-catalog] request failed "
            "category={} status={} request_id={} elapsed_ms={} origin={}",
            _market_auth_http_failure_category(upstream.status_code),
            upstream.status_code,
            _safe_market_request_id(upstream.headers.get("x-request-id")),
            elapsed_ms,
            _market_api_log_origin(),
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


@router.get("/catalog/api/v1/plugins")
async def market_catalog_plugins(request: Request) -> Response:
    return await _proxy_market_catalog(request, "/plugins")


@router.get("/catalog/api/v1/plugins/{plugin_id}/versions")
async def market_catalog_plugin_versions(
    request: Request,
    plugin_id: str,
) -> Response:
    return await _proxy_market_catalog(
        request,
        f"/plugins/{quote(plugin_id, safe='')}/versions",
    )


@router.get("/catalog/api/v1/plugins/{plugin_id}")
async def market_catalog_plugin(request: Request, plugin_id: str) -> Response:
    return await _proxy_market_catalog(
        request,
        f"/plugins/{quote(plugin_id, safe='')}",
    )


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

    return MarketStatusResponse(
        installed_count=count,
        market_url=MARKET_API_URL,
        market_web_url=MARKET_WEB_URL,
    )


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
        expected_plugin_id = payload.expected_plugin_toml_id or payload.plugin_id or ""
        if mgr is None or mgr.find_active_market_entry(expected_plugin_id) is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "plugin_not_installed_for_upgrade",
                    "message": (
                        f"plugin {expected_plugin_id!r} has no active market lock "
                        "entry; cannot upgrade / reinstall"
                    ),
                },
            )

    _cleanup_tasks()
    task_id = secrets.token_urlsafe(16)
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "任务已创建",
        "downloaded_bytes": 0,
        "total_bytes": None,
        "result": None,
        "error": None,
        "error_code": None,
        "created_at": time.time(),
        "completed_at": None,
        "rollback": None,
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
    _cleanup_tasks()

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
        # 一次性拿全量 lock 索引
        mgr = get_install_source_manager()
        snapshot = mgr.snapshot() if mgr is not None else None
        entries_by_pid: dict[str, LockEntry] = {}
        entries_by_dir: dict[tuple[str, str], LockEntry] = {}
        if snapshot is not None:
            entries_by_pid = {
                e.plugin_id: e
                for e in snapshot.entries
                if not e.removed and e.plugin_id
            }
            entries_by_dir = {
                (e.root_id, e.directory_name): e
                for e in snapshot.entries
                if not e.removed and e.root_id and e.directory_name
            }

        installed_by_pid: dict[str, MarketInstalledPlugin] = {}
        for root in _plugin_config_roots():
            if not root.is_dir():
                continue
            for manifest in root.glob("*/plugin.toml"):
                if not manifest.is_file():
                    continue
                plugin_dir = manifest.parent
                plugin_id = _read_plugin_toml_id(manifest) or plugin_dir.name
                entry: LockEntry | None = None
                if mgr is not None:
                    try:
                        root_id, directory_name = classify_plugin_path(
                            plugin_dir,
                            builtin_root=mgr.builtin_root,
                            user_root=mgr.user_root,
                        )
                        entry = entries_by_dir.get((root_id, directory_name))
                    except (InstallSourceError, ValueError):
                        entry = None
                if entry is None:
                    pid_entry = entries_by_pid.get(plugin_id)
                    if (
                        pid_entry is not None
                        and pid_entry.directory_name == plugin_dir.name
                    ):
                        entry = pid_entry

                projected_source = _project_market_source_detail(entry)
                candidate = MarketInstalledPlugin(
                    plugin_id=plugin_id,
                    path=str(plugin_dir),
                    latest_install_source=projected_source,
                )
                existing = installed_by_pid.get(plugin_id)
                if existing is None or (
                    existing.latest_install_source is None
                    and candidate.latest_install_source is not None
                ):
                    installed_by_pid[plugin_id] = candidate
        installed = list(installed_by_pid.values())
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
        "plugin_market_id": detail.plugin_market_id,
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
    if not _consume_one_time_code(payload.one_time_code):
        raise HTTPException(status_code=403, detail="无效的一次性码")

    return MarketTokenExchangeResponse(
        bridge_token=_BRIDGE_TOKEN,
        expires_in=None,
    )


@router.get("/bridge-token", response_model=MarketBridgeTokenResponse)
async def market_bridge_token(request: Request):
    """供同源前端（plugin-manager UI）获取 bridge token。

    plugin-manager UI 由同一个 FastAPI 进程托管，跟 /market/* 同源，所以
    不需要走 one-time code 配对。只允许 127.0.0.1 / localhost 来源，避免
    被外部网页拿到 token。
    """
    _require_local_bridge_token_access(request)

    return MarketBridgeTokenResponse(bridge_token=_BRIDGE_TOKEN, port=_main_server_port())


@router.post("/oauth/start", response_model=MarketOAuthStartResponse)
async def market_oauth_start(
    request: Request,
    token: str | None = Query(
        None,
        description="(legacy) Bridge token; prefer Authorization: Bearer header",
    ),
    authorization: str | None = Header(None),
):
    """启动 N.E.K.O → Auth OAuth 登录。

    本地服务生成 PKCE verifier/challenge 并只把 verifier 存到本机文件；
    前端只拿授权 URL，避免把可换 token 的 secret 暴露到浏览器状态里。
    """
    _verify_token(token, authorization=authorization)
    if not NEKO_AUTH_URL or not MARKET_API_URL:
        raise HTTPException(status_code=400, detail="Auth URL 或 Market API URL 未配置")

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _pkce_s256_challenge(code_verifier)
    expires_at = time.time() + _OAUTH_SESSION_TTL_SECONDS
    redirect_uri = _oauth_redirect_uri_for_request(request)

    _unlink_if_exists(_OAUTH_CALLBACK_FILE)
    _write_private_json(
        _OAUTH_PENDING_FILE,
        {
            "state": state,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "created_at": time.time(),
            "expires_at": expires_at,
            "auth_url": _normalized_base_url(NEKO_AUTH_URL),
            "issuer": _auth_issuer(),
            "client_id": _OAUTH_CLIENT_ID,
            "market_api_url": _normalized_base_url(MARKET_API_URL),
        },
    )

    query = urlencode({
        "client_id": _OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
        "scope": _OAUTH_SCOPE,
    })
    auth_url = f"{NEKO_AUTH_URL.rstrip('/')}/oauth2/auth?{query}"
    return MarketOAuthStartResponse(auth_url=auth_url, state=state)


# Subtags that mark a Chinese tag as Traditional. Matched against the tag's
# own subtags (set intersection), not as a substring of the whole tag, so a
# non-language subtag can never drag a Simplified tag over. Same membership as
# plugin_install.py and application/plugins/ui_query_service.py — one shared
# notion of "Traditional", not a fourth.
_ZH_HANT_SUBTAGS = frozenset({"tw", "hk", "mo", "hant"})


# Callback-page copy, keyed by whatever ``_preferred_oauth_callback_locale``
# can return. The lookup at the use site is a hard subscript on purpose (a
# missing key should fail loudly in tests, not silently serve the wrong
# language), so these two must stay in lockstep — hence a module constant a
# test can assert against rather than a dict literal inline in the handler.
_OAUTH_CALLBACK_COPY: dict[str, dict[str, str]] = {
    "zh-CN": {
        "title": "N.E.K.O 浏览器授权已返回",
        "heading": "浏览器授权已返回",
        "body": "请回到 N.E.K.O 插件管理器，客户端正在确认 Auth 与 Market 账号状态。",
        "close": "这个页面现在可以关闭。",
    },
    "zh-TW": {
        "title": "N.E.K.O 瀏覽器授權已返回",
        "heading": "瀏覽器授權已返回",
        "body": "請回到 N.E.K.O 外掛管理器，用戶端正在確認 Auth 與 Market 帳號狀態。",
        "close": "這個頁面現在可以關閉。",
    },
    "en": {
        "title": "N.E.K.O browser authorization returned",
        "heading": "Browser authorization returned",
        "body": "Return to the N.E.K.O plugin manager while it confirms your Auth and Market account status.",
        "close": "You can close this page now.",
    },
    "ja": {
        "title": "N.E.K.O ブラウザー認証が戻りました",
        "heading": "ブラウザー認証が戻りました",
        "body": "N.E.K.O プラグインマネージャーに戻ってください。Auth と Market のアカウント状態を確認しています。",
        "close": "このページは閉じてもかまいません。",
    },
}


def _preferred_oauth_callback_locale(accept_language: str) -> str:
    """Select the highest-priority supported callback locale."""

    supported = {"zh": "zh-CN", "ja": "ja", "en": "en", "*": "en"}
    candidates: list[tuple[float, int, str]] = []
    for index, raw_entry in enumerate(accept_language.lower().split(",")):
        parts = [part.strip() for part in raw_entry.split(";")]
        tag = parts[0]
        if not tag:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            key, separator, value = parameter.partition("=")
            if separator and key.strip() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
                break
        primary_tag = tag.split("-", 1)[0]
        locale = supported.get(primary_tag)
        # accept_language was lower()ed as a whole above, so subtags compare
        # lowercase. Looking at the primary tag alone would serve zh-TW / zh-HK
        # a Simplified page.
        if locale == "zh-CN" and _ZH_HANT_SUBTAGS & set(tag.split("-")[1:]):
            locale = "zh-TW"
        if locale is None or not 0.0 < quality <= 1.0:
            continue
        candidates.append((-quality, index, locale))
    if not candidates:
        return "en"
    candidates.sort()
    return candidates[0][2]


@router.get("/oauth/callback", response_class=HTMLResponse)
async def market_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
):
    """Browser loopback callback for Market OAuth.

    Linux desktop environments often do not have the custom ``neko://`` scheme
    registered, causing xdg-open/KIO to treat the callback as an unreadable
    file URL. A loopback redirect mirrors the pattern used by desktop apps such
    as VS Code and lets the already-running local server receive the code.
    """
    pending = _read_json_file(_OAUTH_PENDING_FILE)
    if not pending:
        raise HTTPException(status_code=400, detail="OAuth 登录尚未开始")
    if time.time() > float(pending.get("expires_at") or 0):
        _unlink_if_exists(_OAUTH_PENDING_FILE)
        raise HTTPException(status_code=400, detail="OAuth 登录已过期，请重新登录")
    expected_state = str(pending.get("state") or "")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="OAuth state 校验失败")

    _write_private_json(
        _OAUTH_CALLBACK_FILE,
        {"code": code, "state": state, "timestamp": time.time()},
    )
    locale = _preferred_oauth_callback_locale(
        request.headers.get("accept-language", "")
    )
    copy = _OAUTH_CALLBACK_COPY[locale]
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="{locale}">
          <head>
            <meta charset="utf-8" />
            <title>{copy["title"]}</title>
            <style>
              body {{
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0f0f1a;
                color: #f8fafc;
                display: grid;
                min-height: 100vh;
                place-items: center;
                margin: 0;
              }}
              main {{
                max-width: 520px;
                padding: 32px;
                border: 1px solid rgba(148, 163, 184, 0.24);
                border-radius: 18px;
                background: rgba(26, 26, 46, 0.92);
                text-align: center;
              }}
              p {{ color: #cbd5e1; line-height: 1.7; }}
            </style>
          </head>
          <body>
            <main>
              <h1>{copy["heading"]}</h1>
              <p>{copy["body"]}</p>
              <p>{copy["close"]}</p>
            </main>
          </body>
        </html>
        """,
        status_code=200,
    )


@router.get("/oauth/status", response_model=MarketOAuthStatusResponse)
async def market_oauth_status(
    token: str | None = Query(
        None,
        description="(legacy) Bridge token; prefer Authorization: Bearer header",
    ),
    authorization: str | None = Header(None),
):
    """返回本地保存的 Auth 登录状态，并用 Market 资源侧接口复核。"""
    _verify_token(token, authorization=authorization)
    token_data = await _ensure_valid_oauth_token()
    if not token_data:
        return MarketOAuthStatusResponse(
            authenticated=False,
            market_web_url=MARKET_WEB_URL,
        )
    token_snapshot = dict(token_data)

    if not _oauth_subject_is_verified(token_data):
        access_token = token_data.get("access_token")
        try:
            auth_user = await _fetch_auth_userinfo(access_token)
        except _OAuthAccessTokenRejected:
            token_data = await _ensure_valid_oauth_token(
                force_refresh=True,
                rejected_access_token=(
                    access_token if isinstance(access_token, str) else None
                ),
            )
            if not token_data:
                _clear_account_summary_cache()
                return MarketOAuthStatusResponse(
                    authenticated=False,
                    market_web_url=MARKET_WEB_URL,
                )
            token_snapshot = dict(token_data)
            try:
                auth_user = await _fetch_auth_userinfo(token_data.get("access_token"))
            except _OAuthAccessTokenRejected:
                logger.info(
                    "Refreshed Auth access token was rejected while resolving subject"
                )
                if _unlink_oauth_token_if_matches(token_snapshot):
                    _clear_account_summary_cache()
                return MarketOAuthStatusResponse(
                    authenticated=False,
                    market_web_url=MARKET_WEB_URL,
                )
        subject = _extract_auth_subject(auth_user)
        if not _oauth_token_snapshot_matches(token_snapshot):
            return _oauth_status_after_cas_conflict(token_snapshot)
        if subject:
            token_data["subject"] = subject
            token_data["subject_pending"] = False
            token_data["auth_state"] = "ready"
            token_data["updated_at"] = time.time()
            if not _write_oauth_token_if_matches(token_snapshot, token_data):
                return _oauth_status_after_cas_conflict(token_snapshot)
            token_snapshot = dict(token_data)
        else:
            token_data["subject"] = None
            token_data["subject_pending"] = True
            token_data["auth_state"] = "pending"
            token_data["updated_at"] = time.time()
            if not _write_oauth_token_if_matches(token_snapshot, token_data):
                return _oauth_status_after_cas_conflict(token_snapshot)
            return MarketOAuthStatusResponse(
                authenticated=False,
                auth_state="pending",
                retryable=True,
                expires_at=token_data.get("expires_at"),
                market_web_url=MARKET_WEB_URL,
            )

    cached_user = _fresh_cached_market_user(token_data)
    if cached_user is not None:
        return MarketOAuthStatusResponse(
            authenticated=True,
            auth_state="ready",
            market_state="ready",
            user=cached_user,
            expires_at=token_data.get("expires_at"),
            market_web_url=MARKET_WEB_URL,
        )

    market_probe = await _probe_market_user(token_data.get("access_token"))
    if market_probe.state == "token_rejected" and token_data.get("refresh_token"):
        rejected_access_token = token_data.get("access_token")
        token_data = await _ensure_valid_oauth_token(
            force_refresh=True,
            rejected_access_token=(
                rejected_access_token
                if isinstance(rejected_access_token, str)
                else None
            ),
        )
        if not token_data:
            _clear_account_summary_cache()
            return MarketOAuthStatusResponse(
                authenticated=False,
                market_web_url=MARKET_WEB_URL,
            )
        token_snapshot = dict(token_data)
        market_probe = await _probe_market_user(token_data.get("access_token"))
    token_data["market_state"] = market_probe.state
    if market_probe.user is not None:
        token_data["user"] = market_probe.user
        token_data["market_api_url_last_verified"] = _normalized_base_url(MARKET_API_URL)
        token_data["market_user_verified_at"] = time.time()
    token_data["auth_state"] = "ready"
    token_data["updated_at"] = time.time()
    if not _write_oauth_token_if_matches(token_snapshot, token_data):
        return _oauth_status_after_cas_conflict(token_snapshot)

    return MarketOAuthStatusResponse(
        authenticated=True,
        auth_state="ready",
        market_state=market_probe.state,
        retryable=market_probe.retryable,
        user=market_probe.user or token_data.get("user"),
        expires_at=token_data.get("expires_at"),
        market_web_url=MARKET_WEB_URL,
    )


@router.get("/oauth/account-summary", response_model=MarketOAuthAccountSummaryResponse)
async def market_oauth_account_summary(
    token: str | None = Query(
        None,
        description="(legacy) Bridge token; prefer Authorization: Bearer header",
    ),
    authorization: str | None = Header(None),
):
    """Return the local desktop's safe, short-lived account projection.

    The bridge owns OAuth refresh and all remote calls.  The UI only receives
    a display projection, never a reusable Auth or Market credential.
    """

    _verify_token(token, authorization=authorization)
    token_data = await _ensure_valid_oauth_token()
    if not token_data:
        return _unauthenticated_account_summary()
    if not _oauth_subject_is_verified(token_data):
        return _unauthenticated_account_summary()
    token_snapshot = dict(token_data)

    cache_key = _account_summary_cache_key(token_data)
    cached = _fresh_account_summary(cache_key)
    if cached is not None:
        return MarketOAuthAccountSummaryResponse.model_validate(cached)

    async with _ACCOUNT_SUMMARY_LOCK:
        if not _oauth_token_snapshot_matches(token_snapshot):
            return _account_summary_for_invalidated_snapshot(token_snapshot)
        cached = _fresh_account_summary(cache_key)
        if cached is not None:
            return MarketOAuthAccountSummaryResponse.model_validate(cached)

        access_token = token_data.get("access_token")
        try:
            auth_user, market_user = await asyncio.gather(
                _fetch_auth_userinfo(access_token),
                _fetch_current_market_user(token_data),
            )
        except _OAuthAccessTokenRejected:
            token_data = await _ensure_valid_oauth_token(
                force_refresh=True,
                rejected_access_token=(
                    access_token if isinstance(access_token, str) else None
                ),
            )
            if not token_data:
                _clear_account_summary_cache()
                return _unauthenticated_account_summary()

            token_snapshot = dict(token_data)
            access_token = token_data.get("access_token")
            try:
                auth_user, market_user = await asyncio.gather(
                    _fetch_auth_userinfo(access_token),
                    _fetch_current_market_user(token_data),
                )
            except _OAuthAccessTokenRejected:
                logger.info("Refreshed Auth access token was rejected by userinfo")
                if _unlink_oauth_token_if_matches(token_snapshot):
                    _clear_account_summary_cache()
                return _unauthenticated_account_summary()

            cache_key = _account_summary_cache_key(token_data)
        if not _oauth_token_snapshot_matches(token_snapshot):
            return _account_summary_for_invalidated_snapshot(token_snapshot)
        summary = _build_account_summary(token_data, auth_user, market_user)
        _store_account_summary(cache_key, summary)
        return summary


@router.post("/oauth/complete", response_model=MarketOAuthCompleteResponse)
async def market_oauth_complete(
    token: str | None = Query(
        None,
        description="(legacy) Bridge token; prefer Authorization: Bearer header",
    ),
    authorization: str | None = Header(None),
):
    """消费浏览器回调写入的授权码并换取 Auth token。"""
    _verify_token(token, authorization=authorization)

    pending = _read_json_file(_OAUTH_PENDING_FILE)
    if not pending:
        return MarketOAuthCompleteResponse(
            completed=False,
            authenticated=False,
            message="OAuth 登录尚未开始",
        )
    if time.time() > float(pending.get("expires_at") or 0):
        _unlink_if_exists(_OAUTH_PENDING_FILE)
        _unlink_if_exists(_OAUTH_CALLBACK_FILE)
        raise HTTPException(status_code=400, detail="OAuth 登录已过期，请重新登录")

    callback = _read_json_file(_OAUTH_CALLBACK_FILE)
    if not callback:
        return MarketOAuthCompleteResponse(
            completed=False,
            authenticated=False,
            message="等待浏览器授权回调",
        )

    state = str(callback.get("state") or "")
    if not state or not secrets.compare_digest(state, str(pending.get("state") or "")):
        _unlink_if_exists(_OAUTH_CALLBACK_FILE)
        raise HTTPException(status_code=400, detail="OAuth state 校验失败")

    code = str(callback.get("code") or "")
    code_verifier = str(pending.get("code_verifier") or "")
    if not code or not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth 回调数据不完整")

    redirect_uri = str(pending.get("redirect_uri") or _oauth_default_redirect_uri())
    token_payload = await _exchange_oauth_code(code, code_verifier, redirect_uri)
    await _require_active_oauth_session(state, token_payload)
    access_token = token_payload.get("access_token")
    try:
        auth_user = await _fetch_auth_userinfo(access_token)
    except _OAuthAccessTokenRejected as exc:
        await _require_active_oauth_session(state, token_payload)
        _clear_oauth_session()
        await _revoke_oauth_token_best_effort(token_payload)
        raise HTTPException(status_code=401, detail="auth_token_rejected") from exc
    subject = _extract_auth_subject(auth_user)

    if subject:
        market_probe = await _probe_market_user(access_token)
        user = market_probe.user
        market_state = market_probe.state
        retryable = market_probe.retryable
        auth_state: AuthOAuthState = "ready"
    else:
        user = None
        market_state = None
        retryable = True
        auth_state = "pending"
    expires_in = int(token_payload.get("expires_in") or 3600)
    now = time.time()
    stored = {
        "access_token": access_token,
        "refresh_token": token_payload.get("refresh_token"),
        "token_type": token_payload.get("token_type", "bearer"),
        "scope": token_payload.get("scope", _OAUTH_SCOPE),
        "expires_at": now + expires_in,
        "auth_url": _normalized_base_url(NEKO_AUTH_URL),
        "issuer": _auth_issuer(),
        "subject": subject,
        "subject_pending": subject is None,
        "auth_state": auth_state,
        "session_id": state,
        "client_id": _OAUTH_CLIENT_ID,
        "refresh_generation": 0,
        "state_revision": 0,
        "market_api_url": _normalized_base_url(MARKET_API_URL),
        "market_state": market_state,
        "user": user,
        "created_at": now,
        "updated_at": now,
    }
    if market_state == "ready":
        stored["market_api_url_last_verified"] = _normalized_base_url(MARKET_API_URL)
        stored["market_user_verified_at"] = now
    await _require_active_oauth_session(state, token_payload)
    _write_private_json(_OAUTH_TOKEN_FILE, stored)
    _clear_account_summary_cache()
    _clear_oauth_session()

    return MarketOAuthCompleteResponse(
        completed=True,
        authenticated=auth_state == "ready",
        auth_state=auth_state,
        market_state=market_state,
        retryable=retryable,
        user=user,
        message=(
            _market_oauth_state_message(market_state)
            if market_state is not None
            else "auth_login_pending"
        ),
    )


@router.post("/oauth/logout", response_model=MarketOAuthLogoutResponse)
async def market_oauth_logout(
    token: str | None = Query(
        None,
        description="(legacy) Bridge token; prefer Authorization: Bearer header",
    ),
    authorization: str | None = Header(None),
):
    """清除本地保存的 Auth OAuth token，Auth revoke 失败不影响本地退出。"""
    _verify_token(token, authorization=authorization)
    token_data = _read_json_file(_OAUTH_TOKEN_FILE)
    _clear_account_summary_cache()
    _unlink_if_exists(_OAUTH_TOKEN_FILE)
    _unlink_if_exists(_OAUTH_PENDING_FILE)
    _unlink_if_exists(_OAUTH_CALLBACK_FILE)
    if token_data:
        await _revoke_oauth_token_best_effort(token_data)
    return MarketOAuthLogoutResponse(message="已退出 Auth 登录")


# ─── 内部实现 ──────────────────────────────────────────────────────


def _verify_token(
    token: str | None = None,
    *,
    authorization: str | None = None,
) -> None:
    """验证 bridge token。

    Phase 3 dual-accept window (PR #1480 review-fix bug 1.6): the
    bridge token is accepted from EITHER the legacy ``?token=...``
    query parameter OR an ``Authorization: Bearer <token>`` HTTP
    header, with the header winning when both are present. Currently
    used only by the four ``/market/oauth/*`` endpoints; the rest of
    the bridge surface still uses the positional-query path.

    Why dual-accept (vs. flipping to header-only):

    * Old plugin-manager bundles still in the field send ``?token=``;
      cutting them over before the frontend ships in the same release
      would 403 every login they attempt during the upgrade window.
    * The header path is preferred and we want new code to use it,
      so when both are present (which should not happen in normal
      traffic) we lock to ``Authorization`` to avoid silently
      tolerating leaked query-string tokens.

    The header MUST be of the form ``Bearer <token>`` (case-insensitive
    on the ``Bearer`` keyword); anything else falls through to the
    query parameter as if no header had been sent. ``compare_digest``
    against ``_BRIDGE_TOKEN`` is the final gate either way.
    """

    candidate: str | None = None
    if authorization:
        # Spec: scheme must be Bearer, case-insensitive; whitespace
        # between scheme and token allowed per RFC 7235 §2.1.
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            candidate = parts[1].strip()
    if candidate is None:
        # Treat empty string the same as missing — secrets.compare_digest
        # would happily compare two empty strings as equal if _BRIDGE_TOKEN
        # were ever empty, but we'd rather 403 explicitly.
        candidate = (token or "").strip() or None

    if not candidate or not secrets.compare_digest(candidate, _BRIDGE_TOKEN):
        raise HTTPException(status_code=403, detail="无效的 bridge token")


def _pkce_s256_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _oauth_default_redirect_uri() -> str:
    return f"http://127.0.0.1:{_main_server_port()}{_OAUTH_REDIRECT_PATH}"


def _oauth_redirect_uri_for_request(request: Request) -> str:
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port
    # OAuth loopback callbacks should stay on loopback even if the Host header
    # was an IPv6 or localhost spelling; this avoids custom protocol handling.
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    netloc = host if port is None else f"{host}:{port}"
    return f"{request.url.scheme}://{netloc}{_OAUTH_REDIRECT_PATH}"


def _normalized_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _safe_url_log_origin(value: str) -> str:
    """Return only scheme and host, excluding credentials, path and query."""
    try:
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "unavailable"
    if scheme not in {"http", "https"} or not hostname:
        return "unavailable"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{display_host}{port_suffix}"


def _market_api_log_origin() -> str:
    """Return only the non-secret Market origin for diagnostics."""

    return _safe_url_log_origin(MARKET_API_URL)


def _safe_market_request_id(value: Any) -> str:
    """Keep a bounded, single-line request id or replace it entirely."""

    if not isinstance(value, str):
        return "unavailable"
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > 128
        or any(
            not (char.isascii() and (char.isalnum() or char in "._:-"))
            for char in cleaned
        )
    ):
        return "unavailable"
    return cleaned


def _market_auth_http_failure_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "credential_rejected"
    if status_code == 409:
        return "identity_conflict"
    if status_code == 429:
        return "rate_limited"
    if status_code == 408 or status_code >= 500:
        return "market_unavailable"
    return "unexpected_status"


def _market_auth_network_failure_category(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection_error"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    return "request_error"


def _auth_oauth_http_failure_category(status_code: int) -> str:
    if status_code in {400, 401, 403}:
        return "credential_rejected"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "auth_unavailable"
    return "unexpected_status"


def _log_auth_oauth_failure(
    operation: str,
    *,
    category: str,
    status: int | str,
    request_id: Any,
    started_at: float,
    debug: bool = False,
) -> None:
    log = logger.debug if debug else logger.warning
    log(
        "[market-auth] Auth OAuth request failed "
        "operation={} category={} status={} request_id={} elapsed_ms={} origin={}",
        operation,
        category,
        status,
        _safe_market_request_id(request_id),
        max(0, round((time.monotonic() - started_at) * 1000)),
        _safe_url_log_origin(NEKO_AUTH_URL),
    )


def _market_download_http_failure_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "download_rejected"
    if status_code == 404:
        return "package_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "upstream_unavailable"
    return "unexpected_status"


def _auth_issuer() -> str:
    base = _normalized_base_url(NEKO_AUTH_URL)
    return f"{base}/" if base else ""


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("Failed to tighten {} permissions: {}", path, exc)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read JSON file {}: {}", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _oauth_token_snapshot_matches(snapshot: dict[str, Any]) -> bool:
    """CAS guard for async OAuth work that may finish after logout/re-login."""

    current = _read_json_file(_OAUTH_TOKEN_FILE)
    if not current:
        return False
    expected_token = snapshot.get("access_token")
    current_token = current.get("access_token")
    if not (
        isinstance(expected_token, str)
        and isinstance(current_token, str)
        and secrets.compare_digest(expected_token, current_token)
    ):
        return False
    expected_session = snapshot.get("session_id")
    if isinstance(expected_session, str) and expected_session:
        current_session = current.get("session_id")
        if not (
            isinstance(current_session, str)
            and secrets.compare_digest(expected_session, current_session)
        ):
            return False
    expected_revision = _oauth_state_revision(snapshot)
    current_revision = _oauth_state_revision(current)
    return (
        current.get("refresh_generation") == snapshot.get("refresh_generation")
        and expected_revision is not None
        and current_revision == expected_revision
    )


def _current_oauth_token_for_same_session_snapshot(
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the current token when only same-session state won the CAS race.

    This deliberately ignores ``state_revision`` because a newer revision is
    the expected cause of this recovery path. It only proves that the session
    identity has not been replaced.
    """

    current = _read_json_file(_OAUTH_TOKEN_FILE)
    if (
        not current
        or not _oauth_token_provenance_matches(current)
        or current.get("access_token") != snapshot.get("access_token")
        or current.get("session_id") != snapshot.get("session_id")
        or current.get("refresh_generation") != snapshot.get("refresh_generation")
    ):
        return None
    return current


def _current_oauth_token_for_invalidated_snapshot(
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a same-session CAS winner only after subject verification."""

    current = _current_oauth_token_for_same_session_snapshot(snapshot)
    if current is None or not _oauth_subject_is_verified(current):
        return None
    return current


def _oauth_status_after_cas_conflict(
    snapshot: dict[str, Any],
) -> MarketOAuthStatusResponse:
    current = _current_oauth_token_for_same_session_snapshot(snapshot)
    if current is None:
        return MarketOAuthStatusResponse(
            authenticated=False,
            market_web_url=MARKET_WEB_URL,
        )
    if not _oauth_subject_is_verified(current):
        if current.get("subject_pending") is True:
            return MarketOAuthStatusResponse(
                authenticated=False,
                auth_state="pending",
                retryable=True,
                expires_at=current.get("expires_at"),
                market_web_url=MARKET_WEB_URL,
            )
        return MarketOAuthStatusResponse(
            authenticated=False,
            market_web_url=MARKET_WEB_URL,
        )

    current_state = current.get("market_state")
    if current_state not in get_args(MarketOAuthState):
        current_state = "unavailable"
    current_user = current.get("user")
    return MarketOAuthStatusResponse(
        authenticated=True,
        auth_state="ready",
        market_state=current_state,
        retryable=current_state == "unavailable",
        user=current_user if isinstance(current_user, dict) else None,
        expires_at=current.get("expires_at"),
        market_web_url=MARKET_WEB_URL,
    )


def _write_oauth_token_if_matches(
    snapshot: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    if not _oauth_token_snapshot_matches(snapshot):
        return False
    revision = _oauth_state_revision(snapshot)
    if revision is None:
        return False
    payload["state_revision"] = revision + 1
    _write_private_json(_OAUTH_TOKEN_FILE, payload)
    return True


def _unlink_oauth_token_if_matches(snapshot: dict[str, Any]) -> bool:
    if not _oauth_token_snapshot_matches(snapshot):
        return False
    _unlink_if_exists(_OAUTH_TOKEN_FILE)
    return True


def _market_token_expires_soon(token_data: dict[str, Any]) -> bool:
    expires_at = token_data.get("expires_at")
    if expires_at is None:
        return False
    try:
        return float(expires_at) <= time.time() + _OAUTH_EXPIRE_SKEW_SECONDS
    except (TypeError, ValueError):
        return True


def _market_token_is_expired(token_data: dict[str, Any]) -> bool:
    expires_at = token_data.get("expires_at")
    if expires_at is None:
        return False
    try:
        return float(expires_at) <= time.time()
    except (TypeError, ValueError):
        return True


def _oauth_state_revision(token_data: dict[str, Any]) -> int | None:
    value = token_data.get("state_revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _oauth_subject_is_verified(token_data: dict[str, Any]) -> bool:
    subject = token_data.get("subject")
    return (
        token_data.get("subject_pending") is False
        and isinstance(subject, str)
        and bool(subject.strip())
    )


def _oauth_token_provenance_matches(token_data: dict[str, Any]) -> bool:
    if token_data.get("auth_url") != _normalized_base_url(NEKO_AUTH_URL):
        return False
    if token_data.get("issuer") != _auth_issuer():
        return False
    if token_data.get("client_id") != _OAUTH_CLIENT_ID:
        return False
    subject = token_data.get("subject")
    if (
        not (isinstance(subject, str) and subject.strip())
        and token_data.get("subject_pending") is not True
    ):
        return False
    try:
        int(token_data.get("refresh_generation"))
    except (TypeError, ValueError):
        return False
    if _oauth_state_revision(token_data) is None:
        return False

    granted = _normalize_oauth_scopes(str(token_data.get("scope") or "").split())
    required = _normalize_oauth_scopes(_OAUTH_SCOPE.split())
    return required.issubset(granted)


def _normalize_oauth_scopes(scopes: Iterable[str]) -> set[str]:
    return {
        _OAUTH_SCOPE_ALIASES.get(scope.strip(), scope.strip())
        for scope in scopes
        if scope.strip()
    }


def _fresh_cached_market_user(token_data: dict[str, Any]) -> dict[str, Any] | None:
    user = token_data.get("user")
    if not isinstance(user, dict):
        return None
    if token_data.get("market_api_url_last_verified") != _normalized_base_url(MARKET_API_URL):
        return None
    try:
        verified_at = float(token_data.get("market_user_verified_at"))
    except (TypeError, ValueError):
        return None
    if verified_at <= time.time() - _MARKET_USER_STATUS_TTL_SECONDS:
        return None
    return user


def _extract_subject(user: dict[str, Any] | None) -> str | None:
    if not isinstance(user, dict):
        return None
    for key in ("auth_user_id", "ory_subject", "sub", "id"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def _extract_auth_subject(user: dict[str, Any] | None) -> str | None:
    """Extract the OIDC subject; ordinary profile IDs are not authentication."""

    if not isinstance(user, dict):
        return None
    subject = user.get("sub")
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    return None


async def _ensure_valid_oauth_token(
    *,
    force_refresh: bool = False,
    rejected_access_token: str | None = None,
) -> dict[str, Any] | None:
    token_data = _read_json_file(_OAUTH_TOKEN_FILE)
    if not token_data or not token_data.get("access_token"):
        return None
    if not _oauth_token_provenance_matches(token_data):
        logger.info("Skip saved Auth token: auth_url/client_id provenance mismatch")
        _unlink_if_exists(_OAUTH_TOKEN_FILE)
        return None
    if (
        rejected_access_token is not None
        and token_data.get("access_token") != rejected_access_token
    ):
        return token_data
    if not force_refresh and not _market_token_expires_soon(token_data):
        return token_data
    if not token_data.get("refresh_token"):
        logger.info("Saved Auth token is expired and has no refresh token")
        _unlink_if_exists(_OAUTH_TOKEN_FILE)
        return None

    async with _OAUTH_REFRESH_LOCK:
        current = _read_json_file(_OAUTH_TOKEN_FILE)
        if not current or not current.get("access_token"):
            return None
        if not _oauth_token_provenance_matches(current):
            return None
        if (
            rejected_access_token is not None
            and current.get("access_token") != rejected_access_token
        ):
            return current
        if not force_refresh and not _market_token_expires_soon(current):
            return current
        if not current.get("refresh_token"):
            _unlink_oauth_token_if_matches(current)
            return None

        try:
            refreshed = await _refresh_oauth_token(current)
        except HTTPException as exc:
            logger.info("Auth token refresh failed: {}", exc.detail)
            if exc.status_code == 401:
                _unlink_oauth_token_if_matches(current)
                return None
            if force_refresh:
                raise
            if not _market_token_is_expired(current):
                return current
            return None

        if not _write_oauth_token_if_matches(current, refreshed):
            latest = _current_oauth_token_for_same_session_snapshot(current)
            if latest is not None:
                merged = dict(latest)
                for key in (
                    "access_token",
                    "refresh_token",
                    "token_type",
                    "scope",
                    "expires_at",
                    "auth_url",
                    "issuer",
                    "client_id",
                    "refresh_generation",
                    "updated_at",
                    "refreshed_at",
                ):
                    if key in refreshed:
                        merged[key] = refreshed[key]
                merged.pop("market_user_verified_at", None)
                if _write_oauth_token_if_matches(latest, merged):
                    return merged
            await _revoke_oauth_token_best_effort(refreshed)
            return None
        return refreshed


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to remove {}: {}", path, exc)


def _clear_oauth_session() -> None:
    _unlink_if_exists(_OAUTH_PENDING_FILE)
    _unlink_if_exists(_OAUTH_CALLBACK_FILE)


async def _require_active_oauth_session(
    expected_state: str,
    token_payload: dict[str, Any],
) -> None:
    """Reject a completion whose browser session was logged out or replaced."""

    current = _read_json_file(_OAUTH_PENDING_FILE)
    current_state = str(current.get("state") or "") if current else ""
    active = (
        bool(current_state)
        and secrets.compare_digest(current_state, expected_state)
        and time.time() <= float(current.get("expires_at") or 0)
    )
    if active:
        return
    await _revoke_oauth_token_best_effort(token_payload)
    raise HTTPException(status_code=409, detail="oauth_session_cancelled")


async def _exchange_oauth_code(
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            res = await client.post(
                f"{NEKO_AUTH_URL.rstrip('/')}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": _OAUTH_CLIENT_ID,
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPStatusError as exc:
        _log_auth_oauth_failure(
            "exchange",
            category=_auth_oauth_http_failure_category(exc.response.status_code),
            status=exc.response.status_code,
            request_id=exc.response.headers.get("x-request-id"),
            started_at=started_at,
        )
        raise HTTPException(status_code=400, detail="Auth OAuth token 交换失败") from exc
    except httpx.HTTPError as exc:
        _log_auth_oauth_failure(
            "exchange",
            category=_market_auth_network_failure_category(exc),
            status="unavailable",
            request_id=None,
            started_at=started_at,
        )
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc

    if not isinstance(data, dict) or not data.get("access_token"):
        raise HTTPException(status_code=502, detail="Auth OAuth token 响应无效")
    return data


async def _refresh_oauth_token(token_data: dict[str, Any]) -> dict[str, Any]:
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 refresh token")

    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            res = await client.post(
                f"{NEKO_AUTH_URL.rstrip('/')}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": _OAUTH_CLIENT_ID,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
            )
            res.raise_for_status()
            payload = res.json()
    except httpx.HTTPStatusError as exc:
        _log_auth_oauth_failure(
            "refresh",
            category=_auth_oauth_http_failure_category(exc.response.status_code),
            status=exc.response.status_code,
            request_id=exc.response.headers.get("x-request-id"),
            started_at=started_at,
        )
        if exc.response.status_code in {400, 401, 403}:
            raise HTTPException(status_code=401, detail="Auth refresh token 已失效") from exc
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc
    except httpx.HTTPError as exc:
        _log_auth_oauth_failure(
            "refresh",
            category=_market_auth_network_failure_category(exc),
            status="unavailable",
            request_id=None,
            started_at=started_at,
        )
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc

    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise HTTPException(status_code=502, detail="Auth refresh token 响应无效")

    now = time.time()
    expires_in = int(payload.get("expires_in") or 3600)
    refreshed = dict(token_data)
    refreshed.update(
        {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token") or refresh_token,
            "token_type": payload.get("token_type", token_data.get("token_type", "bearer")),
            "scope": payload.get("scope", token_data.get("scope", _OAUTH_SCOPE)),
            "expires_at": now + expires_in,
            "auth_url": _normalized_base_url(NEKO_AUTH_URL),
            "issuer": _auth_issuer(),
            "client_id": _OAUTH_CLIENT_ID,
            "refresh_generation": int(token_data.get("refresh_generation") or 0) + 1,
            "updated_at": now,
            "refreshed_at": now,
        }
    )
    refreshed.pop("market_user_verified_at", None)
    return refreshed


async def _revoke_oauth_token_best_effort(token_data: dict[str, Any]) -> None:
    tokens = [
        ("refresh_token", token_data.get("refresh_token")),
        ("access_token", token_data.get("access_token")),
    ]
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        for token_type_hint, token_value in tokens:
            if not isinstance(token_value, str) or not token_value:
                continue
            started_at = time.monotonic()
            try:
                response = await client.post(
                    f"{NEKO_AUTH_URL.rstrip('/')}/oauth2/revoke",
                    data={
                        "token": token_value,
                        "token_type_hint": token_type_hint,
                        "client_id": _OAUTH_CLIENT_ID,
                    },
                    headers={
                        "accept": "application/json",
                        "content-type": "application/x-www-form-urlencoded",
                    },
                )
                if response.status_code >= 400:
                    _log_auth_oauth_failure(
                        "revoke",
                        category=_auth_oauth_http_failure_category(
                            response.status_code
                        ),
                        status=response.status_code,
                        request_id=response.headers.get("x-request-id"),
                        started_at=started_at,
                        debug=True,
                    )
            except httpx.HTTPError as exc:
                _log_auth_oauth_failure(
                    "revoke",
                    category=_market_auth_network_failure_category(exc),
                    status="unavailable",
                    request_id=None,
                    started_at=started_at,
                    debug=True,
                )


def _unauthenticated_account_summary() -> MarketOAuthAccountSummaryResponse:
    return MarketOAuthAccountSummaryResponse(
        authenticated=False,
        sources={
            "auth": MarketOAuthAccountSource(status="unavailable"),
            "market": MarketOAuthAccountSource(status="unavailable"),
            # Community profile lookup requires a server-only Market token.
            "community": MarketOAuthAccountSource(status="unavailable"),
        },
    )


def _account_summary_for_invalidated_snapshot(
    snapshot: dict[str, Any],
) -> MarketOAuthAccountSummaryResponse:
    current = _current_oauth_token_for_invalidated_snapshot(snapshot)
    if current is None:
        return _unauthenticated_account_summary()
    return MarketOAuthAccountSummaryResponse(
        authenticated=True,
        sources={
            "auth": MarketOAuthAccountSource(status="unavailable"),
            "market": MarketOAuthAccountSource(status="unavailable"),
            "community": MarketOAuthAccountSource(status="unavailable"),
        },
        expires_at=current.get("expires_at"),
    )


def _account_summary_cache_key(token_data: dict[str, Any]) -> tuple[str, int]:
    return (
        str(token_data.get("subject") or ""),
        int(token_data.get("refresh_generation") or 0),
    )


def _fresh_account_summary(cache_key: tuple[str, int]) -> dict[str, Any] | None:
    cached = _ACCOUNT_SUMMARY_CACHE
    if not cached or cached.get("key") != cache_key:
        return None
    if float(cached.get("expires_at") or 0) <= time.time():
        return None
    payload = cached.get("payload")
    return payload if isinstance(payload, dict) else None


def _store_account_summary(
    cache_key: tuple[str, int],
    summary: MarketOAuthAccountSummaryResponse,
) -> None:
    global _ACCOUNT_SUMMARY_CACHE
    _ACCOUNT_SUMMARY_CACHE = {
        "key": cache_key,
        "expires_at": time.time() + _ACCOUNT_SUMMARY_TTL_SECONDS,
        "payload": summary.model_dump(mode="json"),
    }


def _clear_account_summary_cache() -> None:
    global _ACCOUNT_SUMMARY_CACHE
    _ACCOUNT_SUMMARY_CACHE = None


class _OAuthAccessTokenRejected(Exception):
    """Auth userinfo rejected a token that may need an early refresh."""


@dataclasses.dataclass(frozen=True)
class _MarketUserProbe:
    state: MarketOAuthState
    user: dict[str, Any] | None = None
    retryable: bool = False
    status_code: int | None = None


def _market_oauth_state_message(state: MarketOAuthState) -> str:
    return f"auth_login_complete:{state}"


async def _fetch_auth_userinfo(access_token: Any) -> dict[str, Any] | None:
    if not isinstance(access_token, str) or not access_token:
        return None
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            res = await client.get(
                f"{NEKO_AUTH_URL.rstrip('/')}/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if res.status_code != 200:
                headers = getattr(res, "headers", {})
                _log_auth_oauth_failure(
                    "userinfo",
                    category=_auth_oauth_http_failure_category(res.status_code),
                    status=res.status_code,
                    request_id=(
                        headers.get("x-request-id")
                        if hasattr(headers, "get")
                        else None
                    ),
                    started_at=started_at,
                )
            if res.status_code in {401, 403}:
                raise _OAuthAccessTokenRejected
            if res.status_code != 200:
                return None
            try:
                data = res.json()
            except (TypeError, ValueError):
                _log_auth_oauth_failure(
                    "userinfo",
                    category="invalid_response",
                    status=res.status_code,
                    request_id=(
                        res.headers.get("x-request-id")
                        if hasattr(res.headers, "get")
                        else None
                    ),
                    started_at=started_at,
                )
                return None
    except httpx.HTTPError as exc:
        _log_auth_oauth_failure(
            "userinfo",
            category=_market_auth_network_failure_category(exc),
            status="unavailable",
            request_id=None,
            started_at=started_at,
        )
        return None
    return data if isinstance(data, dict) else None


async def _fetch_current_market_user(token_data: dict[str, Any]) -> dict[str, Any] | None:
    cached = _fresh_cached_market_user(token_data)
    if cached is not None:
        return cached
    return await _fetch_market_user(token_data.get("access_token"))


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _build_account_summary(
    token_data: dict[str, Any],
    auth_user: dict[str, Any] | None,
    market_user: dict[str, Any] | None,
) -> MarketOAuthAccountSummaryResponse:
    auth_user = auth_user or {}
    market_user = market_user or {}
    account_summary = market_user.get("account_summary")
    if not isinstance(account_summary, dict):
        account_summary = {}

    # Market may later project a Community profile server-to-server.  The
    # desktop only reads that already-sanitized projection and never calls
    # Community or receives its privileged service credential.
    profile = MarketOAuthAccountProfile(
        display_name=(
            _optional_text(market_user.get("display_name"))
            or _optional_text(auth_user.get("name"))
        ),
        username=(
            _optional_text(auth_user.get("preferred_username"))
            or _optional_text(market_user.get("username"))
        ),
        avatar_url=(
            _optional_text(market_user.get("avatar_url"))
            or _optional_text(auth_user.get("picture"))
        ),
        login_method=_optional_text(auth_user.get("login_method_kind")),
    )
    market = MarketOAuthAccountMarket(
        member_days=_optional_nonnegative_int(account_summary.get("member_days")),
        published_plugins=_optional_nonnegative_int(account_summary.get("published_plugins")),
        installed_plugins=_optional_nonnegative_int(account_summary.get("installed_plugins")),
        total_downloads=_optional_nonnegative_int(account_summary.get("total_downloads")),
    )
    return MarketOAuthAccountSummaryResponse(
        authenticated=True,
        profile=profile,
        market=market,
        sources={
            "auth": MarketOAuthAccountSource(
                status="ready" if auth_user else "unavailable"
            ),
            "market": MarketOAuthAccountSource(
                status="ready" if market_user else "unavailable"
            ),
            "community": MarketOAuthAccountSource(status="unavailable"),
        },
        expires_at=token_data.get("expires_at"),
    )


async def _fetch_market_user(
    access_token: Any,
) -> dict[str, Any] | None:
    probe = await _probe_market_user(access_token)
    return probe.user


def _log_invalid_market_user_response(response: Any, started_at: float) -> None:
    headers = getattr(response, "headers", {})
    request_id = _safe_market_request_id(
        headers.get("x-request-id") if hasattr(headers, "get") else None
    )
    logger.warning(
        "[market-auth] Market user verification failed "
        "category=invalid_response status={} request_id={} elapsed_ms={} origin={}",
        getattr(response, "status_code", "unavailable"),
        request_id,
        max(0, round((time.monotonic() - started_at) * 1000)),
        _market_api_log_origin(),
    )


async def _probe_market_user(access_token: Any) -> _MarketUserProbe:
    if not isinstance(access_token, str) or not access_token:
        return _MarketUserProbe(state="invalid_response")
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            res = await client.get(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if res.status_code != 200:
                headers = getattr(res, "headers", {})
                request_id = _safe_market_request_id(
                    headers.get("x-request-id") if hasattr(headers, "get") else None
                )
                elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
                logger.warning(
                    "[market-auth] Market user verification failed "
                    "category={} status={} request_id={} elapsed_ms={} origin={}",
                    _market_auth_http_failure_category(res.status_code),
                    res.status_code,
                    request_id,
                    elapsed_ms,
                    _market_api_log_origin(),
                )
                if res.status_code == 401:
                    return _MarketUserProbe(
                        state="token_rejected",
                        status_code=res.status_code,
                    )
                if res.status_code == 403:
                    return _MarketUserProbe(
                        state="forbidden",
                        status_code=res.status_code,
                    )
                if res.status_code == 409:
                    return _MarketUserProbe(
                        state="identity_conflict",
                        status_code=res.status_code,
                    )
                if res.status_code not in {408, 429} and res.status_code < 500:
                    return _MarketUserProbe(
                        state="invalid_response",
                        status_code=res.status_code,
                    )
                return _MarketUserProbe(
                    state="unavailable",
                    retryable=True,
                    status_code=res.status_code,
                )
            try:
                data = res.json()
            except (TypeError, ValueError):
                _log_invalid_market_user_response(res, started_at)
                return _MarketUserProbe(
                    state="invalid_response",
                    status_code=res.status_code,
                )
    except httpx.HTTPError as exc:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-auth] Market user verification failed "
            "category={} status=unavailable request_id=unavailable "
            "elapsed_ms={} origin={}",
            _market_auth_network_failure_category(exc),
            elapsed_ms,
            _market_api_log_origin(),
        )
        return _MarketUserProbe(state="unavailable", retryable=True)
    if not isinstance(data, dict) or not _extract_subject(data):
        _log_invalid_market_user_response(res, started_at)
        return _MarketUserProbe(
            state="invalid_response",
            status_code=res.status_code,
        )
    return _MarketUserProbe(
        state="ready",
        user=data,
        status_code=res.status_code,
    )


async def _report_market_install_best_effort(
    payload: MarketInstallRequest,
    task: dict[str, Any],
) -> None:
    token_data = await _ensure_valid_oauth_token()
    if not token_data or not token_data.get("access_token"):
        return

    try:
        market_plugin_id = int(str(payload.plugin_id or ""))
    except ValueError:
        logger.debug(
            "Skip Market install report: plugin_id is not a Market numeric id: {}",
            payload.plugin_id,
        )
        return

    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    install = result.get("install") if isinstance(result, dict) else {}
    if not isinstance(install, dict):
        install = {}

    report_payload = {
        "plugin_id": market_plugin_id,
        "version": payload.version,
        "channel": payload.channel or install.get("channel"),
        "package_sha256": payload.package_sha256 or install.get("package_sha256"),
        "payload_hash": payload.payload_hash or install.get("payload_hash"),
        "installed_plugin_id": install.get("plugin_id") or payload.expected_plugin_toml_id,
        "client_id": _OAUTH_CLIENT_ID,
    }
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            res = await client.post(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/me/installs",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Content-Type": "application/json",
                },
                json=report_payload,
            )
            if res.status_code == 401:
                logger.info(
                    "[market-install-report] request rejected "
                    "category=credential_rejected status=401 request_id={} "
                    "elapsed_ms={} origin={}",
                    _safe_market_request_id(res.headers.get("x-request-id")),
                    max(0, round((time.monotonic() - started_at) * 1000)),
                    _market_api_log_origin(),
                )
                return
            res.raise_for_status()
            logger.info(
                "Market install reported plugin_id={} version={} status={}",
                market_plugin_id,
                payload.version or "",
                res.status_code,
            )
    except httpx.HTTPStatusError as exc:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-install-report] request failed "
            "category={} status={} request_id={} elapsed_ms={} origin={}",
            _market_auth_http_failure_category(exc.response.status_code),
            exc.response.status_code,
            _safe_market_request_id(exc.response.headers.get("x-request-id")),
            elapsed_ms,
            _market_api_log_origin(),
        )
    except httpx.HTTPError as exc:
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-install-report] request failed "
            "category={} status=unavailable request_id=unavailable "
            "elapsed_ms={} origin={}",
            _market_auth_network_failure_category(exc),
            elapsed_ms,
            _market_api_log_origin(),
        )


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
            await _do_upgrade(task, payload, log_ctx, record_as_reinstall=True)
        else:  # pragma: no cover — Pydantic Literal already enforces this
            raise _TaskError(
                code="invalid_mode",
                message=f"unknown mode: {payload.mode}",
            )
        await _report_market_install_best_effort(payload, task)
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
    task["stage"] = "completed"
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
    task["stage"] = task.get("stage") or "failed"
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
    "upgrade_rollback_incomplete": "升级失败，回滚未完整完成，请检查插件状态",
    "plugin_not_installed_for_upgrade": "该插件未安装，无法升级",
    "version_already_at_target": "当前已是目标版本",
    "lock_write_failed": "安装记录写入失败",
    "market_list_fetch_failed": "无法连接到 Market",
    "download_failed": "下载失败",
    "package_hash_mismatch": "插件包校验失败",
    "install_failed": "安装失败，已清理临时文件",
}


def _human_message_for(code: str) -> str:
    return _HUMAN_MESSAGES.get(code, "")


def _set_task_stage(
    task: dict[str, Any],
    *,
    status: str,
    stage: str,
    progress: float,
    message: str,
) -> None:
    task["status"] = status
    task["stage"] = stage
    task["progress"] = max(0.0, min(1.0, progress))
    task["message"] = message


# ─── install / upgrade flows ─────────────────────────────────────────


def _with_market_operation_status(
    result: dict[str, object],
    *,
    operation: Literal["install", "upgrade"],
    restarted: bool,
    rollback_status: str,
) -> dict[str, object]:
    normalized = {
        **result,
        "operation": operation,
        "restarted": restarted,
        "rollback_status": rollback_status,
    }
    install_result = normalized.get("install")
    if isinstance(install_result, dict):
        normalized["install"] = {
            **install_result,
            "operation": operation,
            "restarted": restarted,
            "rollback_status": rollback_status,
        }
    return normalized


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

    _set_task_stage(
        task,
        status="downloading",
        stage="download",
        progress=0.1,
        message="正在下载插件包...",
    )

    package_path: Path | None = None
    try:
        package_path = await _download_package(payload.package_url, task)
    except Exception as exc:
        raise _TaskError(code="download_failed", message=str(exc)) from exc

    try:
        try:
            sha_check = _verify_sha256_file(
                package_path,
                payload.package_sha256,
                task,
            )
        except ValueError as exc:
            raise _TaskError(code="package_hash_mismatch", message=str(exc)) from exc
        log_ctx["package_sha256_check"] = sha_check

        _set_task_stage(
            task,
            status="installing",
            stage="install",
            progress=0.8,
            message="正在安装插件...",
        )

        filename = _extract_filename(payload.package_url)
        market_override = _build_market_override(payload, mode="install")

        try:
            result = await _cli_service.upload_and_install(
                filename=filename,
                package_path=str(package_path),
                on_conflict=payload.on_conflict,
                install_source_override=market_override,
            )
        except InstallSourceError as exc:
            if exc.code == "lock_write_failed":
                raise _TaskError(
                    code="lock_write_failed",
                    message=str(exc.message),
                ) from exc
            raise _TaskError(code="internal_error", message=str(exc.message)) from exc
        except Exception as exc:
            raise _TaskError(code="install_failed", message=str(exc)) from exc
    finally:
        _cleanup_download_file(package_path)

    _post_install_payload_check(payload, result)
    result = _with_market_operation_status(
        result,
        operation="install",
        restarted=False,
        rollback_status="not_needed",
    )

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
    record_as_reinstall: bool = False,
) -> None:
    """Replace an installed Market plugin through the shared file transaction.

    Market owns artifact download, hash verification and source provenance.
    The shared replacement module owns stop, backup, deployment, restart and
    directory rollback, exactly as it does for locally imported packages.
    """

    requested_plugin_id = payload.plugin_id or ""
    expected_plugin_id = payload.expected_plugin_toml_id or requested_plugin_id

    mgr = get_install_source_manager()
    if mgr is None:
        raise _TaskError(
            code="plugin_not_installed_for_upgrade",
            message="install source manager not initialised",
        )

    entry = mgr.find_active_market_entry(expected_plugin_id)
    if entry is None:
        raise _TaskError(
            code="plugin_not_installed_for_upgrade",
            message=f"plugin {expected_plugin_id!r} has no active market lock entry",
            http_status=400,
        )
    installed_plugin_id = entry.plugin_id

    path_policy = PluginCliPathPolicy.from_settings()
    plugin_dir = (path_policy.user_plugins_root / entry.directory_name).resolve()
    package_path: Path | None = None
    try:
        _set_task_stage(
            task,
            status="downloading",
            stage="download",
            progress=0.1,
            message="正在下载新版本...",
        )
        try:
            package_path = await _download_package(payload.package_url, task)
        except Exception as exc:
            raise _TaskError(code="download_failed", message=str(exc)) from exc

        try:
            sha_check = _verify_sha256_file(
                package_path,
                payload.package_sha256,
                task,
            )
        except ValueError as exc:
            raise _TaskError(code="package_hash_mismatch", message=str(exc)) from exc
        log_ctx["package_sha256_check"] = sha_check

        try:
            inspected = await asyncio.to_thread(inspect_package, package_path)
        except Exception as exc:
            raise _TaskError(code="install_failed", message=str(exc)) from exc
        package_id = str(inspected.package_id).strip()
        if (
            not package_id
            or package_id in {".", ".."}
            or "/" in package_id
            or "\\" in package_id
        ):
            raise _TaskError(code="install_failed", message=f"invalid package id: {package_id!r}")

        installed_package_id = getattr(entry, "package_id", "") or installed_plugin_id
        if package_id != installed_package_id:
            raise _TaskError(
                code="package_id_change",
                message=(
                    "plugin identity mismatch: package id changes are not supported during replacement: "
                    f"installed={installed_package_id!r} incoming={package_id!r}"
                ),
            )
        profile_dir = (path_policy.package_profiles_root / package_id).resolve()
        market_override = _build_market_override(
            payload,
            mode="reinstall" if record_as_reinstall else "upgrade",
            directory_name=entry.directory_name,
        )

        source_write_attempted = False

        async def install_new() -> dict[str, object]:
            nonlocal source_write_attempted
            source_write_attempted = True
            try:
                return await _cli_service.upload_and_install(
                    filename=_extract_filename(payload.package_url),
                    package_path=str(package_path),
                    on_conflict="fail",
                    install_source_override=market_override,
                )
            except InstallSourceError as exc:
                raise RuntimeError(str(exc.message)) from exc

        async def validate_new() -> None:
            actual_plugin_id = _read_plugin_toml_id(plugin_dir / "plugin.toml")
            if actual_plugin_id and actual_plugin_id != installed_plugin_id:
                raise ValueError(
                    "installed plugin identity does not match the Market replacement target"
                )

        async def start(plugin_id: str) -> None:
            await start_plugin_after_upgrade(plugin_id, strict=True)

        _set_task_stage(
            task,
            status="installing",
            stage="replace",
            progress=0.8,
            message="正在写入新版本...",
        )
        task["rollback"] = {"prepared": True, "restored": False}
        try:
            replacement = await replace_plugin(
                layout=resolve_plugin_layout(installed_plugin_id, plugin_dir),
                install_new=install_new,
                validate_new=validate_new,
                is_running=plugin_is_running,
                stop=stop_plugin_for_upgrade,
                start=start,
                cleanup_backup=_async_remove_dir,
                additional_targets=(profile_dir,),
                preserve_targets=(profile_dir,),
            )
        except ReplacePluginError as exc:
            source_restored = True
            restore_source = getattr(mgr, "restore_entry_for_rollback", None)
            if source_write_attempted and callable(restore_source):
                try:
                    await asyncio.to_thread(restore_source, entry)
                except Exception as restore_exc:
                    source_restored = False
                    logger.error(
                        "market install source rollback failed plugin_id={} err={}",
                        installed_plugin_id,
                        restore_exc,
                    )
            rollback_ok = exc.rollback_status == "completed" and source_restored
            _set_task_stage(
                task,
                status="installing",
                stage="rollback",
                progress=0.9,
                message="安装失败，正在回滚...",
            )
            task["rollback"] = {
                "prepared": True,
                "restored": rollback_ok,
                "running": False,
            }
            raise _TaskError(
                code=("upgrade_rollback_completed" if rollback_ok else "upgrade_rollback_incomplete"),
                message=(
                    f"升级失败已回滚: {exc.cause}"
                    if rollback_ok
                    else f"升级失败且回滚未完整完成: {exc.cause}"
                ),
            ) from exc

        result = _with_market_operation_status(
            replacement.install_result,
            operation="upgrade",
            restarted=replacement.restarted,
            rollback_status=replacement.rollback_status,
        )
        task["rollback"] = {
            "prepared": True,
            "backup_dir": str(replacement.backup_dir),
            "restored": False,
        }

        task["progress"] = 1.0
        task["stage"] = "completed"
        task["message"] = "升级成功"
        task["result"] = result

        if isinstance(result, dict) and "install_source_warning" in result:
            task["install_source_warning"] = result["install_source_warning"]
    finally:
        _cleanup_download_file(package_path)


def _build_market_override(
    payload: MarketInstallRequest,
    *,
    mode: str,
    directory_name: str | None = None,
) -> dict[str, Any]:
    """Construct the ``install_source_override`` dict for upload_and_install.

    Caller's ``package_sha256`` is passed through verbatim — the CLI
    service will re-hash and overwrite it with the actual value, but
    for v1 lock entries that legitimately omit the field we want the
    caller-provided value to win when present.
    """

    override = {
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
    if directory_name:
        override["directory_name"] = directory_name
    return override


def _verify_sha256_file(
    path: Path,
    expected_hash: str | None,
    task: dict[str, Any],
) -> Literal["passed", "mismatch"]:
    """Verify sha256 from a downloaded file; raise ValueError on mismatch."""

    raw = _normalize_required_sha256(expected_hash)

    _set_task_stage(
        task,
        status="verifying",
        stage="verify",
        progress=0.7,
        message="正在校验文件完整性...",
    )

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != raw:
        raise ValueError(
            f"SHA256 校验失败\n  期望: {raw}\n  实际: {actual}"
        )
    return "passed"


def _verify_sha256(
    content: bytes,
    expected_hash: str | None,
    task: dict[str, Any],
) -> Literal["passed", "mismatch"]:
    """Verify sha256; raise ValueError on missing, invalid, or mismatch.

    Returns the structured-log status string for ``log_ctx``.
    """

    raw = _normalize_required_sha256(expected_hash)

    _set_task_stage(
        task,
        status="verifying",
        stage="verify",
        progress=0.7,
        message="正在校验文件完整性...",
    )

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


async def _async_remove_dir(target_dir: Path) -> None:
    """Async best-effort rmtree for backup cleanup."""

    try:
        await remove_directory(target_dir)
    except Exception as exc:  # pragma: no cover - platform-specific cleanup failure
        logger.warning("backup cleanup failed for {}: {}", target_dir, exc)
def _utc_iso_now() -> str:
    """Current UTC time in ISO 8601 with microsecond precision and ``Z`` suffix."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _download_package(url: str, task: dict[str, Any]) -> Path:
    """Download a plugin package to a temp file with progress updates."""

    started_at = time.monotonic()
    download_dir = PluginCliPathPolicy.from_settings().package_artifacts_root / ".downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix="neko-market-",
        suffix=".neko-plugin",
        dir=download_dir,
    )
    os.close(fd)
    package_path = Path(raw_path)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _DOWNLOAD_MAX_BYTES:
                    raise ValueError(
                        f"包文件过大: {int(content_length)} bytes "
                        f"(最大 {_DOWNLOAD_MAX_BYTES} bytes)"
                    )

                downloaded = 0
                total_bytes = int(content_length) if content_length else None
                task["total_bytes"] = total_bytes
                task["downloaded_bytes"] = 0

                with package_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        handle.write(chunk)
                        downloaded += len(chunk)
                        task["downloaded_bytes"] = downloaded

                        if downloaded > _DOWNLOAD_MAX_BYTES:
                            raise ValueError(
                                f"下载超过大小限制: {_DOWNLOAD_MAX_BYTES} bytes"
                            )

                        if total_bytes:
                            dl_progress = downloaded / total_bytes
                            task["progress"] = 0.1 + dl_progress * 0.6
                            task["message"] = (
                                f"正在下载: {_format_bytes(downloaded)}"
                                f" / {_format_bytes(total_bytes)}"
                            )
                        else:
                            task["progress"] = min(
                                0.65,
                                task.get("progress", 0.1) + 0.01,
                            )
                            task["message"] = (
                                f"正在下载: {_format_bytes(downloaded)}"
                            )

        return package_path
    except httpx.HTTPStatusError as exc:
        _cleanup_download_file(package_path)
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-download] request failed "
            "category={} status={} request_id={} elapsed_ms={} origin={}",
            _market_download_http_failure_category(exc.response.status_code),
            exc.response.status_code,
            _safe_market_request_id(exc.response.headers.get("x-request-id")),
            elapsed_ms,
            _safe_url_log_origin(url),
        )
        raise ValueError(f"下载失败: HTTP {exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        _cleanup_download_file(package_path)
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-download] request failed "
            "category=timeout status=unavailable request_id=unavailable "
            "elapsed_ms={} origin={}",
            elapsed_ms,
            _safe_url_log_origin(url),
        )
        raise ValueError("下载超时") from exc
    except httpx.RequestError as exc:
        _cleanup_download_file(package_path)
        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.warning(
            "[market-download] request failed "
            "category={} status=unavailable request_id=unavailable "
            "elapsed_ms={} origin={}",
            _market_auth_network_failure_category(exc),
            elapsed_ms,
            _safe_url_log_origin(url),
        )
        raise ValueError("下载网络错误") from exc
    except Exception:
        _cleanup_download_file(package_path)
        raise


def _cleanup_download_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("failed to remove downloaded package {}: {}", path, exc)


def _format_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}MB"
    if value >= 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value}B"


def _extract_filename(url: str) -> str:
    """从 URL 提取文件名。"""
    from urllib.parse import urlparse, unquote
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if "/" in path else "package.neko-plugin"
    # 确保有合法后缀
    if not any(name.endswith(s) for s in _ALLOWED_SUFFIXES):
        name = name + ".neko-plugin"
    return name
