from __future__ import annotations

import json
import tomllib
from pathlib import Path

from plugin.plugins.neko_roast import NekoRoastPlugin
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR


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
    assert "panel.liveStatusReason" in source
    assert "live_state" in source
    assert "panel.liveModeRole" in source
    assert "panel.liveState" in source
    assert "panel.idleHostingCandidate" in source
    assert "activity_level" in source
    assert "panel.activity." in source
    assert "speech_explanation" in source
    assert "panel.speechExplanation." in source
    assert "idle_hosting_status" in source
    assert "panel.idleHostingStatus" in source
    assert "last_activity_age_sec" in source
    assert "engaged_threshold_seconds" in source
    assert "idle_threshold_seconds" in source
    assert "panel.liveState.lastActivityAge" in source
    assert "panel.liveState.quietAfter" in source
    assert "panel.liveState.idleAfter" in source
    assert "response_latency_ms" in source
    assert "panel.columns.responseLatency" in source


def test_panel_renders_interaction_module_split_and_speaking_decision():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "panel.interaction.currentDecision.title" in source
    assert "panel.interaction.currentDecision.latestEvent" in source
    assert "panel.interaction.currentDecision.route" in source
    assert "response_module" in source
    assert "event_signal" in source
    assert "panel.interaction.currentDecision.eventSignal" in source
    assert "panel.interaction.currentDecision.lastResult" in source
    assert "avatar_roast" in source
    assert "danmaku_response" in source
    assert "warmup_hosting" in source
    assert "idle_hosting" in source
    assert "active_engagement" in source
    assert "panel.interaction.module.avatarRoast.desc" in source
    assert "panel.interaction.module.danmakuResponse.desc" in source
    assert "panel.interaction.module.warmupHosting.desc" in source
    assert "panel.interaction.module.idleHosting.desc" in source
    assert "panel.interaction.module.activeEngagement.desc" in source


def test_panel_hides_internal_module_ids_from_streamer_module_cards():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert 'title={`${module.id} · ${t("panel.interaction.module.avatarRoast.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.danmakuResponse.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.warmupHosting.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.idleHosting.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.activeEngagement.title")}`}' not in source


def test_panel_dynamic_labels_have_streamer_facing_fallbacks():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "panelText(" in source
    assert 'solo_idle: "猫猫独播已冷场，可以冷场陪播。"' in source
    assert 'waiting_for_viewer_or_idle_slot: "正在等待观众接话或冷场补位时机。"' in source
    assert "t(`panel.liveDirector.reason.${liveDirectorReason}`)" not in source
    assert "t(`panel.speechExplanation.reason.${speechReason}`)" not in source


def test_panel_recent_results_show_route_and_signal_labels():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "panel.columns.responseModule" in source
    assert "panel.columns.eventSignal" in source
    assert "eventSignalLabel" in source
    assert "panel.eventSignal.gift_signal" in source
    assert "panel.eventSignal.super_chat_signal" in source
    assert "panel.eventSignal.danmaku_signal" in source


def test_panel_shows_independent_pacing_and_active_topic_observability():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "last_viewer_activity_age_sec" in source
    assert "last_output_age_sec" in source
    assert "panel.liveState.lastViewerActivityAge" in source
    assert "panel.liveState.lastOutputAge" in source
    assert "topic_source" in source
    assert "topic_shape" in source
    assert "topic_hook" in source
    assert "panel.interaction.currentDecision.topic" in source
    assert "host_beat_shape" in source
    assert "host_beat_title" in source
    assert "panel.interaction.currentDecision.hostBeat" in source

    required_keys = {
        "panel.liveState.lastViewerActivityAge",
        "panel.liveState.lastOutputAge",
        "panel.interaction.currentDecision.topic",
        "panel.interaction.currentDecision.hostBeat",
    }
    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys - set(data)
        assert not missing, f"{locale_path.name} missing UI observability labels: {sorted(missing)}"


