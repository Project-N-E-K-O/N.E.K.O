"""MMD configuration regressions; real browser scripts, no GPU or user files."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
EMOTIONS = ["neutral", "happy", "relaxed", "sad", "angry", "surprised", "fear"]
pytestmark = pytest.mark.frontend


def setup_runtime(page: Page):
    page.route("http://neko.test/**", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
    page.goto("http://neko.test/")
    for script in ["mmd-core.js", "mmd-expression.js", "mmd-manager.js"]:
        page.add_script_tag(path=str(ROOT / "static/mmd" / script))
    page.evaluate("""() => {
        window.makeModel = (configName = '花火3.0') => ({configName,
            pmx: {header: {modelName: '花火'}},
            mesh: {geometry: {}, morphTargetDictionary: {'笑い': 0, '瞳小': 1, '悲しい': 2, 'VMD': 3},
                morphTargetInfluences: [0, 0, 0, 0]}});
        window.manager = new MMDManager();
        manager.currentModel = makeModel();
        window.expression = manager.expression;
        expression.autoBlink = false;
        expression.autoReturnToNeutral = false;
        window.mapping = {};
        window.requests = [];
        window.fetch = async (url) => {
            requests.push(String(url));
            return {ok: true, json: async () => ({success: true, mapping})};
        };
    }""")


@pytest.mark.parametrize("url,expected", [
    ("/user_mmd/目录/花火3.0.pmx", "花火3.0"),
    ("http://neko.test/workshop/123/%E8%8A%B1%E7%81%AB3.0.PMX?v=4#preview", "花火3.0"),
    ("/static/mmd/a%2520b%23c%3Fd.pMd", "a%20b#c?d"),
    ("/workshop/456/花火3.0.pmx", "花火3.0"),
    ("/user_mmd/100%_model.pmx", "100%_model"),
    ("/user_mmd/100%_花火.pmx", "100%_花火"),
    ("/user_mmd/100%25_model.pmx", "100%_model"),
])
def test_config_name_is_filename_not_internal_metadata(page: Page, url, expected):
    setup_runtime(page)
    info = page.evaluate("""url => {
        const model = makeModel(); model.url = url;
        return manager.core._buildModelInfo(model);
    }""", url)
    assert info["name"] == "花火"
    assert info.get("configName") == expected


@pytest.mark.parametrize("fallback", [False, True])
def test_manager_loads_config_name_on_normal_and_fallback_paths(page: Page, fallback):
    setup_runtime(page)
    result = page.evaluate("""async fallback => {
        mapping = {happy: ['瞳小']};
        manager.core.loadModel = async url => {
            if (fallback && url !== MMDManager.DEFAULT_MODEL_PATH) throw new Error('fixture load failure');
            manager.currentModel = makeModel(fallback ? 'Miku' : '花火3.0');
            return {name: '内部名', configName: manager.currentModel.configName};
        };
        await manager.loadModel('/user_mmd/花火3.0.pmx');
        expression.setEmotion('happy');
        return {requests, weights: manager.currentModel.mesh.morphTargetInfluences};
    }""", fallback)
    assert result["requests"] == ["/api/model/mmd/emotion_mapping?model=" + ("Miku" if fallback else "%E8%8A%B1%E7%81%AB3.0")]
    assert result["weights"][:2] == [0, 1]


def test_reload_rebuilds_defaults_and_preserves_explicit_empty_and_unmatched(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        mapping = {happy: ['瞳小'], sad: []};
        await expression.loadMoodMap('花火3.0');
        mapping = {happy: []};
        await expression.loadMoodMap('花火3.0');
        const empty = structuredClone(expression.moodMap);
        expression.setEmotion('happy');
        const disabled = [...manager.currentModel.mesh.morphTargetInfluences];
        mapping = {happy: ['not-in-model']};
        await expression.loadMoodMap('花火3.0');
        expression.setEmotion('happy');
        const unmatched = [...manager.currentModel.mesh.morphTargetInfluences];
        mapping = {};
        await expression.loadMoodMap('花火3.0');
        return {empty, disabled, unmatched, restored: expression.moodMap};
    }""")
    assert result["empty"]["happy"] == []
    assert "悲しい" in result["empty"]["sad"]
    assert result["disabled"] == result["unmatched"] == [0, 0, 0, 0]
    assert result["restored"]["happy"][0] == "笑い"


