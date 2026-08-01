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

from utils.config_manager import get_config_manager
from utils.token_tracker import set_call_type
from utils.llm_client import SystemMessage, HumanMessage, AIMessage, messages_to_dict, messages_from_dict, create_chat_llm, openai_retry_error_types
import re
import json
import os
import asyncio
import hashlib
import logging
from contextlib import suppress

from config.prompts.prompts_memory import (
    get_recent_history_manager_prompt, get_detailed_recent_history_manager_prompt,
    get_further_summarize_prompt, get_history_review_prompt,
    get_summary_stale_hint,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import (
    detect_prompt_language_with_ascii_fallback,
    get_global_language_full,
)
from utils.tokenize import acount_tokens
from config import (
    LLM_OUTPUT_GUARD_MAX_TOKENS,
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    RECENT_HISTORY_MAX_ITEMS,
    RECENT_COMPRESS_THRESHOLD_ITEMS,
    RECENT_SUMMARY_MAX_TOKENS,
    RECENT_PER_MESSAGE_MAX_TOKENS,
    RECENT_SUMMARY_STALE_HOURS,
    RECENT_COMPRESS_INPUT_BUDGET_TOKENS,
    RECENT_HARD_CAP_TOKENS,
)
from datetime import datetime


def _detect_recent_prompt_language(text: str) -> str:
    return detect_prompt_language_with_ascii_fallback(
        text,
        ui_language=get_global_language_full(),
    )

# Backward-compat alias (Stage-1 → Stage-2 trigger threshold).
# Two-stage flow: Stage 1 (`compress_history`) summarises raw messages with no
# explicit length cap; Stage 2 (`further_compress`) is invoked only when Stage-1
# output exceeds this threshold. Stage-2's own prompt hard-caps output at
# 500 chars/words per language.
MAX_SUMMARY_TOKENS = RECENT_SUMMARY_MAX_TOKENS

# ── Phase C review snapshot/capacity 算法 ─────────────────────────────
# Fingerprint = 末尾 K 条消息的 (type, content[:50]) 元组列表。K=3 兼顾
# 抗碰撞（连续 3 条 mixed user+ai 几乎不会误命中）和定位精度。
REVIEW_FINGERPRINT_K = 3
REVIEW_FINGERPRINT_CONTENT_PREFIX = 50
_PROMPT_TEXT_PART_TYPES = frozenset((None, 'text', 'input_text', 'output_text'))


def _review_message_content(message) -> str:
    if isinstance(message, dict):
        content = message.get('content', '')
        if 'content' not in message and isinstance(message.get('data'), dict):
            content = message['data'].get('content', '')
    elif hasattr(message, 'content'):
        content = message.content
    else:
        return str(message)
    content = content or ''
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get('text', '') or item))
            else:
                parts.append(str(item))
        return '\n'.join(parts)
    if isinstance(content, str):
        return content
    return str(content)


def _message_locale_text(message) -> str:
    if isinstance(message, dict):
        content = message.get('content', '')
        if 'content' not in message and isinstance(message.get('data'), dict):
            content = message['data'].get('content', '')
    elif hasattr(message, 'content'):
        content = message.content
    else:
        return ''
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts = []
    for item in content:
        if not isinstance(item, dict):
            if isinstance(item, str):
                parts.append(item)
            continue
        item_type = item.get('type')
        text = item.get('text')
        if item_type in _PROMPT_TEXT_PART_TYPES and isinstance(text, str):
            parts.append(text)
    return '\n'.join(parts)


def _review_prompt_locale_text(messages: list) -> str:
    """Return raw message content without speaker labels."""
    return '\n\n'.join(_message_locale_text(message) for message in messages)


