"""Model pairing, desktop-token isolation and upstream stream ownership."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request, ClientDisconnect

from plugin.server.routes import model_market as bridge
from plugin.server.routes import market_bridge as auth

LOCAL = 'http://127.0.0.1:48916'
MARKET = 'https://market.example.test'
REQUEST = 'r' * 32


class AsyncBody(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw, self.closed = raw, False

    async def __aiter__(self):
        yield self.raw

    async def aclose(self):
        self.closed = True


def streamed(raw, status=200, **headers):
    return httpx.Response(status, stream=AsyncBody(raw), headers=headers)


@pytest.fixture
async def connected(monkeypatch):
    state = SimpleNamespace(token='desktop-secret', refreshes=0, unauthorized=False,
        metadata_status=200, metadata_raw=None, package_status=200, package_headers={}, bodies=[], calls=[])
    descriptor = {'id': 7, 'status': 'published', 'model_format': 'vrm', 'byte_size': 3,
                  'file_sha256': 'a' * 64, 'capabilities': {'can_download': True},
                  'private_token': 'must-not-be-forwarded'}

    async def token(): return {'access_token': state.token}
    async def refresh(**kwargs):
        assert kwargs == {'force_refresh': True, 'rejected_access_token': 'desktop-secret'}
        state.refreshes += 1; state.token = 'desktop-refreshed'
        return {'access_token': state.token}

    def upstream(request):
        state.calls.append(request)
        if request.url.host == '127.0.0.1':
            assert request.url.port == 48911
            return streamed(b'{"success":true,"phase":"downloading"}', **{'content-type': 'application/json'})
        assert request.url.host == 'market.example.test'
        assert request.headers['authorization'] in {'Bearer desktop-secret', 'Bearer desktop-refreshed'}
        assert request.headers['accept-encoding'] == 'identity'
        if state.unauthorized and request.headers['authorization'] == 'Bearer desktop-secret':
            return streamed(b'{}', 401)
        if request.url.path == '/api/v1/models/7':
            raw = state.metadata_raw if state.metadata_raw is not None else json.dumps(descriptor).encode()
            return streamed(raw, state.metadata_status, **{'content-type': 'application/json'})
        assert request.url.path == '/api/v1/models/7/download'
        body = AsyncBody(b'abc'); state.bodies.append(body)
        return httpx.Response(state.package_status, stream=body, headers={
            'content-type': 'application/zip', 'content-length': '3', **state.package_headers})

    monkeypatch.setattr(auth, '_authenticated_market_token', token)
    monkeypatch.setattr(auth, '_ensure_valid_oauth_token', refresh)
    monkeypatch.setattr(auth, '_oauth_subject_is_verified', lambda _: True)
    monkeypatch.setattr(auth, 'MARKET_API_URL', MARKET)
    monkeypatch.setattr(auth, '_main_server_port', lambda: 48911)
    monkeypatch.setattr(bridge, 'MARKET_ORIGINS', [MARKET])
    monkeypatch.setattr(bridge, '_slots', asyncio.Semaphore(2))
    monkeypatch.setattr(bridge, '_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    app = FastAPI(); app.include_router(bridge.router)
    headers = {'Authorization': 'Bearer ' + auth.get_bridge_token(), 'Origin': LOCAL}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('127.0.0.1', 41000)), base_url=LOCAL) as client:
        yield SimpleNamespace(client=client, state=state, descriptor=descriptor, headers=headers,
                              params={'expected_sha256': 'a' * 64, 'byte_size': 3})


@pytest.mark.asyncio
async def test_local_reader_strips_projection_and_never_reveals_oauth(connected):
    c = connected
    response = await c.client.get('/market/models/descriptor/7', headers=c.headers, params=c.params)
    assert response.status_code == 200
    assert set(response.json()) == {'id', 'file_sha256', 'byte_size', 'model_format'}
    assert b'desktop-secret' not in response.content and b'must-not-be-forwarded' not in response.content
    response = await c.client.get('/market/models/package/7', headers=c.headers, params=c.params)
    assert response.status_code == 200 and response.content == b'abc'
    assert response.headers['cache-control'] == 'private, no-store'
    assert c.state.bodies[0].closed and bridge._slots._value == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [{}, {'Origin': MARKET}, {'Origin': 'https://evil.test'},
                                    {'X-Forwarded-For': '127.0.0.1'}])
async def test_remote_or_forwarded_pages_cannot_use_desktop_reader(connected, headers):
    c = connected
    applied = {**c.headers, **headers} if headers else {}
    response = await c.client.get('/market/models/package/7', headers=applied, params=c.params)
    assert response.status_code == 403 and not c.state.calls


@pytest.mark.asyncio
async def test_remote_relay_preserves_only_command_and_main_auth_headers(connected):
    c = connected
    headers = {**c.headers, 'Origin': MARKET}
    payload = {'model_id': 7, 'expected_sha256': 'a' * 64, 'byte_size': 3}
    response = await c.client.post(f'/market/models/launch/{REQUEST}/select?url=https://evil.test', headers=headers, json=payload)
    assert response.status_code == 200
    request = c.state.calls[0]
    assert str(request.url) == f'http://127.0.0.1:48911/api/model-market/launch/{REQUEST}/select'
    assert json.loads(request.content) == payload
    assert 'authorization' not in request.headers and 'x-csrf-token' in request.headers
    assert request.headers['origin'] == 'http://127.0.0.1:48911'


@pytest.mark.asyncio
async def test_operation_lookup_is_bounded_and_local_readiness_cannot_be_proxied(connected):
    """Pairing grants task reads, never the local manager's next-choice approval."""
    c = connected; path = f'/market/models/launch/{REQUEST}'
    headers = {**c.headers, 'Origin': MARKET}
    response = await c.client.get(path, params={'operation_id': '1' * 32, 'url': 'https://evil.test'}, headers=headers)
    assert response.status_code == 200
    assert dict(c.state.calls[-1].url.params) == {'operation_id': '1' * 32}
    before = len(c.state.calls)
    for action in ('ready', 'invalidate', 'apply'):
        assert (await c.client.post(path + '/' + action, headers=headers, json={'operation_id': '1' * 32})).status_code == 404
    assert (await c.client.get(path, headers=headers, params={'operation_id': '../escape'})).status_code == 400
    assert len(c.state.calls) == before


