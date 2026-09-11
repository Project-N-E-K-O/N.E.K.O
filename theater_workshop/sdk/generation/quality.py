"""User-triggered six-dimension story assessment and single-node optimization."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping

from ..model import ModelAgent, LLMCallFailure

from .numeric_v2 import _SCENE_PROCESS_AUTHORING_RULE
from .runtime_rules import SCORING_RUNTIME_RULES
from .facts import FACT_REVIEW_PROMPT, OPENING_STATE_COMPARISON_RULE, build_fact_sources, validate_fact_review
from .evidence import (EVIDENCE_REVIEW_PROMPT, review_targets, validate_evidence_review,
                       effective_targets, protected_targets, fields_overlap)
from .repair import (
    STORY_BEAT_FIELDS as _STORY_BEAT_FIELDS,
    CHARACTER_STATE_TEXT_FIELDS as _CHARACTER_STATE_TEXT_FIELDS,
    TRANSITION_CONTRACT_FIELDS as _TRANSITION_CONTRACT_FIELDS,
    REPAIR_PLAN_RULE, text_repair_fields, context_nodes, classify_repair, assign_shared_plans, changed_fields,
)
from .plan_review import PLAN_REVIEW_PROMPT, validate_plan_review


QUALITY_DIMENSIONS = {
    "characterization": {"label": "人物塑造", "weight": 0.20},
    "plot": {"label": "情节构思", "weight": 0.25},
    "theme": {"label": "主题思想", "weight": 0.15},
    "prose_style": {"label": "文笔风格", "weight": 0.10},
    "pacing": {"label": "叙事节奏", "weight": 0.15},
    "emotional_resonance": {"label": "情感共鸣", "weight": 0.15},
}
QUALITY_PASS_SCORE = 75.0

# 文学阶段保留作者边界、只关联已核对事实；写稿与节点修订仍使用完整的共享生成合同。
_ASSESSMENT_PROMPT = SCORING_RUNTIME_RULES + "\n" + """# Role: N.E.K.O 互动剧情质量评估编辑

事实一致性已由上一阶段独立核对，fact_review 是完整事实报告，story_outline 是同一份作者稿。本轮只评文学质量：人物塑造、情节构思、主题、文笔、节奏和共鸣，不修改故事。
fact_review.issues仅含证据复核支持的问题；excluded_issue_ids是已排除的问题编号，不能引用或据此扣分。protected_repair_targets所列字段保持原文，不能以文学或人物关系建议要求重写。
不得重新核对或推翻 fact_review，不把它的问题重复写入本轮 issues，也不重新猜测时点、行动主体、证据来源或道具必要条件。事实缺陷对文学效果的影响可在对应维度的summary中说明，并在fact_issue_ids引用事实报告已有issue_id。服务端会原样合并事实问题，高文学分不能覆盖事实问题。
路线可辨识性属于玩家体验：不能只靠隐藏数值暗中分流，但不因单线或接受后的正常移动就批评玩家没有自主权。证据可演性可以评价具体材料是否足以承载情感表达，但不把“建议补细节”写成已确认事实矛盾。角色反馈允许保持分歧或克制，不强求直接夸奖、关系升级或额外确认。

六个维度：
1. characterization 人物塑造：角色立体度、行为逻辑一致性、成长轨迹。
2. plot 情节构思：结构、因果、悬念、伏笔、转折和结局。
3. theme 主题思想：作品探讨的问题及人物选择、结局形成的表达。
4. prose_style 文笔风格：大纲语言、描写计划和人物声音约束。运行时最终对白尚未生成，不得声称已经检查最终对白。
5. pacing 叙事节奏：章节推进、铺垫、冲突递进和高潮安排。实际回合节奏仍受玩家影响。
6. emotional_resonance 情感共鸣：情感铺垫和关键选择的共鸣潜力，不得假装已经观察到真实玩家体验。

