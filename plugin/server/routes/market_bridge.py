"""Market Bridge — 本地客户端与插件市场的双向联动协议。

提供以下能力：
1. Market 前端探测本地客户端状态
2. Market 前端触发插件安装（从 URL 下载 → 校验 → 安装）
3. 查询本地已安装插件列表（供 Market 标记已安装状态）
4. 安装任务进度查询
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from plugin.logging_config import get_logger
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.settings import MARKET_URL

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
    """从 Market 触发安装的请求。"""
    package_url: str = Field(..., description="插件包下载 URL")
    package_sha256: str | None = Field(
        default=None,
        description="包文件 SHA256（可选）。为空或全 0 时跳过校验（仅用于 Market 尚未生成 hash 的场景）。",
    )
    payload_hash: str | None = Field(None, description="可选的 payload hash 二次校验")
    plugin_id: str | None = Field(None, description="Market 侧的插件标识")
    version: str | None = Field(None, description="版本号")
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
    error: str | None = None
    created_at: float = 0.0
    completed_at: float | None = None


class MarketInstalledPlugin(BaseModel):
    plugin_id: str
    path: str


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
    """
    _verify_token(token)

    task_id = secrets.token_urlsafe(16)
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0.0,
        "message": "任务已创建",
        "result": None,
        "error": None,
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
    """查询本地已安装的插件列表。"""
    _verify_token(token)

    try:
        result = await _cli_service.list_local_plugins()
        plugins = result.get("plugins", [])
        # 构建已安装列表
        from plugin.settings import PLUGIN_CONFIG_ROOTS
        installed = []
        for plugin_id in plugins:
            # 查找插件实际路径
            for root in PLUGIN_CONFIG_ROOTS:
                plugin_dir = root / plugin_id
                if plugin_dir.is_dir():
                    installed.append(MarketInstalledPlugin(
                        plugin_id=plugin_id,
                        path=str(plugin_dir),
                    ))
                    break
        return MarketInstalledResponse(installed=installed, count=len(installed))
    except Exception as exc:
        logger.warning("Failed to list installed plugins: {}", exc)
        return MarketInstalledResponse(installed=[], count=0)


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
    """异步执行下载 + 校验 + 安装流程。"""
    task = _tasks[task_id]

    try:
        # 1. 下载
        task["status"] = "downloading"
        task["progress"] = 0.1
        task["message"] = f"正在下载: {payload.package_url}"

        content = await _download_package(payload.package_url, task)

        # 2. 校验 SHA256（可选）
        expected_hash = (payload.package_sha256 or "").strip().lower()
        skip_hash_check = (
            not expected_hash
            or expected_hash == "0" * 64
            or len(expected_hash) != 64
            or not all(c in "0123456789abcdef" for c in expected_hash)
        )

        if skip_hash_check:
            logger.warning(
                "Market install skipping SHA256 verification for {} "
                "(no hash provided by Market)",
                payload.plugin_id or payload.package_url,
            )
            task["message"] = "跳过 SHA256 校验（Market 未提供）"
        else:
            task["status"] = "verifying"
            task["progress"] = 0.7
            task["message"] = "正在校验文件完整性..."

            actual_hash = hashlib.sha256(content).hexdigest().lower()
            if actual_hash != expected_hash:
                raise ValueError(
                    f"SHA256 校验失败\n"
                    f"  期望: {expected_hash}\n"
                    f"  实际: {actual_hash}"
                )

        # 3. 安装
        task["status"] = "installing"
        task["progress"] = 0.8
        task["message"] = "正在安装插件..."

        # 推断文件名
        filename = _extract_filename(payload.package_url)
        result = await _cli_service.upload_and_install(
            filename=filename,
            content=content,
            on_conflict=payload.on_conflict,
        )

        # 4. 可选：校验 payload_hash
        if payload.payload_hash:
            install_result = result.get("install", {})
            installed_payload_hash = install_result.get("payload_hash", "")
            if installed_payload_hash and installed_payload_hash.lower() != payload.payload_hash.lower():
                logger.warning(
                    "Payload hash mismatch after install: expected={}, got={}",
                    payload.payload_hash,
                    installed_payload_hash,
                )
                # 不阻断安装，只记录警告

        # 5. 完成
        task["status"] = "completed"
        task["progress"] = 1.0
        task["message"] = "安装成功"
        task["result"] = result
        task["completed_at"] = time.time()

    except Exception as exc:
        task["status"] = "failed"
        task["progress"] = 0.0
        task["error"] = str(exc)
        task["message"] = f"安装失败: {exc}"
        task["completed_at"] = time.time()
        logger.error("Market install task {} failed: {}", task_id, exc)


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
