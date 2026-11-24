'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');

function createDomStub() {
    class MiniClassList {
        constructor() { this.set = new Set(); }
        add(...t) { t.forEach(x => this.set.add(x)); }
        remove(...t) { t.forEach(x => this.set.delete(x)); }
        toggle(x, force) {
            if (force === undefined) {
                if (this.set.has(x)) { this.set.delete(x); return false; }
                this.set.add(x); return true;
            }
            if (force) this.set.add(x); else this.set.delete(x);
            return !!force;
        }
        contains(x) { return this.set.has(x); }
    }

    function matchesSelector(el, sel) {
        const s = sel.trim();
        if (s.startsWith('#')) return el.id === s.slice(1);
        const attrMatch = s.match(/^\.([a-zA-Z0-9_-]+)\[data-type="([^"]+)"\]$/);
        if (attrMatch) {
            return el.classList.contains(attrMatch[1]) && el.dataset.type === attrMatch[2];
        }
        if (s.startsWith('.')) {
            const parts = s.split('.');
            return parts.filter(Boolean).every(cls => el.classList.contains(cls));
        }
        if (s === 'input[type="checkbox"]:checked') {
            return el.tagName === 'INPUT' && el.type === 'checkbox' && !!el.checked;
        }
        return el.tagName === s.toUpperCase();
    }

    function findAll(root, selector) {
        const out = [];
        function walk(node) {
            for (const c of node.children) {
                if (matchesSelector(c, selector)) out.push(c);
                walk(c);
            }
        }
        walk(root);
        return out;
    }

    class MiniEl {
        constructor(tag) {
            this.tagName = String(tag || 'div').toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.parentElement = null;
            this.dataset = {};
            this.style = { setProperty() {} };
            this.attributes = {};
            this.classList = new MiniClassList();
            this._className = '';
            this._textContent = '';
            this._innerHTML = '';
            this.checked = false;
            this.disabled = false;
            this.type = '';
            this.value = '';
            this.id = '';
            this.listeners = new Map();
        }
        get className() { return this._className; }
        set className(v) {
            this._className = String(v || '');
            this.classList.set = new Set(this._className.split(/\s+/).filter(Boolean));
        }
        get textContent() { return this._textContent; }
        set textContent(v) { this._textContent = String(v ?? ''); }
        get innerHTML() { return this._innerHTML; }
        set innerHTML(v) {
            this._innerHTML = String(v ?? '');
            if (this._innerHTML === '') {
                this.children.forEach(c => { c.parentNode = null; c.parentElement = null; });
                this.children = [];
            }
        }
        appendChild(child) {
            if (child.parentNode) child.parentNode.removeChild(child);
            this.children.push(child);
            child.parentNode = this;
            child.parentElement = this;
            return child;
        }
        removeChild(child) {
            const idx = this.children.indexOf(child);
            if (idx >= 0) {
                this.children.splice(idx, 1);
                child.parentNode = null;
                child.parentElement = null;
            }
            return child;
        }
        setAttribute(k, v) {
            this.attributes[k] = String(v);
            if (k === 'id') this.id = String(v);
        }
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; }
        querySelectorAll(sel) { return findAll(this, sel); }
        querySelector(sel) { return findAll(this, sel)[0] || null; }
        closest(sel) {
            let cur = this;
            while (cur) {
                if (matchesSelector(cur, sel)) return cur;
                cur = cur.parentNode;
            }
            return null;
        }
        addEventListener(type, fn) {
            if (!this.listeners.has(type)) this.listeners.set(type, new Set());
            this.listeners.get(type).add(fn);
        }
        removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
        getBoundingClientRect() { return { top: 0, bottom: 20, left: 0, right: 100, width: 100, height: 20 }; }
        focus() {}
    }

    const docListeners = new Map();
    const doc = {
        readyState: 'complete',
        head: new MiniEl('head'),
        body: new MiniEl('body'),
        createElement(tag) { return new MiniEl(tag); },
        getElementById(id) { return findAll(doc.body, `#${id}`)[0] || null; },
        querySelectorAll(sel) { return findAll(doc.body, sel); },
        querySelector(sel) { return findAll(doc.body, sel)[0] || null; },
        addEventListener(type, fn) {
            if (!docListeners.has(type)) docListeners.set(type, new Set());
            docListeners.get(type).add(fn);
        },
        removeEventListener(type, fn) { docListeners.get(type)?.delete(fn); },
    };
    return { doc, MiniEl };
}

