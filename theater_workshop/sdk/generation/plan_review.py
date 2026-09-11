"""按需复核方案的文字、字段和保留要求；不生成补丁或扩大已有执行权限。"""
from copy import deepcopy
from typing import Any, Mapping

from .facts import _resolve_pointer
from .evidence import effective_targets
from .repair import context_nodes
from .runtime_rules import SCORING_RUNTIME_RULES

# 与评分、修订共享运行合同；字段权限仍由程序核验，不能让模型修改规则。
# 单条范围与组合冲突分别表达，冲突只声明一次，由程序同时保护双方。
PLAN_REVIEW_PROMPT = SCORING_RUNTIME_RULES + "\n\n" + """你是剧本修改方案的执行前复核员。只检查给定plans中的方案文字、repair_targets和preserve是否一致，不判断文学分数，不寻找新剧情缺陷，不替作者改稿，不删除原问题。

story_outline为完整作者稿；各节点text_repair_fields是当前允许直接修订的文本字段。repair_targets是这次方案明确列出的修改位置。modification_plan或suggestion是方案正文；preserve是保留要求。protected_repair_targets中的字段必须原样保留。事实问题若有evidence_review，以复核确认需改的原字段为执行范围，requires_change=false必须保留，原方案中已被该复核否定的描述不作为新增修改命令。

global_strengths_to_preserve是整份报告要求保留的优点，同样不能牺牲。

字段职责（来自当前作者稿投影，不能互相代替）：
- opening_scene：当幕唯一开场的演出文字。
- character_state/catgirl：开场演完后的女主状态记录。
- character_state/player：开场演完后的玩家状态记录。
- character_state/environment：开场演完后的环境与物件状态记录。
- summary：当幕剧情概览；结局节点为结局摘要，scene_summary另指结局节点正文摘要。
- narrative_focus：角色演绎重心指引，属于结构字段。
- outgoing_routes/N/transition_contract：第N个出口自身的转场合同；tone是语气，reason是原因，bridge_scene_narration是旁白，彼此独立。
“修改某字段的状态记录”必须修改该字段，不能通过润色开场或摘要代替；“描写环境”不自动等于要求修改后台环境状态记录。根据方案明确要求与上述字段职责定位，不能把需要保留的字段当成修改目标。

逐项核对：
1. 方案正文明确要求修改的所有既有字段是否列入repair_targets？不能因字段名字出现在原文证据或“保留”要求里，就当作要改。不能把“改演绎指引/目标”通过只改摘要实现。
2. 已列位置是否与正文对应？只检查真实需要改变的原字段，不能仅因润色动作描述就要求把全部状态一起重写。
3. 方案与保留要求是否相容？“保持原文逐字不变”不允许润色；“保持动作事实、时间地点与顺序”允许改变表达。保留事实不等于保留每个字。
4. 需要改goals、narrative_focus、acting_contract、图结构或其它不在text_repair_fields中的字段，不能直接执行。缺失字段或有冲突时指出原因，不补齐授权，不修改方案。如果输入不足以判断，标uncertain。

5. 分开判断“单条方案”和“组合冲突”。只有一条方案时conflicts必须为空，本条内部矛盾只写入本条checks。checks只检查本条修改命令、字段权限及保留要求，不在checks里判断其他方案。conflicts统一记录整批方案之间的互斥要求：每对真实冲突只列一次，issue_ids恰好含双方ID，reason引用双方无法同时满足的要求。字段重叠、相同修法或相容润色不算冲突；同节点的建议会被一次修订统筹为一份补丁，不是依次执行多次。这些是合写同一份文本的要求，不是两个已生成的文本补丁。只判断要求能否同时成立，不判断编辑器锁、资源竞争或并发写入；不能因将来的两份候选句式可能不同就认定当前要求互斥。程序会让冲突双方都不可执行，无需模型再分别给双方填写冲突编号或修改局部状态。

只输出JSON object。先填写conflicts，再逐个覆盖所有原issue_id的checks，各一次。checks.status仅表示单条方案的ready（字段完整且与本条保留要求相容）、blocked（明确缺项/本条冲突/结构修改）或uncertain（信息不足）；程序还会结合conflicts决定能否执行。即使两条单独都ready，只要组合冲突仍会同时被拦截。
reason给具体原文依据，保留模型的单条复核说明与组合冲突说明。missing_targets只列本条正文确实要求而原清单遗漏的既有字段；明确子字段不扩大成父对象或兄弟字段，仅作证据或要求保留的字段不列。它们仅解释，不补授权。没有遗漏或组合冲突均用[]。checks不再输出conflicting_issue_ids。有跨方案冲突时，conflicts中的每项为{"issue_ids":["不同的原ID甲","不同的原ID乙"],"reason":"双方具体互斥要求"}；无真实互斥就使用空数组，不为填写此栏寻找冲突。
{"conflicts":[],"checks":[{"issue_id":"原ID","status":"ready|blocked|uncertain","reason":"单条方案核对依据","missing_targets":[{"node_id":"节点ID","field":"/投影路径"}]}]}
"""


