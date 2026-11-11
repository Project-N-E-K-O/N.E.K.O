"""User-triggered six-dimension story assessment and single-node optimization."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping


from ..model import ModelAgent, LLMCallFailure
from ..numeric_v2_analysis import entry_route_scope

from .runtime_rules import STORY_DESIGN_RULE, AUTHOR_KNOWLEDGE_RULE, ENTRY_ROUTE_SCOPE_RULE, ENTRY_STATE_TIMING_RULE, PUBLIC_TEXT_RULE
from .numeric_v2 import _marked_author_original_spans
from .facts import (fact_review_prompt, OPENING_STATE_COMPARISON_RULE, bind_contract_targets, build_contract_targets, build_fact_sources, validate_fact_review,
                    node_fact_context, fact_review_tasks, fact_task_context, with_opening_source, _resolve_pointer)
from .evidence import (EVIDENCE_REVIEW_PROMPT, review_targets, validate_evidence_review,
                       effective_targets, protected_targets, fields_overlap)
from .repair import (
    STORY_BEAT_FIELDS as _STORY_BEAT_FIELDS,
    CHARACTER_STATE_TEXT_FIELDS as _CHARACTER_STATE_TEXT_FIELDS,
    TRANSITION_CONTRACT_FIELDS as _TRANSITION_CONTRACT_FIELDS,
    BRIDGE_REMOVAL_RULE, REPAIR_PLAN_RULE, can_remove_bridge_narration, text_repair_fields, context_nodes,
    classify_repair, assign_shared_plans, changed_fields,
)
from .plan_review import PLAN_REVIEW_PROMPT, validate_plan_review
from .exit_review import (EXIT_BINDING_PROMPT, EXIT_FACT_REVIEW_PROMPT, build_exit_binding_request,
                          validate_exit_bindings, unproven_exit_candidates, validate_exit_candidate_checks)


QUALITY_DIMENSIONS = {
    "characterization": {"label": "人物塑造", "weight": 0.20},
    "plot": {"label": "情节构思", "weight": 0.25},
    "theme": {"label": "主题思想", "weight": 0.15},
    "prose_style": {"label": "文笔风格", "weight": 0.10},
    "pacing": {"label": "叙事节奏", "weight": 0.15},
    "emotional_resonance": {"label": "情感共鸣", "weight": 0.15},
}
QUALITY_PASS_SCORE = 75.0
_ENTRY_PLAYER_INPUTS_RULE = (
    "\nentry_player_inputs 是入幕后供玩家点击、尚未选择的推荐输入，不是已发生事实；"
    "按将显示的推荐检查作者边界，不据此认定玩家已行动、承诺或完成目标。"
)
_AUTHOR_ORIGINAL_REF_RULE = (
    "\nauthor_intent 为分段数组时，按原顺序阅读。fixed_original_refs 按 node_id 和 fixed_narration_id 定位同文固定旁白，"
    "保留原处的作者要求；对应幕未提供正文时只作定位，不据此认定原文缺失或推测其内容。"
)

_LITERARY_BLOCK_PROMPT = """你只检查本块最多两幕的文学表现，不打分、不改原稿、不复核事实报告。按作者意图检查人物选择、局部因果、文字、节奏和情感；相邻幕是实际路线，不把其他入路的保证合并。保留用户关系距离、配角职责与玩家选择，不强加升温或新任务。
story_outline使用原文{ref,text}，original_sources只定位这些原字段；省略外部独立正文不代表原稿缺失。fact_review是已独立核实的问题，不重新判断或换个文学理由重复报告。目标和台账是规划，不能声称已检查最终运行时对白或玩家体验。
checked_node_ids必须原样返回；node_notes对每个本块节点恰好一项，assessment以一两句说明该幕的选择、变化、铺垫或回收，最多200 tokens。每个note的evidence_refs至少一个该节点原文ref，不凭空概括。issues只列本块原文支持的独立文学问题，evidence_refs至少一个本块实际原文ref；不能把事实缺陷当成文学新问题。修改字段必须真实且在对应节点，text_repair_fields之外仍标structure，不能扩权。没有问题填[]。不返回scores、metric_advice或relationship_advice。
只输出JSON：{"checked_node_ids":["原ID"],"node_notes":[{"node_id":"本块节点ID","assessment":"基于原文的局部观察","evidence_refs":["E1"]}],"issues":[{"dimension":"characterization|plot|theme|prose_style|pacing|emotional_resonance","severity":"minor|major|blocking","target_node_ids":["本块节点ID"],"problem":"原文支持的问题","modification_plan":"最小修法","expected_result":"修后效果","preserve":["保留项"],"repair_scope":"text|structure","repair_targets":[{"node_id":"实际节点ID","field":"节点内原字段路径"}],"evidence_refs":["实际原文ref"]}]}"""

# Literary review preserves author boundaries and links only verified facts; drafting/node revision still use the complete shared generation contract.
_ASSESSMENT_PROMPT = STORY_DESIGN_RULE + "\n\n" + """# Role: N.E.K.O 互动剧情质量评估编辑

事实一致性已由上一阶段独立核对，fact_review 是完整事实报告的评分摘要，story_outline 保留同一份作者稿的可读正文与路线。本轮只评文学质量：人物塑造、情节构思、主题、文笔、节奏和共鸣，不修改故事。
目标是作者规划，固定旁白由程序按触发条件原样展示，演员另写自由回应。普通换幕须有玩家授权，结局可以在结果已成立时自然收束。沿用原稿人物姓名与分工，优先评价两人的选择和互动，不因配角少而扣分；保留明确需要的配角职责。请求省略已核过的事实合同和目标证据元数据，不能把省略当作剧本缺失。relationship_goal 省略时沿用同幕 transition_goal。节点的 repair_field_set 指向 story_outline.repair_field_sets 中的 text_repair_fields 清单，按该清单判断文本修订权限。
fact_review.issues仅含证据复核支持的问题；excluded_issue_ids是已排除的问题编号，不能引用或据此扣分。protected_repair_targets所列字段保持原文，不能以文学或人物关系建议要求重写。
不得重新核对或推翻 fact_review，不把它的问题重复写入本轮 issues，也不重新猜测时点、行动主体、证据来源或道具必要条件。事实缺陷对文学效果的影响可在对应维度的summary中说明，并在fact_issue_ids引用事实报告已有issue_id。服务端会原样合并事实问题，高文学分不能覆盖事实问题。
路线可辨识性属于玩家体验：不能只靠隐藏数值暗中分流，但不因单线或接受后的正常移动就批评玩家没有自主权。证据可演性可以评价具体材料是否足以承载情感表达，但不把“建议补细节”写成已确认事实矛盾。角色反馈允许保持分歧或克制，不强求直接夸奖、关系升级或额外确认。

六个维度：
1. characterization 人物塑造：角色立体度、行为逻辑一致性、成长轨迹。
2. plot 情节构思：逐幕核对玩家面对的选择、场上可见变化及其如何引出后幕。对比合法回应是否真会改变互动或结果，结局是否兑现开场建立的期待。
3. theme 主题思想：作品探讨的问题及人物选择、结局形成的表达。
4. prose_style 文笔风格：大纲语言、描写计划和人物声音约束。运行时最终对白尚未生成，不得声称已经检查最终对白。
5. pacing 叙事节奏：找出因额外手续、重复确认或同一工作拆步而迟迟不给直接结果的位置；相邻幕可互换时指出缺失的因果。具体说明应合并哪一步、保留哪处选择或反应；合并删幕须标structure。实际回合节奏仍受玩家影响。
6. emotional_resonance 情感共鸣：情感铺垫和关键选择的共鸣潜力，不得假装已经观察到真实玩家体验。

每项使用 0—100 分。每个scores维度内同时填写score、summary、fact_issue_ids、related_issue_ids和issues。低于75分必须有该维度的文学问题、明确关联已列文学/关系问题，或引用确实导致扣分的已有事实问题；summary说明所引用的问题如何影响本维度。不为满足低分依据而重复事实问题，也不抬高分数。所有新发现的文学问题（包括高分维度的minor、major、blocking）都放入对应维度issues，每条点名真实target_node_ids并提供modification_plan。
沿用作者规定的关系距离、表达边界与收束方式，不强加赞美、升温、额外确认或抽象难度。
事实缺陷及其同一修法只在事实报告中保留一次：本轮使用fact_issue_ids关联，不重新生成一条问题或人物关系建议。仅当修复已有事实后仍独立存在的文学缺陷，才新增issues。服务端保留事实严重度，不因分数变化而删除问题。
target_node_ids 和 repair_targets.node_id 只能从 allowed_target_node_ids 选择；metrics 不是节点，指标 ID、字段名和维度名也不能充当节点。路线的问题在 problem 中点名路线 ID，并把目标节点填写为该路线所属的来源节点，不能填写路线 ID 或其目标节点来代替来源节点。

另外必须给出：
- metric_advice：recommended_count 必须为 0—4 的整数。只评价当前“数值指标的数量与职责”是否合理，可建议新增、删除或合并指标；不要直接调整具体阈值。若指标实际描述双方信任、好感、亲密、坦诚或疏离，却把 relationship_effect 标成 none，必须指出它无法约束运行时关系距离。
- relationship_advice：只列尚未被事实或六维问题覆盖的独立关系缺陷，并关联具体节点；没有独立不足时返回[]，不为填写此栏要求关系升温或重复角色表态。
指标职责或 relationship_effect 的问题只写入 metric_advice.summary，不再复制到 relationship_advice 或节点 issues。relationship_effect 仅支持 positive、negative、none，分别表示数值越高越亲近、越疏远、不影响关系距离；不能发明枚举，也不控制称呼披露或发声权限。这些建议不自动修改配置。

结构建议可以指出需要新增、删除或重连节点与路线。每条建议必须显式填写 repair_scope：只改既有节点文本、已有三方状态描述或既有路线转场文本时写 text；需要改目标/交付结构、acting_contract 的认知或发声权限、character_state 内连续性/边界字段，或新增、删除、拆分、合并、重连节点、路线、条件、数值、回合预算时写 structure。不能把仅修状态描述误判为必须重构。

先为每条独立文学问题分配本次唯一issue_id（L1、L2等，跨维度不重号），关系建议使用R1、R2等。每个问题只写一次；其他受影响维度在related_issue_ids中明确引用该编号，summary说明本维度影响，不复制问题或修法。可引用后面维度或关系栏的问题，不能引用维度名、未列出的问题或事实编号；事实仍用fact_issue_ids。关联不代表建议符合作者意图，不能因为能被引用就省略原文依据或要求关系升温。

提交前逐维度完成分数、依据和问题，再进入下一维度。六个固定维度必须齐全；数值建议只放metric_advice，不创建metrics或metric_advice维度。issues不再填写dimension，所属维度由所在scores字段确定。顶层不再返回issues。

只输出 JSON object（示例分数仅说明格式，实际分数如实评价）：
{
  "scores": {
    "characterization": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82},
    "plot": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82},
    "theme": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82},
    "prose_style": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82},
    "pacing": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82},
    "emotional_resonance": {"issues": [], "fact_issue_ids": [], "related_issue_ids": [], "summary": "评分依据", "score": 82}
  },
  "strengths": ["必须保留的具体优点"],
  "metric_advice": {
    "recommended_count": 0,
    "summary": "现有数值数量与职责是否合理",
    "add": ["建议增加什么数值以及原因"],
    "remove": ["建议删除什么数值以及原因"],
    "merge": ["建议合并哪些数值以及原因"]
  },
  "relationship_advice": [{
    "issue_id": "R1",
    "target_node_ids": ["关联节点 ID"],
    "problem": "人物关系问题",
    "suggestion": "优化建议",
    "expected_result": "优化后的关系效果",
    "repair_scope": "text | structure",
    "repair_targets": [{"node_id":"实际需要修改的节点ID","field":"实际需改的投影路径"}]
  }]
}
各维度issues中每个问题的结构（没有独立问题时用空数组，不凑数）：
{"issue_id":"L1","severity":"blocking|major|minor","target_node_ids":["真实节点ID"],"problem":"独立文学问题","modification_plan":"修改方案","expected_result":"预期结果","preserve":["保留项"],"repair_scope":"text|structure","repair_targets":[{"node_id":"实际需要修改的节点ID","field":"实际需改的投影路径"}]}
""" + "\n\n" + REPAIR_PLAN_RULE + "\n\n" + (
    # Literary review no longer repeats the complete generation contract's factual review of actors, states, and transitions.
    # Users allow low scores to reference existing facts; fill each dimension to prevent cross-list omissions, while the server only expands without inventing suggestions.
    "本轮只返回文学报告。每个scores维度先写issues和fact_issue_ids，再写summary和score。"
    "输出前检查每个低于75的维度：issues、fact_issue_ids和related_issue_ids不能同时为空。"
    # Include executable fields in examples and require source evidence plus specific fixes; retain all model opinions without post-filtering.
    "每条文学问题在problem点明节点、字段和短原文；modification_plan写具体替换方向，不能只要求丰富层次或增强氛围。"
    "实际开场、转场或反应指引重复才可作为文笔依据，后台状态记录简短或格式重复不等于演出文笔差。"
    "修法逐项核对text_repair_fields，repair_targets列全实际需要改的字段；涉及goals、narrative_focus或acting_contract仍是structure，不借改summary绕开。"
    "跨节点润色不等于结构调整；不能只因涉及多个节点就标structure或省略修改字段。"
    "文学润色保留动作事实、时点及物件位置，不删除或后移动作追求简洁，也不按个人习惯要求改掉合理行动。"
)

# 单节点只需要已确认方案的执行规则，无须再次进行整稿事实审查。
_NODE_OPTIMIZATION_PROMPT = AUTHOR_KNOWLEDGE_RULE + "\n" + BRIDGE_REMOVAL_RULE + "\n" + """# Role: N.E.K.O 单节点质量优化编辑

用户已在故事地图中确认优化一个节点。结合 story_outline、target_node 和 accepted_suggestions，一次解决本节点全部获准修订。text_omitted=true 仅供定位，edit_context_only=true 只提供有关开场、摘要或路线，省略不代表原稿缺失。target_node 中省略的同文内容见 story_outline 同节点。

