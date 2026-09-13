"""小剧场复用 N.E.K.O 现有猫娘 TTS 播放管线的轻量桥接。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from main_logic.mirror_meta import build_mirror_meta
from utils.logger_config import get_module_logger


logger = get_module_logger("services.theater.tts_bridge")


def _websocket_connected(manager: Any) -> bool:
    """只在明确存在前端连接时播放，未知播放器对象保持兼容。"""  # noqa: DOCSTRING_CJK

    if not hasattr(manager, "websocket"):
        return True
    websocket = getattr(manager, "websocket", None)
    if websocket is None:
        return False
    client_state = getattr(websocket, "client_state", None)
    connected_state = getattr(client_state, "CONNECTED", None)
    return True if connected_state is None else client_state == connected_state


async def speak_committed_line(
    line: str,
    *,
    session_id: str,
    state_revision: int,
    lanlan_name: str,
    resolve_current_catgirl: Callable[[], str],
    get_session_manager: Callable[[], Any],
    metadata_kind: str,
    request_id: str,
    interrupt_audio: bool = True,
) -> dict[str, Any]:
    """把已经提交的单段小剧场对白交给既有播放器，失败时降级为文字。"""  # noqa: DOCSTRING_CJK

    text = str(line or "").strip()
    if not text:
        return {"ok": True, "skipped": "empty_dialogue"}
    try:
        current_name = str(resolve_current_catgirl() or "").strip() or "Lan"
    except Exception as exc:
        logger.warning("小剧场 TTS 解析当前猫娘失败，降级为纯文字: %s", type(exc).__name__)
        return {"ok": True, "skipped": "project_tts_unavailable"}
    if current_name != str(lanlan_name or "").strip():
        return {"ok": True, "skipped": "character_changed"}
    try:
        manager = get_session_manager().get(lanlan_name) if lanlan_name else None
    except Exception as exc:
        logger.warning("小剧场 TTS 获取播放器失败，降级为纯文字: %s", type(exc).__name__)
        return {"ok": True, "skipped": "project_tts_unavailable"}
    speak = getattr(manager, "mirror_assistant_speech", None)
    if not callable(speak):
        return {"ok": True, "skipped": "project_tts_unavailable"}
    if not _websocket_connected(manager):
        return {"ok": True, "skipped": "no_frontend_websocket"}
    metadata = build_mirror_meta(
        source="theater",
        kind=metadata_kind,
        session_id=session_id,
        event={"state_revision": int(state_revision)},
    )
    try:
        return await speak(
            text,
            metadata=metadata,
            request_id=request_id,
            mirror_text=False,
            emit_turn_end_after=False,
            interrupt_audio=interrupt_audio,
        )
    except Exception as exc:
        logger.warning("小剧场 TTS 播放失败，降级为纯文字: %s", type(exc).__name__)
        return {"ok": True, "skipped": "project_tts_failed", "error_type": type(exc).__name__}


__all__ = ["speak_committed_line"]
