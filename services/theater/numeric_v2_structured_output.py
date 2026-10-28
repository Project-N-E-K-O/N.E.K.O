"""API shape constraints for theater output; semantic and quote checks remain separate."""

from typing import Any, Mapping
from urllib.parse import urlsplit


def _object(properties: dict[str, Any], *, optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional], "additionalProperties": False}


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _fact_candidates() -> dict[str, Any]:
    return _array(_object({
        "key": {"type": "string"},
        "value": {"anyOf": [{"type": kind} for kind in ("boolean", "integer", "string")]},
        "evidence_quote": {"type": "string"},
    }))


def _narrative_events(phases: tuple[str, ...]) -> dict[str, Any]:
    entities = {"type": "string", "minLength": 1, "maxLength": 80,
                "anyOf": [{"enum": ["player", "character"]}, {
                    "pattern": r"^(item|person|activity|arrangement|environment):[\s\S]*\S[\s\S]*$",
                }]}
    events = _array(_object({
        "phase": {"type": "string", "enum": list(phases)},
        "subject": entities,
        "kind": {"type": "string", "enum": ["action", "speech"]},
        "changes": {**_array({"anyOf": [
            _object({
                "entity": entities,
                "attribute": {"type": "string", "enum": [
                    "position", "location", "condition", "stage_result", "unresolved", "choice",
                    "commitment", "arrangement", "time", "environment",
                ]},
                "value": {"type": "string", "minLength": 1, "maxLength": 120},
            }),
            _object({
                "entity": {"type": "string", "minLength": 1, "maxLength": 80,
                           "pattern": r"^item:[\s\S]*\S[\s\S]*$"},
                "attribute": {"type": "string", "enum": ["holder"]},
                "value": {"type": "string", "minLength": 1, "maxLength": 80,
                          "anyOf": [{"enum": ["none", "player", "character"]}, {
                              "pattern": r"^person:[\s\S]*\S[\s\S]*$",
                          }]},
            }),
        ]}), "minItems": 1, "maxItems": 3},
        "source": {"type": "string", "enum": ["player_input", "performance", "scene_narration"]},
        "quote": {"type": "string", "minLength": 1, "maxLength": 160},
        # DashScope rejects uniqueItems on arrays; the local parser checks it.
        "known_to": {**_array({"type": "string", "enum": ["player", "character"]}),
                     "minItems": 1, "maxItems": 2},
        "authorization_quote": {"type": "string", "minLength": 1, "maxLength": 160},
        "time": {"type": "string", "minLength": 1, "maxLength": 80},
        "reason": {"type": "string", "minLength": 1, "maxLength": 120},
    }, optional=("authorization_quote", "time", "reason")))
    return {**events, "maxItems": 3}


