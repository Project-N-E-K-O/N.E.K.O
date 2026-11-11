"""Verify factual-review completeness and execution boundaries; models judge semantics, and code does not delete findings by keyword."""
from copy import deepcopy
from typing import Any, Mapping

from .facts import FACT_RUNTIME_RULES

# User confirmation adds one review only when the factual stage reports a problem; keep the load-tested per-field boolean output.
# Use the same explanatory framework as the factual and literary stages; review only the original issues and fields, without adding judgment steps.
EVIDENCE_REVIEW_PROMPT = FACT_RUNTIME_RULES + "\n" + """你负责核对已有事实报告的证据，不寻找新问题，不改剧本、不评分。输入story_outline含原作者规划与各入口的开场材料，proposed_issues是另一份可能出错的评审。逐条判断原文是否真的证明该问题，并核对已列修改字段是否必要。
evidence.path 是从 story_outline 根开始的 JSON Pointer，数组索引从 0 开始。按路径读完整字段原文。text_omitted=true 的节点只供定位，省略正文不能作为缺失或冲突的证据。

时间依据：除实际用作旧稿开场的首句外，每幕summary与scene_summary是整幕概览；goals的turn事件是待演规划，不是逐项完成记录。outgoing_routes内的fallback_offer/silent_choice在接受前展示，accept_input是待玩家选择，桥段与目标开场在接受后发生；依出口执行时点核对完成合同及路线条件，不将整份出口合同后移到全部目标已完成之后。结局承接最后互动完成后的结果，没有普通玩家回合。已获保证的结果可以延续，桥段不必逐字重复它；未获保证的预期不能靠出口文字自证。参考实际路线，不把同节点开场和出口当成同一时点。key_props.states为首次出现或状态变化时的生命周期规划，不统一对应开场演完；仅凭同node_id或“被发现”不能反证后来已取出。

terminal_node_ids由程序标明真实终止节点。自然结局不依赖普通邀约或接受按钮，不因其fallback_offer/accept_input/silent_choice为空就判出口不可达。

事实审核不评价门槛是否偏严格。完成部分条件后留幕是正常状态，不能仅因这次互动尚不能离幕就支持“死路”或承接缺口；允许先做A不等于承诺无需B便可离幕。指控提前承接时，反例必须满足实际全部出幕条件；指控违背作者承诺时，必须有明确承诺不做该项仍可离幕的原文，不能把目标的可选顺序改读成出口豁免。缺少这些依据时，对这类指控判unsupported，不替原报告补造新问题。

判定标准：同刻状态检查同一主体、对象是否明确互斥；出幕承接检查原指控的合法反例是否确实满足该路线实际条件、却未发生被宣称为来源历史的结果，不能只因存在另一种相容演法就否定缺口。按完整原文排除违反阶段限制或自然结局要求的反例，也保留正常桥段与目标开场的当时交付。时点一致与行动获准是两回事；开场自动预写玩家新台词或选择本身属于越权，不能仅因character_state与台词相配就判不成立。acting_contract允许演出角色协作或对话，不授权玩家在自动正文中作新决定或说新台词；不能用演出角色许可替玩家台词免责。缺少某处重述、等待不同动作、概览涵盖后来结果、状态保持、正常移动均不能单独证明冲突。不能自行补设备依赖、目标触发方式或玩家已做动作。递向、递出待接不证明对方已接过；单手动作不证明双手被占用，佩戴设备不证明手持介质。若持有者冲突依赖这些推测，不能判supported；明确完成交接后仍写原持有人持物则继续拦截。把引文读完整，不能忽略前半句、把不同动作混成一个，或将演出角色禁令当玩家禁令。

行动权按characters中的实际姓名逐个主体、逐个动作核对；演出角色的allowed_behaviors、岗位分工与作者character_state均不证明玩家已授权。只有适用入路的accept_input确实包含同一行为才可支持玩家授权，宽泛的“查一下情况”不自动包含外出或操作另一设备。共用入口须对每条适用入路成立，不能合并各路不同保证。路线原文明说电话、现场或两者均可时，分别以实际completion_contract、conditions和接受动作核对后幕既成事实；自动转场不能借用手动接受的保证。坏repair_target不推翻原问题的事实证据；无法核实位置时保留事实判断，不猜替代字段、不据此授权修复。

verdict三选一：supported（原文证明互斥、越权或所指承接缺口），unsupported（原文反证指控：同刻状态相容，或反例不合法、结果已有保证或可在当时合法交付），uncertain（现有原文不足以确定）。reason用一两句话说明引用内容、条件与时点，写最终结论。
逐个核对原报告的repair_targets，全部写入target_checks，不删条目。只有本字段必须改变才能消除本条问题时，requires_change才为true。只作证据或改另一字段就够时为false，本条不授权改它。对supported且不需改的字段，必填preserve_original；仅有原文依据证明必须逐字保留时才为true，否则其他问题仍可修它。保留事实或时序不等于逐字保留。其余情况可省略preserve_original；unsupported或uncertain的requires_change必须为false。不能新增目标字段。

字符串正文的引文用中文引号“”，不要内嵌未转义的英文双引号；JSON键和字符串边界仍用英文双引号。只输出JSON，checks覆盖每个原issue_id一次，target_checks覆盖该问题每个原repair_target一次。原建议和每个字段的核对结果都保留，不输出推敲过程：
{"checks":[{"issue_id":"原issue_id","verdict":"supported|unsupported|uncertain","reason":"证据支持或不支持的具体原因","target_checks":[{"node_id":"原报告已列节点","field":"原报告已列字段","requires_change":false,"preserve_original":false,"reason":"该字段是否需改或逐字保留的依据"}]}]}
"""


