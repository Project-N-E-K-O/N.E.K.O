"""加载并公开轻量小剧场 Story Package。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.file_utils import read_json_async

from . import story_contracts
from .story_contracts import (
    StoryRootNotObjectError as StoryRootNotObjectError,
    initial_node_id as initial_node_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_STORY_DIR = PROJECT_ROOT / "config" / "theater" / "stories"


async def validate_story_file(story_path: Path) -> dict[str, Any]:
    """严格校验作者显式指定的单个 Story 文件，不扫描同目录其他内容。"""  # noqa: DOCSTRING_CJK
    path = Path(story_path)
    payload = await read_json_async(path)
    if not isinstance(payload, dict):
        # 单文件作者工具需要把 JSON 语法、根类型和 Story 合同错误稳定地区分开。
        raise StoryRootNotObjectError("Theater story root must be an object")
    # 内部校验器会返回深拷贝，调用方不能借返回值修改刚读取的作者输入。
    return story_contracts.validate_story_package(payload, path)


async def list_stories(*, story_dir: Path | None = None) -> list[dict[str, Any]]:
    """返回可公开的故事卡，不携带内部节点和条件。"""  # noqa: DOCSTRING_CJK
    stories = await _load_config_stories(story_dir or CONFIG_STORY_DIR)
    return [public_story(story) for story in stories]


async def load_story(
    story_id: str | None, *, story_dir: Path | None = None
) -> dict[str, Any]:
    """按 ID 加载正式故事；只有空 ID 才使用目录中的第一份故事。"""  # noqa: DOCSTRING_CJK
    stories = await _load_config_stories(story_dir or CONFIG_STORY_DIR)
    if not stories:
        return {}
    normalized_id = str(story_id or "").strip()
    if not normalized_id:
        # 未指定 Story 时使用稳定排序的默认项；显式错误 ID 不能偷换成另一位作者的内容。
        return stories[0]
    for story in stories:
        if str(story.get("id") or "") == normalized_id:
            return story
    raise FileNotFoundError(normalized_id)


async def load_story_exact(
    story_id: str, *, story_dir: Path | None = None
) -> dict[str, Any]:
    """按 Session 保存的稳定 ID 严格加载 Story，禁止缺失故事回退到其他作者内容。"""  # noqa: DOCSTRING_CJK
    normalized_id = str(story_id or "").strip()
    if not normalized_id:
        raise FileNotFoundError(normalized_id)
    return await load_story(normalized_id, story_dir=story_dir)


def public_story(story: dict[str, Any]) -> dict[str, Any]:
    """只公开故事选择页需要的信息。"""  # noqa: DOCSTRING_CJK
    initial_scene = scene_by_id(story, str(story.get("initial_scene_id") or ""))
    public: dict[str, Any] = {
        "id": str(story.get("id") or ""),
        "title": str(story.get("title") or ""),
        # background 是玩家背景的唯一作者真源；summary 和未来 Scene 都不属于选剧接口。
        "background": str(story.get("background") or ""),
        # 开演前只需要作者指定的初始 Scene，不能把后续转折和结局提前送进浏览器。
        "initial_scene": public_scene(initial_scene),
    }
    card = story.get("scenario_card")
    if isinstance(card, dict):
        # scenario_card 只保留结构化公开身份与目标；背景和生成约束各有独立真源。
        public["scenario_card"] = {
            "player_role": str(card.get("player_role") or ""),
            "catgirl_role": str(card.get("catgirl_role") or ""),
            "primary_goal": str(card.get("primary_goal") or ""),
        }
    return public


def public_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """把内部场景转换为前端稳定字段。"""  # noqa: DOCSTRING_CJK
    return {
        "scene_id": str(scene.get("id") or ""),
        "title": str(scene.get("title") or ""),
        "text": str(scene.get("text") or ""),
    }


def scene_for_phase(story: dict[str, Any], phase: str) -> dict[str, Any]:
    """按节点阶段选择表现层场景。"""  # noqa: DOCSTRING_CJK
    scenes = [scene for scene in story.get("scenes") or [] if isinstance(scene, dict)]
    for scene in scenes:
        if str(scene.get("phase") or scene.get("id") or "") == phase:
            return scene
    # Loader 已保证每个作者阶段都有 Scene；运行时不再拿数组第一项冒充缺失场景。
    return {}


def scene_by_id(story: dict[str, Any], scene_id: str) -> dict[str, Any]:
    """按稳定 ID 读取场景，不用 phase 猜测作者指定的开场。"""  # noqa: DOCSTRING_CJK
    for scene in story.get("scenes") or []:
        if isinstance(scene, dict) and str(scene.get("id") or "") == str(
            scene_id or ""
        ):
            return scene
    return {}


async def _load_config_stories(story_dir: Path) -> list[dict[str, Any]]:
    """读取目录内的 JSON Story Package。"""  # noqa: DOCSTRING_CJK
    if not story_dir.exists():
        return []
    stories: list[dict[str, Any]] = []
    for path in sorted(story_dir.glob("*.json")):
        payload = await read_json_async(path)
        if not isinstance(payload, dict):
            # 坏文件不能被目录扫描静默跳过，否则作者会误以为内容已经成功发布。
            raise StoryRootNotObjectError("Theater story root must be an object")
        stories.append(story_contracts.validate_story_package(payload, path))
    return stories
