"""Bind exit prose to declared facts, then identify unproven candidates without judging defects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .facts import _resolve_pointer


# The workshop SDK must stay importable without config/ (see test_sdk_lifecycle), and theater prompts are zh-only for now.
EXIT_BINDING_PROMPT = """你只做原文语义绑定，不审核对错、不判断条件充分性、不提出修法或新剧情。fact_definitions是本次可绑定的事实定义，其他既有历史不在本次绑定范围；conditions和completion_contract是原始程序条件，不表示事实已经发生。
对每条路线claims中的每个ref恰好返回一条绑定。结合source_context、transition_context、target_entry辨认该句时点与主体：source_history指目标入口之前已完成的来源行动、经历、体验或玩家陈述；entry_delivery指转场或目标开场本次才实际交付的内容，不能把首次展示当作此前已经体验；其余、不确定或找不到对应声明事实时用other。summary/goals规划不能证明已完成，来源开场也不等于离幕时点。
只有source_history填写与原句同一主体、动作和完成范围的已声明fact_key及其值，不用相近目标替代，不造key。entry_delivery/other的fact_key和value均为null。value必须严格符合定义value_type：bool为JSON布尔、int为整数、string为字符串；不把准备、请求或提出等同已交付/亲自完成。
只输出JSON：{"bindings":[{"route_id":"原路线ID","claim_ref":"原ref","kind":"source_history|entry_delivery|other","fact_key":null,"value":null}]}。每个(route_id,claim_ref)恰好一次，不遗漏、不重复、不新增字段。"""  # noqa: INLINE_PROMPT_NON_EN


# The workshop SDK must stay importable without config/ (see test_sdk_lifecycle), and theater prompts are zh-only for now.
EXIT_FACT_REVIEW_PROMPT = """你只复核exit_candidates是否确有来源历史承接缺口。exit_context给出原事实定义、实际路线条件、完成合同和两端入口；checked_node_ids指定来源幕。候选仅表示程序未能充分证明，不是已确认错误，不得照单报错或新增事实。
本范围均为自动推进：最少回合等通用条件满足后，completion_contract.all和该路线conditions满足即可离幕。must_preserve、目标continuity和source_ids只写作者期望，不是额外程序门槛，不会自动执行或登记其内容，不能用这些已然断言反过来证明自己。玩家可以只做实际条件要求的动作；其他独立动作不会随之自动完成，演员也不能在转场中替玩家补做新动作或发言。background/author_intent中明确已发生的前情也可能构成保证，未来规划则不算。
这是静态合同审核，不要求已经观察到一次坏演出。能构造满足实际出幕条件却未发生该来源结果的合法反例，就说明保证关系有缺口，应判gap；不能因“也可能正常完成”或“尚未实演”改判uncertain。uncertain仅用于无法确定主体、时点或是否另有真实保证的情况。
逐候选比对claim_refs原句与definition_ref定义，须同主体、动作、对象和完成范围；绑定不符或原句非已然历史时以原文说明，无法确定则填uncertain。依据实际source_context开场和明确限制，构造满足completion_contract及该路线conditions、却尚未发生所指来源结果的具体合法状态。all全部满足，any满足任一；不能把未列入完成合同等同无法登记。反例违反明确限制或开场已实际交付的事实时无效。summary/goals理想顺序不能补作硬锁，也不能单凭未列某goal认定缺口。
区分此前完成与transition_context/target_entry本次首次交付；合法转场、目标开场展示及正常未来提议不报。来源开场不是离幕时点，已保留结果也不要求重复执行。自然结局不在本次范围，不推测其额外readiness。
仅原文及合法反例共同确认时输出issue，problem写明反例及下游过度承诺。只修错误原字段，可收窄承诺或对齐已有条件，不一律加锁、不编新剧情。跳过已声明的核心交付、体验或玩家发言/决定才按其实际影响评major或blocking，旁枝不自动升格；同因合并。issue的evidence_refs至少两个不同原字段的真实ref；repair_targets只列须改字段。completion_contract是来源节点根字段，route_field定位原出口；text_repair_fields之外或改条件写structure。结构问题若不能准确定位真实原字段，repair_targets填[]交人工处理，不能把claims、ref/text等审核目录当成剧本字段。
每个候选(route_id,fact_key,value及其精确类型)必须在candidate_checks恰回答一次，reason非空。gap须有issues至少一条引用其claim和另一原字段，检查项可引用证明反例的条件定义，无须重复issue的同一引文；每条issue也须对应至少一条gap的claim。clear须有明确正面原文：source_opening只引来源opening_scene/opening_fallback；source_original只引required_before_exit或完成合同指定的来源fixed原文及其after依赖；background引顶层background/author_intent中已发生前情；entry_delivery只引目标opening_scene/opening_fallback或bridge_scene_narration实际正文；binding_mismatch同时引用候选claim和definition证明绑定不符。reason解释该原文为何保证同一结果或证明绑定不符，不能以must_preserve/continuity、事实定义或路线定位自证已完成。不能举证则uncertain，可空evidence_refs，clear_basis填null；gap的clear_basis也为null。空issues不能代替逐项回答。
evidence_refs只填本次原文的E编号，如["E1","E2"]，绝不填JSON Pointer或路径；只有repair_targets.field填节点内原字段JSON Pointer。只输出以下JSON，原样返回checked_node_ids，正常稿允许空issues，不输出contract_checks或额外字段；示例value须换为候选原值并保持精确类型：
{"checked_node_ids":["原来源节点ID"],"issues":[{"category":"state|ownership|sequence","severity":"minor|major|blocking","target_node_ids":["原来源节点ID"],"problem":"已确认缺口及具体合法反例","evidence_refs":["E1","E2"],"modification_plan":"最小修法","expected_result":"修后相容事实","preserve":["保留的正确事实"],"repair_scope":"text|structure","repair_targets":[{"node_id":"须改节点ID","field":"节点内原字段JSON Pointer"}]}],"candidate_checks":[{"route_id":"原路线ID","fact_key":"原候选key","value":true,"status":"gap|clear|uncertain","reason":"原文支持的结论或无法确认的原因","evidence_refs":["E1","E2"],"clear_basis":null}]}"""  # noqa: INLINE_PROMPT_NON_EN


def _nodes(context: Mapping[str, Any]) -> dict[str, Any]:
    nodes = [*context.get("mainline", []), *context.get("endings", []),
             *(node for branch in context.get("branches", []) for node in branch.get("nodes", []))]
    return {node["id"]: _resolve_pointer(context, node["shared_node_ref"])
            if "shared_node_ref" in node else node for node in nodes}


def _entry(node: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: deepcopy(node[key]) for key in
              ("opening_scene", "entry_player_inputs", "fixed_narrations", "must_not_happen", "acting_contract")
              if key in node}
    result["character_state"] = {key: deepcopy(value) for key, value in
                                 (node.get("character_state") or {}).items() if key != "continuity"}
    fallback = node.get("opening_fallback_source")
    if fallback:
        result["opening_fallback"] = deepcopy(node[fallback])
    return result


def build_exit_binding_request(
    context: Mapping[str, Any], scoped: Mapping[str, Any], node_id: str,
    terminal_node_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Reuse the fact review's annotated projection, including canonical shared-node references."""
    marked_nodes, raw_nodes = _nodes(scoped), _nodes(context)
    source, raw_source = marked_nodes[node_id], raw_nodes[node_id]
    scoped_facts = (scoped.get("fact_contract") or {}).get("facts") or {}
    # description is optional for facts that no condition or route references.
    definitions = [{"key": key, "value_type": definition["value_type"],
                    "description": deepcopy((scoped_facts.get(key) or {}).get("description", ""))}
                   for key, definition in (context.get("fact_contract") or {}).get("facts", {}).items()
                   # Match the Evaluator's actual write scope. Earlier scene
                   # facts stay in the main review instead of becoming new work.
                   if not key.startswith("scene:") or key.startswith(f"scene:{node_id}:")]
    terminal_ids = set(terminal_node_ids or ()) | {node["id"] for node in context.get("endings", [])}
    raw_routes = {route["id"]: (index, route) for index, route in enumerate(raw_source.get("outgoing_routes", []))}
    routes = []
    for route in source.get("outgoing_routes", []):
        route_index, raw_route = raw_routes[route["id"]]
        if ((raw_route.get("transition_contract") or {}).get("advance_on_completion") is not True
                or route["target_node_id"] in terminal_ids):
            continue
        destination = marked_nodes[route["target_node_id"]]
        contract = route.get("transition_contract") or {}
        claims = [*(contract.get("must_preserve") or []),
                  *((destination.get("character_state") or {}).get("continuity") or [])]
        claims = list({claim["ref"]: deepcopy(claim) for claim in claims}.values())
        if not claims:
            continue
        routes.append({
            "route_id": route["id"], "source_node_id": node_id, "target_node_id": route["target_node_id"],
            "route_field": f"/outgoing_routes/{route_index}",
            "terminal": route["target_node_id"] in terminal_ids, "claims": claims,
            "advance_on_completion": (raw_route.get("transition_contract") or {}).get("advance_on_completion") is True,
            "conditions": deepcopy(raw_route.get("conditions") or {}),
            "completion_contract": deepcopy(raw_source.get("completion_contract") or {}),
            "source_context": _entry(source), "target_entry": _entry(destination),
            "transition_context": {key: deepcopy(value) for key, value in contract.items() if key != "must_preserve"},
        })
    return {"source_node_id": node_id, "fact_definitions": definitions, "routes": routes,
            **{key: deepcopy(scoped[key]) for key in ("background", "author_intent") if key in scoped},
            "text_repair_fields": {key: deepcopy(marked_nodes[key].get("text_repair_fields", []))
                                   for key in sorted({node_id, *(route["target_node_id"] for route in routes)})}}


