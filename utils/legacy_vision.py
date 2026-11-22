"""Images go to the chat model; a retired vision config keeps working until the release.

The settings page no longer offers a separate vision model: every image path
(screenshot description, watch-together, mini-game frames, proactive
screenshots, images in an offline chat) sends the picture to the chat model.

A user who configured a separate vision model before the slot was retired
keeps it, unchanged, until the release removes it: images still go to that
route (it may be a local endpoint chosen so screenshots never leave the
machine, so the chat provider must not start receiving them silently). Each
use logs a rate-limited deprecation warning, and the frontend is told once
per app start that the separate vision model will be dropped.
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
LEGACY_VISION_DEPRECATED_CODE = "LEGACY_VISION_DEPRECATED"

# A deprecated route still in use is logged again at most this often.
_WARN_INTERVAL_SECONDS = 600.0

# Lowercased fragments that only appear when a model refuses image input.
# Collected from provider replies (2026-11-22): SiliconFlow "The model is not a
# VLM (Vision Language Model). Please use text-only prompts.", DeepSeek
# "unknown variant `image_url`", OpenAI-compatible gateways "does not support
# image input". Zhipu is matched separately (see _TEXT_ONLY_LIST).
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
    """Which config image calls use: the chat model, or a retired vision route."""

    conversation: dict
    legacy: Optional[dict]

    @property
    def using_legacy(self) -> bool:
        return self.legacy is not None

    @property
    def config(self) -> dict:
        return self.legacy if self.legacy is not None else self.conversation


_lock = threading.Lock()
_last_warned: dict[str, float] = {}
_frontend_told = False
_notice_sink: Optional[Callable[[str], Any]] = None


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
    message = json.dumps({"code": LEGACY_VISION_DEPRECATED_CODE, "details": details}, ensure_ascii=False)
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


def note_legacy_vision_use(choice: ImageModelChoice, where: str) -> None:
    """Record that an image call goes to the retired vision config.

    Logs a deprecation warning (per call site, at most every ten minutes) and
    tells the frontend once per app start.
    """
    legacy = choice.legacy
    if legacy is None:
        return
    global _frontend_told
    now = time.monotonic()
    with _lock:
        last = _last_warned.get(where)
        warn = last is None or now - last >= _WARN_INTERVAL_SECONDS
        if warn:
            _last_warned[where] = now
        tell = not _frontend_told
        _frontend_told = True
    if warn:
        logger.warning(
            "[DEPRECATED] %s: images go to the separately configured vision model %s. "
            "The separate vision model is no longer supported and will be removed in the "
            "release, after which images go to the chat model; switch the chat model to one "
            "that can read images.",
            where,
            str(legacy.get("model") or ""),
        )
    if tell:
        _notify_frontend({"vision_model": str(legacy.get("model") or "")})


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


async def run_image_call(
    choice: ImageModelChoice,
    call: Callable[[dict], Awaitable[T]],
    *,
    where: str,
    has_image: bool = True,
) -> T:
    """Run ``call`` on the config image calls use, noting a deprecated route.

    ``has_image=False`` marks a text-only request on the same route: it is
    not a use of the retired vision config and announces nothing.
    """
    if has_image:
        note_legacy_vision_use(choice, where)
    return await call(choice.config)


def _reset_for_tests() -> None:
    """Forget warning timestamps and the frontend notice (tests only)."""
    global _notice_sink, _frontend_told
    with _lock:
        _last_warned.clear()
        _frontend_told = False
    _notice_sink = None
