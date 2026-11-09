"""ChatGPT OAuth and Responses contract tests; no real credentials or network."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from utils.chatgpt_auth import CredentialStore, ChatGPTAuthError
from utils.chatgpt_auth import store as auth
from utils.chatgpt_auth.login import LoginAttempt, verify_identity
from utils.llm_client.chatgpt_client import (
    ChatChatGPT,
    ChatGPTResponseError,
    ResponseState,
    responses_input,
)


def tokens(**kw):
    return {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "id_token": "test-id",
        "scope": auth.PLAN_SCOPE,
        "token_type": "Bearer",
        "expires_in": 3600,
        **kw,
    }


def test_real_auth_http_client_initializes_project_headers():
    with auth.http_client() as client:
        assert client.headers["User-Agent"]
        assert not client.follow_redirects
        assert client.timeout.connect == 30


@pytest.fixture
def store(tmp_path):
    result = CredentialStore(tmp_path / "credentials.json")
    result.registration()
    result.save("issued-1", {"sub": "user-1", "email": "test@example.test"}, tokens())
    return result


def test_atomic_private_store_status_and_account_isolation(store):
    original_host, _ = store.registration("issued-1")
    store.save("issued-2", {"sub": "user-2"}, tokens(access_token="second"))
    assert store.access_token() == "second"
    store.select("issued-1")
    assert store.access_token() == "test-access"
    assert store.registration()[0] == original_host
    assert "test-access" not in json.dumps(store.status())
    assert "test-refresh" not in json.dumps(store.status())
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ChatGPTAuthError):
        store.save("issued-1", {"sub": "other"}, tokens())
    assert store.access_token() == "test-access"


def test_missing_scope_cannot_replace_active_account(store):
    with pytest.raises(ChatGPTAuthError, match="plan usage"):
        store.save("issued-2", {"sub": "other"}, tokens(scope="openid email"))
    assert store.status()["active"] == "issued-1"


def test_refresh_is_serialized_rotates_and_survives_restart(store, monkeypatch):
    data = store._read()
    data["accounts"]["issued-1"]["expires_at"] = 0
    store._write(data)
    calls = []

    def handler(req):
        calls.append(parse_qs(req.content.decode()))
        return httpx.Response(
            200, json=tokens(access_token="new-access", refresh_token="new-refresh")
        )

    monkeypatch.setattr(
        auth,
        "http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(lambda _: CredentialStore(store.path).access_token(), range(4))
        )
    assert values == ["new-access"] * 4
    assert len(calls) == 1
    assert calls[0]["client_id"] == ["issued-1"]
    assert "scope" not in calls[0]
    assert store._read()["accounts"]["issued-1"]["refresh_token"] == "new-refresh"


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_refresh_failure_keeps_credentials_and_redacts(store, monkeypatch, status):
    data = store._read()
    data["accounts"]["issued-1"]["expires_at"] = 0
    store._write(data)
    monkeypatch.setattr(
        auth,
        "http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, json={"error": "test-refresh private"})
            )
        ),
    )
    with pytest.raises(ChatGPTAuthError) as error:
        store.access_token()
    assert "test-refresh" not in str(error.value)
    assert store._read() == data


def test_logout_retains_registration_and_clears_all_tokens(store, monkeypatch):
    def handler(req):
        if req.method == "GET":
            return httpx.Response(
                200, json={"revocation_endpoint": auth.ISSUER + "/revoke"}
            )
        return httpx.Response(200)

    monkeypatch.setattr(
        auth,
        "http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert store.sign_out("issued-1")
    _, account = store.registration("issued-1")
    assert account["subject"] == "user-1"
    assert not any(k in account for k in ("access_token", "refresh_token", "id_token"))
    with pytest.raises(ChatGPTAuthError):
        store.access_token()


@pytest.fixture
def identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-key"
    claims = {
        "iss": auth.ISSUER,
        "aud": "issued-1",
        "sub": "user-1",
        "nonce": "nonce",
        "exp": time.time() + 60,
    }

    def sign(**overrides):
        return jwt.encode(
            {**claims, **overrides}, key, algorithm="RS256", headers={"kid": "test-key"}
        )

    return sign, {"keys": [jwk]}


def test_identity_verifies_signature_and_required_claims(identity):
    sign, jwks = identity
    assert verify_identity(sign(), "issued-1", "nonce", jwks)["sub"] == "user-1"
    for invalid in (
        {"aud": "other"},
        {"iss": "https://attacker.test"},
        {"exp": 1},
        {"nonce": "other"},
        {"sub": ""},
    ):
        with pytest.raises(ChatGPTAuthError):
            verify_identity(sign(**invalid), "issued-1", "nonce", jwks)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"exp": time.time() + 60}, other, algorithm="RS256", headers={"kid": "test-key"}
    )
    with pytest.raises(ChatGPTAuthError):
        verify_identity(forged, "issued-1", "nonce", jwks)


def test_login_pkce_state_denial_and_timeout(store):
    attempt = LoginAttempt(store, timeout=2)
    try:
        query = parse_qs(urlsplit(attempt.url).query)
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [attempt.redirect]
        assert query["ext_agent_host_id"] == [store.registration()[0]]
        with httpx.Client(trust_env=False) as client:
            response = client.get(
                attempt.redirect, params={"state": "wrong", "error": "access_denied"}
            )
            assert response.status_code == 400
            assert attempt.status == "pending"
            response = client.get(
                attempt.redirect,
                params={"state": attempt.state, "error": "access_denied"},
            )
            assert response.status_code == 400
            assert attempt.status == "failed"
        assert store.status()["active"] == "issued-1"
    finally:
        attempt.done.set()
    returning = LoginAttempt(store, "issued-1", timeout=0.01)
    query = parse_qs(urlsplit(returning.url).query)
    assert query["client_id"] == ["issued-1"]
    assert "agent_name_hint" not in query
    assert (
        "id_token_hint" not in query
    )  # No retained token sent to the settings frontend.
    assert returning.done.wait(2)
    assert returning.status == "expired"


def test_returning_login_rejects_changed_client_before_exchange(store):
    attempt = LoginAttempt(store, "issued-1")
    try:
        with pytest.raises(ChatGPTAuthError, match="different client"):
            attempt.complete({"client_id": "attacker", "code": "code"})
    finally:
        attempt.done.set()


def completed(output=None):
    return {
        "type": "response.completed",
        "response": {
            "id": "resp-test",
            "status": "completed",
            "output": output or [],
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
    }


def test_tool_round_trip_preserves_encrypted_reasoning_and_images():
    state = ResponseState()
    state.feed({"type": "response.output_text.delta", "delta": "Checking"})
    chunks = state.feed(
        completed(
            [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque",
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "weather",
                    "arguments": '{"city":"北京"}',
                },
            ]
        )
    )
    state.finish()
    assert chunks[-1].finish_reason == "tool_calls"
    calls = ChatChatGPT.collect_tool_calls([chunks[-1].tool_call_deltas])
    assert calls[0].name == "weather"
    inputs = responses_input(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            },
            {"role": "assistant", "content": "Checking", "tool_calls": state.calls},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ]
    )
    assert inputs[0]["content"][1]["type"] == "input_image"
    assert inputs[2]["encrypted_content"] == "opaque"
    assert inputs[3]["type"] == "function_call"
    assert inputs[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "sunny",
    }
    assert state.raw("test").choices[0].message.tool_calls[0].id == "call_1"


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "response.failed",
            "response": {
                "error": {"code": "subscription_sharing_usage_limit_exceeded"}
            },
        },
        {"type": "response.incomplete"},
        {"type": "error"},
    ],
)
def test_terminal_failures_never_emit_stop_or_tools(event):
    state = ResponseState()
    state.feed({"type": "response.output_text.delta", "delta": "partial"})
    with pytest.raises(ChatGPTResponseError):
        state.feed(event)
    with pytest.raises(ChatGPTResponseError, match="before completion"):
        state.finish()


def test_factory_routes_explicit_oauth_and_blocks_other_origins(store):
    from utils.llm_client import create_chat_llm

    client = create_chat_llm(
        "test",
        auth.RESOURCE,
        "ignored-key",
        provider_type="chatgpt",
        credential_store=store,
    )
    try:
        assert isinstance(client, ChatChatGPT)
        params = client._response_params(
            "hello",
            store=True,
            stream=False,
            extra_headers={"Authorization": "evil"},
            extra_body={"store": True},
        )
        assert params["store"] is False and params["stream"] is True
        assert "extra_headers" not in params
        assert "temperature" not in params
        assert params["include"] == ["reasoning.encrypted_content"]
    finally:
        client.close()
    with pytest.raises(ValueError, match="credentials"):
        ChatChatGPT(base_url="https://attacker.test/v1", credential_store=store)


@pytest.mark.asyncio
async def test_sdk_sse_stream_and_raw_calls_close_and_never_use_chat_completions(store):
    from openai import AsyncOpenAI

    requests = []
    payloads = [{"type": "response.output_text.delta", "delta": "Hello"}, completed()]

    def handler(req):
        requests.append(req)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(event) + "\n\n" for event in payloads),
        )

    client = ChatChatGPT(
        model="test", credential_store=store, max_completion_tokens=1000
    )
    await client._aclient.close()
    client._aclient = AsyncOpenAI(
        api_key="placeholder",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        chunks = [chunk async for chunk in client.astream("hi")]
        assert "".join(c.content for c in chunks) == "Hello"
        assert chunks[-1].finish_reason == "stop"
        raw = await client.ainvoke_raw("hi")
        assert raw.usage.total_tokens == 5
        assert raw.choices[0].message.content == "Hello"
        assert requests[0].url.path == "/v1/responses"
        assert requests[0].headers["authorization"] == "Bearer test-access"
        assert json.loads(requests[0].content)["max_output_tokens"] == 1000
        payloads.pop()
        with pytest.raises(ChatGPTResponseError, match="before completion"):
            await client.ainvoke("hi")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_account_specific_model_catalog(store, monkeypatch):
    def handler(req):
        assert req.url == auth.RESOURCE + "/models"
        assert req.headers["authorization"] == "Bearer test-access"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "visible",
                        "display_name": "Visible",
                        "visibility": "list",
                    },
                    {"slug": "hidden", "visibility": "hidden"},
                    {
                        "slug": "unsupported",
                        "visibility": "list",
                        "supported_in_api": False,
                    },
                ]
            },
        )

    monkeypatch.setattr(
        auth,
        "http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = ChatChatGPT(credential_store=store)
    try:
        assert await client.alist_models() == [{"id": "visible", "name": "Visible"}]
    finally:
        await client.aclose()


def test_successful_login_exchanges_exact_redirect_and_validates_identity(
    store, monkeypatch, identity
):
    from utils.chatgpt_auth import login as login_module

    sign, jwks = identity
    attempt = LoginAttempt(store, "issued-1")
    requests = []

    def handler(req):
        requests.append(req)
        if req.method == "POST":
            return httpx.Response(200, json=tokens(id_token=sign(nonce=attempt.nonce)))
        return httpx.Response(200, json=jwks)

    monkeypatch.setattr(
        login_module,
        "http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with httpx.Client(trust_env=False) as client:
            result = client.get(
                attempt.redirect, params={"state": attempt.state, "code": "one-time"}
            )
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert attempt.status == "connected"
        params = parse_qs(requests[0].content.decode())
        assert params["redirect_uri"] == [attempt.redirect]
        assert params["code_verifier"] == [attempt.verifier]
        assert params["client_id"] == ["issued-1"]
        assert params["resource"] == [auth.RESOURCE]
    finally:
        attempt.done.set()


@pytest.mark.asyncio
async def test_oauth_routes_require_loopback_origin_and_custom_header(
    store, monkeypatch
):
    from fastapi import FastAPI
    from main_routers.config_router import chatgpt

    monkeypatch.setattr(chatgpt, "get_store", lambda: store)
    monkeypatch.setattr(chatgpt, "_attempt", None)
    app = FastAPI()
    app.include_router(chatgpt.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
        base_url="http://127.0.0.1:48999",
    ) as client:
        path = "/api/config/chatgpt/status"
        assert (await client.get(path)).status_code == 403
        headers = {"x-neko-chatgpt": "1", "origin": "https://evil.test"}
        assert (await client.get(path, headers=headers)).status_code == 403
        headers["origin"] = "http://127.0.0.1:48999"
        response = await client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert (
            "test-access" not in response.text and "test-refresh" not in response.text
        )
        assert response.json()["active"] == "issued-1"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 123)),
        base_url="http://127.0.0.1:48999",
    ) as client:
        assert (await client.get(path, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_consumer_cancellation_closes_response_stream(store):
    client = ChatChatGPT(model="test", credential_store=store)
    closed = []

    async def events(*args, **kwargs):
        try:
            yield {"type": "response.output_text.delta", "delta": "partial"}
            yield completed()
        finally:
            closed.append(True)

    client._events = events
    try:
        stream = client.astream("hello")
        assert (await anext(stream)).content == "partial"
        await stream.aclose()
        assert closed == [True]
    finally:
        await client.aclose()


def test_sync_invoke_consumes_responses_stream_and_returns_chat_contract(store):
    from openai import OpenAI

    def handler(req):
        assert req.url.path == "/v1/responses"
        events = [{"type": "response.output_text.delta", "delta": "Hello"}, completed()]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
        )

    client = ChatChatGPT(model="test", credential_store=store)
    client._client.close()
    client._client = OpenAI(
        api_key="placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = client.invoke("hello")
        assert result.content == "Hello"
        assert result.response_metadata["finish_reason"] == "stop"
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_http_auth_and_limit_errors_are_actionable_without_response_body(status):
    response = httpx.Response(
        status, request=httpx.Request("GET", auth.RESOURCE + "/models")
    )
    with pytest.raises((ChatGPTAuthError, ChatGPTResponseError)) as error:
        ChatChatGPT._raise_auth_or_limit(
            httpx.HTTPStatusError(
                "private-body", request=response.request, response=response
            )
        )
    assert "private-body" not in str(error.value)


def test_completed_response_without_usage_still_supports_raw_contract():
    event = completed()
    event["response"].pop("usage")
    state = ResponseState()
    state.feed(event)
    assert state.raw("test").usage.total_tokens == 0


@pytest.mark.parametrize('failure', ['timeout', 'server_error', 'always_timeout', 'denied'])
def test_logout_retries_transient_revocation_and_always_clears_tokens(store, monkeypatch, failure):
    calls = []
    delays = []

    def handler(req):
        if req.method == 'GET':
            return httpx.Response(200, json={'revocation_endpoint': auth.ISSUER + '/revoke'})
        calls.append(parse_qs(req.content.decode()))
        if failure == 'always_timeout' or (failure == 'timeout' and len(calls) == 1):
            raise httpx.ReadTimeout('private-refresh-token', request=req)
        if failure == 'denied':
            return httpx.Response(400)
        return httpx.Response(503 if failure == 'server_error' and len(calls) == 1 else 200)

    monkeypatch.setattr(auth, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    from tests.fake_clock import patch_module_clock
    patch_module_clock(monkeypatch, auth, sleep=delays.append)
    assert store.sign_out('issued-1') is (failure in ('timeout', 'server_error'))
    assert len(calls) == (1 if failure == 'denied' else 2)
    assert delays == ([] if failure == 'denied' else [0.5])
    assert all(call['client_id'] == ['issued-1'] and call['token'] == ['test-refresh'] for call in calls)
    assert store.status()['active'] is None
    assert 'refresh_token' not in store.registration('issued-1')[1]


def test_response_params_preserve_tools_formats_and_per_call_budget(store):
    tool = {'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object', 'properties': {}}}}
    client = ChatChatGPT(model='test', credential_store=store, tools=[tool], max_completion_tokens=512)
    try:
        params = client._response_params('Reply with JSON', max_completion_tokens=64,
            tool_choice={'type': 'function', 'function': {'name': 'lookup'}},
            response_format={'type': 'json_schema', 'json_schema': {'name': 'answer', 'schema': {'type': 'object'}}})
        assert params['tools'] == [{'type': 'function', 'strict': False, **tool['function']}]
        assert params['tool_choice'] == {'type': 'function', 'name': 'lookup'}
        assert params['text']['format']['type'] == 'json_schema'
        assert params['max_output_tokens'] == 64
        next_params = client._response_params('Reply with JSON', tools=[], response_format={'type': 'json_object'})
        assert next_params['max_output_tokens'] == 512
        assert 'tools' not in next_params and 'tool_choice' not in next_params
        assert next_params['text'] == {'format': {'type': 'json_object'}}
    finally:
        client.close()


@pytest.mark.asyncio
async def test_account_mutations_reject_pending_login_and_clear_stale_login_error(store, monkeypatch):
    import threading
    from types import SimpleNamespace
    from fastapi import FastAPI
    from main_routers.config_router import chatgpt

    monkeypatch.setattr(chatgpt, 'get_store', lambda: store)
    attempt = SimpleNamespace(done=threading.Event(), status='pending', error='')
    monkeypatch.setattr(chatgpt, '_attempt', attempt)
    app = FastAPI()
    app.include_router(chatgpt.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 123)),
            base_url='http://127.0.0.1:48999', headers={'X-Neko-ChatGPT': '1'}) as client:
        for action in ('select', 'logout', 'login'):
            response = await client.post('/api/config/chatgpt/' + action, json={'account_id': 'issued-1'})
            assert response.status_code == 400
        assert store.access_token() == 'test-access'
        attempt.done.set()
        attempt.status = 'failed'
        attempt.error = 'Previous login was denied'
        response = await client.post('/api/config/chatgpt/select', json={'account_id': 'issued-1'})
        assert response.status_code == 200
        status = (await client.get('/api/config/chatgpt/status')).json()
        assert status['active'] == 'issued-1'
        assert status['login_status'] == 'idle' and status['error'] == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['async', 'sync'])
async def test_sdk_raised_stream_limit_error_is_safe_and_not_retried(store, mode):
    from openai import AsyncOpenAI, OpenAI

    calls = []
    def handler(req):
        calls.append(req)
        event = {'type': 'error', 'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'private upstream details'}}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='data: ' + json.dumps(event) + '\n\n')

    client = ChatChatGPT(model='test', credential_store=store)
    try:
        if mode == 'async':
            await client._aclient.close()
            client._aclient = AsyncOpenAI(api_key='placeholder', max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            with pytest.raises(ChatGPTResponseError, match='plan usage') as error:
                await client.ainvoke_raw('hi')
        else:
            client._client.close()
            client._client = OpenAI(api_key='placeholder', max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            with pytest.raises(ChatGPTResponseError, match='plan usage') as error:
                client.invoke_raw('hi')
        assert 'private upstream details' not in str(error.value)
        assert len(calls) == 1
    finally:
        await client.aclose()
