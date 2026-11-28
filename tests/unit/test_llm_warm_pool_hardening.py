"""Unit tests for PR1 HTTP keepalive, stale-connection retry, shutdown, and timeout hardening."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from utils.http.pool_utils import (
    DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
    DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
    build_split_http_timeout,
    install_stale_connection_retry,
)

pytestmark = pytest.mark.unit


# ─────────────────────────── timeout splitting ───────────────────────────


def test_build_split_http_timeout_splits_bare_numbers_and_preserves_caps():
    assert build_split_http_timeout(None) is None

    explicit = httpx.Timeout(connect=3.0, read=45.0, write=12.0, pool=4.0)
    assert build_split_http_timeout(explicit) is explicit

    sentinel = object()
    assert build_split_http_timeout(sentinel) is sentinel
    assert build_split_http_timeout(True) is True

    long_timeout = build_split_http_timeout(180.0)
    assert long_timeout == httpx.Timeout(
        connect=DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
        read=180.0,
        write=DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
        pool=DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
    )

    # Short budgets must never be enlarged on connect/write/pool.
    short_timeout = build_split_http_timeout(8.0)
    assert short_timeout == httpx.Timeout(connect=8.0, read=8.0, write=8.0, pool=8.0)

    # Memory's 110s ceiling stays <=110s with fast connect/pool failure.
    from config.memory_settings import MEMORY_LLM_HARD_TIMEOUT_SECONDS

    memory_timeout = build_split_http_timeout(MEMORY_LLM_HARD_TIMEOUT_SECONDS)
    assert memory_timeout == httpx.Timeout(
        connect=10.0,
        read=float(MEMORY_LLM_HARD_TIMEOUT_SECONDS),
        write=30.0,
        pool=10.0,
    )


# ──────────────────── stale-connection retry (async + sync) ────────────────────


class _ScriptedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        await request.aread()
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ScriptedSyncTransport(httpx.BaseTransport):
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        request.read()
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "error",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ReadError("[WinError 10054] connection reset"),
        httpx.WriteError("broken pipe"),
    ],
)
def test_sync_stale_connection_errors_are_retried_once(error):
    transport = _ScriptedSyncTransport(error, httpx.Response(200, text="ok"))
    with install_stale_connection_retry(httpx.Client(transport=transport)) as client:
        resp = client.post("https://llm.test/v1/chat/completions", json={"hi": 1})
    assert resp.status_code == 200
    assert resp.text == "ok"
    assert transport.calls == 2


def test_sync_second_failure_and_non_replayable_stream_are_not_retried():
    err = httpx.RemoteProtocolError("Server disconnected")
    t1 = _ScriptedSyncTransport(err, err, httpx.Response(200))
    with install_stale_connection_retry(httpx.Client(transport=t1)) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            client.post("https://llm.test/v1/chat/completions", json={"hi": 1})
    assert t1.calls == 2

    def _gen():
        yield b"chunk"

    t2 = _ScriptedSyncTransport(err, httpx.Response(200))
    with install_stale_connection_retry(httpx.Client(transport=t2)) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            client.post("https://llm.test/v1/chat/completions", content=_gen())
    assert t2.calls == 1


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_sync_non_stale_errors_are_not_retried(error):
    transport = _ScriptedSyncTransport(error, httpx.Response(200))
    with install_stale_connection_retry(httpx.Client(transport=transport)) as client:
        with pytest.raises(type(error)):
            client.post("https://llm.test/v1/chat/completions", json={"hi": 1})
    assert transport.calls == 1


def test_install_stale_connection_retry_is_idempotent_and_fake_safe():
    err = httpx.RemoteProtocolError("Server disconnected")
    t_sync = _ScriptedSyncTransport(err, err, httpx.Response(200))
    with httpx.Client(transport=t_sync) as client:
        install_stale_connection_retry(install_stale_connection_retry(client))
        with pytest.raises(httpx.RemoteProtocolError):
            client.post("https://llm.test/v1/chat/completions", json={})
    assert t_sync.calls == 2

    fake = SimpleNamespace(name="fake-client-without-transport")
    assert install_stale_connection_retry(fake) is fake


# ────────────────── ChatOpenAI / ChatAnthropic symmetry & warm_pool ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("client_name", ["ChatOpenAI", "ChatAnthropic"])
async def test_chat_clients_share_keepalive_stale_retry_and_split_timeout(client_name):
    from utils import llm_client

    client_cls = getattr(llm_client, client_name)
    warm = client_cls(
        model="test-model",
        base_url="https://api.example.test/v1",
        api_key="sk-test",
        timeout=180.0,
        keepalive_expiry=DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    )
    plain = client_cls(
        model="test-model",
        base_url="https://api.example.test/v1",
        api_key="sk-test",
    )
    try:
        assert warm._aclient._client._transport._pool._keepalive_expiry == 55.0
        assert warm._client._client._transport._pool._keepalive_expiry == 55.0
        assert plain._aclient._client._transport._pool._keepalive_expiry == 5.0

        assert getattr(warm._aclient._client._transport.handle_async_request, "_neko_stale_retry", False)
        assert getattr(warm._client._client._transport.handle_request, "_neko_stale_retry", False)

        expected_timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
        assert warm._aclient.timeout == expected_timeout
        assert warm._client.timeout == expected_timeout
        assert warm.is_closed is False
    finally:
        await warm.aclose()
        await plain.aclose()

    assert warm.is_closed is True


def test_chat_clients_split_per_call_numeric_timeout():
    from utils.llm_client import ChatAnthropic, ChatOpenAI

    openai_llm = ChatOpenAI(model="m", base_url="https://api.example.test/v1", api_key="k")
    anthropic_llm = ChatAnthropic(model="claude-test", base_url="https://api.anthropic.com", api_key="k")
    try:
        params = openai_llm._params([{"role": "user", "content": "hi"}], timeout=60.0)
        assert params["timeout"] == httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

        payload = anthropic_llm._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": 60.0})
        assert payload["timeout"] == httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
    finally:
        openai_llm.close()
        anthropic_llm.close()


@pytest.mark.asyncio
async def test_chat_anthropic_reuses_shared_default_ssl_context(monkeypatch):
    from utils.llm_client import ChatAnthropic, lifecycle as lifecycle_module

    original_create = lifecycle_module.ssl.create_default_context
    calls = []

    def counting_create(*args, **kwargs):
        calls.append((args, kwargs))
        return original_create(*args, **kwargs)

    monkeypatch.setattr(lifecycle_module, "_DEFAULT_SSL_CONTEXT", None)
    monkeypatch.setattr(lifecycle_module.ssl, "create_default_context", counting_create)

    clients = [
        ChatAnthropic(model="claude-a", base_url="https://api.anthropic.com", api_key="k"),
        ChatAnthropic(model="claude-b", base_url="https://api.anthropic.com", api_key="k"),
    ]
    try:
        assert len(calls) == 1
    finally:
        for c in clients:
            await c.aclose()


@pytest.mark.asyncio
async def test_warm_pool_uses_55s_keepalive_and_rebuilds_closed_clients():
    from utils.llm_client import warm_pool

    assert warm_pool.WARM_POOL_KEEPALIVE_SECONDS == 55.0

    created = []

    class _ClosableFake:
        def __init__(self):
            self.is_closed = False

        async def aclose(self):
            self.is_closed = True

    async def _create(*_args, **_kwargs):
        inst = _ClosableFake()
        created.append(inst)
        return inst

    with patch("utils.llm_client.create_chat_llm_async", _create):
        first = await warm_pool.get_warm_chat_llm("m1", "https://x.test/v1", "k", max_retries=0)
        second = await warm_pool.get_warm_chat_llm("m1", "https://x.test/v1", "k", max_retries=0)
        assert second is first
        await first.aclose()
        rebuilt = await warm_pool.get_warm_chat_llm("m1", "https://x.test/v1", "k", max_retries=0)
        assert rebuilt is not first
        assert len(created) == 2
        await warm_pool.close_warm_chat_llms()
        assert rebuilt.is_closed is True


# ─────────────── decision_client loopback reuse, stale retry, and close ───────────────


async def _read_http_request(reader: asyncio.StreamReader) -> None:
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length:
        await reader.readexactly(length)


@pytest.mark.asyncio
async def test_decision_http_client_reuses_connection_and_retries_stale_drop():
    from utils.decision_client import adapters

    assert adapters.DECISION_KEEPALIVE_SECONDS == 55.0
    await adapters.aclose_decision_http_clients()

    connections: list[asyncio.StreamWriter] = []
    drop_on_second_reuse = {"enabled": False}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)
        conn_idx = len(connections)
        try:
            await _read_http_request(reader)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n{"ok":123}')
            await writer.drain()
            while True:
                await _read_http_request(reader)
                if drop_on_second_reuse["enabled"] and conn_idx == 1:
                    drop_on_second_reuse["enabled"] = False
                    writer.close()
                    return
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n{"ok":123}')
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/decide"
    try:
        # 1) Warm reuse: two back-to-back calls share a single TCP connection.
        r1 = await adapters._post_decision_request(url, {"a": 1}, {}, timeout=8.0)
        r2 = await adapters._post_decision_request(url, {"a": 2}, {}, timeout=8.0)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(connections) == 1

        # 2) Stale drop on reuse: peer closes right as request 3 lands -> retried once on conn 2.
        drop_on_second_reuse["enabled"] = True
        r3 = await adapters._post_decision_request(url, {"a": 3}, {}, timeout=8.0)
        assert r3.status_code == 200
        assert len(connections) == 2

        loop = asyncio.get_running_loop()
        pooled = adapters._http_clients[loop]
        assert pooled.is_closed is False
        await adapters.aclose_decision_http_clients()
        assert loop not in adapters._http_clients
        assert pooled.is_closed is True
    finally:
        await adapters.aclose_decision_http_clients()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_decision_post_preserves_short_float_and_splits_long_timeout(monkeypatch):
    from utils.decision_client import adapters

    recorded: list[object] = []

    class _FakeClient:
        is_closed = False

        async def post(self, endpoint, *, json, headers, timeout):
            recorded.append(timeout)
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(adapters, "_decision_http_client", lambda: _FakeClient())

    await adapters._post_decision_request("https://x.test/d", {}, {}, timeout=8.0)
    await adapters._post_decision_request("https://x.test/d", {}, {}, timeout=60.0)

    assert recorded[0] == 8.0
    assert recorded[1] == httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


# ─────────────── long-lived clients, embeddings, and shutdown wiring ───────────────


@pytest.mark.asyncio
async def test_long_lived_clients_configure_55s_keepalive_and_bounded_defaults(monkeypatch):
    from config.nekopower_settings import DSH_FORWARD_CONNECT_SECONDS, DSH_MODEL_RETRIES
    from main_logic.omni_offline_client import _streaming
    from nekopower.dsh.forward import ModelForwarder
    from nekopower.runtime.model import LLMClientProvider
    from nekopower.runtime.protocol import ModelConfig
    from utils.language_utils import TranslationService
    from utils.llm_client import OpenAIEmbeddings

    # NekoPower defaults tightened from 300s x 3 (15 min) to 180s x 2 (6 min).
    cfg = ModelConfig(model="fake-model")
    assert cfg.request_timeout_seconds == 180.0
    assert cfg.max_retries == 1
    assert DSH_MODEL_RETRIES == 1
    assert DSH_FORWARD_CONNECT_SECONDS == 15.0

    # LLMClientProvider forwards 55s keepalive + bounded defaults.
    captured_np = {}

    async def _fake_np_create(*_args, **kwargs):
        captured_np.update(kwargs)
        return SimpleNamespace(aclose=AsyncMock())

    with patch("utils.llm_client.create_chat_llm_async", _fake_np_create):
        provider = LLMClientProvider(cfg)
        await provider._create_client()
    assert captured_np["keepalive_expiry"] == 55.0
    assert captured_np["timeout"] == 180.0
    assert captured_np["max_retries"] == 1

    # ModelForwarder shared httpx client has 55s keepalive and stale retry.
    forwarder = ModelForwarder(on_usage=lambda *_args: None, report_tool="mcp__host__report")
    try:
        http = await forwarder._http()
        assert http._transport._pool._keepalive_expiry == 55.0
        assert getattr(http._transport.handle_async_request, "_neko_stale_retry", False)
    finally:
        await forwarder.aclose()

    # ConfiguredDecisionLLM cached client passes 55s keepalive, 120s timeout, max_retries=0.
    from nekopower.host.decision import ConfiguredDecisionLLM, TIER_COARSE

    captured_dec = {}

    class _FakeCfgMgr:
        def get_model_api_config(self, _slot):
            return {
                "model": "m-dec",
                "base_url": "https://dec.test/v1",
                "api_key": "k-dec",
                "provider_type": "openai_compatible",
            }

    async def _fake_dec_create(*_args, **kwargs):
        captured_dec.update(kwargs)
        return SimpleNamespace(aclose=AsyncMock())

    with (
        patch("utils.config_manager.get_config_manager", lambda migrate=False: _FakeCfgMgr()),
        patch("utils.llm_client.create_chat_llm_async", _fake_dec_create),
    ):
        dec_llm = ConfiguredDecisionLLM()
        await dec_llm._client(TIER_COARSE, 256)
        await dec_llm.aclose()
    assert captured_dec["keepalive_expiry"] == 55.0
    assert captured_dec["timeout"] == 120.0
    assert captured_dec["max_retries"] == 0

    # OmniOfflineClient.__init__ passes 55s keepalive.
    from main_logic.omni_offline_client import _client as offline_client_mod

    captured_init = {}

    def _fake_init_create(*_args, **kwargs):
        captured_init.update(kwargs)
        return SimpleNamespace(
            max_completion_tokens=kwargs.get("max_completion_tokens"),
            aclose=AsyncMock(),
            close=lambda: None,
        )

    monkeypatch.setattr(offline_client_mod, "create_chat_llm", _fake_init_create)
    offline_client_mod.OmniOfflineClient(
        api_key="k",
        base_url="https://x.test/v1",
        model="conv",
    )
    assert captured_init["keepalive_expiry"] == 55.0
    assert captured_init["max_retries"] == 0

    # OmniOfflineClient.switch_model passes 55s keepalive.
    captured_switch = {}

    async def _fake_switch_create(*_args, **kwargs):
        captured_switch.update(kwargs)
        return SimpleNamespace(max_completion_tokens=kwargs.get("max_completion_tokens"))

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_switch_create)
    monkeypatch.setattr(_streaming, "_budget_to_max_tokens", lambda _v: 512)
    obj = _streaming._StreamingMixin.__new__(_streaming._StreamingMixin)
    obj.model = "conv"
    obj.max_response_length = 200
    obj.base_url = "http://x"
    obj.api_key = "k"
    obj.vision_base_url = "http://x"
    obj.vision_api_key = "k"
    obj.llm = SimpleNamespace(max_completion_tokens=512)
    obj._conversation_history = []
    obj._genai_client = None
    obj.provider_type = None
    obj.vision_provider_type = None
    await obj.switch_model("vision-model", use_vision_config=True)
    assert captured_switch["keepalive_expiry"] == 55.0

    # TranslationService cached client passes 55s keepalive and max_retries=1.
    captured_tr = {}

    class _Cfg:
        async def aget_model_api_config(self, _group, *, core_config=None):
            return {
                "model": "m",
                "base_url": "https://example.test/v1",
                "api_key": "k",
                "provider_type": "openai_compatible",
            }

    async def _fake_tr_create(*_args, **kwargs):
        captured_tr.update(kwargs)
        return object()

    monkeypatch.setattr("utils.language_utils.create_chat_llm_async", _fake_tr_create)
    svc = TranslationService(_Cfg())
    await svc._get_llm_client()
    assert captured_tr["keepalive_expiry"] == 55.0
    assert captured_tr["max_retries"] == 1
    assert captured_tr["timeout"] == 30.0

    # OpenAIEmbeddings gets split 30s timeout, 55s keepalive, stale retry, and aclose.
    emb = OpenAIEmbeddings(base_url="https://api.example.test/v1", model="text-embedding-3-small", api_key="k")
    try:
        assert emb._aclient.timeout == httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        assert emb._aclient.max_retries == 1
        assert emb._aclient._client._transport._pool._keepalive_expiry == 55.0
        assert getattr(emb._aclient._client._transport.handle_async_request, "_neko_stale_retry", False)
        assert getattr(emb._client._client._transport.handle_request, "_neko_stale_retry", False)
    finally:
        await emb.aclose()


def test_server_shutdown_hooks_wire_warm_pool_and_decision_cleanup():
    root = Path(__file__).resolve().parents[2]
    for rel in (
        "app/main_server/__init__.py",
        "app/memory_server/runtime.py",
        "app/agent_server/api_runtime.py",
        "launcher_core/runtime.py",
    ):
        src = (root / rel).read_text(encoding="utf-8")
        assert "close_warm_chat_llms" in src, f"{rel} missing close_warm_chat_llms"
        assert "aclose_decision_http_clients" in src, f"{rel} missing aclose_decision_http_clients"
    agent_src = (root / "app/agent_server/api_runtime.py").read_text(encoding="utf-8")
    assert "deadline_monotonic=time.monotonic() + 2.0" in agent_src
    assert agent_src.index("submit_save") < agent_src.index("close_warm_chat_llms")


@pytest.mark.asyncio
async def test_merged_loop_deferred_pool_cleanup_markers():
    from utils.http.pool_utils import (
        defer_loop_pool_cleanup,
        is_loop_pool_cleanup_deferred,
        release_deferred_loop_pool_cleanup,
    )

    loop = asyncio.get_running_loop()
    release_deferred_loop_pool_cleanup(loop)
    assert is_loop_pool_cleanup_deferred(loop) is False
    defer_loop_pool_cleanup(loop)
    assert is_loop_pool_cleanup_deferred(loop) is True
    release_deferred_loop_pool_cleanup(loop)
    assert is_loop_pool_cleanup_deferred(loop) is False
