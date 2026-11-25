"""Real manager messages must reach the actual receiver and reload local resources."""

import json
from contextlib import nullcontext
from urllib.parse import quote

import pytest

from tests.frontend.test_model_workshop_apply_browser import (
    NAME, MODEL_READY, full_manager_app as full_manager_app, owner as owner, real_package, legacy_import_and_preview,
)


@pytest.mark.parametrize('kind,exit_action', [
    ('live2d', 'return'), ('live2d', 'close'), ('vrm', 'return'),
    ('mmd', 'return'), ('mmd', 'close_loading'), ('pmd', 'close'),
])
def test_saved_workshop_model_reloads_the_opener(full_manager_app, tmp_path, kind, exit_action):
    from playwright.sync_api import sync_playwright

    url, cfg, applied = full_manager_app
    with sync_playwright() as pw:
        # Use full Chromium's platform renderer for actual avatar acceptance.
        # Keep the software-renderer failure as separate diagnostic evidence.
        browser = pw.chromium.launch(channel='chromium')
        context = browser.new_context(locale='zh-CN', viewport={'width': 1280, 'height': 900})
        context.set_default_timeout(20000)
        if kind == 'live2d' and exit_action == 'close':
            # Also cover the existing postMessage fallback without BroadcastChannel.
            context.add_init_script('window.BroadcastChannel = undefined;')
        host = context.new_page()
        # Bound host fill work in this two-window acceptance fixture.
        # The manager retains its normal desktop-sized viewport and real shaders.
        host.set_viewport_size({'width': 800, 'height': 600})
        held_model_requests = []
        if exit_action == 'close_loading':
            # Pause only the opener's PMX transfer. Continue the original HTTP
            # request after close; no model bytes, loader or receiver are replaced.
            host.route('**/user_mmd/**/*.pmx', lambda route: held_model_requests.append(route))
        logs = []
        def log(text):
            logs.append(text)
            (tmp_path / 'crosswindow-log.txt').write_text('\n'.join(logs), encoding='utf-8')
        host.on('console', lambda event: log(event.type + ': ' + event.text))
        host.on('pageerror', lambda error: log('pageerror: ' + str(error)))
        host.on('requestfailed', lambda request: log('requestfailed: ' + request.url + ' ' + str(request.failure)))
        host.on('response', lambda response: log('HTTP ' + str(response.status) + ' ' + response.url)
                if response.status >= 400 or 'ammo' in response.url else None)
        try:
            host.goto(url + '/workshop-host?lanlan_name=' + quote(NAME))
            host.wait_for_function("window.live2dManager?._modelLoadState === 'ready'", timeout=60000)
            log('checkpoint: host ready')
            with host.expect_popup() as opened:
                host.evaluate("url => window.open(url, 'workshop-model-manager')", url + '/model_manager?lanlan_name=' + quote(NAME))
            manager = opened.value
            manager.on('console', lambda event: log('manager ' + event.type + ': ' + event.text))
            manager.on('pageerror', lambda error: log('manager pageerror: ' + str(error)))
            manager.wait_for_function("document.querySelector('#model-workshop-panel')?.dataset.mounted === 'true'", timeout=90000)
            log('checkpoint: manager mounted')
            skip = manager.locator('#neko-page-tutorial-skip-btn')
            if skip.is_visible(): skip.click()
            # Exercise actual receiver filters before changing any saved binding.
            with host.expect_console_message(predicate=lambda message: '忽略来自其它角色' in message.text):
                manager.evaluate("sendMessageToMainPage('reload_model', {lanlan_name:'Other role'})")
            assert 'neko_model_' not in host.evaluate('window.live2dManager._lastLoadedModelPath')
            with host.expect_popup() as foreign_opened:
                host.evaluate("url => window.open(url, 'foreign-sender')", url.replace('127.0.0.1', 'localhost') + '/bridge-sender')
            foreign = foreign_opened.value
            foreign.wait_for_load_state()
            with host.expect_console_message(predicate=lambda message: '拒绝来自不同源的消息' in message.text):
                foreign.evaluate("name => window.opener.postMessage({action:'reload_model',lanlan_name:name,timestamp:Date.now()}, '*')", NAME)
            foreign.close()
            installed = legacy_import_and_preview(manager, url, real_package(tmp_path, kind), kind)
            manager.wait_for_function(MODEL_READY, arg={'kind':kind,'url':installed['url']}, timeout=90000)
            log('checkpoint: imported model visible')
            assert 'neko_model_' not in host.evaluate('window.live2dManager._lastLoadedModelPath')
            assert applied == []
            if skip.is_visible(): skip.click()
            manager.locator('#save-position-btn').click()
            manager.wait_for_function("window._modelManagerHasSaved && !window.hasUnsavedChanges")
            log('checkpoint: binding saved')
            if exit_action in ('return', 'close_loading'):
                # Save starts an asynchronous existing card-face choice. Await the
                # actual dialog, then explicitly choose the existing default-card flow.
                manager.locator('.modal-overlay').wait_for()
                request_started = (host.expect_request('**/user_mmd/**/*.pmx')
                                   if exit_action == 'close_loading' else nullcontext())
                with request_started:
                    manager.locator('.modal-overlay').last.locator('.modal-btn-secondary').click()
                manager.wait_for_function("!window._modelManagerCardFacePromptActive")
                # The flow catches card errors before notifying the host. Verify
                # the persisted PNG too, rather than accepting finalization alone.
                card = cfg.app_docs_dir / 'card_faces' / (NAME + '.png')
                assert card.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                log('checkpoint: default card persisted')
                if exit_action == 'close_loading':
                    host.wait_for_function('window.mmdManager?._isLoadingModel', polling=100)
                    assert held_model_requests
                    assert not host.evaluate(MODEL_READY, {'kind': kind, 'url': installed['url']})
                    log('checkpoint: opener loading while PMX request is held')
                    with manager.expect_event('close'):
                        manager.evaluate('window.close()')
                else:
                    # Return immediately; do not wait for the opener to load.
                    with manager.expect_event('close'):
                        manager.locator('#backToMainBtn').click()
            else:
                # Closing a saved manager must use the real beforeunload fallback.
                with manager.expect_event('close'):
                    manager.evaluate('window.close()')
            log('checkpoint: manager closed')
            for route in held_model_requests:
                route.continue_()
            # Closing a popup does not guarantee that headless Chromium foregrounds
            # its opener; resume real animation frames before checking visibility.
            host.bring_to_front()
            host.wait_for_function(MODEL_READY, arg={'kind':kind,'url':installed['url']}, polling=250, timeout=90000)
            log('checkpoint: host model visible')
            assert applied == [NAME]
            # Closing/reloading changes the avatar binding, not its Steam card
            # provenance or the number of existing characters.
            characters = cfg.load_characters()['猫娘']
            assert len(characters) == 1
            reserved = characters[NAME]['_reserved']
            assert reserved['avatar']['asset_source'] == 'local_imported'
            assert not reserved['avatar'].get('asset_source_id')
            assert reserved['character_origin']['source'] == 'steam_workshop'
            assert not [text for text in logs if '模型热切换失败' in text]
            assert not [text for text in logs if text.startswith('pageerror:') or text.startswith('manager pageerror:')]
            host.screenshot(path=str(tmp_path / 'host-reloaded.png'), timeout=10000)
            (tmp_path / 'result.json').write_text(json.dumps(installed), encoding='utf-8')
        finally:
            browser.close()


