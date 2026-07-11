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

"""Cloudsave runtime package.

Formerly the monolithic ``utils/cloudsave_runtime.py`` (4.2k lines); now
split by domain. Submodules are imported below in dependency order and every
top-level name of the old module is re-exported here so existing imports
keep working:

- ``_shared``: constants, exception types, availability gates, name audit,
  sensitive-value scanning.
- ``fence``: root mode state, the global write fence and the cross-process
  cloud apply lock (the process-wide lock globals live there exclusively).
- ``staging``: staging workspace, hashing, atomic copy, SQLite shadow copy
  and tombstone-state primitives.
- ``legacy_migration``: one-shot legacy runtime root import/repair.
- ``bindings``: character binding derivation and catalog payloads.
- ``snapshots``: local/cloud character snapshots and summary/detail views.
- ``operations``: single-character and full snapshot export/import with
  backup/rollback.
- ``bootstrap``: manifest handling and the phase-0 environment bootstrap.

Note for tests: monkeypatching this package facade keeps working for
``_atomic_copy_file`` / ``_apply_runtime_file`` (in-package callers resolve
them through the facade at call time) and for symbols that out-of-package
consumers import lazily inside function bodies (for example
``assert_cloudsave_writable`` and ``runtime_root_has_user_content``). For
other helpers, patch the submodule that consumes them -- re-exports are
snapshots and do not rebind submodule globals.

The mutable cross-process lock globals (``fence._cloud_apply_lock_handle``
and ``fence._cloud_apply_lock_file``) are intentionally not re-exported: a
facade re-export would only be a stale snapshot of process-wide state.
"""

