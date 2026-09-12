"""Core image generation infrastructure. No plugin, HTTP route or UI consumer."""
import asyncio
import math
import re
import sys

import httpx

from . import dashscope, openai
from .config import ImageConfig
from .types import GeneratedImage, ImageGenerationError, ImageRequest, ImageResult

__all__ = ["generate_image", "GeneratedImage", "ImageGenerationError", "ImageRequest", "ImageResult"]


async def generate_image(request: ImageRequest, *, config_manager=None, client=None, timeout: float = 180) -> ImageResult:
    """Generate one image using a single snapshot of core API settings.

    No automatic retry (a failed/timeout request may already have been billed).
    Cancellation propagates; a submitted provider job may still finish remotely.
    Injected clients remain caller-owned. No disk, logging or chat side effects.
    """
    if not isinstance(request, ImageRequest) or not isinstance(request.prompt, str) or not request.prompt.strip() or len(request.prompt) > 2000:
        raise ImageGenerationError("invalid_prompt")
    if not isinstance(request.size, str) or not re.fullmatch(r"[1-9][0-9]{1,3}x[1-9][0-9]{1,3}", request.size):
        raise ImageGenerationError("invalid_size")
    width, height = map(int, request.size.split("x"))
    if max(width, height) > 4096 or min(width, height) < 64:
        raise ImageGenerationError("invalid_size")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ImageGenerationError("invalid_timeout")
    if config_manager is None:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
    try:
        resolved = await config_manager.aget_model_api_config("image")
    except ValueError:
        raise ImageGenerationError("invalid_configuration") from None
    if not resolved.get("enabled"):
        raise ImageGenerationError("not_configured")
    config = ImageConfig(resolved["provider"], resolved["protocol"], resolved["base_url"], resolved["model"], resolved["api_key"])
    if not config.api_key:
        raise ImageGenerationError("missing_api_key")
    adapter = {"openai": openai, "dashscope": dashscope}.get(config.protocol)
    if adapter is None:
        raise ImageGenerationError("invalid_configuration")
    owned = client is None
    if owned:
        try:
            client = await asyncio.to_thread(httpx.AsyncClient, trust_env=True, follow_redirects=False)
        except (ValueError, OSError, httpx.InvalidURL):
            raise ImageGenerationError("client_initialization_failed") from None
    try:
        async with asyncio.timeout(timeout):
            image = await adapter.generate(client, config, request)
        return ImageResult(config.provider, config.model, image)
    except (TimeoutError, httpx.TimeoutException):
        raise ImageGenerationError("provider_timeout") from None
    except httpx.InvalidURL:
        raise ImageGenerationError("invalid_configuration") from None
    except httpx.HTTPError:
        raise ImageGenerationError("provider_network_error") from None
    finally:
        if owned:
            active_error = sys.exception()
            try:
                await client.aclose()
            except Exception:
                # Cleanup must not replace a provider error or cancellation.
                if active_error is None:
                    raise ImageGenerationError("client_close_failed") from None