每项使用 0—100 分。每个scores维度内同时填写score、summary、fact_issue_ids、related_issue_ids和issues。低于75分必须有该维度的文学问题、明确关联已列文学/关系问题，或引用确实导致扣分的已有事实问题；summary说明所引用的问题如何影响本维度。不为满足低分依据而重复事实问题，也不抬高分数。所有新发现的文学问题（包括高分维度的minor、major、blocking）都放入对应维度issues，每条点名真实target_node_ids并提供modification_plan。
沿用作者规定的关系距离、表达边界与收束方式，不强加赞美、升温、额外确认或抽象难度。
事实缺陷及其同一修法只在事实报告中保留一次：本轮使用fact_issue_ids关联，不重新生成一条问题或人物关系建议。仅当修复已有事实后仍独立存在的文学缺陷，才新增issues。服务端保留事实严重度，不因分数变化而删除问题。
target_node_ids 填节点 ID；路线的问题在 problem 中点名路线 ID，并把目标节点填写为该路线所属的来源节点，不能填写路线 ID 或其目标节点来代替来源节点。

另外必须给出：
- metric_advice：只评价当前“数值指标的数量与职责”是否合理，可建议新增、删除或合并指标；不要直接调整具体阈值。若指标实际描述双方信任、好感、亲密、坦诚或疏离，却把 relationship_effect 标成 none，必须指出它无法约束运行时关系距离。
- relationship_advice：只列尚未被事实或六维问题覆盖的独立关系缺陷，并关联具体节点；没有独立不足时返回[]，不为填写此栏要求关系升温或重复角色表态。

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
    # 文学阶段不再重复整份生成合同里的事实复核任务，避免再次检查主体、状态和转场。
    # 用户允许低分关联已有事实；逐维填写避免跨列表漏项，服务端只展开、不补造建议。
    "本轮只返回文学报告。每个scores维度先写issues和fact_issue_ids，再写summary和score。"
    "低分依据可以是本维度新文学问题、related_issue_ids引用的已有文学/关系问题或已有事实ID；关联理由写入summary，不重复修改方案。"
    "无独立文学问题可用issues=[]；高分发现的问题也全部保留。数值建议只放metric_advice。"
    "若已有事实的修改方案执行后该问题就消失，只引用该事实ID，不再以人物、文笔、节奏或关系名义重写同一方案。"
    # 用户允许跨维度/关系建议明确关联；无关联的空问题低分仍拒绝，不自动挪动或补造问题。
    "关系建议或其它维度问题作为低分依据时，必须填写related_issue_ids，不能只在summary提到后省略引用。"
    "输出前检查每个低于75的维度：issues、fact_issue_ids和related_issue_ids不能同时为空。"
    # 把可执行字段写进示例，并要求原文依据与具体修法；保留模型给出的所有意见，不事后过滤。
    "每条文学问题在problem点明节点、字段和短原文；modification_plan写具体替换方向，不能只要求丰富层次或增强氛围。"
    "实际开场、转场或反应指引重复才可作为文笔依据，后台状态记录简短或格式重复不等于演出文笔差。"
    "修法逐项核对text_repair_fields，repair_targets列全实际需要改的字段；涉及goals、narrative_focus或acting_contract仍是structure，不借改summary绕开。"
    "跨节点润色不等于结构调整；不能只因涉及多个节点就标structure或省略修改字段。"
    "文学润色保留动作事实、时点及物件位置，不删除或后移动作追求简洁，也不按个人习惯要求改掉合理行动。"
    "若本维度唯一缺陷是事实报告已有问题，低于75也允许issues=[]，由fact_issue_ids及summary说明依据；不得把同一根因或修法再写成新问题。"
)

