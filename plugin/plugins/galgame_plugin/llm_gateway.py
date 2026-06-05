from __future__ import annotations

import asyncio
from collections import OrderedDict
from enum import Enum
import json
import logging
from difflib import SequenceMatcher
import time
from typing import Any, Callable, Mapping

from plugin.sdk.shared.models import Err

from .context_metrics import ContextMetric, ContextMetricsCollector
from .context_tokens import count_tokens_heuristic
from .llm_backend import GalgameLLMBackend
from .models import json_copy
from .service import (
    build_explain_degraded_result,
    build_local_scene_summary,
    build_suggest_degraded_result,
    build_summarize_degraded_result,
)


from ._gateway_utils import (
    _EXPLAIN_EVIDENCE_TYPES,
    _KEY_POINT_TYPES,
    _LLM_NEAR_MATCH_CACHE_MAX_ITEMS,
    _LLM_PROVIDER_BACKOFF_CATEGORIES,
    _LLM_PROVIDER_BACKOFF_SECONDS,
    _LLM_RESPONSE_CACHE_MAX_ITEMS,
    _LOCAL_QUEUE_TIMEOUT_DIAGNOSTIC,
    _NEAR_MATCH_EXCLUDED_KEYS,
    _NEAR_MATCH_OBSERVED_SIGNATURE_MAX_CHARS,
    _NEAR_MATCH_SUPPORTED_OPERATIONS,
    _OBSERVED_SIMILARITY_THRESHOLD,
    _REPEAT_GUARD_MAX_ITEMS,
    _context_lines,
    _current_line_for_near_match,
    _hash_line,
    _hash_stable_lines,
    _jaccard_similarity,
    _json_payload_copy,
    _line_similarity_signature,
    _near_match_context_value,
    _ngrams,
    _normalize_observed_text,
    _observed_similarity,
    _response_similarity,
    _stable_json_fingerprint,
    _stable_json_value,
)
from ._repeat_guard import PluginErrorCategory, ResponseRepeatGuard


