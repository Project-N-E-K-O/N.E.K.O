# -*- coding: utf-8 -*-
"""
奇遇铸造机 — 本地 facts 抽取 + 故事生成服务器
端口: 3001
启动:
    uv run python local_server/card_forge_server/server.py
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import platform
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger("card_forge_server")
SERVER_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.card_forge_facts import (  # noqa: E402
    _safe_character_segment,
    build_forge_facts_payload,
    resolve_active_neko_context,
)

# ---------------------------------------------------------------------------
# 应用与 CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="NEKO 奇遇铸造机服务器", version="0.1.0")


def _resolve_cors_origins() -> list[str]:
    """允许跨域的来源白名单。

    默认只放行本机 card-forge 开发前端 (127.0.0.1:5173 / localhost:5173)。
    facts / story 接口会返回个人化的猫娘记忆，所以不能像旧版那样 allow_origins=["*"]
    —— 那样任何浏览器站点都能在用户登录态下读到响应。如需添加额外来源，
    可设置 NEKO_CARD_FORGE_ALLOWED_ORIGINS 环境变量（逗号分隔），或显式置
    为 "*" 以恢复旧行为（不推荐）。
    """
    raw = os.environ.get("NEKO_CARD_FORGE_ALLOWED_ORIGINS", "").strip()
    if not raw:
        # 只放行本机 card-forge 前端 (5173)。/forge/facts 返回个人化本地记忆，
        # 因此不要默认放行社区服务或其它常见本机端口；如需添加额外来源，由
        # NEKO_CARD_FORGE_ALLOWED_ORIGINS 显式加入。
        return [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    return [item.strip() for item in raw.split(",") if item.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# 奇遇铸造机 — facts.json 只读（与 FactStore 同 schema，不改 NEKO 核心）
# ---------------------------------------------------------------------------


async def _resolve_active_facts_context(
    character: Optional[str] = None,
    runtime_character_hint: Optional[str] = None,
):
    # 默认必须跟随 NEKO 当前猫娘。character 只作为显式调试开关保留，避免旧前端
    # 或大厅展示名误导铸造机读取另一只猫娘的 facts。
    allow_override = os.environ.get("NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE", "").strip() == "1"
    return await resolve_active_neko_context(
        character if allow_override else None,
        runtime_character_hint,
    )




def _is_avatar_data_url(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("data:image/")
        and "," in value
        and len(value) > 128
    )


_CARD_FACE_DATA_URL_CACHE: dict[str, Any] = {
    "name": "",
    "path": "",
    "mtime": 0.0,
    "dataUrl": "",
}


def _config_manager_path(attribute: str) -> Path | None:
    """Resolve a platform-aware user-data path from NEKO's ConfigManager."""
    try:
        from utils.config_manager import get_config_manager

        value = getattr(get_config_manager(), attribute, None)
        return Path(value) if value else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("card-forge: cannot resolve ConfigManager.%s: %s", attribute, exc)
        return None


