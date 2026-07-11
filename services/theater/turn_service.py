"""编排当前版单猫娘小剧场的一条线性回合链。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from . import llm, projector, rules, session_store, story_graph, story_loader


# 模型只消费最近四轮对话，幂等缓存只服务近期网络重试，避免长剧本存档无限增长。
MAX_RECENT_TURN_MESSAGES = 8
MAX_IDEMPOTENT_RESULTS = 32


async def submit(
    root: Path,
    *,
    session_id: str,
    input_kind: str,
    choice_id: str,
    message: str,
    client_turn_id: str,
    base_revision: Any,
    config_manager: Any | None,
) -> dict[str, Any]:
    """校验并原子提交一个 Choice、自由输入或离场回合。"""  # noqa: DOCSTRING_CJK
    request, error = _normalize_request(
        input_kind=input_kind,
        choice_id=choice_id,
        message=message,
        client_turn_id=client_turn_id,
        base_revision=base_revision,
    )
    if error:
        return {"ok": False, "reason": error}
    async with session_store.session_guard(session_id):
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        cached = _cached_result(session, request["client_turn_id"])
        if cached:
            return cached
        if await session_store.is_stale_session(root, session):
            return {"ok": False, "reason": "stale_session", "skipped": True}
        if session.get("ended_at"):
            return {"ok": False, "reason": "session_ended"}
        revision = session_store.state_revision(session)
        expected = request.get("base_revision")
        if expected is not None and expected != revision:
            return _revision_conflict(revision)

        # 所有业务变化先写候选副本；只有完整公开响应生成后才替换原存档。
        candidate = deepcopy(session)
        story = await story_loader.load_story(str(candidate.get("story_id") or ""))
        response = await _apply_turn(candidate, story, request, config_manager=config_manager)
        if response.get("ok") is not True:
            return response

        latest = await session_store.load_session(root, session_id)
        if latest is None:
            return {"ok": False, "reason": "session_not_found"}
        if session_store.state_revision(latest) != revision:
            return _revision_conflict(session_store.state_revision(latest))
        next_revision = revision + 1
        candidate["state_revision"] = next_revision
        candidate["updated_at"] = _now_ms()
        response["state_revision"] = next_revision
        candidate["public_snapshot"] = deepcopy(response)
        index = candidate.setdefault("turn_results_by_client_id", {})
        index[request["client_turn_id"]] = deepcopy(response)
        # 字典保持提交顺序；超过上限时淘汰最早结果，旧请求仍会被 revision 校验阻止重复推进。
        while len(index) > MAX_IDEMPOTENT_RESULTS:
            index.pop(next(iter(index)))
        await session_store.save_session(root, candidate)
        if candidate.get("ended_at"):
            # 正式结局和主动离场都清除当前角色的恢复索引。
            await session_store.clear_active_session(
                root,
                str(candidate.get("lanlan_name") or ""),
                str(candidate.get("session_id") or ""),
            )
        return deepcopy(response)


async def _apply_turn(
    session: dict[str, Any],
    story: dict[str, Any],
    request: dict[str, Any],
    *,
    config_manager: Any | None,
) -> dict[str, Any]:
    """在候选 Session 上执行一次轻量回合。"""  # noqa: DOCSTRING_CJK
    if request["input_kind"] == "user_exit":
        return _apply_exit(session, story)

    state = session.get("story_state") if isinstance(session.get("story_state"), dict) else {}
    current = story_graph.current_node(story, state)
    choice: dict[str, Any] = {}
    progress_kind = "roleplay_response"
    message = request["message"]

    if request["input_kind"] == "choice":
        choice = story_graph.resolve_choice(story, state, request["choice_id"])
        if not choice:
            return {"ok": False, "reason": "choice_not_available"}
        message = str(choice.get("label") or "")
        progress_kind = "graph_progress"

    target = current
    if choice:
        target = story_graph.node_by_id(story, str(choice.get("target_node_id") or ""))
        rules.apply_node(story, state, target)
    else:
        # 自由互动只形成短期非权威笔记，不改变节点、线索或结局。
        rules.append_scene_note(state, message)

    phase = str(target.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    # 自由回合把当前稳定 Choice 提供给同一次模型调用，只允许生成上下文化标签。
    choice_options = story_graph.suggestion_options(story, state) if progress_kind == "roleplay_response" else []
    performance = await llm.generate_turn_async(
        config_manager=config_manager,
        lanlan_name=str(session.get("lanlan_name") or "Lan"),
        story=story,
        scene=scene,
        node=target,
        user_message=message,
        progress_kind=progress_kind,
        callback=str(choice.get("callback") or ""),
        state=state,
        recent_turns=list(session.get("turns") or []),
        choice_options=choice_options,
    )
    rewrites = performance.pop("choice_rewrites", [])
    if progress_kind == "roleplay_response":
        allowed_ids = {item["choice_id"] for item in choice_options}
        # 再做一次本地白名单收口，防止模型增加按钮或覆盖已经过期的 Choice。
        next_overrides = {
            str(choice_id): str(label)
            for choice_id, label in (state.get("choice_label_overrides") or {}).items()
            if choice_id in allowed_ids and str(label).strip()
        }
        next_overrides.update(
            {
                str(item["choice_id"]): str(item["label"])
                for item in rewrites
                if isinstance(item, dict) and item.get("choice_id") in allowed_ids and str(item.get("label") or "").strip()
            }
        )
        state["choice_label_overrides"] = next_overrides
    outgoing = story_graph.outgoing_nodes(story, state)
    ending = rules.ending_for_state(story, state, target, has_outgoing=bool(outgoing))
    if progress_kind == "roleplay_response":
        # 单纯对话不能因为当前节点暂无出口而自动结束，正式结束只发生在剧情推进后。
        ending = {"should_offer_ending": False, "should_end_session": False, "ending_id": ""}

    session["phase"] = phase
    session["story_state"] = state
    trace = projector.scenario_trace(
        progress_kind=progress_kind,
        choice=choice,
    )
    _append_turns(session, message=message, performance=performance, trace=trace)
    if ending.get("should_end_session"):
        session["ended_at"] = _now_ms()

    response = projector.public_response(
        session=session,
        story=story,
        scene=scene,
        narration=performance["narration"],
        dialogue=performance["dialogue"],
        trace=trace,
        ending=ending,
        can_resume=not bool(session.get("ended_at")),
    )
    return response


def _apply_exit(session: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    """结束本场演出，但不伪装成作者结局。"""  # noqa: DOCSTRING_CJK
    now = _now_ms()
    session["ended_at"] = now
    ending = {"should_offer_ending": False, "should_end_session": True, "ending_id": "", "reason": "user_exit"}
    state = session.get("story_state") if isinstance(session.get("story_state"), dict) else {}
    node = story_graph.current_node(story, state)
    phase = str(node.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    dialogue = f"{str(session.get('lanlan_name') or 'Lan')}会把剧本合好，等你下次再来喵。"
    trace = projector.scenario_trace(progress_kind="user_exit")
    _append_turns(session, message="结束小剧场", performance={"narration": "", "dialogue": dialogue}, trace=trace)
    return projector.public_response(
        session=session,
        story=story,
        scene=scene,
        narration="",
        dialogue=dialogue,
        trace=trace,
        ending=ending,
        can_resume=False,
    )


def _append_turns(
    session: dict[str, Any], *, message: str, performance: dict[str, str], trace: dict[str, Any]
) -> None:
    """保存最小公开历史，供恢复和下一轮演绎使用。"""  # noqa: DOCSTRING_CJK
    turns = session.setdefault("turns", [])
    now = _now_ms()
    turns.append({"role": "user", "text": message, "created_at": now})
    turns.append(
        {
            "role": "assistant",
            "text": performance.get("dialogue", ""),
            "narration": performance.get("narration", ""),
            "scenario_trace": dict(trace),
            "created_at": now,
        }
    )
    # 只保存模型真正会读取的最近四轮，公开恢复依赖 snapshot，不依赖完整聊天流水。
    session["turns"] = turns[-MAX_RECENT_TURN_MESSAGES:]


def _normalize_request(
    *, input_kind: str, choice_id: str, message: str, client_turn_id: str, base_revision: Any
) -> tuple[dict[str, Any], str]:
    """校验三类互斥输入和客户端幂等字段。"""  # noqa: DOCSTRING_CJK
    kind = str(input_kind or "").strip()
    if kind not in {"choice", "free_input", "user_exit"}:
        return {}, "invalid_input_kind"
    client_id = str(client_turn_id or "").strip()
    if not client_id or len(client_id) > 128:
        return {}, "invalid_client_turn_id"
    if base_revision is not None and (not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 0):
        return {}, "invalid_base_revision"
    normalized_choice = str(choice_id or "").strip()
    normalized_message = str(message or "").strip()
    if kind == "choice" and (not normalized_choice or normalized_message):
        return {}, "invalid_choice_input"
    if kind == "free_input" and (not normalized_message or normalized_choice):
        return {}, "invalid_free_input"
    if kind == "user_exit" and (normalized_message or normalized_choice):
        return {}, "invalid_user_exit"
    return {
        "input_kind": kind,
        "choice_id": normalized_choice,
        "message": normalized_message,
        "client_turn_id": client_id,
        "base_revision": base_revision,
    }, ""


def _cached_result(session: dict[str, Any], client_turn_id: str) -> dict[str, Any]:
    """回放首次提交结果，不重复调用模型或推进剧情。"""  # noqa: DOCSTRING_CJK
    index = session.get("turn_results_by_client_id")
    cached = index.get(client_turn_id) if isinstance(index, dict) else None
    return deepcopy(cached) if isinstance(cached, dict) else {}


def _revision_conflict(revision: int) -> dict[str, Any]:
    """返回前端可恢复的版本冲突。"""  # noqa: DOCSTRING_CJK
    return {"ok": False, "reason": "state_revision_conflict", "retryable": True, "state_revision": revision}


def _now_ms() -> int:
    """使用毫秒时间戳保存 Session 生命周期。"""  # noqa: DOCSTRING_CJK
    import time

    return int(time.time() * 1000)
