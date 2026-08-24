const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SOURCE_PATH = path.join(PROJECT_ROOT, 'static', 'js', 'api_key_settings.js');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

function createInputContext() {
    const document = {
        activeElement: null,
        getElementById() { return null; },
    };
    const context = vm.createContext({ document });
    const helperSource = [
        "const MASKED_SECRET_SENTINEL = '__NEKO_SECRET_MASKED__';",
        "const MASKED_SECRET_DISPLAY = '••••••••••••';",
        sourceBetween('function maskApiKey(', '/**\n * 判断后端保留哨兵'),
        sourceBetween('function isMaskedSecretValue(', '/**\n * 将真实 key 写入'),
        sourceBetween('function setMaskedInput(', 'function setSecretInputValue('),
        sourceBetween('function setSecretInputValue(', '/**\n * ⚠️ 重要'),
        sourceBetween('function getRealKey(', '/**\n * 为 API Key 输入框绑定'),
        sourceBetween('function attachMaskBehavior(', '// 允许的来源列表'),
        'globalThis.maskApiKey = maskApiKey;',
        'globalThis.isMaskedSecretValue = isMaskedSecretValue;',
        'globalThis.setMaskedInput = setMaskedInput;',
        'globalThis.setSecretInputValue = setSecretInputValue;',
        'globalThis.getRealKey = getRealKey;',
        'globalThis.attachMaskBehavior = attachMaskBehavior;',
        'globalThis.MASKED_SECRET_SENTINEL_FOR_TEST = MASKED_SECRET_SENTINEL;',
        'globalThis.MASKED_SECRET_DISPLAY_FOR_TEST = MASKED_SECRET_DISPLAY;',
    ].join('\n');
    vm.runInContext(helperSource, context, { filename: SOURCE_PATH });
    return context;
}

function createFakeInput() {
    const listeners = new Map();
    return {
        dataset: {},
        value: '',
        selected: false,
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        dispatch(type) {
            for (const listener of listeners.get(type) || []) listener({ type });
        },
        select() {
            this.selected = true;
        },
    };
}

test('masked secret sentinel is displayed generically and round-trips without entering the DOM as a real key', () => {
    const context = createInputContext();
    const input = createFakeInput();

    context.setMaskedInput(input, context.MASKED_SECRET_SENTINEL_FOR_TEST);
    context.attachMaskBehavior(input);

    assert.equal(input.value, context.MASKED_SECRET_DISPLAY_FOR_TEST);
    assert.equal(input.dataset.realKey, '');
    assert.equal(input.dataset.maskedSecret, 'true');
    assert.equal(context.getRealKey(input), context.MASKED_SECRET_SENTINEL_FOR_TEST);

    context.document.activeElement = input;
    input.dispatch('focus');
    assert.equal(input.value, context.MASKED_SECRET_DISPLAY_FOR_TEST);
    assert.equal(input.selected, true);
    assert.equal(context.getRealKey(input), context.MASKED_SECRET_SENTINEL_FOR_TEST);

    context.document.activeElement = null;
    input.dispatch('blur');
    assert.equal(input.value, context.MASKED_SECRET_DISPLAY_FOR_TEST);
    assert.equal(context.getRealKey(input), context.MASKED_SECRET_SENTINEL_FOR_TEST);
});

test('editing or deleting the generic mask replaces the preserved secret state', () => {
    const context = createInputContext();
    const input = createFakeInput();
    context.setMaskedInput(input, context.MASKED_SECRET_SENTINEL_FOR_TEST);
    context.attachMaskBehavior(input);

    context.document.activeElement = input;
    input.dispatch('focus');
    input.dispatch('beforeinput');
    input.value = 'sk-new-user-value';
    input.dispatch('input');
    context.document.activeElement = null;
    input.dispatch('blur');

    assert.equal(input.dataset.maskedSecret, undefined);
    assert.equal(context.getRealKey(input), 'sk-new-user-value');

    context.setMaskedInput(input, context.MASKED_SECRET_SENTINEL_FOR_TEST);
    context.document.activeElement = input;
    input.dispatch('focus');
    // Backspace/Delete produces beforeinput; the listener clears the reserved state.
    input.dispatch('beforeinput');
    input.value = '';
    input.dispatch('input');
    context.document.activeElement = null;
    input.dispatch('blur');

    assert.equal(input.dataset.maskedSecret, undefined);
    assert.equal(input.value, '');
    assert.equal(context.getRealKey(input), '');
});

