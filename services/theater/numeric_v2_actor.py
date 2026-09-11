"""Numeric v2 演绎编排：一次生成表现正文与玩家输入推荐。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from copy import deepcopy
import difflib
import inspect
import json
import logging
import time
from typing import Any, Callable, Mapping

from config.prompts.prompts_theater import (
    NUMERIC_V2_ACTOR_JSON_INSTRUCTION,
    NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION,
)
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.token_tracker import set_call_type
from .numeric_v2_usage import invoke_with_usage
from utils.tokenize import count_tokens, truncate_head_tail_tokens, truncate_to_tokens

from .llm_context import (
    _load_character_profile,
    _load_player_address,
)
from .numeric_v2_budget import (
    NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    numeric_v2_actor_budget,
)
from .numeric_v2_cast import NumericV2CastProjection
from .numeric_v2_context import (
    PLAYER_ACTION_LANGUAGE_RULE,
    SCENE_ENTRY_STATE_RULE,
    HISTORY_EVIDENCE_RULE,
    history_evidence,
    history_lookup_note,
    current_scene_records,
    pending_transition_performance,
    scene_narrative_focus,
    scene_narrative_summary,
    scene_opening_text,
)
from .numeric_v2_fixed_narration import actor_note
from .numeric_v2_actor_output import (
    NumericV2ActorError,
    NumericV2ActorOutputError,
    NumericV2ActorUnavailableError,
    _parse_output,
    _sentence_units,
    _text_is_covered,
)
from .numeric_v2_performance import (
    content_blocks,
    performance_content_blocks,
    transition_source_dialogue_policy,
)
from .numeric_v2_runtime import NumericV2Engine, ScriptSessionV2, TurnOutcomeV2


NUMERIC_V2_ACTOR_TIMEOUT_SECONDS = 35.0
NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS = 700
NUMERIC_V2_ACTOR_OPENING_MAX_OUTPUT_TOKENS = 900
NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS = 1200
NUMERIC_V2_ACTOR_SUGGESTION_FILL_MAX_OUTPUT_TOKENS = 260
NUMERIC_V2_ACTOR_SLOW_CALL_SECONDS = 15.0
NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_BLOCKS = 2
NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_TOKENS = 80
_RESTRICTED_KNOWLEDGE_SCOPE = (
    "只把 current_story_beat、recent_context、player_input 与 acting_context 明确允许的可观察状态视为已知；"
    "其余身份、历史和关系未知。"
)
_STYLE_ONLY_PERSONA_FIELDS = frozenset({
    "自称",
    "性格",
    "核心特质",
    "核心特点",
    "口癖",
    "常用口癖",
    "说话风格",
    "语言风格",
    "表达风格",
    "语气",
})
_PERSONA_TONE_FIELDS = frozenset({"性格", "核心特质", "核心特点"})
def _output_schema_instruction(phase: str) -> str:
    """只发送当前调用需要的输出形状，避免普通回合重复携带换场协议。"""  # noqa: DOCSTRING_CJK

    if phase == "opening":
        shape = (
            "顶层字段必须包含 scene_narration:string、performance:string、"
            "suggested_inputs:string[]、transition_offered:boolean。开场通常将 transition_offered 设为 false。"
        )
    elif phase == "transition":
        # 无作者桥段时仍使用三段数组；给出实际 JSON，避免把 phase 值误生成为嵌套键名。
        shape = (
            '严格使用此 JSON 形状：{"segments":[{"phase":"source_response","performance":"来源回应"},'
            '{"phase":"transition_bridge","scene_narration":"换场旁白"},'
            '{"phase":"target_opening","performance":"目标回应"}],"suggested_inputs":[]}。'
            "phase 是固定字符串值，不是外层键名；两个 performance 必须是非空正文字符串，进入终局也不例外。"
            "source_response 可增加 scene_narration:string，呈现本轮在场 NPC 的必要动作或答复，无则省略。"
        )
    elif phase == "transition_compact":
        shape = (
            "顶层字段必须包含 source_scene_narration:string、source_performance:string、target_performance:string、"
            "bridge_scene_narration:string、target_scene_narration:string、"
            "suggested_inputs:string[]。"
            # 来源段也能承载外部回应，避免把 NPC 答复挤进猫娘表演或换幕桥段。
            "先写 source_scene_narration：本轮询问在场 NPC 时，用‘角色名回答：“具体内容”’交付他的答复；"
            "无待答问题或必要外部动作时填空字符串。"
            "两个 performance 与 target_scene_narration 必须非空。"
            "仅当 transition.bridge_required 为 false 时，bridge_scene_narration 可以填空字符串，"
            "不为凑过场补写动作；为 true 时必须交付必要桥段。"
            "两个 performance 写猫娘表演；桥段和目标旁白只写场景事实。"
            "不要输出 segments、phase 或 opening_scene。"
        )
    elif phase == "suggestion_fill":
        shape = "顶层字段必须且只能是 suggested_inputs:string[]。"
    elif phase == "transition_suggestion_fill":
        shape = (
            "顶层字段必须且只能是 accept_input:string、alternative_inputs:string[]；"
            "accept_input 是玩家明确接受并亲自执行正文转场提议的输入，"
            "alternative_inputs 是 1—2 条拒绝、暂缓或当前幕替代行动。"
        )
    else:
        shape = (
            "顶层必须包含 performance:string、suggested_inputs:string[]、transition_offered:boolean；仅当本轮产生新的可见环境、"
            "时间、地点、实体或关键物品状态时，才额外输出 scene_update:string，否则必须省略该字段。"
            "scene_update 只能记录当前幕已经发生的变化；无论 transition_offered 为 true 还是 false，都不得在其中写成玩家已接受、"
            "双方已离开或抵达、收束动作已执行，也不得提前跨越正式换幕后的时间或地点。"
        )
    return NUMERIC_V2_ACTOR_JSON_INSTRUCTION + shape
logger = logging.getLogger(__name__)

_RELATIONSHIP_STAGES = ("stranger", "guarded", "cooperative", "trusted", "intimate")
_RELATIONSHIP_STAGE_LABELS = {
    "陌生": "stranger",
    "戒备": "guarded",
    "合作": "cooperative",
    "信赖": "trusted",
    "亲密": "intimate",
}
_RELATIONSHIP_BEHAVIORS = {
    "stranger": {
        "allowed": ["基本礼貌", "核验身份", "保持明显距离", "只授予可随时撤销的单次许可"],
        "forbidden": ["主动肢体接触", "主动撒娇或依赖", "使用亲昵称呼", "作出关系承诺", "交出核心权限或无条件服从"],
    },
    "guarded": {
        "allowed": ["有限软化", "说明边界", "在安全距离内回应", "授予用途和范围明确的临时许可"],
        "forbidden": ["主动肢体接触", "主动撒娇或依赖", "暧昧试探", "伴侣式称呼或承诺", "无限授权或放弃自主判断"],
    },
    "cooperative": {
        "allowed": ["主动协作", "表达普通关心", "分享与当前任务有关的信息", "授予当前任务所需的有限权限"],
        "forbidden": ["恋人式肢体接触", "主动撒娇或依赖", "占有式表达", "确认爱意或永久绑定", "永久授权或把个人安全完全托付"],
    },
    "trusted": {
        "allowed": ["主动信任", "表达明确关心", "有限度靠近", "分享敏感信息但保留撤销权"],
        "forbidden": ["未经铺垫的恋人式接触", "强依赖或占有", "直接确认相爱", "永久关系承诺", "放弃人格、自主权或全部控制权限"],
    },
    "intimate": {
        "allowed": ["在已发生事实支撑下表达亲密", "自然使用已建立的亲昵称呼"],
        "forbidden": ["超出已发生事实的关系结论", "替玩家作出亲密选择或承诺"],
    },
}
_RELATIONSHIP_RESPONSE_CONTRACTS = {
    "stranger": (
        "把玩家视为尚待核验的陌生人；保持距离和自主判断，不主动建立亲密、依赖或无条件信任。"
    ),
    "guarded": (
        "可以礼貌合作，但保留自己的判断和边界；不要把一次配合扩大成亲密、依赖或关系承诺。"
    ),
    "cooperative": (
        "把玩家当作合作对象，可以主动提供信息和普通关心；不要主动发起或索求牵手、搂抱、依靠等恋人式接触，"
        "也不要把协作写成恋人关系或永久承诺。"
    ),
    "trusted": (
        "可以表现已由剧情建立的信任和关心，但保留自主判断，不越级确认恋爱或永久绑定。"
    ),
    "intimate": (
        "可以自然表达已由剧情建立的亲密，但不能替玩家作出选择或承诺永久关系。"
    ),
}


def _band_projection(
    engine: NumericV2Engine,
    metrics: Mapping[str, int],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for metric_id, definition in engine.metric_schema.items():
        label = ""
        stage = "only"
        bands = list(definition.get("bands") or [])
        for band_index, band in enumerate(bands):
            if int(band["min"]) <= int(metrics[metric_id]) <= int(band["max"]):
                label = str(band["label"])
                if len(bands) > 1:
                    if band_index == 0:
                        stage = "lowest"
                    elif band_index == len(bands) - 1:
                        stage = "highest"
                    else:
                        stage = "middle"
                break
        # 只投影区间名称和相对阶段，帮助 Actor 控制关系进度，同时继续隐藏真实数值与阈值。
        result[metric_id] = {"label": label, "stage": stage}
    return result


def _relationship_metric_stage(
    definition: Mapping[str, Any],
    value: int,
) -> str:
    """把作者声明的关系数值方向投影为统一关系阶段，不依赖数值名称。"""  # noqa: DOCSTRING_CJK

    bands = list(definition.get("bands") or [])
    band_index = 0
    for index, band in enumerate(bands):
        if int(band["min"]) <= int(value) <= int(band["max"]):
            band_index = index
            break
    if len(bands) <= 1:
        return "cooperative"
    closeness = band_index / (len(bands) - 1)
    if definition.get("relationship_effect") == "negative":
        closeness = 1 - closeness
    stage_index = min(4, 1 + int(closeness * 3))
    return _RELATIONSHIP_STAGES[stage_index]


def _relationship_control(
    engine: NumericV2Engine,
    node: Mapping[str, Any],
    metrics: Mapping[str, int],
) -> dict[str, Any]:
    """合并数值阶段和当前幕上限，向 Actor 只暴露可演绎的关系边界。"""  # noqa: DOCSTRING_CJK

    beat = node.get("story_beat", {})
    structured_ceiling = str(beat.get("relationship_ceiling") or "")
    if structured_ceiling in _RELATIONSHIP_STAGES:
        # 新包直接服从结构化上限。
        scene_ceiling = structured_ceiling
    else:
        scene_text = str(beat.get("catgirl_situation") or "")
        normalized_scene_text = scene_text.replace("：", ":")
        marker_index = normalized_scene_text.find("关系上限:")
        declared_label = (
            normalized_scene_text[marker_index + len("关系上限:"):].strip()
            if marker_index >= 0
            else ""
        )
        scene_ceiling = next(
            (
                stage
                for label, stage in _RELATIONSHIP_STAGE_LABELS.items()
                if declared_label.startswith(label)
            ),
            "intimate",
        )
    metric_states: dict[str, dict[str, str]] = {}
    metric_ceiling = "intimate"
    projections = _band_projection(engine, metrics)
    for metric_id, definition in engine.metric_schema.items():
        effect = str(definition.get("relationship_effect") or "none")
        if effect not in {"positive", "negative"}:
            continue
        stage = _relationship_metric_stage(definition, int(metrics[metric_id]))
        projection = projections[metric_id]
        metric_states[metric_id] = {
            "effect": effect,
            "label": projection["label"],
            "stage": stage,
        }
        if _RELATIONSHIP_STAGES.index(stage) < _RELATIONSHIP_STAGES.index(metric_ceiling):
            metric_ceiling = stage
    effective_stage = min(
        (metric_ceiling, scene_ceiling),
        key=_RELATIONSHIP_STAGES.index,
    )
    behaviors = _RELATIONSHIP_BEHAVIORS[effective_stage]
    return {
        "metric_ceiling": metric_ceiling,
        "scene_ceiling": scene_ceiling,
        "effective_stage": effective_stage,
        "metric_states": metric_states,
        "allowed_behaviors": list(behaviors["allowed"]),
        "forbidden_behaviors": list(behaviors["forbidden"]),
        "response_contract": _RELATIONSHIP_RESPONSE_CONTRACTS[effective_stage],
        "rule": "effective_stage 是实际关系硬上限，禁止行为不得由角色卡风格、剧情身份或推荐输入绕过。",
    }


def _relationship_contract_for_actor(control: Mapping[str, Any]) -> dict[str, Any]:
    """只投影 Actor 真正需要的关系结论，避免再发送已被 response_contract 涵盖的数值标签和行为清单。"""  # noqa: DOCSTRING_CJK

    return {
        "effective_stage": str(control.get("effective_stage") or ""),
        "response_contract": str(control.get("response_contract") or ""),
    }


def _story_context_for_actor(
    cast: NumericV2CastProjection,
    story: Mapping[str, Any],
    *,
    beats: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """按认知合同投影稳定前提，未知阶段不发送可被模型偷看的后台事实。"""  # noqa: DOCSTRING_CJK

    contracts = tuple(_acting_contract_for_actor(cast, beat) for beat in beats)
    if any(_acting_contract_restricts_knowledge(contract) for contract in contracts):
        return {"knowledge_scope": _RESTRICTED_KNOWLEDGE_SCOPE}

    intro = cast.intro(story)
    return {
        "background": str(intro.get("background") or ""),
        "player_identity": str(intro.get("player_identity") or ""),
    }


def _project_player_address(player_address: str, *, known: bool) -> str:
    """称呼未知时只向 Actor 投影第二人称，不发送配置中的真实昵称。"""  # noqa: DOCSTRING_CJK

    if known:
        return str(player_address or "你").strip() or "你"
    return "你"


def _assert_no_unknown_player_address_leak(
    performance: Mapping[str, Any],
    *,
    player_address: str,
    player_address_known: bool,
    player_input: str = "",
) -> None:
    """未知阶段只允许模型复述玩家本轮精确披露的完整昵称。"""  # noqa: DOCSTRING_CJK

    configured_address = str(player_address or "").strip()
    if (
        player_address_known
        or not configured_address
        or configured_address in {"你", "男主"}
        or configured_address in str(player_input or "")
    ):
        return
    if configured_address in json.dumps(performance, ensure_ascii=False, separators=(",", ":")):
        raise NumericV2ActorOutputError("numeric_v2_actor_player_address_leak")


def _acting_context(
    engine: NumericV2Engine,
    cast: NumericV2CastProjection,
    node: Mapping[str, Any],
    metrics: Mapping[str, int],
    character_profile: str,
    *,
    relationship_metrics: Mapping[str, int] | None = None,
    target: Mapping[str, Any] | None = None,
    dialogue_policy: str = "required",
    target_dialogue_policy: str = "required",
) -> dict[str, Any]:
    # 剧情身份先提供局势，核心人格随后决定表达方式；关系合同最后收口可演行为，
    # 避免角色卡中的粘人、撒娇等关系依赖特质在低关系阶段被直接照演。
    # 本轮新产生的关系变化从下一轮开始影响演绎，避免一次行为跨 band 后
    # 在同一句回复里突然从戒备跳到亲密；能力类数值仍使用结算后的状态。
    relationship_control = _relationship_control(
        engine,
        node,
        relationship_metrics if relationship_metrics is not None else metrics,
    )
    capability_state = {
        metric_id: projection
        for metric_id, projection in _band_projection(engine, metrics).items()
        if engine.metric_schema[metric_id].get("relationship_effect", "none") == "none"
    }
    current_contract = _acting_contract_for_actor(cast, node["story_beat"])
    current_character_state = _character_state_for_actor(cast, node["story_beat"])
    target_contract = (
        _acting_contract_for_actor(cast, target["story_beat"])
        if target is not None
        else {}
    )
    target_character_state = (
        _character_state_for_actor(cast, target["story_beat"])
        if target is not None
        else {}
    )
    knowledge_restricted = any(
        _acting_contract_restricts_knowledge(contract)
        for contract in (current_contract, target_contract)
    )
    profile_contract = {
        "persona_scope": (
            "style_only"
            if any(
                contract.get("persona_scope") == "style_only"
                for contract in (current_contract, target_contract)
            )
            else ""
        ),
        "self_reference_mode": (
            "system_neutral"
            if any(
                contract.get("self_reference_mode") == "system_neutral"
                for contract in (current_contract, target_contract)
            )
            else ""
        ),
    }
    visible_persona = _profile_for_acting_contract(character_profile, profile_contract)
    context: dict[str, Any] = {}
    if not knowledge_restricted:
        context.update({
            "story_identity": cast.text(engine.story["intro"]["catgirl_identity"]),
            "story_role_context": str(cast.value(engine.story["catgirl_binding"]["role_overlay"]) or ""),
            "current_scene_state": cast.text(node["story_beat"].get("catgirl_situation")),
        })
    # 同一次换场调用会同时生成来源回应和目标开场；只要任一侧失忆，就隐藏共享后台事实，
    # 避免来源幕已知信息越过 target_acting_contract 泄漏。知识范围已在 story_context 中发送一次。
    if target is None:
        context["capability_state"] = capability_state
    else:
        target_control = _relationship_control(
            engine,
            target,
            relationship_metrics if relationship_metrics is not None else metrics,
        )
    context["core_persona"] = visible_persona
    context["dialogue_policy"] = dialogue_policy
    if current_character_state:
        context["character_state"] = current_character_state
    if current_contract:
        context["acting_contract"] = {
            key: value
            for key, value in current_contract.items()
            # 完整剧情方向已经说明本幕如何发展；每轮重复长 allowed 列表会诱导模型逐项复述。
            # 禁演、认知和身份边界仍必须逐轮保留。
            if key != "allowed_behaviors" and value not in ("", [], {})
        }
    if target is None:
        # 静态人格与关系说明已在 system prompt 中声明；人类消息只保留本轮动态合同，
        # 避免每回合重复占用固定预算，同时维持 core_persona → acting_contract → relationship_control 的字段顺序。
        context["relationship_control"] = _relationship_contract_for_actor(relationship_control)
    else:
        context["relationship_control"] = _relationship_contract_for_actor(relationship_control)
        if target_contract:
            context["target_acting_contract"] = {
                key: value
                for key, value in target_contract.items()
                if key != "allowed_behaviors" and value not in ("", [], {})
            }
        if target_character_state:
            context["target_character_state"] = target_character_state
        context["target_dialogue_policy"] = target_dialogue_policy
        context["target_relationship_control"] = _relationship_contract_for_actor(target_control)
    return context


def _role_prompt_text(
    *,
    catgirl_name: str,
    acting_context: Mapping[str, Any],
) -> str:
    """区分作者入幕状态与持续边界，压成一段自然角色说明。"""  # noqa: DOCSTRING_CJK

    lines = [f"你是：{catgirl_name}。"]
    story_identity = str(acting_context.get("story_identity") or "").strip()
    if story_identity:
        lines.append(f"剧本身份：{story_identity}")
    story_role_context = str(acting_context.get("story_role_context") or "").strip()
    if story_role_context:
        lines.append(f"剧本中的职责与背景：{story_role_context}")
    # 这些字段记录作者开场演完后的起点，每轮仍来自同一节点；动态状态以实际演出历史为准。
    entry_state_lines: list[str] = []
    current_scene_state = str(acting_context.get("current_scene_state") or "").strip()
    if current_scene_state:
        entry_state_lines.append(f"开场演完后的处境：{current_scene_state}")

    character_state = acting_context.get("character_state")
    if isinstance(character_state, Mapping):
        catgirl_state = str(character_state.get("catgirl_state") or "").strip()
        if catgirl_state:
            entry_state_lines.append(f"开场演完后的身体与认知状态：{catgirl_state}")
        player_state = str(character_state.get("player_state") or "").strip()
        if player_state:
            entry_state_lines.append(f"玩家开场演完后的已知处境：{player_state}")
        environment_state = str(character_state.get("environment_state") or "").strip()
        if environment_state:
            entry_state_lines.append(f"开场演完后的环境状态：{environment_state}")
    if entry_state_lines:
        lines.extend(entry_state_lines)
        lines.append(
            "开场演完后的状态是起点；动态变化承接 story_so_far 已提交事实，不能复位；"
            "作者身份、能力、认知/记忆限制与硬边界持续有效。"
        )

    core_persona = str(acting_context.get("core_persona") or "").strip()
    if core_persona:
        lines.append(f"性格与说话方式：{core_persona}")
    acting_contract = acting_context.get("acting_contract")
    if isinstance(acting_contract, Mapping):
        assertable_facts = [
            str(item).strip()
            for item in acting_contract.get("assertable_self_facts") or []
            if str(item).strip()
        ]
        if assertable_facts:
            lines.append("当前可以自然确认的自身事实：" + "；".join(assertable_facts))
        cognition_state = str(acting_contract.get("cognition_state") or "").strip()
        memory_state = str(acting_contract.get("memory_state") or "").strip()
        if cognition_state or memory_state:
            state_parts = [part for part in (cognition_state, memory_state) if part]
            lines.append("认知与记忆限制：" + "、".join(state_parts))

    relationship_control = acting_context.get("relationship_control")
    if isinstance(relationship_control, Mapping):
        response_contract = str(
            relationship_control.get("response_contract") or ""
        ).strip()
        if response_contract:
            lines.append(f"与玩家的当前关系：{response_contract}")
    dialogue_policy = str(acting_context.get("dialogue_policy") or "").strip()
    if dialogue_policy == "forbidden":
        lines.append("本轮只能用动作表达，不能说出对白。")
    elif dialogue_policy == "optional":
        lines.append("本轮可以自然选择动作、对白或两者。")
    return "\n".join(lines)


def _blocks_to_performance(blocks: list[dict[str, str]]) -> str:
    """把旧内容块投影成新 Prompt 使用的混合演绎正文。"""  # noqa: DOCSTRING_CJK

    parts: list[str] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        # 旧 Session 的 ordinary narration 原本就是括号微动作；新记录已明确标成 action。
        parts.append(f"（{text}）" if block.get("type") in {"action", "narration"} else text)
    return "".join(parts)


def _prompt_container(container: Mapping[str, Any], *, phase: str) -> dict[str, str]:
    """把新旧 Session 都投影为场景旁白加混合正文，避免 Prompt 保留块协议。"""  # noqa: DOCSTRING_CJK

    if "scene_narration" in container or "performance" in container:
        result = {}
        scene_narration = str(container.get("scene_narration") or "").strip()
        performance = str(container.get("performance") or "").strip()
        if scene_narration:
            result["scene_narration"] = scene_narration
        if performance:
            result["performance"] = performance
        for position in ("before", "after"):
            texts = [item["text"] for item in container.get("fixed_narrations", []) if item["position"] == position]
            if texts:
                result[f"fixed_narration_{position}"] = "\n\n".join(texts)
        return result

    blocks = content_blocks(container)
    if phase in {"opening", "transition_bridge", "target_opening"}:
        scene_narration = "".join(
            block["text"] for block in blocks if block["type"] == "narration"
        )
        dialogue = "".join(
            block["text"] for block in blocks if block["type"] == "dialogue"
        )
        result = {}
        if scene_narration:
            result["scene_narration"] = scene_narration
        if dialogue:
            result["performance"] = dialogue
        return result
    performance = _blocks_to_performance(blocks)
    return {"performance": performance} if performance else {}


def _json_tokens(value: Any) -> int:
    return count_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _history_row(record: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "phase": "turn",
        "revision": record.get("revision"),
        "player_input": str(record.get("input_text") or "").strip(),
    }
    if isinstance(record.get("segments"), list):
        row["segments"] = [
            {
                "phase": str(segment.get("phase") or ""),
                **_prompt_container(
                    segment,
                    phase=str(segment.get("phase") or ""),
                ),
            }
            for segment in record["segments"]
            if isinstance(segment, Mapping)
        ][:3]
    else:
        row.update(_prompt_container(record, phase="ordinary"))
    return row


def _source_response_tail(segment: Mapping[str, Any]) -> str:
    """从已提交的来源幕回应末尾提取少量真实可见内容。"""  # noqa: DOCSTRING_CJK

    visible_blocks = [
        block
        for block in content_blocks(segment)
        if block.get("type") in {"action", "narration", "dialogue"}
        and str(block.get("text") or "").strip()
    ][-NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_BLOCKS:]
    if not visible_blocks:
        return ""

    selected: list[dict[str, str]] = []
    remaining_tokens = NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_TOKENS
    for block in reversed(visible_blocks):
        block_type = str(block.get("type") or "")
        text = str(block.get("text") or "").strip()
        wrapper_tokens = count_tokens("（）") if block_type in {"action", "narration"} else 0
        text_budget = max(0, remaining_tokens - wrapper_tokens)
        if text_budget <= 0:
            continue
        if count_tokens(text) > text_budget:
            # 对白保留结尾，动作保留开头；这样既承接最后一句语气，又不会留下半个动作结果。
            text = (
                truncate_head_tail_tokens(text, 0, text_budget, separator="")
                if block_type == "dialogue"
                else truncate_to_tokens(text, text_budget)
            ).strip()
        if not text:
            continue
        projected = {"type": block_type, "text": text}
        rendered = _blocks_to_performance([projected])
        rendered_tokens = count_tokens(rendered)
        if rendered_tokens > remaining_tokens:
            continue
        selected.insert(0, projected)
        remaining_tokens -= rendered_tokens
    return _blocks_to_performance(selected)


def _current_scene_history_row(
    record: Mapping[str, Any],
    *,
    current_node_id: str,
    include_previous_scene_tail: bool = False,
) -> dict[str, Any]:
    """换场记录保留桥接和目标开场，首回合另投影少量旧幕可见余波。"""  # noqa: DOCSTRING_CJK

    row = _history_row(record)
    from_node_id = str(record.get("from_node_id") or "")
    to_node_id = str(record.get("to_node_id") or "")
    if (
        from_node_id == current_node_id
        or to_node_id != current_node_id
        or not isinstance(row.get("segments"), list)
    ):
        return row
    projected_segments: list[dict[str, Any]] = []
    for segment in row["segments"]:
        if segment.get("phase") != "source_response":
            projected_segments.append(segment)
            continue
        if include_previous_scene_tail:
            tail = _source_response_tail(segment)
            if tail:
                # 合成内容只存在于本次 Prompt，不写回 Session，也不携带旧玩家输入或完整旧幕回应。
                projected_segments.append({
                    "phase": "previous_scene_tail",
                    "performance": tail,
                })
    return {
        **row,
        # 玩家输入属于上一幕；当前幕只承接短尾声、换场事实与目标开场。
        "player_input": "",
        "segments": projected_segments,
    }


def _history(
    session: ScriptSessionV2,
    *,
    max_tokens: int,
    max_turns: int | None = None,
    include_previous_scene_tail: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """只保留当前访问的连续完整后缀，预算不足时整轮舍弃较早记录。"""  # noqa: DOCSTRING_CJK

    budget = max(0, int(max_tokens))
    opening = {
        "phase": "opening",
        "revision": 0,
        "player_input": "",
        **_prompt_container(session.opening_performance, phase="opening"),
    }
    current_node_id = str(session.current_node_id)
    # Actor 与 Evaluator 共用同一套当前节点回溯规则，避免两个模型看到不同的场景边界。
    visit_records, entered_current_node = current_scene_records(session)

    rows = ([] if entered_current_node else [opening]) + [
        _current_scene_history_row(
            record,
            current_node_id=current_node_id,
            include_previous_scene_tail=include_previous_scene_tail,
        )
        for record in reversed(visit_records)
    ]
    if not rows:
        rows = [opening]
    available_revisions = [
        row.get("revision")
        for row in rows
        if isinstance(row.get("revision"), int)
    ]
    if max_turns is not None:
        # 档位只从最早的完整记录开始裁剪；不会切断半个回合或破坏换场 segments。
        rows = rows[-max(1, int(max_turns)):]
    selected: list[dict[str, Any]] = []
    for row in reversed(rows):
        candidate = [row, *selected]
        if _json_tokens(candidate) <= budget:
            selected = candidate
            continue
        # 一旦某个较早回合放不下，更早记录也不再回填，避免留下时间断层。
        break
    if not selected and rows:
        # 最新回合是当前回应的直接前提；它不能被静默抛弃，最终总预算检查会明确报错。
        selected = [rows[-1]]
    if diagnostics is not None:
        included_revisions = [
            row.get("revision")
            for row in selected
            if isinstance(row.get("revision"), int)
        ]
        diagnostics.clear()
        diagnostics.update({
            "history_available_revisions": available_revisions,
            "history_preselected_revisions": included_revisions,
            "history_preselection_dropped_revisions": [
                revision
                for revision in available_revisions
                if revision not in included_revisions
            ],
        })
    return selected


def _story_so_far_row_text(row: Mapping[str, Any]) -> str:
    """把一条真实历史记录渲染为玩家和猫娘都能读懂的叙事文本。"""  # noqa: DOCSTRING_CJK

    parts: list[str] = []
    player_input = str(row.get("player_input") or "").strip()
    if player_input:
        parts.append(f"玩家：{player_input}")
    segments = row.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            scene_narration = str(segment.get("scene_narration") or "").strip()
            performance = str(segment.get("performance") or "").strip()
            if segment.get("phase") == "previous_scene_tail" and performance:
                parts.append(
                    "上一幕尾声（只承接猫娘当时可见的情绪和姿态，不作为当前任务）："
                    f"{performance}"
                )
                continue
            if scene_narration:
                parts.append(f"场景：{scene_narration}")
            if segment.get("fixed_narration_before"):
                parts.append("已展示的作者原文：" + segment["fixed_narration_before"])
            if performance:
                parts.append(f"猫娘：{performance}")
            if segment.get("fixed_narration_after"):
                parts.append("已展示的作者原文：" + segment["fixed_narration_after"])
    else:
        scene_narration = str(row.get("scene_narration") or "").strip()
        performance = str(row.get("performance") or "").strip()
        if scene_narration:
            parts.append(f"场景：{scene_narration}")
        if row.get("fixed_narration_before"):
            parts.append("已展示的作者原文：" + row["fixed_narration_before"])
        if performance:
            parts.append(f"猫娘：{performance}")
        if row.get("fixed_narration_after"):
            parts.append("已展示的作者原文：" + row["fixed_narration_after"])
    return "\n".join(parts)


def _story_so_far_text(history_rows: Sequence[Mapping[str, Any]]) -> str:
    """把当前幕已经提交的完整历史渲染成 Actor 可读文本。"""  # noqa: DOCSTRING_CJK

    return "\n\n".join(
        rendered
        for row in history_rows
        if (rendered := _story_so_far_row_text(row))
    )


def _current_scene_fact_index_text(
    session: ScriptSessionV2,
    *,
    max_tokens: int = 900,
) -> str:
    """把全幕真实可见记录压成连续性索引，长幕后仍保留早期已完成事实。"""  # noqa: DOCSTRING_CJK

    visit_records, _ = current_scene_records(session)
    chronological_records = list(reversed(visit_records))
    if not chronological_records:
        return ""
    per_record_tokens = max(16, min(60, max_tokens // len(chronological_records)))
    lines = []
    for record in chronological_records:
        # 入幕记录只保留当前幕桥段和开场；来源幕输入与回应仍遵守一次性短尾声规则。
        projected = _current_scene_history_row(
            record,
            current_node_id=str(session.current_node_id),
            include_previous_scene_tail=False,
        )
        rendered = _story_so_far_row_text(projected).replace("\n", "；")
        compact = truncate_to_tokens(rendered, per_record_tokens).strip()
        if compact:
            lines.append(compact)
    index = "\n".join(lines)
    return truncate_to_tokens(index, max_tokens).strip()




def _player_input_repeats_recent_context(
    player_input: str,
    recent_context: list[Mapping[str, Any]],
) -> bool:
    """识别玩家再次发送近期原话，让 Actor 承接最新状态而不是倒回旧回合。"""  # noqa: DOCSTRING_CJK

    normalized = "".join(str(player_input or "").split())
    if not normalized:
        return False
    return any(
        normalized == "".join(str(row.get("player_input") or "").split())
        for row in recent_context
    )


def _performance_text(performance: Mapping[str, Any]) -> str:
    return "".join(
        "".join(str(block.get("text") or "").split())
        for block in performance_content_blocks(performance)
    )


def _is_repeated_performance(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> bool:
    """拦截对白照搬且整体近似的上一轮复述，不把常规口头禅误判为整轮重复。"""  # noqa: DOCSTRING_CJK

    current_blocks = performance_content_blocks(performance)
    previous_blocks = performance_content_blocks(previous)
    current_dialogue = [block["text"] for block in current_blocks if block["type"] == "dialogue"]
    previous_dialogue = {
        block["text"]
        for block in previous_blocks
            if block["type"] == "dialogue"
    }
    if not current_dialogue:
        return False
    dialogue_text = "".join("".join(text.split()) for text in current_dialogue)
    if (
        all(text in previous_dialogue for text in current_dialogue)
        and (len(current_dialogue) >= 2 or len(dialogue_text) >= 12)
    ):
        # 动作可以随输入变化，但整组有信息量的对白不能原样复用来伪装成新回应。
        return True
    previous_dialogue_text = "".join(
        "".join(str(block.get("text") or "").split())
        for block in previous_blocks
        if block.get("type") == "dialogue"
    )
    # 不做宽泛语义相似度猜测，只拦本轮主要篇幅逐字复用上一轮长句段的情况。
    # 这能识别“换一个开头 + 原样重复同一威胁/提醒”的机械复读，同时允许短口头禅继续维持人格。
    normalized_current = "".join(
        char for char in dialogue_text
        if char not in "，。！？、；：,.!?;:…—-（）()“”‘’\"'"
    )
    normalized_previous = "".join(
        char for char in previous_dialogue_text
        if char not in "，。！？、；：,.!?;:…—-（）()“”‘’\"'"
    )
    if normalized_current and normalized_previous:
        longest = difflib.SequenceMatcher(
            None,
            normalized_current,
            normalized_previous,
            autojunk=False,
        ).find_longest_match()
        if longest.size >= 16 and longest.size / len(normalized_current) >= 0.4:
            return True
    # 完整对白逐字相同仍作为最后一道确定性兜底。
    return (
        len(dialogue_text) >= 12
        and dialogue_text == previous_dialogue_text
    )


def _performance_variants(performance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把历史换场拆成可比较的两侧表演，避免桥段文本稀释复读率。"""  # noqa: DOCSTRING_CJK

    variants: list[dict[str, Any]] = []
    raw_performance = performance.get("performance")
    if isinstance(raw_performance, str) and raw_performance.strip():
        variants.append({"performance": raw_performance})
    segments = performance.get("segments")
    if isinstance(segments, list):
        variants.extend(
            {"performance": str(segment["performance"])}
            for segment in segments
            if isinstance(segment, Mapping)
            and isinstance(segment.get("performance"), str)
            and str(segment.get("performance") or "").strip()
        )
    if not variants and performance_content_blocks(performance):
        variants.append(dict(performance))
    return variants


