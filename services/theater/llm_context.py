"""构造小剧场模型调用前的有限公开上下文。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import (
    THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS,
    THEATER_BRANCH_RECALL_MAX_FACTS,
    THEATER_BRANCH_RECALL_MAX_HISTORIES,
)
from utils.tokenize import truncate_to_tokens

from . import fact_view


def _complete_model_text(value: Any, max_tokens: int) -> str | None:
    """仅在文本可完整进入预算时返回原文，避免截断片段参与权威判断。"""  # noqa: DOCSTRING_CJK
    text = str(value or "")
    bounded = truncate_to_tokens(text, max_tokens)
    return text if bounded == text else None

def _bounded_completed_branch_recall(value: Any) -> list[dict[str, Any]]:
    """把 History 投影压缩到普通 Actor 的固定事实数与逐字段 token 预算。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    fact_count = 0
    for record in value[-THEATER_BRANCH_RECALL_MAX_HISTORIES:]:
        if not isinstance(record, dict):
            continue
        summaries = [
            truncate_to_tokens(str(item), THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS)
            for item in (record.get("completed_goal_summaries") or [])[:4]
            if isinstance(item, str) and item.strip()
        ]
        facts: list[dict[str, Any]] = []
        for fact in record.get("facts") or []:
            if fact_count >= THEATER_BRANCH_RECALL_MAX_FACTS:
                # 投影函数已经按近期优先排列，达到总预算后无需继续扫描更旧事实。
                break
            if not isinstance(fact, dict):
                continue
            projected = {
                key: truncate_to_tokens(
                    str(fact.get(key) or ""), THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS
                )
                for key in ("subject", "predicate", "object")
            }
            entity = fact.get("public_entity")
            if isinstance(entity, dict):
                # kind/status 是固定枚举，label 仍按同一用户文本预算截断。
                projected["public_entity"] = {
                    "kind": str(entity.get("kind") or ""),
                    "label": truncate_to_tokens(
                        str(entity.get("label") or ""),
                        THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS,
                    ),
                    "status": str(entity.get("status") or ""),
                }
            facts.append(projected)
            fact_count += 1
        if summaries or facts:
            result.append({"completed_goal_summaries": summaries, "facts": facts})
    return result

def _public_state(
    story: dict[str, Any],
    state: dict[str, Any],
    *,
    include_scene_notes: bool = False,
) -> dict[str, Any]:
    """投影作者权威事实；Scene Note 只可按需提供给无权威普通 Actor。"""  # noqa: DOCSTRING_CJK
    result = {
        "已发现线索": list(state.get("clue_ids") or []),
        "已使用道具": list(state.get("used_prop_ids") or []),
        "已确认事实": fact_view.authoritative_facts(story, state)[-8:],
    }
    if include_scene_notes:
        result["场景笔记"] = list(state.get("scene_notes") or [])[-4:]
    return result


def _recent_public_turns(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """提取最近公开旁白与对白，让模型承接已发生动作而不读取私有状态。"""  # noqa: DOCSTRING_CJK
    result: list[dict[str, str]] = []
    for turn in turns[-4:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        dialogue = str(turn.get("text") or turn.get("dialogue") or "").strip()
        narration = (
            str(turn.get("narration") or "").strip() if role == "assistant" else ""
        )
        if role == "user" and dialogue:
            complete_dialogue = _complete_model_text(dialogue, 60)
            if complete_dialogue is None:
                # 历史用户原话的句尾可能含否定或转折；超预算时整条退出模型上下文。
                continue
            result.append({"role": role, "text": complete_dialogue})
        elif role == "assistant" and (dialogue or narration):
            complete_dialogue = _complete_model_text(dialogue, 60)
            complete_narration = _complete_model_text(narration, 24)
            if complete_dialogue is None or complete_narration is None:
                # 猫娘回合的对白与旁白共同描述同一次公开结果；任一截断都会改变下一轮指代语义。
                continue
            result.append(
                {
                    "role": role,
                    "dialogue": complete_dialogue,
                    "narration": complete_narration,
                }
            )
    return result

def _load_character_profile(config_manager: Any | None, lanlan_name: str) -> str:
    """读取当前猫娘的短人格摘要，不加载普通聊天全文。"""  # noqa: DOCSTRING_CJK
    root = (
        getattr(config_manager, "app_docs_dir", None)
        if config_manager is not None
        else None
    )
    if not root or not lanlan_name:
        return ""
    name = str(lanlan_name).strip()
    try:
        characters = config_manager.load_characters()
    except Exception:
        return ""
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    current_name = (
        str(characters.get("当前猫娘") or "").strip()
        if isinstance(characters, dict)
        else ""
    )
    # 当前版本只允许玩家自己的当前猫娘入戏，不能借请求参数读取其他人格目录。
    if not isinstance(catgirls, dict) or name != current_name or name not in catgirls:
        return ""
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        return ""
    try:
        memory_root = (Path(root) / "memory").resolve()
        path = (memory_root / name / "persona.json").resolve()
    except (OSError, RuntimeError):
        # 异常符号链接或不可解析路径只禁用人格摘要，不能中断小剧场演绎。
        return ""
    # 配置文件也可能被手工篡改；解析真实路径后再次保证目标仍位于 memory 根目录。
    if not path.is_relative_to(memory_root):
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines: list[str] = []
    for section_name in ("neko", "relationship"):
        section = payload.get(section_name) if isinstance(payload, dict) else None
        if not isinstance(section, dict):
            continue
        for fact in section.get("facts") or []:
            if isinstance(fact, dict) and str(fact.get("text") or "").strip():
                lines.append(str(fact["text"]).strip())
    return truncate_to_tokens("\n".join(dict.fromkeys(lines)), 320)
