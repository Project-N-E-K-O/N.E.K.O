// Issue #3474: frames handed to the model must have N.E.K.O's own chat history
// and reply-option regions painted over, with the composer left visible.
//
// All image data here is synthetic: the fake canvas encodes what was drawn
// (source + filled rects) as JSON inside the data URL, so assertions read back
// exactly which pixels were covered.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repo = path.resolve(__dirname, '..', '..');
const maskSource = fs.readFileSync(path.join(repo, 'static/app/app-capture-mask.js'), 'utf8');
const proactiveSource = fs.readFileSync(path.join(repo, 'static/app/app-proactive.js'), 'utf8');
const screenSource = fs.readFileSync(path.join(repo, 'static/app/app-screen.js'), 'utf8');

const RAW_FRAME = 'data:image/jpeg;base64,UkFXRlJBTUU=';

// ---------------------------------------------------------------- fakes

function encodeCanvas(mime, record) {
    return 'data:' + mime + ';base64,' + Buffer.from(JSON.stringify(record)).toString('base64');
}

function decodeFrame(dataUrl) {
    const comma = dataUrl.indexOf(',');
    try {
        return JSON.parse(Buffer.from(dataUrl.slice(comma + 1), 'base64').toString('utf8'));
    } catch (_) {
        return null;
    }
}

function makeCanvas() {
    const canvas = {
        width: 0,
        height: 0,
        drawn: null,
        fills: [],
        getContext() {
            return {
                fillStyle: '',
                drawImage(src) { canvas.drawn = src && (src.src || src.id || 'video'); },
                fillRect(x, y, w, h) { canvas.fills.push([x, y, w, h, this.fillStyle]); },
                getImageData: () => ({ data: [200, 200, 200, 255] }),
            };
        },
        toDataURL(mime) {
            const type = mime || 'image/png';
            return encodeCanvas(type, { src: canvas.drawn, w: canvas.width, h: canvas.height, fills: canvas.fills });
        },
    };
    return canvas;
}

function makeElement(rect, style, parent) {
    return {
        nodeType: 1,
        rect,
        style: Object.assign({ display: 'block', visibility: 'visible', opacity: '1' }, style || {}),
        parentElement: parent || null,
        getBoundingClientRect() {
            const r = this.rect;
            return { left: r.x, top: r.y, width: r.w, height: r.h };
        },
    };
}

/**
 * A browser-ish global for the mask module.
 * opts.elements: marked elements; opts.geom: window/screen geometry;
 * opts.multiWindow: desktop shell multi-window mode; opts.shell: C2 stub.
 */
function makeWindow(opts) {
    opts = opts || {};
    const geom = Object.assign({
        innerWidth: 1000, innerHeight: 800, outerWidth: 1000, outerHeight: 800,
        screenX: 0, screenY: 0, screenWidth: 2000, screenHeight: 1000, isExtended: false,
        devicePixelRatio: 1,
    }, opts.geom || {});
    const imageSizes = Object.assign({}, opts.imageSizes || {});
    // focused: document.hasFocus() (browser window in front); default true.
    const state = { elements: opts.elements || [], focused: opts.focused !== false };
    const document = {
        visibilityState: 'visible',
        hasFocus() { return state.focused; },
        documentElement: makeElement({ x: 0, y: 0, w: 1, h: 1 }),
        querySelectorAll(selector) {
            assert.equal(selector, '[data-neko-capture-mask]');
            return state.elements;
        },
        addEventListener() {},
        createElement(tag) {
            assert.equal(tag, 'canvas');
            return makeCanvas();
        },
    };
    class FakeImage {
        constructor() { this.onload = null; this.onerror = null; this._src = ''; }
        set src(value) {
            this._src = value;
            const size = imageSizes[value] || opts.defaultImageSize || [1920, 1080];
            setImmediate(() => {
                if (value.startsWith('data:bad')) { if (this.onerror) this.onerror(); return; }
                this.naturalWidth = size[0];
                this.naturalHeight = size[1];
                if (this.onload) this.onload();
            });
        }
        get src() { return this._src; }
    }
    const w = {
        console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
        document,
        Image: FakeImage,
        innerWidth: geom.innerWidth,
        innerHeight: geom.innerHeight,
        outerWidth: geom.outerWidth,
        outerHeight: geom.outerHeight,
        screenX: geom.screenX,
        screenY: geom.screenY,
        devicePixelRatio: geom.devicePixelRatio,
        screen: { width: geom.screenWidth, height: geom.screenHeight, isExtended: geom.isExtended },
        getComputedStyle(el) { return el.style; },
        addEventListener() {},
        setTimeout, clearTimeout, setInterval() { return 0; }, clearInterval() {},
        setImmediate,
        Promise,
    };
    if (opts.multiWindow) w.__NEKO_MULTI_WINDOW__ = true;
    // Capture Handle is on by default (Chromium); captureHandle: false = no API.
    if (opts.captureHandle !== false) {
        w.location = { origin: 'http://127.0.0.1:48911' };
        w.navigator = { mediaDevices: { setCaptureHandleConfig: (cfg) => { w.__captureHandleConfig = cfg; } } };
    }
    // Chat-window preload exposes C1 before page scripts run.
    if (opts.nekoCaptureMask) w.nekoCaptureMask = opts.nekoCaptureMask;
    if (opts.shell) w.electronScreen = opts.shell;
    w.window = w;
    vm.createContext(w);
    vm.runInContext(maskSource, w);
    return { w, state };
}

function approxRects(actual, expected) {
    assert.equal(actual.length, expected.length);
    actual.forEach((r, i) => {
        for (const k of ['x', 'y', 'width', 'height']) {
            assert.ok(Math.abs(r[k] - expected[i][k]) < 1e-9, `${k}: ${r[k]} vs ${expected[i][k]}`);
        }
    });
}

function fillsOf(dataUrl) {
    const rec = decodeFrame(dataUrl);
    return rec ? rec.fills.map((f) => f.slice(0, 4)) : null;
}

// The three regions of a full-style chat window placed at viewport (600, 100):
// history list, Gal option slot, composer (composer is NOT marked).
function chatElements(opts) {
    opts = opts || {};
    const els = [];
    if (!opts.collapsed) {
        els.push(makeElement({ x: 600, y: 100, w: 300, h: 400 }));            // history
        if (opts.galOpen) els.push(makeElement({ x: 600, y: 520, w: 300, h: 100 })); // options
        if (opts.galClosing) els.push(makeElement({ x: 600, y: 520, w: 300, h: 0 }));  // max-height:0
    } else {
        // Collapsed: the marked elements are still in the DOM under a display:none root.
        const root = makeElement({ x: 0, y: 0, w: 0, h: 0 }, { display: 'none' });
        els.push(makeElement({ x: 0, y: 0, w: 0, h: 0 }, null, root));
    }
    return els;
}

// ---------------------------------------------------------------- collection

test('collect: visible history + open option slot are padded by 8px; composer is not collected', () => {
    const { w } = makeWindow({ elements: chatElements({ galOpen: true }) });
    const rects = w.appCaptureMask.collectLocalMaskRects(w.document, w);
    assert.deepEqual(JSON.parse(JSON.stringify(rects)), [
        { x: 592, y: 92, width: 316, height: 416 },
        { x: 592, y: 512, width: 316, height: 116 },
    ]);
});

test('collect: Gal mode off (no option slot) only yields the history region', () => {
    const on = makeWindow({ elements: chatElements({ galOpen: true }) });
    const off = makeWindow({ elements: chatElements({ galOpen: false }) });
    assert.equal(on.w.appCaptureMask.collectLocalMaskRects(on.w.document, on.w).length, 2);
    assert.equal(off.w.appCaptureMask.collectLocalMaskRects(off.w.document, off.w).length, 1);
});

test('collect: a closed option slot (max-height 0) yields nothing', () => {
    const { w } = makeWindow({ elements: chatElements({ galClosing: true }) });
    assert.equal(w.appCaptureMask.collectLocalMaskRects(w.document, w).length, 1);
});

test('collect: collapsed chat (display:none ancestor) or transparent / hidden elements yield nothing', () => {
    const collapsed = makeWindow({ elements: chatElements({ collapsed: true }) });
    assert.equal(collapsed.w.appCaptureMask.collectLocalMaskRects(collapsed.w.document, collapsed.w).length, 0);

    const fadedRoot = makeElement({ x: 0, y: 0, w: 10, h: 10 }, { opacity: '0' });
    const faded = makeWindow({ elements: [makeElement({ x: 10, y: 10, w: 100, h: 100 }, null, fadedRoot)] });
    assert.equal(faded.w.appCaptureMask.collectLocalMaskRects(faded.w.document, faded.w).length, 0);

    const hidden = makeWindow({ elements: [makeElement({ x: 10, y: 10, w: 100, h: 100 }, { visibility: 'hidden' })] });
    assert.equal(hidden.w.appCaptureMask.collectLocalMaskRects(hidden.w.document, hidden.w).length, 0);

    const visible = makeWindow({ elements: [makeElement({ x: 10, y: 10, w: 100, h: 100 })] });
    assert.equal(visible.w.appCaptureMask.collectLocalMaskRects(visible.w.document, visible.w).length, 1);
});

test('collect: content scrolled out of a clipping container is not collected', () => {
    // History list viewport 100..500; one bubble inside, one scrolled above it,
    // one half visible at the bottom edge.
    const scroller = makeElement({ x: 600, y: 100, w: 300, h: 400 }, { overflowY: 'auto' });
    const inside = makeElement({ x: 610, y: 300, w: 200, h: 50 }, null, scroller);
    const above = makeElement({ x: 610, y: 20, w: 200, h: 50 }, null, scroller);
    const edge = makeElement({ x: 610, y: 480, w: 200, h: 60 }, null, scroller);
    const { w } = makeWindow({ elements: [inside, above, edge] });
    assert.deepEqual(JSON.parse(JSON.stringify(w.appCaptureMask.collectLocalMaskRects(w.document, w))), [
        { x: 602, y: 292, width: 216, height: 66 },
        { x: 602, y: 472, width: 216, height: 36 },
    ]);
    // Same layout without a clipping ancestor keeps all three.
    const plain = makeElement({ x: 600, y: 100, w: 300, h: 400 });
    const free = [makeElement({ x: 610, y: 300, w: 200, h: 50 }, null, plain),
        makeElement({ x: 610, y: 20, w: 200, h: 50 }, null, plain),
        makeElement({ x: 610, y: 480, w: 200, h: 60 }, null, plain)];
    const other = makeWindow({ elements: free });
    assert.equal(other.w.appCaptureMask.collectLocalMaskRects(other.w.document, other.w).length, 3);
});