async def _await_recent_mutation_to_completion(func, *args):
    """Wait for a submitted recent-file mutation before propagating cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        with suppress(BaseException):
            operation.result()
        raise


async def review_context_token_count(messages: list) -> int:
    """Return a stable token-size metric without blocking the event loop."""
    rows = []
    for message in messages:
        role = getattr(message, 'type', '') or ''
        content = _review_message_content(message)
        if isinstance(message, dict):
            role = message.get('type', message.get('role', role)) or ''
        rows.append(f"{role}: {content}")
    return await acount_tokens('\n\n'.join(rows))


def _review_response_hit_output_limit(response) -> bool:
    """Classify only strong evidence that the provider exhausted output tokens."""
    metadata = getattr(response, 'response_metadata', None) or {}
    finish_reason = str(metadata.get('finish_reason') or '').strip().lower()
    if finish_reason in {'length', 'max_tokens'}:
        return True

    content = str(getattr(response, 'content', '') or '').strip()
    if content:
        return False
    usage = metadata.get('token_usage') or {}
    output_tokens = usage.get('completion_tokens')
    if output_tokens is None:
        output_tokens = usage.get('output_tokens')
    try:
        return int(output_tokens or 0) >= LLM_OUTPUT_GUARD_MAX_TOKENS
    except (TypeError, ValueError):
        return False


def _msg_identity(m) -> tuple[str, str]:
    """Normalize a message into its full type/content identity.

    Supports message objects (HumanMessage/AIMessage/...) and dicts (persisted
    fingerprints). When content is a list (multimodal), it is joined into a
    string.
    """
    if isinstance(m, dict):
        t = m.get('type', '') or ''
        c = m.get('content', '') if 'content' in m else (m.get('data', {}).get('content', '') if isinstance(m.get('data'), dict) else '')
    else:
        t = getattr(m, 'type', '') or ''
        c = getattr(m, 'content', '') or ''
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                parts.append(p.get('text', '') or str(p))
            else:
                parts.append(str(p))
        c = ' '.join(parts)
    elif not isinstance(c, str):
        c = str(c)
    return (str(t), c)


def _msg_fingerprint(m) -> tuple[str, str]:
    """Return the legacy type/content-prefix fingerprint tuple."""
    t, c = _msg_identity(m)
    return (t, c[:REVIEW_FINGERPRINT_CONTENT_PREFIX])


def _msg_content_sha256(m) -> str:
    """Return a stable digest of the normalized full message content."""
    return hashlib.sha256(_msg_identity(m)[1].encode("utf-8")).hexdigest()


def build_review_fingerprint(snapshot, k: int = REVIEW_FINGERPRINT_K) -> list[dict]:
    """Take the last K messages of the snapshot as the fingerprint, serialized into JSON-persistable dicts."""
    if not snapshot:
        return []
    tail = snapshot[-k:] if len(snapshot) >= k else list(snapshot)
    out = []
    for m in tail:
        t, c = _msg_fingerprint(m)
        out.append({
            'type': t,
            'content': c,
            'content_sha256': _msg_content_sha256(m),
        })
    return out


def _find_fingerprint_position(current: list, fingerprint: list[dict]) -> int | None:
    """Find the unique run of K consecutive messages matching the fingerprint.

    Returns the index in current of the fingerprint's last element (i.e. the
    cutoff); None when not found or ambiguous. New fingerprints include a full
    content digest; legacy persisted fingerprints fall back to their prefix.
    """
    if not current or not fingerprint:
        return None
    k = len(fingerprint)
    if len(current) < k:
        return None
    def _matches(message, fp: dict) -> bool:
        msg_type, content_prefix = _msg_fingerprint(message)
        if msg_type != fp.get('type', '') or content_prefix != fp.get('content', ''):
            return False
        digest = fp.get('content_sha256')
        return digest is None or _msg_content_sha256(message) == digest

    match = None
    for i in range(len(current) - k + 1):
        if all(_matches(current[i + j], fingerprint[j]) for j in range(k)):
            if match is not None:
                # 多个候选时宁可白做，也不能把 review/压缩结果切进错误区间。
                return None
            match = i + k - 1
    return match


def _compute_review_capacity(snapshot: list, current: list) -> tuple[int, int | None]:
    """Given the snapshot taken at review start and the current history, compute (capacity, cutoff_idx).

    1. Use the snapshot's last K entries as an anchor to locate cutoff_idx in current.
    2. Walk backwards from cutoff_idx, comparing snapshot[-1], snapshot[-2], ...
       against current[cutoff_idx], current[cutoff_idx-1], ...; the consecutive
       match length is the capacity (stopping when an "alien" entry such as a
       compression SystemMessage appears in between).

    Returns ``(0, None)`` for a wasted (no-op) review.
    """
    if not snapshot or not current:
        return (0, None)
    if (
        len(current) >= len(snapshot)
        and all(_msg_identity(current[i]) == _msg_identity(snapshot[i]) for i in range(len(snapshot)))
    ):
        return (len(snapshot), len(snapshot) - 1)
    anchor = build_review_fingerprint(snapshot, REVIEW_FINGERPRINT_K)
    cutoff_idx = _find_fingerprint_position(current, anchor)
    if cutoff_idx is None:
        return (0, None)
    # 从 cutoff 起逆向走（包含 cutoff 自身），算 capacity
    capacity = 0
    s_idx = len(snapshot) - 1
    c_idx = cutoff_idx
    while s_idx >= 0 and c_idx >= 0 and _msg_identity(current[c_idx]) == _msg_identity(snapshot[s_idx]):
        capacity += 1
        s_idx -= 1
        c_idx -= 1
    return (capacity, cutoff_idx)


# Setup logger
from utils.file_utils import (
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils import recent_file
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

# ── recent.json 读态三分 ──────────────────────────────────────────────
# 关键区分：「读不出来」不是「盘上是空的」。重构前两者都被折叠成 []，于是一次
# 瞬时读失败就会让下一次落盘拿这批新消息覆盖掉整段历史。
RECENT_READ_OK = 'ok'
RECENT_READ_MISSING = 'missing'
RECENT_READ_UNREADABLE = 'unreadable'

# 磁盘长期不可写时，未落盘批次的条数上界。超了丢最旧的：降级形态是「内存里
# 留住最近 N 条」，仍严格好于重构前的「立刻把这批丢掉」。
RECENT_PENDING_MAX_ITEMS = 64


class CompressedRecentHistoryManager:
    def __init__(
        self,
        max_history_length: int = RECENT_HISTORY_MAX_ITEMS,
        compress_threshold: int = RECENT_COMPRESS_THRESHOLD_ITEMS,
    ):
        self._config_manager = get_config_manager()
        # 通过get_character_data获取相关变量
        _, _, _, _, name_mapping, _, _, _, recent_log = self._config_manager.get_character_data()
        self.max_history_length = max_history_length      # 压缩后保留条数
        self.compress_threshold = compress_threshold      # >此值才触发压缩
        self.log_file_path = recent_log
        self.name_mapping = name_mapping
        # 这里**不**预加载。两个构造点（memory_server.runtime 首启 + reload）都是
        # async 函数、都跑在事件循环线程上：既不该在那儿等别的写者的文件临界区，
        # 也不该在那儿对 N 个角色各做一次多 MB 的阻塞读。
        # 副产品是「视图未知」（name 不在 dict 里）和「已知为空」（值是 []）从此
        # 是两个可区分的状态——落盘失败的合并逻辑依赖这个区分。
        self.user_histories = {}
        # user_histories 是给调用方看的可见视图，可能已包含 process-wide pending。
        # 单独记录尾部 pending 的长度，读盘失败时才能精确替换它，而不是靠内容去重
        # （用户连续发送相同消息是合法的）。
        self._cached_pending_counts = {}
        # A successful write from another producer invalidates the cached disk
        # baseline even when the next physical read is transiently unavailable.
        self._cached_disk_versions = {}

    # ── 未落盘批次账本 ────────────────────────────────────────────────
    def _pending_batches(self, lanlan_name: str) -> list:
        """Return messages that could not be persisted to this character's file."""
        return recent_file.get_recent_pending(self._ensure_path_for_character(lanlan_name))

    def _set_pending_batches(self, lanlan_name: str, messages: list, file_path=None) -> None:
        """Record (or clear) unpersisted messages while the file lock is held."""
        cap = max(2 * self.compress_threshold, RECENT_PENDING_MAX_ITEMS)
        if len(messages) > cap:
            logger.warning(
                f"[RecentHistory] {lanlan_name} 未落盘消息已达上界 {cap} 条，"
                f"丢弃最旧 {len(messages) - cap} 条"
            )
            messages = messages[-cap:]
        recent_file.set_recent_pending_unlocked(
            file_path or self._ensure_path_for_character(lanlan_name), messages,
        )

    def _get_default_path(self, lanlan_name: str) -> str:
        """Single place for the default path, avoiding duplicated code."""
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, lanlan_name), 'recent.json')

    def _ensure_path_for_character(self, lanlan_name: str) -> str:
        """Ensure the character has a valid file path; returns the path."""
        if lanlan_name not in self.log_file_path:
            self.log_file_path[lanlan_name] = self._get_default_path(lanlan_name)
            logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径")
        return self.log_file_path[lanlan_name]

    def _capture_recent_operation_admission(self, lanlan_name: str):
        """Freeze the path identity before an operation reaches its first await."""
        file_path = (self.log_file_path or {}).get(lanlan_name)
        if not file_path:
            file_path = os.path.join(
                self._config_manager.memory_dir, lanlan_name, "recent.json",
            )
        return file_path, recent_file.capture_recent_generation(file_path)

    def _reset_history_file_unlocked(self, file_path, lanlan_name, reason):
        """Reset a corrupt/empty recent file to ``[]``.

        The caller MUST already hold ``recent_file.recent_file_lock(file_path)``:
        this is reached from inside the locked read path and ``threading.Lock``
        is not reentrant. Naming follows ``event_log._write_line_unlocked``.
        """
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="reset",
                target=f"memory/{lanlan_name}/recent.json",
            )
            recent_file.write_recent_payload_unlocked(file_path, [])
            logger.warning(f"[RecentHistory] {lanlan_name} 的历史记录文件无效（{reason}），已重置为空列表: {file_path}")
        except MaintenanceModeError:
            raise
        except Exception as reset_error:
            logger.error(f"[RecentHistory] 重置 {lanlan_name} 的历史记录文件失败: {reset_error}", exc_info=True)

    def _load_history_unlocked(self, file_path, lanlan_name) -> tuple[str, list]:
        """Read and parse the recent file. The caller MUST already hold the file lock.

        Returns ``(status, history)``:
          ``(RECENT_READ_OK, [...])``       parsed successfully;
          ``(RECENT_READ_MISSING, [])``     the file is absent, or it was empty /
              corrupt and has been reset — empty is the truth here;
          ``(RECENT_READ_UNREADABLE, [])``  the bytes could not be read or
              decoded. This is NOT an empty history; callers must not persist
              anything on top of it.
        """
        if not os.path.exists(file_path):
            return (RECENT_READ_MISSING, [])
        try:
            raw_content = recent_file.read_recent_text_unlocked(file_path)

            if not raw_content.strip():
                self._reset_history_file_unlocked(file_path, lanlan_name, "文件为空")
                return (RECENT_READ_MISSING, [])

            file_content = json.loads(raw_content)
            if not isinstance(file_content, list):
                self._reset_history_file_unlocked(file_path, lanlan_name, "JSON 根节点不是列表")
                return (RECENT_READ_MISSING, [])

            return (RECENT_READ_OK, messages_from_dict(file_content))
        except json.JSONDecodeError as e:
            # 与重构前一致：这个 handler 里 reset 抛出的 MaintenanceModeError 会
            # 直接冒泡（下面的 except Exception 是兄弟分支，接不到）。
            self._reset_history_file_unlocked(file_path, lanlan_name, f"JSON 解析失败: {e}")
            return (RECENT_READ_MISSING, [])
        except Exception as e:
            logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，本轮视为读不到（不覆盖）")
            return (RECENT_READ_UNREADABLE, [])

    def _cache_history_view(
        self, file_path, lanlan_name, disk_history, pending=(),
    ) -> list:
        """Cache and return a visible disk-plus-pending history view."""
        visible = list(disk_history) + list(pending)
        self.user_histories[lanlan_name] = visible
        pending_counts = getattr(self, "_cached_pending_counts", None)
        if pending_counts is None:
            pending_counts = self._cached_pending_counts = {}
        pending_counts[lanlan_name] = len(pending)
        disk_versions = getattr(self, "_cached_disk_versions", None)
        if disk_versions is None:
            disk_versions = self._cached_disk_versions = {}
        disk_versions[lanlan_name] = (
            recent_file.get_recent_content_version_unlocked(file_path)
        )
        return visible

    def _cached_disk_history(self, lanlan_name) -> list:
        """Return the cached disk base without any pending tail already shown."""
        cached = list(self.user_histories.get(lanlan_name, ()))
        pending_count = getattr(self, "_cached_pending_counts", {}).get(lanlan_name, 0)
        if pending_count:
            return cached[:-pending_count]
        return cached

    def _current_cached_disk_history(self, file_path, lanlan_name) -> list:
        """Return the cached disk base only when no successful write superseded it."""
        current_version = recent_file.get_recent_content_version_unlocked(file_path)
        cached_version = getattr(
            self, "_cached_disk_versions", {},
        ).get(lanlan_name)
        if cached_version != current_version:
            return []
        return self._cached_disk_history(lanlan_name)

    def _read_history_locked(
        self, file_path, lanlan_name, expected_generation=None,
    ) -> list:
        """Read one character's history under the file lock. Run me in a worker thread."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if status == RECENT_READ_UNREADABLE:
                # 读失败绝不能被当成「历史是空的」——一旦写进 user_histories，下一次
                # 落盘就会拿这批新消息覆盖整段读不出来的历史。磁盘基线保持既有缓存，
                # 但 process-wide pending 仍是 prompt 必须看见的已接受消息。
                return self._cache_history_view(
                    file_path,
                    lanlan_name,
                    self._current_cached_disk_history(file_path, lanlan_name),
                    pending,
                )
            # 写失败留下的 process-wide pending 也是当前可见历史的一部分。无论
            # recent.json 尚未创建还是仍停在旧版本，普通读取都不能把它从内存视图抹掉。
            return self._cache_history_view(
                file_path, lanlan_name, history, pending,
            )

    def _commit_hard_cap_locked(
        self, file_path, lanlan_name, expected_history, new_history,
        expected_generation=None,
    ) -> str:
        """Persist a precomputed trim only if the locked disk snapshot is unchanged."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, current = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                return 'failed'
            pending = recent_file.get_recent_pending_unlocked(file_path)
            current_visible = list(current) + list(pending)
            if messages_to_dict(current_visible) != messages_to_dict(expected_history):
                return 'stale'
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(
                    f"[RecentHistory] {lanlan_name} 硬上限裁剪落盘失败: {e}",
                    exc_info=True,
                )
                return 'failed'
            recent_file.set_recent_pending_unlocked(file_path, [])
            self._cache_history_view(file_path, lanlan_name, new_history)
            return 'committed'

    def _merge_backup_memo_locked(
        self, file_path, lanlan_name, snapshot, memo, expected_generation=None,
    ) -> tuple[str, int, int]:
        """Re-read, locate, merge and persist a backup memo in one critical section."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            read_status, current = self._load_history_unlocked(file_path, lanlan_name)
            if read_status == RECENT_READ_UNREADABLE:
                return ('failed', 0, 0)
            if not current or not snapshot:
                return ('moot', len(current), len(current))
            capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
            if (
                cutoff_idx is None
                or capacity != len(snapshot)
                or (cutoff_idx - capacity + 1) != 0
            ):
                return ('moot', len(current), len(current))
            new_history = [memo] + current[cutoff_idx + 1:]
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(
                    f"[RecentHistory] {lanlan_name} 后台压缩合并落盘失败: {e}",
                    exc_info=True,
                )
                return ('failed', len(current), len(current))
            pending = recent_file.get_recent_pending_unlocked(file_path)
            self._cache_history_view(
                file_path, lanlan_name, new_history, pending,
            )
            return ('merged', len(current), len(new_history))


    def _get_llm(self):
        """Fetch the LLM instance dynamically to support config hot-reload.

        timeout=30 pairs with business-level max_retries=3 + exponential backoff
        (worst case ~127s), covering the "give up" ceiling after the /process
        upstream's 30s timeout.
        max_retries=0 disables the OpenAI SDK's default 2 automatic retries,
        avoiding a 3× blow-up stacked on the business-level retry.
        """
        api_config = self._config_manager.get_model_api_config('summary')
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=30, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length summary/JSON isn't truncated
            provider_type=api_config.get('provider_type'),
        )

    def _get_review_llm(self):
        """Fetch the review LLM instance dynamically to support config hot-reload.

        timeout uses MEMORY_LLM_HARD_TIMEOUT_SECONDS (the upstream forwards with
        a 120s hard cap; must be ≤110). Review is a pure background task (after
        the Phase C redesign it holds no locks, never blocks the user path, and
        concurrent runs are harmless), so thinking can remain enabled for the
        judgment-dense work of rewriting history.

        ``extra_body=None`` explicitly overrides the provider-aware factory
        default, leaving thinking models on their native/default behavior.
        max_retries=0 as above: SDK auto-retry off; the business-layer retry is
        the safety net.
        """
        api_config = self._config_manager.get_model_api_config('correction')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
            extra_body=None,
            provider_type=api_config.get('provider_type'),
        )

    def _append_and_persist_locked(
        self, file_path, lanlan_name, new_messages, expected_generation=None,
    ) -> tuple[list, bool]:
        """Read → merge unpersisted batches → append → persist, as one critical section.

        Returns ``(history, persisted)``. ``persisted`` is False when the batch
        only made it into memory; those messages are recorded in ``_pending`` so
        that the next call re-attaches them instead of letting the on-disk copy
        silently win.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if status == RECENT_READ_UNREADABLE:
                # 读不到盘上内容 ≠ 盘上是空的。这里一写就是拿这批新消息覆盖掉
                # 整段读不出来的历史（重构前的 `except Exception: return []`
                # 正是这么丢的）。所以本轮完全不写盘。
                updated_pending = list(pending) + list(new_messages)
                self._set_pending_batches(lanlan_name, updated_pending, file_path)
                logger.warning(
                    f"[RecentHistory] {lanlan_name} 历史文件读取失败，本轮不落盘，"
                    f"{len(new_messages)} 条新消息暂存内存等下次补写"
                )
                return (
                    self._cache_history_view(
                        file_path,
                        lanlan_name,
                        self._current_cached_disk_history(file_path, lanlan_name),
                        updated_pending,
                    ),
                    False,
                )

            merged = list(history) + list(pending) + list(new_messages)
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(merged),
                )
            except Exception as e:
                # 落盘失败 ⟹ 目标文件未被替换（atomic_write_text 里
                # _replace_with_busy_retry 是最后一条语句），所以把整批挂回
                # pending 不会造成二次落地，不需要任何去重启发式。
                self._set_pending_batches(
                    lanlan_name, list(pending) + list(new_messages), file_path,
                )
                self._cache_history_view(
                    file_path,
                    lanlan_name,
                    history,
                    list(pending) + list(new_messages),
                )
                logger.error(f"[RecentHistory] 保存历史记录失败: {e}", exc_info=True)
                return (merged, False)

            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, merged)
            return (merged, True)

    def _splice_compressed_locked(
        self, file_path, lanlan_name, snapshot, memo, expected_generation=None,
    ) -> str:
        """Replace the compressed head with ``memo`` and persist, as one critical section.

        Re-reads the file inside the lock. The compression LLM ran for seconds to
        minutes; before this refactor the splice was computed from the in-memory
        view taken *before* the LLM call, so every batch persisted during that
        window was overwritten wholesale.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, current = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                logger.warning(f"[RecentHistory] {lanlan_name} 压缩结果合并时读盘失败，放弃本轮合并")
                return 'failed'
            if not current:
                # 被 /new_dialog 清空 / 被用户在 UI 上清掉：把 memo 塞回去等于
                # 复活已删内容。与 merge_backup_memo 的 'moot' 同一判断。
                logger.info(f"[RecentHistory] {lanlan_name} 压缩期间历史已被清空，丢弃本轮摘要")
                return 'moot'
            capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
            if (
                cutoff_idx is not None
                and capacity == len(snapshot)
                and (cutoff_idx - capacity + 1) == 0
            ):
                new_history = [memo] + current[cutoff_idx + 1:]
            else:
                # 不能证明 snapshot 仍是 current 的完整头部时必须 fail closed。
                # 按条数 fallback 会在短指纹碰撞时把未参与压缩的尾部一起切掉。
                logger.info(f"[RecentHistory] {lanlan_name} 压缩锚点失配，丢弃本轮摘要")
                return 'moot'
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(f"[RecentHistory] {lanlan_name} 压缩结果落盘失败: {e}", exc_info=True)
                return 'failed'
            pending = recent_file.get_recent_pending_unlocked(file_path)
            self._cache_history_view(
                file_path, lanlan_name, new_history, pending,
            )
            return 'merged'

    async def update_history(self, new_messages, lanlan_name, detailed=False, compress=True, on_compress_done=None):
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        # 云存档栅栏留在 try 外：MaintenanceModeError 照旧直接冒泡给调用方。
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )

        try:
            # CS-1：读盘 + 合并未落盘批次 + append + 落盘，一个临界区。
            # 先把未压缩状态落盘再进耗时的 compress_history：后者走 LLM，数秒到
            # 数十秒，期间进程崩溃或 task 被 cancel 会导致本批 new_messages 丢失。
            history, persisted = await _await_recent_mutation_to_completion(
                self._append_and_persist_locked,
                file_path,
                lanlan_name,
                list(new_messages),
                admission_generation,
            )
            logger.debug(
                f"[RecentHistory] {lanlan_name} 添加了 {len(new_messages)} 条新消息，"
                f"当前共 {len(history)} 条（已落盘={persisted}）"
            )
            if not persisted:
                # 与重构前一致：第一次落盘失败就不再往下走压缩（原实现靠异常跳过
                # 整段）。这批消息已记进 _pending，下一次 update_history 会连同
                # 磁盘内容一起补写。
                return

            if compress and len(history) > self.compress_threshold:
                to_compress = history[:-self.max_history_length+1]
                snapshot = list(to_compress)
                # 压缩 LLM 在**锁外**跑。它耗时数秒到数十秒，关进临界区会把
                # /cache 一起挡住。
                compressed_result = await self.compress_history(to_compress, lanlan_name, detailed)
                if compressed_result is None:
                    logger.warning(
                        f"[RecentHistory] {lanlan_name} 摘要失败，跳过本轮压缩以保留原始历史"
                    )
                    # best-effort：通知上层起一个受保护的后台压缩任务尽力压（主路径失败）。
                    # 硬上限裁剪**不在这里**做——否则"历史超 cap 后任何一次暂时性失败"
                    # 都会立刻丢最旧原文，而后台压缩用的是裁剪前 snapshot → 合并失配
                    # moot → 那批对话没被摘要就永久丢了。改由后台 best-effort 也压不成
                    # 后再裁剪（memory_server._run_backup_compress / dead-letter 分支），
                    # 让暂时性失败有机会被后台压成摘要保留。
                    #
                    # ⚠️ 这个回调**必须**留在所有临界区之外：dead-letter 分支会同步
                    # 调 enforce_hard_cap，那条路径要拿同一把文件锁，而 threading.Lock
                    # 不可重入 —— 挪进任何一个临界区就是 worker 线程上的无超时死锁。
                    await self._notify_compress_done(
                        on_compress_done,
                        lanlan_name,
                        snapshot,
                        False,
                        detailed,
                        admission_generation,
                    )
                else:
                    # CS-2：读盘 + 定位 + splice + 落盘，一个临界区。
                    splice_status = await _await_recent_mutation_to_completion(
                        self._splice_compressed_locked,
                        file_path, lanlan_name, snapshot, compressed_result[0],
                        admission_generation,
                    )
                    # merged / moot 都不需要再跑同一份后台摘要；只有读写失败才触发兜底。
                    await self._notify_compress_done(
                        on_compress_done,
                        lanlan_name,
                        snapshot,
                        splice_status != 'failed',
                        detailed,
                        admission_generation,
                    )
        except (MaintenanceModeError, recent_file.RecentFileDeletedError):
            raise
        except Exception as e:
            logger.error(f"[RecentHistory] 更新历史记录时出错: {e}", exc_info=True)


    # ── Past block 更新 meta（防止"几天前的事还在 summary 里被反复带出来"
    #    ——见 config.RECENT_SUMMARY_STALE_HOURS 注释）。
    # 锚点不是"上次 summary 时间"——summary 每轮压缩都会跑，跟着锚点会让
    # stale hint 永远跟在最后一次压缩后 1 小时，无法形成"每隔 N 小时
    # 刷一次 past block"的稳定节奏。这里改记"上次 hint 真正注入的时刻"，
    # 即"上次 LLM 实际更新 past block 的时刻"——只有那种 turn 才推进锚点。
    def _summary_meta_path(self, lanlan_name: str) -> str:
        """Side meta file per character, co-located with recent.json
        ({"last_past_block_update_at": ISO}).

        Prefers the directory of ``self.log_file_path[lanlan_name]`` — the
        actual path source of every recent.json read/write in this class (item
        9 of the character_data tuple). If the user's config moved a
        character's recent.json outside memory_dir, the meta file still sits in
        the same directory instead of wandering off (CodeRabbit review catch on
        PR #1316). Before update_history has run, log_file_path may be empty —
        in that case fall back to the memory_dir-based derivation.
        """
        recent_path = (self.log_file_path or {}).get(lanlan_name)
        if recent_path:
            return os.path.join(os.path.dirname(recent_path), 'recent_meta.json')
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, lanlan_name),
            'recent_meta.json',
        )

    async def _aread_last_past_block_update_at(self, lanlan_name: str) -> datetime | None:
        path = self._summary_meta_path(lanlan_name)
        if not await asyncio.to_thread(os.path.exists, path):
            return None
        try:
            def _read():
                with open(path, encoding='utf-8') as f:
                    return f.read()
            raw = await asyncio.to_thread(_read)
            data = robust_json_loads(raw)
            if not isinstance(data, dict):
                return None
            # 兼容 PR #1316 早期 in-progress 版本的旧 key（last_summary_at）。
            # 用 OR 兜底——已合并版本不会写 last_summary_at，本兼容仅服务
            # 在我本机跑过该 PR 中间 commit 的开发者，下次写 meta 会用新 key
            # 覆盖旧文件。
            ts = data.get('last_past_block_update_at') or data.get('last_summary_at')
            if not ts:
                return None
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    async def _awrite_last_past_block_update_at(self, lanlan_name: str) -> None:
        path = self._summary_meta_path(lanlan_name)
        try:
            await asyncio.to_thread(os.makedirs, os.path.dirname(path), exist_ok=True)
            await atomic_write_json_async(
                path,
                {'last_past_block_update_at': datetime.now().isoformat()},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name}: 写 recent_meta 失败: {e}")

    def _render_message_content(self, msg):
        from utils.tokenize import truncate_head_tail_tokens

        content = getattr(msg, 'content', '')
        half_cap = self._summary_message_half_cap()
        if isinstance(content, str):
            return truncate_head_tail_tokens(content, half_cap, half_cap)
        parts = []
        try:
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get('text', f"|{item.get('type', '')}|"))
                else:
                    parts.append(str(item))
        except Exception:
            parts = [str(content)]
        return truncate_head_tail_tokens(
            "\n".join(parts),
            half_cap,
            half_cap,
        )

    @staticmethod
    def _summary_message_half_cap():
        return RECENT_PER_MESSAGE_MAX_TOKENS // 2

    def _summary_prompt_locale_text(self, messages):
        from utils.tokenize import truncate_head_tail_tokens

        half_cap = self._summary_message_half_cap()
        return "\n".join(
            truncate_head_tail_tokens(
                _message_locale_text(msg),
                half_cap,
                half_cap,
            )
            for msg in messages
        )

    def _render_messages_to_text(self, messages, lanlan_name):
        """把消息列表渲染成喂给摘要 LLM 的文本：每条做头尾保留截断 + role 前缀。

        单条 message 文本超过 RECENT_PER_MESSAGE_MAX_TOKENS 时做头尾保留截断
        （head=tail=半数 token）。用户长贴 / AI 偶尔写小作文都会触发；头尾各
        保留确保问候/问题与结尾的总结/请求都不丢，中段砍掉。
        """
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            lines.append(f"{role} | {self._render_message_content(msg)}")
        return "\n".join(lines)

    def _build_summary_prompt(self, messages_text, detailed, *, locale_text=None):
        """构建 Stage-1 摘要 prompt（不含 stale-hint 前缀；单次压缩与分段 map 共用）。

        ``{MASTER_NAME}`` 是 prompt 里"保留负面反馈"段引用 master 实名的字面
        占位符（与同 prompt 里既有的 ``%s`` 共存）。⚠️ master_name 替换**最后**
        做：它是 user-controlled，含 ``%`` 会让先前的 ``%`` formatting 崩溃；含
        ``%s`` 会被先前的 ``.replace("%s", ...)`` 二次替换（codex P2）。
        """
        lang = _detect_recent_prompt_language(
            locale_text if locale_text is not None else messages_text,
        )
        master_name = self.name_mapping['human']
        if not detailed:
            return (
                get_recent_history_manager_prompt(lang)
                .replace("%s", messages_text)
                .replace("{MASTER_NAME}", master_name)
            )
        return (
            (get_detailed_recent_history_manager_prompt(lang) % messages_text)
            .replace("{MASTER_NAME}", master_name)
        )

    async def _invoke_summary_llm(self, prompt):
        """调摘要 LLM（3 次重试 + 对网络/429 指数退避），成功返回 summary 字符串，
        失败返回 None。不含 further_compress / memo 包装 / stale 锚点——那些由
        compress_history 主体在拿到 summary 后处理。Stage-1 单次压缩与分段 map
        阶段共用本方法。
        """
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(prompt)).content  # noqa: LLM_INPUT_BUDGET  # compression prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从 JSON 字典中提取对话摘要，key 与 prompt 模板里约定的一致
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Qwen 偶尔返回 list/dict 而不是字符串；强制 str-ify 后再用
                    # （不然 acount_tokens 会抛 TypeError 把整轮压缩崩掉）。
                    summary = (
                        raw_summary if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    print(f"💗摘要结果：{summary}")
                    return summary
                else:
                    print('💥 摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 摘要模型失败，已达到最大重试次数: {e}')
                    break
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 摘要模型失败：{e}')
                # 如果解析失败，重试
                retries += 1
        return None

    def _split_messages_by_budget(self, messages, lanlan_name):
        """按渲染后累计 token 把消息切成多段，每段 ≤ RECENT_COMPRESS_INPUT_BUDGET_TOKENS。
        不切碎单条消息（单条已被 per-message 截断到 ≤500 token）。"""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        chunks, cur, cur_tok = [], [], 0
        for msg in messages:
            t = count_tokens(self._render_messages_to_text([msg], lanlan_name))
            if cur and cur_tok + t > budget:
                chunks.append(cur)
                cur, cur_tok = [], 0
            cur.append(msg)
            cur_tok += t
        if cur:
            chunks.append(cur)
        return chunks

    def _split_texts_by_budget(self, texts):
        """按累计 token 把字符串列表分批，每批拼接 ≤ 预算（reduce 阶段用）。"""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        batches, cur, cur_tok = [], [], 0
        for txt in texts:
            tok = count_tokens(txt)
            if cur and cur_tok + tok > budget:
                batches.append(cur)
                cur, cur_tok = [], 0
            cur.append(txt)
            cur_tok += tok
        if cur:
            batches.append(cur)
        return batches

    async def _segmented_compress(self, messages, lanlan_name, detailed):
        """输入过大时的分段 map-reduce：把 messages 切段逐段总结成中间摘要，反复
        reduce 直到拼接 ≤ 预算，返回该文本交给 compress_history 主体做最终总结。
        任一段 LLM 失败返回 None（上层据此跳过本轮压缩）。"""
        chunks = self._split_messages_by_budget(messages, lanlan_name)
        partials = []
        for chunk in chunks:
            s = await self._invoke_summary_llm(
                self._build_summary_prompt(
                    self._render_messages_to_text(chunk, lanlan_name),
                    detailed,
                    locale_text=self._summary_prompt_locale_text(chunk),
                )
            )
            if s is None:
                return None
            partials.append(s)
        logger.info(
            f"[RecentHistory] {lanlan_name} 分段压缩：{len(messages)} 条原始消息 → {len(chunks)} 段中间摘要"
        )
        # 中间摘要拼接仍超预算 → 再分批合并总结，限深度防极端。
        depth = 0
        while (
            len(partials) > 1
            and await acount_tokens("\n\n".join(partials)) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS
            and depth < 3
        ):
            batches = self._split_texts_by_budget(partials)
            if len(batches) >= len(partials):
                break  # 无法再缩（单段已超预算），交给主体兜底
            new_partials = []
            for batch in batches:
                s = await self._invoke_summary_llm(
                    self._build_summary_prompt("\n\n".join(batch), detailed)
                )
                if s is None:
                    return None
                new_partials.append(s)
            partials = new_partials
            depth += 1
        merged = "\n\n".join(partials)
        # reduce 缩不动 / 深度耗尽时 merged 仍可能超预算 → 硬截到预算兜底，保证
        # 交给主体最终总结的输入有界（best-effort，丢尾部）。
        if await acount_tokens(merged) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            from utils.tokenize import atruncate_to_tokens
            merged = await atruncate_to_tokens(merged, RECENT_COMPRESS_INPUT_BUDGET_TOKENS)
        return merged

    # detailed: 保留尽可能多的细节
    async def compress_history(self, messages, lanlan_name, detailed=False):
        messages_text = self._render_messages_to_text(messages, lanlan_name)
        locale_text = self._summary_prompt_locale_text(messages)
        # 输入过大（积压一直压不掉时会膨胀）→ 先分段 map-reduce 缩小输入，减小
        # 单次 LLM 输入、避免输入过大导致超时。正常输入不走这条。
        if await acount_tokens(messages_text) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            reduced = await self._segmented_compress(messages, lanlan_name, detailed)
            if reduced is None:
                logger.warning(f"[RecentHistory] {lanlan_name} 分段压缩失败，跳过本轮压缩")
                return None
            messages_text = reduced
            locale_text = reduced

        lang = _detect_recent_prompt_language(locale_text)
        prompt = self._build_summary_prompt(
            messages_text,
            detailed,
            locale_text=locale_text,
        )

        # Past block 时间衰减：距上次"实际更新 past block"超过
        # RECENT_SUMMARY_STALE_HOURS 小时时，在 prompt 头部加提醒让 LLM 把明显
        # 过时的内容挪到 summary 末尾的"较久前"段落。锚点只在 hint 真正注入时
        # 推进——这样 hint 形成"每 N 小时触发一次"的节奏，而不是每轮压缩都刷。
        # 仅影响本次 summary 文本，不持久化到 reflection / persona。
        stale_hint_injected = False
        first_time_baseline = False
        try:
            last_past_update = await self._aread_last_past_block_update_at(lanlan_name)
            if last_past_update is None:
                # 第一次为该角色 compress——先建立 baseline 锚点，本轮不注入 hint。
                first_time_baseline = True
            else:
                gap_hours = (datetime.now() - last_past_update).total_seconds() / 3600.0
                if gap_hours >= RECENT_SUMMARY_STALE_HOURS:
                    hint = get_summary_stale_hint(lang, gap_hours)
                    prompt = hint + "\n\n" + prompt
                    stale_hint_injected = True
        except Exception as e:
            # 时间衰减提醒是 best-effort；失败不能挡 summary 主流程
            logger.debug(f"[RecentHistory] {lanlan_name}: stale hint 注入失败: {e}")

        # Stage-1 + Stage-2 联合重试：原行为是 further_compress 失败时重试整个
        # stage-1（stage-1 LLM 有随机性，重下一次可能直接生成 ≤MAX 的 summary 而
        # 不必二次压缩）。重构后用有限计数循环复现该重试，同时避免原 `continue`
        # 不计数可能导致的死循环。
        summary = None
        for _ in range(3):
            s = await self._invoke_summary_llm(prompt)
            if s is None:
                # stage-1 连续失败：不生成空备忘录，避免覆盖既有 memo 或丢未压原文。
                logger.warning(f"[RecentHistory] {lanlan_name} 摘要连续失败，跳过本轮压缩")
                return None
            if await acount_tokens(s) <= MAX_SUMMARY_TOKENS:
                summary = s
                break
            reduced = await self.further_compress(s)
            if reduced is not None:
                summary = reduced if isinstance(reduced, str) else json.dumps(reduced, ensure_ascii=False)
                break
            # stage-2 失败 → 重试 stage-1（最多 3 轮）
        if summary is None:
            logger.warning(f"[RecentHistory] {lanlan_name} 二次压缩连续失败，跳过本轮压缩")
            return None

        # 推进 past-block 更新锚点（best-effort）：第一次 compress 建 baseline；
        # 注入过 stale hint 表示 LLM 本轮真的更新了 past block。常规压缩不动锚点，
        # 让 hint 形成稳定的"每 N 小时一次"节奏。
        if first_time_baseline or stale_hint_injected:
            await self._awrite_last_past_block_update_at(lanlan_name)

        # 第二个返回值（用于上层缓存）跟 memo_text 用的 summary 保持一致——之前
        # 用 raw 摘要会出现"用户看到的 memo 用 stage-2、缓存却存 stage-1"的不一致。
        from config.prompts.prompts_sys import _loc, MEMORY_MEMO_WITH_SUMMARY
        memo_text = _loc(
            MEMORY_MEMO_WITH_SUMMARY,
            lang,
        ).format(summary=summary)
        return SystemMessage(content=memo_text), summary

    async def _notify_compress_done(
        self, callback, lanlan_name, snapshot, ok, detailed, admission_generation,
    ):
        """Invoke the best-effort compression callback without blocking the main flow.

        memory_server injects the callback: ok=False starts background
        compression, while ok=True cancels any running background task.
        """
        if callback is None:
            return
        try:
            await callback(
                lanlan_name,
                snapshot,
                ok,
                detailed,
                admission_generation,
            )
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name} on_compress_done({ok}) 回调异常: {e}")

    async def enforce_hard_cap(self, lanlan_name, expected_generation=None):
        """Keep recent-history prompt size bounded as a final fallback.

        When history still exceeds RECENT_HARD_CAP_TOKENS after best-effort
        background compression, discard the oldest uncompressed dialogue text
        while preserving the first memo, when present, and at least the latest
        max_history_length entries.
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation
        from utils.tokenize import count_tokens

        def _raw_tokens(msgs):
            # 硬上限按**真实注入 prompt** 的 token 算，不能走 _render_messages_to_text
            # （那个为压缩输入把每条截到 ≤RECENT_PER_MESSAGE_MAX_TOKENS，会严重低估
            # 长消息、让硬上限对超长原文失效）。这里数原始 content 的 token。
            total = 0
            for m in msgs:
                c = getattr(m, 'content', '')
                if isinstance(c, list):
                    c = ' '.join(
                        p.get('text', '') if isinstance(p, dict) else str(p) for p in c
                    )
                elif not isinstance(c, str):
                    c = str(c)
                total += count_tokens(c)
            return total

        def _trim(history):
            if _raw_tokens(history) <= RECENT_HARD_CAP_TOKENS:
                return None  # 未超，不动
            # 首条若是备忘录（已压缩的长期记忆）则保留，只丢正文里最旧的原文。
            head = [history[0]] if isinstance(history[0], SystemMessage) else []
            body = history[len(head):]
            kept = []
            kept_tok = _raw_tokens(head)
            for msg in reversed(body):
                mtok = _raw_tokens([msg])
                if kept and kept_tok + mtok > RECENT_HARD_CAP_TOKENS and len(kept) >= self.max_history_length:
                    break
                kept.append(msg)
                kept_tok += mtok
            kept.reverse()
            return head + kept

        # tokenize 始终在锁外；提交时用完整磁盘快照做 CAS。若期间有新批次，重读后
        # 最多再算一次，绝不拿 stale 结果覆盖并发 append。
        for _ in range(2):
            try:
                history = await asyncio.to_thread(
                    self._read_history_locked,
                    file_path,
                    lanlan_name,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return
            if not history:
                return
            new_history = await asyncio.to_thread(_trim, list(history))
            if new_history is None or len(new_history) >= len(history):
                return
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager, operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            try:
                commit_status = await _await_recent_mutation_to_completion(
                    self._commit_hard_cap_locked,
                    file_path,
                    lanlan_name,
                    history,
                    new_history,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return
            if commit_status == 'committed':
                dropped = len(history) - len(new_history)
                logger.warning(
                    f"[RecentHistory] {lanlan_name} 历史超硬上限 {RECENT_HARD_CAP_TOKENS} token，"
                    f"丢弃最旧 {dropped} 条未压缩原文以保证有界"
                )
                return
            if commit_status == 'failed':
                return
        logger.info(f"[RecentHistory] {lanlan_name} 硬上限裁剪期间历史持续变化，跳过本轮")

    async def merge_backup_memo(
        self, lanlan_name, snapshot, memo, expected_generation=None,
    ):
        """Merge a background compression memo when its disk snapshot still matches.

        The disk re-read, snapshot match, splice, and write form one file critical
        section. A changed or cleared head makes the result moot instead of
        resurrecting stale content.
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation
        try:
            current = await asyncio.to_thread(
                self._read_history_locked,
                file_path,
                lanlan_name,
                admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            return 'moot'
        if not current or not snapshot:
            return 'moot'
        capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
        if cutoff_idx is None or capacity != len(snapshot) or (cutoff_idx - capacity + 1) != 0:
            return 'moot'
        try:
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager, operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            status, before_count, after_count = await _await_recent_mutation_to_completion(
                self._merge_backup_memo_locked,
                file_path,
                lanlan_name,
                snapshot,
                memo,
                admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            return 'moot'
        except MaintenanceModeError:
            raise
        except Exception as e:
            logger.error(f"[RecentHistory] {lanlan_name} 后台压缩合并落盘失败: {e}", exc_info=True)
            return 'failed'
        if status != 'merged':
            return status
        logger.info(
            f"[RecentHistory] {lanlan_name} 后台压缩合并完成：history {before_count}→{after_count}"
        )
        return 'merged'

    async def further_compress(self, initial_summary):
        # Stage-2 LLM 输出硬限：RECENT_SUMMARY_MAX_TOKENS + 100 余量 = 1100 token。
        # prompt 要求 700 字/words：CJK 700 字 ≈ 1050 token (×1.5)、
        # EN 700 words ≈ 933 token，都安全落在 1100 cap 之下。
        # 仍然防 LLM 写小作文；如果真撞到 cap，下面句末标点回溯保证语义边界。
        from utils.tokenize import truncate_to_last_sentence_end
        stage2_cap = RECENT_SUMMARY_MAX_TOKENS + 100
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(
                        # codex P2：先 % 再 .replace，否则 master_name 含 % 会崩
                        (
                            get_further_summarize_prompt(
                                _detect_recent_prompt_language(initial_summary)
                            )
                            % initial_summary
                        )
                        .replace("{MASTER_NAME}", self.name_mapping['human']),
                        max_completion_tokens=stage2_cap,
                    )).content
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从 JSON 字典中提取对话摘要，key 与 prompt 模板里约定的一致
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Stage-2 归一化和 Stage-1 ([memory/recent.py:382](memory/recent.py:382))
                    # 保持一致：非字符串走 json.dumps(ensure_ascii=False) 而非
                    # str()，避免 list/dict 落到 Python repr (单引号) 漂移持久化
                    # 文本与 token 计量。
                    summary_text = (
                        raw_summary.strip() if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    # 命中 stage2_cap → LLM 输出可能停在句子中段（如逗号 / 短语）。
                    # 回溯到最后一个句末标点（. ! ? 。！？… \n），保证持久化的
                    # 摘要语义边界完整。如果根本没找到句末标点（极端短文本），
                    # truncate_to_last_sentence_end 返回 ""，此时退到原文以避免
                    # 完全丢摘要。
                    sane = truncate_to_last_sentence_end(summary_text)
                    if not sane:
                        sane = summary_text
                    print(f"💗第二轮摘要结果：{sane}")
                    return sane
                else:
                    print('💥 第二轮摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 第二轮摘要模型失败，已达到最大重试次数: {e}')
                    return None
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 第二轮摘要模型失败：{e}')
                retries += 1
        return None

    def get_recent_history(self, lanlan_name):
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = self._config_manager.get_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        # 读者也必须进锁：Windows 上一个裸 open() 就足以让并发的 os.replace 抛
        # PermissionError，只锁写者会漏掉一半以上的失败。
        try:
            return self._read_history_locked(
                file_path, lanlan_name, admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            return []

    async def aget_recent_history(self, lanlan_name, *, include_admission=False):
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        try:
            history = await asyncio.to_thread(
                self._read_history_locked,
                file_path,
                lanlan_name,
                admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            history = []
        if include_admission:
            return history, admission_generation
        return history

    def _commit_review_locked(
        self, file_path, lanlan_name, snapshot, corrected_messages,
        expected_generation=None,
    ) -> tuple[str, list[dict] | None, str]:
        """Locate the review window, splice the corrected messages in and persist.

        One critical section, run in a worker thread: the current history is
        re-read inside the lock so that batches persisted while the review LLM
        was running are not overwritten.

        Returns ``(status, new_fingerprint, detail)`` where status is
        ``'patched'`` / ``'white'`` / ``'failed'`` and detail is a log fragment.
        Persist failures are NOT swallowed — they propagate so that the caller's
        ``except Exception`` maps them to ``('failed', None)`` exactly as before.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            read_status, current = self._load_history_unlocked(file_path, lanlan_name)
            if read_status == RECENT_READ_UNREADABLE:
                # 读不到就定位不了 cutoff。这里绝不能报 'white'：_mutate_review_white
                # 会清掉 last_reviewed_cutoff_tail 和 review_fail_attempts、还故意
                # 不刷 last_review_ts → 闸门全放行 → 每次 /process 重跑一整轮
                # review LLM，永不熔断。只读盘场景下那是整夜空烧。
                return ('failed', None, '提交时读盘失败，无法定位 cutoff')

            capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
            if cutoff_idx is None:
                # 白 review：cutoff 在当前 history 里失配（被压缩 / 被清空）
                return ('white', None, 'review 完成但 cutoff 失配')

            take_count = min(capacity, len(corrected_messages))
            if take_count == 0:
                # corrected 为空（罕见：LLM 返回空 corrected_dialogue），等价于
                # 整段删除 review 范围。视为白 review 让下轮重建锚点；不去
                # 写盘也不更新 fingerprint（避免 anchor 漂移到非 review 区）。
                return ('white', None, 'review 输出为空')

            # 替换 [cutoff_idx - capacity + 1, cutoff_idx] 这 capacity 个 slot
            # 为 corrected 末尾 take_count 条；cutoff_idx 之后新增的保留。
            # take_count < capacity 时，前 (capacity - take_count) 个 slot
            # 直接消失（review 决定删条，结果就比原来短）。
            new_history = (
                current[:cutoff_idx - capacity + 1]
                + corrected_messages[-take_count:]
                + current[cutoff_idx + 1:]
            )

            # 栅栏留在白 review 判定之后：重构前它也在那两个 early return 之后，
            # 维护态下的白 review 照旧不会被抬成 'failed'。
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            recent_file.write_recent_payload_unlocked(
                file_path, messages_to_dict(new_history),
            )
            self._cache_history_view(file_path, lanlan_name, new_history)

            # ── Issue #3 修复：基于 patched 后的 new_history 算新 fingerprint ──
            # patched 区在 new_history 里的范围是 [patched_start, patched_end]：
            #   patched_start = cutoff_idx - capacity + 1
            #   patched_end   = patched_start + take_count - 1
            # 新 fingerprint = K 条以 patched_end 结尾的消息。如果 patched_end
            # 之前的消息不足 K-1 条，取从 0 开始所有可用的。
            patched_end = (cutoff_idx - capacity + 1) + take_count - 1
            fp_start = max(0, patched_end - REVIEW_FINGERPRINT_K + 1)
            fp_messages = new_history[fp_start:patched_end + 1]
            new_fingerprint = build_review_fingerprint(fp_messages, k=REVIEW_FINGERPRINT_K)
            detail = (
                f"cutoff_idx={cutoff_idx}, capacity={capacity}, "
                f"corrected={len(corrected_messages)}, take={take_count}, "
                f"history {len(current)}→{len(new_history)}"
            )
            return ('patched', new_fingerprint, detail)

    async def review_history(
        self,
        lanlan_name,
        snapshot=None,
        cancel_event=None,
        expected_generation=None,
    ):
        """
        Review the history, finding and fixing contradictions, redundancy,
        logical confusion, or repetition.

        Phase C redesign (snapshot + capacity-based replacement):
        - ``snapshot``: a copy of the history taken at spawn time (list of
          message objects). The LLM input uses the snapshot, not the current
          history — so while the review LLM runs, the user path can keep
          appending messages / triggering compression without interference.
        - On completion, fingerprint-match the snapshot's last K=3 entries to
          locate the cutoff position in the current history; walk back to get
          the capacity (consecutive match length); replace the consecutive
          ``capacity`` slots before the cutoff in the current history with the
          last ``min(capacity, len(corrected))`` entries of corrected; messages
          added after the cutoff stay untouched.
        - Cutoff not found (swallowed by compression / cleared by /new_dialog)
          → drop the whole batch = wasted review → the caller should set the
          fingerprint to None so the next review can start immediately.

        Returns:
            (str, list[dict] | None) tuple:
              ('patched', new_fingerprint) — patched and persisted;
                  new_fingerprint is the K-entry fingerprint of the review
                  region at the tail of the patched new_history, for the caller
                  to write into maint_state (it **must** use this new
                  fingerprint rather than ``build_review_fingerprint(snapshot)``
                  — the review may have rewritten any of the last K entries,
                  and the old fingerprint would never locate again in the new
                  history)
              ('white', None) — cutoff failed to match in the current history; batch dropped
              ('failed', None) — LLM failure / cancelled / empty history / malformed response
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation

        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（启动前）")
            return ('failed', None)

        # snapshot 由 caller 提供（spawn 时拍下）；为兼容老调用兜底从磁盘读
        if snapshot is None:
            try:
                snapshot = await asyncio.to_thread(
                    self._read_history_locked,
                    file_path,
                    lanlan_name,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return ('failed', None)

        if not snapshot:
            print(f"{lanlan_name} 的历史记录为空，无需审阅")
            return ('failed', None)

        # 将 snapshot 转为可读文本格式（喂 LLM）
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name

        history_text = ""
        for msg in snapshot:
            if hasattr(msg, 'type') and msg.type in name_mapping:
                role = name_mapping[msg.type]
            else:
                role = "unknown"

            content = _review_message_content(msg)

            history_text += f"{role}: {content}\n\n"

        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（准备调用LLM前）")
            return ('failed', None)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            # 身份可能在 spawn 后、首次 LLM 调用前，或重试退避期间被角色
            # 复用/云导入替换。提交门只能保护磁盘，不能阻止旧任务继续占用
            # correction_tasks 并空烧 LLM；每次调用前都要用原 admission token
            # fail closed。
            if recent_file.capture_recent_generation(file_path) != admission_generation:
                return ('failed', None)
            try:
                # 使用LLM审阅历史记录
                set_call_type("memory_review")
                prompt = (
                    # codex P2：先 % formatting 再 .replace，否则 master_name 含 %
                    # 会让 5-arg `% (...)` 把它当格式符崩溃
                    (
                        get_history_review_prompt(
                            _detect_recent_prompt_language(
                                _review_prompt_locale_text(snapshot)
                            )
                        )
                        % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                    )
                    .replace("{MASTER_NAME}", self.name_mapping['human'])
                )
                review_llm = self._get_review_llm()
                try:
                    response = await review_llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # review prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                finally:
                    await review_llm.aclose()

                # 检查是否被取消（LLM调用后）
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理被取消（LLM调用后，保存前）")
                    return ('failed', None)

                if _review_response_hit_output_limit(response):
                    print(f"⚠️ {lanlan_name} 的历史审阅输出达到 token 上限，本轮暂停解析")
                    return ('output_exhausted', None)

                # 确保response_content是字符串
                response_content = str(response.content).strip()

                # 清理响应内容（使用正则安全提取）
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()

                # 解析JSON响应
                review_result = robust_json_loads(response_content)

                if not (
                    isinstance(review_result, dict)
                    and 'explanation' in review_result
                    and isinstance(review_result.get('corrected_dialogue'), list)
                ):
                    print(f"❌ 审阅响应格式错误：{response_content}")
                    return ('failed', None)

                print(f"记忆整理结果：{review_result['explanation']}")

                # 将修正后的对话转换回消息格式。SystemMessage 类型由 compress
                # 产生（summary 备忘录），review 不应该输出，丢弃以保护压缩边界。
                #
                # content 归一化（trust-boundary 防御）：thinking 模型偶尔会把
                # JSON content 字段输出为 list/dict 而非 string。现有
                # compress_history（[memory/recent.py:329-340](memory/recent.py:329)）
                # 已经针对这种情况做过处理；review 的输出同样是模型生成、同样
                # 不可信，必须归一化后再写回 recent history，否则下游（recall /
                # prompt build / fingerprint 比对的 content[:50] 截取）会拿到非
                # 字符串数据炸掉。
                corrected_messages = []
                for msg_data in review_result['corrected_dialogue']:
                    if not isinstance(msg_data, dict):
                        continue
                    role = msg_data.get('role', 'user')
                    content = msg_data.get('content', '')

                    # 归一化 content 到 str
                    if not isinstance(content, str):
                        if isinstance(content, list):
                            parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    parts.append(item.get('text', '') or str(item))
                                else:
                                    parts.append(str(item))
                            content = '\n'.join(parts)
                        else:
                            content = str(content)

                    if role in ['system', 'system_message', name_mapping['system']]:
                        # prompt <要点3> 让 LLM 保留+可编辑 memo，过滤掉等于
                        # 让其在 prompt 里白做工，且 capacity 走过 head SystemMessage
                        # 后这一格无人填补，导致 memo 在写盘时蒸发（场景 D）。
                        # 但只在 snapshot 头本来就是 SystemMessage 时接收 LLM
                        # 的 system 输出——否则 history 还没压缩过、不该有
                        # SystemMessage，LLM 幻觉吐 system 必须丢，避免把伪
                        # memo 注入未压缩对话区污染下游。
                        if snapshot and isinstance(snapshot[0], SystemMessage):
                            corrected_messages.append(SystemMessage(content=content))
                        # else: 静默 drop，恢复老行为
                    elif role in ['user', 'human', name_mapping['human']]:
                        corrected_messages.append(HumanMessage(content=content))
                    elif role in ['ai', 'assistant', name_mapping['ai']]:
                        corrected_messages.append(AIMessage(content=content))
                    else:
                        # 默认作为用户消息处理
                        corrected_messages.append(HumanMessage(content=content))

                # 规范化 SystemMessage 位置：snapshot 头是 memo 时，
                # corrected_messages 必须以唯一一条 SystemMessage 开头。
                # 处理三种 LLM 坏输出：
                # (a) 完全漏返 → 用 snapshot[0] 兜底
                # (b) 放在中间 → 提到头部
                # (c) 多吐几条 → 只留首条
                # 不规范的话头部 memo 边界会被破，下游 prompt 拼装会拿到错位的
                # system 行（甚至中段 SystemMessage 跟下游 compress 的"alien stop"
                # 不变量打架）。
                # 注意：必须 gate 在 corrected_messages 非空——LLM 返空列表是
                # "整段都删"的语义信号，下面 take_count == 0 那条会按白 review
                # 处理；这里塞 snapshot[0] 进去会绕过白 review 闸门、把对话区
                # 全擦掉只剩 memo。
                if (
                    corrected_messages
                    and snapshot
                    and isinstance(snapshot[0], SystemMessage)
                ):
                    sys_msgs = [m for m in corrected_messages if isinstance(m, SystemMessage)]
                    others = [m for m in corrected_messages if not isinstance(m, SystemMessage)]
                    if not others:
                        # LLM 只返 system、没返任何对话 ≡ "整段对话都删"语义信号，
                        # 跟返空列表等价，应走白 review。重置成空让下面 take_count==0
                        # 闸门接管；不然 normalize 会塞一条 SystemMessage 进 corrected，
                        # 长度变 1 绕过闸门，对话区被擦光只剩 memo。
                        corrected_messages = []
                    else:
                        head = sys_msgs[0] if sys_msgs else snapshot[0]
                        corrected_messages = [head] + others

                # ── Phase C 关键：基于 snapshot 算 capacity 做尾部对齐替换 ──
                # 读 current → 定位 → splice → 落盘必须是**一个**临界区：重构前
                # 这四步中间隔着两次 await，review LLM 跑完到落盘之间涌进来的
                # /cache 批次会被整体覆盖。
                try:
                    commit_status, new_fingerprint, detail = (
                        await _await_recent_mutation_to_completion(
                            self._commit_review_locked,
                            file_path,
                            lanlan_name,
                            snapshot,
                            corrected_messages,
                            admission_generation,
                        )
                    )
                except recent_file.RecentFileDeletedError:
                    return ('failed', None)
                if commit_status == 'white':
                    print(f"⚠️ {lanlan_name} {detail}（白 review，丢弃）")
                    return ('white', None)
                if commit_status != 'patched':
                    print(f"❌ {lanlan_name} review 提交失败：{detail}")
                    return ('failed', None)

                print(f"✅ {lanlan_name} 的记忆已修正：{detail}")
                return ('patched', new_fingerprint)

            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 记忆整理失败，已达到最大重试次数: {e}')
                    return ('failed', None)
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
                # 检查是否被取消
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理在重试等待期间被取消")
                    return ('failed', None)
            except Exception as e:
                logger.error(f"❌ 历史记录审阅失败：{e}")
                return ('failed', None)

        # 如果所有重试都失败
        print(f"❌ {lanlan_name} 的记忆整理失败，已达到最大重试次数")
        return ('failed', None)
