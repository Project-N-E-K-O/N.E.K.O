"""Exercise the real optional import panel, task API and list adapters in Chromium.

Synthetic native packages prove import and UI contracts, not GPU rendering.
The app and storage roots are isolated; no production feature flag is enabled.
"""

import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment

from main_routers import live2d_router, mmd_router, model_market_router as api, vrm_router
from main_routers.system_router import _shared
from main_routers.pages_router import _vrm_defaults_ctx
from tests.unit.test_model_workshop_install import bundle_path, config as config, service as service
from tests.unit.test_model_workshop_legacy import legacy as legacy

ROOT = Path(__file__).resolve().parents[2]


def adapter_source():
    """Load actual page functions with only the renderer and surrounding page state stubbed."""
    source = (ROOT / "static/js/model_manager/page-controller.js").read_text(encoding="utf-8")
    def block(start, end):
        return source[source.index(start):source.index(end, source.index(start))]
    helpers = (ROOT / "static/js/model_manager/path-request-fullscreen.js").read_text(encoding="utf-8").split("const RequestHelper", 1)[0]
    return helpers + "\n" + "\n".join([
        block("function normalizeLive2DSelectionPath(", "function readRememberedLive2DSelection()"),
        block("function createLive2DModelOption(", "async function reloadSelectedLive2DModelAfterModeSwitch()"),
        block("async function loadLive3DModels(", "// 自动选择默认 Live3D 模型"),
        block("async function refreshWorkshopModels(", "// The UI script is absent in production"),
    ])


@pytest.fixture
def browser_app(legacy, monkeypatch):
    _, cfg, _ = legacy
    monkeypatch.setattr(api, "get_config_manager", lambda: cfg)
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "browser-import-token")
    template = (ROOT / "templates/model_manager.html").read_text(encoding="utf-8")
    disabled = Environment().from_string(template).render(**_vrm_defaults_ctx())
    assert 'id="model-workshop-panel"' not in disabled
    assert 'model_manager/model-workshop.js' not in disabled
    rendered = Environment().from_string(template).render(model_workshop_enabled=True, **_vrm_defaults_ctx())
    panel = re.search(r'<details id="model-workshop-panel".*?</details>', rendered, re.S).group()
    harness = """
        const modelSelect = document.getElementById('model-select');
        const vrmModelSelect = document.getElementById('vrm-model-select');
        const mmdModelSelect = document.getElementById('mmd-model-select');
        const vrmModelSelectBtn = document.createElement('button');
        const modelTypeSelect = document.getElementById('model-type-select');
        let currentModelType = 'live2d', modelDisplaySwitchGeneration = 0;
        let workshopListSequence = 0, userModelSelectionGeneration = 0, savingInProgress = false;
        let availableModels = [], character = 'test', changes = 0;
        const t = (_, fallback) => fallback;
        const showStatus = () => {};
        const updateLive2DModelDropdown = () => {};
        const updateLive2DModelSelectButtonText = () => {};
        const updateVRMModelDropdown = () => {};
        const updateVRMModelSelectButtonText = () => {};
        const getLanlanName = async () => character;
        const RequestHelper = { fetchJson: async url => (await fetch(url)).json() };
        const findLive2DOptionBySelection = selection => findLive2DSelectionMatch(
            Array.from(modelSelect.options), selection, getLive2DOptionSelection);
        const dispatchModelManagerChange = select => { changes++; select.dispatchEvent(new Event('change')); };
        const modelTypeChangeHandler = async event => { currentModelType = event.target.value; modelDisplaySwitchGeneration++; };
    """
    setup = """
        window.testState = { setMode(type) { currentModelType = type; modelDisplaySwitchGeneration++; },
            setCharacter(value) { character = value; }, get changes() { return changes; },
            refresh: refreshWorkshopModels, view: viewWorkshopModel,
            fetcher: RequestHelper, option: createLive2DModelOption };
        window.mountPanel = locale => {
            const translate = (key, values = {}) => Object.entries(values).reduce(
                (text, [name, value]) => text.replace('{{' + name + '}}', value), locale[key]);
            NekoModelWorkshop.mount(document.getElementById('model-workshop-panel'), {
                translate, refresh: refreshWorkshopModels, view: viewWorkshopModel });
        };
    """
    app = FastAPI()
    for router in (api.router, live2d_router.router, vrm_router.router, mmd_router.router):
        app.include_router(router)
    app.add_middleware(api.ModelImportBodyGuard, config_getter=lambda: cfg)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/api/config/page_config")
    def page_config():
        return {"autostart_csrf_token": "browser-import-token"}

    @app.get("/test", response_class=HTMLResponse)
    def page(layout: bool = False):
        if layout:
            # Actual full template and styles, with GPU/page initialization replaced.
            shell = re.sub(r"<script\b[^>]*>.*?</script>", "", rendered, flags=re.S)
            return (shell + '<script src="/static/js/model_manager/model-workshop.js"></script><script>'
                    + harness + adapter_source() + setup + '</script>')
        # The template panel, CSS, script and adapters are real; only the GPU host is absent.
        return ('<!doctype html><link rel="stylesheet" href="/static/css/model_manager.css">'
                '<style>body{overflow:auto;background:#eee}#model-workshop-panel{margin:24px}</style>'
                + panel + '<select id="model-select"></select><select id="vrm-model-select"></select>'
                '<select id="mmd-model-select"></select><select id="model-type-select">'
                '<option>live2d</option><option>live3d</option></select>'
                '<script src="/static/js/model_manager/model-workshop.js"></script><script>'
                + harness + adapter_source() + setup + '</script>')

    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def page(browser_app):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_app + "/test")
        yield page
        browser.close()
        assert not errors, errors


