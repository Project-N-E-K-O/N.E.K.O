const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SOURCE_PATH = path.join(PROJECT_ROOT, 'static', 'js', 'api_key_settings.js');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');

const MASKED = '__NEKO_SECRET_MASKED__';
const MODEL_TYPES = ['conversation', 'vision', 'summary', 'correction', 'emotion', 'omni', 'agent', 'tts', 'gameMain', 'gameSummary'];

function sourceBetween(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

const ASSIST_PROVIDERS = {
    free: {
        is_free_version: true,
        conversation_model: 'free-model',
        summary_model: 'free-model',
        correction_model: 'free-model',
        emotion_model: 'free-mini-model',
        vision_model: 'free-vision-model',
        agent_model: 'free-agent-model',
    },
    openrouter: {
        conversation_model: 'google/gemini-2.5-flash',
        vision_model: 'google/gemini-2.5-flash',
        summary_model: 'deepseek/deepseek-v4-flash',
        correction_model: 'deepseek/deepseek-v4-flash',
        emotion_model: 'qwen/qwen3.5-9b',
        agent_model: 'google/gemini-3-flash-preview',
    },
    qwen: {
        conversation_model: 'qwen3.8-flash',
        vision_model: 'qwen3.8-flash',
        summary_model: 'qwen3.8-flash',
        correction_model: 'qwen3.8-flash',
        emotion_model: 'qwen3.7-flash',
        agent_model: 'qwen3.8-flash',
    },
    kimi: {
        conversation_model: 'kimi-k2.6',
        vision_model: 'kimi-k2.6',
        summary_model: 'kimi-k2.6',
        correction_model: 'kimi-k2.6',
        emotion_model: 'kimi-k2.6',
        agent_model: '',
    },
    kimi_code: {
        fixed_model: true,
        conversation_model: 'kimi-for-coding',
        vision_model: 'kimi-for-coding',
        summary_model: 'kimi-for-coding',
        correction_model: 'kimi-for-coding',
        emotion_model: 'kimi-for-coding',
        agent_model: 'kimi-for-coding',
    },
    mimo: { conversation_model: 'mimo-v2.5' },
    openai: { conversation_model: 'gpt-5.6-luna' },
};
const CORE_PROVIDERS = {
    free: { is_free_version: true, core_model: 'free-model' },
    qwen: { core_model: 'qwen3.8-omni-flash-realtime' },
    step: { core_model: 'stepaudio-3-realtime-preview' },
};
Object.entries(ASSIST_PROVIDERS).forEach(([key, profile]) => {
    profile.openrouter_url = `https://${key}.example.test/v1`;
});
const IMAGE_PROVIDERS = {
    openai: { protocol: 'openai', model: 'gpt-image-2' },
    qwen: { protocol: 'dashscope', model: 'wanx2.1-t2i-turbo' },
    custom: { protocol: 'openai', model: '' },
};

function createElement(id, attributes = {}) {
    return {
        id,
        value: '',
        placeholder: attributes.placeholder || '',
        disabled: false,
        textContent: '',
        dataset: {},
        attributes: { ...attributes },
        listeners: {},
        addEventListener(type, callback) {
            (this.listeners[type] ||= []).push(callback);
        },
        dispatchEvent(event) {
            (this.listeners[event.type] || []).forEach(callback => callback(event));
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
    };
}

function createPickerContext({ translations = null, fetchImpl = null } = {}) {
    const elements = new Map();
    const add = (id, attributes) => {
        const element = createElement(id, attributes);
        // initModelIdPicker 会在包装输入框时记下原始占位符
        if (attributes && attributes.placeholder) element.dataset.defaultPlaceholder = attributes.placeholder;
        elements.set(id, element);
        return element;
    };
    add('coreApiSelect');
    add('apiKeyInput');
    add('api-key-form');
    add('useMimoTokenPlan');
    add('assistApiSelect');
    add('imageModelProvider');
    add('imageModelUrl');
    add('imageModelApiKey');
    add('imageModelId', { 'data-i18n-placeholder': 'api.imageModelExamplePlaceholder', placeholder: 'e.g., gpt-image-2' });
    MODEL_TYPES.forEach(modelType => {
        add(`${modelType}ModelProvider`);
        add(`${modelType}ModelUrl`);
        add(`${modelType}ModelApiKey`);
        add(`${modelType}ModelId`, { 'data-i18n-placeholder': 'api.modelExamplePlaceholder', placeholder: 'e.g., gpt-3.5-turbo' });
    });

    const state = {
        customKey: '',
        bookKeys: {},
        tokenPlan: false,
        ttsMeta: {},
        fetchCalls: [],
    };
    const context = vm.createContext({
        console,
        URL,
        _isLoadingSavedConfig: false,
        JSON,
        Map,
        Set,
        Array,
        Object,
        String,
        Math,
        Promise,
        Event: class {
            constructor(type) {
                this.type = type;
            }
        },
        AbortSignal: { timeout: () => undefined },
        queueMicrotask,
        window: translations ? { t: (key, options) => translate(translations, key, options) } : {},
        document: {
            listeners: {},
            addEventListener(type, callback) {
                (this.listeners[type] ||= []).push(callback);
            },
            getElementById: id => elements.get(id) || null,
            activeElement: null,
        },
        fetch: (url, init) => {
            state.fetchCalls.push({ url, body: JSON.parse(init.body) });
            return fetchImpl ? fetchImpl(url, init) : Promise.reject(new Error('fetch not stubbed'));
        },
        MODEL_TYPES,
        MASKED_SECRET_SENTINEL: MASKED,
        MODEL_PROVIDER_FIELD_BY_TYPE: {
            conversation: 'conversation_model',
            summary: 'summary_model',
            gameMain: 'conversation_model',
            gameSummary: 'summary_model',
            correction: 'correction_model',
            emotion: 'emotion_model',
            vision: 'vision_model',
            agent: 'agent_model',
            omni: 'core_model',
        },
        _assistApiProviders: ASSIST_PROVIDERS,
        _assistModelDefaults: {},
        _coreApiProviders: CORE_PROVIDERS,
        _keyBookApiProviders: {},
        _imageProviders: IMAGE_PROVIDERS,
        getProviderInfo: key => ASSIST_PROVIDERS[key] || CORE_PROVIDERS[key] || {},
        isProviderFlagEnabled: value => value === true || value === 1 || value === 'true' || value === '1',
        isFixedModelProvider: key => !!(ASSIST_PROVIDERS[key] && ASSIST_PROVIDERS[key].fixed_model),
        isMimoTokenPlanActive: () => state.tokenPlan,
        getMimoTokenPlanUrl: () => 'https://token-plan-cn.xiaomimimo.com/v1',
        getRealKey: input => (input ? (input.dataset.maskedSecret === 'true' ? MASKED : input.value) : ''),
        getEffectiveAssistKey: key => state.bookKeys[key] || '',
        getTtsProviderMeta: key => state.ttsMeta[key] || null,
        ConnectivityManager: {
            resolveEffectiveKey: () => ({ key: state.customKey }),
        },
        closeProviderSelectDropdown: () => {},
        closeAllProviderSelectDropdowns: () => {},
        bindProviderDropdownGlobalHandlers: () => {},
    });
    const exported = [
        'translateModelPickerText', 'getAssistTierModelId', 'resolveSlotModelState', 'applyModelIdPlaceholder',
        'refreshModelIdHints', 'resolveSlotModelPickerRequest', 'resolveImageModelPickerRequest',
        'fetchModelList', 'getModelPickerErrorMessage', 'initModelIdPickers',
    ];
    vm.runInContext([
        sourceBetween('function getProviderDefaultModelId(', 'function setModelIdFieldHidden('),
        sourceBetween('// ==================== 模型 ID 选择器 ====================', '// ==================== 加载API服务商选项 ===================='),
        ...exported.map(name => `globalThis.${name} = ${name};`),
        'globalThis.invalidateModelLists = invalidateModelLists;',
        'globalThis.focusModelIdPickerOption = focusModelIdPickerOption;',
    ].join('\n'), context, { filename: SOURCE_PATH });

    const el = id => elements.get(id);
    const select = (id, value) => {
        el(id).value = value;
    };
    const setSlot = (modelType, provider, modelId = '') => {
        select(`${modelType}ModelProvider`, provider);
        el(`${modelType}ModelId`).value = modelId;
        if (ASSIST_PROVIDERS[provider]) el(`${modelType}ModelUrl`).value = ASSIST_PROVIDERS[provider].openrouter_url;
    };
    MODEL_TYPES.forEach(modelType => select(`${modelType}ModelProvider`, 'follow_assist'));
    select('gameMainModelProvider', 'follow_conversation');
    select('gameSummaryModelProvider', 'follow_summary');
    select('omniModelProvider', 'follow_core');
    select('coreApiSelect', 'qwen');
    select('assistApiSelect', 'openrouter');
    return { context, el, select, setSlot, state };
}

function translate(translations, key, options) {
    const value = translations[key];
    const template = value === undefined
        ? (options && typeof options === 'object' && options.defaultValue !== undefined ? options.defaultValue : key)
        : value;
    if (!options || typeof options !== 'object') return template;
    return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => (name in options ? String(options[name]) : ''));
}

function plain(value) {
    return JSON.parse(JSON.stringify(value));
}

test('follow_assist text slots use the tier default of the selected assist provider', () => {
    const { context, select } = createPickerContext();
    assert.deepEqual(plain(context.resolveSlotModelState('summary')), {
        defaultModelId: 'deepseek/deepseek-v4-flash', acceptsTypedModelId: true, fixedModelProvider: '',
    });
    assert.equal(context.resolveSlotModelState('emotion').defaultModelId, 'qwen/qwen3.5-9b');

    select('assistApiSelect', 'qwen');
    assert.equal(context.resolveSlotModelState('emotion').defaultModelId, 'qwen3.7-flash');
});

test('agent tier falls back to the vision model when the provider has no agent model', () => {
    const { context, select } = createPickerContext();
    select('assistApiSelect', 'kimi');
    assert.equal(context.getAssistTierModelId('agent'), 'kimi-k2.6');
});

test('follow_core text slots stay on the core provider chain and never borrow assist models', () => {
    const { context, setSlot, select } = createPickerContext();
    setSlot('vision', 'follow_core');
    assert.equal(context.resolveSlotModelState('vision').defaultModelId, 'qwen3.8-flash');

    select('coreApiSelect', 'step');
    assert.equal(context.resolveSlotModelState('vision').defaultModelId, 'stepaudio-3-realtime-preview');
});

test('free and fixed-model sources do not accept a typed model ID', () => {
    const { context, setSlot, select } = createPickerContext();
    select('assistApiSelect', 'free');
    assert.deepEqual(plain(context.resolveSlotModelState('conversation')), {
        defaultModelId: 'free-model', acceptsTypedModelId: false, fixedModelProvider: 'free',
    });

    select('assistApiSelect', 'openrouter');
    setSlot('correction', 'kimi_code', 'typed');
    assert.deepEqual(plain(context.resolveSlotModelState('correction')), {
        defaultModelId: 'kimi-for-coding', acceptsTypedModelId: false, fixedModelProvider: 'kimi_code',
    });
});

test('named and custom slots fall back the same way as the backend snapshot', () => {
    const { context, setSlot } = createPickerContext();
    setSlot('summary', 'qwen');
    assert.equal(context.resolveSlotModelState('summary').defaultModelId, 'qwen3.8-flash');

    setSlot('agent', 'mimo');
    assert.equal(context.resolveSlotModelState('agent').defaultModelId, 'mimo-v2.5');

    setSlot('conversation', 'custom');
    assert.equal(context.resolveSlotModelState('conversation').defaultModelId, 'google/gemini-2.5-flash');
    setSlot('gameMain', 'custom');
    assert.equal(context.resolveSlotModelState('gameMain').defaultModelId, '');
});

test('named slot placeholders require a matching nonempty provider endpoint', () => {
    const { context, el, setSlot } = createPickerContext();
    setSlot('conversation', 'openai');
    for (const url of ['', 'https://other.example.test/v1', 'https://openai.example.test/V1']) {
        el('conversationModelUrl').value = url;
        assert.equal(context.resolveSlotModelState('conversation').defaultModelId, 'google/gemini-2.5-flash');
    }
    for (const url of ['https://OPENAI.example.test:443/v1/', 'https://openai.example.test/v1']) {
        el('conversationModelUrl').value = url;
        assert.equal(context.resolveSlotModelState('conversation').defaultModelId, 'gpt-5.6-luna');
    }
});

test('API provider changes clear only matching follow slots and preserve loads and same-provider refreshes', () => {
    const { context, setSlot, el } = createPickerContext();
    for (const mode of ['follow_core', 'follow_assist']) {
        for (const type of MODEL_TYPES) setSlot(type, mode, 'old-model');
        setSlot('vision', 'custom', 'custom-model');
        context.clearFollowModelIdsOnApiChange(mode, 'qwen', 'qwen');
        assert.equal(el('conversationModelId').value, 'old-model');
        context._isLoadingSavedConfig = true;
        context.clearFollowModelIdsOnApiChange(mode, 'qwen', 'openai');
        assert.equal(el('conversationModelId').value, 'old-model');
        context._isLoadingSavedConfig = false;
        context.clearFollowModelIdsOnApiChange(mode, 'qwen', 'openai');
        for (const type of MODEL_TYPES.filter(type => !['omni', 'tts', 'vision'].includes(type))) {
            assert.equal(el(`${type}ModelId`).value, '');
        }
        assert.equal(el('omniModelId').value, 'old-model');
        assert.equal(el('ttsModelId').value, 'old-model');
        assert.equal(el('visionModelId').value, 'custom-model');
    }
});

test('game slots mirror or follow the text slots and ignore their own input in follow modes', () => {
    const { context, setSlot } = createPickerContext();
    setSlot('conversation', 'custom', 'my-chat-model');
    assert.deepEqual(plain(context.resolveSlotModelState('gameMain')), {
        defaultModelId: 'my-chat-model', acceptsTypedModelId: false, fixedModelProvider: '',
    });

    setSlot('gameMain', 'follow_assist', 'ignored');
    assert.deepEqual(plain(context.resolveSlotModelState('gameMain')), {
        defaultModelId: 'google/gemini-2.5-flash', acceptsTypedModelId: false, fixedModelProvider: '',
    });

    setSlot('gameSummary', 'follow_core');
    assert.equal(context.resolveSlotModelState('gameSummary').defaultModelId, 'qwen3.8-flash');
});

test('game slots keep the fixed-model marker of the text slot they mirror', () => {
    const { context, setSlot, select } = createPickerContext();
    setSlot('conversation', 'kimi_code', 'typed');
    setSlot('gameMain', 'follow_conversation');
    assert.deepEqual(plain(context.resolveSlotModelState('gameMain')), {
        defaultModelId: 'kimi-for-coding', acceptsTypedModelId: false, fixedModelProvider: 'kimi_code',
    });

    select('assistApiSelect', 'free');
    setSlot('conversation', 'follow_assist');
    setSlot('gameMain', 'follow_conversation');
    assert.equal(context.resolveSlotModelState('gameMain').fixedModelProvider, 'free');
});

test('game summary follows the same assist conversation model when its summary tier is empty or missing', () => {
    for (const summary of ['', undefined]) {
        const { context, setSlot, select } = createPickerContext();
        context._assistModelDefaults.extra_provider = {
            CONVERSATION_MODEL: 'provider-chat-model', SUMMARY_MODEL: summary,
        };
        select('assistApiSelect', 'extra_provider');
        setSlot('summary', 'follow_core', 'core-summary');
        setSlot('gameSummary', 'follow_assist');
        assert.equal(context.resolveSlotModelState('gameSummary').defaultModelId, 'provider-chat-model');
    }
});

test('realtime and TTS slots only honor typed model IDs outside follow modes', () => {
    const { context, setSlot, select } = createPickerContext();
    assert.deepEqual(plain(context.resolveSlotModelState('omni')), {
        defaultModelId: 'qwen3.8-omni-flash-realtime', acceptsTypedModelId: false, fixedModelProvider: '',
    });
    select('coreApiSelect', 'free');
    assert.equal(context.resolveSlotModelState('omni').fixedModelProvider, 'free');

    assert.equal(context.resolveSlotModelState('tts').acceptsTypedModelId, false);
    setSlot('tts', 'custom');
    assert.equal(context.resolveSlotModelState('tts').acceptsTypedModelId, true);
});

test('placeholders show the model in use and fall back to the original example', () => {
    const { context, el, setSlot, select } = createPickerContext();
    context.refreshModelIdHints();
    assert.equal(el('conversationModelId').placeholder, '当前使用：google/gemini-2.5-flash');
    assert.equal(el('omniModelId').placeholder, '当前使用：qwen3.8-omni-flash-realtime');
    assert.equal(el('ttsModelId').placeholder, 'e.g., gpt-3.5-turbo');

    select('assistApiSelect', 'free');
    setSlot('gameMain', 'custom');
    context.refreshModelIdHints();
    assert.equal(el('conversationModelId').placeholder, '免费版使用固定模型');
    assert.equal(el('gameMainModelId').placeholder, 'e.g., gpt-3.5-turbo');
});

test('translation helper interpolates its fallback when i18n is unavailable', () => {
    const { context } = createPickerContext();
    assert.equal(context.translateModelPickerText('api.missing', '还有 {{count}} 个', { count: 3 }), '还有 3 个');
});

test('provider requests come from the provider table and switch MiMo to its token plan node', () => {
    const { context, select, state, setSlot } = createPickerContext();
    state.customKey = 'sk-typed';
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), {
        body: { provider_key: 'openrouter', api_key: 'sk-typed' },
    });

    select('assistApiSelect', 'mimo');
    state.tokenPlan = true;
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), {
        body: { provider_key: 'mimo', api_key: 'sk-typed', url: 'https://token-plan-cn.xiaomimimo.com/v1' },
    });

    setSlot('vision', 'mimo');
    state.customKey = '';
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('vision')), {
        body: { provider_key: 'mimo', api_key: '' },
    });

    select('assistApiSelect', 'kimi_code');
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), { reason: 'fixed' });
});

