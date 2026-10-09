const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');
const popup = fs.readFileSync(path.join(root, 'static/avatar/avatar-ui-popup.js'), 'utf8');
const controls = popup.slice(popup.indexOf('function topicRecommendationText('), popup.indexOf('function createSettingsPopupContent('));
const characterId = 'character_' + 'a'.repeat(32);
const secondId = 'character_' + 'b'.repeat(32);
const firstRootGeneration = 'c'.repeat(32);
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function element() {
    return {
        style: {}, attrs: {}, children: [], listeners: {}, isConnected: true,
        setAttribute(key, value) { this.attrs[key] = value; },
        appendChild(child) { this.children.push(child); },
        addEventListener(name, fn) { this.listeners[name] = fn; },
        click() { return this.listeners.click({ stopPropagation() {} }); }
    };
}
function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
function harness(options = {}) {
    const calls = [];
    const panel = element(); panel.style.display = 'flex';
    const state = { proactiveChatEnabled: true, proactiveTopicRecommendationEnabled: true, _switchAttemptCounter: 0 };
    let id = characterId;
    let rootGeneration = firstRootGeneration;
    let availability = options.availability || 'ready';
    let confirms = 0;
    const sandbox = {
        window: { appState: state, lanlan_config: { lanlan_name: 'Yui' },
            crypto: { randomUUID: () => '22222222-2222-4222-8222-222222222222' },
            confirm() { confirms += 1; return options.confirm !== false; },
            nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'token' }), refreshToken: async () => {} },
            t: key => key },
        document: { createElement: element, querySelectorAll: () => [panel] },
        AbortController, setTimeout, clearTimeout,
        async fetch(url, request) {
            calls.push({ url, request });
            if (options.fetch) { const custom = await options.fetch(url, request, calls); if (custom) return custom; }
            if (url.startsWith('/api/characters')) return response({ '猫娘': { Yui: { _reserved: { character_id: id } } } });
            if (url.includes('/status?')) return response({ success: true, character_id: id, availability,
                reset_generation: rootGeneration, reset_confirmation: 'e'.repeat(64),
                epoch: 'epoch-a', revision: 1, capability_enabled: availability !== 'capability_disabled', controls_enabled: availability !== 'user_disabled' });
            const body = JSON.parse(request.body);
            if (body.expected_reset_generation !== rootGeneration) return response({ success: false, error_code: 'stale_operation' }, 409);
            return response({ success: true, character_id: body.character_id, reset_generation: rootGeneration, reset_confirmation: 'e'.repeat(64),
                epoch: 'epoch-b', revision: 2, request_id: body.request_id });
        }
    };
    vm.createContext(sandbox); vm.runInContext(controls, sandbox);
    const ui = sandbox.attachTopicRecommendationControls(panel, 'test');
    return { sandbox, panel, state, ui, calls, setId(value) { id = value; }, setRootGeneration(value) { rootGeneration = value; },
        setAvailability(value) { availability = value; }, get confirms() { return confirms; } };
}

test('accepted cross-window intents and unchanged confirmations both reread authoritative status', async () => {
    const h = harness();
    await h.panel._refreshRecommendationStatus();
    const source = fs.readFileSync(path.join(root, 'static/app/app-settings.js'), 'utf8');
    const apply = source.slice(source.indexOf('    function applySharedRuntimeSettings('), source.indexOf('    function isManualScreenShareActive('));
    vm.runInContext('var S=window.appState; var _SHARED_SETTINGS_KEYS=["proactiveChatEnabled","proactiveTopicRecommendationEnabled"];' + apply, h.sandbox);
    h.sandbox.applySharedRuntimeSettings({ proactiveTopicRecommendationEnabled: false });
    await new Promise(r => setImmediate(r));
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.saveFailed');
    assert.equal(h.calls.filter(c => c.url.includes('/status?')).length, 2);
    h.setAvailability('user_disabled');
    h.sandbox.applySharedRuntimeSettings({ proactiveTopicRecommendationEnabled: false });
    await new Promise(r => setImmediate(r));
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.user_disabled');
    assert.equal(h.calls.filter(c => c.url.includes('/status?')).length, 3);
    assert.equal(h.calls.some(c => c.request && c.request.method === 'POST'), false);
});

