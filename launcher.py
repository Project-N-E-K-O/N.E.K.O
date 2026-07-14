# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Thin compatibility entry point for the N.E.K.O launcher."""

from __future__ import annotations

from launcher_core.bootstrap import (  # noqa: F401
    IS_FROZEN,
    _configure_ssl_cert_bundle,
    _configure_stdio_utf8,
    _ensure_utf8_filesystem_encoding,
    _get_project_venv_python,
    _maybe_reexec_into_project_venv,
    bundle_dir,
    io,
    os,
    signal,
    sys,
)

try:
    from launcher_core.bootstrap import _tiktoken_cache  # noqa: F401
except ImportError:
    pass

if __name__ == '__main__':
    _ensure_utf8_filesystem_encoding()

from launcher_core.runtime import (  # noqa: F401
    APP_NAME,
    AVOID_FALLBACK_PORTS,
    CLOUDSAVE_DISABLED_ENV,
    CLOUDSAVE_DISABLED_LOCAL_STATE_UNAVAILABLE,
    DEFAULT_PORTS,
    Dict,
    Event,
    INSTANCE_ID,
    INTERNAL_DEFAULT_PORTS,
    JOB_HANDLE,
    LAUNCH_ID,
    MAIN_SERVER_PORT,
    MEMORY_SERVER_PORT,
    MODULE_TO_PORT_KEY,
    Path,
    Process,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
    SERVERS,
    SHUTDOWN_MODULE_ORDER,
    STARTUP_WAIT_RESULT_STORAGE_RESTART,
    TOOL_SERVER_PORT,
    _bootstrap_launcher_runtime,
    _build_launcher_relaunch_command,
    _classify_port_conflict,
    _cleanup_done,
    _cleanup_lock,
    _configure_multiprocessing_executable,
    _detach_child_process_session,
    _ensure_playwright_browsers,
    _existing_neko_services,
    _expected_launcher_shutdown,
    _get_last_error,
    _handle_termination_signal,
    _initialize_launcher_context,
    _install_logging_brace_compat,
    _is_expected_launcher_shutdown,
    _is_local_state_directory_error,
    _is_pending_storage_restart_request,
    _is_port_bindable,
    _iter_servers_for_shutdown,
    _mark_expected_launcher_shutdown,
    _maybe_schedule_storage_restart,
    _persist_post_startup_root_state,
    _pick_fallback_port,
    _prepare_cloudsave_runtime_for_launch,
    _reload_runtime_config_from_env,
    _resolve_storage_layout_for_launch,
    _should_detach_stdio_for_relaunch,
    _should_use_merged_mode,
    _show_error_dialog,
    _spawn_restarted_launcher,
    _sync_runtime_config_globals,
    acquire_startup_lock,
    apply_port_strategy,
    atexit,
    bootstrap_local_cloudsave_environment,
    check_port,
    cleanup_servers,
    clear_storage_layout_env,
    cloud_apply_fence,
    config_module,
    ctypes,
    datetime,
    emit_frontend_event,
    export_storage_layout_to_env,
    freeze_support,
    get_cloudsave_manager,
    get_config_manager,
    get_hyperv_excluded_ranges,
    get_port_owners,
    importlib,
    is_port_in_excluded_range,
    itertools,
    json,
    logging,
    main,
    multiprocessing,
    paths_equal,
    probe_neko_health,
    register_shutdown_hooks,
    release_startup_lock,
    report_startup_failure,
    reset_config_manager_cache,
    resolve_storage_layout,
    run_agent_server,
    run_main_server,
    run_memory_server,
    run_merged_servers,
    run_pending_storage_migration,
    set_port_probe_reuse,
    set_root_mode,
    setup_job_object,
    should_write_root_mode_normal_after_startup,
    show_spinner,
    socket,
    start_launcher,
    start_server,
    subprocess,
    threading,
    time,
    timezone,
    uuid,
    wait_for_servers,
)

if __name__ == '__main__':
    sys.exit(start_launcher())