test('custom slots send the masked sentinel only together with their own endpoint', () => {
    const { context, el, setSlot } = createPickerContext();
    setSlot('summary', 'custom');
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), { reason: 'missingUrl' });

    el('summaryModelUrl').value = 'wss://example.test/realtime';
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), { reason: 'unsupported' });

    el('summaryModelUrl').value = 'https://example.test/v1';
    el('summaryModelApiKey').dataset.maskedSecret = 'true';
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('summary')), {
        body: { url: 'https://example.test/v1', api_key: MASKED, model_type: 'summary', provider_type: 'openai_compatible' },
    });
});

test('slots whose input is not used never offer the picker', () => {
    const { context, setSlot, select } = createPickerContext();
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('gameMain')), { reason: 'follow' });
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('omni')), { reason: 'follow' });

    select('assistApiSelect', 'free');
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('conversation')), { reason: 'fixed' });

    setSlot('tts', 'gptsovits');
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('tts')), { reason: 'unsupported' });

    setSlot('tts', 'elevenlabs');
    assert.deepEqual(plain(context.resolveSlotModelPickerRequest('tts')), { reason: 'unsupported' });
});

test('image picker lists only OpenAI-protocol providers or a custom endpoint', () => {
    const { context, el, select, state } = createPickerContext();
    select('imageModelProvider', 'disabled');
    assert.deepEqual(plain(context.resolveImageModelPickerRequest()), { reason: 'noProvider' });

    select('imageModelProvider', 'qwen');
    assert.deepEqual(plain(context.resolveImageModelPickerRequest()), { reason: 'unsupported' });

    select('imageModelProvider', 'openai');
    state.bookKeys.openai = MASKED;
    assert.deepEqual(plain(context.resolveImageModelPickerRequest()), {
        body: { provider_key: 'openai', api_key: MASKED },
    });

    select('imageModelProvider', 'custom');
    el('imageModelUrl').value = 'https://images.example.test/v1';
    el('imageModelApiKey').value = 'sk-image';
    assert.deepEqual(plain(context.resolveImageModelPickerRequest()), {
        body: { url: 'https://images.example.test/v1', api_key: 'sk-image', model_type: 'image', provider_type: 'openai_compatible' },
    });
});

