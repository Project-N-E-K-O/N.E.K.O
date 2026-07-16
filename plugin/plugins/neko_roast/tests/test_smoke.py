from __future__ import annotations

import json
import tomllib
from pathlib import Path

from plugin.plugins.neko_roast import NekoRoastPlugin
from plugin.plugins.neko_roast.core.runtime_dashboard_actions import dashboard_actions
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR
from plugin.sdk.shared.constants import EVENT_META_ATTR


def _panel_ui_source(root: Path) -> str:
    return "\n".join(
        (root / "ui" / name).read_text(encoding="utf-8")
        for name in ("panel.tsx", "panel_data_sections.tsx")
    )


def test_neko_roast_manifest_smoke():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["plugin"]["id"] == "neko_roast"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_roast:NekoRoastPlugin"
    assert manifest["neko_roast"]["roast_strength"] == "normal"
    panel_entry = manifest["plugin"]["ui"]["panel"][0]["entry"]
    assert panel_entry == "ui/panel_compat.tsx"
    assert (root / panel_entry).is_file()
    assert (root / "ui" / "panel.tsx").is_file()
    assert (root / "ui" / "panel_compat.tsx").is_file()
    assert (root / "ui" / "panel_data_sections.tsx").is_file()


def test_dashboard_actions_are_exposed_plugin_entries() -> None:
    projected = {item["entry_id"] for item in dashboard_actions()}
    entry_ids = set()
    ui_action_ids = set()
    for member in vars(NekoRoastPlugin).values():
        entry_meta = getattr(member, EVENT_META_ATTR, None)
        if entry_meta is not None and entry_meta.event_type == "plugin_entry":
            entry_ids.add(entry_meta.id)
        action_meta = getattr(member, UI_ACTION_META_ATTR, None)
        if isinstance(action_meta, dict):
            ui_action_ids.add(action_meta.get("id"))

    assert projected <= entry_ids
    assert projected <= ui_action_ids


def test_hosted_ui_manifest_entry_is_main_branch_compatible():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    assert "export default function NekoRoastPanel" in source
    assert 'from "@neko/plugin-ui"' in source
    assert "from \"./" not in source
    assert "from './" not in source
    assert "import type" not in source
    assert "window.NekoUiKit" not in source
    assert "__modules" not in source
    assert "panel.platform.incompleteSuffix" in source


def test_hosted_ui_compat_entry_keeps_full_live_controls():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    required_markers = [
        "lookup_live_room",
        "connect_live_room",
        "disconnect_live_room",
        "pause_roast",
        "resume_roast",
        "trigger_warmup_hosting",
        "trigger_active_engagement",
        "douyin_cookie_status",
        "douyin_cookie_validate",
        "douyin_cookie_import",
        "douyin_cookie_delete",
        "live_platform",
        "live_status",
        "idle_hosting_status",
        "active_engagement_status",
        "panel.platform.bilibili",
        "panel.platform.douyin",
        "panel.tabs.interaction",
        "panel.tabs.viewers",
        "panel.tabs.settings",
    ]
    for marker in required_markers:
        assert marker in source


def test_dry_run_defaults_off_and_is_hidden_from_normal_panel():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["neko_roast"]["dry_run"] is False
    assert "dry_run: false" in (root / "ui" / "panel_state.ts").read_text(encoding="utf-8")

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        assert 'label={t("panel.fields.dryRun")}' not in source
        assert "config.dry_run === true" in source
        assert "config.dry_run !== false" not in source