def test_late_load_cannot_replace_new_config_or_disposed_state(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        const pending = [];
        fetch = (url, options) => new Promise(resolve => pending.push({resolve, options}));
        const oldLoad = expression.loadMoodMap('花火3.0');
        const newLoad = expression.loadMoodMap('花火3.0');
        pending[1].resolve({ok: true, json: async () => ({success: true, mapping: {happy: []}})});
        await newLoad;
        pending[0].resolve({ok: true, json: async () => ({success: true, mapping: {happy: ['瞳小']}})});
        await oldLoad;
        const latest = expression.moodMap.happy;
        const lastLoad = expression.loadMoodMap('花火3.0');
        expression.dispose();
        pending[2].resolve({ok: true, json: async () => ({success: true, mapping: {happy: ['disposed']}})});
        await lastLoad;
        return {latest, afterDispose: expression.moodMap.happy,
            aborted: pending.map(p => p.options?.signal?.aborted ?? false)};
    }""")
    assert result["latest"] == []
    assert result["afterDispose"] != ["disposed"]
    assert result["aborted"][0] and result["aborted"][2]


def test_replaced_mapping_clears_only_active_manual_morph(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        mapping = {happy: ['瞳小']};
        await expression.loadMoodMap('花火3.0');
        expression.setEmotion('happy');
        const weights = manager.currentModel.mesh.morphTargetInfluences;
        weights[3] = 0.7;
        mapping = {happy: []};
        await expression.loadMoodMap('花火3.0');
        return {weights, manual: expression.manualExpressionInProgress, timer: expression.neutralReturnTimer};
    }""")
    assert result == {"weights": [0, 0, 0, 0.7], "manual": None, "timer": None}


def setup_editor(page: Page):
    setup_runtime(page)
    page.evaluate("""emotions => {
        document.body.innerHTML = `<select id="model-select"></select>
            <div id="model-singleselect"><div class="singleselect-header"></div>
            <div class="selected-text"></div><div class="singleselect-options"></div></div>
            <div id="emotion-config"></div><button id="save-btn"></button>
            <button id="reset-btn"></button><div id="status-message"></div><div id="preview-buttons"></div>`;
        for (const emotion of emotions) {
            document.getElementById('emotion-config').insertAdjacentHTML('beforeend',
                `<div class="custom-multiselect emotion-morph-select" data-emotion="${emotion}">
                <div class="multiselect-header"><span class="selected-text"></span></div>
                <div class="multiselect-options"></div></div>`);
        }
        // Deliberately includes no custom morph: persisted choices must survive a fallback candidate list.
        window.opener = null;
        mapping = {happy: ['瞳小'], fear: []};
        window.saved = null;
        fetch = async (url, options) => {
            if (String(url).endsWith('/models')) return {ok: true, json: async () => ({success: true,
                models: [{name: '花火3.0'}, {name: 'other'}]})};
            if (options?.method === 'POST') {
                saved = JSON.parse(options.body);
                return {ok: true, json: async () => ({success: true})};
            }
            return {ok: true, json: async () => ({success: true, mapping})};
        };
    }""", EMOTIONS)
    page.add_script_tag(path=str(ROOT / "static/js/mmd_emotion_manager.js"))
    page.locator('.singleselect-item[data-value="花火3.0"]').click()
    page.wait_for_function("document.getElementById('status-message').textContent.includes('配置加载成功')")


def test_editor_retains_legacy_defaults_custom_names_and_explicit_empty(page: Page):
    setup_editor(page)
    page.evaluate("MMDEmotionManager.saveEmotionMapping()")
    saved = page.evaluate("saved")
    assert saved["model"] == "花火3.0"
    assert saved["mapping"]["happy"] == ["瞳小"]
    assert saved["mapping"]["fear"] == []
    assert "悲しい" in saved["mapping"]["sad"]
    assert set(saved["mapping"]) == set(EMOTIONS)


def test_editor_can_save_every_emotion_empty(page: Page):
    setup_editor(page)
    page.locator('#emotion-config input').evaluate_all("inputs => inputs.forEach(cb => cb.checked = false)")
    page.evaluate("MMDEmotionManager.saveEmotionMapping()")
    assert page.evaluate("saved.mapping") == {emotion: [] for emotion in EMOTIONS}


