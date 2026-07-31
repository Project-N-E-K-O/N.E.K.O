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

from utils.file_utils import (
    _REPLACE_BUSY_WINERRORS,
    atomic_write_json,
    read_json_tolerating_replace,
    running_on_event_loop,
)

from ._shared import logger

# 守护上面那把「last-good 缓存微锁」的懒创建，见 _last_good_workshop_config_lock。
_LAST_GOOD_LOCK_GUARD = threading.Lock()

# Workshop配置相关常量 - 将在ConfigManager实例化时使用self.workshop_dir


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
            # 与 workshop_utils 保持一致：相对路径按用户目录解析
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
        # 懒创建本身也要串行：两个线程同时进来会各造一把锁、各自守着不同的东西，
        # 那这把锁就等于不存在。用模块级 guard 做双检。
        with _LAST_GOOD_LOCK_GUARD:
            lock = getattr(self, "_last_good_lock_obj", None)
            if lock is None:
                lock = threading.Lock()
                self._last_good_lock_obj = lock
        return lock

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
        # 比较和赋值必须是一个原子步：中间被抢占的话，一次 save 可以在这两步之间
        # 把代数推上去并写好新缓存，然后这条旧读再把它盖回去。这把锁只圈住两行内存
        # 操作、不含任何 I/O，所以拿它不会有「持锁跨 fsync」那类问题。
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

        # ⚠️ 在事件循环上就**不写**，直接把改好的结果返回给调用方。
        # get_workshop_path() 走的就是这条读路径，而 voice_refs / publish 的 async
        # handler 在循环上裸调它 —— 去抢一把 worker 可能正持着（跨 fsync）的锁，就是
        # 把整条循环挂在那儿。自愈只是把盘上的路径修正过来，晚一点写没有任何损失：
        # 下一次跑在 worker 线程上的读（GET /config 走 to_thread、POST 事务、启动期
        # 的 persist）就会落地。这一趟的调用方拿到的路径已经是对的。
        if running_on_event_loop():
            logger.debug("在事件循环上，跳过 workshop 配置路径自愈的落盘，留给下一次线程内读取")
            return rebased_config

        # 只有这里需要锁。锁内**重读一次**再决定：调用方那份快照是在锁外读的，直接
        # 写回去就可能把并发事务刚提交的配置整份盖掉（正是 POST /config 与
        # GET /config 之间那个竞态）。重读之后没得可改就什么都不做。
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
        # 读之前先记下代数。这次读是在锁外做的，可能在一次 save 之前就开始了却在它
        # 之后才回来 —— 那样把结果写进 last-known-good 就是拿旧快照盖掉新配置。
        generation = getattr(self, "_workshop_config_generation", 0)
        try:
            if os.path.exists(config_path):
                # ⚠️ 这条读路径**不许拿 _workshop_config_lock**。get_workshop_path()
                # 走的就是这里，而 voice_refs / publish 等 async handler 在事件循环上
                # 裸调 get_workshop_path()。让它去抢一把 worker 可能正持着（跨 fsync、
                # 甚至跨网络盘 makedirs）的锁，就等于把整条循环挂在那儿 —— 同一个锁
                # 传导陷阱在这个 PR 里已经踩过两次。自愈写的串行化由
                # _rebase_workshop_config_after_storage_migration 自己在锁内完成。
                # ⚠️ 容忍 os.replace 的读：落盘现在跑在 worker 上，Windows 上并发
                # open() 会撞到 replace 中间吃 PermissionError。下面那个 except 会把
                # 它当成「配置读不出来」而退回默认配置 —— 于是 upload / publish 拿着
                # 默认的工坊根目录去干活，而用户的配置明明好好的。
                config = read_json_tolerating_replace(config_path)
                config = self._rebase_workshop_config_after_storage_migration(config)
                logger.debug(f"成功加载workshop配置: {config}")
                self._remember_good_workshop_config(config, generation)
                return config
            else:
                # 配置不存在时直接返回默认值，避免只读查询链路隐式写入配置文件。
                #
                # ⚠️ 事件循环上不拿这把锁。首次 POST /config 正在创建这个文件时，
                # 目标一直不存在、而 worker 持着锁在写 —— 循环上的 get_workshop_path()
                # 就会卡在这儿。这条分支本来就只是「读不到就给默认值」，不需要互斥。
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
            # ⚠️ 先看有没有「上一次成功读到的」。落盘现在跑在 worker 上，Windows 上
            # 事件循环里的一次读可能正撞在 os.replace 中间；而循环上不许退避重试
            # （见 read_json_tolerating_replace），于是这里会收到一个瞬时的
            # PermissionError。直接退回默认配置的话，upload / publish 就拿着默认的
            # 工坊根目录去干活，而用户的配置明明好好的 —— 静默换根目录比报错糟得多。
            # ⚠️ 只对「瞬时 busy」回落。缓存一旦建立就把**所有**读失败都盖掉的话，
            # JSON 被改坏、权限被收走这类真故障就永远不会暴露，upload / publish 会
            # 一直对着旧根目录干活。判据同写入侧：OS 给的 winerror，不是消息猜测。
            transient = getattr(e, "winerror", None) in _REPLACE_BUSY_WINERRORS
            last_good = getattr(self, "_last_good_workshop_config", None)
            if transient and last_good is not None:
                logger.warning("加载workshop配置失败，沿用上一次成功读到的配置: %s", e)
                return dict(last_good)
            error_msg = f"加载workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            # 使用默认配置
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

            # 确保配置目录存在
            self.ensure_config_directory()
            
            # 保存配置
            atomic_write_json(config_path, config_data, indent=4, ensure_ascii=False)

            # 写成功之后同步 last-known-good。只在读成功时更新的话，POST /config
            # 存下新配置后缓存里还是它进来时读到的旧值 —— 之后一次瞬时读失败就会
            # 回落到**改动之前**的配置，比回落到默认值更难查。
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
            # 读—改—写整段持锁：不然启动期这次持久化可以「用户保存配置之前读、之后
            # 写」，用自己那份陈旧快照把用户刚提交的目录设置整份盖掉。锁是 RLock，
            # 所以里面的 load_workshop_config 再取一次不会自死锁。
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
