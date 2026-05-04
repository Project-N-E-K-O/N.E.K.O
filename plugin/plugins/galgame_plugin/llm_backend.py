from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Protocol

from plugin.sdk.plugin import SdkError

from .llm_prompts import build_prompt_messages
from utils.config_manager import get_config_manager
from utils.file_utils import robust_json_loads
from utils.llm_client import ChatOpenAI, create_chat_llm
from utils.token_tracker import set_call_type

_ALLOWED_OPERATIONS = frozenset(
    {"explain_line", "summarize_scene", "suggest_choice", "agent_reply"}
)
_EXPLAIN_EVIDENCE_TYPES = frozenset({"current_line", "history_line", "choice"})
_KEY_POINT_TYPES = frozenset({"plot", "emotion", "decision", "reveal", "objective"})
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)
_JSON_CORRECTION_MAX_ATTEMPTS = 1
_JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS = 12000
_JSON_CORRECTION_ERROR_MAX_CHARS = 600
_LLM_CALL_MAX_ATTEMPTS = 3
_LLM_CALL_RETRY_BASE_DELAY_SECONDS = 0.25


class LoggerLike(Protocol):
    def debug(self, *args: Any, **kwargs: Any) -> Any: ...

    def warning(self, *args: Any, **kwargs: Any) -> Any: ...

    def error(self, *args: Any, **kwargs: Any) -> Any: ...


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        return _CODE_FENCE_RE.sub("", text).strip()
    return text


def _bounded_prompt_text(value: object, *, max_chars: int) -> str:
    text = _as_str(value, str(value))
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n...[truncated {omitted} chars]"


def _api_key_cache_fingerprint(api_key: str) -> str:
    if not api_key:
        return ""
    return f"k:{hash(api_key) & 0xFFFFFFFF:08x}"


def _model_supports_vision(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "gpt-4o",
            "gpt-4.1",
            "gpt-4.5",
            "gpt-5",
            "vision",
            "vl",
            "qwen2.5-vl",
            "qwen-vl",
            "gemini",
            "claude-3",
            "claude-4",
        )
    )


def _message_has_image_content(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == "image_url" for item in content)


def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stripped: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            stripped.append(dict(message))
            continue
        text_parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        next_message = dict(message)
        next_message["content"] = "\n".join(part for part in text_parts if part).strip()
        stripped.append(next_message)
    return stripped


def _attach_vision_image_if_requested(
    messages: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    if not bool(context.get("vision_enabled")):
        return messages
    image_base64 = str(
        context.get("vision_image_base64") or context.get("screen_image_base64") or ""
    ).strip()
    if not image_base64:
        return messages
    image_url = (
        image_base64
        if image_base64.startswith("data:image/")
        else f"data:image/png;base64,{image_base64}"
    )
    detail = str(context.get("vision_detail") or "low").strip().lower()
    if detail not in {"low", "high", "auto"}:
        detail = "low"
    result = [dict(message) for message in messages]
    for index in range(len(result) - 1, -1, -1):
        if str(result[index].get("role") or "") != "user":
            continue
        result[index]["content"] = [
            {"type": "text", "text": str(result[index].get("content") or "")},
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                    "detail": detail,
                },
            },
        ]
        return result
    return result


