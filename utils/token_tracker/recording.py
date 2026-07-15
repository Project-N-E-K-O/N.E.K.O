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
"""In-memory usage and application-lifecycle recording methods."""

import time
from datetime import date, timedelta

from ._shared import _deep_copy_day, _merge_day_stats
from .reporting import record_settings_state

class RecordingMixin:
    """In-memory usage and application-lifecycle recording methods."""

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cached_tokens: int = 0,
        call_type: str = "unknown",
        source: str = "",
        success: bool = True,
        prompt_chars: int = 0,
    ):
        """Record token usage of one LLM call. Thread-safe.

        Data first goes into the in-memory delta, persisted by the periodic save.

        Args:
            prompt_tokens: total prompt tokens (including the cached part)
            completion_tokens: generated tokens
            total_tokens: prompt + completion
            cached_tokens: the cache-hit part of the prompt (OpenAI prompt_tokens_details.cached_tokens)
            prompt_chars: input characters for char-billed SKUs. Use this for TTS / ASR /
                embedding-by-char endpoints whose pricing unit is characters,
                not tokens — keeps the token aggregates clean.
        """
        model = model or "unknown"
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        total_tokens = total_tokens or 0
        cached_tokens = cached_tokens or 0
        prompt_chars = prompt_chars or 0

        today = date.today().isoformat()

        rec = {
            "ts": time.time(),
            "model": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "tt": total_tokens,
            "cch": cached_tokens,
            "pch": prompt_chars,
            "type": call_type,
            "src": source,
            "ok": success,
        }

        with self._lock:
            if today not in self._delta_daily:
                self._delta_daily[today] = self._empty_day()

            day = self._delta_daily[today]
            day["total_prompt_tokens"] += prompt_tokens
            day["total_completion_tokens"] += completion_tokens
            day["total_tokens"] += total_tokens
            day["cached_tokens"] += cached_tokens
            day["total_prompt_chars"] += prompt_chars
            day["call_count"] += 1
            if not success:
                day["error_count"] += 1

            # by_model
            bm = day["by_model"]
            if model not in bm:
                bm[model] = self._empty_bucket()
            b = bm[model]
            b["prompt_tokens"] += prompt_tokens
            b["completion_tokens"] += completion_tokens
            b["total_tokens"] += total_tokens
            b["cached_tokens"] += cached_tokens
            b["prompt_chars"] += prompt_chars
            b["call_count"] += 1

            # by_call_type
            bt = day["by_call_type"]
            if call_type not in bt:
                bt[call_type] = self._empty_bucket()
            c = bt[call_type]
            c["prompt_tokens"] += prompt_tokens
            c["completion_tokens"] += completion_tokens
            c["total_tokens"] += total_tokens
            c["cached_tokens"] += cached_tokens
            c["prompt_chars"] += prompt_chars
            c["call_count"] += 1

            self._delta_records.append(rec)
            self._dirty = True

    def get_stats(self, days: int = 7) -> dict:
        """Return usage statistics for the last N days.

        Reads the disk file + merges the in-memory not-yet-persisted delta; modifies nothing.
        """
        # 读磁盘（atomic_write_json 保证文件一致性，无需文件锁）
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        merged_daily = disk_data.get("daily_stats", {})
        all_records = disk_data.get("recent_records", [])

        # 合并内存中未落盘的 delta
        with self._lock:
            for day_key, day_delta in self._delta_daily.items():
                if day_key not in merged_daily:
                    merged_daily[day_key] = _deep_copy_day(day_delta)
                else:
                    _merge_day_stats(merged_daily[day_key], day_delta)
            all_records = all_records + list(self._delta_records)

        # 按 days 过滤
        today = date.today()
        daily = {}
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            if d in merged_daily:
                daily[d] = merged_daily[d]

        # 去重 recent_records
        unique_records = self._dedupe_records(all_records)

        return {
            "daily_stats": daily,
            "recent_records": unique_records[-20:],
        }

    def get_today_stats(self) -> dict:
        """Return today's usage statistics."""
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        today = date.today().isoformat()
        merged = disk_data.get("daily_stats", {}).get(today, self._empty_day())

        # 合并内存 delta
        with self._lock:
            if today in self._delta_daily:
                _merge_day_stats(merged, self._delta_daily[today])

        return {"date": today, "stats": merged}

    def record_app_start(self, process: str = "main_server"):
        """Record the client startup event (app_start).

        Used for DAU statistics, counted separately from LLM calls.
        Guaranteed to report only once per process lifetime (thread-safe).

        Besides the old ``record(call_type='app_start')`` path (the dashboard's
        by_call_type still uses it), also emits an instrument event ``session_start``
        and stashes the start time on self so _atexit_save can compute session_end's
        duration.
        """
        with self._lock:
            if self._has_recorded_app_start:
                return
            self._has_recorded_app_start = True
            self._session_start_ts = time.time()
            self._session_process = process
            self._session_msg_count = 0  # 新 session 起点，轮数清零

        # 新埋点：sparse event 走本地 events.jsonl（诊断），同时打 counter
        # 走远程聚合通道（dashboard 看 DAU / session 总数）。event 因为带
        # context 字段、暂未集成进远程上报；counter 是聚合数字、走 60s 通道。
        try:
            from utils.instrument import event as _instr_event, counter as _instr_counter
            _instr_event("session_start", process=process)
            _instr_counter("session_start", process=process)
        except Exception:
            # 埋点失败不能挡 app 启动 —— 老 record() 路径下面已经跑过，
            # DAU 仍能从 by_call_type='app_start' 统计出来，不会丢用户。
            pass

        self.record(
            model="app_start",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            call_type="app_start",
            source="",
            success=True,
        )

        # 启动快照：当前主动搭话 / 隐私设置。只在 main_server 打，避免多进程
        # （agent/memory_server 也跑 record_app_start）把同一份设置重复计 3 次。
        # settings 是 user-facing 概念，跟 main 进程绑定最自然。
        if process == "main_server":
            record_settings_state()

    def note_first_user_message(self, input_type: str = "text"):
        """Record the user's first message within this process (a key D1-funnel milestone).

        Recorded once per process lifetime (thread-safe). Distinguishes two kinds of D1 churn:
        - first_message_sent counter: the user actually "spoke" — absence = installed,
          opened, left without speaking (onboarding / configuration obstacles)
        - time_to_first_message_sec histogram: time from app start to first message.
          Stuck on config / character choice / mic permission = long; seconds = smooth onboarding

        input_type: text / voice (low-cardinality)
        """
        with self._lock:
            if self._first_user_message_recorded:
                return
            self._first_user_message_recorded = True
            anchor = self._session_start_ts

        try:
            from utils.instrument import counter as _c, histogram as _h
            _c("first_message_sent", input_type=input_type)
            if anchor > 0:
                _h("time_to_first_message_sec", max(0.0, time.time() - anchor))
        except Exception:
            # 埋点失败不能挡用户消息处理
            pass

    def note_user_message(self, input_type: str = "text"):
        """Called for every user message (unlike note_first_user_message, which records only the first).

        - emits the ``user_message_sent`` counter (input_type dim): sum = total chat
          turns; sliced by input_type = voice/text modality share
        - accumulates this session's turn count ``_session_msg_count``; at session_end
          emits the ``session_turn_count`` histogram (including 0 = zero-message session)

        Callers must ensure each real user message calls this exactly once (see core.py:
        only at the text-side on_user_message entry and the true voice-message point,
        avoiding the openclaw handoff reuse path).
        input_type: text / voice (low-cardinality)
        """
        with self._lock:
            self._session_msg_count += 1
        try:
            from utils.instrument import counter as _c
            _c("user_message_sent", input_type=input_type)
        except Exception:
            # 埋点失败不能挡用户消息处理
            pass

    def note_core_loop_completed(self):
        """The user completed one core-experience loop: send message → get reply → hear speech. Recorded once per process.

        Counted only after the user has sent a message (_first_user_message_recorded=True) —
        purely proactive-triggered speech doesn't count as "the user actively experienced
        the core loop".

        The key distinguishing signal for D1 churn analysis:
        - has first_message_sent but no core_loop_completed = the user spoke but never
          heard a reply (stuck on LLM failure / TTS failure / too slow) →
          first-experience-obstacle churn
        - has core_loop_completed = fully experienced the product core; later churn is
          more likely "tried it, didn't like it" (a product-value problem) — entirely
          different operational responses for the two kinds
        """
        with self._lock:
            if self._core_loop_recorded:
                return
            if not self._first_user_message_recorded:
                return  # 用户还没开口，不算用户发起的核心 loop
            self._core_loop_recorded = True

        try:
            from utils.instrument import counter as _c
            _c("core_loop_completed")
        except Exception:
            # 埋点 best-effort；前面已置位 _core_loop_recorded，丢一次计数
            # 不影响幂等，也不该影响调用方（音频投递路径）。
            pass

    def has_completed_core_loop(self) -> bool:
        """Whether the user has completed a core-experience loop in this process (send → reply → speech).

        For error instrumentation sites to set the ``before_first_loop`` dimension:
        False = the error happened before the user experienced the product core =
        first-experience-obstacle churn (most worth saving); True = an error after
        experiencing the core, where churn is more likely a product-value problem.
        Different operational responses.
        """
        return self._core_loop_recorded
