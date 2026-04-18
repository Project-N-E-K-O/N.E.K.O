"""
Unit tests for OpenFang provider detection and config sync.

Covers:
- _detect_provider_info: all provider types, edge cases, proxy URL early-exit
- sync_config: key-free provider (Ollama) vs cloud provider validation
"""

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from brain.openfang_adapter import _detect_provider_info, OpenFangAdapter


# ── _detect_provider_info ─────────────────────────────────────


class TestDetectProviderInfo:
    """Test _detect_provider_info for all provider types and edge cases."""

    # -- Direct cloud providers --

    def test_anthropic_by_url(self):
        r = _detect_provider_info("https://api.anthropic.com/v1", "claude-sonnet-4-20250514")
        assert r["provider"] == "anthropic"
        assert r["needs_proxy"] is False
        assert r["api_key_env"] == "ANTHROPIC_API_KEY"

    def test_openai_by_url(self):
        r = _detect_provider_info("https://api.openai.com/v1", "gpt-4.1")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is False
        assert r["api_key_env"] == "OPENAI_API_KEY"

    def test_groq_by_url(self):
        r = _detect_provider_info("https://api.groq.com/openai/v1", "llama-3.3-70b")
        assert r["provider"] == "groq"
        assert r["needs_proxy"] is False

    def test_groq_by_model(self):
        r = _detect_provider_info("https://custom-endpoint.example.com/v1", "groq-llama-70b")
        assert r["provider"] == "groq"

    def test_gemini_native_api(self):
        """Native Gemini API (no /openai/) → provider=gemini for native driver."""
        r = _detect_provider_info(
            "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash"
        )
        assert r["provider"] == "gemini"
        assert r["needs_proxy"] is False
        assert r["api_key_env"] == "GEMINI_API_KEY"

    def test_gemini_openai_compat_endpoint(self):
        """Gemini /openai/ endpoint → provider=openai (Bearer token + OpenAI tools)."""
        r = _detect_provider_info(
            "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3-flash-preview"
        )
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is False
        assert r["api_key_env"] == "GEMINI_API_KEY"

    def test_gemini_model_on_unknown_host_falls_through(self):
        """Non-Google host + gemini model name → NOT native Gemini, falls to proxy fallback."""
        r = _detect_provider_info("https://custom-endpoint.example.com/v1", "gemini-2.5-pro")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is True

    def test_deepseek_by_url(self):
        r = _detect_provider_info("https://api.deepseek.com/v1", "deepseek-chat")
        assert r["provider"] == "deepseek"
        assert r["needs_proxy"] is False

    def test_deepseek_by_model(self):
        r = _detect_provider_info("https://custom.example.com/v1", "deepseek-r1")
        assert r["provider"] == "deepseek"

    # -- Proxy URLs: must always go through proxy, never match by model name --

    def test_openrouter_with_gemini_model(self):
        """OpenRouter URL + gemini model → must be proxy, NOT direct gemini."""
        r = _detect_provider_info("https://openrouter.ai/api/v1", "gemini-2.5-flash")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is True
        assert r["api_key_env"] == "OPENAI_API_KEY"
        assert r["effective_url"] != "https://openrouter.ai/api/v1"

    def test_openrouter_with_deepseek_model(self):
        r = _detect_provider_info("https://openrouter.ai/api/v1", "deepseek/deepseek-r1")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is True
        assert r["api_key_env"] == "OPENAI_API_KEY"
        assert r["effective_url"] != "https://openrouter.ai/api/v1"

    # -- Ollama: loopback, LAN, non-standard port --

    def test_ollama_localhost_default_port(self):
        r = _detect_provider_info("http://localhost:11434/v1", "llama3")
        assert r["provider"] == "ollama"
        assert r["needs_proxy"] is False
        assert r["api_key_env"] == ""

    def test_ollama_127_default_port(self):
        r = _detect_provider_info("http://127.0.0.1:11434/v1", "qwen2.5")
        assert r["provider"] == "ollama"
        assert r["api_key_env"] == ""

    def test_ollama_ipv6_default_port(self):
        r = _detect_provider_info("http://[::1]:11434/v1", "llama3")
        assert r["provider"] == "ollama"

    def test_ollama_non_standard_port_with_ollama_model(self):
        """Loopback + 'ollama' in model name → Ollama, even on non-default port."""
        r = _detect_provider_info("http://localhost:8080/v1", "ollama/llama3")
        assert r["provider"] == "ollama"
        assert r["needs_proxy"] is False

    def test_ollama_lan_default_port(self):
        """LAN IP (192.168.x.x) + default port → Ollama."""
        r = _detect_provider_info("http://192.168.1.100:11434/v1", "llama3")
        assert r["provider"] == "ollama"
        assert r["needs_proxy"] is False

    def test_ollama_lan_10_network(self):
        """10.x.x.x LAN + default port → Ollama."""
        r = _detect_provider_info("http://10.0.0.5:11434/v1", "mistral")
        assert r["provider"] == "ollama"

    def test_ollama_lan_172_network(self):
        """172.16-31.x.x (RFC1918) + default port → Ollama."""
        r = _detect_provider_info("http://172.16.0.10:11434/v1", "llama3")
        assert r["provider"] == "ollama"

    def test_172_non_rfc1918_not_lan(self):
        """172.15.x.x is NOT RFC1918 private range, should not be detected as LAN Ollama."""
        r = _detect_provider_info("http://172.15.0.1:8080/v1", "llama3")
        assert r["provider"] != "ollama"

    def test_ollama_lan_https(self):
        """HTTPS LAN address + default port → Ollama (not just HTTP)."""
        r = _detect_provider_info("https://192.168.1.100:11434/v1", "llama3")
        assert r["provider"] == "ollama"

    def test_ollama_reverse_proxy_path_local(self):
        """LAN + URL path containing '/ollama' → Ollama (reverse-proxy setup)."""
        r = _detect_provider_info("http://192.168.1.1:8080/ollama/v1", "llama3")
        assert r["provider"] == "ollama"

    def test_ollama_reverse_proxy_path_remote(self):
        """Remote host + URL path containing '/ollama' → still Ollama."""
        r = _detect_provider_info("https://example.com/ollama/v1", "llama3")
        assert r["provider"] == "ollama"
        assert r["needs_proxy"] is False

    def test_malformed_port_does_not_crash(self):
        """Malformed port in URL should not raise, falls to fallback."""
        r = _detect_provider_info("http://localhost:abc/v1", "llama3")
        assert r["provider"] == "openai"

    def test_ollama_remote_default_port(self):
        """Remote host (not LAN/loopback) with :11434 → still Ollama (strong signal)."""
        r = _detect_provider_info("http://my-gpu-server.example.com:11434/v1", "llama3")
        assert r["provider"] == "ollama"
        assert r["needs_proxy"] is False

    def test_local_non_ollama_port_non_ollama_model(self):
        """Loopback + non-Ollama port + non-Ollama model → fallback to proxy."""
        r = _detect_provider_info("http://localhost:8080/v1", "llama3")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is True

    # -- Fallback: unknown provider → openai + proxy --

    def test_unknown_provider_fallback(self):
        r = _detect_provider_info("https://api.custom-llm.example.com/v1", "my-model")
        assert r["provider"] == "openai"
        assert r["needs_proxy"] is True
        assert r["api_key_env"] == "OPENAI_API_KEY"