test('backend unavailable codes have actionable status-read errors and no reset suggestion', async () => {
    for (const [code, status] of [['store_unavailable', 'degraded'], ['service_unavailable', 'degraded'], ['closing_timeout', 'maintenance']]) {
        const h = harness({ fetch: async url => url.includes('/status?') ? response({ error_code: code }, 503) : null });
        await h.panel._refreshRecommendationStatus();
        assert.equal(h.ui.status.attrs['data-i18n'], `settings.recommendation.${status}`);
        assert.equal(h.calls.some(c => c.request && c.request.method === 'POST'), false);
    }
    const h = harness({ fetch: async url => { if (url.includes('/status?')) throw new Error('read failed'); } });
    await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.degraded');
});

test('a settings confirmation received during a reset is reread when its owner finishes', async () => {
    const gate = deferred();
    const h = harness({ fetch: async (url, request) => {
        if (url.endsWith('/reset')) {
            await gate.promise;
            return response({ success: true, character_id: characterId, reset_generation: firstRootGeneration,
                epoch: 'epoch-b', request_id: JSON.parse(request.body).request_id });
        }
    } });
    const reset = h.ui.reset.click();
    await new Promise(r => setImmediate(r));
    h.state.proactiveTopicRecommendationEnabled = false;
    h.setAvailability('user_disabled');
    await h.panel._refreshRecommendationStatus();
    gate.resolve();
    await reset;
    await new Promise(r => setImmediate(r));
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.user_disabled');
    assert.equal(h.calls.filter(c => c.url.endsWith('/reset')).length, 1);
});

test('status exposes actual capability and server controls, never equates a checkbox with readiness', async () => {
    const h = harness({ availability: 'capability_disabled' });
    await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.capability_disabled');
    h.setAvailability('user_disabled'); await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.saveFailed');
    h.state.proactiveTopicRecommendationEnabled = false;
    await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.user_disabled');
    assert.equal(h.calls.filter(c => c.request && c.request.method === 'POST').length, 0);
});

test('missing confirmation proof cannot authorize a reset', async () => {
    const h = harness({ fetch: async url => url.includes('/status?') ? response({ success: true,
        character_id: characterId, reset_generation: firstRootGeneration, epoch: 'epoch-a',
        revision: 1, availability: 'ready' }) : null });
    await h.ui.reset.click();
    assert.equal(h.confirms, 0);
    assert.equal(h.calls.some(c => c.url.endsWith('/reset')), false);
});

test('new profile proof requires a fresh confirmation after a conflict', async () => {
    let statusCalls = 0, mutations = 0;
    const h = harness({ fetch: async (url, request) => {
        if (url.includes('/status?')) return response({ success: true, character_id: characterId,
            reset_generation: firstRootGeneration, reset_confirmation: (statusCalls++ ? 'f' : 'e').repeat(64),
            epoch: 'epoch-a', revision: statusCalls, availability: 'ready' });
        if (url.endsWith('/reset')) {
            if (mutations++ === 0) return response({ success: false, error_code: 'stale_operation' }, 409);
            assert.equal(JSON.parse(request.body).expected_confirmation, 'f'.repeat(64));
        }
    } });
    await h.ui.reset.click();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.conflict');
    await h.ui.reset.click();
    assert.equal(h.confirms, 2);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.resetDone');
});

test('reset sends real ID, epoch, CSRF and idempotency key only after confirmation', async () => {
    const h = harness();
    await h.ui.reset.click();
    const sent = h.calls.find(c => c.url.endsWith('/reset'));
    assert.equal(h.confirms, 1);
    assert.deepEqual(JSON.parse(sent.request.body), { character_id: characterId, expected_epoch: 'epoch-a',
        expected_reset_generation: firstRootGeneration, expected_confirmation: 'e'.repeat(64), request_id: '22222222-2222-4222-8222-222222222222' });
    assert.equal(sent.request.headers['X-CSRF-Token'], 'token');
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.resetDone');
    assert.equal(h.ui.reset.disabled, false);
    assert.equal(h.state.proactiveTopicRecommendationEnabled, true);
    const cancelled = harness({ confirm: false }); await cancelled.ui.reset.click();
    assert.equal(cancelled.calls.some(c => c.url.endsWith('/reset')), false);
});

