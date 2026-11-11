"""API shape constraints for theater output; semantic and quote checks remain separate."""

from typing import Any, Mapping
from urllib.parse import urlsplit


BOUND_COMPLETION_REJECTION_REASONS = (
    "player_agreement_missing", "role_agreement_missing", "delivery_missing",
    "player_feedback_missing", "evidence_conflict", "uncertain",
)


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
                        transition_tail_only: bool = False,
                        suggestions_only: bool = False, transition_suggestions_only: bool = False,
                        fact_candidates_expected: bool = False,
                        reinvitation_only: bool = False,
                        single_ending_response: bool = False,
                        fixed_entry_only: bool = False,
                        author_entry_choices: bool = False,
                        suppress_transition_offer: bool = False,
                        suppress_suggestions: bool = False,
                        combined_metric_ids: tuple[str, ...] | None = None,
                        combined_history_lookup: bool = False,
                        entry_trigger_required: bool = False,
                        target_dialogue_required: bool = False) -> dict[str, Any]:
    """Declare fields already used by each prompt; scene_update remains optional."""
    suggestions = _array({"type": "string"})
    if author_entry_choices or suppress_suggestions:
        suggestions["maxItems"] = 0
    if transition_suggestions_only:
        return _object({"accept_input": {"type": "string"}, "alternative_inputs": suggestions})
    if suggestions_only:
        return _object({"suggested_inputs": suggestions})
    if reinvitation_only:
        # 重邀仅发布正文提议；接受动作由程序控件承担，模型不再代写玩家的接受输入。
        return _object({"performance": {"type": "string"},
                        "transition_offered": {"type": "boolean"}})
    if transition_required:
        fact_fields = {"fact_candidates": _fact_candidates()} if fact_candidates_expected else {}
        trigger_fields = ({"target_entry_trigger": {"type": "string"}}
                          if entry_trigger_required and not fixed_entry_only and not single_ending_response else {})
        target_performance = {"type": "string", **({"minLength": 1}
                              if trigger_fields and target_dialogue_required else {})}
        if transition_tail_only:
            return _object({
                "bridge_scene_narration": {"type": "string"},
                "target_scene_narration": {"type": "string", **({"enum": [""]} if fixed_entry_only else {})},
                **trigger_fields,
                "target_performance": {**target_performance, **({"enum": [""]} if fixed_entry_only else {})},
                "suggested_inputs": suggestions if not fixed_entry_only else {
                    "type": "array", "items": {"type": "string"}, "maxItems": 0},
            })
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
                **fact_fields,
            })
        if single_ending_response:
            return _object({
                **{key: {"type": "string"} for key in (
                    "source_scene_narration", "performance", "bridge_scene_narration", "target_scene_narration")},
                "response_phase": {"type": "string", "enum": ["source_response", "target_opening", "none"]},
                "suggested_inputs": suggestions,
                **fact_fields,
            })
        return _object({
            **{key: {"type": "string"} for key in (
                "source_scene_narration", "source_performance", "bridge_scene_narration",
                "target_scene_narration",
            )},
            **trigger_fields,
            "target_performance": target_performance,
            "suggested_inputs": suggestions,
            **fact_fields,
        })
    # Independent external events/NPC replies precede the character's reaction in playback.
    properties = {"scene_narration" if opening_required else "scene_update": {"type": "string"}}
    properties["performance"] = {"type": "string"}
    if opening_required:
        properties.update(suggested_inputs=suggestions, transition_offered={"type": "boolean"})
    else:
        properties.update(transition_offer={
            "type": "string", **({"enum": [""]} if suppress_transition_offer else {}),
        }, suggested_inputs=suggestions)
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
            **({"history_query": {"type": "string"}} if combined_history_lookup else {}),
        }, optional=("history_query",))
    return _object(properties, optional=("scene_update",))




def review_output_schema(*, formal: bool = False, transition_intent: str = "",
                         confirmed_acceptance: bool = False, missed_initiation: bool = False,
                         fixed_narrations: bool = False, display_suggestions: bool = False,
                         allow_after_display: bool = False,
                         completion_facts: bool = False, evaluator_facts: bool = False, actor_facts: bool = False,
                         completion_fact_review_only: bool = False,
                         locate_body_issues: bool = False) -> dict[str, Any]:
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


