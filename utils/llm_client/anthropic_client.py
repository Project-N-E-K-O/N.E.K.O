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
"""Anthropic-compatible chat client and wire-format conversion helpers."""

from __future__ import annotations
import base64
import json as _json
import weakref
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import urlparse

if TYPE_CHECKING:
    pass

from .lifecycle import (
    _active_character, _close_async_openai_client_from_sync_best_effort,
    _close_chat_clients_best_effort, _substitute_character_placeholders,
)
from .messages import LLMResponse, LLMStreamChunk, _normalize_messages
from .thinking import strip_thinking_segments

Anthropic: Any = None

AsyncAnthropic: Any = None

def _parse_base_url(base_url: str | None):
    if not base_url:
        return None
    raw = str(base_url).strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"//{raw}"
    return urlparse(raw)

def _is_kimi_code_anthropic_base_url(base_url: str | None) -> bool:
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return False
    return (parsed.hostname or "").lower() == "api.kimi.com" and parsed.path.rstrip("/") == "/coding"

def _normalize_anthropic_sdk_base_url(base_url: str | None) -> str | None:
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return base_url
    if (parsed.hostname or "").lower() == "api.anthropic.com" and parsed.path.rstrip("/") == "/v1":
        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}"
    return base_url

def _is_anthropic_endpoint(base_url: str | None, provider_type: str | None = None) -> bool:
    """Detect endpoints that speak the Anthropic Messages API format."""
    if provider_type and str(provider_type).lower() == "anthropic":
        return True
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return False
    if (parsed.hostname or "").lower() == "api.anthropic.com":
        return True
    return _is_kimi_code_anthropic_base_url(base_url)