test('recovery confirms a separate non-destructive operation and retries the same intent', async () => {
    let attempts = 0;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/recover') && attempts++ === 0) throw new Error('uncertain recovery');
    } });
    await h.ui.recover.click();
    await h.ui.recover.click();
    const requests = h.calls.filter(c => c.url.endsWith('/recover'));
    assert.equal(requests.length, 2);
    assert.equal(requests[0].request.body, requests[1].request.body);
    assert.equal(h.confirms, 1);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.recoverDone');
    assert.equal(h.calls.some(c => c.url.endsWith('/reset')), false);
    assert.equal(h.ui.recover.disabled, false);
    assert.equal(h.state.proactiveTopicRecommendationEnabled, true);
    const cancelled = harness({ confirm: false });
    await cancelled.ui.recover.click();
    assert.equal(cancelled.calls.some(c => c.url.endsWith('/recover')), false);
});

test('uncertain reset retries identical payload and does not ask to clear newer data again', async () => {
    let attempts = 0;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/reset') && attempts++ === 0) throw new Error('network disconnected after commit');
    } });
    await h.ui.reset.click();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.requestFailed');
    await h.ui.reset.click();
    const requests = h.calls.filter(c => c.url.endsWith('/reset'));
    assert.equal(requests.length, 2);
    assert.equal(requests[0].request.body, requests[1].request.body);
    assert.equal(h.confirms, 1);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.resetDone');
});

test('restart rejects an uncertain old reset and a fresh confirmation binds the new owner', async () => {
    let attempts = 0;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/reset') && attempts++ === 0) throw new Error('lost response');
    } });
    await h.ui.reset.click();
    h.setRootGeneration('d'.repeat(32));
    await h.ui.reset.click();
    let requests = h.calls.filter(c => c.url.endsWith('/reset'));
    assert.equal(requests[0].request.body, requests[1].request.body);
    assert.equal(h.confirms, 1);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.conflict');
    await h.ui.reset.click();
    requests = h.calls.filter(c => c.url.endsWith('/reset'));
    assert.equal(requests.length, 3);
    assert.equal(JSON.parse(requests[2].request.body).expected_reset_generation, 'd'.repeat(32));
    assert.equal(h.confirms, 2);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.resetDone');
});

test('a reset receipt from another root owner cannot report success', async () => {
    const h = harness({ fetch: async (url, request) => {
        if (url.endsWith('/reset')) return response({ success: true, character_id: characterId,
            epoch: 'epoch-b', revision: 2, request_id: JSON.parse(request.body).request_id,
            reset_generation: 'd'.repeat(32) });
    } });
    await h.ui.reset.click();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.conflict');
    assert.equal(h.ui.reset.disabled, false);
    await h.ui.reset.click();
    assert.equal(h.confirms, 2);
});

test('reset timeout releases the button and preserves the same request for retry', async () => {
    const entered = deferred(); const gate = deferred(); let attempt = 0;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/reset') && attempt++ === 0) { entered.resolve(); return gate.promise; }
    } });
    const timers = new Set();
    h.sandbox.setTimeout = callback => { timers.add(callback); return callback; };
    h.sandbox.clearTimeout = callback => timers.delete(callback);
    const first = h.ui.reset.click();
    await entered.promise;
    for (const callback of Array.from(timers)) callback();
    await first;
    assert.equal(h.ui.reset.disabled, false);
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.requestFailed');
    await h.ui.reset.click();
    const sent = h.calls.filter(c => c.url.endsWith('/reset'));
    assert.equal(sent[0].request.body, sent[1].request.body);
    assert.equal(h.confirms, 1);
    const accepted = JSON.parse(sent[0].request.body);
    gate.resolve(response({ success: true, character_id: characterId, reset_generation: firstRootGeneration,
        epoch: 'epoch-b', revision: 2, request_id: accepted.request_id }));
    await new Promise(r => setImmediate(r));
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.resetDone');
});

test('confirmed epoch conflict is shown and requires a new confirmation', async () => {
    let attempts = 0;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/reset') && attempts++ === 0) return response({ success: false, error_code: 'epoch_conflict' }, 409);
    } });
    await h.ui.reset.click();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.conflict');
    await h.ui.reset.click();
    assert.equal(h.confirms, 2);
});

