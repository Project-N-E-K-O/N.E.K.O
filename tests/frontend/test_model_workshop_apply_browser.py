"""Render repository avatars through the full manager and real isolated save APIs.

Only chat/runtime startup and Steam discovery are absent. Model renderers,
page scripts, package import, resource reads, character persistence and reload
remain real; all writes stay in the temporary ConfigManager root/project.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.model_workshop_static import ModelWorkshopRequestGate, ReadOnlyWorkshopStaticFiles
from main_routers import characters_router, config_router, live2d_router, mmd_router, model_market_router as api, shared_state, vrm_router
from main_routers.pages_router import _static_assets_ctx, _vrm_defaults_ctx
from services.model_workshop import catalog
from tests.unit.test_model_workshop_formats import make_native_bundle
from tests.unit.test_model_workshop_migration import owner as owner

ROOT = Path(__file__).resolve().parents[2]
NAME = '工坊 测试'

MODEL_READY = '''data => {
    if (data.kind === 'live2d') {
        const manager=window.live2dManager;
        const bounds=manager?.currentModel?.getBounds();
        return manager?._lastLoadedModelPath === data.url && manager?._modelLoadState === 'ready'
            && bounds?.width > 0 && bounds?.height > 0;
    }
    const manager=data.kind === 'vrm'?window.vrmManager:window.mmdManager;
    const path=manager?.currentModel?.url;
    const canvas=manager?.renderer?.domElement;
    const visible=data.kind === 'vrm'?manager?.currentModel?.vrm?.scene?.visible
        :manager?.currentModel?.mesh?.visible;
    const rect=canvas?.getBoundingClientRect();
    // VRM sets currentModel before its animation warmup and fade-in.
    // Wait for the actual visible frame, not the early resource handle.
    return path && new URL(path,location.href).href === new URL(data.url,location.href).href
        && visible && (data.kind !== 'vrm' || (manager._loadState === 'ready' && !manager._isLoadingModel))
        && rect?.width > 0 && rect?.height > 0 && Number(getComputedStyle(canvas).opacity) >= 0.99
        && manager.renderer?.info?.render?.calls > 0;
}'''


@pytest.fixture
def full_manager_app(owner, monkeypatch, tmp_path):
    cfg = owner.config
    original = cfg.load_characters()
    character = copy.deepcopy(next(iter(original['猫娘'].values())))
    character['_reserved']['avatar'] = {
        'model_type': 'live2d', 'live2d': {'model_path': '/static/mao_pro/mao_pro.model3.json'},
        'asset_source': 'builtin', 'asset_source_id': '',
    }
    character['_reserved']['character_origin'] = {'source': 'steam_workshop'}
    original['猫娘'] = {NAME: character}
    original['当前猫娘'] = NAME
    cfg.save_characters(original)
    project = tmp_path / 'project'
    if not (ROOT / 'static/mao_pro/mao_pro.model3.json').is_file():
        pytest.skip('Unpacked repository avatar resources are required for GPU acceptance')
    shutil.copytree(ROOT / 'static/mao_pro', project / 'static/mao_pro')
    (project / 'static/vrm/configs').mkdir(parents=True)
    monkeypatch.setattr(cfg, '_get_project_root', lambda: project)
    monkeypatch.setattr(shared_state, '_state', shared_state._state.copy())
    applied = []
    async def apply(name, **kwargs):
        applied.append(name)
    templates = Jinja2Templates(directory=str(ROOT))
    shared_state.init_shared_state(role_state={}, steamworks=None, templates=templates,
                                   config_manager=cfg, init_one_catgirl=apply)
    monkeypatch.setattr(api, 'get_config_manager', lambda: cfg)
    monkeypatch.setattr(catalog, 'get_config_manager', lambda: cfg)
    for module in (live2d_router, mmd_router, vrm_router):
        monkeypatch.setattr(module, 'get_subscribed_workshop_items', AsyncMock(return_value={'success': True, 'items': []}))
    from utils import frontend_utils, config_manager
    monkeypatch.setattr(config_manager, "get_config_manager", lambda *_, **__: cfg)
    monkeypatch.setattr(frontend_utils, '_resolve_workshop_search_dir', lambda: None)
    app = FastAPI()
    for router in (api.router, config_router.router, characters_router.router,
                   live2d_router.router, vrm_router.router, mmd_router.router):
        app.include_router(router)
    app.add_middleware(ModelWorkshopRequestGate, config_getter=lambda: cfg)
    app.add_middleware(api.ModelImportBodyGuard, config_getter=lambda: cfg)
    for kind in ('live2d', 'vrm', 'mmd'):
        getattr(cfg, 'ensure_' + kind + '_directory')()
        app.mount('/user_' + kind, ReadOnlyWorkshopStaticFiles(
            directory=getattr(cfg, kind + '_dir'), config_manager=cfg, maintain_main=True))
    app.mount('/static/vrm/configs', StaticFiles(directory=project / 'static/vrm/configs'))
    app.mount('/static', StaticFiles(directory=ROOT / 'static'))
    @app.get('/model_manager')
    def manager(request: Request):
        return templates.TemplateResponse(request, 'templates/model_manager.html', {
            'request': request, **_vrm_defaults_ctx(), **_static_assets_ctx(),
            'model_workshop_enabled': True,
        })
    @app.get('/workshop-host')
    def workshop_host(request: Request):
        # Real model renderers plus the actual home-page message receiver/reload
        # parts. Chat startup is absent; no receiver or model reload is stubbed.
        html = templates.get_template('templates/model_manager.html').render({
            'request': request, **_vrm_defaults_ctx(), **_static_assets_ctx()})
        excluded = {'named-window-registration.js', 'page-controller.js',
                    'page-bridge.js', 'card-face.js', 'window-lifecycle.js',
                    'background-model-drag.js'}
        html = re.sub(r'<script[^>]+src="/static/js/model_manager/([^"?]+)(?:\?[^">]*)?"[^>]*></script>',
                      lambda match: '' if match.group(1) in excluded else match.group(0), html)
        bootstrap = ('<script src="/static/app/app-state.js"></script>'
                     '<script src="/static/js/index.js"></script>'
                     '<script>window.showStatusToast=()=>{};window.startPageConfigLoad();</script>')
        html = html.replace('<head>', '<head>' + bootstrap, 1)
        index = (ROOT / 'templates/index.html').read_text(encoding='utf-8')
        sources = re.findall(r'/static/app/app-interpage/[^?" ]+\.js', index)
        scripts = '<script src="/static/common-ui-hud.js"></script>'
        scripts += ''.join('<script src="' + path + '"></script>' for path in sources)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html.replace('</body>', scripts + '</body>'))

    @app.get('/bridge-sender')
    def bridge_sender():
        from fastapi.responses import HTMLResponse
        return HTMLResponse('<!doctype html><title>Isolated message sender</title>')

    import uvicorn
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f'http://127.0.0.1:{sock.getsockname()[1]}', cfg, applied
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def real_package(tmp_path, kind):
    if kind == 'pmd':
        # External binary resources stay out of Git. The acceptance runner supplies
        # the independently sourced PMD directory after verifying its blob hashes.
        supplied = os.environ.get('NEKO_WORKSHOP_PMD_SAMPLE', '')
        if not supplied:
            pytest.skip('A verified external PMD sample is required')
        source = Path(supplied)
        names = ('miku_v2.pmd', 'eyeM2.bmp', 'readme.txt', 'readme_miku_v2.txt')
        return make_native_bundle(tmp_path, kind='mmd', entry='miku_v2.pmd',
                                  files={name: (source / name).read_bytes() for name in names})
    source = ROOT / {'live2d': 'static/mao_pro', 'vrm': 'static/vrm/sister1.0.vrm', 'mmd': 'static/mmd/Miku'}[kind]
    if not source.exists():
        pytest.skip('Unpacked repository model resources are required for GPU acceptance')
    entry = {'live2d': 'mao_pro.model3.json', 'vrm': 'sister1.0.vrm', 'mmd': 'Miku.pmx'}[kind]
    files = ({path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob('*') if path.is_file()}
             if source.is_dir() else {source.name: source.read_bytes()})
    return make_native_bundle(tmp_path, kind=kind, entry=entry, files=files)


@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'mmd', 'pmd'])
def test_full_manager_renders_imported_model_and_reopens_saved_binding(full_manager_app, tmp_path, kind):
    from playwright.sync_api import sync_playwright
    url, cfg, applied = full_manager_app
    package = real_package(tmp_path, kind)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader', '--use-angle=swiftshader'])
        page = browser.new_page(locale='zh-CN', viewport={'width': 1280, 'height': 900})
        logs = []
        def log(message):
            logs.append(message)
            (tmp_path / 'browser-log.txt').write_text('\n'.join(logs), encoding='utf-8')
        page.on('console', lambda message: log(message.type + ': ' + message.text))
        page.on('pageerror', lambda error: log('pageerror: ' + str(error)))
        page.on('response', lambda response: log('HTTP ' + str(response.status) + ' ' + response.url) if response.status >= 400 else None)
        try:
            page.goto(url + '/model_manager?lanlan_name=' + NAME)
            page.wait_for_function("document.querySelector('#model-workshop-panel')?.dataset.mounted === 'true'", timeout=90000)
            skip = page.locator('#neko-page-tutorial-skip-btn')
            if skip.is_visible():
                skip.click()
            page.locator('#model-workshop-panel summary').click()
            page.locator('[data-workshop="file"]').set_input_files(package)
            page.locator('[data-workshop="start"]').click()
            page.wait_for_function("document.querySelector('[data-workshop=view]').hidden === false", timeout=90000)
            assert applied == []
            installed = page.request.get(url + '/api/model-market/installed').json()['items'][0]
            page.locator('[data-workshop="view"]').click()
            ready = MODEL_READY
            page.wait_for_function(ready, arg={'kind':kind,'url':installed['url']}, timeout=60000)
            page.wait_for_function("!isModelManagerSettingsWaiting() && !document.getElementById('save-position-btn').disabled")
            assert applied == []
            assert cfg.load_characters()['猫娘'][NAME]['_reserved']['avatar']['asset_source'] == 'builtin'
            if skip.is_visible():
                skip.click()  # Use the existing tutorial action, without changing tutorial code.
            page.screenshot(path=str(tmp_path / ('imported-' + kind + '.png')))
            # Use the original save button; never save from the workshop adapter.
            page.locator('#save-position-btn').click()
            page.wait_for_function("window._modelManagerHasSaved === true && !window.hasUnsavedChanges", timeout=30000)
            assert applied == [NAME]
            avatar=cfg.load_characters()['猫娘'][NAME]['_reserved']['avatar']
            assert avatar['asset_source'] == 'local_imported'
            assert not avatar.get('asset_source_id')
            assert cfg.load_characters()['猫娘'][NAME]['_reserved']['character_origin']['source'] == 'steam_workshop'
            assert len(cfg.load_characters()['猫娘']) == 1
            # Reopen without choosing card art; the model binding is already saved.
            page.reload()
            page.wait_for_function("document.querySelector('#model-workshop-panel')?.dataset.mounted === 'true'", timeout=90000)
            page.wait_for_function(ready, arg={'kind':kind,'url':installed['url']}, timeout=60000)
            assert applied == [NAME]  # Reopening only reads the persisted binding.
            selected = '#model-select' if kind == 'live2d' else '#vrm-model-select'
            assert page.locator(selected + ' option:checked').text_content() == installed['display_name']
            if skip.is_visible():
                skip.click()
            page.screenshot(path=str(tmp_path / ('reopened-' + kind + '.png')))
            # Rendering/application must not rewrite binary assets or lip-sync curves.
            state = json.loads((cfg.app_docs_dir / 'model_workshop/installed.json').read_text(encoding='utf-8'))
            record = state['installed'][installed['package_sha256']]
            for asset in record['files']:
                assert hashlib.sha256((cfg.app_docs_dir / asset['local_path']).read_bytes()).hexdigest() == asset['sha256']
            assert not [message for message in logs if message.startswith('pageerror:')]

        finally:
            (tmp_path / 'browser-log.txt').write_text('\n'.join(logs), encoding='utf-8')
            page.screenshot(path=str(tmp_path / 'last-page.png'))
            browser.close()
