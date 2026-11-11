"""Bound, unapproved completion discoveries and their final-draft evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .numeric_v2_performance import performance_fingerprint
from .numeric_v2_runtime import NumericV2RuntimeError, _FACT_CANDIDATE_MAX_ITEMS, validate_fact_candidates


DISCOVERY_RULE = (
    "\n在原审核字段之外，最后独立填写fact_discoveries。只从completion_check核对遗漏的完成事实，不读取或相信演员的候选名单。"
    "targets是作者待完成目标，不证明已经发生；每项只返回target_index和refs，无法充分证明则不返回该项。"
    "refs仅选completion_check.sources实际列出的ref，每个ref绑定一个完整原始字段；可选最多四条分别支持同一目标的不同条件，"
    "但必须合起来满足description的全部主体、对象、条件、时点和结果。查看完整原文的否定、未知、后续撤回与尚缺部分。"
    "目标要求实际交付、执行或完整体验时，请求、答应、准备、局部体验、开始执行或试演不证明对应结果已完成；不以结果自称完成代替必要前因。"
    "目标只要求提出条款或形成约定时，实际说清条款或各方已就同一事项表达同意即可；同意是语义回应，不要求执行约定中的未来操作。"
    "玩家明确请求可证明其提出的事项与范围，不能代替角色同意；能力说明、反问或尚未满足条件的意向不自动证明约定成立。"
    "玩家自己的主动动作或决定只由来源为player_input的当前原话证明；旁白、角色复述和玩家指挥别人不授权替玩家执行。"
    "客观展示按实际交付核对；玩家的主观理解或反馈只由玩家原话证明，不从展示、在场或角色评价推定。"
    "作者计划、原sources表的a/d/s/h编号、推荐输入、未实际给出的详情不能充当补查原文；只用completion_check.sources。"
    "保留evidence_source与evidence_anchor约束，不由相似词语、不同对象或不同时点的无关事件补足条件。"
    "事实发现不授予换幕，也不豁免正文违规；先完整填写原审核字段，不因发现目标完成而清除原本的违规。"
    "target.allowed_sources若存在，只能选择该来源类型的refs；完整玩家原话仍用于辨明授权，不能代替该目标要求的实际交付。"
)
OLD_SCOPE = "actor_evidence是本轮可见演出。引文来源为actor_performance时，有本轮演出则指该稿，否则指scene_context的已演原文。"
NEW_SCOPE = (
    "每条引文的原始来源由evidence_bindings逐条绑定，evidence_source_table给出对应完整原文。"
    "actor_performance仅是演出来源类型；其中committed_current_visit是本次到访已提交历史，current_draft是本轮尚未提交的最终稿，不能互换时点。"
    "含components的来源是按原顺序分隔的历史原文集合，分别核对各段，不能把拼接读成连续的新动作。"
    "player_input仅是当前玩家原话。历史与当前稿可以分别证明同一候选的不同条件，但不得把别的主体、对象或无关事件补作缺失条件。"
)
APPROVAL_RULE = (
    "\n所有claims均为待审核主张，没有任何一项已由发现器或演员批准。"
    "不依据候选的subject/action/result标签或自称完成作判断；每一项描述的全部条件必须由其evidence原文共同支持。"
    "完整来源只用于核对引文的实际语境、主体和后续否定，不得用未引用的其他来源偷偷补足缺失条件。"
    "材料已到、双方同意、开始执行和实际完成分别核对；目标只要求提出条款或形成约定时，条款已说清或各方同意本身就是该目标的交付，不额外要求未来工作已完成。"
    "目标要求实际交付或完成的结果仍仅被请求、承诺或准备，或其他必要条件尚缺、给了别的对象、已撤回时，不批准。"
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def build_packet(
    engine: Any, session: Any, performance: Mapping[str, Any], player_input: str, *,
    pending_targets: Sequence[Mapping[str, Any]], history_metadata: Mapping[str, Any],
    history_originals: Sequence[str],
    restrict_player_results: bool = False,
) -> dict[str, Any]:
    """Use the caller's retained history whitelist; author plans are never sources."""
    required = {row["key"]: row["equals"]
                for row in engine.nodes[session.current_node_id].get("completion_contract", {}).get("all", ())
                if isinstance(row.get("key"), str)}
    facts = session.story_state["facts"]
    targets = []
    seen = set()
    for row in pending_targets:
        key = row["key"]
        if (key in required and key not in facts and key not in seen
                and type(row["value"]) is type(required[key]) and row["value"] == required[key]):
            definition = deepcopy(engine.fact_contract[row["key"]])
            targets.append({**deepcopy(dict(row)), "definition": definition,
                            **({"evidence_source": definition["evidence_source"]} if "evidence_source" in definition else {}),
                            **({"allowed_sources": ["player_input" if definition.get("evidence_source") == "player_input"
                                                    else "actor_performance"]} if restrict_player_results else {})})
            seen.add(key)
    sources = []
    if player_input:
        sources.append({"ref": "e0", "source": "player_input", "kind": "current_player", "text": player_input})
    for text in history_originals:
        sources.append({"ref": f"e{len(sources)}", "source": "actor_performance",
                        "kind": "committed_current_visit", "text": text})
    if not performance.get("segments"):
        for field in ("scene_narration", "performance"):
            text = performance.get(field)
            if isinstance(text, str) and text.strip():
                sources.append({"ref": f"e{len(sources)}", "source": "actor_performance",
                                "kind": "current_draft", "field": field, "text": text})
    return {"targets": targets, "sources": sources, "binding": {
        "session_id": session.session_id, "revision": session.revision, "node_id": session.current_node_id,
        "package_hash": session.story_package_hash, "facts_digest": _digest(facts),
        "targets_digest": _digest(targets), "body_fingerprint": performance_fingerprint(performance),
        "sources_digest": _digest(sources), "player_input_digest": _digest(player_input),
    }, "history_allowed_keys": list(history_metadata.get("allowed_keys", ()))}