test('model lists are normalized and cached only when the request succeeds', async () => {
    let calls = 0;
    const { context, state } = createPickerContext({
        fetchImpl: async () => {
            calls += 1;
            return {
                ok: true,
                json: async () => ({ success: true, models: [{ id: ' a ', name: 'A' }, { id: '' }, { name: 'x' }, { id: 'b', name: 5 }] }),
            };
        },
    });
    const body = { provider_key: 'openrouter', api_key: '' };
    const first = await context.fetchModelList(body);
    assert.deepEqual(plain(first), { success: true, models: [{ id: 'a', name: 'A' }, { id: 'b', name: '' }] });
    await context.fetchModelList({ ...body });
    assert.equal(calls, 1);
    assert.equal(state.fetchCalls[0].url, '/api/config/list_models');
});

test('model list failures are mapped to error codes and not cached', async () => {
    const responses = [
        () => Promise.resolve({ ok: false, status: 503 }),
        () => Promise.reject(Object.assign(new Error('late'), { name: 'TimeoutError' })),
        () => Promise.resolve({ ok: true, json: async () => ({ success: true, models: [] }) }),
        () => Promise.resolve({ ok: true, json: async () => ({ success: false, error_code: 'auth_failed', error: 'bad key' }) }),
    ];
    const { context, state } = createPickerContext({ fetchImpl: () => responses.shift()() });
    const body = { provider_key: 'openrouter', api_key: '' };
    assert.equal((await context.fetchModelList(body)).error_code, 'backend_unavailable');
    assert.equal((await context.fetchModelList(body)).error_code, 'timeout');
    assert.equal((await context.fetchModelList(body)).error_code, 'empty');
    assert.equal((await context.fetchModelList(body)).error_code, 'auth_failed');
    assert.equal(state.fetchCalls.length, 4);
});

