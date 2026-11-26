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
            "dependency": [{"id": "dep_a", "providers": ["search"], "untested": ">=1.0.0"}],
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


@pytest.mark.plugin_unit
def test_validate_config_updates_accepts_single_table_plugin_dependency() -> None:
    payload = {
        "plugin": {
            "dependency": {
                "id": "dep_a",
                "providers": ["search"],
                "untested": ">=1.0.0",
            }
        }
    }

    normalized = validate_config_updates(updates=payload)
    assert normalized["plugin"] == payload["plugin"]

    conflict_payload = {"plugin": {"dependency": {"id": "dep_a", "conflicts": True}}}
    normalized_conflict = validate_config_updates(updates=conflict_payload)
    assert normalized_conflict["plugin"] == conflict_payload["plugin"]


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "dependency_item",
    [
        {"id": "", "conflicts": True},
        {"id": "   ", "conflicts": True},
        {"id": "dep_a"},
        {"entry": "a:b", "custom_event": "evt", "untested": ">=1.0.0"},
        {"conflicts": "true", "id": "dep_a"},
        {"id": "dep_a", "providers": 123, "untested": ">=1.0.0"},
        {"id": "dep_a", "conflicts": 123, "untested": ">=1.0.0"},
    ],
)
def test_validate_config_updates_rejects_schema_invalid_dependency_in_single_and_list_forms(
    dependency_item: dict[str, object],
) -> None:
    for shape in (dependency_item, [dependency_item]):
        with pytest.raises(ServerDomainError) as exc_info:
            validate_config_updates(updates={"plugin": {"dependency": shape}})
        assert exc_info.value.code == "INVALID_CONFIG_UPDATE"
        assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "dependency_item",
    [
        {"id": "provider_a", "untested": ">=0", "conflicts": "<1.0.0"},
        {"id": "dep_a", "providers": "search", "untested": ">=1.0.0"},
        {"id": "dep_a", "providers": "  search  ", "conflicts": " >=1.0.0 ", "untested": ">=0"},
        {"id": "dep_a", "providers": "   ", "conflicts": "   ", "untested": ">=0"},
    ],
)
def test_validate_config_updates_accepts_scalar_string_dependency_providers_and_conflicts(
    dependency_item: dict[str, object],
) -> None:
    for shape in (dependency_item, [dependency_item]):
        normalized = validate_config_updates(updates={"plugin": {"dependency": shape}})
        assert normalized["plugin"] == {"dependency": shape}