def test_developer_tools_default_off_until_explicitly_enabled():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["neko_roast"]["developer_tools_enabled"] is False
    assert "developer_tools_enabled: false" in (root / "ui" / "panel_state.ts").read_text(encoding="utf-8")
    assert "developer_tools_enabled: false" in (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")


def test_console_accepts_bilibili_links_and_requires_explicit_login_fallback() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        assert '!/^\\d+$/.test(roomRef)' not in source
        assert 't("panel.console.roomNumeric")' in source
        assert 'const [allowLimitedConnection, setAllowLimitedConnection] = useState(false)' in source
        assert 'connectionAuthMode === "limited_accountless"' in source
        assert 'const loginRequired = livePlatform === "bilibili" && !loginLoggedIn && !limitedConnection' in source
        assert 'loginLoggedIn || limitedConnection' in source
        assert 'allow_accountless: livePlatform === "bilibili" && !loginLoggedIn && allowLimitedConnection' in source
        assert 'onClick={enableLimitedConnection}' in source


def test_first_use_guide_is_local_resettable_and_mirrored() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        assert 'const ONBOARDING_STORAGE_KEY = "neko-roast:onboarding:v2"' in source
        assert "window.localStorage.getItem(ONBOARDING_STORAGE_KEY)" in source
        assert "window.localStorage.setItem(ONBOARDING_STORAGE_KEY, \"done\")" in source
        assert "function resetOnboarding()" in source
        assert 'onClick={resetOnboarding}' in source
        assert 'open={onboardingOpen}' in source
        assert 't("panel.onboarding.actionLabel")' in source
        assert 't("panel.onboarding.successLabel")' in source


def test_live_room_selection_requires_lookup_then_explicit_confirmation() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        compact_source = "".join(source.split())
        confirm_source = source.split("async function confirmLiveRoom()", 1)[1].split("async function connectRoom()", 1)[0]
        lookup_source = source.split("async function lookupLiveRoom()", 1)[1].split("async function confirmLiveRoom()", 1)[0]

        assert 'const [queriedRoomRef, setQueriedRoomRef] = useState("")' in source
        assert 'const canConfirmLiveRoom = Boolean(liveRoomResult?.ok && queriedRoomRef === roomFormRef)' in source
        assert 'onClick={lookupLiveRoom}' in source
        assert 'disabled={consoleSaving || !canConfirmLiveRoom}' in source
        assert 't("panel.console.roomTwoStepHint")' in source
        assert 't("panel.messages.roomLookupRequired")' in source
        assert "lookupLiveRoom()" not in confirm_source
        assert "props.api.refresh()" not in lookup_source
        assert 'setLiveRoomResult(null);setQueriedRoomRef("")' in compact_source or 'setLiveRoomResult(null)setQueriedRoomRef("")' in compact_source


def test_live_panel_polling_pauses_when_hidden_and_cleans_up() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")

        assert "window.setInterval" not in source
        assert "let refreshPending = false" in source
        assert 'document.visibilityState !== "visible"' in source
        assert 'document.addEventListener("visibilitychange", handleVisibilityChange)' in source
        assert 'document.removeEventListener("visibilitychange", handleVisibilityChange)' in source
        assert "timer = window.setTimeout(runRefresh, 3000)" in source
        assert "window.clearTimeout(timer)" in source


def test_console_opens_stream_theme_modal_in_place_of_duplicate_diagnostics_action() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        runtime_source = source.split('<Card title={t("panel.console.runtimeTitle")}>', 1)[1].split(
            '<Card title={t("panel.console.sessionTitle")}>', 1
        )[0]
        session_source = source.split('<Card title={t("panel.console.sessionTitle")}>', 1)[1].split(
            '<Modal', 1
        )[0]

        assert "{streamThemePanel}" not in source
        assert source.count("{streamThemeForm}") == 1
        assert 'open={consoleDialog === "theme"}' in source
        assert 'openConsoleDialog("theme")' in runtime_source
        assert 't("panel.actions.showAdvanced")' not in runtime_source
        assert 't("panel.actions.showAdvanced")' in session_source
        assert 't("panel.fields.streamTheme")' in source
        assert 't("panel.streamTheme.hint")' in source
        assert 't("panel.fields.mode")' in source
        assert 't("panel.fields.liveMode")' not in source
        assert "saveThemeSettings()" in source


def test_console_modal_close_callback_stays_stable_while_typing() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")

        assert 'const closeConsoleDialog = useCallback(() => { setConsoleDialog("") }, [])' in source
        assert 'const closeInteractionDialog = useCallback(() => { setInteractionDialog("") }, [])' in source
        assert source.count("onClose={closeConsoleDialog}") == 5
        assert "onClose={closeInteractionDialog}" in source
        assert 'onClose={() => { setConsoleDialog("") }}' not in source
        assert 'onClose={() => { setInteractionDialog("") }}' not in source


def test_console_uses_pinned_live_control_dock_and_separate_pacing_modal() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        runtime_source = source.split('<Card title={t("panel.console.runtimeTitle")}>', 1)[1].split(
            '<Card title={t("panel.console.sessionTitle")}>', 1
        )[0]
        dock_source = source.split('className="neko-roast-console-dock"', 1)[1].split("</footer>", 1)[0]
        settings_source = source.split("const advancedSection = (", 1)[1].split("const dataSection = (", 1)[0]
        toolbar_source = source.split("<Toolbar>", 1)[1].split("</Toolbar>", 1)[0]

        assert 'className="neko-roast-console-layout"' in source
        assert 'gridTemplateRows: "auto auto"' in source
        assert 'className="neko-roast-console-scroll"' in source
        assert 'overflow: "visible"' in source
        assert 'height: "calc(100vh - 190px)"' not in source
        assert 'position: "sticky"' in dock_source
        assert 'bottom: 0' in dock_source
        assert 'position: "fixed"' not in dock_source
        assert 'gridTemplateColumns: "minmax(260px, 520px)"' in dock_source
        assert 'justifyContent: "center"' in dock_source
        assert 'openConsoleDialog("pacing")' in runtime_source
        assert 'onClick={connectRoom}' not in runtime_source
        assert 'open={consoleDialog === "pacing"}' in source
        assert source.count("{pacingForm}") == 1
        assert 't("panel.pacing.fast")' in source
        assert 't("panel.pacing.standard")' in source
        assert 't("panel.pacing.slow")' in source
        assert 'onClick={connectRoom}' in dock_source
        assert dock_source.count("<Button") == 2
        assert "<StatusBadge" not in dock_source
        assert 'callSimple("clear_queue")' not in dock_source
        assert 'callSimple("pause_roast")' not in dock_source
        assert 'const canStart = roomConfigured' in source
        assert "primaryStatusLabel" in toolbar_source
        assert "primaryStatusTone" in toolbar_source
        assert "showSafetyStatus" in toolbar_source
        assert 't("panel.liveStatusSummary.cooldown")' in toolbar_source
        assert 't("panel.stats.queue")' in toolbar_source
        assert 'dynamicLabel("liveState", "panel.liveState", liveStateName)' not in toolbar_source
        assert 'callSimple("clear_queue")' not in settings_source
        assert 'configForm.values.safety_auto_stop_enabled' in settings_source
        assert 'queue_limit: preset.value' in settings_source
        assert 'id="settings-sections"' in settings_source
        assert 'id: "safety"' in settings_source
        assert 'id: "privacy"' in settings_source
        assert 'id: "help"' in settings_source
        assert 'panel.settings.queueCautious' in source
        assert 'panel.settings.queueStandard' in source
        assert 'panel.settings.queueRelaxed' in source
        assert 'open={safetyDisableConfirmOpen}' in settings_source
        assert 'open={storageDetailsOpen}' in settings_source
        assert 'panel.fields.rateLimit' not in settings_source
        assert 'panel.storage.disabled' not in settings_source
        assert 'saveConfig(advancedConfigPatch())' not in settings_source

        developer_results = source.split('id: "results"', 1)[1].split('id="developer-tools"', 1)[0]
        assert 'panel.advanced.title' in developer_results
        assert '<ModuleOverviewCard modules={modules} t={t} />' in developer_results


def test_nested_navigation_uses_compact_accessible_pills() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")

        assert 'function CompactTabs(' in source
        assert 'className="neko-roast-compact-tabs"' in source
        assert 'role="tablist"' in source
        assert 'role="tab"' in source
        assert 'role="tabpanel"' in source
        assert 'aria-selected={active}' in source
        assert 'minHeight: "26px"' in source
        assert 'fontSize: "12px"' in source
        assert source.count("<CompactTabs") == 3
        assert '<CompactTabs\n        id="settings-sections"' in source
        assert '<CompactTabs\n      id="audience-data"' in source
        assert '<CompactTabs\n        id="developer-tools"' in source


def test_interaction_panel_uses_stable_cards_and_detail_modals() -> None:
    root = Path(__file__).resolve().parents[1]
    required_keys = {
        "panel.interaction.details",
        "panel.interaction.group.audience",
        "panel.interaction.group.audienceHint",
        "panel.interaction.group.hosting",
        "panel.interaction.group.hostingHint",
        "panel.interaction.module.avatarRoast.avatarAnalysisHint",
        "panel.interaction.module.avatarRoast.disabledHint",
        "panel.interaction.module.danmakuResponse.disabledHint",
        "panel.interaction.module.liveSupportEvents.disabledHint",
        "panel.interaction.module.warmupHosting.disabledHint",
        "panel.interaction.module.idleHosting.disabledHint",
        "panel.interaction.module.activeEngagement.disabledHint",
    }

    for name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / name).read_text(encoding="utf-8")
        interaction_source = source.split("const currentDecisionCard = (", 1)[1].split(
            "const viewerStore =", 1
        )[0]

        assert 'const [interactionDialog, setInteractionDialog]' in source
        assert 'open={!!interactionDialog}' in interaction_source
        assert interaction_source.count('renderInteractionDetailsButton("') == 6
        assert 'minHeight: "190px"' in source
        assert 'minHeight: "22px"' in source
        assert 'fontSize: "12px"' in source
        assert 'visibility: enabled ? "hidden" : "visible"' in source
        assert interaction_source.index("{currentDecisionCard}") < interaction_source.index(
            't("panel.interaction.group.audience")'
        )
        assert 'disabled={!configForm.values.avatar_roast_enabled || settingsSaving}' in interaction_source
        assert "<details" not in interaction_source

    for locale_path in sorted((root / "i18n").glob("*.json")):
        locale = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required_keys <= set(locale), locale_path.name