test('collect: regions are clipped to the viewport', () => {
    const { w } = makeWindow({ elements: [makeElement({ x: -50, y: 700, w: 200, h: 300 })] });
    assert.deepEqual(JSON.parse(JSON.stringify(w.appCaptureMask.collectLocalMaskRects(w.document, w))), [
        { x: 0, y: 692, width: 158, height: 108 },
    ]);
});

// ---------------------------------------------------------------- mapping (browser)

test('map: viewport capture normalizes by the viewport', () => {
    const { w } = makeWindow();
    const out = w.appCaptureMask.mapViewportRectsToCapture([{ x: 100, y: 80, width: 200, height: 160 }], 'viewport',
        { innerWidth: 1000, innerHeight: 800 });
    assert.equal(out.known, true);
    approxRects(out.rects, [{ x: 0.1, y: 0.1, width: 0.2, height: 0.2 }]);
});

test('map: screen capture adds the window offset and browser chrome; devicePixelRatio does not matter', () => {
    const { w } = makeWindow();
    const geom = {
        innerWidth: 1000, innerHeight: 760, outerWidth: 1016, outerHeight: 800,
        screenX: 400, screenY: 100, screenWidth: 2000, screenHeight: 1000, multiDisplay: false,
    };
    // chromeLeft = 8, chromeTop = 800 - 760 - 8 = 32 -> viewport origin (408, 132)
    const out = w.appCaptureMask.mapViewportRectsToCapture([{ x: 92, y: 68, width: 500, height: 200 }], 'screen', geom);
    assert.equal(out.known, true);
    approxRects(out.rects, [{ x: 0.25, y: 0.2, width: 0.25, height: 0.2 }]);
});

test('map: screen capture with the window partly off the left edge is clipped to the image', () => {
    const { w } = makeWindow();
    const geom = {
        innerWidth: 1000, innerHeight: 800, outerWidth: 1000, outerHeight: 800,
        screenX: -300, screenY: 0, screenWidth: 2000, screenHeight: 1000, multiDisplay: false,
    };
    const out = w.appCaptureMask.mapViewportRectsToCapture([{ x: 100, y: 0, width: 400, height: 500 }], 'screen', geom);
    approxRects(out.rects, [{ x: 0, y: 0, width: 0.1, height: 0.5 }]);
});

test('map: several displays, an unknown window share, or no capture type -> position unknown', () => {
    const { w } = makeWindow();
    const rect = [{ x: 1, y: 1, width: 10, height: 10 }];
    const base = { innerWidth: 100, innerHeight: 100, outerWidth: 100, outerHeight: 100, screenWidth: 100, screenHeight: 100 };
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({ multiDisplay: true }, base)).known, false);
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({ multiDisplay: false }, base)).known, true);
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, null, base).known, false);
    // Nothing visible: known and empty, whatever the capture type.
    assert.deepEqual(JSON.parse(JSON.stringify(w.appCaptureMask.mapViewportRectsToCapture([], null, base))), { known: true, rects: [] });
});

// ---------------------------------------------------------------- painting

test('paint: normalized rects scale with the real image size (100% / 125% / 150% captures)', () => {
    const { w } = makeWindow();
    const rect = [{ x: 0.25, y: 0.2, width: 0.25, height: 0.2 }];
    for (const [width, height, expected] of [
        [1920, 1080, [480, 216, 480, 216]],
        [2400, 1350, [600, 270, 600, 270]],
        [2880, 1620, [720, 324, 720, 324]],
        [1280, 720, [320, 144, 320, 144]], // downscaled capture of the same screen
    ]) {
        const fills = [];
        w.appCaptureMask.paintMaskRects({ fillRect: (...a) => fills.push(a), set fillStyle(v) { this._f = v; } }, width, height, rect);
        assert.deepEqual(fills, [expected], `${width}x${height}`);
    }
});

test('paint: the image keeps its size and encoding', async () => {
    const { w } = makeWindow({
        elements: [makeElement({ x: 0, y: 0, w: 100, h: 100 })],
        geom: { innerWidth: 1000, innerHeight: 800 },
        imageSizes: { [RAW_FRAME]: [1280, 720], 'data:image/png;base64,UE5H': [640, 360] },
    });
    const jpeg = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', stream: ownTab(w) });
    assert.match(jpeg, /^data:image\/jpeg;/);
    assert.equal(decodeFrame(jpeg).w, 1280);
    assert.equal(decodeFrame(jpeg).h, 720);
    const png = await w.appCaptureMask.maskFrameForModel('data:image/png;base64,UE5H', { captureType: 'viewport', stream: ownTab(w) });
    assert.match(png, /^data:image\/png;/);
    assert.equal(decodeFrame(png).w, 640);
});

// ---------------------------------------------------------------- entry point (browser)

test('browser: visible chat on a single-display screen capture is painted over', async () => {
    const { w } = makeWindow({
        elements: chatElements({ galOpen: true }),
        geom: { innerWidth: 1000, innerHeight: 1000, outerWidth: 1000, outerHeight: 1000, screenX: 0, screenY: 0, screenWidth: 1000, screenHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [1000, 1000] },
    });
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' });
    assert.notEqual(out, RAW_FRAME);
    assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('browser: nothing visible -> the original frame is returned untouched', async () => {
    for (const elements of [[], chatElements({ collapsed: true })]) {
        const { w } = makeWindow({ elements });
        assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen' }), RAW_FRAME);
    }
});

test('browser: visible chat but unknown position (several displays) -> fallback policy sends it unmasked', async () => {
    const { w } = makeWindow({ elements: chatElements({ galOpen: true }), geom: { isExtended: true } });
    assert.equal(w.appCaptureMask.decideWhenGeometryUnavailable({}), 'send-unmasked');
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen' }), RAW_FRAME);
});

test('browser: the fallback decision is the single switch (drop -> null)', async () => {
    const { w } = makeWindow({ elements: chatElements({ galOpen: true }), geom: { isExtended: true } });
    // Simulate the policy being changed to "drop": the entry point honours it.
    const src = maskSource.replace("return 'send-unmasked';", "return 'drop';");
    vm.runInContext(src, w);
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen' }), null);
});

test('browser: a decode failure with known regions falls back through the same policy', async () => {
    const { w } = makeWindow({ elements: chatElements({ galOpen: true }) });
    assert.equal(await w.appCaptureMask.maskFrameForModel('data:bad,xx', { captureType: 'viewport', stream: ownTab(w) }), 'data:bad,xx');
});

test('browser: only what the samples before and after capture agree on is masked (when unsure, mask less)', async () => {
    const history = makeElement({ x: 100, y: 100, w: 100, h: 100 });
    const { w } = makeWindow({ elements: [history], geom: { innerWidth: 1000, innerHeight: 1000 }, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
    const stream = ownTab(w);
    // Unchanged during capture: masked as usual.
    let session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    let out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream }, session);
    assert.deepEqual(fillsOf(out), [[92, 92, 116, 116]]);
    // Moved during capture: the frame may show either position, neither is certain -> nothing painted.
    session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    history.rect = { x: 500, y: 500, w: 100, h: 100 };
    out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream }, session);
    assert.equal(out, RAW_FRAME);
    // Moved a little: only the overlap of the two positions is painted.
    session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    history.rect = { x: 520, y: 500, w: 100, h: 100 };
    out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream }, session);
    assert.deepEqual(fillsOf(out), [[512, 492, 96, 116]]);
    // A region that appeared during capture is not masked; the one present throughout is.
    const option = makeElement({ x: 300, y: 300, w: 50, h: 50 });
    session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    w.document.querySelectorAll = () => [history, option];
    out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream }, session);
    assert.deepEqual(fillsOf(out), [[512, 492, 116, 116]]);
});

// ---------------------------------------------------------------- desktop shell (C2 stub)

function shellStub(behaviour) {
    const calls = [];
    return {
        calls,
        getSelfUiCaptureMask(request) {
            calls.push(JSON.parse(JSON.stringify(request)));
            return behaviour(request, calls.length);
        },
    };
}

test('shell: ok:true rects are painted; request carries version, sourceId and surface', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.25, width: 0.25, height: 0.5 }] }));
    const { w } = makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1600, 900] } });
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: 'screen:2:0', via: 'native' });
    assert.deepEqual(fillsOf(out), [[800, 225, 400, 450]]);
    assert.deepEqual(shell.calls, [{ version: 1, sourceId: 'screen:2:0', surface: 'source' }]);
});

test('shell: backend desktop screenshots ask for the backend surface', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [] }));
    const { w } = makeWindow({ multiWindow: true, shell });
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: null, via: 'backend' }), RAW_FRAME);
    assert.deepEqual(shell.calls, [{ version: 1, sourceId: null, surface: 'backend' }]);
});

test('shell: ok:true with no rects -> untouched; ok:false / missing API / timeout -> fallback (sent unmasked)', async () => {
    const empty = makeWindow({ multiWindow: true, shell: shellStub(async () => ({ ok: true, rects: [] })) });
    assert.equal(await empty.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: 'screen:0:0' }), RAW_FRAME);

    const failed = makeWindow({ multiWindow: true, shell: shellStub(async () => ({ ok: false })) });
    assert.equal(await failed.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: 'screen:0:0' }), RAW_FRAME);

    // Older shell: no getSelfUiCaptureMask. The Pet window's own DOM has no chat, so
    // the local DOM must not be mistaken for "nothing to mask": geometry is unknown.
    const legacy = makeWindow({ multiWindow: true, shell: {}, elements: [] });
    let decided = 0;
    const original = legacy.w.appCaptureMask.decideWhenGeometryUnavailable;
    assert.equal(original({}), 'send-unmasked');
    const src = maskSource.replace("return 'send-unmasked';", "globalThis.__decided = (globalThis.__decided || 0) + 1; return 'send-unmasked';");
    vm.runInContext(src, legacy.w);
    assert.equal(await legacy.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen' }), RAW_FRAME);
    decided = legacy.w.__decided;
    assert.equal(decided, 1, 'missing shell API goes through the fallback policy');

    const hung = makeWindow({ multiWindow: true, shell: shellStub(() => new Promise(() => {})) });
    const started = Date.now();
    assert.equal(await hung.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: 'screen:0:0' }), RAW_FRAME);
    assert.ok(Date.now() - started < 3000, 'a hung shell query does not hold the frame forever');
});