test('F0052: openCardMakerFromModelManager passes navigateOnReuse:true and avoids stale fallback overwrite', async () => {
    const commonDialogsCode = fs.readFileSync(path.join(ROOT, 'static/common_dialogs.js'), 'utf8');
    const pageBridgeCode = fs.readFileSync(path.join(ROOT, 'static/js/model_manager/page-bridge.js'), 'utf8');

    // Set up main-page opener window running real common_dialogs.js (no openManagedPopup)
    const openerReplacedUrls = [];
    let openCalls = 0;
    let fakeMakerWindow = null;
    const openerWin = {
        location: { origin: 'http://127.0.0.1:48911', href: 'http://127.0.0.1:48911/' },
        screen: { availWidth: 1920, availHeight: 1080, width: 1920, height: 1080 },
        closed: false,
        addEventListener() {},
        removeEventListener() {},
        open(url, _name, _features) {
            openCalls += 1;
            fakeMakerWindow = {
                closed: false,
                focus() {},
                resizeTo() {},
                moveTo() {},
                location: {
                    href: String(url),
                    replace(nextUrl) {
                        openerReplacedUrls.push(String(nextUrl));
                        this.href = String(nextUrl);
                    },
                },
            };
            return fakeMakerWindow;
        },
    };
    openerWin.window = openerWin;
    const { doc: openerDoc } = createDomStub();
    const openerCtx = vm.createContext({
        window: openerWin,
        document: openerDoc,
        URL,
        URLSearchParams,
        console,
        setTimeout,
        clearTimeout,
        setInterval: (fn, ms) => {
            const handle = setInterval(fn, ms);
            if (handle && typeof handle.unref === 'function') handle.unref();
            return handle;
        },
        clearInterval,
        localStorage: { getItem: () => null, setItem: () => {} },
    });
    vm.runInContext(commonDialogsCode, openerCtx);
    assert.equal(typeof openerWin.openOrFocusWindow, 'function');
    assert.equal(typeof openerWin.openManagedPopup, 'undefined');

    // Set up model_manager window running real page-bridge.js
    const scheduledTimeouts = [];
    const scheduledIntervals = [];
    let nextTimerId = 1;
    const putCardFaceCalls = [];
    const mmWinListeners = new Map();
    const mmWin = {
        opener: openerWin,
        location: { origin: 'http://127.0.0.1:48911', href: 'http://127.0.0.1:48911/model_manager' },
        addEventListener(type, fn) {
            if (!mmWinListeners.has(type)) mmWinListeners.set(type, new Set());
            mmWinListeners.get(type).add(fn);
        },
        removeEventListener(type, fn) { mmWinListeners.get(type)?.delete(fn); },
        dispatchEvent(evt) {
            for (const fn of Array.from(mmWinListeners.get(evt.type) || [])) fn(evt);
        },
        openOrFocusWindow: openerWin.openOrFocusWindow,
    };
    mmWin.window = mmWin;
    const mmCtx = vm.createContext({
        window: mmWin,
        document: openerDoc,
        URL,
        URLSearchParams,
        AbortController,
        BroadcastChannel: undefined,
        console,
        fetch: async (url, init = {}) => {
            if (String(url).includes('/card-face')) {
                putCardFaceCalls.push({ url: String(url), init });
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
        setTimeout(fn, ms) {
            const id = nextTimerId++;
            scheduledTimeouts.push({ id, fn, ms, cleared: false });
            return id;
        },
        clearTimeout(id) {
            const t = scheduledTimeouts.find(x => x.id === id);
            if (t) t.cleared = true;
        },
        setInterval(fn, ms) {
            const id = nextTimerId++;
            scheduledIntervals.push({ id, fn, ms, cleared: false });
            return id;
        },
        clearInterval(id) {
            const t = scheduledIntervals.find(x => x.id === id);
            if (t) t.cleared = true;
        },
        captureDefaultCardFaceModelImage: async () => 'data:image/png;base64,FALLBACK',
        generateDefaultCardFaceFromModelManager: async (name) => {
            putCardFaceCalls.push({ fallbackFor: name });
        },
    });
    vm.runInContext(pageBridgeCode, mmCtx);

    // Launch #1 (from main-page opener)
    const w1 = mmCtx.openCardMakerFromModelManager('NekoA', {
        fallbackDefaultOnClose: true,
        fallbackToken: 'tok-1',
    });
    assert.equal(openCalls, 1);
    assert.equal(w1, fakeMakerWindow);

    // Launch #2 while window is STILL open, with new fallbackToken 'tok-2'
    const w2 = mmCtx.openCardMakerFromModelManager('NekoA', {
        fallbackDefaultOnClose: true,
        fallbackToken: 'tok-2',
    });
    assert.equal(openCalls, 1, 'must reuse existing window');
    assert.equal(w2, fakeMakerWindow);
    assert.equal(openerReplacedUrls.length, 1, 'must navigate reused window via navigateOnReuse:true');
    assert.ok(openerReplacedUrls[0].includes('fallback_token=tok-2'), 'reused window URL must have new token');

    // Start watcher for tok-2, save using tok-2, then close window -> 350ms fallback must NOT overwrite
    mmCtx.watchCardMakerCloseForDefaultCardFace(w2, 'NekoA', {}, { fallbackToken: 'tok-2' });
    mmWin.dispatchEvent({
        type: 'message',
        origin: 'http://127.0.0.1:48911',
        data: { type: 'card-face-updated', name: 'NekoA', fallbackToken: 'tok-2', timestamp: Date.now() },
    });
    fakeMakerWindow.closed = true;
    scheduledIntervals.filter(i => !i.cleared).forEach(i => i.fn());
    scheduledTimeouts.filter(t => !t.cleared && t.ms === 350).forEach(t => t.fn());
    await new Promise(r => setImmediate(r));
    assert.equal(putCardFaceCalls.length, 0, 'fallback card face must NOT overwrite after valid save');

    // Also test direct window.openOrFocusWindow fallback (!window.opener)
    mmWin.opener = null;
    fakeMakerWindow.closed = false;
    mmCtx.openCardMakerFromModelManager('NekoA', {
        fallbackDefaultOnClose: true,
        fallbackToken: 'tok-3',
    });
    assert.equal(openerReplacedUrls.length, 2);
    assert.ok(openerReplacedUrls[1].includes('fallback_token=tok-3'));
});

test('F0053: deleteSelectedModels preserves vrm/mmd selection and saveModelToCharacter prefers currentModelInfo.path', async () => {
    const controllerCode = fs.readFileSync(
        path.join(ROOT, 'static/js/model_manager/page-controller.js'),
        'utf8'
    );

    const deleteStart = controllerCode.indexOf('async function deleteSelectedModels()');
    const deleteEnd = controllerCode.indexOf('    if (deleteModelBtn) {', deleteStart);
    assert.ok(deleteStart > 0 && deleteEnd > deleteStart);
    const deleteFnCode = controllerCode.slice(deleteStart, deleteEnd);

    const saveBlockStart = controllerCode.indexOf('} else if (currentModelType === \'live3d\') {');
    const saveBlockEnd = controllerCode.indexOf('// 3. 使用【专用模型接口】', saveBlockStart);
    assert.ok(saveBlockStart > 0 && saveBlockEnd > saveBlockStart);
    // Extract just the `if (isMmdModel) { ... } else { ... }` block inside live3d
    const live3dResolveSnippet = controllerCode.slice(
        controllerCode.indexOf('const selectedOpt = vrmModelSelect', saveBlockStart),
        controllerCode.indexOf('if (vrmAnimationSelect)', saveBlockStart)
    ) + '\n}';

    function makeOption(val, subType, rawPath) {
        return {
            value: val,
            getAttribute(k) {
                if (k === 'data-sub-type') return subType;
                if (k === 'data-path') return rawPath;
                return null;
            },
        };
    }

    function makeSelect(options, initialIndex = 0) {
        const sel = {
            options: options.slice(),
            selectedIndex: initialIndex,
            get value() { return this.options[this.selectedIndex]?.value || ''; },
            set value(v) {
                const idx = this.options.findIndex(o => o.value === v);
                if (idx >= 0) this.selectedIndex = idx;
            },
        };
        return sel;
    }

    const optA = makeOption('/user_vrm/A.vrm', 'vrm', '/user_vrm/A.vrm');
    const optB = makeOption('/user_vrm/B.vrm', 'vrm', '/user_vrm/B.vrm');
    const optC = makeOption('/user_vrm/C.vrm', 'vrm', '/user_vrm/C.vrm');
    const optMmd = makeOption('/user_mmd/M1.pmx', 'mmd', '/user_mmd/M1.pmx');

    const vrmModelSelect = makeSelect([optA, optB, optC, optMmd], 2); // Currently selected: C
    const mmdModelSelect = makeSelect([optMmd], 0);
    let vrmButtonTextRefreshed = 0;

    const deleteCtx = vm.createContext({
        selectedDeleteModels: new Set(['vrm:/user_vrm/A.vrm']),
        selectedDeleteCount: null,
        deleteSelectedInfo: null,
        confirmDeleteBtn: { disabled: false, textContent: '' },
        currentModelType: 'live3d',
        currentModelInfo: { type: 'vrm', path: '/user_vrm/C.vrm', url: '/user_vrm/C.vrm' },
        vrmModelSelect,
        mmdModelSelect,
        modelSelect: null,
        t: (_k, fb) => fb,
        showStatus: () => {},
        showConfirm: async () => true,
        showAlert: async () => {},
        hideDeleteModelModal() {},
        updateConfirmDeleteButton() {},
        isBoundPNGTuberDeleteKey: () => false,
        getBoundPNGTuberDeleteKeys: () => ({ committed: '', pending: '' }),
        RequestHelper: {
            async fetchJson() { return { success: true }; },
        },
        loadUserModels: async () => {},
        loadLive3DModels: async () => {
            // Simulates loadLive3DModels resetting options and selectedIndex = 0
            vrmModelSelect.options = [optB, optC, optMmd];
            vrmModelSelect.selectedIndex = 0;
        },
        loadMMDModels: async () => {
            mmdModelSelect.options = [optMmd];
            mmdModelSelect.selectedIndex = 0;
        },
        updateVRMModelSelectButtonText: () => { vrmButtonTextRefreshed += 1; },
        updateMMDModelSelectButtonText: () => {},
        console,
        encodeURIComponent,
    });
    vm.runInContext(deleteFnCode, deleteCtx);
    await deleteCtx.deleteSelectedModels();

    assert.equal(vrmModelSelect.value, '/user_vrm/C.vrm', 'must restore previously selected VRM after delete');
    assert.ok(vrmButtonTextRefreshed >= 1, 'must update VRM dropdown button text');

    // Now test saveModelToCharacter resolution when dropdown is stale (item 0 = B) but currentModelInfo is C (VRM)
    const saveCtxVrm = vm.createContext({
        vrmModelSelect: makeSelect([optB, optC], 0), // Stale index 0 = B
        currentModelInfo: { type: 'vrm', path: '/user_vrm/C.vrm' },
        effectiveLive3dSubType: 'vrm',
        modelName: '/user_vrm/C.vrm',
        modelData: {},
        mmdAnimationSelect: null,
        getSelectedIdleAnimations: () => [],
    });
    vm.runInContext(live3dResolveSnippet, saveCtxVrm);
    assert.equal(saveCtxVrm.modelData.vrm, '/user_vrm/C.vrm', 'VRM save must use active currentModelInfo.path over stale index 0');

    // Test MMD active model while VRM options precede MMD options in vrmModelSelect
    const saveCtxMmd = vm.createContext({
        vrmModelSelect: makeSelect([optB, optMmd], 0), // Stale index 0 = VRM B
        currentModelInfo: { type: 'mmd', path: '/user_mmd/M1.pmx' },
        effectiveLive3dSubType: 'mmd',
        modelName: '/user_mmd/M1.pmx',
        modelData: {},
        mmdAnimationSelect: null,
        getSelectedIdleAnimations: () => [],
    });
    vm.runInContext(live3dResolveSnippet, saveCtxMmd);
    assert.equal(saveCtxMmd.modelData.mmd, '/user_mmd/M1.pmx', 'MMD save must use active currentModelInfo.path over VRM item 0');
});

test('F0585 & F0059: MMD->Live2D disposes mmdManager, allowLive2DReload bypasses guard, and touchPage_open/onClose never wipe server touch_set', async () => {
    const controllerCode = fs.readFileSync(
        path.join(ROOT, 'static/js/model_manager/page-controller.js'),
        'utf8'
    );
    const touchConfigCode = fs.readFileSync(
        path.join(ROOT, 'static/touch-config.js'),
        'utf8'
    );

    // Verify page-controller MMD cleanup in switchModelDisplay live2d branch and allowLive2DReload in loadModel
    const switchLive2dStart = controllerCode.indexOf('// 隐藏 MMD 容器和控件组');
    const switchLive2dEnd = controllerCode.indexOf('isMmdAnimationPlaying = false;', switchLive2dStart);
    assert.ok(switchLive2dStart > 0 && switchLive2dEnd > switchLive2dStart);
    const mmdCleanupSnippet = controllerCode.slice(switchLive2dStart, switchLive2dEnd);

    let mmdStopped = 0;
    let mmdDisposed = 0;
    const switchCtx = vm.createContext({
        window: {
            mmdManager: {
                currentModel: { name: 'miku' },
                stopAnimation() { mmdStopped += 1; },
                dispose() { mmdDisposed += 1; this.currentModel = null; },
            },
        },
        stopIdleRotation() {},
        console,
    });
    vm.runInContext(mmdCleanupSnippet, switchCtx);
    assert.equal(mmdStopped, 1);
    assert.equal(mmdDisposed, 1);
    assert.equal(switchCtx.window.mmdManager, null, 'switching MMD->Live2D must dispose and null window.mmdManager');

    const loadTouchStart = controllerCode.indexOf('// 初始化触摸配置');
    const loadTouchEnd = controllerCode.indexOf('// 启用其他控件', loadTouchStart);
    assert.ok(loadTouchStart > 0 && loadTouchEnd > loadTouchStart);
    const loadTouchSnippet = controllerCode.slice(loadTouchStart, loadTouchEnd);
    assert.ok(loadTouchSnippet.includes('allowLive2DReload: true'), 'model_manager loadModel must pass allowLive2DReload: true');

    // Run real touch-config.js in vm
    function setupTouchConfig(fetchImpl, options = {}) {
        const { doc } = createDomStub();
        let deferredCharactersFetch = null;
        const win = {
            document: doc,
            t: (_k, fb) => fb,
            i18next: { t: (_k, opts) => (opts && opts.defaultValue) || _k },
            location: { search: '?lanlan_name=NekoA' },
            lanlan_config: { lanlan_name: 'NekoA', model_type: 'live2d' },
            waitForStorageLocationStartupBarrier: () => new Promise(() => {}),
            vrmManager: options.vrmManager || null,
            mmdManager: options.mmdManager || null,
            live2dManager: (() => {
                const defaultModel = {
                    internalModel: {
                        settings: {
                            hitAreas: [{ id: 'Head', Name: 'Head' }],
                            json: {
                                FileReferences: {
                                    Motions: { Idle: [{ File: 'motions/idle.motion3.json' }] },
                                    Expressions: [{ Name: 'happy_smile' }],
                                },
                            },
                        },
                    },
                };
                return {
                    modelName: 'mao_pro',
                    touchSet: undefined,
                    _touchSetLoadedAt: 0,
                    currentModel: defaultModel,
                    getCurrentModel() {
                        return this.currentModel;
                    },
                    setupHitAreaInteraction() {},
                };
            })(),
        };
        win.window = win;

        const scheduledTimeouts = [];
        let tid = 1;
        const ctx = vm.createContext({
            window: win,
            document: doc,
            URLSearchParams,
            AbortController,
            BroadcastChannel: undefined,
            fetch: fetchImpl,
            localStorage: { getItem: () => options.localModelType || 'live2d', setItem() {} },
            requestAnimationFrame: () => 1,
            setTimeout(fn, ms) {
                const id = tid++;
                scheduledTimeouts.push({ id, fn, ms, cleared: false });
                return id;
            },
            clearTimeout(id) {
                const t = scheduledTimeouts.find(x => x.id === id);
                if (t) t.cleared = true;
            },
            console,
            encodeURIComponent,
            deferredCharactersFetch,
        });
        vm.runInContext(touchConfigCode, ctx);
        return { ctx, win, doc, scheduledTimeouts };
    }

    // Scenario A: InitializationTouchSet with allowLive2DReload:true succeeds even with stale VRM/localStorage
    {
        const { ctx, win } = setupTouchConfig(
            async (url) => {
                if (String(url) === '/api/characters') {
                    return {
                        ok: true,
                        json: async () => ({
                            '猫娘': {
                                NekoA: {
                                    _reserved: {
                                        touch_set: {
                                            mao_pro: {
                                                Head: { motions: ['idle'], expressions: ['happy_smile'] },
                                            },
                                        },
                                    },
                                },
                            },
                        }),
                    };
                }
                return { ok: true, json: async () => ({ success: true }) };
            },
            { localModelType: 'live3d', vrmManager: { currentModel: {} } }
        );
        const blocked = await ctx.InitializationTouchSet(undefined, {});
        assert.equal(blocked, false, 'without allowLive2DReload, stale localStorage/VRM blocks init');
        const allowed = await ctx.InitializationTouchSet(undefined, { allowLive2DReload: true });
        assert.equal(allowed, true, 'with allowLive2DReload:true, init succeeds');
        assert.deepEqual(
            win.live2dManager.touchSet.mao_pro.Head,
            { motions: ['idle'], expressions: ['happy_smile'] }
        );
    }

    // Scenario B: touchPage_open called before touchSet loaded -> awaits InitializationTouchSet, renders server data, closing without edits sends 0 PATCHes
    {
        const patchCalls = [];
        const { ctx, win, doc } = setupTouchConfig(async (url, init = {}) => {
            if (String(url).includes('/touch_set') && init.method === 'PATCH') {
                patchCalls.push(JSON.parse(init.body));
                return { ok: true, json: async () => ({ success: true }) };
            }
            if (String(url) === '/api/characters') {
                return {
                    ok: true,
                    json: async () => ({
                        '猫娘': {
                            NekoA: {
                                _reserved: {
                                    touch_set: {
                                        mao_pro: {
                                            Head: { motions: ['idle'], expressions: ['happy_smile'] },
                                        },
                                    },
                                },
                            },
                        },
                    }),
                };
            }
            return { ok: true, json: async () => ({ success: true }) };
        });

        await ctx.touchPage_open();
        assert.deepEqual(
            win.live2dManager.touchSet.mao_pro.Head,
            { motions: ['idle'], expressions: ['happy_smile'] }
        );
        const overlay = doc.querySelector('.touch-config-overlay');
        assert.ok(overlay, 'config window must open after loading touchSet');
        // Close via overlay click without making any edits -> 0 PATCHes
        overlay.onclick({ target: overlay });
        await new Promise(r => setImmediate(r));
        assert.equal(patchCalls.length, 0, 'closing without edits must NOT PATCH server');
    }

    // Scenario C: touchPage_open when /api/characters returns 500 -> does NOT open config window, 0 PATCHes
    {
        const patchCalls = [];
        const { ctx, doc } = setupTouchConfig(async (url, init = {}) => {
            if (String(url).includes('/touch_set') && init.method === 'PATCH') {
                patchCalls.push(init);
            }
            if (String(url) === '/api/characters') {
                return { ok: false, status: 500, json: async () => ({}) };
            }
            return { ok: true, json: async () => ({ success: true }) };
        });

        await ctx.touchPage_open();
        const hitareaItems = doc.querySelectorAll('.hitarea-item');
        assert.equal(hitareaItems.length, 0, 'must not render empty hitarea list when /api/characters failed');
        assert.equal(patchCalls.length, 0);
    }

    // Scenario D: Direct showTouchSetConfigWindow without loaded touchSet -> onClose sends 0 PATCHes
    {
        const patchCalls = [];
        const { ctx, doc } = setupTouchConfig(async (url, init = {}) => {
            if (String(url).includes('/touch_set') && init.method === 'PATCH') {
                patchCalls.push(init);
            }
            return { ok: true, json: async () => ({ success: true }) };
        });
        ctx.showTouchSetConfigWindow([{ id: 'Head', Name: 'Head' }], {}, []);
        const overlay = doc.querySelector('.touch-config-overlay');
        overlay.onclick({ target: overlay });
        await new Promise(r => setImmediate(r));
        assert.equal(patchCalls.length, 0, 'unloaded snapshot must NEVER PATCH server on close');
    }

    // Scenario E: Loaded snapshot + edit + immediate close before 500ms autoSave -> sends 1 PATCH with edited data
    {
        const patchCalls = [];
        const { ctx, win, doc } = setupTouchConfig(async (url, init = {}) => {
            if (String(url).includes('/touch_set') && init.method === 'PATCH') {
                patchCalls.push(JSON.parse(init.body));
                return { ok: true, json: async () => ({ success: true }) };
            }
            return { ok: true, json: async () => ({ success: true }) };
        });
        win.live2dManager.touchSet = { mao_pro: { Head: { motions: [], expressions: [] } } };
        win.live2dManager._touchSetLoadedAt = Date.now();
        ctx.showTouchSetConfigWindow(
            [{ id: 'Head', Name: 'Head' }],
            { Idle: [{ File: 'motions/idle.motion3.json' }] },
            []
        );
        const checkbox = doc.querySelector('.hitarea-item input[type="checkbox"]')
            || doc.querySelector('input[type="checkbox"]');
        // Find checkbox inside multiselect
        const allCheckboxes = [];
        (function walk(n) {
            if (n.tagName === 'INPUT' && n.type === 'checkbox') allCheckboxes.push(n);
            n.children.forEach(walk);
        })(doc.body);
        assert.ok(allCheckboxes.length >= 2);
        const headCheckbox = allCheckboxes.find(cb => cb.closest('.custom-multiselect')?.dataset.hitAreaId === 'Head');
        assert.ok(headCheckbox, 'must find Head motion checkbox');
        headCheckbox.checked = true;
        headCheckbox.onchange();
        const overlay = doc.querySelector('.touch-config-overlay');
        overlay.onclick({ target: overlay });
        await new Promise(r => setImmediate(r));
        assert.equal(patchCalls.length, 1, 'closing with pending edit must flush 1 PATCH');
        assert.deepEqual(Array.from(patchCalls[0].touch_set.Head.motions), ['idle']);
    }

    // Scenario F (#3 / #16): model switches during /api/characters fetch inside touchPage_open -> aborts silently, 0 PATCHes
    {
        const patchCalls = [];
        const floatingMessages = [];
        let resolveCharsFetch;
        const modelA = {
            internalModel: {
                settings: {
                    hitAreas: [{ id: 'Head', Name: 'Head' }],
                    json: { FileReferences: { Motions: {}, Expressions: [] } },
                },
            },
        };
        const modelB = {
            internalModel: {
                settings: {
                    hitAreas: [{ id: 'Body', Name: 'Body' }],
                    json: { FileReferences: { Motions: {}, Expressions: [] } },
                },
            },
        };
        let activeModel = modelA;
        const { ctx, win, doc } = setupTouchConfig(async (url, init = {}) => {
            if (String(url).includes('/touch_set') && init.method === 'PATCH') {
                patchCalls.push(JSON.parse(init.body));
                return { ok: true, json: async () => ({ success: true }) };
            }
            if (String(url) === '/api/characters') {
                return await new Promise(resolve => {
                    resolveCharsFetch = resolve;
                });
            }
            return { ok: true, json: async () => ({ success: true }) };
        });
        win.live2dManager.touchSet = {
            haru: { Body: { motions: ['existing_haru'], expressions: [] } },
        };
        win.live2dManager._touchSetLoadedAt = 12345;
        win.live2dManager.getCurrentModel = () => activeModel;
        const origCreateWindow = ctx.createTouchConfigFloatingWindow;
        ctx.createTouchConfigFloatingWindow = (opts) => {
            floatingMessages.push(opts);
            return origCreateWindow(opts);
        };

        const openPromise = ctx.touchPage_open();
        await new Promise(r => setImmediate(r));
        assert.equal(typeof resolveCharsFetch, 'function');

        // User switches model A -> B while /api/characters is in flight
        win.live2dManager.modelName = 'haru';
        activeModel = modelB;

        resolveCharsFetch({
            ok: true,
            json: async () => ({
                '猫娘': {
                    NekoA: {
                        _reserved: {
                            touch_set: {
                                mao_pro: { Head: { motions: ['idle'], expressions: [] } },
                                haru: { Body: { motions: ['tap'], expressions: [] } },
                            },
                        },
                    },
                },
            }),
        });
        await openPromise;
        assert.equal(doc.querySelector('.touch-config-overlay'), null, 'must NOT open config window for stale model after switch');
        assert.equal(floatingMessages.length, 0, 'must abort silently without error popup on switch');
        assert.equal(patchCalls.length, 0, 'must send 0 PATCHes');
        assert.deepEqual(
            win.live2dManager.touchSet,
            { haru: { Body: { motions: ['existing_haru'], expressions: [] } } },
            'stale InitializationTouchSet must NOT overwrite haru touchSet on model switch'
        );
        assert.equal(win.live2dManager._touchSetLoadedAt, 12345, 'stale InitializationTouchSet must NOT update _touchSetLoadedAt');
    }

    // Scenario G (wehos opt #5): InitializationTouchSet returns 'superseded' because a newer load already populated the same model -> opens cleanly
    {
        const floatingMessages = [];
        let resolveSlowFetch;
        let fetchCount = 0;
        const { ctx, win, doc } = setupTouchConfig(async (url) => {
            if (String(url) === '/api/characters') {
                fetchCount += 1;
                if (fetchCount === 1) {
                    return await new Promise(resolve => {
                        resolveSlowFetch = resolve;
                    });
                }
                return {
                    ok: true,
                    json: async () => ({
                        '猫娘': {
                            NekoA: {
                                _reserved: {
                                    touch_set: {
                                        mao_pro: { Head: { motions: ['idle'], expressions: [] } },
                                    },
                                },
                            },
                        },
                    }),
                };
            }
            return { ok: true, json: async () => ({ success: true }) };
        });
        const origCreateWindow = ctx.createTouchConfigFloatingWindow;
        ctx.createTouchConfigFloatingWindow = (opts) => {
            floatingMessages.push(opts);
            return origCreateWindow(opts);
        };

        const openPromise = ctx.touchPage_open();
        await new Promise(r => setImmediate(r));
        assert.equal(typeof resolveSlowFetch, 'function');

        // A newer refresh completes for the exact same model first
        const newerResult = await ctx.InitializationTouchSet(undefined, { allowLive2DReload: true });
        assert.equal(newerResult, true);

        // Now the older fetch inside touchPage_open resolves -> 'superseded', yet touchSet is populated for mao_pro
        resolveSlowFetch({
            ok: true,
            json: async () => ({
                '猫娘': {
                    NekoA: {
                        _reserved: {
                            touch_set: {
                                mao_pro: { Head: { motions: [], expressions: [] } },
                            },
                        },
                    },
                },
            }),
        });
        await openPromise;
        assert.ok(doc.querySelector('.touch-config-overlay'), 'superseded result with loaded same-model touchSet must open cleanly');
        assert.deepEqual(
            Array.from(win.live2dManager.touchSet.mao_pro.Head.motions),
            ['idle'],
            'superseded empty fetch must NOT clobber newer touchSet data'
        );
        assert.equal(floatingMessages.filter(m => m && m.content).length, 0, 'must not show modelDataNotReady error popup');
    }
});

test('PR3976 #14 & #15: switchModelDisplay aborts stale live2d switch before MMD dispose and deleteSelectedModels preserves newer Live3D selection', async () => {
    const controllerCode = fs.readFileSync(
        path.join(ROOT, 'static/js/model_manager/page-controller.js'),
        'utf8'
    );

    // Part 1 (#14): switchModelDisplay('live2d') superseded during loadLive2DModelOptions must not dispose window.mmdManager
    const switchStart = controllerCode.indexOf('async function switchModelDisplay(type, subType, options = {})');
    const switchEnd = controllerCode.indexOf('    // 模型类型选择事件', switchStart);
    assert.ok(switchStart > 0 && switchEnd > switchStart);
    const switchFnCode = controllerCode.slice(switchStart, switchEnd);

    const { doc } = createDomStub();
    let resolveLive2dOptions;
    let mmdDisposedCount = 0;
    const mmdManagerObj = {
        currentModel: { name: 'miku' },
        stopAnimation() {},
        dispose() { mmdDisposedCount += 1; },
    };
    const noopEl = () => ({ style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, appendChild() {} });
    const switchCtx = vm.createContext({
        currentModelType: 'live3d',
        currentLive3dSubType: 'mmd',
        modelDisplaySwitchGeneration: 1,
        pngtuberPreviewGeneration: 0,
        pendingPNGTuberPreview: null,
        unfinalizedPNGTuberCommit: null,
        currentModelInfo: null,
        modelSelect: null,
        modelTypeSelect: { value: 'live3d' },
        modelTypeManager: { updateButtonText() {} },
        pngtuberPreviewGroup: null,
        pngtuberContainer: null,
        live2dModelGroup: null,
        vrmModelGroup: null,
        live2dContainer: noopEl(),
        vrmExpressionGroup: null,
        vrmContainer: null,
        mmdContainer: noopEl(),
        mmdModelGroup: null,
        mmdAnimationGroup: null,
        vrmAnimationGroup: null,
        playMmdAnimationBtn: null,
        uploadBtn: null,
        modelUpload: null,
        vrmFileUpload: null,
        isMmdAnimationPlaying: false,
        isMmdIdlePlaying: false,
        isVrmAnimationPlaying: false,
        isVrmExpressionPlaying: false,
        vrmManager: null,
        clearPNGTuberPreviewControls() {},
        stopIdleRotation() {},
        updateMMDAnimationPlayButtonIcon() {},
        loadLive2DModelOptions() {
            return new Promise(resolve => { resolveLive2dOptions = resolve; });
        },
        localStorage: { setItem() {}, getItem: () => null },
        document: doc,
        window: {
            mmdManager: mmdManagerObj,
            pngtuberManager: null,
            vrmManager: null,
            live2dManager: null,
            dispatchEvent() {},
        },
        CustomEvent: class { constructor(type, o = {}) { this.type = type; this.detail = o.detail; } },
        t: (_k, fb) => fb,
        showStatus() {},
        console,
    });
    vm.runInContext(switchFnCode, switchCtx);

    const staleSwitchPromise = switchCtx.switchModelDisplay('live2d');
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveLive2dOptions, 'function');

    // User switches back to live3d/mmd while loadLive2DModelOptions is still awaiting
    switchCtx.modelDisplaySwitchGeneration += 1;
    switchCtx.currentModelType = 'live3d';
    switchCtx.currentLive3dSubType = 'mmd';

    resolveLive2dOptions();
    const staleResult = await staleSwitchPromise;
    assert.equal(staleResult, false, 'superseded switchModelDisplay(live2d) must return false');
    assert.equal(mmdDisposedCount, 0, 'superseded live2d switch must NOT dispose active window.mmdManager');
    assert.equal(switchCtx.window.mmdManager, mmdManagerObj, 'superseded live2d switch must NOT null window.mmdManager');

    // Part 2 (#15): deleteSelectedModels preserves newer user selection made while loadLive3DModels() is in flight
    const deleteStart = controllerCode.indexOf('async function deleteSelectedModels()');
    const deleteEnd = controllerCode.indexOf('    if (deleteModelBtn) {', deleteStart);
    const deleteFnCode = controllerCode.slice(deleteStart, deleteEnd);

    const makeOption = (val, subType = 'vrm') => ({
        value: val,
        getAttribute(k) {
            if (k === 'data-sub-type') return subType;
            if (k === 'data-path') return val;
            return null;
        },
    });
    const makeSelect = (options, initialIndex = 0) => ({
        options: options.slice(),
        selectedIndex: initialIndex,
        get value() { return this.options[this.selectedIndex]?.value || ''; },
        set value(v) {
            const idx = this.options.findIndex(o => o.value === v);
            if (idx >= 0) this.selectedIndex = idx;
        },
    });

    const optDel = makeOption('/user_vrm/Del.vrm');
    const optA = makeOption('/user_vrm/A.vrm');
    const optB = makeOption('/user_vrm/B.vrm');
    const vrmModelSelect = makeSelect([optDel, optA, optB], 1); // Starts on A
    const mmdModelSelect = makeSelect([], -1);

    let resolveReloadLive3D;
    const deleteCtx = vm.createContext({
        userModelSelectionGeneration: 1,
        vrmAdmittedSelectionLoad: { modelUrl: '/user_vrm/A.vrm' },
        vrmSelectionLoad: { modelUrl: '/user_vrm/A.vrm' },
        selectedDeleteModels: new Set(['vrm:/user_vrm/Del.vrm']),
        selectedDeleteCount: null,
        deleteSelectedInfo: null,
        confirmDeleteBtn: { disabled: false, textContent: '' },
        currentModelType: 'live3d',
        currentModelInfo: { type: 'vrm', path: '/user_vrm/A.vrm', url: '/user_vrm/A.vrm' },
        vrmModelSelect,
        mmdModelSelect,
        modelSelect: null,
        t: (_k, fb) => fb,
        showStatus() {},
        showConfirm: async () => true,
        showAlert: async () => {},
        hideDeleteModelModal() {},
        updateConfirmDeleteButton() {},
        isBoundPNGTuberDeleteKey: () => false,
        getBoundPNGTuberDeleteKeys: () => ({ committed: '', pending: '' }),
        RequestHelper: { async fetchJson() { return { success: true }; } },
        loadUserModels: async () => {},
        loadLive3DModels: () => new Promise(resolve => {
            resolveReloadLive3D = () => {
                vrmModelSelect.options = [optA, optB];
                vrmModelSelect.selectedIndex = 0; // List rebuild resets selectedIndex to 0 (A)
                resolve();
            };
        }),
        loadMMDModels: async () => {},
        updateVRMModelSelectButtonText() {},
        updateMMDModelSelectButtonText() {},
        console,
        encodeURIComponent,
    });
    vm.runInContext(deleteFnCode, deleteCtx);
    const deletePromise = deleteCtx.deleteSelectedModels();
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveReloadLive3D, 'function', 'deleteSelectedModels must suspend inside loadLive3DModels');

    // Strictly while loadLive3DModels is in flight: user selects pending B while admitted load & currentModelInfo are still A (no userModelSelectionGeneration bump yet)
    vrmModelSelect.value = '/user_vrm/B.vrm';
    deleteCtx.vrmSelectionLoad = { modelUrl: '/user_vrm/B.vrm', admitted: false, vetoed: false };
    resolveReloadLive3D();
    await deletePromise;

    assert.equal(vrmModelSelect.value, '/user_vrm/B.vrm', 'deleteSelectedModels must prefer pending user vrmSelectionLoad B over admitted load A even without generation bump');

    // T6 subcase: pending B is vetoed by voice check during loadLive3DModels (vrmSelectionLoad cleared, vetoed=true, no gen bump) -> must restore A, NOT vetoed prevVrmValue B
    const optDel2 = makeOption('/user_vrm/Del2.vrm');
    vrmModelSelect.options = [optDel2, optA, optB];
    vrmModelSelect.selectedIndex = 2; // Started on pending B before refresh
    let resolveReload2;
    const pendingBJob = { modelUrl: '/user_vrm/B.vrm', admitted: false, vetoed: false };
    const vetoCtx = vm.createContext({
        userModelSelectionGeneration: 1,
        vrmAdmittedSelectionLoad: null,
        vrmSelectionLoad: pendingBJob,
        selectedDeleteModels: new Set(['vrm:/user_vrm/Del2.vrm']),
        selectedDeleteCount: null,
        deleteSelectedInfo: null,
        confirmDeleteBtn: { disabled: false, textContent: '' },
        currentModelType: 'live3d',
        currentModelInfo: { type: 'vrm', path: '/user_vrm/A.vrm', url: '/user_vrm/A.vrm' },
        vrmModelSelect,
        mmdModelSelect,
        modelSelect: null,
        t: (_k, fb) => fb,
        showStatus() {},
        showConfirm: async () => true,
        showAlert: async () => {},
        hideDeleteModelModal() {},
        updateConfirmDeleteButton() {},
        isBoundPNGTuberDeleteKey: () => false,
        getBoundPNGTuberDeleteKeys: () => ({ committed: '', pending: '' }),
        RequestHelper: { async fetchJson() { return { success: true }; } },
        loadUserModels: async () => {},
        loadLive3DModels: () => new Promise(resolve => {
            resolveReload2 = () => {
                vrmModelSelect.options = [optA, optB];
                vrmModelSelect.selectedIndex = 1;
                resolve();
            };
        }),
        loadMMDModels: async () => {},
        updateVRMModelSelectButtonText() {},
        updateMMDModelSelectButtonText() {},
        console,
        encodeURIComponent,
    });
    vm.runInContext(deleteFnCode, vetoCtx);
    const vetoDeletePromise = vetoCtx.deleteSelectedModels();
    await new Promise(r => setImmediate(r));

    // Voice check vetoes pending B in-flight without incrementing userModelSelectionGeneration
    pendingBJob.vetoed = true;
    vetoCtx.vrmSelectionLoad = null;
    resolveReload2();
    await vetoDeletePromise;

    assert.equal(vrmModelSelect.value, '/user_vrm/A.vrm', 'vetoed pending VRM selection B must fall back to admitted currentModelInfo A');
});

