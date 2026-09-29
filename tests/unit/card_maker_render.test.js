const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const cardMakerSource = fs.readFileSync(
    'static/js/card_maker.js',
    'utf8'
);

function loadCardMakerRendering({ layered = true, manager = {} } = {}) {
    const sourceHelpers = cardMakerSource.slice(
        cardMakerSource.indexOf('    function getDrawableSourceSize(source) {'),
        cardMakerSource.indexOf('    function isCrossOriginHttpUrl(value) {')
    );
    const drawStart = cardMakerSource.indexOf('    function drawModelWithComposition(');
    const drawEnd = cardMakerSource.indexOf('    // ====== 预览循环 ======', drawStart);
    const drawFunction = cardMakerSource.slice(drawStart, drawEnd);
    const drawableGetter = cardMakerSource.slice(
        cardMakerSource.indexOf('    function getPNGTuberDrawableSource('),
        cardMakerSource.indexOf('    async function waitForImageReady(')
    );
    const canvasGetter = cardMakerSource.slice(
        cardMakerSource.indexOf('    function getModelCanvas()'),
        cardMakerSource.indexOf('    /**\n     * 在截图前确保渲染器输出最新帧')
    );
    const context = {
        window: {
            cardMakerPNGTuberManager: {
                isLayeredActive: () => layered,
                layeredCanvasLogicalWidth: 1200,
                layeredCanvasLogicalHeight: 1600,
                layeredCanvasPadding: 100,
                ...manager
            }
        },
        document: {
            createElement(tagName) {
                return tagName === 'canvas'
                    ? createCanvasWithAlphaBounds(1, 1)
                    : null;
            }
        }
    };
    const source = `(() => {
        let currentModelType = 'pngtuber';
        let pngtuberCardFrame = null;
        const composition = { offsetX: 0, offsetY: 0, scale: 100, rotation: 0 };
        ${sourceHelpers}
        ${drawableGetter}
        ${canvasGetter}
        ${drawFunction}
        return {
            draw: drawModelWithComposition,
            prepare: () => preparePNGTuberCardFrame(window.cardMakerPNGTuberManager),
            getCanvas: getModelCanvas
        };
    })()`;
    return vm.runInNewContext(source, context);
}

function createContext() {
    const calls = [];
    return {
        calls,
        ctx: {
            drawImage(...args) {
                calls.push(args);
            },
            save() {},
            restore() {},
            translate() {},
            rotate() {}
        }
    };
}

function createCanvasWithAlphaBounds(width, height, bounds = { x: 0, y: 0, width: 0, height: 0 }) {
    let data = new Uint8ClampedArray(width * height * 4);
    for (let y = bounds.y; y < bounds.y + bounds.height; y += 1) {
        for (let x = bounds.x; x < bounds.x + bounds.width; x += 1) {
            data.set([x % 256, y % 256, 127, 255], (y * width + x) * 4);
        }
    }
    // Only full-canvas, same-size copies are needed here. Reject unsupported
    // operations rather than silently returning unrealistic pixel data.
    const context = {
        getImageData(x, y, readWidth, readHeight) {
            assert.deepEqual([x, y, readWidth, readHeight], [0, 0, width, height]);
            return { data: data.slice(), width, height };
        },
        drawImage(source, x, y, drawWidth, drawHeight) {
            assert.deepEqual([x, y, drawWidth, drawHeight], [0, 0, width, height]);
            assert.equal(source.width, width);
            assert.equal(source.height, height);
            data.set(source.getContext('2d').getImageData(0, 0, width, height).data);
        }
    };
    return {
        get width() { return width; },
        set width(value) {
            width = value;
            data = new Uint8ClampedArray(width * height * 4);
        },
        get height() { return height; },
        set height(value) {
            height = value;
            data = new Uint8ClampedArray(width * height * 4);
        },
        getContext() {
            return context;
        }
    };
}

test('contains a wide layered PNGTuber after removing logical canvas padding', () => {
    const { draw } = loadCardMakerRendering();
    const { ctx, calls } = createContext();
    draw(ctx, createCanvasWithAlphaBounds(600, 800, { x: 50, y: 50, width: 500, height: 700 }), 600, 800);

    assert.equal(calls.length, 1);
    const [, sx, sy, sw, sh, dx, dy, dw, dh] = calls[0];
    assert.deepEqual({ sx, sy, sw, sh }, { sx: 50, sy: 50, sw: 500, sh: 700 });
    assert.ok(Math.abs(dx - (600 - 500 * (800 / 700)) / 2) < 1e-9);
    assert.equal(dy, 0);
    assert.ok(Math.abs(dw - 500 * (800 / 700)) < 1e-9);
    assert.equal(dh, 800);
});

test('keeps layered pixels that move into the logical padding area', () => {
    const { draw } = loadCardMakerRendering();
    const { ctx, calls } = createContext();
    draw(ctx, createCanvasWithAlphaBounds(600, 800, { x: 0, y: 30, width: 600, height: 740 }), 600, 800);

    const [, sx, sy, sw, sh, dx, dy, dw, dh] = calls[0];
    assert.deepEqual({ sx, sy, sw, sh }, { sx: 0, sy: 30, sw: 600, sh: 740 });
    assert.equal(dx, 0);
    assert.equal(dy, 30);
    assert.equal(dw, 600);
    assert.equal(dh, 740);
});

