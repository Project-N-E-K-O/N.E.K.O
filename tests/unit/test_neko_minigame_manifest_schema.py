import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-manifest.schema.json"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def manifest() -> dict:
    return {
        "id": "schema-test",
        "version": "1.0.0",
        "requiredCapabilities": ["logging", "runtime"],
        "contracts": {
            "events": {
                "score": {
                    "type": "object",
                    "properties": {
                        "goals": {"type": "integer", "minimum": 0},
                    },
                    "required": ["goals"],
                },
            },
        },
    }


def assert_invalid(validator: Draft202012Validator, value: dict) -> None:
    assert list(validator.iter_errors(value)), "manifest unexpectedly passed the machine schema"


def test_valid_runtime_contract_manifest_passes(validator: Draft202012Validator) -> None:
    validator.validate(manifest())


def test_array_contract_requires_items(validator: Draft202012Validator) -> None:
    value = manifest()
    value["contracts"]["events"]["score"] = {"type": "array"}
    assert_invalid(validator, value)


def test_contracts_and_host_context_require_runtime(validator: Draft202012Validator) -> None:
    contracts_without_runtime = manifest()
    contracts_without_runtime["requiredCapabilities"] = ["logging"]
    assert_invalid(validator, contracts_without_runtime)

    memory_without_runtime = {
        "id": "memory-test",
        "version": "1.0.0",
        "requiredCapabilities": ["logging", "memory"],
    }
    assert_invalid(validator, memory_without_runtime)


def test_quick_lines_requires_dialogue(validator: Draft202012Validator) -> None:
    value = {
        "id": "quick-lines-test",
        "version": "1.0.0",
        "requiredCapabilities": ["logging"],
        "optionalCapabilities": ["quick-lines"],
    }
    assert_invalid(validator, value)
    value["optionalCapabilities"].append("dialogue")
    validator.validate(value)


def test_contract_enum_values_match_declared_type(validator: Draft202012Validator) -> None:
    value = manifest()
    value["contracts"]["events"]["score"] = {
        "type": "integer",
        "enum": [1, "two"],
    }
    assert_invalid(validator, value)

    valid_value = deepcopy(value)
    valid_value["contracts"]["events"]["score"]["enum"] = [1, 2]
    validator.validate(valid_value)


def test_local_leaderboard_manifest_contract(validator: Draft202012Validator) -> None:
    value = {
        "id": "leaderboard-test",
        "version": "1.0.0",
        "requiredCapabilities": ["logging", "leaderboard-local"],
        "leaderboards": {
            "main": {
                "scoreField": "score",
                "order": "descending",
                "maxEntries": 20,
                "retention": "recent",
            },
        },
    }
    validator.validate(value)

    without_definitions = deepcopy(value)
    without_definitions.pop("leaderboards")
    assert_invalid(validator, without_definitions)

    without_capability = deepcopy(value)
    without_capability["requiredCapabilities"] = ["logging"]
    assert_invalid(validator, without_capability)


def test_server_leaderboard_requires_runtime(validator: Draft202012Validator) -> None:
    value = {
        "id": "server-leaderboard-test",
        "version": "1.0.0",
        "requiredCapabilities": ["logging", "leaderboard-server"],
        "leaderboards": {"main": {"scoreField": "score"}},
    }
    assert_invalid(validator, value)
    value["requiredCapabilities"].append("runtime")
    validator.validate(value)


def test_voice_input_requires_runtime(validator: Draft202012Validator) -> None:
    """Voice input is only meaningful inside a live route.

    It is delivered through the runtime lifecycle: the host owns microphone
    capture and hands transcripts to the game against an active route
    generation, and every ``voice.*`` command requires that route. A manifest
    asking for it without ``runtime`` declares a combination the host cannot
    honour, so reject it at the contract layer rather than at call time.

    ``speech-output`` is deliberately NOT paired this way: ``speech.speak()``
    works before a route exists, so a game may narrate without ever taking over
    the runtime lifecycle.
    """
    capability = "voice-input"
    invalid = deepcopy(manifest())
    invalid["requiredCapabilities"] = ["logging", capability]
    assert_invalid(validator, invalid)

    with_required = deepcopy(manifest())
    with_required["requiredCapabilities"] = ["logging", "runtime", capability]
    validator.validate(with_required)

    # ``runtime`` may also satisfy the dependency from the optional list.
    with_optional = deepcopy(manifest())
    with_optional["requiredCapabilities"] = ["logging", capability]
    with_optional["optionalCapabilities"] = ["runtime"]
    validator.validate(with_optional)


def test_contract_property_names_reject_clone_reserved_names(
    validator: Draft202012Validator,
) -> None:
    """The published schema must reject what the SDK rejects at connect time.

    ``validateContractValue`` refuses these property names while cloning every
    payload, so a contract declared with one connects fine and then rejects
    every message it was declared for. The schema is the contract authors read;
    a rule that lives only in the runtime is a rule they discover at runtime.
    """
    for reserved in ("__proto__", "prototype", "constructor"):
        value = manifest()
        value["contracts"]["events"]["score"] = {
            "type": "object",
            "properties": {reserved: {"type": "integer"}},
            "required": [reserved],
        }
        assert_invalid(validator, value)


def test_contract_property_names_reject_empty_and_overlong_names(
    validator: Draft202012Validator,
) -> None:
    """Same bound the SDK applies at manifest normalization: 1..64 characters."""
    empty_name = manifest()
    empty_name["contracts"]["events"]["score"] = {
        "type": "object",
        "properties": {"": {"type": "integer"}},
    }
    assert_invalid(validator, empty_name)

    overlong = manifest()
    overlong["contracts"]["events"]["score"] = {
        "type": "object",
        "properties": {"g" * 65: {"type": "integer"}},
    }
    assert_invalid(validator, overlong)

    at_the_limit = manifest()
    at_the_limit["contracts"]["events"]["score"] = {
        "type": "object",
        "properties": {"g" * 64: {"type": "integer"}},
        "required": ["g" * 64],
    }
    validator.validate(at_the_limit)


def test_required_entries_reject_clone_reserved_names(
    validator: Draft202012Validator,
) -> None:
    """`required` names are property names too, and were bounded separately."""
    value = manifest()
    value["contracts"]["events"]["score"] = {
        "type": "object",
        "properties": {"goals": {"type": "integer"}},
        "required": ["constructor"],
    }
    assert_invalid(validator, value)
