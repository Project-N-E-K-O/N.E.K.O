"""提供轻量小剧场的生命周期外观。"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import projector, rules, session_store, story_graph, story_loader, turn_service


THEATER_SESSION_TTL_MS = 24 * 60 * 60 * 1000
# 只保留近期已朗读 revision，既覆盖网络重试又避免长剧本存档无限增长。
MAX_SPOKEN_DIALOGUE_REVISIONS = 32


async def list_stories() -> list[dict[str, Any]]:
    """列出玩家可以选择的剧本。"""
    return await story_loader.list_stories()


async def start_session(
    root: Path,
    *,
    lanlan_name: str,
    story_id: str | None = None,
) -> dict[str, Any]:
    """创建使用唯一轻量演绎链的单猫娘 Session。"""
    story = await story_loader.load_story(story_id)
    node_id = story_loader.initial_node_id(story)
    node = story_graph.node_by_id(story, node_id)
    if not node:
        return {"ok": False, "reason": "story_has_no_initial_node"}

    session_id = f"theater_{uuid.uuid4()}"
    now = _now_ms()
    state = rules.initial_state(story, initial_node_id=node_id)
    # 开场节点是已经发生的作者事实，因此在返回首组选项前先提交一次。
    rules.apply_node(story, state, node)
    phase = str(node.get("belong_phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    opening_dialogue = str(story.get("opening_dialogue") or f"{lanlan_name}已经准备好和你一起开始了喵。")
    session: dict[str, Any] = {
        "schema_version": session_store.SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "story_id": str(story.get("id") or ""),
        "lanlan_name": str(lanlan_name or "Lan"),
        "phase": phase,
        "story_state": state,
        "turns": [{"role": "assistant", "text": opening_dialogue, "narration": str(scene.get("text") or ""), "created_at": now}],
        "state_revision": 0,
        "turn_results_by_client_id": {},
        # TTS 去重只记录公开快照 revision，不保存音频或额外台词副本。
        "spoken_dialogue_revisions": [],
        "started_at": now,
        "updated_at": now,
        "ended_at": None,
    }
    ending = {"should_offer_ending": False, "should_end_session": False, "ending_id": ""}
    response = projector.public_response(
        session=session,
        story=story,
        scene=scene,
        narration=str(scene.get("text") or ""),
        dialogue=opening_dialogue,
        trace=None,
        ending=ending,
        can_resume=True,
    )
    session["public_snapshot"] = deepcopy(response)
    await session_store.save_session(root, session)
    await session_store.set_active_session(root, str(session["lanlan_name"]), session_id)
    return response


async def submit_input(
    root: Path,
    *,
    session_id: str,
    message: str = "",
    input_kind: str = "",
    choice_id: str = "",
    client_turn_id: str = "",
    base_revision: Any | None = None,
    config_manager: Any | None = None,
) -> dict[str, Any]:
    """提交轻量结构化输入并交给唯一回合服务处理。"""
    return await turn_service.submit(
        root,
        session_id=session_id,
        input_kind=input_kind,
        choice_id=choice_id,
        message=message,
        client_turn_id=client_turn_id,
        base_revision=base_revision,
        config_manager=config_manager,
    )


async def get_state(root: Path, session_id: str) -> dict[str, Any]:
    """读取最后一次已保存的公开快照，不重新运行模型。"""
    session = await session_store.load_session(root, session_id)
    if session is None:
        return {"ok": False, "reason": "session_not_found"}
    snapshot = session.get("public_snapshot")
    if not isinstance(snapshot, dict):
        return {"ok": False, "reason": "session_snapshot_missing"}
    result = deepcopy(snapshot)
    stale = await session_store.is_stale_session(root, session)
    result["stale"] = stale
    result["can_resume"] = not stale and not bool(session.get("ended_at"))
    result["state_revision"] = session_store.state_revision(session)
    if not result["can_resume"]:
        result["suggestion_options"] = []
    return result


async def claim_dialogue_speech(root: Path, *, session_id: str, state_revision: Any) -> dict[str, Any]:
    """原子认领当前公开猫娘对白，保证同一 revision 最多触发一次 TTS。"""
    if not isinstance(state_revision, int) or isinstance(state_revision, bool) or state_revision < 0:
        return {"ok": False, "reason": "invalid_state_revision"}
    async with session_store.session_guard(session_id):
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        current_revision = session_store.state_revision(session)
        if state_revision != current_revision:
            return {
                "ok": True,
                "skipped": "stale_revision",
                "state_revision": current_revision,
            }
        snapshot = session.get("public_snapshot")
        dialogue = snapshot.get("dialogue") if isinstance(snapshot, dict) else None
        line = str(dialogue.get("text") or "").strip() if isinstance(dialogue, dict) else ""
        if not line:
            return {"ok": True, "skipped": "empty_dialogue", "state_revision": current_revision}

        spoken = session.setdefault("spoken_dialogue_revisions", [])
        normalized_spoken = [item for item in spoken if isinstance(item, int) and not isinstance(item, bool)]
        if current_revision in normalized_spoken:
            return {"ok": True, "skipped": "already_spoken", "state_revision": current_revision}
        normalized_spoken.append(current_revision)
        session["spoken_dialogue_revisions"] = normalized_spoken[-MAX_SPOKEN_DIALOGUE_REVISIONS:]
        # 先落盘再调用外部 TTS；即使响应丢失，HTTP 重试也不会重复朗读同一句。
        await session_store.save_session(root, session)
        return {
            "ok": True,
            "line": line,
            "lanlan_name": str(session.get("lanlan_name") or ""),
            "session_id": str(session.get("session_id") or session_id),
            "state_revision": current_revision,
        }


async def get_active_state(root: Path, *, lanlan_name: str) -> dict[str, Any]:
    """恢复当前猫娘最后一场仍可继续的演出。"""
    session_id = await session_store.get_active_session_id(root, lanlan_name)
    if not session_id:
        return {"ok": False, "reason": "active_session_not_found"}
    result = await get_state(root, session_id)
    if result.get("ok") is not True or result.get("can_resume") is not True:
        await session_store.clear_active_session(root, lanlan_name, session_id)
        return {"ok": False, "reason": "active_session_not_found"}
    return result


async def end_session(root: Path, *, session_id: str) -> dict[str, Any]:
    """管理性关闭 Session，不生成剧情结局。"""
    async with session_store.session_guard(session_id):
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        if not session.get("ended_at"):
            session["ended_at"] = _now_ms()
            session["updated_at"] = session["ended_at"]
            session["phase"] = "ended"
            snapshot = session.get("public_snapshot")
            if isinstance(snapshot, dict):
                snapshot["can_resume"] = False
                snapshot["suggestion_options"] = []
            await session_store.save_session(root, session)
        await session_store.clear_active_session(
            root,
            str(session.get("lanlan_name") or ""),
            str(session.get("session_id") or ""),
        )
    return {"ok": True, "session_id": session_id, "ended": True}


async def clear_character_session(root: Path, *, lanlan_name: str) -> dict[str, Any]:
    """角色切换时关闭旧猫娘的活动演出，防止 Session 跨人格恢复。"""
    session_id = await session_store.get_active_session_id(root, lanlan_name)
    if not session_id:
        return {"ok": True, "cleared": False}
    result = await end_session(root, session_id=session_id)
    if result.get("ok") is not True:
        # 旧协议或损坏 Session 无法进入 Runtime，但角色切换仍必须清掉其活动索引。
        await session_store.clear_active_session(root, lanlan_name, session_id)
        return {"ok": True, "cleared": True, "session_id": session_id}
    return {"ok": bool(result.get("ok")), "cleared": bool(result.get("ok")), "session_id": session_id}


async def cleanup_expired_sessions(root: Path, *, now_ms: int | None = None) -> dict[str, int]:
    """机会性关闭超过 24 小时未更新的活动 Session。"""
    now = int(now_ms if now_ms is not None else _now_ms())
    expired = 0
    for session_id in await session_store.list_session_ids(root):
        async with session_store.session_guard(session_id):
            session = await session_store.load_session(root, session_id)
            if session is None or session.get("ended_at"):
                continue
            updated_at = int(session.get("updated_at") or session.get("started_at") or 0)
            if not updated_at or now - updated_at <= THEATER_SESSION_TTL_MS:
                continue
            session["ended_at"] = now
            snapshot = session.get("public_snapshot")
            if isinstance(snapshot, dict):
                snapshot["can_resume"] = False
                snapshot["suggestion_options"] = []
            await session_store.save_session(root, session)
            await session_store.clear_active_session(
                root,
                str(session.get("lanlan_name") or ""),
                session_id,
            )
            expired += 1
    return {"expired": expired}


def _now_ms() -> int:
    """返回毫秒时间戳。"""
    return int(time.time() * 1000)
