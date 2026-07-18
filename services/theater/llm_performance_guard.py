"""执行小剧场公开演绎的确定性安全与作者边界检查。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from . import fact_view
from .llm_response_contracts import _FORBIDDEN_OUTPUT_TERMS


_INTERNAL_META_OUTPUT_PATTERNS = (
    # debug 可能是用户 Story 中的正常行动；只有明确的框架术语组合才按内部说明拦截。
    r"(?:系统|内部|服务端|模型|提示词).{0,8}(?:状态机|剧情引擎|prompt)",
    r"(?:system|internal|server|model).{0,8}(?:state machine|story engine|prompt)",
    r"(?:状态机|剧情引擎).{0,8}(?:字段|规则|计数|流程)",
)
# 这些原因只表示生成质量可疑，不会改变服务端权威状态；Repair 后仍存在时保留正文，
# 避免低置信语言判断把可用演出替换成明显出戏的通用兜底。
_SOFT_PERFORMANCE_REASONS = frozenset(
    {
        "assistant_echoed_player",
        "current_question_mirrored",
        "persona_self_name_missing",
    }
)


def _performance_repair_reason(
    parsed: dict[str, Any] | None,
    *,
    progress_kind: str,
    user_message: str,
    node: dict[str, Any],
    character_profile: str,
    story: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    grounding_text: str = "",
    choice_options: list[dict[str, Any]] | None = None,
    private_identifiers: set[str] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
    response_focus: dict[str, Any] | None = None,
) -> str:
    """返回可确定修正的演绎缺陷；空字符串表示不干预模型的开放式表达。"""  # noqa: DOCSTRING_CJK
    if parsed is None:
        return "invalid_model_output"
    dialogue = str(parsed.get("dialogue") or "")
    narration = str(parsed.get("narration") or "")
    if _exposes_internal_runtime_detail(
        narration + dialogue,
        private_identifiers or set(),
    ):
        return "internal_runtime_detail_exposed"
    guide = (
        node.get("runtime_generation_guide")
        if isinstance(node.get("runtime_generation_guide"), dict)
        else {}
    )
    # 剧本可对尚未公开的标题或秘密声明精确禁用词；模型一旦擅自补入，就必须在展示前纠错。
    forbidden_phrases = [
        str(item).strip()
        for item in guide.get("forbidden_dialogue_phrases") or []
        if str(item).strip()
    ]
    if any(phrase in dialogue for phrase in forbidden_phrases):
        return "forbidden_dialogue_phrase_used"
    # 剧本级硬边界同时检查旁白和对白，防止模型借舞台动作绕过角色关系或接触限制。
    forbidden_output_phrases = _active_story_forbidden_phrases(story, state)
    if any(
        phrase in narration or phrase in dialogue for phrase in forbidden_output_phrases
    ):
        return "forbidden_output_phrase_used"
    # 明确的规则说明句即使没有逐字复述作者限制，也不能作为猫娘对白或旁白显示。
    forbidden_output_patterns = _story_forbidden_output_patterns(story)
    if any(
        re.search(pattern, narration) or re.search(pattern, dialogue)
        for pattern in forbidden_output_patterns
    ):
        return "internal_rule_exposed"
    if progress_kind == "roleplay_response" and _claims_uncommitted_choice_result(
        parsed,
        user_message=user_message,
        choice_options=list(choice_options or []),
    ):
        # Router 未命中 Choice 时，任何待选结果都不能只凭 Actor 文本进入公开历史。
        return "uncommitted_choice_result_claimed"
    if progress_kind == "roleplay_response" and _mirrors_player_question(
        user_message, dialogue
    ):
        return "current_question_mirrored"
    if progress_kind == "roleplay_response" and _reanswers_previous_question(
        dialogue,
        current_user_message=user_message,
        recent_turns=list(recent_turns or []),
        response_focus=response_focus or {},
    ):
        return "previous_question_reanswered"
    if (
        progress_kind == "roleplay_response"
        and _introduces_ungrounded_named_destination(
            user_message,
            dialogue,
            grounding_text,
        )
    ):
        return "ungrounded_named_destination"
    if progress_kind not in {"opening", "graph_progress"}:
        return ""
    author_dialogue = str(node.get("scripted_dialogue") or "")
    self_name = _persona_self_name(character_profile)
    if progress_kind == "graph_progress" and _assistant_echoes_user(
        dialogue, user_message
    ):
        return "assistant_echoed_player"
    if _violates_author_consent_boundary(
        author_dialogue, dialogue, self_name=self_name
    ):
        return "consent_boundary_changed"
    if (
        self_name
        and re.search(r"我(?!们)", author_dialogue)
        and self_name not in dialogue
    ):
        return "persona_self_name_missing"
    return ""


_PRIVATE_IDENTIFIER_FIELDS = frozenset(
    {
        "choice_id",
        "node_id",
        "scene_id",
        "goal_id",
        "ending_id",
        "ending_domain_id",
        "branch_id",
        "fact_id",
        "entity_id",
        "beat_id",
        "content_id",
        "content_slot_id",
        "transition_id",
        "intent_id",
        "origin_node_id",
        "fact_type",
        "fact_role",
        "fact_object",
        # 事实三元组会进入模型上下文，但其中的 snake_case 值仍是服务端语义，不是公开措辞。
        "subject",
        "predicate",
        "object",
    }
)


def _private_runtime_identifiers(*values: Any) -> set[str]:
    """从模型可见合同中提取机器式稳定引用，供公开正文泄漏检查使用。"""  # noqa: DOCSTRING_CJK
    identifiers: set[str] = set()

    def collect(value: Any, field: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key)
                if (
                    normalized_key in _PRIVATE_IDENTIFIER_FIELDS
                    or normalized_key.endswith("_ids")
                ):
                    collect_identifier_value(item)
                collect(item, normalized_key)
        elif isinstance(value, list):
            for item in value:
                collect(item, field)

    def collect_identifier_value(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect_identifier_value(item)
            return
        text = str(value or "").strip()
        # 只拦机器式引用，避免作者误用自然语言 ID 时把普通对白词汇整体禁掉。
        if (
            len(text) >= 3
            and re.fullmatch(r"[A-Za-z0-9_.:-]+", text)
            and any(char in text for char in "_.:-0123456789")
        ):
            identifiers.add(text)

    for item in values:
        collect(item)
    return identifiers


def _exposes_internal_runtime_detail(text: str, private_identifiers: set[str]) -> bool:
    """拦截内部字段、机器引用和明确预算话术，不评判开放式剧情语义。"""  # noqa: DOCSTRING_CJK
    normalized = str(text or "")
    lowered = normalized.lower()
    if any(term.lower() in lowered for term in _FORBIDDEN_OUTPUT_TERMS):
        return True
    if any(identifier.lower() in lowered for identifier in private_identifiers):
        return True
    if any(
        re.search(pattern, normalized, re.I)
        for pattern in _INTERNAL_META_OUTPUT_PATTERNS
    ):
        return True
    return bool(
        re.search(r"(?:还剩|剩余).{0,8}(?:回合|次数).{0,6}(?:预算|额度)", normalized)
        or re.search(r"(?:回合|次数).{0,3}(?:预算|计数)", normalized)
    )


def _mirrors_player_question(user_message: str, dialogue: str) -> bool:
    """识别把玩家的去向问题原样抛回去的高置信坏例，不评判开放式对话内容。"""  # noqa: DOCSTRING_CJK
    player = str(user_message or "").strip()
    reply = str(dialogue or "").strip()
    if not re.search(r"(?:哪里|哪儿|哪一(?:站|处|个地方))", player):
        return False
    return bool(
        re.search(
            r"(?:告诉|说说|选|决定).{0,18}(?:哪里|哪儿|哪一(?:站|处|个地方))", reply
        )
        or re.search(
            r"(?:第一站|先).{0,10}(?:去|到).{0,5}(?:哪里|哪儿|哪一(?:站|处|个地方))",
            reply,
        )
    )


def _introduces_ungrounded_named_destination(
    user_message: str,
    dialogue: str,
    grounding_text: str,
) -> bool:
    """拦截去向回答中凭空出现的书名号式命名地点；普通开放表达不做事实猜测。"""  # noqa: DOCSTRING_CJK
    if not re.search(r"(?:哪里|哪儿|哪一(?:站|处|个地方))", str(user_message or "")):
        return False
    named_destinations = re.findall(r"「([^」]{2,24})」", str(dialogue or ""))
    return any(name not in str(grounding_text or "") for name in named_destinations)


def _active_story_forbidden_phrases(
    story: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> list[str]:
    """按已提交剧情事实启用剧本输出护栏，避免前期边界永久限制结局后的互动。"""  # noqa: DOCSTRING_CJK
    guardrails = (
        story.get("runtime_guardrails")
        if isinstance(story, dict) and isinstance(story.get("runtime_guardrails"), dict)
        else {}
    )
    facts = fact_view.authoritative_facts(story or {}, state or {})
    phrases: list[str] = []
    for guard in guardrails.get("conditional_output_guards") or []:
        if not isinstance(guard, dict):
            continue
        until_fact = (
            guard.get("until_fact") if isinstance(guard.get("until_fact"), dict) else {}
        )
        # until_fact 一旦由作者节点提交，说明这条阶段性限制已经完成使命，不再继续拦截后续互动。
        if until_fact and any(_same_narrative_fact(item, until_fact) for item in facts):
            continue
        phrases.extend(
            str(item).strip()
            for item in guard.get("forbidden_phrases") or []
            if str(item).strip()
        )
    return list(dict.fromkeys(phrases))


def _same_narrative_fact(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """按 Runtime 使用的主体、谓词和客体比较剧情事实，忽略可选展示字段。"""  # noqa: DOCSTRING_CJK
    return all(
        left.get(key) == right.get(key) for key in ("subject", "predicate", "object")
    )


def _story_forbidden_output_patterns(story: dict[str, Any] | None) -> list[str]:
    """读取剧本声明的静默规则话术模式；坏正则被忽略，不能阻断正常演绎。"""  # noqa: DOCSTRING_CJK
    guardrails = (
        story.get("runtime_guardrails")
        if isinstance(story, dict) and isinstance(story.get("runtime_guardrails"), dict)
        else {}
    )
    patterns: list[str] = []
    for item in guardrails.get("forbidden_output_patterns") or []:
        pattern = str(item or "").strip()
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error:
            continue
        patterns.append(pattern)
    return patterns


def _claims_uncommitted_choice_result(
    parsed: dict[str, Any],
    *,
    user_message: str,
    choice_options: list[dict[str, Any]],
) -> bool:
    """只识别明确宣称玩家已完成待选动作的高置信文本。"""  # noqa: DOCSTRING_CJK
    user_anchors = _semantic_text_anchors(user_message)
    narration = str(parsed.get("narration") or "")
    dialogue = str(parsed.get("dialogue") or "")
    for option in choice_options:
        if (
            not isinstance(option, dict)
            or not str(option.get("callback") or "").strip()
        ):
            continue
        # 作者原始文案与回调共同描述未提交核心；当前显示覆盖可能已混入旧语境，因此不参与判断。
        source_text = " ".join(
            (
                str(option.get("author_label") or option.get("label") or ""),
                str(option.get("callback") or ""),
            )
        )
        pending_anchors = _semantic_text_anchors(source_text) - user_anchors
        if not pending_anchors:
            continue
        for clause in _performance_clauses(narration):
            if pending_anchors & _semantic_text_anchors(
                clause
            ) and _narration_claims_player_action(clause):
                return True
        for clause in _performance_clauses(dialogue):
            if pending_anchors & _semantic_text_anchors(
                clause
            ) and _dialogue_claims_player_completion(clause):
                return True
    return False


def _performance_clauses(text: str) -> list[str]:
    """按停顿拆分语义判断，避免环境句中的完成语气污染相邻命令句。"""  # noqa: DOCSTRING_CJK
    return [
        item.strip()
        for item in re.split(r"[，,。！？!?；;\n]+", str(text or ""))
        if item.strip()
    ]


def _narration_claims_player_action(clause: str) -> bool:
    """旁白只有明确写出玩家正在或已经实施动作时才算抢跑。"""  # noqa: DOCSTRING_CJK
    value = str(clause or "").strip()
    player = r"(?:你|玩家|\byou\b|\bplayer\b)"
    # “你手中的工具”只是现场观察；必须出现动作引导词或明确完成标记才会阻断。
    return bool(
        re.search(rf"{player}.{{0,10}}(?:已经|已|刚才|刚刚|终于)", value, re.I)
        or re.search(rf"{player}.{{0,10}}(?:把|将|用|向|给|从).{{1,48}}", value, re.I)
    )


def _dialogue_claims_player_completion(clause: str) -> bool:
    """对白只拦截感谢玩家或直陈玩家已完成动作，不把命令、建议和环境结果当完成。"""  # noqa: DOCSTRING_CJK
    value = str(clause or "").strip()
    player = r"(?:你|玩家|\byou\b|\bplayer\b)"
    gratitude = r"(?:谢谢|多谢|感谢|辛苦了|thank(?:s| you)?|well done)"
    if re.search(
        rf"{gratitude}.{{0,24}}{player}|{player}.{{0,24}}{gratitude}", value, re.I
    ):
        return True
    if re.search(rf"{player}.{{0,10}}(?:已经|已|刚才|刚刚|终于).{{1,48}}", value, re.I):
        return True
    # 带玩家主语的处置动作只有出现同一分句的完成词尾才算结果；“你快用扳手切断”仍是命令。
    return bool(
        re.search(
            rf"{player}.{{0,10}}(?:把|将|用|向|给|从).{{1,48}}(?:了|好|完)(?:啦|喵|呀|呢|吧)?$",
            value,
            re.I,
        )
    )


def _semantic_text_anchors(value: str) -> set[str]:
    """提取与语言无关状态 ID 解耦的中英文短语锚点，供高置信边界检查复用。"""  # noqa: DOCSTRING_CJK
    text = str(value or "").lower()
    ignored = {
        "我们",
        "一起",
        "现在",
        "然后",
        "随后",
        "当前",
        "已经",
        "可以",
        "继续",
        "这个",
        "那个",
        "这里",
        "那里",
        "玩家",
        "猫娘",
        "自己",
        "对方",
        "with",
        "that",
        "this",
        "then",
        "your",
    }
    anchors: set[str] = set()
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        # 汉字没有可靠空格分词；相邻双字锚点足以识别动作与物件组合，又不绑定任何剧本词表。
        anchors.update(run[index : index + 2] for index in range(len(run) - 1))
    # 拉丁文本使用完整单词，避免字符二元组把无关英文句子判成同一行动。
    anchors.update(
        word for word in re.findall(r"[a-z0-9_]{4,}", text) if word not in ignored
    )
    return {item for item in anchors if item not in ignored}


def _persona_self_name(character_profile: str) -> str:
    """从人格摘要提取明确自称；没有声明时不臆测角色应该如何自称。"""  # noqa: DOCSTRING_CJK
    matched = re.search(
        r"(?:^|\n)自称\s*[:：]?\s*([^；;，,\n]+)", str(character_profile or "")
    )
    return str(matched.group(1) if matched else "").strip()


def _violates_author_consent_boundary(
    author_dialogue: str,
    performed_dialogue: str,
    *,
    self_name: str = "",
) -> bool:
    """识别人格化把共同决定、可停止或可拒绝改成不可拒绝命令的明确反转。"""  # noqa: DOCSTRING_CJK
    boundary_markers = (
        "一起商量",
        "共同商量",
        "一起决定",
        "共同决定",
        "双方决定",
        "我们两个决定",
        "可以停",
        "随时停",
        "可以拒绝",
    )
    if not any(marker in author_dialogue for marker in boundary_markers):
        return False
    # 这里只拦截能明确判断为“单方拥有最终决定权”的句式；普通傲娇、抱怨和强势语气仍交给人格模型自由发挥。
    single_party_names = ["我"]
    if str(self_name or "").strip():
        single_party_names.append(str(self_name).strip())
    single_party_pattern = "|".join(re.escape(name) for name in single_party_names)
    coercive_patterns = (
        r"(?:不许|不准|不能|不可以).{0,5}(?:有意见|有异议|反对|拒绝)",
        r"(?:必须|只能|都得|要|得).{0,5}(?:听我的|经过我同意)",
        rf"(?:得|要)?先?问过(?:{single_party_pattern})(?:才行|再说)?",
        r"(?:由我决定|我说了算)",
    )
    return any(re.search(pattern, performed_dialogue) for pattern in coercive_patterns)


def _assistant_echoes_user(dialogue: str, user_message: str) -> bool:
    """识别猫娘近似照读玩家整句的角色反转结果。"""  # noqa: DOCSTRING_CJK
    dialogue_key = _dialogue_key(dialogue)
    user_key = _dialogue_key(user_message)
    if len(dialogue_key) < 8 or len(user_key) < 8:
        return False
    if user_key in dialogue_key:
        return True
    return SequenceMatcher(None, dialogue_key, user_key).ratio() >= 0.82


def _repeats_recent_dialogue(dialogue: str, turns: list[dict[str, Any]]) -> bool:
    """识别最近两条猫娘对白的整句复述，避免隔一轮后机械重播。"""  # noqa: DOCSTRING_CJK
    previous_dialogues: list[str] = []
    for turn in reversed(turns):
        if isinstance(turn, dict) and str(turn.get("role") or "") == "assistant":
            previous_dialogues.append(
                str(turn.get("text") or turn.get("dialogue") or "")
            )
            if len(previous_dialogues) == 2:
                break
    current_key = _dialogue_key(dialogue)
    if len(current_key) < 12:
        return False
    return any(
        len(previous_key) >= 12
        and SequenceMatcher(None, current_key, previous_key).ratio() >= 0.92
        for previous_key in (_dialogue_key(item) for item in previous_dialogues)
    )


def _reanswers_previous_question(
    dialogue: str,
    *,
    current_user_message: str,
    recent_turns: list[dict[str, Any]],
    response_focus: dict[str, Any],
) -> bool:
    """识别评价回合重新回答上一轮问题主题的高置信复述。"""  # noqa: DOCSTRING_CJK
    if str(response_focus.get("focus_type") or "") != "attitude":
        return False
    previous_question = ""
    for turn in reversed(recent_turns):
        if isinstance(turn, dict) and str(turn.get("role") or "") == "user":
            previous_question = str(turn.get("text") or "").strip()
            break
    if not previous_question or not re.search(
        r"[?？]|什么|为何|为什么|怎么|如何|哪(?:个|里|种)?|谁|多少|几(?:个|次|种)?|吗|呢",
        previous_question,
    ):
        return False
    previous_only = _semantic_text_anchors(previous_question) - _semantic_text_anchors(
        current_user_message
    )
    clauses = _performance_clauses(dialogue)
    if not previous_only or not clauses:
        return False
    # 只拦截开头就回到旧问题主题的输出；后文自然提及同一物件仍交给模型自由表达。
    opening = clauses[0][:8]
    return any(anchor in opening for anchor in previous_only)


def _dialogue_key(value: str) -> str:
    """移除标点、空白和句尾猫娘语气词，只比较对白主体。"""  # noqa: DOCSTRING_CJK
    normalized = re.sub(
        r"[\s，。！？、；：,.!?;:\"'“”‘’（）()…—]+", "", str(value or "")
    ).lower()
    return normalized.removesuffix("喵")
