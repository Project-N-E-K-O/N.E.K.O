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

"""Memory server package.

Formerly the monolithic ``app/memory_server.py`` (4.6k lines); now split by
domain following the ``main_routers/game_router`` runtime pattern (mutable
state co-located with its consumers):

  - ``_shared``           — logging setup + ``validate_lanlan_name``
                            (dependency root, imports no sibling)
  - ``rows``              — pure SQL-row / message-list extraction helpers
  - ``gates``             — maintenance state, feature switches, idle
                            detection, loop scheduling constants
  - ``runtime``           — FastAPI ``app``, component singletons +
                            init/reload/startup/shutdown, storage-limited
                            middleware, lifecycle endpoints, background-task
                            registry, settle locks
  - ``outbox_infra``      — outbox handler registry + startup replay
  - ``signal_extraction`` — Stage-1/Stage-2 path A/B + signal dispatch
  - ``review``            — review + backup-compression pipeline
  - ``evidence_loops``    — rebuttal / auto-promote / idle-maint / archive
                            sweep / migrations / slow recheck loops
  - ``refine_loops``      — persona/reflection refine + synthesis loops
  - ``post_turn``         — per-turn signals task + outbox op registration
  - ``routes``            — session API endpoints + /new_dialog QPS loop

Submodules are imported below in dependency order (endpoints register on
``runtime.app`` as ``routes``/``runtime`` import), and the top-level names
of the old module are re-exported here so existing imports keep working
(``launcher`` only needs ``memory_server.app``).

Note for tests: ``monkeypatch.setattr`` must target the submodule that
*owns* a symbol (e.g. ``runtime.fact_store``,
``gates._ais_powerful_memory_enabled``), not this package facade --
re-exports are snapshots, they do not rebind submodule globals. Rebindable
submodule state (the ``runtime`` component singletons and init flags,
``gates._maint_state`` / ``gates._last_activity_time``,
``outbox_infra._replay_semaphore``, ``evidence_loops._RECHECK_RR_CURSOR``,
``runtime.enable_shutdown``) is deliberately NOT re-exported: a facade
snapshot would silently go stale after the first in-place rebind.
Container objects that are only ever mutated (``_OUTBOX_HANDLERS``,
``_signal_check_state``, ``correction_tasks``, ...) ARE re-exported --
they share identity with the owning module.
"""

import sys
import os
# Three dirname hops: __init__.py → memory_server/ → app/ → repo root.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Force project root to sys.path[0] — see agent_server.py top for rationale.
if sys.path[0:1] != [_repo_root]:
    sys.path.insert(0, _repo_root)

# Wire DI bindings explicitly — kept from the pre-package era when direct
# script invocation (``python app/memory_server.py``) bypassed app/__init__.py.
# Idempotent under both remaining paths (launcher's ``from app import
# memory_server`` and standalone ``python -m app.memory_server``).
from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

from ._shared import logger, log_config, validate_lanlan_name  # noqa: F401

from . import gates  # noqa: F401
from .gates import (  # noqa: F401
    IDLE_CHECK_INTERVAL,
    IDLE_THRESHOLD,
    LONG_IDLE_REVIEW_BYPASS_SECONDS,
    MIN_NEW_MSGS_FOR_REVIEW,
    REVIEW_MIN_INTERVAL,
    REVIEW_SKIP_HISTORY_LEN,
    _INITIAL_DELAY_ARCHIVE,
    _INITIAL_DELAY_AUTO_PROMOTE,
    _INITIAL_DELAY_IDLE_MAINT,
    _INITIAL_DELAY_PERSONA_REFINE,
    _INITIAL_DELAY_REBUTTAL,
    _INITIAL_DELAY_REFLECTION_REFINE,
    _INITIAL_DELAY_REFLECTION_SYNTHESIS,
    _INITIAL_DELAY_SIGNAL,
    _aclear_review_clean,
    _ais_powerful_memory_enabled,
    _ais_review_enabled,
    _aload_maint_state,
    _asave_maint_state,
    _is_idle,
    _is_review_clean,
    _maint_state_path,
    _touch_activity,
)
from .rows import (  # noqa: F401
    _coerce_db_ts,
    _extract_ai_response,
    _extract_role_tagged_messages_from_rows,
    _extract_user_messages,
    _extract_user_messages_from_rows,
    _extract_user_messages_with_ts_from_rows,
    _has_human_messages,
    _trim_to_user_msg_bracket,
)