def _read_active_character_config_snapshot() -> dict[str, str]:
    """Read the current character name directly from the local NEKO config."""
    candidates: list[Path] = []
    env_config_dir = os.environ.get("NEKO_USER_CONFIG_DIR", "").strip()
    if env_config_dir:
        candidates.append(Path(env_config_dir) / "characters.json")
    config_dir = _config_manager_path("config_dir")
    if config_dir is not None:
        candidates.append(config_dir / "characters.json")
    candidates.extend(
        [
            Path.home() / "Library/Application Support/N.E.K.O/config/characters.json",
            PROJECT_ROOT / "config" / "characters.json",
        ]
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("当前猫娘")
        master_obj = data.get("主人")
        master = ""
        if isinstance(master_obj, dict):
            master = str(master_obj.get("档案名") or master_obj.get("name") or "")
        if isinstance(name, str) and name.strip():
            return {"name": name.strip(), "master_name": master.strip()}
    return {}


def _read_card_face_data_url(name: str) -> str:
    """Return the persisted card face as a data URL for the active character."""
    path = _find_card_face_path(name)
    if not path:
        return ""
    stat = path.stat()
    cache_path = str(path)
    if (
        _CARD_FACE_DATA_URL_CACHE.get("name") == name
        and _CARD_FACE_DATA_URL_CACHE.get("path") == cache_path
        and float(_CARD_FACE_DATA_URL_CACHE.get("mtime") or 0.0) == stat.st_mtime
    ):
        return str(_CARD_FACE_DATA_URL_CACHE.get("dataUrl") or "")
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"
    _CARD_FACE_DATA_URL_CACHE.update(
        {
            "name": name,
            "path": cache_path,
            "mtime": stat.st_mtime,
            "dataUrl": data_url,
        }
    )
    return data_url


def _main_server_port_config_path() -> Path:
    """Return Electron's platform-specific runtime port configuration path."""
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "N.E.K.O" / "port_config.json"


def _read_main_server_port_config() -> int | None:
    """Read MAIN_SERVER_PORT written by Electron, ignoring invalid snapshots."""
    try:
        payload = json.loads(
            _main_server_port_config_path().read_text(encoding="utf-8")
        )
        raw = payload.get("MAIN_SERVER_PORT") if isinstance(payload, dict) else None
        candidate = int(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return candidate if 1 <= candidate <= 65535 else None


def _resolve_main_server_active_character_url() -> str:
    """Resolve the active-character endpoint using the main-server port precedence."""
    override = os.environ.get("NEKO_MAIN_ACTIVE_CHARACTER_URL", "").strip()
    if override:
        return override

    port: int | None = None
    for key in ("NEKO_MAIN_SERVER_PORT", "MAIN_SERVER_PORT"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            candidate = int(raw)
        except ValueError:
            continue
        if 1 <= candidate <= 65535:
            port = candidate
            break
    if port is None:
        port = _read_main_server_port_config() or 48911
    return f"http://127.0.0.1:{port}/card-forge/active-character"


def _read_main_server_active_character_snapshot(include_avatar: bool = False) -> dict[str, str]:
    """Read the runtime-synced active character snapshot from the main NEKO server."""
    base_url = _resolve_main_server_active_character_url()
    parsed = urlsplit(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if include_avatar:
        query["include_avatar"] = "true"
    url = urlunsplit(parsed._replace(query=urlencode(query)))
    try:
        with urllib.request.urlopen(url, timeout=0.8) as response:
            if response.status >= 400:
                return {}
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "name": str(payload.get("name") or ""),
        "dataUrl": str(payload.get("dataUrl") or ""),
        "characterReferenceDataUrl": str(
            payload.get("characterReferenceDataUrl")
            or payload.get("character_reference_data_url")
            or ""
        ),
    }


def _read_main_server_character_reference_data_url() -> str:
    """Return the full-body character reference cached by the main NEKO server."""
    data_url = _read_main_server_active_character_snapshot(include_avatar=True).get(
        "characterReferenceDataUrl", ""
    )
    return data_url if _is_avatar_data_url(data_url) else ""


def _card_face_dirs() -> list[Path]:
    candidates: list[Path] = []
    env_config_dir = os.environ.get("NEKO_USER_CONFIG_DIR", "").strip()
    if env_config_dir:
        candidates.append(Path(env_config_dir).parent / "card_faces")
    card_faces_dir = _config_manager_path("card_faces_dir")
    if card_faces_dir is not None:
        candidates.append(card_faces_dir)
    candidates.extend(
        [
            Path.home() / "Library/Application Support/N.E.K.O/card_faces",
            PROJECT_ROOT / "card_faces",
        ]
    )
    return list(dict.fromkeys(candidates))


def _find_card_face_path(name: str) -> Path | None:
    safe_name = _safe_character_segment(name)
    if not safe_name:
        return None
    suffixes = (".png", ".jpg", ".jpeg", ".webp")
    for base_dir in _card_face_dirs():
        for suffix in suffixes:
            path = base_dir / f"{safe_name}{suffix}"
            if path.is_file():
                return path
    return None


def _card_face_avatar_url(name: str) -> str:
    path = _find_card_face_path(name)
    if not path:
        return ""
    try:
        version = str(int(path.stat().st_mtime))
    except OSError:
        version = str(int(time.time()))
    return f"/forge/active-character/avatar?name={quote(name)}&v={version}"


# 与 forge_story_generator._FORGE_SENSITIVE_FIELDS 对齐:route 层 log 里只有
# storyLead 来源敏感(其余是 id/provider/model/elapsedMs 之类的元数据)。
# 仍然单独维护一份,避免 server.py 反向依赖 generator 的内部实现细节。
_FORGE_ROUTE_SENSITIVE_FIELDS = frozenset({"storyLead"})
_FORGE_ROUTE_LOG_PREVIEW_CHARS = 40


def _mask_route_sensitive(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    clipped = text[:_FORGE_ROUTE_LOG_PREVIEW_CHARS]
    suffix = "…" if len(text) > _FORGE_ROUTE_LOG_PREVIEW_CHARS else ""
    return f"{clipped}{suffix}(len={len(text)})"


def _forge_route_log(request_id: str, event: str, **fields: Any) -> None:
    """Print forge-card diagnostics to the server console only; no local log file.

    敏感字段 (storyLead) 在打印前先脱敏 —— 个人化记忆不能整段进控制台日志,
    跟 forge_story_generator._forge_log 行为保持一致。
    """

    safe_fields = {
        key: (_mask_route_sensitive(value) if key in _FORGE_ROUTE_SENSITIVE_FIELDS else value)
        for key, value in fields.items()
    }
    print(
        f"[forge-card-story][{request_id}][route.{event}] "
        f"{json.dumps(safe_fields, ensure_ascii=False, default=str)}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.get("/forge/facts")
async def arena_forge_facts(
    character: Optional[str] = Query(None, description="调试用猫娘名；默认忽略，实际读取 NEKO 当前猫娘"),
    runtime_character_hint: Optional[str] = Query(None, description="NEKO 本体运行态同步的当前猫娘名"),
    min_importance: int = Query(5, ge=0, le=10),
    include_absorbed: bool = Query(True),
    limit: int = Query(5, ge=1, le=10, description="抽取候选事实数量；奇遇铸造机默认使用 5 条"),
    exclude_fact_ids: Optional[str] = Query(None, description="逗号分隔，排除已经铸造过的 fact id"),
    exclude_hashes: Optional[str] = Query(None, description="逗号分隔，排除已经铸造过的 fact hash"),
):
    """Standalone-server wrapper around the shared local-facts selector."""
    payload = await build_forge_facts_payload(
        character=character,
        runtime_character_hint=runtime_character_hint,
        min_importance=min_importance,
        include_absorbed=include_absorbed,
        limit=limit,
        exclude_fact_ids=exclude_fact_ids,
        exclude_hashes=exclude_hashes,
    )
    return JSONResponse(payload)


@app.post("/forge/card-story")
async def arena_forge_card_story(body: dict[str, Any]):
    """用 NEKO 核心 LLM 配置把故事引子生成 Forged 卡牌专属小故事。"""
    request_id = f"forge-{uuid.uuid4().hex[:10]}"
    safe_body = body if isinstance(body, dict) else {}
    runtime_hint = str(
        safe_body.get("runtimeCharacterHint")
        or safe_body.get("runtime_character_hint")
        or safe_body.get("character")
        or ""
    ).strip()
    if not runtime_hint:
        return JSONResponse(
            {
                "success": False,
                "requestId": request_id,
                "storyGenerationStatus": "failed",
                "error": "active_neko_runtime_not_linked",
            }
        )
    route_started_at = time.perf_counter()
    card = safe_body.get("card") if isinstance(safe_body.get("card"), dict) else {}
    _forge_route_log(
        request_id,
        "request",
        sourceFactId=safe_body.get("sourceFactId") or safe_body.get("factId"),
        storyLead=safe_body.get("storyLead"),
        card={
            "attrName": card.get("attrName"),
        },
    )
    try:
        from forge_story_generator import ForgeStoryGenerationError, generate_forge_card_story

        result = await generate_forge_card_story({**safe_body, "_requestId": request_id})
        elapsed_ms = round((time.perf_counter() - route_started_at) * 1000, 1)
        _forge_route_log(
            request_id,
            "success",
            provider=result.provider,
            model=result.model,
            sourceFactId=result.source_fact_id,
            storyChars=len(result.story),
            elapsedMs=elapsed_ms,
        )
        return JSONResponse(
            {
                "success": True,
                "requestId": request_id,
                "story": result.story,
                "storyGenerationStatus": "ready",
                "provider": result.provider,
                "model": result.model,
                "sourceFactId": result.source_fact_id,
            }
        )
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        _forge_route_log(
            request_id,
            "failed",
            error=error,
            errorType=type(exc).__name__,
            elapsedMs=round((time.perf_counter() - route_started_at) * 1000, 1),
        )
        if exc.__class__.__name__ != "ForgeStoryGenerationError":
            logger.warning("forge-card-story: generation failed: %s", error)
        return JSONResponse(
            {
                "success": False,
                "requestId": request_id,
                "storyGenerationStatus": "failed",
                "error": error,
            }
        )


@app.get("/forge/active-character")
async def arena_active_character(include_avatar: bool = False):
    """返回当前 NEKO 配置的猫娘名、主人名与角色卡脸图（无则空串）。

    社区 SPA 跨源调本端点拿"当前猫娘"，再据此调 /forge/facts 抽本地记忆。
    复用 active_neko_context（与 forge-facts 同一份"当前猫娘"解析），享受同一 CORS 白名单。
    master_name 透传给云端 /api/cards/forge-beta 当故事 prompt 的主人称谓；社区前端只读，
    不写回本地 —— 这里仅暴露 NEKO 既有的主人设定与本地角色卡脸图。
    """
    config_snapshot = _read_active_character_config_snapshot()
    runtime_snapshot = await asyncio.to_thread(
        _read_main_server_active_character_snapshot, include_avatar,
    )
    name = str(runtime_snapshot.get("name") or "").strip()
    master = str(config_snapshot.get("master_name") or "").strip() if name else ""
    avatar_data_url = ""
    character_reference_data_url = ""
    avatar_url = _card_face_avatar_url(name) if name else ""
    if name and include_avatar:
        card_face_data_url = _read_card_face_data_url(name)
        if _is_avatar_data_url(card_face_data_url):
            avatar_data_url = card_face_data_url
        main_server_reference = runtime_snapshot.get("characterReferenceDataUrl") or ""
        character_reference_data_url = (
            main_server_reference if _is_avatar_data_url(main_server_reference) else ""
        )
    return {
        "name": name or "",
        "master_name": master or "",
        "avatarUrl": avatar_url,
        "avatar_url": avatar_url,
        "dataUrl": avatar_data_url,
        "avatarDataUrl": avatar_data_url,
        "characterReferenceDataUrl": character_reference_data_url,
        "character_reference_data_url": character_reference_data_url,
    }


@app.get("/forge/active-character/avatar")
async def arena_active_character_avatar(name: str = ""):
    """Serve the persisted card face image for the current or requested character."""
    resolved_name = name.strip()
    if not resolved_name:
        resolved_name = _read_active_character_config_snapshot().get("name", "")
    path = _find_card_face_path(resolved_name)
    if not path:
        return JSONResponse({"error": "card_face_not_found"}, status_code=404)
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return FileResponse(path, media_type=media_type)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "card_forge_server"}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 默认只监听 loopback：facts 含个人化记忆，禁止开箱即用就被局域网读取。
    # 若确需在其他网卡监听，请显式设置 NEKO_CARD_FORGE_HOST="0.0.0.0"
    # （并相应收紧 NEKO_CARD_FORGE_ALLOWED_ORIGINS）。
    #
    # app_dir 必传：reload=True 时 uvicorn 会 fork worker 子进程，再用
    # `"server:app"` 字符串 import-by-name；worker 不会继承 __main__ 里给
    # sys.path 加的 SERVER_ROOT，所以从项目根 (`uv run local_server/card_forge_server/server.py`)
    # 启动时 worker 找不到 `server` 模块。显式 app_dir=SERVER_ROOT 把目录交给 uvicorn
    # 自己加到 worker 的 sys.path 上。
    uvicorn.run(
        "server:app",
        host=os.environ.get("NEKO_CARD_FORGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("NEKO_CARD_FORGE_PORT", "3001")),
        reload=True,
        app_dir=str(SERVER_ROOT),
    )
