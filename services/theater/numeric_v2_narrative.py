"""Reviewed narrative events and their deterministic, replayable current view."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping

from utils.tokenize import count_tokens

from .numeric_v2_performance import scene_narration_text


RECORD_SCHEMA = "numeric_v2_narrative_record_v2"
_LEGACY_RECORD_SCHEMA = "numeric_v2_narrative_record_v1"
STATE_SCHEMA = "numeric_v2_narrative_state_v1"
PHASES = ("opening", "player_input", "turn", "source_response", "transition_bridge", "target_opening")
ATTRIBUTES = {"position", "holder", "location", "condition", "stage_result", "unresolved",
              "choice", "commitment", "arrangement", "time", "environment"}
COVERAGE = {"confirmed_changes", "confirmed_no_change", "partial", "unrecorded", "unconfirmed"}
_REQUIRED = {"phase", "subject", "kind", "changes", "source", "quote", "known_to"}
_OPTIONAL = {"authorization_quote", "time", "reason", "summary"}


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _entity(value: Any) -> bool:
    if not _text(value, 80):
        return False
    if value in {"player", "character"}:
        return True
    prefix, separator, name = value.partition(":")
    return bool(separator and name.strip() and prefix in {
        "item", "person", "activity", "arrangement", "environment",
    })


def parse_narrative_events(value: Any) -> list[dict[str, Any]] | None:
    """Bad auxiliary data degrades without requesting another Actor response."""
    if not isinstance(value, list) or len(value) > 3:
        return None
    for event in value:
        if (not isinstance(event, dict) or not _REQUIRED <= set(event)
                or set(event) - _REQUIRED - _OPTIONAL
                or event["phase"] not in PHASES or not _entity(event["subject"])
                or not isinstance(event["kind"], str) or event["kind"] not in {"action", "speech"}
                or not isinstance(event["source"], str)
                or event["source"] not in {"player_input", "performance", "scene_narration"}
                or not _text(event["quote"], 160)):
            return None
        known = event["known_to"]
        if (not isinstance(known, list) or not 1 <= len(known) <= 2
                or any(not isinstance(item, str) or item not in {"player", "character"} for item in known)
                or len(set(known)) != len(known)):
            return None
        if any(not _text(event[key], 80 if key in {"time", "summary"} else 160 if key == "authorization_quote" else 120)
               for key in _OPTIONAL if key in event):
            return None
        changes = event["changes"]
        if not isinstance(changes, list) or not 1 <= len(changes) <= 3:
            return None
        pairs = set()
        for change in changes:
            if (not isinstance(change, dict) or set(change) != {"entity", "attribute", "value"}
                    or not _entity(change["entity"]) or not isinstance(change["attribute"], str)
                    or change["attribute"] not in ATTRIBUTES
                    or not _text(change["value"], 120)):
                return None
            if change["attribute"] == "holder" and (
                not change["entity"].startswith("item:")
                or not (change["value"] in {"none", "player", "character"}
                        or (change["value"].startswith("person:") and _entity(change["value"])))
            ):
                return None
            pair = (change["entity"], change["attribute"])
            if pair in pairs:
                return None
            pairs.add(pair)
    # An Actor-authored summary is auxiliary prose, not a second factual account.
    return [{key: deepcopy(data) for key, data in event.items() if key != "summary"} for event in value]


def _evidence_text(performance: Mapping[str, Any], event: Mapping[str, Any], message: str) -> str:
    phase, source = event["phase"], event["source"]
    if source == "player_input":
        return message if phase == "player_input" else ""
    if phase == "player_input":
        return ""
    if phase in {"opening", "turn"}:
        if performance.get("segments"):
            return ""
        return str(performance.get(source) or (
            performance.get("scene_update") if source == "scene_narration" else ""
        ) or "")
    segments = performance.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, Mapping) and segment.get("phase") == phase:
                return str(segment.get(source) or "")
        return ""
    key = {
        ("source_response", "performance"): "source_performance",
        ("source_response", "scene_narration"): "source_scene_narration",
        ("transition_bridge", "scene_narration"): "bridge_scene_narration",
        ("target_opening", "performance"): "target_performance",
        ("target_opening", "scene_narration"): "target_scene_narration",
    }.get((phase, source))
    return str(performance.get(key) or "") if key else ""


def _ordered_evidence_blocks(
    performance: Mapping[str, Any], phase: str, message: str,
) -> tuple[list[dict[str, str]], bool]:
    """Read current source blocks in display order, without rendering author templates."""

    container = None
    if phase in {"opening", "turn"} and not performance.get("segments"):
        container = performance
    elif isinstance(performance.get("segments"), list):
        container = next((row for row in performance["segments"]
                          if isinstance(row, Mapping) and row.get("phase") == phase), None)
    # 旧 content／紧凑别名没有在此容器展示固定片段；桥段本来禁止挂载固定原文。
    # 只读已捕获的 text，不查剧本 definitions、不按当前姓名重新渲染旧引文。
    fixed = (container.get("fixed_narrations", []) if isinstance(container, Mapping)
             and ("performance" in container or "scene_narration" in container)
             and phase != "transition_bridge" else [])
    fixed = [row for row in fixed if isinstance(row, Mapping)
             and isinstance(row.get("text"), str) and row["text"].strip()
             and row.get("position") in {"before", "after"}] if isinstance(fixed, list) else []
    before = [{"source": "scene_narration", "text": row["text"]} for row in fixed if row["position"] == "before"]
    after = [{"source": "scene_narration", "text": row["text"]} for row in fixed if row["position"] == "after"]
    # 新固定片段的顺序只按播放器可见字段取值；scene_update 的旧引用兼容留在原 reader，
    # 不能把混合新结构中未播放的旧别名当作新的现场来源。共用播放器的 before 回声去重。
    narration = (scene_narration_text(container) if fixed else
                 _evidence_text(performance, {"phase": phase, "source": "scene_narration"}, message))
    acting = _evidence_text(performance, {"phase": phase, "source": "performance"}, message)
    blocks = [{"source": "scene_narration", "text": narration}] if narration else []
    blocks.extend(before)
    if acting:
        blocks.append({"source": "performance", "text": acting})
    blocks.extend(after)
    return blocks, bool(fixed)


def _valid_evidence(
    event: Mapping[str, Any], performance: Mapping[str, Any], message: str, *, check_values: bool = True,
) -> bool:
    authorization = event.get("authorization_quote")
    # 保留旧字符串／空白／别名的引用语义；新增原文也必须完整落在同源单块中，不能拼接。
    source_matches = (event["quote"] in _evidence_text(performance, event, message)
                      or any(event["source"] == block["source"] and event["quote"] in block["text"]
                             for block in _ordered_evidence_blocks(performance, event["phase"], message)[0]))
    return (source_matches
            and (authorization is None or authorization in message)
            and (not check_values or all(change["attribute"] == "holder" or change["value"] in event["quote"]
                                         for change in event["changes"])))


def _bound_event(event: Mapping[str, Any], known: set[str]) -> bool:
    references = [event["subject"], *(change["entity"] for change in event["changes"])]
    references.extend(change["value"] for change in event["changes"] if change["attribute"] == "holder")
    return all(ref in {"player", "character", "none"} or ref in known
               or ref.partition(":")[2] in event["quote"] for ref in references)


def _allowed_phases(performance: Mapping[str, Any], opening: bool) -> set[str]:
    if opening:
        return {"opening"}
    if performance.get("segments") or "source_performance" in performance:
        return {"player_input", "source_response", "transition_bridge", "target_opening"}
    return {"player_input", "turn"}


def narrative_event_table(
    performance: Mapping[str, Any], message: str, *, opening: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Freeze the exact proposal table; invalid tables never mean 'no change'."""
    events = parse_narrative_events(performance.get("narrative_events"))
    if events is None or any(event["phase"] not in _allowed_phases(performance, opening)
                             or not _valid_evidence(event, performance, message, check_values=False) for event in events):
        raise ValueError("numeric_v2_narrative_candidates_invalid")
    # Sort only at application time: Review indexes always refer to Actor's table.
    return tuple(events)


