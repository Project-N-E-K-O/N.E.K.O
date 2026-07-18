"""维护轻量小剧场的权威状态与确定性结局规则。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from typing import Any

from . import fact_view


MAX_SCENE_NOTE_CHARS = 160


def initial_state(story: dict[str, Any], *, initial_node_id: str) -> dict[str, Any]:
    """创建只包含当前版所需集合的轻量状态。"""  # noqa: DOCSTRING_CJK
    opening_facts = (
        story.get("seed", {}).get("opening_facts", [])
        if isinstance(story.get("seed"), dict)
        else []
    )
    return {
        "current_node_id": initial_node_id,
        "completed_node_ids": [],
        "narrative_facts": [
            dict(item) for item in opening_facts if isinstance(item, dict)
        ],
        "available_prop_ids": _initial_prop_ids(story, initial_node_id),
        "used_prop_ids": [],
        "clue_ids": [],
        "flags": [],
        "scene_notes": [],
        # 只覆盖当前节点按钮文案，不改变 Choice ID、目标节点或作者事实。
        # v2.4 的局部意图跟踪只服务隐藏边路由；这些字段不是角色知识，也不会投影给前端。
        "active_goal_id": "",
        "focused_intent_id": "",
        "intent_streak": 0,
        "goal_pullback_count": 0,
        # 已正式进入的隐藏边在后续汇流中保留，供作者节点读取支线余波。
        "branch_commitment": "",
        # v2.5 通用自由意图与作者隐藏边分开累计；该字段不属于公开事实。
        "dynamic_intent": {},
        # 复合输入后半句只在目标节点短期重验，不携带 streak 或事实权威。
        "pending_intent": {},
        # v2.5 活动支线只保存已验证 Patch 与服务端计数；空对象表示当前仍沿作者静态图演出。
        "active_runtime_branch": {},
        # 动态事实、已完成 Goal 与只读 History 都是服务端权威状态，Projector 只公开其允许展示的派生结果。
        "branch_facts": [],
        "completed_goal_ids": [],
        "branch_history": [],
    }


def node_is_available(
    story: dict[str, Any], node: dict[str, Any], state: dict[str, Any]
) -> bool:
    """按节点前置事实过滤静态图出口。"""  # noqa: DOCSTRING_CJK
    preconditions = (
        node.get("preconditions") if isinstance(node.get("preconditions"), dict) else {}
    )
    facts = {_fact_key(item) for item in fact_view.authoritative_facts(story, state)}
    required = {_fact_key(item) for item in preconditions.get("required_facts") or []}
    forbidden = {_fact_key(item) for item in preconditions.get("forbidden_facts") or []}
    return required.issubset(facts) and facts.isdisjoint(forbidden)


def apply_node(
    story: dict[str, Any], state: dict[str, Any], node: dict[str, Any]
) -> None:
    """提交作者声明的节点增量；公开 Board 会直接读取提交后的权威状态。"""  # noqa: DOCSTRING_CJK
    node_id = str(node.get("node_id") or "")
    completed = list(state.get("completed_node_ids") or [])
    if node_id and node_id not in completed:
        completed.append(node_id)
    state["current_node_id"] = node_id
    state["completed_node_ids"] = completed
    completed_goals = list(state.get("completed_goal_ids") or [])
    for goal_id in node.get("completes_goal_ids") or []:
        normalized_goal_id = str(goal_id or "").strip()
        if normalized_goal_id and normalized_goal_id not in completed_goals:
            # 静态作者节点与动态支线共用同一完成集合；运行时不根据节点内容猜测 Goal。
            completed_goals.append(normalized_goal_id)
    state["completed_goal_ids"] = completed_goals
    # 旧 Session 可能残留模型按钮覆盖；新节点只保留作者 Choice，并清除兼容字段。
    state.pop("choice_label_overrides", None)
    # 拉回次数只属于原节点的当前目标；节点推进后必须清零，不能污染下一段剧情。
    clear_latent_intent_tracking(state)
    # 通用自由意图同样绑定 origin 节点；主线推进后必须重新取得连续证据。
    state["dynamic_intent"] = {}
    # 旧 Pending 不能跨越新的作者节点；合法后半句会在目标节点提交后重新创建。
    state["pending_intent"] = {}

    facts = [
        dict(item)
        for item in state.get("narrative_facts") or []
        if isinstance(item, dict)
    ]
    diff = node.get("state_diff") if isinstance(node.get("state_diff"), dict) else {}
    remove_keys = {_fact_key(item) for item in diff.get("remove") or []}
    facts = [item for item in facts if _fact_key(item) not in remove_keys]
    known = {_fact_key(item) for item in facts}
    for item in diff.get("add") or []:
        if isinstance(item, dict) and _fact_key(item) not in known:
            facts.append(dict(item))
            known.add(_fact_key(item))
    state["narrative_facts"] = facts

    # 道具只用 available_from_node 表达出现时机，不再维护多维实体生命周期。
    newly_available = [
        str(prop.get("id") or "")
        for prop in story.get("stage_props") or []
        if isinstance(prop, dict)
        and str(prop.get("available_from_node") or "") == node_id
    ]
    state["available_prop_ids"] = _append_unique(
        state.get("available_prop_ids"), newly_available
    )

    action = (
        node.get("script_action") if isinstance(node.get("script_action"), dict) else {}
    )
    state["clue_ids"] = _append_unique(
        state.get("clue_ids"), action.get("reveals_clues")
    )
    state["used_prop_ids"] = _append_unique(
        state.get("used_prop_ids"), action.get("uses_props")
    )
    used_prop_ids = set(state["used_prop_ids"])
    # 道具一旦被节点消费，就从可用区移除；公开面板不能同时显示为可用和已使用。
    state["available_prop_ids"] = [
        prop_id
        for prop_id in state.get("available_prop_ids") or []
        if prop_id not in used_prop_ids
    ]

    # 旧 Story 没有 flags 字段；存在时只接受作者节点中声明的稳定字符串。
    state["flags"] = _append_unique(state.get("flags"), diff.get("add_flags"))


def record_latent_intent(state: dict[str, Any], transition: dict[str, Any]) -> bool:
    """记录同一目标内的连续意图；超过作者拉回阈值时才允许提交隐藏边。"""  # noqa: DOCSTRING_CJK
    goal_id = str(transition.get("goal_id") or "")
    intent_id = str(transition.get("intent_id") or "")
    threshold = max(0, int(transition.get("pullbacks_before_transition") or 0))
    same_intent = (
        str(state.get("active_goal_id") or "") == goal_id
        and str(state.get("focused_intent_id") or "") == intent_id
    )
    streak = int(state.get("intent_streak") or 0) + 1 if same_intent else 1
    state["active_goal_id"] = goal_id
    state["focused_intent_id"] = intent_id
    state["intent_streak"] = streak
    state["goal_pullback_count"] = min(streak, threshold)
    # 阈值表示仍留在当前节点自然回应的次数；配置为 2 时第三次连续表达才进入支线。
    return streak > threshold


def clear_latent_intent_tracking(state: dict[str, Any]) -> None:
    """清除当前节点的连续意图，不影响已经提交的支线承诺。"""  # noqa: DOCSTRING_CJK
    state["active_goal_id"] = ""
    state["focused_intent_id"] = ""
    state["intent_streak"] = 0
    state["goal_pullback_count"] = 0


def commit_latent_transition(state: dict[str, Any], transition_id: str) -> None:
    """记录已经正式进入的作者隐藏边，供汇流后的演绎保留上下文。"""  # noqa: DOCSTRING_CJK
    state["branch_commitment"] = str(transition_id or "").strip()


def append_scene_note(
    state: dict[str, Any], user_message: str, *, limit: int = 6
) -> None:
    """保存非权威自由互动笔记；笔记不会参与路由和结局判断。"""  # noqa: DOCSTRING_CJK
    note = " ".join(str(user_message or "").strip().split())
    if not note or len(note) > MAX_SCENE_NOTE_CHARS:
        # 长输入不能只保存正向前缀；句尾否定一旦丢失，后续演绎会误读玩家原意。
        return
    notes = [str(item) for item in state.get("scene_notes") or [] if str(item).strip()]
    notes.append(note)
    state["scene_notes"] = notes[-limit:]


def ending_for_state(
    story: dict[str, Any],
    state: dict[str, Any],
    node: dict[str, Any],
    *,
    has_outgoing: bool,
) -> dict[str, Any]:
    """只按作者节点和已提交事实判断正式结束。"""  # noqa: DOCSTRING_CJK
    # has_outgoing 保留在调用协议中供可观测性使用；“没有出口”本身不是作者结局声明。
    _ = has_outgoing
    if str(node.get("node_type") or "") != "ending":
        return {
            "should_offer_ending": False,
            "should_end_session": False,
            "ending_id": "",
        }

    facts = {_fact_key(item) for item in fact_view.authoritative_facts(story, state)}
    clue_ids = set(state.get("clue_ids") or [])
    selected_id = ""
    for ending in story.get("ending_attractors") or []:
        if not isinstance(ending, dict):
            continue
        required = ending.get("required_facts") or []
        fact_requirements = {
            _fact_key(item) for item in required if isinstance(item, dict)
        }
        clue_requirements = {
            str(item)
            for item in ending.get("required_clue_ids") or []
            if str(item).strip()
        }
        forbidden = {_fact_key(item) for item in ending.get("forbidden_facts") or []}
        if (
            fact_requirements.issubset(facts)
            and facts.isdisjoint(forbidden)
            and clue_requirements.issubset(clue_ids)
        ):
            selected_id = str(ending.get("id") or "")
            break
    if not selected_id:
        # Loader 要求每个 ending 节点显式声明 ending_id；运行时不能拿 Node ID 冒充结局。
        selected_id = str(node.get("ending_id") or "")
    if not selected_id:
        return {
            "should_offer_ending": False,
            "should_end_session": False,
            "ending_id": "",
        }
    return {
        "should_offer_ending": True,
        "should_end_session": True,
        "ending_id": selected_id,
        "reason": "story_complete",
    }


def _initial_prop_ids(story: dict[str, Any], initial_node_id: str) -> list[str]:
    """收集开场可见道具；后续被节点使用的道具会按需加入。"""  # noqa: DOCSTRING_CJK
    result: list[str] = []
    for prop in story.get("stage_props") or []:
        available_from = (
            str(prop.get("available_from_node") or "") if isinstance(prop, dict) else ""
        )
        if (
            isinstance(prop, dict)
            and prop.get("id")
            and available_from in {"", initial_node_id}
        ):
            result.append(str(prop["id"]))
    return list(dict.fromkeys(result))


def _append_unique(existing: Any, additions: Any) -> list[str]:
    """向字符串集合追加新值，同时保持 JSON 中的稳定顺序。"""  # noqa: DOCSTRING_CJK
    values = [str(item) for item in existing or [] if str(item).strip()]
    for item in additions or []:
        normalized = str(item).strip()
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _fact_key(value: Any) -> str:
    """把结构化事实转换成可比较的稳定键。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        return ""
    # Story 的 opening_facts 常带 type，而前置条件省略 type；剧情等价性只比较三元组主体。
    triple = {
        key: value.get(key)
        for key in ("subject", "predicate", "object")
        if key in value
    }
    comparable = triple or value
    return json.dumps(
        comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
