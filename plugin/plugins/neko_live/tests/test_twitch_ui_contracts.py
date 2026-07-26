from __future__ import annotations

import json
from pathlib import Path

from plugin.plugins.neko_live import NekoLivePlugin
from plugin.plugins.neko_live.core.runtime_dashboard_actions import dashboard_actions


ROOT = Path(__file__).resolve().parents[1]


def test_twitch_network_actions_declare_entry_timeouts_above_host_default() -> None:
    expected = {
        "twitch_device_authorization_start": 25.0,
        "twitch_device_authorization_check": 40.0,
        "twitch_credential_validate": 55.0,
    }

    for method_name, timeout in expected.items():
        method = getattr(NekoLivePlugin, method_name)
        meta = getattr(method, "__neko_event_meta__")
        assert meta.timeout == timeout


def test_twitch_auth_actions_are_exposed_to_hosted_ui() -> None:
    expected = {
        "twitch_device_authorization_start",
        "twitch_device_authorization_check",
        "twitch_device_authorization_cancel",
        "twitch_login_status",
        "twitch_credential_validate",
        "twitch_logout",
    }
    actions = {item["id"] for item in dashboard_actions()}
    source = (ROOT / "__init__.py").read_text(encoding="utf-8")

    assert expected <= actions
    for action_id in expected:
        assert f'@ui.action(id="{action_id}"' in source
        assert f'@plugin_entry(\n        id="{action_id}"' in source or f'@plugin_entry(id="{action_id}"' in source


def test_both_panels_expose_twitch_account_channel_and_device_flow_controls() -> None:
    required = {
        'value: "twitch"',
        'panel.platform.twitch',
        'panel.fields.twitchClientId',
        'panel.fields.twitchRoom',
        'panel.placeholders.twitchRoom',
        'twitch_device_authorization_start',
        'twitch_device_authorization_check',
        'twitch_device_authorization_cancel',
        'twitch_login_status',
        'twitch_credential_validate',
        'twitch_logout',
        'verification_uri',
        'user_code',
        'result.started === true && result.pending === true',
        'authorization_state === "unverified"',
        'window.setTimeout',
        'twitchPollIntervalRef.current = safeInterval(result.interval)',
        'schedule(twitchPollBackoffRef.current || twitchPollIntervalRef.current)',
        'result.cancelled === true',
        'target="_blank"',
        'twitchCancelAuthorization',
    }
    for filename in ("panel.tsx", "panel_compat.tsx"):
        source = (ROOT / "ui" / filename).read_text(encoding="utf-8")
        missing = required - {item for item in required if item in source}
        assert not missing, f"{filename} missing Twitch UI contracts: {sorted(missing)}"
        assert "function twitchCheckAuthorization" not in source
        assert "function twitchStatus" not in source
        assert "function twitchValidate" not in source
        visibility_handler = source.split("const handleTwitchVisibilityChange", 1)[1].split(
            'document.addEventListener("visibilitychange", handleTwitchVisibilityChange)', 1
        )[0]
        assert "twitchAuthState.interval" not in visibility_handler


def test_twitch_config_fields_exist_in_modular_and_compat_panel_defaults() -> None:
    state_source = (ROOT / "ui" / "panel_state.ts").read_text(encoding="utf-8")
    compat_source = (ROOT / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    assert 'twitch_client_id?: string' in state_source
    assert 'twitch_client_id: ""' in state_source
    assert 'twitch_client_id?: string' in compat_source
    assert 'twitch_client_id: ""' in compat_source


def test_twitch_validation_copy_requires_a_configured_client_id() -> None:
    for filename in ("panel.tsx", "panel_compat.tsx"):
        source = (ROOT / "ui" / filename).read_text(encoding="utf-8")
        assert 'const twitchClientIdConfigured = Boolean(String(configForm.values.twitch_client_id || "").trim())' in source
        assert "const twitchAuthorizationValidating = twitchAuthorizationUnverified && twitchClientIdConfigured" in source
        assert 'twitchAuthorizationUnverified ? t("panel.twitchAuth.validating")' not in source


def test_all_locales_define_twitch_panel_action_and_entry_copy() -> None:
    required = {
        "panel.platform.twitch",
        "panel.fields.twitchClientId",
        "panel.fields.twitchRoom",
        "panel.placeholders.twitchClientId",
        "panel.placeholders.twitchRoom",
        "panel.twitchAuth.authorized",
        "panel.twitchAuth.notAuthorized",
        "panel.twitchAuth.unverified",
        "panel.twitchAuth.deviceHint",
        "panel.twitchAuth.waiting",
        "panel.twitchAuth.waitingCountdown",
        "panel.twitchAuth.validating",
        "panel.twitchAuth.cancelled",
        "panel.twitchAuth.expired",
        "panel.twitchAuth.userCode",
        "panel.twitchAuth.verificationUri",
        "panel.actions.twitchAuthorize",
        "panel.actions.twitchCancelAuthorization",
        "panel.actions.twitchCheckAuthorization",
        "panel.actions.twitchStatus",
        "panel.actions.twitchValidate",
        "panel.actions.twitchLogout",
        "actions.twitch_device_authorization_start.label",
        "actions.twitch_device_authorization_check.label",
        "actions.twitch_device_authorization_cancel.label",
        "actions.twitch_login_status.label",
        "actions.twitch_credential_validate.label",
        "actions.twitch_logout.label",
        "entries.twitch_device_authorization_start.name",
        "entries.twitch_device_authorization_start.description",
        "entries.twitch_device_authorization_check.name",
        "entries.twitch_device_authorization_check.description",
        "entries.twitch_device_authorization_cancel.name",
        "entries.twitch_device_authorization_cancel.description",
        "entries.twitch_login_status.name",
        "entries.twitch_login_status.description",
        "entries.twitch_credential_validate.name",
        "entries.twitch_credential_validate.description",
        "entries.twitch_logout.name",
        "entries.twitch_logout.description",
    }
    locale_files = sorted((ROOT / "i18n").glob("*.json"))

    assert len(locale_files) == 8
    for locale_path in locale_files:
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required - set(payload)
        assert not missing, f"{locale_path.name} missing Twitch keys: {sorted(missing)}"
