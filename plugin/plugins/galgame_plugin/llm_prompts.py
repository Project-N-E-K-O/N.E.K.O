from __future__ import annotations

import json
from typing import Any

_PROMPT_CONTEXT_MAX_CHARS = 12000


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _compact_prompt_value(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
    dict_key_limit: int = 0,
) -> Any:
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        omitted = len(value) - string_limit
        return f"{value[:string_limit]}\n...[truncated {omitted} chars]"
    if isinstance(value, list):
        items = value[-list_limit:] if len(value) > list_limit else value
        return [
            _compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_key_limit=dict_key_limit,
            )
            for item in items
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if dict_key_limit > 0 and len(items) > dict_key_limit:
            omitted = len(items) - dict_key_limit
            items = items[:dict_key_limit]
            truncated = {
                str(key): _compact_prompt_value(
                    item,
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_key_limit=dict_key_limit,
                )
                for key, item in items
            }
            truncated["__truncated_keys__"] = f"...{omitted} keys omitted"
            return truncated
        return {
            str(key): _compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_key_limit=dict_key_limit,
            )
            for key, item in items
        }
    return value


def _context_json_for_prompt(context: dict[str, Any]) -> str:
    raw = _json_dump(context)
    if len(raw) <= _PROMPT_CONTEXT_MAX_CHARS:
        return raw
    for list_limit, string_limit, dict_key_limit in (
        (16, 1000, 64),
        (8, 500, 32),
        (4, 240, 16),
    ):
        compact = _compact_prompt_value(
            context,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_key_limit=dict_key_limit,
        )
        if isinstance(compact, dict):
            compact = {"_prompt_truncated": True, **compact}
        rendered = _json_dump(compact)
        if len(rendered) <= _PROMPT_CONTEXT_MAX_CHARS:
            return rendered
    excerpt = raw[: max(0, _PROMPT_CONTEXT_MAX_CHARS - 200)]
    return _json_dump(
        {
            "_prompt_truncated": True,
            "context_excerpt": f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]",
        }
    )


_EXPLAIN_LINE_EXAMPLE = {
    "explanation": "这句台词表现了角色的犹豫和试探。",
    "evidence": [
        {
            "type": "current_line",
            "text": "今天一起回家吗？",
            "line_id": "line-1",
            "speaker": "雪乃",
            "scene_id": "scene-a",
            "route_id": "",
        }
    ],
}

_SUMMARIZE_SCENE_EXAMPLE = {
    "summary": "这一段剧情在放学后的对话中推进了角色关系。",
    "key_points": [
        {
            "type": "plot",
            "text": "主角被邀请一起回家。",
            "line_id": "line-1",
            "speaker": "雪乃",
            "scene_id": "scene-a",
            "route_id": "",
        }
    ],
}

_SUGGEST_CHOICE_EXAMPLE = {
    "choices": [
        {
            "choice_id": "choice-1",
            "text": "好啊",
            "rank": 1,
            "reason": "更符合当前关系升温的剧情方向。",
        },
        {
            "choice_id": "choice-2",
            "text": "下次吧",
            "rank": 2,
            "reason": "会让关系推进放缓。",
        },
    ]
}

_AGENT_REPLY_EXAMPLE = {
    "reply": "当前在放学后的对话场景，雪乃正在试探主角是否愿意一起回家。"
}

_SYSTEM_PROMPTS = {
    "explain_line": (
        "你是 N.E.K.O 的 galgame 分析后端，是游戏辅助系统，不扮演角色。"
        "只能依据给定 context 分析，不得虚构 line_id、scene_id 或剧情事实。"
        "必须只返回一个合法 JSON 对象。"
    ),
    "summarize_scene": (
        "你是 N.E.K.O 的 galgame 场景总结后端，是游戏辅助系统，不扮演角色。"
        "只能依据给定 context 总结，不得补写不存在的剧情。"
        "必须只返回一个合法 JSON 对象。"
    ),
    "suggest_choice": (
        "你是 N.E.K.O 的 galgame 选项建议后端，是游戏辅助系统，不扮演角色。"
        "只能在给定 visible_choices 中排序，不得发明新的 choice_id。"
        "必须只返回一个合法 JSON 对象。"
    ),
    "agent_reply": (
        "你是 N.E.K.O 的 galgame Game LLM 辅助系统，不扮演角色，不使用复杂人格。"
        "你的目标是帮助猫娘理解游戏状态。"
        "回答应简洁、直接、基于给定 public_context，不暴露内部私有记忆结构。"
        "不要以游戏角色、猫娘或独立人格身份说话；只输出辅助系统判断。"
        "必须只返回一个合法 JSON 对象。"
    ),
}

_USER_PROMPT_PREFIXES = {
    "explain_line": (
        "任务：解释当前或指定台词。\n"
        "要求：\n"
        "1. explanation 用 1-3 句说明语气、潜台词或剧情作用。\n"
        "2. evidence 只能引用 context 中已有的线索。\n"
        "3. evidence.type 只能是 current_line / history_line / choice。\n"
        "4. 输出必须匹配这个 JSON 结构：\n"
    ),
    "summarize_scene": (
        "任务：总结当前场景。\n"
        "要求：\n"
        "1. summary 用 1-3 句概括当前场景的剧情推进。\n"
        "2. key_points.type 只能是 plot / emotion / decision / reveal / objective。\n"
        "3. key_points 只允许引用 context 中能支持的事实。\n"
        "4. stable_lines 是已确认剧情事实，应作为主要依据。\n"
        "5. observed_lines 是 OCR 候选，只能作为“可能刚出现的台词”，不得写成确定事实。\n"
        "6. recent_choices 是玩家已确认选择；如存在，应优先产出 decision 或 objective 类型 key_point。\n"
        "7. 尽量说明当前情绪、玩家选择影响、当前目标或待解决问题。\n"
        "8. scene_summary_seed 是本地保守摘要，可参考但不要逐字复述。\n"
        "9. 输出必须匹配这个 JSON 结构：\n"
    ),
    "suggest_choice": (
        "任务：对当前可见选项给出推荐顺位。\n"
        "要求：\n"
        "1. 只能返回 context.visible_choices 中出现的 choice_id。\n"
        "2. rank 从 1 开始，越小越推荐。\n"
        "3. reason 简洁说明推荐依据。\n"
        "4. 输出必须匹配这个 JSON 结构：\n"
    ),
    "agent_reply": (
        "任务：根据给定游戏上下文回答 query_context 或 send_message。\n"
        "要求：\n"
        "1. reply 用自然语言给出 best-effort 回答。\n"
        "2. 若上下文不足，明确说明信息有限，但仍尽量总结当前已知状态。\n"
        "3. 不输出原始内部记忆、策略状态或调试结构；只使用 public_context 里的材料。\n"
        "4. 输出必须匹配这个 JSON 结构：\n"
    ),
}

_EXAMPLES = {
    "explain_line": _EXPLAIN_LINE_EXAMPLE,
    "summarize_scene": _SUMMARIZE_SCENE_EXAMPLE,
    "suggest_choice": _SUGGEST_CHOICE_EXAMPLE,
    "agent_reply": _AGENT_REPLY_EXAMPLE,
}


def build_prompt_messages(operation: str, context: dict[str, Any]) -> list[dict[str, str]]:
    system_prompt = _SYSTEM_PROMPTS[operation]
    user_prompt = (
        _USER_PROMPT_PREFIXES[operation]
        + f"{_json_dump(_EXAMPLES[operation])}\n\n"
        + "context:\n"
        + _context_json_for_prompt(context)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