def test_live_room_entries_are_platform_neutral():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    checked_values = {
        "plugin.toml:plugin.description": manifest["plugin"]["description"],
    }
    room_entry_keys = {
        "plugin.description",
        "entries.set_live_room.description",
        "entries.lookup_live_room.description",
    }
    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        for key in room_entry_keys:
            checked_values[f"{locale_path.name}:{key}"] = str(data[key])

    platform_specific = {
        label: value
        for label, value in checked_values.items()
        if "bilibili" in value.lower() or "b站" in value
    }
    assert platform_specific == {}

    source = (root / "__init__.py").read_text(encoding="utf-8")
    assert "B站直播间 ID" not in source
    assert "Bilibili live room ID" not in source
    assert "直播间目标" in source


def test_live_events_module_doc_uses_provider_neutral_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "modules" / "live_events.md").read_text(encoding="utf-8")

    assert "provider-neutral rich events" in source
    assert "provider_event.py" in source
    assert "signal-only" in source
    assert "LiveDanmaku-compatible" not in source
    assert "rich Bilibili events" not in source
    assert "bili_live_ingest` publishes" not in source


def test_panel_renders_live_status_summary():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)

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
    source = _panel_ui_source(root)

    assert "panel.interaction.currentDecision.title" in source
    assert "panel.interaction.currentDecision.latestEvent" in source
    assert "panel.interaction.currentDecision.route" in source
    assert "response_module" in source
    assert "event_signal" in source
    assert "panel.interaction.currentDecision.eventSignal" in source
    assert "panel.interaction.currentDecision.lastResult" in source
    assert "avatar_roast" in source
    assert "danmaku_response" in source
    assert "live_support_events" in source
    assert "warmup_hosting" in source
    assert "idle_hosting" in source
    assert "active_engagement" in source
    assert "panel.interaction.module.avatarRoast.desc" in source
    assert "panel.interaction.module.danmakuResponse.desc" in source
    assert "panel.interaction.module.liveSupportEvents.desc" in source
    assert "panel.interaction.module.warmupHosting.desc" in source
    assert "panel.interaction.module.idleHosting.desc" in source
    assert "panel.interaction.module.activeEngagement.desc" in source