def mount(page, locale="en"):
    translations = json.loads((ROOT / "static/locales" / (locale + ".json")).read_text(encoding="utf-8"))["modelWorkshop"]
    page.evaluate("locale => mountPanel(locale)", translations)
    page.locator("summary").click()
    return translations


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx", "pmd"])
def test_real_package_upload_task_and_explicit_preview(page, tmp_path, kind):
    texts = mount(page)
    if kind != "live2d":
        page.evaluate("testState.setMode('live3d')")
    package, _ = bundle_path(tmp_path, kind)
    requests = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.locator('[data-workshop="file"]').set_input_files(package)
    page.locator('[data-workshop="start"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=display_name]').textContent === 'Native fixture'")
    page.wait_for_function("text => document.querySelector('[data-workshop=status]').textContent === text", arg=texts["completed"])
    assert page.locator('[data-workshop="status"]').text_content() == texts["completed"]
    assert page.evaluate("testState.changes") == 0
    assert page.evaluate("document.getElementById('model-select').selectedIndex") == -1
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") is None
    page.locator('[data-workshop="view"]').click()
    page.wait_for_function("testState.changes === 1")
    assert page.locator('[data-workshop="status"]').text_content() == texts["previewRequested"]
    assert not any('/characters/' in url or 'save' in url for _, url in requests)
    assert sum(method == 'POST' and url.endswith('/imports') for method, url in requests) == 1