def validate_plan_review(context: Mapping[str, Any], plans: list[dict], payload: Mapping[str, Any]) -> dict[str, dict]:
    """必须逐个覆盖原方案；遗漏字段仅供展示，绝不并入 repair_targets。"""
    nodes = context_nodes(context)
    expected = {plan["issue_id"]: plan for plan in plans}
    result = {}
    try:
        # 一对冲突只声明一次；先校验原ID与说明，再将同一结论投影到双方，不猜自然语言关联。
        pairs = payload["conflicts"]
        if not isinstance(pairs, list):
            raise ValueError()
        by_issue = {identity: [] for identity in expected}
        seen_pairs = set()
        for pair in pairs:
            identities, reason = pair["issue_ids"], pair["reason"]
            if (not isinstance(identities, list) or len(identities) != 2
                    or any(not isinstance(identity, str) or identity not in expected for identity in identities)
                    or identities[0] == identities[1] or not isinstance(reason, str) or not reason.strip()):
                raise ValueError()
            key = tuple(sorted(identities))
            if key in seen_pairs:
                raise ValueError()
            seen_pairs.add(key)
            for identity in identities:
                by_issue[identity].append({"issue_ids": identities[:], "reason": reason})
        checks = payload["checks"]
        if not isinstance(checks, list):
            raise ValueError()
        for row in checks:
            issue_id = row["issue_id"]
            if issue_id not in expected or issue_id in result:
                raise ValueError()
            status, reason, missing = row["status"], row["reason"], row["missing_targets"]
            if status not in {"ready", "blocked", "uncertain"} or not isinstance(reason, str) or not reason.strip():
                raise ValueError()
            if not isinstance(missing, list) or (status == "ready" and missing):
                raise ValueError()
            # 新模型协议只从成对声明读取冲突，不能静默忽略旧格式中的单边冲突意见。
            if "conflicting_issue_ids" in row:
                raise ValueError()
            seen = set()
            for target in missing:
                node_id, field = target["node_id"], target["field"]
                # 可以指出别幕或结构字段的实际遗漏，但不存在的字段不能被伪造为定位结果。
                _resolve_pointer(nodes[node_id], field)
                key = (node_id, field)
                if key in seen or any(node_id == t["node_id"] and
                        (field == t["field"] or field.startswith(t["field"] + "/"))
                        for t in effective_targets(expected[issue_id])):
                    raise ValueError()
                seen.add(key)
            conflicts = by_issue[issue_id]
            result[issue_id] = deepcopy({
                "status": "blocked" if conflicts else status,
                # 局部肯定与组合冲突分别留档、展示；程序只收紧权限，不改写模型的原理由。
                "local_status": status, "reason": reason, "missing_targets": missing,
                "conflicting_issue_ids": [other for pair in conflicts for other in pair["issue_ids"] if other != issue_id],
                "conflict_checks": conflicts,
            })
        if set(result) != set(expected):
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("invalid_repair_plan_review") from error
    return result