test('named and follow_core slots read the merged defaults whichever assist provider is selected', () => {
    const { context, select, setSlot } = createPickerContext();
    context._assistModelDefaults.qwen = { VISION_MODEL: '', CONVERSATION_MODEL: 'merged-qwen' };
    for (const assistProvider of ['qwen', 'openrouter']) {
        select('assistApiSelect', assistProvider);
        for (const mode of ['follow_core', 'qwen']) {
            setSlot('vision', mode);
            assert.equal(context.resolveSlotModelState('vision').defaultModelId, 'merged-qwen');
        }
    }
});

test('follow_core picker uses the core input and marks stored credential origin', () => {
    const { context, el, state, setSlot } = createPickerContext();
    setSlot('vision', 'follow_core');
    state.customKey = 'other-book-key';
    el('apiKeyInput').value = 'core-draft-key';
    assert.equal(context.resolveSlotModelPickerRequest('vision').body.api_key, 'core-draft-key');
    assert.equal(context.resolveSlotModelPickerRequest('vision').body.key_source, 'core');
});

test('a model-list 404 adds a URL path hint to the localized error', () => {
    const { context } = createPickerContext({ translations: {
        'api.modelPicker.error.unsupported': 'No catalog',
        'api.modelPicker.error.urlHint': 'Check /v1',
    } });
    assert.equal(context.getModelPickerErrorMessage({ error_code: 'unsupported', check_url: true }), 'No catalog Check /v1');
});

