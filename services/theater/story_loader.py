"""加载并校验轻量小剧场 Story Package。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.file_utils import read_json_async


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_STORY_DIR = PROJECT_ROOT / "config" / "theater" / "stories"


async def list_stories(*, story_dir: Path | None = None) -> list[dict[str, Any]]:
    """返回可公开的故事卡，不携带内部节点和条件。"""
    stories = await _load_config_stories(story_dir or CONFIG_STORY_DIR)
    return [public_story(story) for story in stories]


async def load_story(story_id: str | None, *, story_dir: Path | None = None) -> dict[str, Any]:
    """按 ID 加载正式故事；空 ID 或未知 ID 使用目录中的第一份故事。"""
    stories = await _load_config_stories(story_dir or CONFIG_STORY_DIR)
    if not stories:
        return {}
    normalized_id = str(story_id or "").strip()
    for story in stories:
        if str(story.get("id") or "") == normalized_id:
            return story
    # 默认值只依赖排序后的正式 JSON，不再维护第二套代码内 Story 协议。
    return stories[0]


def public_story(story: dict[str, Any]) -> dict[str, Any]:
    """只公开故事选择页需要的信息。"""
    public: dict[str, Any] = {
        "id": str(story.get("id") or ""),
        "title": str(story.get("title") or ""),
        "summary": str(story.get("summary") or ""),
        "scenes": [public_scene(scene) for scene in story.get("scenes") or [] if isinstance(scene, dict)],
    }
    card = story.get("scenario_card")
    if isinstance(card, dict):
        # 开场卡只保留玩家已知信息，隐藏线索和内部规则不会发送给前端。
        public["scenario_card"] = {
            "brief": str(card.get("brief") or ""),
            "player_role": str(card.get("player_role") or ""),
            "catgirl_role": str(card.get("catgirl_role") or ""),
            "primary_goal": str(card.get("primary_goal") or ""),
            "rules": [str(item) for item in card.get("rules") or [] if str(item).strip()],
        }
    return public


def public_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """把内部场景转换为前端稳定字段。"""
    return {
        "scene_id": str(scene.get("id") or ""),
        "title": str(scene.get("title") or ""),
        "text": str(scene.get("text") or ""),
    }


def scene_for_phase(story: dict[str, Any], phase: str) -> dict[str, Any]:
    """按节点阶段选择表现层场景。"""
    scenes = [scene for scene in story.get("scenes") or [] if isinstance(scene, dict)]
    for scene in scenes:
        if str(scene.get("phase") or scene.get("id") or "") == phase:
            return scene
    return scenes[0] if scenes else {"id": phase or "setup", "phase": phase or "setup", "title": "", "text": ""}


def initial_node_id(story: dict[str, Any]) -> str:
    """取得静态图入口；优先选择 setup 阶段的 seed 节点。"""
    nodes = [node for node in story.get("narrative_nodes") or [] if isinstance(node, dict)]
    for node in nodes:
        if node.get("node_type") == "seed" and node.get("belong_phase") == "setup":
            return str(node.get("node_id") or "")
    for node in nodes:
        if node.get("belong_phase") == "setup":
            return str(node.get("node_id") or "")
    return str(nodes[0].get("node_id") or "") if nodes else ""


async def _load_config_stories(story_dir: Path) -> list[dict[str, Any]]:
    """读取目录内的 JSON Story Package。"""
    if not story_dir.exists():
        return []
    stories: list[dict[str, Any]] = []
    for path in sorted(story_dir.glob("*.json")):
        payload = await read_json_async(path)
        if isinstance(payload, dict):
            stories.append(_validate_story(payload, path))
    return stories


def _validate_story(story: dict[str, Any], path: Path) -> dict[str, Any]:
    """执行当前轻量协议检查，阻止断边和无入口故事进入运行时。"""
    required = ("id", "title", "initial_scene_id", "scenes", "narrative_nodes", "edges")
    missing = [key for key in required if not story.get(key)]
    if missing:
        raise ValueError(f"Theater story {path} missing fields: {', '.join(missing)}")
    nodes = [node for node in story.get("narrative_nodes") or [] if isinstance(node, dict)]
    node_ids = [str(node.get("node_id") or "") for node in nodes]
    if not node_ids or any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
        raise ValueError(f"Theater story {path} has invalid or duplicate node ids")
    for edge in story.get("edges") or []:
        if not isinstance(edge, dict):
            raise ValueError(f"Theater story {path} has invalid edge")
        if str(edge.get("from_node") or "") not in node_ids or str(edge.get("to_node") or "") not in node_ids:
            raise ValueError(f"Theater story {path} edge references unknown node")
    if not initial_node_id(story):
        raise ValueError(f"Theater story {path} has no setup node")
    _validate_reachable_ending(story, path)
    return deepcopy(story)


def _validate_reachable_ending(story: dict[str, Any], path: Path) -> None:
    """确保作者静态图至少存在一条从开场抵达落幕的路径。"""
    adjacency: dict[str, list[str]] = {}
    for edge in story.get("edges") or []:
        adjacency.setdefault(str(edge.get("from_node") or ""), []).append(str(edge.get("to_node") or ""))
    nodes = {str(node.get("node_id") or ""): node for node in story.get("narrative_nodes") or [] if isinstance(node, dict)}
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
        node_id in visited and (str(node.get("node_type") or "") == "ending" or not adjacency.get(node_id))
        for node_id, node in nodes.items()
    )
    if not reachable_ending:
        raise ValueError(f"Theater story {path} has no reachable ending")