test('partial bullet input fallback cannot become a real key without beforeinput', () => {
    const context = createInputContext();
    const input = createFakeInput();
    context.setMaskedInput(input, context.MASKED_SECRET_SENTINEL_FOR_TEST);
    context.attachMaskBehavior(input);

    context.document.activeElement = input;
    input.dispatch('focus');
    input.value = '••••••';
    input.dispatch('input');
    context.document.activeElement = null;
    input.dispatch('blur');

    assert.equal(input.dataset.realKey, '');
    assert.equal(input.dataset.maskedSecret, 'true');
    assert.equal(input.value, context.MASKED_SECRET_DISPLAY_FOR_TEST);
    assert.equal(context.getRealKey(input), context.MASKED_SECRET_SENTINEL_FOR_TEST);
});

test('secret loading clears stale masked state when a reload returns an empty value', () => {
    const context = createInputContext();
    const input = createFakeInput();
    context.document.getElementById = id => id === 'mimoTokenPlanKeyInput' ? input : null;

    context.setSecretInputValue('mimoTokenPlanKeyInput', context.MASKED_SECRET_SENTINEL_FOR_TEST);
    assert.equal(input.dataset.maskedSecret, 'true');

    context.setSecretInputValue('mimoTokenPlanKeyInput', undefined);
    assert.equal(input.dataset.maskedSecret, undefined);
    assert.equal(input.dataset.realKey, '');
    assert.equal(input.value, '');
});

test('legacy masks are recognized narrowly without treating arbitrary starred keys as placeholders', () => {
    const context = createInputContext();

    assert.equal(context.isMaskedSecretValue(context.MASKED_SECRET_DISPLAY_FOR_TEST), true);
    assert.equal(context.isMaskedSecretValue('•'), true);
    assert.equal(context.isMaskedSecretValue('••••••'), true);
    assert.equal(context.isMaskedSecretValue('***'), true);
    assert.equal(context.isMaskedSecretValue('********'), true);
    assert.equal(context.isMaskedSecretValue('abcdef***ghijkl'), true);
    assert.equal(context.isMaskedSecretValue('abcdef*******ghijkl'), true);
    assert.equal(context.isMaskedSecretValue('abc***def'), false);
    assert.equal(context.isMaskedSecretValue('abcdef**ghijkl'), false);
    assert.equal(context.isMaskedSecretValue('abcde****fghijkl'), false);
    assert.equal(context.isMaskedSecretValue('prefix-a***b-suffix'), false);
    assert.equal(context.isMaskedSecretValue('sk-live-a***b-extra-long'), false);
});

test('connectivity testKey refuses the sentinel before making a request', async () => {
    const connectivitySource = sourceBetween(
        'const ConnectivityManager = {',
        '// ==================== 连通性测试：集成初始化 ====================',
    );
    let fetchCalls = 0;
    const context = vm.createContext({
        console,
        fetch() {
            fetchCalls += 1;
            throw new Error('fetch must not be called for a masked secret');
        },
    });
    vm.runInContext([
        "const MASKED_SECRET_SENTINEL = '__NEKO_SECRET_MASKED__';",
        "const MASKED_SECRET_DISPLAY = '••••••••••••';",
        sourceBetween('function isMaskedSecretValue(', '/**\n * 将真实 key 写入'),
        connectivitySource,
        'globalThis.ConnectivityManagerForTest = ConnectivityManager;',
    ].join('\n'), context, { filename: SOURCE_PATH });

    for (const apiKey of [
        '__NEKO_SECRET_MASKED__',
        '••••••••••••',
        'abcdef***ghijkl',
    ]) {
        const result = await context.ConnectivityManagerForTest.testKey({ api_key: apiKey });
        assert.equal(result.skipped, true);
        assert.equal(result.success, false);
    }
    assert.equal(fetchCalls, 0);
});

