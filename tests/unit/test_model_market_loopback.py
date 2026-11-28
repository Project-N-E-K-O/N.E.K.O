"""Real loopback Main/Plugin hops; only upstream Market identity/data is synthetic."""

import asyncio
import hashlib
import json
import socket
import threading
import time
import zipfile
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request

from app.main_server import web_app
from main_routers import model_market_router as api
from main_routers.system_router import _shared
from plugin.server.routes import market_bridge as auth, model_market as bridge
from services.model_workshop import download
from tests.unit.test_model_workshop_install import bundle_path
from tests.unit.test_model_workshop_migration import owner as owner


@contextmanager
def serving(app):
    """Reserve an ephemeral port; never reuse the user's running desktop ports."""
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        yield f'http://127.0.0.1:{sock.getsockname()[1]}'
    finally:
        server.should_exit = True; thread.join(5); sock.close()
        assert not thread.is_alive()


def test_pair_select_stream_install_over_real_service_hops(owner, tmp_path, monkeypatch):
    import config

    cfg = owner.config; cfg.save_characters(cfg.load_characters())
    before = cfg.get_runtime_config_path('characters.json').read_bytes()
    package, _ = bundle_path(tmp_path, 'vrm')
    raw = package.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    urls, calls = {}, []
    market = 'https://market.example.test'
    driver = download.ConnectedDownloads(api.apply_installed)
    monkeypatch.setattr(api, 'connected_downloads', driver)
    monkeypatch.setattr(api, 'get_config_manager', lambda: cfg)
    monkeypatch.setattr(config, 'AUTOSTART_CSRF_TOKEN', 'loopback-test-csrf')
    monkeypatch.setattr(_shared, 'AUTOSTART_CSRF_TOKEN', 'loopback-test-csrf')
    monkeypatch.setattr(download, 'get_market_web_url', lambda: market)
    monkeypatch.setattr(download, 'resolve_user_plugin_base', lambda: urls['plugin'])
    monkeypatch.setattr(web_app, '_resolve_user_plugin_base', lambda: urls['plugin'])
    monkeypatch.setattr(auth, '_main_server_port', lambda: urlsplit(urls['main']).port)
    monkeypatch.setattr(auth, 'MARKET_API_URL', market)
    monkeypatch.setattr(bridge, 'MARKET_ORIGINS', [market])
    monkeypatch.setattr(bridge, '_slots', asyncio.Semaphore(2))

    async def token(): return {'access_token': 'desktop-test-only-token'}
    monkeypatch.setattr(auth, '_authenticated_market_token', token)

    class Upstream(httpx.AsyncBaseTransport):
        def __init__(self): self.local = httpx.AsyncHTTPTransport()
        async def handle_async_request(self, request):
            if request.url.host == 'market.example.test':
                calls.append(request)
                assert request.headers['authorization'] == 'Bearer desktop-test-only-token'
                if request.url.path == '/api/v1/models/7':
                    body = json.dumps({'id': 7, 'status': 'published', 'model_format': 'vrm',
                        'file_sha256': digest, 'byte_size': len(raw), 'capabilities': {'can_download': True}}).encode()
                    return httpx.Response(200, stream=httpx.ByteStream(body), headers={'content-type': 'application/json'})
                assert request.url.path == '/api/v1/models/7/download'
                return httpx.Response(200, stream=httpx.ByteStream(raw),
                    headers={'content-type': 'application/zip', 'content-length': str(len(raw))})
            assert request.url.host == '127.0.0.1' and request.url.port == urlsplit(urls['main']).port
            return await self.local.handle_async_request(request)
        async def aclose(self): await self.local.aclose()

    monkeypatch.setattr(bridge, '_client', lambda: httpx.AsyncClient(transport=Upstream(),
        timeout=5, trust_env=False, follow_redirects=False))

    @asynccontextmanager
    async def lifecycle(_):
        yield
        await driver.close()

    main = FastAPI(lifespan=lifecycle); main.include_router(api.router)
    main.add_api_route('/market/{path:path}', web_app.proxy_user_plugin_market_bridge, methods=['GET', 'POST'])
    plugin = FastAPI(); plugin.include_router(bridge.router)
    plugin.add_api_route('/market/pair-code', auth.market_pair_code, methods=['POST'])
    plugin.add_api_route('/market/bridge-token', auth.market_bridge_token, methods=['GET'])
    plugin.add_api_route('/market/token-exchange', auth.market_token_exchange, methods=['POST'])
    with serving(plugin) as urls['plugin'], serving(main) as urls['main'], httpx.Client(timeout=5, trust_env=False) as client:
        opened = client.post(urls['main'] + '/api/model-market/launch', json={},
            headers={'Origin': urls['main'], 'X-CSRF-Token': 'loopback-test-csrf'})
        assert opened.status_code == 200, opened.text
        selection = opened.json(); params = parse_qs(urlsplit(selection['url']).fragment.split('?', 1)[1])
        paired = client.post(urls['main'] + '/market/token-exchange', headers={'Origin': market},
                             json={'one_time_code': params['model_code'][0]})
        assert paired.status_code == 200, paired.text
        headers = {'Origin': market, 'Authorization': 'Bearer ' + paired.json()['bridge_token']}
        path = urls['main'] + '/market/models/launch/' + selection['request_id']
        body = {'model_id': 7, 'expected_sha256': digest, 'byte_size': len(raw), 'operation_id': '1' * 32}
        assert client.post(path + '/select', json=body, headers=headers).status_code == 202
        deadline = time.monotonic() + 5
        while True:
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            value = response.json()
            if value['phase'] not in {'downloading', 'installing'}: break
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert value['phase'] == 'installed', value
        assert value['result']['package_sha256'] == digest
        assert str(cfg.app_docs_dir) not in response.text and 'desktop-test-only-token' not in response.text
        assert cfg.get_runtime_config_path('characters.json').read_bytes() == before
        record = owner.installer.registry.installed()[0]
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert (owner.paths.root / record['local_entrypoint']).read_bytes() == archive.read('model/' + manifest['entrypoint'])
        assert client.post(path + '/select', json=body, headers=headers).status_code == 202
        assert len(calls) == 3  # descriptor, rechecked descriptor, one immutable download.
        assert client.get(urls['main'] + '/market/models/package/7', headers=headers).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['models/package/7', 'models/descriptor/7',
    'models/launch/' + 'r' * 32 + '/../../package/7', 'models/launch/' + 'r' * 32 + '/apply',
    'models/launch/' + 'r' * 32 + '/ready', 'models/launch/' + 'r' * 32 + '/invalidate',
    'x/../models/package/7', 'x/%2e%2e/models/package/7', 'x/./models/package/7',
    'x//models/package/7'])
async def test_generic_proxy_cannot_buffer_model_files_or_normalized_aliases(monkeypatch, path):
    monkeypatch.setattr(web_app, '_resolve_user_plugin_base', lambda: pytest.fail('must reject before any forwarding'))
    request = Request({'type': 'http', 'method': 'GET', 'path': '/market/' + path,
        'query_string': b'', 'headers': [], 'client': ('127.0.0.1', 40000)})
    response = await web_app.proxy_user_plugin_market_bridge(request, path)
    assert response.status_code == 403
