"""事实复核的完整性与执行边界；模型判语义，程序不按关键词删问题。"""
from copy import deepcopy
from typing import Any, Mapping

from .runtime_rules import SCORING_RUNTIME_RULES

# 用户确认仅在事实报出问题时增加一次复核；沿用已压测的逐字段布尔输出。
# 与事实和文学阶段使用同一框架解释，仍只复核原问题及其原字段，不添加新判断步骤。
EVIDENCE_REVIEW_PROMPT = SCORING_RUNTIME_RULES + "\n" + """你负责核对已有事实报告的证据，不寻找新问题，不改剧本、不评分。输入story_outline是原作者规划，proposed_issues是另一份可能出错的评审。逐条判断原文是否真的证明该问题，并核对已列修改字段是否必要。

时间依据：每幕summary是整幕概览；opening_scene与character_state同属开场演完时点；goals的turn事件在普通回合中交付；outgoing_routes是来源目标完成、玩家接受换场后的桥段；目标幕开场随后发生。结局承接最后互动完成后的结果，没有普通玩家回合。目标结果可以延续，桥段不必逐字重复它。参考实际路线，不把同节点开场和出口当成同一时点。

判定标准：同一主体、对象、时点是否存在明确互斥事实或权限？缺少某处重述、等待不同动作、概览涵盖后来结果、状态保持、正常移动均不能单独证明冲突。不能自行补设备依赖、目标触发方式或玩家已做动作。把引文读完整，不能忽略前半句、把不同动作混成一个，或将女主禁令当玩家禁令。

verdict三选一：supported（原文能证明该问题），unsupported（原文与该判断相反或互相相容），uncertain（现有原文不足以确定）。reason用一两句话说明引用内容与时点，写最终结论。
逐个核对原报告的repair_targets，全部写入target_checks，不删条目。每项先写requires_change布尔值，再用一句话解释。本字段原文有错、必须改变才能消除该问题时才为true；原文已正确、仅作证据或仅需保留时为false。若只改另一个字段就能消除冲突，本字段为false。不能新增字段。verdict为unsupported或uncertain时，本问题全部requires_change为false。

只输出JSON，checks覆盖每个原issue_id一次，target_checks覆盖该问题每个原repair_target一次。原建议和每个字段的核对结果都保留，不输出推敲过程：
{"checks":[{"issue_id":"原issue_id","verdict":"supported|unsupported|uncertain","reason":"证据支持或不支持的具体原因","target_checks":[{"node_id":"原报告已列节点","field":"原报告已列字段","requires_change":false,"reason":"该字段要改或保持原文的理由"}]}]}
"""


def review_targets(issue: Mapping[str, Any]) -> list[dict[str, str]]:
    """复核只接收原方案中可识别的位置，缺失/非法位置仍留在原报告且不会伪造补齐。"""
    targets = issue.get("repair_targets")
    result = []
    for item in targets if isinstance(targets, list) else []:
        if isinstance(item, Mapping) and all(isinstance(item.get(k), str) and item[k] for k in ("node_id", "field")):
            target = {k: item[k] for k in ("node_id", "field")}
            if target not in result:
                result.append(target)
    return result


def validate_evidence_review(issues: list[dict], payload: Any) -> list[dict]:
    """必须逐问题、逐字段完整返回，未知/重复/扩权或非布尔结论让整次复核失败。"""
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
            if check["verdict"] != "supported" and target["requires_change"]:
                fail()
            seen.add(key)
        if seen != expected:
            fail()
        found[identity] = {k: deepcopy(check[k]) for k in ("verdict", "reason", "target_checks")}
    if set(found) != set(originals):
        fail()
    # 原问题、原方案、证据和范围均不改写；复核是附加意见，前端可完整对照。
    return [{**deepcopy(issue), "evidence_review": found[issue["issue_id"]]} for issue in issues]


def effective_targets(issue: Mapping[str, Any]) -> list[dict[str, str]]:
    """事实仅执行受支持且确认需改的原字段；文学建议沿用原字段范围。"""
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
    """复核要求保留的字段也约束同节点文学建议，避免换个建议来源又被重写。"""
    return [{"node_id": row["node_id"], "field": row["field"]}
            for issue in issues if issue.get("source") == "facts"
            for row in (issue.get("evidence_review") or {}).get("target_checks", [])
            if row["requires_change"] is False]


def fields_overlap(left: str, right: str) -> bool:
    """父级数组/对象替换也会触及受保护子字段，不能用较宽路径绕过保留要求。"""
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")