@pytest.mark.asyncio
async def test_relay_rejects_unknown_actions_origins_and_excess_actual_bytes(connected):
    c = connected; path = f'/market/models/launch/{REQUEST}'
    assert (await c.client.get(path, headers=c.headers)).status_code == 403
    headers = {**c.headers, 'Origin': MARKET}
    assert (await c.client.post(path + '/apply', headers=headers, json={})).status_code == 404
    assert (await c.client.post(path + '/select', headers=headers, content=b'x' * 4097)).status_code == 413
    assert not c.state.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['private', 'hash', 'size', 'format', 'json', 'large', 'redirect'])
async def test_metadata_failures_never_start_package_response(connected, monkeypatch, fault):
    c = connected
    if fault == 'private': c.descriptor['status'] = 'pending_review'
    if fault == 'hash': c.descriptor['file_sha256'] = 'b' * 64
    if fault == 'size': c.descriptor['byte_size'] = True
    if fault == 'format': c.descriptor['model_format'] = 'unsupported'
    if fault == 'json': c.state.metadata_raw = b'[]'
    if fault == 'large': monkeypatch.setattr(bridge, '_JSON_MAX', 64)
    if fault == 'redirect': c.state.metadata_status = 302
    response = await c.client.get('/market/models/package/7', headers=c.headers, params=c.params)
    assert response.status_code in {409, 502} and not c.state.bodies and bridge._slots._value == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['type', 'encoding', 'length', 'redirect'])
async def test_package_headers_rejected_and_all_resources_closed(connected, fault):
    c = connected
    if fault == 'type': c.state.package_headers['content-type'] = 'text/html'
    if fault == 'encoding': c.state.package_headers['content-encoding'] = 'gzip'
    if fault == 'length': c.state.package_headers['content-length'] = '4'
    if fault == 'redirect': c.state.package_status = 302
    response = await c.client.get('/market/models/package/7', headers=c.headers, params=c.params)
    assert response.status_code in {409, 502} and c.state.bodies[0].closed
    assert bridge._slots._value == 2


@pytest.mark.asyncio
async def test_existing_oauth_refresh_reused_once_before_stream(connected):
    c = connected; c.state.unauthorized = True
    response = await c.client.get('/market/models/package/7', headers=c.headers, params=c.params)
    assert response.status_code == 200 and c.state.refreshes == 1
    assert [r.headers['authorization'] for r in c.state.calls] == [
        'Bearer desktop-secret', 'Bearer desktop-refreshed', 'Bearer desktop-refreshed']


@pytest.mark.asyncio
async def test_header_send_failure_releases_upstream_client_and_slot(connected):
    c = connected
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'http',
        'path': '/market/models/package/7', 'query_string': b'',
        'client': ('127.0.0.1', 41000), 'server': ('127.0.0.1', 48916),
        'headers': [(k.lower().encode(), v.encode()) for k, v in {**c.headers, 'Host': '127.0.0.1:48916'}.items()]})
    response = await bridge.local_model_file(7, request, **c.params)
    assert bridge._slots._value == 1
    async def send(_): raise OSError('consumer disconnected before body')
    async def receive(): return {'type': 'http.disconnect'}
    with pytest.raises((OSError, ClientDisconnect)):
        await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    assert c.state.bodies[0].closed and response.client.is_closed
    assert bridge._slots._value == 2


@pytest.mark.asyncio
async def test_desktop_reader_concurrency_rejected_without_upstream_call(connected):
    c = connected
    await bridge._slots.acquire(); await bridge._slots.acquire()
    try:
        response = await c.client.get('/market/models/package/7', headers=c.headers, params=c.params)
        assert response.status_code == 429 and not c.state.calls
    finally:
        bridge._slots.release(); bridge._slots.release()