def test_panel_hides_internal_module_ids_from_streamer_module_cards():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)

    assert 'title={`${module.id} · ${t("panel.interaction.module.avatarRoast.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.danmakuResponse.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.liveSupportEvents.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.warmupHosting.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.idleHosting.title")}`}' not in source
    assert 'title={`${module.id} · ${t("panel.interaction.module.activeEngagement.title")}`}' not in source


def test_panel_dynamic_labels_have_streamer_facing_fallbacks():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)
    helper_source = (root / "ui" / "panel_helpers.ts").read_text(encoding="utf-8")

    assert "panelText(" in helper_source
    assert 'solo_idle: "猫猫独播已冷场，可以冷场陪播。"' in helper_source
    assert 'waiting_for_viewer_or_idle_slot: "正在等待观众接话或冷场补位时机。"' in helper_source
    assert "t(`panel.liveDirector.reason.${liveDirectorReason}`)" not in source
    assert "t(`panel.speechExplanation.reason.${speechReason}`)" not in source


def test_panel_recent_results_show_route_and_signal_labels():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)
    helper_source = (root / "ui" / "panel_helpers.ts").read_text(encoding="utf-8")

    assert "panel.columns.responseModule" in source
    assert "panel.columns.eventSignal" in source
    assert "eventSignalLabel" in source
    assert "panel.eventSignal.gift_signal" in helper_source
    assert "panel.eventSignal.super_chat_signal" in helper_source
    assert "panel.eventSignal.danmaku_signal" in helper_source


def test_panel_renders_live_explanation_and_viewer_preference_columns():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)
    state_source = (root / "ui" / "panel_state.ts").read_text(encoding="utf-8")

    assert "live_explain" in source
    assert "live_explain" in state_source
    assert "panel.explain.title" in source
    assert "panel.explain.topicThemes" in source
    assert "panel.explain.viewerMemory" in source
    assert "liveExplain.timeline" in source
    assert "explainTimeline" in source
    assert "preference_tags" in source
    assert "favorite_topics" in source
    assert "running_jokes" in source
    assert "impression_summary" in source
    assert "avoid_guidance" in source
    assert "last_interaction_summary" in source
    assert "panel.columns.danmakuCount" in source
    assert "panel.columns.preferenceTags" in source
    assert "panel.columns.favoriteTopics" in source
    assert "panel.columns.runningJokes" in source
    assert "panel.columns.latestSummary" in source
    assert "panel.columns.viewerStage" in source
    assert "panel.columns.profileConfidence" in source
    assert "panel.columns.profileFreshness" in source
    assert 'profileBadge("viewerStage", row.viewer_stage, t)' in source
    assert 'profileBadge("profileConfidence", row.profile_confidence, t)' in source
    assert 'profileBadge("profileFreshness", row.profile_freshness, t)' in source
    assert "panel.columns.avoidGuidance" in source
    assert "panel.columns.replyGuidance" in source

    required_keys = {
        "panel.explain.title",
        "panel.explain.summary",
        "panel.explain.trace",
        "panel.explain.topicThemes",
        "panel.explain.viewerMemory",
        "panel.explain.latestResult",
        "panel.explain.stage",
        "panel.columns.detail",
        "panel.columns.danmakuCount",
        "panel.columns.preferenceTags",
        "panel.columns.favoriteTopics",
        "panel.columns.runningJokes",
        "panel.columns.latestSummary",
        "panel.columns.viewerStage",
        "panel.columns.profileConfidence",
        "panel.columns.profileFreshness",
        "panel.viewerStage.new_viewer",
        "panel.viewerStage.returning_viewer",
        "panel.viewerStage.regular_viewer",
        "panel.viewerStage.familiar_viewer",
        "panel.profileConfidence.none",
        "panel.profileConfidence.low",
        "panel.profileConfidence.medium",
        "panel.profileConfidence.high",
        "panel.profileFreshness.none",
        "panel.profileFreshness.fresh",
        "panel.profileFreshness.warm",
        "panel.profileFreshness.stale",
        "panel.profileFreshness.old",
        "panel.columns.avoidGuidance",
        "panel.columns.replyGuidance",
    }
    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys - set(data)
        assert not missing, f"{locale_path.name} missing live explanation labels: {sorted(missing)}"
        placeholder_values = {
            key: data.get(key)
            for key in required_keys
            if "?" in str(data.get(key, ""))
        }
        assert not placeholder_values, f"{locale_path.name} has placeholder labels: {placeholder_values}"


