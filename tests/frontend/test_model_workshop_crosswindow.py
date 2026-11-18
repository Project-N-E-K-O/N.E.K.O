"""Real manager messages must reach the actual receiver and reload local resources."""

import json
from contextlib import nullcontext
from urllib.parse import quote

import pytest

from tests.frontend.test_model_workshop_apply_browser import (
    NAME, MODEL_READY, full_manager_app as full_manager_app, owner as owner, real_package,
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
            manager.locator('#model-workshop-panel summary').click()
            manager.locator('[data-workshop=file]').set_input_files(real_package(tmp_path, kind))
            manager.locator('[data-workshop=start]').click()
            manager.wait_for_function("!document.querySelector('[data-workshop=view]').hidden", timeout=60000)
            installed = manager.request.get(url + '/api/model-market/installed').json()['items'][0]
            manager.locator('[data-workshop=view]').click()
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
