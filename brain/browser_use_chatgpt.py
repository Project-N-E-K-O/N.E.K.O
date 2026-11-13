# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""browser-use contract backed by Neko's fixed-origin OAuth Responses client."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, TypeVar

from browser_use.llm import ChatOpenAI
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openai.serializer import OpenAIMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

from config import COMPUTER_USE_MAX_TOKENS, get_agent_extra_body
from utils.chatgpt_auth import ChatGPTAuthError
from utils.llm_client.chatgpt_client import ChatChatGPT, ChatGPTResponseError

T = TypeVar('T', bound=BaseModel)


@dataclass
class NekoChatChatGPT(ChatOpenAI):
    """Reuse browser-use serialization/schema handling, never its API-key transport."""

    credential_store: Any = field(default=None, repr=False)
    _terminal_error: Exception | None = field(default=None, init=False, repr=False)

    @property
    def provider(self) -> str:
        return 'chatgpt'

    def raise_terminal_error(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error

    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        self.raise_terminal_error()
        serialized = OpenAIMessageSerializer.serialize_messages(messages)
        params: dict[str, Any] = {}
        if output_format is not None:
            schema = SchemaOptimizer.create_optimized_json_schema(
                output_format,
                remove_min_items=self.remove_min_items_from_schema,
                remove_defaults=self.remove_defaults_from_schema,
            )
            if self.dont_force_structured_output:
                serialized = [
                    {'role': 'system', 'content': 'Return only JSON matching this schema: ' + json.dumps(schema)},
                    *serialized,
                ]
                params['response_format'] = {'type': 'json_object'}
            else:
                params['response_format'] = {
                    'type': 'json_schema',
                    'json_schema': {'name': 'agent_output', 'strict': True, 'schema': schema},
                }
        client = await asyncio.to_thread(
            ChatChatGPT, model=self.model, base_url=self.base_url,
            credential_store=self.credential_store,
            max_completion_tokens=self.max_completion_tokens or COMPUTER_USE_MAX_TOKENS,
            timeout=self.timeout or 65.0,
            extra_body=get_agent_extra_body(self.model, self.base_url or ''),
        )
        try:
            # browser-use owns the bounded history and image input budget.
            response = await client.ainvoke_raw(serialized, **params)  # noqa: LLM_INPUT_BUDGET
            choice = response.choices[0]
            text = choice.message.content or ''
            if output_format is not None and choice.finish_reason == 'length':
                raise ChatGPTResponseError('ChatGPT browser action output was truncated. Increase the output budget.')
            completion = output_format.model_validate_json(text) if output_format is not None else text
            return ChatInvokeCompletion(
                completion=completion, usage=self._get_usage(response), stop_reason=choice.finish_reason,
            )
        except (ChatGPTAuthError, ChatGPTResponseError) as exc:
            # browser-use catches model errors internally. Remember the original
            # terminal failure so its step callback stops schema/text fallbacks.
            self._terminal_error = exc
            raise ModelProviderError(message=str(exc), status_code=getattr(exc, 'status_code', None) or 400, model=self.name) from exc
        finally:
            await client.aclose()