def test_panel_shows_independent_pacing_and_active_topic_observability():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)
    helper_source = (root / "ui" / "panel_helpers.ts").read_text(encoding="utf-8")

    assert "last_viewer_activity_age_sec" in source
    assert "last_output_age_sec" in source
    assert "panel.liveState.lastViewerActivityAge" in source
    assert "panel.liveState.lastOutputAge" in source
    assert "topic_source" in source
    assert "activeTopicSourceLabel" in source
    assert "topic_shape" in source
    assert "activeTopicShapeLabel" in source
    assert "topic_intent" in source
    assert "topic_reply_affordance" in source
    assert "activeTopicIntentLabel" in source
    assert "activeTopicReplyAffordanceLabel" in source
    assert "panel.activeEngagementIntent.quickVote" in helper_source
    assert "panel.activeEngagementReplyAffordance.oneSide" in helper_source
    assert "panel.interaction.currentDecision.topic" in source
    assert "host_beat_shape" in source
    assert "idleHostBeatShapeLabel" in source
    assert "host_beat_title" in source
    assert "panel.interaction.currentDecision.hostBeat" in source
    assert "latestResult.event.topic_hook" not in source
    assert "latestResult.event.host_beat_hint" not in source

    required_keys = {
        "panel.liveState.lastViewerActivityAge",
        "panel.liveState.lastOutputAge",
        "panel.interaction.currentDecision.topic",
        "panel.interaction.currentDecision.hostBeat",
        "panel.idleHostingBeatShape.softObservation",
        "panel.idleHostingBeatShape.tinyChoice",
        "panel.idleHostingBeatShape.lightTease",
        "panel.idleHostingBeatShape.smallMood",
        "panel.activeEngagementSource.fallback",
        "panel.activeEngagementSource.biliTrending",
        "panel.activeEngagementSource.recentDanmaku",
        "panel.activeEngagementShape.eitherOr",
        "panel.activeEngagementShape.lightStance",
        "panel.activeEngagementShape.tinyTease",
        "panel.activeEngagementShape.smallChallenge",
        "panel.activeEngagementIntent.quickVote",
        "panel.activeEngagementIntent.agreeOrPushback",
        "panel.activeEngagementIntent.teaseBack",
        "panel.activeEngagementIntent.tinyAnswer",
        "panel.activeEngagementIntent.quickReply",
        "panel.activeEngagementReplyAffordance.oneSide",
        "panel.activeEngagementReplyAffordance.agreeOrPushback",
        "panel.activeEngagementReplyAffordance.teaseBack",
        "panel.activeEngagementReplyAffordance.fewWords",
        "panel.activeEngagementReplyAffordance.quickReply",
    }
    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = required_keys - set(data)
        assert not missing, f"{locale_path.name} missing UI observability labels: {sorted(missing)}"


def test_panel_renders_solo_stream_test_readiness():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)

    assert "solo_test_readiness" in source
    assert "panel.soloTestReadiness.title" in source
    assert "panel.soloTestReadiness.summary" in source
    assert "panel.soloTestReadiness.item" in source
    assert "panel.soloTestReadiness.profileCount" in source
    assert "clearViewerMemory" in source
    assert "panel.messages.clearViewerProfilesConfirm" in source


def test_panel_renders_platform_switch_and_douyin_cookie_controls():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    compact_source = "".join(source.split())

    assert "live_platform" in source
    assert "live_room_ref" in source
    assert "lookupRoomRef" in source
    assert "liveRoomResult?.room_ref || liveRoomResult?.room_id" in source
    assert "function switchLivePlatform" in source
    assert 'configForm.setField("live_room_ref", "")' in source
    assert 'configForm.setField("live_room_id", "")' in source
    assert 'configForm.setField("live_enabled", false)' in source
    assert 'saveConfig({live_platform:next,live_room_ref:"",live_room_id:0,live_enabled:false})' in compact_source
    assert (
        "constpatchedPayload=hasPatchedRoomRef||hasPatchedRoomId"
        "?{...patch,live_room_ref:liveRoomRef,live_room_id:liveRoomId,}:patch"
    ) in compact_source
    assert "panel.platform.title" in source
    assert "panel.platform.bilibili" in source
    assert "panel.platform.douyin" in source
    assert "douyin_cookie_status" in source
    assert "douyin_cookie_import" in source
    assert "douyin_cookie_validate" in source
    assert "douyin_cookie_delete" in source
    assert "connection.connection_plan" in source
    assert "connectionPlan?.message" in source
    assert "connectionMissing.join" in source
    assert "reconnectState.retry_count" in source
    assert "panel.douyinAuth.manualHint" in source


