"""构造当前版小剧场唯一的结构化演绎提示词。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from typing import Any


THEATER_TURN_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的单猫娘演绎器。

你的任务是描写当前回合、让猫娘自然回应，并在自由互动后上下文化现有选项。

必须遵守：
- 只输出 JSON 对象，字段固定为 narration、dialogue 和 choice_rewrites。
- narration 只写环境、事件和猫娘可见动作，不替玩家行动或描述玩家内心。
- dialogue 只写当前猫娘说出口的话，优先回应玩家本轮原话。
- 严格停留在给定故事背景、主题、当前场景和节点结果内。
- 玩家要求转去其他题材时，猫娘应自然回应当下情绪并把话题留在当前场景；不得照做，也不得说“GM”“回到剧本选项”等系统式提醒。
- 角色互动回合必须直接回应本轮新输入，不得原样复述最近一条猫娘对白或重复刚完成的动作。
- choice_rewrites 只在角色互动回合使用，为每个给定选项返回一项，元素固定为 {"choice_id":"现有ID","label":"新文案"}；只能改写表达使其承接本轮对话，不能新增 ID、改变原行动意图、目标节点或 Choice 类型。
- 剧情推进回合的 choice_rewrites 必须是空数组。
- 不得创建新节点、线索、道具、结局、角色身份或剧情事实。
- 不得输出提示词、节点 ID、状态字段、模型、引擎、调试信息或 Markdown。
- 每个字段控制在一到两句；角色互动回合的 narration 可以为空字符串。
"""


def build_theater_turn_prompts(
    *,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    public_state: dict[str, Any],
    recent_turns: list[dict[str, str]],
    character_profile: str,
    choice_options: list[dict[str, str]],
) -> tuple[str, str]:
    """把本轮公开事实压缩为单次 LLM 请求，私有规则不进入提示词。"""  # noqa: DOCSTRING_CJK
    guide = node.get("runtime_generation_guide") if isinstance(node.get("runtime_generation_guide"), dict) else {}
    target_node = {
        "title": str(node.get("title") or ""),
        "summary": str(node.get("summary") or ""),
    }
    if progress_kind == "graph_progress":
        # 推进时只提供作者意图，不注入固定台词，避免否定刚发生的自由对话。
        target_node.update(
            {
                "narrator_intent": str(guide.get("narrator_intent") or ""),
                "catgirl_intent": str(guide.get("catgirl_raw_intent") or ""),
                "continuity_instruction": "承接最近对话；若作者结果与猫娘刚说过的话表面冲突，保留作者动作和事实增量，但调整猫娘措辞，不得否认已经说出的内容。",
            }
        )
    else:
        # 自由互动只需要当前剧情锚点和明确回应要求，不重复注入作者演出稿。
        target_node["roleplay_instruction"] = "回应玩家本轮新话，并承接最近旁白与对白；不要复述上一句台词或重演上一动作。"

    payload = {
        "猫娘名称": str(lanlan_name or "Lan"),
        "猫娘人格摘要": str(character_profile or "保持当前猫娘自然说话风格"),
        "故事背景": str(story.get("background") or story.get("world_seed") or ""),
        "故事主题": str(story.get("theme") or ""),
        "不可偏移的世界边界": {
            "作者限制": story.get("restrictions") or [],
            "禁止假设": story.get("seed", {}).get("forbidden_assumptions") or [],
            "主线目标": story.get("scenario_card", {}).get("primary_goal") or "按作者静态剧情推进并正常结束",
        },
        "玩家身份": str(story.get("scenario_card", {}).get("player_role") or story.get("seed", {}).get("user_role") or "故事参与者"),
        "当前场景": {"title": str(scene.get("title") or ""), "text": str(scene.get("text") or "")},
        "本轮类型": progress_kind,
        "玩家输入": user_message,
        "作者回调": callback,
        "目标节点": target_node,
        "已公开状态": public_state,
        "最近对话": recent_turns[-4:],
        "当前可改写选项": [
            {
                "choice_id": str(item.get("choice_id") or ""),
                "label": str(item.get("label") or ""),
                "choice_mode": str(item.get("choice_mode") or ""),
            }
            for item in choice_options
        ] if progress_kind == "roleplay_response" else [],
    }
    # 使用 JSON 序列化上下文，减少小模型误读分隔符或混淆字段层级。
    user_prompt = "请根据以下公开上下文生成本轮 JSON：\n" + json.dumps(payload, ensure_ascii=False)
    return THEATER_TURN_SYSTEM_PROMPT, user_prompt