def review_targets(issue: Mapping[str, Any]) -> list[dict[str, str]]:
    """Review receives only identifiable locations from the original plan; missing or invalid locations stay in the report without fabricated repairs."""
    targets = issue.get("repair_targets")
    result = []
    for item in targets if isinstance(targets, list) else []:
        if isinstance(item, Mapping) and all(isinstance(item.get(k), str) and item[k] for k in ("node_id", "field")):
            target = {k: item[k] for k in ("node_id", "field")}
            if target not in result:
                result.append(target)
    return result


def validate_evidence_review(issues: list[dict], payload: Any) -> list[dict]:
    """Require complete results for each issue and field; unknown, duplicate, expanded or non-boolean decisions fail the entire review."""
    def fail():
        raise ValueError("invalid_fact_evidence_review")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("checks"), list):
        fail()
    checks = payload["checks"]
    originals = {issue["issue_id"]: issue for issue in issues}
    found = {}
    for check in checks:
        if not isinstance(check, Mapping):
            fail()
        identity = check.get("issue_id")
        if not isinstance(identity, str) or identity not in originals or identity in found:
            fail()
        if check.get("verdict") not in ("supported", "unsupported", "uncertain") or not isinstance(check.get("reason"), str) or not check["reason"].strip():
            fail()
        targets = check.get("target_checks")
        if not isinstance(targets, list):
            fail()
        expected = {(x["node_id"], x["field"]) for x in review_targets(originals[identity])}
        seen = set()
        for target in targets:
            if not isinstance(target, Mapping) or not all(isinstance(target.get(k), str) for k in ("node_id", "field")):
                fail()
            key = (target["node_id"], target["field"])
            if key not in expected or key in seen or type(target.get("requires_change")) is not bool:
                fail()
            if not isinstance(target.get("reason"), str) or not target["reason"].strip():
                fail()
            needs_preservation_check = check["verdict"] == "supported" and not target["requires_change"]
            preserve = target.get("preserve_original", None if needs_preservation_check else False)
            if (type(preserve) is not bool or (preserve and target["requires_change"])
                    or (check["verdict"] != "supported" and (target["requires_change"] or preserve))):
                fail()
            seen.add(key)
        if seen != expected:
            fail()
        found[identity] = {k: deepcopy(check[k]) for k in ("verdict", "reason", "target_checks")}
    if set(found) != set(originals):
        fail()
    # Preserve the original issue, proposal, evidence, and scope; review adds an opinion that the frontend can compare in full.
    return [{**deepcopy(issue), "evidence_review": found[issue["issue_id"]]} for issue in issues]


def effective_targets(issue: Mapping[str, Any]) -> list[dict[str, str]]:
    """Apply factual repairs only to supported original fields confirmed as needing changes; literary suggestions retain their original field scope."""
    if issue.get("source") != "facts":
        return review_targets(issue)
    review = issue.get("evidence_review") or {}
    if review.get("verdict") != "supported":
        return []
    original = review_targets(issue)
    return [target for target in original if any(
        check.get("node_id") == target["node_id"] and check.get("field") == target["field"]
        and check.get("requires_change") is True for check in review.get("target_checks") or [])]


def protected_targets(issues: list[dict]) -> list[dict[str, str]]:
    """Apply explicit cross-plan preservation, retaining protection in older saved reviews."""
    return [{"node_id": row["node_id"], "field": row["field"]}
            for issue in issues if issue.get("source") == "facts"
            if (review := issue.get("evidence_review") or {}).get("verdict") == "supported"
            for row in review.get("target_checks", [])
            if row["requires_change"] is False and row.get("preserve_original", True) is True]


def fields_overlap(left: str, right: str) -> bool:
    """Replacing a parent array or object touches protected children and cannot bypass preservation through a broader path."""
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")
