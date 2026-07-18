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
"""Remote telemetry reporting state and TokenTracker reporting methods."""

import asyncio
import copy
import gzip
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from utils.file_utils import atomic_write_json

from ._shared import _file_lock, _merge_day_stats, logger
from .telemetry import (
    _bucket_proactive_interval, _get_anonymous_device_id, _get_app_version_from_changelog,
    _get_device_hw, _get_legacy_device_id, _get_telemetry_branch,
    _get_telemetry_locale, _get_telemetry_metadata, _get_telemetry_timezone,
)

_TELEMETRY_SERVER_URL = "http://118.31.122.91:8099"

if _TELEMETRY_SERVER_URL and not _TELEMETRY_SERVER_URL.startswith(("http://", "https://")):
    logger.warning("Token tracker: invalid telemetry URL scheme, disabling remote reporting")
    _TELEMETRY_SERVER_URL = ""

_TELEMETRY_HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"  # noqa: S105

_DO_NOT_TRACK = any(
    os.getenv(v, "").strip() in ("1", "true", "yes")
    for v in ("NEKO_DO_NOT_TRACK", "DO_NOT_TRACK")
)

_TELEMETRY_REPORT_INTERVAL = 60

_TELEMETRY_TIMEOUT = 10  # 秒

_TELEMETRY_GZIP_THRESHOLD = 1024

def _compute_telemetry_signature(payload_json: str, timestamp: float) -> str:
    """Compute the HMAC-SHA256 signature for telemetry reporting."""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _TELEMETRY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def record_settings_state() -> None:
    """Read the current proactive-chat / privacy-mode settings and emit one settings_state counter.

    Trigger timing: **only** at app start (record_app_start, main_server process only).

    Semantics (finalized after CodeRabbit feedback): the server-side instrument_counters
    UPSERTs cumulatively by (stat_date, device_id, metric_key). This function only emits
    at startup, so one record = "the user's settings combo at this launch". N launches
    per device per day add +N — an **observation count**, not a gauge-style "current
    final state" — but good enough for analyzing "which tiers heavy users habitually
    use": per device, the combo with the highest count is its habitual tier.

    Deliberately **not** instrumented in save_global_conversation_settings: that would
    +1 a new combo every time a user flips a setting within a day, polluting the
    distribution into a "switching trajectory". Precise per-device-per-day final state
    would need server-side gauge/overwrite semantics, which the current instrument
    pipeline doesn't support and this analysis doesn't require.

    Use: the server slices out heavy users by device active days / event_count, then
    looks at the distribution of their settings_state dim combos — i.e. "where do heavy
    users set proactive chat / privacy mode".

    Dims are all low-cardinality enums; interval is bucketed, raw seconds not sent:
    - proactive: on / off (proactiveChatEnabled)
    - interval: <10s / 10-30s / ... / >=300s ("off" when off)
    - vision_chat: on / off (proactiveVisionChatEnabled)
    - privacy: on / off (privacy mode = inverse of proactiveVisionEnabled, default off)
    """
    if _DO_NOT_TRACK:
        return
    try:
        from utils.preferences import load_global_conversation_settings
        from utils.instrument import counter as _c
        s = load_global_conversation_settings()
        proactive_on = bool(s.get("proactiveChatEnabled", False))
        _c(
            "settings_state", 1,
            proactive="on" if proactive_on else "off",
            interval=(_bucket_proactive_interval(s.get("proactiveChatInterval", 0))
                      if proactive_on else "off"),
            vision_chat="on" if s.get("proactiveVisionChatEnabled", False) else "off",
            # 隐私模式 = proactiveVisionEnabled 的反面（default True → 默认隐私关）
            privacy="on" if not s.get("proactiveVisionEnabled", True) else "off",
        )
    except Exception:
        # 埋点失败不影响业务，静默
        pass


