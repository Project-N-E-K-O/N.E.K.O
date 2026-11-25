"""Real connected panel/template in Chromium; HTTP outcomes and GPU host isolated."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jinja2 import Environment
from playwright.sync_api import sync_playwright

from main_routers.pages_router import _vrm_defaults_ctx

ROOT = Path(__file__).resolve().parents[2]
REQUEST = 'r' * 32
OPERATION = '1' * 32


@pytest.fixture
def page():
    template = (ROOT / 'templates/model_manager.html').read_text()
    disabled = Environment().from_string(template).render(**_vrm_defaults_ctx())
    assert 'id="model-workshop-panel"' not in disabled
    rendered = Environment().from_string(template).render(model_workshop_enabled=True, **_vrm_defaults_ctx())
    panel = re.search(r'<section id="model-workshop-panel".*?</section>', rendered, re.S).group()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://127.0.0.1:45899/**', lambda route: route.fulfill(content_type='text/html', body='<title>Workshop fixture</title>'))
        # Load actual artwork/styles for visual evidence; API outcomes stay isolated.
        page.route('**/static/**', lambda route: route.fulfill(path=str(ROOT / urlsplit(route.request.url).path.lstrip('/')))
                   if (ROOT / urlsplit(route.request.url).path.lstrip('/')).is_file() else route.abort())
        page.goto('http://127.0.0.1:45899/model_manager')
        page.set_content('<link rel="stylesheet" href="/static/css/model_manager.css">' + panel + '<input id="editor">')
        page.add_style_tag(content=(ROOT / 'static/css/model_manager.css').read_text())
        page.add_script_tag(content=(ROOT / 'static/js/model_manager/model-workshop.js').read_text())
        page.evaluate('''() => {
            window.testState = {dirty:false, character:'Original', applied:[], clean:[], opened:[], events:[], openFailed:false, minimizeFailed:false};
            window.electronShell = {openExternal: async url => {
                testState.events.push('open');
                if (testState.openFailed) return {success:false};
                testState.opened.push(url); return {success:true};
            }};
            window.nekoWindowControl = {minimize: async () => {
                testState.events.push('minimize');
                if (testState.minimizeFailed) throw new Error('window unavailable');
                return {ok:true};
            }};
            window.mountPanel = locale => NekoModelWorkshop.mount(document.getElementById('model-workshop-panel'), {
                translate: (key, values={}) => Object.entries(values).reduce((text,[name,value]) => text.replace('{{'+name+'}}',value), locale[key]),
                capture: async () => ({character:testState.character}),
                current: async (context,name) => !testState.dirty && context.character === name && testState.character === name,
                applied: (data,clean) => { testState.applied.push(data.character_name); testState.clean.push(clean); }
            });
        }''')
        yield page
        browser.close()
        assert not errors, errors


def mounted(page, locale='en'):
    texts = json.loads((ROOT / 'static/locales' / (locale + '.json')).read_text())['modelWorkshop']
    page.evaluate('texts => mountPanel(texts)', texts)
    return texts


def network(page, phase='browsing'):
    state = {'phase': phase, 'writes': [], 'reads': 0, 'deny': False, 'unknown': False, 'saved_on_cancel': False,
             'ready': phase == 'browsing', 'operation_id': OPERATION, 'connection_error': None}
    def respond(route):
        path = route.request.url.split('/api/model-market/launch', 1)[-1]
        if route.request.method == 'POST':
            state['writes'].append(path)
            assert route.request.headers['x-csrf-token'] == 'test-native-only'
            assert route.request.post_data_json == ({'operation_id': state['operation_id']}
                if path.endswith(('/apply', '/ready')) else {})
            if path.endswith('/apply'): state['phase'] = 'saved'; state['ready'] = False
            if path.endswith('/ready'): state['ready'] = True
            if path.endswith('/invalidate'):
                state['phase'] = 'saved' if state['saved_on_cancel'] else state['phase'] if state['phase'] in {'saved', 'applying'} else 'cancelled'
                state['connection_error'] = 'model_market_target_changed'
        else:
            state['reads'] += 1
            if state['unknown']: return route.abort()
        if state['deny']: return route.fulfill(status=404, json={'success':False, 'error_code':'model_market_expired'})
        route.fulfill(json={'success':True, 'request_id':REQUEST, 'character_name':'Original',
                            'phase':state['phase'], 'url':'https://market.example.test/#/models?model_code=test',
                            'operation_id': None if state['phase'] == 'browsing' else state['operation_id'],
                            'can_select':state['ready'], 'connection_error':state['connection_error']})
    page.route('**/api/config/page_config', lambda route: route.fulfill(json={'autostart_csrf_token':'test-native-only'}))
    page.route('**/api/model-market/launch**', respond)
    return state


@pytest.mark.parametrize('locale', ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'])
def test_button_uses_workshop_and_keeps_original_import_separate(page, locale):
    page.set_viewport_size({'width':390,'height':844})
    state = network(page); texts = mounted(page, locale)
    assert page.locator('[data-workshop=file]').count() == 0
    assert page.locator('[data-workshop=view]').count() == 0
    assert page.locator('#model-workshop-panel p, #model-workshop-panel progress, [data-workshop=cancel], [data-workshop=retry]').count() == 0
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.opened.length === 1')
    assert page.locator('[data-workshop=start]').inner_text().strip() == texts['title']
    assert page.locator('[data-workshop=start] span').get_attribute('data-text') == texts['title']
    assert page.locator('#model-workshop-panel').inner_text() == texts['title']
    assert page.evaluate('testState.events') == ['open', 'minimize']
    assert page.locator('[data-workshop=start]').evaluate('button => button.scrollWidth <= button.clientWidth + 1')
    assert state['writes'] == ['']
    assert page.evaluate('sessionStorage.getItem("neko.modelWorkshop.launch")') == REQUEST


def test_installed_result_applies_once_and_recovers_connection_after_clean_reload(page):
    state = network(page, 'installed'); mounted(page)
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.applied.length === 1')
    assert state['writes'] == ['', '/' + REQUEST + '/apply']
    assert page.evaluate('sessionStorage.getItem("neko.modelWorkshop.launch")') == REQUEST
    # Recreate only the page instance: a pending save cannot acknowledge its own reload.
    page.evaluate('''() => { dispatchEvent(new Event('pagehide')); delete document.getElementById('model-workshop-panel').dataset.mounted; }''')
    mounted(page)
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    assert state['writes'] == ['', '/' + REQUEST + '/apply', '/' + REQUEST + '/ready']
    assert page.evaluate('testState.applied') == ['Original']
    assert page.locator('[data-workshop=start]').is_enabled()


def test_next_operation_is_applied_after_reload_without_another_entry_click(page):
    """Only a new operation causes another save/notification on the retained pairing."""
    state = network(page, 'installed'); mounted(page)
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.applied.length === 1')
    page.evaluate('''() => { dispatchEvent(new Event('pagehide')); delete document.getElementById('model-workshop-panel').dataset.mounted; }''')
    mounted(page)
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    state.update(phase='installed', ready=False, operation_id='2' * 32)
    page.wait_for_function('testState.applied.length === 2')
    assert state['writes'].count('') == 1
    assert state['writes'].count('/' + REQUEST + '/apply') == 2
    assert page.evaluate('testState.opened.length') == 1


@pytest.mark.parametrize('reason', ['dirty', 'role', 'input'])
def test_edit_or_character_change_never_applies_late_install(page, reason):
    state = network(page); mounted(page)
    page.locator('[data-workshop=start]').click(); page.wait_for_function('testState.opened.length === 1')
    if reason == 'dirty': page.evaluate('testState.dirty = true')
    elif reason == 'role': page.evaluate('testState.character = "Other"')
    else: page.locator('#editor').fill('changed and then restored'); page.locator('#editor').fill('')
    state['phase'] = 'installed'
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    assert state['writes'] == ['', '/' + REQUEST + '/invalidate']
    assert page.evaluate('testState.applied') == []


def test_dirty_page_does_not_create_launch(page):
    state = network(page); mounted(page); page.evaluate('testState.dirty = true')
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    assert state['writes'] == [] and page.evaluate('testState.opened') == []


def test_edit_cancellation_racing_save_reports_actual_saved_result(page):
    state = network(page); mounted(page)
    page.locator('[data-workshop=start]').click(); page.wait_for_function('testState.opened.length === 1')
    page.locator('#editor').fill('keep this edit')
    state['saved_on_cancel'] = True; state['phase'] = 'installed'
    page.wait_for_function('testState.applied.length === 1')
    assert state['phase'] == 'saved'
    assert page.locator('#model-workshop-panel p, #model-workshop-panel progress').count() == 0
    assert page.locator('#editor').input_value() == 'keep this edit'
    assert page.evaluate('testState.clean') == [False]
    assert state['writes'] == ['', '/' + REQUEST + '/invalidate']


def test_revocation_during_commit_observes_final_save_without_reauthorizing(page):
    """An in-flight commit remains observable after editing revokes future selections."""
    state = network(page); mounted(page)
    page.locator('[data-workshop=start]').click(); page.wait_for_function('testState.opened.length === 1')
    state['phase'], state['ready'] = 'applying', False
    with page.expect_response(lambda response: response.url.endswith('/invalidate')):
        page.locator('#editor').fill('keep this unsaved edit')
    page.wait_for_function('document.querySelector("[data-workshop=start]").disabled')
    assert page.evaluate('testState.applied') == []
    state['phase'] = 'saved'
    page.wait_for_function('testState.applied.length === 1')
    assert page.evaluate('testState.clean') == [False]
    assert not any(path.endswith(('/apply', '/ready')) for path in state['writes'])
    assert page.locator('#editor').input_value() == 'keep this unsaved edit'


def test_missing_task_never_relaunches_or_applies(page):
    state = network(page); state['deny'] = True
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch",id)', REQUEST)
    mounted(page)
    page.wait_for_function('sessionStorage.getItem("neko.modelWorkshop.launch") === null')
    assert page.locator('[data-workshop=start]').is_enabled()
    assert state['writes'] == []


def test_transient_status_failure_recovers_without_inline_ui_or_relaunch(page):
    state = network(page); state['unknown'] = True
    mounted(page); page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.opened.length === 1')
    page.wait_for_timeout(1100)
    state['unknown'] = False; state['phase'] = 'installed'
    page.wait_for_function('testState.applied.length === 1')
    assert state['writes'] == ['', '/' + REQUEST + '/apply']
    assert page.locator('#model-workshop-panel').inner_text() == json.loads((ROOT / 'static/locales/en.json').read_text())['modelWorkshop']['title']


def test_external_open_failure_does_not_minimize_the_manager(page):
    state = network(page); mounted(page); page.evaluate('testState.openFailed = true')
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    assert page.evaluate('testState.events') == ['open']
    assert state['writes'] == ['', '/' + REQUEST + '/invalidate']


def test_minimize_failure_keeps_accepted_task_and_automatic_apply(page):
    state = network(page, 'installed'); mounted(page); page.evaluate('testState.minimizeFailed = true')
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.applied.length === 1')
    assert page.evaluate('testState.events') == ['open', 'minimize']
    assert state['writes'] == ['', '/' + REQUEST + '/apply']


def test_browser_fallback_opens_market_without_native_window_control(page):
    network(page); mounted(page)
    page.evaluate('delete window.electronShell; delete window.nekoWindowControl')
    page.context.route('https://market.example.test/**', lambda route: route.fulfill(content_type='text/html',body='<title>Market</title>'))
    with page.expect_popup() as opened:
        page.locator('[data-workshop=start]').click()
    popup = opened.value
    popup.wait_for_url('https://market.example.test/**')
    # Playwright retains the creation relationship; verify the page's actual
    # scripting access instead of its historical opener metadata.
    assert popup.evaluate('window.opener === null')
    assert page.evaluate('testState.events') == []



def test_saved_handle_recovery_notifies_once_and_never_saves_again(page):
    state = network(page, 'saved')
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch",id)', REQUEST)
    mounted(page); page.wait_for_function('testState.applied.length === 1')
    assert not state['writes']


def test_reload_cannot_renew_permission_invalidated_by_page_edit(page):
    state = network(page, 'installed')
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch", "blocked:" + id)', REQUEST)
    mounted(page)
    page.wait_for_function('!document.querySelector("[data-workshop=start]").disabled')
    assert state['writes'] == ['/' + REQUEST + '/invalidate']
    assert page.evaluate('testState.applied') == []


def test_light_dark_narrow_panel_preserves_manager_theme(page, tmp_path):
    template = (ROOT / 'templates/model_manager.html').read_text()
    rendered = Environment().from_string(template).render(model_workshop_enabled=True, **_vrm_defaults_ctx())
    # Full production layout and assets, without GPU/startup or provider calls.
    page.set_content(re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S))
    network(page); mounted(page, 'zh-CN')
    for theme in ['light', 'dark']:
        page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        for width in [390, 1100]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.locator('#model-workshop-panel').bounding_box()['width'] <= width
            # The workshop participates in the same real CSS rules as import.
            styles = page.evaluate('''() => {
                const read = (selector, pseudo) => {
                    const style = getComputedStyle(document.querySelector(selector), pseudo);
                    return Object.fromEntries(['backgroundImage','borderWidth','borderRadius','padding',
                        'fontSize','fontWeight','minHeight','boxSizing'].map(key => [key,style[key]]));
                };
                return {entry:read('#model-workshop-btn'), original:read('#upload-btn'),
                    entryMask:read('#model-workshop-btn','::before'), originalMask:read('#upload-btn','::before'),
                    entryText:read('#model-workshop-btn span'), originalText:read('#upload-btn span')};
            }''')
            assert styles['entry'] == styles['original']
            assert styles['entryText'] == styles['originalText']
            assert styles['entryMask'] == styles['originalMask']
            entry = page.locator('#model-workshop-btn').bounding_box()
            original = page.locator('#upload-btn').bounding_box()
            assert entry['x'] == original['x'] and entry['width'] == original['width']
            page.screenshot(path=str(tmp_path / ('connected-' + theme + '-' + str(width) + '.png')))


def test_rejected_csrf_token_is_reloaded_for_the_next_explicit_attempt(page):
    network(page)
    reads = []
    def config(route):
        reads.append(True)
        route.fulfill(json={"autostart_csrf_token": "old-token" if len(reads) == 1 else "test-native-only"})
    page.route("**/api/config/page_config", config)
    def launch(route):
        if route.request.method == "POST" and route.request.headers.get("x-csrf-token") == "old-token":
            return route.fulfill(status=403, json={"success": False, "error_code": "csrf_validation_failed"})
        route.fallback()
    page.route("**/api/model-market/launch**", launch)
    mounted(page)
    button = page.locator('[data-workshop=start]')
    button.click()
    page.wait_for_function("!document.querySelector('[data-workshop=start]').disabled")
    assert page.evaluate('testState.opened.length') == 0
    button.click()
    page.wait_for_function('testState.opened.length === 1')
    assert len(reads) == 2
