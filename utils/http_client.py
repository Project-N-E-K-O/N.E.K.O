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
"""Centralized User-Agent management prevents Cloudflare Bot Management blocking custom API endpoints.

Background: official OpenAI / Anthropic SDKs send User-Agents such as ``AsyncOpenAI/Python 2.8.1``.
Cloudflare's \"Manage AI bots\" rules recognize known AI SDK UAs
and block them, causing some self-hosted / reverse-proxied custom endpoints to return block pages.

Inject ``User-Agent: neko/<version>`` into SDK clients to override their built-in UA. SDK
default_headers have highest header-merge priority, reliably replacing the built-in User-Agent.
"""

from __future__ import annotations
from typing import Any

from config.application import APP_VERSION


def get_default_user_agent() -> str:
    """Return the shared User-Agent, ``neko/<version>`` (e.g. ``neko/0.9.0``)."""
    return f"neko/{APP_VERSION}"


def ensure_user_agent(headers: dict[str, Any] | None) -> dict[str, Any]:
    """Return a new headers dictionary containing a default User-Agent.

    Preserve an explicitly supplied User-Agent in any case;
    otherwise inject ``neko/<version>``. Used for OpenAI / Anthropic SDK default_headers.

    Args:
        headers: Original headers, possibly None.

    Returns:
        New merged headers dictionary containing at least User-Agent.
    """
    merged: dict[str, Any] = dict(headers) if headers else {}

    # Try all case variants and select the first explicit nonempty value, skipping None / empty strings.
    ua_value: Any | None = None
    for k, value in merged.items():
        if str(k).lower() == "user-agent" and value not in (None, ""):
            ua_value = value
            break

    # Remove all user-agent case variants to avoid duplicate headers.
    keys_to_remove = [k for k in merged if str(k).lower() == "user-agent"]
    for k in keys_to_remove:
        del merged[k]

    # Write one canonical User-Agent key only.
    if ua_value is not None:
        # Preserve an explicit nonempty caller value.
        merged["User-Agent"] = ua_value
    else:
        # Inject the default when absent or empty.
        merged["User-Agent"] = get_default_user_agent()

    return merged


def patch_requests_default_user_agent() -> None:
    """Globally override requests' default User-Agent to prevent Cloudflare blocking plugins using requests.

    Override only the default UA (originally ``python-requests/x.y.z``); callers explicitly passing
    ``headers={\"User-Agent\": ...}`` remain unaffected. Call once at startup.
    """
    import requests.utils

    _default_ua = get_default_user_agent()
    _original_default_user_agent = requests.utils.default_user_agent

    def _neko_default_user_agent(name: str = "python-requests") -> str:
        # Return neko UA for the default name; otherwise call the original function to preserve the caller's name.
        if name == "python-requests":
            return _default_ua
        return _original_default_user_agent(name)

    requests.utils.default_user_agent = _neko_default_user_agent
