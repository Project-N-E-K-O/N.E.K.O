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
    assert host_slot_status(host_slot(host_api="core")) == {"configured": False, "error_code": "host_api_unsupported"}