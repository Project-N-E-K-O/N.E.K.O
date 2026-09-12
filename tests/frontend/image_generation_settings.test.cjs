const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');

function setup(restricted = []) {
    const inputs = Object.fromEntries(['Provider', 'Url', 'Id', 'ApiKey'].map(suffix => [
        'imageModel' + suffix, {value: '', dataset: {}, options: [], appendChild(option) { this.options.push(option); }, replaceChildren() { this.options = []; }}
    ]));
    const context = vm.createContext({
        document: {getElementById: id => inputs[id], createElement: () => ({})},
        appendModelProviderOption: (select, value) => select.appendChild({value}),
        setSecretInputValue: (id, value) => { inputs[id].value = value; },
        getRealKey: input => input.value,
        syncProviderSelectDropdowns: () => {},
        isProviderRestricted: key => restricted.includes(key),
    });
    vm.runInContext('let _imageProviders = {};\n'.replace('\\n', '\n') + source.slice(source.indexOf('function populateImageProviders(')), context);
    return {inputs, context};
}
test('image settings preserve masked custom key and entered endpoint', () => {
    const {inputs, context} = setup();
    context.populateImageProviders({custom: {name: 'Custom', base_url: '', model: ''}});
    context.loadImageSettings({imageModelProvider: 'custom', imageModelUrl: 'https://custom.example/v1', imageModelId: 'model', imageModelApiKey: '__NEKO_SECRET_MASKED__'});
    assert.equal(context.imageSettingsPayload().imageModelApiKey, '__NEKO_SECRET_MASKED__');
    assert.equal(inputs.imageModelUrl.value, 'https://custom.example/v1');
    assert.equal(inputs.imageModelUrl.readOnly, false);
});
test('switching from custom clears its key and uses named endpoint defaults', () => {
    const {inputs, context} = setup();
    context.populateImageProviders({openai: {name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-image-2'}});
    inputs.imageModelApiKey.value = 'custom-secret';
    inputs.imageModelProvider.value = 'openai';
    context.onImageProviderChange();
    assert.equal(context.imageSettingsPayload().imageModelApiKey, '');
    assert.equal(inputs.imageModelApiKey.disabled, true);
    assert.equal(inputs.imageModelUrl.value, 'https://api.openai.com/v1');
    assert.equal(inputs.imageModelId.value, 'gpt-image-2');
});
test('missing provider metadata preserves saved values', () => {
    const {context} = setup();
    context.populateImageProviders({});
    context.loadImageSettings({imageModelProvider: 'qwen', imageModelUrl: 'https://dashscope.aliyuncs.com', imageModelId: 'saved-model', imageModelApiKey: ''});
    assert.equal(context.imageSettingsPayload().imageModelProvider, 'qwen');
    assert.equal(context.imageSettingsPayload().imageModelId, 'saved-model');
    context.populateImageProviders({custom: {name: 'Custom'}});
    assert.equal(context.imageSettingsPayload().imageModelProvider, 'qwen');
    assert.equal(context.imageSettingsPayload().imageModelId, 'saved-model');
});

test('restricted providers preserve saved selection without becoming selectable', () => {
    const {inputs, context} = setup(['openai', 'qwen_intl']);
    context.populateImageProviders({openai: {name: 'OpenAI'}, qwen_intl: {name: 'Singapore'}, qwen: {name: 'Beijing'}, custom: {name: 'Custom'}});
    assert.deepEqual(inputs.imageModelProvider.options.map(option => option.value), ['disabled', 'qwen', 'custom']);
    context.loadImageSettings({imageModelProvider: 'openai', imageModelId: 'saved'});
    assert.equal(context.imageSettingsPayload().imageModelProvider, 'openai');
    assert.equal(context.imageSettingsPayload().imageModelId, 'saved');
    assert.equal(inputs.imageModelProvider.options.find(option => option.value === 'openai').disabled, true);
    context.populateImageProviders({openai: {name: 'OpenAI'}, qwen: {name: 'Beijing'}});
    assert.equal(context.imageSettingsPayload().imageModelProvider, 'openai');
    assert.equal(inputs.imageModelProvider.options.find(option => option.value === 'openai').disabled, true);
    inputs.imageModelProvider.value = 'disabled';
    context.onImageProviderChange();
    assert.equal(context.imageSettingsPayload().imageModelProvider, 'disabled');
});


test('unknown provider fields round trip verbatim across reloads', () => {
    const {context} = setup();
    context.populateImageProviders({});
    const saved = {imageModelProvider: 'future', imageModelUrl: '  https://future.example/v1  ', imageModelId: ' model ', imageModelApiKey: '__NEKO_SECRET_MASKED__'};
    context.loadImageSettings(saved);
    context.populateImageProviders({custom: {name: 'Custom'}});
    assert.deepEqual(JSON.parse(JSON.stringify(context.imageSettingsPayload())), saved);
});