# ── sync_config: key-free provider handling ───────────────────


class TestSyncConfigKeyFreeProvider:
    """Test that sync_config allows Ollama (no API key) to proceed."""

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("brain.openfang_adapter.get_config_manager")
    def test_ollama_no_key_proceeds(self, mock_get_cm):
        """Ollama with empty api_key should NOT be blocked by sync_config."""
        cm = MagicMock()
        cm.get_model_api_config.return_value = {
            "model": "llama3",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
        }
        mock_get_cm.return_value = cm

        adapter = OpenFangAdapter(base_url="http://127.0.0.1:12345")

        # Mock httpx to avoid real HTTP calls — make it raise to short-circuit
        # but the key point is: we should NOT return False before reaching HTTP
        with patch("brain.openfang_adapter.httpx.AsyncClient") as mock_client_cls, \
             patch.object(OpenFangAdapter, "_write_openfang_model_config"):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Config/set and reload endpoints
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "ok"
            mock_client.post.return_value = mock_resp
            mock_client.put.return_value = mock_resp

            result = self._run(adapter.sync_config())

        # Should succeed (not blocked by empty key)
        assert result is True
        # Provider should be detected as ollama
        assert adapter._provider_info["provider"] == "ollama"
        # Critical: must NOT push to /api/providers/{provider}/key for local providers
        for call in mock_client.post.call_args_list:
            url = call.args[0] if call.args else call.kwargs.get("url", "")
            assert "/api/providers/ollama/key" not in url, \
                f"Local provider should not push key, but got POST to {url}"
            assert "/api/providers/openai/key" not in url, \
                f"Local provider should not fall back to openai key push, got POST to {url}"

    @patch("brain.openfang_adapter.get_config_manager")
    def test_cloud_provider_no_key_blocked(self, mock_get_cm):
        """Cloud provider (OpenAI) with empty api_key should be blocked."""
        cm = MagicMock()
        cm.get_model_api_config.return_value = {
            "model": "gpt-4.1",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
        }
        mock_get_cm.return_value = cm

        adapter = OpenFangAdapter()
        result = self._run(adapter.sync_config())

        assert result is False

    @patch("brain.openfang_adapter.get_config_manager")
    def test_gemini_openai_compat_with_key_proceeds(self, mock_get_cm):
        """Gemini OpenAI-compat endpoint with valid API key should proceed as openai provider."""
        cm = MagicMock()
        cm.get_model_api_config.return_value = {
            "model": "gemini-3-flash-preview",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": "AIza-test-key-12345",
        }
        mock_get_cm.return_value = cm

        adapter = OpenFangAdapter(base_url="http://127.0.0.1:12345")

        with patch("brain.openfang_adapter.httpx.AsyncClient") as mock_client_cls, \
             patch.object(OpenFangAdapter, "_write_openfang_model_config"):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "ok"
            mock_client.post.return_value = mock_resp
            mock_client.put.return_value = mock_resp

            result = self._run(adapter.sync_config())

        assert result is True
        assert adapter._provider_info["provider"] == "openai"

    @patch("brain.openfang_adapter.get_config_manager")
    def test_no_model_returns_false(self, mock_get_cm):
        """Missing model should return False immediately."""
        cm = MagicMock()
        cm.get_model_api_config.return_value = {
            "model": "",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
        }
        mock_get_cm.return_value = cm

        adapter = OpenFangAdapter()
        result = self._run(adapter.sync_config())
        assert result is False

    @patch("brain.openfang_adapter.get_config_manager")
    def test_no_base_url_returns_false(self, mock_get_cm):
        """Missing base_url should return False immediately."""
        cm = MagicMock()
        cm.get_model_api_config.return_value = {
            "model": "llama3",
            "base_url": "",
            "api_key": "",
        }
        mock_get_cm.return_value = cm

        adapter = OpenFangAdapter()
        result = self._run(adapter.sync_config())
        assert result is False
