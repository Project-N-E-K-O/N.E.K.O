from __future__ import annotations

from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")


def _plugin_python_sources() -> list[Path]:
    return [
        path
        for path in PLUGIN_ROOT.rglob("*.py")
        if "\\tests\\" not in str(path)
        and "/tests/" not in str(path)
        and "__pycache__" not in path.parts
    ]


def test_plugin_code_does_not_import_host_core():
    forbidden = ("from " + "main_logic", "import " + "main_logic")

    offenders = [
        str(path.relative_to(PLUGIN_ROOT))
        for path in _plugin_python_sources()
        if any(token in path.read_text(encoding="utf-8") for token in forbidden)
    ]

    assert offenders == []


def test_pipeline_routing_stays_pure():
    source = _source("core/pipeline_routing.py")

    forbidden = (
        "dispatcher",
        "viewer_store",
        "viewer_profile",
        "safety_guard",
        "record_result",
        "InteractionResult",
        "InteractionRequest",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_stays_public_preflight_facade():
    source = _source("core/pipeline.py")

    forbidden = (
        "import asyncio",
        "resolve_viewer_context",
        "build_request_for_route",
        "dispatch_routed_request",
        "skip_already_roasted",
        "fail_pipeline",
        "lock_for",
        "claim_roasted",
        "viewer_profile",
        "mark_roasted",
    )
    assert [token for token in forbidden if token in source] == []

    assert "pipeline_flow.run_event_flow" in source


def test_pipeline_flow_owns_post_safety_event_flow_only():
    source = _source("core/pipeline_flow.py")

    forbidden = (
        "allows_source",
        "permission_gate",
        "reject_missing_uid",
        "skip_permission",
        "skip_before_event",
        "before_event",
        "before_output(",
        "push_roast(",
        "mark_roasted(",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_results_only_builds_results_and_audit_records():
    source = _source("core/pipeline_results.py")

    forbidden = ("\ndef ", "\nclass ", "InteractionResult", "PipelineStep")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "pipeline_dispatch_results",
        "pipeline_failure_results",
        "pipeline_skip_results",
    )
    assert [module for module in required_modules if module not in source] == []


def test_pipeline_skip_results_only_handles_gate_skips():
    source = _source("core/pipeline_skip_results.py")

    forbidden = (
        "push_roast",
        "route_for_event",
        "viewer_store",
        "viewer_profile.",
        "bili_identity",
        "build_request",
        "neko_dispatcher",
        "record_failure",
        "dry_run",
        "pipeline_failed",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_dispatch_results_only_handles_dispatch_outcomes():
    source = _source("core/pipeline_dispatch_results.py")

    forbidden = (
        "push_roast",
        "route_for_event",
        "viewer_store",
        "viewer_profile.",
        "bili_identity",
        "build_request",
        "record_failure",
        "safety_guard",
        "permission_gate",
        "viewer_gate",
        "pipeline_failed",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_failure_results_only_handles_failure_accounting():
    source = _source("core/pipeline_failure_results.py")

    forbidden = (
        "push_roast",
        "route_for_event",
        "viewer_store",
        "viewer_profile.",
        "bili_identity",
        "build_request",
        "permission_gate",
        "viewer_gate",
        "dispatcher_dry_run",
        "pipeline_pushed",
        "dispatcher_skipped",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_viewers_only_prepares_identity_and_profile():
    source = _source("core/pipeline_viewers.py")

    forbidden = (
        "route_for_event",
        "PipelineRoute",
        "build_request",
        "dispatcher",
        "push_roast",
        "safety_guard",
        "record_result",
        "InteractionResult",
        "audit.record",
        "mark_roasted",
        "bili_identity",
        "douyin_identity",
    )
    assert [token for token in forbidden if token in source] == []


def test_douyin_live_ingest_runtime_does_not_hardcode_bridge_binary():
    source = _source("modules/douyin_live_ingest/__init__.py")
    embedded_source = _source("modules/douyin_live_ingest/embedded_bridge.py")

    forbidden = ("douyinLive.exe", "vendor", "windows-amd64")

    assert [token for token in forbidden if token in source] == []
    assert [token for token in forbidden if token in embedded_source] == []
    assert "default_douyin_bridge_backend" in embedded_source


def test_pipeline_requests_only_builds_module_requests():
    source = _source("core/pipeline_requests.py")

    forbidden = (
        "bili_identity",
        "viewer_profile.",
        "has_roasted",
        "mark_roasted",
        "dispatcher",
        "push_roast",
        "safety_guard",
        "record_result",
        "InteractionResult",
        "audit.record",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_dispatch_only_runs_output_stage():
    source = _source("core/pipeline_dispatch.py")

    forbidden = (
        "route_for_event",
        "PipelineRoute",
        "build_request",
        "bili_identity",
        "viewer_profile.upsert",
        "has_roasted",
        "already_roasted",
        "lock_for",
        "allows_source",
        "before_event",
    )
    assert [token for token in forbidden if token in source] == []


def test_pipeline_session_only_tracks_session_gates():
    source = _source("core/pipeline_session.py")

    forbidden = (
        "route_for_event",
        "PipelineRoute",
        "build_request",
        "dispatcher",
        "push_roast",
        "safety_guard",
        "record_result",
        "InteractionResult",
        "audit.record",
        "mark_roasted",
    )
    assert [token for token in forbidden if token in source] == []


def test_safety_guard_stays_public_facade():
    source = _source("core/safety_guard.py")

    forbidden = (
        "import time",
        "time.monotonic",
        "automatic stop after",
        "rate limited",
    )
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "safety_guard_cooldown",
        "safety_guard_failures",
    )
    assert [module for module in required_modules if module not in source] == []


def test_safety_guard_cooldown_only_handles_output_timing():
    source = _source("core/safety_guard_cooldown.py")

    forbidden = (
        "queue_size",
        "queue_overflows",
        "_pipeline_failures",
        "_output_failures",
        "auto_paused =",
        "audit.record",
        "before_event",
        "snapshot",
    )
    assert [token for token in forbidden if token in source] == []


def test_safety_guard_failures_only_handles_failure_windows():
    source = _source("core/safety_guard_failures.py")

    forbidden = (
        "queue_size",
        "queue_overflows",
        "_last_output_at",
        "rate_limit_seconds",
        "SafetyDecision",
        "before_event",
        "before_output",
        "output_cooldown",
        "snapshot",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_content_catalogs_stay_static():
    catalog_paths = sorted((PLUGIN_ROOT / "core").glob("live_content_*catalog*.py"))
    assert catalog_paths

    for path in catalog_paths:
        source = path.read_text(encoding="utf-8")
        forbidden = (
            "runtime",
            "pipeline",
            "dispatcher",
            "active_topic",
            "live_status",
            "ViewerEvent",
            "InteractionResult",
        )
        assert [token for token in forbidden if token in source] == []


def test_meme_knowledge_stays_static_retrieval_only():
    source = _source("core/meme_knowledge.py")

    forbidden = (
        "main_logic",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "socket.",
        "runtime",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_choice_catalog_stays_aggregate_only():
    source = _source("core/live_content_active_catalog_choice.py")

    forbidden = ("\"source\":", "\"title\":", "\"hint\":", "\ndef ", "\nclass ")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "live_content_active_catalog_choice_props",
        "live_content_active_catalog_choice_room",
        "live_content_active_catalog_choice_verdict",
    )
    assert [module for module in required_modules if module not in source] == []


def test_module_registry_stays_registry_facade_only():
    source = _source("core/module_registry.py")

    forbidden = (
        "async def _toggle",
        "def _record_failure",
        "def _safe_meta",
        "@dataclass",
        "Pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
    )
    assert [token for token in forbidden if token in source] == []

    assert "setup_all_modules" in source
    assert "module_snapshot" in source


def test_module_registry_lifecycle_only_handles_isolated_hooks():
    source = _source("core/module_registry_lifecycle.py")

    forbidden = (
        "module_snapshot",
        "safe_meta",
        "ModuleRecord",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "config_schema",
        "status()",
    )
    assert [token for token in forbidden if token in source] == []


def test_module_registry_snapshot_only_projects_metadata():
    source = _source("core/module_registry_snapshot.py")

    forbidden = (
        "setup(",
        "teardown(",
        "on_enable",
        "on_disable",
        "record_failure",
        "audit.record",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_events_room_topic_uses_provider_event_helpers():
    source = _source("modules/live_events/room_topic.py")

    required = (
        "from .provider_event import",
        "event_uid",
        "event_nickname",
        "event_prompt_text",
        "public_text",
    )
    assert [token for token in required if token not in source] == []

    forbidden = (
        'str(getattr(event, "uid"',
        'str(getattr(event, "nickname"',
        'str(getattr(event, "text"',
        'str(getattr(event, "danmaku_text"',
        'str(event.get("uid"',
        'str(event.get("nickname"',
        'str(event.get("text"',
        'str(event.get("danmaku_text"',
    )
    assert [token for token in forbidden if token in source] == []


def test_live_events_provider_event_stays_provider_neutral_and_offline():
    source = _source("modules/live_events/provider_event.py")

    forbidden = (
        "bili_live_ingest",
        "douyin_live_ingest",
        "bili_identity",
        "douyin_identity",
        "CredentialStore",
        "urlopen",
        "requests",
        "aiohttp",
        "httpx",
        "websocket",
        "websockets",
        "socket.",
        "gethostby",
        "asyncio",
        "ctx.",
        "audit.record",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_status_facade_stays_reexport_only():
    source = _source("core/live_status.py")

    forbidden = ("\ndef ", "\nclass ")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "live_status_active",
        "live_status_core",
        "live_status_timing",
        "live_status_director",
        "live_status_idle",
        "live_status_readiness",
    )
    assert [module for module in required_modules if module not in source] == []


def test_live_status_core_stays_pure_projection():
    source = _source("core/live_status_core.py")

    forbidden = (
        "runtime",
        "dispatcher",
        "pipeline",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_status_active_and_idle_stay_pure_eligibility():
    for relative_path in (
        "core/live_status_active.py",
        "core/live_status_idle.py",
    ):
        source = _source(relative_path)
        forbidden = (
            "runtime",
            "dispatcher",
            "pipeline",
            "ViewerEvent",
            "InteractionResult",
            "record_result",
            "audit.record",
            "live_director_status",
            "solo_test_readiness",
            "speech_explanation",
        )
        assert [token for token in forbidden if token in source] == []


def test_live_status_director_only_selects_next_action():
    source = _source("core/live_status_director.py")

    forbidden = (
        "def active_engagement_status",
        "def idle_hosting_status",
        "def idle_hosting_wait_remaining_for_quiet_state",
        "runtime",
        "dispatcher",
        "pipeline",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_live_status_api_stays_projection_only():
    source = _source("core/runtime_live_status_api.py")

    forbidden = (
        "def _active_engagement",
        "def _idle_hosting",
        "def _solo_warmup",
        "def _live_state_threshold",
        "def _recent_live_danmaku",
        "def _last_viewer_activity",
        "def _last_output",
        "def _age_sec",
        "def _iso_age_sec",
    )
    assert [token for token in forbidden if token in source] == []

    assert "RuntimeLiveStatusHelperMixin" in source


def test_runtime_live_status_helpers_stay_compat_helpers_only():
    source = _source("core/runtime_live_status_helpers.py")

    forbidden = (
        "dashboard",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
        "trigger_",
        "maybe_trigger",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_dashboard_stays_dashboard_assembly_only():
    source = _source("core/runtime_dashboard.py")

    forbidden = (
        "def runtime_health_rows",
        "def dashboard_actions",
        "def _status_from_outcome",
        "def _module_status",
        "def _dispatcher_skip_reason",
    )
    assert [token for token in forbidden if token in source] == []

    assert "runtime_health_rows" in source
    assert "dashboard_actions" in source


def test_runtime_health_stays_readonly_projection_only():
    source = _source("core/runtime_health.py")

    forbidden = (
        "dashboard_state",
        "dashboard_actions",
        "pipeline.handle_event",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
        "trigger_",
        "maybe_trigger",
        "update_config",
        "connect_live_room",
        "disconnect_live_room",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_provider_callers_stay_platform_neutral():
    caller_paths = (
        "core/runtime_live_controls.py",
        "core/runtime_live_input.py",
        "core/runtime_live_listener.py",
        "core/runtime_config.py",
        "core/runtime_health.py",
        "core/pipeline_viewers.py",
        "modules/live_events/module.py",
    )
    forbidden = (
        "bili_live_ingest",
        "douyin_live_ingest",
        "bili_identity",
        "douyin_identity",
        "LiveDanmaku",
        "MessageType",
    )

    offenders: dict[str, list[str]] = {}
    for relative_path in caller_paths:
        source = _source(relative_path)
        hits = [token for token in forbidden if token in source]
        if hits:
            offenders[relative_path] = hits

    assert offenders == {}


def test_bili_live_ingest_stays_readonly():
    source = _source("modules/bili_live_ingest/danmaku_core.py")

    forbidden = (
        "def send_danmaku",
        "msg/send",
        "csrf_token",
        "danmaku_max_length",
        "_danmaku_max_length",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_dashboard_actions_stays_static_projection_only():
    source = _source("core/runtime_dashboard_actions.py")

    forbidden = (
        "runtime",
        "dashboard_state",
        "runtime_health_rows",
        "pipeline",
        "dispatcher",
        "viewer_store",
        "safety_guard",
        "trigger_idle_hosting(",
        "trigger_warmup_hosting(",
        "trigger_active_engagement(",
        "connect_live_room(",
        "disconnect_live_room(",
    )
    assert [token for token in forbidden if token in source] == []


def test_recent_context_stays_compat_facade_only():
    source = _source("core/recent_context.py")

    forbidden = ("\ndef ", "\nclass ", "for result", "result.get", "spent_output_text(")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "recent_context_builders",
        "recent_context_lines",
        "recent_context_routes",
        "recent_context_text",
        "recent_output_families",
    )
    assert [module for module in required_modules if module not in source] == []


def test_recent_context_builders_only_scan_results():
    source = _source("core/recent_context_builders.py")

    forbidden = (
        "host_beat_shape",
        "topic_shape",
        "topic_title",
        "identity.get",
        "event_signal_from_result",
        "signal_route_for_event_type",
        "recent_spent_output_families",
    )
    assert [token for token in forbidden if token in source] == []


def test_recent_context_lines_only_render_context_lines():
    source = _source("core/recent_context_lines.py")

    forbidden = (
        "route_from_result",
        "spent_output_text",
        "spent_output_families",
        "recent_spent_output_families",
        "event_signal_from_result",
        "signal_route_for_event_type",
        "reversed(",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_config_stays_config_orchestration_facade():
    source = _source("core/runtime_config.py")

    forbidden = (
        "start_listening(",
        "stop_listening(",
        "update_own_config",
        "profile_ensure_active",
        "config_persist_timeout",
        "deque(",
    )
    assert [token for token in forbidden if token in source] == []

    assert "clean_config_updates" in source
    assert "persist_config_best_effort" in source
    assert "reconcile_live_listener_after_config" in source


def test_runtime_config_activation_stays_in_memory_activation_only():
    source = _source("core/runtime_config_activation.py")

    forbidden = (
        "update_own_config",
        "profile_ensure_active",
        "start_listening",
        "stop_listening",
        "restore_instructions",
        "audit.record",
        "pipeline",
        "dispatcher",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_config_persistence_stays_persistence_only():
    source = _source("core/runtime_config_persistence.py")

    forbidden = (
        "activate_config",
        "LiveConfig",
        "parse_room_id",
        "start_listening",
        "stop_listening",
        "live_connection_state",
        "safety_guard",
        "pipeline",
        "dispatcher",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_live_listener_stays_listener_reconcile_only():
    source = _source("core/runtime_live_listener.py")

    forbidden = (
        "update_own_config",
        "profile_ensure_active",
        "persist_config",
        "LiveConfig",
        "parse_room_id",
        "recent_results",
        "recent_sandbox_results",
        "dispatcher",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_active_engagement_api_stays_action_facade_only():
    source = _source("core/runtime_active_engagement_api.py")

    forbidden = (
        "from . import active_topic_rules",
        "active_topic_rules.",
        "from .active_topic_selector",
        "active_engagement_fallback_topic_candidates",
        "choose_candidate",
        "topic_candidates",
        "bili_trending_topic_candidates",
        "recent_danmaku_topic_candidates",
    )
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "runtime_active_topic_api",
        "runtime_active_topic_rules_api",
    )
    assert [module for module in required_modules if module not in source] == []


def test_runtime_active_topic_api_only_proxies_selector_helpers():
    source = _source("core/runtime_active_topic_api.py")

    forbidden = (
        "runtime_active_engagement",
        "record_active_engagement_skip",
        "trigger_active_engagement",
        "maybe_trigger_active_engagement",
        "active_topic_rules",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_runtime_active_topic_rules_api_only_proxies_rule_helpers():
    source = _source("core/runtime_active_topic_rules_api.py")

    forbidden = (
        "runtime_active_engagement",
        "active_topic_selector",
        "active_engagement_fallback_topic_candidates",
        "choose_candidate",
        "topic_candidates",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_selection_stays_selection_only():
    source = _source("core/active_topic_selection.py")

    forbidden = ("\ndef ", "\nclass ", "active_topic_rules")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "active_topic_builder",
        "active_topic_candidate_picker",
    )
    assert [module for module in required_modules if module not in source] == []


def test_active_topic_candidate_picker_only_selects_candidates():
    source = _source("core/active_topic_candidate_picker.py")

    forbidden = (
        "active_topic_sources",
        "active_engagement_fallback_topic_candidates",
        "active_topic_builder",
        "build_topic",
        "remember_topic",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "trigger_active_engagement",
        "maybe_trigger_active_engagement",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_builder_only_assembles_topic_and_rotation_state():
    source = _source("core/active_topic_builder.py")

    forbidden = (
        "active_topic_rules",
        "active_topic_sources",
        "active_engagement_fallback_topic_candidates",
        "choose_candidate",
        "choose_fresh_candidate",
        "choose_fallback_candidate",
        "clear_topic_cache",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_materials_stays_facade_only():
    source = _source("core/active_topic_materials.py")

    forbidden = ("\ndef ", "\nclass ", "for marker", "material.get")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "active_topic_material_family",
        "active_topic_material_profile",
    )
    assert [module for module in required_modules if module not in source] == []


def test_active_topic_material_family_stays_family_classifier_only():
    source = _source("core/active_topic_material_family.py")

    forbidden = (
        "active_topic_material_profile",
        "live_column",
        "reply_affordance",
        "hint",
        "runtime",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_material_profile_stays_profile_hints_only():
    source = _source("core/active_topic_material_profile.py")

    forbidden = (
        "host_material_family",
        "material.get",
        "runtime",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_sources_only_aggregates_source_modules():
    source = _source("core/active_topic_sources.py")

    forbidden = (
        "asyncio",
        "time.",
        "fetch_bilibili_trending",
        "recent_results",
        "_route_from_result",
        "material_profile",
        "is_meaningful_topic_text",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_recent_source_does_not_fetch_external_topics():
    source = _source("core/active_topic_recent_source.py")

    forbidden = (
        "asyncio",
        "import time",
        "time.monotonic",
        "fetch_bilibili_trending",
        "_active_engagement_topic_cache",
        "_active_engagement_topic_fetcher",
        "bili_trending",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_trending_source_does_not_read_recent_results():
    source = _source("core/active_topic_trending_source.py")

    forbidden = (
        "recent_results",
        "_route_from_result",
        "avatar_roast",
        "has_streak",
        "live_danmaku",
        "single_viewer_flood",
    )
    assert [token for token in forbidden if token in source] == []


def test_active_topic_selector_stays_orchestrator_and_proxy_only():
    source = _source("core/active_topic_selector.py")

    forbidden = (
        "active_topic_rules",
        "active_topic_sources",
        "active_topic_shapes",
        "active_engagement_fallback_topic_candidates",
        "def choose_candidate",
        "def fallback_topic_candidates",
        "def topic_pack",
        "def is_meaningful_topic_text",
        "def next_shape",
        "def guarded_shape",
    )
    assert [token for token in forbidden if token in source] == []

    assert "ActiveTopicCompatibilityMixin" in source


def test_active_topic_compat_stays_facade_only():
    source = _source("core/active_topic_compat.py")

    forbidden = (
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
        "trigger_",
        "maybe_trigger",
        "select_topic",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_beats_does_not_trigger_pipeline_or_create_events():
    source = _source("core/live_hosting_beats.py")

    forbidden = ("def ", "class ", "active_topic_rules", "raw_idle_hosting")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "live_hosting_beat_picker",
        "live_hosting_beat_rules",
        "live_hosting_beat_state",
    )
    assert [module for module in required_modules if module not in source] == []


def test_live_hosting_beat_picker_only_selects_and_delegates_state():
    source = _source("core/live_hosting_beat_picker.py")

    forbidden = (
        "pipeline.handle_event",
        "ViewerEvent",
        "InteractionResult",
        "PipelineStep",
        "record_result",
        "audit.record",
        "raw_idle_hosting",
        "active_topic_rules",
        "_idle_hosting_recent_beat_keys.append",
        "_idle_hosting_recent_beat_axes.append",
        "_idle_hosting_recent_beat_titles.append",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_beat_state_only_records_rotation_state():
    source = _source("core/live_hosting_beat_state.py")

    forbidden = (
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "active_topic_rules",
        "raw_idle_hosting",
        "recent_spent_output_families",
        "choose_idle_hosting",
        "_is_similar_idle_hosting_beat_title",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_beat_rules_stay_material_rules_only():
    source = _source("core/live_hosting_beat_rules.py")

    forbidden = (
        "runtime",
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "_idle_hosting_recent_beat_keys",
        "_recent_host_material_families",
        "_idle_hosting_beat_index",
        "record_chosen",
        "choose_idle_hosting",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_events_only_builds_events_and_skip_results():
    source = _source("core/live_hosting_events.py")

    forbidden = (
        "pipeline.handle_event",
        "next_idle_hosting_beat",
        "live_hosting_beats",
        "idle_hosting_loop",
        "maybe_trigger",
        "create_task",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_gates_do_not_trigger_or_construct_outputs():
    source = _source("core/live_hosting_gates.py")

    forbidden = (
        "pipeline",
        "dispatcher",
        "ViewerEvent",
        "InteractionResult",
        "record_result",
        "audit.record",
        "next_idle_hosting_beat",
        "idle_hosting_event",
        "warmup_hosting_event",
        "handle_event",
        "create_task",
    )
    assert [token for token in forbidden if token in source] == []


def test_live_hosting_loop_only_runs_auto_loop():
    source = _source("core/live_hosting_loop.py")

    forbidden = (
        "ViewerEvent",
        "InteractionResult",
        "PipelineStep",
        "live_hosting_beats",
        "live_hosting_events",
        "next_idle_hosting_beat",
        "idle_hosting_event",
        "warmup_hosting_event",
        "record_result",
        "handle_event",
        "dispatcher",
        "build_request",
    )
    assert [token for token in forbidden if token in source] == []


def test_output_contract_bridge_stays_host_core_free():
    source = _source("adapters/output_contract_bridge.py")

    assert "main_logic" not in source
    assert "send_lanlan_response" not in source


def test_contracts_facade_stays_reexport_only():
    source = _source("core/contracts.py")

    forbidden = ("@dataclass", "\nclass ", "\ndef ", "field(")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "contracts_config",
        "contracts_events",
        "contracts_interaction",
        "contracts_safety",
        "contracts_types",
        "contracts_viewer",
    )
    assert [module for module in required_modules if module not in source] == []


def test_contract_submodules_stay_in_their_lane():
    module_forbidden_tokens = {
        "core/contracts_config.py": (
            "ViewerEvent",
            "InteractionResult",
            "SafetyDecision",
            "LiveRoomStatus",
        ),
        "core/contracts_events.py": (
            "LiveConfig",
            "ViewerIdentity",
            "InteractionResult",
            "SafetyDecision",
        ),
        "core/contracts_viewer.py": (
            "LiveConfig",
            "ViewerEvent",
            "InteractionResult",
            "SafetyDecision",
        ),
        "core/contracts_interaction.py": (
            "LiveConfig",
            "SafetyDecision",
            "LiveRoomStatus",
        ),
        "core/contracts_safety.py": (
            "LiveConfig",
            "ViewerEvent",
            "InteractionResult",
        ),
    }

    for relative_path, forbidden in module_forbidden_tokens.items():
        source = _source(relative_path)
        assert [token for token in forbidden if token in source] == []


def test_prompt_context_stays_compat_facade_only():
    source = _source("modules/_prompt_context.py")

    forbidden = ("\ndef ", "\nclass ", "getattr(", "try:", "if kind ==")
    assert [token for token in forbidden if token in source] == []

    required_modules = (
        "_prompt_context_blocks",
        "_prompt_context_compaction",
        "_prompt_rules",
    )
    assert [module for module in required_modules if module not in source] == []


def test_prompt_rules_do_not_read_context_providers():
    source = _source("modules/_prompt_rules.py")

    forbidden = (
        "recent_interaction_context",
        "viewer_session_context",
        "prompt_block_for_event",
        "compact_context_line",
        "Any",
    )
    assert [token for token in forbidden if token in source] == []


def test_prompt_context_blocks_only_render_provider_blocks():
    source = _source("modules/_prompt_context_blocks.py")

    forbidden = (
        "SHORT_REPLY_CONTRACT",
        "HOST_REPLY_CONTRACT",
        "short_reply_rules",
        "anti_repeat_rules",
        "live_output_quality_rules",
        "sustained_charm_rules",
        "SPENT_OUTPUT_FAMILY_MARKER",
        "REPLY_PATH_MARKER",
    )
    assert [token for token in forbidden if token in source] == []


def test_prompt_context_compaction_stays_string_compaction_only():
    source = _source("modules/_prompt_context_compaction.py")

    forbidden = (
        "recent_interaction_context",
        "viewer_session_context",
        "prompt_block_for_event",
        "short_reply_rules",
        "anti_repeat_rules",
        "live_output_quality_rules",
        "sustained_charm_rules",
    )
    assert [token for token in forbidden if token in source] == []