def _detect_image_media_type(image_bytes: bytes) -> str:
    """Guess image media type from magic bytes."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"GIF":
        return "image/gif"
    if image_bytes[:2] == b"BM":
        return "image/bmp"
    if image_bytes[:4] == b"RIFF" and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/jpeg"

def _coerce_anthropic_max_tokens(value: Any, *, default: int = 2048) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)

def _anthropic_usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        data = dict(usage)
    elif hasattr(usage, "model_dump"):
        data = usage.model_dump()
    else:
        data = {
            key: getattr(usage, key)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
            if hasattr(usage, key)
        }
    return {str(k): v for k, v in data.items() if v is not None}

def _merge_anthropic_usage(target: dict[str, Any], usage: Any) -> None:
    target.update(_anthropic_usage_to_dict(usage))

def _anthropic_usage_with_openai_aliases(usage: dict[str, Any]) -> dict[str, Any]:
    """Expose Anthropic usage under both native and shared token field names."""
    result = dict(usage)
    prompt_tokens = sum(
        int(result.get(key) or 0)
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    completion_tokens = int(result.get("output_tokens") or 0)
    result.setdefault("prompt_tokens", prompt_tokens)
    result.setdefault("completion_tokens", completion_tokens)
    result.setdefault("total_tokens", prompt_tokens + completion_tokens)
    return result

def _record_anthropic_token_usage(model: str, usage_dict: dict[str, Any]) -> None:
    if not usage_dict:
        return
    try:
        from utils.token_tracker import record_anthropic_usage
        record_anthropic_usage(model=model, usage=usage_dict)
    except Exception:
        # Usage telemetry must never make an otherwise valid response fail.
        pass

def _anthropic_stop_reason_to_finish_reason(stop_reason: Any) -> str | None:
    if not stop_reason:
        return None
    reason = str(stop_reason)
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
    }.get(reason, reason)

def _convert_openai_content_to_anthropic(content: Any) -> list[dict]:
    """Convert an OpenAI message content value to Anthropic content blocks."""
    blocks: list[dict] = []
    if content is None:
        return blocks
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
        return blocks
    if not isinstance(content, list):
        blocks.append({"type": "text", "text": str(content)})
        return blocks

    for part in content:
        if part is None:
            continue
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        elif part_type == "image_url":
            image_url_data = part.get("image_url", {}) or {}
            url = image_url_data.get("url", "") if isinstance(image_url_data, dict) else str(image_url_data)
            if url.startswith("data:"):
                try:
                    _, base64_data = url.split(",", 1)
                    image_bytes = base64.b64decode(base64_data)
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _detect_image_media_type(image_bytes),
                            "data": base64_data,
                        },
                    })
                except Exception:
                    blocks.append({"type": "text", "text": "[图片解析失败]"})
            elif url:
                blocks.append({"type": "text", "text": f"[图片: {url}]"})
        else:
            blocks.append(part)
    return blocks

def _anthropic_text_from_blocks(blocks: list[dict]) -> str:
    text_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "")
            if text:
                text_parts.append(text)
        elif block:
            text_parts.append(_json.dumps(block, ensure_ascii=False))
    return "\n".join(text_parts)

def _drop_unanswered_anthropic_tool_uses(messages: list[dict], tool_use_ids: set[str]) -> None:
    """Remove tool_use blocks that have no matching tool_result in history."""
    if not tool_use_ids:
        return
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        message["content"] = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and str(block.get("id") or "") in tool_use_ids
            )
        ] or [{"type": "text", "text": "..."}]

def _convert_openai_tool_call_to_anthropic(
    tool_call: Any,
    *,
    fallback_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        tool_call = {
            "id": getattr(tool_call, "id", ""),
            "type": getattr(tool_call, "type", ""),
            "function": getattr(tool_call, "function", None),
        }
    fn = tool_call.get("function")
    if fn is not None and not isinstance(fn, dict):
        fn = {
            "name": getattr(fn, "name", ""),
            "arguments": getattr(fn, "arguments", ""),
        }
    fn = fn if isinstance(fn, dict) else {}
    name = str(fn.get("name") or tool_call.get("name") or "").strip()
    if not name:
        return None
    raw_args = fn.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            parsed_args = _json.loads(raw_args) if raw_args.strip() else {}
        except Exception:
            parsed_args = {"arguments": raw_args}
    elif isinstance(raw_args, dict):
        parsed_args = raw_args
    else:
        parsed_args = {}
    tool_use_id = str(tool_call.get("id") or fallback_id or name)
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": parsed_args,
    }

def _convert_openai_tool_schema_to_anthropic(tool: Any) -> dict[str, Any] | None:
    """Convert an OpenAI Chat Completions tool definition to Anthropic schema."""
    if tool is None:
        return None
    if not isinstance(tool, dict):
        if hasattr(tool, "model_dump"):
            tool = tool.model_dump()
        else:
            tool = {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "input_schema": getattr(tool, "input_schema", None) or getattr(tool, "parameters", None),
            }

    fn = tool.get("function") if isinstance(tool, dict) else None
    if fn is not None and not isinstance(fn, dict):
        fn = {
            "name": getattr(fn, "name", ""),
            "description": getattr(fn, "description", ""),
            "parameters": getattr(fn, "parameters", None),
        }
    fn = fn if isinstance(fn, dict) else {}

    name = str(fn.get("name") or tool.get("name") or "").strip()
    if not name:
        return None

    input_schema = fn.get("parameters") or tool.get("input_schema") or tool.get("parameters")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    input_schema = dict(input_schema)
    input_schema.setdefault("type", "object")

    converted: dict[str, Any] = {
        "name": name,
        "input_schema": input_schema,
    }
    description = fn.get("description") or tool.get("description")
    if description:
        converted["description"] = str(description)
    return converted

def _convert_openai_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    if not tools:
        return []
    if isinstance(tools, dict):
        tools = [tools]
    converted = []
    for tool in tools:
        anthropic_tool = _convert_openai_tool_schema_to_anthropic(tool)
        if anthropic_tool:
            converted.append(anthropic_tool)
    return converted

def _convert_openai_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        choice = tool_choice.strip().lower()
        if choice in {"auto", "any", "none"}:
            return {"type": choice}
        if choice in {"required", "required_auto"}:
            return {"type": "any"}
        return None
    if not isinstance(tool_choice, dict):
        return None

    if isinstance(tool_choice.get("type"), str):
        choice_type = tool_choice["type"].strip().lower()
        if choice_type in {"auto", "any", "none"}:
            return {"type": choice_type}
        if choice_type == "tool" and tool_choice.get("name"):
            return {"type": "tool", "name": str(tool_choice["name"])}
        if choice_type == "function":
            fn = tool_choice.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                return {"type": "tool", "name": str(fn["name"])}
    return None

def _convert_openai_tool_result_to_anthropic(
    msg: dict,
    *,
    tool_use_id: str | None = None,
) -> dict[str, Any]:
    blocks = _convert_openai_content_to_anthropic(msg.get("content"))
    resolved_tool_use_id = str(
        tool_use_id
        or msg.get("tool_call_id")
        or msg.get("id")
        or msg.get("name")
        or "tool_result"
    )
    content: str | list[dict] = _anthropic_text_from_blocks(blocks)
    if any(isinstance(block, dict) and block.get("type") != "text" for block in blocks):
        content = blocks
    return {
        "type": "tool_result",
        "tool_use_id": resolved_tool_use_id,
        "content": content,
    }

def _convert_orphan_tool_result_to_user_blocks(msg: dict) -> list[dict]:
    blocks = _convert_openai_content_to_anthropic(msg.get("content"))
    text = _anthropic_text_from_blocks(blocks)
    if text:
        return [{"type": "text", "text": f"[tool result] {text}"}]
    return [{"type": "text", "text": "[tool result]"}]

def _normalize_messages_to_anthropic(messages: Any) -> tuple[str, list[dict]]:
    """Convert OpenAI-format messages to Anthropic (system, messages).

    Anthropic requires:
      - ``system`` as a top-level string (not a message).
      - ``messages`` alternating user/assistant only.
    """
    normalized = _normalize_messages(messages)
    system_parts: list[str] = []
    anthropic_messages: list[dict] = []
    pending_tool_use_ids: set[str] = set()
    pending_source_tool_use_ids: dict[str, list[str]] = {}
    pending_fallback_tool_uses: list[tuple[str, str]] = []
    emitted_tool_use_ids: set[str] = set()
    fallback_tool_use_seq = 0

    for msg in normalized:
        role = msg.get("role", "")
        content = msg.get("content", None)
        if role == "system":
            if content is None:
                continue
            if isinstance(content, str):
                system_parts.append(content)
            else:
                system_text = _anthropic_text_from_blocks(
                    _convert_openai_content_to_anthropic(content)
                )
                if system_text:
                    system_parts.append(system_text)
            continue
        if role == "tool":
            explicit_tool_use_id = msg.get("tool_call_id") or msg.get("id")
            if explicit_tool_use_id:
                source_tool_use_id = str(explicit_tool_use_id)
                effective_ids = pending_source_tool_use_ids.get(source_tool_use_id) or []
                tool_use_id = effective_ids.pop(0) if effective_ids else source_tool_use_id
                if not effective_ids:
                    pending_source_tool_use_ids.pop(source_tool_use_id, None)
            else:
                result_name = str(msg.get("name") or "")
                fallback_match = next(
                    (
                        item
                        for item in pending_fallback_tool_uses
                        if not result_name or item[1] == result_name
                    ),
                    None,
                )
                tool_use_id = (
                    fallback_match[0]
                    if fallback_match
                    else str(msg.get("name") or "tool_result")
                )
            if tool_use_id not in pending_tool_use_ids:
                anthropic_messages.append({
                    "role": "user",
                    "content": _convert_orphan_tool_result_to_user_blocks(msg),
                })
                continue
            pending_tool_use_ids.discard(tool_use_id)
            pending_fallback_tool_uses = [
                item for item in pending_fallback_tool_uses if item[0] != tool_use_id
            ]
            anthropic_messages.append({
                "role": "user",
                "content": [
                    _convert_openai_tool_result_to_anthropic(
                        msg,
                        tool_use_id=tool_use_id,
                    )
                ],
            })
            continue
        if role not in ("user", "assistant"):
            role = "user"
        blocks = _convert_openai_content_to_anthropic(content)
        if role == "assistant":
            seen_tool_use_ids: set[str] = set()
            deduped_blocks: list[dict] = []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    source_tool_use_id = str(block.get("id") or "")
                    tool_use_id = source_tool_use_id
                    if not tool_use_id:
                        fallback_tool_use_seq += 1
                        tool_use_id = f"toolu_fallback_{fallback_tool_use_seq}"
                        block = {**block, "id": tool_use_id}
                        pending_fallback_tool_uses.append(
                            (tool_use_id, str(block.get("name") or ""))
                        )
                    if tool_use_id in emitted_tool_use_ids:
                        fallback_tool_use_seq += 1
                        tool_use_id = f"toolu_fallback_{fallback_tool_use_seq}"
                        block = {**block, "id": tool_use_id}
                    if source_tool_use_id:
                        pending_source_tool_use_ids.setdefault(
                            source_tool_use_id,
                            [],
                        ).append(tool_use_id)
                    emitted_tool_use_ids.add(tool_use_id)
                    seen_tool_use_ids.add(tool_use_id)
                deduped_blocks.append(block)
            blocks = deduped_blocks
            for tool_call in msg.get("tool_calls") or []:
                raw_tool_call_id = (
                    tool_call.get("id")
                    if isinstance(tool_call, dict)
                    else getattr(tool_call, "id", "")
                )
                fallback_id = None
                if not raw_tool_call_id:
                    fallback_tool_use_seq += 1
                    fallback_id = f"toolu_fallback_{fallback_tool_use_seq}"
                converted = _convert_openai_tool_call_to_anthropic(
                    tool_call,
                    fallback_id=fallback_id,
                )
                converted_id = str(converted.get("id") or "") if converted else ""
                if converted and converted_id:
                    source_tool_use_id = str(raw_tool_call_id or "")
                    if converted_id in emitted_tool_use_ids:
                        fallback_tool_use_seq += 1
                        converted_id = f"toolu_fallback_{fallback_tool_use_seq}"
                        converted = {**converted, "id": converted_id}
                    blocks.append(converted)
                    emitted_tool_use_ids.add(converted_id)
                    seen_tool_use_ids.add(converted_id)
                    if source_tool_use_id:
                        pending_source_tool_use_ids.setdefault(
                            source_tool_use_id,
                            [],
                        ).append(converted_id)
                    else:
                        pending_fallback_tool_uses.append(
                            (converted_id, str(converted.get("name") or ""))
                        )
            pending_tool_use_ids |= seen_tool_use_ids
        elif role == "user":
            _drop_unanswered_anthropic_tool_uses(anthropic_messages, pending_tool_use_ids)
            pending_tool_use_ids.clear()
            pending_source_tool_use_ids.clear()
            pending_fallback_tool_uses.clear()
        anthropic_messages.append({"role": role, "content": blocks or [{"type": "text", "text": "..."}]})

    _drop_unanswered_anthropic_tool_uses(anthropic_messages, pending_tool_use_ids)

    # Enforce strict user/assistant alternation.
    fixed: list[dict] = []
    for msg in anthropic_messages:
        if not fixed:
            if msg["role"] != "user":
                fixed.append({"role": "user", "content": [{"type": "text", "text": "..."}]})
            fixed.append(msg)
            continue
        if msg["role"] == fixed[-1]["role"]:
            # Merge consecutive same-role turns into one content list.
            fixed[-1]["content"] = list(fixed[-1]["content"]) + list(msg["content"])
        else:
            fixed.append(msg)

    if not fixed:
        fixed.append({"role": "user", "content": [{"type": "text", "text": "..."}]})

    system = "\n".join(system_parts).strip()
    return system, fixed

_ANTHROPIC_BODY_OVERRIDE_KEYS = {
    "metadata",
    "stop_sequences",
    "system",
    "temperature",
    "thinking",
    "top_k",
    "top_p",
    "service_tier",
}

_ANTHROPIC_REQUEST_OPTION_KEYS = {"timeout", "extra_headers", "extra_query"}

def _sanitize_anthropic_metadata(metadata: Any) -> dict[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    user_id = metadata.get("user_id")
    if user_id is None or user_id == "":
        return None
    return {"user_id": str(user_id)}

def _apply_anthropic_body_fields(payload: dict[str, Any], fields: Any) -> None:
    if not isinstance(fields, dict):
        return
    for key, value in fields.items():
        if value is None:
            continue
        if key == "max_tokens":
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                value, default=int(payload.get("max_tokens") or 2048)
            )
        elif key == "max_completion_tokens":
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                value, default=int(payload.get("max_tokens") or 2048)
            )
        elif key == "metadata":
            metadata = _sanitize_anthropic_metadata(value)
            if metadata:
                payload["metadata"] = metadata
        elif key in _ANTHROPIC_BODY_OVERRIDE_KEYS:
            payload[key] = value

class ChatAnthropic:
    """Anthropic Messages API client with a ChatOpenAI-compatible surface.

    Used for Kimi Code (``https://api.kimi.com/coding``) and native Anthropic
    endpoints. Text chat, streaming, and OpenAI-style tool calling are adapted
    to the Anthropic Messages API schema.
    """

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = False,
        max_retries: int = 2,
        extra_body: dict | None = None,
        max_completion_tokens: int | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
        default_headers: dict | None = None,
        enable_cache_control: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        **_kwargs: Any,
    ):
        anthropic_cls, async_anthropic_cls = Anthropic, AsyncAnthropic
        if anthropic_cls is None or async_anthropic_cls is None:
            try:
                from anthropic import Anthropic as anthropic_cls, AsyncAnthropic as async_anthropic_cls
            except Exception as exc:
                raise RuntimeError("anthropic package is required for Anthropic-compatible providers") from exc
        self.model = model
        self.base_url = _normalize_anthropic_sdk_base_url(base_url)
        self.temperature = temperature
        self.extra_body = dict(extra_body) if extra_body else {}
        self._max_tokens = _coerce_anthropic_max_tokens(
            max_tokens if max_tokens is not None else max_completion_tokens
        )
        self.enable_cache_control = enable_cache_control
        self.tools = list(tools) if tools else None
        self.tool_choice = tool_choice

        _api_key = api_key or "sk-placeholder"
        _timeout = timeout or request_timeout
        client_kw: dict[str, Any] = dict(api_key=_api_key, max_retries=max_retries)
        if self.base_url:
            client_kw["base_url"] = self.base_url
        if _timeout is not None:
            client_kw["timeout"] = _timeout
        if default_headers:
            client_kw["default_headers"] = default_headers

        self._client = anthropic_cls(**client_kw)
        self._aclient = async_anthropic_cls(**client_kw)
        self._client_finalizer = weakref.finalize(
            self,
            _close_chat_clients_best_effort,
            self._client,
            self._aclient,
        )

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value: Any) -> None:
        self._max_tokens = _coerce_anthropic_max_tokens(value)

    @property
    def max_completion_tokens(self) -> int:
        return self._max_tokens

    @max_completion_tokens.setter
    def max_completion_tokens(self, value: Any) -> None:
        self._max_tokens = _coerce_anthropic_max_tokens(value)

    def _build_payload(self, messages: Any, *, include_default_extra_body: bool = True) -> dict:
        system, anthropic_messages = _normalize_messages_to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        payload["max_tokens"] = int(self.max_tokens)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if include_default_extra_body and self.extra_body:
            _apply_anthropic_body_fields(payload, self.extra_body)
        anthropic_tools = _convert_openai_tools_to_anthropic(self.tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            anthropic_tool_choice = _convert_openai_tool_choice_to_anthropic(self.tool_choice)
            if anthropic_tool_choice:
                payload["tool_choice"] = anthropic_tool_choice
        # Substitute character placeholders the same way ChatOpenAI does.
        active = _active_character.get()
        if active is not None and (active[0] or active[1]):
            payload["messages"] = _substitute_character_placeholders(
                payload["messages"], active[0], active[1]
            )
            if payload.get("system"):
                payload["system"] = _substitute_character_placeholders(
                    [{"role": "system", "content": payload["system"]}], active[0], active[1]
                )[0]["content"]
        return payload

    def _apply_overrides(self, payload: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        overrides = dict(overrides)
        max_tokens = overrides.pop("max_tokens", None)
        if max_tokens is None:
            max_tokens = overrides.pop("max_completion_tokens", None)
        else:
            overrides.pop("max_completion_tokens", None)
        if max_tokens is not None:
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                max_tokens, default=int(payload.get("max_tokens") or self.max_tokens)
            )
        overrides.pop("stream", None)
        extra_body = overrides.pop("extra_body", _SENTINEL)
        if extra_body:
            _apply_anthropic_body_fields(payload, extra_body)
        tools = overrides.pop("tools", _SENTINEL)
        if tools is not _SENTINEL:
            anthropic_tools = _convert_openai_tools_to_anthropic(tools)
            if anthropic_tools:
                payload["tools"] = anthropic_tools
            else:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
        tool_choice = overrides.pop("tool_choice", _SENTINEL)
        if tool_choice is not _SENTINEL:
            anthropic_tool_choice = _convert_openai_tool_choice_to_anthropic(tool_choice)
            if anthropic_tool_choice:
                payload["tool_choice"] = anthropic_tool_choice
            else:
                payload.pop("tool_choice", None)
        for key, value in overrides.items():
            if value is None:
                continue
            if key in _ANTHROPIC_REQUEST_OPTION_KEYS:
                payload[key] = value
            elif key in _ANTHROPIC_BODY_OVERRIDE_KEYS:
                _apply_anthropic_body_fields(payload, {key: value})
        return payload

    def _build_payload_for_call(self, messages: Any, overrides: dict[str, Any]) -> dict[str, Any]:
        include_default_extra_body = "extra_body" not in overrides
        payload = self._build_payload(
            messages,
            include_default_extra_body=include_default_extra_body,
        )
        return self._apply_overrides(payload, overrides)

    async def ainvoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        payload = self._build_payload_for_call(messages, overrides)
        resp = await self._aclient.messages.create(**payload)
        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        content = strip_thinking_segments("".join(text_parts))
        usage = getattr(resp, "usage", None)
        usage_dict = _anthropic_usage_to_dict(usage)
        _record_anthropic_token_usage(self.model, usage_dict)
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        payload = self._build_payload_for_call(messages, overrides)
        resp = self._client.messages.create(**payload)
        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        content = strip_thinking_segments("".join(text_parts))
        usage = getattr(resp, "usage", None)
        usage_dict = _anthropic_usage_to_dict(usage)
        _record_anthropic_token_usage(self.model, usage_dict)
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    async def astream(self, messages: Any, **overrides: Any) -> AsyncIterator[LLMStreamChunk]:
        payload = self._build_payload_for_call(messages, overrides)
        stream = self._aclient.messages.stream(**payload)
        usage_dict: dict[str, Any] = {}
        try:
            async with stream as response_stream:
                async for event in response_stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "message_start":
                        message = getattr(event, "message", None)
                        _merge_anthropic_usage(usage_dict, getattr(message, "usage", None))
                    elif event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", "") == "tool_use":
                            index = int(getattr(event, "index", 0) or 0)
                            raw_input = getattr(block, "input", None)
                            arguments = ""
                            if raw_input:
                                arguments = _json.dumps(raw_input, ensure_ascii=False)
                            yield LLMStreamChunk(
                                content="",
                                tool_call_deltas=[{
                                    "index": index,
                                    "id": getattr(block, "id", "") or "",
                                    "type": "function",
                                    "function": {
                                        "name": getattr(block, "name", "") or "",
                                        "arguments": arguments,
                                    },
                                }],
                            )
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta and getattr(delta, "type", "") == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield LLMStreamChunk(content=text)
                        elif delta and getattr(delta, "type", "") == "input_json_delta":
                            partial_json = getattr(delta, "partial_json", "") or ""
                            if partial_json:
                                yield LLMStreamChunk(
                                    content="",
                                    tool_call_deltas=[{
                                        "index": int(getattr(event, "index", 0) or 0),
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": partial_json},
                                    }],
                                )
                    elif event_type == "message_delta":
                        finish_reason = None
                        delta = getattr(event, "delta", None)
                        if delta:
                            finish_reason = getattr(delta, "stop_reason", None)
                        _merge_anthropic_usage(usage_dict, getattr(delta, "usage", None))
                        _merge_anthropic_usage(usage_dict, getattr(event, "usage", None))
                        if finish_reason:
                            mapped_reason = _anthropic_stop_reason_to_finish_reason(finish_reason)
                            yield LLMStreamChunk(content="", finish_reason=mapped_reason)
                    elif event_type == "message_stop":
                        message = getattr(event, "message", None)
                        _merge_anthropic_usage(usage_dict, getattr(message, "usage", None))
        finally:
            if usage_dict:
                _record_anthropic_token_usage(self.model, usage_dict)
        if usage_dict:
            shared_usage = _anthropic_usage_with_openai_aliases(usage_dict)
            yield LLMStreamChunk(
                content="",
                usage_metadata=shared_usage,
                response_metadata={"token_usage": shared_usage},
            )

    async def ainvoke_raw(self, messages: Any, **overrides: Any):
        """Return the raw Anthropic Message object."""
        payload = self._build_payload_for_call(messages, overrides)
        resp = await self._aclient.messages.create(**payload)
        _record_anthropic_token_usage(self.model, _anthropic_usage_to_dict(getattr(resp, "usage", None)))
        return resp

    def invoke_raw(self, messages: Any, **overrides: Any):
        """Return the raw Anthropic Message object (sync)."""
        payload = self._build_payload_for_call(messages, overrides)
        resp = self._client.messages.create(**payload)
        _record_anthropic_token_usage(self.model, _anthropic_usage_to_dict(getattr(resp, "usage", None)))
        return resp

    async def aclose(self) -> None:
        await self._aclient.close()
        self._client.close()
        self._client_finalizer.detach()

    def close(self) -> None:
        self._client.close()
        _close_async_openai_client_from_sync_best_effort(self._aclient)
        self._client_finalizer.detach()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

_SENTINEL = object()