def narrative_body_fingerprint(performance: Mapping[str, Any]) -> str:
    fields = {key: performance[key] for key in (
        "performance", "scene_narration", "scene_update", "segments", "fixed_narrations",
        "source_performance", "source_scene_narration", "bridge_scene_narration",
        "target_performance", "target_scene_narration",
    ) if key in performance}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _order_new_record_events(
    events: list[dict[str, Any]], performance: Mapping[str, Any], message: str,
) -> list[dict[str, Any]]:
    """Order newly approved fixed-text events only; stored record replay keeps its own order."""

    located = []
    for index, event in enumerate(events):
        blocks, has_fixed = _ordered_evidence_blocks(performance, event["phase"], message)
        if not has_fixed or event["source"] == "player_input":
            located.append(((PHASES.index(event["phase"]), index, 0), None, event))
            continue
        matches = [(order, block["text"]) for order, block in enumerate(blocks)
                   if block["source"] == event["source"] and event["quote"] in block["text"]]
        # 块间或块内重复引文都不能猜最早时点；丢整项，后续覆盖判定会保留缺口。
        if len(matches) != 1:
            continue
        order, text = matches[0]
        start = text.find(event["quote"])
        if start != text.rfind(event["quote"]):
            continue
        located.append(((PHASES.index(event["phase"]), order, start), start + len(event["quote"]), event))
    rejected = set()
    for left, (position, end, event) in enumerate(located):
        if end is None:
            continue
        changes = {(row["entity"], row["attribute"]): row["value"] for row in event["changes"]}
        for right in range(left + 1, len(located)):
            other_position, other_end, other = located[right]
            other_changes = {(row["entity"], row["attribute"]): row["value"] for row in other["changes"]}
            if (other_end is not None and position[:2] == other_position[:2]
                    and position[2] < other_end and other_position[2] < end
                    and any(pair in changes and changes[pair] != value for pair, value in other_changes.items())):
                # 同一属性的重叠引文无法证明先后，不能按模型列表顺序选择最终值。
                rejected.update((left, right))
    return [event for index, (_, _, event) in sorted(enumerate(located), key=lambda row: row[1][0])
            if index not in rejected]


