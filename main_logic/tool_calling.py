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

"""Unified tool calling primitives shared by ``OmniOfflineClient``,
``OmniRealtimeClient``, and ``LLMSessionManager``.

Design goals
============
- One canonical ``ToolDefinition`` shape (OpenAI-style JSON schema for
  parameters) that every provider adapter knows how to translate into its
  on-the-wire format (OpenAI Realtime / Gemini Live / GLM / StepFun / OpenAI
  Chat Completions / google-genai).
- ``ToolRegistry`` lives on ``LLMSessionManager`` so callers can
  ``register_tool(...)`` / ``unregister_tool(...)`` from anywhere
  (including agent_server / plugins via the cross-process RPC layer).
- The registry executes ``ToolCall`` → ``ToolResult`` and the active
  client (offline or realtime) feeds the result back to the model and
  resumes generation.

Provider-specific schema translation lives in the client classes
(``OmniOfflineClient`` / ``OmniRealtimeClient``); this module stays
provider-agnostic.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from PIL import Image

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


ToolHandler = Callable[[Dict[str, Any]], Union[Awaitable[Any], Any]]

# Ceilings on the image channel of a tool result. A handler that hands back a
# 4K frame would stall the whole tool loop, so the limit is enforced here for
# both in-process handlers and remote plugin callbacks.
# What a tool is ALLOWED to hand back. Kept where it was: this is the
# author-facing contract, and shrinking it to suit a transport detail would
# push the problem onto every tool that returns a screenshot.
_MAX_TOOL_IMAGE_B64_BYTES = 2 * 1024 * 1024

# What actually rides the request -- and therefore what the frames bus can
# carry. It is the frame channel's IMAGE budget, which is that channel's event
# ceiling minus the envelope around the pixels (event_id, source, mime,
# turn_id, generation, metadata, lanlan_name). Not the event ceiling itself:
# both were 500 KiB for a while and looked consistent, but one measures the
# image and the other measures image + envelope, so an image compressed to
# exactly the ladder's limit was silently dropped at the publish -- the model
# got it, no plugin did. Pinned equal to
# ``agent_event_bus.PROVIDER_FRAME_MAX_IMAGE_B64_BYTES`` by a test that builds
# a full-size event and measures it, rather than by comparing constants.
#
# Not imported from there at runtime: agent_event_bus pulls in pyzmq, and this
# module sits on the tool path.
#
# 值就是对外文档承诺的 500 KiB：信封加在**事件上限**那一侧，不从这里扣——
# 「图片 500 KiB vs plane 记录 512 KiB」那 12 KB 差额本来就是留给信封的。
_TOOL_IMAGE_DELIVER_MAX_B64_BYTES = 500 * 1024
_MAX_TOOL_IMAGES = 2
_MAX_TOOL_IMAGE_PIXELS = 3840 * 2160
_MAX_TOOL_IMAGE_VISION_PROMPT_CHARS = 2000
_TOOL_IMAGE_TURN_MAX_COUNT = 2
_TOOL_IMAGE_TURN_MAX_B64_BYTES = 4 * 1024 * 1024
_ALLOWED_TOOL_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})
_JPEG_MAGIC = b"\xff\xd8"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass
class ToolDefinition:
    """Canonical, provider-agnostic tool description.

    ``parameters`` is an OpenAI-flavoured JSON Schema object
    (``{"type": "object", "properties": {...}, "required": [...]}``).
    Provider adapters convert it to their wire format.

    ``handler`` is optional — if absent, the registry treats the tool as
    "remote" and dispatches via ``remote_dispatcher`` (used for
    plugin/agent_server tools that live in another process).
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Optional[ToolHandler] = None
    # Free-form metadata: e.g. {"source": "plugin", "plugin_id": "...", "version": "..."}
    # Used by the cross-process RPC layer to route remote calls.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_openai_chat(self) -> Dict[str, Any]:
        """OpenAI Chat Completions / StepFun Realtime / Qwen-text format
        (nested under ``function``)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_openai_realtime(self) -> Dict[str, Any]:
        """OpenAI Realtime / GLM Realtime format (flat — name, description,
        parameters at the top level alongside ``type``)."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_gemini_function_declaration(self) -> Dict[str, Any]:
        """Gemini Live + google-genai chat: ``function_declarations`` entry."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolCall:
    """Parsed tool invocation from the model.

    ``call_id`` originates from the provider:
        - OpenAI Realtime / StepFun: ``call_id`` field on the event
        - Gemini Live / google-genai: ``function_call.id``
        - GLM Realtime: synthesized from ``response_id + output_index`` (no
          native call_id; the protocol echoes it back in the response item
          and we don't need to round-trip it)
        - OpenAI Chat Completions streaming: ``tool_calls[].id``

    ``arguments`` is parsed JSON; ``raw_arguments`` is the original string
    if parsing failed (some providers stream incomplete JSON; clients
    accumulate then attempt parse).
    """

    name: str
    arguments: Dict[str, Any]
    call_id: str = ""
    raw_arguments: str = ""
    # Used internally by some providers for state tracking; opaque to callers.
    provider_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolImage:
    """One picture a tool wants the model to see.

    ``data_b64`` is raw base64 with no ``data:`` prefix. ``vision_prompt``
    says what the model should look for; it becomes the text part sitting
    next to the image in the turn, which is the only thing that tells the
    model why it was handed a frame.
    """

    data_b64: str
    mime: str = "image/jpeg"
    vision_prompt: str = ""


@dataclass
class ToolResult:
    """Outcome of executing a ``ToolCall``."""

    call_id: str
    name: str
    # Result payload — typically a dict; will be JSON-encoded by the
    # provider adapter when sending back. Strings pass through unchanged
    # for providers that expect a raw string body.
    output: Any
    is_error: bool = False
    error_message: str = ""
    # Pictures ride beside ``output``, never inside it: they must not be
    # serialized into the string the model reads. ``LLMSessionManager.
    # _route_tool_images`` decides whether the session can show them to the
    # model at all, and empties the list when it cannot -- so anything
    # downstream may treat a non-empty list as "these are going out".
    images: List["ToolImage"] = field(default_factory=list)

    def output_as_json_string(self) -> str:
        """Render ``output`` as the JSON string that OpenAI Realtime / GLM /
        StepFun expect in ``conversation.item.create.item.output``."""
        if isinstance(self.output, str):
            return self.output
        try:
            return json.dumps(self.output, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"result": str(self.output)}, ensure_ascii=False)

    def merge_into_output(self, **fields: Any) -> None:
        """Add fields to ``output``, normalizing it to a dict first.

        ``output`` is whatever the tool returned — a plugin may well hand
        back a bare string. Non-dict payloads are wrapped as
        ``{"result": <original>}`` so there is one shape for every caller
        that needs to annotate a result (today: image warnings).

        Normalization runs even with no fields to add, so the payload shape
        does not depend on whether the caller happened to have something to
        say.
        """
        if not isinstance(self.output, dict):
            self.output = {"result": self.output}
        self.output.update(fields)

    def add_image_warnings(self, *warnings: str) -> None:
        """Append to the model-visible ``_image_warnings`` list.

        Three stages annotate the same result -- the envelope parser, the
        per-turn image budget, and the routing decision -- and each has to keep
        what the previous one said, so the existing list is read back rather
        than overwritten.
        """
        existing = (
            self.output.get("_image_warnings")
            if isinstance(self.output, dict)
            else None
        )
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(warnings)
        self.merge_into_output(_image_warnings=merged)


def tool_result_output_payload(body: Dict[str, Any]) -> Any:
    """Pick the model-readable ``output`` from a tool result envelope.

    When the handler omits ``output``, fall back to the rest of the body —
    but never the ``images`` channel. Those pixels ride ``ToolResult.images``.
    """
    if "output" in body:
        return body["output"]
    return {k: v for k, v in body.items() if k != "images"}


def _decode_tool_image_b64(data_b64: str) -> Tuple[bytes, str] | None:
    """Decode base64 image bytes and return ``(raw, detected_mime)``."""
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    if raw.startswith(_JPEG_MAGIC):
        detected_mime = "image/jpeg"
        expected_format = "JPEG"
    elif raw.startswith(_PNG_MAGIC):
        detected_mime = "image/png"
        expected_format = "PNG"
    else:
        return None

    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != expected_format:
                return None
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > _MAX_TOOL_IMAGE_PIXELS:
                return None
            image.verify()
        # JPEG's lightweight ``verify`` does not decode entropy data and can
        # accept a missing EOI marker, so force a bounded full decode too.
        with Image.open(BytesIO(raw)) as image:
            image.load()
    except Exception:
        return None
    return raw, detected_mime


# 压到投递上限以内的阶梯。第一档就是送模型的常规档位（1280x720 / q80）；后面
# 几档只在它仍然放不下时才走，逐级让分辨率和质量。这些数字不是随手挑的：
# 720p/q80 是本仓所有送模型画面的既有档位，其余各档只是在它之下继续降。
_TOOL_IMAGE_FIT_LADDER: Tuple[Tuple[int, int], ...] = (
    (720, 80),
    (720, 60),
    (540, 55),
    (480, 45),
)


def _fit_tool_image_for_delivery(data_b64: str) -> Tuple[str, str] | None:
    """Bring one tool image under the delivery ceiling. ``(b64, mime)`` or None.

    NOT ``normalize_image_for_model``. That function is a FIXED POINT: a JPEG
    already inside 1280x720 comes back as the identical string without being
    re-encoded, which is exactly right for its own job (an image rides history
    for several turns and must not degrade one round-trip per turn) and exactly
    wrong here. A 1280x720 q95 screenshot is inside the profile and still far
    over the byte ceiling, so leaning on that function dropped pictures a single
    q80 pass would have halved. Its ``except`` path returns the input unchanged
    too, which read as "cannot fit" rather than "could not try".

    So this walks a real ladder against the BYTE budget, which the pixel-bound
    helpers explicitly do not provide (see ``compress_screenshot``'s docstring).
    The first rung is the standard model profile; the rest only run when that
    still does not fit.

    Returns None when no rung fits -- then the caller drops the image with a
    model-visible warning rather than sending pixels whose bus copy would be
    refused downstream anyway.

    Synchronous and CPU-bound, like the decode above it. Both call sites reach
    this through ``asyncio.to_thread`` (see ``tool_result_from_envelope``'s
    callers), which is why re-encoding here does not stall the event loop.
    """
    if len(data_b64) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES:
        return data_b64, ""
    try:
        import base64 as _base64
        from io import BytesIO as _BytesIO

        from PIL import ImageOps

        from utils.screenshot_utils import (
            MODEL_IMAGE_MAX_WIDTH,
            compress_screenshot,
        )

        image = Image.open(_BytesIO(_base64.b64decode(data_b64)))
        # 摆正再缩放，和 normalize_image_for_model 同一个理由：JPEG 存盘不带
        # EXIF，先缩放会把 orientation 标记连同信息一起丢掉，模型拿到一张躺倒
        # 的照片且无从察觉。
        image = ImageOps.exif_transpose(image) or image
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
    except Exception:
        return None

    for target_h, quality in _TOOL_IMAGE_FIT_LADDER:
        try:
            encoded = _base64.b64encode(
                compress_screenshot(
                    image,
                    target_h=target_h,
                    quality=quality,
                    max_w=MODEL_IMAGE_MAX_WIDTH,
                )
            ).decode("ascii")
        except Exception:
            return None
        if len(encoded) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES:
            # 重编码一律出 JPEG，所以声明的 mime 必须跟着字节走——一张被重编码
            # 却仍自称 PNG 的图过不了 provider 的嗅探，也会在总线记录上留下错误
            # 的 mime。
            return encoded, "image/jpeg"
    return None


def _normalize_tool_image_mime(mime: Any) -> str | None:
    """Strip parameters / case so ``Image/PNG; charset=binary`` can match."""
    if not isinstance(mime, str):
        return None
    normalized = mime.strip().lower().split(";", 1)[0].strip()
    if normalized not in _ALLOWED_TOOL_IMAGE_MIMES:
        return None
    return normalized


def parse_tool_images(body: Dict[str, Any]) -> Tuple[List[ToolImage], List[str]]:
    """Extract the optional ``images`` array from a tool result envelope.

    Returns the accepted images plus human-readable warnings for whatever was
    rejected. A body with no ``images`` key yields ``([], [])`` — that is the
    entire backward-compatibility contract for tools written before the image
    channel existed.
    """
    if "images" not in body:
        return [], []
    raw = body["images"]
    if not isinstance(raw, list):
        return [], ["tool images must be a list; ignored"]

    images: List[ToolImage] = []
    warnings: List[str] = []
    if len(raw) > _MAX_TOOL_IMAGES:
        warnings.append(
            f"a tool may return at most {_MAX_TOOL_IMAGES} image(s); "
            f"{len(raw) - _MAX_TOOL_IMAGES} dropped"
        )
    for index, entry in enumerate(raw[:_MAX_TOOL_IMAGES]):
        if not isinstance(entry, dict):
            warnings.append(f"image #{index} is not an object; dropped")
            continue
        data_b64 = entry.get("data_b64")
        if not isinstance(data_b64, str) or not data_b64:
            warnings.append(f"image #{index} has empty data_b64; dropped")
            continue
        normalized_data_b64 = "".join(data_b64.split())
        if not normalized_data_b64:
            warnings.append(f"image #{index} has empty data_b64; dropped")
            continue
        if len(normalized_data_b64) > _MAX_TOOL_IMAGE_B64_BYTES:
            warnings.append(
                f"image #{index} is too large "
                f"({len(normalized_data_b64)} > {_MAX_TOOL_IMAGE_B64_BYTES} base64 bytes); dropped"
            )
            continue
        decoded = _decode_tool_image_b64(normalized_data_b64)
        if decoded is None:
            warnings.append(
                f"image #{index} has invalid base64 or non-image bytes; dropped"
            )
            continue
        _, detected_mime = decoded
        declared = entry.get("mime")
        mime = (
            detected_mime
            if declared is None or declared == ""
            else _normalize_tool_image_mime(declared)
        )
        if mime is None:
            warnings.append(f"image #{index} has unsupported mime {declared!r}; dropped")
            continue
        if mime != detected_mime:
            warnings.append(
                f"image #{index} mime does not match image bytes; dropped"
            )
            continue
        raw_vision_prompt = entry.get("vision_prompt")
        vision_prompt = raw_vision_prompt if isinstance(raw_vision_prompt, str) else ""
        if len(vision_prompt) > _MAX_TOOL_IMAGE_VISION_PROMPT_CHARS:
            warnings.append(
                f"image #{index} vision_prompt is too long "
                f"({len(vision_prompt)} > {_MAX_TOOL_IMAGE_VISION_PROMPT_CHARS} characters); "
                "truncated"
            )
            vision_prompt = vision_prompt[:_MAX_TOOL_IMAGE_VISION_PROMPT_CHARS]
        fitted = _fit_tool_image_for_delivery(normalized_data_b64)
        if fitted is None:
            warnings.append(
                f"image #{index} could not be compressed under "
                f"{_TOOL_IMAGE_DELIVER_MAX_B64_BYTES} base64 bytes; dropped"
            )
            continue
        delivered_b64, rewritten_mime = fitted
        if rewritten_mime:
            mime = rewritten_mime
            warnings.append(
                f"image #{index} was re-encoded to fit the delivery budget"
            )
        images.append(ToolImage(
            data_b64=delivered_b64,
            mime=mime,
            vision_prompt=vision_prompt,
        ))
    return images, warnings


def looks_like_tool_envelope(body: Any) -> bool:
    """Is this the remote-callback envelope, or the tool's own business data?

    The wire shape ``/api/tools/register`` documents is
    ``{"output": <any JSON>, "is_error": bool}``, so either key marks an
    envelope. Anything else is the tool's own dict and belongs in ``output``
    untouched -- in particular an ``images`` key on its own, which a search
    tool uses for result URLs. Parsing that as the pixel channel pulls the key
    out of the model-visible output and then rejects the URLs as malformed
    base64, replacing the tool's data with warnings.

    Deliberately NOT the same predicate as the plugin callback route in
    ``plugin/server/routes/llm_tools.py``: that one receives a plugin
    handler's return value, where a bare ``output`` key is ordinary data, so it
    additionally requires ``images``. Same-looking condition, different wire
    contract -- do not merge them.
    """
    if not isinstance(body, dict):
        return False
    return "is_error" in body or "output" in body


def tool_result_from_envelope(call: "ToolCall", body: Any) -> ToolResult:
    """Normalize a local/remote handler return value into ``ToolResult``.

    Dict envelopes may carry ``images`` beside ``output`` (the plugin wire
    shape). Non-dict values become ``output`` as-is with no images, preserving
    handlers that return a bare string or list.
    """
    if not isinstance(body, dict):
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            output=body,
            is_error=False,
        )
    images, image_warnings = parse_tool_images(body)
    result = ToolResult(
        call_id=call.call_id,
        name=call.name,
        output=tool_result_output_payload(body),
        is_error=bool(body.get("is_error", False)),
        error_message=(
            str(body.get("error") or "") if body.get("is_error") else ""
        ),
        images=images,
    )
    if image_warnings:
        logger.warning(
            "tool '%s' returned %d unusable image(s): %s",
            call.name, len(image_warnings), "; ".join(image_warnings),
        )
        result.add_image_warnings(*image_warnings)
    return result


# Callback shape exposed to the clients. Clients invoke this when the
# model emits a tool call; the implementation (registry on
# LLMSessionManager) returns the result, and the client sends it back to
# the provider on the wire.
OnToolCallCallback = Callable[[ToolCall], Awaitable[ToolResult]]


class ToolRegistryError(Exception):
    pass


class ToolRegistry:
    """Process-local tool registry.

    Local handlers run in-process (any sync/async callable). Remote tools
    have ``handler=None`` and rely on ``remote_dispatcher``, which the
    plugin/agent_server RPC layer plugs in (see
    ``main_routers/tool_router.py`` for the HTTP wiring).
    """

    def __init__(
        self,
        *,
        remote_dispatcher: Optional[Callable[[ToolCall, Dict[str, Any]], Awaitable[ToolResult]]] = None,
    ) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._lock = asyncio.Lock()
        self._remote_dispatcher = remote_dispatcher
        # Telemetry: last execution timing for each tool. Useful for
        # surface-level observability without having to plumb full tracing.
        self._last_invocation_ms: Dict[str, float] = {}

    # ---- registration ---------------------------------------------------

    def register(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        if not tool.name:
            raise ToolRegistryError("ToolDefinition.name must be non-empty")
        if not replace and tool.name in self._tools:
            raise ToolRegistryError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        logger.info(
            "ToolRegistry: registered '%s' (handler=%s, source=%s)",
            tool.name,
            "local" if tool.handler else "remote",
            tool.metadata.get("source", "unknown"),
        )

    def unregister(self, name: str) -> bool:
        existed = self._tools.pop(name, None) is not None
        if existed:
            logger.info("ToolRegistry: unregistered '%s'", name)
        return existed

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def all(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def clear(self, *, source: Optional[str] = None) -> int:
        """Remove all tools, or only those with a matching ``metadata.source``.

        Returns the number removed. Callers like ``unregister_plugin_tools``
        use ``source="plugin:<id>"`` to drop just that plugin's tools.
        """
        if source is None:
            n = len(self._tools)
            self._tools.clear()
            return n
        to_drop = [k for k, t in self._tools.items() if t.metadata.get("source") == source]
        for k in to_drop:
            self._tools.pop(k, None)
        return len(to_drop)

    # ---- execution ------------------------------------------------------

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a tool call. Never raises — wraps errors into a
        ``ToolResult(is_error=True)`` so the calling client can still feed
        a structured response back to the model (model sees the error as
        a normal tool result string, often recoverable)."""
        tool = self._tools.get(call.name)
        if tool is None:
            msg = f"tool '{call.name}' is not registered"
            logger.warning("ToolRegistry.execute: %s", msg)
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output={"error": msg, "available_tools": self.names()},
                is_error=True,
                error_message=msg,
            )

        start = time.time()
        try:
            if tool.handler is not None:
                result_value = tool.handler(call.arguments or {})
                if asyncio.iscoroutine(result_value) or isinstance(result_value, asyncio.Future):
                    result_value = await result_value
                # Match the remote callback route: ``is_error`` is explicit,
                # while a successful image envelope needs both channel keys.
                # Plain business data may legitimately contain ``images`` or
                # ``output`` and must stay intact.
                if isinstance(result_value, dict) and (
                    "is_error" in result_value
                    or ("output" in result_value and "images" in result_value)
                ):
                    return await asyncio.to_thread(
                        tool_result_from_envelope,
                        call,
                        result_value,
                    )
                return ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=result_value,
                    is_error=False,
                )

            # Remote tool — delegate to the dispatcher (plugin/agent_server).
            if self._remote_dispatcher is None:
                msg = f"tool '{call.name}' is remote but no dispatcher is bound"
                logger.error("ToolRegistry.execute: %s", msg)
                return ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output={"error": msg},
                    is_error=True,
                    error_message=msg,
                )
            return await self._remote_dispatcher(call, tool.metadata)
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            logger.exception("ToolRegistry.execute: '%s' raised: %s", call.name, err_text)
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output={"error": err_text},
                is_error=True,
                error_message=err_text,
            )
        finally:
            self._last_invocation_ms[call.name] = (time.time() - start) * 1000.0

    # ---- export ---------------------------------------------------------

    def specs_for(self, *, dialect: str) -> List[Dict[str, Any]]:
        """Return all tool specs serialized for the given provider dialect.

        ``dialect`` ∈ {"openai_chat", "openai_realtime", "gemini"}.
        Provider adapters in the clients call this to fill the wire
        ``tools`` field at session-config time.
        """
        if not self._tools:
            return []
        if dialect == "openai_chat":
            return [t.to_openai_chat() for t in self._tools.values()]
        if dialect == "openai_realtime":
            return [t.to_openai_realtime() for t in self._tools.values()]
        if dialect == "gemini":
            return [t.to_gemini_function_declaration() for t in self._tools.values()]
        raise ToolRegistryError(f"unknown dialect: {dialect}")

    def gemini_tools_config(self) -> List[Any]:
        """Return ``[types.Tool(function_declarations=[…])]`` ready for
        ``GenerateContentConfig(tools=…)`` / ``LiveConnectConfig(tools=…)``.

        Lazy-imports ``google.genai.types`` so this module is importable
        on systems without the SDK (the realtime client already does this
        dance — we mirror it here for the offline path)."""
        if not self._tools:
            return []
        try:
            from google.genai import types as genai_types  # noqa: WPS433
        except Exception as e:
            raise ToolRegistryError(f"google-genai SDK unavailable: {e}")
        decls = [t.to_gemini_function_declaration() for t in self._tools.values()]
        return [genai_types.Tool(function_declarations=decls)]


# ---------------------------------------------------------------------------
# Argument parsing helpers used by client adapters
# ---------------------------------------------------------------------------


def parse_arguments_json(arguments: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    """Best-effort JSON decode for streamed tool-call arguments.

    Providers like OpenAI Realtime / StepFun stream argument fragments
    that the client accumulates into a single string before parsing;
    google-genai already exposes a parsed dict. This helper normalizes
    both."""
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    text = (arguments or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        # Some providers emit Python-literal-ish strings; fall back to a
        # raw passthrough so the model still sees what it intended.
        return {"_raw": text}