def test_vmd_write_order_is_unchanged(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        mapping = {happy: ['瞳小']};
        await expression.loadMoodMap('花火3.0');
        expression.setEmotion('happy');
        const weights = manager.currentModel.mesh.morphTargetInfluences;
        weights[3] = 0.6; // A different VMD track can coexist.
        expression.update(0.016);
        const different = [...weights];
        weights[1] = 0.2; // The next VMD frame still owns the same morph.
        expression.update(0.016);
        return {different, same: weights};
    }""")
    assert result["different"] == [0, 1, 0, 0.6]
    assert result["same"] == [0, 0.2, 0, 0.6]


@pytest.mark.parametrize("switch_editor", [False, True])
@pytest.mark.parametrize("storage_blocked", [False, True])
def test_save_notifies_matching_runtime_without_opener(page: Page, switch_editor, storage_blocked):
    setup_runtime(page)
    page.evaluate("""() => {
        mapping = {happy: ['瞳小']};
        window.other = new MMDExpression({currentModel: makeModel('other')});
        window.shared = new MMDExpression({currentModel: makeModel('花火3.0')});
    }""")
    with page.context.new_page() as editor:
        setup_editor(editor)
        if storage_blocked:
            editor.evaluate("() => { Storage.prototype.setItem = () => { throw new Error('storage denied'); }; }")
        if switch_editor:
            editor.evaluate("""() => {
                const previousFetch = fetch;
                fetch = (url, options) => options?.method === 'POST'
                    ? new Promise(resolve => window.finishSave = () => resolve({ok: true,
                        json: async () => ({success: true})})) : previousFetch(url, options);
                window.savePromise = MMDEmotionManager.saveEmotionMapping();
            }""")
            editor.locator('.singleselect-item[data-value="other"]').click()
            editor.evaluate("async () => { finishSave(); await savePromise; }")
        else:
            editor.evaluate("MMDEmotionManager.saveEmotionMapping()")
        page.wait_for_function("expression.moodMap.happy[0] === '瞳小' && shared.moodMap.happy[0] === '瞳小'", timeout=5000)
        assert page.evaluate("other.moodMap.happy[0]") == "笑い"
        assert len(page.evaluate("requests")) == 2
        assert editor.evaluate("localStorage.getItem('neko_mmd_emotion_mapping_changed')") is None


@pytest.mark.parametrize("failure", ["http", "json", "network"])
def test_failed_load_uses_fresh_defaults_not_previous_model(page: Page, failure):
    setup_runtime(page)
    result = page.evaluate("""async failure => {
        mapping = {happy: ['瞳小'], sad: []};
        await expression.loadMoodMap('花火3.0');
        fetch = async () => {
            if (failure === 'network') throw new Error('offline');
            return {ok: failure !== 'http', status: 500, json: async () => { throw new Error('bad JSON'); }};
        };
        await expression.loadMoodMap('花火3.0');
        return expression.moodMap;
    }""", failure)
    assert result["happy"][0] == "笑い"
    assert result["sad"][0] == "悲しい"


def test_model_unload_cancels_request_and_expression_timer(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        expression.autoReturnToNeutral = true;
        expression.setEmotion('happy');
        let finish, signal;
        fetch = (url, options) => {
            signal = options.signal;
            return new Promise(resolve => finish = resolve);
        };
        const pending = expression.loadMoodMap('花火3.0');
        delete manager.currentModel.mesh.geometry; // No GPU resources in this fixture.
        manager.core._clearModel();
        manager.currentModel = makeModel('other');
        finish({ok: true, json: async () => ({success: true, mapping: {happy: ['瞳小']}})});
        await pending;
        return {aborted: signal.aborted, timer: expression.neutralReturnTimer,
            weights: expression.currentWeights, happy: expression.moodMap.happy[0],
            influences: manager.currentModel.mesh.morphTargetInfluences};
    }""")
    assert result == {"aborted": True, "timer": None, "weights": {}, "happy": "笑い", "influences": [0, 0, 0, 0]}


def test_request_timeout_and_listener_disposal_are_bounded(page: Page):
    setup_runtime(page)
    page.clock.install()
    page.evaluate("""() => {
        window.removed = false;
        const originalRemove = window.removeEventListener.bind(window);
        window.removeEventListener = (type, handler, ...rest) => {
            if (type === 'storage' && handler === expression._moodMapStorageHandler) removed = true;
            originalRemove(type, handler, ...rest);
        };
        window.aborted = false;
        fetch = (url, {signal}) => new Promise((resolve, reject) => {
            signal.addEventListener('abort', () => { aborted = true; reject(new DOMException('aborted', 'AbortError')); }, {once: true});
        });
        window.pending = expression.loadMoodMap('花火3.0');
    }""")
    page.clock.fast_forward(10001)
    page.evaluate("async () => { await pending; expression.dispose(); }")
    assert page.evaluate("({aborted, removed, request: expression._moodMapRequest})") == {"aborted": True, "removed": True, "request": None}


