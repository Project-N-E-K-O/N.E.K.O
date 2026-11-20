"""Connected HTTP commands and actual N2 installation with bounded network substitutes."""

import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from main_routers import model_market_router as api
from main_routers.system_router import _shared
from services.model_workshop import download
from tests.unit.test_model_workshop_install import bundle_path, install
from tests.unit.test_model_workshop_migration import owner as owner

AUTH = {"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "connected-test-csrf"}


def network_json(value, status=200):
    # MockTransport's json= response is pre-consumed; use a real unread stream
    # so raw streaming paths run exactly as they do on an HTTP connection.
    raw = json.dumps(value).encode()
    return httpx.Response(status, stream=httpx.ByteStream(raw), headers={'content-type': 'application/json'})


class Body(httpx.SyncByteStream):
    def __init__(self, raw, enter=None, release=None):
        self.raw, self.enter, self.release, self.closed = raw, enter, release, False

    def __iter__(self):
        if self.enter:
            self.enter.set()
            assert self.release.wait(5), 'test did not release body'
        yield self.raw

    def close(self):
        self.closed = True


@pytest.fixture
async def flow(owner, tmp_path, monkeypatch):
    # Normal startup persists the runtime role file before this feature opens.
    # The storage-only fixture intentionally omits it; initialize only tmp_path.
    owner.config.save_characters(owner.config.load_characters())
    package, _ = bundle_path(tmp_path, 'vrm')
    raw = package.read_bytes()
    model = {"id": 7, "file_sha256": hashlib.sha256(raw).hexdigest(),
             "byte_size": len(raw), "model_format": "vrm"}
    trace, bodies = [], []
    state = SimpleNamespace(raw=raw, metadata=dict(model), headers={}, status=200,
                            enter=None, release=None, pair_status=200, pair_raw=None)

    def loopback(request):
        trace.append(request)
        assert request.url.host == '127.0.0.1' and request.url.port == 48916
        assert request.headers['origin'] == 'http://127.0.0.1:48916'
        if request.url.path == '/market/bridge-token':
            return network_json({"bridge_token": "local-bridge-only"})
        assert request.headers['authorization'] == 'Bearer local-bridge-only'
        assert dict(request.url.params) == {"expected_sha256": model['file_sha256'], "byte_size": str(len(raw))}
        if '/descriptor/' in request.url.path:
            return network_json(state.metadata)
        assert request.url.path == '/market/models/package/7'
        body = Body(state.raw, state.enter, state.release); bodies.append(body)
        return httpx.Response(state.status, stream=body, headers={
            'content-type': 'application/zip', 'content-length': str(len(raw)), **state.headers})

    def pair(request):
        assert request.url.path == '/market/pair-code'
        assert 'authorization' not in request.headers
        trace.append(request)
        return httpx.Response(state.pair_status, stream=httpx.ByteStream(state.pair_raw)) if state.pair_raw is not None else network_json(
            {"one_time_code": "single-use-code"}, state.pair_status)

    monkeypatch.setattr(download, 'resolve_user_plugin_base', lambda: 'http://127.0.0.1:48916')
    monkeypatch.setattr(download, 'get_market_web_url', lambda: 'https://market.example.test')
    monkeypatch.setattr(download, '_client', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(loopback)))
    monkeypatch.setattr(download, '_pair_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(pair)))
    driver = download.ConnectedDownloads()
    monkeypatch.setattr(api, 'connected_downloads', driver)
    monkeypatch.setattr(api, 'get_config_manager', lambda: owner.config)
    monkeypatch.setattr(_shared, 'AUTOSTART_CSRF_TOKEN', AUTH['X-CSRF-Token'])
    app = FastAPI(); app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('127.0.0.1', 40000)),
                                 base_url='http://127.0.0.1:48911') as client:
        yield SimpleNamespace(client=client, driver=driver, config=owner.config, model=model,
                              package=package, state=state, trace=trace, bodies=bodies, owner=owner)
    if state.release:
        state.release.set()
    await driver.close()


async def opened(flow):
    response = await flow.client.post('/api/model-market/launch', json={}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


async def select(flow, row, **changes):
    body = {"model_id": 7, "expected_sha256": flow.model['file_sha256'], 'byte_size': flow.model['byte_size'], **changes}
    return await flow.client.post('/api/model-market/launch/' + row['request_id'] + '/select', json=body, headers=AUTH)


async def settled(flow):
    await asyncio.wait_for(asyncio.gather(*list(flow.driver.jobs.values())), 5)


@pytest.mark.asyncio
async def test_launch_is_scoped_and_download_reuses_actual_installer(flow):
    before = flow.config.get_runtime_config_path('characters.json').read_bytes()
    row = await opened(flow)
    url = urlsplit(row['url'])
    assert url.netloc == 'market.example.test' and url.fragment.startswith('/models?')
    params = parse_qs(url.fragment.split('?', 1)[1])
    assert params == {'model_origin': ['http://127.0.0.1:48911'],
                      'model_request': [row['request_id']], 'model_code': ['single-use-code']}
    assert 'local-bridge-only' not in row['url']
    accepted = await select(flow, row)
    assert accepted.status_code == 202 and accepted.headers['cache-control'] == 'no-store'
    assert (await select(flow, row)).status_code == 202
    await settled(flow)
    status = await flow.client.get('/api/model-market/launch/' + row['request_id'])
    assert status.headers['cache-control'] == 'no-store'
    result = status.json()
    assert result['phase'] == 'installed' and result['result']['model_format'] == 'vrm'
    assert len(flow.owner.installer.registry.installed()) == 1
    assert len(flow.bodies) == 1 and flow.bodies[0].closed
    assert flow.config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
async def test_already_installed_package_reuses_files_after_fresh_auth_check(flow):
    original = install(flow.owner, flow.package)['result']
    row = await opened(flow)
    await select(flow, row); await settled(flow)
    value = flow.driver.status(flow.config, row['request_id'])
    assert value['phase'] == 'installed' and value['result']['already_installed']
    assert value['result']['install_id'] == original['install_id']
    assert any('/descriptor/' in request.url.path for request in flow.trace)
    assert not flow.bodies


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['hash', 'truncated', 'type', 'encoding', 'redirect', 'metadata'])
async def test_untrusted_response_never_publishes_model(flow, fault):
    row = await opened(flow)
    if fault == 'hash': flow.state.raw = bytes([flow.state.raw[0] ^ 1]) + flow.state.raw[1:]
    if fault == 'truncated': flow.state.raw = flow.state.raw[:-1]
    if fault == 'type': flow.state.headers['content-type'] = 'text/html'
    if fault == 'encoding': flow.state.headers['content-encoding'] = 'gzip'
    if fault == 'redirect': flow.state.status = 302; flow.state.raw = b'{}'
    if fault == 'metadata': flow.state.metadata['file_sha256'] = 'a' * 64
    await select(flow, row); await settled(flow)
    result = flow.driver.status(flow.config, row['request_id'])
    assert result['phase'] == 'failed'
    assert not flow.owner.installer.registry.installed()
    assert all(body.closed for body in flow.bodies)


