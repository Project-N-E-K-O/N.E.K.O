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

from urllib.parse import urlparse


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def should_bypass_proxy_for_url(url: str) -> bool:
    """Return True when the target URL must bypass local proxy settings."""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    return host in _LOOPBACK_HOSTS


def aiohttp_session_kwargs_for_url(url: str, *, default_trust_env: bool = True) -> dict[str, object]:
    """Build ClientSession kwargs that keep loopback traffic on a direct connection."""
    if should_bypass_proxy_for_url(url):
        # aiohttp only consults proxy environment variables when trust_env=True.
        return {"trust_env": False}
    return {"trust_env": default_trust_env}