# 结构示例之后重申共享交付规则，避免长篇字段说明淹没反应因果与结局边界。
# 修订也沿用相同运行合同，避免根据正确的评分结论又补造分支或结局待办。
_NODE_OPTIMIZATION_PROMPT = SCORING_RUNTIME_RULES + "\n" + """# Role: N.E.K.O 单节点质量优化编辑

用户已在故事地图中点击并确认优化一个高亮节点。请结合 story_outline、target_node 和 accepted_suggestions，只优化 target_node。若同一节点有多条六维评分问题或人物关系建议，必须在一次修改中统筹解决。

先按路线指向核对相邻节点的目标、状态和开场，再修改当前节点文本。入边桥段属于来源节点，出边桥段属于当前节点；不得通过改写当前开场掩盖其他节点仍存在的抢演。结局摘要同时存在于 story_beat.summary 与 ending.summary，涉及同一收束内容时须同步给出两处替换值；保留原始 author_intent 与未获授权修改的目标、状态、演绎合同。

不得修改其他节点，不得修改 ID、节点类型、路线指向、路线条件、数值、阈值、priority、回合预算或未被用户确认的结构。可以修改目标节点已有出口的 transition_contract 文本；不得新增、删除或改指路线。必须保留 global_strengths_to_preserve 和每条建议中的 preserve 内容。

依据建议里的原文逐项核对目标字段。开场正确而状态写错时，直接修正已有 character_state.catgirl_state/player_state/environment_state，只返回实际需要改的子字段；不要重写正确开场迁就错误状态。同一错误若在 catgirl_situation 中复述，同步修该段处境，保留其中仍有效的认知、关系与事实。状态文字只替换冲突的事实，其余仍相容的描述沿用原文；无需补造新的手势、视线、心理或物件位置来填补删去的错误。不得改 character_state 的其它字段，不得改 goals 或 acting_contract；说明动作已经发生不等于要求再演一次。修改后再对照原稿与建议：错误是否消失、正常字段是否保留、相邻节点是否仍相容，不新增行动、任务或玩家承诺作为修复手段。

只输出 JSON object：
{
  "node_updates": [{
    "node_id": "目标节点 ID",
    "chapter": "可选的新标题",
    "story_beat": {
      "summary": "可选的完整替换值",
      "opening_scene": "可选的完整替换值；只写环境、女主行动和已确定结果，不得替玩家执行自主行动",
      "must_not_happen": ["可选的完整替换数组"],
      "catgirl_situation": "可选的完整替换值",
      "transition_goal": "可选的完整替换值",
      "character_state": {"catgirl_state": "可选，仅修既有女主状态描述", "player_state": "可选，仅修既有男主状态描述", "environment_state": "可选，仅修既有环境状态描述"}
    },
    "routes": [{
      "route_id": "目标节点的已有出口 ID",
      "transition_contract": {
        "reason": "可选的完整替换值",
        "bridge_scene_narration": "可选的完整替换值；不得复制目标幕开场或替玩家执行自主行动",
        "must_deliver": ["可选的完整替换数组；只写转场独有的剧情事实"],
        "must_preserve": ["可选的完整替换数组"],
        "tone": "可选的完整替换值"
      }
    }],
    "ending": {"title": "仅结局节点可选", "summary": "仅结局节点可选"}
  }]
}

目标节点必须恰好返回一次；只返回确实需要变更的允许字段。
事实建议附evidence_review，逐字段requires_change=false及protected_repair_targets中的字段必须保持原文；原方案若要求修改它们，以复核的保留要求为准。仅消除受支持的错误，不必重写所有允许字段。
本次还必须遵守accepted_suggestions中的repair_targets，只改这些字段在target_node中的对应原字段。它们使用story_outline的投影路径：relationship_state对应catgirl_situation，character_state.catgirl/player/environment对应三方_state，普通幕summary对应story_beat.summary，结局summary对应ending.summary而scene_summary对应story_beat.summary。outgoing_routes按输入索引找到route_id，再返回routes补丁。不要同步重写未列入方案的正确字段。related_impacts是共用该修法的其它影响与保留项，须一并照顾。
""" + "\n\n" + _SCENE_PROCESS_AUTHORING_RULE


class QualityAssessmentError(RuntimeError):
    """Assessment or user-confirmed node optimization did not return a safely usable result."""

    def __init__(self, code: str, *, provider_details: Mapping[str, Any] | None = None, phase: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.provider_details = dict(provider_details or {})
        # 评分失败必须标出事实、证据、文学或方案复核阶段，不用旧报告或半份结果冒充本次评分。
        self.phase = phase


def _is_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= float(value) <= 100
    )


