# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""ChatGPT plan Responses API adapter for Neko's chat-client contract."""

from __future__ import annotations

import asyncio
from typing import Any

from utils.chatgpt_auth import ChatGPTAuthError, get_store
from utils.chatgpt_auth.store import RESOURCE
from utils.http.url import same_endpoint
from .messages import LLMResponse, LLMStreamChunk
from .openai_client import ChatOpenAI


class ChatGPTResponseError(RuntimeError):
    """A terminal plan/inference failure; not eligible for automatic chat retries."""


def responses_input(messages):
    result = []
    for message in messages:
        role = message.get("role", "user")
        if role == "tool":
            content = message.get("content", "")
            if not isinstance(content, str):
                import json

                content = json.dumps(content, ensure_ascii=False)
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": content,
                }
            )
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content:
                result.append({"role": role, "content": content})
        elif content:
            parts = []
            for part in content:
                if part.get("type") == "text":
                    kind = "output_text" if role == "assistant" else "input_text"
                    parts.append({"type": kind, "text": part["text"]})
                elif part.get("type") == "image_url" and role == "user":
                    image = part["image_url"]
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": image["url"],
                            "detail": image.get("detail", "auto"),
                        }
                    )
                else:
                    raise ValueError(
                        "ChatGPT supports text and user image inputs in this integration."
                    )
            result.append({"role": role, "content": parts})
        seen_reasoning = set()
        for call in message.get("tool_calls") or []:
            # Preserve encrypted reasoning on the tool round via Neko's existing
            # per-call opaque metadata, without global conversation caches.
            extra = (call.get("extra_content") or {}).get("neko_chatgpt", {})
            for item in extra.get("reasoning", []):
                if item.get("id") not in seen_reasoning:
                    result.append(item)
                    seen_reasoning.add(item.get("id"))
            result.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                }
            )
    return result


def _dump(value):
    return value if isinstance(value, dict) else value.model_dump(exclude_none=True)


class ResponseState:
    def __init__(self):
        self.completed = False
        self.text = ""
        self.calls = []
        self.usage = {}
        self.id = ""

    def feed(self, event):
        event = _dump(event)
        kind = event.get("type")
        if kind in ("response.failed", "response.incomplete", "error"):
            response = event.get("response") or {}
            error = response.get("error") or event.get("error") or {}
            code = str(error.get("code") or event.get("code") or kind)
            # Only known codes are exposed; upstream messages can contain input.
            if code.startswith("subscription_sharing_"):
                raise ChatGPTResponseError(
                    "ChatGPT plan usage is unavailable or its limit was reached. Check ChatGPT Settings → Usage."
                )
            raise ChatGPTResponseError(
                "ChatGPT did not complete the response. Try again or check the selected model."
            )
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            delta = event.get("delta", "")
            self.text += delta
            return [LLMStreamChunk(content=delta)]
        if kind != "response.completed":
            return []
        response = event["response"]
        if response.get("status") != "completed":
            raise ChatGPTResponseError(
                "ChatGPT returned an unsuccessful terminal response."
            )
        self.completed = True
        self.id = response.get("id", "")
        usage = response.get("usage") or {}
        self.usage = {
            **usage,
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get(
                "total_tokens",
                usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            ),
        }
        output = response.get("output") or []
        reasoning = [
            item
            for item in output
            if item.get("type") == "reasoning" and item.get("encrypted_content")
        ]
        self.calls = [
            {
                "index": index,
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
                **(
                    {"extra_content": {"neko_chatgpt": {"reasoning": reasoning}}}
                    if reasoning
                    else {}
                ),
            }
            for index, item in enumerate(output)
            if item.get("type") == "function_call"
        ]
        return [
            LLMStreamChunk(
                content="",
                tool_call_deltas=self.calls or None,
                finish_reason="tool_calls" if self.calls else "stop",
                usage_metadata=self.usage,
            )
        ]

    def finish(self):
        if not self.completed:
            raise ChatGPTResponseError(
                "ChatGPT stream ended before completion. Please retry."
            )

    def raw(self, model):
        from openai.types.chat import ChatCompletion

        return ChatCompletion.model_validate(
            {
                "id": self.id,
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "usage": self.usage,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if self.calls else "stop",
                        "message": {
                            "role": "assistant",
                            "content": self.text,
                            "tool_calls": [
                                {k: v for k, v in call.items() if k != "index"}
                                for call in self.calls
                            ]
                            or None,
                        },
                    }
                ],
            }
        )