def contract_output_schema(*, grounded: bool = False, confirmation: bool = False,
                           check_offer: bool = False, confirmation_many: bool = False,
                           check_route: bool = False, check_departure: bool = False,
                           check_departure_scope: bool = False, author_rule_count: int = 0,
                           rule_count: int | None = None,
                           check_player_authority: bool = False,
                           candidate_refs: list[str] | None = None,
                           prior_refs: list[str] | None = None,
                           clearance_refs: list[str] | None = None,
                           authorization_refs: list[str] | None = None,
                           fixed_original_refs: list[str] | None = None,
                           player_authority_refs: list[str] | None = None,
                           authority_repair_only: bool = False,
                           bound_completion_count: int | None = None,
                           bound_completion_refs: list[str] | None = None) -> dict[str, Any]:
    """Bind body-contract findings to request-local original blocks when enabled."""
    narrow_findings = {
        field: {**_array(_object({"candidate_ref": {"type": "string"},
                                  "quote": {"type": "string", "minLength": 1}})), "maxItems": 1}
        for field, enabled in (("route_conflicts", check_route and not confirmation),
                               ("premature_departure", check_departure and not check_departure_scope))
        if enabled
    }
    if grounded and check_departure and check_departure_scope:
        narrow_findings["departure_scope"] = _array(_object({
            "scope": {"type": "string", "enum": ["current_scene", "prospective", "departed", "uncertain"]},
            "candidate_ref": {"type": "string"}, "quote": {"type": "string", "minLength": 1},
        }))
    if confirmation:
        verdict = {
            "verdict": {"type": "string", "enum": ["violation", "clear", "uncertain"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 48},
            "clearance": {**_array({"type": "string", "enum": clearance_refs} if clearance_refs is not None else
                _object({"source_ref": {"type": "string"}, "quote": {"type": "string", "minLength": 1}})), "maxItems": 3},
        }
        properties = {"verdicts": _array(_object(verdict))} if confirmation_many else verdict
        if check_departure and not check_departure_scope:
            properties["departure_clearance"] = {**_array(_object({
                "reason": {"type": "string", "enum": ["current_scene", "prospective", "recap"]},
                "quote": {"type": "string", "minLength": 1},
                "prior_quote": {"type": "string"},
            })), "maxItems": 1}
        return _object({**properties, **narrow_findings})
    if grounded:
        authority = {}
        if bound_completion_count is not None:
            authority["bound_completion_approved_indexes"] = {
                **_array({"type": "integer", "enum": list(range(bound_completion_count))}),
                "maxItems": bound_completion_count,
            }
            authority["bound_completion_rejections"] = {
                **_array(_object({
                    "index": {"type": "integer", "enum": list(range(bound_completion_count))},
                    "reason": {"type": "string", "enum": list(BOUND_COMPLETION_REJECTION_REASONS)},
                    "refs": {**_array({"type": "string", **({"enum": bound_completion_refs}
                        if bound_completion_refs is not None else {})}), "maxItems": 4},
                })), "maxItems": bound_completion_count,
            }
        if check_player_authority:
            action = _object({"quote": {"type": "string", "minLength": 1},
                "authorization_ref": {"type": "string", "enum": ["", *(authorization_refs or [])]}})
            authority["player_authority"] = _object({ref: {"anyOf": [
                {"type": "string", "enum": ["none", "uncertain"]},
                {**_array(action), "minItems": 1},
            ]} for ref in (candidate_refs if player_authority_refs is None else player_authority_refs) or []
                if ref.startswith("c")})
        if authority_repair_only:
            return _object({"player_authority": authority["player_authority"]})
        if fixed_original_refs:
            cause_finding = _object({"quote": {"type": "string", "minLength": 1},
                "prior_ref": {"type": "string", **({"enum": prior_refs} if prior_refs is not None else {})}})
            authority["fixed_original_cause"] = _object({ref: {"anyOf": [
                {"type": "string", "enum": ["clear", "uncertain"]}, cause_finding,
            ]} for ref in fixed_original_refs})
        author_checks = {}
        if author_rule_count:
            author_refs = [ref for ref in candidate_refs if ref.startswith("c")] if candidate_refs is not None else None
            author_finding = _object({
                "candidate_ref": {"type": "string", **({"enum": author_refs} if author_refs else {})},
                "quote": {"type": "string", "minLength": 1},
                "prior_ref": {"type": "string", **({"enum": prior_refs} if prior_refs is not None else {})},
            })
            author_checks["author_checks"] = _object({
                str(index): {"anyOf": [{"type": "string", "enum": ["clear", "uncertain"]}, author_finding]}
                    if author_refs != [] else {"type": "string", "enum": ["clear", "uncertain"]}
                for index in range(author_rule_count)
            })
        violations = _array(_object({
            "rule_index": {"type": "integer"},
            "candidate_ref": {"type": "string"}, "quote": {"type": "string", "minLength": 1},
            "prior_ref": {"type": "string"},
        }))
        violations["maxItems"] = 4
        finding = violations["items"]["properties"]
        if rule_count is not None:
            generic_indices = list(range(author_rule_count, rule_count))
            if generic_indices:
                finding["rule_index"]["enum"] = generic_indices
            else:
                violations["maxItems"] = 0
        if candidate_refs is not None:
            if candidate_refs:
                finding["candidate_ref"]["enum"] = candidate_refs
            else:
                violations["maxItems"] = 0
        if prior_refs is not None:
            finding["prior_ref"]["enum"] = prior_refs
        return _object({**narrow_findings, **authority, **author_checks,
                        **({"offer_actionable": {"type": "boolean"}} if check_offer else {}),
                        "violations": violations})
    return _object({"violated": _array({"type": "string"})})
