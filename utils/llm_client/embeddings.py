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
"""OpenAI-compatible embeddings adapter."""

from __future__ import annotations
import weakref
from typing import Any

from .lifecycle import (
    _close_async_openai_client_from_sync_best_effort,
    _close_chat_openai_clients_best_effort,
    _get_default_ssl_context,
)

DEFAULT_EMBEDDING_TIMEOUT_SECONDS: float = 30.0


class OpenAIEmbeddings:
    """Lightweight OpenAI embeddings client."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "",
        api_key: str | None = None,
        *,
        timeout: float | Any | None = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        keepalive_expiry: float | None = None,
        max_retries: int = 1,
        **_kwargs: Any,
    ):
        import httpx
        from openai import (
            DEFAULT_CONNECTION_LIMITS,
            AsyncOpenAI,
            DefaultAsyncHttpxClient,
            DefaultHttpxClient,
            OpenAI,
        )
        from utils.http.pool_utils import (
            DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            build_split_http_timeout,
            install_stale_connection_retry,
        )

        self.model = model
        _api_key = api_key or "sk-placeholder"
        # Override the SDK UA with neko/<version> to prevent custom embedding endpoints' Cloudflare
        # "Manage AI bots" rules from blocking requests.
        # Extract and merge _kwargs default_headers explicitly before client construction to preserve caller headers.
        # Extract only default_headers; do not forward other _kwargs: sync/async clients validate
        # parameters such as http_client differently, so forwarding everything can break initialization.
        from utils.http_client import ensure_user_agent
        _caller_headers = _kwargs.get("default_headers")
        _headers = ensure_user_agent(_caller_headers)

        effective_keepalive = (
            DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
            if keepalive_expiry is None
            else keepalive_expiry
        )
        http_kw: dict[str, Any] = {
            "verify": _get_default_ssl_context(),
            "limits": httpx.Limits(
                max_connections=DEFAULT_CONNECTION_LIMITS.max_connections,
                max_keepalive_connections=DEFAULT_CONNECTION_LIMITS.max_keepalive_connections,
                keepalive_expiry=effective_keepalive,
            ),
        }
        client_kw: dict[str, Any] = dict(
            base_url=base_url,
            api_key=_api_key,
            default_headers=_headers,
            max_retries=max_retries,
        )
        _timeout = build_split_http_timeout(timeout)
        if _timeout is not None:
            client_kw["timeout"] = _timeout

        sync_http = install_stale_connection_retry(
            DefaultHttpxClient(**http_kw),
            label="Embeddings HTTP",
        )
        async_http = install_stale_connection_retry(
            DefaultAsyncHttpxClient(**http_kw),
            label="Embeddings HTTP",
        )
        self._client = OpenAI(**{**client_kw, "http_client": sync_http})
        self._aclient = AsyncOpenAI(**{**client_kw, "http_client": async_http})
        self._client_finalizer = weakref.finalize(
            self,
            _close_chat_openai_clients_best_effort,
            self._client,
            self._aclient,
        )

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    async def aembed_query(self, text: str) -> list[float]:
        resp = await self._aclient.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    async def aclose(self) -> None:
        """Close underlying httpx clients (async path)."""
        await self._aclient.close()
        self._client.close()
        self._client_finalizer.detach()

    def close(self) -> None:
        """Close underlying httpx clients (sync path)."""
        self._client.close()
        _close_async_openai_client_from_sync_best_effort(self._aclient)
        self._client_finalizer.detach()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