def _packet_intact(packet: Mapping[str, Any]) -> bool:
    try:
        return (isinstance(packet["binding"], Mapping)
                and _digest(packet["targets"]) == packet["binding"]["targets_digest"]
                and _digest(packet["sources"]) == packet["binding"]["sources_digest"])
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class CompletionFactBatch:
    packet: dict[str, Any]
    claims: tuple[dict[str, Any], ...]
    bindings: tuple[tuple[str, ...], ...]
    errors: tuple[str, ...] = ()

    @property
    def operations(self) -> tuple[dict[str, Any], ...]:
        return tuple({key: deepcopy(claim[key]) for key in ("op", "key", "value", "visibility")}
                     for claim in self.claims)

    def select(self, indexes: Sequence[int]) -> CompletionFactBatch:
        indexes = tuple(indexes)
        if (any(type(index) is not int or not 0 <= index < len(self.claims) for index in indexes)
                or len(set(indexes)) != len(indexes)):
            raise NumericV2RuntimeError("numeric_v2_fact_discovery_indexes_invalid")
        return CompletionFactBatch(deepcopy(self.packet), tuple(deepcopy(self.claims[index]) for index in indexes),
                                   tuple(self.bindings[index] for index in indexes), self.errors)

    def validate(self) -> None:
        """Check server values and quote provenance, independently of model approval."""
        if (not _packet_intact(self.packet) or len(self.claims) != len(self.bindings)
                or len(self.claims) > _FACT_CANDIDATE_MAX_ITEMS):
            raise NumericV2RuntimeError("numeric_v2_fact_discovery_batch_invalid")
        targets = {row["key"]: row for row in self.packet["targets"]}
        sources = {row["ref"]: row for row in self.packet["sources"]}
        seen = set()
        for claim, refs in zip(self.claims, self.bindings):
            if (not isinstance(claim, Mapping) or set(claim) != {
                    "op", "key", "value", "visibility", "description",
                    "subject", "action", "object", "result", "evidence"}
                    or not isinstance(claim.get("key"), str)):
                raise NumericV2RuntimeError("numeric_v2_fact_discovery_claim_changed")
            key = claim.get("key")
            target = targets.get(key)
            if (target is None or key in seen or claim.get("op") != "set"
                    or type(claim.get("value")) is not type(target["value"]) or claim["value"] != target["value"]
                    or claim.get("visibility") != target["definition"]["visibility"]
                    or claim.get("description") != target["description"]):
                raise NumericV2RuntimeError("numeric_v2_fact_discovery_claim_changed")
            evidence = claim.get("evidence")
            if (not isinstance(refs, (tuple, list)) or any(not isinstance(ref, str) for ref in refs)
                    or not isinstance(evidence, list) or not 1 <= len(refs) <= 4 or len(set(refs)) != len(refs)
                    or len(evidence) != len(refs)):
                raise NumericV2RuntimeError("numeric_v2_fact_discovery_binding_invalid")
            for row, ref in zip(evidence, refs):
                source = sources.get(ref)
                if (source is None or not isinstance(row, Mapping) or set(row) != {"source", "quote"}
                        or row.get("source") != source["source"]
                        or source["source"] not in target.get("allowed_sources", ("player_input", "actor_performance"))
                        or not isinstance(row.get("quote"), str) or not row["quote"]
                        or row["quote"] not in source["text"]
                        or source["kind"] == "committed_current_visit" and key not in self.packet["history_allowed_keys"]):
                    raise NumericV2RuntimeError("numeric_v2_fact_discovery_binding_invalid")
            validate_fact_candidates([{
                **{field: claim[field] for field in ("op", "key", "value", "visibility", "subject", "action", "object", "result", "evidence")},
                "confidence": "confirmed",
            }], fact_contract={"facts": {key: target["definition"]}},
                evidence_sources={name: "\n".join(row["text"] for row in self.packet["sources"] if row["source"] == name)
                                  for name in ("player_input", "actor_performance")}, defer_actor_completion_review=True)
            seen.add(key)


