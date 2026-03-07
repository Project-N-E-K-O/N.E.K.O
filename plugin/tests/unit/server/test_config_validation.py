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