test('role ABA switch during CSRF bootstrap blocks mutation and leaves the new panel untouched', async () => {
    const gate = deferred();
    const entered = deferred();
    const h = harness();
    h.sandbox.window.nekoLocalMutationSecurity.getMutationHeaders = () => { entered.resolve(); return gate.promise; };
    const operation = h.ui.reset.click();
    await entered.promise;
    h.state._switchAttemptCounter += 2; // Yui → another role → Yui.
    h.ui.status.textContent = 'new role status';
    gate.resolve({ 'X-CSRF-Token': 'token' });
    await operation;
    assert.equal(h.calls.some(c => c.url.endsWith('/reset')), false);
    assert.equal(h.ui.status.textContent, 'new role status');
    assert.equal(h.ui.reset.disabled, false);
});

test('same-name role recreation does not reuse uncertain reset identity', async () => {
    let fail = true;
    const h = harness({ fetch: async url => {
        if (url.endsWith('/reset') && fail) { fail = false; throw new Error('lost response'); }
    } });
    await h.ui.reset.click(); h.setId(secondId);
    await h.ui.reset.click();
    const requests = h.calls.filter(c => c.url.endsWith('/reset'));
    assert.equal(JSON.parse(requests[1].request.body).character_id, secondId);
    assert.equal(h.confirms, 2);
});

test('stale status replies and removed panels cannot overwrite a current result', async () => {
    const gate = deferred(); let delay = true;
    const h = harness({ fetch: async url => { if (url.includes('/status?') && delay) { delay = false; return gate.promise; } } });
    const old = h.panel._refreshRecommendationStatus();
    await new Promise(r => setImmediate(r));
    h.setAvailability('waiting_context');
    await h.panel._refreshRecommendationStatus();
    gate.resolve(response({ success: true, character_id: characterId, reset_generation: firstRootGeneration,
        epoch: 'epoch-a', revision: 1, availability: 'ready' }));
    await old;
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.waiting_context');
    h.panel.isConnected = false;
    await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.checking');
});

test('missing role and route failure remain failures rather than old-server capability detection', async () => {
    const missing = harness({ fetch: async url => url.startsWith('/api/characters') ? response({ '猫娘': {} }) : null });
    await missing.ui.reset.click();
    assert.equal(missing.ui.status.attrs['data-i18n'], 'settings.recommendation.roleMissing');
    assert.equal(missing.calls.some(c => c.url.endsWith('/reset')), false);
    const old = harness({ fetch: async url => url.includes('/status?') ? response({ error_code: 'route_not_found' }, 404) : null });
    await old.panel._refreshRecommendationStatus();
    assert.equal(old.ui.status.attrs['data-i18n'], 'settings.recommendation.degraded');
});

test('unloaded state is shown as degraded with unknown counts and cannot submit a made-up epoch', async () => {
    const h = harness({ fetch: async url => url.includes('/status?') ? response({ success: true,
        character_id: characterId, reset_generation: firstRootGeneration, epoch: null, revision: null,
        counts: { subjects: null, interests: null, deliveries: null },
        availability: 'degraded', capability_enabled: true, controls_enabled: true }) : null });
    await h.panel._refreshRecommendationStatus();
    assert.equal(h.ui.status.attrs['data-i18n'], 'settings.recommendation.degraded');
    await h.ui.reset.click();
    assert.equal(h.calls.some(c => c.url.endsWith('/reset')), false);
    assert.equal(h.confirms, 0);
});

test('all locales include identical recommendation states and approved sidebar entry', () => {
    const locales = ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es'];
    const expected = Object.keys(JSON.parse(fs.readFileSync(path.join(root, 'static/locales/en.json'))).settings.recommendation).sort();
    for (const locale of locales) {
        const data = JSON.parse(fs.readFileSync(path.join(root, `static/locales/${locale}.json`)));
        assert.deepEqual(Object.keys(data.settings.recommendation).sort(), expected);
        assert.ok(data.settings.toggles.proactiveTopicRecommendation);
        assert.ok(data.settings.toggles.proactiveTopicRecommendationTooltip);
    }
    const drag = fs.readFileSync(path.join(root, 'static/avatar/avatar-ui-drag.js'), 'utf8');
    assert.ok(drag.indexOf("mode: 'mini_game'") < drag.indexOf("mode: 'topic_recommendation'"));
    assert.ok(popup.indexOf('sidePanel.appendChild(authLink)') < popup.indexOf('attachTopicRecommendationControls(sidePanel, prefix)'));
});

