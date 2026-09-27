const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { exercisePreviewCleanup } = require('./fixtures/live2d_preview_cleanup.cjs');

const root = path.resolve(__dirname, '../..');
const sources = [
    'static/live2d/live2d-interaction.js',
    'static/js/character_card_manager/model-previews.js',
];

for (const options of [
    {}, { cleanupFails: true },
    { cleanupFails: true, removeFails: true, destroyFails: true },
    { legacy: true },
]) {
    test(`preview teardown completes and releases listeners: ${JSON.stringify(options)}`, async () => {
        const elements = new Map(['live2d-canvas', 'live2d-preview-canvas'].map(id => [id, {
            style: {}, removeEventListener() {},
        }]));
        const context = vm.createContext({
            Live2DManager: function Live2DManager() {},
            window: new EventTarget(), Event, setTimeout, clearTimeout,
            console: { ...console, debug() {} },
            selectedModelInfo: null,
            setLive2DPreviewRefreshButtonState() {},
            document: {
                addEventListener() {}, querySelector: () => null,
                getElementById: id => elements.get(id) || null,
            },
        });
        for (const file of sources) {
            vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context, { filename: file });
        }
        const result = await vm.runInContext(`(${exercisePreviewCleanup})(${JSON.stringify(options)})`, context);
        assert.deepEqual(Array.from(result.order), options.legacy ? ['remove', 'destroy'] : ['remove', 'cleanup', 'destroy']);
        assert.equal(result.governorStops, 1);
        assert.equal(result.resizeCalls, 0);
        assert.equal(result.revealFired, false);
        for (const field of ['released', 'reset', 'listenersRemoved', 'stylesCleared', 'mainInteractive']) {
            assert.equal(result[field], true, field);
        }
        assert.equal(result.cleanupErrorReported, !!options.cleanupFails);
        assert.equal(result.destroyErrorReported, !!options.destroyFails);
        assert.equal(result.removeErrorReported, !!options.removeFails);
    });
}