def test_reload_does_not_restart_unchanged_manual_expression(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        expression.autoReturnToNeutral = true;
        mapping = {happy: ['瞳小']};
        await expression.loadMoodMap('花火3.0');
        expression.setEmotion('happy');
        const timer = expression.neutralReturnTimer;
        await expression.loadMoodMap('花火3.0');
        return {sameTimer: timer === expression.neutralReturnTimer,
            manual: expression.manualExpressionInProgress, weight: expression.getMorphWeight('瞳小')};
    }""")
    assert result == {"sameTimer": True, "manual": "瞳小", "weight": 1}


@pytest.mark.parametrize("failure", ["http", "json", "network", "unsuccessful", "invalid_mapping"])
def test_editor_read_failure_cannot_overwrite_saved_mapping(page: Page, failure):
    setup_editor(page)
    page.evaluate("""failure => {
        window.workingFetch = fetch;
        fetch = async (url, options) => {
            if (options?.method === 'POST') return workingFetch(url, options);
            if (failure === 'network') throw new Error('offline');
            return {ok: failure !== 'http', status: 500, text: async () => 'failed', json: async () => {
                if (failure === 'json') throw new Error('bad JSON');
                return failure === 'unsuccessful' ? {success: false} : {success: true, mapping: []};
            }};
        };
    }""", failure)
    page.locator('.singleselect-item[data-value="other"]').click()
    page.wait_for_function("document.getElementById('status-message').textContent.includes('配置加载失败')")
    assert page.locator('#save-btn').is_disabled()
    page.evaluate("MMDEmotionManager.resetConfig(); MMDEmotionManager.saveEmotionMapping()")
    assert page.evaluate("saved") is None
    page.evaluate("fetch = workingFetch; mapping = {}")
    page.locator('.singleselect-item[data-value="other"]').click()
    page.wait_for_function("!document.getElementById('save-btn').disabled")
    page.evaluate("MMDEmotionManager.saveEmotionMapping()")
    assert page.evaluate("saved.model") == "other"


def test_broadcast_fallback_receiver_is_closed_on_dispose(page: Page):
    setup_runtime(page)
    result = page.evaluate("""() => {
        const channel = expression._moodMapChannel;
        if (!channel) return {created: false};
        let closed = 0;
        const originalClose = channel.close.bind(channel);
        channel.close = () => { closed++; originalClose(); };
        expression.dispose(); expression.dispose();
        return {created: true, closed, released: expression._moodMapChannel === null,
            detached: channel.onmessage === null};
    }""")
    assert result == {"created": True, "closed": 1, "released": True, "detached": True}


@pytest.mark.parametrize("channel_result", ["success", "constructor_error", "post_error"])
def test_broadcast_sender_cleanup_and_opener_fallback(page: Page, channel_result):
    setup_editor(page)
    result = page.evaluate("""async channelResult => {
        let closed = 0, notified = 0;
        window.opener = {closed: false, mmdManager: {currentModel: {configName: '花火3.0'},
            expression: {loadMoodMap: () => { notified++; }}}};
        Storage.prototype.setItem = () => { throw new Error('storage denied'); };
        window.BroadcastChannel = class {
            constructor() { if (channelResult === 'constructor_error') throw new Error('denied'); }
            postMessage() { if (channelResult === 'post_error') throw new Error('send failed'); }
            close() { closed++; }
        };
        await MMDEmotionManager.saveEmotionMapping();
        return {closed, notified, saved: saved.model};
    }""", channel_result)
    assert result == {"closed": int(channel_result != "constructor_error"),
                      "notified": int(channel_result != "success"), "saved": "花火3.0"}


def test_blocked_broadcast_receiver_keeps_storage_support(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        window.BroadcastChannel = class { constructor() { throw new Error('denied'); } };
        const receiver = new MMDExpression(manager);
        mapping = {happy: ['瞳小']};
        window.dispatchEvent(new StorageEvent('storage', {key: 'neko_mmd_emotion_mapping_changed',
            newValue: JSON.stringify({model: '花火3.0'})}));
        await new Promise(resolve => setTimeout(resolve, 0));
        const happy = receiver.moodMap.happy;
        receiver.dispose();
        return {happy, channel: receiver._moodMapChannel};
    }""")
    assert result == {"happy": ["瞳小"], "channel": None}


