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
    const state = { elements: opts.elements || [] };
    const document = {
        visibilityState: 'visible',
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
    const jpeg = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport' });
    assert.match(jpeg, /^data:image\/jpeg;/);
    assert.equal(decodeFrame(jpeg).w, 1280);
    assert.equal(decodeFrame(jpeg).h, 720);
    const png = await w.appCaptureMask.maskFrameForModel('data:image/png;base64,UE5H', { captureType: 'viewport' });
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
    assert.equal(await w.appCaptureMask.maskFrameForModel('data:bad,xx', { captureType: 'viewport' }), 'data:bad,xx');
});

test('browser: regions before and after capture are unioned (chat moved mid-capture)', async () => {
    const history = makeElement({ x: 100, y: 100, w: 100, h: 100 });
    const { w } = makeWindow({ elements: [history], geom: { innerWidth: 1000, innerHeight: 1000 }, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
    const session = w.appCaptureMask.beginMaskSession({ via: 'stream' });
    history.rect = { x: 500, y: 500, w: 100, h: 100 }; // moved while the frame was being taken
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream' }, session);
    assert.deepEqual(fillsOf(out), [[92, 92, 116, 116], [492, 492, 116, 116]]);

    // Without the pre-capture snapshot only the final position would be covered.
    const noSession = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { captureType: 'viewport', via: 'stream' });
    assert.deepEqual(fillsOf(noSession), [[492, 492, 116, 116]]);
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

test('shell: pre- and post-capture answers for the same target are unioned; a different target uses post only', async () => {
    const answers = [
        { ok: true, rects: [{ x: 0, y: 0, width: 0.1, height: 0.1 }] },
        { ok: true, rects: [{ x: 0.5, y: 0.5, width: 0.1, height: 0.1 }] },
    ];
    const shell = shellStub(async (_req, n) => answers[n - 1] || answers[1]);
    const { w } = makeWindow({ multiWindow: true, shell, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
    const session = w.appCaptureMask.beginMaskSession({ sourceId: 'screen:1:0', via: 'native', captureType: 'screen' });
    const out = await w.appCaptureMask.maskFrameForModel(RAW_FRAME, { sourceId: 'screen:1:0', via: 'native', captureType: 'screen' }, session);
    assert.deepEqual(fillsOf(out), [[0, 0, 100, 100], [500, 500, 100, 100]]);

    const shell2 = shellStub(async (_req, n) => answers[n - 1] || answers[1]);
    const other = makeWindow({ multiWindow: true, shell: shell2, imageSizes: { [RAW_FRAME]: [1000, 1000] } });
    const session2 = other.w.appCaptureMask.beginMaskSession({ sourceId: 'screen:1:0', via: 'stream', captureType: 'screen' });
    const out2 = await other.w.appCaptureMask.maskFrameForModel(RAW_FRAME, { sourceId: null, via: 'backend', captureType: 'screen' }, session2);
    assert.deepEqual(fillsOf(out2), [[500, 500, 100, 100]]);
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
    const track = { readyState: 'live' };
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

test('single-shot: chat moved during capture -> both positions are covered', async () => {
    const { w } = bootProactive({
        elements: [makeElement({ x: 10, y: 10, w: 50, h: 50 })],
        duringCapture(state) { state.elements[0].rect = { x: 400, y: 400, w: 50, h: 50 }; },
    });
    const shot = await w.appProactive.captureProactiveChatScreenshotWithSource();
    assert.deepEqual(fillsOf(shot.dataUrl), [[2, 2, 66, 66], [392, 392, 66, 66]]);
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
