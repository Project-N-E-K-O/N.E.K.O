from __future__ import annotations

import pytest

from main_logic.asr_client.provider_policy import (
    AsrProviderPolicy,
    resolve_provider_policy,
)


def test_streaming_manual_provider_requires_smart_turn() -> None:
    policy = resolve_provider_policy("qwen", "manual")

    assert policy == AsrProviderPolicy(
        transport="streaming",
        endpoint_authority="smart_turn",
        smart_turn_required=True,
        max_segment_ms=None,
        warm_transport_ms=25_000,
        replay_policy="preconnect_only",
    )


def test_provider_endpoint_is_the_logical_turn_authority() -> None:
    policy = resolve_provider_policy("soniox", "provider")

    assert policy.endpoint_authority == "provider"
    assert policy.smart_turn_required is False
    assert policy.replay_policy == "provider_managed"


@pytest.mark.parametrize("provider_key", ["glm", "gemini"])
def test_segmented_provider_always_requires_smart_turn(provider_key: str) -> None:
    policy = resolve_provider_policy(provider_key, "manual")

    assert policy.transport == "segmented"
    assert policy.endpoint_authority == "smart_turn"
    assert policy.smart_turn_required is True
    assert policy.max_segment_ms == 27_000
    assert policy.warm_transport_ms == 0


def test_unsupported_provider_mode_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="ASR_ENDPOINTING_NOT_SUPPORTED"):
        resolve_provider_policy("openai", "provider")