@pytest.mark.parametrize("locale", ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'])
def test_translated_component_and_plain_text_attribution(page, locale):
    texts = mount(page, locale)
    task = 'a' * 32
    payload = {"task_id": task, "phase": "completed", "result": {
        "install_id": task, "model_format": "vrm", "url": "/user_vrm/shared/Avatar.vrm", "display_name": '<img src=x onerror="window.pwned=1">',
        "attribution": "<script>window.pwned=1</script>", "license": "<b>license</b>"}}
    page.route('**/api/model-market/imports', lambda route: route.fulfill(status=202, json={"task_id": task}))
    page.route('**/api/model-market/tasks/*', lambda route: route.fulfill(json=payload))
    page.locator('[data-workshop="file"]').set_input_files({"name": "safe.neko-model.zip", "mimeType": "application/zip", "buffer": b"fixture"})
    page.locator('[data-workshop="start"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=result]').hidden === false")
    assert page.locator('summary').text_content() == texts['title']
    assert page.locator('[data-workshop="license"]').text_content() == '<b>license</b>'
    assert page.locator('[data-workshop="result"] img, [data-workshop="result"] script').count() == 0
    assert not page.evaluate('window.pwned || false')


def test_refresh_uses_latest_selection_and_never_dispatches_change(page):
    mount(page)
    value = page.evaluate("""async () => {
        const select = document.getElementById('model-select');
        const rows = ['a','b'].map(name => ({name, path:'/user_live2d/' + name + '/Avatar.model3.json', source:'local', display_name:'same title'}));
        select.replaceChildren(...rows.map(testState.option));
        select.selectedIndex = 0;
        let resolve;
        testState.fetcher.fetchJson = () => new Promise(done => resolve = done);
        const pending = testState.refresh({model_format:'live2d'});
        while (!resolve) await new Promise(done => setTimeout(done, 0));
        select.selectedIndex = 1;
        resolve(rows);
        const response = await pending;
        return {response, path:select.selectedOptions[0].dataset.modelPath, changes:testState.changes};
    }""")
    assert value == {"response": {"status": "refreshed"}, "path": "/user_live2d/b/Avatar.model3.json", "changes": 0}


@pytest.mark.parametrize("change", ["mode", "character", "missing", "malformed", "network"])
def test_refresh_rejects_stale_or_failed_lists_without_mutation(page, change):
    mount(page)
    value = page.evaluate("""async change => {
        const select = document.getElementById('model-select');
        const row = {name:'old',path:'/user_live2d/old/Avatar.model3.json',source:'local'};
        select.replaceChildren(testState.option(row));
        let resolve, reject;
        testState.fetcher.fetchJson = () => new Promise((done, fail) => {resolve=done;reject=fail;});
        const pending = testState.refresh({model_format:'live2d'});
        while (!resolve) await new Promise(done => setTimeout(done, 0));
        if (change === 'mode') { testState.setMode('live3d'); testState.setMode('live2d'); }
        if (change === 'character') testState.setCharacter('other');
        if (change === 'network') reject(new Error('network'));
        else resolve(change === 'malformed' ? [null] : change === 'missing' ? [] : [row]);
        return {response:await pending, html:select.innerHTML, changes:testState.changes};
    }""", change)
    expected = 'view_changed' if change in ('mode', 'character') else 'selection_unavailable' if change == 'missing' else 'refresh_failed'
    assert value['response']['status'] == expected
    assert '/user_live2d/old/Avatar.model3.json' in value['html']
    assert value['changes'] == 0


def test_combined_3d_refresh_is_atomic_and_keeps_hidden_mmd_selection(page):
    mount(page)
    value = page.evaluate("""async () => {
        testState.setMode('live3d');
        const vrm = {filename:'a%20b.vrm',name:'same',path:'/user_vrm/a%20b.vrm',url:'/user_vrm/a%2520b.vrm'};
        const mmd = {filename:'a.pmx',name:'same',path:'/user_mmd/a.pmx'};
        testState.fetcher.fetchJson = async url => ({success:true,models:url.includes('/vrm/')?[vrm]:[mmd]});
        await testState.refresh({model_format:'vrm'});
        const combined=document.getElementById('vrm-model-select'), hidden=document.getElementById('mmd-model-select');
        combined.selectedIndex=1; hidden.selectedIndex=0;
        await testState.refresh({model_format:'mmd'});
        const choice=combined.value, auxiliary=hidden.value, previous=combined.innerHTML;
        testState.fetcher.fetchJson = async url => url.includes('/mmd/')?{success:false}:{success:true,models:[]};
        const response=await testState.refresh({model_format:'vrm'});
        return {choice,auxiliary,response,unchanged:combined.innerHTML===previous,changes:testState.changes};
    }""")
    assert value == {"choice": "/user_mmd/a.pmx", "auxiliary": "/user_mmd/a.pmx", "response": {"status": "refresh_failed"}, "unchanged": True, "changes": 0}


def test_restored_missing_task_does_not_upload_or_claim_deletion(page):
    page.evaluate("sessionStorage.setItem('neko.modelWorkshop.task', 'b'.repeat(32))")
    texts = mount(page)
    page.wait_for_function("document.querySelector('[data-workshop=status]').textContent.includes('no longer available')")
    assert page.locator('[data-workshop="status"]').text_content() == texts['taskMissing']
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") is None


def test_interrupted_upload_is_unknown_and_not_automatically_retried(page):
    texts = mount(page)
    calls = []
    def abort(route):
        calls.append(route.request.url)
        route.abort()
    page.route('**/api/model-market/imports', abort)
    page.locator('[data-workshop="file"]').set_input_files({"name": "safe.neko-model.zip", "mimeType": "application/zip", "buffer": b"fixture"})
    page.locator('[data-workshop="start"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=status]').textContent.includes('Acceptance is unknown')")
    assert page.locator('[data-workshop="status"]').text_content() == texts['uploadUnknown']
    assert len(calls) == 1
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") == 'receiving'


def test_cancel_response_preserves_completed_install_and_stale_poll_cannot_revert(page):
    task = 'c' * 32
    queued = {"task_id": task, "phase": "queued", "result": None}
    complete = {"task_id": task, "phase": "completed", "result": {
        "install_id": task, "model_format": "vrm", "url": "/user_vrm/shared/Avatar.vrm", "display_name": "Finished model",
        "attribution": "Author", "license": "Test only"}}
    page.evaluate("task => sessionStorage.setItem('neko.modelWorkshop.task',task)", task)
    polls = []
    def respond(route):
        if route.request.method == 'POST':
            route.fulfill(json=complete)
        elif not polls:
            polls.append(True)
            route.fulfill(json=queued)
        else:
            # Hold the older GET across cancel's terminal response.
            polls.append(route)
    page.route('**/api/model-market/tasks/**', respond)
    texts = mount(page)
    page.wait_for_function("document.querySelector('[data-workshop=status]').textContent === 'Waiting to install…'")
    page.wait_for_timeout(1100)
    page.locator('[data-workshop="cancel"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=view]').hidden === false")
    assert page.locator('[data-workshop="status"]').text_content() == texts['viewChanged']
    for route in polls[1:]:
        route.fulfill(json=queued)
    page.wait_for_timeout(50)
    assert page.locator('[data-workshop="status"]').text_content() == texts['viewChanged']
    assert page.locator('[data-workshop="cancel"]').is_hidden()
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") is None


def test_committing_task_disables_cancel_and_page_reload_only_resumes_query(page):
    task = 'd' * 32
    mutations = []
    page.evaluate("task => sessionStorage.setItem('neko.modelWorkshop.task',task)", task)
    def respond(route):
        if route.request.method == 'POST':
            mutations.append(route.request.url)
        route.fulfill(json={"task_id": task, "phase": "committing", "result": None})
    page.route('**/api/model-market/tasks/**', respond)
    texts = mount(page)
    page.wait_for_function("document.querySelector('[data-workshop=cancel]').disabled")
    assert page.locator('[data-workshop="status"]').text_content() == texts['committing']
    page.reload()
    mount(page)
    page.wait_for_function("document.querySelector('[data-workshop=cancel]').disabled")
    assert not mutations
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") == task


def test_abort_before_acceptance_never_claims_server_cancellation(page):
    texts = mount(page)
    held = []
    page.route('**/api/model-market/imports', lambda route: held.append(route))
    page.locator('[data-workshop="file"]').set_input_files({"name": "safe.neko-model.zip", "mimeType": "application/zip", "buffer": b"fixture"})
    page.locator('[data-workshop="start"]').click()
    page.wait_for_timeout(100)
    page.locator('[data-workshop="cancel"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=status]').textContent.includes('Acceptance is unknown')")
    assert page.locator('[data-workshop="status"]').text_content() == texts['uploadUnknown']
    assert len(held) == 1
    held[0].abort()


def test_real_csrf_rejection_is_not_retried_and_does_not_create_task(page, legacy):
    _, cfg, service = legacy
    texts = mount(page)
    page.route('**/api/config/page_config', lambda route: route.fulfill(json={"autostart_csrf_token": "wrong-token"}))
    page.locator('[data-workshop="file"]').set_input_files({"name": "safe.neko-model.zip", "mimeType": "application/zip", "buffer": b"fixture"})
    page.locator('[data-workshop="start"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=status]').textContent.includes('authorization unavailable')")
    assert page.locator('[data-workshop="status"]').text_content() == texts['accessDenied']
    assert not list((service.paths.private / "transactions").iterdir())
    assert not list(cfg.live2d_dir.iterdir())
    assert page.evaluate("sessionStorage.getItem('neko.modelWorkshop.task')") is None


def test_refresh_retry_does_not_upload_or_preview_again(page):
    task = 'e' * 32
    payload = {"task_id": task, "phase": "completed", "result": {
        "install_id": task, "model_format": "live2d", "url": "/user_live2d/shared/Avatar.model3.json", "display_name": "Installed",
        "attribution": "Author", "license": "Test only"}}
    page.evaluate("task => sessionStorage.setItem('neko.modelWorkshop.task',task)", task)
    page.evaluate("() => { testState.fetcher.fetchJson = async () => { throw new Error('network'); }; }")
    posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    page.route('**/api/model-market/tasks/*', lambda route: route.fulfill(json=payload))
    texts = mount(page)
    page.wait_for_function("document.querySelector('[data-workshop=retry]').hidden === false")
    assert page.locator('[data-workshop="status"]').text_content() == texts['refreshFailed']
    page.evaluate("() => { testState.fetcher.fetchJson = async () => []; }")
    page.locator('[data-workshop="retry"]').click()
    page.wait_for_function("document.querySelector('[data-workshop=retry]').hidden")
    assert page.locator('[data-workshop="status"]').text_content() == texts['completed']
    assert not posts
    assert page.evaluate('testState.changes') == 0


@pytest.mark.parametrize("kind", ["live2d", "vrm", "mmd"])
def test_preview_resolves_install_identity_despite_same_names_or_url_encoding(page, kind):
    mount(page)
    value = page.evaluate("""async kind => {
        testState.setMode(kind === 'live2d'?'live2d':'live3d');
        const task='f'.repeat(32), other='0'.repeat(32);
        const rows=[other,task].map((id,index) => ({name:'same',filename:"猫 #'%."+kind,
            path:'/user_' + kind + '/' + index + "/猫 #'%." + kind,
            model_workshop:{install_id:id}}));
        testState.fetcher.fetchJson = async url => kind === 'live2d'? rows :
            {success:true,models:url.includes('/'+kind+'/')? rows:[]};
        // The task's encoded URL intentionally differs from the catalog spelling.
        const response=await testState.view({model_format:kind,install_id:task,url:'/user_'+kind+'/1/%E7%8C%AB%20%23%27%25.'+kind});
        const select=document.getElementById(kind==='live2d'?'model-select':'vrm-model-select');
        return {response,id:select.selectedOptions[0]?.dataset.workshopInstallId,changes:testState.changes};
    }""", kind)
    assert value == {"response": {"status": "preview_requested"}, "id": "f" * 32, "changes": 1}


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_full_template_sidebar_contains_panel_and_leaves_legacy_controls(page, locale, tmp_path):
    page.goto(page.url + '?layout=true')
    mount(page, locale)
    panel = page.locator('#model-workshop-panel')
    assert panel.is_visible()
    bounds = panel.bounding_box()
    assert bounds and bounds['width'] <= 270
    assert page.locator('#model-upload').count() == 1
    assert page.locator('#live2d-model-select-btn').is_enabled()
    assert page.evaluate("testState.changes") == 0
    # Keep a local inspection artifact without adding screenshots to source control.
    page.screenshot(path=str(tmp_path / ('workshop-sidebar-' + locale + '.png')))
