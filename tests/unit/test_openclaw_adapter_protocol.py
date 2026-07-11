from unittest.mock import MagicMock

import httpx

from brain.openclaw_adapter import OpenClawAdapter, _resolve_qwenpaw_urls


def _adapter_for_protocol_test() -> OpenClawAdapter:
    adapter = object.__new__(OpenClawAdapter)
    adapter.base_url = "http://127.0.0.1:8088"
    adapter.process_url = f"{adapter.base_url}/api/agent/process"
    adapter.responses_url = f"{adapter.base_url}/api/agent/compatible-mode/v1/responses"
    adapter.health_url = f"{adapter.base_url}/api/agent/health"
    adapter.version_url = f"{adapter.base_url}/api/version"
    adapter.console_chat_url = f"{adapter.base_url}/api/console/chat"
    adapter.api_variant = "unknown"
    adapter.auth_token = ""
    adapter.last_error = None
    adapter.reload_config = lambda: None
    return adapter


def test_resolve_qwenpaw_url_accepts_v2_endpoint():
    base_url, process_url, responses_url, health_url = _resolve_qwenpaw_urls(
        "http://127.0.0.1:8088/api/console/chat"
    )

    assert base_url == "http://127.0.0.1:8088"
    assert process_url == "http://127.0.0.1:8088/api/agent/process"
    assert responses_url.endswith("/api/agent/compatible-mode/v1/responses")
    assert health_url == "http://127.0.0.1:8088/api/agent/health"


def test_availability_prefers_qwenpaw_v2_version_endpoint(monkeypatch):
    adapter = _adapter_for_protocol_test()
    response = httpx.Response(200, json={"version": "2.0.0"})
    client = MagicMock()
    client.get.return_value = response
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=context))

    result = adapter.is_available()

    assert result["ready"] is True
    assert adapter.api_variant == "v2"
    client.get.assert_called_once_with("http://127.0.0.1:8088/api/version")


def test_availability_falls_back_to_legacy_health_after_version_server_error(monkeypatch):
    adapter = _adapter_for_protocol_test()
    client = MagicMock()
    client.get.side_effect = (
        httpx.Response(503, json={"detail": "not supported"}),
        httpx.Response(200, json={"status": "ok"}),
    )
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=context))

    result = adapter.is_available()

    assert result["ready"] is True
    assert adapter.api_variant == "legacy"
    assert [call.args[0] for call in client.get.call_args_list] == [
        "http://127.0.0.1:8088/api/version",
        "http://127.0.0.1:8088/api/agent/health",
    ]


def test_availability_falls_back_after_version_transport_error(monkeypatch):
    adapter = _adapter_for_protocol_test()
    client = MagicMock()
    client.get.side_effect = (
        httpx.ConnectTimeout(
            "version probe timed out",
            request=httpx.Request("GET", adapter.version_url),
        ),
        httpx.Response(200, json={"status": "ok"}),
    )
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=context))

    result = adapter.is_available()

    assert result["ready"] is True
    assert adapter.api_variant == "legacy"
    assert client.get.call_count == 2


def test_console_payload_uses_qwenpaw_runtime_content_types():
    adapter = _adapter_for_protocol_test()

    payload = adapter._build_console_payload(
        session_id="stable-session",
        user_id="user-a",
        channel="console",
        instruction="describe this image",
        attachments=[{"url": "data:image/png;base64,abc"}],
    )

    assert payload["user_id"] == "user-a"
    assert payload["input"][0]["content"] == [
        {"type": "text", "text": "describe this image"},
        {"type": "image", "image_url": "data:image/png;base64,abc"},
    ]


def test_sse_parser_keeps_completed_response_before_trailing_usage():
    payload = "\n".join(
        (
            'data: {"object":"response","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"text","text":"done"}]}]}',
            'data: {"type":"turn_usage","usage":{"total_tokens":12}}',
        )
    )

    parsed = OpenClawAdapter._parse_process_sse_payload(payload)

    assert parsed["object"] == "response"
    assert OpenClawAdapter._extract_reply_text(_adapter_for_protocol_test(), parsed) == "done"