def test_panel_renders_solo_stream_test_readiness():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "solo_test_readiness" in source
    assert "panel.soloTestReadiness.title" in source
    assert "panel.soloTestReadiness.summary" in source
    assert "panel.soloTestReadiness.item" in source
    assert "panel.soloTestReadiness.profileCount" in source
    assert "clearViewerProfiles" in source
    assert "panel.messages.clearViewerProfilesConfirm" in source


def test_panel_confirms_before_clearing_viewer_profiles():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert "async function clearViewerProfiles()" in source
    assert "window.confirm" in source
    assert 'callSimple("clear_viewer_profiles")' in source
    assert 'onClick={clearViewerProfiles}' in source


def test_once_per_uid_copy_scopes_to_first_appearance_roast():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))

    assert data["panel.fields.oncePerUid"] == "每个观众只做一次出场锐评"
    assert "后续弹幕仍会正常接话" in data["panel.fields.oncePerUidHint"]
    assert data["panel.interaction.tags.oncePerUid"] == "出场锐评一次"


def test_interaction_module_titles_do_not_expose_internal_ids():
    root = Path(__file__).resolve().parents[1]
    title_keys = {
        "panel.interaction.module.avatarRoast.title",
        "panel.interaction.module.danmakuResponse.title",
        "panel.interaction.module.warmupHosting.title",
        "panel.interaction.module.idleHosting.title",
        "panel.interaction.module.activeEngagement.title",
    }
    forbidden = ("avatar_roast", "danmaku_response", "warmup_hosting", "idle_hosting", "active_engagement")

    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        leaked = {
            key: data.get(key)
            for key in title_keys
            if any(token in str(data.get(key, "")) for token in forbidden)
        }
        assert not leaked, f"{locale_path.name} exposes internal IDs: {leaked}"


def test_chinese_panel_copy_has_no_question_mark_placeholders():
    root = Path(__file__).resolve().parents[1]
    checked_prefixes = ("panel.", "entries.trigger_warmup_hosting")
    bad: dict[str, dict[str, str]] = {}

    for locale_name in ("zh-CN.json", "zh-TW.json"):
        data = json.loads((root / "i18n" / locale_name).read_text(encoding="utf-8"))
        bad_values = {
            key: value
            for key, value in data.items()
            if key.startswith(checked_prefixes) and isinstance(value, str) and "??" in value
        }
        if bad_values:
            bad[locale_name] = bad_values

    assert not bad


def test_independent_mode_plan_keeps_solo_validation_checklist():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "independent-mode-product-plan.md").read_text(encoding="utf-8")

    assert "## Solo Stream Validation Checklist" in source
    assert "Streamer trust" in source
    assert "Dead-air control" in source
    assert "Danmaku continuity" in source
    assert "Pacing safety" in source
    assert "Persona fit" in source


def test_trigger_idle_hosting_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.trigger_idle_hosting, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "trigger_idle_hosting"
    assert meta["group"] == "safety"
    assert meta["refresh_context"] is True


def test_trigger_warmup_hosting_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.trigger_warmup_hosting, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "trigger_warmup_hosting"
    assert meta["group"] == "safety"
    assert meta["refresh_context"] is True


def test_trigger_active_engagement_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.trigger_active_engagement, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "trigger_active_engagement"
    assert meta["group"] == "safety"
    assert meta["refresh_context"] is True


def test_clear_viewer_profiles_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.clear_viewer_profiles, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "clear_viewer_profiles"
    assert meta["group"] == "developer"
    assert meta["refresh_context"] is True