class ChatChatGPT(ChatOpenAI):
    """Fixed-origin OAuth client. API keys and arbitrary endpoint overrides never apply."""

    def __init__(self, *, model="", base_url=None, credential_store=None, **kwargs):
        if base_url and not same_endpoint(base_url, RESOURCE):
            raise ValueError(
                "ChatGPT plan credentials can only be used at https://api.openai.com/v1."
            )
        self.credential_store = credential_store or get_store()
        for key in (
            "api_key",
            "max_retries",
            "default_headers",
            "extra_body",
            "model_kwargs",
            "enable_cache_control",
        ):
            kwargs.pop(key, None)
        super().__init__(
            model=model,
            base_url=RESOURCE,
            api_key="oauth-managed",
            max_retries=0,
            **kwargs,
        )

    def _response_params(self, messages, **overrides):
        # Keep shared prompt processing (character placeholders, slop filter and
        # leak checks), then translate only the supported Responses fields.
        for key in ("extra_body", "stream", "store", "extra_headers", "extra_query"):
            overrides.pop(key, None)
        p = super()._params(messages, stream=True, **overrides)
        body = {
            "model": p["model"],
            "input": responses_input(p["messages"]),
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        limit = p.get("max_completion_tokens") or p.get("max_tokens")
        if limit:
            body["max_output_tokens"] = limit
        if p.get("tools"):
            if any(tool.get("type") != "function" for tool in p["tools"]):
                raise ValueError(
                    "Only function tools are supported by the ChatGPT adapter."
                )
            body["tools"] = [
                {"type": "function", "strict": False, **tool["function"]}
                for tool in p["tools"]
            ]
            choice = p.get("tool_choice")
            if isinstance(choice, dict):
                choice = {"type": "function", "name": choice["function"]["name"]}
            if choice is not None:
                body["tool_choice"] = choice
        response_format = p.get("response_format") or {}
        if response_format.get("type") == "json_object":
            body["text"] = {"format": response_format}
        elif response_format.get("type") == "json_schema":
            body["text"] = {
                "format": {"type": "json_schema", **response_format["json_schema"]}
            }
        if p.get("timeout") is not None:
            body["timeout"] = p["timeout"]
        return body

    async def _events(self, messages, **overrides):
        token = await asyncio.to_thread(self.credential_store.access_token)
        params = self._response_params(messages, **overrides)
        try:
            stream = await self._aclient.responses.create(
                **params, extra_headers={"Authorization": "Bearer " + token}
            )
        except Exception as exc:
            self._raise_auth_or_limit(exc)
            raise
        try:
            async for event in stream:
                yield event
        except Exception as exc:
            # The SDK can raise APIError for an SSE error before yielding it.
            self._raise_auth_or_limit(exc)
            raise
        finally:
            await stream.close()

    @staticmethod
    def _raise_auth_or_limit(exc):
        if str(getattr(exc, "code", "") or "").startswith("subscription_sharing_"):
            raise ChatGPTResponseError(
                "ChatGPT plan usage is unavailable or its limit was reached. Check ChatGPT Settings → Usage."
            ) from None
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        if status in (401, 403):
            raise ChatGPTAuthError(
                "ChatGPT authorization is unavailable. Continue with ChatGPT again."
            ) from None
        if status == 429:
            raise ChatGPTResponseError(
                "ChatGPT usage limit reached. Check ChatGPT Settings → Usage and retry later."
            ) from None

    async def astream(self, messages: Any, **overrides):
        state = ResponseState()
        events = self._events(messages, **overrides)
        try:
            async for event in events:
                for chunk in state.feed(event):
                    yield chunk
            state.finish()
        finally:
            await events.aclose()

    async def ainvoke_raw(self, messages, **overrides):
        state = ResponseState()
        events = self._events(messages, **overrides)
        try:
            async for event in events:
                state.feed(event)
            state.finish()
        finally:
            await events.aclose()
        return state.raw(self.model)

    def invoke_raw(self, messages, **overrides):
        token = self.credential_store.access_token()
        state = ResponseState()
        try:
            with self._client.responses.create(
                **self._response_params(messages, **overrides),
                extra_headers={"Authorization": "Bearer " + token},
            ) as stream:
                for event in stream:
                    state.feed(event)
        except Exception as exc:
            self._raise_auth_or_limit(exc)
            raise
        state.finish()
        return state.raw(self.model)

    @staticmethod
    def _response(raw):
        return LLMResponse(
            content=raw.choices[0].message.content or "",
            response_metadata={
                "model_name": raw.model,
                "finish_reason": raw.choices[0].finish_reason,
                "token_usage": raw.usage.model_dump() if raw.usage else {},
            },
        )

    async def ainvoke(self, messages, **overrides):
        return self._response(await self.ainvoke_raw(messages, **overrides))  # noqa: LLM_INPUT_BUDGET # Caller budgets are preserved by this adapter.

    def invoke(self, messages, **overrides):
        return self._response(self.invoke_raw(messages, **overrides))  # noqa: LLM_INPUT_BUDGET # Caller budgets are preserved by this adapter.

    async def alist_models(self, *, limit=2000):
        token = await asyncio.to_thread(self.credential_store.access_token)
        from utils.chatgpt_auth.store import http_client

        def fetch():
            with http_client() as client:
                response = client.get(
                    RESOURCE + "/models", headers={"Authorization": "Bearer " + token}
                )
                response.raise_for_status()
                return [
                    {"id": item["slug"], "name": item.get("display_name", item["slug"])}
                    for item in response.json().get("models", [])
                    if item.get("visibility") == "list"
                    and item.get("supported_in_api", True)
                ][:limit]

        try:
            return await asyncio.to_thread(fetch)
        except Exception as exc:
            self._raise_auth_or_limit(exc)
            raise