test('named realtime and TTS providers never list conversation models', () => {
    const { context, setSlot, el, state } = createPickerContext();
    for (const type of ['omni', 'tts']) {
        setSlot(type, 'qwen');
        assert.equal(context.resolveSlotModelPickerRequest(type).reason, 'unsupported');
        setSlot(type, 'custom');
        el(`${type}ModelUrl`).value = 'http://localhost:8000/v1';
        assert.ok(context.resolveSlotModelPickerRequest(type).body);
    }
    state.ttsMeta.local = { editable_endpoint: true };
    setSlot('tts', 'local');
    assert.ok(context.resolveSlotModelPickerRequest('tts').body);
});

test('credential and Token Plan changes discard cached and pending model lists', async () => {
    let finish;
    let calls = 0;
    const response = { ok: true, json: async () => ({ success: true, models: [{ id: 'a' }] }) };
    const { context, el } = createPickerContext({ fetchImpl: () => {
        calls += 1;
        return calls === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(response);
    } });
    context.initModelIdPickers();
    const body = { provider_key: 'openrouter', api_key: MASKED };
    const pending = context.fetchModelList(body);
    el('useMimoTokenPlan').dispatchEvent({ type: 'change' });
    finish(response);
    await pending;
    await context.fetchModelList(body);
    assert.equal(calls, 2);
    for (const id of ['apiKeyInput', 'assistApiKeyInput', 'mimoTokenPlanKeyInput',
        'assistApiKeyOpenAI', 'conversationModelApiKey', 'imageModelApiKey']) {
        context.document.listeners.change.forEach(handler => handler({ target: { id } }));
        await context.fetchModelList(body);
    }
    assert.equal(calls, 8);
});