test('switching core providers preserves key-book provenance in the save payload', () => {
    const context = vm.createContext({});
    vm.runInContext([
        sourceBetween('function resolveCoreApiKeyForSave(', 'async function save_button_down('),
        'globalThis.resolveCoreApiKeyForSaveForTest = resolveCoreApiKeyForSave;',
        'globalThis.buildKeyBookSecretPayloadForTest = buildKeyBookSecretPayload;',
    ].join('\n'), context, { filename: SOURCE_PATH });

    const sentinel = '__NEKO_SECRET_MASKED__';
    const allBookKeys = {
        qwen: sentinel,
        openai: sentinel,
    };
    const registry = {
        qwen: { config_field: 'assistApiKeyQwen' },
        openai: { config_field: 'assistApiKeyOpenai' },
    };
    const coreApiKey = context.resolveCoreApiKeyForSaveForTest(
        'openai',
        sentinel,
        allBookKeys,
        false,
    );
    const keyBookPayload = context.buildKeyBookSecretPayloadForTest(allBookKeys, registry);

    // coreApi identifies the selected source. Both book fields remain present so
    // the backend can preserve the previous provider and copy the selected one.
    assert.equal(coreApiKey, sentinel);
    assert.deepEqual(
        JSON.parse(JSON.stringify(keyBookPayload)),
        {
            assistApiKeyQwen: sentinel,
            assistApiKeyOpenai: sentinel,
        },
    );
    assert.equal(
        context.resolveCoreApiKeyForSaveForTest('openai', 'sk-new-openai', allBookKeys, true),
        'sk-new-openai',
    );
});

test('all secret-bearing form fields use masked loading and masked save accessors', () => {
    const modelTypes = [
        'conversation', 'summary', 'gameMain', 'gameSummary', 'correction',
        'emotion', 'vision', 'agent', 'omni', 'tts',
    ];
    for (const modelType of modelTypes) {
        assert.match(
            source,
            new RegExp(`setSecretInputValue\\('${modelType}ModelApiKey', data\\.${modelType}ModelApiKey\\)`),
        );
        assert.doesNotMatch(
            source,
            new RegExp(`setInputValue\\('${modelType}ModelApiKey',`),
        );
    }
    assert.match(source, /setSecretInputValue\('mcpTokenInput', data\.mcpToken\)/);
    assert.match(source, /const mcpToken = getKeyVal\('mcpTokenInput'\)/);
    assert.match(source, /setSecretInputValue\('mimoTokenPlanKeyInput', data\.assistApiKeyMimoTokenPlan\)/);
    assert.doesNotMatch(source, /mimoTokenPlanKeyInput && data\.assistApiKeyMimoTokenPlan/);
    assert.match(source, /if \(!coreResult\.secretMasked && coreCacheId/);
    assert.match(source, /if \(!assistResult\.secretMasked && assistCacheId/);
    assert.match(source, /if \(!customResult\.secretMasked && customCacheId/);
});

test('manual core and assist tests show a localized notice instead of testing masked secrets', () => {
    const guardedBranches = source.match(
        /if \(resolved\.secretMasked\) \{\s*showMaskedSecretConnectivityNotice\(\);\s*\} else if \(resolved\.cacheId\) \{/g,
    ) || [];
    assert.equal(guardedBranches.length, 2);
    assert.match(
        source,
        /window\.t\('connectivity\.maskedSecretRetype', fallback\)/,
    );

    for (const locale of ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es']) {
        const localePath = path.join(PROJECT_ROOT, 'static', 'locales', `${locale}.json`);
        const messages = JSON.parse(fs.readFileSync(localePath, 'utf8'));
        assert.equal(typeof messages.connectivity.maskedSecretRetype, 'string', locale);
        assert.notEqual(messages.connectivity.maskedSecretRetype.trim(), '', locale);
    }
});
