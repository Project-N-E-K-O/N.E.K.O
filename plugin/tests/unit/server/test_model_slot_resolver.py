from __future__ import annotations

import typing

import pytest

from plugin.server.domain.model_config import (
    HOST_API_MODEL_TYPES,
    HostApi,
    ModelSlot,
    PluginModelsConfig,
)
from plugin.server.infrastructure.host_model_slots import client_headers
from plugin.server.infrastructure.model_config_store import CONFIG_FILENAME
from plugin.server.infrastructure.model_slot_resolver import (
    ModelSlotUnavailable,
    list_model_slots,
    resolve_model_slot,
)
from utils.file_utils import atomic_write_json


pytestmark = pytest.mark.plugin_unit

MANUAL_ID = "slot_" + "1" * 32
PLAIN_ID = "slot_" + "2" * 32
AGENT_ID = "slot_" + "3" * 32


class Manager:
    """Config manager double: runtime files under tmp_path, host tiers in memory."""

    def __init__(self, root, host=None):
        self.config_dir = root / "config"
        self.host = host if host is not None else {}
        self.asked = []

    def get_runtime_config_path(self, filename):
        return self.config_dir / filename

    def save_json_config(self, filename, data):
        raise AssertionError("the resolver must never write")

    def get_model_api_config(self, model_type):
        self.asked.append(model_type)
        value = self.host.get(model_type, {})
        if isinstance(value, Exception):
            raise value
        return value


def write_slots(manager, slots):
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    document = PluginModelsConfig(slots=slots).model_dump(mode="json")
    atomic_write_json(manager.get_runtime_config_path(CONFIG_FILENAME), document)


def manual(**updates):
    fields = {
        "name": "Own model", "protocol": "openai_chat", "base_url": "https://own.test/v1",
        "model": "own-model", "api_key": "sk-own", "capabilities": ["text", "tool_calling"],
    }
    fields.update(updates)
    return ModelSlot(**fields)


def follow(host_api, **updates):
    fields = {"name": "Follow " + host_api, "source": "host_follow", "host_api": host_api}
    fields.update(updates)
    return ModelSlot(**fields)


@pytest.fixture
def env(tmp_path):
    manager = Manager(tmp_path, host={
        "agent": {
            "model": "agent-model", "base_url": "https://api.kimi.com/coding/", "api_key": "sk-host",
            "provider_type": "anthropic",
        },
    })
    write_slots(manager, {
        MANUAL_ID: manual(),
        PLAIN_ID: follow("agent"),
        AGENT_ID: follow("agent", capabilities=["text", "tool_calling", "image_input"]),
    })
    return manager


def test_followable_tiers_match_the_validated_literal():
    assert set(typing.get_args(HostApi)) == set(HOST_API_MODEL_TYPES)


@pytest.mark.parametrize("host_api", ["conversation", "summary", "assist", "core", "realtime", "vision"])
def test_unlisted_main_app_tiers_cannot_be_followed(host_api):
    with pytest.raises(ValueError):
        follow(host_api)


def test_manual_slot_resolves_to_its_own_settings(env):
    resolved = resolve_model_slot(MANUAL_ID, require=["tool_calling"], config_manager=env)
    assert (resolved.base_url, resolved.model, resolved.protocol, resolved.api_key) == (
        "https://own.test/v1", "own-model", "openai_chat", "sk-own",
    )
    assert resolved.source == "manual" and resolved.host_api is None
    assert resolved.free is False
    assert resolved.headers == {}
    assert env.asked == []


def test_follow_slot_resolves_the_main_app_tier_now(env):
    env.host["agent"] = {"model": "free-agent-model", "base_url": "https://free.test/v1", "api_key": "free-access"}
    resolved = resolve_model_slot(PLAIN_ID, config_manager=env)
    assert env.asked == ["agent"]
    assert (resolved.base_url, resolved.model, resolved.protocol) == ("https://free.test/v1", "free-agent-model", "openai_chat")
    assert resolved.free is True
    env.host["agent"] = {"model": "paid-model", "base_url": "https://paid.test/v1", "api_key": "sk-paid"}
    again = resolve_model_slot(PLAIN_ID, config_manager=env)
    assert (again.model, again.free) == ("paid-model", False)


def test_agent_tier_uses_the_main_app_agent_model_and_its_client_headers(env):
    resolved = resolve_model_slot(AGENT_ID, require=["tool_calling", "image_input"], config_manager=env)
    assert env.asked == ["agent"]
    assert resolved.protocol == "anthropic_messages"
    assert resolved.model == "agent-model"
    assert resolved.headers == {"User-Agent": "claude-code/0.1.0"}
    assert resolved.capabilities == ("text", "tool_calling", "image_input")