def _content_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        # 证据目录只存在于本次评分内，恢复后的报告继续使用既有path/quote协议。
        fact_outline, fact_sources = build_fact_sources(context)
        node_ids = self._context_node_ids(context)
        # 在同一事实请求末尾集中展示同一时点的对照，覆盖主线、支线与结局。
        # 复用原ref/text及状态字段，不改作者原文、引用路径或后续文学/修订输入。
        opening_comparisons = [
            {"node_id": node["id"],
             "opening_performance": node.get("opening_scene"),
             "declared_picture_after_performance": node.get("character_state"),
             "same_moment_context": node.get("relationship_state")}
            for node in [*fact_outline.get("mainline", []),
                         *(node for branch in fact_outline.get("branches", []) for node in branch.get("nodes", [])),
                         *fact_outline.get("endings", [])]
        ]
        # 作者显式评分时顺序执行；事实失败不进入文学阶段，任一步失败都不返回可保存报告。
        try:
            facts_response = self.call_llm(
                [{"role": "system", "content": FACT_REVIEW_PROMPT + "\n\n" + REPAIR_PLAN_RULE + "\n" + OPENING_STATE_COMPARISON_RULE},
                 {"role": "user", "content": json.dumps(
                     {"story_outline": fact_outline, "checked_node_ids": sorted(node_ids),
                      "opening_end_state_comparisons": opening_comparisons},
                     # 事实阶段按build_fact_sources的阅读顺序序列化；字母排序会把结局提前。
                     ensure_ascii=False)}],
                temperature=0.1, max_tokens=6000, max_retries=1,
                response_format={"type": "json_object"}, thinking={"type": "disabled"},
                operation="numeric_v2_fact_review",
            )
            facts_payload = self._parse_response(facts_response, "invalid_fact_review")
            facts = validate_fact_review(context, facts_payload, node_ids, evidence_sources=fact_sources)
            facts["issues"] = [classify_repair(context, issue) for issue in facts["issues"]]
        except QualityAssessmentError as error:
            error.phase = "facts"
            raise
        except ValueError as error:
            raise QualityAssessmentError("invalid_fact_review", phase="facts") from error
        # 仅有问题时复核一次；失败不继续文学，不保存半份报告，不自动重试。
        if facts["issues"]:
            try:
                proposed = [{**issue, "repair_targets": review_targets(issue)} for issue in facts["issues"]]
                response = self.call_llm(
                    [{"role": "system", "content": EVIDENCE_REVIEW_PROMPT},
                     {"role": "user", "content": json.dumps({"story_outline": context, "proposed_issues": proposed}, ensure_ascii=False, sort_keys=True)}],
                    temperature=0.1, max_tokens=6000, max_retries=1,
                    response_format={"type": "json_object"}, thinking={"type": "disabled"},
                    operation="numeric_v2_fact_evidence_review",
                )
                facts["issues"] = validate_evidence_review(facts["issues"], self._parse_response(response, "invalid_fact_evidence_review"))
            except QualityAssessmentError as error:
                error.phase = "evidence"
                raise
            except ValueError as error:
                raise QualityAssessmentError("invalid_fact_evidence_review", phase="evidence") from error
        protected = protected_targets(facts["issues"])
        facts["issues"] = [classify_repair(context, issue, protected=protected) for issue in facts["issues"]]
        supported = [issue for issue in facts["issues"] if issue["evidence_review"]["verdict"] == "supported"]
        # 原误报仍在最终报告展示，但不把被否定的指控重复灌入文学输入，避免诱导再次扣分。
        literary_facts = {**facts, "issues": supported,
                          "excluded_issue_ids": [issue["issue_id"] for issue in facts["issues"] if issue["evidence_review"]["verdict"] != "supported"],
                          "protected_repair_targets": protected}
        try:
            report = self._assess_literature(context, literary_facts)
        except QualityAssessmentError as error:
            error.phase = "literature"
            raise
        # 事实结论由服务端原样保留，文学模型无权删掉、改分抵消或覆盖其证据。
        report["issues"] = facts["issues"] + [classify_repair(context, dict(issue, source="literature"), protected=protected) for issue in report["issues"]]
        report["relationship_advice"] = [classify_repair(context, issue, protected=protected) for issue in report["relationship_advice"]]
        report["fact_check"] = {"status": "complete", "checked_node_ids": facts["checked_node_ids"],
                                "issue_count": len(facts["issues"]), "supported_count": len(supported),
                                "evidence_review_status": "complete" if facts["issues"] else "not_needed"}
        report["passed"] = report["passed"] and not any(
            issue["severity"] in {"major", "blocking"} for issue in supported
        )
        report = self._review_plans(context, report)
        assign_shared_plans(report)
        return report

    def _review_plans(self, context: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
        """Review eligible candidates once as a group; request or format failures must not return a partially savable report."""
        result = deepcopy(dict(report))
        rows = [*result["issues"], *result["relationship_advice"]]
        protected = protected_targets(result["issues"])
        candidates = [row for row in rows if row.get("repairable") is True]
        if candidates:
            # 方案复核只核对修改命令；原生压测曾把问题背景/引文中提及的字段当成修改要求。
            # 原问题与证据仍完整保留在报告及节点修订中，此处只隔离复核输入；保留逐字段事实复核。
            plans = [{key: deepcopy(row[key]) for key in (
                "issue_id", "source", "modification_plan", "suggestion", "preserve",
                "target_node_ids", "repair_targets", "evidence_review") if key in row}
                for row in candidates]
            # 显式给出不同方案对；空列表使单条方案只做内部检查，避免把自相矛盾伪造成跨方案冲突。
            # 比较同一份成稿能否满足要求，而非假想两份补丁并发写入；每对仅列一次。
            pairs = [{"issue_ids": [first["issue_id"], second["issue_id"]],
                      "question": "同一份成稿有没有可能同时满足这两条修改要求？可同时满足就不列为冲突；不要判断并发写入或资源竞争。"}
                     for index, first in enumerate(plans) for second in plans[index + 1:]]
            try:
                response = self.call_llm(
                    [{"role": "system", "content": PLAN_REVIEW_PROMPT},
                     {"role": "user", "content": json.dumps({"plans": plans, "story_outline": context,
                         "global_strengths_to_preserve": result.get("strengths") or [],
                         "protected_repair_targets": protected, "pairs_to_compare": pairs,
                         "comparison_scope": "组合冲突只从pairs_to_compare列出的不同方案对中查找；空列表表示没有需要检查的跨方案冲突。只检查同一份成稿的要求是否互斥。"}, ensure_ascii=False)}],
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
        # 只有完整流程可以发布新版报告；旧评分仍可读，不能凭旧repairable直接执行。
        result.update(repair_plan_version=3, plan_review={
            "status": "complete" if candidates else "not_needed", "reviewed_count": len(candidates)})
        return result

    def _assess_literature(self, context: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
        """Use the same author draft and validated factual report for literary assessment, retaining all literary findings and existing score validation."""
        response = self.call_llm(
            [
                {"role": "system", "content": _ASSESSMENT_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        # 已核对事实先于作者稿呈现，原文和作者节点顺序完整保留，不归一或删减内容。
                        {"fact_review": facts, "story_outline": context},
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=6000,
            max_retries=1,
            response_format={"type": "json_object"},
            thinking={"type": "disabled"},
            operation="numeric_v2_quality_assessment",
        )
        payload = self._parse_response(response, "invalid_quality_assessment")
        # 模型逐维填写，服务端机械展开成既有报告；不猜维度、不补问题、不过滤建议。
        if "issues" in payload or not isinstance(payload.get("scores"), Mapping):
            raise QualityAssessmentError("invalid_quality_assessment")
        flattened = deepcopy(dict(payload))
        flattened["issues"] = []
        for dimension, row in flattened["scores"].items():
            if (not isinstance(row, dict) or not isinstance(row.get("issues"), list)
                    or not isinstance(row.get("fact_issue_ids"), list)):
                raise QualityAssessmentError("invalid_quality_assessment")
            for issue in row.pop("issues"):
                if not isinstance(issue, Mapping) or "dimension" in issue:
                    raise QualityAssessmentError("invalid_quality_assessment")
                flattened["issues"].append({**issue, "dimension": dimension})
        return self._validated_assessment(context, flattened, fact_issues=facts["issues"])

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

        # 保留要求覆盖所有建议来源，重新从原事实复核计算，避免旧高亮或缓存放宽权限。
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
                    # 事实建议携带已校验原文，修订不能仅凭文学化的问题概述补猜。
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

        # 完全相同的修法只发一次；各来源的理由、预期和保留项都带入，不能吞掉不同影响。
        merged = {}
        for suggestion in suggestions:
            key = (suggestion["suggestion"].strip(), tuple(sorted((target["node_id"], target["field"]) for target in suggestion["repair_targets"])))
            if key in merged:
                impact = {key: value for key, value in suggestion.items() if key not in {"suggestion", "repair_targets"}}
                merged[key].setdefault("related_impacts", []).append(impact)
            else:
                merged[key] = suggestion
        suggestions = list(merged.values())
        # 完整方案可能覆盖多幕；本次请求的执行字段必须与单节点校验范围一致。
        # 原报告、证据及跨幕理由仍完整保留，只收窄本次请求的授权字段，不改共用方案判定。
        for suggestion in suggestions:
            suggestion["repair_targets"] = [
                target for target in suggestion["repair_targets"] if target["node_id"] == node_id
            ]

        response = self.call_llm(
            [
                {"role": "system", "content": _NODE_OPTIMIZATION_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "story_outline": context,
                            "target_node": deepcopy(dict(all_nodes[node_id])),
                            "accepted_suggestions": suggestions,
                            "global_strengths_to_preserve": assessment.get("strengths") or [],
                            "protected_repair_targets": protected,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
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
        # 实际变更须落在作者看到的方案范围，不能借文字权限顺带重写正确开场或其他节点。
        before_nodes = context_nodes(context)
        after_nodes = context_nodes(self._assessment_context(repaired, setup, authoring))
        changes = [(current_id, field) for current_id, node in before_nodes.items()
                   for field in changed_fields(node, after_nodes[current_id])]
        # relationship_goal可只读投影transition_goal；修改原字段造成的镜像变化不是额外修订。
        changes = [(current_id, "/transition_goal" if field == "/relationship_goal"
                    and before_nodes[current_id]["relationship_goal"] == before_nodes[current_id]["transition_goal"]
                    and after_nodes[current_id]["relationship_goal"] == after_nodes[current_id]["transition_goal"]
                    else field) for current_id, field in changes]
        # 即使另一条建议列了更宽的父字段，也不能改动复核明确要求保留的内容。
        if any(current_id == row["node_id"] and fields_overlap(field, row["field"])
               for current_id, field in changes for row in protected):
            raise QualityAssessmentError("quality_repair_outside_plan")
        if not changes:
            raise QualityAssessmentError("quality_repair_no_change")
        if any(current_id != node_id or not any(field == allowed or field.startswith(allowed + "/")
               for allowed in allowed_fields) for current_id, field in changes):
            raise QualityAssessmentError("quality_repair_outside_plan")
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
        character_state_arc = authoring.get("character_state_arc") or {}
        character_state_stages = {
            str(stage.get("node_id") or ""): stage
            for stage in character_state_arc.get("stages") or []
            if isinstance(stage, Mapping) and stage.get("node_id")
        }

        def compact_node(node_id: str) -> dict[str, Any]:
            node = all_nodes[node_id]
            beat = node.get("story_beat") or {}
            stage = relationship_stages.get(node_id) or {}
            # 评分核对实际送入演绎器的状态；旧包缺失时才使用作者侧状态线。
            # 不能用可能滞后的作者缓存盖掉已经编辑过的 Story Package。
            state_stage = beat.get("character_state") or character_state_stages.get(node_id) or {}
            return {
                "id": node_id,
                # 目录来自实际补丁权限，评分模型不自行决定哪些字段可以直接修改。
                "text_repair_fields": text_repair_fields(node),
                "title": str(node.get("chapter") or "").strip(),
                "summary": str(beat.get("summary") or "").strip(),
                "opening_scene": str(beat.get("opening_scene") or "").strip(),
                "narrative_focus": str(beat.get("narrative_focus") or "").strip(),
                "must_not_happen": deepcopy(beat.get("must_not_happen") or []),
                "acting_contract": deepcopy(beat.get("acting_contract") or {}),
                "goals": [
                    {
                        # 保留实际目标ID和交付依据，评分才能定位错位而非凭目标描述补猜。
                        "id": str(item.get("id") or ""),
                        "owner": str(item.get("owner") or ""),
                        "description": str(item.get("description") or "").strip(),
                        "delivery_type": str((item.get("delivery") or {}).get("type") or ""),
                        # 输出通道也是行动主体的证据，不能只靠自然语言或类型猜测。
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
                                for field in _TRANSITION_CONTRACT_FIELDS
                            },
                            # 桥段核对需要来源目标，但只读输入不能扩大文字修订白名单。
                            "source_ids": deepcopy((route.get("transition_contract") or {}).get("source_ids") or []),
                        },
                    }
                    for route in node.get("route_gates") or []
                    if isinstance(route, Mapping)
                ],
                "character_state": {
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
            # 保留作者原意供评分和单节点优化共用；不触发默认评分或自动改稿。
            "author_intent": str(setup.get("brief") or "").strip(),
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
            "key_props": deepcopy(authoring.get("key_props") or []),
            "mainline": [compact_node(node_id) for node_id in mainline_ids],
            "branches": branches,
            "endings": [
                {
                    # 终点与普通幕使用相同投影，避免漏掉仍等待输入的状态或禁令。
                    # 单独保留正文摘要，以便发现它与结局记录摘要互相矛盾。
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
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            raise QualityAssessmentError(invalid_code) from None
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
            # 只接受本次已完成事实阶段的ID；旧报告没有此字段时仍走原有文学依据校验。
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
        # 真实评分会把路线 ID 放进目标列表。仅按现有图的唯一归属转为来源节点，
        # 不猜测自然语言指代、不改路线指向；未知或多归属 ID 仍拒绝整份报告。
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
            dimension = str(raw.get("dimension") or "")
            if dimension not in QUALITY_DIMENSIONS:
                raise QualityAssessmentError("invalid_quality_assessment")
            # 所有问题都校验并保留，包括高分维度的轻微建议；分数只参与通过判定。
            severity = str(raw.get("severity") or "")
            targets = resolve_targets(raw.get("target_node_ids"))
            preserve = raw.get("preserve")
            repair_scope = str(raw.get("repair_scope") or "")
            fields = {
                key: str(raw.get(key) or "").strip()
                for key in ("problem", "modification_plan", "expected_result")
            }
            if (
                severity not in {"blocking", "major", "minor"}
                or not isinstance(targets, list)
                or not targets
                or any(not isinstance(node_id, str) or node_id not in node_ids for node_id in targets)
                or not all(fields.values())
                or not isinstance(preserve, list)
                or any(not isinstance(item, str) or not item.strip() for item in preserve)
                or repair_scope not in {"text", "structure"}
            ):
                raise QualityAssessmentError("invalid_quality_assessment")
            issues.append(classify_repair(context, {
                "repair_targets": deepcopy(raw.get("repair_targets")),
                "issue_id": f"quality_issue_{index:02d}",
                "dimension": dimension,
                "severity": severity,
                "target_node_ids": list(dict.fromkeys(targets)),
                **fields,
                "preserve": [item.strip() for item in preserve],
                "repair_scope": repair_scope,
                "repairable": repair_scope == "text",
            }))
        issue_dimensions = {issue["dimension"] for issue in issues}
        # 模型局部编号只用于本次明确引用；映射到既有卡片ID，不按文字相似度推断关联。
        # 先收集全部问题，支持引用后面维度或关系栏；旧的无编号、无引用输出仍可校验。
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
        # 关联只提供低分依据，不复制问题、不改变严重度或扩大节点修订权限。
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
            # 此处只是文学结果规范化；评分编排完成方案复核后才升级到可执行的版本3。
            "repair_plan_version": 2,
            "content_sha256": _content_hash(context),
            "overall_score": overall,
            "scores": normalized_scores,
            "strengths": [item.strip() for item in strengths],
            "metric_advice": metric_advice,
            "relationship_advice": relationship_advice,
            "issues": issues,
            "failed_dimensions": failed_dimensions,
            # 分数全部合格也不能抵消严重问题；仅有 minor 时仍可通过并展示建议。
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
                        # 合并已有状态的文字子字段；保留未修改主体及全部权限/边界元数据。
                        # 原稿缺结构时拒绝候选，不从叙事猜测并创建另一份角色状态。
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
                        or set(contract_update).difference(_TRANSITION_CONTRACT_FIELDS)
                    ):
                        raise QualityAssessmentError("invalid_quality_repair")
                    contract = routes[route_id].get("transition_contract")
                    if not isinstance(contract, dict):
                        raise QualityAssessmentError("quality_issue_stale")
                    for field, value in contract_update.items():
                        if field in {"must_deliver", "must_preserve"}:
                            if (
                                not isinstance(value, list)
                                or (field == "must_deliver" and not value)
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
                for field, value in ending_update.items():
                    if not isinstance(value, str) or not value.strip():
                        raise QualityAssessmentError("invalid_quality_repair")
                    ending[field] = value.strip()
                if "title" in ending_update:
                    node["chapter"] = ending["title"]
        return repaired


__all__ = [
    "QUALITY_DIMENSIONS",
    "QUALITY_PASS_SCORE",
    "NumericV2QualityAssessor",
    "QualityAssessmentError",
]