def bind_source_delivery(
    engine: Any, claims: Sequence[Mapping[str, Any]], packet: dict[str, Any],
    *, restrict_player_results: bool = False,
) -> CompletionFactBatch:
    """Keep original historical citations distinct from a newly delivered source body."""
    if not _packet_intact(packet):
        return CompletionFactBatch(deepcopy(packet), (), (), ("packet_changed",))
    packet = deepcopy(packet)
    current = "\n".join(row["text"] for row in packet["sources"] if row["kind"] == "current_draft")
    packet["sources"] = [row for row in packet["sources"] if row["kind"] != "current_draft"]
    draft = {"ref": "delivery_source", "source": "actor_performance", "kind": "current_draft",
             "field": "source_delivery", "text": current}
    if current:
        packet["sources"].append(draft)
    targets, bound, bindings, errors = [], [], [], []
    allowed = engine.scene_fact_targets(packet["binding"]["node_id"])
    for claim in claims:
        if not current or not any(row["key"] == claim["key"] and type(row["value"]) is type(claim["value"])
                                  and row["value"] == claim["value"] for row in allowed):
            errors.append("delivery_target_not_issued")
            continue
        definition = deepcopy(engine.fact_contract[claim["key"]])
        target = {"key": claim["key"], "value": deepcopy(claim["value"]),
                  "description": claim["description"], "definition": definition}
        if restrict_player_results:
            target["allowed_sources"] = ["player_input" if definition.get("evidence_source") == "player_input"
                                         else "actor_performance"]
        refs, evidence = [], []
        missing = False
        for row in claim["evidence"]:
            if row["source"] == "player_input" and restrict_player_results:
                continue
            matches = [source for source in packet["sources"]
                       if source["kind"] == ("current_player" if row["source"] == "player_input"
                                             else "committed_current_visit")
                       and not source.get("components")
                       and row["quote"] in source["text"]]
            if not matches:
                missing = True
                break
            original = matches[0]
            quote = original["text"] if original["kind"] == "current_player" else row["quote"]
            if original["ref"] in refs:
                evidence[refs.index(original["ref"])]["quote"] = original["text"]
            else:
                refs.append(original["ref"])
                evidence.append({"source": row["source"], "quote": quote})
        if missing:
            errors.append("delivery_history_evidence_missing")
            continue
        refs.append(draft["ref"])
        evidence.append({"source": "actor_performance", "quote": current})
        if len(refs) > 4:
            originals = [row for row in packet["sources"]
                         if row["kind"] == "committed_current_visit" and row["ref"] in refs]
            combined = {"ref": f"delivery_history_{len(bound)}", "source": "actor_performance",
                        "kind": "committed_current_visit", "components": [row["ref"] for row in originals],
                        "text": "\n\n".join(row["text"] for row in originals)}
            packet["sources"].append(combined)
            retained = [(ref, row) for ref, row in zip(refs, evidence) if ref not in combined["components"]]
            refs = [ref for ref, _ in retained] + [combined["ref"]]
            evidence = [row for _, row in retained] + [{"source": "actor_performance", "quote": combined["text"]}]
        targets.append(target)
        bound.append({**deepcopy(dict(claim)), "evidence": evidence})
        bindings.append(tuple(refs))
    packet["targets"] = targets
    packet["binding"]["targets_digest"] = _digest(targets)
    packet["binding"]["sources_digest"] = _digest(packet["sources"])
    valid, valid_refs = [], []
    for claim, refs in zip(bound, bindings):
        candidate = CompletionFactBatch(packet, (claim,), (refs,))
        try:
            candidate.validate()
        except NumericV2RuntimeError as exc:
            errors.append(str(exc))
            continue
        valid.append(claim)
        valid_refs.append(refs)
    return CompletionFactBatch(packet, tuple(valid), tuple(valid_refs), tuple(errors))