class GalgameLLMBackend:
    def __init__(self, logger: LoggerLike, config: Any = None) -> None:
        self._logger = logger
        self._config = config
        self._llm_cache: dict[tuple[Any, ...], ChatOpenAI] = {}
        self._llm_cache_loop: asyncio.AbstractEventLoop | None = None
        self._llm_cache_lock: asyncio.Lock | None = None

    def _ensure_loop_affinity(self) -> None:
        loop = asyncio.get_running_loop()
        if self._llm_cache_loop is loop and self._llm_cache_lock is not None:
            return
        if self._llm_cache_loop is not None and self._llm_cache_loop is not loop:
            self._drop_loop_bound_cache(previous_loop=self._llm_cache_loop)
        self._llm_cache_loop = loop
        self._llm_cache_lock = asyncio.Lock()

    def _cache_lock(self) -> asyncio.Lock:
        self._ensure_loop_affinity()
        assert self._llm_cache_lock is not None
        return self._llm_cache_lock

    def _drop_loop_bound_cache(
        self,
        *,
        previous_loop: asyncio.AbstractEventLoop,
    ) -> None:
        llms = list(self._llm_cache.values())
        self._llm_cache.clear()
        if not llms:
            return
        if previous_loop.is_closed():
            for llm in llms:
                close = getattr(llm, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            return
        for llm in llms:
            try:
                previous_loop.call_soon_threadsafe(
                    lambda current=llm: previous_loop.create_task(
                        self._close_llm_client(current, reason="loop switch")
                    )
                )
            except RuntimeError:
                close = getattr(llm, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

    async def _close_llm_client(self, llm: ChatOpenAI, *, reason: str) -> None:
        try:
            await llm.aclose()
        except Exception as exc:
            try:
                self._logger.warning(
                    "galgame LLM client close failed during {}: {}",
                    reason,
                    exc,
                )
            except Exception:
                pass

    async def shutdown(self) -> None:
        async with self._cache_lock():
            llms = list(self._llm_cache.values())
            self._llm_cache.clear()
        for llm in llms:
            await self._close_llm_client(llm, reason="shutdown")

    async def invoke(
        self,
        *,
        operation: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if operation not in _ALLOWED_OPERATIONS:
            raise SdkError(f"unsupported operation: {operation!r}")
        if not isinstance(context, dict):
            raise SdkError("context must be an object")
        return await self._invoke_operation(operation, dict(context))

    async def _invoke_operation(
        self,
        operation: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        messages = _attach_vision_image_if_requested(
            self._build_messages(operation, context),
            context,
        )
        raw_text = await self._invoke_json_with_correction(
            operation=operation,
            messages=messages,
        )
        parsed = self._parse_json_object(raw_text)
        return self._normalize_result(operation, parsed, context)

    async def _invoke_json_with_correction(
        self,
        *,
        operation: str,
        messages: list[dict[str, Any]],
    ) -> str:
        raw_text = await self._call_model(
            operation=operation,
            messages=messages,
        )
        last_error: SdkError | None = None
        for attempt in range(_JSON_CORRECTION_MAX_ATTEMPTS + 1):
            try:
                self._parse_json_object(raw_text)
                return raw_text
            except SdkError as exc:
                last_error = exc
                if attempt >= _JSON_CORRECTION_MAX_ATTEMPTS:
                    break

            correction_messages = self._build_json_correction_messages(
                operation=operation,
                messages=messages,
                bad_output=raw_text,
                parse_error=last_error,
                attempt=attempt + 1,
                max_attempts=_JSON_CORRECTION_MAX_ATTEMPTS,
            )
            raw_text = await self._call_model(
                operation=operation,
                messages=correction_messages,
            )

        raise SdkError(
            "llm result is not valid json object after "
            f"{_JSON_CORRECTION_MAX_ATTEMPTS} correction attempt(s): {last_error}"
        )

    def _build_json_correction_messages(
        self,
        *,
        operation: str,
        messages: list[dict[str, Any]],
        bad_output: object,
        parse_error: object,
        attempt: int,
        max_attempts: int,
    ) -> list[dict[str, Any]]:
        if operation not in _ALLOWED_OPERATIONS:
            raise SdkError(f"unsupported operation: {operation!r}")
        bounded_bad_output = _bounded_prompt_text(
            bad_output,
            max_chars=_JSON_CORRECTION_BAD_OUTPUT_MAX_CHARS,
        )
        bounded_error = _bounded_prompt_text(
            parse_error,
            max_chars=_JSON_CORRECTION_ERROR_MAX_CHARS,
        )
        correction_messages = list(messages)
        correction_messages.append({"role": "assistant", "content": bounded_bad_output})
        correction_messages.append(
            {
                "role": "user",
                "content": (
                    f"JSON 修正请求 {attempt}/{max_attempts}，operation={operation}。\n"
                    f"解析错误：{bounded_error}\n"
                    "你上一条回复不是合法 JSON 对象。请只返回一个合法 JSON 对象，"
                    "不要带 Markdown、解释或额外文本。"
                ),
            }
        )
        return correction_messages

    async def _call_model(
        self,
        *,
        operation: str,
        messages: list[dict[str, Any]],
    ) -> str:
        model_role = "agent" if operation == "agent_reply" else "summary"
        api_config = get_config_manager().get_model_api_config(model_role)
        base_url = _as_str(api_config.get("base_url")).strip()
        model = _as_str(api_config.get("model")).strip()
        api_key = _as_str(api_config.get("api_key")).strip()
        if not base_url or not model:
            raise SdkError(f"missing configured {model_role} model")
        if any(_message_has_image_content(message) for message in messages) and not _model_supports_vision(model):
            messages = _strip_image_content(messages)

        cfg = self._config
        if operation == "agent_reply":
            temperature = float(getattr(cfg, "llm_temperature_agent_reply", 0.2) if cfg else 0.2)
            max_completion_tokens = int(getattr(cfg, "llm_max_tokens_agent_reply", 900) if cfg else 900)
        else:
            temperature = float(getattr(cfg, "llm_temperature_default", 0.0) if cfg else 0.0)
            max_completion_tokens = int(getattr(cfg, "llm_max_tokens_default", 1200) if cfg else 1200)
        cache_key = (
            model_role,
            base_url,
            _api_key_cache_fingerprint(api_key),
            model,
            temperature,
            max_completion_tokens,
        )
        llm = await self._get_or_create_llm(
            cache_key=cache_key,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
        )
        return await self._invoke_llm_with_retry(
            model_role=model_role,
            llm=llm,
            messages=messages,
        )

    async def _invoke_llm_with_retry(
        self,
        *,
        model_role: str,
        llm: ChatOpenAI,
        messages: list[dict[str, Any]],
    ) -> str:
        ainvoke = getattr(llm, "ainvoke", None)
        last_exc: Exception | None = None
        for attempt in range(_LLM_CALL_MAX_ATTEMPTS):
            try:
                set_call_type("agent" if model_role == "agent" else "summary")
                if callable(ainvoke):
                    response = await ainvoke(messages)
                else:
                    response = await asyncio.to_thread(llm.invoke, messages)
                return _as_str(getattr(response, "content", ""), str(response))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= _LLM_CALL_MAX_ATTEMPTS - 1:
                    raise
                delay = _LLM_CALL_RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                try:
                    self._logger.warning(
                        "galgame LLM {} call failed; retrying {}/{} after {:.2f}s: {}",
                        model_role,
                        attempt + 1,
                        _LLM_CALL_MAX_ATTEMPTS,
                        delay,
                        exc,
                    )
                except Exception:
                    pass
                await asyncio.sleep(delay)
        raise last_exc or RuntimeError("galgame LLM call failed")

    async def _get_or_create_llm(
        self,
        *,
        cache_key: tuple[Any, ...],
        model: str,
        base_url: str,
        api_key: str,
        temperature: float,
        max_completion_tokens: int,
    ) -> ChatOpenAI:
        async with self._cache_lock():
            cached = self._llm_cache.get(cache_key)
            if cached is not None:
                return cached
            llm = create_chat_llm(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                timeout=30.0,
            )
            self._llm_cache[cache_key] = llm
            return llm

    def _build_messages(
        self,
        operation: str,
        context: dict[str, Any],
    ) -> list[dict[str, str]]:
        return build_prompt_messages(operation, context)

    def _parse_json_object(self, raw_text: str) -> dict[str, Any]:
        text = _strip_code_fences(raw_text)
        try:
            parsed = robust_json_loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise SdkError("llm result is not valid json object")
            try:
                parsed = robust_json_loads(match.group(0))
            except Exception as exc:
                raise SdkError(f"llm result is not valid json object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SdkError("llm result must be a json object")
        return dict(parsed)

    def _normalize_result(
        self,
        operation: str,
        raw: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "explain_line":
            return self._normalize_explain(raw)
        if operation == "summarize_scene":
            return self._normalize_summarize(raw)
        if operation == "suggest_choice":
            return self._normalize_suggest(raw, context)
        return self._normalize_agent_reply(raw)

    def _normalize_explain(self, raw: dict[str, Any]) -> dict[str, Any]:
        explanation = _as_str(raw.get("explanation")).strip()
        if not explanation:
            raise SdkError("missing explanation")
        evidence_items = raw.get("evidence")
        if not isinstance(evidence_items, list):
            raise SdkError("evidence must be array")

        evidence: list[dict[str, Any]] = []
        for item in evidence_items:
            current = _as_dict(item)
            evidence_type = _as_str(current.get("type")).strip()
            text = _as_str(current.get("text")).strip()
            if evidence_type not in _EXPLAIN_EVIDENCE_TYPES or not text:
                continue
            evidence.append(
                {
                    "type": evidence_type,
                    "text": text,
                    "line_id": _as_str(current.get("line_id")),
                    "speaker": _as_str(current.get("speaker")),
                    "scene_id": _as_str(current.get("scene_id")),
                    "route_id": _as_str(current.get("route_id")),
                }
            )
        return {"explanation": explanation, "evidence": evidence}

    def _normalize_summarize(self, raw: dict[str, Any]) -> dict[str, Any]:
        summary = _as_str(raw.get("summary")).strip()
        if not summary:
            raise SdkError("missing summary")
        key_points_obj = raw.get("key_points")
        if not isinstance(key_points_obj, list):
            raise SdkError("key_points must be array")

        key_points: list[dict[str, Any]] = []
        for item in key_points_obj:
            current = _as_dict(item)
            item_type = _as_str(current.get("type")).strip()
            text = _as_str(current.get("text")).strip()
            if item_type not in _KEY_POINT_TYPES or not text:
                continue
            key_points.append(
                {
                    "type": item_type,
                    "text": text,
                    "line_id": _as_str(current.get("line_id")),
                    "speaker": _as_str(current.get("speaker")),
                    "scene_id": _as_str(current.get("scene_id")),
                    "route_id": _as_str(current.get("route_id")),
                }
            )
        return {"summary": summary, "key_points": key_points}

    def _normalize_suggest(
        self,
        raw: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        visible_choices = {
            _as_str(item.get("choice_id")).strip(): dict(item)
            for item in context.get("visible_choices", [])
            if isinstance(item, dict) and _as_str(item.get("choice_id")).strip()
        }
        raw_choices = raw.get("choices")
        if not isinstance(raw_choices, list):
            raise SdkError("choices must be array")

        preliminary: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(raw_choices):
            current = _as_dict(item)
            choice_id = _as_str(current.get("choice_id")).strip()
            if choice_id not in visible_choices:
                continue
            reason = _as_str(current.get("reason")).strip()
            if not reason:
                continue
            text = _as_str(current.get("text")).strip() or _as_str(
                visible_choices[choice_id].get("text")
            ).strip()
            if not text:
                continue
            try:
                rank = int(current.get("rank") or index + 1)
            except (TypeError, ValueError):
                rank = index + 1
            preliminary.append(
                (
                    max(1, rank),
                    index,
                    {
                        "choice_id": choice_id,
                        "text": text,
                        "reason": reason,
                    },
                )
            )

        preliminary.sort(key=lambda item: (item[0], item[1]))
        choices: list[dict[str, Any]] = []
        seen_choice_ids: set[str] = set()
        for original_rank, _, item in preliminary:
            choice_id = _as_str(item.get("choice_id"))
            if choice_id in seen_choice_ids:
                continue
            seen_choice_ids.add(choice_id)
            choices.append(
                {
                    "choice_id": choice_id,
                    "text": _as_str(item.get("text")),
                    "rank": original_rank,
                    "reason": _as_str(item.get("reason")),
                }
            )

        if visible_choices and not choices:
            raise SdkError("model returned no valid visible choice suggestions")
        return {"choices": choices}

    def _normalize_agent_reply(self, raw: dict[str, Any]) -> dict[str, Any]:
        reply = _as_str(raw.get("reply")).strip() or _as_str(raw.get("result")).strip()
        if not reply:
            raise SdkError("missing reply")
        return {"reply": reply}