test('typing a slot refreshes only that slot and its game mirror', async () => {
    const { context, el } = createPickerContext();
    context.initModelIdPickers();
    const untouched = el('visionModelId').placeholder;
    el('conversationModelId').value = 'my-chat-model';
    el('conversationModelId').dispatchEvent({ type: 'input' });
    await Promise.resolve();
    assert.equal(el('gameMainModelId').placeholder, '当前使用：my-chat-model');
    assert.equal(el('visionModelId').placeholder, untouched);
});

test('arrow navigation scrolls the menu without scrolling the page', () => {
    const { context } = createPickerContext();
    let focusOptions;
    const option = { offsetTop: 150, offsetHeight: 30, focus: options => { focusOptions = options; } };
    const menuScroll = { offsetTop: 10, scrollTop: 0, clientHeight: 100, querySelectorAll: () => [option] };
    context.focusModelIdPickerOption({ menuScroll }, 1);
    assert.deepEqual(plain(focusOptions), { preventScroll: true });
    assert.equal(menuScroll.scrollTop, 70);
});

test('agent defaults prefer vision before conversation for both core and named slots', () => {
    const { context, setSlot, select } = createPickerContext();
    context._assistModelDefaults.openai = { AGENT_MODEL: '', VISION_MODEL: 'vision-only', CONVERSATION_MODEL: 'text-only' };
    select('coreApiSelect', 'openai');
    for (const mode of ['follow_core', 'openai']) {
        setSlot('agent', mode);
        assert.equal(context.resolveSlotModelState('agent').defaultModelId, 'vision-only');
    }
});

