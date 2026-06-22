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
    assert "live_state" in source
    assert "panel.liveModeRole." in source
    assert "panel.liveState." in source
    assert "panel.idleHostingCandidate." in source
    assert "activity_level" in source
    assert "panel.activity." in source
    assert "speech_explanation" in source
    assert "panel.speechExplanation." in source
    assert "idle_hosting_status" in source
    assert "panel.idleHostingStatus." in source


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
        "panel.liveModeRole.co_stream",
        "panel.liveModeRole.solo_stream",
        "panel.fields.activityLevel",
        "panel.activity.quiet",
        "panel.activity.standard",
        "panel.activity.active",
        "panel.liveModeRoleHint.companion",
        "panel.liveModeRoleHint.solo_host",
        "panel.liveState.title",
        "panel.liveState.engaged",
        "panel.liveState.quiet",
        "panel.liveState.idle",
        "panel.liveState.paused",
        "panel.liveState.blocked",
        "panel.liveStateReason.recent_activity",
        "panel.liveStateReason.quiet_activity_gap",
        "panel.liveStateReason.no_recent_activity",
        "panel.liveStateReason.manual_paused",
        "panel.liveStateReason.blocked_by_live_status",
        "panel.idleHostingCandidate.true",
        "panel.idleHostingCandidate.false",
        "panel.idleHostingStatus.title",
        "panel.idleHostingStatus.cooldown",
        "panel.idleHostingStatus.minInterval",
        "panel.idleHostingStatus.eligible.true",
        "panel.idleHostingStatus.eligible.false",
        "panel.idleHostingStatus.reason.eligible",
        "panel.idleHostingStatus.reason.not_candidate",
        "panel.idleHostingStatus.reason.minimum_interval",
        "panel.idleHostingStatus.reason.auto_disabled",
        "panel.speechExplanation.title",
        "panel.speechExplanation.lastResult",
        "panel.speechExplanation.summary.ready",
        "panel.speechExplanation.summary.test_only",
        "panel.speechExplanation.summary.temporarily_not_speaking",
        "panel.speechExplanation.summary.cannot_stream",
        "panel.speechExplanation.summary.waiting_for_activity",
        "panel.speechExplanation.summary.recently_spoke",
        "panel.speechExplanation.summary.recently_skipped",
        "panel.speechExplanation.summary.failed",
        "panel.speechExplanation.reason.ready",
        "panel.speechExplanation.reason.dry_run",
        "panel.speechExplanation.reason.manual_paused",
        "panel.speechExplanation.reason.room_not_configured",
        "panel.speechExplanation.reason.live_ingest_disconnected",
        "panel.speechExplanation.reason.cooldown",
        "panel.speechExplanation.reason.safety_tripped",
        "panel.speechExplanation.reason.safety_degraded",
        "panel.speechExplanation.reason.idle_hosting_candidate",
        "panel.speechExplanation.reason.quiet_activity_gap",
        "panel.speechExplanation.reason.no_recent_activity",
        "panel.speechExplanation.reason.recent_output",
        "panel.speechExplanation.reason.recently_skipped",
        "panel.speechExplanation.reason.failed",
        "panel.speechExplanation.reason.dispatcher.dry_run",
    }

    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys.difference(data)
        assert not missing, f"{locale_path.name} missing keys: {sorted(missing)}"