def combine_actor_claims(
    engine: Any, batch: CompletionFactBatch, claims: Sequence[Mapping[str, Any]],
) -> CompletionFactBatch:
    """Bind existing Actor candidates and omitted discoveries for one independent review."""
    batch.validate()
    packet = deepcopy(batch.packet)
    allowed = engine.scene_fact_targets(packet["binding"]["node_id"])
    bindings = []
    targets = {row["key"]: row for row in packet["targets"]}
    for claim in claims:
        key = claim.get("key")
        if (not any(row["key"] == key and type(row["value"]) is type(claim.get("value"))
                    and row["value"] == claim["value"] for row in allowed)
                or claim.get("description") != engine.fact_contract[key]["description"]):
            raise NumericV2RuntimeError("numeric_v2_fact_merge_target_not_issued")
        if key not in targets:
            definition = deepcopy(engine.fact_contract[key])
            target = {"key": key, "value": deepcopy(claim["value"]),
                      "description": definition["description"], "definition": definition}
            packet["targets"].append(target)
            targets[key] = target
        refs = []
        for evidence in claim["evidence"]:
            kind = "current_player" if evidence["source"] == "player_input" else "current_draft"
            originals = [row for row in packet["sources"]
                         if row["kind"] == kind and row["source"] == evidence["source"]
                         and evidence["quote"] in row["text"]]
            if not originals:
                raise NumericV2RuntimeError("numeric_v2_fact_merge_original_missing")
            refs.append(originals[0]["ref"])
        bindings.append(tuple(refs))
    packet["binding"]["targets_digest"] = _digest(packet["targets"])
    merged = CompletionFactBatch(packet, (*deepcopy(tuple(claims)), *batch.claims),
                                 (*bindings, *batch.bindings), batch.errors)
    merged.validate()
    return merged


