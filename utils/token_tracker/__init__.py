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
"""Compatibility facade for the token-tracker package.

The historical module API is preserved while mutable state remains owned by
the submodule whose functions rebind or consume it. ``TokenTracker`` is
assembled here from domain mixins so its public identity remains unchanged.
"""
# ruff: noqa: F401

import atexit
import asyncio
import copy
import functools
import gzip
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

from ._shared import _deep_copy_day, _file_lock, _merge_day_stats, logger
from .call_context import _current_call_type, llm_call_context, set_call_type
from .telemetry import (
    _CONTROL_PROACTIVE_INTERVAL,
    _DEVICE_HW_CACHE,
    _MACHINE_ID_PLACEHOLDERS,
    _RETIRED_INTERVAL_EXPERIMENT_VALUE,
    _RETIRED_INTERVAL_ROLLBACK_BRANCH,
    _TELEMETRY_BRANCHES,
    _TELEMETRY_BRANCH_FILE,
    _bucket_proactive_interval,
    _get_anonymous_device_id,
    _get_app_version_from_changelog,
    _get_device_hw,
    _get_legacy_device_id,
    _get_telemetry_branch,
    _get_telemetry_locale,
    _get_telemetry_metadata,
    _get_telemetry_timezone,
    _is_release_build,
    _is_valid_machine_id,
    _read_os_machine_id,
    _rollback_retired_proactive_interval,
    _telemetry_branch_cache,
    get_telemetry_branch,
)
from .storage import StorageMixin as _StorageMixin
from .recording import RecordingMixin as _RecordingMixin
from .reporting import (
    ReportingMixin as _ReportingMixin,
    _DO_NOT_TRACK,
    _TELEMETRY_GZIP_THRESHOLD,
    _TELEMETRY_HMAC_SECRET,
    _TELEMETRY_REPORT_INTERVAL,
    _TELEMETRY_SERVER_URL,
    _TELEMETRY_TIMEOUT,
    _compute_telemetry_signature,
    record_settings_state,
)


class TokenTracker(_StorageMixin, _RecordingMixin, _ReportingMixin):
    """Thread-safe + multi-process-safe global LLM token usage tracker.

    Design:
    - all processes share a single token_usage.json file
    - memory only tracks the "not yet persisted increments" (delta)
    - save() does read-merge-write under a file lock, so multiple processes lose no data
    - get_stats() reads disk + merges the in-memory delta, never deleting any file
    """

    _instance: Optional['TokenTracker'] = None
    _init_lock = threading.Lock()
    @classmethod
    def get_instance(cls) -> 'TokenTracker':
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._config_manager = get_config_manager()

        # 尚未落盘的增量数据（save 成功后清空）
        self._delta_daily: dict = {}
        self._delta_records: deque = deque(maxlen=200)

        # 持久化控制
        self._save_interval = 60  # 秒
        self._dirty = False
        self._save_task: Optional[asyncio.Task] = None

        # 远程遥测上报
        self._device_id: str = ""  # 延迟生成
        self._branch: str = ""  # 延迟生成（首次上报时读盘/抽签）
        self._last_report_time: float = 0.0
        self._report_interval = _TELEMETRY_REPORT_INTERVAL
        self._unsent_daily: dict = {}  # 尚未成功上报到服务器的增量
        self._unsent_records: list = []
        # batch_seq：当前正在上报或重传中的窗口标识。新窗口首次进入 _report_to_server
        # 时分配一次（secrets.token_hex），失败重传时保留同一个值，让 server
        # seen_batches 能 dedupe "网络 timeout 但 server 已经 commit" 的重传。
        # 成功 200 后清空，下次窗口再分配新 seq。跟 _unsent_daily 一起持久化。
        self._pending_batch_seq: Optional[str] = None
        self._has_recorded_app_start: bool = False  # 🔒 app_start 单次上报锁
        self._session_start_ts: float = 0.0  # session_end 计算 duration 用
        self._session_process: str = "unknown"
        # 本 session 用户消息轮数。note_user_message 累加，record_app_start 重置，
        # _atexit_save(session_end) emit 成 session_turn_count histogram —— 含 0
        # 即"零消息会话"（开了 app 一句没聊就走），D1 流失最直接信号。
        self._session_msg_count: int = 0
        self._first_user_message_recorded: bool = False  # 🔒 首条用户消息单次锁
        self._core_loop_recorded: bool = False  # 🔒 首次完成核心 loop 单次锁

        # 首次启动：迁移旧版 per-instance 文件
        self._migrate_legacy_files()

        # 恢复上次未成功上报的远程数据
        self._load_unsent_queue()

        # atexit 兜底：不管进程如何退出（SIGTERM / 异常 / 正常结束），都尝试保存
        # 注意：SIGKILL (kill -9) 无法被拦截，此时最多丢 60s 数据
        atexit.register(self._atexit_save)



from .hooks import (
    _AsyncStreamWrapper,
    _CACHED_TOKEN_FIELDS,
    _NESTED_DETAIL_FIELDS,
    _SyncStreamWrapper,
    _add_to_blocklist,
    _blocklist_lock,
    _extract_cached_tokens,
    _get_base_url,
    _handle_async_stream,
    _handle_sync_stream,
    _hooks_install_lock,
    _install_crash_excepthook,
    _record_usage_from_response,
    _should_inject_stream_options,
    _stream_options_blocklist,
    _usage_to_dict,
    calculate_cache_hit_rate,
    install_hooks,
    record_anthropic_usage,
)
def _install_state_proxy() -> None:
    """Install live forwarding for historical mutable module state."""
    import sys
    from types import ModuleType

    from . import call_context, hooks, reporting, telemetry

    owners = {
        "_current_call_type": call_context,
        "_DEVICE_HW_CACHE": telemetry,
        "_telemetry_branch_cache": telemetry,
        "_stream_options_blocklist": hooks,
        "_blocklist_lock": hooks,
        "_hooks_install_lock": hooks,
        "_TELEMETRY_SERVER_URL": reporting,
        "_TELEMETRY_HMAC_SECRET": reporting,
        "_DO_NOT_TRACK": reporting,
        "_TELEMETRY_REPORT_INTERVAL": reporting,
        "_TELEMETRY_TIMEOUT": reporting,
        "_TELEMETRY_GZIP_THRESHOLD": reporting,
    }

    class TokenTrackerFacade(ModuleType):
        """Forward compatibility-state access to its canonical owner module."""

        def __getattribute__(self, name: str):
            owner = owners.get(name)
            if owner is not None:
                return getattr(owner, name)
            return super().__getattribute__(name)

        def __setattr__(self, name: str, value) -> None:
            owner = owners.get(name)
            if owner is not None:
                setattr(owner, name, value)
                return
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            owner = owners.get(name)
            if owner is not None:
                delattr(owner, name)
                return
            super().__delattr__(name)

    sys.modules[__name__].__class__ = TokenTrackerFacade


_install_state_proxy()
del _install_state_proxy, _StorageMixin, _RecordingMixin, _ReportingMixin