def test_all_locales_define_live_status_summary_labels():
    root = Path(__file__).resolve().parents[1]
    required_keys = {
        "panel.liveStatusSummary.title",
        "panel.liveStatusSummary.ready_to_stream",
        "panel.liveStatusSummary.test_only",
        "panel.liveStatusSummary.temporarily_not_speaking",
        "panel.liveStatusSummary.cannot_stream",
        "panel.liveStatusSummary.cooldown",
        "panel.columns.responseLatency",
        "panel.columns.responseModule",
        "panel.columns.eventSignal",
        "panel.liveStatusReason.ready",
        "panel.liveStatusReason.dry_run",
        "panel.liveStatusReason.manual_paused",
        "panel.liveStatusReason.room_not_configured",
        "panel.liveStatusReason.live_ingest_disconnected",
        "panel.liveStatusReason.cooldown",
        "panel.liveStatusReason.safety_tripped",
        "panel.liveStatusReason.safety_degraded",
        "panel.liveStatusReason.output_channel_unavailable",
        "panel.liveStatusReason.all_ready",
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
        "panel.liveState.warmup",
        "panel.liveState.quiet",
        "panel.liveState.idle",
        "panel.liveState.paused",
        "panel.liveState.blocked",
        "panel.liveStateReason.recent_activity",
        "panel.liveStateReason.solo_stream_warmup",
        "panel.liveStateReason.quiet_activity_gap",
        "panel.liveStateReason.low_activity",
        "panel.liveStateReason.no_recent_activity",
        "panel.liveStateReason.manual_paused",
        "panel.liveStateReason.blocked_by_live_status",
        "panel.liveState.lastActivityAge",
        "panel.liveState.quietAfter",
        "panel.liveState.idleAfter",
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
        "panel.idleHostingStatus.reason.solo_idle_ready",
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
        "panel.speechExplanation.summary.waiting",
        "panel.speechExplanation.reason.ready",
        "panel.speechExplanation.reason.dry_run",
        "panel.speechExplanation.reason.manual_paused",
        "panel.speechExplanation.reason.room_not_configured",
        "panel.speechExplanation.reason.live_ingest_disconnected",
        "panel.speechExplanation.reason.cooldown",
        "panel.speechExplanation.reason.safety_tripped",
        "panel.speechExplanation.reason.safety_degraded",
        "panel.speechExplanation.reason.output_channel_unavailable",
        "panel.speechExplanation.reason.solo_stream_warmup",
        "panel.speechExplanation.reason.idle_hosting_candidate",
        "panel.speechExplanation.reason.quiet_activity_gap",
        "panel.speechExplanation.reason.no_recent_activity",
        "panel.speechExplanation.reason.waiting_for_viewer_or_idle_slot",
        "panel.speechExplanation.reason.recent_output",
        "panel.speechExplanation.reason.recently_skipped",
        "panel.speechExplanation.reason.failed",
        "panel.speechExplanation.reason.dispatcher.dry_run",
        "panel.interaction.currentDecision.title",
        "panel.interaction.currentDecision.subtitle",
        "panel.interaction.currentDecision.latestEvent",
        "panel.interaction.currentDecision.route",
        "panel.interaction.currentDecision.eventSignal",
        "panel.interaction.currentDecision.lastResult",
        "panel.interaction.currentDecision.skipReason",
        "panel.interaction.currentDecision.noResult",
        "panel.liveDirector.nextAutoAction",
        "panel.liveDirector.cooldown",
        "panel.liveDirector.action.none",
        "panel.liveDirector.action.warmup_hosting",
        "panel.liveDirector.action.active_engagement",
        "panel.liveDirector.action.idle_hosting",
        "panel.liveDirector.reason.waiting_for_viewer",
        "panel.liveDirector.reason.companion_mode",
        "panel.liveDirector.reason.paused",
        "panel.liveDirector.reason.blocked",
        "panel.liveDirector.reason.recent_activity",
        "panel.liveDirector.reason.solo_quiet",
        "panel.liveDirector.reason.solo_warmup",
        "panel.liveDirector.reason.solo_idle",
        "panel.liveDirector.reason.solo_idle_ready",
        "panel.liveDirector.reason.minimum_interval",
        "panel.liveDirector.reason.recent_danmaku_output",
        "panel.liveDirector.reason.not_candidate",
        "panel.liveDirector.reason.auto_disabled",
        "panel.liveDirector.reason.active_engagement_not_ready",
        "panel.liveDirector.reason.warmup_hosting_not_ready",
        "panel.liveDirector.reason.idle_hosting_not_ready",
        "panel.interaction.module.avatarRoast.title",
        "panel.interaction.module.avatarRoast.desc",
        "panel.interaction.module.avatarRoast.badge",
        "panel.interaction.module.danmakuResponse.title",
        "panel.interaction.module.danmakuResponse.desc",
        "panel.interaction.module.danmakuResponse.badge",
        "panel.interaction.module.warmupHosting.title",
        "panel.interaction.module.warmupHosting.desc",
        "panel.interaction.module.warmupHosting.badge",
        "panel.warmupHostingCandidate.true",
        "panel.warmupHostingCandidate.false",
        "panel.interaction.module.idleHosting.title",
        "panel.interaction.module.idleHosting.desc",
        "panel.interaction.module.idleHosting.badge",
        "panel.interaction.module.activeEngagement.title",
        "panel.interaction.module.activeEngagement.desc",
        "panel.interaction.module.activeEngagement.badge",
        "panel.soloTestReadiness.title",
        "panel.soloTestReadiness.summary.ready_for_test",
        "panel.soloTestReadiness.summary.ready_for_live_test",
        "panel.soloTestReadiness.summary.ready",
        "panel.soloTestReadiness.summary.not_solo_stream",
        "panel.soloTestReadiness.summary.live_not_ready",
        "panel.soloTestReadiness.profileCount",
        "panel.soloTestReadiness.status.ready",
        "panel.soloTestReadiness.status.blocked",
        "panel.soloTestReadiness.status.observed",
        "panel.soloTestReadiness.status.warning",
        "panel.soloTestReadiness.item.preflight",
        "panel.soloTestReadiness.item.test_isolation",
        "panel.soloTestReadiness.item.warmup_hosting",
        "panel.soloTestReadiness.item.avatar_roast",
        "panel.soloTestReadiness.item.danmaku_response",
        "panel.soloTestReadiness.item.active_engagement",
        "panel.soloTestReadiness.item.idle_hosting",
        "panel.soloTestReadiness.item.pacing_control",
        "panel.activeEngagementCandidate.true",
        "panel.activeEngagementCandidate.false",
        "panel.activeEngagementStatus.reason.eligible",
        "panel.activeEngagementStatus.reason.deferred",
        "panel.activeEngagementStatus.reason.not_solo_stream",
        "panel.activeEngagementStatus.reason.paused",
        "panel.activeEngagementStatus.reason.blocked",
        "panel.activeEngagementStatus.reason.not_quiet",
        "panel.activeEngagementStatus.reason.cooldown",
        "panel.activeEngagementStatus.reason.minimum_interval",
        "panel.activeEngagementStatus.reason.live_status_not_ready",
        "panel.activeEngagementStatus.minimumIntervalRemaining",
        "panel.activeEngagementStatus.recentDanmakuWait",
        "panel.actions.triggerActiveEngagement",
        "panel.actions.triggerWarmupHosting",
        "panel.actions.clearViewerProfiles",
        "panel.messages.clearViewerProfilesConfirm",
        "actions.clear_viewer_profiles.label",
        "entries.clear_viewer_profiles.name",
        "entries.clear_viewer_profiles.description",
        "entries.trigger_warmup_hosting.name",
        "entries.trigger_warmup_hosting.description",
        "entries.trigger_active_engagement.name",
        "entries.trigger_active_engagement.description",
        "panel.interaction.tags.currentDanmaku",
        "panel.interaction.tags.noAvatarCount",
        "panel.interaction.tags.safetyRequired",
        "panel.interaction.tags.oncePerUid",
        "panel.interaction.tags.future",
        "panel.interaction.tags.cooldown",
        "panel.interaction.tags.activeQuestion",
        "panel.interaction.tags.openingBeat",
        "panel.eventSignal.danmaku_signal",
        "panel.eventSignal.gift_signal",
        "panel.eventSignal.super_chat_signal",
        "panel.eventSignal.unknown",
    }

    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys.difference(data)
        assert not missing, f"{locale_path.name} missing keys: {sorted(missing)}"