普通换幕须有玩家授权。按 completion_contract 与路线 conditions 承接有保证的结果，可变的内容、数量、归属不写死。goals 和 source_ids 只是规划，Evaluator 不逐项判定目标完成，Runtime 不执行目标 evidence 或 state_effects。固定旁白由程序展示，exact anchors 不代替原文。邀约在接受前，桥段在接受后；拒绝或暂缓可留幕，结局无新玩家回合。保留姓名、认知、发声边界及因果必需的配角回应。

入边桥段属于来源节点，出边桥段属于当前节点；不得改写当前开场来掩盖其他节点的抢演。结局的两处摘要独立授权；只获准一处时保留另一处。保留原始 author_intent 与未获授权修改的目标、状态、演绎合同。

不得修改其他节点，不得修改 ID、节点类型、路线指向、路线条件、数值、阈值、priority、回合预算或未被用户确认的结构。可以修改目标节点已有出口的 transition_contract 文本；不得新增、删除或改指路线。必须保留 global_strengths_to_preserve 和每条建议中的 preserve 内容。

must_deliver 可显式为 []；这不会删除原有旁白。删除获准的重复旁白时填 remove_bridge_scene_narration: true，省略 bridge_scene_narration。

按建议的evidence核对原文；若只有path则从story_outline按JSON Pointer读取。开场正确而状态写错时，在获准范围内直接修正已有 character_state.catgirl_state/player_state/environment_state，只返回实际需要改的子字段；不要重写正确开场迁就错误状态。同一错误若在 catgirl_situation 中复述，仅在 /relationship_state 也获准时同步修处境，保留其中仍有效的认知、关系与事实。状态文字只替换冲突的事实，其余仍相容的描述沿用原文；无需补造新的手势、视线、心理或物件位置来填补删去的错误。不得改 character_state 的其它字段，不得改 goals 或 acting_contract；说明动作已经发生不等于要求再演一次。修改后再对照原稿与建议：错误是否消失、正常字段是否保留、相邻节点是否仍相容，不新增行动、任务或玩家承诺作为修复手段。

只输出 JSON object：
{
  "node_updates": [{
    "node_id": "目标节点 ID",
    "chapter": "可选的新标题",
    "story_beat": {
      "summary": "可选的完整替换值",
      "opening_scene": "可选的完整替换值；只写环境、演出角色行动和已确定结果，不得替玩家执行自主行动",
      "must_not_happen": ["可选的完整替换数组"],
      "catgirl_situation": "可选的完整替换值",
      "transition_goal": "可选的完整替换值",
      "character_state": {"catgirl_state": "可选，仅修既有演出角色状态描述", "player_state": "可选，仅修既有玩家状态描述", "environment_state": "可选，仅修既有环境状态描述"}
    },
    "routes": [{
      "route_id": "目标节点的已有出口 ID",
      "transition_contract": {
        "reason": "可选的完整替换值",
        "bridge_scene_narration": "可选的完整替换值；不得复制目标幕开场或替玩家执行自主行动",
        "remove_bridge_scene_narration": true,
        "must_deliver": ["可选的完整替换数组；只写转场独有的剧情事实，无独立事项时可填空数组"],
        "must_preserve": ["可选的完整替换数组"],
        "tone": "可选的完整替换值"
      }
    }],
    "ending": {"title": "仅结局节点可选", "summary": "仅结局节点可选"}
  }]
}

目标节点必须恰好返回一次；修改已批准方案在该节点列明的全部必要字段，只返回确实需要变更的允许字段。
事实建议附evidence_review；requires_change=false不授权本条修改该字段。protected_repair_targets中的字段必须保持原文。
本次还必须遵守accepted_suggestions中的repair_targets，只改这些字段在target_node中的对应原字段。它们使用story_outline的投影路径：relationship_state对应catgirl_situation，character_state.catgirl/player/environment对应三方_state，普通幕summary对应story_beat.summary，结局summary对应ending.summary而scene_summary对应story_beat.summary。target_node中省略的同文状态、goals与转场字段见story_outline，目标和路线各按原索引对应；用该route_id返回routes补丁。不要同步重写未列入方案的正确字段。related_impacts是共用该修法的其它影响与保留项，须一并照顾。
""" + "\n\n" + ENTRY_STATE_TIMING_RULE + "\n" + PUBLIC_TEXT_RULE


class QualityAssessmentError(RuntimeError):
    """Assessment or user-confirmed node optimization did not return a safely usable result."""

    def __init__(self, code: str, *, provider_details: Mapping[str, Any] | None = None, phase: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.provider_details = dict(provider_details or {})
        # Scoring failure must identify the fact/evidence/literary/plan-review stage; never present old reports or partial results as current scores.
        self.phase = phase


def _is_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= float(value) <= 100
    )


def _format_repair_messages(response: str, error: Exception, instruction: str, *,
                            details: Mapping[str, Any] | None = None,
                            include_excerpt: bool = True) -> list[dict[str, str]]:
    from utils.tokenize import truncate_head_tail_tokens

    instruction += ((" 若附有上一份答复，仅沿用可核实的判断，不把省略标记写入结果。"
                     "按原输入和输出合同返回完整结果。") if include_excerpt else
                    " 按原输入和输出合同返回完整JSON，不得用省略标记或片段代替字段与目标。")
    return [
        *([{"role": "assistant", "content": truncate_head_tail_tokens(response, 384, 128)}] if include_excerpt else []),
        {"role": "user", "content": json.dumps({
            "validation_error": str(error.__cause__ or error), "instruction": instruction, **(details or {}),
        }, ensure_ascii=False)},
    ]


def _fact_repair_catalog(context, node_ids):
    """List actual field roots in this task, without expanding repair permissions."""
    result = {}
    for node_id, node in context_nodes(context).items():
        if node_id not in node_ids:
            continue
        paths = {f"/{key}" for key in node if key not in {"id", "text_repair_fields"}}
        for path in node.get("text_repair_fields", []):
            try:
                _resolve_pointer(node, path)
                paths.add(path)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        for index, route in enumerate(node.get("outgoing_routes", [])):
            paths.update(f"/outgoing_routes/{index}/{key}" for key in route)
            for key in ("conditions", "transition_contract"):
                if isinstance(route.get(key), Mapping):
                    paths.update(f"/outgoing_routes/{index}/{key}/{field}" for field in route[key])
        result[node_id] = sorted(paths)
    return result


def _fact_core(issues):
    if not isinstance(issues, list) or any(not isinstance(row, Mapping) for row in issues):
        raise ValueError("invalid_fact_issue")
    return [{key: value for key, value in row.items()
             if key not in {"repair_targets", "modification_plan", "repair_scope"}} for row in issues]


def _shared_original_sources(sources, refs, graph, story_context):
    """Keep one complete copy of quoted text already present in the global request."""
    locations = {}
    def visit(value, path):
        if isinstance(value, str):
            locations.setdefault(value, path)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + f"/{index}")
    visit(graph, "/story_graph")
    visit(story_context, "/story_context")
    return [{"ref": ref, "path": sources[ref]["path"],
             **({"text_path": locations[sources[ref]["quote"]]} if sources[ref]["quote"] in locations
                else {"quote": sources[ref]["quote"]})} for ref in sorted(refs)]


def _choice_coverage_rows(payload, targets, evidence_sources):
    """Preserve cited alternatives and handoff judgments while correcting their format."""
    targets = {target["id"]: target for target in targets}
    checks = payload.get("contract_checks") if isinstance(payload, Mapping) else None
    found = {}
    for check in checks if isinstance(checks, list) else []:
        if not isinstance(check, Mapping) or not isinstance(check.get("target_id"), str):
            continue
        target = targets.get(check["target_id"])
        if target is not None and target.get("kind") == "transition_contract":
            reason = evidence_sources[target["definition_ref"]]["quote"]
            for row in check.get("choice_checks", []) if isinstance(check.get("choice_checks"), list) else []:
                if (not isinstance(row, Mapping) or not isinstance(row.get("choice_quote"), str)
                        or not row["choice_quote"].strip() or row["choice_quote"] not in reason
                        or row.get("compatible") is not None and type(row.get("compatible")) is not bool
                        or "compatible" not in row or not isinstance(row.get("counterexample"), str)):
                    continue
                original = {"target_id": check["target_id"], **{key: row[key]
                    for key in ("choice_quote", "compatible", "counterexample")}}
                found[json.dumps(original, ensure_ascii=False, sort_keys=True)] = original
        rows = check.get("alternatives")
        if target is None or not isinstance(rows, list):
            continue
        for row in rows:
            if (not isinstance(row, Mapping) or row.get("kind") not in ("completed", "refusal", "deferral")
                    or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                    or not isinstance(row.get("evidence_refs"), list) or not row["evidence_refs"]
                    or any(not isinstance(ref, str) or ref not in evidence_sources for ref in row["evidence_refs"])):
                continue
            original = {"target_id": check["target_id"], "kind": row["kind"],
                        "evidence_refs": sorted(set(row["evidence_refs"])), "reason": row["reason"].strip()}
            if (row["kind"] == "completed" and type(row.get("value")) in (bool, int, str)
                    and type(row["value"]) is type(target["requirement"].get("equals"))):
                original["value"] = row["value"]
            found[json.dumps(original, ensure_ascii=False, sort_keys=True)] = original
    return list(found.values())


def _content_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _omit_reviewed_exit_history(value, refs):
    """Keep locators for specialist-owned claims, without asking the main review to rejudge them."""
    if isinstance(value, Mapping):
        if set(value) == {"ref", "text"} and isinstance(value["ref"], str) and value["ref"] in refs:
            return {"exit_history_reviewed_ref": value["ref"]}
        return {key: _omit_reviewed_exit_history(item, refs) for key, item in value.items()}
    if isinstance(value, list):
        return [_omit_reviewed_exit_history(item, refs) for item in value]
    return value


def _story_direction_rule(context: Mapping[str, Any]) -> str:
    """Explain Runtime's optional direction field only when present, keeping legacy requests unchanged."""
    if not any("narrative_summary" in node for node in context_nodes(context).values()):
        return ""
    return (
        "\nnarrative_summary非空时是Runtime优先于story_beat.summary采用的整幕待演方向；"
        "为空或缺失时沿用原有回退。它不替代opening_scene，也不证明行动已发生。"
        "该字段在本次节点文本优化中只读，问题可引用其原文并提出手动调整建议；"
        "不能用改写summary掩盖该方向中仍存在的问题，摘要及结局摘要保留各自原义。"
    )


