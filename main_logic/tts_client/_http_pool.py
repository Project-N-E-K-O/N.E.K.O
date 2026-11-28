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

"""Connection reuse for the per-sentence HTTP TTS workers.

Each sentence is one HTTP request. httpx keeps an idle connection for 5s by
default, so the first sentence of a reply that starts more than 5s after the
previous one paid a fresh TCP + TLS handshake. These workers keep idle
connections for ``TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS`` instead.

A longer keep-alive window means more requests go out on a connection the
server may already have dropped. httpcore notices a peer close that already
arrived (the idle socket reads as EOF) and opens a new connection by itself;
it does not retry when the close lands while the request is being sent, which
surfaces as ``RemoteProtocolError`` ("Server disconnected without sending a
response") or a read/write error on a reset socket. httpx's
``AsyncHTTPTransport(retries=...)`` only retries failed connects, so it does
not cover that. ``install_stale_connection_retry`` retries such a request
once, and only when no response has arrived yet.
"""

from __future__ import annotations

import httpx

from utils.http.pool_utils import (
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    STALE_CONNECTION_ERRORS as _STALE_CONNECTION_ERRORS,
    install_stale_connection_retry as _install_shared_stale_connection_retry,
)

# Below the 60s+ idle timeouts common at TTS endpoints and their load
# balancers, so the client usually drops a connection before the server does.
TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS = DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS


def tts_http_limits(
    *,
    max_connections: int = 4,
    max_keepalive_connections: int = 2,
) -> httpx.Limits:
    """Pool limits for a TTS worker client, with the longer keep-alive."""
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        keepalive_expiry=TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    )


def install_stale_connection_retry(client):
    """Retry a request once when its connection turns out to be dead.

    Wraps the client's transports (the default one and any proxy mounts),
    below event hooks, auth and redirects, so a retry resends exactly the
    request that failed. Returns the client.
    """
    return _install_shared_stale_connection_retry(client, label="TTS HTTP")


def make_tts_http_client(**kwargs):
    """An ``httpx.AsyncClient`` for a per-sentence TTS worker.

    ``limits`` defaults to :func:`tts_http_limits`; other arguments pass
    through. ``httpx.AsyncClient`` is looked up on each call, so tests that
    patch it keep working.
    """
    kwargs.setdefault("limits", tts_http_limits())
    return install_stale_connection_retry(httpx.AsyncClient(**kwargs))
