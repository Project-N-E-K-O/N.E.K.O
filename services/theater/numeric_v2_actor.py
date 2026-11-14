"""Numeric v2 performance orchestration: generate narrative and player-input recommendations together."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import difflib
import inspect
import json
import logging
import re
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from config.prompts.prompts_theater import (
    NUMERIC_V2_CONTEXT_READING_INSTRUCTION,
    NUMERIC_V2_ACTOR_EVENT_OWNERSHIP_INSTRUCTION,
    NUMERIC_V2_ACTOR_JSON_INSTRUCTION,
    NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION,
    NUMERIC_V2_ACTOR_OPENING_NARRATION_INSTRUCTION,
)
from utils.llm_client import HumanMessage, _normalize_messages, create_chat_llm_async
from .numeric_v2_prompt_mark import theater_system_message
from utils.token_tracker import set_call_type
from .numeric_v2_usage import invoke_with_usage
from .numeric_v2_structured_output import actor_output_schema, response_format_for
from .numeric_v2_options import aload_theater_module_options
from .numeric_v2_trace import trace_event
from .numeric_v2_turn_control import optimized_turn_enabled
from utils.tokenize import count_tokens, truncate_head_tail_tokens, truncate_to_tokens

from .llm_context import (
    THEATER_BEHAVIOR_PERSONA_FIELDS,
    THEATER_STYLE_PERSONA_FIELDS,
    _load_character_profile,
    _load_player_address,
    theater_persona_field,
)
from .numeric_v2_budget import (
    NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    numeric_v2_actor_budget,
)
from .numeric_v2_action_projection import (
    player_action_projection_for_prompt,
    project_player_action_result,
    reference_player_action_input,
)
from .numeric_v2_cast import NumericV2CastProjection
from .numeric_v2_context import (
    current_entry_cause,
    current_entry_fixed_narrations,
    EVIDENCE_SCOPE_RULE,
    SUGGESTION_EVIDENCE_RULE,
    PLAYER_ACTION_EXECUTION_RULE,
    PLAYER_ACTION_LANGUAGE_RULE,
    PLAYER_ACTION_PROJECTION_RULE,
    PLAYER_ACTION_PROJECTION_SUMMARY,
    SCENE_ENTRY_STATE_RULE,
    HISTORY_EVIDENCE_RULE,
    BRIDGE_DELIVERY_REFERENCE_RULE,
    history_evidence,
    history_lookup_note,
    current_scene_history_index,
    current_scene_records,
    pending_transition_performance,
    pending_transition_record,
    recorded_silent_choice,
    recorded_transition_offer,
    project_contract_boundaries,
    prompt_data_block,
    prompt_data_block_payload,
    render_prompt_context,
    scene_narrative_focus,
    scene_narrative_summary,
    scene_opening_text,
    public_entry_state,
    public_player_fact_evidence,
    silent_exit_allowed,
    transition_delivery_references,
)
from .numeric_v2_fixed_narration import ENTRY_TRIGGER_ACTION_RULE, actor_note, add_entry, displayed_ids, has_pending_entry
from .numeric_v2_identity import numeric_v2_authoring_names
from .numeric_v2_actor_output import (
    NumericV2ActorError,
    NumericV2ActorOutputError,
    NumericV2ActorUnavailableError,
    _parse_actor_suggestions,
    _parse_output,
    _sentence_units,
    _text_is_covered,
)
from .numeric_v2_performance import (
    content_blocks,
    is_current_scene_speech,
    performance_content_blocks,
    performance_fingerprint,
    scene_narration_text,
    transition_source_dialogue_policy,
)
from .numeric_v2_runtime import (
    PLAYER_ADDRESS_BOUNDARY_CHARS,
    current_visit_started_revision,
    NumericV2Engine,
    ScriptSessionV2,
    TurnOutcomeV2,
)
from config.prompts.prompts_theater import NUMERIC_V2_PENDING_OFFER_PROMPT_LABEL as _PENDING_OFFER_PROMPT_LABEL


NUMERIC_V2_ACTOR_TIMEOUT_SECONDS = 35.0
NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS = 1000
NUMERIC_V2_ACTOR_OPENING_MAX_OUTPUT_TOKENS = 1200
NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS = 1700
NUMERIC_V2_ACTOR_SUGGESTION_FILL_MAX_OUTPUT_TOKENS = 260
NUMERIC_V2_ACTOR_SLOW_CALL_SECONDS = 15.0
NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_BLOCKS = 2
NUMERIC_V2_PREVIOUS_SCENE_TAIL_MAX_TOKENS = 80
_RESTRICTED_KNOWLEDGE_SCOPE = (
    "仅可读取 current_story_beat、recent_context、player_input 与 acting_context 明确允许的公开信息；"
    "其中人物陈述与可观察状态按各自来源核对，不把推测变成确定知识；"
    "其余身份、历史和关系未知。"
)
_ACTOR_IDENTITY_RULE = (
    "catgirl和player只是角色槽位，不能从catgirl或player槽位推断性别；"
    "姓名、人格与性别按对应明确设定，不把槽位名或缺名指代当姓名自报。"
)
_STYLE_ONLY_PERSONA_FIELDS = THEATER_STYLE_PERSONA_FIELDS
_PERSONA_TONE_FIELDS = frozenset({"性格", "核心特质", "核心特点"})
def _output_schema_instruction(phase: str, *, single_ending_response: bool = False,
                               optional_ending_target: bool = False,
                               entry_narration: bool = False,
                               entry_trigger_required: bool = False,
                               fixed_entry_only: bool = False,
                               ending_fact_candidates: bool = False,
                               combined_evaluation: bool = False,
                               transition_tail_only: bool = False,
                               entry_part: str = "") -> str:
    """Send only the current call's output shape, excluding transition protocols from ordinary turns."""  # noqa: DOCSTRING_CJK

    entry_target_rule = (
        "本次目标入幕有程序原文：target_scene_narration可空，不复制原文；"
        "target_performance仅交付作者开场要求、原文尚未承担的新回应；optional或forbidden且原文已完整交付开场时填空，等玩家回应后再表演。"
        "纯环境原文不替代作者要求的角色发问；required仍须交付原文未承担的必要对白。"
    )
    if phase == "opening":
        shape = (
            "顶层字段必须包含 scene_narration:string、performance:string、"
            "suggested_inputs:string[]、transition_offered:boolean。开场通常将 transition_offered 设为 false。"
        )
    elif phase == "transition_compact":
        shape = (
            (("顶层字段为" if ending_fact_candidates else "顶层仅输出")
             + "source_scene_narration:string、response_phase:string、performance:string、"
             "bridge_scene_narration:string、target_scene_narration:string、suggested_inputs:string[]。"
             "最多写一份performance；response_phase用source_response或target_opening定位必要的新回应。"
             "没有待回应内容、旁白已完整收束时用none且performance为空，不为填字段补动作或旧幕反应。"
             "下文source_performance与target_performance指这两个播放位置，择一对应performance，不输出这两个顶层字段。"
             if single_ending_response else
             "顶层字段按播放顺序生成：source_scene_narration:string、source_performance:string、"
             "bridge_scene_narration:string、target_scene_narration:string、"
             + ("target_entry_trigger:string、" if entry_trigger_required else "")
             + "target_performance:string、suggested_inputs:string[]。")
            +
            # Source segments may contain external responses, keeping NPC replies out of neko performance and transition bridges.
            "本轮直接询问在场 NPC 时，在 source_scene_narration 给出他的具体答复、明确拒绝或说明未知；用‘角色名回答：“具体内容”’标明主体。"
            "无待答问题或必要外部动作时填空字符串。"
            +
            ("尚需交付的最后互动放在source_response，只有结局新反应时选target_opening；两处均无新回应则选none。"
             if single_ending_response else "source_performance 必须提供；没有尚需交付的来源回应时填空字符串。")
            + (entry_target_rule if entry_narration else "target_scene_narration 必须非空。"
               if single_ending_response else
               "target_scene_narration 必须非空；目标无独立角色动作或回应时，target_performance 可空。"
               if optional_ending_target else "target_performance 与 target_scene_narration 必须非空。")
            +
            "transition.bridge_required 为 false 且无独立时空变化或必要结果时，bridge_scene_narration 填空字符串；"
            "为 true 时必须交付必要桥段，不为凑过场补写动作。"
            "performance 只写你的表演；桥段和目标旁白只写场景事实。"
            + ("" if ending_fact_candidates else "只填写此处列出的转场字段。")
        )
    elif phase == "suggestion_fill":
        shape = "顶层字段必须且只能是 suggested_inputs:string[]。"
    elif phase == "transition_suggestion_fill":
        shape = (
            "顶层字段必须且只能是 accept_input:string、alternative_inputs:string[]；"
            "accept_input 是玩家明确接受并亲自执行正文转场提议的输入，"
            "alternative_inputs 是 1—2 条拒绝、暂缓或当前幕替代行动。"
        )
    elif phase == "reinvitation":
        shape = (
            "顶层必须且只能包含 performance:string、transition_offered:boolean。"
            "performance 只交付你提出的未来邀请，设置 transition_offered=true；"
            "不得输出 scene_update、fact_candidates 或其他状态生成字段，也不得输出 suggested_inputs。"
        )
    else:
        shape = (
            "按 scene_update（如需）、performance、transition_offer、suggested_inputs、fact_candidates（如需）的顺序生成。"
            "顶层必须包含 performance:string、transition_offer:string、suggested_inputs:string[]；"
            "独立外部变化与当前幕已出现 NPC 的动作或回应，写入 scene_update:string；没有则省略。"
            "按player_input辨明受话人：在场NPC的问答、接物、拒绝或缺项在scene_update明写姓名或身份与具体回应，"
            "不能以神情或你的代答替代；NPC仍受自身权限和前提约束。performance 的说话人仍是角色设定中的你。"
            "如果输入提供结构化幕完成事实，顶层必须包含 fact_candidates:object[]"
            + ("。" if combined_evaluation else "；没有本轮新确认事实时必须填空数组。")
            + "输入未提供结构化幕完成事实时，省略 fact_candidates。"
            "scene_update只交付本幕已有前因的实际变化，不把邀约当作接受、离开或抵达。"
        )
    if fixed_entry_only:
        # Author-marked route: the program delivers the whole target entry, so the model
        # only owns source response, source narration and the bridge.
        shape = (
            "本次目标入幕由程序原文完整交付。顶层必须包含以下六个字段："
            "source_scene_narration:string、source_performance:string、bridge_scene_narration:string、"
            "target_scene_narration:string、target_performance:string、suggested_inputs:string[]。"
            "只生成来源段和桥段；target_scene_narration 与 target_performance 必须为空字符串，suggested_inputs 必须为空数组。"
        )
    if ending_fact_candidates:
        shape += "另有必填顶层字段 fact_candidates:object[]，具体证据要求见下方来源段合同。"
    if transition_tail_only:
        shape = (
            "顶层必须且只能输出 bridge_scene_narration:string、target_scene_narration:string、"
            + ("target_entry_trigger:string、" if entry_trigger_required else "")
            + "target_performance:string、suggested_inputs:string[]。"
            "transition.reviewed_source 是本轮已经演出并审核的来源段，尚未持久提交；只承接其后果，"
            "不重新生成、改写或重复其中的对白和动作。只生成获准桥段与目标开场。"
            + ("目标入幕由程序原文交付，两个target字段必须为空字符串，推荐为空数组。" if fixed_entry_only else "")
            + (entry_target_rule if entry_narration and not fixed_entry_only else "")
        )
    if entry_part == "prefix":
        shape = (
            "顶层必须且只能输出 bridge_scene_narration:string、target_scene_narration:string。"
            if transition_tail_only else
            "顶层必须输出 source_scene_narration:string、source_performance:string、"
            "bridge_scene_narration:string、target_scene_narration:string。"
        ) + "只交付程序入幕原文之前的部分；目标表演与玩家推荐由后续同轮调用生成，本次不输出。"
        if ending_fact_candidates:
            shape += "另有必填顶层字段 fact_candidates:object[]，具体证据要求见下方来源段合同。"
    elif entry_part == "response":
        shape = "顶层必须且只能输出 target_performance:string、suggested_inputs:string[]。"
    if entry_trigger_required:
        shape += "target_entry_trigger没有需要补足的合法非语言触发动作时填空字符串，不为填字段补造前因。"
    return NUMERIC_V2_ACTOR_JSON_INSTRUCTION + shape
logger = logging.getLogger(__name__)


def _staged_fact_prompt_fields(claim: Mapping[str, Any], cast: NumericV2CastProjection,
                               *, visible: bool) -> dict[str, Any]:
    if claim.get("validated_only"):
        fields = {"validated_this_turn": True, "validated_value": claim["value"]}
    else:
        fields = {"approved_this_turn": True, "approved_value": claim["value"]}
    if visible:
        fields["evidence"] = [
            {"source": item["source"], "quote_ref": "player_input"} if "quote_ref" in item
            else {"source": item["source"], "quote": (
                item["quote"] if item["source"] == "committed_player_input" else cast.text(item["quote"])
            )}
            for item in claim.get("evidence", ())
        ]
    return fields


def _completion_fact_prompt_context(
    engine: NumericV2Engine,
    node: Mapping[str, Any],
    outcome: TurnOutcomeV2,
    *,
    cast: NumericV2CastProjection,
    approved_facts: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any] | None:
    """Keep unfinished targets and registered values without inventing occurrence provenance."""  # noqa: DOCSTRING_CJK

    contract = node.get("completion_contract")
    if not isinstance(contract, Mapping):
        return None
    facts = outcome.session.story_state.get("facts")
    if not isinstance(facts, Mapping):
        facts = {}
    # 预审与磁盘提交分开：仅接收 Workflow 独立批准的结果，不能把所有暂存候选当历史。
    # 本视图不写 Session，整轮失败时原存档仍可原子回滚。
    approved = {claim["key"]: claim for claim in approved_facts
                if claim.get("op") == "set"}
    requirements: list[dict[str, Any]] = []
    all_satisfied = True
    narration_requirements = [row["fixed_narration_id"] for row in contract.get("all") or []
                             if isinstance(row, Mapping) and "fixed_narration_id" in row]
    narration_delivered = 0
    unavailable_narrations = 0
    if narration_requirements:
        displayed = displayed_ids(outcome.session)
        narration_delivered = sum((node["id"], piece_id) in displayed for piece_id in narration_requirements)
        all_satisfied = narration_delivered == len(narration_requirements)
        if outcome.ledger_event.get("condition_narrations_enabled") is False:
            unavailable_narrations = sum(
                piece["id"] in narration_requirements and piece["trigger"]["type"] == "condition"
                and (node["id"], piece["id"]) not in displayed
                for piece in node["story_beat"].get("fixed_narrations", ())
            )
    for requirement in contract.get("all") or []:
        if not isinstance(requirement, Mapping):
            continue
        key = str(requirement.get("key") or "")
        definition = engine.fact_contract.get(key)
        if not isinstance(definition, Mapping):
            continue
        fact = facts.get(key)
        approval = approved.get(key)
        satisfied = (
            isinstance(fact, Mapping)
            and fact.get("value") == requirement.get("equals")
        )
        row = {
            "key": key,
            "equals": requirement.get("equals"),
            "value_type": definition.get("value_type"),
            "visibility": definition.get("visibility"),
            "committed": key in facts,
            "satisfied": (approval.get("value") == requirement.get("equals")
                          if approval is not None else satisfied),
            **({"evidence_source": definition["evidence_source"]} if "evidence_source" in definition else {}),
        }
        if approval is not None:
            # 普通视图原本含 public/story 的值；不因补记扩大 story 原文的知情范围。
            row.update(_staged_fact_prompt_fields(approval, cast, visible=definition.get("visibility") == "public"))
        if not row["satisfied"]:
            row["description"] = cast.text(definition.get("description", ""))
            if "evidence_anchor" in definition:
                row["evidence_anchor"] = definition["evidence_anchor"]
        elif satisfied:
            # Runtime revisions record when a value was written. Evaluator may
            # backfill a previous performance, so that round's body cannot be
            # attached as proof of who acted, when, or how the result occurred.
            # Keep the value; normal history remains the source for those details.
            revision = fact.get("updated_revision", fact.get("source_revision"))
            if type(revision) is int:
                row["registration_revision"] = revision
        if isinstance(fact, Mapping) and "value" in fact:
            row["committed_value"] = fact["value"]
        requirements.append(row)
        all_satisfied = all_satisfied and row["satisfied"]
    return (
        {
            "status": "satisfied" if all_satisfied else "pending",
            "all": requirements,
            **({"fixed_narrations": {"required": len(narration_requirements), "displayed": narration_delivered,
                                     **({"unavailable": unavailable_narrations} if unavailable_narrations else {})}}
               if narration_requirements else {}),
        }
        if requirements or narration_requirements
        else None
    )