test('shell: pre- and post-capture answers for the same target are intersected; a different target uses post only', async () => {
    const A = { ok: true, rects: [{ x: 0, y: 0, width: 0.2, height: 0.2 }] };
    const B = { ok: true, rects: [{ x: 0.1, y: 0.1, width: 0.2, height: 0.2 }] };
    const run = async (answers, hint, ctx) => {
        const shell = shellStub(async (_req, n) => answers[Math.min(n, answers.length) - 1]);
        const { w } = makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
        const session = w.appCaptureMask.beginMaskSession(hint);
        return fillsOf(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx, session));
    };
    const same = { sourceId: 'screen:1:0', via: 'native', captureType: 'screen' };
    assert.deepEqual(await run([A, A], same, same), [[0, 0, 200, 200]], 'unchanged -> masked');
    assert.deepEqual(await run([A, B], same, same), [[100, 100, 100, 100]], 'moved -> only the overlap');
    assert.equal(await run([A, { ok: false }], same, same), null, 'one side unknown -> unknown (sent unmasked)');
    assert.equal(await run([{ ok: false }, A], same, same), null, 'one side unknown -> unknown (sent unmasked)');
    // A pre-capture answer for another target is not comparable: post only.
    assert.deepEqual(await run([A, B], { sourceId: 'screen:1:0', via: 'stream' }, { sourceId: null, via: 'backend', captureType: 'screen' }), [[100, 100, 200, 200]]);
});

// ---------------------------------------------------------------- C1 reporter

test('reporter: chat windows report viewport-relative regions, only when they change', async () => {
    const reports = [];
    const els = chatElements({ galOpen: true });
    const { w } = makeWindow({ elements: els });
    w.nekoCaptureMask = { report: (p) => { reports.push(JSON.parse(JSON.stringify(p))); } };
    const reporter = w.appCaptureMask.startShellReporter(w, w.document);
    reporter.flush();
    reporter.flush();
    assert.equal(reports.length, 1, 'unchanged payload is not re-sent');
    assert.deepEqual(reports[0], {
        version: 1, visible: true, viewport: { width: 1000, height: 800 },
        rects: [{ x: 592, y: 92, width: 316, height: 416 }, { x: 592, y: 512, width: 316, height: 116 }],
    });
    els.pop(); // Gal mode turned off
    reporter.flush();
    assert.equal(reports.length, 2);
    assert.equal(reports[1].rects.length, 1);
});

// ---------------------------------------------------------------- send paths

function bootProactive(opts) {
    const { w, state } = makeWindow(Object.assign({ imageSizes: { [RAW_FRAME]: [1000, 800] } }, opts));
    // The cached stream is a share of this tab.
    const track = { readyState: 'live', getCaptureHandle: () => ({ handle: w.appCaptureMask.OWN_CAPTURE_HANDLE }) };
    const S = {
        isRecording: true, proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true,
        screenCaptureStream: { active: true, getVideoTracks: () => [track], getTracks: () => [track] },
        selectedScreenSourceId: null,
    };
    w.appState = S;
    w.appConst = {};
    w.localStorage = { getItem() { return null; } };
    w.detectScreenshotCaptureType = () => 'viewport';
    w.appScreen = {
        captureFrameFromStream: async () => {
            if (opts && opts.duringCapture) opts.duringCapture(state);
            return { dataUrl: RAW_FRAME };
        },
        scheduleScreenCaptureIdleCheck() {},
    };
    w.document.getElementById = () => null;
    vm.runInContext(proactiveSource, w);
    return { w, S, state };
}

test('single-shot (proactive / screenshot_request): the returned frame is already masked', async () => {
    const { w } = bootProactive({ elements: chatElements({ galOpen: true }) });
    const shot = await w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.notEqual(shot.dataUrl, RAW_FRAME, 'frame handed to the sender must be masked');
    assert.deepEqual(fillsOf(shot.dataUrl), [[592, 92, 316, 416], [592, 512, 316, 116]]);
    assert.equal(shot.via, 'stream');
});

test('single-shot: chat moved during capture -> not masked in that frame (when unsure, mask less)', async () => {
    const { w } = bootProactive({
        elements: [makeElement({ x: 10, y: 10, w: 50, h: 50 })],
        duringCapture(state) { state.elements[0].rect = { x: 400, y: 400, w: 50, h: 50 }; },
    });
    const shot = await w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.dataUrl, RAW_FRAME);
});

test('single-shot: Gal off and chat collapsed are paired with the masked cases', async () => {
    const galOff = bootProactive({ elements: chatElements({ galOpen: false }) });
    const shotOff = await galOff.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.deepEqual(fillsOf(shotOff.dataUrl), [[592, 92, 316, 416]]);

    const collapsed = bootProactive({ elements: chatElements({ collapsed: true }) });
    const shotCollapsed = await collapsed.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shotCollapsed.dataUrl, RAW_FRAME);
});

function bootScreen(opts) {
    const { w } = makeWindow(Object.assign({ imageSizes: {} , defaultImageSize: [1000, 800] }, opts));
    const sent = [];
    const intervals = [];
    const S = {
        isRecording: true, voiceChatActive: true,
        socket: { readyState: 1, send: (m) => sent.push(JSON.parse(m)) },
        screenCaptureStream: null, selectedScreenSourceId: null,
    };
    const noop = () => {};
    const baseCreate = w.document.createElement;
    w.document.createElement = (tag) => {
        if (tag === 'video') {
            return { play: () => Promise.resolve(), videoWidth: 1000, videoHeight: 800, srcObject: null, id: 'video' };
        }
        return baseCreate(tag);
    };
    w.document.getElementById = () => null;
    w.document.querySelector = () => null;
    w.document.body = makeElement({ x: 0, y: 0, w: 1, h: 1 });
    Object.assign(w, {
        location: { pathname: '/', origin: 'http://x', search: '' },
        appState: S, appConst: { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 },
        appUtils: { isMobile: () => false }, safeT: (k, f) => f, t: (k) => k,
        navigator: { userAgent: 'node', mediaDevices: {} },
        WebSocket: { OPEN: 1 },
        localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        removeEventListener: noop, dispatchEvent: noop, CustomEvent: function () {},
        // Unref'd so the idle-check timer app-screen.js arms cannot keep node alive.
        setTimeout(fn, ms) { const t = setTimeout(fn, ms); if (t && t.unref) t.unref(); return t; },
        setInterval(fn) { intervals.push(fn); return intervals.length; },
        clearInterval: noop,
        showStatusToast: noop,
    });
    vm.runInContext(screenSource, w);
    return { w, S, sent, intervals };
}

test('screen share frames (stream_data) are masked before they are sent', async () => {
    const { w, S, sent, intervals } = bootScreen({
        elements: chatElements({ galOpen: true }),
        geom: { innerWidth: 1000, innerHeight: 800, screenWidth: 1000, screenHeight: 800 },
    });
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    S.screenCaptureStream = stream;
    w.startScreenVideoStreaming(stream, 'screen');
    for (let i = 0; i < 10 && intervals.length === 0; i++) await new Promise((r) => setImmediate(r));
    assert.equal(intervals.length, 1, 'frame sender installed');
    await intervals[0]();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].action, 'stream_data');
    const rec = decodeFrame(sent[0].data);
    assert.ok(rec && rec.fills.length === 2, 'stream frame must be masked');
    assert.deepEqual(rec.fills.map((f) => f.slice(0, 4)), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('buildMaskedStreamDataMessage: camera frames pass through untouched', async () => {
    const { w } = bootScreen({ elements: chatElements({ galOpen: true }) });
    const msg = await w.appScreen.buildMaskedStreamDataMessage(RAW_FRAME, 'camera');
    assert.equal(msg.data, RAW_FRAME);
});

test('shell: production hints carry no captureType and the pre-capture answer is still used', async () => {
    const answers = [
        { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] },
        { ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.1, height: 0.1 }] },
    ];
    const shell = shellStub(async (_req, n) => answers[n - 1] || answers[1]);
    const { w } = makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
    // Callers only know sourceId / via before capture; captureType is decided afterwards.
    // The pre-capture answer is compared (no overlap -> nothing certain), not discarded.
    const session = w.appCaptureMask.beginMaskSession({ sourceId: 'screen:1:0', via: 'stream' });
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { sourceId: 'screen:1:0', via: 'stream', captureType: 'screen' }, session);
    assert.equal(out, RAW_FRAME);
});

test('voice-time vision heartbeat: the stream_data frame is masked before it is sent', async () => {
    for (const revokeDuringMask of [false, true]) {
        let resolveShell;
        const shell = shellStub(async (_req, n) => {
            if (n === 2 && revokeDuringMask) {
                // Consent is withdrawn while the post-capture mask query is pending.
                await new Promise((r) => { resolveShell = r; setImmediate(() => { env.S.proactiveVisionEnabled = false; r(); }); });
            }
            return { ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] };
        });
        const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
        const { w, S, sent } = env;
        Object.assign(S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
        const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
        const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
        w.appScreen.acquireOrReuseCachedStream = async () => stream;
        w.appScreen.captureFrameFromStream = async () => ({ dataUrl: RAW_FRAME });
        w.document.getElementById = () => null;
        vm.runInContext(proactiveSource, w);
        await w.appProactive.sendOneProactiveVisionFrame();
        if (revokeDuringMask) {
            assert.equal(sent.length, 0, 'a frame whose consent was revoked during masking is not sent');
            continue;
        }
        assert.equal(sent.length, 1);
        assert.equal(sent[0].action, 'stream_data');
        assert.deepEqual(fillsOf(sent[0].data), [[500, 400, 250, 200]], 'heartbeat frame must be masked');
        assert.equal(shell.calls[0].surface, 'source');
        assert.equal(shell.calls.length, 2, 'regions sampled before and after capture');
    }
});

test('native screen share: the stream_data frame is masked before it is sent', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }] }));
    const { w, sent } = bootScreen({ multiWindow: true, shell, defaultImageSize: [1200, 800] });
    w.captureDesktopSourceWithTimeout = async () => ({ success: true, dataUrl: RAW_FRAME });
    const provider = { nativeFrameCapture: true, captureSourceAsDataUrl() {} };
    await w.appScreen.startNativeScreenStreaming(provider, 'screen:3:0', 'screen');
    assert.equal(sent.length, 1, 'first native frame sent');
    assert.deepEqual(fillsOf(sent[0].data), [[300, 200, 600, 400]], 'native frame must be masked');
    assert.deepEqual(shell.calls[0], { version: 1, sourceId: 'screen:3:0', surface: 'source' });
});

