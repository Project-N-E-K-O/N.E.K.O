"""Image generation profiles owned by the core API settings.

Inspired by Alumin-Hydro's image generator in PR #2830. No plugin runtime
or consumer state belongs here.
"""
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from utils.http.url import same_endpoint

PROVIDERS = {
    "openai": {"name": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-image-2", "key_field": "assistApiKeyOpenai"},
    "qwen": {"name": "Qwen (Beijing)", "protocol": "dashscope", "base_url": "https://dashscope.aliyuncs.com", "model": "wanx2.1-t2i-turbo", "key_field": "assistApiKeyQwen"},
    "qwen_intl": {"name": "Qwen (Singapore)", "protocol": "dashscope", "base_url": "https://dashscope-intl.aliyuncs.com", "model": "wanx2.1-t2i-turbo", "key_field": "assistApiKeyQwenIntl"},
    "custom": {"name": "OpenAI-compatible", "protocol": "openai", "base_url": "", "model": "", "key_field": ""},
}


@dataclass(frozen=True)
class ImageConfig:
    provider: str
    protocol: str
    base_url: str
    model: str
    api_key: str = field(repr=False)


def resolve_image_config(raw: dict, *, enabled: bool = True) -> ImageConfig | None:
    """Resolve only image settings; never fall back to a chat model or key."""
    provider = raw.get("imageModelProvider", "")
    if not enabled or provider in ("", "disabled", None):
        return None
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Unsupported image provider")
    profile = PROVIDERS[provider]
    values = {}
    for suffix in ("Url", "Id", "ApiKey"):
        value = raw.get(f"imageModel{suffix}", "")
        if not isinstance(value, str):
            raise ValueError("Image settings must be strings")
        values[suffix] = value.strip()
    # Named providers own their endpoint and use only their own Key Book entry.
    # An arbitrary endpoint always requires the explicitly separate custom key.
    url = values["Url"] if provider == "custom" else profile["base_url"]
    if provider != "custom" and values["Url"] and not same_endpoint(values["Url"], url):
        raise ValueError("Use custom image provider for a custom endpoint")
    model = values["Id"] or profile["model"]
    key = values["ApiKey"] if provider == "custom" else raw.get(profile["key_field"], "")
    if not isinstance(key, str):
        raise ValueError("Image API key must be a string")
    key = key.strip()
    try:
        httpx.URL(url)  # Match the transport parser, including IDNA and IPv4 validation.
        parsed = urlsplit(url)
        # Access .port separately to reject malformed ports, while allowing 443 implicit.
        parsed.port
        valid_url = parsed.scheme == "https" and parsed.hostname and not (
            parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.endswith("//")
        )
    except (ValueError, httpx.InvalidURL):
        valid_url = False
    if not valid_url or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("Image endpoint must be an HTTPS base URL")
    if not model or len(model) > 200 or any(ord(c) < 32 for c in model):
        raise ValueError("Invalid image model")
    if len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("Invalid image API key")
    return ImageConfig(provider, profile["protocol"], url.rstrip("/"), model, key)