test('contains a tall ordinary PNGTuber without cropping its source', () => {
    const { draw } = loadCardMakerRendering({ layered: false });
    const { ctx, calls } = createContext();
    draw(ctx, { width: 600, height: 1200 }, 600, 800);

    const [, sx, sy, sw, sh, dx, dy, dw, dh] = calls[0];
    assert.deepEqual({ sx, sy, sw, sh }, { sx: 0, sy: 0, sw: 600, sh: 1200 });
    assert.equal(dx, 100);
    assert.equal(dy, 0);
    assert.equal(dw, 400);
    assert.equal(dh, 800);
});

test('freezes copied pixels and alpha bounds when a layered snapshot is unavailable or empty', () => {
    for (const unavailable of [null, { width: 0, height: 800 }, { width: 600, height: 0 }]) {
        const runtimeCanvas = createCanvasWithAlphaBounds(600, 800, { x: 0, y: 30, width: 600, height: 740 });
        const expectedPixels = runtimeCanvas.getContext('2d').getImageData(0, 0, 600, 800).data;
        let snapshot = createCanvasWithAlphaBounds(60, 80, { x: 0, y: 0, width: 60, height: 80 });
        const api = loadCardMakerRendering({ manager: {
            canvasElement: runtimeCanvas,
            renderLayeredSnapshotCanvas: () => snapshot
        } });
        api.prepare();
        assert.equal(api.getCanvas(), snapshot);

        snapshot = unavailable;
        assert.doesNotThrow(() => api.prepare());
        const frozenCanvas = api.getCanvas();
        assert.notEqual(frozenCanvas, runtimeCanvas);
        assert.deepEqual(
            { width: frozenCanvas.width, height: frozenCanvas.height },
            { width: 600, height: 800 }
        );
        const frozenContext = frozenCanvas.getContext('2d');
        const frozenPixels = frozenContext.getImageData(0, 0, 600, 800).data;
        assert.equal(frozenPixels.length, expectedPixels.length);
        assert.ok(frozenPixels.every((value, index) => value === expectedPixels[index]), 'fallback must copy every RGBA pixel');

        // Resizing clears the runtime bitmap, even when the width is unchanged.
        // Neither the frozen pixels nor the preview/export crop may follow it.
        runtimeCanvas.width = 600;
        assert.ok(runtimeCanvas.getContext('2d').getImageData(0, 0, 600, 800).data.every(value => value === 0));
        assert.deepEqual(frozenContext.getImageData(0, 0, 600, 800).data, expectedPixels);
        const originalGetImageData = frozenContext.getImageData;
        let repeatedReads = 0;
        frozenContext.getImageData = (...args) => {
            repeatedReads += 1;
            return originalGetImageData(...args);
        };
        const { ctx, calls } = createContext();
        api.draw(ctx, api.getCanvas(), 600, 800);
        api.draw(ctx, api.getCanvas(), 1200, 1600);
        assert.equal(calls.length, 2);
        for (const call of calls) {
            assert.equal(call[0], frozenCanvas);
            assert.deepEqual(call.slice(1, 5), [0, 30, 600, 740]);
        }
        assert.equal(repeatedReads, 0);
    }
});

test('reuses a valid layered snapshot and its measured bounds for every render', () => {
    const snapshot = createCanvasWithAlphaBounds(60, 80, { x: 5, y: 5, width: 50, height: 70 });
    const originalGetContext = snapshot.getContext;
    let reads = 0;
    snapshot.getContext = () => {
        reads += 1;
        return originalGetContext();
    };
    const api = loadCardMakerRendering({ manager: {
        canvasElement: { width: 30, height: 40 },
        renderLayeredSnapshotCanvas: (state) => {
            assert.equal(state, 'idle');
            return snapshot;
        }
    } });
    api.prepare();
    const { ctx, calls } = createContext();
    for (let frame = 0; frame < 3; frame += 1) {
        assert.equal(api.getCanvas(), snapshot);
        api.draw(ctx, api.getCanvas(), 600, 800);
    }
    assert.equal(reads, 1);
    assert.equal(calls.length, 3);
    assert.deepEqual(calls[0].slice(1, 5), [5, 5, 50, 70]);
});

test('does not clear a newer model context when an older save completes', async () => {
    const bridge = fs.readFileSync('static/js/model_manager/page-bridge.js', 'utf8');
    const start = bridge.indexOf('function captureModelManagerSaveContext(currentState = {}) {');
    const end = bridge.indexOf('// 仅当本页确实保存过配置时', start);
    const helpers = bridge.slice(start, end);
    const source = `(() => {
        let currentModelType = 'live2d';
        let currentLive3dSubType = '';
        let currentModelInfo = { path: '/models/a.model3.json' };
        function captureSettingsSnapshot() {
            return { modelType: currentModelType, stableSetting: 'unchanged' };
        }
        function snapshotsEqual(a, b) {
            return !!a && !!b && Object.keys(a).length === Object.keys(b).length
                && Object.keys(a).every(key => String(a[key]) === String(b[key]));
        }
        ${helpers}
        return {
            capture: captureModelManagerSaveContext,
            isCurrent: isModelManagerSaveContextCurrent,
            setState(type, subType, path) {
                currentModelType = type;
                currentLive3dSubType = subType;
                currentModelInfo = path ? { path } : null;
            }
        };
    })()`;
    const api = vm.runInNewContext(source, {});
    const oldSave = api.capture();
    let unsaved = true;
    const oldSaveCompletion = Promise.resolve().then(() => {
        if (api.isCurrent(oldSave)) unsaved = false;
    });

    api.setState('pngtuber', '', '/models/b.png');
    await oldSaveCompletion;
    assert.equal(unsaved, true);

    api.setState('live2d', '', '/models/b.model3.json');
    assert.equal(api.isCurrent(oldSave), false);

    api.setState('live2d', '', '/models/a.model3.json');
    assert.equal(api.isCurrent(oldSave), true);
});