class LLMGateway:
    def __init__(self, plugin, logger, config, *, backend: GalgameLLMBackend | None = None) -> None:
        self._plugin = plugin
        self._logger = logger
        self._config = config
        self._backend = backend or GalgameLLMBackend(logger, config)
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_limit = 0
        self._semaphore_acquired = 0
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._cache: OrderedDict[str, tuple[float, dict[str, Any], dict[str, Any]]] = OrderedDict()
        self._near_match_cache: OrderedDict[
            str,
            tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]],
        ] = OrderedDict()
        self._provider_backoff: dict[tuple[str, str], tuple[float, str]] = {}
        self._context_metrics: ContextMetricsCollector | None = None
        self._repeat_guard = ResponseRepeatGuard()

    def update_config(self, config) -> None:
        old_cache_config_fingerprint = self._cache_config_fingerprint()
        old_near_match_config_fingerprint = self._near_match_config_fingerprint()
        old_repeat_config_fingerprint = self._repeat_config_fingerprint()
        self._config = config
        if not self._metrics_enabled():
            self._context_metrics = None
        if hasattr(self._backend, "_config"):
            self._backend._config = config
        cache_config_changed = (
            self._cache_config_fingerprint() != old_cache_config_fingerprint
        )
        near_match_config_changed = (
            self._near_match_config_fingerprint()
            != old_near_match_config_fingerprint
        )
        if cache_config_changed:
            self._cache.clear()
        if cache_config_changed or near_match_config_changed:
            self._near_match_cache.clear()
        if self._repeat_config_fingerprint() != old_repeat_config_fingerprint:
            self._repeat_guard.clear()

    @property
    def context_metrics(self) -> ContextMetricsCollector | None:
        return getattr(self, "_context_metrics", None)

    def _metrics_enabled(self) -> bool:
        return bool(getattr(self._config, "context_metrics_enabled", False))

    def _metrics_collector(self) -> ContextMetricsCollector | None:
        if not self._metrics_enabled():
            return None
        if getattr(self, "_context_metrics", None) is None:
            self._context_metrics = ContextMetricsCollector()
        return self._context_metrics

    def _ensure_loop_affinity(self) -> None:
        loop = asyncio.get_running_loop()
        limit = self._max_in_flight()
        if (
            self._runtime_loop is loop
            and self._lock is not None
            and self._semaphore is not None
        ):
            if limit > self._semaphore_limit:
                for _ in range(limit - self._semaphore_limit):
                    self._semaphore.release()
            self._semaphore_limit = limit
            return
        if self._runtime_loop is not None and self._runtime_loop is not loop:
            self._clear_loop_bound_state()
        self._runtime_loop = loop
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(limit)
        self._semaphore_limit = limit
        self._semaphore_acquired = 0

    def _clear_loop_bound_state(self) -> None:
        for task in self._inflight.values():
            self._cancel_foreign_task(task)
        self._inflight.clear()
        self._provider_backoff.clear()
        self._semaphore = None
        self._semaphore_limit = 0
        self._semaphore_acquired = 0

    def _max_in_flight(self) -> int:
        try:
            value = int(getattr(self._config, "llm_max_in_flight", 2))
        except (TypeError, ValueError):
            value = 2
        return max(1, value)

    @staticmethod
    def _cancel_foreign_task(task: asyncio.Task[dict[str, Any]]) -> None:
        try:
            task_loop = task.get_loop()
        except Exception:
            logging.getLogger(__name__).warning(
                "galgame _cancel_foreign_task: get_loop failed",
                exc_info=True,
            )
            return
        if task.done():
            return
        try:
            if task_loop.is_closed():
                return
            task_loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            return

    async def shutdown(self) -> None:
        self._ensure_loop_affinity()
        async with self._lock:
            tasks = list(self._inflight.values())
            self._inflight.clear()
            self._cache.clear()
            self._near_match_cache.clear()
            self._provider_backoff.clear()
            self._repeat_guard.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._backend.shutdown()

    async def explain_line(self, context: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke_cached(
            operation="explain_line",
            context=context,
            validate=self._validate_explain_result,
            degraded=lambda diagnostic: build_explain_degraded_result(
                context,
                diagnostic=diagnostic,
            ),
        )

    async def summarize_scene(self, context: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke_cached(
            operation="summarize_scene",
            context=context,
            validate=self._validate_summarize_result,
            degraded=lambda diagnostic: build_summarize_degraded_result(
                context,
                diagnostic=diagnostic,
            ),
        )

    async def suggest_choice(self, context: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke_cached(
            operation="suggest_choice",
            context=context,
            validate=lambda raw: self._validate_suggest_result(raw, context=context),
            degraded=lambda diagnostic: build_suggest_degraded_result(
                context,
                diagnostic=diagnostic,
            ),
        )

    async def agent_reply(self, context: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke_cached(
            operation="agent_reply",
            context=context,
            validate=self._validate_agent_reply_result,
            degraded=lambda diagnostic: self._build_agent_reply_fallback(
                context,
                diagnostic=diagnostic,
            ),
        )

    async def _invoke_cached(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        self._ensure_loop_affinity()
        fingerprint = self._cache_fingerprint(
            operation,
            context,
            self._cache_config_fingerprint(),
        )
        provider_key = self._provider_backoff_key()
        now = time.monotonic()
        start_time = now
        wait_task: asyncio.Task[dict[str, Any]] | None = None
        cached_payload: dict[str, Any] | None = None
        cached_prompt_metadata: dict[str, Any] | None = None
        near_match_key = self._near_match_fingerprint(operation, context)
        near_match_meta = self._build_near_match_meta(context)

        async with self._lock:
            cached = self._cache.get(fingerprint)
            if cached is not None and cached[0] > now:
                self._cache.move_to_end(fingerprint)
                cached_payload = cached[1]
                cached_prompt_metadata = cached[2]
            elif cached is not None:
                self._cache.pop(fingerprint, None)

            if cached_payload is None:
                if near_match_key:
                    near_cached = self._near_match_cache.get(near_match_key)
                    if near_cached is not None and near_cached[0] > now:
                        if self._validate_near_match(
                            near_cached[3],
                            near_match_meta,
                        ):
                            self._near_match_cache.move_to_end(near_match_key)
                            cached_payload = near_cached[1]
                            cached_prompt_metadata = near_cached[2]
                    elif near_cached is not None:
                        self._near_match_cache.pop(near_match_key, None)

            if cached_payload is None:
                backoff = self._active_provider_backoff_locked(provider_key, now=now)
                if backoff is not None:
                    _category, diagnostic = backoff
                    return degraded(diagnostic)

                in_flight = self._inflight.get(fingerprint)
                if in_flight is not None:
                    wait_task = in_flight
                else:
                    wait_task = asyncio.create_task(
                        self._perform_call(
                            fingerprint=fingerprint,
                            provider_key=provider_key,
                            operation=operation,
                            context=context,
                            validate=validate,
                            degraded=degraded,
                        )
                    )
                    self._inflight[fingerprint] = wait_task

        if cached_payload is not None:
            self._record_context_metric(
                operation=operation,
                context=context,
                prompt_metadata=cached_prompt_metadata or {},
                cache_hit=True,
                total_time_ms=(time.monotonic() - start_time) * 1000.0,
            )
            return _json_payload_copy(cached_payload)

        try:
            return _json_payload_copy(await wait_task)
        except asyncio.CancelledError:
            return degraded("cancelled: llm request was cancelled")

    def _cache_config_fingerprint(self) -> str:
        mode = str(
            getattr(self._config, "context_counting_mode", "char") or "char"
        ).strip().lower()
        semantic_compression = bool(
            getattr(self._config, "context_semantic_compression", False)
        )
        if mode != "token":
            return _stable_json_fingerprint(
                {
                    "context_counting_mode": "char",
                    "context_semantic_compression": semantic_compression,
                }
            )
        try:
            budget = int(getattr(self._config, "context_max_tokens", 6000))
        except (TypeError, ValueError):
            budget = 6000
        return _stable_json_fingerprint(
            {
                "context_counting_mode": "token",
                "context_max_tokens": max(1, budget),
                "context_semantic_compression": semantic_compression,
            }
        )

    def _near_match_config_fingerprint(self) -> str:
        try:
            ttl = float(getattr(self._config, "llm_near_match_cache_ttl_seconds", 15.0))
        except (TypeError, ValueError):
            ttl = 15.0
        return _stable_json_fingerprint(
            {
                "llm_near_match_cache_enabled": bool(
                    getattr(self._config, "llm_near_match_cache_enabled", False)
                ),
                "llm_near_match_cache_ttl_seconds": max(0.0, ttl),
            }
        )

    def _repeat_config_fingerprint(self) -> str:
        raw_threshold = getattr(self._config, "llm_repeat_similarity_threshold", None)
        try:
            threshold = 0.85 if raw_threshold is None else float(raw_threshold)
        except (TypeError, ValueError):
            threshold = 0.85
        return _stable_json_fingerprint(
            {
                "llm_repeat_detection_enabled": bool(
                    getattr(self._config, "llm_repeat_detection_enabled", False)
                ),
                "llm_repeat_similarity_threshold": max(0.0, min(threshold, 1.0)),
            }
        )

    @staticmethod
    def _cache_fingerprint(
        operation: str,
        context: dict[str, Any],
        config_fingerprint: str = "",
    ) -> str:
        return (
            f"{operation}:{config_fingerprint}:"
            f"{_stable_json_fingerprint(context)}"
        )

    def _near_match_fingerprint(
        self,
        operation: str,
        context: dict[str, Any],
    ) -> str | None:
        if operation not in _NEAR_MATCH_SUPPORTED_OPERATIONS:
            return None
        if not bool(getattr(self._config, "llm_near_match_cache_enabled", False)):
            return None
        context_view = _near_match_context_value(context)
        return f"{operation}:{_stable_json_fingerprint(context_view)}"

    @staticmethod
    def _build_near_match_meta(context: dict[str, Any]) -> dict[str, Any]:
        observed_lines = _context_lines(context, "observed_lines")
        return {
            "stable_hash": _hash_stable_lines(_context_lines(context, "stable_lines")),
            "current_line_hash": _hash_line(_current_line_for_near_match(context)),
            "observed_signature": [
                _line_similarity_signature(item)
                for item in observed_lines
                if _line_similarity_signature(item)
            ],
        }

    @staticmethod
    def _validate_near_match(
        cached_meta: dict[str, Any],
        current_meta: dict[str, Any],
    ) -> bool:
        if not cached_meta or not current_meta:
            return False
        if str(cached_meta.get("stable_hash") or "") != str(
            current_meta.get("stable_hash") or ""
        ):
            return False
        if str(cached_meta.get("current_line_hash") or "") != str(
            current_meta.get("current_line_hash") or ""
        ):
            return False
        cached_observed = cached_meta.get("observed_signature")
        current_observed = current_meta.get("observed_signature")
        if not isinstance(cached_observed, list) or not isinstance(current_observed, list):
            return False
        return _observed_similarity(
            [str(item) for item in cached_observed],
            [str(item) for item in current_observed],
        ) >= _OBSERVED_SIMILARITY_THRESHOLD

    def _ttl_for_operation(self, operation: str) -> float:
        if operation == "explain_line":
            return self._safe_config_float("llm_explain_cache_ttl_seconds", 8.0)
        if operation in {"scene_summary", "summarize_scene"}:
            return self._safe_config_float("llm_scene_summary_cache_ttl_seconds", 10.0)
        if operation == "suggest_choice":
            return self._safe_config_float("llm_choice_cache_ttl_seconds", 4.0)
        return self._safe_config_float("llm_request_cache_ttl_seconds", 2.0)

    def _near_match_ttl(self) -> float:
        if not bool(getattr(self._config, "llm_near_match_cache_enabled", False)):
            return 0.0
        return self._safe_config_float("llm_near_match_cache_ttl_seconds", 15.0)

    def _safe_config_float(self, name: str, fallback: float) -> float:
        try:
            value = float(getattr(self._config, name, fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(0.0, value)

    async def _perform_call(
        self,
        *,
        fingerprint: str,
        provider_key: str,
        operation: str,
        context: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        prompt_metadata: dict[str, Any] = {}
        try:
            semaphore = self._semaphore
            if semaphore is None:
                self._ensure_loop_affinity()
                semaphore = self._semaphore
            if semaphore is None:
                raise RuntimeError("LLM gateway semaphore was not initialized")
            call_timeout = self._safe_config_float("llm_call_timeout_seconds", 30.0)
            deadline = time.monotonic() + call_timeout
            try:
                acquired = await self._acquire_call_slot(semaphore, deadline=deadline)
            except asyncio.TimeoutError:
                result = degraded(_LOCAL_QUEUE_TIMEOUT_DIAGNOSTIC)
            else:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    result = await asyncio.wait_for(
                        self._call_and_maybe_retry_repeated_response(
                            operation=operation,
                            context=context,
                            validate=validate,
                            degraded=degraded,
                        ),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    result = degraded("timeout: llm request exceeded llm_call_timeout_seconds")
                finally:
                    self._release_call_slot(acquired)
            prompt_metadata = self._consume_backend_prompt_metadata()
            total_time_ms = (time.monotonic() - start_time) * 1000.0
            ttl = self._ttl_for_operation(operation)
            near_match_ttl = self._near_match_ttl()
            near_match_key = self._near_match_fingerprint(operation, context)
            near_match_meta = self._build_near_match_meta(context)
            async with self._lock:
                self._update_provider_backoff_locked(
                    provider_key,
                    result,
                    now=time.monotonic(),
                )
                if ttl > 0 and not result.get("degraded"):
                    self._cache[fingerprint] = (
                        time.monotonic() + ttl,
                        _json_payload_copy(result),
                        dict(prompt_metadata),
                    )
                    self._cache.move_to_end(fingerprint)
                    while len(self._cache) > _LLM_RESPONSE_CACHE_MAX_ITEMS:
                        self._cache.popitem(last=False)
                if (
                    near_match_key
                    and near_match_ttl > 0
                    and not result.get("degraded")
                ):
                    self._near_match_cache[near_match_key] = (
                        time.monotonic() + near_match_ttl,
                        _json_payload_copy(result),
                        dict(prompt_metadata),
                        dict(near_match_meta),
                    )
                    self._near_match_cache.move_to_end(near_match_key)
                    while len(self._near_match_cache) > _LLM_NEAR_MATCH_CACHE_MAX_ITEMS:
                        self._near_match_cache.popitem(last=False)
            self._record_context_metric(
                operation=operation,
                context=context,
                prompt_metadata=prompt_metadata,
                cache_hit=False,
                total_time_ms=total_time_ms,
            )
            return result
        finally:
            async with self._lock:
                self._inflight.pop(fingerprint, None)

    async def _call_and_maybe_retry_repeated_response(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        result = await self._call_target(
            operation=operation,
            context=context,
            validate=validate,
            degraded=degraded,
        )
        return await self._maybe_retry_repeated_response(
            operation=operation,
            context=context,
            result=result,
            validate=validate,
            degraded=degraded,
        )

    async def _acquire_call_slot(
        self,
        semaphore: asyncio.Semaphore,
        *,
        deadline: float,
    ) -> asyncio.Semaphore:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("llm semaphore acquire timed out")
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise asyncio.TimeoutError("llm semaphore acquire timed out") from exc
            acquired_slot = False
            try:
                async with self._lock:
                    self._ensure_loop_affinity()
                    if self._semaphore is not semaphore:
                        continue
                    if self._semaphore_acquired < self._semaphore_limit:
                        self._semaphore_acquired += 1
                        acquired_slot = True
                        return semaphore
            finally:
                if not acquired_slot:
                    semaphore.release()
            await asyncio.sleep(0)

    def _release_call_slot(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore_acquired = max(0, self._semaphore_acquired - 1)
        semaphore.release()

    async def _maybe_retry_repeated_response(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        result: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        if operation != "agent_reply":
            return result
        if not bool(getattr(self._config, "llm_repeat_detection_enabled", False)):
            return result
        if bool(result.get("degraded")):
            return result
        try:
            threshold = float(getattr(self._config, "llm_repeat_similarity_threshold", 0.85))
        except (TypeError, ValueError):
            threshold = 0.85
        threshold = max(0.0, min(threshold, 1.0))
        if not self._repeat_guard.is_repeat(result, threshold=threshold):
            self._repeat_guard.record(result)
            return result

        retry_context = _json_payload_copy(context)
        retry_context["_anti_repeat_instruction"] = (
            "The previous agent reply was too similar to a recent reply. "
            "Answer using the current public_context, avoid repeating the same wording, "
            "and add only facts supported by context."
        )
        retry = await self._call_target(
            operation=operation,
            context=retry_context,
            validate=validate,
            degraded=degraded,
        )
        final = result if bool(retry.get("degraded")) else retry
        self._repeat_guard.record(final)
        return final

    def _consume_backend_prompt_metadata(self) -> dict[str, Any]:
        consume = getattr(self._backend, "consume_prompt_metadata", None)
        if not callable(consume):
            return {}
        try:
            metadata = consume()
        except Exception as exc:
            self._log_warning("galgame prompt metadata consume failed: {}", exc)
            return {}
        return dict(metadata) if isinstance(metadata, dict) else {}

    def _log_warning(self, message: str, *args: Any) -> None:
        logger = getattr(self, "_logger", None)
        warning = getattr(logger, "warning", None)
        if not callable(warning):
            return
        try:
            warning(message, *args)
        except Exception:
            logging.getLogger(__name__).warning(
                "galgame logger.warning failed",
                exc_info=True,
            )

    @staticmethod
    def _metadata_int(
        metadata: dict[str, Any],
        key: str,
        fallback: int | Callable[[], int],
    ) -> int:
        def fallback_value() -> int:
            return fallback() if callable(fallback) else fallback

        value = metadata.get(key)
        if value is None:
            return fallback_value()
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback_value()

    def _record_context_metric(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        prompt_metadata: dict[str, Any],
        cache_hit: bool,
        total_time_ms: float,
    ) -> None:
        collector = self._metrics_collector()
        if collector is None:
            return

        raw_text: str | None = None

        def rendered_context() -> str:
            nonlocal raw_text
            if raw_text is None:
                raw_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
            return raw_text

        raw_tokens = self._metadata_int(
            prompt_metadata,
            "raw_tokens",
            lambda: count_tokens_heuristic(rendered_context()),
        )
        raw_chars = self._metadata_int(
            prompt_metadata,
            "raw_chars",
            lambda: len(rendered_context()),
        )
        compacted_tokens = self._metadata_int(
            prompt_metadata,
            "compacted_tokens",
            raw_tokens,
        )
        compacted_chars = self._metadata_int(
            prompt_metadata,
            "compacted_chars",
            raw_chars,
        )
        compression_level = self._metadata_int(prompt_metadata, "compression_level", 0)
        collector.record(
            ContextMetric(
                operation=operation,
                raw_tokens=raw_tokens,
                compacted_tokens=compacted_tokens,
                raw_chars=raw_chars,
                compacted_chars=compacted_chars,
                compression_level=compression_level,
                cache_hit=cache_hit,
                total_time_ms=max(0.0, float(total_time_ms)),
            )
        )

    def _provider_backoff_key(self) -> str:
        target_entry_ref = str(self._config.llm_target_entry_ref or "").strip()
        return f"target:{target_entry_ref}" if target_entry_ref else "internal"

    def _active_provider_backoff_locked(
        self,
        provider_key: str,
        *,
        now: float,
    ) -> tuple[str, str] | None:
        active: tuple[str, str, float] | None = None
        expired_keys: list[tuple[str, str]] = []
        for key, (expires_at, diagnostic) in self._provider_backoff.items():
            key_provider, category = key
            if expires_at <= now:
                expired_keys.append(key)
                continue
            if key_provider != provider_key:
                continue
            if active is None or expires_at > active[2]:
                active = (category, diagnostic, expires_at)
        for key in expired_keys:
            self._provider_backoff.pop(key, None)
        if active is None:
            return None
        return active[0], active[1]

    def _update_provider_backoff_locked(
        self,
        provider_key: str,
        result: dict[str, Any],
        *,
        now: float,
    ) -> None:
        if not bool(result.get("degraded")):
            self._clear_provider_backoff_locked(provider_key)
            return
        diagnostic = str(result.get("diagnostic") or "").strip()
        if diagnostic == _LOCAL_QUEUE_TIMEOUT_DIAGNOSTIC:
            return
        category = self._provider_backoff_category(diagnostic)
        if category is None:
            return
        self._provider_backoff[(provider_key, category)] = (
            now + _LLM_PROVIDER_BACKOFF_SECONDS,
            diagnostic or category,
        )

    def _clear_provider_backoff_locked(self, provider_key: str) -> None:
        for key in list(self._provider_backoff):
            if key[0] == provider_key:
                self._provider_backoff.pop(key, None)

    @staticmethod
    def _provider_backoff_category(diagnostic: str) -> str | None:
        category = str(diagnostic or "").split(":", 1)[0].strip()
        if category in _LLM_PROVIDER_BACKOFF_CATEGORIES:
            return category
        return None

    async def _call_target(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        target_entry_ref = str(self._config.llm_target_entry_ref or "").strip()
        if not target_entry_ref:
            return await self._call_internal_backend(
                operation=operation,
                context=context,
                validate=validate,
                degraded=degraded,
            )

        try:
            response = await asyncio.wait_for(
                self._plugin.plugins.call_entry(
                    target_entry_ref,
                    params={"operation": operation, "context": context},
                    timeout=float(self._config.llm_call_timeout_seconds),
                ),
                timeout=float(self._config.llm_call_timeout_seconds) + 0.5,
            )
        except asyncio.TimeoutError:
            return degraded("timeout: llm target entry timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return degraded(self._normalize_plugin_error(exc))

        if isinstance(response, Err):
            return degraded(self._normalize_plugin_error(response.error))
        if not isinstance(response.value, dict):
            return degraded("invalid_result: target entry returned non-object payload")

        try:
            return validate(dict(response.value))
        except Exception as exc:
            return degraded(f"invalid_result: {exc}")

    async def _call_internal_backend(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        degraded: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                self._backend.invoke(
                    operation=operation,
                    context=context,
                ),
                timeout=float(self._config.llm_call_timeout_seconds) + 0.5,
            )
        except asyncio.TimeoutError:
            return degraded("timeout: internal llm backend timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return degraded(self._normalize_plugin_error(exc))

        if not isinstance(response, dict):
            return degraded("invalid_result: internal llm backend returned non-object payload")

        try:
            return validate(dict(response))
        except Exception as exc:
            return degraded(f"invalid_result: {exc}")

    @staticmethod
    def _normalize_plugin_error(error: object) -> str:
        message = str(error or "plugin call failed").strip() or "plugin call failed"
        category = LLMGateway._classify_plugin_error(error, message=message)
        if category == PluginErrorCategory.TIMEOUT:
            return f"timeout: {message}"
        if category == PluginErrorCategory.BUSY:
            return "busy: provider rate limited"
        if category == PluginErrorCategory.PROVIDER_REJECTED:
            return "gateway_unavailable: provider rejected request"
        if category == PluginErrorCategory.PROVIDER_UNAVAILABLE:
            return "gateway_unavailable: provider unavailable"
        if category == PluginErrorCategory.ENTRY_UNAVAILABLE:
            return f"gateway_unavailable: {message}"
        return f"internal_error: {message}"

    @staticmethod
    def _classify_plugin_error(error: object, *, message: str) -> PluginErrorCategory:
        status_code = LLMGateway._error_attr(error, "status_code", "status", "code")
        if status_code in {"408", "504"}:
            return PluginErrorCategory.TIMEOUT
        if status_code == "429":
            return PluginErrorCategory.BUSY
        if status_code in {"400", "401", "403"}:
            return PluginErrorCategory.PROVIDER_REJECTED
        if status_code in {"502", "503"}:
            return PluginErrorCategory.PROVIDER_UNAVAILABLE
        error_type = LLMGateway._error_attr(error, "type", "error_type", "kind")
        normalized_type = error_type.lower().replace("-", "_").replace(" ", "_")
        if normalized_type in {"timeout", "timed_out", "request_timeout"}:
            return PluginErrorCategory.TIMEOUT
        if normalized_type in {"rate_limit", "rate_limited", "too_many_requests"}:
            return PluginErrorCategory.BUSY
        if normalized_type in {"authentication_error", "permission_error", "invalid_request"}:
            return PluginErrorCategory.PROVIDER_REJECTED
        if normalized_type in {"service_unavailable", "network_error", "connection_error"}:
            return PluginErrorCategory.PROVIDER_UNAVAILABLE
        if normalized_type in {"not_found", "invalid_entry"}:
            return PluginErrorCategory.ENTRY_UNAVAILABLE
        lowered = message.lower()
        if "timeout" in lowered:
            return PluginErrorCategory.TIMEOUT
        if any(token in lowered for token in ("rate limit", "too many requests", "429")):
            return PluginErrorCategory.BUSY
        if any(
            token in lowered
            for token in (
                "not using lanlan",
                "stop abuse the api",
                "invalid request",
                "bad request",
                "unauthorized",
                "authentication",
                "forbidden",
                "api key",
                "access denied",
                "permission denied",
            )
        ):
            return PluginErrorCategory.PROVIDER_REJECTED
        if any(
            token in lowered
            for token in (
                "service unavailable",
                "temporarily unavailable",
                "connection refused",
                "connection reset",
                "connection aborted",
                "host unreachable",
                "name resolution",
                "dns",
                "network",
                "overloaded",
            )
        ):
            return PluginErrorCategory.PROVIDER_UNAVAILABLE
        if "not found" in lowered or "invalid entry" in lowered:
            return PluginErrorCategory.ENTRY_UNAVAILABLE
        return PluginErrorCategory.INTERNAL_ERROR

    @staticmethod
    def _error_attr(error: object, *names: str) -> str:
        if isinstance(error, Mapping):
            for name in names:
                value = error.get(name)
                if value is not None:
                    return str(value).strip()
            return ""
        for name in names:
            value = getattr(error, name, None)
            if value is not None:
                return str(value).strip()
        return ""

    @staticmethod
    def _validate_explain_result(raw: dict[str, Any]) -> dict[str, Any]:
        explanation = str(raw.get("explanation") or "").strip()
        evidence_obj = raw.get("evidence")
        if not explanation:
            raise ValueError("missing explanation")
        if not isinstance(evidence_obj, list):
            raise ValueError("evidence must be array")

        evidence: list[dict[str, Any]] = []
        for item in evidence_obj:
            if not isinstance(item, dict):
                raise ValueError("evidence item must be object")
            evidence_type = str(item.get("type") or "")
            text = str(item.get("text") or "")
            if evidence_type not in _EXPLAIN_EVIDENCE_TYPES or not text:
                raise ValueError("invalid evidence item")
            evidence.append(
                {
                    "type": evidence_type,
                    "text": text,
                    "line_id": str(item.get("line_id") or ""),
                    "speaker": str(item.get("speaker") or ""),
                    "scene_id": str(item.get("scene_id") or ""),
                    "route_id": str(item.get("route_id") or ""),
                }
            )

        return {
            "degraded": False,
            "explanation": explanation,
            "evidence": evidence,
            "diagnostic": "",
        }

    @staticmethod
    def _validate_summarize_result(raw: dict[str, Any]) -> dict[str, Any]:
        summary = str(raw.get("summary") or "").strip()
        key_points_obj = raw.get("key_points")
        if not summary:
            raise ValueError("missing summary")
        if not isinstance(key_points_obj, list):
            raise ValueError("key_points must be array")

        key_points: list[dict[str, Any]] = []
        for item in key_points_obj:
            if not isinstance(item, dict):
                raise ValueError("key_points item must be object")
            item_type = str(item.get("type") or "")
            text = str(item.get("text") or "")
            if item_type not in _KEY_POINT_TYPES or not text:
                raise ValueError("invalid key point item")
            key_points.append(
                {
                    "type": item_type,
                    "text": text,
                    "line_id": str(item.get("line_id") or ""),
                    "speaker": str(item.get("speaker") or ""),
                    "scene_id": str(item.get("scene_id") or ""),
                    "route_id": str(item.get("route_id") or ""),
                }
            )

        return {
            "degraded": False,
            "summary": summary,
            "key_points": key_points,
            "diagnostic": "",
        }

    @staticmethod
    def _validate_suggest_result(raw: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
        choices_obj = raw.get("choices")
        visible = {
            str(item.get("choice_id") or ""): dict(item)
            for item in context.get("visible_choices", [])
            if str(item.get("choice_id") or "")
        }
        if not isinstance(choices_obj, list):
            raise ValueError("choices must be array")

        normalized: list[dict[str, Any]] = []
        seen_choice_ids: set[str] = set()
        seen_ranks: set[int] = set()

        for item in choices_obj:
            if not isinstance(item, dict):
                raise ValueError("choice item must be object")
            choice_id = str(item.get("choice_id") or "")
            if not choice_id or choice_id not in visible:
                raise ValueError(f"unknown choice_id: {choice_id}")
            rank = int(item.get("rank") or 0)
            if rank < 1:
                raise ValueError("rank must be >= 1")
            if choice_id in seen_choice_ids or rank in seen_ranks:
                raise ValueError("duplicate choice rank or id")
            seen_choice_ids.add(choice_id)
            seen_ranks.add(rank)
            fallback_text = str(visible[choice_id].get("text") or "")
            text = str(item.get("text") or fallback_text).strip()
            reason = str(item.get("reason") or "").strip()
            if not text or not reason:
                raise ValueError("choice text/reason missing")
            normalized.append(
                {
                    "choice_id": choice_id,
                    "text": text,
                    "rank": rank,
                    "reason": reason,
                }
            )

        normalized.sort(key=lambda item: item["rank"])
        return {
            "degraded": False,
            "choices": normalized,
            "diagnostic": "",
        }

    @staticmethod
    def _validate_agent_reply_result(raw: dict[str, Any]) -> dict[str, Any]:
        reply = str(raw.get("reply") or raw.get("result") or "").strip()
        if not reply:
            raise ValueError("missing reply")
        return {
            "degraded": False,
            "reply": reply,
            "diagnostic": "",
        }

    @staticmethod
    def _build_agent_reply_fallback(
        context: dict[str, Any],
        *,
        diagnostic: str,
    ) -> dict[str, Any]:
        public_context = context.get("public_context")
        public_context = public_context if isinstance(public_context, dict) else {}
        scene_id = str(context.get("scene_id") or "")
        route_id = str(context.get("route_id") or "")
        latest_line = str(
            public_context.get("latest_line")
            or context.get("latest_line")
            or ""
        )
        recent_lines = public_context.get("recent_lines") or context.get("recent_lines")
        selected_choices = public_context.get("recent_choices") or context.get("recent_choices")
        current_line = public_context.get("current_line")
        snapshot = current_line if isinstance(current_line, dict) else context.get("current_snapshot", {})
        summary = build_local_scene_summary(
            scene_id=scene_id,
            route_id=route_id,
            lines=list(recent_lines) if isinstance(recent_lines, list) else [],
            selected_choices=list(selected_choices) if isinstance(selected_choices, list) else [],
            snapshot=snapshot,
        )
        if latest_line:
            reply = f"{summary} Current line: {latest_line}"
        else:
            reply = summary
        prompt = str(context.get("prompt") or "").strip()
        if prompt:
            reply = f"Received request \"{prompt}\". {reply}"
        return {
            "degraded": True,
            "reply": reply,
            "diagnostic": diagnostic,
        }