def validate_exit_bindings(request: Mapping[str, Any], payload: Any) -> list[dict[str, Any]]:
    """Reject incomplete mappings and Python's implicit bool/int equivalence."""
    if not isinstance(payload, Mapping) or set(payload) != {"bindings"} or not isinstance(payload["bindings"], list):
        raise ValueError("invalid_exit_bindings")
    expected = {(route["route_id"], claim["ref"]) for route in request["routes"] for claim in route["claims"]}
    definitions = {row["key"]: row for row in request["fact_definitions"]}
    seen = set()
    fields = {"route_id", "claim_ref", "kind", "fact_key", "value"}
    for row in payload["bindings"]:
        if not isinstance(row, Mapping) or set(row) != fields:
            raise ValueError("invalid_exit_binding")
        if not isinstance(row["route_id"], str) or not isinstance(row["claim_ref"], str):
            raise ValueError("invalid_exit_binding_ref")
        identity = (row["route_id"], row["claim_ref"])
        if identity not in expected or identity in seen:
            raise ValueError("invalid_exit_binding_ref")
        seen.add(identity)
        if row["kind"] not in ("source_history", "entry_delivery", "other"):
            raise ValueError("invalid_exit_binding_kind")
        if row["kind"] != "source_history":
            if row["fact_key"] is not None or row["value"] is not None:
                raise ValueError("invalid_exit_binding_fact")
            continue
        key = row["fact_key"]
        if not isinstance(key, str) or key not in definitions:
            raise ValueError("invalid_exit_binding_fact")
        if type(row["value"]) is not {"bool": bool, "int": int, "string": str}.get(definitions[key]["value_type"]):
            raise ValueError(f"invalid_exit_binding_value:{row['route_id']}:{row['claim_ref']}:"
                             f"expected {definitions[key]['value_type']}; "
                             "source_history必须有唯一确定的原值；可变结果或未知原值用other且fact_key/value均为null")
    if seen != expected:
        raise ValueError("incomplete_exit_bindings")
    return deepcopy(payload["bindings"])