from . import runtime  # noqa: F401
from .runtime import (  # noqa: F401
    ContinueStorageStartupRequest,
    _BACKGROUND_TASKS,
    _STORAGE_LIMITED_MODE_ALLOWED_PATHS,
    _bootstrap_embedding_worker,
    _defer_time_manager_cleanup,
    _deferred_time_managers,
    _get_settle_lock,
    _memory_runtime_init_lock,
    _reload_lock,
    _reset_confirmed_at_for_all_characters,
    _settle_locks,
    _spawn_background_task,
    app,
    block_storage_startup,
    continue_storage_startup,
    ensure_memory_server_runtime_initialized,
    handle_maintenance_mode_error,
    health,
    internal_reset_confirmed_at,
    release_character_resources,
    reload_config,
    reload_memory_components,
    shutdown_event,
    shutdown_event_handler,
    shutdown_memory_server,
    startup_event_handler,
    storage_limited_mode_guard,
)

from . import outbox_infra  # noqa: F401
from .outbox_infra import (  # noqa: F401
    OutboxHandler,
    _OUTBOX_HANDLERS,
    _REPLAY_CONCURRENCY,
    _replay_pending_outbox,
    _run_outbox_op,
    register_outbox_handler,
)

from . import signal_extraction  # noqa: F401
from .signal_extraction import (  # noqa: F401
    _adispatch_evidence_signals,
    _amaybe_trigger_negative_keyword_hook,
    _periodic_signal_extraction_loop,
    _run_path_b,
    _signal_check_mark_done,
    _signal_check_record_turn,
    _signal_check_should_run,
    _signal_check_state,
    _signal_check_window_start,
    _stage1_path_a_bump_failure,
    _stage1_path_b_bump_failure,
)

from . import review  # noqa: F401
from .review import (  # noqa: F401
    _clear_compress_backup_failure,
    _count_new_user_msgs_since_last_review,
    _get_review_spawn_lock,
    _on_compress_done,
    _record_compress_backup_failure,
    _record_review_failure,
    _review_spawn_locks,
    _run_backup_compress,
    _run_review_in_background,
    compress_backup_tasks,
    correction_cancel_flags,
    correction_tasks,
    maybe_spawn_review,
)

from . import evidence_loops  # noqa: F401
from .evidence_loops import (  # noqa: F401
    AUTO_PROMOTE_CHECK_INTERVAL,
    REBUTTAL_CHECK_INTERVAL,
    REBUTTAL_DRAIN_BATCH_LIMIT,
    REBUTTAL_FIRST_RUN_LOOKBACK_HOURS,
    REBUTTAL_SQL_ROW_LIMIT,
    _MIGRATION_MARKER_ENTITY,
    _MIGRATION_MARKER_ENTRY,
    _aone_shot_archive_migration_if_needed,
    _aone_shot_migration_if_needed,
    _migration_seed_from_reflection_status,
    _periodic_archive_sweep_loop,
    _periodic_auto_promote_loop,
    _periodic_idle_maintenance_loop,
    _periodic_rebuttal_loop,
    _periodic_slow_memory_recheck_loop,
    _rebuttal_bump_failure,
    _rebuttal_clear_failures,
    _rebuttal_failures,
    _resolve_rebuttal_start_time,
)

from . import refine_loops  # noqa: F401
from .refine_loops import (  # noqa: F401
    _periodic_persona_refine_loop,
    _periodic_reflection_refine_loop,
    _periodic_reflection_synthesis_loop,
    _run_persona_refine_for_character,
    _run_reflection_refine_for_character,
)

from . import post_turn  # noqa: F401
from .post_turn import (  # noqa: F401
    _outbox_post_turn_signals_handler,
    _run_post_turn_signals,
    _spawn_outbox_post_turn_signals,
)

from . import routes  # noqa: F401
from .routes import (  # noqa: F401
    HistoryRequest,
    NEW_DIALOG_QPS_FLUSH_INTERVAL,
    QueryMemoryRequest,
    _format_legacy_settings_as_text,
    _new_dialog_qps_counter,
    _periodic_new_dialog_qps_log_loop,
    _safe_auto_promote,
    api_followup_topics,
    api_memory_funnel,
    api_record_surfaced,
    api_reflect,
    cache_conversation,
    cancel_correction,
    get_memory,
    get_persona,
    get_recent_history,
    get_settings,
    last_conversation_gap,
    new_dialog,
    process_conversation,
    process_conversation_for_renew,
    query_memory,
    settle_conversation,
)
