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
            window.testState = {dirty:false, character:'Original', applied:[], clean:[], opened:[]};
            window.electronShell = {openExternal: async url => testState.opened.push(url)};
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
    state = {'phase': phase, 'writes': [], 'reads': 0, 'deny': False, 'unknown': False, 'saved_on_cancel': False}
    def respond(route):
        path = route.request.url.split('/api/model-market/launch', 1)[-1]
        if route.request.method == 'POST':
            state['writes'].append(path)
            assert route.request.headers['x-csrf-token'] == 'test-native-only'
            assert route.request.post_data_json == {}
            if path.endswith('/apply'): state['phase'] = 'saved'
            if path.endswith('/cancel'): state['phase'] = 'saved' if state['saved_on_cancel'] else 'cancelled'
        else:
            state['reads'] += 1
            if state['unknown']: return route.abort()
        if state['deny']: return route.fulfill(status=403, json={'success':False})
        route.fulfill(json={'success':True, 'request_id':REQUEST, 'character_name':'Original',
                            'phase':state['phase'], 'url':'https://market.example.test/#/models?model_code=test'})
    page.route('**/api/config/page_config', lambda route: route.fulfill(json={'autostart_csrf_token':'test-native-only'}))
    page.route('**/api/model-market/launch**', respond)
    return state


@pytest.mark.parametrize('locale', ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'])
def test_button_uses_workshop_and_keeps_original_import_separate(page, locale):
    state = network(page); texts = mounted(page, locale)
    assert page.locator('[data-workshop=file]').count() == 0
    assert page.locator('[data-workshop=view]').count() == 0
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.opened.length === 1')
    assert page.locator('[data-workshop=start]').text_content() == texts['title']
    assert state['writes'] == ['']
    assert page.evaluate('sessionStorage.getItem("neko.modelWorkshop.launch")') == REQUEST


def test_installed_result_automatically_applies_once_without_save_or_preview(page):
    state = network(page, 'installed'); mounted(page)
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('testState.applied.length === 1')
    assert state['writes'] == ['', '/' + REQUEST + '/apply']
    assert page.evaluate('sessionStorage.getItem("neko.modelWorkshop.launch")') is None
    assert page.locator('[data-workshop=cancel]').is_hidden()


@pytest.mark.parametrize('reason', ['dirty', 'role', 'input'])
def test_edit_or_character_change_never_applies_late_install(page, reason):
    state = network(page); mounted(page)
    page.locator('[data-workshop=start]').click(); page.wait_for_function('testState.opened.length === 1')
    if reason == 'dirty': page.evaluate('testState.dirty = true')
    elif reason == 'role': page.evaluate('testState.character = "Other"')
    else: page.locator('#editor').fill('changed and then restored'); page.locator('#editor').fill('')
    state['phase'] = 'installed'
    page.wait_for_function('document.querySelector("[data-workshop=cancel]").hidden')
    assert state['writes'] == ['', '/' + REQUEST + '/cancel']
    assert page.evaluate('testState.applied') == []


def test_dirty_page_does_not_create_launch(page):
    state = network(page); mounted(page); page.evaluate('testState.dirty = true')
    page.locator('[data-workshop=start]').click()
    page.wait_for_function('document.querySelector("[data-workshop=status]").textContent.includes("edits")')
    assert state['writes'] == [] and page.evaluate('testState.opened') == []


def test_edit_cancellation_racing_save_reports_actual_saved_result(page):
    state = network(page); texts = mounted(page)
    page.locator('[data-workshop=start]').click(); page.wait_for_function('testState.opened.length === 1')
    page.locator('#editor').fill('keep this edit')
    state['saved_on_cancel'] = True; state['phase'] = 'installed'
    page.wait_for_function('testState.applied.length === 1')
    assert page.locator('[data-workshop=status]').text_content() == texts['connectedSaved']
    assert page.locator('#editor').input_value() == 'keep this edit'
    assert page.evaluate('testState.clean') == [False]
    assert state['writes'] == ['', '/' + REQUEST + '/cancel']


def test_missing_task_never_relaunches_or_applies(page):
    state = network(page); state['deny'] = True
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch",id)', REQUEST)
    mounted(page)
    page.wait_for_function('!document.querySelector("[data-workshop=retry]").hidden')
    assert state['writes'] == []
    page.locator('[data-workshop=retry]').click()
    page.wait_for_timeout(100)
    assert state['writes'] == []


def test_saved_handle_recovery_notifies_once_and_never_saves_again(page):
    state = network(page, 'saved')
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch",id)', REQUEST)
    mounted(page); page.wait_for_function('testState.applied.length === 1')
    assert not state['writes']


def test_reload_cannot_renew_permission_invalidated_by_page_edit(page):
    state = network(page, 'installed')
    page.evaluate('id => sessionStorage.setItem("neko.modelWorkshop.launch", "blocked:" + id)', REQUEST)
    mounted(page)
    page.wait_for_function('document.querySelector("[data-workshop=cancel]").hidden')
    assert state['writes'] == ['/' + REQUEST + '/cancel']
    assert page.evaluate('testState.applied') == []


def test_light_dark_narrow_panel_preserves_manager_theme(page, tmp_path):
    template = (ROOT / 'templates/model_manager.html').read_text()
    rendered = Environment().from_string(template).render(model_workshop_enabled=True, **_vrm_defaults_ctx())
    # Full production layout and assets, without GPU/startup or provider calls.
    page.set_content(re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S))
    network(page); mounted(page, 'zh-CN')
    for theme in ['light', 'dark']:
        page.set_viewport_size({'width':390,'height':844})
        page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        assert page.locator('#model-workshop-panel').bounding_box()['width'] <= 390
        page.screenshot(path=str(tmp_path / ('connected-' + theme + '.png')))