@pytest.mark.parametrize("raw,expected", [
    # #3379: raw names from config / model lists; '#', '?', '%' must not become URL syntax.
    ("/user_mmd/a#b/a#b.pmx", "/user_mmd/a%23b/a%23b.pmx"),
    ("/user_mmd/50%.pmx", "/user_mmd/50%25.pmx"),
    ("/user_mmd/what?.pmx", "/user_mmd/what%3F.pmx"),
    ("/static/mmd/空 格/花火.pmx", "/static/mmd/%E7%A9%BA%20%E6%A0%BC/%E8%8A%B1%E7%81%AB.pmx"),
    ("/workshop/123/Folder#20%/m.PMD", "/workshop/123/Folder%2320%25/m.PMD"),
    # Already-encoded input (page config, earlier URLs) is not encoded twice.
    ("/user_mmd/a%23b/a%23b.pmx", "/user_mmd/a%23b/a%23b.pmx"),
    ("/static/mmd/%E8%8A%B1%E7%81%AB.pmx", "/static/mmd/%E8%8A%B1%E7%81%AB.pmx"),
    # Not a local model path: left alone.
    ("http://neko.test/user_mmd/a#b.pmx", "http://neko.test/user_mmd/a#b.pmx"),
    ("relative/a#b.pmx", "relative/a#b.pmx"),
])
def test_request_url_escapes_raw_local_names_once(page: Page, raw, expected):
    setup_runtime(page)
    assert page.evaluate("raw => MMDCore.toRequestUrl(raw)", raw) == expected


def test_request_url_honours_configured_prefixes(page: Page):
    setup_runtime(page)
    result = page.evaluate("""() => {
        window.MMD_PATHS = {user_mmd: '/custom_mmd', static_mmd: '/static/mmd'};
        return MMDCore.toRequestUrl('/custom_mmd/a#b.pmx');
    }""")
    assert result == "/custom_mmd/a%23b.pmx"


@pytest.mark.parametrize("raw", ["/user_mmd/a#b/a#b.pmx", "/user_mmd/50%.pmx", "/user_mmd/空 格 %41.pmx",
                                 "/workshop/123/x?y.pmx"])
def test_browser_requests_the_exact_raw_file(page: Page, raw):
    """The server must see the same path segments the model list reported."""
    setup_runtime(page)
    seen = []

    def fulfill(route):
        seen.append(route.request.url)
        route.fulfill(body="pmx")

    page.route("http://neko.test/user_mmd/**", fulfill)
    page.route("http://neko.test/workshop/**", fulfill)
    # setup_runtime stubs fetch, so issue a real request through XHR.
    body = page.evaluate("""raw => new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', MMDCore.toRequestUrl(raw));
        xhr.onload = () => resolve(xhr.responseText);
        xhr.onerror = reject;
        xhr.send();
    })""", raw)
    assert body == "pmx"
    path = urlsplit(seen[0]).path
    # %41 in a raw name is read as an escape (shared rule with getConfigName), so it decodes to A.
    assert unquote(path) == raw.replace("%41", "A")
    assert not urlsplit(seen[0]).query and not urlsplit(seen[0]).fragment


@pytest.mark.parametrize("raw,expected", [
    ("/user_mmd/a#b/x#1.pmx", "x#1"),
    ("/user_mmd/what?.pmx", "what?"),
    ("/user_mmd/50%.pmx", "50%"),
])
def test_config_name_keeps_raw_url_syntax_characters(page: Page, raw, expected):
    setup_runtime(page)
    assert page.evaluate("raw => MMDCore.getConfigName(raw)", raw) == expected


def test_loader_gets_escaped_url_while_identity_stays_raw(page: Page):
    setup_runtime(page)
    result = page.evaluate("""async () => {
        window.THREE = {LoadingManager: class {}};
        const loaded = [];
        manager.currentModel = null;
        manager.core._getMMDModule = async () => ({MMDLoader: class {
            load(url, onLoad, onProgress, onError) { loaded.push(url); onError(new Error('fixture stop')); }
        }});
        try { await manager.core.loadModel('/user_mmd/a#b/a#b.pmx', {}, manager._activeLoadToken); }
        catch (_) { /* the fixture loader always fails after recording the URL */ }
        return loaded;
    }""")
    assert result == ["/user_mmd/a%23b/a%23b.pmx"]
