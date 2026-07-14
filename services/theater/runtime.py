"""提供轻量小剧场的生命周期外观。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import llm, projector, rules, session_store, story_graph, story_loader, turn_service


THEATER_SESSION_TTL_MS = 24 * 60 * 60 * 1000
# 只保留近期已朗读 revision，既覆盖网络重试又避免长剧本存档无限增长。
MAX_SPOKEN_DIALOGUE_REVISIONS = 32


async def list_stories() -> list[dict[str, Any]]:
    """列出玩家可以选择的剧本。"""  # noqa: DOCSTRING_CJK
    return await story_loader.list_stories()


async def start_session(
    root: Path,
    *,
    lanlan_name: str,
    story_id: str | None = None,
    client_start_id: str = "",
    replace_incompatible_session: bool = False,
    config_manager: Any | None = None,
) -> dict[str, Any]:
    """按角色串行创建 Session；提供开场 ID 时复用已提交结果。"""  # noqa: DOCSTRING_CJK
    normalized_start_id = str(client_start_id or "").strip()
    if len(normalized_start_id) > 160:
        return {"ok": False, "reason": "invalid_client_start_id"}
    normalized_name = str(lanlan_name or "Lan").strip() or "Lan"
    async with session_store.character_guard(root, normalized_name):
        if config_manager is not None and await _current_catgirl_name(config_manager) != normalized_name:
            # 请求等待旧角色锁期间可能已经切换猫娘；锁内重验失败时不能创建孤立的旧角色 Session。
            return {"ok": False, "reason": "session_character_mismatch"}
        active_session_id = await session_store.get_active_session_id(root, normalized_name)
        active_session: dict[str, Any] | None = None
        if active_session_id:
            active_session, active_reason = await session_store.load_session_with_status(root, active_session_id)
            if active_reason in {"session_upgrade_required", "session_version_unsupported"}:
                if replace_incompatible_session is not True:
                    # 未得到玩家明确的新开场动作前，保留旧存档的活动恢复入口。
                    return {"ok": False, "reason": active_reason, "session_id": active_session_id}
                # 显式替换仍先完整创建新 Session，最后才原子覆盖活动索引；旧文件继续保留。
                active_session = None
        if normalized_start_id:
            snapshot = active_session.get("public_snapshot") if isinstance(active_session, dict) else None
            if (
                isinstance(active_session, dict)
                and active_session.get("start_client_id") == normalized_start_id
                and not active_session.get("ended_at")
                and isinstance(snapshot, dict)
            ):
                # 网络重试直接复用已提交快照；TTS revision 认领会继续保证开场对白不重播。
                return deepcopy(snapshot)
        return await _create_session(
            root,
            lanlan_name=normalized_name,
            story_id=story_id,
            start_client_id=normalized_start_id,
            replaced_session=active_session,
            config_manager=config_manager,
        )


async def _current_catgirl_name(config_manager: Any) -> str:
    """从 Router 使用的配置管理器重读当前猫娘，兼容同步与异步加载接口。"""  # noqa: DOCSTRING_CJK
    async_loader = getattr(config_manager, "aload_characters", None)
    if callable(async_loader):
        characters = await async_loader()
    else:
        sync_loader = getattr(config_manager, "load_characters", None)
        characters = sync_loader() if callable(sync_loader) else {}
    if not isinstance(characters, dict):
        characters = {}
    return str(characters.get("当前猫娘") or "").strip() or "Lan"


async def _create_session(
    root: Path,
    *,
    lanlan_name: str,
    story_id: str | None,
    start_client_id: str,
    replaced_session: dict[str, Any] | None = None,
    config_manager: Any | None = None,
) -> dict[str, Any]:
    """在角色锁内创建、保存并切换活动 Session。"""  # noqa: DOCSTRING_CJK
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
    scene = story_loader.scene_by_id(story, str(story.get("initial_scene_id") or ""))
    opening_dialogue = str(story.get("opening_dialogue") or f"{lanlan_name}已经准备好和你一起开始了喵。")
    opening_narration = str(scene.get("text") or "")
    if config_manager is not None:
        # 正式开场也属于猫娘演绎，不能绕过人格层直接朗读作者文案。这里把 opening_dialogue 放进节点副本，
        # 让模型按当前猫娘人格转述；模型不可用时 generate_turn_async 仍会安全回退到作者原文。
        opening_node = dict(node)
        opening_node["scripted_dialogue"] = opening_dialogue
        opening_performance = await llm.generate_turn_async(
            config_manager=config_manager,
            lanlan_name=lanlan_name,
            story=story,
            scene=scene,
            node=opening_node,
            user_message="",
            progress_kind="opening",
            callback=opening_narration,
            state=state,
            recent_turns=[],
            choice_options=story_graph.suggestion_options(story, state, lanlan_name=lanlan_name),
        )
        opening_narration = str(opening_performance.get("narration") or opening_narration)
        opening_dialogue = str(opening_performance.get("dialogue") or opening_dialogue)
    session: dict[str, Any] = {
        "schema_version": session_store.SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "story_id": str(story.get("id") or ""),
        "lanlan_name": str(lanlan_name or "Lan"),
        "start_client_id": start_client_id,
        "phase": phase,
        "story_state": state,
        "turns": [{"role": "assistant", "text": opening_dialogue, "narration": opening_narration, "created_at": now}],
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
        narration=opening_narration,
        dialogue=opening_dialogue,
        trace=None,
        ending=ending,
        can_resume=True,
    )
    session["public_snapshot"] = deepcopy(response)
    await session_store.save_session(root, session)
    previous_snapshot = deepcopy(replaced_session) if isinstance(replaced_session, dict) else None
    if isinstance(replaced_session, dict):
        # 新 Session 文件先落盘，再结束旧演出；active 发布失败时可以恢复旧存档而不丢进度。
        replaced_at = _now_ms()
        replaced_session["ended_at"] = replaced_session.get("ended_at") or replaced_at
        replaced_session["updated_at"] = replaced_at
        replaced_session["phase"] = "ended"
        replaced_public = replaced_session.get("public_snapshot")
        if isinstance(replaced_public, dict):
            replaced_public["can_resume"] = False
            replaced_public["suggestion_options"] = []
            replaced_public["phase"] = "ended"
        await session_store.save_session(root, replaced_session)
    try:
        await session_store.set_active_session(root, str(session["lanlan_name"]), session_id)
    except Exception:
        # 新 Session 从未成功发布给客户端，必须标记为终结；否则索引损坏重建时会把它误认成可恢复演出。
        failed_at = _now_ms()
        session["ended_at"] = failed_at
        session["updated_at"] = failed_at
        session["phase"] = "ended"
        failed_public = session.get("public_snapshot")
        if isinstance(failed_public, dict):
            failed_public["can_resume"] = False
            failed_public["suggestion_options"] = []
            failed_public["phase"] = "ended"
        await session_store.save_session(root, session)
        if previous_snapshot is not None:
            # active 索引发布失败时恢复旧 Session，确保玩家仍能继续发布前的剧情。
            await session_store.save_session(root, previous_snapshot)
        raise
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
    expected_lanlan_name: str = "",
) -> dict[str, Any]:
    """提交轻量结构化输入并交给唯一回合服务处理。"""  # noqa: DOCSTRING_CJK
    return await turn_service.submit(
        root,
        session_id=session_id,
        input_kind=input_kind,
        choice_id=choice_id,
        message=message,
        client_turn_id=client_turn_id,
        base_revision=base_revision,
        config_manager=config_manager,
        expected_lanlan_name=expected_lanlan_name,
    )


async def get_state(root: Path, session_id: str, *, expected_lanlan_name: str = "") -> dict[str, Any]:
    """读取最后一次已保存的公开快照，不重新运行模型。"""  # noqa: DOCSTRING_CJK
    session, load_reason = await session_store.load_session_with_status(root, session_id)
    if session is None:
        result = {"ok": False, "reason": load_reason or "session_not_found"}
        if load_reason in {"session_upgrade_required", "session_version_unsupported"}:
            result["session_id"] = str(session_id or "")
        return result
    expected_name = str(expected_lanlan_name or "").strip()
    if expected_name and str(session.get("lanlan_name") or "").strip() != expected_name:
        # 恢复本地 Session 指针前校验角色归属，避免切换猫娘后重新打开上一人格的私有剧情。
        return {"ok": False, "reason": "session_character_mismatch"}
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


async def claim_dialogue_speech(
    root: Path,
    *,
    session_id: str,
    state_revision: Any,
    expected_lanlan_name: str = "",
    play: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """原子认领公开对白；传入播放器时在同一角色边界内提交 TTS。"""  # noqa: DOCSTRING_CJK
    if not isinstance(state_revision, int) or isinstance(state_revision, bool) or state_revision < 0:
        return {"ok": False, "reason": "invalid_state_revision"}
    async with session_store.session_guard(session_id):
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        lanlan_name = str(session.get("lanlan_name") or "")
        async with session_store.character_guard(root, lanlan_name):
            # 等待角色锁后重新读盘，确保开场/结束转换不能夹在 active 校验与认领写盘之间。
            session = await session_store.load_session(root, session_id)
            if session is None:
                return {"ok": False, "reason": "session_not_found"}
            current_revision = session_store.state_revision(session)
            expected_name = str(expected_lanlan_name or "").strip()
            if expected_name and str(session.get("lanlan_name") or "").strip() != expected_name:
                # 角色已切换时不写入已朗读 revision，避免旧猫娘对白占用新角色的播放权。
                return {"ok": True, "skipped": "character_changed", "state_revision": current_revision}
            snapshot = session.get("public_snapshot")
            ending = snapshot.get("ending") if isinstance(snapshot, dict) else None
            committed_terminal = bool(
                session.get("ended_at")
                and isinstance(ending, dict)
                and ending.get("should_end_session") is True
            )
            if await session_store.is_stale_session(root, session) or (session.get("ended_at") and not committed_terminal):
                # 新开场替换和管理性关闭都拒绝播放；只保留已提交终局快照的最后一句。
                return {"ok": True, "skipped": "stale_session", "state_revision": current_revision}
            if state_revision != current_revision:
                return {
                    "ok": True,
                    "skipped": "stale_revision",
                    "state_revision": current_revision,
                }
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
            # 认领写盘和 active 校验共用角色锁；网络重试不会重播，新开场也不能中途插队。
            await session_store.save_session(root, session)
            claim = {
                "ok": True,
                "line": line,
                "lanlan_name": str(session.get("lanlan_name") or ""),
                "session_id": str(session.get("session_id") or session_id),
                "state_revision": current_revision,
            }
            if play is None:
                return claim
            # 播放提交保持在角色锁内：新开场或结束只能在旧对白进入 TTS 管线后切换活动 Session。
            return await play(claim)


async def publish_character_switch(
    root: Path,
    *,
    old_lanlan_name: str,
    publish: Callable[[], Awaitable[None]],
) -> dict[str, Any]:
    """在小剧场角色边界内发布新当前猫娘，并结束旧角色活动 Session。"""  # noqa: DOCSTRING_CJK
    normalized_name = str(old_lanlan_name or "").strip()
    while True:
        active_session_id = await session_store.get_active_session_id(root, normalized_name)
        if not active_session_id:
            async with session_store.character_guard(root, normalized_name):
                # 等锁期间可能刚好创建了新 Session；出现变化时释放锁并按完整锁顺序重试。
                if await session_store.get_active_session_id(root, normalized_name):
                    continue
                await publish()
                return {"ok": True, "published": True, "cleared": False}

        async with session_store.session_guard(active_session_id):
            async with session_store.character_guard(root, normalized_name):
                # 锁外读取的 active ID 可能已经变化，不能拿旧 Session 锁处理新的活动演出。
                if await session_store.get_active_session_id(root, normalized_name) != active_session_id:
                    continue
                await publish()
                try:
                    session = await session_store.load_session(root, active_session_id)
                    if session is not None:
                        now = _now_ms()
                        session["ended_at"] = session.get("ended_at") or now
                        session["updated_at"] = now
                        session["phase"] = "ended"
                        snapshot = session.get("public_snapshot")
                        if isinstance(snapshot, dict):
                            snapshot["can_resume"] = False
                            snapshot["suggestion_options"] = []
                            snapshot["phase"] = "ended"
                        await session_store.save_session(root, session)
                    await session_store.clear_active_session(root, normalized_name, active_session_id)
                except Exception as exc:
                    # 当前猫娘已经成功发布时不能重复写配置；返回清理错误交给角色 Router 记录。
                    return {
                        "ok": True,
                        "published": True,
                        "cleared": False,
                        "cleanup_error": type(exc).__name__,
                    }
                return {"ok": True, "published": True, "cleared": True, "session_id": active_session_id}


async def get_active_state(root: Path, *, lanlan_name: str) -> dict[str, Any]:
    """恢复当前猫娘最后一场仍可继续的演出。"""  # noqa: DOCSTRING_CJK
    session_id = await session_store.get_active_session_id(root, lanlan_name)
    if not session_id:
        return {"ok": False, "reason": "active_session_not_found"}
    result = await get_state(root, session_id, expected_lanlan_name=lanlan_name)
    if result.get("reason") in {"session_upgrade_required", "session_version_unsupported"}:
        # 不兼容存档必须保留文件和活动索引，由玩家看到明确提示后决定是否新开演出。
        return result
    if result.get("ok") is not True or result.get("can_resume") is not True:
        await session_store.clear_active_session(root, lanlan_name, session_id)
        return {"ok": False, "reason": "active_session_not_found"}
    return result


async def end_session(root: Path, *, session_id: str) -> dict[str, Any]:
    """管理性关闭 Session，不生成剧情结局。"""  # noqa: DOCSTRING_CJK
    async with session_store.session_guard(session_id):
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        lanlan_name = str(session.get("lanlan_name") or "")
        async with session_store.character_guard(root, lanlan_name):
            # 结束转换与新开场、TTS 认领共享角色锁，避免 active 状态在中途交叉变化。
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
                    snapshot["phase"] = "ended"
                await session_store.save_session(root, session)
            await session_store.clear_active_session(
                root,
                lanlan_name,
                str(session.get("session_id") or ""),
            )
    return {"ok": True, "session_id": session_id, "ended": True}


async def clear_character_session(root: Path, *, lanlan_name: str) -> dict[str, Any]:
    """角色切换时关闭旧猫娘的活动演出，防止 Session 跨人格恢复。"""  # noqa: DOCSTRING_CJK
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
    """机会性关闭超过 24 小时未更新的活动 Session。"""  # noqa: DOCSTRING_CJK
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
            session["updated_at"] = now
            session["phase"] = "ended"
            snapshot = session.get("public_snapshot")
            if isinstance(snapshot, dict):
                snapshot["can_resume"] = False
                snapshot["suggestion_options"] = []
                snapshot["phase"] = "ended"
            await session_store.save_session(root, session)
            await session_store.clear_active_session(
                root,
                str(session.get("lanlan_name") or ""),
                session_id,
            )
            expired += 1
    return {"expired": expired}


def _now_ms() -> int:
    """返回毫秒时间戳。"""  # noqa: DOCSTRING_CJK
    return int(time.time() * 1000)