def test_panel_console_keeps_live_operations_compact_and_modal() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert "ConfirmDialog" in source
        assert "consoleDialog" in source
        assert 'openConsoleDialog("account")' in source
        assert 'openConsoleDialog("room")' in source
        assert 'openConsoleDialog("diagnostics")' in source
        assert 'props.api.call("set_live_room", { room_id: roomRef })' in source
        assert 'callSimple("disconnect_live_room")' in source
        assert 'interactionPaused ? "resume_roast" : "pause_roast"' in source
        assert 't("panel.room.lookupOk") + ": "' not in source
        assert "const roastEnabled = configForm.values.avatar_roast_enabled !== false" in source
        assert '{ id: "console", label: t("panel.tabs.console"), content: consoleSection }' in source
        assert '{ id: "interaction", label: t("panel.tabs.interaction"), content: modulesSection }' in source
        assert '{ id: "viewers", label: t("panel.tabs.viewers"), content: dataSection }' in source
        assert '{ id: "settings", label: t("panel.tabs.settings"), content: advancedSection }' in source
        assert '{ id: "dm", label: t("panel.tabs.dm")' not in source
        assert '{ id: "automation", label: t("panel.tabs.automation")' not in source

        modules_section = source.split("const modulesSection = (", 1)[1].split("const viewerStore", 1)[0]
        assert modules_section.count("<div style={interactionCardGridStyle}>") == 2
        assert modules_section.index("{currentDecisionCard}") < modules_section.index("renderAvatarRoastCard")
        assert modules_section.index("{currentDecisionCard}") < modules_section.index("renderActiveEngagementCard")
        for key in (
            "avatar_roast_enabled",
            "avatar_analysis_enabled",
            "danmaku_response_enabled",
            "live_support_events_enabled",
            "warmup_hosting_enabled",
            "idle_hosting_enabled",
            "active_engagement_enabled",
        ):
            assert key in source


def test_console_dialogs_keep_independent_drafts_and_scoped_saves():
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert "const [themeDraft, setThemeDraft]" in source
        assert "const [pacingDraft, setPacingDraft]" in source
        assert 'const [roomDraft, setRoomDraft] = useState("")' in source
        assert "async function saveThemeSettings()" in source
        assert "async function savePacingSettings()" in source
        assert "stream_theme: themeDraft.stream_theme.trim()" in source
        assert "activity_level: pacingDraft.activity_level" in source
        assert "saveConfig(advancedConfigPatch())" not in source
        assert "function advancedConfigPatch()" not in source


def test_panel_renders_guarded_viewer_memory_controls():
    root = Path(__file__).resolve().parents[1]
    source = _panel_ui_source(root)
    compat_source = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    assert "ViewerProfilesTable" in source
    for panel_source in (source, compat_source):
        assert "viewer_memory_enabled" in panel_source
        assert "async function clearViewerMemory()" in panel_source
        assert "async function runViewerProfileAction" in panel_source
        assert 'props.api.call("clear_viewer_profiles", {})' in panel_source
        assert 'props.api.call(action, { uid })' in panel_source
        assert 'runViewerProfileAction("reset_viewer_impression", uid)' in panel_source
        assert 'runViewerProfileAction("delete_viewer_profile", uid)' in panel_source
        assert "clearViewerMemoryConfirmOpen" in panel_source
        assert "panel.messages.clearViewerProfilesConfirm" in panel_source

    assert "panel.messages.resetViewerImpressionConfirm" in source
    assert "panel.messages.deleteViewerProfileConfirm" in source


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
        "panel.interaction.module.liveSupportEvents.title",
        "panel.interaction.module.warmupHosting.title",
        "panel.interaction.module.idleHosting.title",
        "panel.interaction.module.activeEngagement.title",
    }
    forbidden = ("avatar_roast", "danmaku_response", "live_support_events", "warmup_hosting", "idle_hosting", "active_engagement")

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
    assert meta["group"] == "hosting"
    assert meta["refresh_context"] is True


def test_trigger_warmup_hosting_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.trigger_warmup_hosting, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "trigger_warmup_hosting"
    assert meta["group"] == "hosting"
    assert meta["refresh_context"] is True


def test_trigger_active_engagement_is_exposed_as_hosted_ui_action():
    meta = getattr(NekoRoastPlugin.trigger_active_engagement, UI_ACTION_META_ATTR, None)

    assert meta is not None
    assert meta["id"] == "trigger_active_engagement"
    assert meta["group"] == "hosting"
    assert meta["refresh_context"] is True


def test_viewer_profile_destructive_entries_are_not_exposed_as_hosted_ui_actions():
    meta = getattr(NekoRoastPlugin.clear_viewer_profiles, UI_ACTION_META_ATTR, None)
    delete_meta = getattr(NekoRoastPlugin.delete_viewer_profile, UI_ACTION_META_ATTR, None)
    reset_meta = getattr(NekoRoastPlugin.reset_viewer_impression, UI_ACTION_META_ATTR, None)

    assert meta is None
    assert delete_meta is None
    assert reset_meta is None


