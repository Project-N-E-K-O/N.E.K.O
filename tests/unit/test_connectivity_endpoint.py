# -*- coding: utf-8 -*-
"""Unit tests for the backend connectivity test endpoint.

Tests cover:
1. Endpoint existence and schema validation (Req 1.1)
2. WebSocket connectivity mock scenarios (Req 1.3)
3. Error scenario return values (timeout, auth_failed, connection_refused, etc.)
4. Concurrent requests do not block (Req 1.6)

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
"""

from __future__ import annotations

import asyncio
import ssl
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from main_routers.config_router import (
    ConnectivityTestRequest,
    ConnectivityTestResponse,
    _test_openai_compatible,
    _test_websocket,
    _classify_openai_error,
    test_connectivity as _endpoint_test_connectivity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int) -> MagicMock:
    """Create a mock httpx.Response with the given status code."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    return resp


# ===========================================================================
# 1. Endpoint existence and schema validation (Req 1.1)
# ===========================================================================

class TestSchemaValidation:
    """Verify the Pydantic request/response models and basic endpoint routing."""

    def test_request_model_accepts_minimal_fields(self):
        """ConnectivityTestRequest requires only url and api_key."""
        req = ConnectivityTestRequest(url="https://example.com", api_key="sk-123")
        assert req.url == "https://example.com"
        assert req.api_key == "sk-123"
        assert req.provider_type == "openai_compatible"
        assert req.is_free is False

    def test_request_model_accepts_all_fields(self):
        """ConnectivityTestRequest accepts optional provider_type and is_free."""
        req = ConnectivityTestRequest(
            url="wss://realtime.example.com",
            api_key="sk-ws-key",
            provider_type="websocket",
            is_free=True,
        )
        assert req.provider_type == "websocket"
        assert req.is_free is True

    def test_request_model_defaults(self):
        """Default values for optional fields."""
        req = ConnectivityTestRequest(url="https://x.com", api_key="k")
        assert req.provider_type == "openai_compatible"
        assert req.is_free is False

    def test_response_model_success(self):
        """ConnectivityTestResponse can represent a success."""
        resp = ConnectivityTestResponse(success=True)
        assert resp.success is True
        assert resp.error is None
        assert resp.error_code is None

    def test_response_model_failure(self):
        """ConnectivityTestResponse can represent a failure with error details."""
        resp = ConnectivityTestResponse(
            success=False, error="请求超时（10秒）", error_code="timeout"
        )
        assert resp.success is False
        assert resp.error_code == "timeout"

    async def test_endpoint_returns_missing_params_for_empty_url(self):
        """Empty url → missing_params (Req 1.4)."""
        req = ConnectivityTestRequest(url="", api_key="sk-valid")
        result = await _endpoint_test_connectivity(req)
        assert result["success"] is False
        assert result["error_code"] == "missing_params"

    async def test_endpoint_allows_empty_api_key_for_keyless_services(self):
        """Empty api_key is allowed for local/keyless services (Decision 15)."""
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_http:
            req = ConnectivityTestRequest(url="https://api.example.com", api_key="")
            result = await _endpoint_test_connectivity(req)
            mock_http.assert_awaited_once()
            assert result["success"] is True

    async def test_endpoint_returns_missing_params_for_whitespace_url(self):
        """Whitespace-only url → missing_params (Req 1.4)."""
        req = ConnectivityTestRequest(url="   \t\n  ", api_key="sk-valid")
        result = await _endpoint_test_connectivity(req)
        assert result["success"] is False
        assert result["error_code"] == "missing_params"

    async def test_endpoint_allows_whitespace_api_key_for_keyless_services(self):
        """Whitespace-only api_key is stripped and allowed for keyless services (Decision 15)."""
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_http:
            req = ConnectivityTestRequest(url="https://api.example.com", api_key="   ")
            result = await _endpoint_test_connectivity(req)
            mock_http.assert_awaited_once()
            assert result["success"] is True

    async def test_endpoint_routes_to_openai_compatible_by_default(self):
        """Default provider_type routes to _test_openai_compatible (Req 1.1)."""
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_http:
            req = ConnectivityTestRequest(
                url="https://api.example.com/v1", api_key="sk-test"
            )
            result = await _endpoint_test_connectivity(req)
            mock_http.assert_awaited_once()
            assert result["success"] is True

    async def test_endpoint_routes_to_websocket_when_specified(self):
        """provider_type='websocket' routes to _test_websocket (Req 1.1, 1.3)."""
        with patch(
            "main_routers.config_router._test_websocket",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_ws:
            req = ConnectivityTestRequest(
                url="wss://realtime.example.com",
                api_key="sk-ws",
                provider_type="websocket",
            )
            result = await _endpoint_test_connectivity(req)
            mock_ws.assert_awaited_once()
            assert result["success"] is True


# ===========================================================================
# 2. WebSocket connectivity mock scenarios (Req 1.3)
# ===========================================================================

class TestWebSocketConnectivity:
    """Mock websockets.connect to test all WebSocket error branches."""

    async def test_ws_success(self):
        """Successful WebSocket handshake + session.update → success: True."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.send = AsyncMock()
        mock_conn.recv = AsyncMock(return_value='{"type": "session.created"}')

        with patch("websockets.connect", return_value=mock_conn):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is True

    async def test_ws_timeout_error(self):
        """TimeoutError during WebSocket connect → timeout."""
        with patch("websockets.connect", side_effect=TimeoutError("timed out")):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "timeout"

    async def test_ws_asyncio_timeout_error(self):
        """asyncio.TimeoutError during WebSocket connect → timeout."""
        with patch("websockets.connect", side_effect=asyncio.TimeoutError()):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "timeout"

    async def test_ws_ssl_error(self):
        """ssl.SSLError during WebSocket connect → ssl_error."""
        with patch(
            "websockets.connect",
            side_effect=ssl.SSLError("certificate verify failed"),
        ):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "ssl_error"

    async def test_ws_dns_error(self):
        """OSError with 'getaddrinfo' → dns_error."""
        with patch(
            "websockets.connect",
            side_effect=OSError("[Errno 11001] getaddrinfo failed"),
        ):
            result = await _test_websocket("wss://nonexistent.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "dns_error"

    async def test_ws_connection_refused(self):
        """OSError with 'connection refused' → connection_refused."""
        with patch(
            "websockets.connect",
            side_effect=OSError("[Errno 111] Connection refused"),
        ):
            result = await _test_websocket("wss://localhost:9999", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "connection_refused"

    async def test_ws_generic_os_error(self):
        """Other OSError → ws_error."""
        with patch(
            "websockets.connect",
            side_effect=OSError("some other OS error"),
        ):
            result = await _test_websocket("wss://example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "ws_error"

    async def test_ws_auth_failed_401(self):
        """Exception with status_code=401 → auth_failed."""
        exc = Exception("HTTP 401")
        exc.status_code = 401
        with patch("websockets.connect", side_effect=exc):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_ws_auth_failed_403(self):
        """Exception with status_code=403 → auth_failed."""
        exc = Exception("HTTP 403")
        exc.status_code = 403
        with patch("websockets.connect", side_effect=exc):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_ws_auth_failed_response_status_code(self):
        """Exception with response.status_code=401 (websockets 15.0.1 style) → auth_failed."""
        exc = Exception("HTTP 401")
        mock_response = MagicMock()
        mock_response.status_code = 401
        exc.response = mock_response
        with patch("websockets.connect", side_effect=exc):
            result = await _test_websocket("wss://realtime.example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_ws_generic_exception(self):
        """Generic Exception without status_code → ws_error."""
        with patch(
            "websockets.connect",
            side_effect=Exception("unexpected failure"),
        ):
            result = await _test_websocket("wss://example.com", "sk-key")

        assert result["success"] is False
        assert result["error_code"] == "ws_error"

    async def test_ws_url_with_model_param(self):
        """URL gets ?model= appended for non-free models."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.send = AsyncMock()
        mock_conn.recv = AsyncMock(return_value='{"type": "session.created"}')

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            result = await _test_websocket(
                "wss://realtime.example.com", "sk-key", model="step-audio-2"
            )
            call_args = mock_connect.call_args
            ws_url = call_args[0][0]
            assert "?model=step-audio-2" in ws_url

        assert result["success"] is True

    async def test_ws_url_free_model_no_model_param(self):
        """free-model skips ?model= parameter (same as OmniRealtimeClient)."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.send = AsyncMock()
        mock_conn.recv = AsyncMock(return_value='{"type": "session.created"}')

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            result = await _test_websocket("wss://realtime.example.com", "sk-key", model="free-model")
            call_args = mock_connect.call_args
            ws_url = call_args[0][0]
            assert "?model=" not in ws_url

        assert result["success"] is True

    async def test_ws_auth_header_only_no_query_key(self):
        """API key is sent via Authorization header only, not as URL query param."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.send = AsyncMock()
        mock_conn.recv = AsyncMock(return_value='{"type": "session.created"}')

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            result = await _test_websocket("wss://realtime.example.com", "sk-key")
            call_args = mock_connect.call_args
            ws_url = call_args[0][0]
            headers = call_args[1].get("additional_headers", {})
            assert "api_key" not in ws_url
            assert headers.get("Authorization") == "Bearer sk-key"

        assert result["success"] is True


# ===========================================================================
# 3. Error scenarios — all error codes (Req 1.2, 1.5)
# ===========================================================================

class TestOpenAICompatibleErrors:
    """Test _test_openai_compatible error handling via ChatOpenAI client.

    Now uses the project's ChatOpenAI client internally, so we mock at the
    ChatOpenAI level rather than httpx level.
    """

    async def test_success(self):
        """Successful chat completion → success."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hi"
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(return_value=MagicMock(content="hi"))
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client) as mock_cls:
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is True

    async def test_auth_error(self):
        """AuthenticationError → auth_failed."""
        from openai import AuthenticationError
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=AuthenticationError("invalid key", response=MagicMock(status_code=401), body=None)
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_timeout_error(self):
        """APITimeoutError → timeout."""
        from openai import APITimeoutError
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "timeout"

    async def test_connection_error_dns(self):
        """APIConnectionError with DNS failure → dns_error."""
        from openai import APIConnectionError
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIConnectionError(message="[Errno 11001] getaddrinfo failed", request=MagicMock())
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "dns_error"

    async def test_connection_error_refused(self):
        """APIConnectionError with connection refused → connection_refused."""
        from openai import APIConnectionError
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIConnectionError(message="[Errno 111] Connection refused", request=MagicMock())
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "connection_refused"

    async def test_ssl_error(self):
        """ssl.SSLError → ssl_error."""
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(side_effect=ssl.SSLError("certificate verify failed"))
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "ssl_error"

    async def test_status_error_500(self):
        """APIStatusError with 500 → unknown."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIStatusError("server error", response=mock_resp, body=None)
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "unknown"

    async def test_status_error_401(self):
        """APIStatusError with 401 → auth_failed."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIStatusError("unauthorized", response=mock_resp, body=None)
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_free_400_success(self):
        """Free version + APIStatusError 400 → success (service reachable)."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIStatusError("bad request", response=mock_resp, body=None)
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key", is_free=True)
        assert result["success"] is True

    async def test_non_free_400_unknown(self):
        """Non-free + APIStatusError 400 → unknown."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(
            side_effect=APIStatusError("bad request", response=mock_resp, body=None)
        )
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key", is_free=False)
        assert result["success"] is False
        assert result["error_code"] == "unknown"

    async def test_generic_exception_unknown(self):
        """Unexpected Exception → unknown."""
        mock_client = MagicMock()
        mock_client.ainvoke = AsyncMock(side_effect=RuntimeError("something unexpected"))
        mock_client.aclose = AsyncMock()

        with patch("utils.llm_client.ChatOpenAI", return_value=mock_client):
            result = await _test_openai_compatible("https://api.example.com/v1", "sk-key")
        assert result["success"] is False
        assert result["error_code"] == "unknown"


class TestClassifyOpenAIError:
    """Test _classify_openai_error directly for edge cases."""

    def test_asyncio_timeout(self):
        """asyncio.TimeoutError → timeout."""
        result = _classify_openai_error(asyncio.TimeoutError())
        assert result["error_code"] == "timeout"

    def test_builtin_timeout(self):
        """Built-in TimeoutError → timeout."""
        result = _classify_openai_error(TimeoutError("timed out"))
        assert result["error_code"] == "timeout"

    def test_generic_connection_error(self):
        """APIConnectionError without specific pattern → connection_refused."""
        from openai import APIConnectionError
        result = _classify_openai_error(
            APIConnectionError(message="some network issue", request=MagicMock())
        )
        assert result["error_code"] == "connection_refused"

    def test_free_400_success(self):
        """Free version + 400 status → success."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        result = _classify_openai_error(
            APIStatusError("bad request", response=mock_resp, body=None),
            is_free=True,
        )
        assert result["success"] is True

    def test_non_free_400_unknown(self):
        """Non-free + 400 status → unknown."""
        from openai import APIStatusError
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        result = _classify_openai_error(
            APIStatusError("bad request", response=mock_resp, body=None),
            is_free=False,
        )
        assert result["error_code"] == "unknown"


# ===========================================================================
# 4. Concurrent requests do not block (Req 1.6)
# ===========================================================================

class TestConcurrency:
    """Verify that multiple test_connectivity calls can run concurrently."""

    async def test_concurrent_openai_requests(self):
        """Multiple openai_compatible requests run concurrently via asyncio.gather."""
        call_count = 0

        async def mock_test_openai(url, api_key, model="", is_free=False):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate network latency
            return {"success": True}

        with patch(
            "main_routers.config_router._test_openai_compatible",
            side_effect=mock_test_openai,
        ):
            requests = [
                ConnectivityTestRequest(
                    url=f"https://api{i}.example.com/v1", api_key=f"sk-key-{i}"
                )
                for i in range(5)
            ]
            results = await asyncio.gather(
                *[_endpoint_test_connectivity(req) for req in requests]
            )

        assert len(results) == 5
        assert all(r["success"] is True for r in results)
        assert call_count == 5

    async def test_concurrent_websocket_requests(self):
        """Multiple websocket requests run concurrently via asyncio.gather."""
        call_count = 0

        async def mock_test_ws(url, api_key, model=""):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return {"success": True}

        with patch(
            "main_routers.config_router._test_websocket",
            side_effect=mock_test_ws,
        ):
            requests = [
                ConnectivityTestRequest(
                    url=f"wss://realtime{i}.example.com",
                    api_key=f"sk-ws-{i}",
                    provider_type="websocket",
                )
                for i in range(5)
            ]
            results = await asyncio.gather(
                *[_endpoint_test_connectivity(req) for req in requests]
            )

        assert len(results) == 5
        assert all(r["success"] is True for r in results)
        assert call_count == 5

    async def test_concurrent_mixed_requests(self):
        """Mixed openai_compatible and websocket requests run concurrently."""
        async def mock_test_openai(url, api_key, model="", is_free=False):
            await asyncio.sleep(0.05)
            return {"success": True}

        async def mock_test_ws(url, api_key, model=""):
            await asyncio.sleep(0.05)
            return {"success": True}

        with (
            patch(
                "main_routers.config_router._test_openai_compatible",
                side_effect=mock_test_openai,
            ),
            patch(
                "main_routers.config_router._test_websocket",
                side_effect=mock_test_ws,
            ),
        ):
            requests = [
                ConnectivityTestRequest(
                    url="https://api.example.com/v1", api_key="sk-http"
                ),
                ConnectivityTestRequest(
                    url="wss://realtime.example.com",
                    api_key="sk-ws",
                    provider_type="websocket",
                ),
                ConnectivityTestRequest(
                    url="https://api2.example.com/v1", api_key="sk-http2"
                ),
            ]
            results = await asyncio.gather(
                *[_endpoint_test_connectivity(req) for req in requests]
            )

        assert len(results) == 3
        assert all(r["success"] is True for r in results)

    async def test_concurrent_with_failures(self):
        """Concurrent requests where some fail — failures don't block others."""
        call_order = []

        async def mock_test_openai(url, api_key, model="", is_free=False):
            call_order.append(url)
            await asyncio.sleep(0.05)
            if "fail" in url:
                return {"success": False, "error": "timeout", "error_code": "timeout"}
            return {"success": True}

        with patch(
            "main_routers.config_router._test_openai_compatible",
            side_effect=mock_test_openai,
        ):
            requests = [
                ConnectivityTestRequest(
                    url="https://api-ok.example.com/v1", api_key="sk-1"
                ),
                ConnectivityTestRequest(
                    url="https://api-fail.example.com/v1", api_key="sk-2"
                ),
                ConnectivityTestRequest(
                    url="https://api-ok2.example.com/v1", api_key="sk-3"
                ),
            ]
            results = await asyncio.gather(
                *[_endpoint_test_connectivity(req) for req in requests]
            )

        assert len(results) == 3
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert results[1]["error_code"] == "timeout"
        assert results[2]["success"] is True
        # All three were called
        assert len(call_order) == 3


# ===========================================================================
# 5. End-to-end endpoint — unexpected exception handling
# ===========================================================================

class TestEndpointExceptionHandling:
    """Test the top-level try/except in test_connectivity."""

    async def test_unexpected_exception_returns_unknown(self):
        """If the helper raises an unexpected exception, endpoint returns unknown."""
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            side_effect=RuntimeError("totally unexpected"),
        ):
            req = ConnectivityTestRequest(
                url="https://api.example.com/v1", api_key="sk-key"
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is False
        assert result["error_code"] == "unknown"

    async def test_provider_type_case_insensitive(self):
        """provider_type is case-insensitive (e.g. 'WebSocket' → websocket)."""
        with patch(
            "main_routers.config_router._test_websocket",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_ws:
            req = ConnectivityTestRequest(
                url="wss://example.com",
                api_key="sk-key",
                provider_type="WebSocket",
            )
            result = await _endpoint_test_connectivity(req)
            mock_ws.assert_awaited_once()
            assert result["success"] is True

    async def test_is_free_passed_to_openai_compatible(self):
        """is_free flag is forwarded to _test_openai_compatible."""
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_http:
            req = ConnectivityTestRequest(
                url="https://api.example.com/v1",
                api_key="sk-free",
                is_free=True,
            )
            await _endpoint_test_connectivity(req)
            # Verify is_free=True was passed
            mock_http.assert_awaited_once_with(
                "https://api.example.com/v1", "sk-free", model="gpt-3.5-turbo", is_free=True
            )