def _pack_shared_branches(outline: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Keep branch membership and indexes while sending shared node prose once."""
    paths = {}
    branches = []
    shared = False
    for branch_index, branch in enumerate(outline["branches"]):
        nodes = []
        for node_index, node in enumerate(branch["nodes"]):
            node_id = node["id"]
            if node_id in paths:
                nodes.append({"id": node_id, "shared_node_ref": paths[node_id]})
                shared = True
            else:
                paths[node_id] = f"/branches/{branch_index}/nodes/{node_index}"
                nodes.append(node)
        branches.append({**branch, "nodes": nodes})
    rule = ("\nshared_node_ref 是从 story_outline 根开始的 JSON Pointer，指向同一支线节点的唯一投影；"
            "投影标记 text_omitted 时也只供定位。各入口仍独立。"
            if shared else "")
    return {**outline, "branches": branches}, rule


def _author_intent_parts(context: Mapping[str, Any]) -> list[str | dict] | None:
    """Replace exact marked copies in the request without changing stored intent or node paths."""
    brief = str(context.get("author_intent") or "")
    if "[原文起]" not in brief:
        return None
    try:
        spans = _marked_author_original_spans(brief)
    except ValueError:
        return None
    locations: dict[str, list[dict[str, str]]] = {}
    for node_id, node in context_nodes(context).items():
        for piece in node.get("fixed_narrations") or ():
            if isinstance(piece, Mapping) and isinstance(piece.get("text"), str) and isinstance(piece.get("id"), str):
                locations.setdefault(piece["text"], []).append({
                    "node_id": node_id, "fixed_narration_id": piece["id"],
                })
    parts = []
    cursor = 0
    for start, end in spans:
        matches = locations.get(brief[start:end])
        if not matches:
            continue
        parts.extend((brief[cursor:start], {"fixed_original_refs": matches}))
        cursor = end
    if not parts:
        return None
    return [*parts, brief[cursor:]]


def _audit_sources(context: Mapping[str, Any]):
    """Use one stable citation order and point exact author-original copies to their stored fields."""
    marked, sources = build_fact_sources(context)
    parts, fragment_paths = _author_intent_parts(context), {}
    if parts is not None:
        marked_parts = []
        for index, part in enumerate(parts):
            if isinstance(part, str) and part.strip():
                ref = f"E{len(sources) + 1}"
                sources[ref] = {"path": "/author_intent", "quote": part}
                fragment_paths[ref] = f"/author_intent/{index}"
                marked_parts.append({"ref": ref, "text": part})
            else:
                marked_parts.append(part)
        marked["author_intent"] = marked_parts
    return marked, sources, parts, fragment_paths


def _optimizer_evidence_source(outline: Mapping[str, Any], path: str) -> Any:
    """Follow a packed branch alias before looking up the exact source text."""
    if not isinstance(path, str):
        raise ValueError("invalid_fact_evidence_path")
    parts = path.split("/", 5)
    if len(parts) == 6 and parts[1] == "branches" and parts[3] == "nodes":
        node = _resolve_pointer(outline, "/".join(parts[:5]))
        if "shared_node_ref" in node:
            path = node["shared_node_ref"] + "/" + parts[5]
    return _resolve_pointer(outline, path)


def _repair_outline(context: Mapping[str, Any], focus: set[str], *, include_neighbors: bool = True) -> dict[str, Any]:
    """Keep local prose and original evidence paths; other nodes become locators."""
    nodes = context_nodes(context)
    nearby = context_nodes(node_fact_context(context, focus & nodes.keys())) if include_neighbors else focus

    def pack(rows):
        return [with_opening_source(node) if node["id"] in nearby else {
            "id": node["id"],
            "title": node["title"],
            "text_repair_fields": node["text_repair_fields"],
            "outgoing_routes": [
                {key: route[key] for key in ("id", "route_id", "target_node_id") if key in route}
                for route in node.get("outgoing_routes", ())
            ],
            "text_omitted": True,
        } for node in rows]

    return {
        **context,
        "mainline": pack(context.get("mainline", ())),
        "endings": pack(context.get("endings", ())),
        "branches": [{**branch, "nodes": pack(branch.get("nodes", ()))}
                     for branch in context.get("branches", ())],
    }


def _required_source_originals(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_pieces = node.get("fixed_narrations")
    if not isinstance(raw_pieces, list):
        return []
    pieces = [piece if isinstance(piece, Mapping) and isinstance(piece.get("id"), str) else {}
              for piece in raw_pieces]
    required = {row["fixed_narration_id"] for row in (node.get("completion_contract") or {}).get("all") or ()
                if isinstance(row, Mapping) and isinstance(row.get("fixed_narration_id"), str)}
    for piece in reversed(pieces):
        if piece and (piece.get("required_before_exit") is True or piece["id"] in required):
            required.add(piece["id"])
            required.update(key for key in piece.get("after") or () if isinstance(key, str))
    # Keep original indexes for evidence paths, even when only a later original is needed.
    return [piece if piece.get("id") in required else {"id": piece.get("id"), "text_omitted": True}
            for piece in pieces] if required else []


def _optimizer_outline(context: Mapping[str, Any], node_id: str, focus: set[str], fields: set[str], *,
                       story: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the edited node and only the adjacent evidence needed by its approved fields."""
    selected = context_nodes(context)[node_id]
    summary = "/scene_summary" if "scene_summary" in selected else "/summary"
    check_entry = any(fields_overlap(field, base) for field in fields
                      for base in ("/opening_scene", "/character_state", "/relationship_state"))
    check_entry |= not selected.get("opening_scene") and summary in fields
    destinations = {selected["outgoing_routes"][int(field.split("/")[2])]["target_node_id"]
                    for field in fields if field.startswith("/outgoing_routes/")}
    nearby = context_nodes(node_fact_context(context, node_id)).keys() | focus
    outline = _repair_outline(context, nearby, include_neighbors=False)
    if check_entry:
        scope = entry_route_scope(story, node_id)
        if len(scope["routes"]) + scope["initial_entry"] > 1:
            outline["entry_route_scope"] = scope

    def pack(rows):
        result = []
        for node in rows:
            if node["id"] == node_id:
                result.append(node)
                continue
            if node.get("text_omitted"):
                result.append({key: value for key, value in node.items() if key != "text_repair_fields"})
                continue
            incoming = check_entry and any(route["target_node_id"] == node_id for route in node["outgoing_routes"])
            keys = {"id", "title", "summary", "scene_summary", "opening_scene", "opening_fallback_source", "entry_from"}
            if incoming:
                keys.update({"completion_contract", "character_state"})
            if node["id"] in destinations:
                keys.update({"character_state", "fixed_narrations", "narrative_summary", "acting_contract"})
            originals = _required_source_originals(node) if incoming and node["id"] not in destinations else []
            result.append({**{key: value for key, value in node.items() if key in keys},
                           **({"fixed_narrations": originals} if originals else {}),
                           "outgoing_routes": [route if incoming and route["target_node_id"] == node_id else {
                               "id": route["id"], "target_node_id": route["target_node_id"],
                               **({"transition_contract": {"fixed_entry_only": True}}
                                  if route["target_node_id"] == node_id and
                                  route["transition_contract"].get("fixed_entry_only") is True else {}),
                           } for route in node["outgoing_routes"]],
                           "edit_context_only": True})
        return result

    return {**outline, "mainline": pack(outline["mainline"]), "endings": pack(outline["endings"]),
            "branches": [{**branch, "nodes": pack(branch["nodes"])} for branch in outline["branches"]]}


def _plan_review_outline(context: Mapping[str, Any], plans: list[dict], protected: list[dict]) -> dict[str, Any]:
    """Keep the specified text and enough context to review all commands together."""
    fields_by_node = {node_id: set() for plan in plans for node_id in plan.get("target_node_ids", ())}
    for target in [*(target for plan in plans for target in plan.get("repair_targets", ())), *protected]:
        fields_by_node.setdefault(target["node_id"], set()).add(target["field"])
    outline = _repair_outline(context, set(fields_by_node), include_neighbors=False)
    visible = {"id", "title", "summary", "scene_summary", "narrative_summary", "opening_scene",
               "opening_fallback_source", "narrative_focus", "text_repair_fields", "text_omitted"}

    def pack(rows):
        packed = []
        for node in rows:
            fields = fields_by_node.get(node["id"], ())
            keys = visible | {field.split("/", 2)[1] for field in fields if field.startswith("/")}
            entry = {key: value for key, value in node.items() if key in keys}
            entry["outgoing_routes"] = [
                route if any(fields_overlap(field, f"/outgoing_routes/{index}") for field in fields)
                else {"id": route["id"], "target_node_id": route["target_node_id"]}
                for index, route in enumerate(node["outgoing_routes"])
            ]
            packed.append(entry)
        return packed

    return {**{key: value for key, value in outline.items() if key not in {"metrics", "fact_contract"}},
            "mainline": pack(outline["mainline"]), "endings": pack(outline["endings"]),
            "branches": [{**branch, "nodes": pack(branch["nodes"])} for branch in outline["branches"]]}


def _literary_outline(context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep whole-story prose and routes, leaving technical evidence to the fact review."""

    field_sets: dict[tuple[str, ...], str] = {}

    def pack(rows):
        nodes = []
        for row in rows:
            node = {key: value for key, value in row.items() if key != "completion_contract"}
            relationship, transition = node.get("relationship_goal"), node.get("transition_goal")
            if (relationship.get("text") if isinstance(relationship, Mapping) else relationship) == (
                    transition.get("text") if isinstance(transition, Mapping) else transition):
                node.pop("relationship_goal", None)
            fields = tuple(node.pop("text_repair_fields"))
            node["repair_field_set"] = field_sets.setdefault(fields, str(len(field_sets)))
            node["goals"] = [
                {key: value for key, value in goal.items() if key not in {"evidence", "source_ids", "state_effects"}}
                for goal in node.get("goals", ())
            ]
            nodes.append(node)
        return nodes

    outline = {
        **{key: value for key, value in context.items() if key != "fact_contract"},
        "mainline": pack(context["mainline"]),
        "endings": pack(context["endings"]),
        "branches": [{**branch, "nodes": pack(branch["nodes"])} for branch in context["branches"]],
    }
    outline["repair_field_sets"] = {key: fields for fields, key in field_sets.items()}
    return outline


def _literary_issue(context: Mapping[str, Any], raw: Mapping[str, Any], target_ids: list[str]) -> dict[str, Any]:
    if (not isinstance(raw, Mapping) or raw.get("dimension") not in QUALITY_DIMENSIONS
            or raw.get("severity") not in {"blocking", "major", "minor"}
            or not target_ids or not set(target_ids).issubset(context_nodes(context))
            or raw.get("repair_scope") not in {"text", "structure"}
            or not isinstance(raw.get("preserve"), list)
            or any(not isinstance(item, str) or not item.strip() for item in raw["preserve"])):
        raise QualityAssessmentError("invalid_quality_assessment")
    fields = {key: str(raw.get(key) or "").strip()
              for key in ("problem", "modification_plan", "expected_result")}
    if not all(fields.values()):
        raise QualityAssessmentError("invalid_quality_assessment")
    return classify_repair(context, {"dimension": raw["dimension"], "severity": raw["severity"],
        "target_node_ids": list(dict.fromkeys(target_ids)), **fields,
        "preserve": [item.strip() for item in raw["preserve"]],
        "repair_scope": raw["repair_scope"], "repairable": raw["repair_scope"] == "text",
        "repair_targets": deepcopy(raw.get("repair_targets"))})


def _complete_chunk_coverage(context: Mapping[str, Any], assessment: Mapping[str, Any]) -> bool:
    """Old reports remain readable; only a complete current audit can authorize a repair."""
    tasks = fact_review_tasks(context)
    expected_tasks = {task["id"] for task in tasks}
    expected_nodes = set(context_nodes(context))
    expected_edges = {(task["source_node_id"], task["route_id"], task["target_node_id"])
                      for task in tasks if task["kind"] == "transition"}

    def exact(values, expected):
        return (isinstance(values, list) and all(isinstance(value, str) for value in values)
                and len(values) == len(expected) and set(values) == expected)

    def edges(rows):
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) or any(
                not isinstance(row.get(key), str) for key in ("source_node_id", "route_id", "target_node_id")) for row in rows):
            return None
        return [(row["source_node_id"], row["route_id"], row["target_node_id"]) for row in rows]

    facts, literature = assessment.get("fact_check"), assessment.get("literary_coverage")
    if (not isinstance(facts, Mapping) or facts.get("status") != "complete"
            or not exact(facts.get("checked_tasks"), expected_tasks)
            or not exact(facts.get("checked_node_ids"), expected_nodes)
            or not isinstance(literature, Mapping) or literature.get("status") != "complete"
            or not exact(literature.get("checked_node_ids"), expected_nodes)):
        return False
    for report in (facts, literature):
        covered = edges(report.get("checked_edges"))
        if covered is None or len(covered) != len(expected_edges) or set(covered) != expected_edges:
            return False
    blocks = literature.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return False
    covered_nodes, covered_edges, block_ids = set(), [], set()
    for block in blocks:
        if (not isinstance(block, Mapping) or not isinstance(block.get("block_id"), str)
                or block["block_id"] in block_ids or not isinstance(block.get("checked_node_ids"), list)
                or not 1 <= len(block["checked_node_ids"]) <= 2
                or any(not isinstance(value, str) for value in block["checked_node_ids"])
                or len(set(block["checked_node_ids"])) != len(block["checked_node_ids"])
                or not set(block["checked_node_ids"]).issubset(expected_nodes)):
            return False
        block_edges = edges(block.get("covered_edges"))
        if block_edges is None or any(source not in block["checked_node_ids"] or target not in block["checked_node_ids"]
                                      for source, _, target in block_edges):
            return False
        block_ids.add(block["block_id"])
        covered_nodes.update(block["checked_node_ids"])
        covered_edges.extend(block_edges)
    return (covered_nodes == expected_nodes and len(covered_edges) == len(expected_edges)
            and set(covered_edges) == expected_edges)


class NumericV2QualityAssessor(ModelAgent):
    """Explicitly assess the full story and optimize one selected node after user confirmation."""

    def __init__(self, model_call=None) -> None:
        super().__init__("NEKO_Numeric_drama Quality Assessor", model_call)

    def process(self, input_data: dict[str, Any]) -> dict[str, Any]:
        story = input_data.get("story")
        setup = input_data.get("setup")
        authoring = input_data.get("authoring")
        if not all(isinstance(item, Mapping) for item in (story, setup, authoring)):
            raise QualityAssessmentError("quality_assessment_input_required")
        return self.assess(story=story, setup=setup, authoring=authoring)

    def assess(
        self,
        *,
        story: Mapping[str, Any],
        setup: Mapping[str, Any],
        authoring: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = self._assessment_context(story, setup, authoring)
        entry_rule = (_ENTRY_PLAYER_INPUTS_RULE if any(
            "entry_player_inputs" in node for node in context_nodes(context).values()) else "")
        # The evidence catalog exists only for this scoring run; restored reports retain the existing path/quote protocol.
        fact_outline, fact_sources, author_parts, author_fragment_paths = _audit_sources(context)
        original_rule = _AUTHOR_ORIGINAL_REF_RULE if author_parts is not None else ""
        terminal_ids = sorted({node["id"] for node in context.get("endings", [])} | {
            node["id"] for node in story.get("nodes", [])
            if node.get("type") == "ending" or node.get("terminal") is True})
        facts = {"checked_node_ids": [], "issues": [], "contract_checks": []}
        issue_batches: list[tuple[set[str], list[dict]]] = []
        seen_issues: set[str] = set()
        evidence_paths: dict[str, list[str]] = {}

        def source_refs(value):
            if isinstance(value, Mapping):
                if set(value) == {"ref", "text"} and isinstance(value["ref"], str) and isinstance(value["text"], str):
                    return {value["ref"]}
                return set().union(*(source_refs(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(source_refs(item) for item in value))
            return set()

        # Internal duties and actual graph handoffs own disjoint target subsets.
        # Citation IDs come from the one whole-story catalog, never rebuilt per task.
        try:
            tasks = fact_review_tasks(context)
        except ValueError as error:
            raise QualityAssessmentError("invalid_fact_review", phase="facts",
                                         provider_details={"validation_error": str(error)}) from error
        completed_tasks = set()
        for task in tasks:
            node_id = task.get("node_id", task.get("source_node_id"))
            try:
                scoped, dependency_originals = fact_task_context(context, fact_outline, task)
                scoped_ids = self._context_node_ids(scoped)
                direction_rule = _story_direction_rule(scoped)
                scoped, shared_rule = _pack_shared_branches(scoped)
                visible_sources = {ref: fact_sources[ref] for ref in source_refs([scoped, dependency_originals])}
                targets = [row for row in build_contract_targets(context, {node_id}) if row["id"] in task["target_ids"]]
                binding_request = (build_exit_binding_request(context, scoped, node_id, set(terminal_ids))
                                   if task["kind"] == "transition" else {"routes": [], "fact_definitions": []})
                exit_bindings = self._exit_bindings(binding_request)
                exit_candidates = unproven_exit_candidates(binding_request, exit_bindings, visible_sources)
                exit_sources = {ref: fact_sources[ref] for ref in source_refs(binding_request)}
                exit_report, exit_issues = self._review_exit_candidates(context, binding_request, exit_candidates,
                                                                       scoped_ids, exit_sources)
                reviewed_refs = {row["claim_ref"] for row in exit_bindings if row["kind"] == "source_history"}
                main_outline = _omit_reviewed_exit_history(scoped, reviewed_refs)
                main_sources = {ref: fact_sources[ref] for ref in source_refs([main_outline, dependency_originals])}
                bind_contract_targets(targets, main_sources)
                messages = [
                    {"role": "system", "content": REPAIR_PLAN_RULE + "\n" + OPENING_STATE_COMPARISON_RULE + entry_rule + direction_rule + shared_rule + original_rule + "\n\n" + fact_review_prompt(task)},
                    {"role": "user", "content": json.dumps({"audit_task": task, "dependency_originals": dependency_originals, "contract_targets": targets,
                        "repair_target_catalog": _fact_repair_catalog(context, scoped_ids),
                        "story_outline": main_outline, "checked_node_ids": [node_id], "start_node_id": story.get("start_node_id"),
                        "terminal_node_ids": terminal_ids}, ensure_ascii=False, separators=(",", ":"))}]
                preserved_alternatives = []
                preserved_issues = None
                location_error = False
                for attempt in range(2):
                    response = self.call_llm(messages, temperature=0.1, max_tokens=6000, max_retries=1,
                        response_format={"type": "json_object"},
                        thinking={"type": "disabled"}, operation=(
                            "numeric_v2_fact_review" if attempt == 0 else "numeric_v2_fact_review_format_repair"))
                    raw_report = None
                    try:
                        raw_report = self._parse_response(response, "invalid_fact_review")
                        if preserved_issues is not None and _fact_core(raw_report.get("issues", [])) != _fact_core(preserved_issues):
                            raise ValueError("fact_location_repair_changed_findings")
                        corrected = _choice_coverage_rows(raw_report, targets, main_sources)
                        if any(not any(all(row.get(key) == value for key, value in original.items())
                                       for row in corrected) for original in preserved_alternatives):
                            raise ValueError("fact_format_repair_changed_choice_coverage")
                        review = validate_fact_review(context, raw_report, scoped_ids,
                            evidence_sources=main_sources,
                            checked_node_ids={node_id}, expected_target_ids=task["target_ids"],
                            strict_repair_targets=attempt == 0)
                        review["issues"] = [*exit_issues, *review["issues"]]
                        raw_report = {**raw_report, "issues": [*exit_report["issues"], *raw_report["issues"]]}
                        break
                    except (QualityAssessmentError, ValueError) as error:
                        if attempt or isinstance(error, QualityAssessmentError) and error.code != "invalid_fact_review":
                            raise
                        preserved_alternatives = _choice_coverage_rows(raw_report, targets, main_sources)
                        try:
                            validate_fact_review(context, raw_report, scoped_ids, evidence_sources=main_sources,
                                checked_node_ids={node_id}, check_contracts=False)
                            preserved_issues = deepcopy(raw_report["issues"])
                        except (KeyError, TypeError, ValueError):
                            pass
                        if str(error) == "invalid_fact_repair_target":
                            location_error = True
                        # One visible format correction for this node, not a semantic retry or provider retry.
                        messages.extend(_format_repair_messages(response, error,
                            "纠正事实报告的JSON及字段合同。正文引文用中文引号。保留确有证据的问题及可核实引用，"
                            "移出仅观察或并无冲突的条目；只用本次提供的证据ref和节点字段。"
                            "contract_checks须逐项覆盖contract_targets，补全validation_error指出的字段，包括status。"
                            "逐条件status只填clear或uncertain；修订问题放回原issues举证，不能判断填uncertain。"
                            "不返回issue_indexes或requires_revision，不编造冲突，也不能省略目标。"
                            "required_alternatives中的原选项/拒绝身份及判断理由须保留，不能清空或用重复行顶替；"
                            "交接项的choice_quote、compatible及counterexample也须保留，不得在修格式时改成相容或清掉反例；"
                            "仅纠正其类型或路线字段。按completion_value_types返回JSON原类型，严禁自动转换、"
                            "改成事实等式字符串或改用空数组绕过。", include_excerpt=False, details={
                                "required_target_ids": task["target_ids"],
                                "required_issues": preserved_issues if preserved_issues is not None else (raw_report or {}).get("issues", []),
                                **({
                                    "location_instruction": "只从本次repair_target_catalog核对实际存在的字段；可核实的更深子字段也须真实存在。"
                                    "保留required_issues的问题、证据、结论与保留要求，只能调整repair_targets、modification_plan、repair_scope。"
                                    "仍无法定位时保留原问题及未核实位置，不猜字段，后续禁止自动修复。"} if location_error else {}),
                                "required_alternatives": preserved_alternatives,
                                "completion_value_types": [{"target_id": target["id"],
                                    "expected_json_type": {bool: "bool", int: "int", str: "string"}[
                                        type(target["requirement"]["equals"])],
                                    "requirement": target["requirement"]} for target in targets
                                    if type(target["requirement"].get("equals")) in (bool, int, str)],
                            }))
                completed_tasks.add(task["id"])
                if task["kind"] == "node":
                    facts["checked_node_ids"].extend(review["checked_node_ids"])
                batch = []
                for issue, raw_issue in zip(review["issues"], raw_report["issues"], strict=True):
                    if node_id not in issue["target_node_ids"]:
                        continue
                    paths = [author_fragment_paths.get(ref, item["path"]) for ref, item in
                             zip(raw_issue["evidence_refs"], issue["evidence"], strict=True)]
                    fingerprint = json.dumps([{key: value for key, value in issue.items() if key != "issue_id"}, paths],
                                             ensure_ascii=False, sort_keys=True)
                    if fingerprint in seen_issues:
                        continue
                    seen_issues.add(fingerprint)
                    issue["issue_id"] = f"fact_issue_{len(seen_issues):02d}"
                    evidence_paths[issue["issue_id"]] = paths
                    batch.append(issue)
                facts["contract_checks"].extend(review["contract_checks"])
                if batch:
                    issue_batches.append((scoped_ids, batch))
            except QualityAssessmentError as error:
                error.phase = "facts"
                error.provider_details["node_id"] = node_id
                raise
            except ValueError as error:
                raise QualityAssessmentError("invalid_fact_review", phase="facts",
                    provider_details={"node_id": node_id, "task_id": task["id"], "validation_error": str(error)}) from error
        expected_checks = {row["id"] for row in build_contract_targets(context, set(context_nodes(context)))}
        actual_checks = [row["target_id"] for row in facts["contract_checks"]]
        if (len(completed_tasks) != len(tasks) or completed_tasks != {task["id"] for task in tasks}
                or len(actual_checks) != len(set(actual_checks))
                or set(actual_checks) != expected_checks):
            raise QualityAssessmentError("incomplete_fact_review", phase="facts")
        # Review one allegation at a time; a neighbor's unrelated prose is not another task.
        issue_batches = [(scope, [issue]) for scope, batch in issue_batches for issue in batch]
        # Each origin keeps a bounded evidence scope; the report is only published after all batches pass validation.
        for scoped_ids, batch in issue_batches:
            try:
                # Verify the allegation against source text before considering its suggested rewrite.
                # Keep the complete original report below; repair plans must not bias this evidence check.
                proposed = [{**{key: issue[key] for key in ("issue_id", "problem", "target_node_ids")},
                    "evidence": [{"path": path} for path in evidence_paths[issue["issue_id"]]],
                    "repair_targets": review_targets(issue)} for issue in batch]
                focus = {node_id for issue in proposed for node_id in issue["target_node_ids"]}
                focus.update(target["node_id"] for issue in proposed for target in issue["repair_targets"])
                for issue in proposed:
                    for evidence in issue["evidence"]:
                        parts = evidence["path"].split("/")
                        if parts[1] in ("mainline", "endings"):
                            focus.add(context[parts[1]][int(parts[2])]["id"])
                        elif parts[1] == "branches" and len(parts) >= 6 and parts[3] == "nodes":
                            focus.add(context["branches"][int(parts[2])]["nodes"][int(parts[4])]["id"])
                outline = _repair_outline(context, focus, include_neighbors=False)
                # An allegation about an automatic entry must retain its real
                # inbound dispatch, even when it did not cite the source prose.
                outlined_nodes = context_nodes(outline)
                for source_id, source in context_nodes(context).items():
                    if source_id not in focus and any(route["target_node_id"] in focus for route in source.get("outgoing_routes", [])):
                        outlined_nodes[source_id]["outgoing_routes"] = [deepcopy(route) if route["target_node_id"] in focus
                            else {"id": route["id"], "target_node_id": route["target_node_id"]}
                            for route in source.get("outgoing_routes", [])]
                if author_parts is not None:
                    outline["author_intent"] = author_parts
                direction_rule = _story_direction_rule(outline)
                outline, shared_rule = _pack_shared_branches(outline)
                messages = [
                    {"role": "system", "content": EVIDENCE_REVIEW_PROMPT + direction_rule + shared_rule + original_rule},
                    {"role": "user", "content": json.dumps({"story_outline": outline, "proposed_issues": proposed,
                                                           "start_node_id": story.get("start_node_id"),
                                                           "terminal_node_ids": terminal_ids}, ensure_ascii=False)}]
                for attempt in range(2):
                    response = self.call_llm(messages, temperature=0.1, max_tokens=6000, max_retries=1,
                        response_format={"type": "json_object"}, thinking={"type": "disabled"},
                        operation=("numeric_v2_fact_evidence_review" if attempt == 0
                                   else "numeric_v2_fact_evidence_review_format_repair"))
                    try:
                        reviewed = validate_evidence_review(batch, self._parse_response(response, "invalid_fact_evidence_review"))
                        break
                    except (QualityAssessmentError, ValueError) as error:
                        if attempt or isinstance(error, QualityAssessmentError) and error.code != "invalid_fact_evidence_review":
                            raise
                        messages.extend(_format_repair_messages(response, error,
                            "纠正证据复核的JSON及checks/target_checks合同。"
                            "所有原issue_id及其原repair_targets必须各覆盖一次，requires_change与preserve_original须为布尔值。"
                            "target_checks须放在对应issue_id的check内部，不能放顶层。"
                            "正文引文用中文引号，只返回完整JSON。"))
                        messages[-1]["content"] = json.dumps({
                            **json.loads(messages[-1]["content"]),
                            "required_checks": [{"issue_id": issue["issue_id"], "target_checks": review_targets(issue)}
                                                for issue in batch],
                        }, ensure_ascii=False)
                facts["issues"].extend(reviewed)
            except QualityAssessmentError as error:
                error.phase = "evidence"
                raise
            except ValueError as error:
                raise QualityAssessmentError("invalid_fact_evidence_review", phase="evidence") from error
        protected = protected_targets(facts["issues"])
        facts["issues"] = [classify_repair(context, issue, protected=protected) for issue in facts["issues"]]
        supported = [issue for issue in facts["issues"] if issue["evidence_review"]["verdict"] == "supported"]
        for check in facts["contract_checks"]:
            related = [issue for issue in supported if check["node_id"] in issue["target_node_ids"]
                       and any(item["path"] == check["definition_path"] for item in issue["evidence"])]
            check["issue_ids"] = [issue["issue_id"] for issue in related]
            # Related evidence is not authority to change the definition; original issues own scope and severity.
            if any(issue["severity"] in {"major", "blocking"} for issue in related):
                check["status"] = "requires_revision"
        # Keep original false positives in the final report, but exclude rejected accusations from literary input to avoid inducing renewed deductions.
        literary_facts = {**facts, "issues": supported,
                          "excluded_issue_ids": [issue["issue_id"] for issue in facts["issues"] if issue["evidence_review"]["verdict"] != "supported"],
                          "protected_repair_targets": protected}
        try:
            report = self._assess_literature(context, literary_facts)
        except QualityAssessmentError as error:
            error.phase = "literature"
            raise
        # The server preserves factual conclusions verbatim; literary models cannot remove them, offset them through rescoring, or overwrite evidence.
        report["issues"] = facts["issues"] + [classify_repair(context, dict(issue, source="literature"), protected=protected) for issue in report["issues"]]
        report["relationship_advice"] = [classify_repair(context, issue, protected=protected) for issue in report["relationship_advice"]]
        report["fact_check"] = {"status": "complete", "checked_node_ids": facts["checked_node_ids"],
                                "issue_count": len(facts["issues"]), "supported_count": len(supported),
                                "evidence_review_status": "complete" if facts["issues"] else "not_needed",
                                "checked_tasks": [task["id"] for task in tasks],
                                "checked_edges": [{key: task[key] for key in ("source_node_id", "route_id", "target_node_id")}
                                                  for task in tasks if task["kind"] == "transition"]}
        checks = facts["contract_checks"]
        contract_status = ("requires_revision" if any(row["status"] == "requires_revision" for row in checks)
                           else "needs_rehearsal" if any(row["status"] == "uncertain" for row in checks)
                           else "clear" if checks else "not_needed")
        report["contract_check"] = {"status": contract_status, "checks": checks, "rehearsal": "not_run"}
        report["passed"] = report["passed"] and not any(
            issue["severity"] in {"major", "blocking"} for issue in supported
        ) and contract_status in {"clear", "not_needed"}
        report = self._review_plans(context, report)
        assign_shared_plans(report)
        return report

    def _exit_bindings(self, request):
        if not request["routes"]:
            return []
        messages = [{"role": "system", "content": EXIT_BINDING_PROMPT},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
        for attempt in range(2):
            response = self.call_llm(messages, temperature=0.1, max_tokens=3000, max_retries=1,
                response_format={"type": "json_object"}, thinking={"type": "disabled"},
                operation=("numeric_v2_exit_fact_binding" if not attempt
                           else "numeric_v2_exit_fact_binding_format_repair"))
            try:
                return validate_exit_bindings(request, self._parse_response(response, "invalid_exit_bindings"))
            except (QualityAssessmentError, ValueError) as error:
                if attempt or isinstance(error, QualityAssessmentError) and error.code != "invalid_exit_bindings":
                    raise
                messages.extend([{"role": "assistant", "content": response}, {"role": "user", "content": json.dumps({
                    "validation_error": str(error.__cause__ or error),
                    "instruction": "只纠正绑定JSON合同，逐路线逐claim恰好覆盖一次。沿用原ref和已声明事实及精确值类型；"
                        "source_history的值不能为null，无法确定唯一原值时改用other且fact_key/value均为null。",
                }, ensure_ascii=False)}])

    def _review_exit_candidates(self, context, request, candidates, node_ids, evidence_sources):
        if not candidates:
            return {"issues": []}, []
        messages = [{"role": "system", "content": EXIT_FACT_REVIEW_PROMPT},
                    {"role": "user", "content": json.dumps({"exit_context": request,
                        "exit_candidates": [{key: value for key, value in candidate.items() if key != "evidence"}
                                            for candidate in candidates],
                        "checked_node_ids": [request["source_node_id"]]}, ensure_ascii=False)}]
        def findings(payload, *, location_change=False):
            """Only validated findings are immutable; malformed list slots are not findings."""
            valid_issues, gaps, claim_additions = {}, set(), {}
            if not isinstance(payload, Mapping):
                return valid_issues, gaps, claim_additions
            rows = payload.get("issues")
            rows = rows if isinstance(rows, list) else []
            for row in rows:
                try:
                    validated = validate_fact_review(context, {"checked_node_ids": [request["source_node_id"]], "issues": [row]},
                        node_ids, evidence_sources=evidence_sources,
                        checked_node_ids={request["source_node_id"]}, check_contracts=False)
                    if request["source_node_id"] in row["target_node_ids"]:
                        finding = validated["issues"][0]
                        mutable = {"evidence"} | ({"repair_targets", "modification_plan", "repair_scope", "repairable"}
                                                  if location_change else set())
                        identity = json.dumps({key: value for key, value in finding.items() if key not in mutable},
                                              ensure_ascii=False, sort_keys=True)
                        valid_issues.setdefault(identity, set()).update(row["evidence_refs"])
                        related = [candidate for candidate in candidates
                                   if candidate["definition_ref"] in row["evidence_refs"]
                                   or set(candidate["claim_refs"]).intersection(row["evidence_refs"])]
                        claim_additions.setdefault(identity, set()).update(
                            ref for candidate in related for ref in candidate["claim_refs"])
                except ValueError:
                    continue
            checks = payload.get("candidate_checks")
            for row in checks if isinstance(checks, list) else []:
                for candidate in candidates:
                    if (not isinstance(row, Mapping) or row.get("status") != "gap"
                            or any(row.get(key) != candidate[key] for key in ("route_id", "fact_key", "value"))
                            or type(row.get("value")) is not type(candidate["value"])):
                        continue
                    # Bad citations may be corrected, but do not authorize a
                    # format-only retry to reverse this identified gap.
                    gaps.add(json.dumps([row[key] for key in ("route_id", "fact_key", "value")],
                                        ensure_ascii=False))
            return valid_issues, gaps, claim_additions

        preserved = None
        location_error = False
        for attempt in range(2):
            response = self.call_llm(messages, temperature=0.1, max_tokens=6000, max_retries=1,
                response_format={"type": "json_object"}, thinking={"type": "disabled"},
                operation=("numeric_v2_exit_fact_review" if not attempt else "numeric_v2_exit_fact_review_format_repair"))
            raw = None
            try:
                raw = self._parse_response(response, "invalid_exit_fact_review")
                if preserved is not None:
                    current = findings(raw, location_change=location_error)
                    if (any(not refs.issubset(current[0].get(identity, set()))
                            or not (current[0].get(identity, set()) - refs).issubset(preserved[2][identity])
                            for identity, refs in preserved[0].items())
                            or not preserved[1].issubset(current[1])):
                        raise ValueError("exit_format_repair_changed_findings")
                errors = []
                try:
                    reviewed = validate_fact_review(context, raw, node_ids, evidence_sources=evidence_sources,
                        checked_node_ids={request["source_node_id"]}, check_contracts=False,
                        strict_repair_targets=attempt == 0)
                except ValueError as error:
                    errors.append(str(error))
                try:
                    checks = validate_exit_candidate_checks(request, candidates, raw, evidence_sources)
                except ValueError as error:
                    errors.append(str(error))
                if errors:
                    raise ValueError(" | ".join(errors))
                uncertain = [check for check in checks if check["status"] == "uncertain"]
                if uncertain:
                    raise QualityAssessmentError("exit_fact_review_inconclusive", provider_details={"checks": uncertain})
                if any(request["source_node_id"] not in issue["target_node_ids"] for issue in raw["issues"]):
                    raise ValueError("exit_issue_requires_source_node")
                return raw, reviewed["issues"]
            except (QualityAssessmentError, ValueError) as error:
                if attempt or isinstance(error, QualityAssessmentError) and error.code != "invalid_exit_fact_review":
                    raise
                location_error = "invalid_fact_repair_target" in str(error)
                preserved = findings(raw, location_change=location_error)
                required_claims = []
                for candidate in candidates:
                    if json.dumps([candidate[key] for key in ("route_id", "fact_key", "value")], ensure_ascii=False) not in preserved[1]:
                        continue
                    related = [index for index, issue in enumerate(raw.get("issues", []))
                               if isinstance(issue, Mapping) and isinstance(issue.get("evidence_refs"), list)
                               and all(isinstance(ref, str) for ref in issue["evidence_refs"])
                               and (candidate["definition_ref"] in issue["evidence_refs"]
                                    or set(candidate["claim_refs"]).intersection(issue["evidence_refs"]))]
                    required_claims.append({**{key: candidate[key] for key in ("route_id", "fact_key", "value", "definition_ref")},
                        "required_claim_refs": candidate["claim_refs"], "related_issue_indexes": related})
                # This compact specialist input leaves room for the complete
                # rejected reply; formatting cannot silently clear a finding.
                messages.extend([{"role": "assistant", "content": response}, {"role": "user", "content": json.dumps({
                    "validation_error": str(error.__cause__ or error),
                    "required_issue_claims": required_claims,
                    **({"repair_target_catalog": _fact_repair_catalog(context, {request["source_node_id"]})}
                       if location_error else {}),
                    "instruction": "只纠正JSON和原引用字段，不删除问题或重新改判。每条引文必须指向原文，"
                        "每个问题至少引用一个候选claim_ref和另一个原字段，evidence_refs只填原E编号，不填路径。"
                        "原证据不得删除或替换；缺少claim时，仅从required_issue_claims给原有相关问题追加真实claim引用，不加无关ref。"
                        + ("有效问题的描述、级别、主体、预期、保留项和原引用逐字保留；仅从实际字段目录重选repair_targets，"
                           "可相应纠正modification_plan及repair_scope；仍无法核实则保留问题与未核实位置，禁止猜修。"
                           if location_error else
                           "有效问题的描述、级别、主体、修法、预期、保留项和修订字段逐字保留；原已核实引用保留，只补缺失引用。") +
                        "candidate_checks逐项完整覆盖，保留原gap结论与每条有效独立问题。"
                        "不完整重复占位可合并补全，不能用新问题顶替有效问题；无法纠正则本轮失败。",
                }, ensure_ascii=False)}])

    def _review_plans(self, context: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
        """Review eligible candidates once as a group; request or format failures must not return a partially savable report."""
        result = deepcopy(dict(report))
        rows = [*result["issues"], *result["relationship_advice"]]
        protected = protected_targets(result["issues"])
        candidates = [row for row in rows if row.get("repairable") is True]
        if candidates:
            # Plan review checks modification commands only; native stress tests mistook fields mentioned in issue context/quotations for requested changes.
            # Preserve full original issues/evidence in reports and node revisions; isolate only review input here, retaining field-by-field factual review.
            plans = [{key: deepcopy(row[key]) for key in (
                "issue_id", "source", "modification_plan", "suggestion", "preserve",
                "target_node_ids", "repair_targets", "evidence_review") if key in row}
                for row in candidates]
            nodes = context_nodes(context)
            removable_bridges = []
            for node_id, field in sorted({(target["node_id"], target["field"])
                                           for row in candidates for target in effective_targets(row)}):
                if not field.endswith("/transition_contract/bridge_scene_narration"):
                    continue
                route = _resolve_pointer(nodes[node_id], field.rsplit("/", 2)[0])
                opening = nodes.get(route["target_node_id"], {}).get("opening_scene")
                if can_remove_bridge_narration(route["transition_contract"], opening):
                    removable_bridges.append({"node_id": node_id, "field": field})
            outline = _plan_review_outline(context, plans, protected)
            author_parts = _author_intent_parts(context)
            if author_parts is not None:
                outline["author_intent"] = author_parts
            outline, shared_rule = _pack_shared_branches(outline)
            try:
                response = self.call_llm(
                    [{"role": "system", "content": PLAN_REVIEW_PROMPT + _story_direction_rule(context) + shared_rule
                      + (_AUTHOR_ORIGINAL_REF_RULE if author_parts is not None else "")},
                     {"role": "user", "content": json.dumps({"plans": plans, "story_outline": outline,
                         "global_strengths_to_preserve": result.get("strengths") or [],
                         "protected_repair_targets": protected, "removable_bridge_targets": removable_bridges},
                          ensure_ascii=False, separators=(",", ":"))}],
                    temperature=0.1, max_tokens=6000, max_retries=1,
                    response_format={"type": "json_object"}, thinking={"type": "disabled"},
                    operation="numeric_v2_repair_plan_review",
                )
                reviews = validate_plan_review(context, plans, self._parse_response(response, "invalid_repair_plan_review"))
            except QualityAssessmentError as error:
                error.phase = "plan_review"
                raise
            except ValueError as error:
                raise QualityAssessmentError("invalid_repair_plan_review", phase="plan_review") from error
            for row in candidates:
                row["plan_review"] = reviews[row["issue_id"]]
                row.update(classify_repair(context, row, protected=protected, require_plan_review=True))
        # Only the complete workflow publishes new-version reports; old scores remain readable but old repairable flags cannot authorize execution.
        result.update(repair_plan_version=3, plan_review={
            "status": "complete" if candidates else "not_needed", "reviewed_count": len(candidates)})
        return result

    def _assess_literature(self, context: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
        """Review actual two-scene windows, then score the whole graph once from retained evidence."""
        from utils.tokenize import count_tokens

        marked, sources, author_parts, _ = _audit_sources(context)
        nodes = context_nodes(context)
        pairs = {}
        for source_id, node in nodes.items():
            for route in node.get("outgoing_routes", []):
                key = tuple(dict.fromkeys((source_id, route["target_node_id"])))
                pairs.setdefault(key, []).append({"source_node_id": source_id, "route_id": route["id"],
                                                  "target_node_id": route["target_node_id"]})
        covered = {node_id for pair in pairs for node_id in pair}
        for node_id in nodes.keys() - covered:
            pairs[(node_id,)] = []
        wire_facts = {key: facts[key] for key in ("excluded_issue_ids", "protected_repair_targets") if key in facts}
        wire_facts["issues"] = [{key: issue[key] for key in ("issue_id", "category", "severity", "problem", "target_node_ids") if key in issue}
                                for issue in facts["issues"]]
        blocks, retained, fingerprints = [], [], set()

        def refs(value):
            if isinstance(value, Mapping):
                if set(value) == {"ref", "text"} and isinstance(value["ref"], str) and isinstance(value["text"], str):
                    return {value["ref"]}
                return set().union(*(refs(item) for item in value.values()))
            return set().union(*(refs(item) for item in value)) if isinstance(value, list) else set()

        def source_node(path):
            parts = path.split("/")
            if len(parts) > 2 and parts[1] in {"mainline", "endings"}:
                return context[parts[1]][int(parts[2])]["id"]
            if len(parts) > 4 and parts[1] == "branches" and parts[3] == "nodes":
                return context["branches"][int(parts[2])]["nodes"][int(parts[4])]["id"]
            return None

        for index, (pair, edges) in enumerate(pairs.items()):
            outline = _literary_outline(marked)
            for group in ("mainline", "endings"):
                outline[group] = [node for node in outline[group] if node["id"] in pair]
            outline["branches"] = [{**branch, "nodes": [node for node in branch["nodes"] if node["id"] in pair]}
                                   for branch in outline["branches"] if any(node["id"] in pair for node in branch["nodes"])]
            outline, shared_rule = _pack_shared_branches(outline)
            visible = refs(outline)
            originals = [{"ref": ref, "path": sources[ref]["path"], "node_id": source_node(sources[ref]["path"])}
                         for ref in sorted(visible)]
            request = {"block_id": f"literary:{index}", "checked_node_ids": list(pair), "covered_edges": edges,
                       "story_outline": outline, "original_sources": originals, "fact_review": wire_facts}
            messages = [{"role": "system", "content": _LITERARY_BLOCK_PROMPT + "\n" + STORY_DESIGN_RULE + shared_rule
                         + (_AUTHOR_ORIGINAL_REF_RULE if author_parts is not None else "")},
                        {"role": "user", "content": json.dumps(request, ensure_ascii=False, separators=(",", ":"))}]
            for attempt in range(2):
                response = self.call_llm(messages, temperature=0.1, max_tokens=6000, max_retries=1,
                    response_format={"type": "json_object"}, thinking={"type": "disabled"},
                    operation="numeric_v2_literary_block" if not attempt else "numeric_v2_literary_block_format_repair")
                try:
                    payload = self._parse_response(response, "invalid_literary_block")
                    if (set(payload) != {"checked_node_ids", "node_notes", "issues"}
                            or payload["checked_node_ids"] != list(pair)
                            or not isinstance(payload["node_notes"], list) or not isinstance(payload["issues"], list)):
                        raise ValueError("invalid_literary_block")
                    notes = {}
                    for note in payload["node_notes"]:
                        if (not isinstance(note, Mapping) or set(note) != {"node_id", "assessment", "evidence_refs"}
                                or note["node_id"] not in pair or note["node_id"] in notes
                                or not isinstance(note["assessment"], str) or not note["assessment"].strip()
                                or count_tokens(note["assessment"]) > 200
                                or not isinstance(note["evidence_refs"], list) or not note["evidence_refs"]
                                or any(ref not in visible for ref in note["evidence_refs"])
                                or not any(source_node(sources[ref]["path"]) == note["node_id"] for ref in note["evidence_refs"])):
                            raise ValueError("invalid_literary_note")
                        notes[note["node_id"]] = deepcopy(note)
                    if set(notes) != set(pair):
                        raise ValueError("incomplete_literary_block")
                    local_issues = []
                    for raw in payload["issues"]:
                        if (not isinstance(raw, Mapping) or not isinstance(raw.get("evidence_refs"), list)
                                or not raw["evidence_refs"] or any(ref not in visible for ref in raw["evidence_refs"])
                                or not isinstance(raw.get("target_node_ids"), list)
                                or not set(raw["target_node_ids"]).issubset(pair)):
                            raise ValueError("invalid_literary_evidence")
                        issue = _literary_issue(context, raw, raw["target_node_ids"])
                        local_issues.append({**issue, "evidence": [deepcopy(sources[ref]) for ref in raw["evidence_refs"]]})
                    break
                except (QualityAssessmentError, ValueError, TypeError, KeyError) as error:
                    if attempt:
                        raise QualityAssessmentError("invalid_literary_block", phase="literature") from error
                    messages.extend(_format_repair_messages(response, error,
                        "只纠正本块JSON、原节点覆盖及原引文，保留可核实问题，不新增分数或原文。"))
            for issue in local_issues:
                fingerprint = json.dumps(issue, ensure_ascii=False, sort_keys=True)
                if fingerprint not in fingerprints:
                    fingerprints.add(fingerprint)
                    retained.append({**issue, "issue_id": f"L{len(retained) + 1}"})
            blocks.append({"block_id": request["block_id"], "checked_node_ids": list(pair),
                           "covered_edges": edges, "node_notes": [notes[node_id] for node_id in pair]})
        checked_edges = [edge for block in blocks for edge in block["covered_edges"]]
        edge_keys = lambda rows: [(row["source_node_id"], row["route_id"], row["target_node_id"]) for row in rows]
        expected_edges = [{"source_node_id": node_id, "route_id": route["id"], "target_node_id": route["target_node_id"]}
                          for node_id, node in nodes.items() for route in node.get("outgoing_routes", [])]
        if ({node for block in blocks for node in block["checked_node_ids"]} != set(nodes)
                or len(checked_edges) != len(expected_edges) or set(edge_keys(checked_edges)) != set(edge_keys(expected_edges))):
            raise QualityAssessmentError("incomplete_literary_coverage", phase="literature")
        graph = {"nodes": [{key: deepcopy(node[key]) for key in (
                    "id", "title", "summary", "scene_summary", "opening_scene", "narrative_focus", "narrative_summary",
                    "relationship_goal", "transition_goal") if key in node} for node in nodes.values()],
                 "edges": [{"source_node_id": node_id, **deepcopy(route)}
                           for node_id, node in nodes.items() for route in node.get("outgoing_routes", [])]}
        graph_context = {key: deepcopy(context[key]) for key in (
            "author_intent", "background", "characters", "metrics", "key_props", "start_node_id") if key in context}
        if author_parts is not None:
            graph_context["author_intent"] = author_parts
        note_refs = {ref for block in blocks for note in block["node_notes"] for ref in note["evidence_refs"]}
        source_refs = {(row["path"], row["quote"]): ref for ref, row in sources.items()}
        retained_wire = [{**{key: issue[key] for key in (
            "issue_id", "dimension", "severity", "target_node_ids", "problem")},
            "evidence_refs": [source_refs[(row["path"], row["quote"])] for row in issue["evidence"]]} for issue in retained]
        note_refs.update(ref for issue in retained_wire for ref in issue["evidence_refs"])
        request = {"fact_review": wire_facts, "story_graph": graph, "literary_blocks": blocks,
                   "story_context": graph_context, "allowed_target_node_ids": sorted(nodes),
                   "original_sources": _shared_original_sources(sources, note_refs, graph, graph_context),
                   "retained_local_issues": retained_wire}
        response = self.call_llm([
            {"role": "system", "content": _ASSESSMENT_PROMPT +
             "\n本次全局评分输入为完整story_graph、story_context及已完成literary_blocks；不是整稿逐字重发。"
             "按全部路线、各结局及跨幕铺垫回收统一评分，绝不平均块分数，重叠节点不重复加权。"
             "retained_local_issues由程序保留，不重写、删除或扩大其修订范围；可在related_issue_ids关联其L编号，"
             "新问题编号从所给最大L编号之后开始。original_sources恢复局部意见所引原文及完整路径。"
             "其中text_path是本次请求内完整原文字段的JSON Pointer，与quote等价；原path仍是作者稿证据位置。"
             "局部意见仍不是运行时事实或最终演绎评分。" + (_AUTHOR_ORIGINAL_REF_RULE if author_parts is not None else "")},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False, separators=(",", ":"))}],
            temperature=0.1, max_tokens=6000, max_retries=1, response_format={"type": "json_object"},
            thinking={"type": "disabled"}, operation="numeric_v2_quality_assessment")
        payload = self._parse_response(response, "invalid_quality_assessment")
        if "issues" in payload or not isinstance(payload.get("scores"), Mapping):
            raise QualityAssessmentError("invalid_quality_assessment")
        flattened = deepcopy(dict(payload))
        flattened["issues"] = [deepcopy(issue) for issue in retained]
        local_ids = {issue["issue_id"] for issue in retained}
        for dimension, row in flattened["scores"].items():
            if not isinstance(row, dict) or not isinstance(row.get("issues"), list) or not isinstance(row.get("fact_issue_ids"), list):
                raise QualityAssessmentError("invalid_quality_assessment")
            for issue in row.pop("issues"):
                if (not isinstance(issue, Mapping) or "dimension" in issue
                        or "issue_id" in issue and not isinstance(issue["issue_id"], str)
                        or issue.get("issue_id") in local_ids):
                    raise QualityAssessmentError("invalid_quality_assessment")
                flattened["issues"].append({**issue, "dimension": dimension})
        report = self._validated_assessment(context, flattened, fact_issues=facts["issues"])
        for issue, original in zip(report["issues"], retained):
            issue["evidence"] = original["evidence"]
        report["literary_coverage"] = {"status": "complete", "blocks": blocks,
            "checked_node_ids": list(nodes), "checked_edges": expected_edges}
        return report

    def optimize_node(
        self,
        *,
        story: Mapping[str, Any],
        setup: Mapping[str, Any],
        authoring: Mapping[str, Any],
        assessment: Mapping[str, Any],
        node_id: str,
    ) -> dict[str, Any]:
        """Combine executable assessment suggestions for the current node and modify only that node in one model call."""

        if assessment.get("repair_plan_version") != 3:
            raise QualityAssessmentError("quality_reassessment_required")
        context = self._assessment_context(story, setup, authoring)
        if node_id not in self._context_node_ids(context):
            raise QualityAssessmentError("quality_issue_stale")
        all_nodes = {
            str(node.get("id") or ""): node
            for node in story.get("nodes") or []
            if isinstance(node, Mapping) and node.get("id")
        }
        if node_id not in all_nodes:
            raise QualityAssessmentError("quality_issue_stale")

        # Preservation requirements cover every suggestion source; recompute from original factual review so old highlights/caches cannot relax permissions.
        protected = protected_targets(assessment.get("issues") or [])
        suggestions: list[dict[str, Any]] = []
        allowed_fields: set[str] = set()
        for issue in assessment.get("issues") or []:
            issue = classify_repair(context, issue, protected=protected, require_plan_review=True) if isinstance(issue, Mapping) else issue
            if (
                isinstance(issue, Mapping)
                and issue.get("repairable") is True
                and node_id in (issue.get("repair_node_ids") or [])
            ):
                allowed_fields.update(target["field"] for target in effective_targets(issue) if target["node_id"] == node_id)
                suggestions.append({
                    "repair_targets": effective_targets(issue),
                    "evidence_review": deepcopy(issue.get("evidence_review")),
                    # Factual suggestions include validated original text; revision must not guess from literary issue summaries alone.
                    "source": "fact_check" if issue.get("source") == "facts" else "quality_issue",
                    "evidence": deepcopy(issue.get("evidence") or []),
                    "dimension": issue.get("dimension"),
                    "problem": issue.get("problem"),
                    "suggestion": issue.get("modification_plan"),
                    "expected_result": issue.get("expected_result"),
                    "preserve": issue.get("preserve") or [],
                })
        for advice in assessment.get("relationship_advice") or []:
            advice = classify_repair(context, advice, protected=protected, require_plan_review=True) if isinstance(advice, Mapping) else advice
            if (
                isinstance(advice, Mapping)
                and advice.get("repairable") is True
                and node_id in (advice.get("repair_node_ids") or [])
            ):
                allowed_fields.update(target["field"] for target in advice["repair_targets"] if target["node_id"] == node_id)
                suggestions.append({
                    "repair_targets": deepcopy(advice["repair_targets"]),
                    "source": "relationship_advice",
                    "problem": advice.get("problem"),
                    "suggestion": advice.get("suggestion"),
                    "expected_result": advice.get("expected_result"),
                    "preserve": [],
                })
        if not suggestions:
            raise QualityAssessmentError("quality_node_not_repairable")

        contract = assessment.get("contract_check")
        checks = contract.get("checks") if isinstance(contract, Mapping) else None
        if (not isinstance(checks, list) or any(not isinstance(check, Mapping) or any(
                not isinstance(value, str) for value in (
                    check.get("target_id"), check.get("kind", "completion"), check.get("definition_path")))
                for check in checks)):
            raise QualityAssessmentError("quality_reassessment_required")
        expected = {(target["id"], target.get("kind", "completion"), target["definition_path"])
                    for target in build_contract_targets(context, set(context_nodes(context)))}
        covered = [(check["target_id"], check.get("kind", "completion"), check["definition_path"])
                   for check in checks]
        if len(covered) != len(expected) or set(covered) != expected:
            # Keep old reports, but do not apply plans from an incomplete contract audit.
            raise QualityAssessmentError("quality_reassessment_required")
        for check in checks:
            matrix = {"entry_ownership": "ownership_checks", "transition_contract": "choice_checks"}.get(check.get("kind"))
            if matrix and (not isinstance(check.get(matrix), list) or not check[matrix]):
                raise QualityAssessmentError("quality_reassessment_required")
        if not _complete_chunk_coverage(context, assessment):
            raise QualityAssessmentError("quality_reassessment_required")

        # Keep cross-act context from all reviewed plans while authorizing only the current node.
        focus = {node_id, *(target["node_id"] for suggestion in suggestions for target in suggestion["repair_targets"])}
        # Merge identical local instructions after narrowing; retain each source's rationale and evidence.
        merged = {}
        for suggestion in suggestions:
            suggestion["repair_targets"] = [
                target for target in suggestion["repair_targets"] if target["node_id"] == node_id
            ]
            key = (suggestion["suggestion"].strip(), tuple(sorted((target["node_id"], target["field"]) for target in suggestion["repair_targets"])))
            if key in merged:
                impact = {key: value for key, value in suggestion.items() if key not in {"suggestion", "repair_targets"}}
                merged[key].setdefault("related_impacts", []).append(impact)
            else:
                merged[key] = suggestion
        suggestions = list(merged.values())
        outline = _optimizer_outline(context, node_id, focus, allowed_fields, story=story)
        target_node = deepcopy(dict(all_nodes[node_id]))
        # Keep raw fields only when the author projection differs or lacks them.
        projected = context_nodes(outline)[node_id]
        for key, projected_key in (("chapter", "title"), ("completion_contract", "completion_contract")):
            if key in target_node and target_node[key] == projected.get(projected_key):
                target_node.pop(key)
        beat = target_node.get("story_beat")
        if isinstance(beat, dict):
            aliases = {"catgirl_situation": "relationship_state",
                       "summary": "scene_summary" if target_node.get("type") == "ending" else "summary"}
            for key in (*aliases, "opening_scene", "narrative_summary", "narrative_focus", "transition_goal",
                        "must_not_happen", "acting_contract", "fixed_narrations", "entry_player_inputs"):
                if key in beat and beat[key] == projected.get(aliases.get(key, key)):
                    beat.pop(key)
            state = beat.get("character_state")
            if isinstance(state, dict):
                aliases = {"catgirl_state": "catgirl", "player_state": "player",
                           "environment_state": "environment", "continuity_from_previous": "continuity",
                           "scene_boundaries": "boundaries", "temporal_context": "temporal_context"}
                for key, field in aliases.items():
                    if key in state and state[key] == projected["character_state"].get(field):
                        state.pop(key)
            goals = beat.get("goals")
            if isinstance(goals, list) and goals:
                for goal, reference in zip(goals, projected["goals"]):
                    if not isinstance(goal, dict) or goal.get("id") != reference["id"]:
                        continue
                    for key in ("id", "owner", "description", "evidence"):
                        if key in goal and goal[key] == reference[key]:
                            goal.pop(key)
                    delivery = goal.get("delivery")
                    if isinstance(delivery, dict) and delivery:
                        for key in ("type", "output_field", "timing", "source_ids", "state_effects"):
                            field = "delivery_type" if key == "type" else key
                            if key in delivery and delivery[key] == reference[field]:
                                delivery.pop(key)
                        if not delivery:
                            goal.pop("delivery")
                if all(goal == {} for goal in goals):
                    beat.pop("goals")
        for route, projected_route in zip(target_node.get("route_gates") or [], projected["outgoing_routes"]):
            if not isinstance(route, dict) or route.get("id") != projected_route["id"]:
                continue
            for key in ("conditions", "target_node_id"):
                if key in route and route[key] == projected_route.get(key):
                    route.pop(key)
            contract = route.get("transition_contract")
            if isinstance(contract, dict):
                reference = projected_route["transition_contract"]
                for key in contract.keys() & reference.keys():
                    if contract[key] == reference[key]:
                        contract.pop(key)
        outline, shared_rule = _pack_shared_branches(outline)
        author_parts = _author_intent_parts(context)
        if author_parts is not None:
            outline["author_intent"] = author_parts
        for suggestion in suggestions:
            for reason in (suggestion, *suggestion.get("related_impacts", ())):
                for evidence in reason.get("evidence", ()):
                    try:
                        original = _optimizer_evidence_source(outline, evidence["path"])
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if isinstance(original, str) and original == evidence.get("quote"):
                        del evidence["quote"]
        response = self.call_llm(
            [
                {"role": "system", "content": _NODE_OPTIMIZATION_PROMPT + _story_direction_rule(context) + shared_rule
                 + (ENTRY_ROUTE_SCOPE_RULE if "entry_route_scope" in outline else "")
                 + (_AUTHOR_ORIGINAL_REF_RULE if author_parts is not None else "")},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "story_outline": outline,
                            "start_node_id": story.get("start_node_id"),
                            "target_node": target_node,
                            "accepted_suggestions": suggestions,
                            "global_strengths_to_preserve": assessment.get("strengths") or [],
                            "protected_repair_targets": protected,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            temperature=0.15,
            max_tokens=6000,
            max_retries=1,
            response_format={"type": "json_object"},
            thinking={"type": "disabled"},
            operation="numeric_v2_quality_single_node_optimization",
        )
        payload = self._parse_response(response, "invalid_quality_repair")
        repaired = self._apply_node_updates(story, payload, [node_id])
        # Actual changes must remain inside the plan shown to authors; text permissions cannot justify rewriting correct openings or other nodes.
        before_nodes = context_nodes(context)
        after_nodes = context_nodes(self._assessment_context(repaired, setup, authoring))
        changes = [(current_id, field) for current_id, node in before_nodes.items()
                   for field in changed_fields(node, after_nodes[current_id])]
        # Ending projection shows the ending record title, which can differ from the visible chapter.
        if all_nodes[node_id].get("type") == "ending" and (node_id, "/title") not in changes:
            ending_node = next(node for node in repaired["nodes"] if node["id"] == node_id)
            if ending_node.get("chapter") != all_nodes[node_id].get("chapter"):
                changes.append((node_id, "/title"))
        # relationship_goal may project transition_goal read-only; mirrored changes caused by editing the source field are not additional revisions.
        changes = [(current_id, "/transition_goal" if field == "/relationship_goal"
                    and before_nodes[current_id]["relationship_goal"] == before_nodes[current_id]["transition_goal"]
                    and after_nodes[current_id]["relationship_goal"] == after_nodes[current_id]["transition_goal"]
                    else field) for current_id, field in changes]
        # Even another suggestion naming a broader parent field cannot override explicitly reviewed preservation requirements.
        if any(current_id == row["node_id"] and fields_overlap(field, row["field"])
               for current_id, field in changes for row in protected):
            raise QualityAssessmentError("quality_repair_outside_plan")
        if not changes:
            raise QualityAssessmentError("quality_repair_no_change")
        if any(current_id != node_id or not any(field == allowed or field.startswith(allowed + "/")
               for allowed in allowed_fields) for current_id, field in changes):
            raise QualityAssessmentError("quality_repair_outside_plan")
        if not all(any(field == required or field.startswith(required + "/") for _, field in changes)
                   for required in allowed_fields):
            raise QualityAssessmentError("invalid_quality_repair")
        return repaired

    @staticmethod
    def _assessment_context(
        story: Mapping[str, Any],
        setup: Mapping[str, Any],
        authoring: Mapping[str, Any],
    ) -> dict[str, Any]:
        all_nodes = {
            str(node.get("id") or ""): node
            for node in story.get("nodes") or []
            if isinstance(node, Mapping) and node.get("id")
        }
        mainline_ids = [
            str(node_id)
            for node_id in authoring.get("mainline_node_ids") or []
            if str(node_id) in all_nodes and all_nodes[str(node_id)].get("type") != "ending"
        ]
        if not mainline_ids:
            raise QualityAssessmentError("mainline_order_required")
        mainline_set = set(mainline_ids)
        ending_nodes = {
            node_id: node for node_id, node in all_nodes.items() if node.get("type") == "ending"
        }
        branch_node_ids = {
            node_id for node_id, node in all_nodes.items()
            if node_id not in mainline_set and node.get("type") != "ending"
        }
        relationship_arc = authoring.get("relationship_arc") or {}
        relationship_stages = {
            str(stage.get("node_id") or ""): stage
            for stage in relationship_arc.get("stages") or []
            if isinstance(stage, Mapping) and stage.get("node_id")
        }
        def compact_node(node_id: str) -> dict[str, Any]:
            node = all_nodes[node_id]
            beat = node.get("story_beat") or {}
            stage = relationship_stages.get(node_id) or {}
            # Only the formal package state reaches the performer, including old packages without it.
            state_stage = beat.get("character_state") or {}
            return {
                "id": node_id,
                # Derive the catalog from actual patch permissions; scoring models cannot decide which fields permit direct modification.
                "text_repair_fields": text_repair_fields(node),
                "title": str(node.get("chapter") or "").strip(),
                "summary": str(beat.get("summary") or "").strip(),
                # Runtime prefers this optional full direction to summary. Keep its raw value citable,
                # including empty fallbacks, without granting revision rights or merging ending summaries.
                **({"narrative_summary": deepcopy(beat["narrative_summary"])}
                   if "narrative_summary" in beat else {}),
                "opening_scene": str(beat.get("opening_scene") or "").strip(),
                **({"entry_player_inputs": deepcopy(beat["entry_player_inputs"])}
                   if "entry_player_inputs" in beat else {}),
                "narrative_focus": str(beat.get("narrative_focus") or "").strip(),
                "must_not_happen": deepcopy(beat.get("must_not_happen") or []),
                "acting_contract": deepcopy(beat.get("acting_contract") or {}),
                "completion_contract": deepcopy(node.get("completion_contract") or {}),
                **({"fixed_narrations": deepcopy(beat["fixed_narrations"])}
                   if "fixed_narrations" in beat else {}),
                "goals": [
                    {
                        # Preserve actual target IDs and delivery evidence so scoring locates mismatches without guessing from target descriptions.
                        "id": str(item.get("id") or ""),
                        "owner": str(item.get("owner") or ""),
                        "description": str(item.get("description") or "").strip(),
                        "delivery_type": str((item.get("delivery") or {}).get("type") or ""),
                        # Output channels also evidence action actors; do not guess solely from natural language or types.
                        "output_field": str((item.get("delivery") or {}).get("output_field") or ""),
                        "timing": str((item.get("delivery") or {}).get("timing") or ""),
                        "source_ids": deepcopy((item.get("delivery") or {}).get("source_ids") or []),
                        "evidence": deepcopy(item.get("evidence") or {}),
                        "state_effects": deepcopy((item.get("delivery") or {}).get("state_effects") or {}),
                    }
                    for item in beat.get("goals") or []
                    if isinstance(item, Mapping)
                ],
                "relationship_state": str(beat.get("catgirl_situation") or "").strip(),
                "relationship_goal": str(
                    stage.get("progress_opportunity") or beat.get("transition_goal") or ""
                ).strip(),
                "transition_goal": str(beat.get("transition_goal") or "").strip(),
                "outgoing_routes": [
                    {
                        "id": str(route.get("id") or ""),
                        "target_node_id": str(route.get("target_node_id") or ""),
                        "conditions": deepcopy(route.get("conditions") or {}),
                        "transition_contract": {
                            **{
                                field: deepcopy((route.get("transition_contract") or {}).get(field))
                                for field in (*_TRANSITION_CONTRACT_FIELDS, "fallback_offer", "accept_input", "silent_choice")
                            },
                            **({"accept_input_confirmation_only": deepcopy(route["transition_contract"]["accept_input_confirmation_only"])}
                               if "accept_input_confirmation_only" in (route.get("transition_contract") or {}) else {}),
                            **({"fixed_entry_only": True}
                               if (route.get("transition_contract") or {}).get("fixed_entry_only") is True else {}),
                            **({"advance_on_completion": True}
                               if (route.get("transition_contract") or {}).get("advance_on_completion") is True else {}),
                            # Bridge checks require source/target information, but read-only input cannot expand text-revision allowlists.
                            "source_ids": deepcopy((route.get("transition_contract") or {}).get("source_ids") or []),
                        },
                    }
                    for route in node.get("route_gates") or []
                    if isinstance(route, Mapping)
                ],
                "character_state": {
                    **({"temporal_context": deepcopy(state_stage["temporal_context"])}
                       if "temporal_context" in state_stage else {}),
                    "catgirl": str(state_stage.get("catgirl_state") or "").strip(),
                    "player": str(state_stage.get("player_state") or "").strip(),
                    "environment": str(state_stage.get("environment_state") or "").strip(),
                    "continuity": deepcopy(state_stage.get("continuity_from_previous") or []),
                    "boundaries": deepcopy(state_stage.get("scene_boundaries") or []),
                },
            }

        route_semantics = authoring.get("route_semantics") or {}

        def compact_condition(route: Mapping[str, Any]) -> dict[str, Any]:
            conditions = route.get("conditions") or {}
            mode = "any" if isinstance(conditions, Mapping) and "any" in conditions else "all"
            rows = conditions.get(mode) or [] if isinstance(conditions, Mapping) else []
            semantic = route_semantics.get(str(route.get("id") or "")) or {}
            return {
                "label": str(semantic.get("label") or "").strip(),
                "detail": str(semantic.get("detail") or "").strip(),
                "mode": mode,
                "rules": [
                    {"type": "fact_equals", "key": row.get("key"), "value": row.get("value")}
                    if row.get("type") == "fact_equals" else
                    {
                        "metric": str(row.get("metric") or ""),
                        "op": str(row.get("op") or ""),
                        "value": row.get("value"),
                    }
                    for row in rows
                    if isinstance(row, Mapping)
                ],
            }

        branches: list[dict[str, Any]] = []
        claimed_branch_nodes: set[str] = set()
        for source_id in mainline_ids:
            for route in all_nodes[source_id].get("route_gates") or []:
                if not isinstance(route, Mapping):
                    continue
                first_id = str(route.get("target_node_id") or "")
                if first_id not in branch_node_ids:
                    continue
                stack = [first_id]
                branch_ids: list[str] = []
                destinations: set[str] = set()
                seen: set[str] = set()
                while stack:
                    node_id = stack.pop()
                    if node_id in seen:
                        continue
                    seen.add(node_id)
                    if node_id not in branch_node_ids:
                        if node_id in all_nodes:
                            destinations.add(node_id)
                        continue
                    branch_ids.append(node_id)
                    claimed_branch_nodes.add(node_id)
                    outgoing = [
                        str(item.get("target_node_id") or "")
                        for item in all_nodes[node_id].get("route_gates") or []
                        if isinstance(item, Mapping) and item.get("target_node_id")
                    ]
                    stack.extend(reversed(outgoing))
                branches.append({
                    "id": str(route.get("id") or f"branch_from_{source_id}"),
                    "source_node_id": source_id,
                    "entry_condition": compact_condition(route),
                    "nodes": [compact_node(node_id) for node_id in branch_ids],
                    "destination_ids": sorted(destinations),
                })

        for node_id in sorted(branch_node_ids - claimed_branch_nodes):
            branches.append({
                "id": f"unlinked_{node_id}",
                "source_node_id": None,
                "entry_condition": None,
                "nodes": [compact_node(node_id)],
                "destination_ids": [],
            })

        ending_records = {
            str(ending.get("id") or ""): ending
            for ending in story.get("endings") or []
            if isinstance(ending, Mapping) and ending.get("id")
        }
        incoming: dict[str, list[str]] = {node_id: [] for node_id in ending_nodes}
        for source_id, node in all_nodes.items():
            for route in node.get("route_gates") or []:
                target_id = str(route.get("target_node_id") or "") if isinstance(route, Mapping) else ""
                if target_id in incoming:
                    incoming[target_id].append(source_id)

        metrics = []
        for metric_id, definition in (story.get("metric_schema") or {}).items():
            if not isinstance(definition, Mapping):
                continue
            metrics.append({
                "id": str(metric_id),
                "name": str(definition.get("name") or metric_id),
                "purpose": str(definition.get("description") or ""),
                "relationship_effect": definition.get("relationship_effect", "none"),
                "bands": [
                    {
                        "label": str(band.get("label") or ""),
                        "min": band.get("min"),
                        "max": band.get("max"),
                    }
                    for band in definition.get("bands") or []
                    if isinstance(band, Mapping)
                ],
            })

        intro = story.get("intro") or {}
        binding = story.get("catgirl_binding") or {}
        return {
            "start_node_id": story.get("start_node_id"),
            # Preserve author intent for shared scoring/single-node optimization without triggering default scoring or automatic rewriting.
            "author_intent": str(setup.get("brief") or "").strip(),
            # Formal background supports checking/citations only and never enters the node-text revision catalog.
            "background": str(intro.get("background") or ""),
            "characters": {
                **{field: intro[field] for field in ("player_name", "catgirl_name") if field in intro},
                "player_identity": str(intro.get("player_identity") or "").strip(),
                "catgirl_identity": str(intro.get("catgirl_identity") or "").strip(),
                "relationship": {
                    "opening": str(
                        relationship_arc.get("opening_relationship") or setup.get("relationship") or ""
                    ).strip(),
                    "direction": str(relationship_arc.get("long_term_direction") or "").strip(),
                    "current_dynamic": str(binding.get("role_overlay") or "").strip(),
                },
            },
            "metrics": metrics,
            "fact_contract": deepcopy(story.get("fact_contract") or {}),
            "key_props": deepcopy(authoring.get("key_props") or []),
            "mainline": [compact_node(node_id) for node_id in mainline_ids],
            "branches": branches,
            "endings": [
                {
                    # Endings use the same projection as ordinary acts, preserving states still awaiting input and prohibitions.
                    # Retain body summaries separately to detect contradictions with ending-record summaries.
                    **compact_node(node_id),
                    "scene_summary": str((node.get("story_beat") or {}).get("summary") or "").strip(),
                    "title": str(
                        ending_records.get(str(node.get("ending_id") or ""), {}).get("title")
                        or node.get("chapter")
                        or ""
                    ).strip(),
                    "summary": str(
                        ending_records.get(str(node.get("ending_id") or ""), {}).get("summary")
                        or (node.get("story_beat") or {}).get("summary")
                        or ""
                    ).strip(),
                    "opening_scene": str(
                        (node.get("story_beat") or {}).get("opening_scene") or ""
                    ).strip(),
                    "entry_from": sorted(incoming.get(node_id) or []),
                }
                for node_id, node in sorted(ending_nodes.items())
            ],
        }

    @staticmethod
    def _context_node_ids(context: Mapping[str, Any]) -> set[str]:
        return {
            str(node.get("id") or "")
            for node in [
                *(context.get("mainline") or []),
                *(context.get("endings") or []),
                *[
                    node
                    for branch in context.get("branches") or []
                    if isinstance(branch, Mapping)
                    for node in branch.get("nodes") or []
                ],
            ]
            if isinstance(node, Mapping) and node.get("id")
        }

    @staticmethod
    def _parse_response(response: Any, invalid_code: str) -> dict[str, Any]:
        if isinstance(response, LLMCallFailure):
            raise QualityAssessmentError(response.error_code, provider_details=response.diagnostic())
        if not isinstance(response, str):
            raise QualityAssessmentError(invalid_code)
        # Accept one complete Markdown wrapper, never extract a JSON fragment
        # from commentary or reconstruct truncated/ambiguous string values.
        text = response.strip()
        header, separator, body = text.partition("\n")
        if header in {"```json", "```"} and separator and body.endswith("\n```"):
            text = body[:-4]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise QualityAssessmentError(invalid_code) from error
        if not isinstance(payload, dict):
            raise QualityAssessmentError(invalid_code)
        return payload

    @staticmethod
    def _validated_assessment(context: Mapping[str, Any], payload: Mapping[str, Any], *,
                              fact_issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        scores = payload.get("scores")
        if not isinstance(scores, Mapping) or set(scores) != set(QUALITY_DIMENSIONS):
            raise QualityAssessmentError("invalid_quality_assessment")
        normalized_scores: dict[str, dict[str, Any]] = {}
        fact_ids = {issue["issue_id"] for issue in fact_issues or []}
        for dimension in QUALITY_DIMENSIONS:
            row = scores.get(dimension)
            if not isinstance(row, Mapping) or not _is_score(row.get("score")):
                raise QualityAssessmentError("invalid_quality_assessment")
            summary = str(row.get("summary") or "").strip()
            if not summary:
                raise QualityAssessmentError("invalid_quality_assessment")
            normalized_scores[dimension] = {"score": float(row["score"]), "summary": summary}
            # Accept only IDs from this run's completed fact stage; old reports without this field retain existing literary-evidence validation.
            if "fact_issue_ids" in row:
                refs = row["fact_issue_ids"]
                if (not isinstance(refs, list)
                        or any(not isinstance(ref, str) or ref not in fact_ids for ref in refs)
                        or len(refs) != len(set(refs))):
                    raise QualityAssessmentError("invalid_quality_assessment")
                normalized_scores[dimension]["fact_issue_ids"] = list(refs)
        failed_dimensions = [
            key for key, row in normalized_scores.items() if row["score"] < QUALITY_PASS_SCORE
        ]

        strengths = payload.get("strengths")
        raw_metric_advice = payload.get("metric_advice")
        raw_relationship_advice = payload.get("relationship_advice")
        raw_issues = payload.get("issues")
        if (
            not isinstance(strengths, list)
            or any(not isinstance(item, str) or not item.strip() for item in strengths)
            or not isinstance(raw_metric_advice, Mapping)
            or not isinstance(raw_relationship_advice, list)
            or not isinstance(raw_issues, list)
        ):
            raise QualityAssessmentError("invalid_quality_assessment")
        recommended_count = raw_metric_advice.get("recommended_count")
        metric_summary = str(raw_metric_advice.get("summary") or "").strip()
        if (
            not isinstance(recommended_count, int)
            or isinstance(recommended_count, bool)
            or not 0 <= recommended_count <= 4
            or not metric_summary
        ):
            raise QualityAssessmentError("invalid_quality_assessment")
        metric_lists: dict[str, list[str]] = {}
        for key in ("add", "remove", "merge"):
            values = raw_metric_advice.get(key)
            if (
                not isinstance(values, list)
                or any(not isinstance(item, str) or not item.strip() for item in values)
            ):
                raise QualityAssessmentError("invalid_quality_assessment")
            metric_lists[key] = [item.strip() for item in values]
        metric_advice = {
            "current_count": len(context.get("metrics") or []),
            "recommended_count": recommended_count,
            "summary": metric_summary,
            **metric_lists,
        }

        node_ids = NumericV2QualityAssessor._context_node_ids(context)
        # Real scoring includes route IDs in target lists. Convert only uniquely owned IDs in the current graph to source nodes,
        # without guessing natural-language references or changing route destinations; reject whole reports with unknown/multiply-owned IDs.
        route_owners: dict[str, set[str]] = {}
        context_nodes = [
            *(context.get("mainline") or []),
            *(node for branch in context.get("branches") or [] for node in branch.get("nodes") or []),
            *(context.get("endings") or []),
        ]
        for node in context_nodes:
            for route in node.get("outgoing_routes") or []:
                route_id = str(route.get("id") or "")
                if route_id:
                    route_owners.setdefault(route_id, set()).add(node["id"])

        def resolve_targets(targets: Any) -> list[str]:
            if not isinstance(targets, list) or not targets:
                raise QualityAssessmentError("invalid_quality_assessment")
            resolved = []
            for target in targets:
                if not isinstance(target, str):
                    raise QualityAssessmentError("invalid_quality_assessment")
                owners = route_owners.get(target) or set()
                if target in node_ids:
                    resolved.append(target)
                elif len(owners) == 1:
                    resolved.append(next(iter(owners)))
                else:
                    raise QualityAssessmentError("invalid_quality_assessment")
            return list(dict.fromkeys(resolved))

        relationship_advice: list[dict[str, Any]] = []
        for advice_index, raw in enumerate(raw_relationship_advice, start=1):
            if not isinstance(raw, Mapping):
                raise QualityAssessmentError("invalid_quality_assessment")
            targets = resolve_targets(raw.get("target_node_ids"))
            repair_scope = str(raw.get("repair_scope") or "")
            fields = {
                key: str(raw.get(key) or "").strip()
                for key in ("problem", "suggestion", "expected_result")
            }
            if (
                not isinstance(targets, list)
                or not targets
                or any(not isinstance(node_id, str) or node_id not in node_ids for node_id in targets)
                or not all(fields.values())
                or repair_scope not in {"text", "structure"}
            ):
                raise QualityAssessmentError("invalid_quality_assessment")
            relationship_advice.append(classify_repair(context, {
                "issue_id": f"relationship_advice_{advice_index:02d}",
                "repair_targets": deepcopy(raw.get("repair_targets")),
                "target_node_ids": list(dict.fromkeys(targets)),
                **fields,
                "repair_scope": repair_scope,
                "repairable": repair_scope == "text",
            }))

        issues: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_issues, start=1):
            if not isinstance(raw, Mapping):
                raise QualityAssessmentError("invalid_quality_assessment")
            issue = _literary_issue(context, raw, resolve_targets(raw.get("target_node_ids")))
            issues.append({**issue, "issue_id": f"quality_issue_{index:02d}"})
        issue_dimensions = {issue["dimension"] for issue in issues}
        # Model-local numbering serves explicit references only in this run; map to existing card IDs without inferring links from text similarity.
        # Collect all issues first to support references to later dimensions/relationship sections; legacy unnumbered/unreferenced output remains validatable.
        issue_ids: dict[str, str] = {}
        for raw, issue in zip([*raw_issues, *raw_relationship_advice], [*issues, *relationship_advice]):
            if "issue_id" not in raw:
                continue
            ref = raw["issue_id"]
            if not isinstance(ref, str) or not re.fullmatch(r"[LR][0-9]+", ref) or ref in issue_ids:
                raise QualityAssessmentError("invalid_quality_assessment")
            issue_ids[ref] = issue["issue_id"]
        for dimension, row in scores.items():
            if "related_issue_ids" not in row:
                continue
            refs = row["related_issue_ids"]
            if (not isinstance(refs, list)
                    or any(not isinstance(ref, str) or ref not in issue_ids for ref in refs)
                    or len(refs) != len(set(refs))):
                raise QualityAssessmentError("invalid_quality_assessment")
            normalized_scores[dimension]["related_issue_ids"] = [issue_ids[ref] for ref in refs]
        # Associations justify low scores only, without duplicating issues, changing severity, or expanding node-revision permissions.
        fact_dimensions = {key for key, row in normalized_scores.items() if row.get("fact_issue_ids")}
        linked_dimensions = {key for key, row in normalized_scores.items() if row.get("related_issue_ids")}
        if not set(failed_dimensions).issubset(issue_dimensions | fact_dimensions | linked_dimensions):
            raise QualityAssessmentError("invalid_quality_assessment")
        overall = round(sum(
            normalized_scores[key]["score"] * QUALITY_DIMENSIONS[key]["weight"]
            for key in QUALITY_DIMENSIONS
        ), 2)
        return {
            "scope": "full_story_simple",
            # This only normalizes literary output; scoring orchestration upgrades to executable version 3 only after plan review.
            "repair_plan_version": 2,
            "content_sha256": _content_hash(context),
            "overall_score": overall,
            "scores": normalized_scores,
            "strengths": [item.strip() for item in strengths],
            "metric_advice": metric_advice,
            "relationship_advice": relationship_advice,
            "issues": issues,
            "failed_dimensions": failed_dimensions,
            # Passing every score cannot offset serious issues; minor-only results can still pass while showing suggestions.
            "passed": not failed_dimensions and not any(
                issue["severity"] in {"major", "blocking"} for issue in issues
            ),
            "stale": False,
        }

    @staticmethod
    def _apply_node_updates(
        story: Mapping[str, Any],
        payload: Mapping[str, Any],
        target_ids: list[str],
    ) -> dict[str, Any]:
        updates = payload.get("node_updates")
        if not isinstance(updates, list) or len(updates) != len(target_ids):
            raise QualityAssessmentError("invalid_quality_repair")
        updates_by_id: dict[str, Mapping[str, Any]] = {}
        for update in updates:
            if not isinstance(update, Mapping):
                raise QualityAssessmentError("invalid_quality_repair")
            node_id = str(update.get("node_id") or "")
            if node_id not in target_ids or node_id in updates_by_id:
                raise QualityAssessmentError("invalid_quality_repair")
            if set(update).difference({"node_id", "chapter", "story_beat", "routes", "ending"}) or len(update) == 1:
                raise QualityAssessmentError("invalid_quality_repair")
            updates_by_id[node_id] = update
        if set(updates_by_id) != set(target_ids):
            raise QualityAssessmentError("invalid_quality_repair")

        repaired = deepcopy(dict(story))
        nodes = {str(node.get("id") or ""): node for node in repaired.get("nodes") or []}
        endings = {str(ending.get("id") or ""): ending for ending in repaired.get("endings") or []}
        for node_id, update in updates_by_id.items():
            node = nodes.get(node_id)
            if not isinstance(node, dict):
                raise QualityAssessmentError("quality_issue_stale")
            if "chapter" in update:
                chapter = update["chapter"]
                if not isinstance(chapter, str) or not chapter.strip():
                    raise QualityAssessmentError("invalid_quality_repair")
                if node.get("type") == "ending" and chapter.strip() != str(node.get("chapter") or "").strip() and (
                    not isinstance(update.get("ending"), Mapping)
                    or str(update["ending"].get("title") or "").strip() != chapter.strip()
                ):
                    raise QualityAssessmentError("quality_repair_outside_plan")
                node["chapter"] = chapter.strip()
            if "story_beat" in update:
                beat_update = update["story_beat"]
                if not isinstance(beat_update, Mapping) or not beat_update or set(beat_update).difference(_STORY_BEAT_FIELDS):
                    raise QualityAssessmentError("invalid_quality_repair")
                beat = node.setdefault("story_beat", {})
                for field, value in beat_update.items():
                    if field == "must_not_happen":
                        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                            raise QualityAssessmentError("invalid_quality_repair")
                        beat[field] = [item.strip() for item in value]
                    elif field == "character_state":
                        # Merge text subfields into existing state, preserving unmodified actors and all permission/boundary metadata.
                        # Reject candidates when original structure is missing instead of inventing another character state from narration.
                        state = beat.get(field)
                        if (not isinstance(value, Mapping) or not value
                                or set(value).difference(_CHARACTER_STATE_TEXT_FIELDS)
                                or not isinstance(state, dict)
                                or any(not isinstance(state.get(key), str)
                                       or not isinstance(text, str) or not text.strip()
                                       for key, text in value.items())):
                            raise QualityAssessmentError("invalid_quality_repair")
                        state.update({key: text.strip() for key, text in value.items()})
                    else:
                        if not isinstance(value, str) or not value.strip():
                            raise QualityAssessmentError("invalid_quality_repair")
                        beat[field] = value.strip()
            if "routes" in update:
                route_updates = update["routes"]
                if not isinstance(route_updates, list) or not route_updates:
                    raise QualityAssessmentError("invalid_quality_repair")
                routes = {
                    str(route.get("id") or ""): route
                    for route in node.get("route_gates") or []
                    if isinstance(route, dict) and route.get("id")
                }
                updated_route_ids: set[str] = set()
                for route_update in route_updates:
                    if (
                        not isinstance(route_update, Mapping)
                        or set(route_update) != {"route_id", "transition_contract"}
                    ):
                        raise QualityAssessmentError("invalid_quality_repair")
                    route_id = str(route_update.get("route_id") or "")
                    if route_id not in routes or route_id in updated_route_ids:
                        raise QualityAssessmentError("invalid_quality_repair")
                    contract_update = route_update.get("transition_contract")
                    if (
                        not isinstance(contract_update, Mapping)
                        or not contract_update
                        or set(contract_update).difference(_TRANSITION_CONTRACT_FIELDS | {"remove_bridge_scene_narration"})
                    ):
                        raise QualityAssessmentError("invalid_quality_repair")
                    contract = routes[route_id].get("transition_contract")
                    if not isinstance(contract, dict):
                        raise QualityAssessmentError("quality_issue_stale")
                    if "remove_bridge_scene_narration" in contract_update:
                        target = nodes.get(routes[route_id].get("target_node_id")) or {}
                        opening = (target.get("story_beat") or {}).get("opening_scene")
                        if (contract_update["remove_bridge_scene_narration"] is not True
                                or "bridge_scene_narration" in contract_update
                                or contract_update.get("must_deliver", []) != []
                                or not can_remove_bridge_narration(contract, opening)):
                            raise QualityAssessmentError("invalid_quality_repair")
                        del contract["bridge_scene_narration"]
                    for field, value in contract_update.items():
                        if field == "remove_bridge_scene_narration":
                            continue
                        if field in {"must_deliver", "must_preserve"}:
                            if (
                                not isinstance(value, list)
                                or any(
                                    not isinstance(item, str) or not item.strip()
                                    for item in value
                                )
                            ):
                                raise QualityAssessmentError("invalid_quality_repair")
                            contract[field] = [item.strip() for item in value]
                        else:
                            if not isinstance(value, str) or not value.strip():
                                raise QualityAssessmentError("invalid_quality_repair")
                            contract[field] = value.strip()
                    updated_route_ids.add(route_id)
            if "ending" in update:
                if node.get("type") != "ending":
                    raise QualityAssessmentError("invalid_quality_repair")
                ending_update = update["ending"]
                if (
                    not isinstance(ending_update, Mapping)
                    or not ending_update
                    or set(ending_update).difference({"title", "summary"})
                ):
                    raise QualityAssessmentError("invalid_quality_repair")
                ending = endings.get(str(node.get("ending_id") or ""))
                if not isinstance(ending, dict):
                    raise QualityAssessmentError("quality_issue_stale")
                previous_title = ending.get("title")
                for field, value in ending_update.items():
                    if not isinstance(value, str) or not value.strip():
                        raise QualityAssessmentError("invalid_quality_repair")
                    ending[field] = value.strip()
                if ending.get("title") != previous_title:
                    node["chapter"] = ending["title"]
        return repaired


__all__ = [
    "QUALITY_DIMENSIONS",
    "QUALITY_PASS_SCORE",
    "NumericV2QualityAssessor",
    "QualityAssessmentError",
]
