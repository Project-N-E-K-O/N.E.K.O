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
from typing import Any, Awaitable, Callable, Iterable, Literal
from urllib.parse import urlparse, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.public import inspect_package
from plugin.server.application.install_source import (
    InstallSourceError,
    InstallSourceManager,
    LockEntry,
    SourceDetailMarket,
    classify_plugin_path,
    get_install_source_manager,
)
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugins.upgrade_support import (
    backup_path_for,
    merge_directory_contents,
    plugin_is_running,
    remove_directory,
    restore_directory,
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
_NEKO_STATE_DIR = Path.home() / ".neko"
_OAUTH_PENDING_FILE = _NEKO_STATE_DIR / "market_oauth_pending.json"
_OAUTH_CALLBACK_FILE = _NEKO_STATE_DIR / "oauth_callback.json"
_OAUTH_TOKEN_FILE = _NEKO_STATE_DIR / "market_auth.json"
_OAUTH_REFRESH_LOCK = asyncio.Lock()

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
    # 让前端识别稳定错误码（upgrade_rollback_completed / version_already_at_target / ...）。
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


class MarketOAuthStatusResponse(BaseModel):
    authenticated: bool
    user: dict[str, Any] | None = None
    expires_at: float | None = None
    market_web_url: str = ""


class MarketOAuthCompleteResponse(BaseModel):
    completed: bool
    authenticated: bool
    user: dict[str, Any] | None = None
    message: str = ""


class MarketOAuthLogoutResponse(BaseModel):
    message: str


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


@router.get("/oauth/callback", response_class=HTMLResponse)
async def market_oauth_callback(
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
    return HTMLResponse(
        """
        <!doctype html>
        <html lang="zh-CN">
          <head>
            <meta charset="utf-8" />
            <title>N.E.K.O Market 授权完成</title>
            <style>
              body {
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0f0f1a;
                color: #f8fafc;
                display: grid;
                min-height: 100vh;
                place-items: center;
                margin: 0;
              }
              main {
                max-width: 520px;
                padding: 32px;
                border: 1px solid rgba(148, 163, 184, 0.24);
                border-radius: 18px;
                background: rgba(26, 26, 46, 0.92);
                text-align: center;
              }
              p { color: #cbd5e1; line-height: 1.7; }
            </style>
          </head>
          <body>
            <main>
              <h1>Market 授权已完成</h1>
              <p>请回到 N.E.K.O 插件管理器，登录状态会在几秒内自动更新。</p>
              <p>这个页面现在可以关闭。</p>
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

    cached_user = _fresh_cached_market_user(token_data)
    if cached_user is not None:
        return MarketOAuthStatusResponse(
            authenticated=True,
            user=cached_user,
            expires_at=token_data.get("expires_at"),
            market_web_url=MARKET_WEB_URL,
        )

    user = await _fetch_market_user(token_data.get("access_token"))
    if user is None:
        return MarketOAuthStatusResponse(
            authenticated=False,
            expires_at=token_data.get("expires_at"),
            market_web_url=MARKET_WEB_URL,
        )
    subject = _extract_subject(user)
    if not subject:
        return MarketOAuthStatusResponse(
            authenticated=False,
            expires_at=token_data.get("expires_at"),
            market_web_url=MARKET_WEB_URL,
        )
    token_data["user"] = user
    token_data["subject"] = subject
    token_data["market_api_url_last_verified"] = _normalized_base_url(MARKET_API_URL)
    token_data["market_user_verified_at"] = time.time()
    token_data["updated_at"] = time.time()
    _write_private_json(_OAUTH_TOKEN_FILE, token_data)

    return MarketOAuthStatusResponse(
        authenticated=True,
        user=user,
        expires_at=token_data.get("expires_at"),
        market_web_url=MARKET_WEB_URL,
    )


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
    user = await _fetch_market_user(token_payload.get("access_token"))
    if user is None:
        _clear_oauth_session()
        raise HTTPException(status_code=401, detail="Market 未接受当前 Auth token")
    subject = _extract_subject(user)
    if not subject:
        _clear_oauth_session()
        raise HTTPException(status_code=502, detail="Market 用户信息缺少 subject")
    expires_in = int(token_payload.get("expires_in") or 3600)
    now = time.time()
    stored = {
        "access_token": token_payload.get("access_token"),
        "refresh_token": token_payload.get("refresh_token"),
        "token_type": token_payload.get("token_type", "bearer"),
        "scope": token_payload.get("scope", _OAUTH_SCOPE),
        "expires_at": now + expires_in,
        "auth_url": _normalized_base_url(NEKO_AUTH_URL),
        "issuer": _auth_issuer(),
        "subject": subject,
        "client_id": _OAUTH_CLIENT_ID,
        "refresh_generation": 0,
        "market_api_url": _normalized_base_url(MARKET_API_URL),
        "market_api_url_last_verified": _normalized_base_url(MARKET_API_URL),
        "market_user_verified_at": now,
        "user": user,
        "created_at": now,
        "updated_at": now,
    }
    _write_private_json(_OAUTH_TOKEN_FILE, stored)
    _clear_oauth_session()

    return MarketOAuthCompleteResponse(
        completed=True,
        authenticated=True,
        user=user,
        message="Auth 登录成功",
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
    if token_data:
        await _revoke_oauth_token_best_effort(token_data)
    _unlink_if_exists(_OAUTH_TOKEN_FILE)
    _unlink_if_exists(_OAUTH_PENDING_FILE)
    _unlink_if_exists(_OAUTH_CALLBACK_FILE)
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


def _oauth_token_provenance_matches(token_data: dict[str, Any]) -> bool:
    if token_data.get("auth_url") != _normalized_base_url(NEKO_AUTH_URL):
        return False
    if token_data.get("issuer") != _auth_issuer():
        return False
    if token_data.get("client_id") != _OAUTH_CLIENT_ID:
        return False
    if not isinstance(token_data.get("subject"), str) or not token_data["subject"].strip():
        return False
    try:
        int(token_data.get("refresh_generation"))
    except (TypeError, ValueError):
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


async def _ensure_valid_oauth_token() -> dict[str, Any] | None:
    token_data = _read_json_file(_OAUTH_TOKEN_FILE)
    if not token_data or not token_data.get("access_token"):
        return None
    if not _oauth_token_provenance_matches(token_data):
        logger.info("Skip saved Auth token: auth_url/client_id provenance mismatch")
        _unlink_if_exists(_OAUTH_TOKEN_FILE)
        return None
    if not _market_token_expires_soon(token_data):
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
        if not _market_token_expires_soon(current):
            return current
        if not current.get("refresh_token"):
            _unlink_if_exists(_OAUTH_TOKEN_FILE)
            return None

        try:
            refreshed = await _refresh_oauth_token(current)
        except HTTPException as exc:
            logger.info("Auth token refresh failed: {}", exc.detail)
            if exc.status_code == 401:
                _unlink_if_exists(_OAUTH_TOKEN_FILE)
                return None
            if not _market_token_is_expired(current):
                return current
            return None

        _write_private_json(_OAUTH_TOKEN_FILE, refreshed)
        return refreshed


def _split_version(value: str) -> tuple[list[int], list[str]]:
    cleaned = (value or "").lstrip("vV").split("+", 1)[0]
    core_part, _, pre_part = cleaned.partition("-")
    core = [int(seg) if seg.isdigit() else 0 for seg in core_part.split(".") if seg != ""]
    pre = pre_part.split(".") if pre_part else []
    return core, pre


def _compare_version(a: str, b: str) -> int:
    """Return -1/0/1 if ``a`` < / == / > ``b`` (mirrors frontend ``compareVersion``).

    Implements semver §11.4 rules: numeric core compared segment-wise,
    no-prerelease > with-prerelease, shorter prerelease prefix wins on
    equal prefixes, numeric prerelease segments sort before alphabetic.
    """

    core_a, pre_a = _split_version(a)
    core_b, pre_b = _split_version(b)
    for index in range(max(len(core_a), len(core_b))):
        left = core_a[index] if index < len(core_a) else 0
        right = core_b[index] if index < len(core_b) else 0
        if left != right:
            return -1 if left < right else 1
    if not pre_a and not pre_b:
        return 0
    if not pre_a:
        return 1
    if not pre_b:
        return -1
    for index in range(max(len(pre_a), len(pre_b))):
        if index >= len(pre_a):
            return -1
        if index >= len(pre_b):
            return 1
        seg_a, seg_b = pre_a[index], pre_b[index]
        a_num = seg_a.isdigit()
        b_num = seg_b.isdigit()
        if a_num and b_num:
            na, nb = int(seg_a), int(seg_b)
            if na != nb:
                return -1 if na < nb else 1
        elif a_num:
            return -1
        elif b_num:
            return 1
        elif seg_a != seg_b:
            return -1 if seg_a < seg_b else 1
    return 0


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


async def _exchange_oauth_code(
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
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
        detail = exc.response.text
        logger.warning("Auth OAuth token exchange rejected: {}", detail)
        raise HTTPException(status_code=400, detail="Auth OAuth token 交换失败") from exc
    except httpx.HTTPError as exc:
        logger.warning("Auth OAuth token exchange failed: {}", exc)
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc

    if not isinstance(data, dict) or not data.get("access_token"):
        raise HTTPException(status_code=502, detail="Auth OAuth token 响应无效")
    return data


async def _refresh_oauth_token(token_data: dict[str, Any]) -> dict[str, Any]:
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 refresh token")

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
        logger.warning("Auth refresh rejected: {}", exc.response.text)
        if exc.response.status_code in {400, 401, 403}:
            raise HTTPException(status_code=401, detail="Auth refresh token 已失效") from exc
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc
    except httpx.HTTPError as exc:
        logger.warning("Auth refresh failed: {}", exc)
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
            try:
                await client.post(
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
            except httpx.HTTPError as exc:
                logger.debug("Auth token revoke failed for {}: {}", token_type_hint, exc)


async def _fetch_market_user(access_token: Any) -> dict[str, Any] | None:
    if not isinstance(access_token, str) or not access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            res = await client.get(
                f"{MARKET_API_URL.rstrip('/')}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if res.status_code != 200:
                return None
            data = res.json()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Market user after OAuth login: {}", exc)
        return None
    return data if isinstance(data, dict) else None


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
                logger.info("Market install report rejected: saved token is unauthorized")
                return
            res.raise_for_status()
            logger.info(
                "Market install reported plugin_id={} version={} status={}",
                market_plugin_id,
                payload.version or "",
                res.status_code,
            )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Market install report failed status={} body={}",
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.HTTPError as exc:
        logger.warning("Market install report failed: {}", exc)


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

    requested_plugin_id = payload.plugin_id or ""
    target_version = payload.version or ""
    expected_plugin_id = payload.expected_plugin_toml_id or requested_plugin_id

    # Step 1: probe active lock entry.
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

    # Step 2: version-ordering guard (skipped for reinstall).
    #
    # Upgrade requests must advance the version. Without comparing values the
    # old equality check let a stable target downgrade an installed beta
    # (e.g. installed=2.0.0-beta, target=1.9.0) through the backup/unpack
    # path and recorded it as an upgrade. ``_compare_version`` follows the
    # same semver §11.4 rules as the frontend ``compareVersion`` helper so
    # the gate is consistent across both sides.
    current_version = ""
    if isinstance(entry.source_detail, SourceDetailMarket):
        current_version = entry.source_detail.version
    if not allow_same_version and current_version:
        order = _compare_version(target_version, current_version)
        if order == 0:
            raise _TaskError(
                code="version_already_at_target",
                message=(
                    f"plugin {installed_plugin_id!r} is already at version {target_version!r}"
                ),
            )
        if order < 0:
            raise _TaskError(
                code="upgrade_target_not_greater",
                message=(
                    f"upgrade target {target_version!r} is not greater than "
                    f"installed {current_version!r}"
                ),
            )

    path_policy = PluginCliPathPolicy.from_settings()
    plugin_dir = (path_policy.user_plugins_root / entry.directory_name).resolve()
    backup_dir = backup_path_for(plugin_dir)
    rollback_steps: list[Callable[[], Awaitable[None]]] = []
    was_running = await plugin_is_running(installed_plugin_id)

    # Step 3: lifecycle stop.
    if was_running:
        _set_task_stage(
            task,
            status="installing",
            stage="stop_old",
            progress=0.05,
            message="正在停止旧版本插件...",
        )
        await stop_plugin_for_upgrade(installed_plugin_id)

    # Step 4: rename old dir → backup.
    try:
        _set_task_stage(
            task,
            status="installing",
            stage="backup_old",
            progress=0.08,
            message="正在备份旧版本...",
        )
        await asyncio.to_thread(backup_dir.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(os.rename, plugin_dir, backup_dir)
        rollback_steps.append(_make_restore_dir_step(backup_dir, plugin_dir))
        task["rollback"] = {
            "prepared": True,
            "backup_dir": str(backup_dir),
            "restored": False,
        }
    except OSError as exc:
        rollback_ok = await _run_rollback(
            task if rollback_steps else None,
            rollback_steps,
            was_running,
            installed_plugin_id,
        )
        raise _TaskError(
            code=("upgrade_rollback_completed" if rollback_ok else "upgrade_rollback_incomplete"),
            message=f"无法备份旧目录: {exc}",
        ) from exc

    try:
        # Step 5: download + verify sha256.
        _set_task_stage(
            task,
            status="downloading",
            stage="download",
            progress=0.1,
            message="正在下载新版本...",
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
                raise _TaskError(
                    code="package_hash_mismatch",
                    message=str(exc),
                ) from exc
            log_ctx["package_sha256_check"] = sha_check

            inspected = await asyncio.to_thread(inspect_package, package_path)
            package_id = str(inspected.package_id).strip()
            if (
                not package_id
                or package_id in {".", ".."}
                or "/" in package_id
                or "\\" in package_id
            ):
                raise _TaskError(
                    code="install_failed",
                    message=f"invalid package id: {package_id!r}",
                )
            # Legacy rows have no trustworthy package/profile key. Do not use
            # an incoming-named directory as proof of ownership: it may be
            # stale or belong to another package. Historical single-plugin
            # packages used plugin_id as package_id, so ambiguous renames are
            # rejected against that conservative baseline.
            installed_package_id = getattr(entry, "package_id", "") or installed_plugin_id
            if package_id != installed_package_id:
                raise _TaskError(
                    code="package_id_change",
                    message=(
                        "plugin identity mismatch: package id changes are not supported during upgrade: "
                        f"installed={installed_package_id!r} incoming={package_id!r}"
                    ),
                )
            profile_dir = (path_policy.package_profiles_root / package_id).resolve()
            profile_backup_dir = backup_path_for(profile_dir)
            if profile_dir.exists():
                await asyncio.to_thread(profile_backup_dir.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(os.rename, profile_dir, profile_backup_dir)
                rollback_steps.append(_make_restore_dir_step(profile_backup_dir, profile_dir))
                task["rollback"]["profile_backup_dir"] = str(profile_backup_dir)

            # Step 6: unpack + record_market_upgrade (single atomic call).
            _set_task_stage(
                task,
                status="installing",
                stage="install",
                progress=0.8,
                message="正在写入新版本...",
            )

            market_override = _build_market_override(
                payload,
                mode="reinstall" if allow_same_version else "upgrade",
                directory_name=entry.directory_name,
            )

            try:
                result = await _cli_service.upload_and_install(
                    filename=_extract_filename(payload.package_url),
                    package_path=str(package_path),
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
        finally:
            _cleanup_download_file(package_path)

        rollback_steps.append(_make_remove_dir_step(plugin_dir))
        rollback_steps.append(_make_remove_dir_step(profile_dir))

        if profile_backup_dir.exists():
            await merge_directory_contents(profile_backup_dir, profile_dir)

        # Step 7: lifecycle start.
        if was_running:
            _set_task_stage(
                task,
                status="installing",
                stage="restart",
                progress=0.92,
                message="正在启动新版本...",
            )
            await start_plugin_after_upgrade(installed_plugin_id, strict=True)

        # Step 8: async cleanup of backup.
        for cleanup_label, cleanup_dir in (
            ("plugin", backup_dir),
            ("profile", profile_backup_dir),
        ):
            if cleanup_dir.exists():
                asyncio.create_task(
                    _async_remove_dir(cleanup_dir),
                    name=f"market-upgrade-cleanup-{cleanup_label}-{installed_plugin_id}",
                )

        task["progress"] = 1.0
        task["stage"] = "completed"
        task["message"] = "升级成功"
        task["result"] = result

        if isinstance(result, dict) and "install_source_warning" in result:
            task["install_source_warning"] = result["install_source_warning"]

    except _TaskError as exc:
        rollback_ok = await _run_rollback(task, rollback_steps, was_running, installed_plugin_id)
        if rollback_steps and exc.code not in (
            "version_already_at_target",
            "plugin_not_installed_for_upgrade",
        ):
            raise _TaskError(
                code=("upgrade_rollback_completed" if rollback_ok else "upgrade_rollback_incomplete"),
                message=(
                    f"升级失败已回滚: {exc.message}"
                    if rollback_ok
                    else f"升级失败且回滚未完整完成: {exc.message}"
                ),
            ) from exc
        raise
    except Exception as exc:
        # Other (network / sha256 / unpack) failures collapse into one code.
        rollback_ok = await _run_rollback(task, rollback_steps, was_running, installed_plugin_id)
        raise _TaskError(
            code=("upgrade_rollback_completed" if rollback_ok else "upgrade_rollback_incomplete"),
            message=(
                f"升级失败已回滚: {exc}"
                if rollback_ok
                else f"升级失败且回滚未完整完成: {exc}"
            ),
        ) from exc


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


# ─── lifecycle / rollback helpers ─────────────────────────────────────


def _make_restore_dir_step(
    backup_dir: Path,
    target_dir: Path,
) -> Callable[[], Awaitable[None]]:
    """Build a rollback step that renames ``backup_dir`` back to ``target_dir``."""

    async def _step() -> None:
        await restore_directory(backup_dir, target_dir)

    return _step


def _make_remove_dir_step(target_dir: Path) -> Callable[[], Awaitable[None]]:
    """Build a rollback step that removes a directory, ignoring missing.

    Used for the *new* directory after upload_and_install succeeds; if a
    later step (lifecycle start) fails we rmtree the new dir to make room
    for the backup-restore step to rename the old one back.
    """

    async def _step() -> None:
        await remove_directory(target_dir)

    return _step


async def _async_remove_dir(target_dir: Path) -> None:
    """Async best-effort rmtree for backup cleanup."""

    try:
        await remove_directory(target_dir)
    except Exception as exc:  # pragma: no cover - platform-specific cleanup failure
        logger.warning("backup cleanup failed for {}: {}", target_dir, exc)


async def _run_rollback(
    task: dict[str, Any] | None,
    rollback_steps: list[Callable[[], Awaitable[None]]],
    was_running: bool,
    plugin_id: str,
) -> bool:
    """Execute rollback steps in reverse order, then re-start old plugin.

    Each step is wrapped in try/except so one failure does not stop the
    rest from running. The returned value includes the non-strict restart
    result so callers never report a complete rollback when the old plugin
    did not resume running.
    """

    if task is not None:
        _set_task_stage(
            task,
            status="installing",
            stage="rollback",
            progress=0.9,
            message="安装失败，正在回滚...",
        )
        rollback_info = dict(task.get("rollback") or {})
        rollback_info["running"] = True
        rollback_info["restored"] = False
        task["rollback"] = rollback_info

    rollback_ok = True
    for step in reversed(rollback_steps):
        try:
            await step()
        except Exception as exc:
            rollback_ok = False
            logger.error(
                "rollback step failed plugin_id={} err={}",
                plugin_id,
                exc,
            )
    if was_running:
        restarted = await start_plugin_after_upgrade(plugin_id, strict=False)
        rollback_ok = rollback_ok and restarted
    if task is not None:
        rollback_info = dict(task.get("rollback") or {})
        rollback_info["running"] = False
        rollback_info["restored"] = rollback_ok
        task["rollback"] = rollback_info
    return rollback_ok


def _utc_iso_now() -> str:
    """Current UTC time in ISO 8601 with microsecond precision and ``Z`` suffix."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _download_package(url: str, task: dict[str, Any]) -> Path:
    """Download a plugin package to a temp file with progress updates."""

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
        raise ValueError(f"下载失败: HTTP {exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        _cleanup_download_file(package_path)
        raise ValueError("下载超时") from exc
    except httpx.RequestError as exc:
        _cleanup_download_file(package_path)
        raise ValueError(f"下载网络错误: {exc}") from exc
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