test('resize keeps the focused model picker open and closes unrelated dropdowns', () => {
    const handlers = {};
    const kept = [];
    const picker = {};
    const context = vm.createContext({
        providerDropdownHandlersBound: false,
        document: { addEventListener() {}, activeElement: { closest: () => picker } },
        window: { addEventListener: (type, handler) => { handlers[type] = handler; } },
        closeAllProviderSelectDropdowns: except => kept.push(except),
    });
    vm.runInContext(sourceBetween('function bindProviderDropdownGlobalHandlers()', '// ====================') + '\nbindProviderDropdownGlobalHandlers();', context);
    handlers.resize();
    assert.equal(kept[0], picker);
    context.document.activeElement = null;
    handlers.resize();
    assert.equal(kept[1], undefined);
});

test('error messages prefer picker texts, then connectivity texts, then the backend message', () => {
    const { context } = createPickerContext({
        translations: {
            'api.modelPicker.error.unsupported': 'No model list here',
            'api.modelPicker.error.core_key_required': 'Core provider changed; enter its key',
            'connectivity.error.auth_failed': 'Invalid key',
        },
    });
    assert.equal(context.getModelPickerErrorMessage({ error_code: 'unsupported' }), 'No model list here');
    assert.equal(context.getModelPickerErrorMessage({ error_code: 'auth_failed' }), 'Invalid key');
    assert.equal(context.getModelPickerErrorMessage({ error_code: 'core_key_required' }), 'Core provider changed; enter its key');
    assert.equal(context.getModelPickerErrorMessage({ error_code: 'weird', error: 'raw detail' }), 'raw detail');
});
