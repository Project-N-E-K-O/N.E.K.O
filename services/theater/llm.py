"""用一次模型调用生成旁白和当前猫娘对白。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from config.prompts.prompts_theater import build_theater_turn_prompts
from utils.file_utils import robust_json_loads
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.token_tracker import set_call_type
from utils.tokenize import truncate_to_tokens


THEATER_TURN_TIMEOUT_SECONDS = 10.0
THEATER_TURN_OUTPUT_MAX_TOKENS = 360
THEATER_CONTEXT_MAX_TOKENS = 500
_FORBIDDEN_OUTPUT_TERMS = ("scene_id", "node_id", "prompt", "状态机", "剧情引擎", "debug")


async def generate_turn_async(
    *,
    config_manager: Any | None,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    choice_options: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """生成一次结构化演绎；配置缺失、超时或坏输出时使用作者文本。"""  # noqa: DOCSTRING_CJK
    fallback = fallback_turn(
        lanlan_name=lanlan_name,
        scene=scene,
        node=node,
        user_message=user_message,
        progress_kind=progress_kind,
        callback=callback,
        has_scene_notes=bool(state.get("scene_notes")),
    )
    api_config = _model_config(config_manager)
    if not api_config:
        return fallback

    public_state = {
        "已发现线索": list(state.get("clue_ids") or []),
        "已使用道具": list(state.get("used_prop_ids") or []),
        "场景笔记": list(state.get("scene_notes") or [])[-4:],
    }
    prompt_story = dict(story)
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""), THEATER_CONTEXT_MAX_TOKENS
    )
    system_prompt, user_prompt = build_theater_turn_prompts(
        lanlan_name=lanlan_name,
        story=prompt_story,
        scene=scene,
        node=node,
        user_message=truncate_to_tokens(user_message, 140),
        progress_kind=progress_kind,
        callback=truncate_to_tokens(callback, 120),
        public_state=public_state,
        recent_turns=_recent_public_turns(recent_turns),
        character_profile=_load_character_profile(config_manager, lanlan_name),
        choice_options=list(choice_options or []),
    )
    try:
        set_call_type("theater_turn")
        client = await create_chat_llm_async(
            api_config["model"],
            api_config["base_url"],
            api_config.get("api_key"),
            provider_type=api_config.get("provider_type"),
            timeout=THEATER_TURN_TIMEOUT_SECONDS,
            max_retries=0,
            max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
        )
        async with client:
            result = await asyncio.wait_for(
                client.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
                timeout=THEATER_TURN_TIMEOUT_SECONDS,
            )
    except Exception:
        return fallback
    allowed_choice_ids = {str(item.get("choice_id") or "") for item in choice_options or []}
    parsed = _parse_output(
        getattr(result, "content", ""),
        progress_kind=progress_kind,
        allowed_choice_ids=allowed_choice_ids,
    )
    if parsed and progress_kind == "roleplay_response" and _repeats_recent_dialogue(parsed["dialogue"], recent_turns):
        # 小模型偶尔只删改语气词后复述上一句；视为无效输出，保持单次调用并走安全回应。
        return fallback
    return parsed or fallback


def fallback_turn(
    *,
    lanlan_name: str,
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    has_scene_notes: bool = False,
) -> dict[str, Any]:
    """使用作者文本生成离线演绎，确保模型故障时仍能继续游戏。"""  # noqa: DOCSTRING_CJK
    name = str(lanlan_name or "Lan").strip() or "Lan"
    if progress_kind == "roleplay_response":
        message = str(user_message or "").strip()
        dialogue = (
            f"我听见你的意思了。关于「{message[:36]}」，让我先留在眼前这件事里，认真想清楚再回答你喵。"
            if message
            else f"{name}还在这里喵。"
        )
        return {"narration": "", "dialogue": dialogue, "choice_rewrites": []}
    narration = str(callback or node.get("summary") or scene.get("text") or "").strip()
    # 发生过自由互动后，固定台词可能与刚形成的语境冲突，只在纯作者路径中回退使用。
    dialogue = "" if has_scene_notes else str(node.get("scripted_dialogue") or "").strip()
    if not dialogue:
        guide = node.get("runtime_generation_guide") if isinstance(node.get("runtime_generation_guide"), dict) else {}
        dialogue = str(guide.get("catgirl_raw_intent") or f"我们继续看看接下来会发生什么吧，{name}会陪着你喵。").strip()
    return {"narration": narration, "dialogue": dialogue, "choice_rewrites": []}


def _model_config(config_manager: Any | None) -> dict[str, Any]:
    """读取 summary 档模型配置；不完整时返回空配置。"""  # noqa: DOCSTRING_CJK
    if config_manager is None:
        return {}
    try:
        config = dict(config_manager.get_model_api_config("summary") or {})
    except Exception:
        return {}
    if not str(config.get("model") or "").strip() or not str(config.get("base_url") or "").strip():
        return {}
    config["model"] = str(config["model"]).strip()
    config["base_url"] = str(config["base_url"]).strip()
    return config


def _parse_output(
    raw: Any,
    *,
    progress_kind: str,
    allowed_choice_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """解析模型 JSON，并把 Choice 改写限制到当前稳定 ID。"""  # noqa: DOCSTRING_CJK
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        payload = robust_json_loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    narration = str(payload.get("narration") or "").strip()
    dialogue = str(payload.get("dialogue") or "").strip()
    combined = narration + dialogue
    if not dialogue or any(term.lower() in combined.lower() for term in _FORBIDDEN_OUTPUT_TERMS):
        return None
    if progress_kind != "roleplay_response" and not narration:
        return None
    rewrites: list[dict[str, str]] = []
    allowed = allowed_choice_ids or set()
    seen: set[str] = set()
    if progress_kind == "roleplay_response" and isinstance(payload.get("choice_rewrites"), list):
        for item in payload["choice_rewrites"]:
            if not isinstance(item, dict):
                continue
            choice_id = str(item.get("choice_id") or "").strip()
            label = str(item.get("label") or "").strip()
            if choice_id not in allowed or choice_id in seen or not 2 <= len(label) <= 80:
                continue
            if any(term.lower() in label.lower() for term in _FORBIDDEN_OUTPUT_TERMS):
                continue
            seen.add(choice_id)
            rewrites.append({"choice_id": choice_id, "label": label})
    return {"narration": narration, "dialogue": dialogue, "choice_rewrites": rewrites}


def _recent_public_turns(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """提取最近公开旁白与对白，让模型承接已发生动作而不读取私有状态。"""  # noqa: DOCSTRING_CJK
    result: list[dict[str, str]] = []
    for turn in turns[-8:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        dialogue = str(turn.get("text") or turn.get("dialogue") or "").strip()
        narration = str(turn.get("narration") or "").strip() if role == "assistant" else ""
        text = " ".join(item for item in (narration, dialogue) if item)
        if role in {"user", "assistant"} and text:
            result.append({"role": role, "text": truncate_to_tokens(text, 80)})
    return result


def _repeats_recent_dialogue(dialogue: str, turns: list[dict[str, Any]]) -> bool:
    """识别只改标点或语气词的上一句复述，避免自由互动看似没有听见玩家。"""  # noqa: DOCSTRING_CJK
    previous = ""
    for turn in reversed(turns):
        if isinstance(turn, dict) and str(turn.get("role") or "") == "assistant":
            previous = str(turn.get("text") or turn.get("dialogue") or "")
            break
    current_key = _dialogue_key(dialogue)
    previous_key = _dialogue_key(previous)
    if len(current_key) < 12 or len(previous_key) < 12:
        return False
    return SequenceMatcher(None, current_key, previous_key).ratio() >= 0.92


def _dialogue_key(value: str) -> str:
    """移除标点、空白和句尾猫娘语气词，只比较对白主体。"""  # noqa: DOCSTRING_CJK
    normalized = re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()…—]+", "", str(value or "")).lower()
    return normalized.removesuffix("喵")


def _load_character_profile(config_manager: Any | None, lanlan_name: str) -> str:
    """读取当前猫娘的短人格摘要，不加载普通聊天全文。"""  # noqa: DOCSTRING_CJK
    root = getattr(config_manager, "app_docs_dir", None) if config_manager is not None else None
    if not root or not lanlan_name:
        return ""
    path = Path(root) / "memory" / str(lanlan_name).strip() / "persona.json"
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