def _route_fact_prompt_context(
    engine: NumericV2Engine,
    node_id: str,
    session: ScriptSessionV2,
    *,
    cast: NumericV2CastProjection,
    approved_facts: tuple[Mapping[str, Any], ...] = (),
    completion_evidence_keys: frozenset[str] = frozenset(),
    completion_anchor_keys: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Keep alternative exit facts separate from the act's mandatory completion targets."""  # noqa: DOCSTRING_CJK

    committed = session.story_state.get("facts") or {}
    approved = {claim["key"]: claim for claim in approved_facts if claim.get("op") == "set"}
    groups: dict[str, dict[str, Any]] = {}
    defined_fields: dict[str, set[str]] = {}
    for target in engine.scene_fact_targets(node_id):
        if "route_id" not in target:
            continue
        key, value = target["key"], target["value"]
        definition = engine.fact_contract[key]
        fact, approval = committed.get(key), approved.get(key)
        satisfied = (isinstance(fact, Mapping) and type(fact.get("value")) is type(value)
                     and fact["value"] == value)
        approved_match = (isinstance(approval, Mapping) and type(approval.get("value")) is type(value)
                          and approval["value"] == value)
        row = {"key": key, "value": value, "value_type": definition["value_type"],
               "visibility": definition["visibility"], "committed": key in committed,
               "satisfied": approved_match if approval is not None else satisfied,
               **({"evidence_source": definition["evidence_source"]} if "evidence_source" in definition else {})}
        if not row["satisfied"]:
            row["description"] = cast.text(definition.get("description", ""))
            if "evidence_anchor" in definition and key not in completion_anchor_keys:
                row["evidence_anchor"] = definition["evidence_anchor"]
        if isinstance(fact, Mapping) and "value" in fact:
            row["committed_value"] = fact["value"]
        if satisfied:
            revision = fact.get("updated_revision", fact.get("source_revision"))
            if type(revision) is int:
                row["registration_revision"] = revision
        if approval is not None and (key not in defined_fields if approval.get("validated_only") else approved_match):
            # 预审结果尚未落盘；只交接该批获准原证据，不从本轮待生成稿补历史。
            row.update(_staged_fact_prompt_fields(approval, cast, visible=key not in completion_evidence_keys))
        fields = defined_fields.setdefault(key, set())
        for name in ("value_type", "visibility", "evidence_source", "description", "evidence_anchor"):
            if name not in row:
                continue
            if name in fields:
                row.pop(name)
            else:
                fields.add(name)
        group = groups.setdefault(target["route_id"], {"route_id": target["route_id"],
            "condition_mode": target["condition_mode"], "facts": []})
        group["facts"].append(row)
    return list(groups.values())

_RELATIONSHIP_STAGES = ("stranger", "guarded", "cooperative", "trusted", "intimate")
_RELATIONSHIP_STAGE_LABELS = {
    "陌生": "stranger",
    "戒备": "guarded",
    "合作": "cooperative",
    "信赖": "trusted",
    "亲密": "intimate",
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


def _capability_labels(
    engine: NumericV2Engine,
    metrics: Mapping[str, int],
) -> list[str]:
    labels: list[str] = []
    for metric_id, definition in engine.metric_schema.items():
        if definition.get("relationship_effect", "none") != "none":
            continue
        for band in definition.get("bands") or ():
            if int(band["min"]) <= int(metrics[metric_id]) <= int(band["max"]):
                label = str(band["label"])
                if label and label not in labels:
                    labels.append(label)
                break
    return labels


_CAPABILITY_LABEL_RULE = "capability_state 的标签只提示演出倾向，不证明能力权限、玩家选择、实际行动或尚未公开的选项。"


def _relationship_metric_stage(
    definition: Mapping[str, Any],
    value: int,
) -> str:
    """Project author-defined relationship metric directions into uniform stages independently of metric names."""  # noqa: DOCSTRING_CJK

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
    """Merge metric stages with scene caps, exposing actionable relationship boundaries only."""  # noqa: DOCSTRING_CJK

    beat = node.get("story_beat", {})
    structured_ceiling = str(beat.get("relationship_ceiling") or "")
    if structured_ceiling in _RELATIONSHIP_STAGES:
        # New packages directly obey structured limits.
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
    metric_ceiling = "intimate"
    for metric_id, definition in engine.metric_schema.items():
        effect = str(definition.get("relationship_effect") or "none")
        if effect not in {"positive", "negative"}:
            continue
        stage = _relationship_metric_stage(definition, int(metrics[metric_id]))
        if _RELATIONSHIP_STAGES.index(stage) < _RELATIONSHIP_STAGES.index(metric_ceiling):
            metric_ceiling = stage
    effective_stage = min(
        (metric_ceiling, scene_ceiling),
        key=_RELATIONSHIP_STAGES.index,
    )
    # 调用方只消费最终上限及对应规则，不构造随后会被投影丢弃的中间表和行为副本。
    return {
        "effective_stage": effective_stage,
        "response_contract": _RELATIONSHIP_RESPONSE_CONTRACTS[effective_stage],
    }


def _story_context_for_actor(
    cast: NumericV2CastProjection,
    story: Mapping[str, Any],
    *,
    beats: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Project author intro through cognition contracts, hiding backstage facts during unknown stages."""  # noqa: DOCSTRING_CJK

    contracts = tuple(_acting_contract_for_actor(cast, beat) for beat in beats)
    if any(_acting_contract_restricts_knowledge(contract) for contract in contracts):
        return {"knowledge_scope": _RESTRICTED_KNOWLEDGE_SCOPE}

    intro = cast.intro(story)
    return {
        "background": str(intro.get("background") or ""),
        "player_identity": str(intro.get("player_identity") or ""),
    }


def _project_player_address(player_address: str, *, known: bool) -> str:
    """When names are unknown, expose second-person address only, never configured nicknames."""  # noqa: DOCSTRING_CJK

    if known:
        return str(player_address or "你").strip() or "你"
    return "你"


_PLAYER_AUTHORED_PERFORMANCE_KEYS = frozenset({
    "suggested_inputs",
    "accept_input",
    "alternative_inputs",
})


def _assert_no_unknown_player_address_leak(
    performance: Mapping[str, Any],
    *,
    player_address: str,
    player_address_known: bool,
    player_input: str = "",
) -> None:
    """Unknown stages allow repetition only of complete nicknames exactly disclosed by the player this turn."""  # noqa: DOCSTRING_CJK

    configured_address = str(player_address or "").strip()
    if (
        player_address_known
        or not configured_address
        or configured_address in {"你", "男主"}
        or configured_address in str(player_input or "")
    ):
        return
    # Recommended input is player dialogue, not neko forms of address; inspect neko text/narration only.
    # Reuse Runtime disclosure boundaries, adding mixed-narrative action parentheses and quotation/tone punctuation,
    # so （抬头）哥哥， counts as direct address while 小哥哥/你哥哥 do not.
    boundary = PLAYER_ADDRESS_BOUNDARY_CHARS + r"（）()\[\]【】\"'“”‘’「」『』？?、…～~—"
    pattern = re.compile(
        rf"(?:^|(?<=[{boundary}])){re.escape(configured_address)}(?=$|[{boundary}])",
        flags=re.IGNORECASE,
    )

    def speech_texts(value: Any, key: str = "") -> list[str]:
        if key in _PLAYER_AUTHORED_PERFORMANCE_KEYS:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            return [text for k, v in value.items() for text in speech_texts(v, str(k))]
        if isinstance(value, (list, tuple)):
            return [text for item in value for text in speech_texts(item)]
        return []

    if any(pattern.search(text) for text in speech_texts(performance)):
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
    # Plot identity establishes circumstances. Full persona may shape attention,
    # judgment and the character's own response, but grants no story abilities or
    # player authorization; style_only and relationship ceilings remain stricter.
    # New relationship changes affect performance only next turn, avoiding sudden guarded-to-intimate shifts within
    # one reply after crossing bands. Ability metrics still use settled state.
    relationship_control = _relationship_control(
        engine,
        node,
        relationship_metrics if relationship_metrics is not None else metrics,
    )
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
    # Transition calls generate both source response and target opening; if either side is amnesiac, hide shared backstage facts,
    # preventing source-known information leaking through target_acting_contract. story_context already supplies knowledge scope once.
    if target is None:
        if labels := _capability_labels(engine, metrics):
            context["capability_state"] = labels
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
            # Review sees these same author permissions. Keep their full conditions
            # for Actor too; permissions are options, never tasks or proof of results.
            if value not in ("", [], {})
        }
    context["relationship_control"] = relationship_control
    if target is not None:
        if target_contract:
            context["target_acting_contract"] = {
                key: value
                for key, value in target_contract.items()
                # Source and target permissions stay separate: entering the next
                # phase must not authorize its actions in the source response.
                if value not in ("", [], {})
            }
        if target_character_state:
            context["target_character_state"] = target_character_state
        context["target_dialogue_policy"] = target_dialogue_policy
        context["target_relationship_control"] = target_control
    return context


def _author_entry_situation(acting_context: Mapping[str, Any]) -> str:
    """Keep only unique legacy staging, preserving excerpts embedded in other sentences."""

    current_scene_state = str(acting_context.get("current_scene_state") or "").strip()
    character_state = acting_context.get("character_state")
    if isinstance(character_state, Mapping):
        states = [text for key in ("catgirl_state", "player_state", "environment_state")
                  if (text := str(character_state.get(key) or "").strip())]
        if current_scene_state in states:
            return ""
        # Workshop prepends all three states in this order. Only remove the
        # exact compiled prefix, never search the author's prose for a sentence.
        if len(states) > 1:
            prefix = "".join(text if text[-1] in "。！？" else text + "。" for text in states)
            if current_scene_state.startswith(prefix):
                return current_scene_state[len(prefix):].lstrip()
    return current_scene_state


def _author_entry_prompt_text(acting_context: Mapping[str, Any]) -> str:
    """Render author staging separately from the character identity and persona."""

    entry_state_lines: list[str] = []
    current_scene_state = _author_entry_situation(acting_context)
    character_state = acting_context.get("character_state")
    if current_scene_state:
        entry_state_lines.append(f"作者入幕规划中的处境：{current_scene_state}")

    if isinstance(character_state, Mapping):
        catgirl_state = str(character_state.get("catgirl_state") or "").strip()
        if catgirl_state:
            entry_state_lines.append(f"作者入幕规划中的角色状态：{catgirl_state}")
        player_state = str(character_state.get("player_state") or "").strip()
        if player_state:
            entry_state_lines.append(f"作者入幕规划中的玩家处境：{player_state}")
        environment_state = str(character_state.get("environment_state") or "").strip()
        if character_state.get("temporal_context"):
            entry_state_lines.append("作者入幕时序规划：" + json.dumps(character_state["temporal_context"], ensure_ascii=False))
        if environment_state:
            entry_state_lines.append(f"作者入幕规划中的环境：{environment_state}")
        # Structured opening/transition prompts already carry this cast-projected
        # author plan. Retain it in ordinary role text without treating it as
        # committed history or bypassing the existing identity/knowledge gates.
        continuity = character_state.get("continuity_from_previous") or []
        if continuity:
            entry_state_lines.append(
                "作者入幕延续规划（须核对实际开场、已提交历史与本轮授权，"
                "不是已发生事实、任务或新增权限）：\n" + "\n".join(continuity)
            )
    if entry_state_lines:
        entry_state_lines.append(
            "这些规划须与实际开场及已提交历史核对；动态变化承接 story_so_far 已提交事实，不能复位；"
            "作者身份、能力、认知/记忆限制与硬边界持续有效。"
        )
    return "\n".join(entry_state_lines)


def _role_prompt_text(
    *,
    catgirl_name: str,
    acting_context: Mapping[str, Any],
    story_context: Mapping[str, Any] | None = None,
) -> str:
    """Render identity, persona and continuing limits without author staging."""

    lines = [f"你是：{catgirl_name}。"]
    # Accept stable premises already filtered by cognition contracts, never adding backstage identities/future plot to character knowledge.
    for key, label in (("player_identity", "玩家身份"),
                       ("knowledge_scope", "认知范围")):
        value = str((story_context or {}).get(key) or "").strip()
        if value:
            lines.append(f"{label}：{value}")
    story_identity = str(acting_context.get("story_identity") or "").strip()
    if story_identity:
        lines.append(f"剧本身份：{story_identity}")
    story_role_context = str(acting_context.get("story_role_context") or "").strip()
    if story_role_context:
        lines.append(f"剧本中的职责与背景：{story_role_context}")
    core_persona = str(acting_context.get("core_persona") or "").strip()
    if core_persona:
        lines.append(f"性格与说话方式：{core_persona}")
    capability_state = acting_context.get("capability_state")
    if capability_state:
        lines.append("非关系演绎参考（不证明权限、选择或行动）：" + "；".join(capability_state))
    acting_contract = acting_context.get("acting_contract")
    if isinstance(acting_contract, Mapping):
        allowed_behaviors = [str(item).strip() for item in acting_contract.get("allowed_behaviors") or []
                             if str(item).strip()]
        if allowed_behaviors:
            # Ordinary turns consume natural role text rather than the raw contract.
            # Preserve author conditions without turning a permission into a checklist.
            lines.append("你可自主选择的行为（可选权限，不是待办或已完成事实；"
                         "仍受当前认知、关系、禁令和实际前提限制）：" + "；".join(allowed_behaviors))
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
    """Project legacy blocks into mixed performance text for new prompts."""  # noqa: DOCSTRING_CJK

    parts: list[str] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        # Legacy Session ordinary narration already meant parenthesized micro-actions; new records explicitly use action.
        parts.append(f"（{text}）" if block.get("type") in {"action", "narration"} else text)
    return "".join(parts)


def _prompt_container(container: Mapping[str, Any], *, phase: str) -> dict[str, str]:
    """Project old/new Sessions as scene narration plus mixed text, avoiding block protocols in prompts."""  # noqa: DOCSTRING_CJK

    if "scene_narration" in container or "performance" in container:
        result = {}
        scene_narration = scene_narration_text(container)
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
    """Extract a little actual visible content from committed source-response endings."""  # noqa: DOCSTRING_CJK

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
            # Keep dialogue endings and action beginnings, preserving final tone without half an action result.
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
    """Transition records retain bridges/target openings; first turns additionally project limited visible old-scene aftermath."""  # noqa: DOCSTRING_CJK

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
            original_source = next((part for part in record.get("segments", [])
                                    if isinstance(part, Mapping)
                                    and part.get("phase") == "source_response"), {})
            # _history_row has renamed originals to fixed_narration_before/after;
            # content_blocks cannot read those prompt-only fields and would lose
            # delivered source text. Read its captured original so display order,
            # echo handling and saved name bindings survive the existing tail cap.
            # Keep the old projection for all other sources, including legacy
            # content whose dialogue blocks were already combined for prompts.
            tail_source = (original_source if original_source.get("fixed_narrations")
                           and ("performance" in original_source or "scene_narration" in original_source)
                           else segment)
            tail = _source_response_tail(tail_source)
            if tail:
                # Synthetic content exists only in this prompt, never writes Session, and carries neither old player input nor full prior-scene responses.
                projected_segments.append({
                    "phase": "previous_scene_tail",
                    "performance": tail,
                })
    return {
        **row,
        # Player input belongs to the previous scene; current scenes inherit only short epilogues, transition facts, and target openings.
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
    """Retain only this visit's continuous complete suffix, dropping earlier whole turns when budgets require."""  # noqa: DOCSTRING_CJK

    budget = max(0, int(max_tokens))
    opening = {
        "phase": "opening",
        "revision": 0,
        "player_input": "",
        **_prompt_container(session.opening_performance, phase="opening"),
    }
    current_node_id = str(session.current_node_id)
    # Actor/Evaluator share current-node backtracking so models see identical scene boundaries.
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
        # Trim tiers from the earliest complete record only, never splitting turns or transition segments.
        rows = rows[-max(1, min(2, int(max_turns)) if optimized_turn_enabled() else int(max_turns)):]
    selected: list[dict[str, Any]] = []
    for row in reversed(rows):
        candidate = [row, *selected]
        if _json_tokens(candidate) <= budget:
            selected = candidate
            continue
        # If an earlier turn does not fit, stop backfilling older records too, avoiding temporal gaps.
        break
    if not selected and rows:
        # Latest turns directly support current responses and must never disappear silently; final total-budget validation reports explicit errors.
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
    """Render one real historical record as narrative understandable to player and neko."""  # noqa: DOCSTRING_CJK

    parts: list[str] = []
    player_input = str(row.get("player_input") or "").strip()
    if player_input:
        parts.append(f"玩家 | {player_input}")
    segments = row.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            scene_narration = str(segment.get("scene_narration") or "").strip()
            performance = str(segment.get("performance") or "").strip()
            if segment.get("phase") == "previous_scene_tail" and performance:
                parts.append(
                    "上一幕尾声（只承接你当时可见的情绪和姿态，不作为当前任务）："
                    f"{performance}"
                )
                continue
            if scene_narration:
                parts.append(f"场景／NPC | {scene_narration}")
            if segment.get("fixed_narration_before"):
                parts.append("已展示的作者原文 | " + segment["fixed_narration_before"])
            if performance:
                parts.append(f"角色（你） | {performance}")
            if segment.get("fixed_narration_after"):
                parts.append("已展示的作者原文 | " + segment["fixed_narration_after"])
    else:
        scene_narration = str(row.get("scene_narration") or "").strip()
        performance = str(row.get("performance") or "").strip()
        if scene_narration:
            parts.append(f"场景／NPC | {scene_narration}")
        if row.get("fixed_narration_before"):
            parts.append("已展示的作者原文 | " + row["fixed_narration_before"])
        if performance:
            parts.append(f"角色（你） | {performance}")
        if row.get("fixed_narration_after"):
            parts.append("已展示的作者原文 | " + row["fixed_narration_after"])
    return "\n".join(parts)


def _story_so_far_text(history_rows: Sequence[Mapping[str, Any]]) -> str:
    """Render the current scene's complete committed history for Actor."""  # noqa: DOCSTRING_CJK

    return "\n\n".join(
        rendered
        for row in history_rows
        if (rendered := _story_so_far_row_text(row))
    )


def _staged_history_evidence_ref(history: Sequence[Mapping[str, Any]], quote: str, *, source: str) -> str:
    """Reference one whole source field that still appears in the packed current-visit history."""

    matches = []
    fields = {"scene_narration": "场景／NPC", "performance": "角色（你）",
              "fixed_narration_before": "之前的作者", "fixed_narration_after": "之后的作者"}
    visible_history = [row for row in history if _story_so_far_row_text(row)]
    for distance, row in enumerate(reversed(visible_history), 1):
        if source == "committed_player_input":
            if str(row.get("player_input") or "").strip() == quote:
                matches.append(f"story_so_far 倒数第{distance}个完整回合的玩家原话")
            continue
        parts = row.get("segments")
        for part in parts if isinstance(parts, list) else [row]:
            if not isinstance(part, Mapping) or part.get("phase") in {"previous_scene_tail", "transition_bridge"}:
                continue
            matches.extend(
                f"story_so_far 倒数第{distance}个完整回合的{label}原文"
                for field, label in fields.items() if str(part.get(field) or "").strip() == quote
            )
    return matches[0] if len(matches) == 1 else ""


def _current_scene_fact_index(
    session: ScriptSessionV2,
    *,
    query: str = "",
    max_tokens: int = 900,
    within_prompt: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any] | None:
    """Pack complete committed originals, never treating a request prefix as its result."""  # noqa: DOCSTRING_CJK

    # Keep structured provenance until final packing: a rendered string cannot
    # safely identify originals already carried by another evidence attachment.
    return current_scene_history_index(session, query, max_tokens=max_tokens,
                                       within_prompt=within_prompt)




def _player_input_repeats_recent_context(
    player_input: str,
    recent_context: list[Mapping[str, Any]],
) -> bool:
    """Detect repeated recent player wording so Actor follows latest state rather than reverting old turns."""  # noqa: DOCSTRING_CJK

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


def _repeats_player_dialogue(
    performance: Mapping[str, Any], player_input: str, *, route_changed: bool = False,
) -> bool:
    """Reject whole player utterances copied into speech or narration, not shared actions or brief acknowledgments."""
    def dialogue(text: str) -> str:
        return "".join(char for block in performance_content_blocks({"performance": text})
                       if block["type"] == "dialogue" for char in block["text"] if char.isalnum())

    spoken = dialogue(player_input)
    literal_input = "".join(char for char in player_input if char.isalnum())
    input_types = {block["type"] for block in performance_content_blocks({"performance": player_input})}
    # A copied mixed turn can have short speech but a substantial player action.
    # Keep brief reciprocal acknowledgments and shared action-only turns legal.
    # A formal transition can replay a concrete player action together with a
    # one-word acceptance. Keep the ordinary-turn allowance for shared actions.
    mixed_copy_min_length = 10 if route_changed else 16
    whole_mixed_copy = len(literal_input) >= mixed_copy_min_length and {"action", "dialogue"} <= input_types
    if len(spoken) < 10 and not whole_mixed_copy:
        return False
    containers = performance.get("segments")
    input_blocks = [(block["type"], "".join(block["text"].split()))
                    for block in performance_content_blocks({"performance": player_input})]
    for part in containers if isinstance(containers, list) else [performance]:
        if isinstance(part, Mapping):
            for field in ("performance", "scene_narration"):
                text = str(part.get(field) or "")
                if ((len(spoken) >= 10 and dialogue(text) == spoken)
                        or (whole_mixed_copy and "".join(char for char in text if char.isalnum()) == literal_input)):
                    return True
                blocks = [(block["type"], "".join(block["text"].split()))
                          for block in performance_content_blocks({"performance": text})]
                if input_blocks and blocks[:len(input_blocks)] == input_blocks:
                    return True
    return False


def _is_repeated_performance(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> bool:
    """Block copied dialogue and globally similar previous-round paraphrases without mistaking catchphrases for entire repeated turns."""  # noqa: DOCSTRING_CJK

    current_blocks = performance_content_blocks(performance)
    previous_blocks = performance_content_blocks(previous)
    previous_narration = {block["text"] for block in previous_blocks if block["type"] == "narration"}
    if any(block["type"] == "narration" and block["text"] not in previous_narration
           and is_current_scene_speech(block["text"], block["text"]) for block in current_blocks):
        return False
    current_dialogue = [block["text"] for block in current_blocks if block["type"] == "dialogue"]
    previous_dialogue = {
        block["text"]
        for block in previous_blocks
            if block["type"] == "dialogue"
    }
    if not current_dialogue:
        return False
    if optimized_turn_enabled():
        from .numeric_v2_repetition import repetition_overlap
        overlap = repetition_overlap(performance, previous)
        if overlap["current_chars"] >= 80 and overlap["overlap"] >= 0.8:
            return True
    dialogue_text = "".join("".join(text.split()) for text in current_dialogue)
    if (
        all(text in previous_dialogue for text in current_dialogue)
        and (len(current_dialogue) >= 2 or len(dialogue_text) >= 12)
    ):
        # Actions may vary by input, but never reuse whole informative dialogue groups unchanged as supposedly new replies.
        return True
    previous_dialogue_text = "".join(
        "".join(str(block.get("text") or "").split())
        for block in previous_blocks
        if block.get("type") == "dialogue"
    )
    # Avoid broad semantic-similarity guesses; block only main-content verbatim reuse of long previous-turn passages.
    # Catch changed-openings plus repeated threats/reminders while allowing short catchphrases that sustain persona.
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
    # Identical complete dialogue remains the final deterministic safeguard.
    return (
        len(dialogue_text) >= 12
        and dialogue_text == previous_dialogue_text
    )


def _performance_variants(performance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Split historical transitions into comparable source/target performances so bridges cannot dilute repetition rates."""  # noqa: DOCSTRING_CJK

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
    """Across Sessions, block highly similar narrative only, avoiding false positives from common short phrases."""  # noqa: DOCSTRING_CJK

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


def _is_short_stable_dialogue(performance: Mapping[str, Any]) -> bool:
    """Recognize brief unchanged-scene confirmations, avoiding mechanical-repetition classifications for closing lines such as see-you-tomorrow."""  # noqa: DOCSTRING_CJK

    blocks = performance_content_blocks(performance)
    if not blocks or any(block.get("type") != "dialogue" for block in blocks):
        return False
    dialogue = "".join(
        "".join(str(block.get("text") or "").split())
        for block in blocks
    )
    return bool(dialogue) and len(dialogue) <= 16


def _can_defer_dialogue_repetition(performance: Mapping[str, Any]) -> bool:
    """Only wording can be deferred; actions, narration, facts and offers retain their guards."""
    blocks = performance_content_blocks(performance)
    return bool(blocks) and all(block.get("type") == "dialogue" for block in blocks) and not any(
        performance.get(key) for key in ("segments", "fact_candidates", "transition_offered")
    ) and count_tokens(_performance_text(performance)) <= 80  # 沿用既有短回应口径。


def _timeline_visit_id(record: Mapping[str, Any]) -> str:
    """Read Runtime scene-visit IDs; return empty for legacy history lacking projections."""  # noqa: DOCSTRING_CJK

    projection = record.get("timeline_projection")
    scope = projection.get("scene_scope") if isinstance(projection, Mapping) else None
    return str(scope.get("visit_id") or "") if isinstance(scope, Mapping) else ""


def _repeats_earlier_session_performance(
    performance: Mapping[str, Any],
    session: ScriptSessionV2,
    *,
    route_changed: bool,
) -> bool:
    """Check openings and older turns; existing lower-threshold rules still cover the most recent turn."""  # noqa: DOCSTRING_CJK

    if not session.performance_history:
        return False
    current_variants = _performance_variants(performance)
    if route_changed and current_variants:
        current_variants = current_variants[:1]
    current_visit_id = (
        f"{session.current_node_id}:r"
        f"{current_visit_started_revision(session)}"
    )
    earlier: list[Mapping[str, Any]] = []
    # Opening text belongs only to the first scene visit; later scenes must not use its farewells/catchphrases to classify current repetition.
    if current_visit_id.endswith(":r0"):
        earlier.append(session.opening_performance)
    for record in session.performance_history[:-1]:
        visit_id = _timeline_visit_id(record)
        if visit_id:
            if visit_id == current_visit_id:
                earlier.append(record)
            continue
        # Legacy Sessions without timeline projections retain same-node history only, avoiding cross-scene false positives.
        from_node_id = str(record.get("from_node_id") or "")
        to_node_id = str(record.get("to_node_id") or "")
        if session.current_node_id in {from_node_id, to_node_id}:
            earlier.append(record)
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
    """Compare transition source responses only, preventing new-scene text masking repeated prior dialogue."""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if not isinstance(segments, list) or not segments or not isinstance(segments[0], Mapping):
        return False
    source_response = segments[0]
    if not isinstance(source_response.get("performance"), str):
        return False
    source_text = "".join(source_response["performance"].split())
    # Match the existing target guard: a long copied action remains a replay
    # even when its dialogue is too short for the ordinary speech threshold.
    return any(
        _is_repeated_performance({"performance": source_response["performance"]}, variant)
        or (len(source_text) >= 24 and source_text == "".join(
            str(variant.get("performance") or "").split()))
        for variant in _performance_variants(previous)
    )


def _stale_transition_narration(performance: Mapping[str, Any], session: ScriptSessionV2,
                                *, initial_node_id: str, target_node_id: str,
                                target_is_ending: bool = False) -> str:
    """Reject complete long paragraphs copied from a different scene or route."""
    current = {part.get('phase'): str(part.get('scene_narration') or '').strip()
               for part in performance.get('segments', []) if isinstance(part, Mapping)}
    openings = [(initial_node_id, scene_narration_text(session.opening_performance))]
    bridges = []
    for row in session.performance_history:
        pair = (row.get('from_node_id'), row.get('to_node_id'))
        if not all(isinstance(node_id, str) and node_id for node_id in pair):
            continue
        for part in row.get('segments') or ():
            if not isinstance(part, Mapping):
                continue
            if part.get('phase') == 'target_opening':
                openings.append((pair[1], scene_narration_text(part)))
            elif part.get('phase') == 'transition_bridge':
                bridges.append((pair, scene_narration_text(part)))
    for phase, sources, identity in (
        ('target_opening', openings, target_node_id),
        ('transition_bridge', bridges, (session.current_node_id, target_node_id)),
    ):
        text = current.get(phase, '')
        # Use the existing 80-token short-body boundary; brief common scenery,
        # partial matches and another traversal of the same route remain legal.
        if count_tokens(text) > 80 and any(
            previous_id != identity and ''.join(text.split()) == ''.join(previous.split())
            for previous_id, previous in sources
        ):
            return 'transition_target_narration' if phase == 'target_opening' else 'transition_bridge_narration'
        if phase == 'target_opening' and target_is_ending:
            units = [''.join(unit.split()) for unit in _sentence_units(text)]
            for previous_id, previous in sources:
                if previous_id == identity:
                    continue
                old_units = [''.join(unit.split()) for unit in _sentence_units(previous)]
                matches = difflib.SequenceMatcher(None, units, old_units, autojunk=False).get_matching_blocks()
                if any(count_tokens(''.join(units[match.a:match.a + match.size])) > 80 for match in matches):
                    return 'transition_target_narration'
    return ''


def _replays_entry_original(performance: Mapping[str, Any]) -> bool:
    """Reject substantial entry originals, passages or quoted lines repeated verbatim."""
    # Compare quote styles equivalently without rewriting either stored or generated text.
    quote_forms = str.maketrans({mark: '"' for mark in "'“”‘’「」『』"})
    for part in performance.get("segments") or (performance,):
        for piece in part.get("fixed_narrations") or ():
            if piece.get("position") != "before":
                continue
            original = "".join(str(piece.get("text") or "").split())
            quoted_lines = [line for line in re.findall(r"“([^“”]{20,})”", original)
                            if sum(char.isalnum() for char in line) >= 20]
            old_units = ["".join(unit.split()).translate(quote_forms) for unit in _sentence_units(original)]
            original = original.translate(quote_forms)
            for field in ("scene_narration", "performance"):
                text = scene_narration_text(part) if field == "scene_narration" else str(part.get(field) or "")
                normalized = "".join(text.split()).translate(quote_forms)
                if len(original) >= 20 and original in normalized:
                    return True
                if any(line.translate(quote_forms) in normalized for line in quoted_lines):
                    return True
                if field == "performance":
                    dialogue = "".join(block["text"] for block in performance_content_blocks(
                        {"performance": text}) if block["type"] == "dialogue")
                    if count_tokens(dialogue) > 80 and "".join(dialogue.split()).translate(quote_forms) in original:
                        return True
                units = ["".join(unit.split()).translate(quote_forms) for unit in _sentence_units(text)]
                matches = difflib.SequenceMatcher(None, units, old_units, autojunk=False).get_matching_blocks()
                if any(count_tokens("".join(units[match.a:match.a + match.size])) > 80 for match in matches):
                    return True
    return False


def _soft_pacing(
    node: Mapping[str, Any],
    current_turn: int,
    *,
    transition_intent: str = "unclear",
    actor_can_invite: bool = True,
) -> dict[str, Any]:
    # 推荐回合只影响软节奏，不授权收束或转场。
    raw_budget = node.get("recommended_turns")
    recommended_turns = (
        int(raw_budget)
        if isinstance(raw_budget, int) and not isinstance(raw_budget, bool)
        else 4
    )
    turns_remaining = max(recommended_turns - current_turn, 0)
    if transition_intent == "reject":
        # After refusal, do not return to old proposals or repeatedly urge scene_complete; respond to the player's current choice.
        phase = "after_reject"
        instruction = (
            "玩家拒绝或暂停了上一提议：回应当前意图并留在本幕，不再重复催促。"
            "留幕不冻结当前因果；玩家等候已开始的过程时，可概括时间经过，交付本幕配角自己的答复、拒绝或过程结果，"
            "按玩家实际授权和作者边界承接结果，不把预测或临时补出的手续变成必做条件。"
            "结果仍未知时保留可行选择，不能只反复劝等、重做准备或另加传话任务。"
        )
    elif current_turn > recommended_turns:
        phase = "overdue"
        instruction = (
            "当前幕已超过推荐展开长度：先回应玩家，再推动当前核心因果发生一次可见变化。"
            "已获授权且没有新风险或选择的连续过程，概括过去并交付结果。"
            "由在场角色自主给出该给的答复或结果，普通手续随结果收束，不再要求玩家确认。"
            + ("结果已经成立且自然出口成熟时，提出基于已发生事实的具体未来行动。"
               if actor_can_invite else "已有结果时自然收住本幕互动。")
            + "不得新增支线或补齐可选内容来延长。"
        )
    elif turns_remaining == 0:
        phase = "closure"
        instruction = (
            "已到推荐回合：这只是软节奏参考，不是必须提议或换幕的倒计时。回应玩家并开始自然收束当前话题；"
            + ("已有自然出口时可以提出具体下一步，否则继续交付当前行动的可见结果。"
               if actor_can_invite else "继续交付当前行动的可见结果。")
            + "尚未出现的内容建议和道具可以直接舍弃，不要为了补齐它们延后转场，也不要新开任务链。"
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
        instruction = (
            "专注回应玩家并交付当前行动的结果；推荐回合不是最低篇幅，不为填满回合拆分连续动作或延后已成熟的出口。"
            "内容建议只提供灵感，不必按顺序执行。"
        )
    return {
        "recommended_turns": recommended_turns,
        "current_turn": current_turn,
        "phase": phase,
        "instruction": instruction,
    }


def _beat_for_actor(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
    *,
    include_opening_only_boundaries: bool = False,
) -> dict[str, Any]:
    """v2.2 sends opening visuals, full natural direction, and hard boundaries only, excluding targets/evidence from Actor."""  # noqa: DOCSTRING_CJK

    projected = cast.value(beat)
    return {
        "opening_scene": scene_opening_text(projected),
        # Plot direction is director information rather than character knowledge; restricted cognition limits asserted facts,
        # not Actor's understanding of event order after formal transitions, needed to avoid premature recommendation spoilers.
        "scene_direction": str(
            projected.get("narrative_summary")
            or projected.get("summary")
            or scene_narrative_focus(projected)
            or ""
        ),
        # Transition Actor must retain source/target hard boundaries; consumers previously read
        # a field projections never supplied, allowing target-opening recommendations to spoil later events.
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
    """Project explicit author cognition/expression permissions only, never infer activation state from natural language."""  # noqa: DOCSTRING_CJK

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
    """Project current-node author state; return empty when undeclared."""  # noqa: DOCSTRING_CJK

    raw = public_entry_state(beat)
    if not raw:
        return {}
    projected = cast.value(raw)
    return {
        **({"temporal_context": projected["temporal_context"]} if "temporal_context" in projected else {}),
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
    """Incomplete cognition/memory must not convert author backstage settings into character knowledge."""  # noqa: DOCSTRING_CJK

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
    """Restricted-cognition nodes omit chapter titles whose places/events could become presumed character knowledge."""  # noqa: DOCSTRING_CJK

    contract = _acting_contract_for_actor(cast, node.get("story_beat") or {})
    if _acting_contract_restricts_knowledge(contract):
        return ""
    return cast.text(node.get("chapter"))


def _profile_field(line: str) -> tuple[str, str]:
    """Use the same explicit labels as the bound-card and memory reader."""  # noqa: DOCSTRING_CJK

    return theater_persona_field(line)


def _profile_self_reference_tokens(character_profile: str) -> tuple[str, ...]:
    """Read only explicit persona self-address labels, never infer relationships/emotions from free text."""  # noqa: DOCSTRING_CJK

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
    """Keep personality choices distinct from identity, knowledge and consent.

    full can retain existing card behavior/preferences as tendencies; style_only
    keeps its established expressive scope. Neither turns real-world biography,
    relationships or a favorite activity into a fact in the author's world.
    """  # noqa: DOCSTRING_CJK

    # Keep explicit field continuations together, including unknown fields that
    # must be rejected as a whole. The reader separates independent facts with
    # blank lines, so a legacy unlabelled supplement keeps expressive-only scope.
    parsed: list[tuple[str, str]] = []
    continuing_field = False
    for line in str(character_profile or "").splitlines():
        if not line.strip():
            continuing_field = False
            continue
        field_name = _profile_field(line)[0]
        if not field_name and continuing_field:
            previous_field, previous_text = parsed[-1]
            parsed[-1] = (previous_field, previous_text + "\n" + line)
        else:
            parsed.append((field_name, line))
            continuing_field = bool(field_name)
    full = acting_contract.get("persona_scope") != "style_only"
    allowed = _STYLE_ONLY_PERSONA_FIELDS | (THEATER_BEHAVIOR_PERSONA_FIELDS if full else frozenset())
    selected: list[str] = []
    has_character_traits = False
    for field_name, line in parsed:
        if not (
            field_name in allowed
            or not field_name
        ):
            continue
        if (
            acting_contract.get("self_reference_mode") == "system_neutral"
            and field_name == "自称"
        ):
            continue
        if full and field_name in _PERSONA_TONE_FIELDS | THEATER_BEHAVIOR_PERSONA_FIELDS:
            # Do not guess which words are safe actions. Preserve the explicit
            # trait and give it a bounded role, under world/knowledge/consent rules.
            selected.append(line)
            has_character_traits = True
        elif field_name in _PERSONA_TONE_FIELDS or not field_name:
            # Unstructured legacy prose has no reliable field ownership; retain
            # only the expressive fallback rather than promote it to world facts.
            raw_value = _profile_field(line)[1]
            selected.append(
                "语言氛围参考（只影响措辞，不授权肢体接触、亲昵称呼、依赖或既有关系）："
                + (raw_value if field_name else line)
            )
        else:
            selected.append(line)
    if has_character_traits:
        selected.insert(0,
            "人格倾向参考（影响关注、判断与自主反应；姓名和人设不证明剧中身世或技术来源，"
            "不授权新增能力、知识、玩家行动或既有关系；行动服从当前权限与认知边界）：")
    return "\n".join(selected).strip()


def actor_visible_profile(character_profile: str) -> str:
    """Return every persona line any Actor prompt or output check can consume.

    Actor prompts only see ``_profile_for_acting_contract`` projections and the
    output check only reads the self-reference field. An empty contract keeps
    the widest projection, so other persona facts (for example relationship
    notes promoted by background memory work) can change without invalidating
    a turn that never saw them.
    """

    return _profile_for_acting_contract(character_profile, {})


def _assert_acting_contract_output(
    performance: Mapping[str, Any],
    *,
    character_profile: str,
    acting_contract: Mapping[str, Any],
) -> None:
    """Protect only contract-forbidden card self-address; forbid semantic regex guesses and output rewriting."""  # noqa: DOCSTRING_CJK

    if acting_contract.get("self_reference_mode") != "system_neutral":
        return
    output = json.dumps(performance, ensure_ascii=False, separators=(",", ":"))
    if any(token in output for token in _profile_self_reference_tokens(character_profile)):
        raise NumericV2ActorOutputError("numeric_v2_actor_acting_contract_violation")


def _deduplicate_scene_update(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove environmental narration already appearing sentence-for-sentence in the immediately prior turn, retaining actual new changes."""  # noqa: DOCSTRING_CJK

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


def _trim_entry_narration_echo(target_segment: dict[str, Any]) -> None:
    """Normalize exact original echoes in a newly generated target draft."""  # noqa: DOCSTRING_CJK

    narration = str(target_segment.get("scene_narration") or "")
    for piece in target_segment.get("fixed_narrations", []):
        if piece["position"] != "before":
            continue
        text = piece["text"]
        if narration.startswith(text) and (rest := narration[len(text):].strip()):
            narration = rest
        if narration.endswith(text) and (rest := narration[:-len(text)].strip()):
            narration = rest
    target_segment["scene_narration"] = narration
    performance = str(target_segment.get("performance") or "")
    remainder = performance
    originals = [piece for piece in target_segment.get("fixed_narrations", []) if piece["position"] == "before"]
    for piece in originals:
        prefix = piece["text"] + "\n"
        if remainder.startswith(prefix):
            remainder = remainder[len(prefix):].strip()
    if (remainder != performance and any(
            block["type"] == "dialogue" and block["text"] not in {piece["text"].strip() for piece in originals}
            for block in performance_content_blocks({"performance": remainder}))
            and not _replays_entry_original({"performance": remainder, "fixed_narrations": originals})):
        target_segment["performance"] = remainder


def _deduplicate_transition_bridge_prefix(
    performance: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Trim an exact bridge prefix only when it repeats the previous narration's complete, ordered suffix."""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if not isinstance(segments, list) or len(segments) != 3:
        return dict(performance)
    previous_segments = previous.get("segments")
    if isinstance(previous_segments, list):
        previous = next(
            (part for part in reversed(previous_segments) if part.get("scene_narration")),
            {},
        )
    previous_narration = str(previous.get("scene_narration") or "")
    # Quoted or parenthetical text may describe a memory or hypothetical event, not an action already played.
    if any(char in previous_narration for char in "“”‘’「」『』\"'（）()[]{}【】"):
        return dict(performance)
    previous_units = _sentence_units(previous_narration)
    bridge = str(segments[1].get("scene_narration") or "")
    offset = 0
    matched_units = []
    for unit in _sentence_units(bridge):
        if unit not in previous_units or unit[-1] not in "。！？!?":
            break
        matched_units.append(unit)
        offset = bridge.index(unit, offset) + len(unit)
    remainder = bridge[offset:].strip()
    if not offset or not remainder:
        return dict(performance)
    if matched_units != previous_units[-len(matched_units):]:
        return dict(performance)
    # The shared splitter ends at punctuation inside quotes too; never leave a closing delimiter behind.
    prefix = bridge[:offset]
    if any(
        prefix.count(left) != prefix.count(right)
        for left, right in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ("（", "）"), ("(", ")"))
    ) or prefix.count('"') % 2:
        return dict(performance)
    sanitized = dict(performance)
    sanitized["segments"] = list(segments)
    sanitized["segments"][1] = {**segments[1], "scene_narration": remainder}
    trace_event(
        "actor.transition_bridge_prefix_deduplicated",
        removed_sentences=len(_sentence_units(prefix)),
        removed_characters=offset,
    )
    return sanitized


def _opening_beat_for_actor(
    cast: NumericV2CastProjection,
    node: Mapping[str, Any],
) -> dict[str, Any]:
    """Openings show current visuals only, never author targets as to-do/delivery instructions."""  # noqa: DOCSTRING_CJK

    # Full scene direction serves ordinary Actor after the player's first input; public opening establishes opening_scene only.
    # Otherwise models compress later identity/ability/crisis outcomes prematurely into the first sentence.
    return {
        "opening_scene": scene_opening_text(cast.value(node["story_beat"])),
        "boundaries": _suggestion_hard_boundaries(
            cast,
            node["story_beat"],
            include_opening_only=True,
        ),
    }




def _next_scene_preview_for_actor(
    engine: NumericV2Engine,
    cast: NumericV2CastProjection,
    source: Mapping[str, Any],
    metrics: Mapping[str, int],
    *,
    facts: Mapping[str, Mapping[str, Any]] | None = None,
    departure_blocker: str | None = None,
) -> dict[str, Any]:
    """Project selected-exit reasons/themes/movement scope, without target plot/opening reads."""  # noqa: DOCSTRING_CJK

    if departure_blocker is not None:
        return {"status": "departure_pending"}
    if not source.get("route_gates"):
        return {"status": "none"}
    # Share Guard's read-only routing for this round: multiple exits provide feasible direction without freezing invitations.
    # Runtime reroutes using actual metrics on acceptance; hidden values and unselected contracts stay out of Actor prompts.
    route = engine.preview_route(str(source["id"]), metrics, facts=facts)
    if route is None:
        return {"status": "runtime_unresolved"}
    target_id = str(route["target_node_id"])
    target = engine.nodes[target_id]
    contract = route.get("transition_contract") if isinstance(route, Mapping) else None
    transition = dict(contract) if isinstance(contract, Mapping) else {}
    target_is_ending = target.get("type") == "ending" or target.get("terminal") is True
    authored_offer = "" if target_is_ending else cast.text(str(transition.get("fallback_offer") or "")).strip()
    return {
        "status": ("completion_continuation" if transition.get("advance_on_completion") is True
                   else "after_acceptance_only"),
        "chapter_title": _chapter_title_for_actor(cast, target),
        # Ending nodes may stay at the current location; mark that deterministically as future direction,
        # allowing closing actions like starting records/continuing companionship rather than inventing departure.
        "target_is_ending": target_is_ending,
        "transition_direction": cast.text(str(transition.get("reason") or "")),
        # Source reasons may say only continue exploring while bridges specify destinations. Project existing movement contracts,
        # never letting ordinary Actor rehearse future target interactions from summaries/openings. Endings retain closing summaries.
        **({"program_silent": True} if silent_exit_allowed(source)
           and isinstance(transition.get("silent_choice"), Mapping)
           and not transition.get("fallback_offer") else {}),
        "entry_movement": cast.text(str(transition.get("bridge_scene_narration") or ""))
        if not target_is_ending else "",
        **({"authored_offer": authored_offer} if authored_offer else {}),
    }


def _next_scene_summary_text(preview: Mapping[str, Any], *, authored_offer: str = "") -> str:
    """Summarize next-scene direction only; explicitly preserve unknown when routing is undecided."""  # noqa: DOCSTRING_CJK

    status = str(preview.get("status") or "")
    if status == "departure_pending":
        return "本轮没有可执行的跨幕安排。"
    if status == "completion_continuation":
        return (
            "本出口承接已经作出的决定。交付本幕动作和属于本幕的结果；下一幕的后果由程序衔接。"
            "不提出转场邀约，不要求玩家再次确认已执行的行动，也不提前演出下一幕的结果。"
        )
    if status == "after_acceptance_only":
        # Ordinary turns get one direction sentence so complete next-scene summaries cannot steal current focus or leak facts.
        direction = str(preview.get("transition_direction") or "").strip()
        if bool(preview.get("target_is_ending")):
            # Target ending summaries can include time jumps, unique places, and final state; ordinary turns retain source-route reasons only,
            # preventing premature cabins/routines/final outcomes enacted merely to fit endings.
            # Final scenes may end naturally without extra invitations; actual completion still requires Runtime authorization.
            suffix = f"作者收束条件（仅说明何时可收束，不证明条件已成立）：{direction}" if direction else ""
            return (
                "下一阶段是结局余韵。当前仍在本幕，只演前因已具备的角色或环境回应。"
                "下一步需要玩家作出选择或亲自操作时，留给玩家。"
                "是否进入结局由程序决定。不为结束追加邀请、劳动或下次活动，"
                "也不得提前描写结局独有的地点、时间推进、生活状态或最终结果。"
                f"{suffix}"
            )
        entry_movement = str(preview.get("entry_movement") or "").strip()
        entry_hint = (
            f"作者预期的出口衔接范围（不是已发生事实，也不否定实际历史）：{entry_movement}\n"
            "按实际历史承接其中已发生的部分，不重新执行；邀请只覆盖仍未发生的下一阶段，获准后才推进。"
            "具备必要前因并提出邀请时，须说明这个范围中的去向、时段和活动，不能只凭主题或含糊理由另造目的地。"
            "来源理由列出的其他地点若与实际移动不符，不把它们作为本出口可兑现的选择。"
            "仅在已公开路径支持时承接途中经过，不临时编造捷径、停业或玩家同意来解释换错地方。"
            if entry_movement else ""
        )
        if preview.get("program_silent"):
            return (
                "本出口由程序在完成条件满足后提供静默选择。"
                "当前只承接本幕已发生的结果和本轮回应，不生成邀请或提前演出下一幕。"
            )
        offer_hint = (
            f"当前出口可用的作者邀约原文（只作未来提议，保留其中的同行分工、地点和活动）：{authored_offer}\n"
            if authored_offer and authored_offer not in direction and authored_offer not in entry_movement else ""
        )
        if direction:
            return (
                f"{entry_hint}{offer_hint}本出口的来源前因与后续方向（不表示已经满足或已有邀请）：{direction}\n"
                "这是作者希望的因果衔接方向，不是目标清单或固定动作；可以使用 story_so_far 已自然建立的语义等价方案，"
                # Equivalence permits history-adapted wording only, never unrelated everyday destinations replacing actual exits.
                "等价方案须保持该方向的时间、地点和下一互动阶段；不能为回避转场而另造去向或当前追加任务。"
                "但不能凭空补出尚未发生的关键事实，也不能用换场桥段代替当前幕必要的因果。"
                # Next-step questions need complete acceptable arrangements; preserve author timing so future interactions cannot become present and prevent transition.
                # Route reasons often describe expected after-acquisition conditions, never proof of acquisition authorizing immediate directions.
                "玩家问下一步时，先对照当前幕方向与实际历史：离开所依赖的关键结果尚未成立，"
                "就回应眼前尚在发生的因果，不把结果后的去向说成现在即可出发的安排；"
                "必要结果已经成立且同一安排尚未公开，再说明这条方向支持的时间、地点与行动，停在等待其选择的位置。"
                "方向指定未来时点的互动，应邀请届时开始，不能改成现在先讨论或实施该互动；"
                "实际历史已经做过的动作只承接结果，不重做。"
            )
        return f"{entry_hint}{offer_hint}必要前因成立且玩家接受合法邀请后才进入下一幕；当前回合不能提前写成已经抵达。"
    if status == "runtime_unresolved":
        # Preserve unknown when no exit conditions hold; never invent an eligible route.
        return "当前没有可用出口；留在本幕交付已授权的结果，出口由程序结算。"
    return "暂未提供下一幕方向；继续留在当前幕回应玩家。"


def _actor_can_offer_transition(preview: Mapping[str, Any], dialogue_policy: str) -> bool:
    """Keep new spoken offers out of program-owned exits and natural endings."""

    return (preview.get("status") == "after_acceptance_only"
            and dialogue_policy != "forbidden"
            and not preview.get("program_silent")
            and not preview.get("target_is_ending"))


def _allows_local_suggestions_before_ending(
    engine: NumericV2Engine, source: Mapping[str, Any], dialogue_policy: str,
) -> bool:
    """Allow an active climax to offer local replies while its authored ending facts settle."""

    routes = source.get("route_gates", ())
    if dialogue_policy == "forbidden" or (silent_exit_allowed(source) and any(
        isinstance(gate["transition_contract"].get("silent_choice"), Mapping)
        and not gate["transition_contract"].get("fallback_offer") for gate in routes
    )):
        return False
    return any(
        (target.get("type") == "ending" or target.get("terminal") is True)
        and any(condition["type"] == "fact_equals"
                for condition in gate["conditions"].get("any", gate["conditions"].get("all", ())))
        for gate in routes
        if (target := engine.nodes.get(gate["target_node_id"])) is not None
    )


def _suggestion_source_text(performance: Mapping[str, Any]) -> str:
    """Compress generated narrative into visible recommendation context without internal state."""  # noqa: DOCSTRING_CJK

    # Post-transition recommendations respond only to the target opening last seen by the player. Including source responses/bridges
    # encourages repeating old-scene departures instead of addressing new-scene questions/choices.
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
            return _suggestion_source_text(target_segment)
    if "target_performance" in performance or "target_scene_narration" in performance:
        return "\n".join(
            str(performance.get(key) or "").strip()
            for key in ("target_scene_narration", "target_performance")
            if str(performance.get(key) or "").strip()
        )

    if performance.get("fixed_narrations"):
        # When code inserts originals, supply actual playback order and speaker identities to recommendation completion, without internal IDs.
        return json.dumps(content_blocks(performance), ensure_ascii=False, separators=(",", ":"))

    parts: list[str] = []
    # Ordinary/opening environment and NPC outcomes are visible too; recommendation completion must include those facts.
    for key in ("scene_narration", "performance", "source_performance"):
        value = str(performance.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts)


def _authored_entry_player_inputs(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
) -> list[str] | None:
    """Project compiled author entry choices through the existing player-input parser."""  # noqa: DOCSTRING_CJK

    if "entry_player_inputs" not in beat:
        return None
    projected = cast.value(beat["entry_player_inputs"])
    suggestions = _parse_actor_suggestions(projected)
    trace_event(
        "actor.entry_player_inputs_projected",
        authored_count=len(projected),
        accepted_count=len(suggestions),
    )
    return suggestions


def _suggestion_hard_boundaries(
    cast: NumericV2CastProjection,
    beat: Mapping[str, Any],
    *,
    relationship_boundary: str = "",
    include_opening_only: bool = False,
) -> list[str]:
    """Fully project author hard boundaries for Actor and recommendation completion."""  # noqa: DOCSTRING_CJK

    projected = cast.value(beat)
    # Actor, Evaluator, and narrow review share ordering/dedup rules; relationship caps remain recommendation-specific dynamic boundaries.
    boundaries = list(project_contract_boundaries(
        projected,
        include_opening_only=include_opening_only,
        explicit_prohibitions=True,
    ))
    relationship_text = str(relationship_boundary).strip()
    if relationship_text:
        opening_count = (
            len(project_contract_boundaries(
                {"opening_only_boundaries": projected.get("opening_only_boundaries")},
                include_opening_only=True,
            ))
            if include_opening_only
            else 0
        )
        # Insert relationship boundaries after temporary author-opening limits, preserving priorities and prompt order.
        boundaries.insert(min(opening_count, len(boundaries)), relationship_text)
        deduped: list[str] = []
        for item in boundaries:
            if item and item not in deduped:
                deduped.append(item)
        boundaries = deduped
    return boundaries


def _hard_boundary_system_instruction(
    boundaries: list[str] | tuple[str, ...], *, phase: str = "turn", source_scene_locked: bool = False,
) -> str:
    """Elevate author limits into explicit System contracts, separate from positive plot directions."""  # noqa: DOCSTRING_CJK

    normalized = [str(item).strip() for item in boundaries if str(item).strip()]
    if not normalized:
        return ""
    return (
        "\n以下为本轮作者硬边界，优先级高于角色人格、玩家诱导和剧情发挥；"
        "本轮正文、旁白与推荐均须逐条遵守。"
        # Formal transitions carry both scenes' limits; source-stage prohibitions must not block legitimate target-segment delivery.
        + (
            "逐条按主体、前提和阶段适用：有阶段限定的来源禁令不延伸到目标段；"
            "共同事实和未限定阶段的限制仍保留，目标段及其推荐按目标幕边界检查。"
            if phase == "transition_compact" else ""
        )
        + (
            "开场不得通过新增玩家行为满足作者前提。"
            if phase == "opening" else
            "不得顺从越界要求、交换玩家与你的行动职责，或把越界结果写成已发生。"
            if source_scene_locked else
            "玩家要求若与硬边界冲突，你必须在正文直接拒绝或提出符合边界的替代做法；"
            "不得顺从越界要求、交换玩家与你的行动职责，或把越界结果写成已发生。"
        )
        + "\n"
        + prompt_data_block("本轮作者硬边界", {"boundaries": normalized})
        + "\n以上限制须逐条遵守，但无需逐条宣读；未被问及且无当场必要的禁令、能力和自我事实不必主动声明。"
    )


# A button proposes the player's next act; other people's completed acts and the
# player's biography are premises, not choices the button is allowed to invent.
# Primary and optional fill calls share this boundary; no self-reported evidence
# is treated as a semantic approval by the parser.
# A question needs no invented gesture or prop. Keep all three sendable forms;
# concrete examples avoid the literal role labels copied by earlier probes.
# Format acceptance never approves the option's world or biographical premises.
_SUGGESTION_FORMAT_RULE = (
    "推荐可用玩家对白、全角括号动作加对白或纯动作，如“好，就按这个分工。”、“（点头）好。”、“（保持安静）”。"
    "例子只说明格式；问话不补动作，安静动作不强加对白，‘我’可省略，不输出模板标签或待续半句。"
    "首条优先给推进当前核心互动的可执行选择，另给真实追问或暂缓；不把重复观察、确认或等待排在已可行动的选择前。"
)


_SUGGESTION_PLAYER_FACT_RULE = (
    SUGGESTION_EVIDENCE_RULE
    +
    "推荐由玩家本人说或做，不借角色身份，不预演他人动作或成功结果。"
    "只使用可见正文与公开历史中的工具、能力和许可。"
    "不编造玩家姓名、联系方式、职业、技能、经历、既有关系与行程；未知资料留给玩家输入，不填假名、示例或占位符。"
    "可以提出当下偏好或待商量的新安排，不能把它们写成既定事实。"
    "已明确的一批同类工作可以完整实施，不逐件生成等价按钮；确有新选择或风险时才分开。"
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
    """Make one lightweight request for malformed recommendations, never regenerate narrative."""  # noqa: DOCSTRING_CJK

    transition_instruction = ""
    if transition_offered:
        # With visible transitions, recommendations distinguish acceptance from alternatives so players can advance or explicitly defer.
        transition_instruction = "正文已经提出了具体离幕提议，所有选项围绕这项实际公开的安排。"
    scene_change_instruction = ""
    if after_scene_change:
        scene_change_instruction = (
            "程序已经正式换幕；visible_performance 只包含玩家此刻看到的目标幕开场。"
            "所有推荐必须回应这个新场景刚出现的问题或选择，不得继续回应旧幕输入、旧转场动作或其它已完成事项。"
        )
    system_prompt = (
        "你是 N.E.K.O Numeric v2 的玩家输入推荐补全器。"
        "当前角色的姓名由下方 character_name 给出。你只拟写玩家候选输入，不能替该角色说话或行动。"
        f"{_output_schema_instruction('transition_suggestion_fill' if transition_offered else 'suggestion_fill')}"
        "只根据已经生成的可见正文和玩家本轮输入，给出一至三条真实不同、可直接发送的玩家选择，不能为凑数量补造前提。"
        "不得把 player_input 原样或仅改空白后再次列为推荐；玩家已经做过的同一句不是下一步选择。"
        f"{_SUGGESTION_FORMAT_RULE}"
        "有动作括号时由程序标记为玩家动作，不能把当前角色、NPC、环境或外部结果写成玩家动作的主体。"
        "推荐是玩家对当前回应的下一步反应，不能把当前角色正在做的动作误写成玩家已在做；不混淆双方职责与物品持有者。"
        "只能使用可见正文与玩家输入已经支持的玩家身份、地点、能力、物品和事实；"
        "不得保留方括号占位符。"
        f"{_SUGGESTION_PLAYER_FACT_RULE}"
        "一般状态不授权推荐补出未经支持的具体属性、子类、位置或程度；物品只有在可见正文明确由玩家持有或可直接取得时，才能写成玩家正在使用。"
        "hard_boundaries 是作者硬边界，所有推荐的动作、对白、物品用途和关系距离都必须逐条遵守；"
        "不能因为正文刚刚越界就继续沿用该越界内容。"
        f"{transition_instruction}"
        f"{scene_change_instruction}"
        "不要输出解释、purpose、goal_id、kind 或其他字段。"
    )
    data = {
        "character_name": catgirl_name,
        "visible_performance": _suggestion_source_text(performance),
        # Source responses/bridges already consumed old input after formal transition; resending it pulls recommendations back to the old scene.
        "player_input": "" if after_scene_change else str(player_input or ""),
        "hard_boundaries": list(hard_boundaries),
    }
    return _ensure_actor_messages_fit(
        [
            theater_system_message(system_prompt),
            HumanMessage(content=prompt_data_block("玩家推荐补全数据", data)),
        ],
        max_tokens=max_tokens,
    )


def _transition_contract_for_actor(
    cast: NumericV2CastProjection,
    contract: Mapping[str, Any] | None,
    *,
    target_opening: str,
) -> dict[str, Any]:
    """Runtime delivers target openings; Actor no longer receives duplicate contracts encouraging repetition."""  # noqa: DOCSTRING_CJK

    projected = cast.value(contract or {})
    target_references = _sentence_units(target_opening)
    must_deliver = []
    for item in projected.get("must_deliver") or []:
        text = str(item).strip()
        if text and not _text_is_covered(
            text,
            target_references,
        ):
            must_deliver.append(text)
    return {
        "reason": str(projected.get("reason") or ""),
        "must_deliver": must_deliver,
        "bridge_scene_narration": str(projected.get("bridge_scene_narration") or "").strip(),
        # Independent target-opening facts surviving dedup mean this is not a same-location continuous action eligible for direct visual switching.
        "bridge_required": bool(
            must_deliver or str(projected.get("bridge_scene_narration") or "").strip()
        ),
        "must_preserve": list(projected.get("must_preserve") or []),
        "tone": str(projected.get("tone") or ""),
        # Author-marked program-delivered entry; absent/false keeps every old branch byte-identical.
        "fixed_entry_only": projected.get("fixed_entry_only") is True,
    }


def _uses_single_ending_response(natural_ending: bool, target_dialogue_policy: str, authored_bridge: str) -> bool:
    # An explicit bridge can separate two independent deliveries; an optional
    # target policy permits silence but does not make either delivery redundant.
    return natural_ending and target_dialogue_policy in {"optional", "forbidden"} and not authored_bridge.strip()


# Both opening and continuing calls need positive author permission semantics;
# openings deliberately do not inherit the continuing player's action contract.
_AUTHOR_BEHAVIOR_PERMISSION_RULE = (
    "允许行为是条件成立后的可选权限，不是已执行事实或逐项任务；"
    "角色可按人格自主选择自己的合法反应，不能据此替玩家作出决定、跳过前因或证明未知结果。"
)


_PLAYER_AUTHORIZATION_RULE = (
    "玩家授权只取本轮player_input、实际历史与已公开邀请，不因角色本稿的新请求扩大范围。"
    "作者开场、摘要与职业身份不授予玩家操作权限。"
    "可补不改变位置、持物、结果或立场的轻微衔接，不补玩家心理、发言、重要操作、选择、承诺或新交接。"
    "玩家已实施的幕内动作从外部回应开始，不重演、不转给你重做；持物与位置承接实际交接，注视不重置持有者。"
) + _AUTHOR_BEHAVIOR_PERMISSION_RULE




# Expression detail applies to the active scene, not an already concluded transition source.
_ACTOR_EXPRESSION_RULE = (
    "从玩家本轮的选择、提问或行动中抓住一个影响当前互动的具体细节，以自然对白或必要反应表达角色的态度；"
    "针对具体对象回应，不复制玩家整句台词，也不每轮夸奖或说明自己的能力和做法。"
    "玩家已明确收束或转向时接住新话头，不再评价旧安排。事实有限时允许简短，不用无关风景或泛泛附和代替关键答复。"
)
_ACTOR_STATE_RULE = (
    # Narration/dialogue once described identical work as fully complete and still unfinished; enforce within-turn state consistency too.
    "正文、旁白和推荐按播放顺序承接实际状态：已经全部完成的同一工作不再要求完成剩余部分；角色失去意识、休眠或失声后不再说话。"
    "时间承接最新实际演出，不按入幕的夜晚、清晨等静态描写倒退；未来约定不表示时间已经过去。"
    "人格影响判断与表达，可保留意见或拒绝，但不证明新危险、手续或资格，不授予关键事实、新任务、关系承诺或行动权限。"
    "本幕已经有完整结果时，接住玩家当前回应并走向已有出口；玩家顺着你的追问作答不等于要求另开调查。"
    "悬念可以留到作者安排的后幕，不必在离幕前把所有未知都解决。"
)
_ACTOR_DRAMATIZATION_RULE = (
    "作者状态只作连续性依据，旁白自然呈现现场，不照抄摘要。写作禁令不改成自我声明；"
    "有当场理由的世界内规则、许可和个人边界照常表达，实际越界仍明确拒绝或给出替代。"
    "作者剧情方向是导演信息，明确的因果先后不能倒置；作者只给出抽象状态或待确认事项时，不得自行具体化。"
) + EVIDENCE_SCOPE_RULE


def _system_prompt(
    *,
    catgirl_name: str,
    player_address_known: bool = True,
    phase: str = "turn",
    single_ending_response: bool = False,
    optional_ending_target: bool = False,
    entry_narration: bool = False,
    entry_trigger_required: bool = False,
    fixed_entry_only: bool = False,
    author_entry_choices: bool = False,
    terminal_transition: bool = False,
    suppress_suggestions: bool = False,
    local_suggestions_only: bool = False,
    ending_fact_candidates: bool = False,
    combined_evaluation: bool = False,
    transition_tail_only: bool = False,
    source_delivery_only: bool = False,
    shared_agreement_delivery: bool = False,
    entry_part: str = "",
) -> str:
    player_address_state_rule = (
        "玩家称呼已确认，正文用“你”或给定称呼。"
        if player_address_known
        else "玩家称呼尚未确认，正文只用“你”，不要猜昵称。"
    )
    if optimized_turn_enabled() and phase == "turn":
        return (
            "1. 按角色设定、认知、关系和对白策略演出；人格不增加事实或权限。" + player_address_state_rule
            + "\n2. 先回应玩家本次输入，从最近已播状态之后继续；不复述上轮或重演已完成动作。"
            "\n3. 玩家尚未实施的行动、发言、心理、选择和反馈留给玩家；仅承接原话真实授权的范围。"
            "\n4. 只演当前公开场景；next_scene仅为未来邀请依据，程序授权前不出发、不交付后幕事件。"
            "\n5. 作者方向与待办不是已发生事实，按历史承接位置、持物、前因和结果，不为推进补造条件。"
            "\n6. 旁白负责环境和NPC，performance负责当前角色；先写独立外部结果，再写角色反应，不重复。"
            "\n7. 合法前因具备时交付本幕尚缺的一项结果；需要玩家操作或选择时停下。"
            "\n8. 推荐只从正文结束状态继续，保留暂缓权；公开可执行的转场提议才标记transition_offered。"
            + ("suggested_inputs填空数组。" if suppress_suggestions or author_entry_choices else
               "只给玩家留在当前可见场景的回应、问题或动作。" if local_suggestions_only else
               "给一至三条可直接发送的玩家输入。")
            + ("本幕补充交付段：source_delivery_task只提示本幕待交付结果，审核通过且出幕条件满足后由程序接续后续请求。"
               "可以不同意或说明尚未确定的部分，不强行达成共识。"
               + ("共识只交付共识，不替玩家同意、不写成已经执行。" if shared_agreement_delivery else "")
               if source_delivery_only else "")
            + _output_schema_instruction(phase, combined_evaluation=combined_evaluation)
            + "只返回JSON，不输出理由、推理、规则或内部字段说明。"
        )
    if phase == "reinvitation":
        # 控制请求从入口只生成邀请，不继承普通回合的 NPC 回应、结果交付或事实抽取任务。
        # 人设、认知、历史与状态仍由原有六块投影提供；此处不批准任何新动作或换幕。
        return (
            "重新邀请控制合同：用户点击了重新邀请，这是控制请求，不是玩家台词，也不表示玩家已接受。"
            "你只负责以角色设定中的身份，依据真实公开历史与 next_scene 的合法去向，简短提出未来邀请。"
            f"{_output_schema_instruction(phase)}"
            "role 中的完整人格、剧内身份、能力、认知和关系限制持续有效，人格影响关注点与表达方式，不授予新权限。"
            f"{_ACTOR_STATE_RULE}{_ACTOR_DRAMATIZATION_RULE}"
            "current_scene 的入幕设定只作历史背景；幕完成值只读，不重新核验、执行或提交。"
            "当前状态以 story_so_far 的已提交原文为准，不复位位置、持物、分工或已完成结果。"
            "next_scene 只限定接受后的去向、时段和活动，不是已获准演出的剧情。"
            "fallback 与桥段是作者预期去向，不证明其中的步骤尚未发生；根据实际历史表达剩余的合法邀请。"
            "不复演已完成动作，不添加新任务、结果或事实，不演出双方移动到目标、出发或到达。"
            "玩家是否接受由独立的程序控件表达，不生成玩家推荐输入。"
            f"{player_address_state_rule}"
            "你的动作与对白遵守 role 的对白策略；动作括号默认省略主语，只写你自身的动作。"
            "不要提及数值、阈值、路线、节点、系统或提示词，只输出 JSON。"
        )
    entry_cause_rule = (ENTRY_TRIGGER_ACTION_RULE.replace("在目标旁白交付一次", "在target_entry_trigger交付一次")
                        if entry_trigger_required else ENTRY_TRIGGER_ACTION_RULE)
    if phase == "opening":
        phase_structure_rule = (
            "开场：scene_narration 建立场景，performance 演出你的入场回应。只自然建立 current_story_beat.opening_scene，"
            "不要罗列后续内容建议，并留下玩家可回应的话头。"
            + ("入场选项由作者提供，suggested_inputs 填空数组。" if author_entry_choices else
               "suggested_inputs 至少给出 1 条可直接发送的玩家输入，一至三条即可，不能为凑数量补造前提；"
               f"{_SUGGESTION_FORMAT_RULE}"
               "不得预写环境、他人或成功结果。")
        )
    elif phase == "transition_compact":
        phase_structure_rule = (
            "换场顺序：player_input→source_scene_narration→source_performance→bridge_scene_narration→目标段。"
            "来源只收住当前互动，不出现桥段后的时地、到达、醒来或target_scene独有事实。"
            # Closure need not add micro-actions; acknowledge answered questions' meaning without paraphrasing them again.
            "若玩家只接受上一轮已说定的安排，且来源互动已交付完毕，source_performance 填空字符串，直接承接桥段；"
            "不再次邀请、确认或补收尾动作；有新问题或行动结果才回应来源。"
            "桥段只交付来源到目标间的独立时空变化或必要结果，不抢用目标开场画面；target_scene_narration首次建立目标环境/NPC变化。"
            + ("opening_situation 中尚未由程序入幕原文交付的自身动作和对白才交给 target_performance；"
               "target_scene_narration只写原文之前的氛围，target_performance只接原文之后的新反应，"
               "不重演原文已经承担的动作、信息、问题或邀请。"
               if entry_narration else
               "opening_situation 中你自己的动作和对白交给 target_performance，两段共同交付开场，不在旁白预演你的动作再在 performance 重演。")
            +
            "transition.authorized只授权本次转场；accept时按pending_invitation、player_input与recent_context核对原邀请及后续澄清。"
            # User permits all transitions to adapt to history: preserve factual contracts and express completed actions as resulting state.
            "作者桥段与 opening_situation 是时空、必要结果和边界约束，不是必须逐字播放的文字。"
            "按当前transition去向及recent_context、player_input改写旁白，保留必要事实、因果与阶段边界；旧桥段不覆盖本次目的地。"
            "后段承接前段已完成结果，不重做或重分配归属；不覆盖玩家选择或提前完成目标幕互动。"
            "同地收束不虚构时空变化；无独立变化时可空桥段保持空。must_preserve约束连续性，不要求复述或状态摘要。"
            # Dedup must not remove target content entirely; without new messages, brief in-character responses to established results remain valid.
            "目标承接当前话题与开场，不再总结玩家已结束的来源话题。"
            + ("同场自然收束只在source_performance或target_performance一处回应玩家，另一处为空字符串。"
               "尚有最后动作或直接回应时先在来源演完，目标仅用旁白收束；若只剩结局新反应，则来源为空，在目标回应一次。"
               "不凑第二次回应；新地点或独立目标互动仍须交付。"
               if single_ending_response else
               "目标入幕的程序原文独立交付事件、问题或邀请；没有额外场景建立时target_scene_narration填空，"
               "目标对白策略为optional或forbidden且无必要新反应时target_performance填空；不重复原文或替玩家作答。"
               if entry_narration else
               "作者桥段前后的独立角色动作或回应分别在来源段和目标段交付，不合并时点，不重复回应；"
               "目标没有必要的新动作或回应时，target_performance 留空。"
               if optional_ending_target else
               "没有新问题时，target_performance 仍按目标对白策略简短交付非空回应；"
               "target_scene_narration 与 target_performance 仍须非空；不另造动作、任务或规则说明填充篇幅。")
        )
        if entry_narration:
            phase_structure_rule = (
                "换场按来源回应、桥段、目标旁白、"
                + ("target_entry_trigger、" if entry_trigger_required else "")
                + "程序入幕原文、目标表演的顺序播放。"
                "source_scene_narration只交付本轮在场NPC的必要具体答复或独立外部结果；source_performance只回应玩家尚未得到回应的新问题或行动，单纯接受已说定安排时填空，不重复确认。"
                "两处来源回应均在桥段前，不使用目标时地或事件。"
                "桥段依据本次transition及实际历史，交付获准的时空变化和必要结果，不抢演目标事件或重复旧移动。"
                + ("目标旁白建立原文前的环境；必要前因写target_entry_trigger。" if entry_trigger_required else
                   "目标旁白先交付程序原文所需的直接前因，再建立环境。") + entry_cause_rule +
                "原文已经交付的动作、结果、问题和邀请不再由模型复述或换词重演。"
                "没有必要的新场景文字时target_scene_narration填空；目标表演的空值及对白要求按本次输出合同。"
                "作者规划不覆盖实际持物、时间、行动分工和已发生结果，不代玩家选择或同意。"
            )
    else:
        phase_structure_rule = (
            "先完整回应 player_input，再结合 story_so_far 自然延展；"
            "performance 保持简短完整，不为了推进剧本答非所问。"
        )
    # Identity comes from dynamic script context, never implementation names that encourage characters to identify as framework/protocol models.
    if phase == "transition_compact":
        if entry_part == "response":
            return (
                "你只生成本轮程序入幕原文之后尚缺的目标表演和玩家推荐。"
                "你扮演的角色以角色设定中的姓名为准。"
                + _output_schema_instruction(phase, entry_part=entry_part)
                + _ACTOR_STATE_RULE + _ACTOR_DRAMATIZATION_RULE + _PLAYER_AUTHORIZATION_RULE
                + PLAYER_ACTION_LANGUAGE_RULE + PLAYER_ACTION_PROJECTION_RULE + SCENE_ENTRY_STATE_RULE
                + NUMERIC_V2_ACTOR_EVENT_OWNERSHIP_INSTRUCTION
                + "target_performance在允许发声时优先纯对白，通常一至三句；有必要的新动作才用一对括号写一个即时动作。"
                + "transition.entry_prefix.blocks是按实际播放顺序冻结的同轮未提交候选，不是已提交历史，"
                "不构成新授权、已审批事实或story_evidence；本轮仍须整稿审核后一并提交。"
                "只承接其最后状态，不修改、复述或重演前段和原文已承担的动作、结果、问题或邀请；"
                "不再次启动、检查或等待已经播放的事件。"
                "target_scene.performed_entry_ref指向本轮已冻结前段；author_entry_plan只作边界参考，不是待执行清单；"
                "acting_context.acting_contract.allowed_behaviors只说明权限，不构成待办，也不授权玩家行动。"
                "只补原文之后尚缺的必要对白、新反应或后续行动，不能省掉目标合同要求的必要对白。"
                "A漏做的原文前因不得在本段补执行；交由整稿审核修订前段，不在原文之后补启动、准备或等待。"
                "entry_prefix.originals_before_response用block_index指出本段之前的程序原文，仅定位本稿顺序，"
                "不证明已提交或已审批；原文所述事件在本稿前段完整展示后，本段从其结束状态接续。"
                "角色认知、身份、关系与人格范围遵守acting_context的合同；"
                "作者预期状态不证明玩家已执行动作，也不增加未知NPC的成功结果。"
                "target_performance遵守acting_context.dialogue_policy：required含括号外对白，"
                "forbidden只动作，optional两者均可；optional或forbidden且没有必要新反应时可空。"
                + ("同场自然收束只回应玩家一次；前段已有回应时target_performance留空。"
                   if single_ending_response else "")
                + ("suggested_inputs必须为空数组。" if terminal_transition or author_entry_choices else
                   "suggested_inputs给出一至三条可直接发送的玩家输入，只从最终已播放状态继续，"
                   "不推荐重做原文已完成的操作。" + _SUGGESTION_FORMAT_RULE + _SUGGESTION_PLAYER_FACT_RULE)
                + player_address_state_rule + "只输出JSON。"
            )
        event_ownership_rule = (
            "除入幕前因规则允许的非语言必要触发动作外，" if entry_narration and not fixed_entry_only else ""
        ) + NUMERIC_V2_ACTOR_EVENT_OWNERSHIP_INSTRUCTION
        if transition_tail_only:
            if event_ownership_rule.count("只在 performance 中") != 1:
                raise NumericV2ActorOutputError("numeric_v2_actor_prompt_template_invalid")
            event_ownership_rule = event_ownership_rule.replace("只在 performance 中", "只在 target_performance 中")
        if fixed_entry_only:
            # The program owns the target entry, so this call only needs the source-side ordering.
            phase_structure_rule = (
                "source_scene_narration没有必要外部结果或NPC答复时可空；来源旁白与表演均在桥段前。"
                "bridge_scene_narration按本次transition和实际历史承接获准的时空变化及必要结果；"
                "不复制旧桥段，不提前演目标地点、事件、伤势或邀请，也不复述程序原文。"
                "历史已发生的动作只承接结果，不重演、不重分配持物或覆盖玩家选择。"
                "must_preserve约束连续性，不要求逐项复述；同地且无独立变化时，合同允许空桥段便保持空。"
            )
            if not terminal_transition:
                phase_structure_rule += (
                    "玩家只是接受邀约、请继续或请介绍入幕后才公开的内容时，source_performance填空，由程序原文接答。"
                    "以公开邀约和transition.reason核对交付归属；追问已获准的下一阶段内容也归程序原文，即使玩家用了问句。"
                    "只在来源还有独立问题或即时动作必须回应时写来源表演，不提前给出下一幕的答案或问句。"
                )
        # Formal transitions use separate contracts, excluding ordinary-turn not-accepted/no-transition instructions.
        # Four texts share history; Runtime assembles labels only, separating source reactions from completed player actions.
        target_stage_rule = (
            "旁白不替玩家作新选择，不重复表演中的自身动作；"
            + ("目标表演从程序原文结束状态接续，不把作者开场计划重新当作待办。"
               if entry_narration else "目标普通开场停在新互动之前。") +
            "共用开场只交付与已提交历史及前段结果相容的状态；备用方案和道具用途不证明本路已启用。"
            + ("" if entry_trigger_required else
               "符合入幕前因规则的非语言必要触发动作写目标旁白，其余自身动作和对白交给原文之后的target_performance。"
               if entry_narration else "环境/NPC与自身表演分别写目标旁白和target_performance。")
            if not fixed_entry_only else
            "旁白不替玩家作新选择，不重复表演中的自身动作。"
        )
        policy_rule = (
            "source_performance遵守acting_context.dialogue_policy，target_performance遵守target_dialogue_policy："
            "required含括号外对白，forbidden只动作，optional两者均可；空值例外按当前输出合同。"
            if not fixed_entry_only else
            "source_performance遵守acting_context.dialogue_policy：required含括号外对白，forbidden只动作，optional两者均可；空值例外按当前输出合同。"
        )
        if entry_part == "prefix":
            phase_structure_rule = (
                "本次只生成播放器原文之前的来源回应、桥段和目标旁白。"
                "来源旁白只交付本轮在场NPC的必要答复或独立外部结果；来源表演只回应尚未交付的新问题或行动。"
                "单纯接受已说定安排时来源表演填空，不重复确认，也不使用目标时地或事件。"
                "桥段依据本次transition和实际历史交付获准的时空变化及必要结果，不抢演目标事件或重复旧移动。"
                "目标旁白先交付程序原文所需的直接前因，再建立环境。" + ENTRY_TRIGGER_ACTION_RULE +
                "目标作者开场中尚缺的对白、原文之后的新反应和推荐留给后续同轮调用；本次不生成这些字段。"
                "作者规划不覆盖实际持物、时间、行动分工或已发生结果，不代玩家选择或同意。"
            )
            target_stage_rule = (
                "目标旁白不替玩家作新选择，只交付环境或入幕前因规则允许的动作。"
                "作者要求且已获准的必要入幕前因须在本段目标旁白先交付，不能留到原文之后补执行；"
                "缺少授权或必要依赖时不编造，由整稿审核处理。"
            )
            policy_rule = (
                "source_performance遵守acting_context.dialogue_policy：required含括号外对白，"
                "forbidden只动作，optional两者均可；没有尚需交付的来源回应时可空。"
            )
        suggestion_rule = (
            "" if fixed_entry_only or entry_part == "prefix" else
            "suggested_inputs 必须为空数组。" if terminal_transition else
            "入场选项由作者提供，suggested_inputs 填空数组。" if author_entry_choices else
            "非终局的 suggested_inputs 给出一至三条可直接发送的玩家输入。"
            "suggested_inputs 只承接最终可见的目标幕内容（含程序入幕原文）。"
            "来源安排仍待回应且符合目标幕边界时才可延续。"
            f"{_SUGGESTION_FORMAT_RULE}{_SUGGESTION_PLAYER_FACT_RULE}"
        )
        if transition_tail_only:
            return (
                "你只生成本轮已经获准转场的桥段和目标开场。来源回应已审核，保持原文。"
                + _output_schema_instruction(phase, transition_tail_only=True, fixed_entry_only=fixed_entry_only,
                                             entry_narration=entry_narration,
                                             entry_trigger_required=entry_trigger_required,
                                             entry_part=entry_part)
                + _ACTOR_STATE_RULE + _ACTOR_DRAMATIZATION_RULE + _PLAYER_AUTHORIZATION_RULE
                + PLAYER_ACTION_LANGUAGE_RULE + PLAYER_ACTION_PROJECTION_RULE + SCENE_ENTRY_STATE_RULE
                + (entry_cause_rule if entry_narration and not fixed_entry_only else "")
                + event_ownership_rule
                + "承接reviewed_source中的实际位置、持物和约定；目标开场不能反过来证明来源段缺失结果。"
                + (("按桥段、目标旁白、target_entry_trigger、程序入幕原文、目标表演的顺序播放。"
                    if entry_trigger_required else "按桥段、目标旁白、程序入幕原文、目标表演的顺序播放。")
                   if entry_narration and not fixed_entry_only else "")
                + target_stage_rule
                + ("目标对白和推荐留给后续同轮调用，本次不生成。" if entry_part == "prefix" else
                   "target_performance遵守acting_context.target_dialogue_policy：required含对白，"
                   "forbidden只动作，optional两者均可；允许空值的情形按目标合同。")
                + suggestion_rule + player_address_state_rule
                + "桥段只交付获准的必要时空变化，不抢演目标独有内容。只输出JSON。"
            )
        return (
            "你负责生成本次程序已授权的正式转场：按下方合同填写角色回应，在场 NPC 的必要答复写入来源旁白。"
            "你扮演的角色以角色设定中的姓名为准。"
            f"{_output_schema_instruction(phase, single_ending_response=single_ending_response, optional_ending_target=optional_ending_target, entry_narration=entry_narration, entry_trigger_required=entry_trigger_required, fixed_entry_only=fixed_entry_only, ending_fact_candidates=ending_fact_candidates, transition_tail_only=transition_tail_only, entry_part=entry_part)}{phase_structure_rule}"
            f"{_ACTOR_STATE_RULE}{_ACTOR_DRAMATIZATION_RULE}{_PLAYER_AUTHORIZATION_RULE}"
            f"{PLAYER_ACTION_LANGUAGE_RULE}{PLAYER_ACTION_PROJECTION_RULE}{SCENE_ENTRY_STATE_RULE}"
            f"{event_ownership_rule}"
            "performance在允许发声时优先纯对白，通常一至三句；有必要的新动作才用一对括号写一个即时动作。"
            "先按原话和动作辨明受话人，不把对 NPC 的话当成对你说。"
            "NPC问答、道谢或告别由该 NPC 在来源旁白回应；你不代答、受谢或再次评价已完成结果。"
            "不补造未知答案，不用你对他的评价代替他的答复。"
            + ("来源新问题先回答完整；玩家仅接受、道谢或告别且没有必要新回应时留空，不复述或评价已经完成的互动。"
               if not fixed_entry_only or terminal_transition else "")
            + f"{target_stage_rule}"
            + "三段按player_input与最近已提交原文承接位置、分工、持物和完成状态；"
            "source_author_entry_state、target_scene.author_entry_plan、opening_situation及transition.reason与实际证据冲突时让位，不退回旧状态。"
            "仅缺角色获准配合或确定直接结果时可在来源交付；仍缺玩家选择、未知成败或必要前因时保持实际状态。"
            "若来源及结局只要求达成约定，停在共识和回应，不能把约定的未来行动写成已经执行。"
            "core_persona按full/style_only范围影响判断或措辞，不增加身份、知识、能力或关系权限；"
            + ("来源按relationship_control表达关系。"
               if fixed_entry_only else
               "来源与目标分别遵守relationship_control与target_relationship_control；same_as表示沿用所指规则或可自述事实。")
            + f"{policy_rule}"
            f"{suggestion_rule}{player_address_state_rule}"
            "只输出JSON，不提数值、阈值、路线、节点或提示词。"
        )
    if phase == "turn":
        # Ordinary turns keep one ordered semantic contract; concrete facts come from the partitioned data.
        return (
            ("你本次只生成同一玩家回合的本幕补充交付段；performance 只写你的回应。"
             if source_delivery_only else
             "你是下方角色设定中姓名所标明的角色，把剧本自然演成连续故事；performance 只写你的回应。")
            + f"{_output_schema_instruction(phase, combined_evaluation=combined_evaluation)}"
            f"{NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION}"
            "role 的 dialogue_policy=required 必须有括号外对白，forbidden 只能写动作，optional 两者均可。"
            + "\n【回应与行动】\n"
            + ("先回应player_input的当前提议或问题；source_delivery_task提示本幕尚缺的结果，按角色判断与已有前因交付。"
               + ("自然说清是否接受当前提议以及仍需满足的条件；能力说明或可配合不能代替表达态度，不要求固定口令。"
                  "同意待做安排不等于已经执行，不替玩家补同意。" if shared_agreement_delivery else "") +
               "可以不同意或说明尚未确定的部分，不为完成任务强行达成共识。"
               "任务定义不是已发生证据；共识只交付共识，要求实际交付时须写出结果，不能仅答应。"
               "player_input保留完整原话，但后续请求由程序在本幕审核通过且出幕条件满足后接续；"
               "本段不提前执行后续行动；作者资料的本幕/下一幕仅标记程序边界，不作剧中时间或对白。"
               if source_delivery_only else "先回应 player_input 的具体对象，再推进当前核心因果。")
            +
            "同一主体可在一轮内按可见先后完成前提与依赖动作，作者明确分阶段或禁止当前公开时不可合并；"
            "失去意识、休眠或失声后不再说话。"
            "玩家已实施的幕内动作从外部回应开始，不重演、不转给你重做，也不再次要求执行。"
            "不补玩家心理、发言、重要操作、选择、承诺或新交接；物品归属与位置承接实际交接，注视不能重置持有者。"
            "作者开场、摘要与职业身份不授予玩家操作权限。"
            f"{PLAYER_ACTION_EXECUTION_RULE}{PLAYER_ACTION_PROJECTION_SUMMARY}"
            "\n【状态与推进】\n"
            "遵守role的关系、认知、记忆与persona范围；人格影响判断与措辞，不增加身份、知识、能力或关系权限，不因性格、职业习惯或担心新增客观危险、手续或资格条件。"
            "关系上限不是已达关系；实际互动满足契机才有限升温。允许行为是条件成立后的可选权限，不是已执行事实或逐项任务，不能据此替玩家作出决定。"
            "current_scene是作者规划，story_so_far是实际历史；作者禁令不证明未发生，也不撤销历史，next_scene 只允许未来邀请。"
            "位置、持物、时间、分工与完成状态以实际历史为准，已播放开场只承接结果，也不把物件退回操作前；temporal_context 和未来约定均不证明当前已跨日。"
            f""
            "前提须由指定主体公开成立，不以其他主体、沉默或依赖动作代替；未发生内容不作知识、环境事实或完成结果。"
            "关键能力、机制或结果未知时保持未知，不从检查、猜测或问句补造线索、技术、未知路径或成败。"
            "不新增关键设施、权限、密钥或敌情；现象不证明完整过程，未观察到不等于未发生，未操作不等于已测试失败。"
            "传闻、猜测与假设保留来源和不确定，不升为客观事实；有限推断须有已公开世界规则和实际证据，有权限的在场者可首次给出确知答复。"
            "未知或越界明确说明，自己先前说错可以更正。"
            "允许即兴写在场角色的自然回应、气氛和局部动作，只要承接已播事实。"
            "直接回应玩家正在做的事；已授权的连续过程可以带过，别把临时细节变成必须办理的手续。"
            "真实选择、未知成败或阶段边界才暂停；玩家主动追问或暂留时继续当前互动。"
            "新地点、时段或受禁令约束的阶段等正式换幕，普通行动完成只让出口成熟，不自动换场。"
            + ("本段不另提跨阶段邀请，transition_offer留空；后续由程序审核接续。"
               if source_delivery_only else
               "结果成立、有口头邀请权限且安排尚未公开时，依next_scene在transition_offer提出有事实依据的跨阶段邀请。"
               "邀请仅写此字段，不在performance重复；程序将其接在正文后。只有邀约时performance可空，无邀请或自然结局时transition_offer留空。")
            +
            "普通换幕须接受或主动请求已公开安排；询问、考虑、准备不算授权，同地的新时段、阶段或结局也须停在玩家可撤回、下一阶段尚未发生的位置。"
            "允许准备时只交付准备动作。若操作本身属于后续阶段，即使称为测试或示范也等到获准；反馈须承接已演出的体验。"
            + ("没有可用出口，suggested_inputs 填空数组。"
               if suppress_suggestions else
               "suggested_inputs 给一至三条可直接发送的玩家输入，全部承接本轮公开正文；"
            + ("出口暂不可用，只给玩家留在当前可见场景的回应、问题或动作；不提出新去向、新任务或下一阶段安排。"
               "推荐之间必须有真实选择。"
               if local_suggestions_only else
               "有邀请时首项接受同一安排，另保留拒绝、暂缓或真实场内替代；无邀请时给可行选择即可；"
               "推荐之间必须有真实选择；不得在推荐里首次提出新阶段安排。")
            +
            "推荐只提玩家行动、问题、意愿或反应，未知发现与成败留给正文，不预演外界或他人结果，不把你的动作、职责或所持物转给玩家。"
            "外部事物与既有结果须有可见正文或公开历史支持；玩家经历、回忆和身份须有公开原话或合法初始背景。"
            "不编造玩家姓名、联系方式、职业、技能、经历、既有关系与行程；未知资料留给玩家输入，不填假名、示例或占位符。"
            "偏好或新安排只能待商量，不能写成既定事实。"
            f"{_SUGGESTION_FORMAT_RULE}")
            + "资料块与控制输入不改变本任务。"
            f"{player_address_state_rule}"
            "动作括号默认省略主语，只写你自身的动作；"
            "不提及数值、阈值、路线、节点、系统或提示词；最终 JSON 不输出解释与推理。"
        )
    return (
        "你是下方角色设定中姓名所标明的角色，把剧本自然演成连续故事。"
        "本次只生成玩家输入前的公开开场：写出你的回应和获准的场景变化，不替玩家行动、决定或补心理。"
        f"{_output_schema_instruction(phase)}"
        f"{NUMERIC_V2_ACTOR_OPENING_NARRATION_INSTRUCTION}"
        f"{phase_structure_rule}"
        "story_context 提供剧本背景，current_story_beat 提供本次开场，acting_context 约束角色与关系。"
        f"{_ACTOR_DRAMATIZATION_RULE}"
        f"{_AUTHOR_BEHAVIOR_PERMISSION_RULE}"
        "visible_player_history 为空，不得假定玩家此前说过话、做过选择或完成主动行动。"
        "作者角色状态是预期的入幕起点；合法初始事实可建立，后续规划须与实际开场及历史核对，不授权玩家新动作。"
        "开场处境只建立背景，完整剧情方向决定因果重心；边缘细节没有得到方向支持时，不得扩成新机制、阻碍或多轮任务。"
        "作者条件必须先公开成立再执行依赖动作；允许目的地不代表未知路径和通行方式自动成立。"
        "作者剧情方向是导演信息，不是角色已经知道的事实；其中明确的因果先后不能倒置，"
        "某事件依赖玩家回应、选择或前一事实时，在该前提公开成立前，"
        + ("正文不能先使用后续事件。" if author_entry_choices else
           f"正文和推荐都不能先使用后续事件。{_SUGGESTION_PLAYER_FACT_RULE}")
        +
        "作者只给出抽象状态或待确认事项时，不得自行具体化；重要事物保持已建立的归属、状态和生命周期。"
        "可选内容不是任务清单，不提前演完本幕或进入后续阶段。"
        "提议必须公开、具体、由已发生事实导向并停在下一阶段结果之前。"
        + ("普通幕内行动或泛泛询问的 transition_offered 必须为 false。" if author_entry_choices else
           "推荐若包含结束当前互动阶段的行动，transition_offered 必须为 true，且要同时提供玩家执行路径和真实替代；"
           "普通幕内行动或泛泛询问必须为 false。")
        +
        "acting_context.core_persona 在 full 范围内影响关注、判断与自主反应，在 style_only 范围内只影响表达；"
        "acting_contract 决定认知与身份，dialogue_policy 决定能否说话，人格不授予额外权限，"
        "relationship_control.response_contract 决定当前关系边界。"
        "dialogue_policy=required 必须有括号外对白，forbidden 只能写动作，optional 两者均可。"
        f"{player_address_state_rule}"
        "你扮演的角色以角色设定中的姓名为准；动作括号默认省略主语，只写你自身的动作。"
        "不要提及数值、阈值、路线、节点、系统或提示词；最终 JSON 不输出解释与推理。"
    )


def _messages_tokens(messages: list[Any]) -> int:
    return count_tokens(json.dumps(_normalize_messages(messages), ensure_ascii=False))


def _ensure_actor_messages_fit(
    messages: list[Any],
    *,
    max_tokens: int = 4800,
) -> list[Any]:
    """Never runtime-truncate fixed plot contracts; return explicit errors above total budgets."""  # noqa: DOCSTRING_CJK

    if _messages_tokens(messages) > max_tokens:
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    return messages


def _remaining_history_evidence(
    history: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Remove only complete same-turn/same-source text retained in final history; restore evidence automatically when history is trimmed."""  # noqa: DOCSTRING_CJK

    retained_quotes = set()
    retained_fixed: dict[tuple[Any, bool], list[str]] = {}
    for row in history:
        revision = row.get("revision")
        retained_quotes.add((revision, "player_input", str(row.get("player_input") or "").strip(), False))
        segments = row.get("segments")
        for part in segments if isinstance(segments, list) else [row]:
            if not isinstance(part, Mapping) or part.get("phase") == "previous_scene_tail":
                continue
            is_bridge = part.get("phase") == "transition_bridge"
            for field in ("scene_narration", "performance", "fixed_narration_before", "fixed_narration_after"):
                raw_text = str(part.get(field) or "")
                text = raw_text.strip()
                retained_quotes.add((revision, "performance", text, is_bridge))
                if field in {"fixed_narration_before", "fixed_narration_after"} and text:
                    # _prompt_container joins multiple fixed pieces with a blank line.
                    # Keep the joined field for exact matches, and its complete pieces
                    # for evidence units retrieved as one original block.
                    retained_fixed.setdefault((revision, is_bridge), []).append(raw_text)

    def _fixed_piece_retained(revision: Any, text: str, is_bridge: bool) -> bool:
        # A fixed piece is complete only when it is the whole joined field or is
        # delimited by the same blank-line boundary on both sides. Do not split,
        # normalize, or accept arbitrary substrings.
        for container in retained_fixed.get((revision, is_bridge), ()):
            if container == text or f"\n\n{text}\n\n" in f"\n\n{container}\n\n":
                return True
        return False

    remaining = []
    for item in evidence:
        is_bridge = item.get("current_entry_bridge") is True
        revision, source, text = item.get("revision"), item.get("source"), item.get("text")
        if item.get("current_visit") or is_bridge:
            if (revision, source, text, is_bridge) in retained_quotes:
                continue
            if source == "performance" and _fixed_piece_retained(revision, str(text or ""), is_bridge):
                continue
        remaining.append(item)
    return remaining


def _prompt_token_counter(system_prompt: str) -> Callable[[str], int]:
    # 每次装箱独立计量；System 不变，重复检查同一完整 Human 时只复用上一次结果。
    # 内容一旦改变就重新编码整串，不相加字段计数，也不跨回合缓存人设或历史。
    previous_human: str | None = None
    previous_tokens = 0

    def total(human: str) -> int:
        nonlocal previous_human, previous_tokens
        if human != previous_human:
            previous_tokens = _messages_tokens([theater_system_message(system_prompt), HumanMessage(content=human)])
            previous_human = human
        return previous_tokens

    return total


_HistoryEvidenceExpander = Callable[
    [Sequence[Mapping[str, Any]], Callable[[Sequence[Mapping[str, Any]]], bool]],
    Sequence[Mapping[str, Any]],
]




def _fit_turn_prompt_data(
    *,
    system_prompt: str,
    human_prefix: str,
    data: dict[str, Any],
    max_tokens: int = 4800,
    diagnostics: dict[str, Any] | None = None,
    render_committed_history: bool = False,
    evidence_expander: _HistoryEvidenceExpander | None = None,
) -> dict[str, Any]:
    """Delete auxiliary data/earliest whole turns only, never edit retained text."""  # noqa: DOCSTRING_CJK

    fitted = dict(data)
    count_prompt_tokens = _prompt_token_counter(system_prompt)
    count_without_evidence = count_prompt_tokens
    if render_committed_history and HISTORY_EVIDENCE_RULE in system_prompt:
        count_without_evidence = _prompt_token_counter(system_prompt.replace(HISTORY_EVIDENCE_RULE, "", 1))
    evidence = list(fitted.get("history_evidence") or [])
    initial_history_revisions = [
        item.get("revision")
        for item in fitted.get("recent_context") or []
        if isinstance(item, Mapping) and isinstance(item.get("revision"), int)
    ]
    def prompt_data(evidence_override: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        if not render_committed_history:
            return fitted if evidence_override is None else {**fitted, "history_evidence": evidence_override}
        # Keep raw rows for evidence deduplication; only the wire representation loses old output phases.
        rendered = {**fitted, "recent_context": [
            {"revision": row.get("revision"), "player_input": row.get("player_input", ""),
             "committed_text": _story_so_far_row_text({**row, "player_input": ""})}
            for row in fitted.get("recent_context") or []
        ]}
        transition = fitted.get("transition")
        invitation = transition.get("pending_invitation") if isinstance(transition, Mapping) else None
        if evidence_override is not None:
            rendered["history_evidence"] = evidence_override
        if isinstance(invitation, Mapping):
            revision = invitation.get("revision")
            original = next((row["committed_text"] for row in rendered["recent_context"]
                             if row["revision"] == revision), "")
            offer_text = invitation.get("offer_text")
            reference = ""
            if offer_text:
                if offer_text in original:
                    reference = "recent_context中同revision的角色原文末尾的独立邀请"
                elif any(row.get("current_visit") is True and row.get("revision") == revision
                         and row.get("source") == "performance" and (
                             row.get("text") == offer_text or str(row.get("text") or "").endswith("\n" + offer_text)
                         ) for row in rendered.get("history_evidence") or ()):
                    reference = "history_evidence中同revision的角色原文末尾的独立邀请"
            elif invitation.get("content") and all(block.get("text") and block["text"] in original
                                                  for block in invitation["content"]):
                reference = "recent_context中同revision的已公开原文"
            if reference:
                rendered["transition"] = {**transition, "pending_invitation": {
                    "revision": revision, "content_reference": reference,
                }}
        # Keep the just-finished player turn after old quotes and the transition
        # plan on the wire; budget checks must count this identical ordering.
        if "player_input" in rendered:
            rendered["player_input"] = rendered.pop("player_input")
        return rendered

    def tokens() -> int:
        if evidence:
            fitted["history_evidence"] = _remaining_history_evidence(
                fitted.get("recent_context") or [], evidence,
            )
        human = human_prefix + render_prompt_context(prompt_data())
        counter = count_prompt_tokens if fitted.get("history_evidence") else count_without_evidence
        return counter(human)

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
        return prompt_data()

    history = list(fitted.get("recent_context") or [])
    while tokens() > max_tokens and len(history) > 1:
        history.pop(0)
        fitted["recent_context"] = list(history)


    if evidence_expander is not None and evidence:
        # Preserve useful later corrections, with one bounded supplementary allowance.
        evidence_limit = count_tokens(json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))) + numeric_v2_actor_budget(
            NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE)["evidence_max_tokens"]
        def within_prompt(candidate: Sequence[Mapping[str, Any]]) -> bool:
            # 只替换最终 wire 的检索附件，不重新裁历史、状态或合同。
            if count_tokens(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > evidence_limit:
                return False
            trial = prompt_data(_remaining_history_evidence(history, candidate))
            counter = count_prompt_tokens if trial["history_evidence"] else count_without_evidence
            return counter(
                human_prefix + render_prompt_context(trial),
            ) <= max_tokens

        fits = tokens() <= max_tokens
        if fits or within_prompt(()):
            # 正常请求仍保留原附件并只用余量扩充。超限且固定内容能完整保留时，
            # 让原检索器按问题/答复等既有优先级重选整条原文；不能按时间删旧引文。
            evidence = list(evidence_expander(evidence if fits else (), within_prompt))
            # 空选择也必须清掉旧附件，否则 tokens() 的非空判断会留下原超限副本。
            fitted["history_evidence"] = _remaining_history_evidence(history, evidence)
    if tokens() > max_tokens:
        # Never silently delete or sentence-truncate current player input, stable background, target contracts, or character context.
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    return finish()


def _fit_simple_turn_prompt_data(
    *,
    system_prompt: str,
    human_prefix: str,
    data: dict[str, Any],
    history_rows: list[Mapping[str, Any]],
    max_tokens: int,
    history_preselected: bool = False,
    evidence_text: str = "",
    evidence_records: Sequence[Mapping[str, Any]] = (),
    fact_index: Callable[[], Mapping[str, Any] | None] | None = None,
    fact_index_expander: Callable[[Callable[[Mapping[str, Any]], bool]], Mapping[str, Any] | None] | None = None,
    pending_offer_text: str = "",
    pending_offer_revision: int | None = None,
    diagnostics: dict[str, Any] | None = None,
    evidence_expander: _HistoryEvidenceExpander | None = None,
    staged_history_quotes: tuple[str, ...] = (),
    staged_player_history_quotes: tuple[str, ...] = (),
    retained_player_inputs: list[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Pack partitioned prompts from earlier whole turns, never truncating current input/turn."""  # noqa: DOCSTRING_CJK

    fitted = dict(data)
    entry_cause = fitted.pop("entry_cause", None)
    entry_cause_text = prompt_data_block("前一幕入幕依据", entry_cause) if entry_cause else ""
    count_prompt_tokens = _prompt_token_counter(system_prompt)
    history = list(history_rows)
    original_pacing = str(data.get("pacing") or "")
    offer_marker = _PENDING_OFFER_PROMPT_LABEL + pending_offer_text
    story_prefix = ""
    index_loaded = False
    history_index: Mapping[str, Any] | None = None
    indexed_evidence: Sequence[Mapping[str, Any]] = ()
    staged_references: dict[str, str] = {}
    initial_history_revisions = [
        row.get("revision")
        for row in history
        if isinstance(row.get("revision"), int)
    ]
    def story_with_evidence(records: Sequence[Mapping[str, Any]]) -> str:
        remaining = _remaining_history_evidence(history, records)
        # Only replace an unambiguous, byte-exact current-visit source with the
        # richer source already retained in the index. Different visits, speakers,
        # revisions and final negations remain independent evidence. The generic
        # retrieval has no field identity, so multiple matching fields stay intact.
        def source_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
            return (item.get("revision"), item.get("source"), item.get("text"),
                    item.get("current_entry_bridge") is True)

        def same_visit(item: Mapping[str, Any]) -> bool:
            return item.get("current_visit") is True or item.get("current_entry_bridge") is True

        indexed_sources = Counter(source_key(item) for item in indexed_evidence
                                  if same_visit(item))
        retrieved_sources = Counter(source_key(item) for item in records
                                    if same_visit(item))
        remaining = [item for item in remaining if not (
            same_visit(item)
            and indexed_sources[source_key(item)] == 1
            and retrieved_sources[source_key(item)] == 1
        )]
        retrieved = (
            "history_evidence（已提交原文）："
            + json.dumps(remaining, ensure_ascii=False, separators=(",", ":"))
            if remaining else ""
        )
        return "\n\n".join(
            part for part in (entry_cause_text, evidence_text, retrieved, story_prefix, _story_so_far_text(history)) if part
        )

    def refresh_story() -> None:
        # Preserve committed wording, including uncertain speech, not a derived
        # truth summary. Rerender complete sources after every budget eviction.
        nonlocal story_prefix, index_loaded, indexed_evidence, history_index
        if not index_loaded and (history_preselected or len(history) < len(history_rows)):
            # Both candidate windows and total-budget packing trim; generate indexes only when actual records disappear.
            index_loaded = True
            history_index = fact_index() if fact_index is not None else None
        if history_index:
            # The index is a second view of the same originals, not mandatory
            # duplicate evidence. Recompute against retained complete turns so
            # evicting a turn restores its indexed source rather than losing both.
            indexed_evidence = tuple(_remaining_history_evidence(
                history, history_index.get("evidence") or (),
            ))
            compact = json.dumps({**history_index, "evidence": list(indexed_evidence)},
                                 ensure_ascii=False, separators=(",", ":"))
            story_prefix = (
                "当前幕已提交原文索引（历史已裁剪；仅所列原文完整，省略项保持未知；"
                "按回合和来源承接后续变化，不代表任务或完成判定）：\n"
                f"{compact}\n\n最近完整对话："
            )
        # Remove originals only for the same turn/source when retained completely. Recalculate after every eviction,
        # restoring evidence for trimmed history rather than losing both; never deduplicate merely similar wording.
        fitted["story_so_far"] = story_with_evidence(evidence_records)
        staged_references.clear()
        for source, quotes in (("actor_performance", staged_history_quotes),
                               ("committed_player_input", staged_player_history_quotes)):
            for quote in quotes:
                reference = _staged_history_evidence_ref(history, quote, source=source)
                if reference:
                    original = json.dumps({"source": source, "quote": quote},
                                          ensure_ascii=False, separators=(",", ":"))
                    staged_references[original] = json.dumps(
                        {"source": source, "quote_ref": reference},
                        ensure_ascii=False, separators=(",", ":"),
                    )
        if pending_offer_text and original_pacing.count(offer_marker) == 1:
            reference = ""
            if pending_offer_revision is not None:
                for distance, row in enumerate(reversed(history), 1):
                    if (row.get("revision") == pending_offer_revision
                            and pending_offer_text in str(row.get("performance") or "")):
                        reference = ("见 story_so_far 中角色最新的完整原文。" if distance == 1 else
                                     f"见倒数第{distance}个完整回合。")
                        break
            elif history:
                # Legacy offers lack a reliable span. Retain the existing latest-role-only shortcut.
                latest_parts = history[-1].get("segments")
                latest_parts = latest_parts if isinstance(latest_parts, list) else [history[-1]]
                latest_performances = [
                    str(part.get("performance") or "").strip()
                    for part in latest_parts
                    if isinstance(part, Mapping) and part.get("phase") != "previous_scene_tail"
                    and part.get("performance")
                ]
                if latest_performances and pending_offer_text in latest_performances[-1]:
                    reference = "见 story_so_far 中角色最新的完整原文。"
            fitted["pacing"] = (original_pacing.replace(offer_marker, _PENDING_OFFER_PROMPT_LABEL + reference, 1)
                                if reference else original_pacing)

    def drop_previous_scene_tail() -> bool:
        # Prior-scene aftermath is disposable continuity; drop it before sacrificing real current-scene turns under budget pressure.
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

    def prompt_data() -> dict[str, Any]:
        if not staged_references:
            return fitted
        scene = fitted["current_scene"]
        for original, reference in staged_references.items():
            if scene.count(original) == 1:
                scene = scene.replace(original, reference, 1)
        return {**fitted, "current_scene": scene}

    def tokens() -> int:
        human = human_prefix + render_prompt_context(prompt_data())
        return count_prompt_tokens(human)

    refresh_story()
    if tokens() > max_tokens and drop_previous_scene_tail():
        refresh_story()
    if entry_cause_text and tokens() > max_tokens:
        entry_cause_text = ""
        refresh_story()
    while tokens() > max_tokens and len(history) > 1:
        # Preserve the latest actual record, evicting earlier turns only as complete records.
        history.pop(0)
        refresh_story()
    if evidence_expander is not None and evidence_records:
        evidence_limit = count_tokens(json.dumps(evidence_records, ensure_ascii=False, separators=(",", ":"))) + numeric_v2_actor_budget(
            NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE)["evidence_max_tokens"]
        def within_prompt(candidate: Sequence[Mapping[str, Any]]) -> bool:
            # 整份 Human JSON 计量自然包括 story_so_far 的二次转义；试装不改基线字段。
            if count_tokens(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > evidence_limit:
                return False
            trial = {**prompt_data(), "story_so_far": story_with_evidence(candidate)}
            return count_prompt_tokens(
                human_prefix + render_prompt_context(trial),
            ) <= max_tokens

        fits = tokens() <= max_tokens
        if fits or within_prompt(()):
            # Keep the original seed on a fitting request, including duplicates
            # carried elsewhere, so retrieval does not gain extra source slots.
            # Otherwise reselect whole originals using the existing priorities;
            # the fixed role, index, latest exchange and current input stay put.
            evidence_records = evidence_expander(evidence_records if fits else (), within_prompt)
            fitted["story_so_far"] = story_with_evidence(evidence_records)
    if tokens() > max_tokens and history_index and fact_index_expander is not None:
        had_evidence = bool(evidence_records)
        # Neither optional view may reserve space while preventing the other
        # selector from fitting even its empty projection.
        evidence_records = ()
        refresh_story()

        def index_within_prompt(candidate: Mapping[str, Any]) -> bool:
            nonlocal history_index
            original = history_index
            try:
                history_index = candidate
                refresh_story()
                return tokens() <= max_tokens
            finally:
                history_index = original
                refresh_story()

        # The index is optional evidence too. Reselect complete originals using
        # the same relevance/correction priorities, measured on the final wire.
        selected_index = fact_index_expander(index_within_prompt)
        if selected_index is not None:
            history_index = selected_index
            refresh_story()
        if had_evidence and evidence_expander is not None and tokens() <= max_tokens:
            evidence_records = evidence_expander((), within_prompt)
            fitted["story_so_far"] = story_with_evidence(evidence_records)
    if tokens() > max_tokens:
        # Recheck the exact final wire even if a selector ignored its predicate.
        # Fixed character data, scene contracts and player input are never truncated.
        raise NumericV2ActorError("numeric_v2_actor_fixed_context_budget_exceeded")
    if retained_player_inputs is not None:
        # Deduplicated evidence is still visible in the complete row or index that replaced it.
        recent_inputs = ((row["revision"], row["player_input"].strip()) for row in history
                         if type(row.get("revision")) is int and row.get("player_input"))
        earlier_inputs = ((row["revision"], row["text"]) for row in (*evidence_records, *indexed_evidence)
                          if row.get("source") == "player_input" and row.get("current_visit") is True
                          and type(row.get("revision")) is int)
        retained_player_inputs[:] = list(dict.fromkeys((*recent_inputs, *earlier_inputs)))
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
    return prompt_data()


def _append_draft_repair(
    *, system_prompt: str, human_prefix: str, data: dict[str, Any],
    material: Mapping[str, Any], max_tokens: int, diagnostics: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Use spare wire capacity for uncommitted editing material without evicting evidence."""
    repair_prefix = human_prefix.removesuffix("：\n") + (
        "；draft_repair 仅为未提交编辑材料，不是历史或批准。按 issues 定位修改，"
        "其余内容也须全部重审；不自动保留旧结果或沿用旧稿的行动授权。\n"
    )
    repair_data = {key: value for key, value in data.items() if key != "player_input"}
    repair_data.update(draft_repair=dict(material), player_input=data["player_input"])
    repair_tokens = _prompt_token_counter(system_prompt)(
        repair_prefix + render_prompt_context(repair_data),
    )
    if repair_tokens <= max_tokens:
        diagnostics.update(draft_repair="admitted", final_tokens=repair_tokens)
        return repair_prefix, repair_data
    diagnostics["draft_repair"] = "omitted_budget"
    return human_prefix, data


def _log_prompt_diagnostics(session: ScriptSessionV2, diagnostics: Mapping[str, Any]) -> None:
    """Log packing versions/tokens only, never player text."""  # noqa: DOCSTRING_CJK

    trace_event("prompt.packed", stage="actor", diagnostics=diagnostics)
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
    runtime_target_opening: str,
    transition_contract: Mapping[str, Any],
    recent_context: list[dict[str, Any]],
    acting_context: Mapping[str, Any],
    player_input: str,
) -> dict[str, Any]:
    """Construct transition working memory directly without building full ordinary context destined for deletion."""  # noqa: DOCSTRING_CJK

    transition = {
        "authorized": True,
        "source_scene": {
            "chapter_title": str(current_chapter_title or ""),
            # Source narrative guides response emphasis independently of recent_context's actual events.
            "story_direction": str(source_story_direction or ""),
        },
        "target_scene": {
            "chapter_title": str(target_chapter_title or ""),
            "opening_situation": str(runtime_target_opening or ""),
        },
        "reason": str(transition_contract.get("reason") or ""),
        "must_deliver": list(transition_contract.get("must_deliver") or []),
        # Share parsing/Runtime projections rather than letting models guess whether bridges may be omitted.
        "bridge_required": bool(transition_contract.get("bridge_required", True)),
        "bridge_scene_narration": str(
            transition_contract.get("bridge_scene_narration") or ""
        ),
        "must_preserve": list(transition_contract.get("must_preserve") or []),
        "tone": str(transition_contract.get("tone") or ""),
    }
    source_author_fields = {
        "current_scene_state": "source_author_entry_situation",
        "character_state": "source_author_entry_state",
    }
    source_situation = _author_entry_situation(acting_context)
    transition_acting_context = {
        source_author_fields.get(key, key): source_situation if key == "current_scene_state" else value
        for key, value in acting_context.items()
        if key != "current_scene_state" or source_situation
    }
    # Match Review's target_scene.author_entry_plan: preserve the complete
    # author snapshot beside its planned opening, separate from acting policies
    # and committed history. Moving the plan does not establish any result.
    target_author_entry_plan = transition_acting_context.pop("target_character_state", None)
    if target_author_entry_plan:
        transition["target_scene"]["author_entry_plan"] = target_author_entry_plan
    # The shared continuity contract covers every segment. Send exact common
    # statements there once, preserving any phase-specific additions.
    for container, key in (
        (transition_acting_context, "source_author_entry_state"),
        (transition["target_scene"], "author_entry_plan"),
    ):
        state = container.get(key)
        if state and state.get("continuity_from_previous"):
            continuation = [item for item in state["continuity_from_previous"]
                            if item not in transition["must_preserve"]]
            plan = {**state, "continuity_from_previous": continuation}
            if not continuation:
                plan.pop("continuity_from_previous")
            container[key] = plan
    if (target_control := transition_acting_context.get("target_relationship_control")) and (
        target_control == transition_acting_context.get("relationship_control")
    ):
        transition_acting_context["target_relationship_control"] = {"same_as": "relationship_control"}
    # Effective per-segment policies already live above; author policies must not require a finished source to speak.
    for field in ("acting_contract", "target_acting_contract"):
        if field in transition_acting_context:
            transition_acting_context[field] = {
                key: value for key, value in transition_acting_context[field].items()
                if key != "dialogue_policy"
            }
    target_contract = transition_acting_context.get("target_acting_contract")
    source_contract = transition_acting_context.get("acting_contract") or {}
    if target_contract and (shared_facts := source_contract.get("assertable_self_facts")) and (
        target_contract.get("assertable_self_facts") == shared_facts
    ):
        target_contract["assertable_self_facts"] = {"same_as": "acting_contract.assertable_self_facts"}
    return {
        "story_context": dict(story_context),
        "player_address": str(player_address or "你"),
            "recent_context": list(recent_context),
        "acting_context": transition_acting_context,
        "player_input": str(player_input or ""),
        "transition": transition_delivery_references(transition),
    }


def _opening_messages(
    engine: NumericV2Engine,
    character_profile: str,
    catgirl_name: str,
    player_address: str,
    player_address_known: bool = True,
    max_tokens: int = 4800,
    retry_hint: str = "",
    condition_narrations_enabled: bool = True,
) -> list[Any]:
    cast = NumericV2CastProjection.from_story(
        engine.story,
        # Author data names the player in third person; unknown nicknames must
        # not become the same 'you' that addresses the Actor in instructions.
        player_name=player_address if player_address_known else "玩家",
        catgirl_name=catgirl_name,
    )
    node = engine.nodes[str(engine.story["start_node_id"])]
    author_entry_choices = "entry_player_inputs" in node["story_beat"]
    opening_beat = _opening_beat_for_actor(cast, node)
    dialogue_policy = str(
        _acting_contract_for_actor(cast, node["story_beat"]).get("dialogue_policy") or "required"
    )
    opening_response = (
        "再由你以可见动作主动回应，不说出对白；"
        if dialogue_policy == "forbidden"
        else "再由你以动作、对白或两者自然回应；"
        if dialogue_policy == "optional"
        else "再由你主动说出第一句；"
    )
    data = {
        "character_name": catgirl_name,
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
            dialogue_policy=dialogue_policy,
        ),
        "instruction": (
            f"这是玩家输入前的公开开场。使用必要的环境或你可见行动建立当下场景，{opening_response}"
            "不得假定玩家已经说话、做出选择或完成无前因的主动行动，不得使用‘你刚才说/做’或同义的隐形前史。"
            "若 opening_scene 同句明确给出可见前因，可以建立玩家受伤、失衡或被外力带动等即时身体结果；"
            "若节点摘要只有玩家台词、决定或无前因主动行为，把它们视为后续可发展的剧情边界，不要在开场代替玩家执行。"
            "你的回应必须由本段旁白能够直接解释，并留下玩家可以自然回应的话头；"
            "话头不得反问玩家来替你确认本次 opening_scene 已经明确建立的状态；不要提前演完本节点。"
            + ("" if author_entry_choices else
               "开场推荐只能使用本次可见开场已经建立的玩家身份、地点、物品、能力和环境事实；"
               "不得把相似但未声明的地点标签、身份判断或状态猜测写成玩家已知事实，也不能要求玩家沿用正文尚未建立的推断。")
        ),
    }
    opening_context = data["acting_context"]
    if opening_situation := _author_entry_situation(opening_context):
        opening_context["current_scene_state"] = opening_situation
    else:
        opening_context.pop("current_scene_state", None)
    system_prompt = NUMERIC_V2_CONTEXT_READING_INSTRUCTION + _ACTOR_IDENTITY_RULE + _system_prompt(
        catgirl_name=catgirl_name,
        player_address_known=player_address_known,
        phase="opening",
        author_entry_choices=author_entry_choices,
    ) + _hard_boundary_system_instruction(
        list(opening_beat.get("boundaries") or []), phase="opening",
    )
    system_prompt += "\n<本次开场任务>\n" + data.pop("instruction") + "\n</本次开场任务>"
    if opening_context.get("capability_state"):
        system_prompt += "\n" + _CAPABILITY_LABEL_RULE
    if retry_hint:
        system_prompt += "\n本次公开开场必须改写：\n" + prompt_data_block("开场重试反馈", {"retry_hint": retry_hint})
    fixed_note = actor_note(node, None, {"catgirl_name": catgirl_name, "player_address": player_address},
                            player_address_known, project_condition=cast.text,
                            condition_triggers_enabled=condition_narrations_enabled)
    if fixed_note:
        system_prompt += "\n" + fixed_note
    opening_boundary_text = system_prompt
    if "======以下为本轮作者硬边界======" in system_prompt:
        opening_boundary_text += "\n- ".join(prompt_data_block_payload(
            system_prompt, "本轮作者硬边界")["boundaries"])
    # The complete opening boundaries already live in System, as in formal turns.
    # Keep any author rule not actually rendered there; never trim its exceptions.
    for container, field in (
        (opening_beat, "boundaries"),
        (data["acting_context"].get("acting_contract", {}), "forbidden_behaviors"),
        (data["acting_context"].get("character_state", {}), "scene_boundaries"),
    ):
        if field in container:
            container[field] = [text for text in container[field] if text not in opening_boundary_text]
            if not container[field]:
                container.pop(field)
    return _ensure_actor_messages_fit(
        [
            theater_system_message(system_prompt),
            HumanMessage(content=render_prompt_context(data)),
        ],
        max_tokens=max_tokens,
    )


def _has_unconfirmed_stage_claim(
    outcome: TurnOutcomeV2, player_input: str, input_source: str, quote: str,
) -> bool:
    return bool(
        input_source != "reinvite" and outcome.session.status == "active"
        and outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"]
        and quote and quote in player_input
    )


def _generation_source_scope(data: Mapping[str, Any], *, player_name: str, catgirl_name: str) -> dict[str, Any]:
    """Bind generation duties to available source fields without copying their originals."""

    transition = data.get("transition")
    formal = isinstance(transition, Mapping)
    history_ref = "recent_context" if formal else "story_so_far"
    scope = {
        "cast": {"player": player_name, "active_catgirl": catgirl_name},
        "writable_subjects": ["active_catgirl", "npc", "environment"],
        "player_authorization_sources": [{"ref": "player_input", "source": "current_player_input"},
                                         {"ref": history_ref, "filter": "player_input_only"}],
        "author_reference_refs": [],
        "history_records": [{"ref": history_ref, "status": "committed_record"}],
    }
    if not formal:
        scope["author_reference_refs"] = [{"ref": "current_scene", "filter": "author_staging_and_direction_only"}]
        return scope
    for key in ("source_scene", "target_scene"):
        scene = transition.get(key)
        if not isinstance(scene, Mapping):
            continue
        for field in ("story_direction", "opening_situation", "author_entry_plan"):
            if field in scene:
                scope["author_reference_refs"].append(f"transition.{key}.{field}")
    for key in ("source_author_entry_state", "source_author_entry_situation"):
        if key in data["acting_context"]:
            scope["author_reference_refs"].append("acting_context." + key)
    if transition.get("transition_intent") == "accept" and transition.get("pending_invitation"):
        scope["player_authorization_sources"].append(
            {"ref": "transition.pending_invitation", "source": "accepted_public_offer"})
    if transition.get("reviewed_source"):
        scope["draft_source"] = {"ref": "transition.reviewed_source", "status": "reviewed_this_turn"}
    if transition.get("entry_prefix"):
        scope["draft_prefix"] = {"ref": "transition.entry_prefix.blocks", "status": "uncommitted_this_turn"}
        scope["response_after_ref"] = "transition.entry_prefix.blocks"
    if transition.get("program_entry_delivery"):
        scope["program_delivery"] = {"ref": "transition.program_entry_delivery", "status": "scheduled_this_draft"}
    return scope


def _turn_messages(
    engine: NumericV2Engine,
    session: ScriptSessionV2,
    outcome: TurnOutcomeV2,
    player_input: str,
    character_profile: str,
    catgirl_name: str,
    player_address: str,
    player_address_known: bool = True,
    retry_hint: str = "",
    input_source: str = "freeform",
    recent_ledger_events: tuple[Mapping[str, Any], ...] = (),
    history_lookup: Mapping[str, Any] | None = None,
    player_action_projection: Mapping[str, Any] | None = None,
    approved_completion_facts: tuple[Mapping[str, Any], ...] = (),
    *,
    draft_repair_context: Mapping[str, Any] | None = None,
    combined_evaluation: bool = False,
    combined_history_lookup: bool = False,
    combined_history: list[str] | None = None,
    combined_player_inputs: list[tuple[int, str]] | None = None,
    fixed_entry_only: bool = False,
    next_scene_preview: Mapping[str, Any] | None = None,
    wrong_exit_offer_quote: str = "",
    exit_mismatch_reason: str = "",
    progress_direction: str = "",
    unconfirmed_departure_quote: str = "",
    current_scene_task: Mapping[str, Any] | None = None,
    departure_attempt_observed: bool = False,
    transition_source: Mapping[str, Any] | None = None,
    source_delivery_task: Mapping[str, Any] | None = None,
    entry_part: str = "",
    entry_prefix: Mapping[str, Any] | None = None,
) -> list[Any]:
    cast = NumericV2CastProjection.from_story(
        engine.story,
        player_name=player_address if player_address_known else "玩家",
        catgirl_name=catgirl_name,
    )
    source = engine.nodes[str(outcome.ledger_event["from_node_id"])]
    target = engine.nodes[str(outcome.ledger_event["to_node_id"])]
    route_changed = source["id"] != target["id"]
    entry_trigger_required = (route_changed and has_pending_entry(target, session) and not fixed_entry_only
                              and outcome.session.status != "ended" and not entry_part)
    reinvitation = input_source == "reinvite" and not route_changed
    if route_changed or reinvitation:
        source_delivery_task = None
    if _has_unconfirmed_stage_claim(outcome, player_input, input_source, unconfirmed_departure_quote):
        # No selected route authorizes the claimed stage, even without a Guard blocker.
        next_scene_preview = {"status": "departure_pending"}
    if not route_changed and next_scene_preview is None:
        next_scene_preview = _next_scene_preview_for_actor(
            engine, cast, source, outcome.session.metrics, facts=outcome.session.story_state["facts"],
            departure_blocker=outcome.departure_blocker,
        )
    natural_ending = (route_changed and outcome.session.status == "ended"
                      and outcome.ledger_event.get("natural_ending_ready") is True and not fixed_entry_only)
    pending_ending_facts = engine.pending_natural_ending_fact_targets(outcome) if route_changed else []
    local_suggestions_only = (
        not route_changed and not reinvitation and outcome.session.status == "active"
        and (next_scene_preview or {}).get("status") == "runtime_unresolved"
        and _allows_local_suggestions_before_ending(engine, source, outcome.session.dialogue_policy)
    )
    system_prompt = NUMERIC_V2_CONTEXT_READING_INSTRUCTION + _ACTOR_IDENTITY_RULE + _system_prompt(
        catgirl_name=catgirl_name,
        player_address_known=player_address_known,
        phase="transition_compact" if route_changed else "reinvitation" if reinvitation else "turn",
        single_ending_response=_uses_single_ending_response(
            natural_ending, outcome.session.dialogue_policy,
            str((outcome.transition_contract or {}).get("bridge_scene_narration") or "")),
        optional_ending_target=natural_ending and outcome.session.dialogue_policy in {"optional", "forbidden"},
        entry_narration=route_changed and has_pending_entry(target, session),
        entry_trigger_required=entry_trigger_required,
        fixed_entry_only=fixed_entry_only,
        author_entry_choices=(route_changed and outcome.session.status != "ended"
                              and "entry_player_inputs" in target["story_beat"]),
        terminal_transition=route_changed and outcome.session.status == "ended",
        suppress_suggestions=(not route_changed and not reinvitation
                              and (next_scene_preview or {}).get("status") != "after_acceptance_only"
                              and not local_suggestions_only),
        local_suggestions_only=local_suggestions_only,
        ending_fact_candidates=bool(pending_ending_facts),
        combined_evaluation=combined_evaluation,
        transition_tail_only=transition_source is not None,
        source_delivery_only=bool(source_delivery_task),
        shared_agreement_delivery=(source_delivery_task or {}).get("kind") == "shared_agreement",
        entry_part=entry_part,
    )
    if not reinvitation:
        system_prompt += (
            "\ngeneration_scope是程序给定的生成职责和来源：仅为writable_subjects新增主动行为；"
            "玩家行动只依player_authorization_sources中所标来源承接，正常共同移动、交接的直接结果可自然承接。"
            "author_reference_refs只标作者参考，不证明已执行，也不授予玩家操作权限；已登记或独立审批值仍按原标签生效。"
            "history_records记录说过/演过的原文，不自动证明其主张完成；同轮draft不作已提交事实，"
            "response_after_ref存在时从该前段结束状态续接。"
        )
    if not route_changed and not reinvitation and session.performance_history:
        last = session.performance_history[-1]
        if (_can_defer_dialogue_repetition(last)
                and last.get("from_node_id") == last.get("to_node_id") == session.current_node_id
                and (_repeats_earlier_session_performance(last, session, route_changed=False)
                     or (len(session.performance_history) > 1 and _is_repeated_performance(
                         last, session.performance_history[-2])))):
            # 从已提交原文恢复软修正，不另存记忆、不改写历史或再加一次模型审核。
            system_prompt += (
                "\n上一轮有重复措辞；本轮直接回应玩家最新输入，换一种自然表达。"
                "只修正措辞，不重演动作、不撤销事实，不要求玩家重做选择。"
            )
    if input_source == "accept_invitation" and route_changed:
        # 控件只确认已公开邀请本身；空输入不是缺失台词，不能让 Actor 补出额外行动授权。
        system_prompt += (
            "\n程序已确认玩家接受刚展示的邀请；这是控制动作，不是玩家台词，player_input 为空。"
            "仅按 transition.pending_invitation 的原文、已提交历史与获准转场合同承接，"
            "不新增玩家动作、分工或重要操作的授权，不替玩家执行目标幕尚待决定的事项。"
        )
    current_player_input = str(player_input or "")
    projected_player_action = player_action_projection_for_prompt(
        player_action_projection
        if player_action_projection is not None
        else project_player_action_result(current_player_input),
    )
    if not route_changed and any(
        row["kind"] == "leave_current_scene" and row["source"] == "player_input"
        for row in projected_player_action["confirmed_actions"]
    ):
        # Syntactic execution is not stage authorization. Keep raw input and
        # local action permissions, without elevating departure to a System fact.
        projected_player_action["confirmed_actions"] = [
            row for row in projected_player_action["confirmed_actions"]
            if not (row["kind"] == "leave_current_scene" and row["source"] == "player_input")
        ]
        projected_player_action.pop("player_left_current_scene", None)
        projected_player_action["has_confirmed_player_action"] = any(
            row["source"] == "player_input" for row in projected_player_action["confirmed_actions"]
        )
    player_action_note = (
        reference_player_action_input(projected_player_action, current_player_input)
        + "\n本轮玩家动作结果投影（仅为约束，不是新增剧情）："
        + prompt_data_block("本轮玩家动作结果投影", projected_player_action)
        + "。"
    )
    binding = {"catgirl_name": catgirl_name, "player_address": player_address}
    program_entry_pieces = (
        [{key: piece[key] for key in ("text", "position") if key in piece}
         for piece in add_entry(target, {}, outcome.session.catgirl_binding, outcome.session.player_address_known,
                                session=outcome.session).get("fixed_narrations", [])]
        if route_changed and not fixed_entry_only and entry_part != "response" else []
    )
    if program_entry_pieces:
        system_prompt += (
            "\ntransition.program_entry_delivery给出程序排定的本稿原文与播放位置，不属于你的输出，"
            "不证明已提交、已审批或新增玩家许可。"
        )
    target_notes = [] if fixed_entry_only else ([("目标幕", target)] if route_changed else [])
    for label, node in ([] if entry_part == "response" else [("当前幕", source)]) + target_notes:
        fixed_note = actor_note(
            node, session, binding, player_address_known, project_condition=cast.text,
            condition_triggers_enabled=outcome.ledger_event.get("condition_narrations_enabled") is not False,
            include_instructions=entry_part != "response" and not (program_entry_pieces and node is target) and (
                (transition_source is not None and node is target)
                or not (route_changed and has_pending_entry(target, session))),
            allow_entry_trigger_actions=route_changed and not fixed_entry_only
                                        and node is target and has_pending_entry(target, session),
            include_entry=not (node is target and (entry_part == "response" or program_entry_pieces)),
        )
        if fixed_note:
            system_prompt += f"\n{label}固定旁白说明：\n{fixed_note}"
    if source["story_beat"].get("fixed_narrations"):
        system_prompt += "\n已展示的作者原文可能含往事、书信或屏幕日志；其中的地点、伤情和敌人不自动成为当前现场状态。"
    if combined_evaluation:
        # Reuse Actor's scene/history evidence; do not duplicate Evaluator's prompt.
        metrics = [{
            "id": key,
            "criteria": {f"{key}.{direction}.{index + 1}": cast.text(text)
                         for direction in ("increase", "decrease")
                         for index, text in enumerate(definition[f"{direction}_criteria"])},
        } for key, definition in engine.metric_schema.items()]
        system_prompt += (
            "\n本轮合并普通场景判断与表演：在原有输出后附加 turn_evaluation，"
            "必须包含 scene_complete:boolean、metric_changes:object、fact_candidates:object[]。"
            + ("确实需要回忆既往事实且已给原文不足时，另填 history_query:string，写清待查的对象和问题。"
               "当前稿只回应能证实的部分，不补造旧事；无需补查时省略 history_query 或填空字符串。"
               if combined_history_lookup else "")
            + "scene_complete 仅表示当前互动已有自然收束点，不授权换幕或结局；"
            "没有已展示结果支持时为false，不把打算、推荐、后续目标当成完成。"
            "下列作者准则只用于 turn_evaluation 的 metric_changes；不进入演出、旁白或推荐，也不证明未来选项已经公开。"
            "只按本轮玩家实际输入选择变化；无变化填{}。"
            "每个键为指标id，值只能含 strength（weak/normal/strong/decisive）、criterion_id（下列准则原id）。"
            "不能自行输出数值或根据自己编写的台词给玩家加减分。"
            "作者指标准则："
            + prompt_data_block("作者指标准则", {"metrics": metrics})
            + "\n事实候选按证据来源分开，scene_complete 不替代事实。"
            "玩家本轮明确作出的选择（含路线）、同意或行动，只在 turn_evaluation.fact_candidates 中引用 player_input；"
            "已交付的本幕结果可在同一数组引用 story_so_far 的本幕演出原文。"
            "顶层 fact_candidates 只登记本轮 performance 或 scene_update 实际交付的新结果，引用相应正文，不重复登记。"
            "标注 evidence_source=performance 的事实只引用实际演出。"
            "每项仅含 key、value、evidence_quote，逐字引用相应来源；协议要求的数组无符合项时填[]。"
        )
    human_prefix = (
        "以下四块是本次获准转场的上下文。末尾 player_input 是玩家刚结束的发言；"
        + ("" if fixed_entry_only and outcome.session.status != "ended" else
           "source_performance 由角色设定中的你承接来源幕尚未回应的内容。")
        + ("目标入幕由程序原文交付，两个目标字段和推荐按系统合同留空。"
           if fixed_entry_only else
           "target_performance 在目标开场接话，不把旧幕台词重新当作当前回应。"
           "终局以外，suggested_inputs 是玩家看到目标回应后的下一句。")
        + "已提交事实与待演作者要求分开使用：\n"
    )
    if transition_source is not None:
        human_prefix = "以下为获准转场的上下文。reviewed_source是本轮已审来源段，只续写其后的桥段和目标开场。\n"
    if entry_part:
        human_prefix = (
            "以下为获准转场的完整上下文。本次只生成程序原文之前的来源、桥段和目标旁白。\n"
            if entry_part == "prefix" else
            "以下为获准转场的完整上下文。entry_prefix是同轮未提交有序前段；只补原文之后的目标表演和推荐。\n"
        )
    if input_source == "accept_invitation" and route_changed:
        human_prefix = human_prefix.replace(
            "末尾 player_input 是玩家刚结束的发言", "末尾 player_input 为空，接受控制已由程序确认",
        )
    story_context = _story_context_for_actor(
        cast,
        engine.story,
        beats=(
            (target["story_beat"],) if entry_part == "response" else
            (source["story_beat"],)
            if fixed_entry_only
            else (source["story_beat"], target["story_beat"])
            if route_changed
            else (source["story_beat"],)
        ),
    )
    if player_facts := public_player_fact_evidence(session, engine.fact_contract):
        if route_changed:
            story_context["player_fact_evidence"] = player_facts
        system_prompt += (
            "\nplayer_fact_evidence 的已登记具体值须跨幕承接。布尔值旁仅列登记轮玩家原话，"
            "按description承接该事实所记事项。同句里其他愿望、试用或暂定偏好不变成已定方案；"
            "仅仅提过备选不证明已有体验、使用记录或反馈。本幕获准的当场比较和展示仍可实际交付，"
            "不因此替玩家改选或补写玩家感受。"
        )
    if "background" in story_context:
        story_context = {"initial_background" if key == "background" else key: value
                         for key, value in story_context.items()}
        system_prompt += (
            "\n剧本初始前情描述故事开始时的处境，其中稳定的世界设定、身份和关系约束仍有效；"
            "其中的时地和动作不作为当前时地或重演动作的依据。"
        )
    system_prompt += (
        "\n章节标题只标示主题，不证明当前时地或事件已经发生；"
        "当前状态承接实际历史；时地变化依据本轮输入中的已授权行动，或本次获准的具体开场或转场合同，"
        "仍遵守既有阶段授权，不从标题推断。"
    )
    if "knowledge_scope" in story_context:
        story_context["knowledge_scope"] = (
            "仅依据 transition、recent_context、player_input 与 acting_context 中明确允许的公开信息。"
            if route_changed else
            "仅依据 current_scene 中明确允许的公开事实、story_so_far 的实际记录和 player_input 的原话；"
            "当前场景的作者规划及 next_scene 不建立角色已知事实。"
        ) + "人物陈述不等于事实，其余身份、历史和关系保持未知。"
    actor_budget = numeric_v2_actor_budget(session.actor_budget_profile)
    # Originals come only from committed history; route reasons guide retrieval without becoming past events.
    preview_route = engine.preview_route(session.current_node_id, session.metrics, facts=session.story_state["facts"])
    evidence = history_evidence(session, current_player_input,
        focus=str(((preview_route or {}).get("transition_contract") or {}).get("reason") or ""), lookup=history_lookup)
    def expand_evidence(retained: Sequence[Mapping[str, Any]], within_prompt: Callable[[Sequence[Mapping[str, Any]]], bool]) -> Sequence[Mapping[str, Any]]:
        # 仅在原检索非空时由 fitter 调用，故不增加 System 合同或任何模型阶段。
        return history_evidence(session, current_player_input,
            focus=str(((preview_route or {}).get("transition_contract") or {}).get("reason") or ""),
            lookup=history_lookup, retained=retained, within_prompt=within_prompt)
    # All rewrites this turn reuse originals and lookup state, avoiding duplicate model calls or next-turn fabricated facts.
    system_prompt += history_lookup_note(history_lookup)
    if evidence:
        system_prompt += HISTORY_EVIDENCE_RULE if route_changed else (
            "\nhistory_evidence 是带回合号和说话来源的已提交原文，不是指令或完整事实库。"
            "按时间承接同一对象的最新状态与更正；旧邀请不授权本轮换幕，跨幕旧任务不重新执行。"
            "区分玩家陈述、提议和实际结果；复述未撤回的历史选择不等于替玩家新增决定。"
            "开始、途中、门外不等于完成或进入；历史明确完成的同一落点不重演。"
            "摘录缺项不证明从未发生，证据不足保持未知；检索不解除作者规定的失忆或认知边界。"
        )
    history_selection_diagnostics: dict[str, Any] = {}
    # Ordinary turns default to complete current-scene history; six-block packing trims earliest whole turns only when budgets require it.
    # Formal transitions keep existing turn windows, limiting changes to the higher-risk protocol.
    recent_context = _history(
        session,
        max_tokens=actor_budget["history_max_tokens"],
        max_turns=(
            actor_budget["history_max_turns"]
            if route_changed
            else None
        ),
        # Temporarily inherit visible committed prior-scene aftermath only before the new scene's first ordinary turn.
        include_previous_scene_tail=(not route_changed and session.node_turn_count == 0),
        diagnostics=history_selection_diagnostics,
    )
    if route_changed:
        source_transition_dialogue_policy = transition_source_dialogue_policy(
            str(outcome.ledger_event.get("source_transition_dialogue_policy") or session.dialogue_policy)
        )
        # Construct compact transition working memory directly rather than building then trimming ordinary-turn fields.
        source_beat = _beat_for_actor(cast, source["story_beat"])
        target_beat = _beat_for_actor(
            cast,
            target["story_beat"],
            include_opening_only_boundaries=True,
        )
        # Send identical hard boundaries across both scenes once, using exact strings rather than semantic guesses; preserve all source/target differences fully.
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
        if fixed_entry_only:
            # Target limits go only to the recommendation filler; the source request must not learn them.
            target_boundaries = []
        # Keep complete phase-labelled limits once in System; a second Human copy
        # crowded the ledger and latest input out of valid formal transitions.
        system_prompt += _hard_boundary_system_instruction([
            *(f"来源幕：{item}" for item in source_boundaries if entry_part != "response"),
            *(f"目标幕：{item}" for item in target_boundaries),
            *shared_boundaries,
        ], phase="transition_compact")
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
            target_chapter_title="" if fixed_entry_only else _chapter_title_for_actor(cast, target),
            runtime_target_opening="" if fixed_entry_only else target_opening,
            transition_contract=transition_contract,
            recent_context=recent_context,
            acting_context=_acting_context(
                engine,
                cast,
                source,
                outcome.session.metrics,
                character_profile,
                relationship_metrics=session.metrics,
                target=None if fixed_entry_only else target,
                dialogue_policy=source_transition_dialogue_policy,
                target_dialogue_policy=outcome.session.dialogue_policy,
            ),
            player_input=current_player_input,
        )
        if evidence:
            data["history_evidence"] = evidence
        final_transition = data.pop("transition")
        if any(isinstance(item, Mapping) for item in final_transition["must_deliver"]):
            system_prompt += BRIDGE_DELIVERY_REFERENCE_RULE
        if fixed_entry_only:
            # Program-delivered entry: the source request must not carry target state,
            # target opening or target boundaries. The author bridge contract remains intact.
            final_transition.pop("target_scene", None)
            if data["acting_context"].get("capability_state"):
                system_prompt += "\n" + _CAPABILITY_LABEL_RULE
        if program_entry_pieces:
            final_transition["program_entry_delivery"] = {
                "status": "scheduled_this_draft",
                "after": "target_entry_trigger" if entry_trigger_required else "target_scene_narration",
                "before": "target_performance",
                "fixed_narrations": program_entry_pieces,
            }
        final_transition["transition_intent"] = str(outcome.ledger_event.get("transition_intent") or "unclear")
        if final_transition["transition_intent"] == "accept":
            invitation = pending_transition_record(
                session, ledger_events=recent_ledger_events, include_withdrawn=True,
            )
            silent_choice = recorded_silent_choice(invitation, recent_ledger_events)
            if silent_choice is not None:
                final_transition["pending_invitation"] = {
                    "kind": "program_silent", "revision": invitation.get("revision"),
                    **({"accept_input_ref": "player_input"} if silent_choice and silent_choice == current_player_input
                       else {"accept_input": silent_choice}),
                }
                system_prompt += ("pending_invitation.kind=program_silent 时只按程序展示的按钮与本次授权承接；"
                                  "accept_input_ref 指向实际原话，空值不补造原安排。按钮不是角色台词或先前已实施的行动。")
            else:
                offer_text = recorded_transition_offer(invitation) if invitation is not None else ""
                final_transition["pending_invitation"] = (
                    {"revision": invitation.get("revision"),
                     **({"offer_text": offer_text} if offer_text else {"content": performance_content_blocks(invitation)})}
                    if invitation is not None else None
                )
        if outcome.session.status == "ended":
            # Ordinary targets deliver opening only, saving full direction for later turns; endings lack further input and retain closing material.
            if "target_scene" in final_transition:
                final_transition["target_scene"]["story_direction"] = str(
                    target_beat.get("scene_direction") or ""
                )
            # Terminal commits close input immediately, so generate no unsendable buttons or pending conversational questions.
            system_prompt += ("本轮进入终局，交付后不再接收输入；只承接已冻结前段和原文完成目标收束，"
                              "不留下等待玩家回应的新任务。" if entry_part == "response" else (
                "本轮进入终局，交付后不再接收输入。正文自然收住，不留下等待玩家回答的新问题。"
                # Ending state must not hide source dialogue that still treats this turn's authorized action as pending.
                "只有尚未交付的最后互动、本轮新增的在场 NPC 问答或必要直接结果才在来源段交付；"
                + ("已在历史中完成的互动不再重演，来源回应可以为空；目标段只播放程序原文，不再补写目标回应。"
                   if fixed_entry_only else
                   "已在历史中完成的互动不再重演，来源回应可以为空，由目标段一次承接已成立结果。")
                + "不能退回递工具、催促实施或以完成为条件等待玩家。"
                # Ending delivery follows the authored closing material, without requiring another appraisal of completed source work.
                + ("按本次来源与桥段合同收住，不另发邀请。" if fixed_entry_only else
                   "按目标开场和结局方向完成收束，不发出新的邀请。")
            ))
        if outcome.ledger_event.get("transition_intent") == "initiate":
            # Proactive player requests need no invented prior invitation; authorization covers only this movement/stage start.
            final_transition["player_initiated"] = True
            system_prompt += ("" if entry_part == "response" else (
                "本次转场由玩家主动发起，直接回应其已公开去向的请求并承接作者桥段；"
                "不声称玩家接受了未提出的邀请，不再次征求这次转场的确认，不替玩家完成目标幕仍待决定的后续操作。"
            ))
        if outcome.session.status == "ended" and outcome.ledger_event.get("natural_ending_ready") is True and outcome.ledger_event.get("transition_intent") not in {"accept", "initiate"}:
            # Distinguish ordinary accepted transitions so Actor cannot portray natural runtime endings as player acceptance of a proposal.
            final_transition["natural_ending"] = True
            # Same-turn endings still deliver authorized final interaction/reactions before closing, never skipping from initiation straight to ending.
            system_prompt += ("" if entry_part == "response" else (
                "本轮程序已授权自然结局：最后互动尚未交付时，先在来源回应完成玩家已授权的最后互动、"
                "角色配合与必要反应，再承接作者桥段和结局余韵；已经完成的动作不重演。"
                "必须交付已知条件支持的直接结果，不得跳过最后互动直接宣告结束；"
                "不声称玩家接受了未提出的邀请，不补写玩家新行动、承诺，不再发出结束确认或后续任务。"
            ))
        if pending_ending_facts and entry_part != "response":
            final_transition["source_fact_targets"] = [
                {**row, "description": cast.text(definition["description"]),
                 **({"evidence_anchor": definition["evidence_anchor"]} if "evidence_anchor" in definition else {})}
                for row in pending_ending_facts
                for definition in (engine.fact_contract[row["key"]],)
            ]
            system_prompt += (
                "\ntransition.source_fact_targets 是本幕尚未登记的结果，不证明尚未发生。先核对实际历史，"
                "已经发生的操作不重演；只差可观察的最后状态且已有授权和条件时，在来源段交付该状态。"
                "仅在最终来源段已实际交付完整结果时，"
                "逐项填写 key、value、evidence_quote。键和值按合同原样填写；引文逐字取自来源段的旁白或角色演出，"
                "不用玩家输入、桥段或结局余韵。evidence_anchor 存在时须覆盖每组所需短语。没有结果填[]，不重演或补造。"
            )
        # Carry prior public results into transitions too; this turn's unreviewed candidates are not committed history.
        committed_completion = None
        if entry_part != "response" and not any(_acting_contract_restricts_knowledge(data["acting_context"].get(key) or {})
                   for key in ("acting_contract", "target_acting_contract")):
            committed_completion = _completion_fact_prompt_context(
                engine, source, replace(outcome, session=session), cast=cast,
                approved_facts=approved_completion_facts,
            )
        if committed_completion is not None:
            completed_facts = [
                row for row in committed_completion["all"]
                if row["visibility"] == "public" and row["satisfied"]
                and (row["committed"] or row.get("approved_this_turn") or row.get("validated_this_turn"))
            ]
            if completed_facts:
                # Missing occurrence bindings do not undo registered completion.
                # Expose only values, never a guessed round of supporting prose.
                final_transition["source_committed_completion"] = {
                    "facts": completed_facts,
                }
                validated_facts_present = any(row.get("validated_this_turn") for row in completed_facts)
                system_prompt += (
                    ("\ntransition.source_committed_completion 含已入账、预审通过或待本轮提交的公开完成值；"
                     if validated_facts_present else
                     "\ntransition.source_committed_completion 只含本轮前已入账或本轮独立预审通过的公开完成值；")
                    +
                    "approved_this_turn表示已审结果尚待整轮提交，按evidence所引输入或历史承接，不重演补账；"
                    "registration_revision仅表示入账回合，不证明事件发生回合、主体或过程。具体过程按实际历史核对，"
                    "原文缺失时保持未知；完成值不是待播放台词，也不证明物件当前位置或持有人；按全部历史承接最新状态，"
                    "不退回准备、催促或再次执行已完成项。"
                    "未列出的事项不等于未发生，仍须核对实际历史；入账值不授权改写历史。"
                )
                if validated_facts_present:
                    system_prompt += (
                        "validated_this_turn 是已校验来源、待本轮一并提交的结果，承接而不重演；"
                        "evidence.quote_ref 指向本轮 player_input 原话。"
                    )
        if transition_source is not None and entry_part != "response":
            final_transition["reviewed_source"] = {
                field: transition_source[field] for field in ("scene_narration", "performance", "fixed_narrations")
                if field in transition_source
            }
            # The source is already bound; do not issue its old author plan
            # again as work for the bridge/target-only request.
            final_transition.pop("source_scene", None)
            data["acting_context"].pop("source_author_entry_state", None)
            data["acting_context"].pop("source_author_entry_situation", None)
        if entry_prefix is not None:
            final_transition["entry_prefix"] = deepcopy(dict(entry_prefix))
        if entry_part == "response":
            # The prefix owns the source/bridge delivery. B receives only the target's
            # effective contract and author duties, plus the original authorization.
            target_context = _acting_context(
                engine, cast, target, outcome.session.metrics, character_profile,
                relationship_metrics=session.metrics, dialogue_policy=outcome.session.dialogue_policy,
            )
            target_context.pop("current_scene_state", None)
            target_state = target_context.pop("character_state", None)
            target_scene = final_transition["target_scene"]
            # A already delivered the entry. Resending its author template to B
            # encourages replaying its trigger after the immutable original.
            target_scene.pop("opening_situation", None)
            target_scene["performed_entry_ref"] = "entry_prefix.blocks"
            if target_state:
                target_scene["author_entry_plan"] = target_state
            data["acting_context"] = target_context
            final_transition = {key: value for key, value in final_transition.items() if key in {
                "authorized", "target_scene", "entry_prefix", "pending_invitation", "transition_intent",
                "player_initiated", "natural_ending",
            }}
        data["transition"] = final_transition
        final_transition["generation_scope"] = _generation_source_scope(
            data, player_name=cast.player_name, catgirl_name=catgirl_name)
        phase_boundary_text = system_prompt
        if "======以下为本轮作者硬边界======" in system_prompt:
            phase_boundary_text += "\n- ".join(prompt_data_block_payload(
                system_prompt, "本轮作者硬边界")["boundaries"])
        # Phase-labelled System boundaries are authoritative; omit only exact Human copies.
        for contract in (data["acting_context"].get("acting_contract", {}),
                         data["acting_context"].get("target_acting_contract", {})):
            if "forbidden_behaviors" in contract:
                contract["forbidden_behaviors"] = [text for text in contract["forbidden_behaviors"]
                                                  if text not in phase_boundary_text]
                if not contract["forbidden_behaviors"]:
                    contract.pop("forbidden_behaviors")
        for state in (data["acting_context"].get("source_author_entry_state", {}),
                      (final_transition.get("target_scene") or {}).get("author_entry_plan", {})):
            if "scene_boundaries" in state:
                state["scene_boundaries"] = [text for text in state["scene_boundaries"] if text not in phase_boundary_text]
                if not state["scene_boundaries"]:
                    state.pop("scene_boundaries")
        system_prompt += player_action_note
        if retry_hint:
            # Formal repairs, like ordinary ones, follow the complete contracts.
            system_prompt += "\n本轮是输出重试：\n" + prompt_data_block("本轮重试反馈", {"retry_hint": retry_hint})
        data["character_name"] = catgirl_name
        packing_diagnostics: dict[str, Any] = {}
        data = _fit_turn_prompt_data(
            system_prompt=system_prompt,
            human_prefix=human_prefix,
            data=data,
            max_tokens=actor_budget["input_max_tokens"],
            diagnostics=packing_diagnostics,
            render_committed_history=True,
            evidence_expander=expand_evidence,
        )
        if evidence and not data.get("history_evidence"):
            system_prompt = system_prompt.replace(HISTORY_EVIDENCE_RULE, "", 1)
        repair_draft = draft_repair_context.get("draft") if draft_repair_context is not None else None
        if retry_hint and isinstance(repair_draft, Mapping) and {
            "source_performance", "bridge_scene_narration", "target_scene_narration", "target_performance",
        }.issubset(repair_draft):
            human_prefix, data = _append_draft_repair(
                system_prompt=system_prompt, human_prefix=human_prefix, data=data,
                material=draft_repair_context, max_tokens=actor_budget["input_max_tokens"],
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
            theater_system_message(system_prompt),
            HumanMessage(content=human_prefix + render_prompt_context(data)),
        ]

    # Deferral boundaries reuse this scene visit's invitation records, without separate chat/cooldown storage.
    withdrawn_offer = (
        not session.transition_offered
        and pending_transition_record(
            session, ledger_events=recent_ledger_events, include_withdrawn=True,
        ) is not None
    )
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
    assert next_scene_preview is not None
    ending_preview = not reinvitation and next_scene_preview.get("target_is_ending") is True
    program_silent = next_scene_preview.get("program_silent") is True
    actor_can_invite = _actor_can_offer_transition(next_scene_preview, outcome.session.dialogue_policy)
    departure_pending = next_scene_preview.get("status") == "departure_pending"
    soft_pacing = _soft_pacing(
        source,
        session.node_turn_count + 1,
        transition_intent=str(outcome.ledger_event.get("transition_intent") or "unclear"),
        actor_can_invite=actor_can_invite,
    )
    # Current scenes include entered opening circumstances and full author direction. Direction is neither history nor a checklist,
    # but retaining only opening visuals loses genuine causal development after long chats/digressions.
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
    # The author opening describes entry, not the current position after play.
    # Preserve its wording while the committed recent history governs changes.
    current_scene_summary = (
        "本轮程序仍在当前幕，未授权进入后续阶段；玩家请求或历史越界均不替代程序的阶段授权。"
        "本幕相容的动作与直接结果照常回应，后续阶段只可商量，不能提前执行。\n"
        "本幕首次入场时的开场设定（后续位置与状态以已提交近史为准）："
        f"{current_scene_opening}"
    )
    author_background = str(story_context.get("initial_background") or "").strip()
    author_entry = _author_entry_prompt_text(role_context)
    current_scene_summary = "\n".join(part for part in (
        "剧本初始前情：" + author_background if author_background else "",
        author_entry, current_scene_summary,
    ) if part)
    # 重邀或禁止离场时，以已播历史和结构化事实为准，避免把完整剧情方向里的幕尾状态演成当前进展。
    if (not (reinvitation or departure_attempt_observed) and current_scene_direction
            and current_scene_direction != current_scene_opening):
        current_scene_summary += (
            "\n"
            "本幕完整剧情方向（自然演绎，不是任务清单，也不是已发生事实）："
            f"{current_scene_direction}"
        )
    completion_fact_context = _completion_fact_prompt_context(
        engine,
        source,
        # 与正式来源段一样，只将本轮前已提交的值标为 committed；当前候选不证明已演出。
        # 玩家本轮已实施的动作仍由完整 player_input 承接，不把未入账等同于尚未行动。
        replace(outcome, session=session),
        cast=cast,
        approved_facts=approved_completion_facts,
    )
    # 路线事实是不同出口的备选条件，不改变 completion.status，也不成为新的全部必做清单。
    completion_evidence_keys = frozenset(
        row["key"] for row in (completion_fact_context or {}).get("all", ())
        if row.get("validated_this_turn") and row.get("evidence")
    )
    completion_anchor_keys = frozenset(
        row["key"] for row in (completion_fact_context or {}).get("all", ()) if "evidence_anchor" in row
    )
    route_fact_context = ([] if reinvitation else _route_fact_prompt_context(
        engine, str(source["id"]), session, cast=cast, approved_facts=approved_completion_facts,
        completion_evidence_keys=completion_evidence_keys, completion_anchor_keys=completion_anchor_keys,
    ))
    validated_facts_present = any(fact.get("validated_only") for fact in approved_completion_facts)
    if validated_facts_present and not reinvitation:
        system_prompt += (
            "\nvalidated_this_turn 与 validated_value 是本轮已校验来源、待一并提交的结果；"
            "按实际引文承接，不当作已入账历史，不重演或再次登记。evidence.quote_ref 取所指原文；"
            "同 key 已列在幕完成条件中的证据可同时用于路线条件。"
        )
    staged_history_quotes = tuple(dict.fromkeys(
        cast.text(item["quote"]) for claim in approved_completion_facts if claim.get("validated_only")
        for item in claim.get("evidence", ()) if item.get("source") == "actor_performance" and item.get("quote")
    )) if not reinvitation else ()
    staged_player_history_quotes = tuple(dict.fromkeys(
        item["quote"] for claim in approved_completion_facts if claim.get("validated_only")
        for item in claim.get("evidence", ()) if item.get("source") == "committed_player_input" and item.get("quote")
    )) if not reinvitation else ()
    if route_fact_context:
        current_scene_summary += (
            "\n当前幕路线事实条件（按route_id分组，仅列本幕公开事实子集；condition_mode保留作者all/any，"
            "同key的类型、来源和说明各只定义一次，逐路线目标值与状态分别保留；"
            "不代表必须满足所有出口，也不证明整条路线已获准。其他条件和最终出口由程序核对；"
            "目标值不是已发生事实，未登记不证明未发生，先核对实际历史；不为选中出口补造结果）："
            + json.dumps(route_fact_context, ensure_ascii=False, separators=(",", ":"))
        )
        # 仅在本次确实提供路线事实时扩展原输出合同，普通无路线事实的 wire 保持不变。
        system_prompt = system_prompt.replace(
            "如果输入提供结构化幕完成事实，", "如果输入提供结构化幕完成事实或当前幕路线事实，", 1,
        ).replace("输入未提供结构化幕完成事实时，", "输入未提供上述结构化事实时，", 1)
        if combined_evaluation and any(
            not row["committed"] and isinstance(row["value"], str)
            and engine.fact_contract[row["key"]].get("evidence_source") != "performance"
            for group in route_fact_context for row in group["facts"]
        ):
            system_prompt += (
                "\n仅在 turn_evaluation.fact_candidates 补记未入账的本幕路线选择。"
                "只可逐字引用 story_so_far 中本次到访时作出且未撤回或改变的完整玩家原话；"
                "角色先前给出的选项不是玩家选择，旧话也不授权本轮出发。"
            )
    if completion_fact_context is not None:
        if completion_fact_context.get("fixed_narrations", {}).get("unavailable"):
            system_prompt += (
                "\n本幕有完成条件依赖当前无法播放的固定旁白。它尚未满足，"
                "不得代写旁白、假称已展示，或反复要求玩家重新触发；只回应眼前能交付的内容。"
            )
        # Completion contracts are author control data, never history; Actor proposes only results it actually performed this round.
        current_scene_summary += (
            ("\n已提交幕完成值（只读）" if reinvitation else
             "\n结构化幕完成事实" if completion_fact_context["all"] else "\n程序展示完成条件")
            + "（description仅描述未完成目标，committed=true表示值已入账；"
            "approved_this_turn=true表示输入或历史结果已独立预审、本轮尚待原子提交，不再要求重新执行；"
            "registration_revision仅表示入账回合，不证明事件发生回合、主体或过程，具体细节按实际历史核对；"
            "原文缺失时不从作者计划补造；入账值不授权改写历史）："
            + json.dumps(completion_fact_context, ensure_ascii=False, separators=(",", ":"))
        )
        if "fixed_narrations" in completion_fact_context:
            current_scene_summary += (
                "。fixed_narrations.required/displayed为程序要求/已展示的原文片段数，"
                "只由真实展示记录决定，不由正文自称完成或fact_candidates更新。"
            )
    if ((completion_fact_context is not None and completion_fact_context["all"]) or route_fact_context) and not reinvitation:
        system_prompt += (
            ("\n结构化事实候选合同（仅在输入提供幕完成事实或当前幕路线事实时适用）：" if route_fact_context else
             "\n结构化事实候选合同（仅在输入提供幕完成事实时适用）：")
            + ("候选必须有实际证据证明该条描述的全部条件已成立。" if combined_evaluation else
               "逐项核对尚未登记的完成事实。实际证据表明该条描述的全部完成条件已成立，且本轮 performance 或 scene_update 交付了该结果时，必须逐项写入顶层 fact_candidates。")
            + "完成一项就登记一项，不等整幕完成。准备、开始或部分完成不能证明整项完成。"
            + ("双方选定仍须有各方实际同意。" if combined_evaluation else
               "角色提议只证明提出；玩家同意或双方选定须核对玩家已表达的选择或同意，不以角色单方面提议为证。")
            + ("不得为了满足条件而补造正文或提前写入计划；已登记同值不重复提交，"
               "改变分支选择必须有本轮实际授权或结果支持，不能用旧史倒改当前选择。" if route_fact_context else
               "不得为了满足完成条件而补造正文、提前写入计划或重复提交 committed=true 的事实。")
            + ("key 和 value 必须匹配输入合同。" if combined_evaluation else
               "每项必须且只能包含 key、value、evidence_quote，准确形状为"
               "{\"key\":\"合同中的键\",\"value\":合同目标值,\"evidence_quote\":\"本轮最终正文中的逐字引文\"}。"
               "key 和 value 必须匹配输入合同；evidence_quote 必须逐字出现在本轮最终可见正文中。")
            + ("有 evidence_anchor 时，同一实际结果引文须包含完整短语。若为分组，须按顺序每组选中至少一条原文。"
               if completion_anchor_keys or any("evidence_anchor" in row for group in route_fact_context
                                                for row in group["facts"]) else "")
            + ("" if combined_evaluation else "即使本轮没有新确认事实，也必须返回 fact_candidates:[]，不得省略字段。")
            + ("完成合同与各路线条件分别核对，不把多个出口当成全部必做，也不替玩家选择分支；"
               "候选key/value只取相应合同原值，保留类型和拼写。"
               "NPC可在自身权限和已具备前因内回应当前问题，不能由玩家意向代替NPC许可或结果。"
               "合法候选不等于已登记或获准换幕。"
               if route_fact_context else
               "候选本身也不授权换幕。")
            + (("只按next_scene中程序实际给出的出口提出邀请。" if route_fact_context else
                "候选可补足本轮提出邀请的完成前提；仍缺事实时不得提前提议。")
               if actor_can_invite and not retry_hint else "")
        )
    hard_boundaries = _suggestion_hard_boundaries(cast, source["story_beat"])
    # Put precise boundaries in System for adherence; Human six-block context carries character/plot/history/current input only, avoiding duplicate tokens.
    system_prompt += _hard_boundary_system_instruction(hard_boundaries, source_scene_locked=departure_pending)
    if input_source == "suggestion":
        # Recommendation clicks follow the previous public route but still validate action authorization against actual player wording.
        system_prompt += (
            "\n推荐承接合同（本轮必须遵守）：玩家点击了你上一轮公开给出的推荐。"
            "与自由输入适用同一行动授权：承接已经明确表达的动作与对白，不能补出玩家未表达的后续操作。"
            "不要重新询问已做出的选择；若旧推荐有误，自然澄清可行做法，不得为了兑现推荐而越界。"
            + ("若这项选择本身会结束当前互动、进入下一时段或下一场景，且当前尚无待确认提议，"
               "在 transition_offer 说清下一步并等玩家确认。"
               if actor_can_invite else
               "交付本幕已获授权的行动及其直接结果，换幕仍由程序处理。")
        )
    if not reinvitation and not actor_can_invite:
        system_prompt += (
            "\n本轮 transition_offer 必须为空字符串。"
            + ("此出口由程序在满足完成条件后提供静默选择；不要自行写邀请或接受按钮。"
               if program_silent else
               "" if ending_preview else
               "本轮只回应与交付本幕结果。"
               if next_scene_preview.get("status") != "after_acceptance_only" else
               "只用允许的动作和场景变化回应，不补口头邀请。")
        )
    pacing_header = (
        f"当前是第 {int(soft_pacing['current_turn'])} 回合，本幕推荐 "
        f"{int(soft_pacing['recommended_turns'])} 回合。"
    )
    pacing_text = pacing_header
    # Centralize fixed facts/ownership in System; project only this turn's input mode, pacing, and proposal state here.
    if (source_delivery_task or {}).get("kind") == "shared_agreement" and not departure_pending:
        pacing_text += "回合数只供参考；本轮自然回应当前提议的态度和条件，不催促执行后续行动。"
    elif departure_pending:
        pacing_text += "本轮按舞台范围回应；推荐轮数只供参考。"
    elif ending_preview and soft_pacing["phase"] != "after_reject":
        pacing_text += "结局尚未获准，推荐回合只供节奏参考。"
    else:
        pacing_text += str(soft_pacing["instruction"])
    if (not ending_preview and not departure_pending
            and (source_delivery_task or {}).get("kind") != "shared_agreement"):
        pacing_text += "按本幕边界交付已获授权的结果；待确认的跨幕行动只商定做法。"
    if withdrawn_offer:
        pacing_text += (
            "不自动恢复历史撤回的提议，也不把历史撤回当作本轮仍在拒绝。"
            "玩家重新询问下一步或当前情境已改变时，按当轮意图商量相应安排；"
            "不要重复催促旧安排，新的跨阶段安排仍须公开并等待接受。"
        )
    natural_closure_ready = (
        not reinvitation
        and not retry_hint
        and outcome.ledger_event.get("scene_complete") is True
        and (
            completion_fact_context is None
            or completion_fact_context.get("status") == "satisfied"
        )
        and not session.transition_offered
        and outcome.ledger_event.get("transition_intent") != "reject"
        and not withdrawn_offer
        and next_scene_preview.get("status") == "after_acceptance_only"
        and not next_scene_preview.get("target_is_ending")
    )
    completion_closure_ready = (
        not reinvitation
        and not retry_hint
        and isinstance(completion_fact_context, Mapping)
        and completion_fact_context.get("status") == "satisfied"
        and not route_changed
        and not session.transition_offered
        and outcome.ledger_event.get("transition_intent") != "reject"
        and not withdrawn_offer
        and next_scene_preview.get("status") == "after_acceptance_only"
        and not next_scene_preview.get("target_is_ending")
    )
    closure_offer = (
        str(next_scene_preview.get("authored_offer") or "")
        if actor_can_invite and (completion_closure_ready or natural_closure_ready) else ""
    )
    entry_offer_published = False
    legacy_entry_offer_check = False
    if closure_offer:
        records, entered = current_scene_records(session)
        if entered or (session.current_node_id == engine.story["start_node_id"] and len(records) == session.revision):
            fixed_entry_texts = current_entry_fixed_narrations(session)
            entry_offer_published = any(closure_offer in text for text in fixed_entry_texts)
            # Old packages can use paraphrased fixed originals. Preserve their
            # model comparison only when all exits share one authored offer.
            legacy_entry_offer_check = bool(fixed_entry_texts and not entry_offer_published) and all(
                cast.text(str(gate["transition_contract"].get("fallback_offer") or "")).strip() == closure_offer
                for gate in source["route_gates"]
            )
    if (completion_closure_ready or natural_closure_ready) and not actor_can_invite:
        system_prompt += (
            "\n本幕已有收束结果，按当前发声权限回应玩家，不重做已经交付的结果，不追加任务或等待。"
        )
        pacing_text = pacing_header + "本轮收束已有互动。"
    elif entry_offer_published or legacy_entry_offer_check:
        system_prompt += (
            ("\n本轮确定性完成收束合同：当前幕完成条件已满足。" if completion_closure_ready
             else "\n本轮自然收束合同：本幕核心变化已成立。")
            + ("本次入幕原文已实际公开当前出口的邀约。只承接玩家的追问、暂缓或回应，"
               "transition_offer 留空，不换词重发同一邀请。"
               if entry_offer_published else
               "本幕入场原文已展示。先对照 story_so_far 中的原文，再回应玩家。"
               "原文已公开同一安排时只承接其追问、暂缓或回应，transition_offer 留空，不换词重发同一邀请。"
               "仅列出可选方向不算发出邀约。确实尚未公开且玩家未暂缓时，按 next_scene 提出邀请并等玩家决定。")
        )
        pacing_text = pacing_header + "从入场原文已经交付的结果继续，不重演、不追加任务。"
    elif completion_closure_ready:
        # Completion state comes from Runtime-adjudicated facts, never Actor guesses from prose/scene_complete.
        # Require public future invitations only; actual transition still needs explicit next-turn player acceptance and Runtime routing.
        # Rewrites retain completed facts but must not also receive a conflicting demand to propose another invitation.
        system_prompt += (
            "\n本轮确定性完成收束合同：当前幕 completion_contract 已在生成演出前全部满足。"
            + ("不要重复演出、重新检查这些已有结果；" if validated_facts_present
               else "不要重复演出、重新检查或质疑这些已提交结果；")
            + "先回应玩家当前输入。玩家明确暂缓或还有必须先答的独立问题时留在本幕；其他情况依据 next_scene "
            "在 transition_offer 公开一个具体的未来跨阶段行动并等待玩家决定。"
            "只提出邀请，不得写成玩家已接受、双方已出发或下一阶段已经发生。"
        )
        pacing_text = pacing_header + "本轮按确定性完成收束合同回应，不再补当前幕任务。"
    elif natural_closure_ready:
        # Natural closure is not a Runtime transition condition; Actor merely makes mature causal progress into public proposals players can answer.
        # Explicit closure contracts prevent lengthy character backgrounds recreating resolved emotional barriers.
        system_prompt += (
            "\n本轮自然收束合同：核心变化已成立，先回应玩家。玩家明确暂缓或还有必须先答的独立问题时留在本幕；"
            "其他情况在 transition_offer 提出 next_scene 支持的具体跨阶段行动，停在玩家可接受或暂缓的位置。"
            "性格只影响表达，不重新否认已完成的变化；"
            "没有作者支持的未决事实时，不新增等待、复查或必须继续安抚的前提。"
        )
        pacing_text = pacing_header + "本轮按自然收束合同回应，不自动换幕。"
    # Retain explicit author focus only; legacy transition_goal often says acquired/connected,
    # and fallback must not mix unmet exit conditions into current pacing. Full plot remains current_scene.
    narrative_focus = cast.text(str(source["story_beat"].get("narrative_focus") or "")).strip()
    if narrative_focus and not completion_closure_ready and not reinvitation:
        system_prompt += (
            ("\n以下导演重点只供参考，不是历史或新权限。\n" if ending_preview or departure_pending else
             "\n以下导演重点与结构化幕完成事实说明待演结果，不是历史或新权限。先回应玩家，"
             "再连贯交付前因已具备、属于你或环境职责的必要事件及直接结果；"
             "不等玩家猜隐藏信息，不用自造检查或调查链替换本幕内容。"
             "需要玩家选择时停下，尊重明确暂缓。\n")
            + prompt_data_block("本幕导演重点", {"narrative_focus": narrative_focus})
        )
    if (
        not ending_preview and not departure_pending
        and (source_delivery_task or {}).get("kind") != "shared_agreement"
        and not session.transition_offered
        and not completion_closure_ready
        and not natural_closure_ready
        and outcome.ledger_event.get("transition_intent") != "reject"
        and not withdrawn_offer
        and int(soft_pacing["current_turn"]) >= int(soft_pacing["recommended_turns"])
    ):
        pacing_text += (
            "对照本幕方向与已提交历史：核心冲突尚未清楚时先交付关键事实；"
            "已清楚时让连续行动落到结果或自然出口。优先交付一个尚缺的关键结果或直接前因，普通评价随结果收束。"
            + ("推荐从已有结果后的取舍开始，不新增中间条件。"
               if next_scene_preview.get("status") == "after_acceptance_only" else "不新增中间条件。")
        )
    invalidated_invitation = outcome.ledger_event.get("transition_offer_invalidated") is True
    origin = (pending_transition_record(session, ledger_events=recent_ledger_events,
        include_withdrawn=invalidated_invitation) if session.transition_offered or invalidated_invitation else None)
    silent_choice = recorded_silent_choice(origin, recent_ledger_events)
    if session.transition_offered and silent_choice is None and not invalidated_invitation:
        # Existing visible proposals without formal transition still cannot make target locations/results already happened.
        pacing_text += (
            "上一轮已有待确认提议，本回合尚未完成换幕；留在本幕回应，不重复催促。"
        )
        # Pending confirmation means previously public, not mandatory acceptance buttons for invalid invitations merely because latched.
        if next_scene_preview.get("status") == "after_acceptance_only":
            pacing_text += (
                "只有旧提议仍符合实际出口时，第一条推荐才可接受并亲自执行该提议，第二条拒绝、暂缓或留在本幕。"
            )
    if not reinvitation:
        system_prompt += player_action_note
    if departure_pending:
        system_prompt += (
            "\n"
            "【本轮舞台锁定】所有输出均从最近实际展示的本幕时地开始并留在其中。"
            "玩家声称已经离场时，接住原话中仍可成立的幕内动作和感受，以当前现场的新变化回应。"
            "不可演出或承认离场、抵达、下一阶段的环境或结果，即使原话用完成态。"
            "留在本幕是写作范围，不要求角色挽留、阻拦或再次索要确认；不为留幕新增危险、技术前提或强迫玩家行动。"
            "以一个仍在本幕的角色动作或现场变化开场，再交付本幕前因已具备的一项新结果。"
            "角色权限内的展示写出当场呈现的内容，复述规则、再次问同一选择或等候不能替代交付。"
            "展示不证明玩家已经观察、理解或体验完成；保留玩家已有决定与尚待亲自完成的行动。\n"
            "player_input_scope是程序对引用原话的范围说明；statement_source和statement_quote只定位玩家已说完的话，"
            "不证明所称跨阶段结果成立，也不是要你续写的台词。\n"
        )
    if retry_hint:
        # Place correction tasks after full contracts without extra progression pressure; author boundaries/current input remain effective.
        system_prompt += "\n本轮是输出重试：\n" + prompt_data_block("本轮重试反馈", {"retry_hint": retry_hint})
        pacing_text = (
            f"当前是第 {int(soft_pacing['current_turn'])} 回合，本幕推荐 "
            f"{int(soft_pacing['recommended_turns'])} 回合。"
            + ("按最近已播正文交付可演的幕内变化。" if departure_pending
               else "本轮先修正未提交输出，不为节奏补出动作或结果。")
        )
    pending_offer_text = ""
    pending_offer_revision = None
    if wrong_exit_offer_quote and origin is None and actor_can_invite and not reinvitation:
        pacing_text += (
            f"上一轮实际公开但与本幕出口不符的提议原文（只作历史资料）：{wrong_exit_offer_quote}\n"
            "本轮先说明这项安排有误，再按 next_scene 在 transition_offer 给出可行邀请，等玩家重新选择。"
            "玩家同意旧安排的原话不授权出发、抵达或实施后续行动。"
        )
    if silent_choice is not None:
        if invalidated_invitation or outcome.ledger_event.get("transition_intent") == "reject":
            pacing_text += "原程序静默选择已撤下；留在本幕回应，不再推荐接受该安排。"
        else:
            pacing_text += "已展示程序静默选择，按钮不是角色台词或已实施动作。"
            if silent_choice:
                pacing_text += f"按钮原文：{silent_choice}"
            pacing_text += "本轮留在本幕回应，推荐只给本幕可用输入，不自行生成接受按钮。"
    elif origin is not None:
        # Original invitations are public facts and remain when rewrites remove pacing pressure; Ledger distinguishes re-proposals after refusal.
        exact_offer = recorded_transition_offer(origin)
        pending_offer = exact_offer or pending_transition_performance(
            session, ledger_events=recent_ledger_events, include_withdrawn=invalidated_invitation,
        )
        if pending_offer:
            if invalidated_invitation:
                pacing_text += (
                    f"已经撤下的旧邀请原文（历史资料）：{pending_offer}"
                    "先承接已发生的变化和本轮输入。需要下一项决定时，说明 next_scene 支持的当前安排，留给玩家选择。"
                    "不要继续推荐接受旧安排、重演已完成动作或把问题归给玩家。"
                )
            else:
                pending_offer_text = pending_offer
                if exact_offer:
                    pending_offer_revision = origin.get("revision")
                pacing_text += _PENDING_OFFER_PROMPT_LABEL + pending_offer
            # Old proposals may themselves be erroneous performance; never substitute another exit or blame player memory to maintain them.
            pacing_text += (
                "若原提议与 next_scene 的实际去向不相符，承认是自己先前说错并说明当前可行安排，"
                "保留玩家原意与重新选择权；不指责玩家误记，不悄悄执行不同去向，也不继续重复错误邀请。"
                "不为更正补造人物行程或新的阻碍；推荐承接本轮已澄清的安排，不推荐继续执行已指出错误的旧去向。"
            )
    # Keep packing fields stable; render four sections with player input last, reducing history overriding current requests.
    data: dict[str, Any] = {
        "role": _role_prompt_text(
            catgirl_name=catgirl_name,
            acting_context=role_context,
            story_context=story_context,
        ),
        "current_scene": current_scene_summary,
        # Fitter fills complete history before token checks; keep this block's wire position.
        "story_so_far": "",
        "pacing": pacing_text,
        "next_scene": (
            "本轮只用允许的动作和场景变化回应，不生成口头邀请。"
            if next_scene_preview.get("status") == "after_acceptance_only"
            and not actor_can_invite and not program_silent and not next_scene_preview.get("target_is_ending")
            else _next_scene_summary_text(
                next_scene_preview, authored_offer="" if entry_offer_published else closure_offer,
            )
        ),
        **({"player_input_scope": (
            "程序确认仍在最近已演的本幕。只接续玩家原话中与这个时地相容的动作或感受。"
            "跨幕声称不改变地点或完成状态。先演角色能交付的一项尚缺结果或它的直接前因，再说话；"
            "已有选择继续有效，后续抉择留给玩家。"
        )} if departure_pending else {}),
        "player_input": current_player_input,
    }
    if not reinvitation:
        data["current_scene"] += "\n本轮生成职责：" + json.dumps(_generation_source_scope(
            data, player_name=cast.player_name, catgirl_name=catgirl_name), ensure_ascii=False, separators=(",", ":"))
    if (departure_pending and unconfirmed_departure_quote
            and unconfirmed_departure_quote in current_player_input):
        data["player_input_scope"] = {
            "statement_source": "player_input", "statement_quote": unconfirmed_departure_quote,
            "stage_change_authorized": False,
            "scope": "其中跨阶段结果尚未成立；本幕相容的自主动作、感受和提问照常承接。",
        }
    if player_facts:
        data["player_fact_evidence"] = player_facts
    if source_delivery_task:
        data["source_delivery_task"] = dict(source_delivery_task)
    if not reinvitation and (entry_cause := current_entry_cause(session)):
        data["entry_cause"] = entry_cause
    if exit_mismatch_reason and not reinvitation and not departure_pending:
        data["next_scene"] += "\n本轮未授权换幕。出口核对说明（不是历史事实）：" + exit_mismatch_reason
        system_prompt += (
            "\n本轮出口核对只限制跨幕安排，普通场内问答和合法局部动作照常承接。"
            "当前请求的去向、时段或分工与出口不相容时，先回应并澄清可行安排，等玩家选择；"
            "不直接演成双方已按另一安排出发、抵达或完成下一阶段，也不替玩家改主意。"
            "判定说明不覆盖已发生的历史或玩家已经明确实施的动作。只说明与已公开安排的差异，"
            "不转述程序规则，不为解释差异编造剧中规章、危险或手续。"
        )
    if not reinvitation and (current_scene_task or (departure_pending and progress_direction)):
        data["current_scene_task"] = dict(current_scene_task) if current_scene_task else {"direction": progress_direction}
        system_prompt += (
            "\ncurrent_scene_task是程序或独立预审排定的当前幕作者任务。selected_id存在时仅指定本轮这一项，"
            "其余goals只作依赖背景；owner、source_ids和allowed_behaviors说明主体、作者引用与角色权限，"
            "不证明前因或结果已成立，也不要求执行全部目标。按已播历史与玩家意愿交付可行内容；"
            "仍需玩家行动或同意时留给玩家，不把这些调度资料写成剧中对白或旁白。"
        )
    elif progress_direction and not reinvitation:
        data["pacing"] += "\n本轮独立推进建议：" + json.dumps(progress_direction, ensure_ascii=False)
        system_prompt += (
            "\n推进建议仅用于选择本轮合法回应。按已演证据和玩家当前输入接续，"
            "在作者边界和既有授权内交付下一步。建议中的结果仍需实际演出，不能当成已经发生。"
        )
    human_prefix = (
        "末尾player_input是完整玩家原话；source_delivery_task只提示本幕待核验目标，先自然回应当前发言，"
        "其余请求待本幕审核通过后由程序接续。以最新已提交原文为起点，不把任务定义当成已发生结果。\n"
        if source_delivery_task else
        "以下是本轮演出依据。先按最新已提交原文确定当前现场，在程序给定的本幕范围内交付前因已具备的角色行动或环境变化；"
        "玩家原话中的幕内动作与感受照常承接，跨幕声称按意向回应。玩家尚未实施的观察、操作和反馈留待玩家输入。\n"
        if departure_pending else
        "末尾 player_input 是玩家刚结束的发言；从最新已提交原文和本轮独立预审证据确定当前起点，"
        "承接 player_input 的实际原话，再交付尚缺的回应。\n"
    )
    if reinvitation:
        # 重新邀请沿用六块原文、状态和装箱预算，只切换生成任务；不构成玩家发言或新事件。
        data["pacing"] = "本轮只重新邀请，不补当前幕任务、不执行换幕；保留玩家接受或暂缓的选择。"
        human_prefix = (
            "以下四块是本次重新邀请的真实演绎上下文。末尾 player_input 为空；"
            "由角色设定中的你在 performance 提出未来邀请，玩家选择由程序控件处理：\n"
        )
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
        evidence_records=evidence,
        fact_index=lambda: _current_scene_fact_index(session, query=player_input),
        fact_index_expander=lambda within: _current_scene_fact_index(
            session, query=player_input, within_prompt=within,
        ),
        pending_offer_text=pending_offer_text,
        pending_offer_revision=pending_offer_revision,
        diagnostics=packing_diagnostics,
        evidence_expander=expand_evidence,
        staged_history_quotes=staged_history_quotes,
        staged_player_history_quotes=staged_player_history_quotes,
        retained_player_inputs=combined_player_inputs if combined_evaluation else None,
    )
    if combined_history is not None:
        # Parser authority is limited to the history retained in this exact request.
        combined_history[:] = [str(data["story_so_far"])]
    if draft_repair_context is not None:
        packing_diagnostics["draft_repair"] = "omitted_phase" if reinvitation else "omitted_not_repair"
        if retry_hint and not reinvitation:
            # 旧稿是可省的编辑附件：原六块先完整装箱，不为它裁掉历史、原话或权限合同。
            human_prefix, data = _append_draft_repair(
                system_prompt=system_prompt, human_prefix=human_prefix, data=data,
                material=draft_repair_context, max_tokens=actor_budget["input_max_tokens"],
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
        theater_system_message(system_prompt),
        HumanMessage(content=human_prefix + render_prompt_context(data)),
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
    """Orchestrate Actor narrative, recommendations, and transition intent without Session write authority."""  # noqa: DOCSTRING_CJK

    def __init__(self, config_manager: Any):
        self.config_manager = config_manager
        # Count actual provider requests by this Actor instance; narrative resampling and lightweight recommendation calls count separately.
        self.provider_call_count = 0
        # Distinguish recommendation triggers from real provider cost to identify optimization opportunities in stress testing.
        self.suggestion_fill_attempt_count = 0
        self.suggestion_fill_provider_call_count = 0
        self.suggestion_fill_reason_counts = {
            "invalid_or_missing": 0,
        }
        self.base_suggestion_parse_counts: dict[str, int] = {}
        # Record structured-field adherence only, without candidate text or provider retries for missing fields.
        self.base_fact_candidate_parse_counts: dict[str, int] = {}

    def _character_profile(self) -> str:
        """Read current server-side neko persona summaries only; accept no client character names."""  # noqa: DOCSTRING_CJK

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
            return numeric_v2_authoring_names(self.config_manager)["catgirl_name"]
        except Exception as exc:
            raise NumericV2ActorUnavailableError("numeric_v2_actor_config_unavailable") from exc

    async def _ensure_suggestions(
        self,
        *,
        allow_fill: bool = True,
        performance: Mapping[str, Any],
        player_input: str,
        catgirl_name: str,
        max_input_tokens: int,
        hard_boundaries: list[str] | tuple[str, ...] = (),
        scene_changed: bool = False,
    ) -> list[str]:
        """Keep usable recommendations; optionally fill missing suggestions/incomplete new-invitation acceptance once."""  # noqa: DOCSTRING_CJK

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
        transition_offered = performance.get("transition_offered") is True
        # Ordinary single recommendations display immediately; new invitations may have deferral only and still use structured acceptance completion.
        if len(suggestions) in {2, 3} or (len(suggestions) == 1 and not transition_offered):
            return suggestions
        if not allow_fill:
            # Recommendation completion disabled: make no extra call; display Actor's actual output.
            # Add this key only when needed, leaving existing cause statistics unchanged when enabled.
            self.suggestion_fill_reason_counts["disabled"] = (
                self.suggestion_fill_reason_counts.get("disabled", 0) + 1)
            return suggestions
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
            # Recommendation failure cannot roll back narrative; retain even one existing valid suggestion,
            # preventing auxiliary-call glitches clearing every button.
            logger.warning(
                "Numeric v2 Actor suggestion fill failed: reason=%s",
                str(exc),
            )
            return suggestions
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
        if filled_suggestions:
            return filled_suggestions
        return suggestions

    async def generate_opening(
        self,
        *,
        engine: NumericV2Engine,
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
        retry_hint: str = "",
        allow_suggestion_fill: bool = False,
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
        entry_inputs = _authored_entry_player_inputs(start_cast, start_node["story_beat"])
        performance = await self._invoke(
            _opening_messages(
                engine,
                profile,
                catgirl_name,
                player_address,
                player_address_known,
                max_tokens=actor_budget["input_max_tokens"],
                retry_hint=retry_hint,
                condition_narrations_enabled=(await aload_theater_module_options())["review"],
            ),
            opening_required=True,
            max_input_tokens=actor_budget["input_max_tokens"],
            max_output_tokens=NUMERIC_V2_ACTOR_OPENING_MAX_OUTPUT_TOKENS,
            dialogue_policy=opening_dialogue_policy,
            author_entry_choices=entry_inputs is not None,
        )
        if entry_inputs is not None:
            performance = {**performance, "suggested_inputs": entry_inputs}
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
        # Submit valid primary-call recommendations directly; fill missing/malformed buttons once, avoiding fixed extra opening calls.
        performance["suggested_inputs"] = await self._ensure_suggestions(
            allow_fill=allow_suggestion_fill and entry_inputs is None,
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
        input_source: str = "freeform",
        recent_ledger_events: tuple[Mapping[str, Any], ...] = (),
        history_lookup: Mapping[str, Any] | None = None,
        allow_suggestion_fill: bool = False,
        approved_completion_facts: tuple[Mapping[str, Any], ...] = (),
        draft_repair_context: Mapping[str, Any] | None = None,
        defer_dialogue_repetition: bool = False,
        combined_evaluation: bool = False,
        combined_history_lookup: bool = False,
        wrong_exit_offer_quote: str = "",
        exit_mismatch_reason: str = "",
        progress_direction: str = "",
        unconfirmed_departure_quote: str = "",
        current_scene_task: Mapping[str, Any] | None = None,
        departure_attempt_observed: bool = False,
        transition_source: Mapping[str, Any] | None = None,
        source_delivery_task: Mapping[str, Any] | None = None,
        draft_provenance_sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if draft_provenance_sink is not None:
            draft_provenance_sink.clear()
        entry_provenance: dict[str, Any] | None = {} if draft_provenance_sink is not None else None
        # Workflow may freeze actual persona text used this turn, enabling precommit validation against the same generation.
        profile = (
            self._character_profile()
            if character_profile is None
            else str(character_profile)
        )
        catgirl_name = str(session.catgirl_binding.get("catgirl_name") or "").strip() or self._current_catgirl_name()
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
        if transition_source is not None and not route_changed:
            raise NumericV2ActorOutputError("numeric_v2_actor_transition_tail_invalid")
        reinvitation = input_source == "reinvite" and not route_changed
        source = engine.nodes[str(outcome.ledger_event["from_node_id"])]
        target = engine.nodes[str(outcome.ledger_event["to_node_id"])]
        cast = NumericV2CastProjection.from_story(
            engine.story,
            player_name=player_address,
            catgirl_name=catgirl_name,
        )
        # Finalization only needs the formal target opening; ordinary turns consume no target beat here.
        target_opening = (
            scene_opening_text(cast.value(target["story_beat"]))
            if route_changed
            else ""
        )
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
        # Author-marked route where the program plays the complete target entry text.
        # Absent/false and already-displayed entries keep the old path.
        fixed_entry_only = bool(
            route_changed
            and transition_contract.get("fixed_entry_only") is True
            and has_pending_entry(target, session)
            # Target-scene policy: the session carries the node just entered.
            # Natural endings included: the author mark itself decides the program owns the entry.
            and outcome.session.dialogue_policy in {"optional", "forbidden"}
        )
        # All formal transitions use compact four-text contracts, Runtime-defined labels, and history-adapted text.
        source_transition_dialogue_policy = transition_source_dialogue_policy(
            str(outcome.ledger_event.get("source_transition_dialogue_policy") or session.dialogue_policy)
        )
        actor_budget = numeric_v2_actor_budget(session.actor_budget_profile)
        natural_ending = (route_changed and outcome.session.status == "ended"
                          and outcome.ledger_event.get("natural_ending_ready") is True and not fixed_entry_only)
        combined_history: list[str] = []
        combined_player_inputs: list[tuple[int, str]] = []
        next_scene_preview = None if route_changed else _next_scene_preview_for_actor(
            engine, cast, source, outcome.session.metrics, facts=outcome.session.story_state["facts"],
            departure_blocker=outcome.departure_blocker,
        )
        if _has_unconfirmed_stage_claim(outcome, player_input, input_source, unconfirmed_departure_quote):
            next_scene_preview = {"status": "departure_pending"}
        local_suggestions_only = (
            not route_changed and not reinvitation and outcome.session.status == "active"
            and (next_scene_preview or {}).get("status") == "runtime_unresolved"
            and _allows_local_suggestions_before_ending(engine, source, outcome.session.dialogue_policy)
        )
        suppress_suggestions = (not route_changed and not reinvitation
                                and (next_scene_preview or {}).get("status") != "after_acceptance_only"
                                and not local_suggestions_only)
        entry_inputs = (
            _authored_entry_player_inputs(cast, target["story_beat"])
            if route_changed and outcome.session.status != "ended"
            else None
        )
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
                retry_hint,
                input_source,
                # Ledger identifies original proposal records only, never exposing whole hidden state to Actor.
                recent_ledger_events,
                history_lookup,
                outcome.ledger_event.get("player_action_projection"),
                approved_completion_facts,
                draft_repair_context=draft_repair_context,
                combined_evaluation=combined_evaluation,
                combined_history_lookup=combined_history_lookup,
                combined_history=combined_history if combined_evaluation else None,
                combined_player_inputs=combined_player_inputs if combined_evaluation else None,
                fixed_entry_only=fixed_entry_only,
                next_scene_preview=next_scene_preview,
                wrong_exit_offer_quote=wrong_exit_offer_quote,
                exit_mismatch_reason=exit_mismatch_reason,
                progress_direction=progress_direction,
                unconfirmed_departure_quote=unconfirmed_departure_quote,
                current_scene_task=current_scene_task,
                departure_attempt_observed=departure_attempt_observed,
                transition_source=transition_source,
                source_delivery_task=source_delivery_task,
            ),
            transition_required=route_changed,
            transition_tail_only=transition_source is not None,
            bridge_required=bool(transition_contract.get("bridge_required", True)),
            max_input_tokens=actor_budget["input_max_tokens"],
            max_output_tokens=(
                NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS
                if route_changed
                else NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS
            ),
            dialogue_policy=outcome.session.dialogue_policy,
            source_dialogue_policy=source_transition_dialogue_policy,
            target_dialogue_policy=outcome.session.dialogue_policy,
            natural_ending=natural_ending,
            single_ending_response=transition_source is None and _uses_single_ending_response(
                natural_ending, outcome.session.dialogue_policy, authored_bridge),
            entry_narration=route_changed and has_pending_entry(target, session),
            entry_trigger_required=(route_changed and has_pending_entry(target, session) and not fixed_entry_only
                                    and outcome.session.status != "ended"),
            fixed_entry_only=fixed_entry_only,
            author_entry_choices=entry_inputs is not None,
            suppress_transition_offer=(not route_changed and not reinvitation
                and not _actor_can_offer_transition(next_scene_preview or {}, outcome.session.dialogue_policy)),
            ordinary_entry_bridge=str((next_scene_preview or {}).get("entry_movement") or ""),
            suppress_suggestions=suppress_suggestions,
            reinvitation_only=reinvitation,
            combined_metric_ids=tuple(engine.metric_schema) if combined_evaluation else None,
            combined_history_lookup=combined_history_lookup,
            fact_candidates_expected=(
                transition_source is None and bool(engine.pending_natural_ending_fact_targets(outcome)) if route_changed else
                not reinvitation and bool(engine.scene_fact_targets(str(source["id"])))
            ),
            **({"draft_provenance_sink": entry_provenance} if entry_provenance is not None else {}),
        )

        if combined_evaluation:
            performance["_turn_visible_history"] = combined_history
            performance["_turn_visible_player_inputs"] = combined_player_inputs
        if reinvitation and (
            performance.get("scene_narration") or performance.get("fact_candidates")
        ):
            # Parser 将 scene_update 规范为 scene_narration；去重前拒绝，不能靠删掉旧句掩盖控制越界。
            raise NumericV2ActorOutputError("numeric_v2_actor_reinvitation_state_update")
        if entry_inputs is not None:
            performance = {**performance, "suggested_inputs": entry_inputs}
        if route_changed:
            if transition_source is not None:
                performance["source_performance"] = str(transition_source.get("performance") or "")
                if transition_source.get("scene_narration"):
                    performance["source_scene_narration"] = transition_source["scene_narration"]
            # Runtime builds commit contracts before ownership/persona/fact validation.
            performance = engine.finalize_transition_performance(
                outcome,
                performance,
                target_opening=target_opening,
                bridge_required=bool(transition_contract.get("bridge_required")),
                bridge_scene_narration=authored_bridge,
                source_dialogue_policy=source_transition_dialogue_policy,
                target_dialogue_policy=outcome.session.dialogue_policy,
            )
            if transition_source is not None and transition_source.get("fixed_narrations"):
                performance["segments"][0]["fixed_narrations"] = deepcopy(transition_source["fixed_narrations"])
            segments = performance.get("segments")
            source_segment = segments[0] if isinstance(segments, list) and len(segments) == 3 else {}
            target_segment = segments[2] if isinstance(segments, list) and len(segments) == 3 else {}
            # Normalize this new draft only; do not reinterpret saved history.
            # Keep the server original intact and remove exact edge echoes only
            # when independent scene narration remains. Partial quotes stay.
            _trim_entry_narration_echo(target_segment)
            # Persona contracts inspect neko text on both sides only; Workflow reviews dynamic narration's facts/stage boundaries holistically.
            source_output = {"performance": source_segment.get("performance", "")}
            target_output = {"performance": target_segment.get("performance", "")}
            target_text = "".join(str(target_output["performance"]).split())
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
        # Without ordinary first-turn history, opening is the last performance the player saw; repetition protection must cover it.
        if session.performance_history:
            previous_visible_performance = session.performance_history[-1]
            previous_scene_performance = previous_visible_performance
        else:
            previous_visible_performance = session.opening_performance
            previous_scene_performance = previous_visible_performance
            if isinstance(previous_visible_performance.get("performance"), str):
                # Opening environmental narration does not recur in ordinary turns; compare neko text only so long narration cannot dilute repetition.
                previous_visible_performance = {
                    "performance": previous_visible_performance["performance"],
                }
        if not route_changed:
            performance = _deduplicate_scene_update(
                performance,
                previous_scene_performance,
            )
        else:
            performance = _deduplicate_transition_bridge_prefix(
                performance,
                previous_scene_performance,
            )
        from .numeric_v2_repetition import repetition_overlap
        overlap = repetition_overlap(performance, previous_scene_performance)
        trace_event("actor.repetition", **overlap, route_changed=route_changed)
        logger.info("Numeric v2 Actor overlap: session_id=%s revision=%s ngram=%s overlap=%s chars=%s",
                    session.session_id, session.revision, overlap["ngram"], overlap["overlap"], overlap["current_chars"])
        def reject_repeated_output(guard: str) -> None:
            """Record repetition-protection source labels for Workflow retry budgets."""  # noqa: DOCSTRING_CJK

            trace_event(
                "actor.repeated_output_detected",
                guard=guard,
                route_changed=route_changed,
                player_input_repeated=_player_input_repeats_recent_context(
                    player_input,
                    _history(
                        session,
                        max_tokens=actor_budget["history_max_tokens"],
                        max_turns=actor_budget["history_max_turns"],
                    ),
                ),
                has_scene_narration=bool(
                    str(performance.get("scene_narration") or "").strip()
                ),
                transition_offered=performance.get("transition_offered") is True,
                metric_change_count=len(outcome.metric_changes),
                fact_candidate_count=len(performance.get("fact_candidates") or []),
            )
            error = NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
            # Workflow-only repetition-source identifiers never enter public exceptions or plot history.
            error.repetition_guard = guard
            raise error

        # History guards compare Actor replies; player echoes need their own source check.
        if not reinvitation and _repeats_player_dialogue(performance, player_input, route_changed=route_changed):
            reject_repeated_output("player_input")

        if route_changed:
            if _replays_entry_original(performance):
                reject_repeated_output("fixed_narration")
            stale_narration = _stale_transition_narration(performance, session,
                initial_node_id=str(engine.story['start_node_id']), target_node_id=str(target['id']),
                target_is_ending=outcome.session.status == 'ended')
            if stale_narration:
                reject_repeated_output(stale_narration)

        if route_changed and _repeats_earlier_session_performance(
            target_output,
            session,
            route_changed=False,
        ):
            # Check the target independently: a nonempty source must not hide an older same-visit replay.
            reject_repeated_output("transition_target")
        # 用户主动重新索要邀请时允许重述邀请；普通回合与实际转场仍沿用原有重复守卫。
        repeats_earlier = not reinvitation and _repeats_earlier_session_performance(
            performance,
            session,
            route_changed=route_changed,
        )
        stable_short_dialogue = (
            not route_changed
            and not outcome.metric_changes
            and performance.get("transition_offered") is not True
            and not performance.get("fact_candidates")
            and _is_short_stable_dialogue(performance)
        )
        # Apply the existing unchanged-input allowance before either history
        # guard; the older-history guard used to reject it before the last-turn check.
        stable_confirmation = (
            not route_changed
            and not outcome.metric_changes
            and not outcome.ledger_event.get("fact_operations")
            and not outcome.ledger_event.get("routing_fact_operations")
            and "scene_narration" not in performance
            and not performance.get("transition_offered")
            and not performance.get("fact_candidates")
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
        # 仅有独立关键复核时，纯对白重复不消耗本轮重生成；下一轮从原文修正措辞。
        deferred_repetition = defer_dialogue_repetition and not route_changed and _can_defer_dialogue_repetition(performance)
        if deferred_repetition and (repeats_earlier or (
            previous_visible_performance and _is_repeated_performance(performance, previous_visible_performance)
        )):
            logger.info("Numeric v2 dialogue repetition deferred: session_id=%s revision=%s",
                        session.session_id, session.revision)
        if repeats_earlier and not stable_short_dialogue and not stable_confirmation and not deferred_repetition:
            logger.warning(
                "Numeric v2 Actor failed: reason=numeric_v2_actor_repeated_session_output session_id=%s revision=%s",
                session.session_id,
                session.revision,
            )
            reject_repeated_output("earlier_session")
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
            reject_repeated_output("transition_source")
        if previous_visible_performance and route_changed and any(
            _is_repeated_performance(target_output, previous_variant)
            or (len(target_text) >= 24 and target_text == "".join(
                str(previous_variant.get("performance") or "").split()))
            for previous_variant in _performance_variants(previous_visible_performance)
        ):
            # Include long verbatim actions even when their accompanying dialogue is too short for the speech guard.
            reject_repeated_output("transition_target")
        if (
            previous_visible_performance
            and not reinvitation
            and _is_repeated_performance(performance, previous_visible_performance)
        ):
            if stable_confirmation or stable_short_dialogue or deferred_repetition:
                # Repeated identical player input with unchanged state legitimately permits brief acknowledgment.
                logger.debug(
                    "Numeric v2 Actor accepted repeated dialogue: session_id=%s revision=%s",
                    session.session_id,
                    session.revision,
                )
            else:
                logger.warning(
                    "Numeric v2 Actor failed: reason=numeric_v2_actor_repeated_output session_id=%s revision=%s",
                    session.session_id,
                    session.revision,
                )
                reject_repeated_output("previous_performance")
        # After deterministic narrative validation, keep usable recommendations from the same Actor call;
        # opening, proposed-transition, and formal-transition paths retain valid suggestions, completing only anomalies once.
        performance = dict(performance)

        def finished_draft() -> dict[str, Any]:
            if entry_provenance and route_changed and draft_provenance_sink is not None:
                segments = performance.get("segments")
                target_narration = (
                    segments[2].get("scene_narration", "")
                    if isinstance(segments, list) and len(segments) == 3 else None
                )
                original_narration = "\n".join(text for text in (
                    entry_provenance["target_scene_narration"], entry_provenance["target_entry_trigger"],
                ) if text)
                # Never reverse a normalization or infer which part was the trigger.
                if target_narration == original_narration:
                    draft_provenance_sink.update(
                        body_fingerprint=performance_fingerprint(performance),
                        entry_fields=dict(entry_provenance),
                    )
            return performance

        if outcome.session.status == "ended" or reinvitation or suppress_suggestions:
            # Endings close input, reinvitation uses a program control, and no-route turns remain freeform.
            performance["suggested_inputs"] = []
            return finished_draft()
        performance["suggested_inputs"] = await self._ensure_suggestions(
            # Explicit author mode already published the program entry text, so the existing
            # filler must produce the buttons even though workflow keeps auto-fill disabled.
            allow_fill=not optimized_turn_enabled()
                       and (allow_suggestion_fill or fixed_entry_only) and entry_inputs is None
                       and not local_suggestions_only,
            performance=performance,
            player_input=player_input,
            catgirl_name=catgirl_name,
            max_input_tokens=actor_budget["input_max_tokens"],
            # Hide consumed old-scene input during transitions only when recommendation completion is actually needed.
            scene_changed=route_changed,
            hard_boundaries=_suggestion_hard_boundaries(
                cast,
                (target if route_changed else source)["story_beat"],
                # Refill responds to the same target opening as the primary
                # reply; ordinary turns must not prolong entry-only limits.
                include_opening_only=route_changed,
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
        return finished_draft()

    async def _invoke(
        self,
        messages: list[Any],
        *,
        opening_required: bool = False,
        transition_required: bool = False,
        transition_tail_only: bool = False,
        bridge_required: bool = True,
        max_input_tokens: int = 4800,
        max_output_tokens: int = NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS,
        dialogue_policy: str = "required",
        source_dialogue_policy: str = "required",
        target_dialogue_policy: str = "required",
        natural_ending: bool = False,
        single_ending_response: bool | None = None,
        entry_narration: bool = False,
        entry_trigger_required: bool = False,
        fixed_entry_only: bool = False,
        author_entry_choices: bool = False,
        suppress_transition_offer: bool = False,
        ordinary_entry_bridge: str = "",
        suppress_suggestions: bool = False,
        suggestions_only: bool = False,
        transition_suggestions_only: bool = False,
        reinvitation_only: bool = False,
        fact_candidates_expected: bool = False,
        combined_metric_ids: tuple[str, ...] | None = None,
        combined_history_lookup: bool = False,
        entry_part: str = "",
        draft_provenance_sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if draft_provenance_sink is not None:
            draft_provenance_sink.clear()
        parser_provenance: dict[str, Any] | None = {} if draft_provenance_sink is not None else None
        if single_ending_response is None:
            single_ending_response = natural_ending and target_dialogue_policy in {"optional", "forbidden"}
        set_call_type("theater_numeric_v2_actor")
        started_at = time.monotonic()
        config_finished_at = started_at
        client_finished_at = started_at
        request_finished_at = started_at
        model_name = ""
        provider_host = ""
        request_stage = "suggestions" if suggestions_only or transition_suggestions_only else "actor"
        try:
            # Total deadlines cover configuration, client creation, network, parsing, and client closure.
            async with asyncio.timeout(NUMERIC_V2_ACTOR_TIMEOUT_SECONDS):
                config_task = asyncio.create_task(_model_config(self.config_manager))
                try:
                    request_messages = await asyncio.to_thread(
                        _ensure_actor_messages_fit, messages, max_tokens=max_input_tokens)
                    if optimized_turn_enabled():
                        from .numeric_v2_prompt_cache import cache_ordered_messages
                        ordered = cache_ordered_messages(request_messages)
                        # The extra framing must fit the same admitted wire budget.
                        if _messages_tokens(ordered) <= max_input_tokens:
                            request_messages = ordered
                    config = await config_task
                finally:
                    if not config_task.done():
                        config_task.cancel()
                    await asyncio.gather(config_task, return_exceptions=True)
                model_name = str(config.get("model") or "")
                # Log hostnames only, excluding full URLs so query parameters/credentials cannot enter performance logs.
                provider_host = urlsplit(str(config.get("base_url") or "")).netloc
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
                    # _ensure_actor_messages_fit already trimmed request_messages to model input budgets.
                    self.provider_call_count += 1
                    # Account separately for every completion/recommendation call; observe provider responses without changing calls/retries.
                    output_schema = actor_output_schema(
                        opening_required=opening_required, transition_required=transition_required,
                        transition_tail_only=transition_tail_only,
                        suggestions_only=suggestions_only, transition_suggestions_only=transition_suggestions_only,
                        reinvitation_only=reinvitation_only,
                        single_ending_response=False if entry_part else single_ending_response,
                        entry_trigger_required=entry_trigger_required and not entry_part,
                        target_dialogue_required=(entry_trigger_required and target_dialogue_policy == "required"
                                                  and not entry_part),
                        fixed_entry_only=fixed_entry_only,
                        author_entry_choices=author_entry_choices,
                        suppress_transition_offer=suppress_transition_offer,
                        suppress_suggestions=suppress_suggestions,
                        fact_candidates_expected=fact_candidates_expected,
                        combined_metric_ids=combined_metric_ids,
                        combined_history_lookup=combined_history_lookup,
                    )
                    if entry_part:
                        fields = ({"bridge_scene_narration", "target_scene_narration"}
                                  if entry_part == "prefix" else {"target_performance", "suggested_inputs"})
                        if entry_part == "prefix" and not transition_tail_only:
                            fields |= {"source_scene_narration", "source_performance", "fact_candidates"}
                        output_schema["properties"] = {
                            key: value for key, value in output_schema["properties"].items() if key in fields
                        }
                        output_schema["required"] = [key for key in output_schema["required"] if key in fields]
                    response = await invoke_with_usage(client, request_messages,
                        stage=request_stage, response_format=response_format_for(
                            config, "theater_actor", output_schema))  # noqa: LLM_INPUT_BUDGET
                    request_finished_at = time.monotonic()
                    suggestion_diagnostics: dict[str, int] = {}
                    fact_candidate_diagnostics: dict[str, int] = {}
                    parsed = _parse_output(
                        getattr(response, "content", None),
                        opening_required=opening_required,
                        transition_required=transition_required,
                        transition_tail_only=transition_tail_only,
                        bridge_required=bridge_required,
                        dialogue_policy=dialogue_policy,
                        source_dialogue_policy=source_dialogue_policy,
                        target_dialogue_policy=target_dialogue_policy,
                        natural_ending=natural_ending,
                        single_ending_response=single_ending_response,
                        entry_narration=entry_narration,
                        entry_trigger_required=entry_trigger_required and not entry_part,
                        suggestions_only=suggestions_only,
                        transition_suggestions_only=transition_suggestions_only,
                        suggestion_diagnostics=suggestion_diagnostics,
                        fact_candidates_expected=fact_candidates_expected,
                        fact_candidate_diagnostics=fact_candidate_diagnostics,
                        reinvitation_only=reinvitation_only,
                        fixed_entry_only=fixed_entry_only,
                        author_entry_choices=author_entry_choices,
                        suppress_transition_offer=suppress_transition_offer,
                        ordinary_entry_bridge=ordinary_entry_bridge,
                        combined_evaluation=combined_metric_ids is not None,
                        combined_history_lookup=combined_history_lookup,
                        entry_part=entry_part,
                        draft_provenance_sink=parser_provenance,
                    )
                    trace_event("actor.parsed", stage=request_stage,
                                result=parsed, suggestion_diagnostics=suggestion_diagnostics,
                                fact_candidate_diagnostics=fact_candidate_diagnostics)
                    if (
                        not suggestions_only
                        and not transition_suggestions_only
                    ):
                        for reason, count in suggestion_diagnostics.items():
                            self.base_suggestion_parse_counts[reason] = (
                                self.base_suggestion_parse_counts.get(reason, 0)
                                + int(count)
                            )
                        for reason, count in fact_candidate_diagnostics.items():
                            self.base_fact_candidate_parse_counts[reason] = (
                                self.base_fact_candidate_parse_counts.get(reason, 0)
                                + int(count)
                            )
        except asyncio.TimeoutError as exc:
            trace_event(
                "actor.failed",
                error_code="numeric_v2_actor_timeout",
                stage=request_stage,
                model=model_name,
                provider_host=provider_host,
            )
            logger.warning(
                "Numeric v2 Actor failed: reason=numeric_v2_actor_timeout stage=%s model=%s provider_host=%s max_output_tokens=%d elapsed=%.3f",
                request_stage,
                model_name,
                provider_host,
                max_output_tokens,
                time.monotonic() - started_at,
            )
            raise NumericV2ActorError("numeric_v2_actor_timeout") from exc
        except NumericV2ActorError as exc:
            reason = str(exc) if str(exc).startswith("numeric_v2_actor_") else type(exc).__name__
            trace_event("actor.failed", error_code=reason)
            logger.warning("Numeric v2 Actor failed: reason=%s", reason)
            raise
        except Exception as exc:
            trace_event("actor.failed", error_code="numeric_v2_actor_model_call_failed", error_type=type(exc).__name__)
            logger.warning("Numeric v2 Actor failed: reason=numeric_v2_actor_model_call_failed error_type=%s", type(exc).__name__)
            raise NumericV2ActorError("numeric_v2_actor_model_call_failed") from exc
        total_seconds = time.monotonic() - started_at
        if total_seconds >= NUMERIC_V2_ACTOR_SLOW_CALL_SECONDS:
            # Record request identifiers and stage duration only, never scripts, player input, or model outputs.
            logger.warning(
                "Numeric v2 Actor slow call: stage=%s model=%s provider_host=%s max_output_tokens=%d total=%.3f config=%.3f client=%.3f request=%.3f finalize=%.3f",
                request_stage,
                model_name,
                provider_host,
                max_output_tokens,
                total_seconds,
                config_finished_at - started_at,
                client_finished_at - config_finished_at,
                request_finished_at - client_finished_at,
                time.monotonic() - request_finished_at,
            )
        if parser_provenance and draft_provenance_sink is not None:
            draft_provenance_sink.update(parser_provenance)
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
    "actor_visible_profile",
]
