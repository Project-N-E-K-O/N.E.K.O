from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.core.contracts import HostTurnSignal
from plugin.plugins.neko_live.core.runtime import RoastRuntime


@pytest.mark.asyncio
async def test_dashboard_projects_default_off_policy_without_output(
    runtime: RoastRuntime,
) -> None:
    state = await runtime.dashboard_state()

    projection = state["co_stream_participation"]
    assert projection == {
        "live_mode": "co_stream",
        "read_only": True,
        "enforced": False,
        "host_turn": {
            "state": "unknown",
            "reliability": "unavailable",
            "confidence": 0.0,
            "source": "fallback",
        },
        "capabilities": [
            {
                "id": "host_pause_fill",
                "activation": "off",
                "effective_activation": "off",
                "auto_enforcement_confirmed": False,
                "requested_level": "L3",
                "effective_level": "L3",
                "priority": 5,
                "decision": "skip",
                "reason_code": "co_stream.policy.capability_off",
            }
        ],
    }
    assert runtime.plugin.pushed_messages == []
    assert list(runtime.recent_results) == []


@pytest.mark.asyncio
async def test_dashboard_projection_is_read_only_for_host_runtime_signal(
    runtime: RoastRuntime,
) -> None:
    runtime.config.co_stream_host_pause_fill_activation = "conditional_auto"
    runtime.config.co_stream_host_pause_fill_auto_consent_version = 1
    signal = HostTurnSignal(
        state="yielded",
        confidence=1.0,
        reliability="reliable",
        observed_at=42.0,
        source="host_runtime",
    )
    runtime.host_turn_signal_provider.update(signal)

    first = (await runtime.dashboard_state())["co_stream_participation"]
    second = (await runtime.dashboard_state())["co_stream_participation"]

    assert first["capabilities"][0]["decision"] == "allow"
    assert first["capabilities"][0]["auto_enforcement_confirmed"] is True
    assert first["capabilities"][0]["reason_code"] == "co_stream.policy.turn_yielded"
    assert second == first
    assert runtime.host_turn_signal_provider.current() == signal
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_unconfirmed_conditional_auto_remains_non_executable(
    runtime: RoastRuntime,
) -> None:
    runtime.config.co_stream_host_pause_fill_activation = "conditional_auto"

    capability = (await runtime.dashboard_state())["co_stream_participation"]["capabilities"][0]

    assert capability["activation"] == "conditional_auto"
    assert capability["effective_activation"] == "off"
    assert capability["auto_enforcement_confirmed"] is False
    assert capability["decision"] == "skip"
    assert runtime.plugin.pushed_messages == []


@pytest.mark.asyncio
async def test_dashboard_projects_only_normalized_host_runtime_turn_facts(
    runtime: RoastRuntime,
) -> None:
    runtime.host_turn_signal_provider = SimpleNamespace(
        current=lambda: HostTurnSignal(
            state="speaking",
            confidence=0.9,
            reliability="reliable",
            observed_at=42.0,
            source="host_runtime",
        )
    )

    host_turn = (await runtime.dashboard_state())["co_stream_participation"]["host_turn"]

    assert host_turn == {
        "state": "speaking",
        "reliability": "reliable",
        "confidence": 0.9,
        "source": "host_runtime",
    }
    assert "observed_at" not in host_turn


@pytest.mark.asyncio
async def test_runtime_config_rejects_removed_manual_mode(
    runtime: RoastRuntime,
) -> None:
    config = await runtime.update_config(
        {"co_stream_host_pause_fill_activation": " MANUAL "}
    )

    assert config.co_stream_host_pause_fill_activation == "off"
    assert runtime.plugin.config.updates[-1] == {
        "neko_live": {"co_stream_host_pause_fill_activation": "off"}
    }


@pytest.mark.asyncio
async def test_generic_config_update_cannot_grant_auto_speech_consent(
    runtime: RoastRuntime,
) -> None:
    config = await runtime.update_config(
        {
            "co_stream_host_pause_fill_activation": "conditional_auto",
            "co_stream_host_pause_fill_auto_consent_version": 1,
        }
    )

    assert config.co_stream_host_pause_fill_activation == "conditional_auto"
    assert config.co_stream_host_pause_fill_auto_consent_version == 0
    capability = (await runtime.dashboard_state())["co_stream_participation"]["capabilities"][0]
    assert capability["effective_activation"] == "off"
    assert capability["auto_enforcement_confirmed"] is False


@pytest.mark.asyncio
async def test_solo_stream_projection_is_passthrough_and_has_no_runtime_effect(
    runtime: RoastRuntime,
) -> None:
    runtime.config.live_mode = "solo_stream"
    before_config = runtime.config.to_dict()

    projection = (await runtime.dashboard_state())["co_stream_participation"]

    capability = projection["capabilities"][0]
    assert projection["live_mode"] == "solo_stream"
    assert capability["decision"] == "allow"
    assert capability["reason_code"] == "co_stream.policy.solo_passthrough"
    assert runtime.config.to_dict() == before_config
    assert runtime.plugin.pushed_messages == []
