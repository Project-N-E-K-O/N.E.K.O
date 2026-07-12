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
"""Cross-session context-append plumbing for ``LLMSessionManager``: request
and durable dedup caches, payload normalization, and pending-append
flushes into the live or next session.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import time
from collections import OrderedDict
from typing import Any, Mapping
from utils.llm_client import AIMessage, HumanMessage
from ._shared import (
    _CONTEXT_APPEND_DEDUP_TTL_SECONDS,
    _CONTEXT_APPEND_DEDUP_MAX_ENTRIES,
    _CONTEXT_APPEND_READY_FLUSH_MAX_PASSES,
    _CONTEXT_APPEND_SOURCE_MAX_TOKENS,
    _CONTEXT_APPEND_BARE_PRIME_SOURCES,
    logger,
    ContextAppendResult,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class ContextAppendMixin:
    """Cross-session context-append methods (see module docstring)."""

    def _fire_task(self, coro):
        """Create a background task with GC protection (prevent Python 3.11+ from collecting it)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _context_append_request_key(self, payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return None
        source = str(payload.get("source") or "").strip()
        lifetime = str(payload.get("lifetime") or "current_session").strip().lower()
        if lifetime == "current_session":
            if payload.get("_dedup_pending_ready"):
                return (source, request_id, lifetime, "pending_ready")
            session_id = payload.get("_dedup_session_id")
            if session_id is None:
                session_id = id(getattr(self, "session", None))
            return (source, request_id, lifetime, session_id)
        return (source, request_id, lifetime)

    def _context_append_durable_cache_key(self, payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
        request_id = str(payload.get("request_id") or "").strip()
        lifetime = str(payload.get("lifetime") or "").strip().lower()
        if not request_id or lifetime not in {"next_session", "session_family"}:
            return None
        source = str(payload.get("source") or "").strip()
        return (source, request_id, lifetime)

    def _context_append_durable_cache_seen(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return False
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if not isinstance(seen, OrderedDict):
            return False
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
            entries = getattr(self, "_context_append_durable_cache_entries", None)
            if isinstance(entries, dict):
                entries.pop(oldest_key, None)
        return key in seen

    def _context_append_durable_cache_contains(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return False
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        expected_entry = None
        if isinstance(entries, dict):
            expected_entry = entries.get(key)
        if expected_entry is None:
            expected_entry = self._context_payload_cache_key(payload)
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list):
            return False
        return expected_entry in {
            (str(entry.get("role") or ""), str(entry.get("text") or ""))
            for entry in cache
            if isinstance(entry, Mapping)
        }

    def _remember_context_append_durable_cache(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if not isinstance(seen, OrderedDict):
            seen = OrderedDict()
            self._context_append_durable_cache_keys = seen
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        if not isinstance(entries, dict):
            entries = {}
            self._context_append_durable_cache_entries = entries
        entries[key] = self._context_payload_cache_key(payload)
        seen[key] = time.time()
        seen.move_to_end(key)
        while len(seen) > _CONTEXT_APPEND_DEDUP_MAX_ENTRIES:
            stale_key, _ = seen.popitem(last=False)
            entries.pop(stale_key, None)

    def _forget_context_append_durable_cache(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if isinstance(seen, OrderedDict):
            seen.pop(key, None)
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        if isinstance(entries, dict):
            entries.pop(key, None)

    def _promote_context_append_request_id_to_current_session(self, payload: dict) -> None:
        if (
            str(payload.get("lifetime") or "").strip().lower() != "current_session"
            or not payload.get("_dedup_pending_ready")
            or not payload.get("request_id")
        ):
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            return
        old_key = self._context_append_request_key(payload)
        if old_key is None:
            return
        timestamp = seen.pop(old_key, None)
        payload.pop("_dedup_pending_ready", None)
        payload["_dedup_session_id"] = id(getattr(self, "session", None))
        new_key = self._context_append_request_key(payload)
        if timestamp is not None and new_key is not None:
            seen[new_key] = timestamp
            self._context_append_request_ids = OrderedDict(
                sorted(seen.items(), key=lambda item: item[1])
            )

    def _remember_context_append_request_id(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_request_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            seen = OrderedDict()
            self._context_append_request_ids = seen
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
        seen[key] = now
        seen.move_to_end(key)
        while len(seen) > _CONTEXT_APPEND_DEDUP_MAX_ENTRIES:
            seen.popitem(last=False)

    def _forget_context_append_request_id(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_request_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if isinstance(seen, OrderedDict):
            seen.pop(key, None)

    def _context_append_request_seen(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_request_key(payload)
        if key is None:
            return False
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            return False
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
        return key in seen

    def _normalize_context_append(
        self,
        *,
        source: str,
        role: str,
        text: str,
        audience: str,
        timing: str,
        lifetime: str,
        request_id: str | None,
        ordering_key: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict | None:
        normalized_source = str(source or "").strip()
        normalized_role = str(role or "").strip().lower()
        normalized_audience = str(audience or "").strip().lower()
        normalized_timing = str(timing or "").strip().lower()
        normalized_lifetime = str(lifetime or "").strip().lower()
        if (
            not normalized_source
            or normalized_role not in {"assistant", "user", "system"}
            or normalized_audience not in {"model", "user_and_model"}
            or normalized_timing not in {"now", "when_ready"}
            or normalized_lifetime not in {"current_session", "next_session", "session_family"}
        ):
            return None
        content = self._normalize_context_text_for_source(normalized_source, text)
        if not content:
            return None
        safe_metadata = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
        return {
            "source": normalized_source,
            "role": normalized_role,
            "text": content,
            "audience": normalized_audience,
            "timing": normalized_timing,
            "lifetime": normalized_lifetime,
            "request_id": str(request_id or "").strip(),
            "ordering_key": str(ordering_key or "").strip(),
            "metadata": safe_metadata,
        }

    def _normalize_context_text_for_source(self, source: str, text: Any) -> str:
        content = str(text or "").strip()
        if not content:
            return ""
        max_tokens = _CONTEXT_APPEND_SOURCE_MAX_TOKENS.get(
            str(source or "").strip(),
            _core_facade._CONTEXT_APPEND_DEFAULT_MAX_TOKENS,
        )
        try:
            from utils.tokenize import truncate_to_tokens
            return truncate_to_tokens(content[: max(max_tokens * 8, max_tokens)], max_tokens).strip()
        except Exception:
            return content[: max(max_tokens * 8, max_tokens)].strip()

    def _append_context_to_new_session_cache(self, role: str, text: str) -> bool:
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list):
            cache = []
            self.next_session_context_messages = cache
        if role == "user":
            speaker = getattr(self, "master_name", "user")
        elif role == "assistant":
            speaker = getattr(self, "lanlan_name", "assistant")
        else:
            speaker = "system"
        cache.append({"role": speaker, "text": text})
        return True

    def _context_payload_cache_key(self, payload: Mapping[str, Any]) -> tuple[str, str]:
        role = str(payload.get("role") or "").strip().lower()
        if role == "user":
            speaker = getattr(self, "master_name", "user")
        elif role == "assistant":
            speaker = getattr(self, "lanlan_name", "assistant")
        else:
            speaker = "system"
        return (speaker, str(payload.get("text") or ""))

    def _mark_pending_context_appends_delivered_in_start_prompt(
        self,
        snapshot: list[dict],
        *,
        owner: object | None = None,
    ) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list) or not pending or not snapshot:
            return
        available: dict[tuple[str, str], int] = {}
        for entry in snapshot:
            if not isinstance(entry, Mapping):
                continue
            key = (str(entry.get("role") or ""), str(entry.get("text") or ""))
            available[key] = available.get(key, 0) + 1
        for payload in pending:
            if (
                not isinstance(payload, dict)
                or not payload.get("_durable_cached")
                or payload.get("_delivered_in_start_prompt")
            ):
                continue
            key = self._context_payload_cache_key(payload)
            count = available.get(key, 0)
            if count <= 0:
                continue
            payload["_delivered_in_start_prompt"] = True
            payload["_delivered_in_start_prompt_owner"] = owner
            available[key] = count - 1

    def _clear_pending_context_start_prompt_marks(self, *, owner: object | None = None) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list):
            return
        for payload in pending:
            if isinstance(payload, dict):
                if owner is not None and payload.get("_delivered_in_start_prompt_owner") is not owner:
                    continue
                payload.pop("_delivered_in_start_prompt", None)
                payload.pop("_delivered_in_start_prompt_owner", None)

    def _snapshot_next_session_context_messages(self) -> list[dict]:
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list) or not cache:
            return []
        return list(cache)

    def _consume_next_session_context_messages(self, count: int) -> None:
        if count <= 0:
            return
        cache = getattr(self, "next_session_context_messages", None)
        if isinstance(cache, list):
            del cache[:count]

    async def _prime_late_next_session_context_after_swap(
        self,
        start_index: int,
        end_index: int | None = None,
    ) -> int:
        consumed_count = max(0, start_index)
        session = getattr(self, "session", None)
        prime_context = getattr(session, "prime_context", None)
        if not callable(prime_context):
            return consumed_count

        snapshot = self._snapshot_next_session_context_messages()
        stop_index = len(snapshot) if end_index is None else max(consumed_count, min(end_index, len(snapshot)))
        late_context = snapshot[consumed_count:stop_index]
        if not late_context:
            return consumed_count
        try:
            await prime_context(self._convert_cache_to_str(late_context), skipped=True)
        except Exception as exc:
            logger.warning(
                "[%s] final-swap late next-session context prime failed: %s",
                self.lanlan_name,
                exc,
            )
            return consumed_count
        consumed_count += len(late_context)

        return consumed_count

    async def _append_context_to_targets(self, payload: dict) -> ContextAppendResult:
        role = payload["role"]
        content = payload["text"]
        audience = payload["audience"]
        lifetime = payload["lifetime"]
        targets: list[str] = []
        if payload.get("_delivered_in_start_prompt") and lifetime in {"next_session", "session_family"}:
            return ContextAppendResult(appended=True, targets=("start_prompt",))
        if lifetime in {"next_session", "session_family"}:
            durable_cache_remembered = (
                payload.get("_durable_cached")
                or self._context_append_durable_cache_seen(payload)
            )
            # 去重记账本（_context_append_durable_cache_*）与真缓存
            # （next_session_context_messages）是两套独立结构：前者记“这条已写过”，
            # 后者存实际内容，而后者会被 session-swap 的 _consume_next_session_context_messages
            # 异步消费/清空。下面用 remembered（记账本）与 present（真缓存）双重核对决定是否
            # 重写，本身能兜住失步、不丢上下文；但两者一旦失步是静默的，故在此显式自检并告警，
            # 把“隐蔽失步”变成日志里可观测的信号（见 _consume_next_session_context_messages
            # 不同步清记账本的设计债）。
            durable_cache_present = self._context_append_durable_cache_contains(payload)
            if durable_cache_remembered and not durable_cache_present:
                # 记账本说写过、内容却没了：通常是 swap 消费了缓存而记账本（TTL 内）未清。
                # 当前靠下方 present 核对兜底重写、不会丢；但若后续有人移除该核对、只信记账本，
                # 这条上下文就会被误判“已写过”而静默丢失。出现此日志即代表两者已失步。
                logger.warning(
                    "[%s] durable context cache desync: dedup record present but content "
                    "missing from next-session cache; re-appending (source=%s request_id=%s)",
                    self.lanlan_name,
                    payload.get("source"),
                    payload.get("request_id"),
                )
            elif durable_cache_present and not durable_cache_remembered:
                # 内容在、记账本却没记：这条会被当作首次写而重复入库，下个 session 可能看到两遍。
                logger.warning(
                    "[%s] durable context cache desync: content present but dedup record "
                    "missing; may duplicate in next session (source=%s request_id=%s)",
                    self.lanlan_name,
                    payload.get("source"),
                    payload.get("request_id"),
                )
            if durable_cache_remembered and durable_cache_present:
                targets.append("new_session_cache")
            elif self._append_context_to_new_session_cache(role, content):
                payload["_durable_cached"] = True
                self._remember_context_append_durable_cache(payload)
                targets.append("new_session_cache")
        session = getattr(self, "session", None)
        history = getattr(session, "_conversation_history", None)
        wrote_active_history = False
        current_session_required = lifetime in {"current_session", "session_family"}
        current_session_delivered = False
        current_session_failed_reason: str | None = None
        if lifetime in {"current_session", "session_family"} and isinstance(history, list):
            if role == "assistant":
                message = AIMessage(content=content)
            elif role == "user":
                message = HumanMessage(content=content)
            else:
                message = HumanMessage(content=f"system: {content}")
            history.append(message)
            targets.append("active_history")
            wrote_active_history = True
            current_session_delivered = True

        if lifetime in {"current_session", "session_family"} and not wrote_active_history:
            prime_context = getattr(session, "prime_context", None)
            if callable(prime_context):
                try:
                    source = str(payload.get("source") or "")
                    prime_text = content if source in _CONTEXT_APPEND_BARE_PRIME_SOURCES else f"{role}: {content}"
                    await prime_context(prime_text, skipped=(audience == "model"))
                    targets.append("realtime_prime")
                    current_session_delivered = True
                except Exception as exc:
                    current_session_failed_reason = "realtime_prime_failed"
                    logger.warning("[%s] context append realtime_prime failed: %s", self.lanlan_name, exc)
            else:
                current_session_failed_reason = "no_current_session_target"

        current_session_delivery_required = (
            current_session_required
            and (
                not bool(getattr(self, "is_preparing_new_session", False))
                or bool(getattr(self, "_require_context_append_current_delivery", False))
            )
        )
        if current_session_delivery_required and not current_session_delivered:
            return ContextAppendResult(
                appended=False,
                targets=tuple(targets),
                reason=current_session_failed_reason or "current_session_target_unavailable",
            )

        if not targets:
            return ContextAppendResult(appended=False, reason="no_context_target")
        return ContextAppendResult(appended=True, targets=tuple(targets))

    async def append_context(
        self,
        *,
        source: str,
        role: str,
        text: str,
        audience: str = "model",
        timing: str = "now",
        lifetime: str = "current_session",
        request_id: str | None = None,
        ordering_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextAppendResult:
        payload = self._normalize_context_append(
            source=source,
            role=role,
            text=text,
            audience=audience,
            timing=timing,
            lifetime=lifetime,
            request_id=request_id,
            ordering_key=ordering_key,
            metadata=metadata,
        )
        if payload is None:
            return ContextAppendResult(appended=False, reason="invalid_context")
        pending_needed = (
            payload["timing"] == "when_ready"
            and (
                not bool(getattr(self, "session_ready", False))
                or getattr(self, "session", None) is None
            )
        )
        if pending_needed and payload["lifetime"] == "current_session":
            payload["_dedup_pending_ready"] = True
        request_key = self._context_append_request_key(payload)
        if self._context_append_request_seen(payload):
            inflight = getattr(self, "_context_append_inflight_results", None)
            if isinstance(inflight, dict) and request_key in inflight:
                original_result = await asyncio.shield(inflight[request_key])
                if original_result.appended:
                    return ContextAppendResult(
                        appended=False,
                        deduped=True,
                        targets=original_result.targets,
                        reason="duplicate_request_id",
                    )
                return original_result
            return ContextAppendResult(appended=False, deduped=True, reason="duplicate_request_id")
        reserved_request_id = bool(payload["request_id"])
        inflight_result: asyncio.Future[ContextAppendResult] | None = None
        if reserved_request_id:
            self._remember_context_append_request_id(payload)
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if not isinstance(inflight, dict):
                    inflight = {}
                    self._context_append_inflight_results = inflight
                inflight_result = asyncio.get_running_loop().create_future()
                inflight[request_key] = inflight_result

        if pending_needed:
            if payload["lifetime"] in {"next_session", "session_family"}:
                payload["_durable_cached"] = self._append_context_to_new_session_cache(
                    payload["role"],
                    payload["text"],
                )
                if payload["_durable_cached"]:
                    self._remember_context_append_durable_cache(payload)
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list):
                pending = []
                self.pending_context_appends = pending
            sequence = int(getattr(self, "_context_append_sequence", 0))
            self._context_append_sequence = sequence + 1
            payload["_sequence"] = sequence
            payload["_pending_ready"] = True
            pending.append(payload)
            result = ContextAppendResult(appended=True, targets=("pending_ready",))
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(result)
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if isinstance(inflight, dict):
                    inflight.pop(request_key, None)
            return result

        try:
            result = await self._append_context_to_targets(payload)
        except asyncio.CancelledError:
            if reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(ContextAppendResult(
                    appended=False,
                    reason="context_inject_cancelled",
                ))
            raise
        except Exception:
            if reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(ContextAppendResult(
                    appended=False,
                    reason="context_inject_failed",
                ))
            raise
        else:
            if not result.appended and reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(result)
        finally:
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if isinstance(inflight, dict):
                    inflight.pop(request_key, None)
        return result

    async def _flush_pending_context_appends(self) -> int:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list) or not pending:
            return 0
        self.pending_context_appends = []
        pending.sort(key=lambda payload: (
            payload.get("ordering_key") or f"~{int(payload.get('_sequence', 0)):020d}",
            int(payload.get("_sequence", 0)),
        ))
        retry: list[dict] = []
        flushed = 0
        for index, payload in enumerate(pending):
            try:
                result = await self._append_context_to_targets(payload)
                if not result.appended:
                    retry.append(payload)
                else:
                    self._promote_context_append_request_id_to_current_session(payload)
                    flushed += 1
            except asyncio.CancelledError:
                retry.append(payload)
                retry.extend(pending[index + 1:])
                if retry:
                    self.pending_context_appends = retry + self.pending_context_appends
                raise
            except Exception as exc:
                retry.append(payload)
                logger.warning("[%s] context append flush failed: %s", self.lanlan_name, exc)
        if retry:
            self.pending_context_appends = retry + self.pending_context_appends
        return flushed

    async def _drain_pending_context_appends_before_ready(self) -> None:
        for _ in range(_CONTEXT_APPEND_READY_FLUSH_MAX_PASSES):
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list) or not pending:
                return
            before_ids = {id(payload) for payload in pending}
            flushed = await self._flush_pending_context_appends()
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list) or not pending:
                return
            after_ids = {id(payload) for payload in pending}
            if flushed <= 0 and after_ids <= before_ids:
                return
        pending = getattr(self, "pending_context_appends", None)
        if isinstance(pending, list) and pending:
            logger.warning(
                "[%s] context append ready drain left %d pending item(s)",
                self.lanlan_name,
                len(pending),
            )

    def _clear_pending_context_appends(self, *, release_durable_cached: bool = False) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if isinstance(pending, list):
            stale_payloads = list(pending)
            pending.clear()
        else:
            stale_payloads = []
            self.pending_context_appends = []
        for payload in stale_payloads:
            if (
                isinstance(payload, dict)
                and payload.get("request_id")
                and (release_durable_cached or not payload.get("_durable_cached"))
            ):
                self._forget_context_append_request_id(payload)
                if release_durable_cached:
                    self._forget_context_append_durable_cache(payload)