def _is_high_confidence_repeated_performance(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> bool:
    """全 Session 只拦截高度近似正文，避免常用短句造成误判。"""  # noqa: DOCSTRING_CJK

    current_text = _performance_text(performance)
    previous_text = _performance_text(previous)
    if len(current_text) >= 12 and current_text == previous_text:
        return True
    current_dialogue = "".join(
        "".join(str(block.get("text") or "").split())
        for block in performance_content_blocks(performance)
        if block.get("type") == "dialogue"
    )
    previous_dialogue = "".join(
        "".join(str(block.get("text") or "").split())
        for block in performance_content_blocks(previous)
        if block.get("type") == "dialogue"
    )
    return (
        len(current_dialogue) >= 12
        and len(previous_dialogue) >= 12
        and current_dialogue == previous_dialogue
    )


def _repeats_earlier_session_performance(
    performance: Mapping[str, Any],
    session: ScriptSessionV2,
    *,
    route_changed: bool,
) -> bool:
    """检查开场和更早回合；最近一回合仍由原有低阈值规则负责。"""  # noqa: DOCSTRING_CJK

    if not session.performance_history:
        return False
    current_variants = _performance_variants(performance)
    if route_changed and current_variants:
        current_variants = current_variants[:1]
    earlier = [session.opening_performance, *session.performance_history[:-1]]
    return any(
        _is_high_confidence_repeated_performance(current, previous)
        for current in current_variants
        for record in earlier
        for previous in _performance_variants(record)
    )


def _transition_source_repeats_previous(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> bool:
    """只比较换场来源回应，避免新场景文本掩盖上一轮对白复读。"""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if not isinstance(segments, list) or not segments or not isinstance(segments[0], Mapping):
        return False
    source_response = segments[0]
    if not isinstance(source_response.get("performance"), str):
        return False
    return _is_repeated_performance(
        {"performance": source_response["performance"]},
        previous,
    )


def _soft_pacing(
    node: Mapping[str, Any],
    current_turn: int,
    *,
    route_changed: bool,
    transition_intent: str = "unclear",
) -> dict[str, Any]:
    # min_turns 只作为作者节奏提示，不参与收束或换幕判断。
    min_turns = int(node.get("min_turns") or 1)
    raw_budget = node.get("recommended_turns")
    recommended_turns = (
        int(raw_budget)
        if isinstance(raw_budget, int) and not isinstance(raw_budget, bool)
        else 4
    )
    turns_remaining = max(recommended_turns - current_turn, 0)
    if route_changed:
        phase = "transition"
        instruction = "路线已确定：回应并收住当前幕，完成作者桥段，再建立下一幕开场。"
    elif transition_intent == "reject":
        # 被拒后不回到旧提议，也不因 scene_complete 反复催促；只回应玩家当前选择。
        phase = "after_reject"
        instruction = "玩家拒绝或暂停了上一提议：回应当前意图并留在本幕，不再重复催促。"
    elif current_turn > recommended_turns:
        phase = "overdue"
        instruction = (
            "当前幕已超过推荐展开长度：先回应玩家，再推动当前核心因果发生一次可见变化。"
            "结果尚未成立时交付结果或真实选择；结果已经成立且自然出口成熟时，"
            "提出基于已发生事实的具体未来行动。不得新增支线或补齐可选内容来延长。"
        )
    elif turns_remaining == 0:
        phase = "closure"
        instruction = (
            "已到推荐回合：这只是软节奏参考，不是必须提议或换幕的倒计时。回应玩家并开始自然收束当前话题；"
            "已有自然出口时可以提出具体下一步，否则继续交付当前行动的可见结果。尚未出现的内容建议和道具可以直接舍弃，"
            "不要为了补齐它们延后转场，也不要新开任务链。"
        )
    elif turns_remaining == 1:
        phase = "focus"
        instruction = (
            "距离推荐回合还剩一轮：开始收束当前话题，利用当前已发生事实自然铺垫下一步方向；"
            "内容建议只在顺手时使用，不合适就舍弃。"
        )
    elif turns_remaining == 2:
        phase = "guided"
        instruction = (
            "已靠近推荐回合：在回应玩家的基础上开始朝下一步方向聚焦；"
            "可顺手采用内容建议，也可跳过，不要逐项补剧情或提前演出下一幕。"
        )
    else:
        phase = "normal"
        instruction = "距离推荐回合尚远：专注回应玩家并自然展开当前互动；内容建议只提供灵感，不必按顺序执行。"
    return {
        "minimum_turns": min_turns,
        "recommended_turns": recommended_turns,
        "current_turn": current_turn,
        "turns_until_minimum": max(min_turns - current_turn, 0),
        "turns_remaining": turns_remaining,
        "overdue_by": max(current_turn - recommended_turns, 0),
        "phase": phase,
        "instruction": instruction,
    }


def _beat_for_actor(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
    *,
    include_opening_only_boundaries: bool = False,
) -> dict[str, Any]:
    """v2.2 只发送开场画面、完整自然方向和硬边界；目标与证据不进入 Actor。"""  # noqa: DOCSTRING_CJK

    projected = cast.value(beat)
    return {
        "opening_scene": scene_opening_text(projected),
        # 当前重心是自然创作提示，不是需要逐项完成的目标，也不参与 Runtime 判定。
        "narrative_focus": scene_narrative_focus(projected),
        # 剧情方向是导演信息而不是角色知识；受限认知只限制正文可声称的事实，
        # 不能让正式换场后的 Actor 不知道本幕事件顺序而在开场推荐里提前泄露后续内容。
        "scene_direction": str(
            projected.get("narrative_summary")
            or projected.get("summary")
            or scene_narrative_focus(projected)
            or ""
        ),
        # 换场 Actor 必须继续看到来源幕与目标幕硬边界；此前消费者读取了该字段，
        # 但投影从未提供，导致目标开场推荐可能提前泄露本幕事件。
        "boundaries": _suggestion_hard_boundaries(
            cast,
            beat,
            include_opening_only=include_opening_only_boundaries,
        ),
    }




def _acting_contract_for_actor(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
) -> dict[str, Any]:
    """只投影作者明确声明的认知与表达权限，不从自然语言猜测开机状态。"""  # noqa: DOCSTRING_CJK

    raw = beat.get("acting_contract")
    if not isinstance(raw, Mapping):
        return {}
    projected = cast.value(raw)
    return {
        "cognition_state": str(projected.get("cognition_state") or ""),
        "memory_state": str(projected.get("memory_state") or ""),
        "self_reference_mode": str(projected.get("self_reference_mode") or ""),
        "persona_scope": str(projected.get("persona_scope") or ""),
        "dialogue_policy": str(projected.get("dialogue_policy") or ""),
        "assertable_self_facts": [
            str(item)
            for item in projected.get("assertable_self_facts") or []
        ],
        "allowed_behaviors": [str(item) for item in projected.get("allowed_behaviors") or []],
        "forbidden_behaviors": [str(item) for item in projected.get("forbidden_behaviors") or []],
    }


def _character_state_for_actor(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
) -> dict[str, Any]:
    """投影当前节点的作者状态线；未声明时返回空对象。"""  # noqa: DOCSTRING_CJK

    raw = beat.get("character_state")
    if not isinstance(raw, Mapping):
        return {}
    projected = cast.value(raw)
    return {
        "catgirl_state": str(projected.get("catgirl_state") or ""),
        "player_state": str(projected.get("player_state") or ""),
        "environment_state": str(projected.get("environment_state") or ""),
        "continuity_from_previous": [
            str(item) for item in projected.get("continuity_from_previous") or []
        ],
        "scene_boundaries": [
            str(item) for item in projected.get("scene_boundaries") or []
        ],
    }


def _acting_contract_restricts_knowledge(contract: Mapping[str, Any]) -> bool:
    """认知或记忆并非完整可用时，不把作者后台设定当作角色已知事实。"""  # noqa: DOCSTRING_CJK

    if not contract:
        return False
    return (
        contract.get("cognition_state") != "normal"
        or contract.get("memory_state") != "available"
    )


def _chapter_title_for_actor(
    cast: NumericV2CastProjection,
    node: Mapping[str, Any],
) -> str:
    """受限认知节点不发送作者章节标题，避免标题中的地点或事件被当成角色已知事实。"""  # noqa: DOCSTRING_CJK

    contract = _acting_contract_for_actor(cast, node.get("story_beat") or {})
    if _acting_contract_restricts_knowledge(contract):
        return ""
    return cast.text(node.get("chapter"))


def _profile_field(line: str) -> tuple[str, str]:
    """读取角色卡显式的“字段: 值”，不分析自由文本句意。"""  # noqa: DOCSTRING_CJK

    stripped = str(line or "").strip()
    separators = [
        index
        for separator in (":", "：")
        if (index := stripped.find(separator)) >= 0
    ]
    if not separators:
        return "", ""
    separator_index = min(separators)
    field_name = "".join(
        character
        for character in stripped[:separator_index]
        if not character.isspace() and character not in "*`\\"
    ).casefold()
    return field_name, stripped[separator_index + 1:].strip()


def _profile_self_reference_tokens(character_profile: str) -> tuple[str, ...]:
    """只读取人格事实中显式标注的自称，不从自由文本推断关系或情绪。"""  # noqa: DOCSTRING_CJK

    tokens: list[str] = []
    for line in str(character_profile or "").splitlines():
        field_name, raw_value = _profile_field(line)
        if field_name != "自称" or not raw_value:
            continue
        cut_indexes = [
            index
            for index, character in enumerate(raw_value)
            if character in "，,。！？；;（(、/|"
        ]
        alternative_index = raw_value.find(" 或 ")
        if alternative_index >= 0:
            cut_indexes.append(alternative_index)
        value = raw_value[:min(cut_indexes)].strip() if cut_indexes else raw_value
        if value and value not in {"我", "本人", "系统"} and value not in tokens:
            tokens.append(value)
    return tuple(tokens)


def _profile_for_acting_contract(
    character_profile: str,
    acting_contract: Mapping[str, Any],
) -> str:
    """按结构化合同投影人格，不从自由文本猜测关系语义。"""  # noqa: DOCSTRING_CJK

    lines = [line for line in str(character_profile or "").splitlines() if line.strip()]
    parsed = [(_profile_field(line)[0], line) for line in lines]
    has_structured_fields = any(field_name for field_name, _ in parsed)
    selected: list[str] = []
    for field_name, line in parsed:
        if not (
            field_name in _STYLE_ONLY_PERSONA_FIELDS
            or (not has_structured_fields and not field_name)
        ):
            continue
        if (
            acting_contract.get("self_reference_mode") == "system_neutral"
            and field_name == "自称"
        ):
            continue
        if field_name in _PERSONA_TONE_FIELDS:
            # “核心特质”常把温柔语气与粘人行为写在同一字段；不分析其中词义，
            # 统一把整个结构化字段降为措辞氛围，关系行为仍只服从 Runtime 投影。
            raw_value = _profile_field(line)[1]
            selected.append(
                "语言氛围参考（只影响措辞，不授权肢体接触、亲昵称呼、依赖或既有关系）："
                + raw_value
            )
        else:
            selected.append(line)
    return "\n".join(selected).strip()


def _assert_acting_contract_output(
    performance: Mapping[str, Any],
    *,
    character_profile: str,
    acting_contract: Mapping[str, Any],
) -> None:
    """只保护合同明确禁止的角色卡自称，禁止自由文本语义正则和输出改写。"""  # noqa: DOCSTRING_CJK

    if acting_contract.get("self_reference_mode") != "system_neutral":
        return
    output = json.dumps(performance, ensure_ascii=False, separators=(",", ":"))
    if any(token in output for token in _profile_self_reference_tokens(character_profile)):
        raise NumericV2ActorOutputError("numeric_v2_actor_acting_contract_violation")


def _deduplicate_scene_update(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """移除紧邻上一回合已经逐句出现的环境旁白，只保留真正的新变化。"""  # noqa: DOCSTRING_CJK

    narration = str(performance.get("scene_narration") or "").strip()
    previous_narration = str(previous.get("scene_narration") or "").strip()
    if not narration or not previous_narration:
        return dict(performance)
    previous_units = {
        "".join(unit.split())
        for unit in _sentence_units(previous_narration)
        if "".join(unit.split())
    }
    current_units = _sentence_units(narration)
    remaining = [
        unit
        for unit in current_units
        if "".join(unit.split()) not in previous_units
    ]
    if len(remaining) == len(current_units):
        return dict(performance)
    sanitized = dict(performance)
    if remaining:
        sanitized["scene_narration"] = "".join(remaining)
    else:
        sanitized.pop("scene_narration", None)
    logger.warning(
        "Numeric v2 Actor removed repeated scene_update sentences: removed_sentences=%s",
        len(current_units) - len(remaining),
    )
    return sanitized


def _opening_beat_for_actor(
    engine: NumericV2Engine,
    cast: NumericV2CastProjection,
    node: Mapping[str, Any],
) -> dict[str, Any]:
    """开场只发送当前画面，不把作者目标投影成待办或交付指令。"""  # noqa: DOCSTRING_CJK

    opening_beat = _beat_for_actor(
        cast,
        node["story_beat"],
        include_opening_only_boundaries=True,
    )
    # 完整本幕方向供玩家首轮输入后的普通 Actor 使用；公开开场只建立 opening_scene。
    # 否则模型容易把方向后半段的身份、能力和危机结果提前压进第一句。
    opening_beat.pop("narrative_focus", None)
    opening_beat.pop("scene_direction", None)
    return opening_beat




def _next_scene_preview_for_actor(
    engine: NumericV2Engine,
    cast: NumericV2CastProjection,
    source: Mapping[str, Any],
    metrics: Mapping[str, int],
) -> dict[str, Any]:
    """投影所选出口的理由、主题与移动范围，不读取目标幕剧情和开场。"""  # noqa: DOCSTRING_CJK

    if not source.get("route_gates"):
        return {"status": "none"}
    # 与同轮 Guard 使用同一只读选路：多出口也能得到当前可行方向，但不冻结邀请。
    # 玩家接受时 Runtime 仍按届时数值重新选路；隐藏数值与未选合同不进入 Actor Prompt。
    route = engine.preview_route(str(source["id"]), metrics)
    if route is None:
        return {"status": "runtime_unresolved"}
    target_id = str(route["target_node_id"])
    target = engine.nodes[target_id]
    contract = route.get("transition_contract") if isinstance(route, Mapping) else None
    transition = dict(contract) if isinstance(contract, Mapping) else {}
    return {
        "status": "after_acceptance_only",
        "chapter_title": _chapter_title_for_actor(cast, target),
        # 结局节点可能仍发生在当前地点；把这一事实作为未来方向的确定性标记，
        # 让 Actor 可以提出“开始记录/继续陪伴”等收束动作，而不是被迫虚构离开地点。
        "target_is_ending": bool(
            target.get("type") == "ending" or target.get("terminal") is True
        ),
        "transition_direction": cast.text(str(transition.get("reason") or "")),
        # 来源理由可能只写“继续逛逛”；桥段才说明实际到哪里。只投影既有移动合同，
        # 不让普通演员通过目标幕的摘要或开场预演尚未发生的互动。结局沿用原收束摘要。
        "entry_movement": cast.text(str(transition.get("bridge_scene_narration") or ""))
        if target.get("type") != "ending" and target.get("terminal") is not True else "",
    }


def _next_scene_summary_text(preview: Mapping[str, Any]) -> str:
    """只把下一幕剧情方向压成一段摘要；路线未决时明确保持未知。"""  # noqa: DOCSTRING_CJK

    status = str(preview.get("status") or "")
    if status == "after_acceptance_only":
        # 普通回合只发送一句方向，避免下一幕完整摘要抢走当前场景注意力或提前泄漏事实。
        direction = str(preview.get("transition_direction") or "").strip()
        chapter_title = str(preview.get("chapter_title") or "").strip()
        chapter_hint = (
            f"接受后进入的下一互动阶段主题是《{chapter_title}》。"
            if chapter_title
            else ""
        )
        if bool(preview.get("target_is_ending")):
            # 目标结局摘要可能含有时间推进、独有地点与最终状态；普通回合只保留来源路线理由，
            # 避免 Actor 为了“贴合结局”提前演出尚未获准的小屋、日常或最终结果。
            # 最后一幕可自然结束，不为结局要求额外邀约；实际结束仍只认 Runtime 授权。
            suffix = f"来源因果方向是：{direction}" if direction else ""
            return (
                "下一阶段是结局余韵。先交付本幕尚未成立的核心结果与必要角色反应；"
                "已经完成时自然收住，不为结束追加邀请、劳动或下次活动。是否正式结束由 Runtime 决定；"
                "当前普通回合不得提前描写结局独有的地点、时间推进、生活状态或最终结果。"
                f"结局主题是《{chapter_title}》。{suffix}"
            )
        entry_movement = str(preview.get("entry_movement") or "").strip()
        entry_hint = (
            f"实际出口的移动范围（尚未发生，只有获准后才执行）：{entry_movement}\n"
            "邀请须说明这个范围中的去向、时段和活动，不能只凭主题或含糊理由另造目的地。"
            "来源理由列出的其他地点若与实际移动不符，不把它们作为本出口可兑现的选择。"
            "仅在已公开路径支持时承接途中经过，不临时编造捷径、停业或玩家同意来解释换错地方。"
            if entry_movement else ""
        )
        if direction:
            return (
                f"{chapter_hint}{entry_hint}接受当前转场提议后，剧情方向是：{direction}\n"
                "这是作者希望的因果衔接方向，不是目标清单或固定动作；可以使用 story_so_far 已自然建立的语义等价方案，"
                # 等价只允许顺应历史改写表达，不能用无关的日常去向替代真实出口。
                "等价方案须保持该方向的时间、地点和下一互动阶段；不能为回避转场而另造去向或当前追加任务。"
                "但不能凭空补出尚未发生的关键事实，也不能用换场桥段代替当前幕必要的因果。"
                # 下一步询问需要可接受的完整安排；保留作者时点，避免把未来互动挪到现在后一直无法换幕。
                # 路线理由常用“取得后”描述作者预期，不能把它误读成已经取得并立即指路。
                "玩家问下一步时，先对照当前幕方向与实际历史：离开所依赖的关键结果尚未成立，"
                "就回应眼前尚在发生的因果，不把结果后的去向说成现在即可出发的安排；"
                "必要结果已经成立，再说明这条方向支持的时间、地点与行动，停在等待其选择的位置。"
                "方向指定未来时点的互动，应邀请届时开始，不能改成现在先讨论或实施该互动；"
                "实际历史已经做过的动作只承接结果，不重做。"
            )
        return f"{chapter_hint}{entry_hint}接受当前转场提议后进入下一幕；当前回合不能提前写成已经抵达。"
    if status == "runtime_unresolved":
        # 当前没有满足条件的出口时保留未知，不能凭空选择不合格路线。
        return "下一幕尚未确定；玩家接受具体转场提议后由 Runtime 决定。"
    return "暂未提供下一幕方向；继续留在当前幕回应玩家。"


def _suggestion_source_text(performance: Mapping[str, Any]) -> str:
    """把已生成正文压成补推荐所需的可见上下文，不携带内部状态。"""  # noqa: DOCSTRING_CJK

    # 正式换场的推荐只应回应玩家最后看到的目标幕开场。若把来源回应和桥段一起发送，
    # 模型容易继续执行旧幕的“离开/出发”，而不是承接新幕刚出现的问题与选择。
    segments = performance.get("segments")
    if isinstance(segments, list):
        target_segment = next(
            (
                segment
                for segment in reversed(segments)
                if isinstance(segment, Mapping)
                and str(segment.get("phase") or "") == "target_opening"
            ),
            None,
        )
        if isinstance(target_segment, Mapping):
            return "\n".join(
                str(target_segment.get(key) or "").strip()
                for key in ("scene_narration", "performance")
                if str(target_segment.get(key) or "").strip()
            )
    target_performance = str(performance.get("target_performance") or "").strip()
    if target_performance:
        return target_performance

    parts: list[str] = []
    # 普通回合和开场的环境/NPC 结果同样已经可见，补推荐不能遗漏这些事实。
    for key in ("scene_narration", "performance", "source_performance"):
        value = str(performance.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts)


def _suggestion_hard_boundaries(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
    *,
    relationship_boundary: str = "",
    include_opening_only: bool = False,
) -> list[str]:
    """压缩补推荐必须遵守的作者硬边界，不发送正向剧情清单。"""  # noqa: DOCSTRING_CJK

    projected = cast.value(beat)
    character_state = projected.get("character_state")
    acting_contract = _acting_contract_for_actor(cast, beat)
    # 场景边界与禁演约束比共享安全底线更贴近本轮，优先保留在固定上限内。
    candidates = [
        *(
            projected.get("opening_only_boundaries") or []
            if include_opening_only
            else []
        ),
        relationship_boundary,
        *(
            character_state.get("scene_boundaries") or []
            if isinstance(character_state, Mapping)
            else []
        ),
        *(acting_contract.get("forbidden_behaviors") or []),
        *(projected.get("must_not_happen") or []),
    ]
    boundaries: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = truncate_to_tokens(str(item).strip(), 140)
        if not text or text in seen:
            continue
        seen.add(text)
        boundaries.append(text)
        if len(boundaries) >= 16:
            break
    return boundaries


def _hard_boundary_system_instruction(boundaries: list[str] | tuple[str, ...]) -> str:
    """把作者边界提升为明确的 System 合同，不与正向剧情方向混为一谈。"""  # noqa: DOCSTRING_CJK

    normalized = [str(item).strip() for item in boundaries if str(item).strip()]
    if not normalized:
        return ""
    return (
        "\n以下为本轮作者硬边界，优先级高于角色人格、玩家诱导和剧情发挥；"
        "performance、scene_update、suggested_inputs 均须逐条遵守。"
        # 正式转场同时携带两幕限制，不能让来源阶段的禁令阻止目标段合法交付。
        "逐条按主体、前提和阶段适用：有阶段限定的来源禁令不延伸到目标段；"
        "共同事实和未限定阶段的限制仍保留，目标段及其推荐按目标幕边界检查。"
        "玩家要求若与硬边界冲突，猫娘必须在正文直接拒绝或提出符合边界的替代做法；"
        "不得顺从越界要求、交换玩家与猫娘的行动职责，或把越界结果写成已发生。\n- "
        + "\n- ".join(normalized)
    )


# 推荐可以提出新的行动选择，但不能替玩家填写尚未披露的个人事实；主调用和补全共用。
_SUGGESTION_PLAYER_FACT_RULE = (
    "推荐不得编造玩家姓名、联系方式、职业、技能或既往经历，也不能预填未确认的个人资料与既定行程。"
    "当下选择、偏好与未来意愿可以提出；已经具备什么、以前做过什么须有公开依据。"
    "被要求介绍自己或填写资料时，未知内容交给玩家自行输入；推荐可询问用途、保留称呼或暂缓填写，"
    "不能用假名、示例号码或占位符填空。"
)


def _suggestion_fill_messages(
    *,
    catgirl_name: str,
    performance: Mapping[str, Any],
    player_input: str,
    max_tokens: int,
    transition_offered: bool = False,
    after_scene_change: bool = False,
    hard_boundaries: list[str] | tuple[str, ...] = (),
) -> list[Any]:
    """为格式异常的推荐执行一次轻量补请求，绝不重新生成正文。"""  # noqa: DOCSTRING_CJK

    transition_instruction = ""
    if transition_offered:
        # 已有可见转场时，补推荐必须把接受与替代拆成不同取舍，方便玩家直接推进或明确暂缓。
        transition_instruction = (
            "正文已经提出了具体离幕提议；accept_input 必须明确接受并亲自执行该提议，"
            "alternative_inputs 必须拒绝、暂缓或选择当前幕替代行动，各项必须是真实不同的选择。"
        )
    scene_change_instruction = ""
    if after_scene_change:
        scene_change_instruction = (
            "Runtime 已经正式换幕；visible_performance 只包含玩家此刻看到的目标幕开场。"
            "所有推荐必须回应这个新场景刚出现的问题或选择，不得继续回应旧幕输入、旧转场动作或其它已完成事项。"
        )
    system_prompt = (
        "你是 N.E.K.O Numeric v2 的玩家输入推荐补全器。"
        f"当前猫娘是“{catgirl_name}”，但你不能替她说话或行动。"
        f"{_output_schema_instruction('transition_suggestion_fill' if transition_offered else 'suggestion_fill')}"
        "只根据已经生成的可见正文和玩家本轮输入，给出 2—3 条真实不同、可直接发送的玩家选择。"
        "不得把 player_input 原样或仅改空白后再次列为推荐；玩家已经做过的同一句不是下一步选择。"
        "每条必须使用“（玩家动作）玩家对白”，动作中的‘我’可以自然省略；"
        "括号内默认由程序标记为玩家动作，不能明写猫娘、她、他、环境或结果为动作主体。"
        "推荐是玩家对当前回应的下一步反应，不能把猫娘正在做的动作误写成玩家已在做；不混淆双方职责与物品持有者。"
        "不能把尚未发生的结果或其他角色行为写成已经发生。"
        "只能使用可见正文与玩家输入已经支持的玩家身份、地点、能力、物品和事实；"
        "不得虚构姓名、职业、地点、装备或检查结果，也不得保留方括号占位符。"
        f"{_SUGGESTION_PLAYER_FACT_RULE}"
        "一般状态不授权推荐补出未经支持的具体属性、子类、位置或程度；物品只有在可见正文明确由玩家持有或可直接取得时，才能写成玩家正在使用。"
        "hard_boundaries 是作者硬边界，所有推荐的动作、对白、物品用途和关系距离都必须逐条遵守；"
        "不能因为正文刚刚越界就继续沿用该越界内容。"
        f"{transition_instruction}"
        f"{scene_change_instruction}"
        "不要输出解释、purpose、goal_id、kind 或其他字段。"
    ) + _hard_boundary_system_instruction(hard_boundaries)
    data = {
        "visible_performance": _suggestion_source_text(performance),
        # 正式换幕后的旧输入已经由来源回应与桥段消费；继续发送会让补推荐回到旧幕。
        "player_input": "" if after_scene_change else str(player_input or ""),
        "hard_boundaries": list(hard_boundaries),
    }
    return _ensure_actor_messages_fit(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
        ],
        max_tokens=max_tokens,
    )


def _transition_contract_for_actor(
    cast: NumericV2CastProjection,
    contract: Mapping[str, Any] | None,
    *,
    target_opening: str,
) -> dict[str, Any]:
    """目标开场由 Runtime 交付，Actor 不再接收会造成复述的同项合同。"""  # noqa: DOCSTRING_CJK

    projected = cast.value(contract or {})
    target_references = _sentence_units(target_opening)
    must_deliver = []
    for item in projected.get("must_deliver") or []:
        text = str(item).strip()
        if text and not _text_is_covered(
            text,
            target_references,
            similarity=0.55,
            common_span=3,
        ):
            must_deliver.append(text)
    return {
        "reason": str(projected.get("reason") or ""),
        "must_deliver": must_deliver,
        "bridge_scene_narration": str(projected.get("bridge_scene_narration") or "").strip(),
        # 目标开场去重后仍有独立事实，表示这不是可以直接切画面的同场连续动作。
        "bridge_required": bool(
            must_deliver or str(projected.get("bridge_scene_narration") or "").strip()
        ),
        "must_preserve": list(projected.get("must_preserve") or []),
        "tone": str(projected.get("tone") or ""),
    }


# 普通回应与正式转场共用表达要求；只影响措辞，不新增剧情任务或运行时完成条件。
_ACTOR_RESPONSE_RULE = (
    "从玩家本轮的选择、提问或行动中抓住一个影响当前互动的具体细节，说明角色对此的判断或它为何令她在意；"
    "让回应包含具体对象及态度或理由，而非只确认事情完成、表示放松。不必复述整句，也不必每轮夸奖。"
    "已说清的内容不重复，事实有限时允许简短，但不能用与本次互动无关的风景或泛泛附和代替关键答复。"
    # 旁白与对白曾分别将同一劳动写成全部完成和仍有剩余；同轮也必须检查状态一致。
    "正文、旁白和推荐按同一时点核对完成程度：已经全部完成的同一工作不再要求完成剩余部分。"
    "按已有核心人格选择关注点、直率程度和句式，不只是替换口头禅；角色可以认可、保留意见或拒绝，"
    "但不得借表现个性增加新事实、关系承诺、额外动作或需要玩家解决的新问题。"
)


def _system_prompt(
    *,
    catgirl_name: str,
    player_address: str,
    player_address_known: bool = True,
    phase: str = "turn",
) -> str:
    player_address_state_rule = (
        "玩家称呼已确认，正文用“你”或给定称呼。"
        if player_address_known
        else "玩家称呼尚未确认，正文只用“你”，不要猜昵称。"
    )
    if phase == "opening":
        phase_structure_rule = (
            "开场：scene_narration 建立场景，performance 演猫娘入场。只自然交付 opening_deliverables，"
            "不要罗列后续内容建议，并留下玩家可回应的话头。"
        )
    elif phase in {"transition", "transition_compact"}:
        if phase == "transition_compact":
            phase_structure_rule = (
                "换场：先播放可选 source_scene_narration，再播放 source_performance，两者都严格位于 player_input 之后、bridge_scene_narration 之前，"
                "只能回应玩家并收住来源互动；不得出现桥段完成后的时间、地点、到达、醒来结果或 target_scene 独有事实。"
                # 收束不强制追加微动作；已答过的问题只需承接其意义，避免换词重复。
                "若玩家确认已经说定的安排或决定，承接该选择对角色的意义，不再复述同一句约定，也不为求新硬加动作。"
                "bridge_scene_narration 承接来源回应；target_scene_narration 建立目标场景，target_performance 写其后的即时反应。"
                "target_scene.story_direction 只用于让目标幕从开场朝正确方向启动，不得一次演完整幕或照抄策划描述。"
                # 用户允许所有转场适配历史；事实合同保持，已发生动作改写为结果状态。
                "作者桥段与 opening_situation 是时空、必要结果和边界约束，不是必须逐字播放的文字。"
                "根据 recent_context 与 player_input 改写两段旁白，保留作者必要事实、因果顺序与阶段边界；"
                "历史已发生的动作只承接结果，不再次演出，不覆盖玩家的实际选择，也不提前完成目标幕互动。"
                "同地点连续收束不凭空换时空；来源交付互动，桥段只承接结果状态与必要时空变化，目标段给出角色后续反应。"
                "同一动作只在首次交付处发生，后段不再写它开始或落下；没有新变化时简短保留状态，不强求每段制造事件。"
                # 去重不能导致目标正文缺失：没有新消息时仍可对既成结果作简短角色回应。
                "目标段没有新消息时，target_performance 简短承接角色对已成立结果的态度，仍按目标对白策略交付非空正文；"
                "避免重复不等于省略字段、输出空串，或另造动作和任务填充篇幅。"
                "suggested_inputs 只承接最终可见的目标 opening_scene 与 target_performance，不再执行来源幕的离开、出发或收束提议。"
            )
        else:
            phase_structure_rule = (
                "换场依次生成来源回应、必要桥段、目标入场；来源回应仍处于旧幕，不能提前出现桥段之后的"
                "时间、地点、到达或目标幕独有事实；再连续地进入目标场景，不复写目标 opening_scene，"
                "也不提前完成目标幕目标。target_scene.story_direction 只用于让目标幕从开场朝正确方向启动。"
                "source_response 的可选 scene_narration 先呈现本轮在场 NPC 的必要答复，performance 再写猫娘回应；"
                "NPC 可明确拒绝或说明未知，不把猫娘评价代替他的答复，也不提前进入桥段后的场景。"
                "suggested_inputs 只承接最终可见的目标 opening_scene 与 target_performance，不再执行来源幕的离开、出发或收束提议。"
            )
    else:
        phase_structure_rule = (
            "普通回合先正面回应 player_input，再结合 story_so_far 自然延展；"
            "performance 保持简短完整，不为了推进剧本答非所问；"
            "scene_update 只在本轮确有新的可见环境变化时输出，否则省略。"
        )
    # 角色身份来自动态剧本上下文，不在身份指令中放实现名称，避免演员自称框架/协议型号。
    if phase == "transition_compact":
        # 正式转场使用独立合同，不再混入普通回合“尚未接受/不得换幕”的指令。
        # 四段文字共享同一历史；Runtime 只组装标签，来源反应与已完成玩家动作分开表达。
        return (
            "你负责生成本次 Runtime 已授权的正式转场：猫娘写入 performance，在场 NPC 的必要答复写入来源旁白。"
            f"{_output_schema_instruction(phase)}{phase_structure_rule}"
            f"{_ACTOR_RESPONSE_RULE}"
            "recent_context 是已发生事实，player_input 是本轮玩家原话。"
            f"{PLAYER_ACTION_LANGUAGE_RULE}{SCENE_ENTRY_STATE_RULE}"
            "玩家对可执行动作的直接表态已授权动作完成；只考虑、准备、尝试不证明完成。"
            "source_performance 回应玩家的具体选择或动作带来的直接结果；不要把同一操作交给猫娘再做一遍。"
            "猫娘确有尚未完成的必要配合才写自己的动作，不能替玩家新增行动、承诺或外部未知结果。"
            "source_performance 与 target_performance 都只扮演猫娘；括号内是猫娘动作，括号外是她的对白。"
            "本轮直接询问在场 NPC 时，在 source_scene_narration 给出他的具体答复、明确拒绝或说明未知；"
            "不能只写他的神情、感受或动作而省略答复内容。"
            "遵守来源幕的认知与事实边界，不补造未知答案，不用猫娘对他的评价代替他的答复。"
            "source_performance 承接该答复后只写猫娘自身回应，不再复述 NPC 原话。"
            "bridge_scene_narration 与 target_scene_narration 只写可见场景事实；所有旁白不得替玩家补出新的选择，不复述角色已交付的内容。"
            # 新的独立合同仍保留原有认知与因果限制，不能用简化 Prompt 放宽未知事实。
            "作者剧情方向是导演信息，明确的因果先后不能倒置；普通目标开场停在玩家互动之前。"
            "target_scene.opening_situation 已明确建立的内容在目标幕承接，历史已发生的动作不重演。"
            "作者只给出抽象状态或待确认事项时，不得自行具体化；重要事物保持实际归属和最新状态。"
            # 作者开场的旧姿态会诱导转场重置持物、位置与同行关系，明确三段共同的起点。
            "生成前先从最近实际原文确定角色位置、同行关系、物件位置和操作是否已结束，三段共用这个起点；"
            "acting_context 的角色状态是作者入幕基线，不能让已结束的操作重新进行，或把同行写成分开等候后重逢。"
            # 目标模板可以依赖作者预期的来源结果，却不能证明该结果在实际游玩中已经交付。
            "先核对目标段将承接的物品、知识与操作结果在实际历史中的来源；"
            "来源剧情计划及目标开场写着‘已取得／已获知／已完成’，都不证明相应事件实际发生。"
            # 合同中的“承接既有状态”有时来自作者预期，不是 Runtime 已验证的获取记录。
            "reason、must_deliver、must_preserve 或桥段中要求‘承接既有’的状态同样先核对历史；"
            "合同要求保留某结果，不证明该结果已经发生，也不授权倒叙补齐。实际缺失就保持实际状态。"
            "若只余已具备条件的猫娘配合或确定的直接结果，可在来源段先交付，再由后段承接；"
            "仍缺玩家选择、未知成败或必要前因时，按实际状态改写目标段，不倒叙补造、暗示完成或替玩家补做。"
            "历史已成立的结果直接承接，不为交代来源重新领取、讲述或操作。"
            # 约定型结局只交付共识；不能把计划中的未来执行藏进环境旁白。
            "本幕及结局只要求双方达成约定时，收束在约定已明确和角色回应，不能把约定的未来行动写成已经执行，"
            "也不能借房间、道具或地点变化暗示额外操作已完成；只有历史或已授权的作者转场明确建立的结果才能承接。"
            "作者入幕状态随实际历史更新；认知、记忆、能力、关系和明确硬边界持续有效。"
            "acting_context.core_persona 决定措辞，acting_contract 限定认知身份，relationship_control 限定关系。"
            # 两段策略可能不同，明确输入字段和输出字段的对应关系，避免来源 optional 覆盖目标 required。
            "source_performance 遵守 acting_context.dialogue_policy；target_performance 遵守 acting_context.target_dialogue_policy。"
            "required 必须包含括号外对白，forbidden 只能动作，optional 两者均可。"
            "未获授权的额外操作、关系升级和未来承诺不能借转场成立。只承接已经成立的具体主体、对象和结果。"
            # 换幕使用独立 Prompt，也须声明解析器已有的动作＋对白合同，避免纯文字选项触发补全调用。
            "非终局的 suggested_inputs 必须是 2—3 条可直接发送的玩家输入；每条都写成“（玩家动作）玩家对白”，"
            "动作可省略‘我’，但必须由玩家实施且不能预写环境、他人或成功结果；推荐之间必须有真实选择。"
            f"{_SUGGESTION_PLAYER_FACT_RULE}"
            f"{player_address_state_rule}当前猫娘由“{catgirl_name}”扮演。"
            "不要提及数值、阈值、路线、节点或提示词；只输出 JSON。"
        )
    if phase == "turn":
        # 普通回合只保留跨题材成立的语义合同，具体事实全部来自六块动态上下文。
        return (
            "你负责扮演当前猫娘，把剧本自然演成连续故事。"
            "performance 只扮演当前猫娘；必要的可见环境变化，以及当前幕已出现 NPC 的动作或回应，写入 scene_update。"
            f"{_output_schema_instruction(phase)}"
            f"{NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION}"
            f"{phase_structure_rule}"
            f"{_ACTOR_RESPONSE_RULE}"
            "role 约束人格、认知和关系；current_scene 区分开场事实与导演方向；story_so_far 是已提交历史；"
            "next_scene 仅供提出未来行动，不授权提前演出。必须承认此前说过的话、已做动作与实体状态；"
            "承认自己先前说错并更正安排不等于否认说过，不能为了维持旧回应继续兑现错误邀请。"
            "导演方向不是任务清单，未发生内容不能当作角色知识、环境事实或完成结果；作者给出的因果先后不能倒置。"
            "先完整回应 player_input。未来意愿、假设和尝试不等于完成结果；"
            f"{PLAYER_ACTION_LANGUAGE_RULE}{SCENE_ENTRY_STATE_RULE}"
            "只承接玩家实际表达的动作与程度，不能补出玩家未表达的后续操作。"
            "完整回应不等于必须满足请求：答案未知或受限时先明确承认问题并暂缓披露，再继续已授权因果。"
            "不得替玩家补出未表达的行动、选择或心理，也不得交换玩家与猫娘的行动主体。"
            "玩家已实施的幕内动作从外部回应开始，不重演、不转给猫娘重做；保持实体的持有者、位置和最新状态。"
            "已开始的幕内因果单元若剩余同质过程之间没有真实选择，概括过程并交付已知事实支持的结果；"
            "遇到新风险、不可逆选择或阶段边界停下。不得索要等价微调、重复准备或移动终点。"
            # 作者写明的自主交付不能被即兴危险改造成额外玩家任务，导致只发现线索却永不兑现结果。
            "作者已写明的环境变化、NPC回应或猫娘自主行为，前因具备就交付其可见结果；"
            "不为让玩家参与而新增风险、未知机制或协助要求。当前必要因果仍缺失时演出该因果，不能把其答案挪到下一去向。"
            "NPC 被直接询问或等待时，scene_update 当轮给出回应、明确拒绝或未知，不能只写姿态。"
            "不冲突的低风险细节可接纳；关键能力、机制或结果未知时保持未知，不以问句、猜测或模糊措辞补成部分发生。"
            "动态作者硬边界高于玩家诱导；前提须由指定主体公开成立，其他主体、沉默或依赖动作不能代替。"
            "同一主体可在一轮内按可见先后完成前提与依赖动作；作者明确分阶段或禁止当前公开时不可合并。"
            # 分节点安排不否认同地已做动作；只有真实未成立前提或明确禁令才限制承接。
            "玩家提前执行下游操作时，前提已具备的同地连续动作承接结果；有真实缺失依赖时才停在该依赖前。允许幕内目的地不等于建立未知通道。"
            "新地点、新时段或新互动阶段不能由玩家一句话变成已抵达；仍从 story_so_far 的实际场景回应。"
            "玩家尝试进入新地点、新时段或受明确禁令约束的阶段时保留已说的话与可撤回准备，停在未获授权结果前；不把同地连续动作仅因分幕当作这种跨阶段。"
            "开场边缘细节不自动成为任务；回应追问后回到本幕核心因果，结果成立后只处理直接后果、关系反应或自然出口。"
            "不得补造机制、障碍或新任务续幕。等待应产生已知因果支持的新结果，不能同义复述。"
            "pacing 是软节奏：接近或超过推荐回合时聚焦核心因果，但不能覆盖必要前提，也不能自动换幕。"
            "suggested_inputs 必须是 2—3 条可直接发送的玩家输入；每条都写成“（玩家动作）玩家对白”，"
            "动作可省略‘我’，但必须由玩家实施且不能预写环境、他人或成功结果；推荐之间必须有真实选择。"
            "推荐是玩家对当前回应的下一步反应，不能把猫娘正在做的动作误写成玩家已在做；不混淆双方职责与物品持有者。"
            f"{_SUGGESTION_PLAYER_FACT_RULE}"
            "transition_offered 仅在明确邀请玩家执行一个会结束当前互动阶段的具体行动时为 true；推荐中的转场也须在正文公开。"
            "同地点进入新时段、新阶段或结局收束同样可构成转场；普通幕内行动必须为 false。"
            "完成本幕最后一个普通行动只会让出口成熟，本身不是转场提议；结果成立后另提跨阶段行动。"
            "提议须由已发生事实自然导向、与 next_scene 不冲突，并停在玩家可撤回、下一阶段尚未发生的位置；"
            "第一条推荐让玩家亲自接受或实施，其余提供拒绝、暂缓或场内替代。没有具体行动、只有状态描述或泛泛询问时为 false。"
            f"{player_address_state_rule}"
            f"当前猫娘统一由“{catgirl_name}”扮演；微动作主语只用她或猫娘名。"
            "不要提及数值、阈值、路线、节点、系统或提示词；最终 JSON 不输出解释与推理。"
        )
    return (
        "你负责扮演当前猫娘，把剧本自然演成连续故事。"
        "只扮演当前猫娘和获准的场景变化，先回应玩家最新输入；不要替玩家行动、决定或补心理。"
        f"{_output_schema_instruction(phase)}"
        f"{NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION}"
        f"{phase_structure_rule}"
        "输入 JSON 是唯一事实来源；承接作者字段、recent_context 和当前玩家输入。"
        "recent_context 是已发生事实；必须承认其中猫娘已说、已做和已提出的内容。"
        "玩家输入只证明玩家说过、选择过或尝试过；未知、受限或冲突的外部结果不能自动成立。"
        "玩家输入已明确实施的动作是已发生事实；从角色回应和外部结果开始，不得转给猫娘重做或倒退成待执行。"
        # 换场来源回应也使用同一语义，不能在进入另一生成阶段后又要求补写动作格式。
        f"{PLAYER_ACTION_LANGUAGE_RULE}{SCENE_ENTRY_STATE_RULE}"
        "开场处境只建立背景，完整剧情方向决定因果重心；边缘细节没有得到方向支持时，不得扩成新机制、阻碍或多轮任务。"
        "玩家把尚未进入的新地点、新时段或新互动阶段写成当前位置时，不能承认该假设；仍从 recent_context 的实际场景回应。"
        "作者条件必须先公开成立再执行依赖动作；允许目的地不代表未知路径和通行方式自动成立。"
        "若玩家直接尝试跨越互动阶段而 Runtime 尚无待确认提议，只保留已发生的可撤回准备，"
        "不得重演玩家动作或播放跨阶段结果；先由猫娘提出具体确认。"
        "作者剧情方向是导演信息，不是角色已经知道的事实；其中明确的因果先后不能倒置，"
        "某事件依赖玩家回应、选择或前一事实时，在该前提进入 recent_context 前，正文和推荐都不能先使用后续事件。"
        f"{_SUGGESTION_PLAYER_FACT_RULE}"
        "target_scene.opening_situation 已明确建立的内容是入幕事实；target_performance 应承接它，不能把已明确归属、状态或边界重新问成未知。"
        "作者只给出抽象状态或待确认事项时，不得自行具体化；重要事物保持已建立的归属、状态和生命周期。"
        "可选内容不是任务清单，能按玩家输入改写、组合、暂缓或舍弃，遗漏不阻止转场。"
        "next 是玩家接受后的下一幕计划；正式换场前不得提前播放其地点、时段、事件或结果。"
        "普通回合只认 scene_horizon.transition.intent；不是 accept 就不得跨越互动阶段。"
        "runtime_unresolved 的候选不得自行选择或混合；status=none 不猜下一幕。"
        "普通换幕须由玩家明确接受邀请或主动要求进入已公开的下一地点、下一阶段；Runtime 明确标记 natural_ending 的结局除外。"
        "提议必须公开、具体、由已发生事实导向并停在下一阶段结果之前。"
        "完成来源幕最后一个普通行动不是转场；结果成立后仍须提出真正跨入下一阶段的行动。"
        "推荐若包含结束当前互动阶段的行动，transition_offered 必须为 true，且要同时提供玩家执行路径和真实替代；"
        "普通幕内行动或泛泛询问必须为 false。reject 后留在本幕且不重复催促。"
        "acting_context.core_persona 决定表达，acting_contract 决定认知与身份，dialogue_policy 决定能否说话，"
        "relationship_control.response_contract 决定当前关系边界。"
        "dialogue_policy=required 必须有括号外对白，forbidden 只能写动作，optional 两者均可；换场两侧分别遵守各自策略。"
        f"{player_address_state_rule}"
        f"当前猫娘统一由“{catgirl_name}”扮演；微动作主语只用她或猫娘名。"
        "不要提及数值、阈值、路线、节点、系统或提示词；最终 JSON 不输出解释与推理。"
    )


def _messages_tokens(messages: list[Any]) -> int:
    return sum(count_tokens(str(getattr(message, "content", ""))) for message in messages)


def _ensure_actor_messages_fit(
    messages: list[Any],
    *,
    max_tokens: int = 4800,
) -> list[Any]:
    """固定剧情合同不做运行时截断，超出总预算时返回明确错误。"""  # noqa: DOCSTRING_CJK

    if _messages_tokens(messages) > max_tokens:
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    return messages


def _fit_turn_prompt_data(
    *,
    system_prompt: str,
    human_prefix: str,
    data: dict[str, Any],
    max_tokens: int = 4800,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """只删除辅助信息和最早完整回合，不修改任何保留文本。"""  # noqa: DOCSTRING_CJK

    fitted = dict(data)
    initial_history_revisions = [
        item.get("revision")
        for item in fitted.get("recent_context") or []
        if isinstance(item, Mapping) and isinstance(item.get("revision"), int)
    ]
    def tokens() -> int:
        human = human_prefix + json.dumps(fitted, ensure_ascii=False, separators=(",", ":"))
        return count_tokens(system_prompt) + count_tokens(human)

    def finish() -> dict[str, Any]:
        if diagnostics is not None:
            included_history_revisions = [
                item.get("revision")
                for item in fitted.get("recent_context") or []
                if isinstance(item, Mapping) and isinstance(item.get("revision"), int)
            ]
            diagnostics.clear()
            diagnostics.update({
                "budget_tokens": max_tokens,
                "final_tokens": tokens(),
                "history_included_revisions": included_history_revisions,
                "history_dropped_revisions": [
                    revision
                    for revision in initial_history_revisions
                    if revision not in included_history_revisions
                ],
            })
        return fitted

    history = list(fitted.get("recent_context") or [])
    while tokens() > max_tokens and history:
        history.pop(0)
        fitted["recent_context"] = list(history)

    if tokens() > max_tokens:
        # 当前玩家输入、稳定背景、目标幕合同和角色上下文都不可静默删除或裁成半句。
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    return finish()


def _fit_simple_turn_prompt_data(
    *,
    system_prompt: str,
    human_prefix: str,
    data: dict[str, str],
    history_rows: list[Mapping[str, Any]],
    max_tokens: int,
    history_preselected: bool = False,
    evidence_text: str = "",
    fact_index: Callable[[], str] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, str]:
    """只从较早完整回合开始压缩六块 Prompt，绝不截断当前输入或当前回合。"""  # noqa: DOCSTRING_CJK

    fitted = dict(data)
    history = list(history_rows)
    story_prefix = ""
    index_loaded = False
    initial_history_revisions = [
        row.get("revision")
        for row in history
        if isinstance(row.get("revision"), int)
    ]
    def refresh_story() -> None:
        # story_so_far 是唯一的已发生区域；每次淘汰都重新从完整记录渲染。
        nonlocal story_prefix, index_loaded
        if not index_loaded and (history_preselected or len(history) < len(history_rows)):
            # 预选窗口与总预算装箱都会裁剪；只在真实记录缺失后按需生成索引。
            index_loaded = True
            compact = fact_index() if fact_index is not None else ""
            if compact:
                story_prefix = (
                    "当前幕已提交记录摘录（历史已裁剪；片段可能不完整，不代表任务或完成判定）：\n"
                    f"{compact}\n\n最近完整对话："
                )
        recent_story = _story_so_far_text(history)
        fitted["story_so_far"] = "\n\n".join(
            part for part in (evidence_text, story_prefix, recent_story) if part
        )

    def drop_previous_scene_tail() -> bool:
        # 旧幕余波是可丢弃的短承接；预算紧张时先移除它，不能因此牺牲当前幕真实回合。
        for index, row in enumerate(history):
            segments = row.get("segments")
            if not isinstance(segments, list):
                continue
            kept_segments = [
                segment
                for segment in segments
                if not (
                    isinstance(segment, Mapping)
                    and segment.get("phase") == "previous_scene_tail"
                )
            ]
            if len(kept_segments) != len(segments):
                history[index] = {**row, "segments": kept_segments}
                return True
        return False

    def tokens() -> int:
        human = human_prefix + json.dumps(
            fitted,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return count_tokens(system_prompt) + count_tokens(human)

    refresh_story()
    if tokens() > max_tokens and drop_previous_scene_tail():
        refresh_story()
    while tokens() > max_tokens and len(history) > 1:
        # 保留最新一条真实记录，较早回合只按整条记录淘汰。
        history.pop(0)
        refresh_story()
    if tokens() > max_tokens:
        # 固定角色、两幕摘要和玩家本轮输入都不能被裁成半句。
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    if diagnostics is not None:
        included_history_revisions = [
            row.get("revision")
            for row in history
            if isinstance(row.get("revision"), int)
        ]
        diagnostics.clear()
        diagnostics.update({
            "budget_tokens": max_tokens,
            "final_tokens": tokens(),
            "fact_index_included": bool(story_prefix),
            "history_included_revisions": included_history_revisions,
            "history_dropped_revisions": [
                revision
                for revision in initial_history_revisions
                if revision not in included_history_revisions
            ],
        })
    return fitted


def _log_prompt_diagnostics(session: ScriptSessionV2, diagnostics: Mapping[str, Any]) -> None:
    """只记录装箱版本号和 token，不把玩家正文写入日志。"""  # noqa: DOCSTRING_CJK

    message = (
        "Numeric v2 Actor prompt packing session_id=%s revision=%s "
        "tokens=%s/%s history_in=%s history_drop=%s"
    )
    args = (
        session.session_id,
        session.revision,
        diagnostics.get("final_tokens"),
        diagnostics.get("budget_tokens"),
        diagnostics.get("history_included_revisions"),
        diagnostics.get("history_dropped_revisions"),
    )
    if diagnostics.get("history_dropped_revisions"):
        logger.info(message, *args)
    else:
        logger.debug(message, *args)


def _transition_prompt_data(
    *,
    story_context: Mapping[str, Any],
    player_address: str,
    current_chapter_title: str,
    source_story_direction: str,
    target_chapter_title: str,
    shared_boundaries: list[Any],
    source_boundaries: list[Any],
    target_boundaries: list[Any],
    runtime_target_opening: str,
    target_story_direction: str,
    transition_contract: Mapping[str, Any],
    recent_context: list[dict[str, Any]],
    acting_context: Mapping[str, Any],
    player_input: str,
) -> dict[str, Any]:
    """直接构造换场工作记忆，不先生成随后会被删除的完整普通回合上下文。"""  # noqa: DOCSTRING_CJK

    transition = {
        "authorized": True,
        "source_scene": {
            "chapter_title": str(current_chapter_title or ""),
            # 来源叙事仅供理解回应重心，独立于 recent_context 的已发生事实。
            "story_direction": str(source_story_direction or ""),
            "boundaries": list(source_boundaries),
        },
        "target_scene": {
            "chapter_title": str(target_chapter_title or ""),
            "opening_situation": str(runtime_target_opening or ""),
            "story_direction": str(target_story_direction or ""),
            "boundaries": list(target_boundaries),
        },
        "shared_boundaries": list(shared_boundaries),
        "reason": str(transition_contract.get("reason") or ""),
        "must_deliver": list(transition_contract.get("must_deliver") or []),
        # 与解析和Runtime使用同一投影结果，不让模型自行猜测桥段是否可以省略。
        "bridge_required": bool(transition_contract.get("bridge_required", True)),
        "bridge_scene_narration": str(
            transition_contract.get("bridge_scene_narration") or ""
        ),
        "must_preserve": list(transition_contract.get("must_preserve") or []),
        "tone": str(transition_contract.get("tone") or ""),
    }
    return {
        "story_context": dict(story_context),
        "player_address": str(player_address or "你"),
        "recent_context": list(recent_context),
        "acting_context": dict(acting_context),
        "player_input": str(player_input or ""),
        "transition": transition,
    }


def _opening_messages(
    engine: NumericV2Engine,
    character_profile: str,
    catgirl_name: str,
    player_address: str,
    player_address_known: bool = True,
    max_tokens: int = 4800,
    retry_hint: str = "",
) -> list[Any]:
    cast = NumericV2CastProjection.from_story(
        engine.story,
        player_name=player_address,
        catgirl_name=catgirl_name,
    )
    node = engine.nodes[str(engine.story["start_node_id"])]
    opening_beat = _opening_beat_for_actor(engine, cast, node)
    # 正文 Actor 不接收玩家待办；推荐只根据已生成正文单独补全，避免职责混入。
    opening_beat = dict(opening_beat)
    opening_beat.pop("player_reply_goals", None)
    opening_beat.pop("current_direction", None)
    data = {
        "opening_phase": True,
        "visible_player_history": [],
        "player_address_state": {
            "known": player_address_known,
            "projection": player_address,
        },
        "story_context": _story_context_for_actor(
            cast,
            engine.story,
            beats=(node["story_beat"],),
        ),
        "current_chapter_title": _chapter_title_for_actor(cast, node),
        "current_story_beat": opening_beat,
        "acting_context": _acting_context(
            engine,
            cast,
            node,
            engine.story["initial_state"]["metrics"],
            character_profile,
            dialogue_policy=str(
                _acting_contract_for_actor(cast, node["story_beat"]).get(
                    "dialogue_policy"
                )
                or "required"
            ),
        ),
        "instruction": (
            "这是玩家输入前的公开开场。使用必要的环境或猫娘可见行动建立当下场景，再由猫娘主动说出第一句；"
            "不得假定玩家已经说话、做出选择或完成无前因的主动行动，不得使用‘你刚才说/做’或同义的隐形前史。"
            "若 opening_scene 同句明确给出可见前因，可以建立玩家受伤、失衡或被外力带动等即时身体结果；"
            "若节点摘要只有玩家台词、决定或无前因主动行为，把它们视为后续可发展的剧情边界，不要在开场代替玩家执行。"
            "猫娘对白必须由本段旁白能够直接解释，并留下男主可以自然回应的话头；"
            "话头不得反问玩家来替猫娘确认 opening_deliverables 已经要求她肯定确认的状态；不要提前演完本节点。"
            "开场推荐只能使用本次可见开场已经建立的玩家身份、地点、物品、能力和环境事实；"
            "不得把相似但未声明的地点标签、身份判断或状态猜测写成玩家已知事实，也不能要求玩家沿用正文尚未建立的推断。"
        ),
    }
    system_prompt = _system_prompt(
        catgirl_name=catgirl_name,
        player_address=player_address,
        player_address_known=player_address_known,
        phase="opening",
    ) + _hard_boundary_system_instruction(
        list(opening_beat.get("boundaries") or [])
    )
    if retry_hint:
        system_prompt += f"\n本次公开开场必须改写：{retry_hint}"
    fixed_note = actor_note(node, None, {"catgirl_name": catgirl_name, "player_address": player_address},
                            player_address_known, project_condition=cast.text)
    if fixed_note:
        system_prompt += "\n" + fixed_note
    return _ensure_actor_messages_fit(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
        ],
        max_tokens=max_tokens,
    )


def _turn_messages(
    engine: NumericV2Engine,
    session: ScriptSessionV2,
    outcome: TurnOutcomeV2,
    player_input: str,
    character_profile: str,
    catgirl_name: str,
    player_address: str,
    player_address_known: bool = True,
    deterministic_transition: bool = False,
    retry_hint: str = "",
    interaction_intent: str = "mixed_or_unclear",
    input_source: str = "freeform",
    recent_ledger_events: tuple[Mapping[str, Any], ...] = (),
    history_lookup: Mapping[str, Any] | None = None,
) -> list[Any]:
    cast = NumericV2CastProjection.from_story(
        engine.story,
        player_name=player_address,
        catgirl_name=catgirl_name,
    )
    source = engine.nodes[str(outcome.ledger_event["from_node_id"])]
    target = engine.nodes[str(outcome.ledger_event["to_node_id"])]
    route_changed = source["id"] != target["id"]
    system_prompt = _system_prompt(
        catgirl_name=catgirl_name,
        player_address=player_address,
        player_address_known=player_address_known,
        phase=(
            "transition_compact"
            if route_changed and deterministic_transition
            else ("transition" if route_changed else "turn")
        ),
    )
    if retry_hint and route_changed:
        # 重试时明确要求改写当前回应，避免同一输入和同一上下文连续生成相同正文。
        system_prompt += f"\n本轮是输出重试：{retry_hint}"
    current_player_input = str(player_input or "")
    binding = {"catgirl_name": catgirl_name, "player_address": player_address}
    for label, node in [("当前幕", source)] + ([("目标幕", target)] if route_changed else []):
        fixed_note = actor_note(node, session, binding, player_address_known, project_condition=cast.text)
        if fixed_note:
            system_prompt += f"\n{label}固定旁白说明：\n{fixed_note}"
    if source["story_beat"].get("fixed_narrations"):
        system_prompt += "\n已展示的作者原文可能含往事、书信或屏幕日志；其中的地点、伤情和敌人不自动成为当前现场状态。"
    human_prefix = "以下 JSON 是已确定性结算的本回合数据：\n"
    soft_pacing = _soft_pacing(
        source,
        session.node_turn_count + 1,
        route_changed=route_changed,
        transition_intent=str(
            outcome.ledger_event.get("transition_intent") or "unclear"
        ),
    )
    story_context = _story_context_for_actor(
        cast,
        engine.story,
        beats=(
            (source["story_beat"], target["story_beat"])
            if route_changed
            else (source["story_beat"],)
        ),
    )
    actor_budget = numeric_v2_actor_budget(session.actor_budget_profile)
    # 原文只来自已提交历史；路线理由仅作检索线索，不会被拼成已发生事实。
    preview_route = engine.preview_route(session.current_node_id, session.metrics)
    evidence = history_evidence(session, current_player_input,
        focus=str(((preview_route or {}).get("transition_contract") or {}).get("reason") or ""), lookup=history_lookup)
    # 本回合所有重写复用相同原文和查找状态，不重复访问模型，也不写成下一轮的新事实。
    system_prompt += history_lookup_note(history_lookup)
    if evidence:
        system_prompt += HISTORY_EVIDENCE_RULE
    history_selection_diagnostics: dict[str, Any] = {}
    # 普通回合默认保留当前幕完整历史，只有上下文预算不足时才由六块装箱器从最早整轮开始压缩；
    # 正式换场继续使用原有回合窗口，避免扩大高风险协议的改动范围。
    recent_context = _history(
        session,
        max_tokens=actor_budget["history_max_tokens"],
        max_turns=(
            actor_budget["history_max_turns"]
            if route_changed
            else None
        ),
        # 只有进入新幕后尚未演过普通回合时，才临时承接上一幕已提交的可见余波。
        include_previous_scene_tail=(not route_changed and session.node_turn_count == 0),
        diagnostics=history_selection_diagnostics,
    )
    if route_changed:
        source_transition_dialogue_policy = transition_source_dialogue_policy(
            session.dialogue_policy
        )
        # 换场直接构造紧凑工作记忆，不创建普通回合字段后再二次裁剪。
        source_beat = _beat_for_actor(cast, source["story_beat"])
        target_beat = _beat_for_actor(
            cast,
            target["story_beat"],
            include_opening_only_boundaries=True,
        )
        # 两幕完全相同的硬边界只发送一次；精确字符串去重不猜语义，来源和目标差异仍分别完整保留。
        source_boundaries = list(source_beat.get("boundaries") or [])
        target_boundaries = list(target_beat.get("boundaries") or [])
        shared_boundaries = [
            boundary
            for boundary in source_boundaries
            if boundary in target_boundaries
        ]
        source_boundaries = [
            boundary
            for boundary in source_boundaries
            if boundary not in shared_boundaries
        ]
        target_boundaries = [
            boundary
            for boundary in target_boundaries
            if boundary not in shared_boundaries
        ]
        system_prompt += _hard_boundary_system_instruction([
            *(f"来源幕：{item}" for item in source_boundaries),
            *(f"目标幕：{item}" for item in target_boundaries),
            *shared_boundaries,
        ])
        target_opening = str(target_beat["opening_scene"])
        transition_contract = _transition_contract_for_actor(
            cast,
            outcome.transition_contract,
            target_opening=target_opening,
        )
        data = _transition_prompt_data(
            story_context=story_context,
            player_address=player_address,
            current_chapter_title=_chapter_title_for_actor(cast, source),
            source_story_direction=str(source_beat.get("scene_direction") or ""),
            target_chapter_title=_chapter_title_for_actor(cast, target),
            shared_boundaries=shared_boundaries,
            source_boundaries=source_boundaries,
            target_boundaries=target_boundaries,
            runtime_target_opening=target_opening,
            target_story_direction=str(target_beat.get("scene_direction") or ""),
            transition_contract=transition_contract,
            recent_context=recent_context,
            acting_context=_acting_context(
                engine,
                cast,
                source,
                outcome.session.metrics,
                character_profile,
                relationship_metrics=session.metrics,
                target=target,
                dialogue_policy=source_transition_dialogue_policy,
                target_dialogue_policy=outcome.session.dialogue_policy,
            ),
            player_input=current_player_input,
        )
        if evidence:
            data["history_evidence"] = evidence
        final_transition = data.pop("transition")
        if outcome.session.status == "ended":
            # 终局提交即关闭输入，不生成玩家无法发送的后续按钮或待回答话头。
            system_prompt += (
                "本轮进入终局，交付后不再接收输入；suggested_inputs 必须为空数组，正文自然收住，不留下等待玩家回答的新问题。"
                # 结束状态不能掩盖来源台词仍把本轮已授权动作写成待办。
                "来源回应必须承接本轮已授权动作的完成及直接反应，不能退回递工具、催促实施或以完成为条件等待玩家。"
                # 终局可具体回应本次结果，不把“只表达感受”误解成与当前互动无关的风景闲话。
                "逐段检查：已完成的动作不再做一遍；最后一句承接本次结果对角色的意义，不发出喝完再走、下次再来等新邀请。"
            )
        if outcome.ledger_event.get("transition_intent") == "initiate":
            # 主动请求无需虚构前置邀请，授权也仅覆盖这次移动或阶段开始。
            final_transition["player_initiated"] = True
            system_prompt += (
                "本次转场由玩家主动发起，直接回应其已公开去向的请求并承接作者桥段；"
                "不声称玩家接受了未提出的邀请，不再次征求这次转场的确认，不替玩家完成目标幕仍待决定的后续操作。"
            )
        if outcome.session.status == "ended" and outcome.ledger_event.get("natural_ending_ready") is True and outcome.ledger_event.get("transition_intent") not in {"accept", "initiate"}:
            # 与普通接受换幕区分，避免 Actor 把运行时的自然结束伪写成玩家答应了某项提议。
            final_transition["natural_ending"] = True
            # 同轮结束仍先交付获准的最后互动与反应，不能从玩家发起直接跳过结果。
            system_prompt += (
                "本轮 Runtime 已授权自然结局：最后互动尚未交付时，先在来源回应完成玩家已授权的最后互动、"
                "女主配合与必要反应，再承接作者桥段和结局余韵；已经完成的动作不重演。"
                "必须交付已知条件支持的直接结果，不得跳过最后互动直接宣告结束；"
                "不声称玩家接受了未提出的邀请，不补写玩家新行动、承诺，不再发出结束确认或后续任务。"
            )
        data["transition"] = final_transition
        packing_diagnostics: dict[str, Any] = {}
        data = _fit_turn_prompt_data(
            system_prompt=system_prompt,
            human_prefix=human_prefix,
            data=data,
            max_tokens=actor_budget["input_max_tokens"],
            diagnostics=packing_diagnostics,
        )
        packing_diagnostics["history_available_revisions"] = (
            history_selection_diagnostics["history_available_revisions"]
        )
        packing_diagnostics["history_dropped_revisions"] = list(dict.fromkeys((
            *history_selection_diagnostics["history_preselection_dropped_revisions"],
            *packing_diagnostics["history_dropped_revisions"],
        )))
        _log_prompt_diagnostics(session, packing_diagnostics)
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
        ]

    role_context = _acting_context(
        engine,
        cast,
        source,
        outcome.session.metrics,
        character_profile,
        relationship_metrics=session.metrics,
        target=None,
        dialogue_policy=outcome.session.dialogue_policy,
    )
    # 当前幕同时给出已进入的开场处境和作者完整方向。方向不是已发生事实或目标清单，
    # 但不能只剩开场画面，否则模型在长对话或跑题后会失去本幕真正的因果线。
    projected_source_beat = cast.value(source["story_beat"])
    current_scene_opening = cast.text(
        scene_narrative_summary(projected_source_beat)
    ).strip()
    current_scene_direction = cast.text(
        str(
            projected_source_beat.get("narrative_summary")
            or projected_source_beat.get("summary")
            or scene_narrative_focus(projected_source_beat)
            or ""
        )
    ).strip()
    if current_scene_direction and current_scene_direction != current_scene_opening:
        current_scene_summary = (
            f"当前已进入的开场处境：{current_scene_opening}\n"
            "本幕完整剧情方向（自然演绎，不是任务清单，也不是已发生事实）："
            f"{current_scene_direction}"
        )
    else:
        current_scene_summary = current_scene_opening
    relationship_control = role_context.get("relationship_control")
    relationship_boundary = (
        str(relationship_control.get("response_contract") or "").strip()
        if isinstance(relationship_control, Mapping)
        else ""
    )
    hard_boundaries = _suggestion_hard_boundaries(
        cast,
        source["story_beat"],
        relationship_boundary=relationship_boundary,
    )
    # 精确边界放进 System 合同以提高遵循度，Human 六块继续只承载角色、剧情、历史与当前输入，避免重复 Token。
    system_prompt += _hard_boundary_system_instruction(hard_boundaries)
    if input_source == "suggestion":
        # 推荐点击已经是 Actor 上一轮公开给出的路径，不能再被纯闲聊合同吞成无效确认。
        system_prompt += (
            "\n推荐承接合同（本轮必须遵守）：玩家点击了你上一轮公开给出的推荐。"
            "与自由输入适用同一行动授权：承接已经明确表达的动作与对白，不能补出玩家未表达的后续操作。"
            "不要重新询问已做出的选择，也不要把它降格为纯闲聊；若旧推荐有误，自然澄清可行做法，不得为了兑现推荐而越界。"
            "若这项选择本身会结束当前互动、进入下一时段或下一场景，且 Runtime 尚无待确认提议，"
            "停在结果发生前明确说明后果并提出确认；不要直接播放换幕结果。"
        )
    elif interaction_intent == "chat":
        # 纯闲聊合同放在 System 末尾，避免下一幕预览和超软预算提示把模型重新拉回推进模式。
        system_prompt += (
            "\n纯闲聊回应合同（本轮必须遵守）：完整回应玩家正在谈的情绪、关系、玩笑或主观问题；"
            "不要主动提出、重述或催促任何离幕行动，不要询问玩家是否继续，"
            "transition_offered 必须为 false。suggested_inputs 应优先提供继续当前闲聊的自然回应，"
            "可以保留至多一条当前幕内的回归剧情选项，但不得离开当前幕或替玩家决定路线。"
        )
    next_scene_preview = _next_scene_preview_for_actor(
        engine,
        cast,
        source,
        outcome.session.metrics,
    )
    pacing_text = (
        f"当前是第 {int(soft_pacing['current_turn'])} 回合，本幕推荐 "
        f"{int(soft_pacing['recommended_turns'])} 回合。"
    )
    # 固定事实与所有权合同集中在 System；这里只投影当前回合的输入方式、节奏和提议状态。
    pure_chat = interaction_intent == "chat" and input_source != "suggestion"
    if pure_chat:
        pacing_text += "本轮主要是当前场景内的闲聊；回合数不要求推进，不能把闲聊当成转场接受。"
    else:
        pacing_text += str(soft_pacing["instruction"])
    if input_source == "suggestion":
        pacing_text += (
            "本轮来自上一轮可见推荐；仅按实际输入承接，准备或尝试不额外授权后续操作与结果。"
        )
    elif interaction_intent == "scene_action":
        pacing_text += (
            "本轮是场内行动或调查：直接交付这个行动的已知结果或明确未知，不重复确认。"
        )
    elif not pure_chat:
        pacing_text += (
            "本轮意图混合或未明：先回应其中的对白或情绪，再承接明确行动，不升级含糊意愿。"
        )
    natural_closure_ready = (
        outcome.ledger_event.get("scene_complete") is True
        and not session.transition_offered
        and outcome.ledger_event.get("transition_intent") != "reject"
        and not pure_chat
        and next_scene_preview.get("status") == "after_acceptance_only"
    )
    if natural_closure_ready:
        # 自然收束仍不是 Runtime 换幕条件；这里只要求 Actor 把已经成熟的因果写成玩家可回应的公开提议。
        # 放在与纯闲聊同级的本轮合同中，避免长角色背景重新制造已经解决的情绪阻碍。
        system_prompt += (
            "\n本轮自然收束合同：核心变化已成立，先回应玩家，再提出 next_scene 支持的具体跨阶段行动，"
            "停在玩家可接受或暂缓的位置。性格只影响表达，不重新否认已完成的变化；"
            "没有作者支持的未决事实时，不新增等待、复查或必须继续安抚的前提。"
        )
        pacing_text += (
            "本轮有自然收束信号；这不是自动换幕授权。先回应玩家，再依据已成立结果提出具体收束行动，"
            "说明接受后跨入哪个已知阶段或时点，只把跨越互动阶段的结果留待下一轮确认。"
        )
    # 这里只保留显式作者重心：旧包 transition_goal 常写“已取得/已连接”，
    # 不能通过回退把尚待满足的出口条件混入本轮运行节奏。完整剧情仍在 current_scene。
    narrative_focus = cast.text(str(source["story_beat"].get("narrative_focus") or "")).strip()
    if narrative_focus:
        pacing_text += f"作者建议重心（不表示事件已经发生）：{narrative_focus}。"
    if (
        not session.transition_offered
        and not pure_chat
        and outcome.ledger_event.get("transition_intent") != "reject"
        and int(soft_pacing["current_turn"]) >= int(soft_pacing["recommended_turns"])
    ):
        pacing_text += (
            "对照本幕方向与已提交历史：核心冲突尚未清楚时先交付关键事实；"
            "已清楚时让连续行动落到结果或自然出口。推荐从已有结果后的取舍开始，不新增中间条件。"
        )
    if session.transition_offered:
        # 上一轮已有可见提议但本轮还没有正式换幕时，禁止把目标地点或目标结果写成已发生。
        pacing_text += (
            "上一轮已有待确认提议，本回合尚未完成换幕；留在本幕回应，不重复催促。"
        )
        if pure_chat:
            pacing_text += (
                "旧提议由 Runtime 保留，本轮正文与推荐继续闲聊。"
            )
        else:
            # 待确认只代表曾公开；错误邀请不能因为锁存就强制成为接受按钮。
            pacing_text += (
                "只有旧提议仍符合实际出口时，第一条推荐才可接受并亲自执行该提议，第二条拒绝、暂缓或留在本幕。"
            )
    if retry_hint:
        # 纠错任务放在完整合同之后，不再叠加本轮的推进压力；作者边界和真实输入仍然有效。
        system_prompt += f"\n本轮是输出重试：{retry_hint}"
        pacing_text = (
            f"当前是第 {int(soft_pacing['current_turn'])} 回合，本幕推荐 "
            f"{int(soft_pacing['recommended_turns'])} 回合。本轮先修正未提交输出，不为节奏补出动作或结果。"
        )
    invalidated_invitation = outcome.ledger_event.get("transition_offer_invalidated") is True
    if (session.transition_offered or invalidated_invitation) and not pure_chat:
        # 原始邀请属于已公开事实，改写撤掉节奏压力时仍应保留；Ledger 区分拒绝后重提。
        pending_offer = pending_transition_performance(session, ledger_events=recent_ledger_events,
                                                       include_withdrawn=invalidated_invitation)
        if pending_offer:
            if invalidated_invitation:
                pacing_text += (
                    f"已确认去向错误并撤下的旧邀请原文：{pending_offer}"
                    "本轮先承认自己先前邀约有误，再说明 next_scene 支持的可行安排，留给玩家选择。"
                    "这不是仍待接受的邀请，不能继续催促出发、暗示仍能兑现旧安排或把错误归给玩家。"
                )
            else:
                pacing_text += f"当前待确认提议原文（只证明此前说过，不证明安排正确）：{pending_offer}"
            # 原提议也可能来自旧的错误演出，不能为了承接它换成另一个出口或责怪玩家误记。
            pacing_text += (
                "若原提议与 next_scene 的实际去向不相符，承认是自己先前说错并说明当前可行安排，"
                "保留玩家原意与重新选择权；不指责玩家误记，不悄悄执行不同去向，也不继续重复错误邀请。"
                "不为更正补造人物行程或新的阻碍；推荐承接本轮已澄清的安排，不推荐继续执行已指出错误的旧去向。"
            )
    # 六块数据按固定顺序写入，玩家输入始终位于最后，减少历史内容覆盖当前要求。
    data: dict[str, str] = {
        "role": _role_prompt_text(
            catgirl_name=catgirl_name,
            acting_context=role_context,
        ),
        "current_scene": current_scene_summary,
        "story_so_far": _story_so_far_text(recent_context),
        "pacing": pacing_text,
        # 纯闲聊无需看到下一幕导演信息；保留固定六块形状，但去掉最容易诱发抢跑的内容锚点。
        "next_scene": (
            "本轮纯闲聊，不使用下一幕方向。"
            if interaction_intent == "chat" and input_source != "suggestion"
            else _next_scene_summary_text(next_scene_preview)
        ),
        "player_input": current_player_input,
    }
    human_prefix = "以下 JSON 是本回合六块演绎上下文：\n"
    packing_diagnostics = {}
    data = _fit_simple_turn_prompt_data(
        system_prompt=system_prompt,
        human_prefix=human_prefix,
        data=data,
        history_rows=recent_context,
        max_tokens=actor_budget["input_max_tokens"],
        history_preselected=bool(
            history_selection_diagnostics["history_preselection_dropped_revisions"]
        ),
        evidence_text=("history_evidence（已提交原文）：" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")) if evidence else ""),
        fact_index=lambda: _current_scene_fact_index_text(session),
        diagnostics=packing_diagnostics,
    )
    packing_diagnostics["history_available_revisions"] = (
        history_selection_diagnostics["history_available_revisions"]
    )
    packing_diagnostics["history_dropped_revisions"] = list(dict.fromkeys((
        *history_selection_diagnostics["history_preselection_dropped_revisions"],
        *packing_diagnostics["history_dropped_revisions"],
    )))
    _log_prompt_diagnostics(session, packing_diagnostics)
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
    ]


async def _model_config(config_manager: Any) -> dict[str, Any]:
    getter = getattr(config_manager, "aget_model_api_config", None) or getattr(config_manager, "get_model_api_config", None)
    if getter is None:
        raise NumericV2ActorUnavailableError("numeric_v2_actor_config_unavailable")
    try:
        value = getter("conversation")
        config = await value if inspect.isawaitable(value) else value
    except Exception as exc:
        raise NumericV2ActorUnavailableError("numeric_v2_actor_config_unavailable") from exc
    if not isinstance(config, Mapping) or not str(config.get("model") or "").strip() or not str(config.get("base_url") or "").strip():
        raise NumericV2ActorUnavailableError("numeric_v2_actor_config_unavailable")
    return dict(config)


class NumericV2Actor:
    """编排一次 Actor 输出正文、推荐与转场意图；不拥有 Session 写权限。"""  # noqa: DOCSTRING_CJK

    def __init__(self, config_manager: Any):
        self.config_manager = config_manager
        # 统计该 Actor 实例真正进入供应商请求的次数；正文重采样和轻量补推荐都会分别计数。
        self.provider_call_count = 0
        # 区分补推荐的触发原因与真实供应商成本，便于压测判断哪类调用可以优化。
        self.suggestion_fill_attempt_count = 0
        self.suggestion_fill_provider_call_count = 0
        self.suggestion_fill_reason_counts = {
            "invalid_or_missing": 0,
        }
        self.base_suggestion_parse_counts: dict[str, int] = {}

    def _character_profile(self) -> str:
        """只读取服务端当前猫娘的人格摘要，不接受客户端角色名。"""  # noqa: DOCSTRING_CJK

        try:
            characters = self.config_manager.load_characters()
        except Exception:
            characters = {}
        current_name = str(characters.get("当前猫娘") or "").strip() if isinstance(characters, Mapping) else ""
        return _load_character_profile(
            self.config_manager,
            current_name,
        )

    def _current_catgirl_name(self) -> str:
        try:
            characters = self.config_manager.load_characters()
        except Exception:
            return "当前猫娘"
        return str(characters.get("当前猫娘") or "当前猫娘").strip() or "当前猫娘"

    async def _ensure_suggestions(
        self,
        *,
        performance: Mapping[str, Any],
        player_input: str,
        catgirl_name: str,
        max_input_tokens: int,
        hard_boundaries: list[str] | tuple[str, ...] = (),
        scene_changed: bool = False,
    ) -> list[str]:
        """只在推荐缺失或格式异常时轻量补全一次，不覆盖合法正文。"""  # noqa: DOCSTRING_CJK

        raw_suggestions = [
            str(item).strip()
            for item in performance.get("suggested_inputs") or []
            if str(item).strip()
        ]
        normalized_player_input = " ".join(str(player_input or "").split())
        suggestions = [
            item
            for item in raw_suggestions
            if " ".join(item.split()) != normalized_player_input
        ]
        repeated_input_count = len(raw_suggestions) - len(suggestions)
        if repeated_input_count:
            self.base_suggestion_parse_counts["repeats_player_input"] = (
                self.base_suggestion_parse_counts.get("repeats_player_input", 0)
                + repeated_input_count
            )
        if len(suggestions) in {2, 3}:
            return suggestions
        transition_offered = performance.get("transition_offered") is True
        self.suggestion_fill_attempt_count += 1
        self.suggestion_fill_reason_counts["invalid_or_missing"] += 1
        provider_calls_before = self.provider_call_count
        try:
            filled = await self._invoke(
                _suggestion_fill_messages(
                    catgirl_name=catgirl_name,
                    performance=performance,
                    player_input=player_input,
                    max_tokens=max_input_tokens,
                    transition_offered=transition_offered,
                    after_scene_change=scene_changed,
                    hard_boundaries=hard_boundaries,
                ),
                suggestions_only=True,
                transition_suggestions_only=transition_offered,
                max_input_tokens=max_input_tokens,
                max_output_tokens=NUMERIC_V2_ACTOR_SUGGESTION_FILL_MAX_OUTPUT_TOKENS,
            )
        except NumericV2ActorError as exc:
            # 补推荐失败不能回滚正文；已有合法推荐时继续保留，避免一次辅助调用抖动清空按钮。
            logger.warning(
                "Numeric v2 Actor suggestion fill failed: reason=%s",
                str(exc),
            )
            return suggestions if len(suggestions) in {2, 3} else []
        finally:
            self.suggestion_fill_provider_call_count += max(
                0,
                self.provider_call_count - provider_calls_before,
            )
        filled_suggestions = [
            str(item).strip()
            for item in filled.get("suggested_inputs") or []
            if str(item).strip()
            and " ".join(str(item).split()) != normalized_player_input
        ]
        if len(filled_suggestions) in {2, 3}:
            return filled_suggestions
        return suggestions if len(suggestions) in {2, 3} else []

    async def generate_opening(
        self,
        *,
        engine: NumericV2Engine,
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
        retry_hint: str = "",
    ) -> dict[str, Any]:
        actor_budget = numeric_v2_actor_budget(actor_budget_profile)
        profile = self._character_profile()
        catgirl_name = self._current_catgirl_name()
        configured_address = _load_player_address(self.config_manager)
        player_address_known = bool(engine.story["initial_state"]["player_address_known"])
        player_address = _project_player_address(
            configured_address,
            known=player_address_known,
        )
        start_node = engine.nodes[str(engine.story["start_node_id"])]
        start_cast = NumericV2CastProjection.from_story(
            engine.story,
            player_name=player_address,
            catgirl_name=catgirl_name,
        )
        start_contract = _acting_contract_for_actor(start_cast, start_node["story_beat"])
        opening_dialogue_policy = str(
            start_contract.get("dialogue_policy") or "required"
        )
        performance = await self._invoke(
            _opening_messages(
                engine,
                profile,
                catgirl_name,
                player_address,
                player_address_known,
                max_tokens=actor_budget["input_max_tokens"],
                retry_hint=retry_hint,
            ),
            opening_required=True,
            max_input_tokens=actor_budget["input_max_tokens"],
            max_output_tokens=NUMERIC_V2_ACTOR_OPENING_MAX_OUTPUT_TOKENS,
            dialogue_policy=opening_dialogue_policy,
        )
        _assert_acting_contract_output(
            performance,
            character_profile=profile,
            acting_contract=start_contract,
        )
        _assert_no_unknown_player_address_leak(
            performance,
            player_address=configured_address,
            player_address_known=player_address_known,
        )
        performance = dict(performance)
        # 主调用已经产出合法推荐时直接提交；只有缺失或格式异常才补一次按钮，避免开场固定增加供应商请求。
        performance["suggested_inputs"] = await self._ensure_suggestions(
            performance=performance,
            player_input="",
            catgirl_name=catgirl_name,
            max_input_tokens=actor_budget["input_max_tokens"],
            hard_boundaries=_suggestion_hard_boundaries(
                start_cast,
                start_node["story_beat"],
                include_opening_only=True,
                relationship_boundary=str(
                    _relationship_control(
                        engine,
                        start_node,
                        engine.story["initial_state"]["metrics"],
                    ).get("response_contract")
                    or ""
                ),
            ),
        )
        return performance

    async def generate_turn(
        self,
        *,
        engine: NumericV2Engine,
        session: ScriptSessionV2,
        outcome: TurnOutcomeV2,
        player_input: str,
        character_profile: str | None = None,
        retry_hint: str = "",
        interaction_intent: str = "mixed_or_unclear",
        input_source: str = "freeform",
        recent_ledger_events: tuple[Mapping[str, Any], ...] = (),
        history_lookup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 工作流可冻结本轮实际使用的人格文本，确保提交前能复验同一生成世代。
        profile = (
            self._character_profile()
            if character_profile is None
            else str(character_profile)
        )
        catgirl_name = str(session.catgirl_binding.get("catgirl_name") or self._current_catgirl_name())
        configured_address = str(
            session.catgirl_binding.get("player_address")
            or _load_player_address(self.config_manager)
        ).strip()
        player_address_known = session.player_address_known
        player_address = _project_player_address(
            configured_address,
            known=player_address_known,
        )
        route_changed = (
            outcome.ledger_event["from_node_id"]
            != outcome.ledger_event["to_node_id"]
        )
        source = engine.nodes[str(outcome.ledger_event["from_node_id"])]
        target = engine.nodes[str(outcome.ledger_event["to_node_id"])]
        cast = NumericV2CastProjection.from_story(
            engine.story,
            player_name=player_address,
            catgirl_name=catgirl_name,
        )
        target_beat = _beat_for_actor(
            cast,
            target["story_beat"],
            include_opening_only_boundaries=route_changed,
        )
        target_opening = target_beat["opening_scene"]
        transition_contract = (
            _transition_contract_for_actor(
                cast,
                outcome.transition_contract,
                target_opening=target_opening,
            )
            if route_changed
            else {}
        )
        authored_bridge = str(
            transition_contract.get("bridge_scene_narration") or ""
        ).strip()
        # 所有正式转场统一用紧凑四文本合同，标签由 Runtime 确定，文字按历史适配。
        deterministic_transition = route_changed
        source_transition_dialogue_policy = transition_source_dialogue_policy(
            session.dialogue_policy
        )
        visible_contract = _acting_contract_for_actor(
            cast,
            (target if route_changed else source)["story_beat"],
        )
        actor_budget = numeric_v2_actor_budget(session.actor_budget_profile)
        performance = await self._invoke(
            _turn_messages(
                engine,
                session,
                outcome,
                player_input,
                profile,
                catgirl_name,
                player_address,
                player_address_known,
                deterministic_transition,
                retry_hint,
                interaction_intent,
                input_source,
                # Ledger 只用于确定原提议记录，不将隐藏状态整体发送给 Actor。
                recent_ledger_events,
                history_lookup,
            ),
            transition_required=route_changed,
            deterministic_transition=deterministic_transition,
            bridge_required=bool(transition_contract.get("bridge_required", True)),
            max_input_tokens=actor_budget["input_max_tokens"],
            max_output_tokens=(
                NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS
                if route_changed
                else NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS
            ),
            target_opening=target_opening,
            dialogue_policy=outcome.session.dialogue_policy,
            source_dialogue_policy=source_transition_dialogue_policy,
            target_dialogue_policy=outcome.session.dialogue_policy,
        )
        if route_changed:
            # 先统一为提交合同，再执行所有权、人格和事实检查；两种 Actor 输出形状因此共享同一验证路径。
            performance = engine.finalize_transition_performance(
                outcome,
                performance,
                target_opening=target_opening,
                bridge_required=bool(transition_contract.get("bridge_required")),
                bridge_scene_narration=authored_bridge,
                source_dialogue_policy=source_transition_dialogue_policy,
                target_dialogue_policy=outcome.session.dialogue_policy,
            )
            segments = performance.get("segments")
            source_segment = segments[0] if isinstance(segments, list) and len(segments) == 3 else {}
            target_segment = segments[2] if isinstance(segments, list) and len(segments) == 3 else {}
            # 人格合同只检查猫娘两侧正文；动态旁白的事实与阶段边界由工作流整段复核。
            source_output = {"performance": source_segment.get("performance", "")}
            target_output = {"performance": target_segment.get("performance", "")}
            _assert_acting_contract_output(
                source_output,
                character_profile=profile,
                acting_contract=_acting_contract_for_actor(cast, source["story_beat"]),
            )
            _assert_acting_contract_output(
                target_output,
                character_profile=profile,
                acting_contract=_acting_contract_for_actor(cast, target["story_beat"]),
            )
        else:
            _assert_acting_contract_output(
                performance,
                character_profile=profile,
                acting_contract=_acting_contract_for_actor(cast, source["story_beat"]),
            )
        _assert_no_unknown_player_address_leak(
            performance,
            player_address=configured_address,
            player_address_known=player_address_known,
            player_input=player_input,
        )
        # 第一回合没有普通历史时，opening 就是玩家上一条看到的演绎；重复保护必须覆盖它。
        if session.performance_history:
            previous_visible_performance = session.performance_history[-1]
            previous_scene_performance = previous_visible_performance
        else:
            previous_visible_performance = session.opening_performance
            previous_scene_performance = previous_visible_performance
            if isinstance(previous_visible_performance.get("performance"), str):
                # 开场场景旁白不会在普通回合复现；比较时只取猫娘开场正文，避免长旁白稀释重复率。
                previous_visible_performance = {
                    "performance": previous_visible_performance["performance"],
                }
        if not route_changed:
            performance = _deduplicate_scene_update(
                performance,
                previous_scene_performance,
            )
        safe_final_chat_repeat = (
            interaction_intent == "chat"
            and input_source == "freeform"
            and "最后一次重复输出重试" in retry_hint
            and not route_changed
            and not outcome.metric_changes
            and performance.get("transition_offered") is not True
            and "scene_update" not in performance
            and "scene_narration" not in performance
            and count_tokens(_performance_text(performance)) <= 120
            and _player_input_repeats_recent_context(
                player_input,
                _history(
                    session,
                    max_tokens=actor_budget["history_max_tokens"],
                    max_turns=actor_budget["history_max_turns"],
                ),
            )
        )
        repeats_earlier = _repeats_earlier_session_performance(
            performance,
            session,
            route_changed=route_changed,
        )
        if repeats_earlier and not safe_final_chat_repeat:
            logger.warning(
                "Numeric v2 Actor failed: reason=numeric_v2_actor_repeated_session_output session_id=%s revision=%s",
                session.session_id,
                session.revision,
            )
            raise NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
        if (
            previous_visible_performance
            and route_changed
            and _transition_source_repeats_previous(
                performance,
                previous_visible_performance,
            )
        ):
            logger.warning(
                "Numeric v2 Actor failed: reason=numeric_v2_actor_repeated_transition_source session_id=%s revision=%s",
                session.session_id,
                session.revision,
            )
            raise NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
        if (
            previous_visible_performance
            and _is_repeated_performance(performance, previous_visible_performance)
        ):
            stable_confirmation = (
                not route_changed
                and not outcome.metric_changes
                and "scene_narration" not in performance
                and count_tokens(_performance_text(performance)) <= 80
                and _player_input_repeats_recent_context(
                    player_input,
                    _history(
                        session,
                        max_tokens=actor_budget["history_max_tokens"],
                        max_turns=actor_budget["history_max_turns"],
                    ),
                )
            )
            if stable_confirmation or safe_final_chat_repeat:
                # 玩家重复同一输入且状态没有任何变化时，简短确认是合理结果；
                # 纯闲聊最后一次重试也允许安全短回应降级，避免因话题自然趋同让整轮发送失败。
                logger.debug(
                    "Numeric v2 Actor accepted stable repeated confirmation: session_id=%s revision=%s",
                    session.session_id,
                    session.revision,
                )
            else:
                logger.warning(
                    "Numeric v2 Actor failed: reason=numeric_v2_actor_repeated_output session_id=%s revision=%s",
                    session.session_id,
                    session.revision,
                )
                raise NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
        # 正文通过全部确定性校验后，再确保同一次 Actor 调用返回的推荐满足数量合同；
        # 开场、已提转场和正式换幕都保留合法推荐，仅在推荐异常时轻量补全一次。
        performance = dict(performance)
        if outcome.session.status == "ended":
            # 终局已经关闭输入；忽略模型误附的按钮，跳过无意义的补推荐与后续按钮复核。
            performance["suggested_inputs"] = []
            return performance
        performance["suggested_inputs"] = await self._ensure_suggestions(
            performance=performance,
            player_input=player_input,
            catgirl_name=catgirl_name,
            max_input_tokens=actor_budget["input_max_tokens"],
            # 换幕时仅在确实需要补推荐的情况下隐藏已消费的旧幕玩家输入。
            scene_changed=route_changed,
            hard_boundaries=_suggestion_hard_boundaries(
                cast,
                (target if route_changed else source)["story_beat"],
                relationship_boundary=str(
                    _relationship_control(
                        engine,
                        target if route_changed else source,
                        session.metrics,
                    ).get("response_contract")
                    or ""
                ),
            ),
        )
        return performance

    async def _invoke(
        self,
        messages: list[Any],
        *,
        opening_required: bool = False,
        transition_required: bool = False,
        deterministic_transition: bool = False,
        bridge_required: bool = True,
        max_input_tokens: int = 4800,
        max_output_tokens: int = NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS,
        target_opening: str = "",
        dialogue_policy: str = "required",
        source_dialogue_policy: str = "required",
        target_dialogue_policy: str = "required",
        suggestions_only: bool = False,
        transition_suggestions_only: bool = False,
    ) -> dict[str, Any]:
        set_call_type("theater_numeric_v2_actor")
        request_messages = _ensure_actor_messages_fit(
            messages,
            max_tokens=max_input_tokens,
        )
        started_at = time.monotonic()
        config_finished_at = started_at
        client_finished_at = started_at
        request_finished_at = started_at
        try:
            # 总时限覆盖配置读取、客户端构造、网络请求、输出解析和客户端关闭。
            async with asyncio.timeout(NUMERIC_V2_ACTOR_TIMEOUT_SECONDS):
                config = await _model_config(self.config_manager)
                config_finished_at = time.monotonic()
                client = await create_chat_llm_async(
                    str(config["model"]),
                    str(config["base_url"]),
                    config.get("api_key"),
                    provider_type=config.get("provider_type"),
                    timeout=NUMERIC_V2_ACTOR_TIMEOUT_SECONDS,
                    max_retries=0,
                    max_completion_tokens=max_output_tokens,
                )
                client_finished_at = time.monotonic()
                async with client:
                    # request_messages 已由 _ensure_actor_messages_fit 按模型输入预算裁剪。
                    self.provider_call_count += 1
                    # 每次补写/补推荐分别记账；仅观察供应商返回，不改变调用和重试策略。
                    response = await invoke_with_usage(client, request_messages,
                        stage="suggestions" if suggestions_only or transition_suggestions_only else "actor")  # noqa: LLM_INPUT_BUDGET
                    request_finished_at = time.monotonic()
                    suggestion_diagnostics: dict[str, int] = {}
                    parsed = _parse_output(
                        getattr(response, "content", None),
                        opening_required=opening_required,
                        transition_required=transition_required,
                        deterministic_transition=deterministic_transition,
                        bridge_required=bridge_required,
                        target_opening=target_opening,
                        dialogue_policy=dialogue_policy,
                        source_dialogue_policy=source_dialogue_policy,
                        target_dialogue_policy=target_dialogue_policy,
                        suggestions_only=suggestions_only,
                        transition_suggestions_only=transition_suggestions_only,
                        suggestion_diagnostics=suggestion_diagnostics,
                    )
                    if (
                        not suggestions_only
                        and not transition_suggestions_only
                    ):
                        for reason, count in suggestion_diagnostics.items():
                            self.base_suggestion_parse_counts[reason] = (
                                self.base_suggestion_parse_counts.get(reason, 0)
                                + int(count)
                            )
        except asyncio.TimeoutError as exc:
            logger.warning(
                "Numeric v2 Actor failed: reason=numeric_v2_actor_timeout elapsed=%.3f",
                time.monotonic() - started_at,
            )
            raise NumericV2ActorError("numeric_v2_actor_timeout") from exc
        except NumericV2ActorError as exc:
            reason = str(exc) if str(exc).startswith("numeric_v2_actor_") else type(exc).__name__
            logger.warning("Numeric v2 Actor failed: reason=%s", reason)
            raise
        except Exception as exc:
            logger.warning("Numeric v2 Actor failed: reason=numeric_v2_actor_model_call_failed error_type=%s", type(exc).__name__)
            raise NumericV2ActorError("numeric_v2_actor_model_call_failed") from exc
        total_seconds = time.monotonic() - started_at
        if total_seconds >= NUMERIC_V2_ACTOR_SLOW_CALL_SECONDS:
            # 只记录阶段耗时，不记录剧本、玩家输入或模型输出。
            logger.warning(
                "Numeric v2 Actor slow call: total=%.3f config=%.3f client=%.3f request=%.3f finalize=%.3f",
                total_seconds,
                config_finished_at - started_at,
                client_finished_at - config_finished_at,
                request_finished_at - client_finished_at,
                time.monotonic() - request_finished_at,
            )
        return parsed


__all__ = [
    "NUMERIC_V2_ACTOR_OPENING_MAX_OUTPUT_TOKENS",
    "NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS",
    "NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS",
    "NUMERIC_V2_ACTOR_TIMEOUT_SECONDS",
    "NumericV2Actor",
    "NumericV2ActorError",
    "NumericV2ActorOutputError",
    "NumericV2ActorUnavailableError",
]