def make_narrative_record(
    performance: Mapping[str, Any], message: str, review: Any = None, *, body_confirmed: bool = True,
    state: Mapping[str, Any] | None = None,
    opening: bool = False,
    preserved_player_events: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Bind approved proposals to the final delivered text, not a discarded draft."""
    proposed = parse_narrative_events(performance.get("narrative_events"))
    frozen = getattr(review, "reviewed_narrative_events", ()) if review is not None else ()
    indexes = getattr(review, "approved_narrative_event_indexes", ()) if review is not None else ()
    same_body = (review is not None and getattr(review, "reviewed_narrative_body", "")
                 == narrative_body_fingerprint(performance))
    candidates = []
    known = set(state["entities"]) if state is not None else set()
    valid_indexes = (isinstance(indexes, (list, tuple))
                     and all(type(index) is int and 0 <= index < len(frozen) for index in indexes)
                     and len(set(indexes)) == len(indexes))
    if valid_indexes:
        indexed = [(index, frozen[index]) for index in indexes
                   if parse_narrative_events([frozen[index]]) is not None]
        for _, event in sorted(indexed, key=lambda item: (PHASES.index(item[1]["phase"]), item[0])):
            if (event["phase"] in _allowed_phases(performance, opening)
                    and _valid_evidence(event, performance, message)
                    and (event["source"] == "player_input" or (body_confirmed and same_body))):
                candidates.append(deepcopy(event))
    # Earlier drafts may approve the immutable player input even when their body
    # is rejected. Never inherit body events or override the last player's verdict.
    inherited = []
    for item in preserved_player_events:
        parsed = parse_narrative_events([item])
        if (parsed is not None and parsed[0]["phase"] == "player_input"
                and parsed[0]["source"] == "player_input"
                and "player_input" in _allowed_phases(performance, opening)
                and _valid_evidence(parsed[0], performance, message)
                and parsed[0] not in inherited and parsed[0] not in candidates):
            inherited.append(parsed[0])
    last_player_pairs = {(change["entity"], change["attribute"])
                         for event in frozen if parse_narrative_events([event]) is not None
                         and event["phase"] == "player_input" and event["source"] == "player_input"
                         for change in event["changes"]}
    inherited_pairs = [{(change["entity"], change["attribute"]) for change in event["changes"]}
                       for event in inherited]
    inherited_added = inherited_gap = False
    for index, event in enumerate(inherited):
        pairs = inherited_pairs[index]
        if (pairs & last_player_pairs
                or any(pairs & other for order, other in enumerate(inherited_pairs) if order != index)
                or len(candidates) >= 3):
            inherited_gap = True
            continue
        candidates.append(deepcopy(event))
        inherited_added = True
    accepted = []
    # Bind after merging: an inherited player event can introduce an entity used
    # by a later approved body event, in the same order as deterministic replay.
    # 只在创建新记录时按实际片段定位排序，且先排序再绑定实体；后置原文不能为早先代词补身份。
    # 审批编号始终指原冻结表；apply_narrative_record 不重排旧 v1/v2 记录。
    for event in _order_new_record_events(candidates, performance, message):
        if _bound_event(event, known):
            accepted.append(event)
            known.update(change["entity"] for change in event["changes"])
    complete = (body_confirmed and same_body and proposed is not None and tuple(proposed) == tuple(frozen)
                and valid_indexes and len(accepted) == len(proposed)
                and not inherited_added and not inherited_gap
                and getattr(review, "narrative_review_complete", False) is True)
    if complete:
        coverage = "confirmed_changes" if accepted else "confirmed_no_change"
    elif accepted:
        coverage = "partial"
    elif proposed is None:
        coverage = "unrecorded"
    else:
        coverage = "unconfirmed"
    return {"schema": RECORD_SCHEMA, "coverage": coverage, "events": accepted,
            "proposal_count": max(len(proposed or ()), len(frozen), len(candidates))}


def _validate_record(record: Any) -> list[dict[str, Any]]:
    if (not isinstance(record, Mapping)
            or set(record) != {"schema", "coverage", "events", "proposal_count"}
            or record["schema"] not in (RECORD_SCHEMA, _LEGACY_RECORD_SCHEMA) or not isinstance(record["coverage"], str)
            or record["coverage"] not in COVERAGE
            or type(record["proposal_count"]) is not int or not 0 <= record["proposal_count"] <= 3):
        raise ValueError("numeric_v2_narrative_record_invalid")
    events = parse_narrative_events(record["events"])
    if events is None or len(events) > record["proposal_count"]:
        raise ValueError("numeric_v2_narrative_record_invalid")
    if ((record["coverage"] in {"unrecorded", "unconfirmed", "confirmed_no_change"} and events)
            or (record["coverage"] in {"confirmed_changes", "partial"} and not events)
            or (record["coverage"] == "confirmed_changes" and len(events) != record["proposal_count"])
            or (record["coverage"] == "confirmed_no_change" and record["proposal_count"] != 0)):
        raise ValueError("numeric_v2_narrative_record_invalid")
    return events


def validate_narrative_state(value: Any, *, revision: int | None = None) -> dict[str, Any]:
    if (not isinstance(value, Mapping)
            or set(value) != {"schema", "revision", "coverage_from", "coverage", "entities", "recent_events"}
            or value["schema"] != STATE_SCHEMA or type(value["revision"]) is not int
            or value["revision"] < 0 or (revision is not None and value["revision"] != revision)
            or type(value["coverage_from"]) is not int
            or not 0 <= value["coverage_from"] <= value["revision"]
            or not isinstance(value["coverage"], str) or value["coverage"] not in COVERAGE
            or not isinstance(value["entities"], Mapping)
            or not isinstance(value["recent_events"], list) or len(value["recent_events"]) > 8):
        raise ValueError("numeric_v2_narrative_state_invalid")
    for entity, attributes in value["entities"].items():
        if not _entity(entity) or not isinstance(attributes, Mapping):
            raise ValueError("numeric_v2_narrative_state_invalid")
        for attribute, data in attributes.items():
            if (attribute not in ATTRIBUTES or not isinstance(data, Mapping)
                    or set(data) != {"value", "source_event", "source_revision", "source_node_id", "quote", "known_to", "current"}
                    or not _text(data["value"], 120) or not _text(data["source_event"], 80)
                    or type(data["source_revision"]) is not int
                    or not 0 <= data["source_revision"] <= value["revision"]
                    or not _text(data["source_node_id"], 160)
                    or not _text(data["quote"], 160) or type(data["current"]) is not bool
                    or not isinstance(data["known_to"], list)
                    or not data["known_to"] or any(not isinstance(k, str) or k not in {"player", "character"}
                                                  for k in data["known_to"])):
                raise ValueError("numeric_v2_narrative_state_invalid")
    # Chain replay verifies the full event content against its source, including old values.
    for event in value["recent_events"]:
        if (not isinstance(event, Mapping) or not _text(event.get("event_id"), 80)
                or type(event.get("revision")) is not int or not 0 <= event["revision"] <= value["revision"]):
            raise ValueError("numeric_v2_narrative_state_invalid")
    return deepcopy(dict(value))


def apply_narrative_record(
    state: Mapping[str, Any] | None, record: Mapping[str, Any], *, revision: int,
    node_id: str, from_node_id: str, performance: Mapping[str, Any], message: str,
) -> dict[str, Any]:
    """Replay reviewed changes in phase order in the same Session/Ledger transaction."""
    events = _validate_record(record)
    if any(event["phase"] not in _allowed_phases(performance, revision == 0)
           or not _valid_evidence(event, performance, message) for event in events):
        raise ValueError("numeric_v2_narrative_evidence_invalid")
    result = validate_narrative_state(state) if state is not None else {
        "schema": STATE_SCHEMA, "revision": revision, "coverage_from": revision,
        "coverage": record["coverage"], "entities": {}, "recent_events": [],
    }
    if state is not None and result["revision"] != revision - 1:
        raise ValueError("numeric_v2_narrative_revision_invalid")
    complete = record["coverage"] in {"confirmed_changes", "confirmed_no_change"}
    if not complete or (record["schema"] == RECORD_SCHEMA
                        and result["coverage"] not in {"confirmed_changes", "confirmed_no_change"}):
        for attributes in result["entities"].values():
            for data in attributes.values():
                data["current"] = False
    if node_id != from_node_id:
        for attributes in result["entities"].values():
            for attribute in ("position", "location"):
                if attribute in attributes:
                    attributes[attribute]["current"] = False
    ordered = sorted(enumerate(events), key=lambda item: (PHASES.index(item[1]["phase"]), item[0]))
    for order, (_, event) in enumerate(ordered, 1):
        if not _bound_event(event, set(result["entities"])):
            raise ValueError("numeric_v2_narrative_entity_unbound")
        event_id = f"r{revision}.{event['phase']}.{order}"
        scene = from_node_id if event["phase"] in {"player_input", "source_response"} else node_id
        changes = []
        for change in event["changes"]:
            attributes = result["entities"].setdefault(change["entity"], {})
            previous = attributes.get(change["attribute"])
            changes.append({**deepcopy(change), "before": previous["value"] if previous and previous["current"] else None})
            attributes[change["attribute"]] = {
                "value": change["value"], "source_event": event_id, "source_revision": revision,
                "source_node_id": scene,
                "quote": event["quote"], "known_to": list(event["known_to"]),
                "current": not (change["attribute"] in {"position", "location"} and scene != node_id),
            }
        context = {"node_id": scene}
        if event["phase"] == "transition_bridge":
            context = {"from_node_id": from_node_id, "to_node_id": node_id}
        result["recent_events"].append({**deepcopy(event), "changes": changes,
            "event_id": event_id, "revision": revision, "order": order, "context": context})
    # Preserve the approved phase history without calling an intermediate value
    # the final state when later changes could not be recorded. Keep v1 replay exact.
    if record["schema"] == RECORD_SCHEMA and not complete:
        for attributes in result["entities"].values():
            for data in attributes.values():
                data["current"] = False
    result.update(revision=revision, coverage=record["coverage"])
    result["recent_events"] = result["recent_events"][-8:]
    return validate_narrative_state(result, revision=revision)


def project_narrative_state(session: Any, *, focus: str = "") -> dict[str, Any] | None:
    """Expose only character-known attributes; gaps do not become current facts."""
    state = getattr(session, "narrative_state", None)
    if state is None:
        return None
    state = validate_narrative_state(state)
    visible = {}
    for entity, attributes in state["entities"].items():
        for attribute, data in attributes.items():
            if "character" not in data["known_to"]:
                continue
            visible.setdefault(entity, {})[attribute] = data
    essential = {
        entity for entity, attributes in visible.items()
        if entity in {"player", "character"}
        or (entity.startswith("item:") and attributes.get("holder", {}).get("value") in {"player", "character"})
        or (entity.partition(":")[2] and entity.partition(":")[2] in focus)
    }
    metadata = {"retained_entities": len(essential), "omitted_entities": len(visible) - len(essential),
                "omitted_recent_events": 0, "quotes_omitted": 0, "over_budget": False,
                "omitted_essential_entities": 0}
    result = {"revision": state["revision"], "coverage_from": state["coverage_from"],
              "coverage": state["coverage"], "current_node_id": session.current_node_id,
              "current": {}, "last_known": {}, "recent_events": [], "projection": metadata}
    current_confirmed = state["coverage"] in {"confirmed_changes", "confirmed_no_change"}

    def add_entity(entity: str) -> None:
        for attribute, data in visible[entity].items():
            view = result["current"] if data["current"] and current_confirmed else result["last_known"]
            projected = {
                "value": data["value"], "source_event": data["source_event"], "quote": data["quote"],
            }
            if metadata["quotes_omitted"]:
                projected.pop("quote")
            view.setdefault(entity, {})[attribute] = projected

    def tokens() -> int:
        return count_tokens(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    for entity in sorted(essential):
        add_entity(entity)
    # Quotes repeat the immutable Ledger source; remove repetition first.
    if tokens() > 1600:
        for view in (result["current"], result["last_known"]):
            for attributes in view.values():
                for data in attributes.values():
                    data.pop("quote")
                    metadata["quotes_omitted"] += 1
    if tokens() > 1600:
        # The full ledger remains intact. Explicitly report overflow rather than
        # making every later turn fail its fixed input budget.
        result["current"].clear()
        result["last_known"].clear()
        metadata.update(retained_entities=0, omitted_entities=len(visible),
                        omitted_essential_entities=len(essential))
        prioritized = sorted(essential, key=lambda entity: (
            entity in {"player", "character"},
            bool(entity.partition(":")[2] and entity.partition(":")[2] in focus),
            max(data["source_revision"] for data in visible[entity].values()), entity,
        ), reverse=True)
        def retain_prefix(count: int) -> None:
            result["current"].clear()
            result["last_known"].clear()
            for entity in prioritized[:count]:
                add_entity(entity)
            metadata.update(retained_entities=count, omitted_entities=len(visible) - count,
                            omitted_essential_entities=len(essential) - count)

        def fit_prefix() -> int:
            # Search priority prefixes without retokenizing for every held item.
            low, high = 0, len(prioritized)
            while low < high:
                middle = (low + high + 1) // 2
                retain_prefix(middle)
                if tokens() <= 1568:
                    low = middle
                else:
                    high = middle - 1
            retain_prefix(low)
            return low

        count = fit_prefix()
        retained = prioritized[:count]
        remaining = 1568 - tokens()
        # A large entity must not hide smaller entities after it. Tokenize only
        # each new fragment, rather than the whole retained projection per item.
        for entity in prioritized[count:]:
            add_entity(entity)
            cost = 2 + sum(count_tokens(json.dumps(
                {entity: result[view][entity]}, ensure_ascii=False, separators=(",", ":"),
            )) for view in ("current", "last_known") if entity in result[view])
            if cost <= remaining:
                retained.append(entity)
                remaining -= cost
            else:
                result["current"].pop(entity, None)
                result["last_known"].pop(entity, None)
        if len(retained) > count:
            prioritized = retained
            retain_prefix(len(retained))
            # Fragment sums are estimates; validate the actual assembled text.
            if tokens() > 1568:
                fit_prefix()
    optional = sorted(set(visible) - essential, key=lambda entity: (
        any(data["source_node_id"] == session.current_node_id for data in visible[entity].values()),
        max(data["source_revision"] for data in visible[entity].values()), entity,
    ), reverse=True)
    for entity in optional[:16]:
        add_entity(entity)
        if tokens() > 1600:
            result["current"].pop(entity, None)
            result["last_known"].pop(entity, None)
        else:
            metadata["retained_entities"] += 1
            metadata["omitted_entities"] -= 1
    events = [event for event in state["recent_events"] if "character" in event["known_to"]]
    metadata["omitted_recent_events"] = len(events)
    for event in reversed(events[-4:]):
        # Before-values can have narrower visibility than the new event.
        item = {key: deepcopy(event[key]) for key in (
            "event_id", "revision", "phase", "subject", "kind", "summary", "source", "quote",
            "context", "time", "reason",
        ) if key in event}
        result["recent_events"].insert(0, item)
        if tokens() > 1600:
            result["recent_events"].pop(0)
        else:
            metadata["omitted_recent_events"] -= 1
    return result
