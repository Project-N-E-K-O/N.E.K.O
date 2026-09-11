"""Provide independent factual-check prompts and citation validation without rewriting or literary scoring."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .runtime_rules import SCORING_RUNTIME_RULES

# 事实阶段只检查可引用的冲突；正常作者稿允许没有问题，不靠凑建议数量判断完整性。
# 先解释实际框架，再检查原文冲突；不让目标/路线字段被误读为另一套运行状态机。
# 初检按时点比较后只提交已确认冲突；精简重复提醒，同时把必填修订字段写进输出示例。
FACT_REVIEW_PROMPT = SCORING_RUNTIME_RULES + "\n" + """你核对互动剧本的事实一致性，不评价文学、不补剧情。输入是作者规划，不是已发生的游戏日志。只报告原文能确认的互斥事实或行动越权，不能把缺少重述、推测或写法偏好写成错误。

按以下顺序阅读，每个节点都检查：
1. 先看 opening_scene 与 character_state：两者都在开场演完、普通回合尚未开始的时点。只比较同一主体、物件、同一时点的状态。summary、narrative_focus 是整幕规划；goals按timing区分，opening对应开场已交付，turn对应普通回合待演，不能要求开场状态预先写成turn目标完成后的结果。未写细节不等于相反事实；人物关系概览不自动等于正在发生的物理动作，角色感受也不必额外提供外部动作证明。
2. 再看 goals 与 acting_contract：核对明确行动者、权限、delivery_type、output_field、source_ids和timing。player的行为应来自玩家输入；角色禁令和发声要求不转嫁给玩家。不能因目标列表/来源未枚举前置步骤就推断玩家被跳过。
3. 按路线的实际 source/target 检查 outgoing_routes：它属于当前节点互动完成后的出幕时点，must_preserve 可保留本幕目标已交付的结果，不能拿它与本幕开场相比较后判为冲突。再看目标节点开场。把来源开场、来源幕内互动、桥段、目标开场放回各自时点；桥段已声明承接互动结果时，不因它未逐字重述全部动作就报缺失。保留结果不是再做一次动作。下一幕仍待玩家实施的动作被提前执行、同一时点状态明确互斥，或结局明确等待新的玩家回答，才是应报的冲突。

每个候选问题先核对上述时点和主体，检查原文是否允许两处描述同时成立。能成立或证据不足则不列入issues；不能把“如果把开场当结尾就冲突”这类假设列为问题。输出只写最终确认的冲突、具体原文依据与最小修法，不输出推敲过程。同一错误及其下游影响合成一条，不拆成状态和转场两条重复问题。明确代玩家执行、重复核心互动或结局待答必须为major或blocking。

引用与字段合同：story_outline字符串以{ref,text}表示，text是完整原文。ID及定位目录保留原值，不作冲突证据。evidence_refs至少含两个不同原字段的真实ref；程序恢复原路径和原文。不要输出旧evidence/path/quote，不把编号写入problem、modification_plan、preserve等正文。repair_targets列实际须改字段，不能把正确但用于佐证的字段也列进去；优先纠正错误字段，保留正确开场、状态与行动权边界。当前节点text_repair_fields目录之外、goals或acting_contract的修改均为structure；不能借text修改结构，也不因目标文字修改要求改目标ID或出口引用。

