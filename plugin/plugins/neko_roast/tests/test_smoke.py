from __future__ import annotations

import json
import tomllib
from pathlib import Path


def test_neko_roast_manifest_smoke():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["plugin"]["id"] == "neko_roast"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_roast:NekoRoastPlugin"
    assert (root / "ui" / "panel.tsx").is_file()


def test_panel_renders_live_status_summary():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "live_status" in source
    assert "panel.liveStatusSummary." in source
    assert "panel.liveStatusReason." in source


def test_all_locales_define_live_status_summary_labels():
    root = Path(__file__).resolve().parents[1]
    required_keys = {
        "panel.liveStatusSummary.title",
        "panel.liveStatusSummary.ready_to_stream",
        "panel.liveStatusSummary.test_only",
        "panel.liveStatusSummary.temporarily_not_speaking",
        "panel.liveStatusSummary.cannot_stream",
        "panel.liveStatusSummary.cooldown",
        "panel.liveStatusReason.ready",
        "panel.liveStatusReason.dry_run",
        "panel.liveStatusReason.manual_paused",
        "panel.liveStatusReason.room_not_configured",
        "panel.liveStatusReason.live_ingest_disconnected",
        "panel.liveStatusReason.cooldown",
        "panel.liveStatusReason.safety_tripped",
        "panel.liveStatusReason.safety_degraded",
    }

    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys.difference(data)
        assert not missing, f"{locale_path.name} missing keys: {sorted(missing)}"