def test_douyin_cookie_actions_are_exposed_as_hosted_ui_actions():
    for method_name, action_id in (
        ("douyin_cookie_import", "douyin_cookie_import"),
        ("douyin_cookie_status", "douyin_cookie_status"),
        ("douyin_cookie_validate", "douyin_cookie_validate"),
        ("douyin_cookie_delete", "douyin_cookie_delete"),
    ):
        meta = getattr(getattr(NekoRoastPlugin, method_name), UI_ACTION_META_ATTR, None)

        assert meta is not None
        assert meta["id"] == action_id
        assert meta["group"] == "auth"
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
        "panel.platform.title",
        "panel.fields.platform",
        "panel.platform.bilibili",
        "panel.platform.douyin",
        "panel.fields.douyinRoom",
        "panel.placeholders.douyinRoom",
        "panel.douyinAuth.title",
        "panel.douyinAuth.cookieReady",
        "panel.douyinAuth.cookieMissing",
        "panel.douyinAuth.savedAt",
        "panel.douyinAuth.manualHint",
        "panel.fields.douyinCookie",
        "panel.placeholders.douyinCookie",
        "panel.fields.douyinUid",
        "panel.fields.douyinNickname",
        "panel.actions.douyinCookieImport",
        "panel.actions.douyinCookieStatus",
        "panel.actions.douyinCookieValidate",
        "panel.actions.douyinCookieDelete",
        "panel.douyinAuth.cookieRequired",
        "panel.douyinAuth.cookieSaved",
        "panel.douyinAuth.cookieSaveFailed",
        "panel.douyinAuth.cookieValid",
        "panel.douyinAuth.cookieInvalid",
        "panel.douyinAuth.cookieDeleted",
        "actions.douyin_cookie_import.label",
        "actions.douyin_cookie_status.label",
        "actions.douyin_cookie_validate.label",
        "actions.douyin_cookie_delete.label",
        "entries.douyin_cookie_import.name",
        "entries.douyin_cookie_import.description",
        "entries.douyin_cookie_status.name",
        "entries.douyin_cookie_status.description",
        "entries.douyin_cookie_validate.name",
        "entries.douyin_cookie_validate.description",
        "entries.douyin_cookie_delete.name",
        "entries.douyin_cookie_delete.description",
        "panel.liveStatusReason.ready",
        "panel.liveStatusReason.dry_run",
        "panel.liveStatusReason.manual_paused",
        "panel.liveStatusReason.room_not_configured",
        "panel.liveStatusReason.live_disabled",
        "panel.liveStatusReason.live_ingest_disconnected",
        "panel.liveStatusReason.cooldown",
        "panel.liveStatusReason.safety_tripped",
        "panel.liveStatusReason.safety_degraded",
        "panel.liveStatusReason.output_channel_unavailable",
        "panel.liveStatusReason.all_ready",
        "panel.liveModeRole.co_stream",
        "panel.liveModeRole.solo_stream",
        "panel.fields.activityLevel",
        "panel.fields.streamTheme",
        "panel.fields.streamGoal",
        "panel.fields.streamColumns",
        "panel.fields.streamAvoidTopics",
        "panel.streamTheme.title",
        "panel.streamTheme.hint",
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
        "panel.interaction.module.liveSupportEvents.title",
        "panel.interaction.module.liveSupportEvents.desc",
        "panel.interaction.module.liveSupportEvents.badge",
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
        "panel.actions.resetViewerImpression",
        "panel.actions.confirmResetViewerImpression",
        "panel.actions.deleteViewerProfile",
        "panel.actions.confirmDeleteViewerProfile",
        "panel.columns.profileActions",
        "panel.messages.viewerUidRequired",
        "panel.messages.clearViewerProfilesConfirm",
        "panel.messages.resetViewerImpressionConfirm",
        "panel.messages.deleteViewerProfileConfirm",
        "panel.settings.viewerMemoryLabel",
        "panel.settings.viewerMemoryEnabledHint",
        "panel.settings.viewerMemoryDisabledHint",
        "panel.settings.viewerMemoryRetentionHint",
        "actions.clear_viewer_profiles.label",
        "actions.delete_viewer_profile.label",
        "actions.reset_viewer_impression.label",
        "entries.clear_viewer_profiles.name",
        "entries.clear_viewer_profiles.description",
        "entries.delete_viewer_profile.name",
        "entries.delete_viewer_profile.description",
        "entries.reset_viewer_impression.name",
        "entries.reset_viewer_impression.description",
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


def test_only_explicit_room_patches_include_normalized_room_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = (
        "...patch,\n"
        "          live_room_ref: liveRoomRef,\n"
        "          live_room_id: liveRoomId,"
    )

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert 'const liveRoomId = livePlatform === "bilibili" ? Number(liveRoomRef) || 0 : 0' in source
        assert expected in source
        assert source.count("live_room_id: liveRoomId,") == 1
        assert 'live_room_id: livePlatform === "bilibili" ? liveRoomRef : 0' not in source
        assert "const fullPayload" not in source


def test_unrelated_patched_panel_saves_do_not_inject_room_drafts() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert 'return roomRef === "0" ? "" : roomRef' in source
        assert "const patchedPayload = hasPatchedRoomRef || hasPatchedRoomId" in source
        assert ": patch" in source
        assert "normalizedRoomRef(configForm.values.live_room_ref)" not in source
        assert "const fullPayload" not in source


def test_platform_switch_explicitly_clears_the_old_room() -> None:
    root = Path(__file__).resolve().parents[1]
    platform_switch = 'saveConfig({ live_platform: next, live_room_ref: "", live_room_id: 0, live_enabled: false })'

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert platform_switch in source


def test_compat_panel_mirrors_live_connection_and_theme_controls() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert "result.logged_in || result.has_cookie" in source
        assert "connection.listening ||" in source
        assert 'connectionState === "receiving"' in source
        for field in ("streamGoal", "streamColumns", "streamAvoidTopics"):
            assert f't("panel.fields.{field}")' in source


