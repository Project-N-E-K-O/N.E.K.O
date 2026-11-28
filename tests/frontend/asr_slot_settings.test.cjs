const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/asr_slot_settings.js'), 'utf8');
const MASK = '__NEKO_SECRET_MASKED__';

const VENDORS = [
    { key: 'openai', default_url: 'https://api.openai.com/v1/audio/transcriptions', default_model: 'gpt-transcribe', url_editable: false, key_source: 'key_book', transports: ['request'] },
    { key: 'qwen', default_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', default_model: 'qwen3-asr-flash', url_editable: false, key_source: 'key_book', transports: ['request'] },
    { key: 'custom', default_url: '', default_model: '', url_editable: true, key_source: 'slot', transports: ['request'] },
];

function element(extra = {}) {
    return {
        value: '', textContent: '', hidden: false, disabled: false, readOnly: false, placeholder: '',
        dataset: {}, style: {}, listeners: {},
        addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
        ...extra,
    };
}

function selectElement() {
    return element({
        options: [],
        appendChild(option) { this.options.push(option); return option; },
        replaceChildren() { this.options = []; },
    });
}

function setup({ restricted = [], book = {}, fetchResult = { success: true } } = {}) {
    const elements = {
        asrModelProvider: selectElement(),
        asrModelUrl: element(), asrModelId: element(), asrModelApiKey: element(),
        'asr-endpoint-fields': element(), 'asr-slot-hint': element(), 'asr-slot-status': element({ hidden: true }),
        asrSlotTestBtn: element(), asrSlotTestResult: element(),
    };
    const calls = { keyBookLinks: [], removedLinks: 0, fetches: [] };
    const context = vm.createContext({
        URL, AbortController, setTimeout, clearTimeout, JSON, Array, Object, Number, String,
        window: { addEventListener() {} },
        document: {
            readyState: 'complete',
            addEventListener() {},
            getElementById: id => elements[id] || null,
            createElement: () => ({}),
        },
        MASKED_SECRET_SENTINEL: MASK,
        _assistApiProviders: { openai: { name: 'OpenAI' }, qwen: { name: 'Qwen' } },
        appendModelProviderOption: (select, value) => select.appendChild({ value, textContent: value }),
        isProviderRestricted: key => restricted.includes(key),
        setSecretInputValue: (id, value) => { elements[id].value = value; },
        getRealKey: input => input.value,
        syncProviderSelectDropdowns: () => {},
        ensureKeyBookLink: (input, vendor) => calls.keyBookLinks.push(vendor),
        removeKeyBookLink: () => { calls.removedLinks += 1; },
        getEffectiveAssistKey: vendor => book[vendor] || '',
        buildCustomModelListRequest: (url, apiKey, modelType) => ({ body: { url, api_key: apiKey, model_type: modelType } }),
        buildProviderModelListRequest: (provider, apiKey) => ({ body: { provider_key: provider, api_key: apiKey } }),
        fetch: async (url, init) => {
            calls.fetches.push({ url, body: JSON.parse(init.body) });
            return { ok: true, json: async () => fetchResult };
        },
    });
    vm.runInContext(source, context);
    context.populateAsrSlotVendors(VENDORS);
    return { elements, context, calls };
}

const plain = value => JSON.parse(JSON.stringify(value));
const choose = (env, vendor) => {
    env.elements.asrModelProvider.value = vendor;
    env.context.onAsrSlotProviderChange();
};

test('dropdown offers follow, the served vendors in order, and hides restricted ones', () => {
    const { elements } = setup({ restricted: ['openai'] });
    assert.deepEqual(elements.asrModelProvider.options.map(o => o.value), ['follow_core', 'qwen', 'custom']);
});

test('a saved restricted or unknown choice is kept but not offered', () => {
    const env = setup({ restricted: ['openai'] });
    env.context.loadAsrSlotSettings({ asrModelProvider: 'openai', asrModelId: 'gpt-transcribe' });
    assert.equal(env.context.asrSlotSettingsPayload().asrModelProvider, 'openai');
    assert.equal(env.elements.asrModelProvider.options.find(o => o.value === 'openai').disabled, true);
    env.context.loadAsrSlotSettings({ asrModelProvider: 'future', asrModelUrl: ' https://x.example ', asrModelTransport: 'stream' });
    env.context.populateAsrSlotVendors(VENDORS);
    assert.deepEqual(plain(env.context.asrSlotSettingsPayload()), {
        asrModelProvider: 'future', asrModelTransport: 'stream',
        asrModelUrl: ' https://x.example ', asrModelId: '', asrModelApiKey: '',
    });
});

test('a vendor uses its fixed endpoint, default model and Key Book key', () => {
    const env = setup();
    env.elements.asrModelApiKey.value = 'typed-for-custom';
    choose(env, 'qwen');
    const { elements, context, calls } = env;
    assert.equal(elements.asrModelUrl.readOnly, true);
    assert.equal(elements.asrModelApiKey.disabled, true);
    assert.deepEqual(calls.keyBookLinks, ['qwen']);
    assert.deepEqual(plain(context.asrSlotSettingsPayload()), {
        asrModelProvider: 'qwen', asrModelTransport: 'request',
        asrModelUrl: VENDORS[1].default_url, asrModelId: 'qwen3-asr-flash', asrModelApiKey: '',
    });
    assert.equal(elements['asr-endpoint-fields'].style.display, '');
});

test('follow hides the fields and saves them empty', () => {
    const env = setup();
    env.context.loadAsrSlotSettings({ asrModelProvider: 'custom', asrModelUrl: 'http://lan:8081', asrModelApiKey: MASK, asrModelTransport: 'request' });
    choose(env, 'follow_core');
    assert.equal(env.elements['asr-endpoint-fields'].style.display, 'none');
    assert.deepEqual(plain(env.context.asrSlotSettingsPayload()), {
        asrModelProvider: 'follow_core', asrModelTransport: 'request',
        asrModelUrl: '', asrModelId: '', asrModelApiKey: '',
    });
    env.context.loadAsrSlotSettings({ asrModelProvider: 'custom' });
    env.context.resetAsrSlotSettings();
    assert.equal(env.context.asrSlotSettingsPayload().asrModelProvider, 'follow_core');
});

test('a custom endpoint keeps its masked key only at the saved address', () => {
    const env = setup();
    env.context.loadAsrSlotSettings({ asrModelProvider: 'custom', asrModelUrl: 'http://lan-box:8081/v1', asrModelId: 'gigaam-v3', asrModelApiKey: MASK });
    assert.equal(env.elements.asrModelUrl.readOnly, false);
    assert.equal(env.elements.asrModelApiKey.disabled, false);
    assert.equal(env.context.asrSlotSettingsPayload().asrModelApiKey, MASK);
    assert.equal(env.context.validateAsrSlotSettings(), null);
    // Same endpoint, other spelling: still the saved key.
    env.elements.asrModelUrl.value = 'http://LAN-BOX:8081/v1/audio/transcriptions/';
    assert.equal(env.context.validateAsrSlotSettings(), null);
    env.elements.asrModelUrl.value = 'http://other-box:8081/v1';
    assert.match(env.context.validateAsrSlotSettings(), /Key/);
    env.elements.asrModelApiKey.value = 'new-key';
    assert.equal(env.context.validateAsrSlotSettings(), null);
    env.elements.asrModelUrl.value = 'lan-box:8081';
    assert.match(env.context.validateAsrSlotSettings(), /http/);
});

test('model picker asks the server for ASR-tagged models', () => {
    const env = setup({ book: { openai: 'book-key' } });
    assert.equal(env.context.resolveAsrModelPickerRequest().reason, 'follow');
    choose(env, 'openai');
    assert.deepEqual(plain(env.context.resolveAsrModelPickerRequest()), {
        body: { provider_key: 'openai', api_key: 'book-key', model_type: 'asr' },
    });
    choose(env, 'custom');
    env.elements.asrModelUrl.value = ' http://lan:8081 ';
    env.elements.asrModelApiKey.value = 'k';
    assert.deepEqual(plain(env.context.resolveAsrModelPickerRequest()), {
        body: { url: 'http://lan:8081', api_key: 'k', model_type: 'asr' },
    });
});

test('saved availability shows a warning until the slot is edited', () => {
    const env = setup();
    env.context.loadAsrSlotSettings({ asrModelProvider: 'qwen', asrSlotActive: true, asrSlotProvider: 'qwen', asrSlotAvailability: 'missing_credentials' });
    const box = env.elements['asr-slot-status'];
    assert.equal(box.hidden, false);
    assert.match(box.textContent, /Qwen/);
    choose(env, 'openai');
    assert.equal(box.hidden, true);
    env.context.loadAsrSlotSettings({ asrModelProvider: 'qwen', asrSlotActive: true, asrSlotAvailability: 'implemented' });
    assert.equal(box.hidden, true);
});

test('test button sends the vendor Key Book key or the custom endpoint', async () => {
    const env = setup({ book: { qwen: 'book-key' }, fetchResult: { success: false, error_code: 'auth_failed' } });
    choose(env, 'qwen');
    await env.context.testAsrSlotConnectivity();
    assert.deepEqual(env.calls.fetches.at(-1), {
        url: '/api/config/test_asr_connectivity',
        body: { provider_key: 'qwen', url: '', api_key: 'book-key', model: 'qwen3-asr-flash' },
    });
    assert.equal(env.elements.asrSlotTestResult.dataset.state, 'failed');
    assert.match(env.elements.asrSlotTestResult.textContent, /API Key/);

    choose(env, 'custom');
    env.elements.asrModelUrl.value = 'no-scheme:8081';
    await env.context.testAsrSlotConnectivity();
    assert.equal(env.calls.fetches.length, 1, 'an unusable URL is not sent');
    env.elements.asrModelUrl.value = 'http://lan:8081';
    env.elements.asrModelId.value = 'gigaam-v3';
    await env.context.testAsrSlotConnectivity();
    assert.deepEqual(env.calls.fetches.at(-1).body, { provider_key: 'custom', url: 'http://lan:8081', api_key: '', model: 'gigaam-v3' });
});

test('a test result that arrives after the slot changed is dropped', async () => {
    const env = setup({ fetchResult: { success: true } });
    choose(env, 'openai');
    const pending = env.context.testAsrSlotConnectivity();
    choose(env, 'qwen');
    await pending;
    assert.equal(env.elements.asrSlotTestResult.textContent, '');
});
