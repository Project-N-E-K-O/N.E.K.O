import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_clipping_layout_and_capacity_contract():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Use the shipped manager and context implementations. Private class names
// intentionally pin this integration test to the checked-in vendor bundle.
const bundle = fs.readFileSync('static/libs/index.min.js', 'utf8');
const start = bundle.indexOf('class fe{');
const end = bundle.indexOf('class Me{', start);
assert.ok(start >= 0 && end > start, 'locate bundled clipping classes');
function bundledClassSource(name, next) {
    const first = bundle.indexOf(`class ${name}{`);
    const last = bundle.indexOf(next, first);
    assert.ok(first >= 0 && last > first, `locate bundled ${name}`);
    return bundle.slice(first, last);
}
// Return the first bundled method definition named `name` whose source
// contains `marker`, which disambiguates same-named methods on other classes.
function bundledMethodSource(name, marker = '') {
    for (let first = bundle.indexOf(`${name}(`); first >= 0;
        first = bundle.indexOf(`${name}(`, first + 1)) {
        const open = bundle.indexOf('{', first);
        if (!/^\w*\)$/.test(bundle.slice(first + name.length + 1, open))) continue;
        let depth = 0;
        for (let i = open; i < bundle.length; i++) {
            if (bundle[i] === '{') depth++;
            else if (bundle[i] === '}' && --depth === 0) {
                const source = bundle.slice(first, i + 1);
                if (source.includes(marker)) return source;
                break;
            }
        }
    }
    throw new Error(`locate bundled ${name}`);
}
function bundledClass(name, next) {
    return new Function(bundledClassSource(name, next) + `; return ${name};`)();
}
const ClippingManager = new Function('ge', 'Ct', 'bt', 'Bt', 'Ot',
    'let _e = null, pe = [0, 0, 800, 600]; const Pt = { CubismBlendMode_Normal: 0 }; '
        + bundle.slice(start, end) + '; return fe;')(
        bundledClass('ge', 'let me,pe,_e;'), bundledClass('Ct', 'class vt{'),
        class {}, () => {}, () => {});

