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

import main_routers.config_router as config_router
from main_routers.config_router import (
    ConnectivityTestRequest,
    ConnectivityTestResponse,
    _auto_resolve_provider_urls_for_save,
    _get_save_provider_api_key,
    _test_openai_compatible,
    _test_anthropic,
    _test_websocket,
    _test_vllm_omni_ws_handshake,
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
            voice_id="S_test",
            is_free=True,
        )
        assert req.provider_type == "websocket"
        assert req.voice_id == "S_test"
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

    async def test_anthropic_connectivity_sets_kimi_code_user_agent(self, monkeypatch):
        captured = {}

        class FakeChatAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def ainvoke(self, messages):
                captured["messages"] = messages
                return MagicMock(content="ok")

            async def aclose(self):
                captured["closed"] = True

        monkeypatch.setattr("utils.llm_client.ChatAnthropic", FakeChatAnthropic)

        result = await _test_anthropic(
            "https://api.kimi.com/coding",
            "sk-test",
            model="kimi-for-coding",
        )

        assert result["success"] is True
        assert captured["default_headers"]["User-Agent"] == "claude-code/0.1.0"
        assert captured["closed"] is True

    async def test_anthropic_connectivity_requires_model_for_non_kimi_endpoint(self, monkeypatch):
        constructor = MagicMock()
        monkeypatch.setattr("utils.llm_client.ChatAnthropic", constructor)

        result = await _test_anthropic(
            "https://api.anthropic.com",
            "sk-test",
            model="",
        )

        assert result == {"success": False, "error": "缺少模型 ID", "error_code": "missing_params"}
        constructor.assert_not_called()

    async def test_builtin_assist_accepts_any_successful_candidate_url(self):
        """内置辅助 provider 有多个候选 URL 时，任一通过即返回可用 URL。"""
        calls = []

        async def fake_test(url, api_key, model="gpt-3.5-turbo", is_free=False):
            calls.append(url)
            if "dashscope-us.aliyuncs.com" in url:
                return {"success": True}
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}

        fake_config = {
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            side_effect=fake_test,
        ):
            req = ConnectivityTestRequest(
                provider_key="qwen_intl",
                provider_scope="assist",
                api_key="sk-region-key",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        assert result["resolved_url"] == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
        assert "https://dashscope-us.aliyuncs.com/compatible-mode/v1" in calls

    async def test_builtin_assist_infers_anthropic_probe_from_url(self):
        """Built-in Anthropic URLs without provider_type still use the Anthropic probe."""
        fake_config = {
            "assist_api_providers": {
                "claude": {
                    "name": "Claude",
                    "openrouter_url": "https://api.anthropic.com/v1",
                    "conversation_model": "claude-sonnet-test",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_anthropic",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_anthropic, patch(
            "main_routers.config_router._test_openai_compatible",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_openai:
            req = ConnectivityTestRequest(
                provider_key="claude",
                provider_scope="assist",
                api_key="sk-anthropic",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        mock_anthropic.assert_awaited_once_with(
            "https://api.anthropic.com/v1",
            "sk-anthropic",
            model="claude-sonnet-test",
            is_free=False,
        )
        mock_openai.assert_not_awaited()

    async def test_builtin_assist_keeps_kimi_coding_v1_on_openai_probe(self):
        """Kimi's documented /coding/v1 OpenAI-compatible URL must not be treated as Messages API."""
        fake_config = {
            "assist_api_providers": {
                "kimi_code": {
                    "name": "Kimi Code",
                    "openrouter_url": "https://api.kimi.com/coding/v1",
                    "conversation_model": "kimi-for-coding",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_anthropic",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_anthropic, patch(
            "main_routers.config_router._test_openai_compatible",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_openai:
            req = ConnectivityTestRequest(
                provider_key="kimi_code",
                provider_scope="assist",
                api_key="sk-kimi",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        mock_openai.assert_awaited_once_with(
            "https://api.kimi.com/coding/v1",
            "sk-kimi",
            model="kimi-for-coding",
            is_free=False,
        )
        mock_anthropic.assert_not_awaited()

    async def test_builtin_mimo_assist_accepts_token_plan_url_override(self):
        """MiMo Token Plan may override the built-in MiMo assist endpoint."""
        calls = []

        async def fake_test(url, api_key, model="gpt-3.5-turbo", is_free=False):
            calls.append((url, api_key, model))
            return {"success": True}

        fake_config = {
            "assist_api_providers": {
                "mimo": {
                    "name": "MiMo",
                    "openrouter_url": "https://api.xiaomimimo.com/v1",
                    "conversation_model": "mimo-v2.5",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            side_effect=fake_test,
        ):
            req = ConnectivityTestRequest(
                provider_key="mimo",
                provider_scope="assist",
                url="https://token-plan-sgp.xiaomimimo.com/v1",
                api_key="tp-token-plan",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        assert result["resolved_url"] == "https://token-plan-sgp.xiaomimimo.com/v1"
        assert calls == [("https://token-plan-sgp.xiaomimimo.com/v1", "tp-token-plan", "mimo-v2.5")]

    async def test_builtin_non_mimo_assist_rejects_token_plan_url_override(self):
        """MiMo Token Plan override must not affect other built-in assist APIs."""
        fake_config = {
            "assist_api_providers": {
                "qwen": {
                    "name": "Qwen",
                    "openrouter_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "conversation_model": "qwen-plus",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
        ) as mock_http:
            req = ConnectivityTestRequest(
                provider_key="qwen",
                provider_scope="assist",
                url="https://token-plan-sgp.xiaomimimo.com/v1",
                api_key="sk-qwen",
            )
            result = await _endpoint_test_connectivity(req)

        mock_http.assert_not_awaited()
        assert result["success"] is False
        assert result["error_code"] == "missing_params"

    async def test_builtin_core_accepts_any_successful_candidate_url(self):
        """内置核心 provider 若配置多个候选 URL，任一通过即返回可用 URL。"""
        calls = []

        async def fake_test(url, api_key, model=""):
            calls.append((url, api_key, model))
            if "realtime-b.example.com" in url:
                return {"success": True}
            return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}

        fake_config = {
            "core_api_providers": {
                "dual_ws": {
                    "name": "双核心候选测试",
                    "core_url": "wss://realtime-a.example.com/v1",
                    "core_urls": [
                        "wss://realtime-a.example.com/v1",
                        "wss://realtime-b.example.com/v1",
                    ],
                    "core_model": "test-realtime-model",
                }
            }
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_websocket",
            side_effect=fake_test,
        ):
            req = ConnectivityTestRequest(
                provider_key="dual_ws",
                provider_scope="core",
                api_key="sk-region-key",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        assert result["resolved_url"] == "wss://realtime-b.example.com/v1"
        assert calls[-1][1] == "sk-region-key"

    async def test_save_auto_resolve_core_prefers_provider_keybook_key(self):
        """核心 provider 有专属 Key 时，保存前检测优先使用专属 Key 而非旧 coreApiKey。"""
        calls = []

        async def fake_test(url, api_key, model=""):
            calls.append((url, api_key, model))
            return {"success": True}

        fake_config = {
            "core_api_providers": {
                "qwen_intl": {
                    "name": "Qwen-Omni（阿里国际版）",
                    "core_url": "wss://realtime-a.example.com/v1",
                    "core_urls": [
                        "wss://realtime-a.example.com/v1",
                        "wss://realtime-b.example.com/v1",
                    ],
                    "core_model": "qwen3.5-omni-flash-realtime-2026-03-15",
                }
            },
            "assist_api_providers": {},
            "api_key_registry": {
                "qwen_intl": {
                    "config_field": "assistApiKeyQwenIntl",
                }
            },
        }
        core_cfg = {
            "coreApi": "qwen_intl",
            "assistApi": "qwen",
            "coreApiKey": "sk-old-core",
            "assistApiKeyQwenIntl": "sk-intl-key",
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_websocket",
            side_effect=fake_test,
        ):
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        assert result["success"] == 1
        assert calls[0][1] == "sk-intl-key"
        assert _get_save_provider_api_key(core_cfg, fake_config, "qwen_intl") == "sk-intl-key"

    async def test_save_auto_resolves_builtin_candidate_url(self):
        """保存配置时会自动检测候选 URL，并写入通过的 URL。"""
        calls = []

        async def fake_test(url, api_key, model="gpt-3.5-turbo", is_free=False):
            calls.append((url, api_key, model))
            if "dashscope-us.aliyuncs.com" in url:
                return {"success": True}
            return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}

        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            },
            "api_key_registry": {
                "qwen_intl": {
                    "config_field": "assistApiKeyQwenIntl",
                }
            },
        }
        core_cfg = {
            "coreApi": "qwen",
            "assistApi": "qwen_intl",
            "coreApiKey": "sk-core",
            "assistApiKeyQwenIntl": "sk-intl",
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            side_effect=fake_test,
        ):
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        assert result["total"] == 1
        assert result["success"] == 1
        assert result["resolved_urls"]["assist:qwen_intl"] == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
        assert core_cfg["resolvedProviderUrls"]["assist:qwen_intl"] == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
        assert calls[-1][1] == "sk-intl"

    async def test_save_auto_resolve_reuses_frontend_checked_url(self):
        """前端本轮已检测通过的 URL 会被复用，避免保存时重复检测。"""
        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            },
            "api_key_registry": {
                "qwen_intl": {
                    "config_field": "assistApiKeyQwenIntl",
                }
            },
        }
        core_cfg = {
            "assistApi": "qwen_intl",
            "coreApiKey": "sk-core",
            "assistApiKeyQwenIntl": "sk-intl",
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
        ) as mock_test:
            result = await _auto_resolve_provider_urls_for_save(
                core_cfg,
                {"assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"},
            )

        mock_test.assert_not_awaited()
        assert result["success"] == 1
        assert core_cfg["resolvedProviderUrls"]["assist:qwen_intl"] == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"

    async def test_save_auto_resolve_drops_stale_url_when_detection_fails(self):
        """保存前检测失败时不能继续保留旧的地域 URL。"""
        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            },
            "api_key_registry": {
                "qwen_intl": {
                    "config_field": "assistApiKeyQwenIntl",
                }
            },
        }
        core_cfg = {
            "assistApi": "qwen_intl",
            "coreApiKey": "sk-core",
            "assistApiKeyQwenIntl": "sk-intl",
            "resolvedProviderUrls": {
                "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            },
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "auth_failed", "error_code": "auth_failed"},
        ):
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        assert result["total"] == 1
        assert result["failed"] == 1
        assert core_cfg["resolvedProviderUrls"] == {}

    async def test_save_auto_resolve_keeps_unrelated_when_target_fails(self):
        """target 测失败时丢掉它自己的旧 resolved，但不该误伤同一份 dict 里
        其它 provider 的记忆。覆盖 CodeRabbit #3258131687 (失败丢旧值) 和
        Codex #3258589662 (保留无关 provider) 的组合语义。
        """
        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            },
            "api_key_registry": {
                "qwen_intl": {"config_field": "assistApiKeyQwenIntl"},
            },
        }
        core_cfg = {
            "assistApi": "qwen_intl",
            "coreApiKey": "sk-core",
            "assistApiKeyQwenIntl": "sk-intl",
            "resolvedProviderUrls": {
                "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                "assist:custom_unrelated": "https://example.com/v1",
            },
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "auth_failed", "error_code": "auth_failed"},
        ):
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        assert result["failed"] == 1
        assert core_cfg["resolvedProviderUrls"] == {
            "assist:custom_unrelated": "https://example.com/v1",
        }

    async def test_save_auto_resolve_skips_assist_provider_with_empty_key(self):
        """assist provider 的 key 缺失时不应该回退到 coreApiKey 去 probe：
        core/assist 是不同 provider 时 coreApiKey 是 OpenAI 的，拿去打 qwen_intl
        必然 401 → 误判失败 → 顺手 pop 掉之前测通的 region pin
        (Codex P2 #3258802582)。target 应该被 _build_save_connectivity_targets
        过滤掉，resolved 旧值保留。
        """
        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {
                "qwen_intl": {
                    "name": "阿里国际版",
                    "openrouter_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "openrouter_urls": [
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                    ],
                    "conversation_model": "qwen3.6-plus",
                }
            },
            "api_key_registry": {
                "qwen_intl": {"config_field": "assistApiKeyQwenIntl"},
            },
        }
        core_cfg = {
            "coreApi": "openai",
            "assistApi": "qwen_intl",
            "coreApiKey": "sk-openai-not-qwen",
            "resolvedProviderUrls": {
                "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            },
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config), patch(
            "main_routers.config_router._test_openai_compatible",
            new_callable=AsyncMock,
        ) as mock_test:
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        mock_test.assert_not_awaited()
        assert result["total"] == 0
        assert core_cfg["resolvedProviderUrls"] == {
            "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        }

    async def test_save_auto_resolve_keeps_unrelated_resolved_when_no_targets(self):
        """没有候选目标时保留历史 resolved URL：本次 save 没动 qwen_intl，
        别把 CosyVoice intl runtime 还要用的 US 端点记忆顺手清掉
        (Codex P1 #3258589662)。
        """
        core_cfg = {
            "coreApi": "openai",
            "assistApi": "openai",
            "coreApiKey": "sk-core",
            "resolvedProviderUrls": {
                "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            },
        }
        fake_config = {
            "core_api_providers": {},
            "assist_api_providers": {},
            "api_key_registry": {},
        }

        with patch("utils.api_config_loader.get_config", return_value=fake_config):
            result = await _auto_resolve_provider_urls_for_save(core_cfg)

        assert result["total"] == 0
        assert core_cfg["resolvedProviderUrls"] == {
            "assist:qwen_intl": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        }


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


# ===========================================================================
# 12. vLLM-Omni TTS handshake-only probe (#1764 review 第六轮)
#     验证 sub_type='vllm_omni_tts' 路径不发 OpenAI Realtime session.update
# ===========================================================================

class TestVllmOmniWsHandshake:
    """vLLM-Omni's /v1/audio/speech/stream uses the Qwen custom protocol
    (session.config / input.text / input.done) and does not understand the
    OpenAI Realtime session.update message. _test_vllm_omni_ws_handshake only
    performs the WebSocket handshake and immediately closes — it never sends
    any application-layer frame."""

    async def test_handshake_succeeds_without_sending_session_update(self):
        """Handshake succeeds and ws.send is never called (core assertion: no session.update)."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.send = AsyncMock()
        mock_conn.recv = AsyncMock()

        with patch("websockets.connect", return_value=mock_conn):
            result = await _test_vllm_omni_ws_handshake(
                "ws://10.0.1.92:8091/v1/audio/speech/stream", ""
            )

        assert result["success"] is True
        # 关键断言：握手探测路径绝不发任何应用层帧（session.update / 任何 send）
        mock_conn.send.assert_not_called()
        mock_conn.recv.assert_not_called()

    async def test_handshake_with_api_key_sets_authorization_header(self):
        """When api_key is provided, the Authorization header is set (matches _test_websocket behaviour)."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            await _test_vllm_omni_ws_handshake(
                "ws://10.0.1.92:8091/v1/audio/speech/stream", "sk-test"
            )
            headers = mock_connect.call_args[1].get("additional_headers", {})
            assert headers.get("Authorization") == "Bearer sk-test"

    async def test_handshake_empty_api_key_no_authorization_header(self):
        """When api_key is empty, no Authorization header is sent (vLLM self-hosted deployments commonly run without auth)."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            await _test_vllm_omni_ws_handshake(
                "ws://10.0.1.92:8091/v1/audio/speech/stream", ""
            )
            headers = mock_connect.call_args[1].get("additional_headers", {})
            assert "Authorization" not in headers

    async def test_handshake_url_passed_through_unchanged(self):
        """URL is passed through unchanged; unlike _test_websocket it does NOT append a ?model= query parameter."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=mock_conn) as mock_connect:
            await _test_vllm_omni_ws_handshake(
                "ws://10.0.1.92:8091/v1/audio/speech/stream", "sk-test"
            )
            ws_url = mock_connect.call_args[0][0]
            assert ws_url == "ws://10.0.1.92:8091/v1/audio/speech/stream"
            assert "?model=" not in ws_url

    async def test_handshake_auth_failed_401(self):
        """A 401 during handshake → auth_failed (matches _test_websocket's error_code)."""
        exc = Exception("HTTP 401")
        exc.status_code = 401
        with patch("websockets.connect", side_effect=exc):
            result = await _test_vllm_omni_ws_handshake(
                "ws://example.com/v1/audio/speech/stream", "wrong-key"
            )

        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_handshake_dns_error(self):
        """OSError 'getaddrinfo' → dns_error。"""
        with patch(
            "websockets.connect",
            side_effect=OSError("[Errno 11001] getaddrinfo failed"),
        ):
            result = await _test_vllm_omni_ws_handshake(
                "ws://nonexistent.example.com/v1/audio/speech/stream", ""
            )

        assert result["success"] is False
        assert result["error_code"] == "dns_error"

    async def test_handshake_connection_refused(self):
        """OSError 'connection refused' → connection_refused。"""
        with patch(
            "websockets.connect",
            side_effect=OSError("[Errno 111] Connection refused"),
        ):
            result = await _test_vllm_omni_ws_handshake(
                "ws://localhost:9999/v1/audio/speech/stream", ""
            )

        assert result["success"] is False
        assert result["error_code"] == "connection_refused"

    async def test_handshake_timeout(self):
        """asyncio.TimeoutError → timeout。"""
        with patch("websockets.connect", side_effect=asyncio.TimeoutError()):
            result = await _test_vllm_omni_ws_handshake(
                "ws://10.0.1.92:8091/v1/audio/speech/stream", ""
            )

        assert result["success"] is False
        assert result["error_code"] == "timeout"

    async def test_endpoint_dispatches_sub_type_to_handshake(self):
        """End-to-end: sub_type='vllm_omni_tts' must dispatch to
        _test_vllm_omni_ws_handshake instead of _test_websocket. This is the
        core contract behind the connectivity-mis-detection fix."""
        with patch(
            "main_routers.config_router._test_vllm_omni_ws_handshake",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_handshake, patch(
            "main_routers.config_router._test_websocket",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_realtime_ws:
            req = ConnectivityTestRequest(
                url="ws://10.0.1.92:8091/v1/audio/speech/stream",
                api_key="",
                model="Qwen3-TTS",
                provider_type="websocket",
                sub_type="vllm_omni_tts",
            )
            await _endpoint_test_connectivity(req)

        mock_handshake.assert_awaited_once()
        mock_realtime_ws.assert_not_called()

    async def test_endpoint_dispatches_doubao_tts_probe(self, monkeypatch):
        captured = {}

        async def fake_probe(url, api_key, model="", voice_id=""):
            captured.update({
                "url": url,
                "api_key": api_key,
                "model": model,
                "voice_id": voice_id,
            })
            return {"success": True}

        monkeypatch.setattr(config_router, "_test_doubao_tts_connectivity", fake_probe)
        with patch(
            "main_routers.config_router._test_openai_compatible",
            new=AsyncMock(return_value={"success": False}),
        ) as mock_openai:
            req = ConnectivityTestRequest(
                url="https://openspeech.bytedance.com",
                api_key="ark-test",
                model="seed-icl-2.0",
                provider_type="tts",
                sub_type="doubao_tts",
                voice_id="S_test",
            )
            result = await _endpoint_test_connectivity(req)

        assert result["success"] is True
        assert captured == {
            "url": "https://openspeech.bytedance.com",
            "api_key": "ark-test",
            "model": "seed-icl-2.0",
            "voice_id": "S_test",
        }
        mock_openai.assert_not_called()

    async def test_endpoint_websocket_without_sub_type_uses_realtime_probe(self):
        """End-to-end inverse contract: when sub_type is omitted, the request
        still routes to _test_websocket (the OpenAI Realtime path) so genuine
        Realtime providers like Qwen Realtime / Step are not hit by mistake."""
        with patch(
            "main_routers.config_router._test_vllm_omni_ws_handshake",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_handshake, patch(
            "main_routers.config_router._test_websocket",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_realtime_ws:
            req = ConnectivityTestRequest(
                url="wss://realtime.example.com",
                api_key="sk-test",
                model="step-audio-2",
                provider_type="websocket",
            )
            await _endpoint_test_connectivity(req)

        mock_realtime_ws.assert_awaited_once()
        mock_handshake.assert_not_called()

    async def test_handshake_auth_failed_via_invalidstatus_response_status_code(self):
        """Real websockets >=15 raises InvalidStatus with status code at e.response.status_code,
        NOT at e.status_code. Production code's second-layer fallback (getattr(e, 'response', None)
        → getattr(_resp, 'status_code', None)) must catch it. The earlier test (test_handshake_auth_failed_401)
        used a bare Exception with .status_code attribute and only exercises the FIRST fallback layer."""
        # Build a fake exception that mimics websockets.exceptions.InvalidStatus shape:
        # bare attribute lookup `e.status_code` returns None; `e.response.status_code` returns 401.
        class _FakeResponse:
            status_code = 401

        class _FakeInvalidStatus(Exception):
            def __init__(self):
                super().__init__("server rejected WebSocket connection: HTTP 401")
                self.response = _FakeResponse()

        with patch("websockets.connect", side_effect=_FakeInvalidStatus()):
            result = await _test_vllm_omni_ws_handshake(
                "ws://example.com/v1/audio/speech/stream", "wrong-key"
            )

        assert result["success"] is False
        assert result["error_code"] == "auth_failed", (
            f"Expected auth_failed via InvalidStatus.response.status_code path, got {result}"
        )

    async def test_handshake_forbidden_via_invalidstatus_403(self):
        """403 via InvalidStatus.response.status_code → auth_failed (same bucket as 401)."""
        class _FakeResponse:
            status_code = 403

        class _FakeInvalidStatus(Exception):
            def __init__(self):
                super().__init__("server rejected WebSocket connection: HTTP 403")
                self.response = _FakeResponse()

        with patch("websockets.connect", side_effect=_FakeInvalidStatus()):
            result = await _test_vllm_omni_ws_handshake(
                "ws://example.com/v1/audio/speech/stream", "expired-key"
            )

        assert result["success"] is False
        assert result["error_code"] == "auth_failed"

    async def test_endpoint_mode1_ignores_sub_type_injection(self):
        """Security gating contract: when both provider_key and provider_scope are set (Mode 1
        built-in provider), sub_type is dropped and Mode 1's resolved provider_type drives the
        probe. A malicious frontend cannot force handshake-only probe on a non-vllm-omni
        built-in provider via sub_type injection.
        (#1764 review round 6 - gating at config_router.py line ~1937-1942)"""
        with patch(
            "main_routers.config_router._test_vllm_omni_ws_handshake",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_handshake, patch(
            "main_routers.config_router._test_websocket",
            new=AsyncMock(return_value={"success": True}),
        ) as mock_realtime_ws:
            # Mode 1 built-in: qwen + scope=core resolves to provider_type="websocket"
            # from api_providers.json. sub_type='vllm_omni_tts' would be a malicious
            # injection attempt, but the gating (line 1941-1943) sets sub_type="" for Mode 1.
            req = ConnectivityTestRequest(
                provider_key="qwen",
                provider_scope="core",
                sub_type="vllm_omni_tts",
            )
            await _endpoint_test_connectivity(req)

        # Mode 1 must use _test_websocket regardless of sub_type. Handshake probe must NEVER fire.
        mock_handshake.assert_not_called()
        # _test_websocket should be called (Mode 1 resolved provider_type="websocket" from api_providers.json).
        mock_realtime_ws.assert_awaited_once()
