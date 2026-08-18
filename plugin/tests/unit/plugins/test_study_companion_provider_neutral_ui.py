from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.plugins.study_companion.entry_status_entries import _StatusEntriesMixin
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR


pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "es", "pt", "ru")

_MODEL_RUNTIME_KEYS = {
    "ui.settings.model_runtime.text",
    "ui.settings.model_runtime.vision",
    "ui.settings.model_runtime.managed_by_neko",
    "ui.settings.model_runtime.meta",
    "ui.settings.model_runtime.not_configured",
    "ui.settings.model_runtime.protocol_unknown",
    "ui.settings.model_runtime.ready",
    "ui.settings.model_runtime.configured_vision_unknown",
    "ui.settings.model_runtime.unsupported",
    "ui.settings.model_runtime.credential_missing",
}

_PROVIDER_NEUTRAL_DIAGNOSTIC_KEYS = {
    "ui.error.llm_unsupported_provider",
    "ui.error.llm_context_limit_exceeded",
    "ui.error.llm_vision_not_supported",
    "ui.error.llm_agent_quota_exceeded",
    "ui.error.llm_invalid_endpoint",
    "ui.error.llm_invalid_request",
}


def _bundles() -> dict[str, dict[str, object]]:
    return {
        locale: json.loads(
            (PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
        )
        for locale in LOCALES
    }


def test_provider_neutral_model_runtime_keys_exist_in_all_locales() -> None:
    bundles = _bundles()
    required = _MODEL_RUNTIME_KEYS | _PROVIDER_NEUTRAL_DIAGNOSTIC_KEYS
    baseline = set(bundles["en"])

    assert required <= baseline
    for locale, bundle in bundles.items():
        assert set(bundle) == baseline, f"{locale} locale keys differ from en"
        assert all(
            isinstance(bundle[key], str) and str(bundle[key]).strip()
            for key in required
        ), locale


def test_provider_failure_copy_does_not_hard_code_qwen() -> None:
    bundles = _bundles()
    checked_keys = {
        "ui.error.llm_rate_limited",
        "ui.error.llm_authentication_failed",
        "ui.error.llm_model_not_supported",
        "ui.error.llm_provider_unavailable",
        "ui.error.llm_call_failed",
        "ui.error.document_analysis_rate_limited",
        "ui.error.document_analysis_authentication_failed",
        "ui.error.document_analysis_provider_unavailable",
        "ui.error.document_analysis_invalid_endpoint",
        "ui.error.document_analysis_invalid_request",
        "ui.error.document_analysis_llm_call_failed",
    }

    for locale, bundle in bundles.items():
        for key in checked_keys:
            assert "qwen" not in str(bundle[key]).casefold(), (locale, key)


def test_static_and_hosted_ui_use_same_provider_neutral_diagnostics() -> None:
    static_source = "\n".join(
        (PLUGIN_DIR / "static" / name).read_text(encoding="utf-8")
        for name in ("main.js", "document-controller.js", "model-runtime.js")
    )
    hosted_source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )
    diagnostics = {
        "unsupported_provider",
        "context_limit_exceeded",
        "vision_not_supported",
        "agent_quota_exceeded",
        "invalid_endpoint",
        "invalid_request",
    }

    for source in (static_source, hosted_source):
        assert diagnostics <= {value for value in diagnostics if value in source}

    assert "model_runtime" in static_source
    assert "study_get_settings_config" in static_source


def test_hosted_model_runtime_settings_entry_is_exposed_as_ui_action() -> None:
    action_meta = getattr(
        _StatusEntriesMixin.study_get_settings_config, UI_ACTION_META_ATTR, None
    )

    assert isinstance(action_meta, dict)


def test_model_runtime_ui_does_not_render_credentials_or_endpoint() -> None:
    static_source = "\n".join(
        (PLUGIN_DIR / "static" / name).read_text(encoding="utf-8")
        for name in ("main.js", "model-runtime.js")
    )
    hosted_source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )

    model_runtime_region = static_source[static_source.find("model_runtime") :]
    assert model_runtime_region
    assert "credential_configured" in model_runtime_region
    assert "api_key" not in model_runtime_region.casefold()
    assert "base_url" not in model_runtime_region.casefold()

    # The Hosted surface currently consumes tutor diagnostics only. If it adds a
    # runtime card later, that card must follow the same no-secret contract.
    if "model_runtime" in hosted_source:
        hosted_region = hosted_source[hosted_source.find("model_runtime") :]
        # `/api_key` is the intended host settings route, not a credential field.
        sanitized_hosted = hosted_region.replace("/api_key", "")
        assert "api_key" not in sanitized_hosted.casefold()
        assert "base_url" not in hosted_region.casefold()


def test_hosted_model_settings_link_uses_the_host_external_bridge() -> None:
    hosted_source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(
        encoding="utf-8"
    )

    assert "neko-hosted-surface-open-external" in hosted_source
    assert "payload: { url }" in hosted_source
    assert "openHostedExternalUrl('/api_key')" in hosted_source
    assert '<a href="/api_key"' not in hosted_source


def test_plugin_manager_vite_proxies_exact_model_settings_route() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    vite_source = (
        repo_root / "frontend" / "plugin-manager" / "vite.config.ts"
    ).read_text(encoding="utf-8")

    assert "'^/api_key(?:\\\\?.*)?$'" in vite_source
    assert "target: BACKEND_TARGET" in vite_source