@pytest.mark.asyncio
async def test_cancel_and_shutdown_drain_receiving_thread(flow):
    flow.state.enter, flow.state.release = threading.Event(), threading.Event()
    row = await opened(flow); await select(flow, row)
    assert await asyncio.to_thread(flow.state.enter.wait, 3)
    response = await flow.client.post('/api/model-market/launch/' + row['request_id'] + '/cancel', json={}, headers=AUTH)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    closing = asyncio.create_task(flow.driver.close())
    # Cleanup must wait for the actual reader, not just cancel its awaiter.
    assert not closing.done()
    flow.state.release.set(); await asyncio.wait_for(closing, 5)
    assert not flow.driver.jobs and all(body.closed for body in flow.bodies)
    assert flow.driver.status(flow.config, row['request_id'])['phase'] == 'cancelled'
    assert not flow.owner.installer.registry.installed()


@pytest.mark.asyncio
async def test_role_switch_during_download_retains_install_but_blocks_application(flow):
    flow.state.enter, flow.state.release = threading.Event(), threading.Event()
    row = await opened(flow); await select(flow, row)
    assert await asyncio.to_thread(flow.state.enter.wait, 3)
    flow.config.save_characters(flow.config.load_characters())
    flow.state.release.set(); await settled(flow)
    value = flow.driver.status(flow.config, row['request_id'])
    assert value['phase'] == 'blocked' and value['error_code'] == 'model_market_target_changed'
    assert value['result']['status'] == 'installed'


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'url': 'https://evil.test/model.zip'}, {'role': 'other'}, [], {'path': '/etc/passwd'}])
async def test_unknown_commands_rejected_before_pairing(flow, body):
    response = await flow.client.post('/api/model-market/launch', json=body, headers=AUTH)
    assert response.status_code == 400 and not flow.trace and not flow.driver.launches.items


@pytest.mark.asyncio
async def test_bad_csrf_large_body_and_storage_gate_do_not_start_pairing(flow, monkeypatch):
    assert (await flow.client.post('/api/model-market/launch', json={})).status_code == 403
    assert (await flow.client.post('/api/model-market/launch', headers={**AUTH, 'Content-Type': 'application/json'},
                                  content=b' ' * 4097)).status_code == 413
    monkeypatch.setattr(download, 'get_storage_startup_blocking_reason_readonly', lambda _: 'selection_required')
    response = await flow.client.post('/api/model-market/launch', json={}, headers=AUTH)
    assert response.status_code == 503 and not flow.trace and not flow.driver.launches.items


@pytest.mark.asyncio
async def test_readonly_maintenance_blocks_launch_before_network(flow):
    from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY

    state = flow.config.load_root_state()
    state['mode'] = ROOT_MODE_MAINTENANCE_READONLY
    flow.config.save_root_state(state)
    response = await flow.client.post('/api/model-market/launch', json={}, headers=AUTH)
    assert response.status_code == 503 and not flow.trace and not flow.driver.launches.items


@pytest.mark.asyncio
@pytest.mark.parametrize('raw,status', [(b'not JSON', 200), (b' ' * 4097, 200), (b'{}', 302)],
                         ids=['invalid-json', 'over-limit', 'redirect'])
async def test_failed_pairing_removes_unusable_launch(flow, raw, status):
    flow.state.pair_raw, flow.state.pair_status = raw, status
    response = await flow.client.post('/api/model-market/launch', json={}, headers=AUTH)
    assert response.status_code == 502 and not flow.driver.launches.items


@pytest.mark.asyncio
async def test_cancelled_shutdown_still_drains_actual_reader(flow):
    flow.state.enter, flow.state.release = threading.Event(), threading.Event()
    row = await opened(flow); await select(flow, row)
    assert await asyncio.to_thread(flow.state.enter.wait, 3)
    closing = asyncio.create_task(flow.driver.close())
    await asyncio.sleep(0)  # Enter close() and signal the reader before cancellation.
    assert flow.driver.closed
    closing.cancel(); flow.state.release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not flow.driver.jobs and all(body.closed for body in flow.bodies)
    assert not flow.owner.installer.registry.installed()