// ---------------------------------------------------------------- C1 payload limits (shell validation)

function assertValidShellPayload(payload) {
    assert.equal(payload.version, 1);
    assert.ok(payload.viewport.width > 0 && payload.viewport.height > 0);
    assert.ok(payload.rects.length <= 16, `at most 16 rects, got ${payload.rects.length}`);
    for (const r of payload.rects) {
        for (const k of ['x', 'y', 'width', 'height']) {
            assert.ok(Number.isFinite(r[k]) && r[k] >= 0, `${k}=${r[k]} must be finite and >= 0`);
        }
        assert.ok(r.width > 0 && r.height > 0);
        assert.ok(r.x + r.width <= payload.viewport.width + 1e-9);
        assert.ok(r.y + r.height <= payload.viewport.height + 1e-9);
    }
}

test('reporter payload: regions crossing every viewport edge are clipped to non-negative, in-viewport rects', () => {
    const { w } = makeWindow({
        elements: [
            makeElement({ x: -40, y: -30, w: 100, h: 100 }),     // top-left corner
            makeElement({ x: 950, y: 750, w: 200, h: 200 }),     // bottom-right corner
            makeElement({ x: -500, y: 100, w: 400, h: 50 }),     // fully off the left edge
            makeElement({ x: 0, y: 0, w: 1000, h: 800 }),        // exactly the viewport
            makeElement({ x: 2, y: 3, w: 4, h: 5 }),             // padding would go negative
        ],
    });
    const payload = w.appCaptureMask.buildReportPayload(w.document, w);
    assertValidShellPayload(payload);
    assert.equal(payload.rects.length, 4, 'the fully off-screen element is dropped');
    assert.deepEqual(JSON.parse(JSON.stringify(payload.rects[0])), { x: 0, y: 0, width: 68, height: 78 });
    assert.deepEqual(JSON.parse(JSON.stringify(payload.rects[1])), { x: 942, y: 742, width: 58, height: 58 });
});

test('reporter payload: more than 16 regions are merged to at most 16 without uncovering any of them', () => {
    const els = [];
    for (let i = 0; i < 40; i++) els.push(makeElement({ x: 600 + (i % 2) * 10, y: 5 + i * 19, w: 250, h: 14 }));
    const { w } = makeWindow({ elements: els });
    const raw = w.appCaptureMask.collectLocalMaskRects(w.document, w);
    assert.equal(raw.length, 40);
    const payload = w.appCaptureMask.buildReportPayload(w.document, w);
    assertValidShellPayload(payload);
    assert.ok(payload.rects.length <= 16);
    for (const r of raw) {
        assert.ok(payload.rects.some((m) => m.x <= r.x + 1e-9 && m.y <= r.y + 1e-9
            && m.x + m.width >= r.x + r.width - 1e-9 && m.y + m.height >= r.y + r.height - 1e-9),
        'every original region is still covered');
    }
    // 16 or fewer are passed through untouched.
    const few = [{ x: 1, y: 1, width: 2, height: 2 }, { x: 10, y: 10, width: 2, height: 2 }];
    assert.deepEqual(JSON.parse(JSON.stringify(w.appCaptureMask.limitRectCount(few, 16))), few);
});

test('reporter: hidden window keeps reporting its layout; the browser path still treats a hidden tab as off screen', () => {
    const { w } = makeWindow({ elements: chatElements({ galOpen: true }) });
    w.document.visibilityState = 'hidden';
    const payload = w.appCaptureMask.buildReportPayload(w.document, w);
    // The shell drops the rects of a visible:false report and decides by itself
    // whether the window is shown, so regions are reported with visible:true.
    assert.equal(payload.visible, true);
    assert.equal(payload.rects.length, 2, 'rects reported while hidden so the shell has them on restore');
    assert.equal(w.appCaptureMask.collectLocalMaskRects(w.document, w).length, 0);
});

test('reporter: visible follows the regions (nothing to mask -> visible:false, no rects)', () => {
    for (const [elements, visible, count] of [
        [chatElements({ galOpen: true }), true, 2],
        [chatElements({ collapsed: true }), false, 0],
        [[], false, 0],
    ]) {
        const { w } = makeWindow({ elements });
        const payload = w.appCaptureMask.buildReportPayload(w.document, w);
        assert.equal(payload.visible, visible);
        assert.equal(payload.rects.length, count);
    }
});

test('reporter: every fresh page (reload, page after a renderer crash) reports on its own load', async () => {
    // Each page load runs app-capture-mask.js in a new document; the shell has
    // dropped the previous report and needs one from the new page.
    const reports = [];
    const shellC1 = { report: (p) => { reports.push(JSON.parse(JSON.stringify(p))); } };
    for (const [label, elements] of [['first load', []], ['reload', chatElements({ galOpen: true })], ['after crash', []]]) {
        const before = reports.length;
        makeWindow({ elements, nekoCaptureMask: shellC1 });
        await new Promise((r) => setTimeout(r, 300));
        assert.equal(reports.length, before + 1, `${label}: one report after load, without any DOM change`);
    }
    assert.deepEqual(reports.map((p) => p.visible), [false, true, false]);
    assert.deepEqual(reports[0], { version: 1, visible: false, viewport: { width: 1000, height: 800 }, rects: [] });
});

test('reporter: reports once on start (new page after navigation) and again on resize', async () => {
    const reports = [];
    const listeners = {};
    const { w } = makeWindow({ elements: [] });
    w.addEventListener = (type, fn) => { listeners[type] = fn; };
    w.nekoCaptureMask = { report: (p) => { reports.push(JSON.parse(JSON.stringify(p))); } };
    w.appCaptureMask.startShellReporter(w, w.document);
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(reports.length, 1, 'initial report even before the chat has mounted');
    assert.deepEqual(reports[0].rects, []);
    assert.equal(typeof listeners.resize, 'function');
    w.innerWidth = 900;
    listeners.resize();
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(reports.length, 2, 'viewport change is reported');
    assert.equal(reports[1].viewport.width, 900);
});


// ---------------------------------------------------------------- sourceId per single-shot entry

test('single-shot entries pass the source they actually captured to the shell; backend passes null + backend surface', async () => {
    const answer = async () => ({ ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] });

    // Cached stream bound to a selected desktop source.
    const cachedShell = shellStub(answer);
    const cached = bootProactive({ multiWindow: true, shell: cachedShell });
    cached.S.selectedScreenSourceId = 'screen:7:0';
    const cachedShot = await cached.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(cachedShot.via, 'stream');
    assert.deepEqual(cachedShell.calls.map((c) => c.sourceId), ['screen:7:0', 'screen:7:0']);

    // Native snapshot of the selected source (no cached stream).
    const nativeShell = shellStub(answer);
    const native = bootProactive({ multiWindow: true, shell: nativeShell });
    native.S.screenCaptureStream = null;
    native.S.selectedScreenSourceId = 'screen:5:0';
    native.w.getDesktopCaptureProvider = () => ({ captureSourceAsDataUrl() {} });
    native.w.captureDesktopSourceWithTimeout = async (_p, _m, id) => {
        assert.equal(id, 'screen:5:0');
        return { success: true, dataUrl: RAW_FRAME };
    };
    const nativeShot = await native.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(nativeShot.via, 'native');
    assert.deepEqual(nativeShell.calls.map((c) => [c.sourceId, c.surface]), [['screen:5:0', 'source'], ['screen:5:0', 'source']]);
    assert.deepEqual(fillsOf(nativeShot.dataUrl), [[0, 0, 100, 80]]);

    // Backend desktop screenshot: the source is genuinely unknown.
    const backendShell = shellStub(answer);
    const backend = bootProactive({ multiWindow: true, shell: backendShell });
    backend.S.screenCaptureStream = null;
    backend.w.appScreen.acquireOrReuseCachedStream = async () => null;
    backend.w.appScreen.fetchBackendScreenshot = async () => ({ dataUrl: RAW_FRAME });
    const backendShot = await backend.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(backendShot.via, 'backend');
    assert.deepEqual(backendShell.calls[backendShell.calls.length - 1], { version: 1, sourceId: null, surface: 'backend' });
});

// ---------------------------------------------------------------- review round 1

test('browser: window moved during capture -> each sample keeps its own position; no overlap, nothing painted', async () => {
    // Whole-screen share; the browser window moves from x=0 to x=500 between
    // the pre-capture sample and the post-capture sample (chat unchanged in the page).
    const history = makeElement({ x: 100, y: 100, w: 100, h: 100 });
    const { w } = makeWindow({
        elements: [history],
        geom: { innerWidth: 1000, innerHeight: 800, outerWidth: 1000, outerHeight: 800, screenX: 0, screenY: 0, screenWidth: 2000, screenHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [2000, 1000] },
    });
    const session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    w.screenX = 500;
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session), RAW_FRAME);
    // Moved only a little: the overlap is painted.
    const session2 = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    w.screenX = 540;
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session2);
    assert.deepEqual(fillsOf(out), [[632, 92, 76, 116]]);
});

test('single-shot: a remembered window that changes while the frame is being masked is declined', async () => {
    let current = true;
    const shell = shellStub(async (_req, n) => {
        if (n === 2) current = false; // the user switches windows during the post-capture query
        return { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] };
    });
    const env = bootProactive({ multiWindow: true, shell });
    env.S.selectedScreenSourceId = 'window:42:0';
    env.w.prepareRememberedWindowCapture = async () => ({ required: true, allowed: true, isCurrent: () => current });
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.dataUrl, null);
    assert.equal(shot.declined, true);

    // Control: the same window throughout -> the masked frame is returned.
    current = true;
    const steadyShell = shellStub(async () => ({ ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] }));
    const steady = bootProactive({ multiWindow: true, shell: steadyShell });
    steady.S.selectedScreenSourceId = 'window:42:0';
    steady.w.prepareRememberedWindowCapture = async () => ({ required: true, allowed: true, isCurrent: () => true });
    const steadyShot = await steady.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.ok(steadyShot.dataUrl && fillsOf(steadyShot.dataUrl).length === 1);
});