const sandbox = { Live2DManager: function () {}, console: { log() {}, warn() {} } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/live2d/live2d-model.js', 'utf8'), sandbox);

function makeManager(n) {
    // Two targets share each unique clipping context. Trailing source
    // drawables do not themselves use masks.
    const masks = Array.from({ length: n * 3 }, (_, i) => i < n * 2
        ? (n > 1 && i % n % 2 ? [n * 2 + i % n, n * 2] : [n * 2 + i % n])
        : []);
    const counts = masks.map(ids => ids.length);
    const core = {
        getDrawableCount: () => masks.length,
        getDrawableMasks: () => masks,
        getDrawableMaskCounts: () => counts,
    };
    const manager = new ClippingManager();
    manager.initialize(core, masks.length, masks, counts, 1);
    return { manager, core };
}

async function configure(n, rendererOverrides = {}) {
    const { manager, core } = makeManager(n);
    const contexts = manager._clippingContextListForMask.slice();
    const mapping = manager._clippingContextListForDraw.slice();
    const targets = contexts.map(context => context._clippedDrawableIndexList.slice());
    const renderer = Object.assign({ _clippingManager: manager }, rendererOverrides);
    const model = { internalModel: { renderer, coreModel: core } };
    const boundary = new Error('stop before unrelated model positioning');
    const host = {
        _isLoadTokenActive: () => true,
        applyModelSettings() { throw boundary; },
    };

    await assert.rejects(
        sandbox.Live2DManager.prototype._configureLoadedModel.call(
            host, model, '/user_live2d/test/test.model3.json', {}, 1),
        error => error === boundary
    );

    assert.deepEqual(manager._clippingContextListForDraw, mapping);
    contexts.forEach((context, i) => {
        assert.equal(manager._clippingContextListForMask[i], context);
        assert.deepEqual(context._clippedDrawableIndexList, targets[i]);
    });
    return { manager, renderer, core };
}

function assertValidLayout(manager, n, activeCount = n, setup = true) {
    if (setup) manager.setupLayoutBounds(activeCount);
    assert.equal(manager.__nekoActiveClippingContextCount, activeCount);
    const slots = new Set();

    manager._clippingContextListForMask.forEach(context => {
        const bounds = context._layoutBounds;
        assert.ok(Number.isInteger(context._bufferIndex));
        assert.ok(context._bufferIndex >= 0);
        assert.ok(context._bufferIndex < manager.getRenderTextureCount());
        assert.ok(Number.isInteger(context._layoutChannelNo));
        assert.ok(context._layoutChannelNo >= 0 && context._layoutChannelNo < 4);
        [bounds.x, bounds.y, bounds.width, bounds.height].forEach(value => {
            assert.ok(Number.isFinite(value));
        });
        assert.ok(bounds.x >= 0 && bounds.y >= 0);
        assert.ok(bounds.width > 0 && bounds.height > 0);
        assert.ok(bounds.x + bounds.width <= 1 + Number.EPSILON);
        assert.ok(bounds.y + bounds.height <= 1 + Number.EPSILON);
        const slot = [context._bufferIndex, context._layoutChannelNo,
            bounds.x, bounds.y, bounds.width, bounds.height].join(':');
        assert.ok(!slots.has(slot), `duplicate mask slot at count ${n}: ${slot}`);
        slots.add(slot);
    });
    assert.equal(slots.size, n);
}

(async () => {
    // Exercise every supported count. This includes the old 63, 94/95 and
    // 125-127 floor-division holes rather than sampling only even boundaries.
    for (let n = 1; n <= 256; n++) {
        const { manager } = await configure(n);
        const expectedTextures = n <= 96 ? 3 : Math.ceil(n / 32);
        assert.equal(manager.getRenderTextureCount(), expectedTextures);
        assert.equal(manager.__nekoClippingFixApplied, true);
        assertValidLayout(manager, n);
    }

    // Sparse checks for the grown atlas, up to the 32-texture budget.
    for (const n of [300, 513, 1024]) {
        const { manager } = await configure(n);
        assert.equal(manager.getRenderTextureCount(), Math.ceil(n / 32));
        assertValidLayout(manager, n);
    }

    const empty = await configure(0);
    assert.equal(empty.manager.getRenderTextureCount(), 3);
    empty.manager.setupLayoutBounds(0);

    // Use the real SDK frame loop, including its rendering of inactive entries.
    // Slots must stay independent through activity changes and mode switches.
    for (const n of [5, 37, 95, 256, 1024]) {
        const { manager } = await configure(n);
        manager.setGL({
            createTexture: () => ({}), createFramebuffer: () => ({}),
            bindFramebuffer() {}, framebufferTexture2D() {}, bindTexture() {},
            texImage2D() {}, texParameteri() {}, viewport() {}, clearColor() {}, clear() {},
        });
        let active = new Set(Array.from({ length: n }, (_, i) => i));
        let invalidVertices = false;
        const core = {
            getDrawableVertexCount: i => (i >= n * 2 || active.has(i % n)
                || invalidVertices) ? 3 : 0,
            getDrawableVertices: i => i >= n * 2 || active.has(i % n)
                ? new Float32Array([-4, -3, -2, -1, -4, -1])
                : new Float32Array([NaN, NaN, Infinity, Infinity, NaN, NaN]),
            getDrawableDynamicFlagVertexPositionsDidChange: () => true,
            getDrawableCulling: () => false,
            getDrawableTextureIndex: () => 0,
            getDrawableVertexIndexCount: () => 3,
            getDrawableVertexIndices: () => new Uint16Array([0, 1, 2]),
            getDrawableVertexUvs: () => new Float32Array([0, 0, 1, 1, 0, 1]),
            getMultiplyColor: () => ({}), getScreenColor: () => ({}),
            getDrawableOpacity: () => 1, getPixelsPerUnit: () => 100,
        };
        let highPrecision = false;
        let currentMask;
        const drawn = new Set();
        const renderer = {
            isUsingHighPrecisionMask: () => highPrecision,
            preDraw() {}, setIsCulling() {},
            setClippingContextBufferForMask(context) { currentMask = context; },
            drawMesh() {
                drawn.add(currentMask);
                if (currentMask._isUsing) {
                    assert.ok([...currentMask._matrixForMask.getArray()].every(Number.isFinite));
                    assert.ok([...currentMask._matrixForDraw.getArray()].every(Number.isFinite));
                }
            },
        };
        const layout = () => manager._clippingContextListForMask.map(c => [
            c._bufferIndex, c._layoutChannelNo,
            c._layoutBounds.x, c._layoutBounds.y,
            c._layoutBounds.width, c._layoutBounds.height,
        ]);
        manager.setupClippingContext(core, renderer);
        const originalLayout = layout();
        const contexts = manager._clippingContextListForMask.slice();
        const mapping = manager._clippingContextListForDraw.slice();
        for (const indices of [
            Array.from({ length: n - 1 }, (_, i) => i + 1),
            [n - 1],
            Array.from({ length: n }, (_, i) => i).filter(i => i % 2 === 0),
            [],
            Array.from({ length: n }, (_, i) => i),
        ]) {
            active = new Set(indices);
            for (invalidVertices of [false, true]) {
                drawn.clear();
                manager.setupClippingContext(core, renderer);
                assert.deepEqual(layout(), originalLayout, 'activity must not move mask slots');
                assert.deepEqual(manager._clippingContextListForDraw, mapping);
                contexts.forEach((context, i) => {
                    assert.equal(manager._clippingContextListForMask[i], context);
                    assert.equal(context._isUsing, active.has(i));
                });
                assert.equal(drawn.size, active.size ? n : 0);
                // An entirely inactive frame returns before SDK layout setup.
                if (active.size) assertValidLayout(manager, n, active.size, false);
            }
        }
        highPrecision = true;
        manager.setupClippingContext(core, renderer);
        assert.ok(layout().every(slot => slot.join(',') === '0,0,0,0,1,1'));
        highPrecision = false;
        active = new Set([n - 1]);
        manager.setupClippingContext(core, renderer);
        assertValidLayout(manager, n, 1, false);
        assert.deepEqual(layout(), originalLayout);
    }

    // Beyond the atlas budget the model must still load: masks switch to the
    // SDK's per-drawable high-precision path, pinned on, with one texture.
    const hpRendererBase = {
        _useHighPrecisionMask: false,
        useHighPrecisionMask(value) { this._useHighPrecisionMask = value; },
        isUsingHighPrecisionMask() { return this._useHighPrecisionMask; },
        preDraw() {}, setIsCulling() {}, drawMesh() {},
        setClippingContextBufferForMask() {},
    };
    const huge = await configure(1025, hpRendererBase);
    const hpRenderer = huge.renderer;
    assert.equal(huge.manager.getRenderTextureCount(), 1);
    assert.equal(hpRenderer.isUsingHighPrecisionMask(), true);
    assert.equal(hpRenderer.__nekoHighPrecisionMaskForced, true);
    hpRenderer.useHighPrecisionMask(false);
    assert.equal(hpRenderer.isUsingHighPrecisionMask(), true, 'high precision stays pinned');
    huge.manager.setGL({
        createTexture: () => ({}), createFramebuffer: () => ({}),
        bindFramebuffer() {}, framebufferTexture2D() {}, bindTexture() {},
        texImage2D() {}, texParameteri() {}, viewport() {}, clearColor() {}, clear() {},
    });
    huge.manager.setupClippingContext({
        getDrawableVertexCount: () => 3,
        getDrawableVertices: () => new Float32Array([-4, -3, -2, -1, -4, -1]),
        getDrawableDynamicFlagVertexPositionsDidChange: () => true,
        getPixelsPerUnit: () => 100,
    }, hpRenderer);
    huge.manager._clippingContextListForMask.forEach(context => {
        assert.equal(context._isUsing, true);
        assert.deepEqual([context._bufferIndex, context._layoutChannelNo,
            context._layoutBounds.x, context._layoutBounds.y,
            context._layoutBounds.width, context._layoutBounds.height], [0, 0, 0, 0, 1, 1]);
        assert.ok([...context._matrixForDraw.getArray()].every(Number.isFinite));
    });
    assert.equal(huge.manager.getMaskRenderTexture().length, 1);

    // Drive the shipped renderer's draw loop: in high-precision mode every
    // clipped drawable must redraw exactly its own mask sources into texture 0
    // immediately before it is drawn with that clipping context.
    const doDrawModel = new Function('Ot', 'Pt', '_e', 'pe',
        `return function ${bundledMethodSource('doDrawModel')};`)(
        () => {}, { CubismBlendMode_Normal: 0 }, null, [0, 0, 800, 600]);
    const hugeCount = 1025;
    const maskFramebuffer = huge.manager.getMaskRenderTexture()[0];
    let boundMaskFramebuffer = null;
    let maskContext = null;
    let drawContext = null;
    const events = [];
    const drawCore = {
        getDrawableCount: () => hugeCount * 3,
        getDrawableRenderOrders: () => Array.from({ length: hugeCount * 3 }, (_, i) => i),
        getDrawableDynamicFlagIsVisible: () => true,
        getDrawableDynamicFlagVertexPositionsDidChange: () => true,
        getDrawableCulling: () => false,
        getDrawableTextureIndex: i => i,
        getDrawableVertexIndexCount: () => 3, getDrawableVertexCount: () => 3,
        getDrawableVertexIndices: () => new Uint16Array([0, 1, 2]),
        getDrawableVertices: () => new Float32Array(6),
        getDrawableVertexUvs: () => new Float32Array(6),
        getMultiplyColor: () => ({}), getScreenColor: () => ({}),
        getDrawableOpacity: () => 1, getDrawableBlendMode: () => 0,
        getDrawableInvertedMaskBit: () => false, getPixelsPerUnit: () => 100,
    };
    Object.assign(hpRenderer, {
        gl: {
            FRAMEBUFFER: 'FRAMEBUFFER', COLOR_BUFFER_BIT: 'COLOR_BUFFER_BIT',
            viewport() {}, clearColor() {}, clear() {},
            bindFramebuffer(target, framebuffer) { boundMaskFramebuffer = framebuffer; },
        },
        _model: drawCore,
        getModel: () => drawCore,
        _sortedDrawableIndexList: [],
        setClippingContextBufferForMask(context) { maskContext = context; },
        setClippingContextBufferForDraw(context) { drawContext = context; },
        drawMesh(drawable) {
            events.push({ drawable, mask: maskContext, draw: drawContext,
                framebuffer: boundMaskFramebuffer });
        },
    });
    doDrawModel.call(hpRenderer);
    const drawMapping = huge.manager.getClippingContextListForDraw();
    let pendingMasks = [];
    let clippedDraws = 0;
    for (const event of events) {
        if (event.mask) {
            assert.equal(event.framebuffer, maskFramebuffer);
            pendingMasks.push(event);
            continue;
        }
        const context = drawMapping[event.drawable];
        assert.equal(event.draw, context);
        if (context) {
            clippedDraws++;
            assert.ok(pendingMasks.every(mask => mask.mask === context));
            assert.deepEqual(pendingMasks.map(mask => mask.drawable),
                context._clippingIdList.slice(0, context._clippingIdCount));
        } else {
            assert.equal(pendingMasks.length, 0);
        }
        pendingMasks = [];
    }
    assert.equal(clippedDraws, hugeCount * 2);

    // setClippingMaskBufferSize() swaps in a brand-new manager. Run the shipped
    // renderer method and require the fixes and GL context to follow it.
    const rebuildGl = { name: 'rebuild-gl' };
    const rebuild = await configure(95, {
        gl: rebuildGl,
        setClippingMaskBufferSize: new Function('fe',
            `return function ${bundledMethodSource('setClippingMaskBufferSize', 'isUsingMasking')};`)(
            ClippingManager),
    });
    rebuild.renderer._model = Object.assign({ isUsingMasking: () => true }, rebuild.core);
    rebuild.renderer.getModel = () => rebuild.renderer._model;
    rebuild.renderer.setClippingMaskBufferSize(512);
    const rebuiltManager = rebuild.renderer._clippingManager;
    assert.notEqual(rebuiltManager, rebuild.manager);
    assert.equal(rebuiltManager.getClippingMaskBufferSize(), 512);
    assert.equal(rebuiltManager.gl, rebuildGl);
    assert.equal(rebuiltManager.__nekoClippingFixApplied, true);
    assert.equal(rebuiltManager.getRenderTextureCount(), 3);
    assertValidLayout(rebuiltManager, 95);

    // Regression for Number.MIN_VALUE: an all-negative drawable must not be
    // expanded to the model-space origin.
    const negative = await configure(1);
    const negativeContext = negative.manager._clippingContextListForMask[0];
    negative.manager.calcClippedDrawTotalBounds({
        getDrawableVertexCount: () => 2,
        getDrawableVertices: () => new Float32Array([-4, -3, -2, -1]),
    }, negativeContext);
    assert.deepEqual(
        [negativeContext._allClippedDrawRect.x, negativeContext._allClippedDrawRect.y,
            negativeContext._allClippedDrawRect.width, negativeContext._allClippedDrawRect.height],
        [-4, -3, 2, 2]
    );

    // Resource allocation follows the computed count and remains lazy. Use 95
    // to cover the former three-texture layout hole at the framebuffer layer.
    const allocation = await configure(95);
    const textures = [];
    const framebuffers = [];
    const attachments = [];
    let boundFramebuffer = null;
    allocation.manager.setGL({
        createTexture() { const texture = {}; textures.push(texture); return texture; },
        createFramebuffer() {
            const framebuffer = {};
            framebuffers.push(framebuffer);
            return framebuffer;
        },
        bindFramebuffer(target, framebuffer) { boundFramebuffer = framebuffer; },
        framebufferTexture2D(target, attachment, type, texture) {
            attachments.push([boundFramebuffer, texture]);
        },
        bindTexture() {},
        texImage2D() {},
        texParameteri() {},
    });
    assert.equal(allocation.manager._maskTexture, undefined);
    const allocated = allocation.manager.getMaskRenderTexture();
    assert.equal(allocated.length, 3);
    assert.equal(textures.length, 3);
    assert.equal(framebuffers.length, 3);
    assert.equal(attachments.length, 3);
    attachments.forEach(([framebuffer, texture], index) => {
        assert.equal(framebuffer, framebuffers[index]);
        assert.equal(texture, textures[index]);
    });
    assert.equal(allocation.manager.getMaskRenderTexture(), allocated);
    assert.equal(textures.length, 3, 'reuse allocated mask textures');

    // The bundled profile overwrites the saved ARRAY_BUFFER value with the
    // ELEMENT_ARRAY_BUFFER value. Patch the shipped class, not a replica, so a
    // vendor change that stops matching the detector fails here.
    const Profile = new Function('Ot', bundledClassSource('Me', 'class Ce{') + '; return Me;')(
        () => {});
    const arrayBinding = { name: 'array' };
    const elementBinding = { name: 'element' };
    const bound = {};
    const gl = new Proxy({}, {
        get(target, prop) {
            if (prop === 'getParameter') {
                return parameter => ({
                    ARRAY_BUFFER_BINDING: arrayBinding,
                    ELEMENT_ARRAY_BUFFER_BINDING: elementBinding,
                    COLOR_WRITEMASK: [true, true, true, true],
                })[parameter] ?? null;
            }
            if (prop === 'bindBuffer') return (bufferTarget, buffer) => { bound[bufferTarget] = buffer; };
            if (typeof prop === 'string' && /^[A-Z0-9_]+$/.test(prop)) return prop;
            return () => null;
        },
    });
    const profile = new Profile();
    profile.setGl(gl);
    await configure(1, { _rendererProfile: profile });
    assert.equal(profile.__nekoBufferBindingFixApplied, true);
    profile.save();
    assert.equal(profile._lastArrayBufferBinding, arrayBinding);
    assert.equal(profile._lastElementArrayBufferBinding, elementBinding);
    profile.restore();
    assert.equal(bound.ARRAY_BUFFER, arrayBinding);
    assert.equal(bound.ELEMENT_ARRAY_BUFFER, elementBinding);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = run_node_stdin(
        node, script, cwd=PROJECT_ROOT, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
