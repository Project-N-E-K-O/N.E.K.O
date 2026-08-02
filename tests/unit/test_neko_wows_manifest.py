"""The manifest and the entry class have to agree, and default to safe."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from plugin.plugins.neko_wows.domain.contracts import (
    ALL_CHANNEL_MODES,
    LANE_NORMAL,
    LANE_URGENT,
    WowsConfig,
)

PLUGIN_DIR = (
    Path(__file__).resolve().parents[2] / "plugin" / "plugins" / "neko_wows"
)
MANIFEST = PLUGIN_DIR / "plugin.toml"


@pytest.fixture(scope="module")
def manifest():
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


# --- identity ------------------------------------------------------------

def test_identity_is_consistent_with_the_folder(manifest):
    plugin = manifest["plugin"]
    assert plugin["id"] == PLUGIN_DIR.name
    assert plugin["entry"] == "plugin.plugins.neko_wows:NekoWowsPlugin"


def test_the_declared_entry_class_exists_and_is_a_plugin():
    from plugin.sdk.plugin import NEKO_PLUGIN_TAG, NekoPluginBase
    from plugin.plugins.neko_wows import NekoWowsPlugin

    assert issubclass(NekoWowsPlugin, NekoPluginBase)
    # The @neko_plugin tag is what the host loader looks for.
    assert getattr(NekoWowsPlugin, NEKO_PLUGIN_TAG, False) is True


def test_the_plugin_is_passive():
    """Its entries are panel actions; the Agent has nothing to route here."""
    assert tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["plugin"]["passive"] is True


def test_manual_start_and_store_enabled(manifest):
    assert manifest["plugin_runtime"]["auto_start"] is False
    assert manifest["plugin"]["store"]["enabled"] is True


# --- hosted UI -----------------------------------------------------------

def test_the_panel_surface_is_declared_and_present(manifest):
    ui = manifest["plugin"]["ui"]
    assert ui["enabled"] is True
    panel = ui["panel"][0]
    assert panel["context"] == "dashboard"
    assert set(panel["permissions"]) == {"state:read", "action:call"}
    assert (PLUGIN_DIR / panel["entry"]).is_file()


def test_the_panel_modules_it_imports_all_exist():
    """Hosted TSX bundles relative imports; a missing one is a 404 at runtime.

    The list is read out of the entry file rather than hardcoded, so adding a page
    cannot quietly escape this check.
    """
    import re

    panel = (PLUGIN_DIR / "ui" / "panel.tsx").read_text(encoding="utf-8")
    specifiers = set(re.findall(r"""from\s+["'](\./[^"']+)["']""", panel))
    assert specifiers, "the panel should import its sections from sibling modules"

    for specifier in specifiers:
        stem = specifier.removeprefix("./")
        candidates = [
            PLUGIN_DIR / "ui" / f"{stem}{suffix}" for suffix in (".tsx", ".ts")
        ]
        assert any(path.is_file() for path in candidates), specifier


def test_all_six_pages_are_wired_into_the_panel():
    panel = (PLUGIN_DIR / "ui" / "panel.tsx").read_text(encoding="utf-8")
    for page_id in ("overview", "timeline", "documents", "prompts",
                    "preferences", "diagnostics"):
        assert f'id: "{page_id}"' in panel, page_id


def test_the_declared_ui_actions_exist_as_plugin_entries():
    from plugin.sdk.plugin import NekoPluginBase  # noqa: F401
    from plugin.plugins.neko_wows import NekoWowsPlugin

    for action_id in ("set_dry_run", "set_channel_mode", "pause", "resume",
                      "reconnect", "clear_timeline", "status",
                      "pick_documents", "import_document_text",
                      "delete_document", "clear_documents",
                      "save_prompt_revision", "activate_prompt_revision",
                      "reset_prompts", "preview_prompt",
                      "set_intrusion_mode", "set_category_enabled",
                      "set_lane_enabled", "set_lane_timing"):
        handler = getattr(NekoWowsPlugin, action_id, None)
        assert callable(handler), action_id


def test_every_action_the_panel_calls_exists_on_the_plugin():
    """A panel button wired to a missing entry fails only at click time."""
    import re

    from plugin.plugins.neko_wows import NekoWowsPlugin

    called: set[str] = set()
    for name in ("panel", "documents", "prompts", "preferences", "diagnostics"):
        source = (PLUGIN_DIR / "ui" / f"{name}.tsx").read_text(encoding="utf-8")
        called.update(re.findall(r"""actionId=["']([\w-]+)["']""", source))
        called.update(re.findall(r"""call\(\s*["']([\w-]+)["']""", source))
        called.update(re.findall(r"""api\.call\(\s*["']([\w-]+)["']""", source))

    assert called, "the panel should call at least one action"
    for action_id in sorted(called):
        assert callable(getattr(NekoWowsPlugin, action_id, None)), action_id


# --- configuration defaults ---------------------------------------------

def test_the_manifest_ships_with_dry_run_on(manifest):
    assert manifest["neko_wows"]["dry_run"] is True


def test_the_manifest_section_parses_into_the_config(manifest):
    cfg = WowsConfig.from_mapping(manifest["neko_wows"])
    assert cfg.dry_run is True
    assert cfg.service_url == "http://127.0.0.1:8111"
    assert cfg.channel_mode in ALL_CHANNEL_MODES
    assert cfg.ttl_for(LANE_URGENT) == 8.0
    assert cfg.min_gap_for(LANE_NORMAL) == 18.0


def test_a_corrupt_config_falls_back_to_dry_run():
    for broken in (None, {}, {"dry_run": "yes please"}, {"dry_run": 0}):
        assert WowsConfig.from_mapping(broken).dry_run is True


def test_out_of_range_values_are_clamped_not_trusted():
    cfg = WowsConfig.from_mapping({
        "rest_poll_interval_seconds": -5.0,
        "urgent_ttl_seconds": 99999.0,
        "safety_failure_limit": 0,
        "channel_mode": "triple",
    })
    assert cfg.rest_poll_interval_seconds >= 0.05
    assert cfg.urgent_ttl_seconds <= 120.0
    assert cfg.safety_failure_limit >= 1
    assert cfg.channel_mode == "dual"


def test_reconnect_window_bounds_stay_ordered():
    cfg = WowsConfig.from_mapping({
        "ws_reconnect_min_seconds": 20.0,
        "ws_reconnect_max_seconds": 5.0,
    })
    assert cfg.ws_reconnect_max_seconds >= cfg.ws_reconnect_min_seconds


def test_low_health_thresholds_are_sorted_high_to_low():
    cfg = WowsConfig.from_mapping({"low_health_ratios": [0.1, 0.5, 0.3, "bad"]})
    assert cfg.low_health_ratios == (0.5, 0.3, 0.1)


def test_the_manifest_declares_every_key_the_config_reads(manifest):
    """A config field with no manifest entry is a setting nobody can find."""
    declared = set(manifest["neko_wows"])
    parsed = {
        name for name in vars(WowsConfig()) if not name.startswith("_")
    }
    # `enabled` and every tunable should be visible in the shipped manifest.
    missing = sorted(parsed - declared)
    assert missing == [], f"undocumented config keys: {missing}"


def test_document_and_preference_defaults_are_safe(manifest):
    cfg = WowsConfig.from_mapping(manifest["neko_wows"])
    assert cfg.tactics_min_term_hits >= 2, "the injection gate must stay strict"
    assert cfg.tactics_chunk_overlap <= cfg.tactics_chunk_chars // 2
    assert cfg.dialogue_intrusion_mode in ("no_interrupt", "critical_only",
                                           "allow_interrupt")
    assert cfg.disabled_categories == ()
    assert cfg.disabled_lanes == ()


def test_the_chunk_overlap_cannot_exceed_half_the_chunk():
    cfg = WowsConfig.from_mapping({
        "tactics_chunk_chars": 400, "tactics_chunk_overlap": 900})
    assert cfg.tactics_chunk_overlap <= 200


# --- i18n ----------------------------------------------------------------

def test_declared_locales_exist(manifest):
    locales_dir = PLUGIN_DIR / manifest["plugin"]["i18n"]["locales_dir"]
    default_locale = manifest["plugin"]["i18n"]["default_locale"]
    assert (locales_dir / f"{default_locale}.json").is_file()