from ._shared import (  # noqa: F401
    CLOUDSAVE_DISABLED_ENV,
    CLOUDSAVE_DISABLED_LOCAL_STATE_UNAVAILABLE,
    CloudsaveDeadlineExceeded,
    CloudsaveOperationError,
    GLOBAL_CONVERSATION_KEY,
    LEGACY_OPTIONAL_STATE_FILES,
    LEGACY_RUNTIME_DIR_NAMES,
    MANAGED_CLOUDSAVE_PREFIXES,
    MANAGED_MEMORY_FILENAMES,
    MaintenanceModeError,
    NON_RUNTIME_CONTENT_DIR_NAMES,
    ROOT_CONFIG_MERGE_FILES,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    ROOT_MODE_BOOTSTRAP_READONLY,
    ROOT_MODE_DEFERRED_INIT,
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
    RUNTIME_ASSET_DIR_NAMES,
    SENSITIVE_KEY_NAMES,
    SENSITIVE_TOKENS,
    SENSITIVE_VALUE_PATTERNS,
    TARGET_OPTIONAL_STATE_FILES,
    WRITE_BLOCKING_MODES,
    _assert_deadline_not_exceeded,
    _ensure_local_state_directory_or_raise,
    _normalize_audit_name,
    _raise_cloudsave_disabled,
    _raise_for_name_audit,
    _utc_now_iso,
    assert_deadline_not_exceeded,
    audit_cloudsave_character_names,
    cloudsave_disabled_reason,
    is_cloudsave_disabled,
    is_cloudsave_disabled_due_to_local_state_unavailable,
    is_cloudsave_provider_available,
    logger,
    scan_for_sensitive_values,
)
from .fence import (  # noqa: F401
    _cloud_apply_mutex_name,
    _process_holds_cloud_apply_lock,
    _recover_stale_write_blocking_mode,
    _should_preserve_write_blocking_mode,
    acquire_cloud_apply_lock,
    assert_cloudsave_writable,
    cloud_apply_fence,
    get_root_mode,
    get_root_state,
    is_write_fence_active,
    maintenance_error_payload,
    release_cloud_apply_lock,
    set_root_mode,
    should_write_root_mode_normal_after_startup,
)
from .staging import (  # noqa: F401
    SQLITE_FILE_HEADER,
    _apply_runtime_file,
    _atomic_copy_file,
    _build_manifest_fingerprint,
    _cleanup_empty_parent_dirs,
    _create_staging_workspace,
    _json_canonical_dumps,
    _list_existing_cloudsave_files,
    _load_json_if_exists,
    _load_local_tombstones_state,
    _load_tombstone_names_from_state_path,
    _looks_like_sqlite_database,
    _make_tombstones_catalog_payload,
    _normalize_tombstone_entry,
    _normalize_tombstones_state,
    _run_sqlite_shadow_copy,
    _save_local_tombstones_state,
    _sha256_bytes,
    _sha256_file,
    _stage_file_copy,
    _stage_json_file,
    _stage_memory_file,
    atomic_copy_file,
    create_staging_workspace,
    load_json_if_exists,
)
from .legacy_migration import (  # noqa: F401
    _character_payload_looks_default,
    _collect_memory_character_names,
    _config_payload_looks_default,
    _config_payload_looks_seeded,
    _copy_optional_legacy_state,
    _copy_runtime_root_entries,
    _create_legacy_import_backup_path,
    _deep_merge_json_dicts,
    _directory_has_meaningful_content,
    _is_ignorable_runtime_entry,
    _legacy_root_provides_repair_benefit,
    _legacy_source_was_already_imported,
    _load_seed_characters_payload,
    _master_payload_looks_default,
    _merge_characters_payloads,
    _merge_preferences_payloads,
    _normalize_catgirl_payload,
    _normalize_preferences_payload,
    _preferences_entry_key,
    _replace_runtime_root,
    _root_has_staged_cloudsave_snapshot,
    _runtime_config_dir_has_user_content,
    _runtime_config_path_matches_pristine_default,
    _runtime_root_has_user_content,
    _runtime_root_summary,
    _stage_merged_runtime_configs,
    import_legacy_runtime_root_if_needed,
    runtime_root_has_user_content,
)
from .bindings import (  # noqa: F401
    _build_catalog_current_character_payload,
    _build_catalog_index_payload,
    _build_character_origin_match_payload,
    _build_character_origin_profile_fingerprint,
    _build_live2d_model_ref_hints,
    _build_runtime_preferences_payload,
    _collect_binding_live2d_roots,
    _collect_binding_workshop_roots,
    _collect_workshop_character_origin_candidates,
    _derive_binding_asset_display_name,
    _derive_binding_asset_source,
    _derive_binding_asset_source_id,
    _derive_binding_asset_state,
    _derive_binding_experience_overrides,
    _derive_binding_model_reference,
    _derive_character_binding_summary,
    _derive_character_origin_metadata,
    _extract_conversation_settings,
    _infer_binding_source_from_resolved_path,
    _is_path_within,
    _load_staged_json_file,
    _load_user_preferences_entries,
    _normalize_workshop_character_model_ref,
    _parse_binding_payloads,
    _parse_catalog_character_names,
    _rank_live2d_model3_path,
    _resolve_binding_file_path,
    _select_workshop_character_origin_candidate,
)
from .bootstrap import (  # noqa: F401
    bootstrap_local_cloudsave_environment,
    build_default_cloudsave_manifest,
    ensure_cloudsave_manifest,
    load_cloudsave_manifest,
    save_cloudsave_manifest,
)
from .snapshots import (  # noqa: F401
    _build_character_meta_payload,
    _build_character_payload_fingerprint,
    _build_character_summary_warnings,
    _build_cloud_character_snapshot,
    _build_cloudsave_summary_state,
    _build_local_character_snapshot,
    _collect_cloudsave_binding_payloads,
    _collect_cloudsave_catalog_entries,
    _collect_cloudsave_memory_hashes,
    _collect_cloudsave_meta_payloads,
    _compute_managed_memory_file_hash,
    _iso_from_timestamp,
    _load_cloudsave_character_payloads,
    _load_cloudsave_character_unit,
    _load_cloudsave_sharded_character_unit,
    _load_cloudsave_tombstone_names,
    _max_mtime_iso,
    _memory_file_hashes_from_root,
    _merge_character_summary_item,
    _stable_binding_payload_for_fingerprint,
    _stage_single_character_cloudsave_entries,
    build_cloudsave_character_detail,
    build_cloudsave_summary,
)
from .operations import (  # noqa: F401
    _assert_single_character_name_safe,
    _build_backup_path,
    _collect_memory_stage_entries,
    _default_catalog_index_payload,
    _default_tombstones_catalog_payload,
    _managed_target_relative_path,
    _rebuild_cloudsave_manifest_from_disk,
    _remove_tombstone_from_catalog_payload,
    _remove_tombstone_from_state_payload,
    _resolve_managed_target_path,
    _restore_backup_records,
    _snapshot_existing_targets,
    _upsert_catalog_character_entry,
    _write_operation_backup_metadata,
    export_cloudsave_character_unit,
    export_local_cloudsave_snapshot,
    import_cloudsave_character_unit,
    import_local_cloudsave_snapshot,
    restore_cloudsave_operation_backup,
)