def test_compat_panel_mirrors_accessible_qr_and_result_tones() -> None:
    root = Path(__file__).resolve().parents[1]
    components = (root / "ui" / "panel_components.tsx").read_text(encoding="utf-8")
    sections = (root / "ui" / "panel_data_sections.tsx").read_text(encoding="utf-8")
    helpers = (root / "ui" / "panel_helpers.ts").read_text(encoding="utf-8")
    compat = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    assert '<button\n                  type="button"\n                  disabled={disabled}\n                  onClick={onLogin}' in components
    assert 'aria-label={t("panel.auth.refreshHint")}' in components
    assert 'recentResultTone(String(row.status || ""))' in sections
    for source in (helpers, compat):
        assert 'if (status === "failed") return "danger"' in source
        assert 'if (status === "skipped") return "warning"' in source
    assert '<button\n                  type="button"\n                  disabled={disabled}\n                  onClick={onLogin}' in compat
    assert 'recentResultTone(String(row.status || ""))' in compat


def test_developer_tools_use_three_internal_subpages_in_both_panels() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        developer_source = source[source.index("const developerSandbox") : source.index("const tabItems")]
        assert developer_source.index('id: "identity"') < developer_source.index('id: "event"')
        assert developer_source.index('id: "event"') < developer_source.index('id: "results"')
        assert 'label: t("panel.dev.lookup.title")' in developer_source
        assert 'label: t("panel.dev.emitter.title")' in developer_source
        assert 'label: t("panel.dev.runtimeResults")' in developer_source
        assert "<CompactTabs" in developer_source
        assert 'id="developer-tools"' in developer_source


def test_audience_page_separates_session_data_from_viewer_profiles() -> None:
    root = Path(__file__).resolve().parents[1]
    authored_panel = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    data_sections = (root / "ui" / "panel_data_sections.tsx").read_text(encoding="utf-8")
    compat_panel = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    for source in (authored_panel, compat_panel):
        audience_source = source[source.index("const dataSection") : source.index("const lookupIdentity")]
        assert 'id="audience-data"' in audience_source
        assert 'id: "session"' in audience_source
        assert 'id: "profiles"' in audience_source
        assert "LiveSessionSection" in audience_source
        assert "ViewerProfilesTable" in audience_source
        assert "LiveExplainSection" not in audience_source
        assert "RecentResultsTable" not in audience_source

    for source in (data_sections, compat_panel):
        assert 'title={t("panel.audience.sessionDetailTitle")}' in source
        assert 'title={t("panel.audience.profileDetailTitle")}' in source
        assert 'maxRows={30}' in source
        assert 'style={{ overflowX: "auto" }}' in source


def test_panel_live_session_controls_are_transition_safe_and_refresh_aware() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert 'const connectionTransitioning = ["connecting", "authenticating", "reconnecting"]' in source
        assert "const sessionInProgress = started || !!config.live_enabled || sessionStartAccepted" in source
        assert "if (startAccepted) setSessionStartAccepted(true)" in source
        assert '(dialog === "account" || dialog === "room") && sessionInProgress' in source
        assert 'const [authPending, setAuthPending] = useState("")' in source
        assert "const [developerPending, setDeveloperPending] = useState(false)" in source
        assert "const [refreshFailureCount, setRefreshFailureCount] = useState(0)" in source
        assert 't("panel.messages.statusStale", { time: lastSuccessfulRefreshLabel })' in source
        assert '<Tabs key={developerToolsEnabled ? "developer-enabled" : "developer-disabled"}' in source
        assert "await refreshDashboard(true)" in source
        assert "<RefreshButton" not in source
        assert "applySettingsPatch({ avatar_roast_enabled: v })" in source
        assert "applySettingsPatch({ live_support_events_enabled: v })" in source


def test_panel_compact_tabs_and_dates_follow_accessibility_and_locale_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    authored_panel = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    compat_panel = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")
    components = (root / "ui" / "panel_components.tsx").read_text(encoding="utf-8")
    sections = (root / "ui" / "panel_data_sections.tsx").read_text(encoding="utf-8")

    for source in (authored_panel, compat_panel):
        assert "tabIndex={active ? 0 : -1}" in source
        assert 'event.key === "ArrowRight"' in source
        assert 'event.key === "ArrowLeft"' in source
        assert 'event.key === "Home"' in source
        assert 'event.key === "End"' in source
        assert "formatLocaleDateTime(lastSuccessfulRefreshAt, props.locale)" in source

    for source in (sections, compat_panel):
        assert "formatDateTime(row.last_interaction_at, locale)" in source
        assert "formatDateTime(row.last_seen_at, locale)" in source
        assert "parsed.toLocaleString(locale || undefined)" in source

    for source in (components, compat_panel):
        assert "disabled?: boolean" in source
        assert "disabled={disabled}" in source


def test_all_locales_define_refresh_and_session_lock_messages() -> None:
    root = Path(__file__).resolve().parents[1]
    required_keys = {
        "panel.messages.refreshFailedAfterAction",
        "panel.messages.statusStale",
        "panel.messages.endSessionBeforeChangingTarget",
    }

    for locale_path in sorted((root / "i18n").glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required_keys <= set(data), locale_path.name
        assert "{time}" in str(data["panel.messages.statusStale"]), locale_path.name
