from pathlib import Path

import pytest
from plugin.plugins.lifekit import LifeKitPlugin, _public_config
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._write_confirmation import WriteConfirmationGate
from plugin.sdk.plugin import Err, Ok


def test_public_config_redacts_map_keys() -> None:
    public = _public_config({
        "default_city": "上海",
        "amap_key": "amap-secret",
        "baidu_map_key": "baidu-secret",
    })

    assert public == {
        "default_city": "上海",
        "amap_configured": True,
        "baidu_map_configured": True,
    }
    assert "secret" not in repr(public)


def test_confirmation_requires_a_token_bound_to_the_exact_payload() -> None:
    gate = WriteConfirmationGate()
    payload = {"location_id": "home"}

    forged, token = gate.authorize_or_issue(
        action="remove_location",
        payload=payload,
        confirmed=True,
        token="",
    )
    changed_payload, _ = gate.authorize_or_issue(
        action="remove_location",
        payload={"location_id": "office"},
        confirmed=True,
        token=token,
    )
    consumed, fresh_token = gate.authorize_or_issue(
        action="remove_location",
        payload=payload,
        confirmed=True,
        token=token,
    )
    valid, _ = gate.authorize_or_issue(
        action="remove_location",
        payload=payload,
        confirmed=True,
        token=fresh_token,
    )

    assert forged is False
    assert changed_payload is False
    assert consumed is False
    assert valid is True


def _bare_plugin() -> LifeKitPlugin:
    plugin = object.__new__(LifeKitPlugin)
    plugin._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
    plugin._write_confirmations = WriteConfirmationGate()
    plugin._resolve_locale = lambda: None
    return plugin


@pytest.mark.asyncio
async def test_config_update_issues_confirmation_without_crashing() -> None:
    result = await _bare_plugin().update_config_entry(
        default_city="上海",
        _ctx={"source": "chat"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["confirmation_token"]

    meta = getattr(LifeKitPlugin.update_config_entry, "__neko_event_meta__")
    assert "confirmation_token" in meta.llm_result_fields


@pytest.mark.asyncio
async def test_chat_cannot_update_or_echo_map_keys() -> None:
    result = await _bare_plugin().update_config_entry(
        amap_key="amap-secret",
        _ctx={"source": "chat"},
    )

    assert isinstance(result, Err)
    assert "amap-secret" not in repr(result)


@pytest.mark.asyncio
async def test_ui_confirmation_never_echoes_map_key_values() -> None:
    result = await _bare_plugin().update_config_entry(amap_key="amap-secret")

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert "amap-secret" not in repr(result.value)


@pytest.mark.asyncio
async def test_ui_secret_confirmation_uses_opaque_server_side_payload() -> None:
    plugin = _bare_plugin()
    updates: list[dict[str, object]] = []

    class _Config:
        async def update(self, value: dict[str, object]) -> None:
            updates.append(value)

    async def reload_config() -> None:
        return None

    plugin.config = _Config()
    plugin._cfg = {}
    plugin._reload_config = reload_config
    first = await plugin.update_config_entry(amap_key="amap-secret")
    second = await plugin.update_config_entry(**first.value["context"])

    assert isinstance(second, Ok)
    assert second.value["status"] == "ready"
    assert updates == [{"lifekit": {"amap_key": "amap-secret"}}]
    assert "amap-secret" not in repr(first.value)


def test_confirmation_gate_bounds_pending_tokens() -> None:
    gate = WriteConfirmationGate(max_pending=2)

    gate.issue("one", {"value": 1})
    gate.issue("two", {"value": 2})
    gate.issue("three", {"value": 3})

    assert len(gate._pending) == 2


def test_confirmation_token_is_bound_to_available_conversation_scope() -> None:
    gate = WriteConfirmationGate()
    token = gate.issue("remove", {"id": "home"}, scope="conversation-a")

    assert not gate.consume(
        token,
        "remove",
        {"id": "home"},
        scope="conversation-b",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("off", False),
        ("no", False),
        ("true", True),
        ("1", True),
        ("on", True),
        ("yes", True),
        (False, False),
        (True, True),
    ],
)
async def test_update_config_parses_boolean_strings_strictly(
    raw_value: object,
    expected: bool,
) -> None:
    plugin = _bare_plugin()
    updates: list[dict[str, object]] = []

    class _Config:
        async def update(self, value: dict[str, object]) -> None:
            updates.append(value)

    async def reload_config() -> None:
        return None

    plugin.config = _Config()
    plugin._cfg = {}
    plugin._reload_config = reload_config

    first = await plugin.update_config_entry(
        force_locale=raw_value,
        enable_geoip=raw_value,
    )
    assert isinstance(first, Ok)
    assert first.value["status"] == "clarify"
    result = await plugin.update_config_entry(**first.value["context"])

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert updates == [{"lifekit": {"force_locale": expected, "enable_geoip": expected}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", ["maybe", "2", "", None, []])
async def test_update_config_rejects_invalid_boolean_strings(bad_value: object) -> None:
    plugin = _bare_plugin()
    plugin._cfg = {}

    first = await plugin.update_config_entry(force_locale=bad_value)
    assert isinstance(first, Ok)
    result = await plugin.update_config_entry(**first.value["context"])
    assert isinstance(result, Err)