test('screen share: a frame captured on one connection is not sent on a reconnected socket', async () => {
    let env;
    const shell = shellStub(async (_req, n) => {
        if (n === 2) {
            // Reconnect while the frame is being masked: S.socket is a new open socket.
            env.S.socket = { readyState: 1, send: (m) => env.lateSent.push(m) };
        }
        return { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] };
    });
    env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    env.lateSent = [];
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.S.screenCaptureStream = stream;
    env.w.startScreenVideoStreaming(stream, 'screen');
    for (let i = 0; i < 10 && env.intervals.length === 0; i++) await new Promise((r) => setImmediate(r));
    await env.intervals[0]();
    assert.equal(env.sent.length, 0, 'not sent on the old connection');
    assert.equal(env.lateSent.length, 0, 'not sent on the new connection');
    // Next tick captures on the new connection and sends there.
    await env.intervals[0]();
    assert.equal(env.lateSent.length, 1);
});

// ---------------------------------------------------------------- review round 2 (maintainer)

test('native screen share: a reconnect while a frame is being masked stops the share completely', async () => {
    let env;
    let calls = 0;
    const shell = shellStub(async () => {
        calls += 1;
        // Frames 1 (calls 1-2) go out normally; during frame 2's post-capture
        // query (call 4) the socket is replaced by a reconnect.
        if (calls === 4) env.S.socket = { readyState: 1, send: (m) => env.newSocketSent.push(JSON.parse(m)) };
        return { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] };
    });
    env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    env.newSocketSent = [];
    env.w.captureDesktopSourceWithTimeout = async () => ({ success: true, dataUrl: RAW_FRAME });
    // The native loop re-arms itself with setTimeout(scheduleNextFrame, 1000); run it by hand.
    const frameTimers = [];
    const realSetTimeout = env.w.setTimeout;
    env.w.setTimeout = (fn, ms) => (ms === 1000 ? (frameTimers.push(fn), frameTimers.length) : realSetTimeout(fn, ms));
    const provider = { nativeFrameCapture: true, captureSourceAsDataUrl() {} };
    await env.w.appScreen.startNativeScreenStreaming(provider, 'screen:3:0', 'screen');
    assert.equal(env.sent.length, 1, 'first frame sent on the original socket');
    assert.equal(frameTimers.length, 1, 'native frame loop armed');
    await frameTimers[0]();
    for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
    assert.equal(env.sent.filter((m) => m.action === 'stream_data').length, 1, 'old frame not sent on the old socket');
    assert.equal(env.newSocketSent.filter((m) => m.action === 'stream_data').length, 0, 'old frame not sent on the new socket');
    assert.ok(env.newSocketSent.some((m) => m.action === 'screen_share_stopped'),
        'the share is stopped for real: the backend is told (not just the frame loop paused)');
});

test('stream_data: avatar_position is computed when the frame is taken, not after masking', async () => {
    const shell = shellStub(async (_req, n) => {
        // The avatar moves while the frame is being masked.
        if (n === 2) env.w.__avatarMoved = true;
        return { ok: true, rects: [] };
    });
    const env = bootScreen({ multiWindow: true, shell });
    env.w.live2dManager = {
        getModelScreenBounds: () => (env.w.__avatarMoved
            ? { centerX: 900, centerY: 700, width: 100, height: 100 }
            : { centerX: 100, centerY: 100, width: 100, height: 100 }),
    };
    const session = env.w.appScreen.beginScreenFrameMaskSession('screen', { sourceId: null, via: 'stream' });
    const msg = await env.w.appScreen.buildMaskedStreamDataMessage(RAW_FRAME, 'screen', undefined, 'viewport', { session });
    assert.ok(msg.avatar_position, 'avatar overlay present');
    assert.ok(Math.abs(msg.avatar_position.centerX - 0.1) < 1e-9, `centerX ${msg.avatar_position.centerX} is the pre-mask position`);
});

test('stream_data: masked frames are re-encoded at the capture quality (0.8)', async () => {
    const qualities = [];
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] }));
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    const create = env.w.document.createElement;
    env.w.document.createElement = (tag) => {
        const el = create(tag);
        if (tag === 'canvas') {
            const toDataURL = el.toDataURL;
            el.toDataURL = (mime, q) => { qualities.push([mime, q]); return toDataURL(mime, q); };
        }
        return el;
    };
    const msg = await env.w.appScreen.buildMaskedStreamDataMessage(RAW_FRAME, 'screen', 'screen:1:0', 'screen', { via: 'native' });
    assert.deepEqual(fillsOf(msg.data), [[0, 0, 100, 80]]);
    assert.deepEqual(qualities, [['image/jpeg', 0.8]]);
});

test('fallback policy is told why: position unknown vs painting failed', async () => {
    const seen = [];
    const src = maskSource.replace("    function decideWhenGeometryUnavailable(ctx, reason) {\n",
        "    function decideWhenGeometryUnavailable(ctx, reason) {\n        globalThis.__reasons = (globalThis.__reasons || []).concat([reason]);\n");
    assert.notEqual(src, maskSource);
    const unknown = makeWindow({ elements: chatElements({ galOpen: true }), geom: { isExtended: true } });
    vm.runInContext(src, unknown.w);
    assert.equal(await unknown.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen' }), RAW_FRAME);
    seen.push(...unknown.w.__reasons);
    const broken = makeWindow({ elements: chatElements({ galOpen: true }) });
    vm.runInContext(src, broken.w);
    assert.equal(await broken.w.appCaptureMask.maskFrameForModel('data:bad,xx', { captureType: 'viewport', stream: ownTab(broken.w) }), 'data:bad,xx');
    seen.push(...broken.w.__reasons);
    assert.deepEqual(seen, ['position_unknown', 'paint_failed']);
});


// ---------------------------------------------------------------- review round 3: background tab / own-tab detection

// A browser-tab share of this page (the stream carries this page's capture handle).
function ownTab(w) {
    return tabStream(w.appCaptureMask.OWN_CAPTURE_HANDLE);
}

function tabStream(handle) {
    const track = {
        readyState: 'live', kind: 'video',
        getSettings: () => ({ displaySurface: 'browser' }),
        getCaptureHandle: () => (handle === undefined ? null : { handle }),
    };
    return { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
}

test('capture handle: this page publishes a random handle readable only by its own origin', () => {
    const { w } = makeWindow({ captureHandle: true });
    const cfg = w.__captureHandleConfig;
    assert.ok(cfg && cfg.handle === w.appCaptureMask.OWN_CAPTURE_HANDLE && /^neko-self-ui-mask:/.test(cfg.handle));
    assert.equal(cfg.exposeOrigin, false);
    assert.deepEqual(JSON.parse(JSON.stringify(cfg.permittedOrigins)), ['http://127.0.0.1:48911']);
});

test('browser tab share of THIS tab: regions are masked even while the tab is in the background', async () => {
    const env = makeWindow({ captureHandle: true, elements: chatElements({ galOpen: true }), imageSizes: { [RAW_FRAME]: [1000, 800] } });
    const own = tabStream(env.w.appCaptureMask.OWN_CAPTURE_HANDLE);
    for (const visibility of ['visible', 'hidden']) {
        env.w.document.visibilityState = visibility;
        const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
        const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: own }, session);
        assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]], `own tab, ${visibility}`);
    }
});

test('background tab + whole-screen share: the page is not on screen, nothing is painted', async () => {
    const env = makeWindow({
        captureHandle: true, elements: chatElements({ galOpen: true }),
        geom: { screenWidth: 1000, screenHeight: 800 },
    });
    env.w.document.visibilityState = 'hidden';
    const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session), RAW_FRAME);
});

test('browser tab share of ANOTHER tab: nothing of this page is in the frame, nothing is painted', async () => {
    for (const otherHandle of [undefined, 'someone-else']) {
        const env = makeWindow({ captureHandle: true, elements: chatElements({ galOpen: true }) });
        const other = tabStream(otherHandle);
        assert.equal(env.w.appCaptureMask.isOwnTabCapture(other), false);
        const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: other });
        assert.equal(out, RAW_FRAME, `other tab (${otherHandle})`);
    }
});

test('browser tab share of ANOTHER N.E.K.O page: its chat UI is in the frame at unknown positions -> fallback policy', async () => {
    const otherNeko = tabStream('neko-self-ui-mask:another-page');
    const env = makeWindow({ captureHandle: true, elements: chatElements({ galOpen: true }) });
    assert.equal(env.w.appCaptureMask.isOwnTabCapture(otherNeko), false);
    // Same result as today's policy (send unmasked), but it goes through the fallback decision...
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: otherNeko }), RAW_FRAME);
    // ...so switching the policy to "drop" drops it, unlike a share of a non-N.E.K.O tab.
    vm.runInContext(maskSource.replace("return 'send-unmasked';", "return 'drop';"), env.w);
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: otherNeko }), null);
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: tabStream('someone-else') }), RAW_FRAME);
});

test('own-tab check uses only the stream the frame came from, never the cached one', () => {
    const env = makeWindow({ captureHandle: true });
    env.w.appState = { screenCaptureStream: tabStream(env.w.appCaptureMask.OWN_CAPTURE_HANDLE) };
    assert.equal(env.w.appCaptureMask.isOwnTabCapture(undefined), null);
    assert.equal(env.w.appCaptureMask.isOwnTabCapture(null), null);
});

test('no Capture Handle API: a tab share cannot be tied to this page -> position unknown, sent unmasked', async () => {
    const env = makeWindow({ captureHandle: false, elements: chatElements({ galOpen: true }), imageSizes: { [RAW_FRAME]: [1000, 800] } });
    const stream = tabStream(undefined);
    assert.equal(env.w.appCaptureMask.isOwnTabCapture(stream), null);
    for (const visibility of ['visible', 'hidden']) {
        env.w.document.visibilityState = visibility;
        assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', stream }), RAW_FRAME, visibility);
    }
    // It goes through the fallback decision (a "drop" policy would drop it).
    vm.runInContext(maskSource.replace("return 'send-unmasked';", "return 'drop';"), env.w);
    env.w.document.visibilityState = 'visible';
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', stream }), null);
});

