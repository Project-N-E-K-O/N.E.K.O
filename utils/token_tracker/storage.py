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

import json
from datetime import datetime
from pathlib import Path
from utils.file_utils import atomic_write_json

from ._shared import _file_lock, _merge_day_stats, logger

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

        Runs only once at first instantiation. Old files are deleted after migration.
        """
        try:
            legacy_files = list(self._storage_dir.glob("token_usage_*.json"))
            if not legacy_files:
                return

            logger.info(f"Token tracker: migrating {len(legacy_files)} legacy per-instance files")

            with _file_lock(self._lock_file_path):
                # 读取现有的合并文件（如果已存在）
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                for p in legacy_files:
                    try:
                        data = self._load_file(p)
                        if data:
                            for day_key, day_val in data.get("daily_stats", {}).items():
                                if day_key not in existing["daily_stats"]:
                                    existing["daily_stats"][day_key] = day_val
                                else:
                                    _merge_day_stats(existing["daily_stats"][day_key], day_val)
                            existing["recent_records"].extend(data.get("recent_records", []))
                        # 迁移完毕，删除旧文件
                        p.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Token tracker: failed to migrate {p.name}: {e}")

                # 去重 recent_records
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])
                existing["last_saved"] = datetime.now().isoformat()

                self._storage_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self._storage_path, existing)

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
            "total_prompt_chars": 0,
            "call_count": 0,
            "error_count": 0,
            "by_model": {},
            "by_call_type": {},
        }

    @staticmethod
    def _empty_bucket() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}

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
