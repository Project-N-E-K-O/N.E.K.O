"""编排当前版单猫娘小剧场的作者状态图回合。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from . import llm, projector, rules, session_store, story_graph, story_loader


# 模型只消费最近四轮对话，幂等缓存只服务近期网络重试，避免长剧本存档无限增长。
MAX_RECENT_TURN_MESSAGES = 8
MAX_IDEMPOTENT_RESULTS = 32
# 自由演绎允许长段输入，但必须限制 Session JSON 和后续模型上下文的最坏体积。
MAX_FREE_INPUT_CHARS = 4000


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
    expected_lanlan_name: str = "",
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
        expected_name = str(expected_lanlan_name or "").strip()
        if expected_name and str(session.get("lanlan_name") or "").strip() != expected_name:
            # Session ID 可能来自旧 localStorage；角色归属不匹配时不能读取幂等结果或继续推进。
            return {"ok": False, "reason": "session_character_mismatch"}
        cached = _cached_result(session, request["client_turn_id"])
        if await session_store.is_stale_session(root, session):
            return {"ok": False, "reason": "stale_session", "skipped": True}
        if session.get("ended_at"):
            ending = cached.get("ending") if isinstance(cached, dict) else None
            if isinstance(ending, dict) and ending.get("should_end_session") is True:
                # 已提交的主动离场/作者结局仍可幂等回放，但普通旧回合不能复活结束态 Session。
                return cached
            return {"ok": False, "reason": "session_ended"}
        if cached:
            return cached
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

        if expected_name and await _current_catgirl_name(config_manager) != expected_name:
            # 角色切换先写当前猫娘、后等待旧 Session 清理；因此模型返回后必须直接重验配置归属。
            return {"ok": False, "reason": "session_character_mismatch"}

        lanlan_name = str(candidate.get("lanlan_name") or "")
        async with session_store.character_guard(root, lanlan_name):
            # 二次校验、写盘和返回共享开场使用的角色边界，新窗口不能在中途替换 active Session。
            latest = await session_store.load_session(root, session_id)
            if latest is None:
                return {"ok": False, "reason": "session_not_found"}
            if await session_store.is_stale_session(root, latest):
                # 模型生成期间可能已有新窗口替换活动 Session，旧候选状态此时必须直接丢弃。
                return {"ok": False, "reason": "stale_session", "skipped": True}
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
                # 正式结局和主动离场都在同一角色边界内清除恢复索引。
                await session_store.clear_active_session(
                    root,
                    lanlan_name,
                    str(candidate.get("session_id") or ""),
                )
            return deepcopy(response)


async def _current_catgirl_name(config_manager: Any | None) -> str:
    """从同一配置管理器重读当前猫娘，兼容同步与异步加载接口。"""  # noqa: DOCSTRING_CJK
    async_loader = getattr(config_manager, "aload_characters", None)
    if callable(async_loader):
        characters = await async_loader()
    else:
        sync_loader = getattr(config_manager, "load_characters", None)
        characters = sync_loader() if callable(sync_loader) else {}
    if not isinstance(characters, dict):
        characters = {}
    # Router 在没有已选角色时使用 Lan；这里保持同一归一化语义，避免默认角色被误判为切换。
    return str(characters.get("当前猫娘") or "").strip() or "Lan"


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
    lanlan_name = str(session.get("lanlan_name") or "猫娘")

    if request["input_kind"] == "choice":
        # 选择解析与公开按钮使用同一真实猫娘名，保证玩家回合和 Trace 不出现作者占位符。
        choice = story_graph.resolve_choice(
            story,
            state,
            request["choice_id"],
            lanlan_name=lanlan_name,
        )
        if not choice:
            return {"ok": False, "reason": "choice_not_available"}
        message = str(choice.get("label") or "")
        progress_kind = "graph_progress"
    elif request["input_kind"] == "free_input":
        # 作者明确列出的短表达先于模型路由；唯一命中后直接演目标节点，避免旧节点先生成重复邀请。
        choice = story_graph.resolve_authored_completion(
            story,
            state,
            message,
            lanlan_name=lanlan_name,
        )
        if choice:
            # Trace 保留玩家实际说法，Choice ID、目标与 callback 仍使用作者静态声明。
            choice["label"] = message
            progress_kind = "graph_progress"

    target = current
    if choice:
        target = story_graph.node_by_id(story, str(choice.get("target_node_id") or ""))
        rules.apply_node(story, state, target)

    phase = str(target.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    # 两类回合都读取“本轮结束后真实可见”的稳定 Choice：自由回合用它做保守路由，剧情推进用它约束人格化对白
    # 必须先铺垫哪些问题、邀请和道具。模型仍不能改变 ID、目标节点、callback 或权威状态。
    choice_options = story_graph.suggestion_options(story, state, lanlan_name=lanlan_name)
    # 隐藏语义边只在未推进的自由回合提供给模型；点击 Choice 和目标节点演出不需要再次分类。
    latent_transitions = (
        story_graph.latent_transition_options(story, state)
        if progress_kind == "roleplay_response"
        else []
    )
    performance = await llm.generate_turn_async(
        config_manager=config_manager,
        lanlan_name=lanlan_name,
        story=story,
        scene=scene,
        node=target,
        user_message=message,
        progress_kind=progress_kind,
        callback=str(choice.get("callback") or ""),
        state=state,
        recent_turns=list(session.get("turns") or []),
        choice_options=choice_options,
        latent_transitions=latent_transitions,
    )
    matched_choice_id = str(performance.pop("matched_choice_id", "") or "")
    observed_intent_id = str(performance.pop("observed_intent_id", "") or "")
    rewrites = performance.pop("choice_rewrites", [])
    if progress_kind == "roleplay_response":
        inferred_transition: dict[str, Any] = {}
        if matched_choice_id:
            # 可见 Choice 永远优先于隐藏意图；目标和 callback 仍从本轮服务端白名单读取。
            inferred_transition = next(
                (dict(item) for item in choice_options if item["choice_id"] == matched_choice_id),
                {},
            )
        elif observed_intent_id:
            latent_transition = story_graph.resolve_latent_transition(
                latent_transitions,
                observed_intent_id,
            )
            if latent_transition and rules.record_latent_intent(state, latent_transition):
                # 前两次只保留模型的自然回应；第三次才把稳定隐藏边转换成普通作者节点提交。
                inferred_transition = {
                    "choice_id": str(latent_transition.get("transition_id") or ""),
                    "label": message,
                    "target_node_id": str(latent_transition.get("target_node_id") or ""),
                    "callback": str(latent_transition.get("callback") or ""),
                    "transition_id": str(latent_transition.get("transition_id") or ""),
                }
        else:
            # 普通聊天、换话题、越界或未知 ID 会打断连续性，避免分散的两句话意外累加成支线。
            rules.clear_latent_intent_tracking(state)

        if inferred_transition:
            choice = inferred_transition
            choice["label"] = message
            target = story_graph.node_by_id(story, str(choice.get("target_node_id") or ""))
            if target:
                rules.apply_node(story, state, target)
                if choice.get("transition_id"):
                    # apply_node 会清除局部计数；正式分支承诺在其后写入，供延迟汇流节点保留余波。
                    rules.commit_latent_transition(state, str(choice["transition_id"]))
                progress_kind = "graph_progress"
                phase = str(target.get("belong_phase") or session.get("phase") or "setup")
                scene = story_loader.scene_for_phase(story, phase)
                # 第一次调用基于旧节点，只负责路由且不能展示；提交后必须用目标节点重演当轮输入。
                choice_options = story_graph.suggestion_options(story, state, lanlan_name=lanlan_name)
                performance = await llm.generate_turn_async(
                    config_manager=config_manager,
                    lanlan_name=lanlan_name,
                    story=story,
                    scene=scene,
                    node=target,
                    user_message=message,
                    progress_kind=progress_kind,
                    callback=str(choice.get("callback") or ""),
                    state=state,
                    recent_turns=list(session.get("turns") or []),
                    choice_options=choice_options,
                    latent_transitions=[],
                )
                # 目标节点演出不再承担输入路由；删除全部内部字段，公开响应只保留演出内容。
                performance.pop("matched_choice_id", None)
                performance.pop("observed_intent_id", None)
                performance.pop("choice_rewrites", None)
    if progress_kind == "roleplay_response":
        # 未明确命中唯一 Choice 的自由互动只形成非权威笔记，不参与静态可达性和结局判断。
        rules.append_scene_note(state, message)
        # 每次自由互动都替换上一轮显示覆盖，不能在模型漏写时继续展示与当前上文无关的旧按钮。
        # 本轮改写不合格时回到作者原文；ID、目标节点和 callback 始终仍由静态图控制。
        state["choice_label_overrides"] = _validated_choice_rewrites(rewrites, choice_options)
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


def _validated_choice_rewrites(
    rewrites: list[dict[str, Any]],
    choice_options: list[dict[str, str]],
) -> dict[str, str]:
    """仅接受当前稳定 ID 且保持行动/对白类型的上下文化显示文案。"""  # noqa: DOCSTRING_CJK
    option_by_id = {str(item.get("choice_id") or ""): item for item in choice_options}
    accepted: dict[str, str] = {}
    for item in rewrites:
        if not isinstance(item, dict):
            continue
        choice_id = str(item.get("choice_id") or "")
        candidate = str(item.get("label") or "").strip()
        option = option_by_id.get(choice_id)
        if not option or not candidate:
            continue
        dialogue_mode = str(option.get("choice_mode") or "") == "dialogue"
        if dialogue_mode != _is_quoted_dialogue_label(candidate):
            continue
        # 原样返回当前或作者文案不算上下文化，避免把旧覆盖值永久带入后续自由对话。
        candidate_key = _choice_label_key(candidate)
        if candidate_key in {
            _choice_label_key(str(option.get("label") or "")),
            _choice_label_key(str(option.get("author_label") or option.get("label") or "")),
        }:
            continue
        # 文案不再要求逐字包含作者原句，否则无法删除已经被自由互动完成的“不追问”等过时修饰语。
        # ID、类型、目标节点和 callback 仍由 option 保留，改写失败最多影响显示而不会污染权威剧情。
        accepted[choice_id] = candidate
    return accepted


def _is_quoted_dialogue_label(label: str) -> bool:
    """判断按钮是否为纯引号对白，防止“轻声说”等动作混入对白分组。"""  # noqa: DOCSTRING_CJK
    text = str(label or "").strip()
    quote_pairs = (("“", "”"), ("「", "」"), ('"', '"'))
    return any(text.startswith(left) and text.endswith(right) for left, right in quote_pairs)


def _choice_label_key(label: str) -> str:
    """忽略引号、标点和空白比较推荐文案，拒绝没有实质变化的模型改写。"""  # noqa: DOCSTRING_CJK
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’「」（）()…—]+", "", str(label or "")).lower()


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
    if kind == "free_input" and len(normalized_message) > MAX_FREE_INPUT_CHARS:
        # 不静默截断玩家演绎；明确拒绝后前端可以保留原文，让玩家自行精简再提交。
        return {}, "free_input_too_long"
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
