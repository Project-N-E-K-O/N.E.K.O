"""Images go to the chat model; the retired vision slot is only a fallback.

The settings page no longer offers a separate vision model: every image path
(screenshot description, watch-together, mini-game frames, proactive
screenshots, images in an offline chat) sends the picture to the chat model.
A user who configured a vision model before the slot was retired keeps it as a
deprecated fallback: when the chat model rejects an image, the call is retried
on the old vision config, a deprecation warning is logged and the frontend is
told once that the separate vision model will be dropped in the release.

The fallback is engaged per chat-model route. Once a chat model has rejected an
image, later image calls go straight to the vision config until the chat model
is changed, so a text-only chat model costs one failed request, not one per
image.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

from utils.http.url import same_endpoint

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status code the frontend translates (static/locales/*.json -> errors.*).
LEGACY_VISION_FALLBACK_CODE = "LEGACY_VISION_FALLBACK"

# A fallback still in use is logged again at most this often.
_WARN_INTERVAL_SECONDS = 600.0

# Lowercased fragments that only appear when a model refuses image input.
# Collected from provider replies (2026-11-22): SiliconFlow "The model is not a
# VLM (Vision Language Model). Please use text-only prompts.", Zhipu
# "messages.content.type ... ['text']", DeepSeek "unknown variant `image_url`",
# OpenAI-compatible gateways "does not support image input".
_IMAGE_REFUSAL_FRAGMENTS = (
    "not a vlm",
    "use text-only prompts",
    "unknown variant `image_url`",
    "unknown variant 'image_url'",
    "does not support image",
    "doesn't support image",
    "do not support image",
    "image input is not supported",
    "image inputs are not supported",
    "images are not supported",
    "image_url is not supported",
    "image_url is only supported",
    "does not support vision",
    "vision is not supported",
    "does not support multimodal",
    "multimodal input is not supported",
    "no endpoints found that support image input",
    "不支持图片",
    "不支持图像",
    "不支持视觉",
    "不支持多模态",
)

_TEXT_ONLY_LIST = re.compile(r"""\[\s*['"]text['"]\s*\]""")

# Status codes a provider uses for "this request shape is not accepted".
_CLIENT_ERROR_CODES = frozenset({400, 404, 415, 422})


def is_image_unsupported_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a provider refusing image input for this model."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int) and status not in _CLIENT_ERROR_CODES:
        return False
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        try:
            parts.append(json.dumps(body, ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(str(body))
    text = " ".join(parts).lower()
    if "messages.content.type" in text and _TEXT_ONLY_LIST.search(text):
        # Zhipu: the allowed content types are text alone.
        return True
    return any(fragment in text for fragment in _IMAGE_REFUSAL_FRAGMENTS)


def _route_key(cfg: dict | None) -> tuple[str, str, str]:
    cfg = cfg or {}
    return (
        str(cfg.get("model") or "").strip(),
        str(cfg.get("base_url") or "").strip().rstrip("/").lower(),
        str(cfg.get("provider_type") or "").strip(),
    )


def _usable(cfg: dict | None) -> bool:
    if not isinstance(cfg, dict):
        return False
    if not str(cfg.get("model") or "").strip():
        return False
    return bool(cfg.get("api_key")) or bool(cfg.get("is_custom"))


def legacy_vision_differs(conversation: dict | None, vision: dict | None) -> bool:
    """Whether the stored vision config is a separate route from the chat model."""
    if not _usable(vision):
        return False
    if not isinstance(conversation, dict):
        return True
    if str(vision.get("model") or "").strip() != str(conversation.get("model") or "").strip():
        return True
    if not same_endpoint(
        str(vision.get("base_url") or ""), str(conversation.get("base_url") or "")
    ):
        return True
    if (vision.get("api_key") or None) != (conversation.get("api_key") or None):
        return True
    return (vision.get("provider_type") or None) != (conversation.get("provider_type") or None)


@dataclass(frozen=True)
class ImageModelChoice:
    """Which config an image call should use right now."""

    conversation: dict
    legacy: Optional[dict]

    @property
    def using_legacy(self) -> bool:
        return self.legacy is not None and _engaged_for(self.conversation)

    @property
    def config(self) -> dict:
        return self.legacy if self.using_legacy and self.legacy is not None else self.conversation


_lock = threading.Lock()
_engaged_routes: set[tuple[str, str, str]] = set()
_last_warned: dict[str, float] = {}
_notice_sink: Optional[Callable[[str], Any]] = None


def _engaged_for(conversation: dict | None) -> bool:
    with _lock:
        return _route_key(conversation) in _engaged_routes


def register_notice_sink(sink: Optional[Callable[[str], Any]]) -> None:
    """Install the callable that shows a status message on the frontend.

    ``sink`` receives the JSON status string (``{"code": ..., "details": ...}``)
    and may return an awaitable; it is scheduled, never awaited by callers.
    """
    global _notice_sink
    _notice_sink = sink


def _notify_frontend(details: dict) -> None:
    sink = _notice_sink
    if sink is None:
        return
    message = json.dumps({"code": LEGACY_VISION_FALLBACK_CODE, "details": details}, ensure_ascii=False)
    try:
        result = sink(message)
    except Exception as exc:  # noqa: BLE001 - a notice must never break the image call
        logger.debug("legacy vision notice sink failed: %s", exc)
        return
    if result is not None and hasattr(result, "__await__"):
        import asyncio

        try:
            task = asyncio.get_running_loop().create_task(result)
        except RuntimeError:
            close = getattr(result, "close", None)
            if callable(close):
                close()
            return
        task.add_done_callback(lambda t: t.cancelled() or t.exception())


def _warn_deprecated(where: str, legacy: dict, *, first: bool) -> None:
    now = time.monotonic()
    with _lock:
        last = _last_warned.get(where)
        if not first and last is not None and now - last < _WARN_INTERVAL_SECONDS:
            return
        _last_warned[where] = now
    logger.warning(
        "[DEPRECATED] %s: the chat model cannot read images, using the separately configured "
        "vision model %s instead. The separate vision model is no longer supported and will be "
        "removed in the release; switch the chat model to one that can read images.",
        where,
        str(legacy.get("model") or ""),
    )


def engage_legacy_vision(choice: ImageModelChoice, where: str) -> bool:
    """Record that the chat model rejected an image; return whether a fallback exists.

    The first engagement for a chat-model route also tells the frontend.
    """
    legacy = choice.legacy
    if legacy is None:
        return False
    key = _route_key(choice.conversation)
    with _lock:
        newly = key not in _engaged_routes
        _engaged_routes.add(key)
    _warn_deprecated(where, legacy, first=newly)
    if newly:
        _notify_frontend({
            "chat_model": str(choice.conversation.get("model") or ""),
            "vision_model": str(legacy.get("model") or ""),
        })
    return True


def note_legacy_vision_use(choice: ImageModelChoice, where: str) -> None:
    """Log (rate-limited) that an image call went to the retired vision config."""
    if choice.using_legacy and choice.legacy is not None:
        _warn_deprecated(where, choice.legacy, first=False)


def choose_image_model(conversation: dict, vision: dict | None) -> ImageModelChoice:
    """Build the choice from already-loaded chat and vision configs."""
    legacy = vision if legacy_vision_differs(conversation, vision) else None
    return ImageModelChoice(conversation=conversation if conversation is not None else {}, legacy=legacy)


async def aresolve_image_model(config_manager, *, core_config: dict | None = None) -> ImageModelChoice:
    """Load the chat config and the retired vision config as one choice."""
    kwargs = {"core_config": core_config} if core_config is not None else {}
    conversation = await config_manager.aget_model_api_config("conversation", **kwargs)
    vision = await config_manager.aget_model_api_config("vision", **kwargs)
    return choose_image_model(conversation, vision)


def resolve_image_model(config_manager, *, core_config: dict | None = None) -> ImageModelChoice:
    """Synchronous twin of :func:`aresolve_image_model`."""
    kwargs = {"_core_config": core_config} if core_config is not None else {}
    conversation = config_manager.get_model_api_config("conversation", **kwargs)
    vision = config_manager.get_model_api_config("vision", **kwargs)
    return choose_image_model(conversation, vision)


async def run_with_image_fallback(
    choice: ImageModelChoice,
    call: Callable[[dict], Awaitable[T]],
    *,
    where: str,
) -> T:
    """Run ``call`` on the chat model, retrying once on the retired vision config.

    The retry happens only when the chat model refused the image and the user
    still has a separate vision config; any other error propagates unchanged.
    """
    if choice.using_legacy:
        note_legacy_vision_use(choice, where)
        return await call(choice.config)
    try:
        return await call(choice.conversation)
    except Exception as exc:
        if not is_image_unsupported_error(exc) or not engage_legacy_vision(choice, where):
            raise
        assert choice.legacy is not None
        logger.info("%s: retrying the image request on the legacy vision config", where)
        return await call(choice.legacy)


def _reset_for_tests() -> None:
    """Forget engaged routes and warning timestamps (tests only)."""
    global _notice_sink
    with _lock:
        _engaged_routes.clear()
        _last_warned.clear()
    _notice_sink = None
