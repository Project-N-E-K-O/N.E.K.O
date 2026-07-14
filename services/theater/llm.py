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
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.tokenize import truncate_to_tokens


THEATER_TURN_TIMEOUT_SECONDS = 10.0
THEATER_TURN_OUTPUT_MAX_TOKENS = 360
THEATER_CONTEXT_MAX_TOKENS = 500
_FORBIDDEN_OUTPUT_TERMS = ("scene_id", "node_id", "prompt", "状态机", "剧情引擎", "debug")
logger = get_module_logger("services.theater.llm")


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
    choice_options: list[dict[str, Any]] | None = None,
    latent_transitions: list[dict[str, Any]] | None = None,
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
    fallback = _contextual_roleplay_fallback(
        fallback,
        user_message=user_message,
        progress_kind=progress_kind,
        choice_options=list(choice_options or []),
    )
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info(
            "Theater turn uses author fallback: reason=model_config_missing progress=%s node=%s catgirl=%s",
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        return fallback

    public_state = {
        "已发现线索": list(state.get("clue_ids") or []),
        "已使用道具": list(state.get("used_prop_ids") or []),
        "场景笔记": list(state.get("scene_notes") or [])[-4:],
        # 只提供已经由作者节点提交的事实，帮助自由互动区分可引用内容与尚未发生的真相。
        "已确认事实": list(state.get("narrative_facts") or [])[-8:],
    }
    prompt_story = dict(story)
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""), THEATER_CONTEXT_MAX_TOKENS
    )
    character_profile = _load_character_profile(config_manager, lanlan_name)
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
        character_profile=character_profile,
        choice_options=list(choice_options or []),
        latent_transitions=list(latent_transitions or []),
    )
    try:
        result = await _invoke_model_once(api_config, system_prompt, user_prompt)
    except Exception as exc:
        # 不记录提示词、玩家输入或模型原文，只记录可定位的失败类型，避免下一次只能从固定台词反推原因。
        logger.warning(
            "Theater turn uses author fallback: reason=model_call_failed error=%s progress=%s node=%s catgirl=%s",
            type(exc).__name__,
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        return fallback
    allowed_choice_ids = {str(item.get("choice_id") or "") for item in choice_options or []}
    # 模型只允许返回当前节点作者声明的 intent_id；目标节点和权威状态从不接受模型输出。
    allowed_intent_ids = {str(item.get("intent_id") or "") for item in latent_transitions or []}
    parsed = _parse_output(
        getattr(result, "content", ""),
        progress_kind=progress_kind,
        allowed_choice_ids=allowed_choice_ids,
        allowed_intent_ids=allowed_intent_ids,
    )
    authored_performance = progress_kind in {"opening", "graph_progress"}
    grounding_text = json.dumps(
        {
            "background": story.get("background") or story.get("world_seed") or "",
            "scene": scene,
            "public_state": public_state,
            "recent_turns": _recent_public_turns(recent_turns),
            "choice_options": list(choice_options or []),
            "user_message": user_message,
        },
        ensure_ascii=False,
    )
    performance_repair_reason = _performance_repair_reason(
        parsed,
        progress_kind=progress_kind,
        user_message=user_message,
        node=node,
        character_profile=character_profile,
        story=story,
        state=state,
        grounding_text=grounding_text,
    )
    choice_rewrite_repair_reason = _choice_rewrite_repair_reason(
        parsed,
        progress_kind=progress_kind,
        choice_options=list(choice_options or []),
    )
    repair_reason = performance_repair_reason or choice_rewrite_repair_reason
    if repair_reason:
        # 只对可机械判定的协议、人格边界或推荐项新鲜度错误重试一次；开放式文风不进入循环评判。
        if progress_kind == "roleplay_response":
            correction = (
                "\n纠错重试：上一版输出未通过检查（"
                + repair_reason
                + "）。请重新输出完整 JSON，不得提及纠错过程。若未命中当前选项，必须逐个返回所有当前 "
                "choice_id；每条 label 都要承接本轮新输入和本轮回应，并与当前显示文案、作者原文有实质差异，"
                "不能只改标点、退回旧文案或继续重复已经完成的话。玩家已经讨论后续但尚未完成当前意图时，"
                "要把当前核心意图与新话题合并成一句自然的下一步。不得使用目标节点列出的禁用对白词。"
                "玩家本轮提出问题时必须先直接回答，不得把同一个问题换种说法反问玩家。"
                "不得编造公开上下文中没有出现的命名地点；若去向尚未确定，只回答已经公开的最近目的地。"
                "内部规则只能执行，不能在旁白、对白或推荐项里解释、承诺或换一种说法复述。"
            )
        else:
            correction = (
                "\n纠错重试：上一版输出未通过检查（"
                + repair_reason
                + "）。请重新输出完整 JSON，不得提及纠错过程。必须显著按猫娘人格转述作者含义，"
                "不得复述玩家，不得增加命令、强迫或单方批准，也不得使用目标节点列出的禁用对白词。"
                "故事输出硬边界同时约束旁白和对白；内部规则只能执行，不能由猫娘说给玩家。"
            )
        try:
            repaired_result = await _invoke_model_once(api_config, system_prompt, user_prompt + correction)
        except Exception as exc:
            logger.warning(
                "Theater turn uses author fallback: reason=repair_call_failed repair=%s error=%s progress=%s node=%s catgirl=%s",
                repair_reason,
                type(exc).__name__,
                progress_kind,
                str(node.get("node_id") or ""),
                lanlan_name,
            )
            return _authored_performance_fallback(fallback, node, progress_kind)
        repaired = _parse_output(
            getattr(repaired_result, "content", ""),
            progress_kind=progress_kind,
            allowed_choice_ids=allowed_choice_ids,
            allowed_intent_ids=allowed_intent_ids,
        )
        remaining_performance_reason = _performance_repair_reason(
            repaired,
            progress_kind=progress_kind,
            user_message=user_message,
            node=node,
            character_profile=character_profile,
            story=story,
            state=state,
            grounding_text=grounding_text,
        )
        remaining_choice_rewrite_reason = _choice_rewrite_repair_reason(
            repaired,
            progress_kind=progress_kind,
            choice_options=list(choice_options or []),
        )
        remaining_reason = remaining_performance_reason or remaining_choice_rewrite_reason
        if remaining_reason:
            if (
                parsed is not None
                and not performance_repair_reason
                and choice_rewrite_repair_reason
                and remaining_performance_reason
            ):
                # 仅推荐项改写有误时，第二次调用不能反过来污染首版已经合格的当前回应；
                # 保留首版对白并退回作者按钮，比展示新产生的反问或幻觉更符合玩家当轮语境。
                parsed = dict(parsed)
                parsed["choice_rewrites"] = []
                logger.warning(
                    "Theater turn keeps first valid dialogue after rewrite repair regressed: first=%s second=%s progress=%s node=%s catgirl=%s",
                    repair_reason,
                    remaining_reason,
                    progress_kind,
                    str(node.get("node_id") or ""),
                    lanlan_name,
                )
            else:
                logger.warning(
                    "Theater turn uses author fallback: reason=repair_rejected first=%s second=%s progress=%s node=%s catgirl=%s",
                    repair_reason,
                    remaining_reason,
                    progress_kind,
                    str(node.get("node_id") or ""),
                    lanlan_name,
                )
                return _authored_performance_fallback(fallback, node, progress_kind)
        else:
            parsed = repaired
    matched_option = next(
        (
            item
            for item in choice_options or []
            if str(item.get("choice_id") or "") == str((parsed or {}).get("matched_choice_id") or "")
        ),
        None,
    )
    if parsed and authored_performance and str(callback or "").strip():
        # 开场 Scene 或 Choice callback 都是作者已确认的公开演出；模型只能增强猫娘回应，不能改写或抢跑旁白。
        parsed["narration"] = str(callback).strip()
    if parsed and progress_kind == "graph_progress" and _assistant_echoes_user(
        parsed["dialogue"],
        user_message,
    ):
        # 猫娘近似复述玩家点击的整句时属于角色反转；优先使用作者台词，不把玩家台词提交成猫娘对白。
        logger.warning(
            "Theater turn uses author fallback: reason=assistant_echoed_player progress=%s node=%s catgirl=%s",
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        return _authored_performance_fallback(fallback, node, progress_kind)
    if parsed and progress_kind == "roleplay_response" and matched_option:
        # 自然语言命中仍必须采用作者 Choice 的 callback；模型只负责选当前 ID 和生成猫娘演绎。
        author_callback = str(matched_option.get("callback") or "").strip()
        if author_callback:
            parsed["narration"] = author_callback
        if _assistant_echoes_user(parsed["dialogue"], user_message):
            author_dialogue = str(matched_option.get("target_scripted_dialogue") or "").strip()
            if author_dialogue:
                parsed["dialogue"] = author_dialogue
    elif parsed and progress_kind == "roleplay_response":
        # 自由互动不需要重复舞台动作；保留新内容并删除模型复述，避免为纠错增加第二次调用。
        parsed = _sanitize_roleplay_repetition(parsed, recent_turns)
        if not parsed["dialogue"] or _repeats_recent_dialogue(parsed["dialogue"], recent_turns):
            # 小模型整段复述上一句时视为无效输出，保持单次调用并走安全回应。
            return fallback
    if parsed is None:
        logger.warning(
            "Theater turn uses author fallback: reason=invalid_model_output progress=%s node=%s catgirl=%s",
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        return fallback
    return parsed


async def _invoke_model_once(
    api_config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
) -> Any:
    """执行一次结构化演绎请求；纠错是否需要第二次调用由上层的确定性检查决定。"""  # noqa: DOCSTRING_CJK
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
        return await asyncio.wait_for(
            client.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
            timeout=THEATER_TURN_TIMEOUT_SECONDS,
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
) -> str:
    """返回可确定修正的演绎缺陷；空字符串表示不干预模型的开放式表达。"""  # noqa: DOCSTRING_CJK
    if parsed is None:
        return "invalid_model_output"
    dialogue = str(parsed.get("dialogue") or "")
    narration = str(parsed.get("narration") or "")
    guide = node.get("runtime_generation_guide") if isinstance(node.get("runtime_generation_guide"), dict) else {}
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
    if any(phrase in narration or phrase in dialogue for phrase in forbidden_output_phrases):
        return "forbidden_output_phrase_used"
    # 明确的规则说明句即使没有逐字复述作者限制，也不能作为猫娘对白或旁白显示。
    forbidden_output_patterns = _story_forbidden_output_patterns(story)
    if any(re.search(pattern, narration) or re.search(pattern, dialogue) for pattern in forbidden_output_patterns):
        return "internal_rule_exposed"
    if progress_kind == "roleplay_response" and _mirrors_player_question(user_message, dialogue):
        return "current_question_mirrored"
    if progress_kind == "roleplay_response" and _introduces_ungrounded_named_destination(
        user_message,
        dialogue,
        grounding_text,
    ):
        return "ungrounded_named_destination"
    if progress_kind not in {"opening", "graph_progress"}:
        return ""
    author_dialogue = str(node.get("scripted_dialogue") or "")
    self_name = _persona_self_name(character_profile)
    if progress_kind == "graph_progress" and _assistant_echoes_user(dialogue, user_message):
        return "assistant_echoed_player"
    if _violates_author_consent_boundary(author_dialogue, dialogue, self_name=self_name):
        return "consent_boundary_changed"
    if self_name and re.search(r"我(?!们)", author_dialogue) and self_name not in dialogue:
        return "persona_self_name_missing"
    if _near_duplicate_text(dialogue, author_dialogue, minimum_length=16):
        return "author_dialogue_nearly_copied"
    return ""


def _mirrors_player_question(user_message: str, dialogue: str) -> bool:
    """识别把玩家的去向问题原样抛回去的高置信坏例，不评判开放式对话内容。"""  # noqa: DOCSTRING_CJK
    player = str(user_message or "").strip()
    reply = str(dialogue or "").strip()
    if not re.search(r"(?:哪里|哪儿|哪一(?:站|处|个地方))", player):
        return False
    return bool(
        re.search(r"(?:告诉|说说|选|决定).{0,18}(?:哪里|哪儿|哪一(?:站|处|个地方))", reply)
        or re.search(r"(?:第一站|先).{0,10}(?:去|到).{0,5}(?:哪里|哪儿|哪一(?:站|处|个地方))", reply)
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
    facts = [item for item in (state or {}).get("narrative_facts") or [] if isinstance(item, dict)]
    phrases: list[str] = []
    for guard in guardrails.get("conditional_output_guards") or []:
        if not isinstance(guard, dict):
            continue
        until_fact = guard.get("until_fact") if isinstance(guard.get("until_fact"), dict) else {}
        # until_fact 一旦由作者节点提交，说明这条阶段性限制已经完成使命，不再继续拦截后续互动。
        if until_fact and any(_same_narrative_fact(item, until_fact) for item in facts):
            continue
        phrases.extend(str(item).strip() for item in guard.get("forbidden_phrases") or [] if str(item).strip())
    return list(dict.fromkeys(phrases))


def _same_narrative_fact(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """按 Runtime 使用的主体、谓词和客体比较剧情事实，忽略可选展示字段。"""  # noqa: DOCSTRING_CJK
    return all(left.get(key) == right.get(key) for key in ("subject", "predicate", "object"))


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


def _choice_rewrite_repair_reason(
    parsed: dict[str, Any] | None,
    *,
    progress_kind: str,
    choice_options: list[dict[str, Any]],
) -> str:
    """未命中自由互动时要求每个推荐项都真正承接本轮新语境。"""  # noqa: DOCSTRING_CJK
    if parsed is None or progress_kind != "roleplay_response":
        return ""
    if str(parsed.get("matched_choice_id") or ""):
        return ""
    expected = {
        str(item.get("choice_id") or ""): item
        for item in choice_options
        if str(item.get("choice_id") or "")
    }
    if not expected:
        return ""
    rewrites = {
        str(item.get("choice_id") or ""): str(item.get("label") or "").strip()
        for item in parsed.get("choice_rewrites") or []
        if isinstance(item, dict)
    }
    if set(rewrites) != set(expected):
        return "choice_rewrites_incomplete"
    for choice_id, option in expected.items():
        candidate = rewrites[choice_id]
        dialogue_mode = str(option.get("choice_mode") or "") == "dialogue"
        if dialogue_mode != _is_quoted_choice_label(candidate):
            return "choice_rewrite_type_changed"
        candidate_key = _choice_label_key(candidate)
        previous_keys = {
            _choice_label_key(str(option.get("label") or "")),
            _choice_label_key(str(option.get("author_label") or option.get("label") or "")),
        }
        if not candidate_key or candidate_key in previous_keys:
            return "choice_rewrite_not_updated"
    return ""


def _is_quoted_choice_label(label: str) -> bool:
    """判断推荐项是否保持为纯对白，供模型重试前做确定性检查。"""  # noqa: DOCSTRING_CJK
    text = str(label or "").strip()
    return any(
        text.startswith(left) and text.endswith(right)
        for left, right in (("“", "”"), ("「", "」"), ('"', '"'))
    )


def _choice_label_key(label: str) -> str:
    """忽略引号、标点和空白，防止模型用表面变化伪装成新推荐项。"""  # noqa: DOCSTRING_CJK
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’「」（）()…—]+", "", str(label or "")).lower()


def _persona_self_name(character_profile: str) -> str:
    """从人格摘要提取明确自称；没有声明时不臆测角色应该如何自称。"""  # noqa: DOCSTRING_CJK
    matched = re.search(r"(?:^|\n)自称\s*[:：]?\s*([^；;，,\n]+)", str(character_profile or ""))
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


def _authored_performance_fallback(
    fallback: dict[str, Any],
    node: dict[str, Any],
    progress_kind: str,
) -> dict[str, Any]:
    """纠错失败时恢复作者台词，避免场景笔记把必要的剧情交接替换成泛化回应。"""  # noqa: DOCSTRING_CJK
    if progress_kind not in {"opening", "graph_progress"}:
        return fallback
    author_fallback = dict(fallback)
    scripted_dialogue = str(node.get("scripted_dialogue") or "").strip()
    if scripted_dialogue:
        author_fallback["dialogue"] = scripted_dialogue
    return author_fallback


def _contextual_roleplay_fallback(
    fallback: dict[str, Any],
    *,
    user_message: str,
    progress_kind: str,
    choice_options: list[dict[str, Any]],
) -> dict[str, Any]:
    """模型连续失败时，用当前公开 Choice 回答明确去向问题，不编造新地点。"""  # noqa: DOCSTRING_CJK
    if progress_kind != "roleplay_response" or not re.search(
        r"(?:哪里|哪儿|哪一(?:站|处|个地方))",
        str(user_message or ""),
    ):
        return fallback
    for option in choice_options:
        # 上一轮上下文化按钮可能省略地点，作者原文仍是可公开且稳定的保守依据。
        for label in (str(option.get("label") or ""), str(option.get("author_label") or "")):
            matched = re.search(r"抵达([^，。；]{1,12})后", label)
            if not matched:
                continue
            destination = matched.group(1).strip()
            if destination:
                contextual = dict(fallback)
                contextual["dialogue"] = f"那就先去{destination}吧。到了那里，我们再一起看接下来怎么走喵。"
                return contextual
    return fallback


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
            "我听见了……但我还在想眼前这件事。先别急，让我把真正想说的话理清楚再回答你喵。"
            if message
            else f"{name}还在这里喵。"
        )
        # 模型不可用时不得猜测玩家是否完成 Choice；保守停留是自然语言推进的安全底线。
        return {
            "narration": "",
            "dialogue": dialogue,
            "choice_rewrites": [],
            "matched_choice_id": "",
            "observed_intent_id": "",
        }
    narration = str(callback or node.get("summary") or scene.get("text") or "").strip()
    # 发生过自由互动后，固定台词可能与刚形成的语境冲突，只在纯作者路径中回退使用。
    dialogue = "" if has_scene_notes else str(node.get("scripted_dialogue") or "").strip()
    if not dialogue:
        guide = node.get("runtime_generation_guide") if isinstance(node.get("runtime_generation_guide"), dict) else {}
        dialogue = str(guide.get("catgirl_raw_intent") or f"我们继续看看接下来会发生什么吧，{name}会陪着你喵。").strip()
    return {
        "narration": narration,
        "dialogue": dialogue,
        "choice_rewrites": [],
        "matched_choice_id": "",
        "observed_intent_id": "",
    }


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
    allowed_intent_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """解析模型 JSON，并把 Choice 与隐藏意图都限制到当前作者白名单。"""  # noqa: DOCSTRING_CJK
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
    matched_choice_id = ""
    observed_intent_id = ""
    if progress_kind == "roleplay_response":
        candidate_match = str(payload.get("matched_choice_id") or "").strip()
        if candidate_match in allowed:
            matched_choice_id = candidate_match
        # 可见 Choice 命中优先；两种路由 ID 同时出现时，不允许模型暗中覆盖主线选择。
        candidate_intent = str(payload.get("observed_intent_id") or "").strip()
        if not matched_choice_id and candidate_intent in (allowed_intent_ids or set()):
            observed_intent_id = candidate_intent
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
    return {
        "narration": narration,
        "dialogue": dialogue,
        "choice_rewrites": rewrites,
        "matched_choice_id": matched_choice_id,
        "observed_intent_id": observed_intent_id,
    }


def _recent_public_turns(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """提取最近公开旁白与对白，让模型承接已发生动作而不读取私有状态。"""  # noqa: DOCSTRING_CJK
    result: list[dict[str, str]] = []
    for turn in turns[-4:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        dialogue = str(turn.get("text") or turn.get("dialogue") or "").strip()
        narration = str(turn.get("narration") or "").strip() if role == "assistant" else ""
        if role == "user" and dialogue:
            result.append({"role": role, "text": truncate_to_tokens(dialogue, 60)})
        elif role == "assistant" and (dialogue or narration):
            # 对白承担上下文逻辑，必须独立优先保留；旁白只提供短动作锚点，不能挤掉对白。
            result.append(
                {
                    "role": role,
                    "dialogue": truncate_to_tokens(dialogue, 60),
                    "narration": truncate_to_tokens(narration, 24),
                }
            )
    return result


def _sanitize_roleplay_repetition(
    parsed: dict[str, Any],
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """删除自由互动中重复的旁白与对白句段，保留模型本轮产生的新内容。"""  # noqa: DOCSTRING_CJK
    previous_dialogues: list[str] = []
    previous_narrations: list[str] = []
    for turn in reversed(turns):
        if isinstance(turn, dict) and str(turn.get("role") or "") == "assistant":
            previous_dialogues.append(str(turn.get("text") or turn.get("dialogue") or "").strip())
            previous_narrations.append(str(turn.get("narration") or "").strip())
            if len(previous_dialogues) == 2:
                break
    result = dict(parsed)
    narration = str(result.get("narration") or "").strip()
    if any(
        _near_duplicate_text(narration, previous_narration, minimum_length=12)
        for previous_narration in previous_narrations
    ):
        # 自由互动旁白允许为空；重复动作直接删除，不牺牲仍然有效的猫娘回应。
        narration = ""
    result["narration"] = narration
    result["dialogue"] = _remove_repeated_dialogue_segments(
        str(result.get("dialogue") or ""),
        previous_dialogues,
    )
    return result


def _remove_repeated_dialogue_segments(dialogue: str, previous: list[str]) -> str:
    """按自然句段删除最近两轮已经说过的长片段，短语气词不参与去重。"""  # noqa: DOCSTRING_CJK
    previous_segments = [
        _dialogue_key(segment)
        for previous_dialogue in previous
        for segment in _sentence_segments(previous_dialogue)
    ]
    kept: list[str] = []
    for segment in _sentence_segments(dialogue):
        key = _dialogue_key(segment)
        repeated = any(
            _near_duplicate_text(key, previous_key, minimum_length=8)
            for previous_key in previous_segments
        )
        if not repeated:
            kept.append(segment)
    return "".join(kept).strip()


def _sentence_segments(value: str) -> list[str]:
    """按句末标点和省略号切分对白，同时保留原标点供界面展示。"""  # noqa: DOCSTRING_CJK
    return [item for item in re.findall(r".*?(?:……|[。！？!?]+|$)", str(value or "")) if item]


def _near_duplicate_text(current: str, previous: str, *, minimum_length: int) -> bool:
    """用归一化文本识别包含关系或高相似复述。"""  # noqa: DOCSTRING_CJK
    current_key = _dialogue_key(current)
    previous_key = _dialogue_key(previous)
    if len(current_key) < minimum_length or len(previous_key) < minimum_length:
        return False
    if current_key in previous_key or previous_key in current_key:
        return True
    return SequenceMatcher(None, current_key, previous_key).ratio() >= 0.9


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
            previous_dialogues.append(str(turn.get("text") or turn.get("dialogue") or ""))
            if len(previous_dialogues) == 2:
                break
    current_key = _dialogue_key(dialogue)
    if len(current_key) < 12:
        return False
    return any(
        len(previous_key) >= 12 and SequenceMatcher(None, current_key, previous_key).ratio() >= 0.92
        for previous_key in (_dialogue_key(item) for item in previous_dialogues)
    )


def _dialogue_key(value: str) -> str:
    """移除标点、空白和句尾猫娘语气词，只比较对白主体。"""  # noqa: DOCSTRING_CJK
    normalized = re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()…—]+", "", str(value or "")).lower()
    return normalized.removesuffix("喵")


def _load_character_profile(config_manager: Any | None, lanlan_name: str) -> str:
    """读取当前猫娘的短人格摘要，不加载普通聊天全文。"""  # noqa: DOCSTRING_CJK
    root = getattr(config_manager, "app_docs_dir", None) if config_manager is not None else None
    if not root or not lanlan_name:
        return ""
    name = str(lanlan_name).strip()
    try:
        characters = config_manager.load_characters()
    except Exception:
        return ""
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    current_name = str(characters.get("当前猫娘") or "").strip() if isinstance(characters, dict) else ""
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
