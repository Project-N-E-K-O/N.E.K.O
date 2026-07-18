"""编排当前版单猫娘小剧场的作者状态图回合。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any
import uuid

from . import (
    branch_lifecycle,
    branch_runtime,
    intent_tracker,
    llm,
    model_trace,
    observability,
    projector,
    rules,
    session_store,
    story_graph,
    story_loader,
    turn_causality,
)
from .turn_branch_flow import (
    _active_branch_continue_is_verified as _active_branch_continue_is_verified,
    _active_branch_handoff_is_verified as _active_branch_handoff_is_verified,
    _apply_exit as _apply_exit,
    _ending_domain as _ending_domain,
    _finish_runtime_branch as _finish_runtime_branch,
    _narrative_goal as _narrative_goal,
    _prepare_active_branch_handoff as _prepare_active_branch_handoff,
    _prepare_active_runtime_branch_turn as _prepare_active_runtime_branch_turn,
    _prepare_runtime_branch_entry as _prepare_runtime_branch_entry,
    _prepare_technical_degraded_active_branch_turn as _prepare_technical_degraded_active_branch_turn,
    _record_committed_branch_outcomes as _record_committed_branch_outcomes,
    _revalidatable_pending_intent as _revalidatable_pending_intent,
)
from .turn_history import (
    MAX_RECENT_TURN_MESSAGES as MAX_RECENT_TURN_MESSAGES,
    _append_turns as _append_turns,
    _compose_graph_progress_dialogue as _compose_graph_progress_dialogue,
    _now_ms as _now_ms,
)
from .turn_request_contracts import (
    MAX_FREE_INPUT_CHARS as MAX_FREE_INPUT_CHARS,
    MAX_IDEMPOTENT_RESULTS as MAX_IDEMPOTENT_RESULTS,
    _cached_result as _cached_result,
    _normalize_request as _normalize_request,
    _revision_conflict as _revision_conflict,
    _turn_execution_surface as _turn_execution_surface,
    _turn_submit_outcome as _turn_submit_outcome,
    _verified_residual_evidence_excerpt as _verified_residual_evidence_excerpt,
)


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
    """提交一个完整回合，并保证所有退出路径只记录一次端到端结果。"""  # noqa: DOCSTRING_CJK
    started_at = observability.start_timer()
    timing: dict[str, Any] = {
        "lock_wait_ms": None,
        "execution_surface": "invalid",
        "idempotent_replay": False,
    }
    outcome = "unexpected_error"
    try:
        result = await _submit_impl(
            root,
            session_id=session_id,
            input_kind=input_kind,
            choice_id=choice_id,
            message=message,
            client_turn_id=client_turn_id,
            base_revision=base_revision,
            config_manager=config_manager,
            expected_lanlan_name=expected_lanlan_name,
            timing=timing,
        )
        outcome = (
            "idempotent_replay"
            if timing["idempotent_replay"] is True
            else _turn_submit_outcome(result)
        )
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        # 指标只接收输入类型、固定结果码和耗时，不能把本轮原话或任何 Session 身份交给观测层。
        observability.record_turn_submit(
            input_kind=str(input_kind or "").strip(),
            surface=str(timing["execution_surface"]),
            outcome=outcome,
            started_at=started_at,
            lock_wait_ms=timing["lock_wait_ms"],
        )


async def _submit_impl(
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
    timing: dict[str, Any],
) -> dict[str, Any]:
    """校验并原子提交候选 Session；完整事务观测由公开入口统一收口。"""  # noqa: DOCSTRING_CJK
    request, error = _normalize_request(
        input_kind=input_kind,
        choice_id=choice_id,
        message=message,
        client_turn_id=client_turn_id,
        base_revision=base_revision,
    )
    if error:
        return {"ok": False, "reason": error}
    timing["execution_surface"] = "unresolved"
    lock_started_at = observability.start_timer()
    async with session_store.session_guard(session_id):
        # 进入锁后立即冻结等待时长；后续持锁模型调用不得混入 lock_wait 指标。
        timing["lock_wait_ms"] = observability.elapsed_ms(lock_started_at)
        session = await session_store.load_session(root, session_id)
        if session is None:
            return {"ok": False, "reason": "session_not_found"}
        expected_name = str(expected_lanlan_name or "").strip()
        if (
            expected_name
            and str(session.get("lanlan_name") or "").strip() != expected_name
        ):
            # Session ID 可能来自旧 localStorage；角色归属不匹配时不能读取幂等结果或继续推进。
            return {"ok": False, "reason": "session_character_mismatch"}
        if not session_store.lifecycle_fields_valid(session):
            # 输入接口不能绕过恢复门禁，把坏休眠时间或任意终止原因当成一次成功唤醒。
            return {
                "ok": False,
                "reason": "session_state_invalid",
                "session_id": str(session_id or ""),
            }
        cached = _cached_result(session, request["client_turn_id"])
        if await session_store.is_stale_session(root, session):
            return {"ok": False, "reason": "stale_session", "skipped": True}
        if session.get("ended_at"):
            ending = cached.get("ending") if isinstance(cached, dict) else None
            if isinstance(ending, dict) and ending.get("should_end_session") is True:
                # 已提交的主动离场/作者结局仍可幂等回放，但普通旧回合不能复活结束态 Session。
                timing["idempotent_replay"] = True
                timing["execution_surface"] = "idempotent_replay"
                cached["session_lifecycle"] = projector.session_lifecycle(session)
                return cached
            return {"ok": False, "reason": "session_ended"}
        if cached:
            timing["idempotent_replay"] = True
            timing["execution_surface"] = "idempotent_replay"
            # 休眠扫描不会改写旧幂等结果；回放时必须覆盖为当前生命周期且不能借重试唤醒。
            cached["session_lifecycle"] = projector.session_lifecycle(session)
            return cached
        revision = session_store.state_revision(session)
        expected = request.get("base_revision")
        if expected is not None and expected != revision:
            return _revision_conflict(revision)

        # 所有业务变化先写候选副本；只有完整公开响应生成后才替换原存档。
        candidate = deepcopy(session)
        stored_model_returns = candidate.get("llm_return_records")
        if stored_model_returns is None:
            # 兼容本字段上线前创建的旧 Session；只在下一次成功回合中补为空列表。
            candidate["llm_return_records"] = []
        elif not isinstance(stored_model_returns, list):
            # 私有诊断数据结构损坏时不能静默覆盖，否则会破坏问题复盘所需证据。
            return {
                "ok": False,
                "reason": "session_state_invalid",
                "session_id": str(session_id or ""),
            }
        stored_causality_records = candidate.get("turn_causality_records")
        if stored_causality_records is None:
            # 旧 Session 在下一次成功回合中懒补；失败候选仍不会修改原文件。
            candidate["turn_causality_records"] = []
        elif not isinstance(stored_causality_records, list):
            # 私有因果记录同样属于 Bug 证据，结构损坏时不能静默覆盖。
            return {
                "ok": False,
                "reason": "session_state_invalid",
                "session_id": str(session_id or ""),
            }
        # 只在候选中清除休眠；后续任一校验或模型失败都不会写盘，只有成功提交才真正唤醒。
        candidate.pop("dormant_at", None)
        try:
            story = await story_loader.load_story_exact(
                str(candidate.get("story_id") or "")
            )
        except (FileNotFoundError, ValueError):
            # 用户 Story 已移除或合同失效时保留 Session，不把旧输入推进到目录中的其他剧本。
            return {
                "ok": False,
                "reason": "session_story_unavailable",
                "session_id": str(session_id or ""),
            }
        stored_story_revision = str(candidate.get("story_revision") or "").strip()
        current_story_revision = str(story.get("story_revision") or "").strip()
        if stored_story_revision and stored_story_revision != current_story_revision:
            # 未刷新页面也必须服从同一 Story 版本门禁，不能用旧按钮推进已改写的作者图。
            return {
                "ok": False,
                "reason": "session_story_revision_mismatch",
                "session_id": str(session_id or ""),
            }
        if not stored_story_revision:
            # 同 schema 早期存档在下一次成功回合中补齐 revision；失败候选仍不会写盘。
            candidate["story_revision"] = current_story_revision
        # 保存提交前快照，供落盘后只记录本轮真正新增的支线终态；不能持有会被候选原地修改的引用。
        before_state = deepcopy(candidate.get("story_state"))
        before_active_branch = bool(
            isinstance(before_state, dict)
            and isinstance(before_state.get("active_runtime_branch"), dict)
            and before_state.get("active_runtime_branch")
        )
        if request["input_kind"] == "user_exit":
            timing["execution_surface"] = "user_exit"
        elif before_active_branch:
            timing["execution_surface"] = "branch_turn"
        elif request["input_kind"] == "free_input":
            timing["execution_surface"] = "roleplay_response"
        elif request["input_kind"] == "choice":
            timing["execution_surface"] = "graph_progress"
        # 采集上下文覆盖本回合全部 Router、Planner、Actor 与 Repair 调用；失败候选退出后直接丢弃。
        turn_diagnostic: dict[str, Any] = {"response_focus": {}}
        with model_trace.capture_model_returns() as model_return_records:
            response = await _apply_turn(
                candidate,
                story,
                request,
                config_manager=config_manager,
                turn_diagnostic=turn_diagnostic,
            )
        if response.get("ok") is not True:
            return response
        timing["execution_surface"] = _turn_execution_surface(
            request=request,
            response=response,
            candidate=candidate,
            before_active_branch=before_active_branch,
        )

        if (
            expected_name
            and await _current_catgirl_name(config_manager) != expected_name
        ):
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
            # 只有 revision 二次校验通过的回合才绑定身份并落盘；幂等重放在采集前返回，不会重复追加。
            for record in model_return_records:
                candidate["llm_return_records"].append(
                    {
                        **record,
                        "session_id": str(candidate.get("session_id") or ""),
                        "client_turn_id": request["client_turn_id"],
                        "base_revision": revision,
                        "result_revision": next_revision,
                    }
                )
            candidate["turn_causality_records"].append(
                turn_causality.build_record(
                    session_id=str(candidate.get("session_id") or ""),
                    request=request,
                    response_focus=turn_diagnostic["response_focus"],
                    model_return_records=model_return_records,
                    response=response,
                    before_state=before_state,
                    after_state=candidate.get("story_state"),
                    base_revision=revision,
                    result_revision=next_revision,
                    session_ended=bool(candidate.get("ended_at")),
                )
            )
            # 与幂等结果使用同一保留窗口；淘汰私有诊断不影响公开历史或权威状态。
            while len(candidate["turn_causality_records"]) > MAX_IDEMPOTENT_RESULTS:
                candidate["turn_causality_records"].pop(0)
            candidate["public_snapshot"] = deepcopy(response)
            index = candidate.setdefault("turn_results_by_client_id", {})
            index[request["client_turn_id"]] = deepcopy(response)
            # 字典保持提交顺序；超过上限时淘汰最早结果，旧请求仍会被 revision 校验阻止重复推进。
            while len(index) > MAX_IDEMPOTENT_RESULTS:
                index.pop(next(iter(index)))
            await session_store.save_session(root, candidate)
            _record_committed_branch_outcomes(
                before_state, candidate.get("story_state")
            )
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
    turn_diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """在候选 Session 上执行一次轻量回合。"""  # noqa: DOCSTRING_CJK
    if request["input_kind"] == "user_exit":
        return _apply_exit(session, story)

    state = (
        session.get("story_state")
        if isinstance(session.get("story_state"), dict)
        else {}
    )
    current = story_graph.current_node(story, state)
    choice: dict[str, Any] = {}
    dynamic_choice: dict[str, Any] = {}
    progress_kind = "roleplay_response"
    message = request["message"]
    lanlan_name = str(session.get("lanlan_name") or "猫娘")
    pullback_intent_summary = ""
    residual_intent: dict[str, str] = {}
    response_focus: dict[str, Any] = {}
    prepared_performance: dict[str, Any] | None = None
    branch_target: dict[str, Any] | None = None
    ending_override: dict[str, Any] | None = None

    if request["input_kind"] == "choice":
        active_branch = state.get("active_runtime_branch")
        if isinstance(active_branch, dict) and active_branch:
            # 活动支线页面只公开动态按钮；旧页面残留或伪造的静态 ID 必须按不可用拒绝。
            dynamic_choice = branch_runtime.resolve_dynamic_choice(
                active_branch,
                state.get("branch_facts") or [],
                request["choice_id"],
            )
            if not dynamic_choice:
                return {"ok": False, "reason": "choice_not_available"}
            message = str(dynamic_choice.get("label") or "")
            # 动态按钮已经过服务端当前可见性校验，其公开标签本身就是本轮已实施行动的完整证据。
            response_focus = llm.verify_response_focus(
                {
                    "focus_type": "action",
                    "evidence_excerpt": message,
                    "requires_state_change": True,
                },
                user_message=message,
            )
            branch_turn = await _prepare_active_runtime_branch_turn(
                session=session,
                story=story,
                state=state,
                current_node=current,
                message=message,
                lanlan_name=lanlan_name,
                config_manager=config_manager,
                response_focus=response_focus,
            )
            prepared_performance = branch_turn["performance"]
            branch_target = branch_turn["target_node"]
            ending_override = branch_turn.get("ending")
        else:
            # 非支线回合继续使用作者稳定 Choice，并保持真实猫娘名与公开 Trace 一致。
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
        elif isinstance(state.get("active_runtime_branch"), dict) and state.get(
            "active_runtime_branch"
        ):
            # 作者完成表达已优先检查；自由输入先做一次无状态轻分类，严格区分续演和显式转交。
            current_phase = str(
                current.get("belong_phase") or session.get("phase") or "setup"
            )
            current_scene = story_loader.scene_for_phase(story, current_phase)
            active_branch = state["active_runtime_branch"]
            handoff_route = await llm.classify_active_branch_handoff_async(
                config_manager=config_manager,
                story=story,
                scene=current_scene,
                user_message=message,
                state=state,
                recent_turns=list(session.get("turns") or []),
                active_branch=active_branch,
            )
            if (
                str(handoff_route.get("route_delivery") or "") == "accepted"
                and str(handoff_route.get("classification") or "") == "intent_handoff"
                and _active_branch_handoff_is_verified(message, handoff_route)
            ):
                branch_turn = _prepare_active_branch_handoff(
                    session=session,
                    story=story,
                    state=state,
                    current_node=current,
                    message=message,
                    handoff_route=handoff_route,
                )
            elif (
                str(handoff_route.get("route_delivery") or "") == "accepted"
                and str(handoff_route.get("classification") or "") == "continue_branch"
                and _active_branch_continue_is_verified(handoff_route)
            ):
                # 即使分类器替身绕过严格解析，进入事实提交链前仍按本轮原话重验焦点。
                response_focus = llm.verify_response_focus(
                    handoff_route.get("response_focus"),
                    user_message=message,
                )
                branch_turn = await _prepare_active_runtime_branch_turn(
                    session=session,
                    story=story,
                    state=state,
                    current_node=current,
                    message=message,
                    lanlan_name=lanlan_name,
                    config_manager=config_manager,
                    response_focus=response_focus,
                )
            else:
                # 无法确认语义时不能把潜在新意图交给旧支线提交事实，也不能消耗玩家支线预算。
                branch_turn = _prepare_technical_degraded_active_branch_turn(
                    story=story,
                    state=state,
                    current_node=current,
                    message=message,
                    lanlan_name=lanlan_name,
                )
            prepared_performance = branch_turn["performance"]
            branch_target = branch_turn["target_node"]
            ending_override = branch_turn.get("ending")
        else:
            # 自由输入先做纯路由：模型同时看到公开上下文、当前推荐边和隐藏边，但只能返回白名单 ID。
            # 路由完成后才提交作者节点并生成台词，避免出现“猫娘说动作完成、权威状态却没有推进”的双重现实。
            current_phase = str(
                current.get("belong_phase") or session.get("phase") or "setup"
            )
            current_scene = story_loader.scene_for_phase(story, current_phase)
            # 旧存档或上一回合留下的 Pending 先按目标节点、Scene 和 revision 做确定性筛选。
            state["pending_intent"] = _revalidatable_pending_intent(
                state.get("pending_intent"),
                current_node_id=str(current.get("node_id") or ""),
                current_scene_id=str(current_scene.get("id") or ""),
                current_revision=session_store.state_revision(session),
            )
            # 保存本轮已经通过节点、Scene 与 revision 重验的只读快照；Router 返回后原 Pending 仍立即消费。
            pending_for_route = (
                dict(state["pending_intent"])
                if isinstance(state.get("pending_intent"), dict)
                else {}
            )
            route_choices = story_graph.suggestion_options(
                story, state, lanlan_name=lanlan_name
            )
            latent_transitions = story_graph.latent_transition_options(story, state)
            route = await llm.route_free_input_async(
                config_manager=config_manager,
                story=story,
                scene=current_scene,
                user_message=message,
                state=state,
                recent_turns=list(session.get("turns") or []),
                choice_options=route_choices,
                latent_transitions=latent_transitions,
            )
            matched_choice_id = str(route.get("matched_choice_id") or "")
            # 即使测试替身或未来适配器绕过 Router 解析器，提交边界仍要复核焦点来自本轮原话。
            response_focus = llm.verify_response_focus(
                route.get("response_focus"),
                user_message=message,
            )
            route_technical_degraded = (
                str(route.get("route_delivery") or "") == "technical_degraded"
            )
            # Pending 只能参与本次 Router 重验；正常语义结果会消费它，技术降级则保留一次重试机会。
            state["pending_intent"] = {}
            # 旧键只兼容升级前的内部测试/缓存形态；v2.5 Router 统一返回 authored_intent_id。
            authored_intent_id = str(
                route.get("authored_intent_id") or route.get("observed_intent_id") or ""
            )
            if route_technical_degraded:
                # Router 技术故障不是玩家语义 idle：保留已重验 Pending、动态线程和作者隐藏意图计数。
                state["pending_intent"] = pending_for_route
            elif matched_choice_id:
                choice = next(
                    (
                        dict(item)
                        for item in route_choices
                        if item["choice_id"] == matched_choice_id
                    ),
                    {},
                )
                residual_intent = (
                    dict(route.get("residual_intent"))
                    if isinstance(route.get("residual_intent"), dict)
                    else {}
                )
                # 作者 Choice 优先，旧通用意图不能跨过即将发生的节点提交继续累计。
                intent_tracker.clear_dynamic_intent(state)
            elif authored_intent_id:
                # 作者隐藏边比通用动态支线拥有更完整的作者语义，因此命中时清除通用意图。
                intent_tracker.clear_dynamic_intent(state)
                latent_transition = story_graph.resolve_latent_transition(
                    latent_transitions,
                    authored_intent_id,
                )
                if latent_transition and rules.record_latent_intent(
                    state, latent_transition
                ):
                    # 超过作者允许的留步次数后，隐藏边与普通推荐边一样只提交作者静态目标。
                    choice = {
                        "choice_id": str(latent_transition.get("transition_id") or ""),
                        "target_node_id": str(
                            latent_transition.get("target_node_id") or ""
                        ),
                        "callback": str(latent_transition.get("callback") or ""),
                        "transition_id": str(
                            latent_transition.get("transition_id") or ""
                        ),
                    }
                elif latent_transition:
                    # 前两次仍留在原节点；只把意图语义作为内部演绎要求，不把 ID、次数或玩法规则交给角色。
                    pullback_intent_summary = str(
                        latent_transition.get("intent_summary") or ""
                    )
            elif str(route.get("route_kind") or "") == "free_intent" and isinstance(
                route.get("free_intent"), dict
            ):
                free_intent = route["free_intent"]
                relation = str(free_intent.get("relation") or "")
                current_dynamic_intent = state.get("dynamic_intent")
                confirmed_pending_evidence = ""
                if (
                    not current_dynamic_intent
                    and pending_for_route
                    and relation in {"continue", "refine"}
                ):
                    # Pending 单独不计数；只有本轮路由明确承接时，才把上一轮原话作为第一条证据。
                    confirmed_pending_evidence = str(
                        pending_for_route.get("evidence_excerpt") or ""
                    )
                # UUID 只由服务端生成；continue/refine 时 tracker 会保留已有身份并忽略这个候选 ID。
                state["dynamic_intent"] = intent_tracker.update_dynamic_intent(
                    current_dynamic_intent,
                    new_intent_key=f"intent_{uuid.uuid4()}",
                    summary=str(free_intent.get("summary") or ""),
                    relation=relation,
                    evidence_message=message,
                    origin_node_id=str(current.get("node_id") or ""),
                    confirmed_pending_evidence=confirmed_pending_evidence,
                )
                # 通用意图已占用本轮语义；作者隐藏边的旧连续计数不能同时保留。
                rules.clear_latent_intent_tracking(state)
                active_branch, prepared_entry = await _prepare_runtime_branch_entry(
                    session=session,
                    story=story,
                    state=state,
                    current_node=current,
                    current_scene=current_scene,
                    message=message,
                    lanlan_name=lanlan_name,
                    config_manager=config_manager,
                    response_focus=response_focus,
                )
                if active_branch is not None and prepared_entry is not None:
                    # Patch 与入口演出都只存在于候选 Session；最终 revision 重验通过后才一起落盘。
                    state["active_runtime_branch"] = active_branch
                    prepared_performance = prepared_entry
            else:
                # 普通闲聊只给同节点意图一次短暂休眠；连续第二次 idle 才清理，且休眠态不能规划。
                rules.clear_latent_intent_tracking(state)
                intent_tracker.mark_dynamic_intent_idle(
                    state,
                    current_node_id=str(current.get("node_id") or ""),
                )
            if choice:
                choice["label"] = message
                progress_kind = "graph_progress"

    target = current
    if choice:
        active_branch = state.get("active_runtime_branch")
        if isinstance(active_branch, dict) and active_branch:
            # 作者 Choice 优先退出活动支线且不消耗预算；正式事实保留在结构化 History 中。
            _, branch_decision = branch_lifecycle.advance_active_branch(
                active_branch,
                event="author_choice",
            )
            _finish_runtime_branch(
                state,
                story=story,
                active_branch=active_branch,
                decision=branch_decision,
                ended_revision=session_store.state_revision(session) + 1,
            )
        target = story_graph.node_by_id(story, str(choice.get("target_node_id") or ""))
        rules.apply_node(story, state, target)
        if choice.get("transition_id"):
            # apply_node 会清除局部计数；随后保存稳定边身份，供作者汇流节点保留已经发生的支线余波。
            rules.commit_latent_transition(state, str(choice["transition_id"]))

    if branch_target is not None:
        # Goal 汇流或安全退出可以把同一回合的公开投影切到作者声明目标；普通支线回合仍停留当前节点。
        target = branch_target
    phase = str(target.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    residual_evidence = _verified_residual_evidence_excerpt(
        message,
        str(residual_intent.get("evidence_excerpt") or ""),
    )
    if choice and residual_intent and residual_evidence:
        # Choice 已经成为目标节点权威状态后，才为能在玩家原话中逐字找到的后半句创建待重验对象。
        state["pending_intent"] = branch_lifecycle.build_pending_intent(
            summary=str(residual_intent.get("summary") or ""),
            evidence_excerpt=residual_evidence,
            source_node_id=str(current.get("node_id") or ""),
            target_node_id=str(target.get("node_id") or ""),
            target_scene_id=str(scene.get("id") or ""),
            created_revision=session_store.state_revision(session) + 1,
        )
    # 演绎只读取路由完成后的真实状态与下一组稳定 Choice；它不再返回任何推进或支线 ID。
    choice_options = story_graph.suggestion_options(
        story, state, lanlan_name=lanlan_name
    )
    if prepared_performance is None:
        # 普通回合和不可执行的入口候选沿用现有安全演绎；模型抖动已在入口层转成可提交安全演出。
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
            pullback_intent_summary=pullback_intent_summary,
            # 普通 Actor 只召回已结束 History 精确索引的公开事实，不读取活动支线或裸服务端身份。
            completed_branch_recall=branch_runtime.completed_branch_recall(
                story=story,
                state=state,
            ),
            response_focus=response_focus,
        )
    else:
        # 支线入口与后续支线回合都复用统一投影，不把内部分类泄漏到 scenario_trace。
        performance = prepared_performance
    if progress_kind == "graph_progress":
        author_dialogue = str(target.get("scripted_dialogue") or "")
        # 没有独立焦点时维持作者对白完全相等；有焦点时只保留 Actor 的即时补充，再逐字追加作者正文。
        performance["dialogue"] = _compose_graph_progress_dialogue(
            author_dialogue=author_dialogue,
            generated_dialogue=str(performance.get("dialogue") or ""),
            response_focus=response_focus,
        )
    # Actor 输出中的 Choice 改写只为旧模型响应兼容而丢弃；玩家始终看到作者原文。
    performance.pop("choice_rewrites", None)
    # Branch Actor 的事实候选已经在服务端合同层消费，绝不能进入公开响应或 turns 文本。
    performance.pop("fact_candidates", None)
    if progress_kind == "roleplay_response":
        # 未明确命中唯一 Choice 的自由互动只形成非权威笔记，不参与静态可达性和结局判断。
        rules.append_scene_note(state, message)
        # 清除旧 Session 遗留的显示覆盖；自由互动不能取得作者 Choice 文案权。
        state.pop("choice_label_overrides", None)
    outgoing = story_graph.outgoing_nodes(story, state)
    ending = ending_override or rules.ending_for_state(
        story, state, target, has_outgoing=bool(outgoing)
    )
    if progress_kind == "roleplay_response" and ending_override is None:
        # 单纯对话不能因为当前节点暂无出口而自动结束，正式结束只发生在剧情推进后。
        ending = {
            "should_offer_ending": False,
            "should_end_session": False,
            "ending_id": "",
        }

    session["phase"] = phase
    session["story_state"] = state
    trace = projector.scenario_trace(
        progress_kind=progress_kind,
        choice=choice or dynamic_choice,
    )
    _append_turns(session, message=message, performance=performance, trace=trace)
    if ending.get("should_end_session"):
        session["ended_at"] = _now_ms()
        ending_reason = str(ending.get("reason") or "story_complete")
        session["end_reason"] = (
            ending_reason
            if ending_reason in session_store.SESSION_END_REASONS
            else "story_complete"
        )
        session.pop("dormant_at", None)

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
    # 只在完整候选已经形成后暴露最终有效焦点；失败回合的私有容器不会落盘。
    turn_diagnostic["response_focus"] = deepcopy(response_focus)
    return response