def parse_discoveries(items: Any, packet: dict[str, Any]) -> CompletionFactBatch:
    """Restore typed claims from issued references; malformed items cannot add facts."""
    errors, claims, bindings = [], [], []
    if not _packet_intact(packet):
        return CompletionFactBatch(deepcopy(packet), (), (), ("packet_changed",))
    if not isinstance(items, list) or len(items) > 4:
        return CompletionFactBatch(deepcopy(packet), (), (), ("discovery_shape",))
    targets = packet["targets"]
    sources = {row["ref"]: row for row in packet["sources"]}
    counts: dict[str, int] = {}
    for item in items:
        index = item.get("target_index") if isinstance(item, dict) else None
        if type(index) is int and 0 <= index < len(targets):
            key = targets[index]["key"]
            counts[key] = counts.get(key, 0) + 1
    for item in items:
        if not isinstance(item, dict) or set(item) != {"target_index", "refs"}:
            errors.append("discovery_item_shape")
            continue
        index, refs = item["target_index"], item["refs"]
        if type(index) is not int or not 0 <= index < len(targets):
            errors.append("target_not_issued")
            continue
        if counts[targets[index]["key"]] != 1:
            errors.append("duplicate_target_key")
            continue
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 4
                or any(not isinstance(ref, str) or ref not in sources for ref in refs)
                or len(set(refs)) != len(refs)):
            errors.append("ref_not_issued_or_duplicate")
            continue
        target = targets[index]
        claim = {"op": "set", "key": target["key"], "value": deepcopy(target["value"]),
                 "visibility": target["definition"]["visibility"], "description": target["description"],
                 "subject": "已核对的实际主体", "action": "按原文交付或执行", "object": target["key"],
                 "result": "目标描述中的全部条件已具备",
                 "evidence": [{"source": sources[ref]["source"], "quote": sources[ref]["text"]} for ref in refs]}
        candidate = CompletionFactBatch(packet, (claim,), (tuple(refs),))
        try:
            candidate.validate()
        except NumericV2RuntimeError as exc:
            errors.append(str(exc))
            continue
        claims.append(claim)
        bindings.append(tuple(refs))
    return CompletionFactBatch(deepcopy(packet), tuple(claims), tuple(bindings), tuple(errors))


def matches(batch_or_packet: CompletionFactBatch | Mapping[str, Any], fresh_packet: Mapping[str, Any]) -> bool:
    if isinstance(batch_or_packet, CompletionFactBatch):
        try:
            batch_or_packet.validate()
        except NumericV2RuntimeError:
            return False
    packet = batch_or_packet.packet if isinstance(batch_or_packet, CompletionFactBatch) else batch_or_packet
    return _packet_intact(packet) and _packet_intact(fresh_packet) and packet["binding"] == fresh_packet["binding"]


def rebind_final(batch: CompletionFactBatch, fresh_packet: dict[str, Any]) -> CompletionFactBatch:
    """Recheck the same original claims in the final draft, without discovering more."""
    batch.validate()
    if (not _packet_intact(fresh_packet) or any(value != fresh_packet["binding"].get(field)
            for field, value in batch.packet["binding"].items() if field not in {"body_fingerprint", "sources_digest"})):
        raise NumericV2RuntimeError("numeric_v2_fact_discovery_context_changed")
    sources = {row["ref"]: row for row in batch.packet["sources"]}
    bindings = []
    for claim, refs in zip(batch.claims, batch.bindings):
        rebound = []
        for evidence, ref in zip(claim["evidence"], refs):
            original = sources[ref]
            found = [row["ref"] for row in fresh_packet["sources"]
                     if row["source"] == original["source"] and row["kind"] == original["kind"]
                     and row.get("field") == original.get("field") and evidence["quote"] in row["text"]
                     and (row["kind"] == "current_draft" or row["text"] == original["text"])]
            if len(found) != 1:
                raise NumericV2RuntimeError("numeric_v2_fact_discovery_final_evidence_missing")
            rebound.append(found[0])
        bindings.append(tuple(rebound))
    result = CompletionFactBatch(deepcopy(fresh_packet), deepcopy(batch.claims), tuple(bindings), batch.errors)
    result.validate()
    return result
