"""提供轻量小剧场的生命周期外观。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import (
    branch_contracts,
    branch_lifecycle,
    projector,
    rules,
    session_store,
    story_graph,
    story_loader,
    turn_service,
)


# 只保留近期已朗读 revision，既覆盖网络重试又避免长剧本存档无限增长。
MAX_SPOKEN_DIALOGUE_REVISIONS = 32
# 这些恢复错误都要求保留原文件和 active 指针，等待用户明确替换或后续兼容迁移。
PRESERVED_RESTORE_ERRORS = frozenset(
    {
        "session_upgrade_required",
        "session_version_unsupported",
        "session_story_unavailable",
        "session_story_revision_mismatch",
        "session_state_invalid",
        "session_snapshot_missing",
    }
)


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
        if (
            config_manager is not None
            and await _current_catgirl_name(config_manager) != normalized_name
        ):
            # 请求等待旧角色锁期间可能已经切换猫娘；锁内重验失败时不能创建孤立的旧角色 Session。
            return {"ok": False, "reason": "session_character_mismatch"}
        active_session_id = await session_store.get_active_session_id(
            root, normalized_name
        )
        active_session: dict[str, Any] | None = None
        if active_session_id:
            (
                active_session,
                active_reason,
            ) = await session_store.load_session_with_status(root, active_session_id)
            restore_error = active_reason or await _active_session_restore_error(
                active_session
            )
            if restore_error in PRESERVED_RESTORE_ERRORS:
                if replace_incompatible_session is not True:
                    # 所有不可安全解释的存档都等待玩家明确重开，旧客户端也不能绕过前端提示。
                    return {
                        "ok": False,
                        "reason": restore_error,
                        "session_id": active_session_id,
                    }
                # 不兼容旧文件必须逐字保留；新 Session 完整落盘后只原子切换活动索引。
                active_session = None
        if normalized_start_id:
            snapshot = (
                active_session.get("public_snapshot")
                if isinstance(active_session, dict)
                else None
            )
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


async def _active_session_restore_error(session: dict[str, Any] | None) -> str:
    """只读预检活动存档能否解释到当前 Story，不执行迁移或写回。"""  # noqa: DOCSTRING_CJK
    if not isinstance(session, dict):
        return ""
    try:
        story = await story_loader.load_story_exact(str(session.get("story_id") or ""))
    except (FileNotFoundError, ValueError):
        # 缺失或合同已失效的用户 Story 不能被替换成任意当前剧本。
        return "session_story_unavailable"
    _, _, restore_error = _restored_session_candidate(session, story)
    if restore_error:
        return restore_error
    if not isinstance(session.get("public_snapshot"), dict):
        return "session_snapshot_missing"
    return ""


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
    try:
        story = await story_loader.load_story(story_id)
    except FileNotFoundError:
        # 显式错误 ID 必须反馈给调用方，不能悄悄开启排序第一的其他剧本。
        return {"ok": False, "reason": "story_not_found"}
    if not story:
        return {"ok": False, "reason": "story_not_found"}
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
    # 开场对白只能来自 Story Package；缺失时保持为空，通用层不得代写人物口癖或关系状态。
    opening_dialogue = str(story.get("opening_dialogue") or "")
    opening_narration = str(scene.get("text") or "")
    # opening_dialogue 已经是作者写好的可播放正文，不再交给模型做“人格化转述”。
    # 人格与关系边界应由 Story 作者在这段对白中表达，框架只负责原样投影。
    session: dict[str, Any] = {
        "schema_version": session_store.SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "story_id": str(story.get("id") or ""),
        # Story revision 与 Session schema 分开保存；同一存档协议不能误读已改写的作者图。
        "story_revision": str(story.get("story_revision") or ""),
        "lanlan_name": str(lanlan_name or "Lan"),
        "start_client_id": start_client_id,
        "phase": phase,
        "story_state": state,
        "turns": [
            {
                "role": "assistant",
                "text": opening_dialogue,
                "narration": opening_narration,
                "created_at": now,
            }
        ],
        "state_revision": 0,
        "turn_results_by_client_id": {},
        # 仅供服务端 Bug 复盘保存每次模型原始返回；Projector 永远不读取或公开此字段。
        "llm_return_records": [],
        # 每个成功回合只保存一条私有因果摘要，便于把输入、模型返回和实际提交结果关联起来。
        "turn_causality_records": [],
        # TTS 去重只记录公开快照 revision，不保存音频或额外台词副本。
        "spoken_dialogue_revisions": [],
        "started_at": now,
        "updated_at": now,
        "ended_at": None,
    }
    ending = {
        "should_offer_ending": False,
        "should_end_session": False,
        "ending_id": "",
    }
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
    previous_snapshot = (
        deepcopy(replaced_session) if isinstance(replaced_session, dict) else None
    )
    if isinstance(replaced_session, dict):
        # 新 Session 文件先落盘，再结束旧演出；active 发布失败时可以恢复旧存档而不丢进度。
        replaced_at = _now_ms()
        replaced_session["ended_at"] = replaced_session.get("ended_at") or replaced_at
        replaced_session["end_reason"] = (
            replaced_session.get("end_reason") or "replaced_by_new_session"
        )
        # 明确终止会覆盖此前的休眠标记，避免一份存档同时表现为休眠和结束。
        replaced_session.pop("dormant_at", None)
        replaced_session["updated_at"] = replaced_at
        replaced_session["phase"] = "ended"
        replaced_public = replaced_session.get("public_snapshot")
        if isinstance(replaced_public, dict):
            replaced_public["can_resume"] = False
            replaced_public["suggestion_options"] = []
            replaced_public["phase"] = "ended"
            replaced_public["session_lifecycle"] = "ended"
        await session_store.save_session(root, replaced_session)
    try:
        await session_store.set_active_session(
            root, str(session["lanlan_name"]), session_id
        )
    except Exception:
        # 新 Session 从未成功发布给客户端，必须标记为终结；否则索引损坏重建时会把它误认成可恢复演出。
        failed_at = _now_ms()
        session["ended_at"] = failed_at
        session["end_reason"] = "start_publish_failed"
        session["updated_at"] = failed_at
        session["phase"] = "ended"
        failed_public = session.get("public_snapshot")
        if isinstance(failed_public, dict):
            failed_public["can_resume"] = False
            failed_public["suggestion_options"] = []
            failed_public["phase"] = "ended"
            failed_public["session_lifecycle"] = "ended"
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


async def get_state(
    root: Path, session_id: str, *, expected_lanlan_name: str = ""
) -> dict[str, Any]:
    """读取最后一次已保存的公开快照，不重新运行模型。"""  # noqa: DOCSTRING_CJK
    session, load_reason = await session_store.load_session_with_status(
        root, session_id
    )
    if session is None:
        result = {"ok": False, "reason": load_reason or "session_not_found"}
        if load_reason in {"session_upgrade_required", "session_version_unsupported"}:
            result["session_id"] = str(session_id or "")
        return result
    expected_name = str(expected_lanlan_name or "").strip()
    if expected_name and str(session.get("lanlan_name") or "").strip() != expected_name:
        # 恢复本地 Session 指针前校验角色归属，避免切换猫娘后重新打开上一人格的私有剧情。
        return {"ok": False, "reason": "session_character_mismatch"}
    try:
        story = await story_loader.load_story_exact(str(session.get("story_id") or ""))
    except (FileNotFoundError, ValueError):
        # Story 文件缺失或合同已无法加载时保留原 Session，不能猜测迁移到其他剧本。
        return {
            "ok": False,
            "reason": "session_story_unavailable",
            "session_id": str(session_id or ""),
        }
    session, restore_error = await _reconcile_session_on_restore(
        root, session_id, session, story
    )
    if restore_error:
        return {
            "ok": False,
            "reason": restore_error,
            "session_id": str(session_id or ""),
        }
    # 恢复只保留仍位于目标节点和 revision 窗口内的 Pending；私有清理不改变公开 revision。
    session = await _clear_invalid_pending_on_restore(root, session_id, session)
    snapshot = session.get("public_snapshot")
    if not isinstance(snapshot, dict):
        # 所有保留型恢复错误都带回原 Session ID，前端才能维持同一份本地恢复指针。
        return {
            "ok": False,
            "reason": "session_snapshot_missing",
            "session_id": str(session_id or ""),
        }
    result = deepcopy(snapshot)
    stale = await session_store.is_stale_session(root, session)
    result["stale"] = stale
    result["can_resume"] = not stale and not bool(session.get("ended_at"))
    # 旧快照可能没有生命周期字段；每次读取都以 Session 私有时间戳重新派生。
    result["session_lifecycle"] = projector.session_lifecycle(session)
    result["state_revision"] = session_store.state_revision(session)
    if not result["can_resume"]:
        result["suggestion_options"] = []
    return result


async def _reconcile_session_on_restore(
    root: Path,
    session_id: str,
    session: dict[str, Any],
    story: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """在 Session 锁内迁移中性默认值、重验活动支线并同步公开快照。"""  # noqa: DOCSTRING_CJK
    candidate, changed, error = _restored_session_candidate(session, story)
    if error or not changed:
        return candidate, error
    async with session_store.session_guard(session_id):
        # 锁外判断后必须重读并重新计算，避免覆盖刚提交的新回合或支线状态。
        latest = await session_store.load_session(root, session_id)
        if latest is None:
            return session, "session_not_found"
        candidate, changed, error = _restored_session_candidate(latest, story)
        if error or not changed:
            return candidate, error
        rebuilt = _rebuild_restored_snapshot(candidate, story)
        if rebuilt is None:
            return latest, "session_snapshot_missing"
        candidate["public_snapshot"] = rebuilt
        await session_store.save_session(root, candidate)
        return candidate, ""


def _restored_session_candidate(
    session: dict[str, Any],
    story: dict[str, Any],
) -> tuple[dict[str, Any], bool, str]:
    """返回不修改输入的兼容候选；无法可靠恢复时只返回明确错误。"""  # noqa: DOCSTRING_CJK
    candidate = deepcopy(session)
    state = candidate.get("story_state")
    if not isinstance(state, dict):
        return candidate, False, "session_state_invalid"
    current_story_revision = str(story.get("story_revision") or "").strip()
    stored_story_revision = str(candidate.get("story_revision") or "").strip()
    if stored_story_revision and stored_story_revision != current_story_revision:
        return candidate, False, "session_story_revision_mismatch"

    changed = False
    dormant_at = candidate.get("dormant_at")
    if not session_store.lifecycle_fields_valid(candidate):
        # 休眠时间和终止原因控制持久化生命周期；坏值不能被 truthy 文本绕过或静默修复。
        return candidate, False, "session_state_invalid"
    if candidate.get("ended_at") and dormant_at is not None:
        # 已结束状态严格高于休眠；这个中性修复不改变任何剧情事实或公开 revision。
        candidate.pop("dormant_at", None)
        changed = True
    if not stored_story_revision:
        # 同 schema 的早期存档只补当前已成功加载 Story 的 revision，不迁移作者节点或剧情事实。
        candidate["story_revision"] = current_story_revision
        changed = True
    neutral_defaults: tuple[tuple[str, object, type], ...] = (
        ("dynamic_intent", {}, dict),
        ("pending_intent", {}, dict),
        ("active_runtime_branch", {}, dict),
        ("branch_facts", [], list),
        ("completed_goal_ids", [], list),
        ("branch_history", [], list),
    )
    for field, default, expected_type in neutral_defaults:
        if field not in state:
            state[field] = deepcopy(default)
            changed = True
        elif not isinstance(state.get(field), expected_type):
            # 权威事实和历史类型损坏时不能用空值覆盖；完整保留文件交给明确修复流程。
            return candidate, False, "session_state_invalid"

    goal_ids = {
        str(item.get("goal_id") or "")
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict)
    }
    if any(
        not isinstance(item, str) or item not in goal_ids
        for item in state["completed_goal_ids"]
    ):
        return candidate, False, "session_state_invalid"
    if len(state["completed_goal_ids"]) != len(set(state["completed_goal_ids"])):
        return candidate, False, "session_state_invalid"
    try:
        for fact in state["branch_facts"]:
            validated_fact = branch_contracts.validate_committed_branch_fact_structure(
                fact,
                story=story,
            )
            if int(validated_fact["source_revision"]) > session_store.state_revision(
                candidate
            ):
                raise ValueError("Committed Branch Fact is newer than Session")
        for history in state["branch_history"]:
            validated_history = branch_contracts.validate_branch_history_entry(
                history,
                story=story,
                branch_facts=state["branch_facts"],
            )
            if int(validated_history["ended_revision"]) > session_store.state_revision(
                candidate
            ):
                raise ValueError("Branch History is newer than Session")
    except ValueError:
        # 已提交事实和 History 属于权威用户进度；结构损坏时保留原文件，绝不丢弃或猜测改写。
        return candidate, False, "session_state_invalid"
    active_branch = state.get("active_runtime_branch")
    if isinstance(active_branch, dict) and active_branch:
        valid, repairable = _restored_active_branch_status(
            active_branch,
            state=state,
            story=story,
            session_revision=session_store.state_revision(candidate),
        )
        if not valid:
            if not repairable:
                return candidate, False, "session_state_invalid"
            if not _close_invalid_restored_branch(
                state,
                story,
                active_branch,
                session_store.state_revision(candidate),
            ):
                return candidate, False, "session_state_invalid"
            changed = True
    candidate["story_state"] = state
    return candidate, changed, ""


def _restored_active_branch_status(
    active_branch: dict[str, Any],
    *,
    state: dict[str, Any],
    story: dict[str, Any],
    session_revision: int,
) -> tuple[bool, bool]:
    """区分合法活动支线、可回锚点关闭的损坏支线和不可安全处理的状态。"""  # noqa: DOCSTRING_CJK
    branch_id = str(active_branch.get("branch_id") or "").strip()
    anchor = active_branch.get("return_anchor")
    anchor_id = (
        str(anchor.get("node_id") or "").strip() if isinstance(anchor, dict) else ""
    )
    repairable = bool(
        branch_id and anchor_id and story_graph.node_by_id(story, anchor_id)
    )
    if not branch_lifecycle.active_runtime_branch_is_valid(active_branch):
        return False, repairable
    if int(active_branch.get("created_revision") or 0) > session_revision:
        return False, repairable
    try:
        validated_patch = branch_contracts.validate_runtime_branch_patch(
            active_branch.get("patch"),
            story=story,
            current_node_id=str(state.get("current_node_id") or ""),
            # 恢复时重用激活校验，旧 Patch 若指向已完成 Goal 就按损坏活动状态安全关闭。
            completed_goal_ids=list(state.get("completed_goal_ids") or []),
        )
    except ValueError:
        return False, repairable
    for fact in state.get("branch_facts") or []:
        if not isinstance(fact, dict) or str(fact.get("branch_id") or "") != branch_id:
            continue
        source_revision = fact.get("source_revision")
        if (
            not str(fact.get("fact_id") or "").strip()
            or type(source_revision) is not int
            or source_revision < int(active_branch.get("created_revision") or 0)
            or source_revision > session_revision
        ):
            return False, repairable
        try:
            # 活动支线仍保存原 Patch；恢复时必须防止事实被改绑到同槽另一个合法目录成员。
            branch_contracts.validate_committed_branch_fact_against_patch(
                fact,
                story=story,
                patch=validated_patch,
            )
        except ValueError:
            # 事实与原 Patch 分叉时不能安全保留并自动回锚，原文件留给玩家明确处理。
            return False, False
    return True, repairable


def _close_invalid_restored_branch(
    state: dict[str, Any],
    story: dict[str, Any],
    active_branch: dict[str, Any],
    session_revision: int,
) -> bool:
    """保留已提交事实，以明确恢复原因记录 History，并回到服务端保存的作者锚点。"""  # noqa: DOCSTRING_CJK
    branch_id = str(active_branch.get("branch_id") or "").strip()
    anchor = (
        active_branch.get("return_anchor")
        if isinstance(active_branch.get("return_anchor"), dict)
        else {}
    )
    anchor_id = str(anchor.get("node_id") or "").strip()
    facts = [item for item in state.get("branch_facts") or [] if isinstance(item, dict)]
    key_fact_ids = [
        str(item.get("fact_id") or "")
        for item in facts
        if str(item.get("branch_id") or "") == branch_id
        and str(item.get("fact_id") or "")
    ]
    try:
        history = branch_contracts.validate_branch_history_entry(
            {
                "branch_id": branch_id,
                "completed_goal_ids": [],
                "key_fact_ids": key_fact_ids,
                "exit_kind": "restore_invalid",
                "ended_revision": session_revision,
            },
            story=story,
            branch_facts=facts,
        )
    except ValueError:
        return False
    state["current_node_id"] = anchor_id
    state["branch_history"] = [
        *[item for item in state.get("branch_history") or [] if isinstance(item, dict)],
        history,
    ]
    state["active_runtime_branch"] = {}
    state["dynamic_intent"] = {}
    return True


def _rebuild_restored_snapshot(
    session: dict[str, Any], story: dict[str, Any]
) -> dict[str, Any] | None:
    """用私有权威状态重建可变公开投影，同时保留最后一次已提交演出文本。"""  # noqa: DOCSTRING_CJK
    snapshot = session.get("public_snapshot")
    state = session.get("story_state")
    if not isinstance(snapshot, dict) or not isinstance(state, dict):
        return None
    node = story_graph.current_node(story, state)
    if not node:
        return None
    phase = str(node.get("belong_phase") or session.get("phase") or "setup")
    scene = story_loader.scene_for_phase(story, phase)
    narration = (
        snapshot.get("narration") if isinstance(snapshot.get("narration"), dict) else {}
    )
    dialogue = (
        snapshot.get("dialogue") if isinstance(snapshot.get("dialogue"), dict) else {}
    )
    ending = snapshot.get("ending") if isinstance(snapshot.get("ending"), dict) else {}
    session["phase"] = phase
    return projector.public_response(
        session=session,
        story=story,
        scene=scene,
        narration=str(narration.get("text") or ""),
        dialogue=str(dialogue.get("text") or ""),
        trace=deepcopy(snapshot.get("scenario_trace"))
        if isinstance(snapshot.get("scenario_trace"), dict)
        else None,
        ending=deepcopy(ending),
        can_resume=not bool(session.get("ended_at")),
    )


async def _clear_invalid_pending_on_restore(
    root: Path,
    session_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """在 Session 锁内清除过期、坏结构或已离开目标节点的 Pending Intent。"""  # noqa: DOCSTRING_CJK
    if _pending_restore_decision(session) in {"", "revalidate"}:
        return session
    async with session_store.session_guard(session_id):
        # 锁外判断后重新读盘，避免覆盖并发回合刚提交的新 revision 或新 Pending。
        latest = await session_store.load_session(root, session_id)
        if latest is None:
            return session
        if _pending_restore_decision(latest) in {"", "revalidate"}:
            return latest
        state = (
            latest.get("story_state")
            if isinstance(latest.get("story_state"), dict)
            else {}
        )
        state["pending_intent"] = {}
        latest["story_state"] = state
        await session_store.save_session(root, latest)
        return latest


def _pending_restore_decision(session: dict[str, Any]) -> str:
    """按已提交公开 Scene 与私有节点计算恢复时的 Pending 状态。"""  # noqa: DOCSTRING_CJK
    state = (
        session.get("story_state")
        if isinstance(session.get("story_state"), dict)
        else {}
    )
    pending = state.get("pending_intent")
    if not isinstance(pending, dict) or not pending:
        return ""
    snapshot = (
        session.get("public_snapshot")
        if isinstance(session.get("public_snapshot"), dict)
        else {}
    )
    scene = snapshot.get("scene") if isinstance(snapshot.get("scene"), dict) else {}
    try:
        return branch_lifecycle.evaluate_pending_intent(
            pending,
            current_node_id=str(state.get("current_node_id") or ""),
            current_scene_id=str(scene.get("scene_id") or ""),
            current_revision=session_store.state_revision(session),
            # 恢复没有新的玩家输入；否定和语义兼容仍留到下一次 Router 重验。
            player_denied=False,
            scene_compatible=True,
        )
    except ValueError:
        # 损坏的私有辅助字段不能阻断公开快照恢复。
        return "discard_invalid"


async def claim_dialogue_speech(
    root: Path,
    *,
    session_id: str,
    state_revision: Any,
    expected_lanlan_name: str = "",
    play: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """原子认领公开对白；传入播放器时在同一角色边界内提交 TTS。"""  # noqa: DOCSTRING_CJK
    if (
        not isinstance(state_revision, int)
        or isinstance(state_revision, bool)
        or state_revision < 0
    ):
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
            if (
                expected_name
                and str(session.get("lanlan_name") or "").strip() != expected_name
            ):
                # 角色已切换时不写入已朗读 revision，避免旧猫娘对白占用新角色的播放权。
                return {
                    "ok": True,
                    "skipped": "character_changed",
                    "state_revision": current_revision,
                }
            snapshot = session.get("public_snapshot")
            ending = snapshot.get("ending") if isinstance(snapshot, dict) else None
            committed_terminal = bool(
                session.get("ended_at")
                and isinstance(ending, dict)
                and ending.get("should_end_session") is True
            )
            if await session_store.is_stale_session(root, session) or (
                session.get("ended_at") and not committed_terminal
            ):
                # 新开场替换和管理性关闭都拒绝播放；只保留已提交终局快照的最后一句。
                return {
                    "ok": True,
                    "skipped": "stale_session",
                    "state_revision": current_revision,
                }
            if state_revision != current_revision:
                return {
                    "ok": True,
                    "skipped": "stale_revision",
                    "state_revision": current_revision,
                }
            dialogue = snapshot.get("dialogue") if isinstance(snapshot, dict) else None
            line = (
                str(dialogue.get("text") or "").strip()
                if isinstance(dialogue, dict)
                else ""
            )
            if not line:
                return {
                    "ok": True,
                    "skipped": "empty_dialogue",
                    "state_revision": current_revision,
                }

            spoken = session.setdefault("spoken_dialogue_revisions", [])
            normalized_spoken = [
                item
                for item in spoken
                if isinstance(item, int) and not isinstance(item, bool)
            ]
            if current_revision in normalized_spoken:
                return {
                    "ok": True,
                    "skipped": "already_spoken",
                    "state_revision": current_revision,
                }
            normalized_spoken.append(current_revision)
            session["spoken_dialogue_revisions"] = normalized_spoken[
                -MAX_SPOKEN_DIALOGUE_REVISIONS:
            ]
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
        active_session_id = await session_store.get_active_session_id(
            root, normalized_name
        )
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
                if (
                    await session_store.get_active_session_id(root, normalized_name)
                    != active_session_id
                ):
                    continue
                await publish()
                try:
                    session = await session_store.load_session(root, active_session_id)
                    if session is not None:
                        now = _now_ms()
                        session["ended_at"] = session.get("ended_at") or now
                        session["end_reason"] = (
                            session.get("end_reason") or "character_switch"
                        )
                        session.pop("dormant_at", None)
                        session["updated_at"] = now
                        session["phase"] = "ended"
                        snapshot = session.get("public_snapshot")
                        if isinstance(snapshot, dict):
                            snapshot["can_resume"] = False
                            snapshot["suggestion_options"] = []
                            snapshot["phase"] = "ended"
                            snapshot["session_lifecycle"] = "ended"
                        await session_store.save_session(root, session)
                    await session_store.clear_active_session(
                        root, normalized_name, active_session_id
                    )
                except Exception as exc:
                    # 当前猫娘已经成功发布时不能重复写配置；返回清理错误交给角色 Router 记录。
                    return {
                        "ok": True,
                        "published": True,
                        "cleared": False,
                        "cleanup_error": type(exc).__name__,
                    }
                return {
                    "ok": True,
                    "published": True,
                    "cleared": True,
                    "session_id": active_session_id,
                }


async def get_active_state(root: Path, *, lanlan_name: str) -> dict[str, Any]:
    """恢复当前猫娘最后一场仍可继续的演出。"""  # noqa: DOCSTRING_CJK
    session_id = await session_store.get_active_session_id(root, lanlan_name)
    if not session_id:
        return {"ok": False, "reason": "active_session_not_found"}
    result = await get_state(root, session_id, expected_lanlan_name=lanlan_name)
    if result.get("reason") in PRESERVED_RESTORE_ERRORS:
        # 不兼容存档必须保留文件和活动索引，由玩家看到明确提示后决定是否新开演出。
        return result
    if result.get("ok") is not True or result.get("can_resume") is not True:
        await session_store.clear_active_session(root, lanlan_name, session_id)
        return {"ok": False, "reason": "active_session_not_found"}
    return result


async def end_session(
    root: Path,
    *,
    session_id: str,
    end_reason: str = "management_end",
) -> dict[str, Any]:
    """管理性关闭 Session，不生成剧情结局。"""  # noqa: DOCSTRING_CJK
    normalized_reason = (
        end_reason
        if end_reason in session_store.SESSION_END_REASONS
        else "management_end"
    )
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
                session["end_reason"] = normalized_reason
                session.pop("dormant_at", None)
                session["updated_at"] = session["ended_at"]
                session["phase"] = "ended"
                snapshot = session.get("public_snapshot")
                if isinstance(snapshot, dict):
                    snapshot["can_resume"] = False
                    snapshot["suggestion_options"] = []
                    snapshot["phase"] = "ended"
                    snapshot["session_lifecycle"] = "ended"
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
    result = await end_session(
        root,
        session_id=session_id,
        end_reason="character_switch",
    )
    if result.get("ok") is not True:
        # 旧协议或损坏 Session 无法进入 Runtime，但角色切换仍必须清掉其活动索引。
        await session_store.clear_active_session(root, lanlan_name, session_id)
        return {"ok": True, "cleared": True, "session_id": session_id}
    return {
        "ok": bool(result.get("ok")),
        "cleared": bool(result.get("ok")),
        "session_id": session_id,
    }


def _now_ms() -> int:
    """返回毫秒时间戳。"""  # noqa: DOCSTRING_CJK
    return int(time.time() * 1000)
