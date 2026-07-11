# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""提供本地轻量小剧场页面所需的 HTTP 接口。"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from main_logic.mirror_meta import build_mirror_meta
from services.theater import runtime
from .shared_state import get_config_manager, get_session_manager
from utils.logger_config import get_module_logger


router = APIRouter(tags=["theater"], prefix="/api/theater")
logger = get_module_logger("main_routers.theater_router")


def _resolve_lanlan_name(raw: Any = None) -> str:
    """解析小剧场本轮使用的角色名，缺省时读取当前猫娘。"""  # noqa: DOCSTRING_CJK
    lanlan_name = str(raw or "").strip()
    if lanlan_name:
        return lanlan_name
    try:
        characters = get_config_manager().load_characters()
        return str(characters.get("当前猫娘") or "").strip()
    except Exception:
        return ""


def _theater_root() -> Path:
    """解析小剧场私有运行目录，优先使用当前 app docs 目录。"""  # noqa: DOCSTRING_CJK
    manager = get_config_manager()
    app_docs_dir = getattr(manager, "app_docs_dir", None)
    if app_docs_dir:
        return Path(app_docs_dir) / "theater"
    config_dir = getattr(manager, "config_dir", None)
    if config_dir:
        return Path(config_dir).parent / "theater"
    return Path("data") / "theater"


def _validate_theater_local_mutation(request: Request, data: dict[str, Any]):
    """复用本地 mutation 校验，保护 theater 写接口不被裸 POST 调用。"""  # noqa: DOCSTRING_CJK
    from .system_router import _validate_local_mutation_request

    return _validate_local_mutation_request(
        request,
        payload=data,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )


async def _cleanup_expired_theater_sessions(root: Path) -> None:
    """在 theater 入口请求中机会性清理过期 session，不阻断用户打开页面。"""  # noqa: DOCSTRING_CJK
    try:
        await runtime.cleanup_expired_sessions(root)
    except Exception as exc:
        logger.warning("小剧场过期 session 清理失败: %s", exc)


async def _speak_committed_dialogue(response: dict[str, Any]) -> dict[str, Any]:
    """把已提交公开对白交给当前猫娘 TTS，失败时只降级文字演绎。"""  # noqa: DOCSTRING_CJK
    if response.get("ok") is not True:
        return {"ok": True, "skipped": "turn_failed"}
    session_id = str(response.get("session_id") or "")
    state_revision = response.get("state_revision")
    claim = await runtime.claim_dialogue_speech(
        _theater_root(),
        session_id=session_id,
        state_revision=state_revision,
    )
    if claim.get("ok") is not True or claim.get("skipped"):
        return claim

    lanlan_name = str(claim.get("lanlan_name") or "")
    manager = get_session_manager().get(lanlan_name) if lanlan_name else None
    speak = getattr(manager, "mirror_assistant_speech", None)
    if not callable(speak):
        return {"ok": True, "skipped": "project_tts_unavailable"}

    # 只复用现有音频管线：不镜像普通聊天文字、不发送普通聊天 turn-end，也不占用游戏 route。
    metadata = build_mirror_meta(
        source="theater",
        kind="theater_dialogue",
        session_id=session_id,
        event={"state_revision": int(claim.get("state_revision") or 0)},
    )
    try:
        return await speak(
            str(claim.get("line") or ""),
            metadata=metadata,
            request_id=f"theater_tts_{session_id}_{claim.get('state_revision')}",
            mirror_text=False,
            emit_turn_end_after=False,
            # 新剧场对白拥有当前播放权，进入下一轮时停止上一段尚未播完的猫娘台词。
            interrupt_audio=True,
        )
    except Exception as exc:
        logger.warning("小剧场猫娘对白 TTS 降级为纯文字: %s", exc)
        return {"ok": True, "skipped": "project_tts_failed", "error_type": type(exc).__name__}


@router.get("/stories")
async def list_theater_stories():
    """返回故事列表，并在打开小剧场时顺手清理过期 session。"""  # noqa: DOCSTRING_CJK
    await _cleanup_expired_theater_sessions(_theater_root())
    return {"ok": True, "stories": await runtime.list_stories()}


@router.post("/session/start")
async def start_theater_session(request: Request):
    """启动小剧场 session，并在创建新 session 前清理过期旧状态。"""  # noqa: DOCSTRING_CJK
    data = await request.json()
    if not isinstance(data, dict):
        data = {}
    validation_error = _validate_theater_local_mutation(request, data)
    if validation_error is not None:
        return validation_error
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name")) or "Lan"
    root = _theater_root()
    await _cleanup_expired_theater_sessions(root)
    result = await runtime.start_session(
        root,
        lanlan_name=lanlan_name,
        story_id=data.get("story_id"),
        # 稳定开场 ID 让响应丢失后的网络重试复用同一 Session，而不是重复开场。
        client_start_id=str(data.get("client_start_id") or ""),
    )
    await _speak_committed_dialogue(result)
    return result


@router.post("/session/input")
async def submit_theater_input(request: Request):
    """提交用户输入并推进指定小剧场 session。"""  # noqa: DOCSTRING_CJK
    data = await request.json()
    if not isinstance(data, dict):
        data = {}
    validation_error = _validate_theater_local_mutation(request, data)
    if validation_error is not None:
        return validation_error
    result = await runtime.submit_input(
        _theater_root(),
        session_id=str(data.get("session_id") or ""),
        message=str(data.get("message") or ""),
        # Router 只做协议转交，互斥校验和 Choice 可见性由轻量 Turn Service 负责。
        input_kind=str(data.get("input_kind") or ""),
        choice_id=str(data.get("choice_id") or ""),
        client_turn_id=str(data.get("client_turn_id") or ""),
        # 保持原始 JSON 类型，让服务层统一验证非负整数 revision。
        base_revision=data.get("base_revision"),
        config_manager=get_config_manager(),
    )
    await _speak_committed_dialogue(result)
    return result


@router.get("/session/state")
async def get_theater_session_state(session_id: str):
    """返回指定小剧场 session 的公开状态摘要。"""  # noqa: DOCSTRING_CJK
    return await runtime.get_state(_theater_root(), session_id=str(session_id or ""))


@router.get("/session/active")
async def get_active_theater_session_state():
    """返回当前角色可恢复的小剧场公开快照，不向前端暴露 active 索引文件。"""  # noqa: DOCSTRING_CJK
    lanlan_name = _resolve_lanlan_name(None) or "Lan"
    return await runtime.get_active_state(_theater_root(), lanlan_name=lanlan_name)