def test_advanced_familiar_tier_resolves_its_own_main_app_tier(env):
    env.host["agent_advanced"] = {"model": "big-model", "base_url": "https://big.test/v1", "api_key": "sk-big"}
    write_slots(env, {PLAIN_ID: follow("agent_advanced")})
    resolved = resolve_model_slot(PLAIN_ID, config_manager=env)
    assert env.asked == ["agent_advanced"]
    assert (resolved.host_api, resolved.model, resolved.base_url) == ("agent_advanced", "big-model", "https://big.test/v1")


def test_small_familiar_tier_resolves_its_own_main_app_tier(env):
    env.host["agent_small"] = {"model": "small-model", "base_url": "https://small.test/v1", "api_key": "sk-small"}
    write_slots(env, {PLAIN_ID: follow("agent_small")})
    resolved = resolve_model_slot(PLAIN_ID, config_manager=env)
    assert env.asked == ["agent_small"]
    assert (resolved.host_api, resolved.model, resolved.base_url) == ("agent_small", "small-model", "https://small.test/v1")


def test_credentials_stay_out_of_repr(env):
    text = repr(resolve_model_slot(AGENT_ID, config_manager=env))
    assert "sk-host" not in text
    assert "agent-model" in text


def test_free_credential_in_a_manual_slot_is_reported_free(env):
    write_slots(env, {MANUAL_ID: manual(api_key="free-access")})
    assert resolve_model_slot(MANUAL_ID, config_manager=env).free is True


def failure(env, slot_id, **kwargs):
    with pytest.raises(ModelSlotUnavailable) as caught:
        resolve_model_slot(slot_id, config_manager=env, **kwargs)
    assert "sk-" not in str(caught.value)
    return caught.value


def test_deleted_slot_is_not_found(env):
    error = failure(env, "slot_" + "9" * 32)
    assert (error.code, error.slot_id) == ("slot_not_found", "slot_" + "9" * 32)


def test_missing_capability_is_reported_before_resolving(env):
    error = failure(env, PLAIN_ID, require=["text", "tool_calling", "image_input"])
    assert error.code == "capability_missing"
    assert "image_input" in error.message and "tool_calling" in error.message
    assert env.asked == []


@pytest.mark.parametrize(("host", "code"), [
    ({}, "host_api_not_configured"),
    ({"model": "m"}, "host_api_not_configured"),
    ({"model": "m", "base_url": "wss://voice.test/realtime"}, "host_api_unsupported"),
    (RuntimeError("broken config"), "host_api_not_configured"),
])
def test_unusable_main_app_tier(env, host, code):
    env.host["agent"] = host
    assert failure(env, PLAIN_ID).code == code


def test_unreadable_document(env):
    env.get_runtime_config_path(CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    assert failure(env, PLAIN_ID).code == "model_config_unreadable"
    with pytest.raises(ModelSlotUnavailable) as caught:
        list_model_slots(config_manager=env)
    assert caught.value.code == "model_config_unreadable"


def test_missing_document_has_no_slots(tmp_path):
    manager = Manager(tmp_path)
    assert list_model_slots(config_manager=manager) == []
    with pytest.raises(ModelSlotUnavailable) as caught:
        resolve_model_slot(PLAIN_ID, config_manager=manager)
    assert caught.value.code == "slot_not_found"
    assert not manager.get_runtime_config_path(CONFIG_FILENAME).exists()


def test_list_shows_what_each_slot_resolves_to_without_credentials(env):
    listed = {summary.slot_id: summary for summary in list_model_slots(config_manager=env)}
    assert list(listed) == [MANUAL_ID, PLAIN_ID, AGENT_ID]
    assert (listed[MANUAL_ID].configured, listed[MANUAL_ID].model, listed[MANUAL_ID].free) == (True, "own-model", False)
    agent = listed[AGENT_ID]
    assert (agent.host_api, agent.configured, agent.model, agent.protocol, agent.free) == (
        "agent", True, "agent-model", "anthropic_messages", False,
    )
    assert agent.capabilities == ("text", "tool_calling", "image_input")
    assert all("sk-" not in repr(summary) for summary in listed.values())
    env.host["agent"] = {"model": "m", "base_url": "wss://voice.test/realtime"}
    broken = {summary.slot_id: summary for summary in list_model_slots(config_manager=env)}[AGENT_ID]
    assert (broken.configured, broken.error_code, broken.model, broken.protocol) == (
        False, "host_api_unsupported", "", None,
    )


# Not "base_url": pytest-base-url owns a session fixture by that name.
@pytest.mark.parametrize(("protocol", "endpoint", "expected"), [
    ("anthropic_messages", "https://api.kimi.com/coding/", {"User-Agent": "claude-code/0.1.0"}),
    ("openai_chat", "https://api.kimi.com/coding/", {}),
    ("anthropic_messages", "https://api.anthropic.com/v1", {}),
])
def test_client_headers(protocol, endpoint, expected):
    assert client_headers(protocol, endpoint) == expected
