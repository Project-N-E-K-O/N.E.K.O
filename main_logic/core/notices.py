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
"""Prominent-notice buffer pool awaiting frontend pickup.

Split out of the former single-file ``main_logic/core.py`` as a pure move (no
behavior change). This module is the single owner of the queue state
(``_prominent_notice_queue`` / ``_prominent_notice_lock`` /
``_prominent_notice_seq``); the package ``__init__`` and
``main_routers.system_router.status`` re-export / import the accessor
functions, so every producer and consumer shares this one queue.
"""
import threading


# ---------------------------------------------------------------------------
# 重要通知缓冲池
# 任何模块随时可以调用 enqueue_prominent_notice() 往池里推消息；
# 前端通过 GET /api/pending-notices 拉取（返回通知列表和游标），
# 用户全部确认后通过 POST /api/pending-notices/ack?cursor=N 只删除已展示的通知，
# 避免 peek→ack 两次 HTTP 往返之间新入队的通知被静默清空（TOCTOU）。
# ---------------------------------------------------------------------------
_prominent_notice_queue: list[dict] = []
_prominent_notice_lock = threading.Lock()
_prominent_notice_seq: int = 0  # 单调递增，每条通知入队时分配


def enqueue_prominent_notice(notice: "str | dict"):
    """Put a prominent notice into the buffer pool, awaiting frontend pickup.
    
    Accepts a string (automatically wrapped as {"message": ...}) or a structured
    dict (recommended fields: "code", "message", "message_en", "details").
    """
    global _prominent_notice_seq
    if isinstance(notice, str):
        item: dict = {"message": notice}
    else:
        item = dict(notice)
    with _prominent_notice_lock:
        _prominent_notice_seq += 1
        item["_nid"] = _prominent_notice_seq
        _prominent_notice_queue.append(item)


def peek_prominent_notices() -> tuple[list[dict], int]:
    """Return a snapshot of the buffer pool and the current cursor (for GET /pending-notices).

    Returns (notices_without_internal_fields, cursor); cursor is the largest _nid in
    this snapshot, and passing it to drain_prominent_notices(cursor) deletes exactly
    the displayed items.
    """
    with _prominent_notice_lock:
        items = list(_prominent_notice_queue)
    cursor = items[-1]["_nid"] if items else 0
    public = [{k: v for k, v in it.items() if k != "_nid"} for it in items]
    return public, cursor


def drain_prominent_notices(up_to_cursor: int) -> list[dict]:
    """Delete notices with _nid <= up_to_cursor, keeping items enqueued afterwards.

    Returns the list of deleted notices. Passing 0 or a negative number deletes nothing.
    """
    if up_to_cursor <= 0:
        return []
    with _prominent_notice_lock:
        remaining = [it for it in _prominent_notice_queue if it.get("_nid", 0) > up_to_cursor]
        drained = [it for it in _prominent_notice_queue if it.get("_nid", 0) <= up_to_cursor]
        _prominent_notice_queue.clear()
        _prominent_notice_queue.extend(remaining)
    return drained


# ---------------------------------------------------------------------------
# CosyVoice 旧版音色通知去重（模块级，startup 和 LLMSessionManager 共享）
# ---------------------------------------------------------------------------
_notified_legacy_voices: set[str] = set()


def enqueue_voice_migration_notice(legacy_names: list) -> None:
    """Push the legacy CosyVoice voice notice after dedup. Called by both the main_server
    startup path and LLMSessionManager, avoiding duplicate popups for the same character."""
    global _notified_legacy_voices
    if not legacy_names:
        return
    new_names = sorted(set(legacy_names) - _notified_legacy_voices)
    if not new_names:
        return
    _notified_legacy_voices.update(new_names)
    enqueue_prominent_notice({
        "code": "notice.voiceMigration.legacyDetected",
        "message": "检测到旧版 CosyVoice 音色可能已失效，建议重新克隆语音。",
        "message_en": "Legacy CosyVoice voices detected that may no longer work. Consider re-cloning your voices.",
        "details": {"voices": new_names},
    })
