from types import SimpleNamespace

from plugin.plugins.neko_live.core.co_stream_capabilities import (
    auto_enforcement_confirmed,
    configured_activation,
    normalize_activation_mode,
    registered_co_stream_capabilities,
    requested_activation,
)
from plugin.plugins.neko_live.core.contracts import LiveConfig


def test_capability_catalog_is_stable_unique_and_default_off() -> None:
    capabilities = registered_co_stream_capabilities()

    assert [capability.id for capability in capabilities] == ["host_pause_fill"]
    assert len({capability.id for capability in capabilities}) == len(capabilities)
    assert capabilities[0].config_key == "co_stream_host_pause_fill_activation"
    assert capabilities[0].requested_level == "L3"
    assert capabilities[0].default_activation == "off"
    assert capabilities[0].auto_consent_key == "co_stream_host_pause_fill_auto_consent_version"
    assert capabilities[0].required_auto_consent_version == 1


def test_activation_normalization_accepts_only_supported_modes() -> None:
    assert normalize_activation_mode(" manual ") == "off"
    assert normalize_activation_mode("CONDITIONAL_AUTO") == "conditional_auto"
    assert normalize_activation_mode("automatic") == "off"
    assert normalize_activation_mode(object()) == "off"


def test_capability_reads_normalized_config_activation() -> None:
    capability = registered_co_stream_capabilities()[0]

    assert configured_activation(LiveConfig(), capability) == "off"
    assert configured_activation(
        LiveConfig(co_stream_host_pause_fill_activation="manual"),  # type: ignore[arg-type]
        capability,
    ) == "off"


def test_conditional_auto_requires_matching_explicit_consent_version() -> None:
    capability = registered_co_stream_capabilities()[0]
    preview_only = LiveConfig(
        co_stream_host_pause_fill_activation="conditional_auto",
    )
    confirmed = LiveConfig(
        co_stream_host_pause_fill_activation="conditional_auto",
        co_stream_host_pause_fill_auto_consent_version=1,
    )

    assert requested_activation(preview_only, capability) == "conditional_auto"
    assert configured_activation(preview_only, capability) == "off"
    assert auto_enforcement_confirmed(preview_only, capability) is False
    assert configured_activation(confirmed, capability) == "conditional_auto"
    assert auto_enforcement_confirmed(confirmed, capability) is True

    wrong_version = LiveConfig(
        co_stream_host_pause_fill_activation="conditional_auto",
        co_stream_host_pause_fill_auto_consent_version=99,
    )
    assert configured_activation(wrong_version, capability) == "off"
    assert auto_enforcement_confirmed(wrong_version, capability) is False


def test_conditional_auto_rejects_coercive_consent_values() -> None:
    capability = registered_co_stream_capabilities()[0]
    config = SimpleNamespace(
        co_stream_host_pause_fill_activation="conditional_auto",
        co_stream_host_pause_fill_auto_consent_version=1.1,
    )

    assert configured_activation(config, capability) == "off"
    assert auto_enforcement_confirmed(config, capability) is False