test('browser screen share of this tab in the background: the sent stream_data frame is masked', async () => {
    const env = bootScreen({ captureHandle: true, elements: chatElements({ galOpen: true }), defaultImageSize: [1000, 800] });
    env.w.document.visibilityState = 'hidden';
    const own = tabStream(env.w.appCaptureMask.OWN_CAPTURE_HANDLE);
    env.S.screenCaptureStream = own;
    env.w.startScreenVideoStreaming(own, 'screen');
    for (let i = 0; i < 10 && env.intervals.length === 0; i++) await new Promise((r) => setImmediate(r));
    await env.intervals[0]();
    assert.equal(env.sent.length, 1);
    assert.deepEqual(fillsOf(env.sent[0].data), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('native screen share: voice ending while a frame is being masked stops the share completely', async () => {
    let env;
    let calls = 0;
    const shell = shellStub(async () => {
        calls += 1;
        if (calls === 4) env.S.voiceChatActive = false; // voice session ends during frame 2's mask
        return { ok: true, rects: [] };
    });
    env = bootScreen({ multiWindow: true, shell });
    env.w.captureDesktopSourceWithTimeout = async () => ({ success: true, dataUrl: RAW_FRAME });
    const frameTimers = [];
    const realSetTimeout = env.w.setTimeout;
    env.w.setTimeout = (fn, ms) => (ms === 1000 ? (frameTimers.push(fn), frameTimers.length) : realSetTimeout(fn, ms));
    await env.w.appScreen.startNativeScreenStreaming({ nativeFrameCapture: true, captureSourceAsDataUrl() {} }, 'screen:3:0', 'screen');
    assert.equal(env.sent.filter((m) => m.action === 'stream_data').length, 1);
    await frameTimers[0]();
    for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
    assert.equal(env.sent.filter((m) => m.action === 'stream_data').length, 1, 'frame 2 not sent');
    assert.ok(env.sent.some((m) => m.action === 'screen_share_stopped'), 'share stopped for real, backend told');
});

test('browser tab share of ANOTHER tab while this page is in the background: nothing is painted', async () => {
    const env = makeWindow({ captureHandle: true, elements: chatElements({ galOpen: true }) });
    env.w.document.visibilityState = 'hidden';
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', stream: tabStream('someone-else') });
    assert.equal(out, RAW_FRAME);
});

test('single-shot: the frame from a share of this tab in the background is masked using the stream it came from', async () => {
    const env = bootProactive({ captureHandle: true, elements: chatElements({ galOpen: true }) });
    const own = tabStream(env.w.appCaptureMask.OWN_CAPTURE_HANDLE);
    env.S.screenCaptureStream = own;
    env.w.document.visibilityState = 'hidden';
    // The cached stream is replaced by another tab's stream while the frame is
    // being masked; the frame must still be judged by the stream it came from.
    env.w.appScreen.captureFrameFromStream = async () => {
        env.S.screenCaptureStream = tabStream('someone-else');
        return { dataUrl: RAW_FRAME };
    };
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.via, 'stream');
    assert.deepEqual(fillsOf(shot.dataUrl), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('single-shot: backend desktop frames are re-encoded at their capture quality (0.8)', async () => {
    const qualities = [];
    const env = bootProactive({ elements: chatElements({ galOpen: true }), geom: { screenWidth: 1000, screenHeight: 800 } });
    env.S.screenCaptureStream = null;
    env.w.appScreen.acquireOrReuseCachedStream = async () => null;
    env.w.appScreen.fetchBackendScreenshot = async () => ({ dataUrl: RAW_FRAME });
    const create = env.w.document.createElement;
    env.w.document.createElement = (tag) => {
        const el = create(tag);
        const toDataURL = el.toDataURL;
        el.toDataURL = (mime, q) => { qualities.push([mime, q]); return toDataURL(mime, q); };
        return el;
    };
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.via, 'backend');
    assert.equal(fillsOf(shot.dataUrl).length, 2);
    assert.deepEqual(qualities, [['image/jpeg', 0.8]]);
});

// ---------------------------------------------------------------- review round 4

// Records the order of shell queries and the backend screenshot.
function backendOrderShell() {
    const order = [];
    const shell = shellStub(async (req) => {
        order.push(req.surface);
        return { ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] };
    });
    shell.order = order;
    shell.fetchBackendScreenshot = async () => { order.push('screenshot'); return { dataUrl: RAW_FRAME }; };
    return shell;
}

test('single-shot backend frame: regions are sampled for the backend surface before the backend screenshot', async () => {
    const shell = backendOrderShell();
    const env = bootProactive({ multiWindow: true, shell });
    env.S.screenCaptureStream = null;
    env.w.appScreen.acquireOrReuseCachedStream = async () => null;
    env.w.appScreen.fetchBackendScreenshot = shell.fetchBackendScreenshot;
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.via, 'backend');
    assert.deepEqual(shell.order, ['backend', 'screenshot', 'backend']);
    assert.deepEqual(fillsOf(shot.dataUrl), [[500, 400, 250, 200]], 'backend frames are masked');
});

test('voice heartbeat backend frame: regions are sampled for the backend surface before the backend screenshot', async () => {
    const shell = backendOrderShell();
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    Object.assign(env.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    env.w.appScreen.acquireOrReuseCachedStream = async () => null;
    env.w.appScreen.fetchBackendScreenshot = shell.fetchBackendScreenshot;
    vm.runInContext(proactiveSource, env.w);
    await env.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(env.sent.length, 1);
    assert.deepEqual(shell.order, ['backend', 'screenshot', 'backend']);
    assert.deepEqual(fillsOf(env.sent[0].data), [[500, 400, 250, 200]]);
});

test('voice heartbeat: a frame captured on one connection is not sent on a reconnected socket', async () => {
    let env;
    const replaced = [];
    const shell = shellStub(async (_req, n) => {
        if (n === 2) env.S.socket = { readyState: 1, send: (m) => replaced.push(m) };
        return { ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] };
    });
    env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    Object.assign(env.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.w.appScreen.acquireOrReuseCachedStream = async () => stream;
    env.w.appScreen.captureFrameFromStream = async () => ({ dataUrl: RAW_FRAME });
    vm.runInContext(proactiveSource, env.w);
    await env.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(env.sent.length, 0, 'not sent on the old socket');
    assert.equal(replaced.length, 0, 'not sent on the new socket either');
});

test('single-shot: avatar_position is pinned when the raw frame arrives, before masking', async () => {
    let centerX = 0.1;
    let shellEnv;
    const shell = shellStub(async (_req, n) => {
        if (n === 2) centerX = 0.9; // avatar moves while the frame is being masked
        return { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] };
    });
    shellEnv = bootProactive({ multiWindow: true, shell });
    shellEnv.w.getAvatarScreenPosition = () => ({ centerX });
    const shot = await shellEnv.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.avatarPositionPinned, true);
    assert.equal(shot.avatarPosition.centerX, 0.1);
});

test('single-shot consumers use the pinned avatar position (text guard only, not a behaviour test)', () => {
    const ws = fs.readFileSync(path.join(repo, 'static/app/app-websocket.js'), 'utf8');
    assert.match(ws, /shot\.avatarPositionPinned === true/);
    assert.match(ws, /hasPinnedAvatarPos\s*\?\s*pinnedAvatarPos/);
    assert.match(proactiveSource, /screenshotResult\.avatarPositionPinned === true/);
});

// ---------------------------------------------------------------- review round 5

test('mobile voice heartbeat: a backend desktop frame (labelled camera) is still masked; real camera frames are not', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] }));
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    env.w.appUtils = { isMobile: () => true };
    Object.assign(env.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    env.w.appScreen.acquireOrReuseCachedStream = async () => null;
    env.w.appScreen.fetchBackendScreenshot = async () => ({ dataUrl: RAW_FRAME });
    vm.runInContext(proactiveSource, env.w);
    await env.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(env.sent.length, 1);
    assert.equal(env.sent[0].input_type, 'camera', 'label unchanged');
    assert.deepEqual(fillsOf(env.sent[0].data), [[500, 400, 250, 200]]);
    const cam = await env.w.appScreen.buildMaskedStreamDataMessage(RAW_FRAME, 'camera', null, null, { via: 'stream' });
    assert.equal(cam.data, RAW_FRAME, 'camera stream frames pass through');
});

test('screenshot_response is only sent on the connection the request came in on (text guard only)', () => {
    const ws = fs.readFileSync(path.join(repo, 'static/app/app-websocket.js'), 'utf8');
    const handler = ws.split("response.type === 'request_screenshot'")[1].split('function replySuppressedScreenshot')[0];
    assert.match(handler, /var requestSocket = S\.socket;/);
    assert.match(handler, /S\.socket !== requestSocket/);
});

// ---------------------------------------------------------------- review round 6

test('voice heartbeat: the pre-capture sample follows the source the stream actually uses', async () => {
    // acquireOrReuseCachedStream rewrites the selected source (null -> screen:1:0).
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] }));
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    Object.assign(env.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.w.appScreen.acquireOrReuseCachedStream = async () => { env.S.selectedScreenSourceId = 'screen:1:0'; return stream; };
    env.w.appScreen.captureFrameFromStream = async () => ({ dataUrl: RAW_FRAME });
    vm.runInContext(proactiveSource, env.w);
    await env.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(env.sent.length, 1);
    // pre (screen:1:0, after the rewrite, right before capture), post (screen:1:0)
    assert.deepEqual(shell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:1:0']);
});

test('single-shot: the pre-capture sample follows the source the stream actually uses', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] }));
    const env = bootProactive({ multiWindow: true, shell });
    env.S.screenCaptureStream = null;
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.w.appScreen.acquireOrReuseCachedStream = async () => { env.S.selectedScreenSourceId = 'screen:1:0'; return stream; };
    env.w.appScreen.captureFrameFromStream = async () => ({ dataUrl: RAW_FRAME });
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(shot.sourceId, 'screen:1:0');
    assert.deepEqual(shell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:1:0']);
    assert.deepEqual(fillsOf(shot.dataUrl), [[500, 400, 250, 200]]);
});

// ---------------------------------------------------------------- review round 7

test('browser: window restored from minimized (-32000,-32000) mid-capture -> nothing stretched, nothing painted in that frame', async () => {
    const history = makeElement({ x: 100, y: 100, w: 100, h: 100 });
    const { w } = makeWindow({
        elements: [history],
        geom: { innerWidth: 1000, innerHeight: 800, outerWidth: 1000, outerHeight: 800, screenX: -32000, screenY: -32000, screenWidth: 2000, screenHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [2000, 1000] },
    });
    const session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    w.screenX = 500;
    w.screenY = 0;
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session), RAW_FRAME);
    // The next frame (window settled) is masked normally.
    const next = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    assert.deepEqual(fillsOf(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, next)), [[592, 92, 116, 116]]);
});

