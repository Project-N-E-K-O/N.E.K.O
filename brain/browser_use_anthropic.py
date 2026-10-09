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

"""browser-use ``ChatAnthropic`` adjusted for current Claude models.

browser-use 0.11's ``ChatAnthropic`` has two problems on current models:

* Structured output always sends ``tool_choice={"type": "tool"}``. Claude
  Sonnet 5.5 / Opus 5.5 / Fable 5.1 / Mythos 5.1 reject forced tool use with
  a 400 (only ``auto`` / ``none`` are accepted). Merely switching to ``auto``
  is not enough: browser-use's system prompt describes its actions like
  tools, and an unforced model sometimes calls an action (``find_elements``)
  as if it were a tool instead of the output tool.
* Plain-text completions read ``response.content[0]``. Models that think by
  default return a ``thinking`` block first, so the "text" becomes the repr
  of that block.

``NekoChatAnthropic.structured_output`` picks how structured output is asked
for:

* ``"forced_tool"``: browser-use's original forced tool call.
* ``"auto_tool"``: the output tool with ``tool_choice="auto"`` plus a
  system-prompt instruction that actions belong inside its input; only a
  call to that tool counts, otherwise a JSON object in the text reply.

Structured outputs (``output_config.format``) and ``strict`` tools are not
used: the agent output schema (a union of every action) is rejected with
"compiled grammar is too large".

This module imports browser-use at import time; load it lazily.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from anthropic import APIConnectionError, APIStatusError, RateLimitError, omit
from anthropic.types import CacheControlEphemeralParam, Message, ToolParam
from browser_use.llm import ChatAnthropic
from browser_use.llm.anthropic.serializer import AnthropicMessageSerializer
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

StructuredOutputMode = Literal["forced_tool", "auto_tool"]

# First Claude version per family whose API rejects forced tool_choice.
_FORCED_TOOL_CHOICE_REJECTED_FROM: dict[str, tuple[int, int]] = {
    "opus": (5, 5),
    "sonnet": (5, 5),
    "haiku": (5, 5),
    "fable": (5, 1),
    "mythos": (5, 1),
}
# Matches "claude-sonnet-5-5", "anthropic.claude-opus-5-5", "claude-fable-5.1";
# a dated snapshot suffix ("claude-sonnet-4-20250514") is not read as a minor.
_CLAUDE_MODEL_RE = re.compile(
    r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:[-.](\d{1,2}))?(?!\d)"
)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def anthropic_rejects_forced_tool_choice(model: str | None) -> bool:
    """Return True when the Claude model 400s on tool_choice ``any``/``tool``."""
    match = _CLAUDE_MODEL_RE.search(str(model or "").lower())
    if not match:
        return False
    family, major, minor = match.group(1), int(match.group(2)), int(match.group(3) or 0)
    return (major, minor) >= _FORCED_TOOL_CHOICE_REJECTED_FROM[family]


def _response_text(response: Message) -> str:
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _parse_json_object(text: str) -> Any:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Expected structured output in response but found no JSON object")
    return json.loads(candidate[start:end + 1])


def _append_system_instruction(
    system_prompt: list[Any] | str | None, instruction: str
) -> list[Any] | str:
    if not system_prompt:
        return instruction
    if isinstance(system_prompt, str):
        return f"{system_prompt}\n\n{instruction}"
    return [*system_prompt, {"type": "text", "text": instruction}]


def _output_schema(output_format: type[BaseModel]) -> dict[str, Any]:
    schema = SchemaOptimizer.create_optimized_json_schema(output_format)
    schema.pop("title", None)
    return schema


@dataclass
class NekoChatAnthropic(ChatAnthropic):
    """browser-use ChatAnthropic that can request structured output without forcing a tool."""

    structured_output: StructuredOutputMode = "forced_tool"

    def _check_response(self, response: Any) -> Message:
        if not isinstance(response, Message):
            raise ModelProviderError(
                message=(
                    f"Unexpected response type from Anthropic API: {type(response).__name__}. "
                    f"Response: {str(response)[:200]}"
                ),
                status_code=502,
                model=self.name,
            )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = (
                details.get("category") if isinstance(details, dict)
                else getattr(details, "category", None)
            )
            raise ModelProviderError(
                message=f"Model declined the request (stop_reason=refusal, category={category})",
                status_code=400,
                model=self.name,
            )
        return response

    async def _create(self, **params: Any) -> Message:
        return self._check_response(await self.get_client().messages.create(
            model=self.model,
            **params,
            **self._get_client_params_for_invoke(),
        ))

    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        if output_format is not None and self.structured_output == "forced_tool":
            return await super().ainvoke(messages, output_format, **kwargs)

        anthropic_messages, system_prompt = AnthropicMessageSerializer.serialize_messages(messages)
        try:
            if output_format is None:
                response = await self._create(
                    messages=anthropic_messages, system=system_prompt or omit,
                )
                return ChatInvokeCompletion(
                    completion=_response_text(response),
                    usage=self._get_usage(response),
                    stop_reason=response.stop_reason,
                )

            tool_name = output_format.__name__
            tool = ToolParam(
                name=tool_name,
                description=f"Extract information in the format of {tool_name}",
                input_schema=_output_schema(output_format),
                cache_control=CacheControlEphemeralParam(type="ephemeral"),
            )
            instruction = (
                f"`{tool_name}` is your only tool. Always respond by calling it exactly "
                "once with your complete answer as its input; anything else described "
                "above (such as actions) goes inside that input, not into separate tool "
                "calls. Do not answer in plain text."
            )
            response = await self._create(
                messages=anthropic_messages,
                tools=[tool],
                system=_append_system_instruction(system_prompt, instruction),
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            )
            tool_uses = [
                block for block in response.content if getattr(block, "type", None) == "tool_use"
            ]
            tool_inputs = [block.input for block in tool_uses if block.name == tool_name]
            if tool_inputs:
                data = tool_inputs[0]
                if isinstance(data, str):
                    data = json.loads(data)
            elif tool_uses and "{" not in _response_text(response):
                raise ValueError(
                    f"Model called {[block.name for block in tool_uses]} instead of `{tool_name}`"
                )
            else:
                data = _parse_json_object(_response_text(response))
            return ChatInvokeCompletion(
                completion=output_format.model_validate(data),
                usage=self._get_usage(response),
                stop_reason=response.stop_reason,
            )
        except ModelProviderError:
            raise
        except APIConnectionError as e:
            raise ModelProviderError(message=e.message, model=self.name) from e
        except RateLimitError as e:
            raise ModelRateLimitError(message=e.message, model=self.name) from e
        except APIStatusError as e:
            raise ModelProviderError(message=e.message, status_code=e.status_code, model=self.name) from e
        except Exception as e:
            raise ModelProviderError(message=str(e), model=self.name) from e