class ReportingMixin:
    """Remote reporting and periodic persistence methods."""

    @property
    def _unsent_queue_path(self) -> Path:
        """Persistence file for the unsent remote-reporting queue.

        _unsent_daily is lost when the process is killed (pure memory).
        Writing the queue to this file lets a restart recover and resend it.
        """
        return self._config_manager.config_dir / ".telemetry_unsent.json"

    def _atexit_save(self):
        """atexit safety net: a final best effort to save before process exit.

        Covers: SIGTERM / uncaught exceptions / normal exit / sys.exit()
        Not covered: SIGKILL (kill -9) / power loss — at most 60s of data lost then

        Ordering matters: first emit session_end into the instrument buffer, then
        save(). save() → _report_to_server snapshots through instrument, so the emit
        must happen first; otherwise session_end's counter/histogram enters the buffer
        but never gets snapshotted, and the remote dashboard never sees session_end —
        the paired session_start is visible while session_end is forever missing, so
        the dashboard's "abnormal exit rate" gets miscomputed as 100%. The event goes
        separately through event_logger.flush into local jsonl.
        """
        # global 声明提到函数开头：下面 3b 步骤会读 _TELEMETRY_SERVER_URL，
        # Python 要求 global 声明先于任何使用（否则 SyntaxError）。
        global _TELEMETRY_SERVER_URL
        # ── 1) session_end 先落 instrument buffer，让随后的 save() 带上 ──
        try:
            from utils.instrument import (
                event as _instr_event,
                counter as _instr_counter,
                histogram as _instr_histogram,
            )
            duration = (time.time() - self._session_start_ts) if self._session_start_ts > 0 else 0.0
            _instr_event(
                "session_end",
                process=self._session_process,
                duration_sec=round(duration, 1),
            )
            _instr_counter("session_end", process=self._session_process)
            if duration > 0:
                # 直接传秒；instrument bounds 是数字通用，没绑定单位
                _instr_histogram("session_duration_sec", duration, process=self._session_process)
            # 本 session 用户消息轮数（无条件 emit，含 0）——0 即零消息会话。
            # 配合 session_duration_sec 看：短时长+0 轮 = 开了就走；长时长+0 轮 =
            # 挂着没互动。是 D1 浅尝 vs 上瘾的核心区分。
            _instr_histogram("session_turn_count", self._session_msg_count, process=self._session_process)
        except Exception:
            # instrument import / emit 失败不能让进程退出卡住 —— 实在丢一条
            # 也比 atexit 抛出强（atexit 异常会让 SIGTERM 退出码变化）。
            pass

        # ── 2) Bypass 60s throttle —— atexit 是最后机会，错过没下次 ──
        # _report_to_server 内部 ``now - self._last_report_time < interval``
        # 在短 session（启动后不到 60s 就退出）下会阻止上报，让刚 emit 的
        # session_end counter / histogram 永远留在 instrument buffer。这里
        # 显式归零让那条 if 一定不命中。会带来一个理论副作用：如果 atexit
        # 之前距上次成功上报 < 60s，这次再发一份；server seen_batches 靠
        # batch_seq dedupe，所以不会双倍计数。
        with self._lock:
            self._last_report_time = 0.0

        # ── 3) save() 把 daily_stats + 上面刚 emit 的 instrument snapshot 一起发 ──
        try:
            # save() first: persists delta to disk and attempts remote report
            # (best-effort final push). Then disable remote URL so no further
            # network calls happen during interpreter teardown.
            self.save()
        except Exception:
            # save 失败不抛进 atexit（同上）。失败时 unsent 已经被持久化，
            # 下次进程启动会重传。
            pass

        # ── 3b) 若第一次 save 是「重传」（进程带着早先失败遗留的 _pending_batch_seq），
        # _report_to_server 会按 is_retry 跳过 instrument snapshot，刚 emit 的
        # session_end / session_duration_sec 仍留在 buffer。常见"网络早先挂、
        # 退出前恢复"场景下重传会成功并清掉 batch_seq，但没有第二次发送，
        # session 指标就在关 URL 前静默丢了（Codex）。所以这里检查：instrument
        # 还有数据 + batch_seq 已清（说明重传成功、下次是 fresh 窗口会 snapshot）
        # → 再 bypass throttle 发一次。
        try:
            from utils.instrument import has_data as _instrument_has_data
            if (_instrument_has_data() and self._pending_batch_seq is None
                    and _TELEMETRY_SERVER_URL and not _DO_NOT_TRACK):
                with self._lock:
                    self._last_report_time = 0.0
                self.save()
        except Exception:
            # A final instrument flush is best-effort during interpreter shutdown.
            pass

        # ── 4) flush event_logger —— event 不走远程 instrument 通道，本地 jsonl 兜底 ──
        try:
            from utils.event_logger import EventLogger
            EventLogger.get_instance().flush()
        except Exception:
            # event_logger flush 失败丢的是本地 jsonl 的稀疏事件，下次启动
            # 没有恢复路径 —— 但 counter/histogram 已经走 instrument 通道
            # 发出去了，这里失败影响的只是诊断细节，不阻塞 atexit。
            pass
        finally:
            _TELEMETRY_SERVER_URL = ""

    def _load_unsent_queue(self):
        """Load remote data that previously failed to report, at startup."""
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            p = self._unsent_queue_path
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            loaded_daily = data.get("daily", {})
            loaded_records = data.get("records", [])
            loaded_batch_seq = data.get("batch_seq")
            if loaded_daily:
                with self._lock:
                    for day_key, day_val in loaded_daily.items():
                        if day_key not in self._unsent_daily:
                            self._unsent_daily[day_key] = day_val
                        else:
                            _merge_day_stats(self._unsent_daily[day_key], day_val)
                    self._unsent_records.extend(loaded_records)
                    if len(self._unsent_records) > 200:
                        self._unsent_records = self._unsent_records[-200:]
                    # 恢复 batch_seq：进程上次没发出去的窗口，重启后下次上报
                    # 仍用同一 seq，让 server seen_batches 能 dedupe 那次的
                    # 不确定成败（client 进程被 kill 时 server 可能已 commit）。
                    if isinstance(loaded_batch_seq, str) and loaded_batch_seq:
                        self._pending_batch_seq = loaded_batch_seq
                logger.debug(f"Token tracker: loaded {len(loaded_daily)} days of unsent telemetry from disk")
            # 加载成功后删除文件，避免下次重复加载
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"Token tracker: failed to load unsent queue: {e}")

    def _save_unsent_queue(self):
        """Persist currently unsent remote data to disk.

        When called:
        1. after a successful save(), if unsent data awaits remote reporting
        2. at the atexit safety net (via save → _report_to_server → failure → persist)

        batch_seq is persisted along: failure + process crash + restart → the retry
        uses the same seq, letting server seen_batches dedupe commits of uncertain
        outcome.
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            with self._lock:
                if not self._unsent_daily:
                    # 无数据，清理残留文件
                    self._unsent_queue_path.unlink(missing_ok=True)
                    return
                data = {
                    "daily": copy.deepcopy(self._unsent_daily),
                    "records": list(self._unsent_records[-200:]),
                    "batch_seq": self._pending_batch_seq,
                    "saved_at": time.time(),
                }
            atomic_write_json(self._unsent_queue_path, data)
        except Exception as e:
            logger.debug(f"Token tracker: failed to persist unsent queue: {e}")

    def save(self):
        """Persist incremental data to disk. Multi-process safe.

        Flow:
        1. take a delta snapshot and clear it inside the thread lock (swap pattern)
        2. read-merge-write inside the file lock
        3. on write failure, put the delta back into memory

        Not-dirty must still trigger remote reporting: for users with pure frontend
        interaction (counter/histogram, no LLM calls), self._dirty stays False forever;
        the old logic's early return kept the instrument accumulation window from ever
        being sent. Skip the local disk write but still call _report_to_server, letting
        it decide internally via has_data() whether to actually POST.
        """
        with self._lock:
            if not self._dirty:
                report_only = True
                delta_daily: dict = {}
                delta_records: list = []
            else:
                report_only = False
                # 取出 delta（swap 模式：先取出，成功后不放回）
                delta_daily = self._delta_daily
                delta_records = list(self._delta_records)
                self._delta_daily = {}
                self._delta_records.clear()
                self._dirty = False

        if report_only:
            # 没 LLM 数据写盘，只问问 instrument 有没有要发的
            try:
                self._report_to_server(delta_daily, delta_records)
            except Exception:
                # 远程失败不影响 idle path —— 已经没本地数据要写，错误就是
                # 纯网络的，下次 60s 周期或 atexit 会再试。_report_to_server
                # 自己内部已有失败 unsent 持久化逻辑，这里不重复打日志。
                pass
            return

        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

            with _file_lock(self._lock_file_path):
                # 读取现有数据
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                # 合并 delta 到 existing
                for day_key, day_delta in delta_daily.items():
                    if day_key not in existing["daily_stats"]:
                        existing["daily_stats"][day_key] = day_delta
                    else:
                        _merge_day_stats(existing["daily_stats"][day_key], day_delta)

                # 合并 recent_records
                existing["recent_records"].extend(delta_records)
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])

                # 清理 90 天前的旧数据
                cutoff = (date.today() - timedelta(days=90)).isoformat()
                old_keys = [k for k in existing["daily_stats"] if k < cutoff]
                for k in old_keys:
                    del existing["daily_stats"][k]

                existing["last_saved"] = datetime.now().isoformat()
                atomic_write_json(self._storage_path, existing)

            # 本地保存成功后，尝试远程上报（在文件锁外，避免阻塞其他进程）
            try:
                self._report_to_server(delta_daily, delta_records)
            except Exception:
                pass  # 远程上报失败不影响本地保存，静默忽略

        except Exception as e:
            logger.warning(f"Failed to save token usage data: {e}")
            # 写入失败，将 delta 放回内存，下次重试
            with self._lock:
                for day_key, day_delta in delta_daily.items():
                    if day_key not in self._delta_daily:
                        self._delta_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._delta_daily[day_key], day_delta)
                # 恢复 records（旧的在前，新的在后）
                restored = delta_records + list(self._delta_records)
                self._delta_records.clear()
                self._delta_records.extend(restored[-200:])
                self._dirty = True

    def _report_to_server(self, delta_daily: dict, delta_records: list):
        """Report incremental data to the remote telemetry server.

        Data-loss prevention design:
        - _unsent_daily accumulates in memory while also persisted to .telemetry_unsent.json
        - after the process is killed and restarted, _load_unsent_queue() recovers unsent data
        - the unsent queue file is cleared after a successful send
        - on send failure, put back into memory + persisted, retried next time
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return

        # 累积 unsent 数据
        with self._lock:
            for day_key, day_delta in delta_daily.items():
                if day_key not in self._unsent_daily:
                    self._unsent_daily[day_key] = copy.deepcopy(day_delta)
                else:
                    _merge_day_stats(self._unsent_daily[day_key], day_delta)
            self._unsent_records.extend(delta_records)
            if len(self._unsent_records) > 200:
                self._unsent_records = self._unsent_records[-200:]

        # 持久化 unsent 队列（防 kill 丢数据）
        self._save_unsent_queue()

        # 检查上报间隔
        now = time.time()
        if now - self._last_report_time < self._report_interval:
            return

        # peek instrument 累积 —— 即使 daily_stats 是空的（用户没触发 LLM 调
        # 用，但有前端互动 counter），只要 instrument 里有东西，就值得发一次。
        try:
            from utils.instrument import has_data as _instrument_has_data
            has_instruments = _instrument_has_data()
        except Exception:
            has_instruments = False

        # 取出待发送数据。同时区分两种状态：
        #   is_retry=False：新窗口，分配新 batch_seq，正常带 instrument snapshot
        #   is_retry=True ：上次失败遗留下来的重传，复用同一 batch_seq，**不**
        #                   附带任何新 instrument —— retry 的 batch_id 已经
        #                   在 server seen_batches 里，整个 batch 会被 dedupe
        #                   返回 duplicate，跟进去的 instrument 会被一起静默
        #                   丢掉。把新 instrument 留在 buffer，下个新窗口
        #                   （新 batch_seq）单独发出去。
        with self._lock:
            if not self._unsent_daily and not has_instruments:
                return
            send_daily = self._unsent_daily
            send_records = self._unsent_records
            self._unsent_daily = {}
            self._unsent_records = []
            is_retry = self._pending_batch_seq is not None
            if self._pending_batch_seq is None:
                self._pending_batch_seq = secrets.token_hex(8)
            batch_seq = self._pending_batch_seq
        # 标记这次发送是否带 daily/records —— instrument-only 失败后清
        # stale batch_seq 时要用（见 except 路径注释）。
        had_unsent_payload = bool(send_daily or send_records)

        # 仅新窗口才 snapshot instrument。重传时跳过保留 buffer 等下窗口。
        instruments_snapshot: dict = {}
        if not is_retry:
            try:
                from utils.instrument import snapshot as _instrument_snapshot
                instruments_snapshot = _instrument_snapshot()
            except Exception as e:
                logger.debug(f"Token tracker: instrument snapshot failed (non-critical): {e}")

        try:
            if not self._device_id:
                self._device_id = _get_anonymous_device_id()
            if not self._branch:
                self._branch = _get_telemetry_branch(self._config_manager.config_dir)

            app_version = _get_app_version_from_changelog()
            telemetry_locale = _get_telemetry_locale()
            telemetry_timezone = _get_telemetry_timezone()
            # 一次调用同时拿 distribution + steam_user_id，两个字段同源 ——
            # 杜绝原本两次独立 GetSteamID() 跨 SDK ready 边界产生的
            # release + 非空 Steam64 矛盾态。
            telemetry_distribution, telemetry_steam_user_id = _get_telemetry_metadata()
            telemetry_device_hw = _get_device_hw()

            payload = {
                "device_id": self._device_id,
                # 迁移期同时带旧算法 ID，便于 server 在 events.payload 里
                # 留底，将来可建 legacy→new 映射 fold 历史 cohort。server
                # 当前 Pydantic model 不声明此字段，会被默认 ignore；HMAC
                # 签名是基于完整 payload dict 的 canonical JSON 计算的，所以
                # server 端验签会自动覆盖到，不需要任何调整。
                "device_id_legacy": _get_legacy_device_id(),
                "app_version": app_version,
                "branch": self._branch,
                "locale": telemetry_locale,
                "timezone": telemetry_timezone,
                "distribution": telemetry_distribution,
                # 仅在 Steamworks SDK 起来 + 拿到 Steam64 时填值，其它情况为
                # 空 string。server 端按 preserve-known 处理：空值不覆写历史。
                "steam_user_id": telemetry_steam_user_id,
                # 设备硬件画像（低基数 enum 复合串）。设备属性，server preserve-known
                # UPSERT；用来 JOIN 留存做"低配设备首日流失"分析。
                "device_hw": telemetry_device_hw,
                "daily_stats": send_daily,
                "recent_records": send_records,
            }
            # instrument snapshot 走 optional 字段：老 server 不识别会 ignore，
            # 新 server 原样存进 events.payload，dashboard 端可后续解析。HMAC
            # 签名覆盖整个 payload dict，所以加字段不影响验签。
            if instruments_snapshot:
                payload["instruments"] = instruments_snapshot
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

            ts = time.time()
            sig = _compute_telemetry_signature(payload_json, ts)

            # batch_id 用于 server seen_batches 幂等去重，必须满足两个目标：
            #   (a) 失败重传 / 网络 timeout（server commit 了 client 没收到 200）
            #       下次重发同一份 daily 时 batch_id 必须**不变**，让 server
            #       识别重复 commit 并跳过。
            #   (b) 不同窗口（含纯 instrument-only 窗口、daily 都空时）的
            #       batch_id 必须**唯一**，否则被前一窗口的 seen_batches dedupe
            #       误伤，后续 instrument 数据全丢。
            #
            # batch_seq 同时满足：进程内首次进入此窗口时分配新值，失败重传
            # （含进程 kill 后重启）保留同一 seq，成功 200 后清空。把 seq 放进
            # hash，daily / records / instruments 自己不需要进 hash —— 尤其
            # instruments 是 clear-on-read、不会在重传中复现，把它进 hash 反而
            # 破坏 (a)。
            #
            # batch_core **只用 retry-stable 字段**：device_id + batch_seq。
            # app_version 故意不进 —— 它在每次上报时实时读 changelog，重试之间
            # 若用户更新了 app，同一份 unsent batch 会算出不同 batch_id；
            # timeout-after-commit 后在新版本上重启重传就绕过 seen_batches、
            # 把已 commit 的 daily_stats 重复计（Codex P1）。device_id_legacy
            # 同理也不进（依赖 uuid.getnode()，多网卡枚举顺序不稳）。
            # batch_seq 已是 per-window 唯一 + 跨重试稳定，device_id 保证跨设备
            # 不撞，二者足够。签名 (HMAC) 仍覆盖完整 payload（含 app_version）。
            batch_core = {
                "device_id": payload["device_id"],
                "batch_seq": batch_seq,
            }
            batch_id = hashlib.sha256(
                json.dumps(batch_core, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:32]
            submission = {
                "timestamp": ts,
                "signature": sig,
                "payload": payload,
                "batch_id": batch_id,
            }
            body = json.dumps(submission, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}

            # >= 1KB 才 gzip：小 payload 不划算（见 _TELEMETRY_GZIP_THRESHOLD 注释）。
            # mtime=0 让同一 body 总是产出相同压缩字节，便于 diff 调试和 fuzzing
            # 期不会因为时间戳差异看起来像两次上报。
            if len(body) >= _TELEMETRY_GZIP_THRESHOLD:
                body = gzip.compress(body, compresslevel=6, mtime=0)
                headers["Content-Encoding"] = "gzip"

            req = urllib.request.Request(
                f"{_TELEMETRY_SERVER_URL}/api/v1/telemetry",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TELEMETRY_TIMEOUT) as resp:
                if resp.status == 200:
                    self._last_report_time = now
                    # 发送成功：清 batch_seq，下次窗口重新分配；清 unsent 文件。
                    with self._lock:
                        self._pending_batch_seq = None
                    self._unsent_queue_path.unlink(missing_ok=True)
                    logger.debug("Token tracker: telemetry reported successfully")
                    return

            raise Exception(f"HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"Token tracker: telemetry report failed (non-critical): {e}")
            # 发送失败：放回 unsent + 持久化。daily-bearing 失败时**不清**
            # _pending_batch_seq —— 下次重试用同一 seq，让 server seen_batches
            # dedupe "网络 timeout 但 server 已经 commit" 的不确定成败重传。
            #
            # 但 instrument-only 失败（send_daily 和 send_records 都空，
            # had_unsent_payload=False）必须清 batch_seq：instruments 是
            # clear-on-read 没东西放回，留着 stale seq 会让**下一个新窗口**
            # 复用它算出与已 commit 的 batch_id 相同的 hash，server 直接
            # 返回 "duplicate, skipped"，新窗口的数据被静默丢弃。
            with self._lock:
                for day_key, day_delta in send_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                restored = send_records + self._unsent_records
                self._unsent_records = restored[-200:]
                if not had_unsent_payload:
                    # 没有真要重传的内容 —— 防 stale seq 误伤下一窗口
                    self._pending_batch_seq = None
            self._save_unsent_queue()

    def start_periodic_save(self):
        """Start the background periodic save task. Must be called inside an asyncio loop."""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            # 两种触发 save() 的条件：
            #   (a) self._dirty —— 有 LLM token delta 要本地写盘 + 远程上报
            #   (b) instrument has_data —— 纯前端互动（前端 ws telemetry /
            #       ws_connect / 各种 feature counter）不会让 _dirty=True，
            #       但 instrument 已经累积了一窗口需要 60s 节奏上报
            # save() 内部对 (b) 走 report-only path（跳过本地 write，只调
            # _report_to_server）；对 (a) 走完整 write + report。
            need_save = self._dirty
            if not need_save:
                try:
                    from utils.instrument import has_data as _instrument_has_data
                    need_save = _instrument_has_data()
                except Exception:
                    # has_data 在锁内只做 dict bool 检查，正常不会抛；
                    # import 失败 fall through，本轮跳过，下轮重试。
                    pass
            if need_save:
                await asyncio.to_thread(self.save)
            # 顺手让 event_logger 落地稀疏事件 buffer + 跑 retention 清理。
            # 即使本周期 token_tracker 没有 dirty，event_logger 也可能有
            # session/crash 之类的事件等着写 —— 不挂在 self._dirty 后面，避免
            # 纯前端互动（不触发 LLM 调用）的事件被一直憋在内存里。
            # event_logger.flush 自带节流（cleanup 5min 一次），nothing-to-do
            # 路径 ~微秒级。
            try:
                from utils.event_logger import EventLogger
                await asyncio.to_thread(EventLogger.get_instance().flush)
            except Exception as e:
                logger.debug(f"Token tracker: event_logger flush failed (non-critical): {e}")