test('voice heartbeat: every capture attempt samples right before it, for the source it captures', async () => {
    // acquireOrReuseCachedStream rewrites the source to screen:1:0, the stream frame
    // fails, and the native path then captures the original source again.
    const shell = shellStub(async () => ({ ok: true, rects: [] }));
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    Object.assign(env.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    env.S.selectedScreenSourceId = 'screen:0:0';
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.w.appScreen.acquireOrReuseCachedStream = async () => { env.S.selectedScreenSourceId = 'screen:1:0'; return stream; };
    env.w.appScreen.captureFrameFromStream = async () => { env.S.selectedScreenSourceId = 'screen:0:0'; return null; };
    env.w.getDesktopCaptureProvider = () => ({ nativeFrameCapture: true, captureSourceAsDataUrl() {} });
    env.w.captureDesktopSourceWithTimeout = async () => ({ success: true, dataUrl: RAW_FRAME });
    vm.runInContext(proactiveSource, env.w);
    await env.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(env.sent.length, 1);
    // pre for the stream attempt (screen:1:0), pre for the native attempt (screen:0:0), post (screen:0:0).
    assert.deepEqual(shell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:0:0', 'screen:0:0']);
});

test('browser: whole-screen share with an unknown display count (no Screen.isExtended) -> position unknown, sent unmasked', async () => {
    const { w } = makeWindow({
        elements: chatElements({ galOpen: true }),
        geom: { isExtended: undefined, screenWidth: 1000, screenHeight: 1000, innerWidth: 1000, innerHeight: 1000, outerWidth: 1000, outerHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [1000, 1000] },
    });
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), RAW_FRAME);
    vm.runInContext(maskSource.replace("return 'send-unmasked';", "return 'drop';"), w);
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), null, 'goes through the fallback decision');
});

test('browser: no Screen.isExtended but the desktop bridge knows there are several displays -> position unknown', async () => {
    const { w } = makeWindow({
        elements: chatElements({ galOpen: true }),
        geom: { isExtended: undefined, screenWidth: 1000, screenHeight: 1000, innerWidth: 1000, innerHeight: 1000, outerWidth: 1000, outerHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [1000, 1000] },
    });
    w.appScreen = { isKnownMultiDisplay: () => true };
    assert.equal(await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), RAW_FRAME);
});

test('single-shot: the pre-capture sample is taken after the stream is ready, right before the frame', async () => {
    // The chat moves while the stream is being acquired (before the frame); it does not move during capture.
    const history = makeElement({ x: 10, y: 10, w: 50, h: 50 });
    const env = bootProactive({ elements: [history] });
    env.S.screenCaptureStream = null;
    const track = { readyState: 'live', getCaptureHandle: () => ({ handle: env.w.appCaptureMask.OWN_CAPTURE_HANDLE }) };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.w.appScreen.acquireOrReuseCachedStream = async () => { history.rect = { x: 400, y: 400, w: 50, h: 50 }; return stream; };
    const shot = await env.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.deepEqual(fillsOf(shot.dataUrl), [[392, 392, 66, 66]]);
});

// ---------------------------------------------------------------- review round 8

test('map: whole-screen share with page zoom other than 100% -> position unknown (80% / 125%), 100% unchanged', () => {
    const { w } = makeWindow();
    const base = { innerHeight: 760, outerWidth: 1016, outerHeight: 800, screenX: 0, screenY: 0, screenWidth: 2000, screenHeight: 1000, multiDisplay: false };
    const rect = [{ x: 600, y: 100, width: 50, height: 50 }];
    // 100%: viewport 1000 CSS px in a 1016 px window.
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({}, base, { innerWidth: 1000 })).known, true);
    // 80%: the same 1000 px viewport is 1250 CSS px wide (and taller).
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({}, base, { innerWidth: 1250, innerHeight: 950 })).known, false);
    // 125%: 800 CSS px.
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({}, base, { innerWidth: 800, innerHeight: 608 })).known, false);
    // Width looks normal but the viewport is taller than the window: not 100% either.
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'screen', Object.assign({}, base, { innerWidth: 1000, innerHeight: 900 })).known, false);
    // Tab shares are in CSS px of the page itself: zoom does not matter there.
    assert.equal(w.appCaptureMask.mapViewportRectsToCapture(rect, 'viewport', Object.assign({}, base, { innerWidth: 1250, innerHeight: 950 })).known, true);
});

test('mask queries use the source the stream actually captures when the selection was cleared (fallback to screen:1:0)', async () => {
    const answer = async () => ({ ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] });
    const track = { readyState: 'live', kind: 'video', getSettings: () => ({ displaySurface: 'monitor' }) };
    const makeStream = () => ({ active: true, getVideoTracks: () => [track], getTracks: () => [track] });

    // Voice heartbeat.
    const hbShell = shellStub(answer);
    const hb = bootScreen({ multiWindow: true, shell: hbShell, defaultImageSize: [1000, 800] });
    Object.assign(hb.S, { proactiveVisionEnabled: true, proactiveChatEnabled: true, proactiveVisionChatEnabled: true });
    const hbStream = makeStream();
    hb.w.appScreen.acquireOrReuseCachedStream = async () => { hb.S.selectedScreenSourceId = null; return hbStream; };
    hb.w.appScreen.getStreamCaptureSourceId = (s) => (s === hbStream ? 'screen:1:0' : null);
    hb.w.appScreen.captureFrameFromStream = async () => ({ dataUrl: RAW_FRAME });
    vm.runInContext(proactiveSource, hb.w);
    await hb.w.appProactive.sendOneProactiveVisionFrame();
    assert.equal(hb.sent.length, 1);
    assert.deepEqual(hbShell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:1:0']);

    // Single shot (new stream, and the cached stream on the next call).
    const ssShell = shellStub(answer);
    const ss = bootProactive({ multiWindow: true, shell: ssShell });
    const ssStream = makeStream();
    ss.S.screenCaptureStream = null;
    ss.w.appScreen.acquireOrReuseCachedStream = async () => { ss.S.selectedScreenSourceId = null; ss.S.screenCaptureStream = ssStream; return ssStream; };
    ss.w.appScreen.getStreamCaptureSourceId = (s) => (s === ssStream ? 'screen:1:0' : null);
    const first = await ss.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(first.sourceId, 'screen:1:0');
    const second = await ss.w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.equal(second.sourceId, 'screen:1:0');
    assert.deepEqual(ssShell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:1:0', 'screen:1:0', 'screen:1:0']);
});

test('acquireOrReuseCachedStream: a stale selection falls back to the first screen and the stream remembers it', async () => {
    const env = bootScreen({ defaultImageSize: [1000, 800] });
    const track = { readyState: 'live', kind: 'video', addEventListener() {}, getSettings: () => ({ displaySurface: 'monitor' }), stop() {} };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    const requested = [];
    env.S.selectedScreenSourceId = 'screen:9:0';
    env.w.getDesktopCaptureProvider = () => ({ getSources() {} });
    env.w.desktopSourceEnumerationMayPrompt = () => false;
    env.w.invokeDesktopCaptureWithTimeout = async () => [{ id: 'window:5:0' }, { id: 'screen:1:0' }];
    env.w.navigator.mediaDevices.getUserMedia = async (c) => { requested.push(c.video.mandatory.chromeMediaSourceId); return stream; };
    const got = await env.w.appScreen.acquireOrReuseCachedStream({ allowPrompt: false });
    assert.equal(got, stream);
    assert.deepEqual(requested, ['screen:1:0']);
    assert.equal(env.S.selectedScreenSourceId, null, 'the stale selection is cleared');
    assert.equal(env.w.appScreen.getStreamCaptureSourceId(stream), 'screen:1:0');
});

// ---------------------------------------------------------------- review round 9

test('manual screen share: mask queries use the source the stream actually captures (stale selection fell back to screen:1:0)', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.25, height: 0.25 }] }));
    const env = bootScreen({ multiWindow: true, shell, defaultImageSize: [1000, 800] });
    const track = { readyState: 'live', kind: 'video', addEventListener() {}, getSettings: () => ({ displaySurface: 'monitor' }), stop() {} };
    const stream = { active: true, getVideoTracks: () => [track], getTracks: () => [track] };
    env.S.selectedScreenSourceId = 'screen:9:0';
    env.w.getDesktopCaptureProvider = () => ({ getSources() {} });
    env.w.desktopSourceEnumerationMayPrompt = () => false;
    env.w.invokeDesktopCaptureWithTimeout = async () => [{ id: 'screen:1:0' }];
    env.w.navigator.mediaDevices.getUserMedia = async () => stream;
    assert.equal(await env.w.appScreen.acquireOrReuseCachedStream({ allowPrompt: false }), stream);
    assert.equal(env.S.selectedScreenSourceId, null);
    env.w.startScreenVideoStreaming(stream, 'screen');
    for (let i = 0; i < 10 && env.intervals.length === 0; i++) await new Promise((r) => setImmediate(r));
    await env.intervals[0]();
    assert.equal(env.sent.length, 1);
    assert.deepEqual(shell.calls.map((c) => c.sourceId), ['screen:1:0', 'screen:1:0']);
    assert.deepEqual(fillsOf(env.sent[0].data), [[500, 400, 250, 200]]);
});

test('reporter: scrolling (no DOM mutation) and text changes trigger a report', async () => {
    const reports = [];
    const docListeners = {};
    let observeOptions = null;
    const history = makeElement({ x: 10, y: 10, w: 100, h: 100 });
    const { w } = makeWindow({ elements: [history] });
    w.document.addEventListener = (type, fn, capture) => { docListeners[type] = { fn, capture }; };
    w.MutationObserver = function () { this.observe = (_t, o) => { observeOptions = o; }; };
    w.nekoCaptureMask = { report: (p) => { reports.push(JSON.parse(JSON.stringify(p))); } };
    w.appCaptureMask.startShellReporter(w, w.document);
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(reports.length, 1);
    assert.equal(observeOptions.characterData, true);
    assert.ok(docListeners.scroll && docListeners.scroll.capture === true, 'scroll listened in the capture phase');
    history.rect = { x: 10, y: 60, w: 100, h: 100 };
    docListeners.scroll.fn();
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(reports.length, 2, 'scroll is reported without waiting for the 1 s recheck');
});


