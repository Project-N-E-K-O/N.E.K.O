from __future__ import annotations

import pytest

from plugin.server.application.config.validation import validate_config_updates
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
def test_validate_config_updates_accepts_valid_payload() -> None:
    payload = {
        "plugin": {
            "name": "demo",
            "version": "1.2.3",
            "description": "desc",
            "author": {"name": "alice", "email": "a@example.com"},
            "sdk": {"recommended": "1.0", "conflicts": ["0.9"]},
            "dependency": [{"id": "dep_a", "providers": ["search"]}],
        },
        "runtime": {"enabled": True},
    }

    normalized = validate_config_updates(updates=payload)
    assert normalized["plugin"] == payload["plugin"]
    assert normalized["runtime"] == payload["runtime"]


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "payload",
    [
        {"plugin": "bad"},
        {"plugin": {"author": "bad"}},
        {"plugin": {"sdk": "bad"}},
        {"plugin": {"dependency": "bad"}},
    ],
)
def test_validate_config_updates_rejects_invalid_shapes(payload: object) -> None:
    with pytest.raises(ServerDomainError):
        validate_config_updates(updates=payload)


@pytest.mark.plugin_unit
def test_validate_config_updates_rejects_protected_plugin_id_change() -> None:
    with pytest.raises(ServerDomainError) as exc_info:
        validate_config_updates(updates={"plugin": {"id": "new-id"}})

    assert "protected" in exc_info.value.message.lower()


@pytest.mark.plugin_unit
def test_validate_config_updates_rejects_invalid_email() -> None:
    with pytest.raises(ServerDomainError) as exc_info:
        validate_config_updates(updates={"plugin": {"author": {"email": "invalid"}}})

    assert "email format" in exc_info.value.message.lower()


@pytest.mark.plugin_unit
def test_validate_config_updates_rejects_sdk_conflicts_non_list_non_bool() -> None:
    with pytest.raises(ServerDomainError) as exc_info:
        validate_config_updates(updates={"plugin": {"sdk": {"conflicts": "x"}}})

    assert "conflicts" in exc_info.value.message.lower()


import pytest

from plugin.server.application.config.validation import validate_config_updates
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "payload",
    [
        {"runtime": {"enabled": True}},
        {"plugin": {"name": "ok", "version": "1.0.0"}},
        {"plugin": {"author": {"name": "alice", "email": "a@b.com"}}},
        {"plugin": {"sdk": {"recommended": "1.0", "conflicts": True}}},
        {"plugin": {"sdk": {"conflicts": ["0.9", "0.8"]}}},
        {"plugin": {"dependency": [{"id": "p1", "providers": ["x", "y"], "entry": "main"}]}},
        {"nested": [{"plugin": {"name": "safe"}}]},
    ],
)
def test_validate_config_updates_accepts_boundary_legal_combinations(payload: object) -> None:
    normalized = validate_config_updates(updates=payload)
    assert isinstance(normalized, dict)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "payload, expected_keyword",
    [
        ([], "must be an object"),
        ({1: "x"}, "keys must be strings"),
        ({"plugin": {"name": "x" * 201}}, "too long"),
        ({"plugin": {"version": "v" * 51}}, "too long"),
        ({"plugin": {"description": "d" * 5001}}, "too long"),
        ({"plugin": {"author": {"name": 1}}}, "must be a string"),
        ({"plugin": {"author": {"email": "invalid"}}}, "email format"),
        ({"plugin": {"sdk": {"recommended": 1}}}, "must be a string"),
        ({"plugin": {"sdk": {"conflicts": [1]}}}, "must be a string"),
        ({"plugin": {"dependency": [{"providers": "bad"}]}}, "providers must be a list"),
        ({"plugin": {"dependency": [1]}}, "must be an object"),
        ({"plugin": {"dependency": [{"id": 123}]}} , "must be a string"),
        ({"plugin": {"entry": "x:y"}}, "protected"),
        ({"plugin": {"id": "other"}}, "protected"),
    ],
)
def test_validate_config_updates_rejects_boundary_illegal_combinations(payload: object, expected_keyword: str) -> None:
    with pytest.raises(ServerDomainError) as exc_info:
        validate_config_updates(updates=payload)

    assert expected_keyword.lower() in exc_info.value.message.lower()


@pytest.mark.plugin_unit
def test_validate_config_updates_currently_allows_nested_forbidden_paths_in_list() -> None:
    # Current validator only blocks exact path "plugin.id"/"plugin.entry".
    # Nested list paths like "a[0].plugin.id" are not blocked today.
    payload = {"a": [{"plugin": {"id": "other", "entry": "x:y"}}]}
    normalized = validate_config_updates(updates=payload)
    assert normalized["a"][0]["plugin"]["id"] == "other"