只输出JSON，必须填写下面全部字段，checked_node_ids完整覆盖本次全部节点，正常稿允许issues=[]：
{"checked_node_ids":["真实节点ID"],"issues":[{"category":"state|ownership|sequence","severity":"minor|major|blocking","target_node_ids":["需改节点ID；出口问题填来源节点"],"problem":"同一时点与主体下确实不能同时成立的原文事实或越权","evidence_refs":["原字段ref","另一原字段ref"],"modification_plan":"只修错误字段的方案","expected_result":"修后相容的事实","preserve":["保留的正确事实"],"repair_scope":"text|structure","repair_targets":[{"node_id":"真实节点ID","field":"节点内的原字段JSON Pointer"}]}]}
"""


# 同次初检显式对照开场与演完后的画面，避免模型把作者快照解释成通常的入场姿态。
# 对照只复用原字段ref/text，不新增事实、输出协议或模型阶段；重复状态合并为同一问题。
OPENING_STATE_COMPARISON_RULE = (
    "优先核对opening_end_state_comparisons：declared_picture_after_performance是作者声明的开场全部动作演完后的画面，不是动作之前的初始姿态。"
    "将opening_performance演完，再与此画面对照；已改变的状态不能仍写成变化之前，不能以“先如此、后改变也合理”放过这个同一时点的冲突。"
    "只对明确互斥的结果报错；最后又恢复、计划未实施、不同对象及相容位置均可成立。"
    "same_moment_context里重复的同一错误状态也应同步修正，与状态字段合为一条问题。"
    # 证据编号和修订路径是两套既有定位合同；长稿不能把字段路径误填进ref列表。
    "evidence_refs只填写对应原文对象的ref值（例如E12、E34），不能填写字段路径；"
    "repair_targets的field则填写节点内字段路径，不能填写证据编号。这两种定位不可混用。"
)


# 稳定ID和定位目录用于连接节点；给它们编号会让模型用名称代替行动正文凑足两份证据。
# source_ids仍可核对引用关系，owner/output_field等真实权限值仍可用于ownership证据。
_LOCATOR_FIELDS = {"id", "target_node_id", "source_node_id", "entry_from", "destination_ids", "text_repair_fields"}


def build_fact_sources(context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Number citable string leaves; retain location metadata verbatim without treating it as conflict evidence."""
    # 保留完整投影和原路径，只把主线放在结局前、开场状态放在目标与出口前，减少跨时点误读。
    # 未知字段仍原样保留，支线按已有作者顺序处理，不猜测图的实际游玩顺序或目标是否完成。
    def ordered(value, keys):
        return {key: value[key] for key in (*keys, *value) if key in value}
    context = ordered(deepcopy(dict(context)), ("author_intent", "characters", "metrics", "key_props", "mainline", "branches", "endings"))
    node_keys = ("id", "title", "summary", "narrative_focus", "acting_contract", "must_not_happen",
                 "relationship_state", "opening_scene", "character_state", "goals", "relationship_goal",
                 "transition_goal", "outgoing_routes", "text_repair_fields")
    for nodes in [context.get("mainline", []), context.get("endings", []),
                  *(branch.get("nodes", []) for branch in context.get("branches", []))]:
        for index, node in enumerate(nodes):
            nodes[index] = ordered(node, node_keys)
    sources: dict[str, dict[str, str]] = {}
    def walk(value: Any, path: str) -> Any:
        if isinstance(value, Mapping):
            return {key: deepcopy(item) if key in _LOCATOR_FIELDS else walk(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(value)]
        if isinstance(value, str) and value.strip():
            ref = f"E{len(sources) + 1}"
            sources[ref] = {"path": path, "quote": value}
            return {"ref": ref, "text": value}
        return value
    return walk(context, ""), sources


def _resolve_pointer(context: Mapping[str, Any], pointer: str) -> Any:
    """Read source text by explicit field path instead of inferring locations from explanation text."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid_fact_evidence_path")
    value: Any = context
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isdecimal() or str(int(key)) != key:
                raise ValueError("invalid_fact_evidence_index")
            value = value[int(key)]
        else:
            value = value[key]
    return value


def validate_fact_review(context: Mapping[str, Any], payload: Any, node_ids: set[str], *,
                         evidence_sources: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """Return a copy after full validation; callers report failures at the factual stage rather than save a partially valid report."""
    try:
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_fact_review")
        checked = payload.get("checked_node_ids")
        raw_issues = payload.get("issues")
        if (not isinstance(checked, list) or not all(isinstance(x, str) for x in checked)
                or len(checked) != len(node_ids) or set(checked) != node_ids
                or not isinstance(raw_issues, list)):
            raise ValueError("incomplete_fact_review")
        issues = []
        for index, raw in enumerate(raw_issues, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("invalid_fact_issue")
            targets = raw.get("target_node_ids")
            preserve = raw.get("preserve")
            if (raw.get("category") not in {"state", "ownership", "sequence"}
                    or raw.get("severity") not in {"minor", "major", "blocking"}
                    or raw.get("repair_scope") not in {"text", "structure"}
                    or not isinstance(targets, list) or not targets
                    or not all(isinstance(x, str) and x in node_ids for x in targets)
                    or not isinstance(preserve, list)
                    or not all(isinstance(x, str) and x.strip() for x in preserve)):
                raise ValueError("invalid_fact_issue")
            fields = {key: raw.get(key) for key in ("problem", "modification_plan", "expected_result")}
            if not all(isinstance(x, str) and x.strip() for x in fields.values()):
                raise ValueError("invalid_fact_description")
            evidence = raw.get("evidence")
            if evidence_sources is not None:
                # 原生调用只接受本次输入的编号；恢复后继续逐字校验，不信任模型自填引文。
                refs = raw.get("evidence_refs")
                if ("evidence" in raw or not isinstance(refs, list) or len(refs) < 2
                        or any(not isinstance(ref, str) or ref not in evidence_sources for ref in refs)):
                    raise ValueError("invalid_fact_evidence_refs")
                evidence = [evidence_sources[ref] for ref in refs]
            if not isinstance(evidence, list) or len(evidence) < 2:
                raise ValueError("missing_fact_evidence")
            citations = []
            for item in evidence:
                if not isinstance(item, Mapping):
                    raise ValueError("invalid_fact_evidence")
                path, quote = item.get("path"), item.get("quote")
                if not isinstance(quote, str) or not quote.strip():
                    raise ValueError("invalid_fact_quote")
                value = _resolve_pointer(context, path)
                citation = {"path": path, "quote": quote}
                # 数组父路径只允许唯一原文匹配；保留原路径，不改写引文或静默选择歧义项。
                if isinstance(value, list):
                    matches = [i for i, text in enumerate(value) if isinstance(text, str) and quote in text]
                    if len(matches) != 1:
                        raise ValueError("ambiguous_fact_evidence")
                    citation.update(original_path=path, path=f"{path}/{matches[0]}")
                    value = value[matches[0]]
                if not isinstance(value, str) or quote not in value:
                    raise ValueError("unverifiable_fact_evidence")
                citations.append(citation)
            if len({x["path"] for x in citations}) < 2:
                raise ValueError("duplicate_fact_evidence")
            # 与既有节点建议共用列表，使用独立ID/来源；所有事实问题及证据均保留。
            issues.append({
                "issue_id": f"fact_issue_{index:02d}", "source": "facts",
                "category": raw["category"], "dimension": "plot", "severity": raw["severity"],
                "target_node_ids": list(dict.fromkeys(targets)), **fields,
                "preserve": deepcopy(preserve), "evidence": citations,
                # 修改字段由评分编排对照实际节点权限核验，不能仅信模型的text标签。
                "repair_targets": deepcopy(raw.get("repair_targets")),
                "repair_scope": raw["repair_scope"], "repairable": raw["repair_scope"] == "text",
            })
        return {"checked_node_ids": list(checked), "issues": issues}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("invalid_fact_review") from exc
