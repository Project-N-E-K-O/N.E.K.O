"""Provide independent factual-check prompts and citation validation without rewriting or literary scoring."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .runtime_rules import SCORING_RUNTIME_RULES

# The fact stage checks only citable conflicts; valid author drafts may have none, without suggestion-count quotas.
# Explain the actual framework before inspecting source conflicts so targets/routes are not mistaken for a separate runtime state machine.
# Initial review compares timing and submits confirmed conflicts only; remove duplicate reminders and include required revision fields in examples.
FACT_REVIEW_PROMPT = SCORING_RUNTIME_RULES + "\n" + """你核对互动剧本的事实一致性，不评价文学、不补剧情。输入含作者规划和自动播放的opening_scene，须区分两者：规划不证明行动已发生，开场正文却不能预写玩家的新台词或决定。只报告原文能确认的互斥事实或行动越权，不能把缺少重述、推测或写法偏好写成错误。

terminal_node_ids由程序标明真实终止节点；目标在其中的出口可以自然收束，不因没有fallback_offer、accept_input或silent_choice就认定无法离幕。
本次只检查checked_node_ids中的一幕；其他节点仅供相邻转场对照，未提供的节点不代表不存在。只报告本幕自身字段或本幕出边的实际问题，不重报相邻幕自身的错误；入边用于核对本幕入口，不擅改来源幕。key_props.states是首次出现或状态变化时的生命周期规划，不统一对应开场演完时点；仅同node_id不能证明同刻互斥，“在某处被发现”也不反证后来已取出。

按以下顺序阅读，每个节点都检查：
1. 先按 characters.player_name/player_identity 确认玩家，逐句检查 opening_scene 的发言主体。自动正文中的玩家新台词属于 ownership 问题，不必等到角色状态也冲突才报告；开场直接预定玩家说什么，即使是合理提议、疑问或日常客套也仍代替了玩家输入。再看 goals 与 acting_contract：核对明确行动者、权限、delivery_type、output_field、source_ids和timing。opening_scene无需玩家输入便自动播放，其中玩家的新主动动作、决定或台词本身就需要玩家授权，不能因作者已经写入正文而当作合法背景。例如开场预写“玩家说出自己的选择”，即使状态也写“正在交谈”，两处相容仍不代表玩家授权，须单独报ownership越权。关系中的已知玩家事实也不能提前把本幕待作的决定记成已发生；准备、构思及等待不等于已提出方案。player的行为应来自玩家输入；acting_contract是女主的权限，允许女主“协作”“对话”不能授权玩家在自动正文中说新台词或作决定。角色禁令和发声要求不转嫁给玩家。不能因目标列表/来源未枚举前置步骤就推断玩家被跳过。
2. 再看 opening_scene 与 character_state：两者都在开场演完、普通回合尚未开始的时点。按同一时点核对同一人物的状态及同一物件的持有人、位置，区分持有与观察、查验。summary、narrative_focus 是整幕规划；goals按timing区分，opening对应开场已交付，turn对应普通回合待演，不能要求开场状态预先写成turn目标完成后的结果。未写细节不等于相反事实；人物关系概览不自动等于正在发生的物理动作，角色感受也不必额外提供外部动作证明。
3. 按路线的实际 source/target 逐条检查 outgoing_routes。先看 completion_contract 对应事实定义与路线条件保证什么，再区分接受前的 fallback_offer/silent_choice、玩家将提交的 accept_input 与接受后的桥段和目标开场；不假定未受保证的 goals 已经完成。核对来源叙事、合同与目标入口的整条因果链；若本幕明确允许互斥处置而路线条件未指定其一，固定桥段和结局不能无条件选定其中一种，只能保留各合法选择共同保证的结果。没有承诺不同结果时，不臆造新选择或要求新增分支。核对：未受保证的结果被固定出口宣称完成、引用了上一出口、目的地或时段不符、提前代做新选择及结局等待新的玩家回答均应举证；条件已经保证的结果无需逐字重述，保留结果也不等于再次执行。不能把合法出幕结果与来源开场当作同一时点而报冲突。

state/sequence 问题先核对上述时点和主体，检查原文是否允许两处描述同时成立；能成立或证据不足则不列入issues，不能把“如果把开场当结尾就冲突”这类假设列为问题。ownership 则检查自动正文是否预定玩家新发言或新决定，不要求另有一处相反事实；背景明确记载的往事、转述已有原话及已接受出口的正常移动不等于新发言。递向、递出待接不证明对方已接过；单手动作不证明双手被占用，佩戴设备也不证明手持介质，不能据这些推测报告持有者冲突。另行检查开场是否直接替玩家说话、操作或作新决定，不因漏报这种越权而用姿态推测代替。输出只写最终确认的冲突、具体原文依据与最小修法，不输出推敲过程。同一错误及其下游影响合成一条，不拆成状态和转场两条重复问题。明确代玩家执行、重复核心互动或结局待答必须为major或blocking。

引用与字段合同：story_outline字符串以{ref,text}表示，text是完整原文。ID及定位目录保留原值，不作冲突证据。evidence_refs至少含两个不同原字段的真实ref；程序恢复原路径和原文。ownership 可以引用含玩家新台词的 opening_scene 与明确该姓名属于玩家的 player_name/player_identity，或明确要求 runtime.player_input 的目标原文；这两处证明主体与越权，不需要编造一对互斥状态。不要输出旧evidence/path/quote，不把编号写入problem、modification_plan、preserve等正文。repair_targets列实际须改字段，不能把正确但用于佐证的字段也列进去；优先纠正错误字段，保留正确开场、状态与行动权边界。若开场越权替玩家说话，应修opening_scene，不能只让character_state追上这段台词而保留越权；行动越权与状态不一致不是同一个问题。当前节点text_repair_fields目录之外、goals或acting_contract的修改均为structure；不能借text修改结构，也不因目标文字修改要求改目标ID或出口引用。

