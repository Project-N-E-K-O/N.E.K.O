"""提供轻量小剧场的静态图查询和 Choice 路由。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any

from . import rules


def node_by_id(story: dict[str, Any], node_id: str) -> dict[str, Any]:
    """按稳定 ID 查找节点。"""  # noqa: DOCSTRING_CJK
    for node in story.get("narrative_nodes") or []:
        if isinstance(node, dict) and str(node.get("node_id") or "") == node_id:
            return node
    return {}


def current_node(story: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """读取当前提交节点。"""  # noqa: DOCSTRING_CJK
    return node_by_id(story, str(state.get("current_node_id") or ""))


def outgoing_nodes(
    story: dict[str, Any], state: dict[str, Any]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """按作者顺序返回当前可达的边和目标节点。"""  # noqa: DOCSTRING_CJK
    current_id = str(state.get("current_node_id") or "")
    completed = set(state.get("completed_node_ids") or [])
    completed_goals = set(state.get("completed_goal_ids") or [])
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for edge in story.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("from_node") or "") != current_id:
            continue
        node = node_by_id(story, str(edge.get("to_node") or ""))
        if not node or str(node.get("node_id") or "") in completed:
            continue
        edge_goal_id = str(edge.get("goal_id") or "").strip()
        node_goal_ids = {
            str(item).strip()
            for item in node.get("completes_goal_ids") or []
            if str(item).strip()
        }
        if edge_goal_id in completed_goals or node_goal_ids.intersection(
            completed_goals
        ):
            # 已完成 Goal 的入口与重复完成节点同时失效，点击、自然语言和恢复投影因此共享同一过滤结果。
            continue
        if rules.node_is_available(story, node, state):
            candidates.append((edge, node))
    return candidates


def suggestion_options(
    story: dict[str, Any],
    state: dict[str, Any],
    *,
    lanlan_name: str = "猫娘",
) -> list[dict[str, Any]]:
    """从当前出边的目标节点生成稳定行动/对白选项。"""  # noqa: DOCSTRING_CJK
    options: list[dict[str, Any]] = []
    for edge, node in outgoing_nodes(story, state):
        # 隐藏语义边只参与自由输入路由，绝不能作为推荐按钮或恢复快照泄露给玩家。
        if str(edge.get("visibility") or "recommended") == "latent":
            continue
        suggestions = [
            item for item in node.get("suggestions") or [] if isinstance(item, dict)
        ]
        matched = [item for item in suggestions if _suggestion_matches_edge(item, edge)]
        for suggestion in matched:
            static_label = str(suggestion.get("label") or "").strip()
            choice_id = str(suggestion.get("choice_id") or "")
            # Choice 的显示文案、ID、模式、目标和 callback 全部来自作者静态图。
            # 作者可以在对白按钮中引用当前猫娘名，避免把玩家自己的角色写死成某个默认名字。
            author_label = render_story_text(static_label, lanlan_name)
            guide = (
                node.get("runtime_generation_guide")
                if isinstance(node.get("runtime_generation_guide"), dict)
                else {}
            )
            options.append(
                {
                    "choice_id": choice_id,
                    "label": author_label,
                    # 作者标签只在服务端演绎上下文中使用，Projector 不会重复暴露该字段。
                    "author_label": author_label,
                    "choice_mode": str(suggestion.get("choice_mode") or ""),
                    "target_node_id": str(node.get("node_id") or ""),
                    "callback": str(suggestion.get("callback") or ""),
                    # 目标节点的公开作者意图只供本轮自然语言路由和演绎使用；Projector 不会把它们暴露给前端。
                    "target_summary": str(node.get("summary") or ""),
                    "target_narrator_intent": str(guide.get("narrator_intent") or ""),
                    "target_catgirl_intent": str(guide.get("catgirl_raw_intent") or ""),
                    "target_scripted_dialogue": str(
                        node.get("scripted_dialogue") or ""
                    ),
                    # 作者完成表达只供服务端确定性路由，不属于玩家可见 Choice 文案。
                    "completion_phrases": [
                        render_story_text(str(phrase), lanlan_name)
                        for phrase in suggestion.get("completion_phrases") or []
                        if str(phrase or "").strip()
                    ],
                }
            )
    return options


def latent_transition_options(
    story: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """返回当前可达的作者隐藏语义边，供单轮模型做白名单意图判断。"""  # noqa: DOCSTRING_CJK
    options: list[dict[str, Any]] = []
    active_goal = str(state.get("active_goal_id") or "")
    focused_intent = str(state.get("focused_intent_id") or "")
    for edge, node in outgoing_nodes(story, state):
        if str(edge.get("visibility") or "recommended") != "latent":
            continue
        goal_id = str(edge.get("goal_id") or "")
        intent_id = str(edge.get("intent_id") or "")
        options.append(
            {
                "transition_id": str(edge.get("transition_id") or ""),
                "goal_id": goal_id,
                "intent_id": intent_id,
                "intent_summary": str(edge.get("intent_summary") or ""),
                "intent_examples": [
                    str(item) for item in edge.get("intent_examples") or []
                ],
                "pullbacks_before_transition": int(
                    edge.get("pullbacks_before_transition") or 0
                ),
                "target_node_id": str(node.get("node_id") or ""),
                "callback": str(edge.get("callback") or ""),
                # 连续次数只帮助模型让回应保持语境；它属于内部路由状态，不会经过 Projector。
                "previous_hits": int(state.get("intent_streak") or 0)
                if goal_id == active_goal and intent_id == focused_intent
                else 0,
            }
        )
    return options


def resolve_latent_transition(
    latent_transitions: list[dict[str, Any]],
    observed_intent_id: str,
) -> dict[str, Any]:
    """仅在当前白名单唯一命中作者 intent_id 时解析隐藏边。"""  # noqa: DOCSTRING_CJK
    matches = [
        item
        for item in latent_transitions
        if str(item.get("intent_id") or "") == str(observed_intent_id or "")
    ]
    return dict(matches[0]) if len(matches) == 1 else {}


def resolve_choice(
    story: dict[str, Any],
    state: dict[str, Any],
    choice_id: str,
    *,
    lanlan_name: str = "猫娘",
) -> dict[str, Any]:
    """只在当前可见选项中解析 Choice，防止提交过期按钮。"""  # noqa: DOCSTRING_CJK
    for option in suggestion_options(story, state, lanlan_name=lanlan_name):
        if option["choice_id"] == str(choice_id or ""):
            return option
    return {}


def resolve_authored_completion(
    story: dict[str, Any],
    state: dict[str, Any],
    message: str,
    *,
    lanlan_name: str = "猫娘",
) -> dict[str, Any]:
    """仅在玩家输入唯一命中作者完成表达时解析当前 Choice。"""  # noqa: DOCSTRING_CJK
    message_key = _natural_input_key(message)
    if not message_key:
        return {}
    matched: list[dict[str, Any]] = []
    for option in suggestion_options(story, state, lanlan_name=lanlan_name):
        phrase_keys = {
            _natural_input_key(phrase)
            for phrase in option.get("completion_phrases") or []
        }
        if message_key in phrase_keys:
            matched.append(option)
    # 多个出口声明同一表达时不猜目标，交回模型按完整上下文处理。
    return matched[0] if len(matched) == 1 else {}


def render_story_text(value: Any, lanlan_name: str) -> str:
    """把作者公开文本中的当前猫娘占位符替换为本场实际角色名。"""  # noqa: DOCSTRING_CJK
    normalized_name = str(lanlan_name or "猫娘").strip() or "猫娘"
    return str(value or "").replace("{{lanlan_name}}", normalized_name).strip()


def _natural_input_key(value: Any) -> str:
    """忽略自然输入中的空白和句末标点，但不做包含或模糊匹配。"""  # noqa: DOCSTRING_CJK
    return re.sub(
        r"[\s，。！？、；：,.!?;:\"'“”‘’「」（）()…—]+", "", str(value or "")
    ).casefold()


def _suggestion_matches_edge(suggestion: dict[str, Any], edge: dict[str, Any]) -> bool:
    """优先选择与入边语义一致的目标节点文案。"""  # noqa: DOCSTRING_CJK
    behavior = str(suggestion.get("behavior_hint") or "")
    meaning = str(suggestion.get("meaning_hint") or "")
    return (not behavior or behavior == str(edge.get("behavior") or "")) and (
        not meaning or meaning == str(edge.get("meaning") or "")
    )