def fact_is_guaranteed(route: Mapping[str, Any], key: str, value: bool | int | str) -> bool:
    """Prove sufficient equality requirements; failure to prove is not evidence of a defect."""
    def matches(row, field):
        return (row.get("key") == key and field in row
                and type(row[field]) is type(value) and row[field] == value)
    if route.get("advance_on_completion") is True and any(
        matches(row, "equals") for row in route.get("completion_contract", {}).get("all", [])
    ):
        return True
    conditions = route.get("conditions") or {}
    mode = "any" if "any" in conditions else "all"
    rows = conditions.get(mode) or []
    guarantees = [row.get("type") == "fact_equals" and matches(row, "value") for row in rows]
    return bool(guarantees) and (all(guarantees) if mode == "any" else any(guarantees))


def unproven_exit_candidates(
    request: Mapping[str, Any], bindings: list[dict[str, Any]], evidence_sources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Group validated bindings for independent evidence review; never emit an issue or repair."""
    routes = {route["route_id"]: route for route in request["routes"]}
    definitions = {row["key"]: row for row in request["fact_definitions"]}
    candidates = {}
    for row in bindings:
        route = routes[row["route_id"]]
        if (row["kind"] != "source_history" or route["terminal"]
                or route["advance_on_completion"] is not True
                or fact_is_guaranteed(route, row["fact_key"], row["value"])):
            continue
        key, value = row["fact_key"], row["value"]
        identity = (row["route_id"], key, type(value), value)
        if identity not in candidates:
            candidates[identity] = {"route_id": row["route_id"], "source_node_id": route["source_node_id"],
                                    "target_node_id": route["target_node_id"], "fact_key": key, "value": value,
                                    "claim_refs": [], "definition_ref": definitions[key]["description"]["ref"],
                                    "guaranteed": False}
        candidates[identity]["claim_refs"].append(row["claim_ref"])
    for candidate in candidates.values():
        candidate["evidence"] = [{"ref": ref, **deepcopy(evidence_sources[ref])}
                                 for ref in [*candidate["claim_refs"], candidate["definition_ref"]]]
    return list(candidates.values())


def validate_exit_candidate_checks(
    request: Mapping[str, Any], candidates: list[dict[str, Any]], payload: Any,
    evidence_sources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Require explicit candidate coverage and positive citation locations, not semantic truth."""
    def refs(value):
        if isinstance(value, Mapping):
            if (set(value) == {"ref", "text"} and isinstance(value["ref"], str)
                    and isinstance(value["text"], str)):
                return {value["ref"]}
            return set().union(*(refs(item) for item in value.values()))
        return set().union(*(refs(item) for item in value)) if isinstance(value, list) else set()
    def identity(row):
        if (not isinstance(row.get("route_id"), str) or not isinstance(row.get("fact_key"), str)
                or type(row.get("value")) not in (bool, int, str)):
            raise ValueError("invalid_exit_candidate_identity")
        return row["route_id"], row["fact_key"], type(row["value"]), row["value"]
    if (not isinstance(payload, Mapping) or set(payload) != {"checked_node_ids", "issues", "candidate_checks"}
            or payload["checked_node_ids"] != [request["source_node_id"]]
            or not isinstance(payload["issues"], list) or not isinstance(payload["candidate_checks"], list)):
        raise ValueError("invalid_exit_candidate_checks")
    expected = {identity(row): row for row in candidates}
    routes = {row["route_id"]: row for row in request["routes"]}
    visible = refs(request).intersection(evidence_sources)
    seen, gap_claims = set(), set()
    fields = {"route_id", "fact_key", "value", "status", "reason", "evidence_refs", "clear_basis"}
    for row in payload["candidate_checks"]:
        if not isinstance(row, Mapping) or set(row) != fields:
            raise ValueError("invalid_exit_candidate_check")
        key = identity(row)
        if key not in expected or key in seen:
            raise ValueError("invalid_exit_candidate_identity")
        seen.add(key)
        cited = row["evidence_refs"]
        if (row["status"] not in ("gap", "clear", "uncertain") or not isinstance(row["reason"], str)
                or not row["reason"].strip() or not isinstance(cited, list)
                or row["clear_basis"] is not None and not isinstance(row["clear_basis"], str)
                or any(not isinstance(ref, str) or ref not in visible for ref in cited)
                or len(cited) != len(set(cited))):
            raise ValueError("invalid_exit_candidate_evidence")
        cited, candidate = set(cited), expected[key]
        claims = set(candidate["claim_refs"])
        if row["status"] != "clear":
            if row["clear_basis"] is not None:
                raise ValueError("invalid_exit_clear_basis")
            if row["status"] == "gap" and not any(
                isinstance(issue, Mapping) and isinstance(issue.get("evidence_refs"), list)
                and all(isinstance(ref, str) and ref in visible for ref in issue["evidence_refs"])
                and len(set(issue["evidence_refs"])) >= 2
                and claims.intersection(issue["evidence_refs"]) for issue in payload["issues"]
            ):
                raise ValueError("exit_gap_requires_issue_claim")
            if row["status"] == "gap":
                gap_claims.update(claims)
            continue
        route = routes[row["route_id"]]
        source, target = route["source_context"], route["target_entry"]
        pieces = source.get("fixed_narrations") or []
        required = {item["fixed_narration_id"] for item in route["completion_contract"].get("all", [])
                    if "fixed_narration_id" in item}
        for piece in reversed(pieces):
            if piece.get("required_before_exit") is True or piece["id"] in required:
                required.add(piece["id"])
                required.update(item["text"] if isinstance(item, Mapping) else item for item in piece.get("after") or [])
        witnesses = {
            "source_opening": refs([source.get("opening_scene"), source.get("opening_fallback")]),
            "source_original": refs([piece.get("text") for piece in pieces if piece["id"] in required]),
            "background": refs([request.get("background"), request.get("author_intent")]),
            "entry_delivery": refs([target.get("opening_scene"), target.get("opening_fallback"),
                                    route["transition_context"].get("bridge_scene_narration")]),
        }
        basis = row["clear_basis"]
        if (basis == "binding_mismatch" and claims.intersection(cited) and candidate["definition_ref"] in cited
                or isinstance(basis, str) and cited.intersection(witnesses.get(basis, set()))):
            continue
        raise ValueError("exit_clear_requires_positive_witness")
    if seen != set(expected):
        raise ValueError("incomplete_exit_candidate_checks")
    if any(not isinstance(issue, Mapping) or not isinstance(issue.get("evidence_refs"), list)
           or any(not isinstance(ref, str) or ref not in visible for ref in issue["evidence_refs"])
           or not gap_claims.intersection(issue["evidence_refs"]) for issue in payload["issues"]):
        raise ValueError("exit_issue_requires_gap_check")
    return deepcopy(payload["candidate_checks"])
