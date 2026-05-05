from __future__ import annotations

import asyncio
from collections import OrderedDict
from enum import Enum
import json
import logging
import time
from typing import Any, Callable, Mapping

from plugin.sdk.shared.models import Err

from .llm_backend import GalgameLLMBackend
from .models import json_copy
from .service import (
    build_explain_degraded_result,
    build_local_scene_summary,
    build_suggest_degraded_result,
    build_summarize_degraded_result,
)

_EXPLAIN_EVIDENCE_TYPES = frozenset({"current_line", "history_line", "choice"})
_KEY_POINT_TYPES = frozenset({"plot", "emotion", "decision", "reveal", "objective"})
_LLM_RESPONSE_CACHE_MAX_ITEMS = 50
_LLM_PROVIDER_BACKOFF_SECONDS = 2.0
_LLM_PROVIDER_BACKOFF_CATEGORIES = frozenset({"busy", "gateway_unavailable", "timeout"})


class PluginErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    BUSY = "busy"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ENTRY_UNAVAILABLE = "entry_unavailable"
    INTERNAL_ERROR = "internal_error"


def _json_payload_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return json_copy(value)


def _stable_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_stable_json_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return {"__non_json_type__": f"{type(value).__module__}.{type(value).__qualname__}"}


def _stable_json_fingerprint(value: Any) -> str:
    return json.dumps(
        _stable_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class LLMGateway:
    def __init__(self, plugin, logger, config, *, backend: GalgameLLMBackend | None = None) -> None:
        self._plugin = plugin
        self._logger = logger
        self._config = config
        self._backend = backend or GalgameLLMBackend(logger, config)
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._provider_backoff: dict[tuple[str, str], tuple[float, str]] = {}
        self._active_calls = 0

    def update_config(self, config) -> None:
        self._config = config
        if hasattr(self._backend, "_config"):
            self._backend._config = config

    def _ensure_loop_affinity(self) -> None:
        loop = asyncio.get_running_loop()
        if self._runtime_loop is loop and self._lock is not None:
            return
        if self._runtime_loop is not None and self._runtime_loop is not loop:
            self._clear_loop_bound_state()
        self._runtime_loop = loop
        self._lock = asyncio.Lock()

    def _clear_loop_bound_state(self) -> None:
        for task in self._inflight.values():
            self._cancel_foreign_task(task)
        self._inflight.clear()
        self._provider_backoff.clear()
        self._active_calls = 0

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
            self._provider_backoff.clear()
            self._active_calls = 0
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
        fingerprint = self._cache_fingerprint(operation, context)
        provider_key = self._provider_backoff_key()
        now = time.monotonic()
        wait_task: asyncio.Task[dict[str, Any]] | None = None

        async with self._lock:
            cached = self._cache.get(fingerprint)
            if cached is not None and cached[0] > now:
                self._cache.move_to_end(fingerprint)
                return _json_payload_copy(cached[1])
            if cached is not None:
                self._cache.pop(fingerprint, None)

            backoff = self._active_provider_backoff_locked(provider_key, now=now)
            if backoff is not None:
                _category, diagnostic = backoff
                return degraded(diagnostic)

            in_flight = self._inflight.get(fingerprint)
            if in_flight is not None:
                wait_task = in_flight
            else:
                if self._active_calls >= int(self._config.llm_max_in_flight):
                    return degraded("busy: throttled by llm_max_in_flight")

                self._active_calls += 1
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

        try:
            return _json_payload_copy(await wait_task)
        except asyncio.CancelledError:
            return degraded("cancelled: llm request was cancelled")

    @staticmethod
    def _cache_fingerprint(operation: str, context: dict[str, Any]) -> str:
        return f"{operation}:{_stable_json_fingerprint(context)}"

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
        try:
            result = await self._call_target(
                operation=operation,
                context=context,
                validate=validate,
                degraded=degraded,
            )
            if operation in {"scene_summary", "summarize_scene"}:
                ttl = max(
                    0.0,
                    float(
                        getattr(
                            self._config,
                            "llm_scene_summary_cache_ttl_seconds",
                            self._config.llm_request_cache_ttl_seconds,
                        )
                    ),
                )
            else:
                ttl = max(0.0, float(self._config.llm_request_cache_ttl_seconds))
            async with self._lock:
                self._update_provider_backoff_locked(
                    provider_key,
                    result,
                    now=time.monotonic(),
                )
                if ttl > 0 and not result.get("degraded"):
                    self._cache[fingerprint] = (time.monotonic() + ttl, _json_payload_copy(result))
                    self._cache.move_to_end(fingerprint)
                    while len(self._cache) > _LLM_RESPONSE_CACHE_MAX_ITEMS:
                        self._cache.popitem(last=False)
            return result
        finally:
            async with self._lock:
                self._inflight.pop(fingerprint, None)
                self._active_calls = max(0, self._active_calls - 1)

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