// ---------------------------------------------------------------- review round 9: focus + user switch

const stateSource = fs.readFileSync(path.join(repo, 'static/app/app-state.js'), 'utf8');
const settingsSource = fs.readFileSync(path.join(repo, 'static/app/app-settings.js'), 'utf8');

function spyReasons(w) {
    const src = maskSource.replace("    function decideWhenGeometryUnavailable(ctx, reason) {\n",
        "    function decideWhenGeometryUnavailable(ctx, reason) {\n        globalThis.__reasons = (globalThis.__reasons || []).concat([reason]);\n");
    assert.notEqual(src, maskSource);
    vm.runInContext(src, w);
}

function screenEnv(opts) {
    return makeWindow(Object.assign({
        elements: chatElements({ galOpen: true }),
        geom: { innerWidth: 1000, innerHeight: 1000, outerWidth: 1000, outerHeight: 1000, screenWidth: 1000, screenHeight: 1000 },
        imageSizes: { [RAW_FRAME]: [1000, 1000] },
    }, opts || {}));
}

test('browser whole-screen share: focused window before and after capture -> masked', async () => {
    const env = screenEnv();
    const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session);
    assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('browser whole-screen share: window not focused before capture -> position unknown, sent unmasked', async () => {
    const env = screenEnv({ focused: false });
    spyReasons(env.w);
    const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    env.state.focused = true;
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session);
    assert.equal(out, RAW_FRAME);
    assert.deepEqual([...env.w.__reasons], ['position_unknown']);
});

test('browser whole-screen share: window loses focus during capture -> position unknown, sent unmasked', async () => {
    const env = screenEnv();
    spyReasons(env.w);
    const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    env.state.focused = false;
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }, session);
    assert.equal(out, RAW_FRAME);
    assert.deepEqual([...env.w.__reasons], ['position_unknown']);
    // Single sample (no pre-capture session) follows the same rule.
    env.w.__reasons = [];
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), RAW_FRAME);
    assert.deepEqual([...env.w.__reasons], ['position_unknown']);
});

test('browser tab share of this tab: still masked while the window is not focused', async () => {
    const env = makeWindow({ focused: false, elements: chatElements({ galOpen: true }), imageSizes: { [RAW_FRAME]: [1000, 800] } });
    const session = env.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream', stream: ownTab(env.w) }, session);
    assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('browser whole-screen share: not focused but nothing to mask -> known, untouched without the fallback', async () => {
    const env = screenEnv({ focused: false, elements: [] });
    spyReasons(env.w);
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), RAW_FRAME);
    assert.equal(env.w.__reasons, undefined);
});

// Load the real app-state.js / app-settings.js into a mask test window; the
// two windows of one test share `store` as their localStorage.
function withSettings(env, store) {
    const w = env.w;
    const listeners = [];
    w.addEventListener = (type, fn) => { listeners.push({ type, fn }); };
    w.removeEventListener = () => {};
    w.dispatchEvent = () => {};
    w.CustomEvent = class { constructor(type) { this.type = type; } };
    w.localStorage = {
        getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
    };
    w.navigator = Object.assign({ userAgent: 'test' }, w.navigator || {});
    w.location = Object.assign({ search: '' }, w.location || {});
    w.matchMedia = () => ({ matches: false });
    w.fetch = () => new Promise(() => {});
    w.setTimeout = (fn, ms) => { const t = setTimeout(fn, ms); if (t && t.unref) t.unref(); return t; };
    w.document.getElementById = () => null;
    w.document.querySelector = () => null;
    vm.runInContext(stateSource, w);
    vm.runInContext(settingsSource, w);
    env.fireStorage = (newValue) => listeners
        .filter((l) => l.type === 'storage')
        .forEach((l) => l.fn({ key: 'project_neko_settings', newValue }));
    return env;
}

test('switch: on by default (app state, saved settings, reset defaults)', async () => {
    const env = withSettings(screenEnv(), {});
    assert.equal(env.w.appState.captureSelfUiMaskEnabled, true);
    assert.equal(env.w.captureSelfUiMaskEnabled, true);
    const out = await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' });
    assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]]);
    // A saved snapshot from before the switch existed has no key: stays on.
    const old = withSettings(screenEnv(), { project_neko_settings: JSON.stringify({ slopFilterEnabled: true }) });
    assert.equal(old.w.appState.captureSelfUiMaskEnabled, true);
});

test('switch off: the frame is returned as captured, no region read, no shell query', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0, y: 0, width: 0.5, height: 0.5 }] }));
    const env = makeWindow({ multiWindow: true, shell });
    env.w.appState = { captureSelfUiMaskEnabled: false };
    assert.equal(env.w.appCaptureMask.beginMaskSession({ via: 'stream', sourceId: 'screen:0:0' }).switchedOff, true);
    for (const via of ['stream', 'native', 'backend']) {
        assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', sourceId: 'screen:0:0', via }), RAW_FRAME);
    }
    assert.equal(shell.calls.length, 0);

    const browser = screenEnv();
    browser.w.appState = { captureSelfUiMaskEnabled: false };
    let reads = 0;
    browser.w.document.querySelectorAll = () => { reads += 1; return browser.state.elements; };
    const offSession = browser.w.appCaptureMask.beginMaskSession({ via: 'stream' });
    assert.equal(offSession.switchedOff, true);
    assert.equal(offSession.local, undefined, 'no pre-capture sample while off');
    assert.equal(await browser.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' }), RAW_FRAME);
    assert.equal(reads, 0);
    // Read on every frame: switching back on takes effect on the next frame.
    browser.w.appState.captureSelfUiMaskEnabled = true;
    const out = await browser.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'screen', via: 'stream' });
    assert.deepEqual(fillsOf(out), [[592, 92, 316, 416], [592, 512, 316, 116]]);
});

test('switch: turned off in the chat window -> the next frame in the Pet window is not masked (and back on)', async () => {
    const store = {};
    const chat = withSettings(makeWindow({}), store);
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.25, width: 0.25, height: 0.5 }] }));
    const pet = withSettings(makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1600, 900] } }), store);
    const ctx = { captureType: 'screen', sourceId: 'screen:0:0', via: 'native' };
    assert.deepEqual(fillsOf(await pet.w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx)), [[800, 225, 400, 450]]);
    assert.equal(shell.calls.length, 1);

    // The settings popup handler: set the global, then save.
    chat.w.captureSelfUiMaskEnabled = false;
    chat.w.saveNEKOSettings();
    assert.equal(JSON.parse(store.project_neko_settings).captureSelfUiMaskEnabled, false);
    pet.fireStorage(store.project_neko_settings);
    assert.equal(await pet.w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx), RAW_FRAME);
    assert.equal(shell.calls.length, 1, 'no shell query while switched off');

    chat.w.captureSelfUiMaskEnabled = true;
    chat.w.saveNEKOSettings();
    pet.fireStorage(store.project_neko_settings);
    assert.deepEqual(fillsOf(await pet.w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx)), [[800, 225, 400, 450]]);
    assert.equal(shell.calls.length, 2);
});

test('switch: wired through settings sync, the server allow-list, the popup and all 8 locales (text guard only)', () => {
    const settings = settingsSource;
    assert.match(settings, /'captureSelfUiMaskEnabled',\n/);
    assert.match(settings, /captureSelfUiMaskEnabled: true,/);
    assert.match(settings, /S\.captureSelfUiMaskEnabled = settings\.captureSelfUiMaskEnabled \?\? true;/);
    const allowList = fs.readFileSync(path.join(repo, 'utils/conversation_settings_constants.py'), 'utf8');
    assert.match(allowList, /"captureSelfUiMaskEnabled",/);
    const popup = fs.readFileSync(path.join(repo, 'static/avatar/avatar-ui-popup.js'), 'utf8');
    assert.match(popup, /id: 'capture-self-ui-mask'.*storageKey: 'captureSelfUiMaskEnabled'/);
    for (const locale of ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es']) {
        const toggles = JSON.parse(fs.readFileSync(path.join(repo, 'static/locales', locale + '.json'), 'utf8')).settings.toggles;
        assert.ok(toggles.captureSelfUiMask && toggles.captureSelfUiMaskTooltip, locale);
    }
});

test('switch turned on while a frame is being captured: that frame is sent as captured, the next one is masked', async () => {
    const shell = shellStub(async () => ({ ok: true, rects: [{ x: 0.5, y: 0.25, width: 0.25, height: 0.5 }] }));
    const env = makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1600, 900] } });
    env.w.appState = { captureSelfUiMaskEnabled: false };
    const ctx = { captureType: 'screen', sourceId: 'screen:0:0', via: 'native' };
    const session = env.w.appCaptureMask.beginMaskSession(ctx);
    env.w.appState.captureSelfUiMaskEnabled = true;
    assert.equal(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx, session), RAW_FRAME);
    assert.equal(shell.calls.length, 0);
    const next = env.w.appCaptureMask.beginMaskSession(ctx);
    assert.deepEqual(fillsOf(await env.w.appCaptureMask.maskFrameForModel(RAW_FRAME, ctx, next)), [[800, 225, 400, 450]]);
});

test('settings popup re-sync reads the switch like the mask module (only false is off)', () => {
    const src = fs.readFileSync(path.join(repo, 'static/avatar/avatar-ui-popup-config.js'), 'utf8');
    const start = src.indexOf('function createSettingsPopupShowSync');
    assert.ok(start >= 0);
    for (const [value, expected] of [[true, true], [false, false], [null, true], [undefined, 'untouched']]) {
        const cb = { checked: 'untouched' };
        const query = (sel) => (sel === '#neko-capture-self-ui-mask' ? cb : null);
        const ctx = { document: { querySelector: query }, window: { captureSelfUiMaskEnabled: value }, requestAnimationFrame() {} };
        const sync = vm.runInNewContext('(' + src.slice(start, src.indexOf('\n}\n', start) + 2) + ')', ctx)('neko');
        sync({ querySelector: query }, 'settings');
        assert.equal(cb.checked, expected, String(value));
    }
});

test('settings popup re-sync is installed for every avatar type (Live2D, VRM, MMD)', () => {
    const src = fs.readFileSync(path.join(repo, 'static/avatar/avatar-ui-popup-config.js'), 'utf8');
    for (const prefix of ['live2d', 'vrm', 'mmd']) {
        assert.ok(src.includes(`_onPopupShow: createSettingsPopupShowSync('${prefix}')`), prefix);
    }
});
