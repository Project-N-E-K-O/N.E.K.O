from __future__ import annotations

import pytest

from plugin.server.domain.model_config import ModelSlot
from plugin.server.infrastructure.host_model_slots import (
    host_slot_status,
    is_free_host_config,
    materialize_host_slot,
    resolve_host_slot,
)
from plugin.server.model_gateway.errors import ModelGatewayError


pytestmark = pytest.mark.plugin_unit


def host_slot(**updates):
    fields = {"name": "Host assist", "source": "host_follow", "host_api": "assist"}
    fields.update(updates)
    return ModelSlot(**fields)


def test_manual_slot_passes_through_unchanged():
    slot = ModelSlot(name="Manual", protocol="openai_chat", base_url="https://x.test/v1", model="m")
    assert resolve_host_slot(slot) is slot


def test_materialize_openai_config():
    resolved = materialize_host_slot(host_slot(), {
        "model": "qwen-flash", "base_url": "https://dashscope.test/v1",
        "api_key": "sk-host", "provider_type": "openai_compatible",
    })
    assert resolved.protocol == "openai_chat"
    assert resolved.base_url == "https://dashscope.test/v1"
    assert resolved.model == "qwen-flash"
    assert resolved.api_key == "sk-host"
    assert resolved.source == "host_follow"


def test_materialize_anthropic_config():
    resolved = materialize_host_slot(host_slot(), {
        "model": "claude-x", "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant", "provider_type": "anthropic",
    })
    assert resolved.protocol == "anthropic_messages"


@pytest.mark.parametrize("base_url", [
    "https://api.anthropic.com/v1",
    "https://api.kimi.com/coding",
])
def test_materialize_detects_anthropic_endpoints_by_url_like_the_main_app(base_url, monkeypatch):
    import utils.config_manager as config_manager

    config = {"model": "m", "base_url": base_url, "api_key": "k", "provider_type": "openai_compatible"}
    resolved = materialize_host_slot(host_slot(), config)
    assert resolved.protocol == "anthropic_messages"

    class Manager:
        def get_model_api_config(self, model_type):
            return config

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    assert host_slot_status(host_slot())["protocol"] == "anthropic_messages"


@pytest.mark.parametrize("config,code", [
    ({}, "host_api_not_configured"),
    ({"model": "m", "base_url": ""}, "host_api_not_configured"),
    ({"model": "", "base_url": "https://x.test/v1"}, "host_api_not_configured"),
    ({"model": "m", "base_url": "wss://realtime.test/core"}, "host_api_unsupported"),
])
def test_unusable_host_config_is_rejected(config, code):
    with pytest.raises(ModelGatewayError) as exc:
        materialize_host_slot(host_slot(), config)
    assert exc.value.code == code
    assert exc.value.status_code == 409


def test_free_credentials_are_detected():
    assert is_free_host_config({"api_key": "free-access"})
    assert not is_free_host_config({"api_key": "sk-paid"})
    assert not is_free_host_config({})


def test_resolve_reads_the_main_app_config(monkeypatch):
    import utils.config_manager as config_manager

    seen = []

    class Manager:
        def get_model_api_config(self, model_type):
            seen.append(model_type)
            return {"model": "m", "base_url": "https://x.test/v1", "api_key": "k"}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    assert resolve_host_slot(host_slot()).model == "m"
    assert resolve_host_slot(host_slot(host_api="core")).base_url == "https://x.test/v1"
    assert seen == ["conversation", "realtime"]


def test_status_reports_configuration_without_credentials(monkeypatch):
    import utils.config_manager as config_manager

    class Manager:
        def get_model_api_config(self, model_type):
            return {"model": "free-model", "base_url": "https://free.test/v1", "api_key": "free-access"}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    status = host_slot_status(host_slot())
    assert status == {"configured": True, "model": "free-model", "protocol": "openai_chat", "free": True}
    assert "free-access" not in str(status)
    assert host_slot_status(ModelSlot(name="Manual", protocol="openai_chat", base_url="https://x.test/v1", model="m")) == {}


def test_status_reports_unconfigured_host(monkeypatch):
    import utils.config_manager as config_manager

    class Manager:
        def get_model_api_config(self, model_type):
            return {"model": "m", "base_url": "wss://realtime.test/core"}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    status = host_slot_status(host_slot(host_api="core"))
    assert status["configured"] is False
    assert status["error_code"] == "host_api_unsupported"
    assert "WebSocket" in status["message"]

def test_legacy_high_default_does_not_block_resolution(monkeypatch):
    # Saving rejects >1 for host-follow slots; a legacy document may still
    # carry it. Resolution must succeed so an explicit valid request override
    # keeps working — the effective value is judged at request preparation.
    slot = host_slot(defaults={"temperature": 1.0})
    object.__setattr__(slot.defaults, "temperature", 1.5)
    config = {"model": "m", "base_url": "https://api.test/v1", "api_key": "k", "provider_type": "anthropic"}
    resolved = materialize_host_slot(slot, config)
    assert resolved.protocol == "anthropic_messages"
    assert resolved.defaults.temperature == 1.5
    # Within the Anthropic range the snapshot resolves normally too.
    ok = materialize_host_slot(host_slot(defaults={"temperature": 1.0}), config)
    assert ok.protocol == "anthropic_messages"
    # OpenAI resolution keeps the legacy default untouched.
    openai = materialize_host_slot(slot, {**config, "provider_type": "openai_compatible"})
    assert openai.defaults.temperature == 1.5


def test_status_stays_configured_with_legacy_high_default(monkeypatch):
    import utils.config_manager as config_manager

    class Manager:
        def get_model_api_config(self, model_type):
            return {"model": "m", "base_url": "https://api.test/v1", "api_key": "k", "provider_type": "anthropic"}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    legacy = host_slot(defaults={"temperature": 1.0})
    object.__setattr__(legacy.defaults, "temperature", 1.5)
    status = host_slot_status(legacy)
    # The slot is usable with an explicit override; do not label it broken.
    assert status["configured"] is True
    assert status["protocol"] == "anthropic_messages"
