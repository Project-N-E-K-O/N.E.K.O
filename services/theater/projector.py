"""把轻量私有状态投影成前端可以安全显示的响应。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any

from . import branch_contracts, branch_runtime, story_graph, story_loader


def session_lifecycle(session: dict[str, Any]) -> str:
    """从私有时间戳派生公开生命周期，不把休眠误报为剧情结束。"""  # noqa: DOCSTRING_CJK
    if session.get("ended_at"):
        return "ended"
    if session.get("dormant_at"):
        return "dormant"
    return "active"


def scenario_board(story: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """生成简化 Board，只公开名称、提示和已确认线索。"""  # noqa: DOCSTRING_CJK
    props = _prop_index(story)
    clues = _clue_index(story)
    available = [props[prop_id] for prop_id in state.get("available_prop_ids") or [] if prop_id in props]
    used = [props[prop_id] for prop_id in state.get("used_prop_ids") or [] if prop_id in props]
    discovered = [clues[clue_id] for clue_id in state.get("clue_ids") or [] if clue_id in clues]
    dynamic = _dynamic_board_entities(state)
    # 动态实体复用现有三组公开结构，前端无需读取原始 Branch Fact 或维护第二套事实状态。
    available.extend(dynamic["available_props"])
    used.extend(dynamic["used_props"])
    discovered.extend(dynamic["discovered_clues"])
    return {
        "available_props": available,
        "used_props": used,
        "discovered_clues": discovered,
        "flags": list(state.get("flags") or []),
    }


def scenario_trace(
    *,
    progress_kind: str,
    choice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成前端实际展示的进度类型与玩家选择。"""  # noqa: DOCSTRING_CJK
    choice = choice or {}
    return {
        "progress_kind": progress_kind,
        "action_label": str(choice.get("label") or ""),
    }


def public_response(
    *,
    session: dict[str, Any],
    story: dict[str, Any],
    scene: dict[str, Any],
    narration: str,
    dialogue: str,
    trace: dict[str, Any] | None,
    ending: dict[str, Any],
    can_resume: bool,
) -> dict[str, Any]:
    """统一组装启动、回合和恢复响应。"""  # noqa: DOCSTRING_CJK
    state = session.get("story_state") if isinstance(session.get("story_state"), dict) else {}
    # 推荐对白必须使用当前 Session 的真实猫娘名，恢复页面时也不能退回作者占位符。
    options = []
    if can_resume:
        active_branch = state.get("active_runtime_branch")
        if isinstance(active_branch, dict) and active_branch:
            # 活动支线只公开当前玩家行动，避免已提交自由选择旁边继续出现互相竞争的作者物件。
            options = branch_runtime.dynamic_choice_options(
                active_branch,
                state.get("branch_facts") or [],
            )
        else:
            # 支线关闭后恢复作者静态推荐；自然语言作者完成入口仍由服务端独立保留。
            options = story_graph.suggestion_options(
                story,
                state,
                lanlan_name=str(session.get("lanlan_name") or "猫娘"),
            )
    public_options = [
        {key: option[key] for key in ("choice_id", "label", "choice_mode")}
        for option in options
    ]
    lanlan_name = str(session.get("lanlan_name") or "猫娘")
    public_scene = story_loader.public_scene(scene)
    public_scene["text"] = story_graph.render_story_text(
        public_scene.get("text"), lanlan_name
    )
    response = {
        "ok": True,
        "session_id": str(session.get("session_id") or ""),
        "story_id": str(session.get("story_id") or ""),
        "state_revision": int(session.get("state_revision") or 0),
        "phase": str(session.get("phase") or "setup"),
        "scene": public_scene,
        "narration": {
            "text": story_graph.render_story_text(narration, lanlan_name)
        },
        "dialogue": {
            "text": story_graph.render_story_text(dialogue, lanlan_name)
        },
        "scenario_board": scenario_board(story, state),
        "scenario_trace": trace,
        "suggestion_options": public_options,
        "ending": dict(ending),
        "can_resume": bool(can_resume),
        "session_lifecycle": session_lifecycle(session),
        "stale": False,
    }
    return response


def _prop_index(story: dict[str, Any]) -> dict[str, dict[str, str]]:
    """索引轻量 stage_props 的公开字段。"""  # noqa: DOCSTRING_CJK
    result: dict[str, dict[str, str]] = {}
    for prop in story.get("stage_props") or []:
        if isinstance(prop, dict) and prop.get("id"):
            result[str(prop["id"])] = {
                "id": str(prop["id"]),
                "label": str(prop.get("label") or prop["id"]),
                "public_hint": str(prop.get("public_hint") or ""),
            }
    return result


def _clue_index(story: dict[str, Any]) -> dict[str, dict[str, str]]:
    """索引线索公开文字，忽略 hidden_meaning。"""  # noqa: DOCSTRING_CJK
    result: dict[str, dict[str, str]] = {}
    for clue in story.get("clues") or []:
        if isinstance(clue, dict) and clue.get("id"):
            result[str(clue["id"])] = {
                "id": str(clue["id"]),
                "title": str(clue.get("title") or clue["id"]),
                "public_text": str(clue.get("public_text") or ""),
            }
    return result


def _dynamic_board_entities(state: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """把带服务端身份的公开实体投影到 Board，不复制任何权威事实字段。"""  # noqa: DOCSTRING_CJK
    # 同一 entity_id 若在恢复或迁移数据中出现多次，只采用最高 revision、最后出现的公开状态。
    latest_entities: dict[str, tuple[int, int, dict[str, str]]] = {}
    for index, fact in enumerate(state.get("branch_facts") or []):
        if not _is_committed_public_fact(fact):
            continue
        entity = fact["public_entity"]
        entity_id = str(entity["entity_id"]).strip()
        candidate = (int(fact["source_revision"]), index, entity)
        previous = latest_entities.get(entity_id)
        if previous is None or candidate[:2] > previous[:2]:
            latest_entities[entity_id] = candidate

    result: dict[str, list[dict[str, str]]] = {
        "available_props": [],
        "used_props": [],
        "discovered_clues": [],
    }
    for _revision, _index, entity in sorted(latest_entities.values(), key=lambda item: item[:2]):
        entity_id = str(entity["entity_id"])
        label = str(entity["label"])
        kind = str(entity["kind"])
        status = str(entity["status"])
        if kind == "clue":
            result["discovered_clues"].append({"id": entity_id, "title": label, "public_text": ""})
        elif status == "used":
            result["used_props"].append({"id": entity_id, "label": label, "public_hint": ""})
        else:
            # available 与 selected 都表示仍在当前场景中可供后续行动使用。
            result["available_props"].append({"id": entity_id, "label": label, "public_hint": ""})
    return result


def _is_committed_public_fact(value: Any) -> bool:
    """只认可具备提交身份、revision 和受支持枚举的公开实体事实。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        return False
    if not str(value.get("fact_id") or "").strip() or not str(value.get("branch_id") or "").strip():
        return False
    if type(value.get("source_revision")) is not int or int(value["source_revision"]) < 0:
        return False
    entity = value.get("public_entity")
    if not isinstance(entity, dict):
        return False
    kind = str(entity.get("kind") or "").strip()
    status = str(entity.get("status") or "").strip()
    return (
        bool(str(entity.get("entity_id") or "").strip())
        and bool(str(entity.get("label") or "").strip())
        and kind in branch_contracts.PUBLIC_ENTITY_STATUSES
        and status in branch_contracts.PUBLIC_ENTITY_STATUSES[kind]
    )
