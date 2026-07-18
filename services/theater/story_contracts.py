"""校验小剧场 Story 根结构和静态作者图。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from . import story_dynamic_contracts


class StoryRootNotObjectError(ValueError):
    """Story Package 顶层不是 JSON object。"""  # noqa: DOCSTRING_CJK


def initial_node_id(story: dict[str, Any]) -> str:
    """取得 Loader 已验证的唯一 setup seed 节点。"""  # noqa: DOCSTRING_CJK
    nodes = [
        node for node in story.get("narrative_nodes") or [] if isinstance(node, dict)
    ]
    for node in nodes:
        if node.get("node_type") == "seed" and node.get("belong_phase") == "setup":
            return str(node.get("node_id") or "")
    return ""


def validate_story_package(story: dict[str, Any], path: Path) -> dict[str, Any]:
    """执行当前轻量协议检查，阻止断边和无入口故事进入运行时。"""  # noqa: DOCSTRING_CJK
    required = (
        "id",
        "title",
        "background",
        "initial_scene_id",
        "scenes",
        "narrative_nodes",
        "edges",
    )
    missing = [key for key in required if not story.get(key)]
    if missing:
        raise ValueError(f"Theater story {path} missing fields: {', '.join(missing)}")
    raw_scenes = story.get("scenes")
    if not isinstance(raw_scenes, list) or any(
        not isinstance(scene, dict) for scene in raw_scenes
    ):
        raise ValueError(f"Theater story {path} has invalid scenes")
    scenes = list(raw_scenes)
    scene_ids = [str(scene.get("id") or "") for scene in scenes]
    if (
        not scene_ids
        or any(not scene_id for scene_id in scene_ids)
        or len(scene_ids) != len(set(scene_ids))
    ):
        raise ValueError(f"Theater story {path} has invalid or duplicate scene ids")
    if str(story.get("initial_scene_id") or "") not in scene_ids:
        raise ValueError(f"Theater story {path} initial scene references unknown scene")
    scene_phases: set[str] = set()
    for scene in scenes:
        phase = str(scene.get("phase") or "").strip()
        if (
            not phase
            or not str(scene.get("title") or "").strip()
            or not str(scene.get("text") or "").strip()
        ):
            raise ValueError(f"Theater story {path} has incomplete scene")
        if phase in scene_phases:
            raise ValueError(f"Theater story {path} has duplicate scene phase: {phase}")
        scene_phases.add(phase)
    raw_nodes = story.get("narrative_nodes")
    if not isinstance(raw_nodes, list) or any(
        not isinstance(node, dict) for node in raw_nodes
    ):
        raise ValueError(f"Theater story {path} has invalid narrative nodes")
    nodes = list(raw_nodes)
    node_ids = [str(node.get("node_id") or "") for node in nodes]
    if (
        not node_ids
        or any(not node_id for node_id in node_ids)
        or len(node_ids) != len(set(node_ids))
    ):
        raise ValueError(f"Theater story {path} has invalid or duplicate node ids")
    _validate_public_story_contract(story, path)
    _validate_static_node_contract(nodes, path, scene_phases)
    # 动态协议只依赖作者稳定 ID；先完成节点收集，再校验 v2.5 的全部交叉引用。
    story_dynamic_contracts.validate_dynamic_story_contract(story, path, nodes)
    raw_edges = story.get("edges")
    if not isinstance(raw_edges, list):
        raise ValueError(f"Theater story {path} has invalid edges")
    edges = list(raw_edges)
    transition_ids: set[str] = set()
    latent_intents_by_node: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError(f"Theater story {path} has invalid edge")
        if (
            str(edge.get("from_node") or "") not in node_ids
            or str(edge.get("to_node") or "") not in node_ids
        ):
            raise ValueError(f"Theater story {path} edge references unknown node")
        # v2.4 沿用原 edges 数组；未声明可见性的旧边等同推荐边，避免迁移无关 Story Package。
        visibility = str(edge.get("visibility") or "recommended").strip()
        if visibility not in {"recommended", "latent"}:
            raise ValueError(
                f"Theater story {path} edge has invalid visibility: {visibility}"
            )
        if visibility != "latent":
            continue
        # 隐藏语义边必须完全由作者声明。模型只能返回 intent_id，不能补写目标、阈值或事实。
        transition_id = str(edge.get("transition_id") or "").strip()
        goal_id = str(edge.get("goal_id") or "").strip()
        intent_id = str(edge.get("intent_id") or "").strip()
        intent_summary = str(edge.get("intent_summary") or "").strip()
        examples = [
            str(item).strip()
            for item in edge.get("intent_examples") or []
            if str(item).strip()
        ]
        pullbacks = edge.get("pullbacks_before_transition")
        if (
            not transition_id
            or not goal_id
            or not intent_id
            or not intent_summary
            or not examples
        ):
            raise ValueError(
                f"Theater story {path} latent edge is missing routing metadata"
            )
        if (
            isinstance(pullbacks, bool)
            or not isinstance(pullbacks, int)
            or pullbacks < 0
        ):
            raise ValueError(
                f"Theater story {path} latent edge has invalid pullback threshold"
            )
        if transition_id in transition_ids:
            raise ValueError(
                f"Theater story {path} has duplicate transition id: {transition_id}"
            )
        intent_key = (str(edge.get("from_node") or ""), intent_id)
        if intent_key in latent_intents_by_node:
            raise ValueError(
                f"Theater story {path} has ambiguous latent intent at one node: {intent_id}"
            )
        transition_ids.add(transition_id)
        latent_intents_by_node.add(intent_key)
    if not initial_node_id(story):
        raise ValueError(f"Theater story {path} has no setup node")
    _validate_static_graph_contract(story, path, nodes, edges)
    _validate_reachable_ending(story, path)
    return deepcopy(story)


def _validate_public_story_contract(story: dict[str, Any], path: Path) -> None:
    """阻止公开背景、初始动作和生成约束重新形成多份作者真源。"""  # noqa: DOCSTRING_CJK
    card = story.get("scenario_card")
    if card is None:
        return
    if not isinstance(card, dict):
        raise ValueError(f"Theater story {path} has invalid scenario card")
    duplicated = {"brief", "rules"}.intersection(card)
    if duplicated:
        raise ValueError(
            f"Theater story {path} scenario card duplicates public or private content"
        )
    for field in ("player_role", "catgirl_role", "primary_goal"):
        if not str(card.get(field) or "").strip():
            raise ValueError(f"Theater story {path} scenario card is missing {field}")


def _validate_static_node_contract(
    nodes: list[dict[str, Any]], path: Path, scene_phases: set[str]
) -> None:
    """校验节点阶段、作者对白与 Choice，禁止运行时补正文或结构字段。"""  # noqa: DOCSTRING_CJK
    seed_nodes = [
        node
        for node in nodes
        if str(node.get("node_type") or "") == "seed"
        and str(node.get("belong_phase") or "") == "setup"
    ]
    if len(seed_nodes) != 1:
        raise ValueError(f"Theater story {path} must have exactly one setup seed")

    choice_ids: set[str] = set()
    allowed_node_types = {"seed", "core", "branch", "ending"}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        phase = str(node.get("belong_phase") or "").strip()
        node_type = str(node.get("node_type") or "").strip()
        if phase not in scene_phases:
            raise ValueError(
                f"Theater story {path} node references unknown scene phase: {node_id}"
            )
        if node_type not in allowed_node_types:
            raise ValueError(f"Theater story {path} node has invalid type: {node_id}")
        if node_type == "ending" and not str(node.get("ending_id") or "").strip():
            raise ValueError(
                f"Theater story {path} ending node is missing ending_id: {node_id}"
            )
        if node_type != "seed" and not str(node.get("scripted_dialogue") or "").strip():
            # 静态节点对白属于作者正文；缺失时必须拒绝加载，不能交给模型临场补写。
            raise ValueError(
                f"Theater story {path} node is missing scripted dialogue: {node_id}"
            )
        suggestions = node.get("suggestions", [])
        if not isinstance(suggestions, list) or any(
            not isinstance(item, dict) for item in suggestions
        ):
            raise ValueError(
                f"Theater story {path} node has invalid suggestions: {node_id}"
            )
        for suggestion in suggestions:
            choice_id = str(suggestion.get("choice_id") or "").strip()
            label = str(suggestion.get("label") or "").strip()
            mode = str(suggestion.get("choice_mode") or "").strip()
            callback = str(suggestion.get("callback") or "").strip()
            if (
                not choice_id
                or not label
                or mode not in {"action", "dialogue"}
                or not callback
            ):
                raise ValueError(
                    f"Theater story {path} has incomplete choice in node: {node_id}"
                )
            if choice_id in choice_ids:
                raise ValueError(
                    f"Theater story {path} has duplicate choice id: {choice_id}"
                )
            choice_ids.add(choice_id)


def _validate_static_graph_contract(
    story: dict[str, Any],
    path: Path,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """校验全节点可达的前向作者图，并证明推荐边都有对应 Choice。"""  # noqa: DOCSTRING_CJK
    node_index = {str(node.get("node_id") or ""): node for node in nodes}
    outgoing_ids = {
        str(edge.get("from_node") or "") for edge in edges if isinstance(edge, dict)
    }
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        if str(node.get("node_type") or "") != "ending" and node_id not in outgoing_ids:
            raise ValueError(
                f"Theater story {path} has non-ending node without outgoing edge: {node_id}"
            )

    effective_choices: set[tuple[str, str]] = set()
    for edge in edges:
        if str(edge.get("visibility") or "recommended") == "latent":
            continue
        target_id = str(edge.get("to_node") or "")
        target = node_index[target_id]
        suggestions = target.get("suggestions") or []
        matched = [
            item
            for item in suggestions
            if _suggestion_matches_recommended_edge(item, edge)
        ]
        if not matched:
            raise ValueError(
                f"Theater story {path} recommended edge has no matching choice: {target_id}"
            )
        for suggestion in matched:
            key = (
                str(edge.get("from_node") or ""),
                str(suggestion.get("choice_id") or ""),
            )
            if key in effective_choices:
                raise ValueError(
                    f"Theater story {path} recommended edges duplicate one visible choice"
                )
            effective_choices.add(key)

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_index}
    for edge in edges:
        adjacency[str(edge.get("from_node") or "")].append(
            str(edge.get("to_node") or "")
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node_id: str) -> None:
        """深度优先证明作者图无环；运行时完成节点过滤不再暗中决定回访语义。"""  # noqa: DOCSTRING_CJK
        if node_id in visiting:
            raise ValueError(f"Theater story {path} static graph contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target_id in adjacency[node_id]:
            _visit(target_id)
        visiting.remove(node_id)
        visited.add(node_id)

    _visit(initial_node_id(story))
    unreachable = sorted(set(node_index) - visited)
    if unreachable:
        # 孤立节点不会被运行时抵达，也可能把环藏在 setup 遍历之外，必须整体拒绝。
        raise ValueError(
            f"Theater story {path} has unreachable static node: {unreachable[0]}"
        )


def _suggestion_matches_recommended_edge(
    suggestion: dict[str, Any], edge: dict[str, Any]
) -> bool:
    """按作者 behavior/meaning 证明推荐边与目标 Choice 对应，不使用数组首项兜底。"""  # noqa: DOCSTRING_CJK
    behavior = str(suggestion.get("behavior_hint") or "")
    meaning = str(suggestion.get("meaning_hint") or "")
    return (not behavior or behavior == str(edge.get("behavior") or "")) and (
        not meaning or meaning == str(edge.get("meaning") or "")
    )


def _validate_reachable_ending(story: dict[str, Any], path: Path) -> None:
    """确保作者静态图至少存在一条从开场抵达落幕的路径。"""  # noqa: DOCSTRING_CJK
    adjacency: dict[str, list[str]] = {}
    for edge in story.get("edges") or []:
        adjacency.setdefault(str(edge.get("from_node") or ""), []).append(
            str(edge.get("to_node") or "")
        )
    nodes = {
        str(node.get("node_id") or ""): node
        for node in story.get("narrative_nodes") or []
        if isinstance(node, dict)
    }
    start = initial_node_id(story)
    pending = [start]
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(adjacency.get(node_id, []))
    reachable_ending = any(
        node_id in visited
        and (str(node.get("node_type") or "") == "ending" or not adjacency.get(node_id))
        for node_id, node in nodes.items()
    )
    if not reachable_ending:
        raise ValueError(f"Theater story {path} has no reachable ending")