不重做编译器的格式检查；兼容旧结局目标未声明timing不代表正文事实冲突，不能猜测运行失败。每条problem、modification_plan、expected_result各用一两句完整结论，不输出自我否定的观察项。字符串正文的引文用中文引号“”，不要内嵌未转义的英文双引号；JSON键和字符串边界仍用英文双引号。只输出JSON，必须填写下面全部字段，checked_node_ids完整覆盖本次全部节点，正常稿允许issues=[]：
{"checked_node_ids":["真实节点ID"],"issues":[{"category":"state|ownership|sequence","severity":"minor|major|blocking","target_node_ids":["需改节点ID；出口问题填来源节点"],"problem":"同一时点与主体下确实不能同时成立的原文事实或越权","evidence_refs":["原字段ref","另一原字段ref"],"modification_plan":"只修错误字段的方案","expected_result":"修后相容的事实","preserve":["保留的正确事实"],"repair_scope":"text|structure","repair_targets":[{"node_id":"真实节点ID","field":"节点内的原字段JSON Pointer"}]}]}
"""


# Explicitly compare opening and post-performance views in the same initial review so author snapshots are not read as usual entrance poses.
# Reuse only existing ref/text fields: add no facts, output protocol, or model stage; merge repeated state issues.
OPENING_STATE_COMPARISON_RULE = (
    "核对开场状态时，character_state是作者声明的opening_scene全部动作演完后的画面，不是动作之前的初始姿态。"
    "将opening_scene演完，再与此画面对照；已改变的状态不能仍写成变化之前，不能以“先如此、后改变也合理”放过这个同一时点的冲突。"
    "只对明确互斥的结果报错；最后又恢复、计划未实施、不同对象及相容位置均可成立。"
    "relationship_state里重复的同一错误状态也应同步修正，与状态字段合为一条问题。"
    # Evidence IDs and revision paths are separate existing location contracts; long drafts must not put field paths in ref lists.
    "evidence_refs只填写对应原文对象的ref值（例如E12、E34），不能填写字段路径；"
    "repair_targets的field则填写节点内字段路径，不能填写证据编号。这两种定位不可混用。"
)


# Stable IDs and location indexes connect nodes; numbering them encourages model names to substitute for action evidence.
# source_ids still verify references; actual owner/output_field permission values remain valid ownership evidence.
_LOCATOR_FIELDS = {"id", "target_node_id", "source_node_id", "entry_from", "destination_ids", "text_repair_fields"}


def node_fact_context(context: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    """Retain the focus, all immediate sources/destinations and their original branch metadata."""
    nodes = {node["id"]: node for node in [*context.get("mainline", []),
        *(node for branch in context.get("branches", []) for node in branch.get("nodes", [])),
        *context.get("endings", [])]}
    selected = {node_id, *(route["target_node_id"] for route in nodes[node_id].get("outgoing_routes", []))}
    selected.update(source for source, node in nodes.items() if any(
        route["target_node_id"] == node_id for route in node.get("outgoing_routes", [])))
    result = deepcopy(dict(context))
    for key in ("mainline", "endings"):
        result[key] = [node for node in result.get(key, []) if node["id"] in selected]
    result["branches"] = [{**branch, "nodes": [node for node in branch.get("nodes", []) if node["id"] in selected]}
                          for branch in result.get("branches", []) if any(node["id"] in selected for node in branch.get("nodes", []))]
    return result


def build_fact_sources(context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Number citable string leaves; retain location metadata verbatim without treating it as conflict evidence."""
    # Preserve complete projections and original paths; place main routes before endings and opening state before targets/exits to reduce timing confusion.
    # Preserve unknown fields and author branch order; do not infer actual graph play order or target completion.
    def ordered(value, keys):
        return {key: value[key] for key in (*keys, *value) if key in value}
    context = ordered(deepcopy(dict(context)), ("author_intent", "background", "characters", "metrics", "key_props", "mainline", "branches", "endings"))
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
                         evidence_sources: Mapping[str, Mapping[str, str]] | None = None,
                         checked_node_ids: set[str] | None = None) -> dict[str, Any]:
    """Return a copy after full validation; callers report failures at the factual stage rather than save a partially valid report."""
    try:
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_fact_review")
        checked = payload.get("checked_node_ids")
        raw_issues = payload.get("issues")
        expected = node_ids if checked_node_ids is None else checked_node_ids
        if (not isinstance(checked, list) or not all(isinstance(x, str) for x in checked)
                or len(checked) != len(expected) or set(checked) != expected
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
                # Native calls accept only current-input IDs; after recovery, still verify quotations verbatim rather than trusting model-filled citations.
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
                # Array-parent paths require unique verbatim matches; preserve original paths without rewriting quotes or silently choosing ambiguous items.
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
            # Share the existing node-suggestion list but use independent IDs/sources; retain all factual issues and evidence.
            issues.append({
                "issue_id": f"fact_issue_{index:02d}", "source": "facts",
                "category": raw["category"], "dimension": "plot", "severity": raw["severity"],
                "target_node_ids": list(dict.fromkeys(targets)), **fields,
                "preserve": deepcopy(preserve), "evidence": citations,
                # Scoring orchestration validates modified fields against actual node permissions, not merely model text labels.
                "repair_targets": deepcopy(raw.get("repair_targets")),
                "repair_scope": raw["repair_scope"], "repairable": raw["repair_scope"] == "text",
            })
        return {"checked_node_ids": list(checked), "issues": issues}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("invalid_fact_review") from exc
