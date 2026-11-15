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

"""Steam Workshop configuration mixin.

workshop_config.json load/save/repair, invalid-path cleanup and workshop
root path resolution.
"""
import json
import os
import threading
import time

from utils.file_utils import (
    _REPLACE_BUSY_WINERRORS,
    atomic_write_json,
    read_json_tolerating_replace,
    running_on_event_loop,
)

from ._shared import logger

# Protects the lazy creation of the "last-good cache micro-lock" above; see _last_good_workshop_config_lock.
_LAST_GOOD_LOCK_GUARD = threading.Lock()

# Upper bound for considering a read-side fallback "transient." Read retries take about 155ms and the os.replace window is only a few milliseconds;
# 5 seconds is many times longer, so ACCESS_DENIED lasting that long is not the same race.
_WORKSHOP_CONFIG_FALLBACK_GRACE_S = 5.0

# Workshop configuration constants — workshop_dir will be accessed through self when ConfigManager is instantiated


class WorkshopMixin:
    """Steam Workshop config and path resolution."""

    def get_workshop_config_path(self):
        """
        Get the workshop config file path
        
        Returns:
            str: absolute path of the workshop config file
        """
        return str(self.get_config_path('workshop_config.json'))

    def _normalize_workshop_folder_path(self, folder_path):
        """Normalize a workshop directory path; returns None on failure."""
        if not isinstance(folder_path, str):
            return None

        path_str = folder_path.strip()
        if not path_str:
            return None

        try:
            # Match workshop_utils: resolve relative paths against the user directory
            if not os.path.isabs(path_str):
                path_str = os.path.join(os.path.expanduser('~'), path_str)
            return os.path.normpath(path_str)
        except Exception:
            return None

    def _cleanup_invalid_workshop_config_file(self, config_path):
        """
        Check and clean up invalid workshop config files.

        Rule: if any path field present in the config is not a valid directory, delete the whole config file.
        """
        if not config_path.exists():
            return False

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"workshop配置文件损坏，准备删除: {config_path}, error={e}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除损坏workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        if not isinstance(config_data, dict):
            logger.warning(f"workshop配置格式非法（非对象），准备删除: {config_path}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除非法workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        path_keys = ("user_mod_folder", "steam_workshop_path", "default_workshop_folder")
        for key in path_keys:
            if key not in config_data:
                continue

            normalized_path = self._normalize_workshop_folder_path(config_data.get(key))
            if not normalized_path or not os.path.isdir(normalized_path):
                logger.warning(
                    f"发现无效workshop路径，准备删除配置文件: {config_path}, "
                    f"field={key}, value={config_data.get(key)!r}"
                )
                try:
                    config_path.unlink()
                    return True
                except Exception as delete_error:
                    logger.error(f"删除无效workshop配置文件失败: {config_path}, error={delete_error}")
                    return False

        return False

    def _cleanup_invalid_workshop_configs(self):
        """Check workshop configs in both the documents and project directories and clean up invalid files."""
        candidates = (
            self.config_dir / "workshop_config.json",
            self.project_config_dir / "workshop_config.json",
        )
        for candidate in candidates:
            self._cleanup_invalid_workshop_config_file(candidate)

    def _read_workshop_config_file(self):
        """Read workshop_config.json as-is, without the self-healing rebase."""
        config_path = self.get_workshop_config_path()
        try:
            if not os.path.exists(config_path):
                return None
            return read_json_tolerating_replace(config_path)
        except Exception:
            return None

    @property
    def _last_good_workshop_config_lock(self):
        """Tiny lock guarding only the cache compare-and-set (never any I/O)."""
        lock = getattr(self, "_last_good_lock_obj", None)
        if lock is not None:
            return lock
        # Lazy creation itself must be serialized: if two threads enter together, they can each create a different lock and guard different things,
        # making the lock effectively nonexistent. Use a module-level guard for double-checking.
        with _LAST_GOOD_LOCK_GUARD:
            lock = getattr(self, "_last_good_lock_obj", None)
            if lock is None:
                lock = threading.Lock()
                self._last_good_lock_obj = lock
        return lock

    def _clear_workshop_config_fallback_streak(self) -> None:
        """Forget an in-progress fallback streak; the file just read fine."""
        with self._last_good_workshop_config_lock:
            self._workshop_config_fallback_since = None
            self._workshop_config_fallback_escalated = False

    def _note_workshop_config_fallback(self, exc) -> None:
        """Escalate once when the read-side fallback stops looking transient.

        ``winerror`` 5 is ACCESS_DENIED, which Windows raises both for the
        millisecond-wide window where ``os.replace`` has the target open and
        for a permanent revocation (an ACL change, Controlled Folder Access, a
        path that is now a directory). The code cannot tell those apart, so
        persistence is the only usable signal: the read-side retry budget is
        about 155ms, and a replace race clears in single-digit milliseconds,
        so anything still failing seconds later is not that race.

        The value keeps being served either way — it is the user's real
        workshop root, and swapping to defaults would silently relocate every
        upload. What changes is that the failure stops being a debug-level
        shrug nobody will ever find.
        """
        now = time.monotonic()
        with self._last_good_workshop_config_lock:
            since = getattr(self, "_workshop_config_fallback_since", None)
            escalated = getattr(self, "_workshop_config_fallback_escalated", False)
            if since is None:
                self._workshop_config_fallback_since = now
                logger.warning("加载workshop配置失败，沿用上一次成功读到的配置: %s", exc)
                return
            if escalated or now - since < _WORKSHOP_CONFIG_FALLBACK_GRACE_S:
                # This endpoint is called repeatedly (for example, get_workshop_path). Under sustained failure, logging
                # one warning every time would flood logs and bury the ERROR below that actually needs attention.
                logger.debug("workshop 配置仍读不出来，继续沿用 last-good: %s", exc)
                return
            self._workshop_config_fallback_escalated = True
        logger.error(
            "workshop 配置已连续 %.0f 秒读不出来，仍在沿用上一次成功读到的配置。"
            "这已经不像 os.replace 的瞬时窗口，请检查 workshop_config.json 的权限"
            "（ACL / 受控文件夹访问）或它是否被换成了目录: %s",
            _WORKSHOP_CONFIG_FALLBACK_GRACE_S, exc,
        )

    def _remember_good_workshop_config(self, config, generation) -> None:
        """Cache a successful read, unless a save landed while it was in flight.

        The read happens outside the lock, so it can start before a save and
        return after it. Writing that snapshot into the cache would make a
        later transient-read fallback hand back the configuration from *before*
        the change — harder to notice than falling back to defaults.
        """
        if not isinstance(config, dict):
            return
        snapshot = dict(config)
        # A successful read resets the "continuous fallback duration" — even if the snapshot is rejected below due to a generation mismatch,
        # the file itself was readable, so this is not a persistent failure.
        self._clear_workshop_config_fallback_streak()
        # Comparison and assignment must be atomic: if interrupted between them, a save could advance the generation and write a new cache,
        # then this stale read would overwrite it. This lock covers only two in-memory
        # operations and no I/O, so it does not have the kind of "lock held across fsync" problem.
        with self._last_good_workshop_config_lock:
            if getattr(self, "_workshop_config_generation", 0) != generation:
                return
            self._last_good_workshop_config = snapshot

    def workshop_config_lock(self):
        """The lock that serializes every read-modify-write of workshop_config.json.

        Exposed so a caller that needs load → merge → save → side effects to be
        one transaction can hold it across the whole sequence. Reentrant, so
        the nested ``load_workshop_config`` inside such a transaction is fine.
        """
        return self._workshop_config_lock

    def repair_workshop_configs(self):
        """Explicitly repair the workshop config file; runs only when the caller explicitly allows writing to disk."""
        with self._workshop_config_lock:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="repair", target="workshop_config.json")
            self._cleanup_invalid_workshop_configs()

    def _rebase_workshop_config_after_storage_migration(self, config):
        if not isinstance(config, dict):
            return config

        try:
            root_state = self.load_root_state()
        except Exception:
            root_state = {}

        candidate_source_roots = []
        if isinstance(root_state, dict):
            for key in ("last_migration_backup", "last_migration_source"):
                raw_root = str(root_state.get(key) or "").strip()
                if raw_root:
                    candidate_source_roots.append(raw_root)

        if not candidate_source_roots:
            return config

        try:
            from utils.storage_path_rewrite import rebase_runtime_bound_workshop_config_paths
        except Exception:
            return config

        rebased_config = config
        for source_root in candidate_source_roots:
            next_config = rebase_runtime_bound_workshop_config_paths(
                rebased_config,
                source_root=source_root,
                target_root=self.app_docs_dir,
            )
            rebased_config = next_config

        if rebased_config is config:
            return config

        # ⚠️ On the event loop, do **not write**; return the corrected result directly to the caller.
        # get_workshop_path() uses this read path, and async voice_refs / publish
        # handlers call it directly on the loop — waiting for a lock a worker may hold across fsync would
        # hang the entire loop. Self-healing only corrects the path on disk; writing later loses nothing:
        # the next read running on a worker thread (GET /config via to_thread, POST transaction, startup
        # persistence) will commit it. This caller already receives the correct path.
        if running_on_event_loop():
            logger.debug("在事件循环上，跳过 workshop 配置路径自愈的落盘，留给下一次线程内读取")
            return rebased_config

        # Only this path needs a lock. **Reread inside the lock** before deciding: the caller's snapshot was read outside the lock, so writing it directly
        # could overwrite a full config just committed by a concurrent transaction (the race between POST /config and
        # GET /config). If the reread needs no change, do nothing.
        try:
            with self._workshop_config_lock:
                fresh = self._read_workshop_config_file()
                if fresh is None:
                    return rebased_config
                rebased_fresh = fresh
                for source_root in candidate_source_roots:
                    rebased_fresh = rebase_runtime_bound_workshop_config_paths(
                        rebased_fresh,
                        source_root=source_root,
                        target_root=self.app_docs_dir,
                    )
                if rebased_fresh is fresh:
                    return fresh
                self.save_workshop_config(rebased_fresh)
                return rebased_fresh
        except Exception as exc:
            logger.warning("保存迁移后的 workshop 配置路径自愈结果失败: %s", exc)
        return rebased_config
    
    def load_workshop_config(self):
        """
        Load workshop config
        
        Returns:
            dict: workshop config data
        """
        config_path = self.get_workshop_config_path()
        # Record the generation before reading. This read happens outside the lock; it may start before a save and return
        # afterward — writing it into last-known-good would overwrite the newer config with a stale snapshot.
        generation = getattr(self, "_workshop_config_generation", 0)
        try:
            if os.path.exists(config_path):
                # ⚠️ This read path must **not acquire _workshop_config_lock**. get_workshop_path()
                # uses it, and async handlers such as voice_refs / publish call get_workshop_path() directly on the event loop.
                # Waiting for a lock a worker may hold across fsync (or even network-drive makedirs) would hang the entire loop — this same lock-
                # propagation trap has already occurred twice in this PR. Serialization of the self-healing write is handled
                # by _rebase_workshop_config_after_storage_migration inside its own lock.
                #
                # ⚠️ Reads must tolerate os.replace: persistence now runs on a worker, and on Windows concurrent
                # open() may hit PermissionError during replace. The except below would treat that as "config unreadable"
                # and return defaults — then upload / publish would use the default workshop root even though the user's config is fine.
                # If config does not exist, return the default directly to avoid implicit file writes during read-only queries.
                config = read_json_tolerating_replace(config_path)
                config = self._rebase_workshop_config_after_storage_migration(config)
                logger.debug(f"成功加载workshop配置: {config}")
                self._remember_good_workshop_config(config, generation)
                return config
            else:
                #
                #
                # ⚠️ Do not acquire this lock on the event loop. During the first POST /config, the file is being created;
                # the target remains absent while the worker holds the lock and writes, so get_workshop_path() on the event loop
                # would hang here. This branch only needs to "return the default if unreadable," so no mutual exclusion is needed.
                if running_on_event_loop():
                    if os.path.exists(config_path):
                        config = read_json_tolerating_replace(config_path)
                        config = self._rebase_workshop_config_after_storage_migration(config)
                        return config
                    return {
                        "default_workshop_folder": str(self.workshop_dir),
                        "auto_create_folder": True
                    }
                with self._workshop_config_lock:
                    if os.path.exists(config_path):
                        config = read_json_tolerating_replace(config_path)
                        config = self._rebase_workshop_config_after_storage_migration(config)
                        logger.debug(f"成功加载workshop配置: {config}")
                        return config

                    default_config = {
                        "default_workshop_folder": str(self.workshop_dir),
                        "auto_create_folder": True
                    }
                    logger.debug(f"workshop配置不存在，返回默认配置: {default_config}")
                    return default_config
        except Exception as e:
            # ⚠️ First check whether there is a "last successfully read" value. Persistence now runs on a worker, and a read on the Windows
            # event loop may land during os.replace; the loop must not retry with backoff
            # (see read_json_tolerating_replace), so this can surface as a transient
            # PermissionError. Returning defaults directly would make upload / publish use the default
            # workshop root even though the user's config is fine — silently changing roots is worse than an error.
            # ⚠️ Fall back only for "transient busy" errors. If an established cache masked **all** read failures,
            # genuine faults such as malformed JSON would never surface, and upload / publish would keep using the old root.
            # Use the same criterion as writes: the OS winerror, not a guess based on the message.
            # ⚠️ But winerror 5 (ACCESS_DENIED) is itself **ambiguous**: both the transient os.replace
            # window and permanently revoked permissions use the same code, so the code alone cannot distinguish them. Add another gate based on
            # **duration** — see _note_workshop_config_fallback.
            busy_code = getattr(e, "winerror", None) in _REPLACE_BUSY_WINERRORS
            last_good = getattr(self, "_last_good_workshop_config", None)
            if busy_code and last_good is not None:
                # Delegate log levels to it: warning on first occurrence, debug during the grace period, one ERROR after
                # the grace period, then debug again. This endpoint is called repeatedly; a warning each time would bury
                # the message that actually needs attention.
                self._note_workshop_config_fallback(e)
                return dict(last_good)
            error_msg = f"加载workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            # Use default configuration
            return {
                "default_workshop_folder": str(self.workshop_dir),
                "auto_create_folder": True
            }
    
    def save_workshop_config(self, config_data):
        """
        Save workshop config
        
        Args:
            config_data: config data to save
        """
        config_path = str(self.get_runtime_config_path('workshop_config.json'))
        try:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target="workshop_config.json")

            # Ensure the configuration directory exists
            self.ensure_config_directory()
            
            # Save configuration
            atomic_write_json(config_path, config_data, indent=4, ensure_ascii=False)

            # Synchronize last-known-good after a successful write. If it were updated only after reads, POST /config
            # would persist new config while the cache retained the old value it read on entry — a later transient read failure would
            # fall back to the config **before the change**, which is harder to diagnose than falling back to defaults.
            if isinstance(config_data, dict):
                snapshot = dict(config_data)
                with self._last_good_workshop_config_lock:
                    self._workshop_config_generation = (
                        getattr(self, "_workshop_config_generation", 0) + 1
                    )
                    self._last_good_workshop_config = snapshot

            logger.info(f"成功保存workshop配置: {config_data}")
        except Exception as e:
            error_msg = f"保存workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            raise
    
    def save_workshop_path(self, workshop_path):
        """
        Set the Steam Workshop root directory path (runtime variable, not written to the config file)
        
        Args:
            workshop_path: Steam Workshop root directory path
        """
        self._steam_workshop_path = workshop_path
        logger.info(f"已设置Steam创意工坊路径（运行时）: {workshop_path}")

    def persist_user_workshop_folder(self, workshop_path):
        """
        Persist the actual Steam Workshop path into the config file (written only once per startup).

        Called only when the Steam Workshop location was obtained dynamically; later reads can serve as a fallback when Steam is not running.
        """
        if self._user_workshop_folder_persisted:
            return
        if not workshop_path or not os.path.isdir(workshop_path):
            return
        try:
            # Hold the lock for the full read-modify-write: otherwise startup persistence could "read before the user saves config, write after"
            # and overwrite the directory settings the user just submitted with its stale snapshot. The lock is an RLock,
            # so calling load_workshop_config inside it does not deadlock.
            with self._workshop_config_lock:
                config = self.load_workshop_config()
                config["user_workshop_folder"] = workshop_path
                self.save_workshop_config(config)
            self._user_workshop_folder_persisted = True
            logger.info(f"已持久化Steam创意工坊路径到配置文件: {workshop_path}")
        except Exception as e:
            logger.error(f"持久化user_workshop_folder失败: {e}")

    def get_steam_workshop_path(self):
        """
        Get the Steam Workshop root directory path (runtime only, set by the startup flow)
        
        Returns:
            str | None: Steam Workshop root directory path
        """
        return self._steam_workshop_path
    
    def get_workshop_path(self):
        """
        Get the workshop root directory path
        
        Priority: user_mod_folder (config) > Steam runtime path > user_workshop_folder (cache file) > default_workshop_folder (config) > self.workshop_dir
        
        Returns:
            str: workshop root directory path
        """
        config = self.load_workshop_config()
        if config.get("user_mod_folder"):
            return config["user_mod_folder"]
        if self._steam_workshop_path:
            return self._steam_workshop_path
        cached = config.get("user_workshop_folder")
        if cached and os.path.isdir(cached):
            return cached
        return config.get("default_workshop_folder", str(self.workshop_dir))
