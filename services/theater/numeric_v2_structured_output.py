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
                        fact_candidates_expected: bool = False) -> dict[str, Any]:
    """Declare fields already used by each prompt; scene_update remains optional."""
    suggestions = _array({"type": "string"})
    if transition_suggestions_only:
        return _object({"accept_input": {"type": "string"}, "alternative_inputs": suggestions})
    if suggestions_only:
        return _object({"suggested_inputs": suggestions})
    if transition_required:
        return _object({
            **{key: {"type": "string"} for key in (
                "source_scene_narration", "source_performance", "target_performance",
                "bridge_scene_narration", "target_scene_narration",
            )},
            "suggested_inputs": suggestions,
        })
    properties = {"scene_narration": {"type": "string"}} if opening_required else {}
    properties.update(performance={"type": "string"}, suggested_inputs=suggestions,
                      transition_offered={"type": "boolean"})
    if not opening_required:
        properties["scene_update"] = {"type": "string"}
        if fact_candidates_expected:
            properties["fact_candidates"] = _fact_candidates()
    return _object(properties, optional=("scene_update",))


def review_output_schema(*, formal: bool = False, transition_intent: str = "",
                         confirmed_acceptance: bool = False, missed_initiation: bool = False,
                         fixed_narrations: bool = False, display_suggestions: bool = False,
                         completion_facts: bool = False, evaluator_facts: bool = False,
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
            "decision": {"type": "string", "enum": ["allow", "reject", "after_display"]},
            "requires": _array({"type": "string"}),
        }))
    else:
        properties["unsafe_suggestion_indexes"] = _array({"type": "integer"})
    if formal:
        properties["delivery_matches_route"] = {"type": "boolean"}
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