def test_connected_native_owner_reloads_and_continues_a_b_a_without_manual_save(full_manager_app, tmp_path, monkeypatch):
    """Actual native edit witness, HTTP apply, runtime and host renderer.

    Pairing/Market selection is the sole substitute; the previous loopback and
    React tests cover those hops separately. This is not a real Auth acceptance.
    """
    import time
    from main_routers import model_market_router as api
    from services.model_workshop.download import ConnectedDownloads
    from playwright.sync_api import sync_playwright

    url, cfg, applied = full_manager_app
    driver = ConnectedDownloads()
    monkeypatch.setattr(api, 'connected_downloads', driver)
    launches = []
    installed = {}

    def received(config, operation):
        # Only the Market byte source is substituted. Installs below, Main's
        # selection admission, edit witness, save, reload and GPU rendering are real.
        model_id, digest, size = operation.selection
        package_size, result = installed[model_id]
        assert config is cfg and digest == result['package_sha256'] and size == package_size
        operation.result, operation.phase = result, 'installed'
    monkeypatch.setattr(driver, '_receive', received)

    async def launch(config, origin):
        assert config is cfg and origin == url
        row = driver.launches.create(config); launches.append(row)
        return {**row.public(), 'url':'https://market.example.test/#/models'}
    monkeypatch.setattr(driver, 'launch', launch)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chromium')
        context = browser.new_context(locale='zh-CN', viewport={'width':1280, 'height':900})
        # Only suppress opening an external Market browser in this isolated test.
        context.add_init_script('window.electronShell = {openExternal: async () => {}};')
        host = context.new_page(); host.set_viewport_size({'width':800, 'height':600})
        logs = []
        manager = None
        try:
            host.goto(url + '/workshop-host?lanlan_name=' + quote(NAME))
            host.wait_for_function("window.live2dManager?._modelLoadState === 'ready'", timeout=60000)
            with host.expect_popup() as opened:
                host.evaluate("url => window.open(url, 'connected-manager')", url + '/model_manager?lanlan_name=' + quote(NAME))
            manager = opened.value
            manager.on('console', lambda message: logs.append(message.type + ': ' + message.text))
            manager.on('pageerror', lambda error: logs.append('pageerror: ' + str(error)))
            manager.wait_for_function("document.querySelector('#model-workshop-panel')?.dataset.mounted === 'true'", timeout=90000)
            skip = manager.locator('#neko-page-tutorial-skip-btn')
            if skip.is_visible(): skip.click()
            manager.wait_for_function('!isModelManagerSettingsWaiting() && !window.hasUnsavedChanges')
            token = manager.request.get(url + '/api/config/page_config').json()['autostart_csrf_token']
            headers = {'Origin':url, 'X-CSRF-Token':token}
            # Install two renderable formats using the normal bounded HTTP receiver.
            for model_id, kind in enumerate(('live2d', 'vrm'), 1):
                folder = tmp_path / str(model_id); folder.mkdir()
                package = real_package(folder, kind)
                accepted = manager.request.post(url + '/api/model-market/imports', headers=headers,
                    multipart={'file':{'name':package.name, 'mimeType':'application/zip', 'buffer':package.read_bytes()}})
                assert accepted.status == 202
                deadline = time.monotonic() + 60
                while True:
                    state = manager.request.get(url + '/api/model-market/tasks/' + accepted.json()['task_id']).json()
                    if state['phase'] in {'completed','failed','cancelled'}: break
                    assert time.monotonic() < deadline
                    time.sleep(.02)
                assert state['phase'] == 'completed'
                installed[model_id] = (package.stat().st_size, state['result'])
            manager.locator('[data-workshop=start]').click()
            manager.wait_for_function("sessionStorage.getItem('neko.modelWorkshop.launch') !== null")
            assert len(launches) == 1 and applied == []
            path = '/api/model-market/launch/' + launches[0].request_id
            for index, model_id in enumerate((1, 2, 1)):
                size, result = installed[model_id]
                operation = {'operation_id': f'{index + 1:032x}', 'model_id': model_id,
                             'expected_sha256': result['package_sha256'], 'byte_size': size}
                accepted = manager.request.post(url + path + '/select', headers=headers, data=operation)
                assert accepted.status == 202, accepted.text()
                # The actual native page must save, notify and reload without another entry click.
                host.bring_to_front()
                expected = {'kind':result['model_format'], 'url':result['url']}
                host.wait_for_function(MODEL_READY, arg=expected, timeout=90000)
                manager.wait_for_function(MODEL_READY, arg=expected, timeout=90000)
                manager.wait_for_function("async path => (await fetch(path).then(r => r.json())).can_select === true", arg=path, timeout=90000)
                assert applied == [NAME] * (index + 1)
                assert not manager.is_closed() and len(launches) == 1
                # Lost/repeated acknowledgements must not create another save or reload.
                assert manager.request.post(url + path + '/select', headers=headers, data=operation).status == 202
                assert applied == [NAME] * (index + 1)
                host.screenshot(path=str(tmp_path / f'continuous-{index + 1}-{result["model_format"]}.png'))
            reserved = cfg.load_characters()['猫娘'][NAME]['_reserved']
            assert reserved['avatar']['asset_source'] == 'local_imported'
            assert reserved['character_origin']['source'] == 'steam_workshop'
            host.screenshot(path=str(tmp_path/'connected-host.png'))
        except Exception:
            # Preserve the actual manager state before teardown, rather than
            # inferring a renderer or edit-witness problem from a timeout alone.
            if manager is not None:
                diagnostic = manager.evaluate('''() => ({url:location.href,
                    handle:sessionStorage.getItem('neko.modelWorkshop.launch'),
                    operation:sessionStorage.getItem('neko.modelWorkshop.launch.operation'),
                    dirty:window.hasUnsavedChanges, parameters:window._modelManagerParameterEditedSinceSave,
                    waiting:typeof isModelManagerSettingsWaiting === 'function' && isModelManagerSettingsWaiting(),
                    model:window.live2dManager?._lastLoadedModelPath, state:window.live2dManager?._modelLoadState,
                    vrm:window.vrmManager?.currentModel?.url, vrmState:window.vrmManager?._loadState})''')
                diagnostic['task'] = driver.status(cfg, launches[0].request_id) if launches else None
                (tmp_path / 'continuous-diagnostic.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2))
                (tmp_path / 'continuous-browser.log').write_text('\n'.join(logs))
                manager.screenshot(path=str(tmp_path / 'continuous-manager-failure.png'), timeout=10000)
            raise
        finally:
            browser.close()