def response_format_for(config: Mapping[str, Any], name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Enable this format only for verified compatible Aliyun endpoints."""
    model = str(config.get("model") or "").lower()
    endpoint = urlsplit(str(config.get("base_url") or ""))
    host = endpoint.hostname or ""
    if (config.get("provider_type") == "anthropic"
            or endpoint.scheme != "https"
            or not endpoint.path.rstrip("/").endswith("/compatible-mode/v1")
            or not (host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"}
                    or host.endswith(".maas.aliyuncs.com"))
            or not any(model == base or model.startswith(base + "-") for base in ("qwen3.8-flash", "qwen3.8-omni-flash", "qwen3.8-max"))):
        return None
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def actor_output_schema(*, opening_required: bool = False, transition_required: bool = False,
                        suggestions_only: bool = False, transition_suggestions_only: bool = False,
                        fact_candidates_expected: bool = False,
                        reinvitation_only: bool = False,
                        single_ending_response: bool = False,
                        fixed_entry_only: bool = False,
                        combined_metric_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Declare fields already used by each prompt; scene_update remains optional."""
    suggestions = _array({"type": "string"})
    if transition_suggestions_only:
        return _object({"accept_input": {"type": "string"}, "alternative_inputs": suggestions})
    if suggestions_only:
        return _object({"suggested_inputs": suggestions})
    if reinvitation_only:
        # 重邀仅发布正文提议；接受动作由程序控件承担，模型不再代写玩家的接受输入。
        return _object({"performance": {"type": "string"},
                        "transition_offered": {"type": "boolean"}})
    if transition_required:
        if fixed_entry_only:
            # Author-marked route: the program plays the complete target entry text.
            # Only source narration/performance and the bridge carry model text.
            return _object({
                "source_scene_narration": {"type": "string"},
                "source_performance": {"type": "string"},
                "bridge_scene_narration": {"type": "string"},
                "target_scene_narration": {"type": "string", "enum": [""]},
                "target_performance": {"type": "string", "enum": [""]},
                "suggested_inputs": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
            })
        if single_ending_response:
            return _object({
                **{key: {"type": "string"} for key in (
                    "source_scene_narration", "performance", "bridge_scene_narration", "target_scene_narration")},
                "response_phase": {"type": "string", "enum": ["source_response", "target_opening", "none"]},
                "suggested_inputs": suggestions,
            })
        return _object({
            **{key: {"type": "string"} for key in (
                "source_scene_narration", "source_performance", "bridge_scene_narration",
                "target_scene_narration", "target_performance",
            )},
            "suggested_inputs": suggestions,
        })
    # Independent external events/NPC replies precede the character's reaction in playback.
    properties = {"scene_narration" if opening_required else "scene_update": {"type": "string"}}
    properties["performance"] = {"type": "string"}
    if opening_required:
        properties.update(suggested_inputs=suggestions, transition_offered={"type": "boolean"})
    else:
        properties.update(transition_offer={"type": "string"}, suggested_inputs=suggestions)
    if not opening_required and fact_candidates_expected:
        properties["fact_candidates"] = _fact_candidates()
    if combined_metric_ids is not None:
        changes = {key: _object({
            "strength": {"type": "string", "enum": ["weak", "normal", "strong", "decisive"]},
            "criterion_id": {"type": "string"},
        }) for key in combined_metric_ids}
        properties["turn_evaluation"] = _object({
            "scene_complete": {"type": "boolean"},
            "metric_changes": _object(changes, optional=combined_metric_ids),
            "fact_candidates": _fact_candidates(),
        })
    return _object(properties, optional=("scene_update",))


def narrative_output_schema(*, opening: bool = False, transition: bool = False,
                            source_ids: tuple[str, ...] | None = None,
                            entity_refs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Keep the existing event contract separate from Actor's public output."""
    phases = (("player_input", "source_response", "transition_bridge", "target_opening") if transition
              else ("opening",) if opening else ("player_input", "turn"))
    events = _narrative_events(phases)
    if source_ids is not None:
        # 只改变提案模型的传输格式；Review、已存事件及旧 Schema 继续使用 phase/source。
        # 编号来自本次请求的来源表，模型不能再独立填写阶段或来源覆盖这个绑定。
        properties = events["items"]["properties"]
        references = dict(entity_refs or {"player": "player", "character": "character"})

        def reference_schema(*, item_only: bool = False, holder: bool = False) -> dict[str, Any]:
            # 已知身份选编号；新实体由模型明确选类型和原文名，程序不抽取名词或猜别名。
            ids = list(references)
            if item_only:
                ids = [key for key in ids if references[key].startswith("item:")]
            elif holder:
                ids = [key for key in ids if references[key] in {"player", "character"}
                       or references[key].startswith("person:")]
            types = (["person"] if holder else ["item"] if item_only else
                     ["item", "person", "activity", "arrangement", "environment"])
            branches = [_object({"type": {"type": "string", "enum": types},
                                 "name": {"type": "string", "minLength": 1, "maxLength": 80}})]
            if ids or holder:
                branches.insert(0, _object({"ref": {"type": "string", "enum": ids + (["none"] if holder else [])}}))
            return {"anyOf": branches}

        changes = properties["changes"]["items"]["anyOf"]
        changes[0]["properties"]["entity"] = reference_schema()
        changes[1]["properties"]["entity"] = reference_schema(item_only=True)
        changes[1]["properties"]["value"] = reference_schema(holder=True)
        events["items"] = _object({
            "source_id": {"type": "string", "enum": list(source_ids)},
            "subject": reference_schema(),
            **{key: value for key, value in properties.items() if key not in {"phase", "source", "subject"}},
        }, optional=("authorization_quote", "time", "reason"))
    return _object({"narrative_events": events})


def review_output_schema(*, formal: bool = False, transition_intent: str = "",
                         confirmed_acceptance: bool = False, missed_initiation: bool = False,
                         fixed_narrations: bool = False, display_suggestions: bool = False,
                         allow_after_display: bool = False,
                         completion_facts: bool = False, evaluator_facts: bool = False, actor_facts: bool = False,
                         completion_fact_review_only: bool = False,
                         locate_body_issues: bool = False, narrative_events: bool = False) -> dict[str, Any]:
    """Match each review prompt branch; the parser still verifies evidence."""
    violations = _array({"type": "string", "enum": ["player_action", "scene_boundary", "author_boundary"]})
    properties: dict[str, Any] = {}
    if missed_initiation and not formal:
        properties.update(player_request_quote={"type": "string"}, missed_initiation={"type": "boolean"},
                          public_destination_index={"type": "integer"})
    if formal and transition_intent == "initiate":
        properties.update(public_destination_quote={"type": "string"}, initiation_authorized={"type": "boolean"})
    if formal and transition_intent == "accept":
        if not confirmed_acceptance:
            properties["acceptance_authorized"] = {"type": "boolean"}
        properties["pending_invitation_invalid"] = {"type": "boolean"}
    properties["offer_present"] = {"type": "boolean"}
    if not formal:
        properties["offer_quote"] = {"type": "string"}
        properties["offer_kind"] = {"type": "string", "enum": ["", "invitation", "exit_mention_only"]}
    properties.update(valid={"type": "boolean"}, body_violations=violations)
    if display_suggestions:
        properties["suggestion_checks"] = _array(_object({
            "index": {"type": "integer"},
            "decision": {"type": "string", "enum": ["allow", "reject"] + (
                ["after_display"] if allow_after_display and fixed_narrations and not formal else []
            )},
            "requires": _array({"type": "string"}),
        }))
    else:
        properties["unsafe_suggestion_indexes"] = _array({"type": "integer"})
    if formal:
        properties["delivery_matches_route"] = {"type": "boolean"}
    # 窄完成修稿只消费短定位，不消费长failure_reason；保留否决/审批字段，避免解释挤掉审批结果。
    if not completion_fact_review_only:
        properties["failure_reason"] = {"type": "string"}
    if not formal:
        properties["player_action_kind"] = {
            "type": "string", "enum": ["", "unauthorized", "requested_movement"],
        }
    if fixed_narrations:
        properties["fixed_narration_triggers"] = _array(_object({
            "id": {"type": "string"}, "evidence": {"type": "string"},
        }))
    if completion_facts:
        properties["fact_candidates"] = _fact_candidates()
    if actor_facts:
        properties["approved_actor_fact_indexes"] = _array({"type": "integer"})
    if narrative_events:
        properties["approved_narrative_event_indexes"] = _array({"type": "integer"})
        properties["narrative_review_complete"] = {"type": "boolean"}
    if evaluator_facts:
        properties["approved_evaluator_fact_indexes"] = _array({"type": "integer"})
    if locate_body_issues:
        codes = ["player_return_after_departure", "other"]
        if fixed_narrations:
            codes.append("fixed_narration_content")
        issues = {"body_issues": _array(_object({
            "code": {"type": "string", "enum": codes},
            "field": {"type": "string", "enum": ["actor_performance", "scene_update"]},
            "quote": {"type": "string"}, "violations": violations,
        })), "scene_update_removal_safe": {"type": "boolean"}}
        properties = {**issues, **properties} if fixed_narrations else {**properties, **issues}
    return _object(properties)


def contract_output_schema() -> dict[str, Any]:
    """Return only the violated boundary list from the existing protocol."""
    return _object({"violated": _array({"type": "string"})})