test('PR3976 U8 + T12 + T13: rapid MMD model switch A -> B isolates session cleanup and aborts late loadCharacterLighting side effects', async () => {
    const controllerCode = fs.readFileSync(
        path.join(ROOT, 'static/js/model_manager/page-controller.js'),
        'utf8'
    );
    const mmdChangeStart = controllerCode.indexOf('// MMD 模型选择事件');
    const mmdChangeEnd = controllerCode.indexOf('    // MMD 动画选择事件', mmdChangeStart);
    assert.ok(mmdChangeStart > 0 && mmdChangeEnd > mmdChangeStart, 'must locate MMD select change listener block');
    const mmdChangeSnippet = controllerCode.slice(mmdChangeStart, mmdChangeEnd);

    const changeListeners = [];
    const mmdModelSelect = {
        value: '',
        addEventListener(type, fn) {
            if (type === 'change') changeListeners.push(fn);
        },
    };
    const mmdListeners = {
        loadedModels: [],
        loadedAnimations: [],
        playedAnimations: 0,
    };
    let resolveModelA;
    let resolveModelB;
    let sessionSeq = 0;
    const endedOverlays = [];
    const restoredCanvases = [];

    const mmdCtx = vm.createContext({
        mmdModelSelect,
        mmdContainer: { classList: { remove() {}, add() {} }, style: {} },
        vrmContainer: { classList: { add() {} }, style: {} },
        playMmdAnimationBtn: { disabled: false },
        currentModelType: 'live3d',
        currentLive3dSubType: 'mmd',
        modelDisplaySwitchGeneration: 1,
        userModelSelectionGeneration: 0,
        isMmdAnimationPlaying: false,
        isMmdIdlePlaying: false,
        _mmdSettingsLoadPromise: null,
        updateMMDModelSelectButtonText() {},
        updateMMDAnimationPlayButtonIcon() {},
        stopIdleRotation() {},
        loadCharacterLighting: async () => {},
        isSuppressedModelManagerChangeEvent: () => false,
        markMMDCanvasLoadingSession() {},
        showStatusForMMDLoadingSession() {},
        restoreMMDCanvasForLoadingSession(_canvas, id) { restoredCanvases.push(id); },
        localStorage: { getItem: () => null },
        t: (_k, fb) => fb,
        document: { getElementById: () => ({ id: 'mmd-canvas' }) },
        window: {
            mmdModuleLoaded: true,
            vrmManager: null,
            _createMMDLoadingSessionId: (p) => `${p}-${++sessionSeq}`,
            MMDLoadingOverlay: { begin() {}, update() {}, fail() {}, end(id) { endedOverlays.push(id); } },
            mmdManager: {
                scene: {},
                stopAnimation() {},
                loadModel(modelPath) {
                    mmdListeners.loadedModels.push(modelPath);
                    return new Promise(resolve => {
                        if (modelPath === 'modelA.pmx') resolveModelA = resolve;
                        else resolveModelB = resolve;
                    });
                },
                async loadAnimation(anim) { mmdListeners.loadedAnimations.push(anim); },
                playAnimation() { mmdListeners.playedAnimations += 1; },
            },
        },
        console,
    });
    vm.runInContext(mmdChangeSnippet, mmdCtx);
    assert.equal(changeListeners.length, 1);

    const loadAPromise = changeListeners[0]({ target: { value: 'modelA.pmx' } });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveModelA, 'function');

    const loadBPromise = changeListeners[0]({ target: { value: 'modelB.pmx' } });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveModelB, 'function');

    // Now stale modelA finishes loading -> must abort ONLY session 1 and leave session 2 loading
    resolveModelA();
    await loadAPromise;
    assert.equal(mmdListeners.loadedAnimations.length, 0, 'superseded MMD load A must NOT call loadAnimation');
    assert.equal(mmdListeners.playedAnimations, 0, 'superseded MMD load A must NOT call playAnimation');
    assert.deepEqual(endedOverlays, ['mmd-manager-1'], 'only session A overlay must be ended before B finishes');
    assert.deepEqual(restoredCanvases, ['mmd-manager-1'], 'only session A canvas may be restored while B is still loading');

    // Current modelB finishes loading -> proceeds normally with session 2
    resolveModelB();
    await loadBPromise;
    assert.equal(mmdListeners.loadedAnimations.length, 1, 'latest MMD load B must load idle animation');
    assert.equal(mmdListeners.playedAnimations, 1, 'latest MMD load B must play idle animation');
    assert.deepEqual(endedOverlays, ['mmd-manager-1', 'mmd-manager-2']);
    assert.deepEqual(restoredCanvases, ['mmd-manager-1', 'mmd-manager-2']);

    // T12: late /api/characters inside real loadCharacterLighting() must not overwrite MMD idle selections or startIdleRotation
    const lightingStart = controllerCode.indexOf('    async function loadCharacterLighting(');
    const lightingEnd = controllerCode.indexOf('    // 注意：VRM 模型列表已在', lightingStart);
    assert.ok(lightingStart > 0 && lightingEnd > lightingStart);
    const lightingSnippet = controllerCode.slice(lightingStart, lightingEnd);

    let resolveCharsA;
    let resolveVrmIdleOptions;
    let waitForIdleOptions = false;
    let mmdGen = 1;
    let idleStoppedCount = 0;
    const mmdSelectedHistory = [];
    const idleStarted = [];
    const lightingCtx = vm.createContext({
        getLanlanName: async () => 'CatA',
        RequestHelper: {
            async fetchJson(url) {
                if (url === '/api/characters') {
                    return await new Promise(resolve => { resolveCharsA = resolve; });
                }
                return {};
            },
        },
        applyLightingValues() {},
        loadIdleAnimationOptions: async () => {
            if (waitForIdleOptions) {
                return await new Promise(resolve => { resolveVrmIdleOptions = resolve; });
            }
        },
        loadMmdIdleAnimationOptions: async () => {},
        setSelectedIdleAnimations(id, list) { mmdSelectedHistory.push({ id, list }); },
        startIdleRotation(type, list) { idleStarted.push({ type, list }); },
        stopIdleRotation() { idleStoppedCount += 1; },
        normalizeBundledVrmAnimationUrl: (u) => u,
        document: { getElementById: () => null },
        console,
    });
    vm.runInContext(lightingSnippet, lightingCtx);

    const callGen = 1;
    const staleLightingPromise = lightingCtx.loadCharacterLighting({
        isMmdSuperseded: () => mmdGen !== callGen,
    });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveCharsA, 'function');

    // Newer MMD load supersedes while /api/characters is in flight
    mmdGen = 2;
    resolveCharsA({
        '猫娘': {
            CatA: {
                model_type: 'live3d',
                live3d_sub_type: 'mmd',
                mmd: 'old.pmx',
                mmd_idle_animations: ['/mmd/old_idle.vmd'],
            },
        },
    });
    await staleLightingPromise;
    assert.equal(mmdSelectedHistory.length, 0, 'superseded loadCharacterLighting must NOT overwrite MMD idle checkboxes');
    assert.equal(idleStarted.length, 0, 'superseded loadCharacterLighting must NOT start stale MMD idle rotation');

    // CodeRabbit 4661235254: superseded during await loadIdleAnimationOptions() must NOT restore VRM idle selections or rotation
    waitForIdleOptions = true;
    resolveCharsA = null;
    const callGen2 = mmdGen;
    const staleVrmIdlePromise = lightingCtx.loadCharacterLighting({
        isMmdSuperseded: () => mmdGen !== callGen2,
    });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveCharsA, 'function');
    resolveCharsA({
        '猫娘': {
            CatA: {
                model_type: 'vrm',
                idle_animation: ['/vrm/old_idle.vrma'],
            },
        },
    });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveVrmIdleOptions, 'function');
    mmdGen += 1;
    resolveVrmIdleOptions();
    await staleVrmIdlePromise;
    assert.equal(mmdSelectedHistory.length, 0, 'superseded loadCharacterLighting must NOT overwrite VRM idle checkboxes after loadIdleAnimationOptions');
    assert.equal(idleStarted.length, 0, 'superseded loadCharacterLighting must NOT start VRM idle rotation after loadIdleAnimationOptions');
    assert.equal(idleStoppedCount, 0, 'superseded loadCharacterLighting must NOT stop VRM idle rotation after loadIdleAnimationOptions');
});
