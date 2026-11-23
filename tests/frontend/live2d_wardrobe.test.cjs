const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/live2d/live2d-wardrobe.js'), 'utf8');
const modelPath = '/static/mao_pro/mao_pro.model3.json';
const choices = {version: 1, wardrobe: 'mao-hoodie-v1', print: 'plain', hat: false, color: '#aabbcc'};

function fixture() {
    const controls = new Map();
    const panel = {querySelector(key) {
        if (!controls.has(key)) controls.set(key, new EventTarget());
        return controls.get(key);
    }};
    const manager = {setOutfitOverrides(model) { applied.push(model); }, clearOutfit() {}};
    const applied = [], requests = [];
    const window = {live2dManager: manager, lanlan_config: {lanlan_name: 'A'}, t: key => key};
    let ready;
    window.addEventListener = (key, callback) => { if (key === 'live2d-model-ready') ready = callback; };
    const fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve}));
    const location = new URL('http://localhost/');
    vm.runInNewContext(source, {window, document: {getElementById: () => panel}, URL, location, fetch});
    const model = () => Object.assign(new EventEmitter(), {textures: [{baseTexture: {}}]});
    const activate = (instance, url = modelPath) => {
        manager.currentModel = instance;
        return ready({detail: {modelPath: url}});
    };
    const response = outfit => ({ok: true, json: async () => ({wardrobe: choices.wardrobe, model_ref: modelPath, outfit})});
    return {panel, manager, applied, requests, model, activate, response};
}

test('preparing a replacement has no wardrobe side effects and late reads cannot dress it', async () => {
    const f = fixture(), old = f.model(), prepared = f.model();
    const loadingOld = f.activate(old);
    assert.equal(f.requests.length, 1);
    const loadingNew = f.activate(prepared);
    f.requests[0].resolve(f.response(choices));
    await loadingOld;
    assert.equal(f.applied.length, 0);
    f.requests[1].resolve(f.response(choices));
    await loadingNew;
    assert.deepEqual(f.applied, [prepared]);
    old.emit('wardrobe-detached');
    assert.equal(f.panel.hidden, false);
    prepared.emit('wardrobe-detached');
    assert.equal(f.panel.hidden, true);
});

test('an unknown catalog on import is neither applied nor overwritten', async () => {
    const f = fixture();
    const imported = f.model();
    await f.activate(imported, '/user_live2d/mao_pro/mao_pro.model3.json');
    assert.equal(f.requests.length, 0);
    const loading = f.activate(imported);
    f.requests[0].resolve(f.response({...choices, wardrobe: 'not-installed'}));
    await loading;
    assert.equal(f.panel.hidden, true);
    assert.equal(f.applied.length, 0);
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].options.method, undefined);
});