function proactiveHarness(route = '/') {
    const requests = [];
    const state = { proactiveChatEnabled: true, proactiveTopicRecommendationEnabled: true,
        proactiveVisionChatEnabled: false, proactiveNewsChatEnabled: false, proactiveCommunityChatEnabled: false,
        proactiveVideoChatEnabled: false, proactivePersonalChatEnabled: false, proactiveMusicEnabled: false,
        proactiveMemeEnabled: false, proactiveMiniGameInviteEnabled: false, proactiveVisionEnabled: false,
        proactiveChatInterval: 15, isRecording: false };
    const sandbox = {
        window: { appState: state, appConst: {}, location: { pathname: route }, addEventListener() {},
            lanlan_config: { lanlan_name: 'Yui' }, nekoLocalMutationSecurity: { getMutationHeaders: async () => ({}) } },
        document: { body: { classList: { contains: () => false }, getAttribute: () => null }, addEventListener() {}, getElementById: () => null },
        localStorage: { getItem: () => null }, console: { log() {}, warn() {}, error() {} },
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
        navigator: { language: 'zh-CN', userAgent: 'test' }, URL,
        fetch: async (url, options) => { requests.push({ url, body: options && JSON.parse(options.body) }); return response({ success: true, action: 'pass' }); }
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-proactive.js'), 'utf8'), sandbox);
    return { sandbox, state, requests, mod: sandbox.window.appProactive };
}

test('recommendation alone passes existing gates and request assembly on shared web and Electron chat routes', async () => {
    for (const route of ['/', '/chat']) {
        const h = proactiveHarness(route);
        assert.equal(h.mod.hasAnyChatModeEnabled(), true);
        assert.equal(h.mod.canTriggerProactively(), true);
        await h.mod.triggerProactiveChat();
        assert.equal(h.requests.length, 1);
        assert.deepEqual(h.requests[0].body.enabled_modes, ['topic_recommendation']);
        assert.equal(h.requests[0].body.mini_game_invite_enabled, false);
        h.state.proactiveChatEnabled = false;
        assert.equal(h.mod.canTriggerProactively(), false);
        h.state.proactiveChatEnabled = true; h.state.proactiveTopicRecommendationEnabled = false;
        assert.equal(h.mod.hasAnyChatModeEnabled(), false);
        assert.equal(h.mod.canTriggerProactively(), false);
        await h.mod.triggerProactiveChat();
        assert.equal(h.requests.length, 1);
    }
});

test('opting out while mutation token is pending prevents recommendation requests', async () => {
    const h = proactiveHarness();
    const entered = deferred(); const gate = deferred();
    h.sandbox.window.nekoLocalMutationSecurity.getMutationHeaders = () => { entered.resolve(); return gate.promise; };
    const attempt = h.mod.triggerProactiveChat();
    await entered.promise;
    h.state.proactiveTopicRecommendationEnabled = false;
    gate.resolve({});
    assert.equal(await attempt, false);
    assert.equal(h.requests.length, 0);
});

test('voice proactive keeps recommendation mode out of its request', async () => {
    const h = proactiveHarness(); h.state.isRecording = true;
    await h.mod.triggerProactiveChat();
    assert.equal(h.requests.length, 1);
    assert.deepEqual(h.requests[0].body.enabled_modes, []);
    assert.equal(h.requests[0].body.voice_mode, true);
});

test('recommendation preference starts off and uses existing state and conversation serialization', () => {
    const sandbox = { window: {}, navigator: { userAgent: '' } };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-state.js'), 'utf8'), sandbox);
    assert.equal(sandbox.window.appState.proactiveTopicRecommendationEnabled, false);
    sandbox.window.proactiveTopicRecommendationEnabled = true;
    assert.equal(sandbox.window.appState.proactiveTopicRecommendationEnabled, true);
    const settings = fs.readFileSync(path.join(root, 'static/app/app-settings.js'), 'utf8').replace(/\r\n/g, '\n');
    const serializer = settings.slice(settings.indexOf('    function getConversationSettings()'), settings.indexOf('    /**\n     * Record which conversation-settings keys'));
    sandbox.S = sandbox.window.appState;
    sandbox._normalizeIndependentAsrProviderPreference = value => value || 'auto';
    vm.runInContext(serializer, sandbox);
    assert.equal(sandbox.getConversationSettings().proactiveTopicRecommendationEnabled, true);
});
