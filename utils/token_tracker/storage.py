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
"""Local token-usage storage and migration methods."""

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from utils import file_utils as _file_utils
from utils.file_utils import atomic_write_json

from ._shared import _file_lock, _merge_day_stats, logger


def _reject_non_finite_json_constant(token: str) -> None:
    """Reject non-standard NaN/Infinity tokens when parsing legacy files."""
    raise ValueError(f"non-finite JSON constant in legacy token_usage: {token}")


class StorageMixin:
    """Local token-usage storage and migration methods."""

    @property
    def _storage_path(self) -> Path:
        return self._config_manager.config_dir / "token_usage.json"

    @property
    def _lock_file_path(self) -> Path:
        return self._config_manager.config_dir / ".token_usage.lock"

    @property
    def _storage_dir(self) -> Path:
        return self._config_manager.config_dir

    def _migrate_legacy_files(self):
        """Merge legacy token_usage_{instance_id}.json files into the new single file.

        Runs only once per legacy file content. Old files are deleted only after
        the merged snapshot (including migrated file digests) is written atomically.
        """
        try:
            if not any(self._storage_dir.glob("token_usage_*.json")):
                return

            with _file_lock(self._lock_file_path):
                legacy_files = sorted(self._storage_dir.glob("token_usage_*.json"))
                if not legacy_files:
                    return

                logger.info(f"Token tracker: migrating {len(legacy_files)} legacy per-instance files")

                # Read the existing merged file, if any.
                existing = self._load_file(self._storage_path)
                if not existing:
                    if self._storage_path.exists():
                        raise ValueError("cannot read migration target")
                    existing = self._empty_file_data()

                raw_migrated = existing.get("migrated_legacy_files")
                migrated_map: dict[str, str] = (
                    {
                        str(k): str(v)
                        for k, v in raw_migrated.items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
                    if isinstance(raw_migrated, dict)
                    else {}
                )
                present_names = {p.name for p in legacy_files}
                stale_names = [k for k in migrated_map if k not in present_names]
                if stale_names:
                    # Only prune digests after confirming prior unlinks in this directory are durable.
                    try:
                        _file_utils._fsync_directory(self._storage_dir)
                    except OSError as e:
                        logger.debug(
                            "Token tracker: failed to sync storage directory before pruning index: %s",
                            e,
                        )
                        pruned_map = dict(migrated_map)
                    else:
                        pruned_map = {k: v for k, v in migrated_map.items() if k in present_names}
                else:
                    pruned_map = dict(migrated_map)
                index_dirty = pruned_map != migrated_map
                migrated_map = pruned_map

                cleanup_files: list[Path] = []
                merged_new = False
                for p in legacy_files:
                    try:
                        raw = p.read_bytes()
                        file_digest = hashlib.sha256(raw).hexdigest()
                        if migrated_map.get(p.name) == file_digest:
                            cleanup_files.append(p)
                            continue
                        data = json.loads(raw, parse_constant=_reject_non_finite_json_constant)
                        if not isinstance(data, dict) or data.get("version") != 1:
                            raise ValueError("unsupported legacy token_usage version")
                        daily_stats = data["daily_stats"]
                        records = data.get("recent_records", [])
                        if not isinstance(daily_stats, dict) or not isinstance(records, list):
                            raise TypeError("legacy token_usage schema invalid")

                        # Stage only the affected days. A malformed later bucket or
                        # record must not leave any contribution from this source.
                        staged_days = {}
                        for day, delta in daily_stats.items():
                            staged = copy.deepcopy(existing["daily_stats"].get(day, {}))
                            _merge_day_stats(staged, delta)
                            staged_days[day] = staged
                        staged_records = self._dedupe_records(existing["recent_records"] + records)
                        # Reject overflow to inf/nan and lone surrogates using the writer's UTF-8 rules.
                        json.dumps(
                            {"daily_stats": staged_days, "recent_records": staged_records},
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode("utf-8")
                        existing["daily_stats"].update(staged_days)
                        existing["recent_records"] = staged_records
                        migrated_map[p.name] = file_digest
                        cleanup_files.append(p)
                        merged_new = True
                        index_dirty = True
                    except Exception as e:
                        logger.debug(f"Token tracker: failed to migrate {p.name}: {e}")

                if not merged_new and not index_dirty and not cleanup_files:
                    return

                # Even an existing digest can follow a replace whose directory
                # sync failed. Re-establish durability before unlinking its source.
                if merged_new:
                    existing["last_saved"] = datetime.now().isoformat()
                if migrated_map:
                    existing["migrated_legacy_files"] = migrated_map
                else:
                    existing.pop("migrated_legacy_files", None)
                self._storage_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self._storage_path, existing, strict_dir_sync=True)

                # Migration complete: delete the old files only after atomic write succeeds.
                unlinked_names: set[str] = set()
                unlinked_dirs: set[Path] = set()
                for p in cleanup_files:
                    try:
                        p.unlink(missing_ok=True)
                        if not p.exists():
                            unlinked_names.add(p.name)
                            unlinked_dirs.add(p.parent)
                    except Exception as e:
                        logger.debug(f"Token tracker: failed to remove migrated file {p.name}: {e}")

                if unlinked_names:
                    try:
                        for parent_dir in sorted(unlinked_dirs):
                            _file_utils._fsync_directory(parent_dir)
                    except OSError as e:
                        logger.debug(
                            "Token tracker: failed to sync migrated directory before pruning index: %s",
                            e,
                        )
                    else:
                        for name in unlinked_names:
                            migrated_map.pop(name, None)
                        if migrated_map:
                            existing["migrated_legacy_files"] = migrated_map
                        else:
                            existing.pop("migrated_legacy_files", None)
                        try:
                            atomic_write_json(self._storage_path, existing)
                        except Exception as e:
                            logger.debug(f"Token tracker: failed to prune migrated file index: {e}")

            logger.info("Token tracker: legacy file migration complete")
        except Exception as e:
            logger.warning(f"Token tracker: legacy migration failed (non-critical): {e}")

    @staticmethod
    def _load_file(path: Path) -> dict:
        """Load data from the file; returns an empty dict when the file is invalid or missing."""
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("version") == 1:
                    return data
        except Exception:
            # Missing or malformed persisted state falls back to an empty snapshot.
            pass
        return {}

    @staticmethod
    def _empty_day() -> dict:
        return {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
            "total_prompt_chars": 0,
            "call_count": 0,
            "error_count": 0,
            "by_model": {},
            "by_call_type": {},
        }

    @staticmethod
    def _empty_bucket() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "cache_creation_tokens": 0,
                "prompt_chars": 0, "call_count": 0}

    @staticmethod
    def _empty_file_data() -> dict:
        return {"version": 1, "daily_stats": {}, "recent_records": [], "last_saved": ""}

    @staticmethod
    def _dedupe_records(records: list, max_keep: int = 200) -> list:
        """Dedupe + sort + truncate recent_records."""
        seen = set()
        unique = []
        for r in records:
            key = (r.get("ts"), r.get("model"), r.get("type"), r.get("src"))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        unique.sort(key=lambda x: x.get("ts", 0))
        return unique[-max_keep:]
