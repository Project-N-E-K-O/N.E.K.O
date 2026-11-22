'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');

function createMiniDom() {
    class MiniClassList {
        constructor(el) {
            this.el = el;
            this.set = new Set();
        }
        add(...tokens) { tokens.forEach(t => this.set.add(t)); }
        remove(...tokens) { tokens.forEach(t => this.set.delete(t)); }
        toggle(token, force) {
            if (force === undefined) {
                if (this.set.has(token)) { this.set.delete(token); return false; }
                this.set.add(token); return true;
            }
            if (force) this.set.add(token); else this.set.delete(token);
            return !!force;
        }
        contains(token) { return this.set.has(token); }
    }

    function matchesSimpleSelector(el, sel) {
        if (!sel || !el) return false;
        const s = sel.trim();
        if (s.startsWith('#')) return el.id === s.slice(1);
        if (s.startsWith('.')) return el.classList.contains(s.slice(1));
        const attrMatch = s.match(/^([a-zA-Z0-9_-]*)\[([a-zA-Z0-9_:-]+)="([^"]*)"\]$/);
        if (attrMatch) {
            const [, tag, attr, val] = attrMatch;
            if (tag && el.tagName !== tag.toUpperCase()) return false;
            if (attr === 'name') return el.name === val;
            if (attr === 'data-testid') return el.dataset.testid === val;
            if (attr === 'value') return el.value === val;
            return el.getAttribute(attr) === val;
        }
        if (s === 'select:not(.conversation-language-select)') {
            return el.tagName === 'SELECT' && !el.classList.contains('conversation-language-select');
        }
        if (s === 'input, textarea, select:not(.conversation-language-select)') {
            return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
                || (el.tagName === 'SELECT' && !el.classList.contains('conversation-language-select'));
        }
        if (s === 'input, textarea') {
            return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA';
        }
        return el.tagName === s.toUpperCase();
    }

    function findAll(root, selector) {
        const selectors = selector.split(',').map(x => x.trim()).filter(Boolean);
        const out = [];
        function walk(node) {
            for (const child of node.children) {
                if (selectors.some(sel => matchesSimpleSelector(child, sel))) {
                    out.push(child);
                }
                walk(child);
            }
        }
        walk(root);
        return out;
    }

    class MiniElement {
        constructor(tagName) {
            this.tagName = String(tagName || 'div').toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.parentElement = null;
            this.dataset = {};
            this.style = { setProperty() {} };
            this.attributes = {};
            this.listeners = new Map();
            this.classList = new MiniClassList(this);
            this._className = '';
            this._textContent = '';
            this._innerHTML = '';
            this._value = '';
            this.disabled = false;
            this.readOnly = false;
            this.required = false;
            this.checked = false;
            this.selected = false;
            this.name = '';
            this.id = '';
            this.type = '';
            this.title = '';
            this.label = '';
            this.scrollLeft = 0;
            this.scrollWidth = 0;
            this.tabIndex = 0;
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
        get firstChild() { return this.children[0] || null; }
        get childNodes() { return this.children; }
        get isConnected() {
            let cur = this;
            while (cur) {
                if (cur === doc.body) return true;
                cur = cur.parentNode;
            }
            return false;
        }
        get options() {
            const opts = [];
            for (const c of this.children) {
                if (c.tagName === 'OPTION') opts.push(c);
                else if (c.tagName === 'OPTGROUP') {
                    for (const gc of c.children) {
                        if (gc.tagName === 'OPTION') opts.push(gc);
                    }
                }
            }
            return opts;
        }
        get value() {
            if (this.tagName === 'SELECT') {
                const opts = this.options;
                const sel = opts.find(o => o.selected);
                if (sel) return sel.value;
                return opts[0] ? opts[0].value : '';
            }
            return this._value;
        }
        set value(v) {
            const str = String(v ?? '');
            if (this.tagName === 'SELECT') {
                const opts = this.options;
                let matched = false;
                for (const o of opts) {
                    if (!matched && o.value === str) {
                        o.selected = true;
                        matched = true;
                    } else {
                        o.selected = false;
                    }
                }
                this._value = matched ? str : (opts[0] ? opts[0].value : '');
            } else {
                this._value = str;
            }
        }
        get selectedIndex() {
            const opts = this.options;
            const idx = opts.findIndex(o => o.selected);
            return idx >= 0 ? idx : (opts.length > 0 ? 0 : -1);
        }
        appendChild(child) {
            if (child.parentNode) child.parentNode.removeChild(child);
            this.children.push(child);
            child.parentNode = this;
            child.parentElement = this;
            return child;
        }
        append(...nodes) { nodes.forEach(n => this.appendChild(n)); }
        removeChild(child) {
            const idx = this.children.indexOf(child);
            if (idx >= 0) {
                this.children.splice(idx, 1);
                child.parentNode = null;
                child.parentElement = null;
            }
            return child;
        }
        remove() {
            if (this.parentNode) this.parentNode.removeChild(this);
        }
        setAttribute(k, v) {
            this.attributes[k] = String(v);
            if (k === 'id') this.id = String(v);
            if (k === 'name') this.name = String(v);
        }
        removeAttribute(k) {
            delete this.attributes[k];
        }
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; }
        addEventListener(type, fn) {
            if (!this.listeners.has(type)) this.listeners.set(type, new Set());
            this.listeners.get(type).add(fn);
        }
        removeEventListener(type, fn) {
            this.listeners.get(type)?.delete(fn);
        }
        dispatchEvent(evt) {
            const eventObj = evt || { type: '' };
            if (!eventObj.target) eventObj.target = this;
            for (const fn of Array.from(this.listeners.get(eventObj.type) || [])) {
                fn.call(this, eventObj);
            }
            return true;
        }
        click() {
            if (typeof this.onclick === 'function') this.onclick({ target: this, stopPropagation() {}, preventDefault() {} });
            return this.dispatchEvent({ type: 'click', target: this, stopPropagation() {}, preventDefault() {} });
        }
        querySelectorAll(sel) { return findAll(this, sel); }
        querySelector(sel) { return findAll(this, sel)[0] || null; }
        closest(sel) {
            let cur = this;
            while (cur) {
                if (matchesSimpleSelector(cur, sel)) return cur;
                cur = cur.parentNode;
            }
            return null;
        }
        focus() {}
        getBoundingClientRect() { return { top: 0, bottom: 20, left: 0, right: 100, width: 100, height: 20 }; }
    }

    const docListeners = new Map();
    const doc = {
        body: new MiniElement('body'),
        title: 'Initial Title',
        createElement(tag) { return new MiniElement(tag); },
        getElementById(id) { return findAll(doc.body, `#${id}`)[0] || null; },
        querySelectorAll(sel) { return findAll(doc.body, sel); },
        querySelector(sel) { return findAll(doc.body, sel)[0] || null; },
        addEventListener(type, fn) {
            if (!docListeners.has(type)) docListeners.set(type, new Set());
            docListeners.get(type).add(fn);
        },
        removeEventListener(type, fn) { docListeners.get(type)?.delete(fn); },
    };
    return { doc, MiniElement };
}

function loadCardFormContext(fetchImpl) {
    const { doc, MiniElement } = createMiniDom();
    const winListeners = new Map();
    const messages = [];
    const win = {
        document: doc,
        t: null,
        i18n: { language: 'zh-CN' },
        localStorage: { getItem: () => null, setItem: () => {} },
        characterCards: {},
        addEventListener(type, fn) {
            if (!winListeners.has(type)) winListeners.set(type, new Set());
            winListeners.get(type).add(fn);
        },
        removeEventListener(type, fn) { winListeners.get(type)?.delete(fn); },
        dispatchEvent(evt) {
            for (const fn of Array.from(winListeners.get(evt.type) || [])) {
                fn.call(win, evt);
            }
            return true;
        },
        innerHeight: 800,
        requestAnimationFrame: () => 1,
        cancelAnimationFrame: () => {},
        nekoCharacterProfileSync: {
            getSyncChoice: async () => false,
            afterCardSave: async () => {},
            afterSave: async () => {},
        },
    };
    class EventMock { constructor(type, opts = {}) { this.type = type; this.detail = opts.detail; } }

    const ctx = vm.createContext({
        window: win,
        document: doc,
        Element: MiniElement,
        Event: EventMock,
        CustomEvent: EventMock,
        CSS: { escape: v => String(v) },
        AbortController,
        getComputedStyle: () => ({
            paddingTop: '0',
            paddingBottom: '0',
            borderTopWidth: '0',
            borderBottomWidth: '0',
            lineHeight: '20',
            minHeight: '0',
            maxHeight: 'none',
        }),
        console,
        fetch: fetchImpl,
        setTimeout,
        clearTimeout,
        requestAnimationFrame: () => 1,
        cancelAnimationFrame: () => {},
        encodeURIComponent,
        URLSearchParams,
        screen: { width: 1920, height: 1080 },
        showMessage: (msg, type) => messages.push({ msg, type }),
        showAlert: async () => {},
        showAlertDialog: async () => {},
        ensureValidCharacterProfileName: async () => true,
        ensureSafeExistingCharacterPathName: async () => true,
        collectCharacterFields: (form, opts) => {
            const data = Object.assign({}, (opts && opts.baseData) || {});
            const exclude = new Set((opts && opts.excludeFieldNames) || []);
            const fieldOrder = [];
            form.querySelectorAll('input, textarea').forEach(el => {
                if (el.name && !exclude.has(el.name)) {
                    data[el.name] = el.value;
                    fieldOrder.push(el.name);
                }
            });
            return { data, duplicateKey: false, fieldOrder };
        },
        attachCharacterFieldOrderPayload: () => {},
        buildLocalCatgirlRawData: (name, data) => ({ '档案名': name, ...data }),
        syncCharacterCardCache: () => {},
        applyLocalVoiceIdToRawData: (raw, voiceId) => { raw.voice_id = voiceId; },
        setLocalRawDataFieldOrder: () => {},
        mergeFreshCatgirlRawDataWithLocal: (fresh, local) => Object.assign({}, fresh, local),
        loadCharacterData: async () => ({
            '猫娘': {
                NekoA: { '档案名': 'NekoA', model_type: 'live2d', live2d: 'mao_pro' },
            },
        }),
        loadCharacterCards: async () => {},
        closeCatgirlPanel: () => {},
        panelAttachAutoSaveListener: () => {},
        createVoiceConfigSwitchOpId: () => 'op-1',
        notifyVoiceConfigSwitching: () => {},
        readCharacterPersonalitySelection: (cat) => {
            const override = cat && cat._reserved && cat._reserved.persona_override;
            if (override && override.preset_id) {
                return { hasOverride: true, displayName: override.preset_id };
            }
            return { hasOverride: false, displayName: '' };
        },
        getWorkshopHiddenFields: () => ['voice_id', 'model_type', 'live2d', '_reserved'],
        getOrderedCharacterFieldKeys: (cat, exclude) => {
            const ex = new Set(exclude || []);
            return Object.keys(cat || {}).filter(k => !ex.has(k));
        },
        normalizeCharacterFieldName: (k) => String(k || '').trim(),
        buildCharacterLanguagePreferenceRow: () => null,
        openManagedPopup: () => null,
    });
    win.window = win;

    const voiceUtilsCode = fs.readFileSync(
        path.join(ROOT, 'static/js/voice_display_utils.js'),
        'utf8'
    );
    vm.runInContext(voiceUtilsCode, ctx);

    const code = fs.readFileSync(
        path.join(ROOT, 'static/js/character_card_manager/card-form-and-actions.js'),
        'utf8'
    );
    vm.runInContext(code, ctx);
    return { ctx, win, doc, messages };
}

test('F0050: _loadPanelVoices failure preserves saved voice_id and prevents /unregister_voice', async () => {
    for (const mode of ['http500', 'badJson', 'networkError']) {
        for (const savedVoiceId of ['custom123', 'gsv:speaker1']) {
            const calls = [];
            const fetchImpl = async (url, init = {}) => {
                calls.push({ url: String(url), method: init.method || 'GET', body: init.body });
                if (String(url) === '/api/characters/voices') {
                    if (mode === 'http500') return { ok: false, status: 500, json: async () => ({}) };
                    if (mode === 'badJson') return { ok: true, status: 200, json: async () => { throw new SyntaxError('bad json'); } };
                    throw new Error('network down');
                }
                if (String(url).startsWith('/api/characters/catgirl/')) {
                    return { ok: true, status: 200, json: async () => ({ success: true, character_name: 'NekoA' }) };
                }
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            };

            const { ctx, doc } = loadCardFormContext(fetchImpl);
            const overlay = doc.createElement('div');
            overlay.className = 'catgirl-panel-overlay active';
            const container = doc.createElement('div');
            overlay.appendChild(container);
            doc.body.appendChild(overlay);

            ctx.buildCatgirlDetailForm('NekoA', {
                '档案名': 'NekoA',
                '性格': '温柔',
                voice_id: savedVoiceId,
                model_type: 'vrm',
            }, false, container);

            const form = container.querySelector('form');
            await form._voicesLoadPromise;

            const voiceSelect = form.querySelector('select[name="voice_id"]');
            assert.equal(voiceSelect._voiceCatalogLoaded, false, `${mode}/${savedVoiceId}: catalog loaded must be false`);
            assert.equal(voiceSelect.value, savedVoiceId, `${mode}/${savedVoiceId}: select.value must preserve saved voice_id`);
            const optValues = voiceSelect.options.map(o => o.value);
            assert.ok(optValues.includes(savedVoiceId), `${mode}/${savedVoiceId}: fallback option must exist`);

            // Even if select.value somehow turned empty without user interaction, save must NOT unregister
            voiceSelect.value = '';
            const ok = await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
            assert.equal(ok, true);
            assert.equal(
                calls.some(c => c.url.includes('/unregister_voice')),
                false,
                `${mode}/${savedVoiceId}: must never POST /unregister_voice when catalog failed to load`
            );
        }
    }
});

test('F0050: explicit user selection of empty voice after 200 catalog load still calls /unregister_voice', async () => {
    const calls = [];
    const fetchImpl = async (url, init = {}) => {
        calls.push({ url: String(url), method: init.method || 'GET', body: init.body });
        if (String(url) === '/api/characters/voices') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    voices: { custom123: { prefix: 'CustomVoice', provider: 'minimax', source: 'clone' } },
                    free_voices: {},
                    native_voices: {},
                }),
            };
        }
        if (String(url) === '/api/characters/gptsovits_voices') {
            return { ok: true, status: 200, json: async () => ({ success: true, voices: [] }) };
        }
        return { ok: true, status: 200, json: async () => ({ success: true, character_name: 'NekoA' }) };
    };

    const { ctx, doc } = loadCardFormContext(fetchImpl);
    const overlay = doc.createElement('div');
    overlay.className = 'catgirl-panel-overlay active';
    const container = doc.createElement('div');
    overlay.appendChild(container);
    doc.body.appendChild(overlay);

    ctx.buildCatgirlDetailForm('NekoA', {
        '档案名': 'NekoA',
        '性格': '温柔',
        voice_id: 'custom123',
        model_type: 'vrm',
    }, false, container);

    const form = container.querySelector('form');
    await form._voicesLoadPromise;

    const voiceSelect = form.querySelector('select[name="voice_id"]');
    assert.equal(voiceSelect._voiceCatalogLoaded, true);
    voiceSelect.value = '';
    voiceSelect.dispatchEvent({ type: 'change' });

    await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
    assert.equal(
        calls.some(c => c.url === '/api/characters/catgirl/NekoA/unregister_voice' && c.method === 'POST'),
        true,
        'explicit clear after successful catalog load must call /unregister_voice'
    );
});

test('F0441: personality update and clear refresh in place without reverting edited fields', async () => {
    let serverSelection = { mode: 'override', preset_id: 'tsundere_sister', source: 'preset', selected_at: '2026-01-01', profile: {} };
    const fetchImpl = async (url, init = {}) => {
        if (String(url).endsWith('/persona-selection')) {
            if ((init.method || 'GET') === 'DELETE') {
                serverSelection = { mode: 'default', preset_id: '', profile: {} };
            }
            return { ok: true, status: 200, json: async () => ({ success: true, selection: serverSelection }) };
        }
        if (String(url) === '/api/characters/voices') {
            return { ok: true, status: 200, json: async () => ({ voices: {} }) };
        }
        if (String(url) === '/api/characters/gptsovits_voices') {
            return { ok: true, status: 200, json: async () => ({ success: true, voices: [] }) };
        }
        return { ok: true, status: 200, json: async () => ({ success: true }) };
    };

    const { ctx, win, doc } = loadCardFormContext(fetchImpl);
    const overlay = doc.createElement('div');
    overlay.className = 'catgirl-panel-overlay active';
    const container = doc.createElement('div');
    overlay.appendChild(container);
    doc.body.appendChild(overlay);

    ctx.buildCatgirlDetailForm('NekoA', {
        '档案名': 'NekoA',
        '性格': '旧性格',
    }, false, container);

    const formBefore = container.querySelector('form');
    const personalityTextarea = formBefore.querySelector('textarea[name="性格"]');
    assert.equal(personalityTextarea.value, '旧性格');
    personalityTextarea.value = '新性格';

    // Dispatch personality updated event
    win.dispatchEvent(new ctx.CustomEvent('neko:character-personality-updated', {
        detail: { characterName: 'NekoA' }
    }));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    const formAfterUpdate = container.querySelector('form');
    assert.equal(formAfterUpdate, formBefore, 'form element must NOT be rebuilt');
    assert.equal(personalityTextarea.value, '新性格', 'edited field value must survive personality update');
    const clearBtn = formBefore.querySelector('[data-testid="character-personality-clear"]');
    assert.equal(clearBtn.disabled, false, 'clear button must be enabled when override is active');

    // Click personality clear button
    await clearBtn.click();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    assert.equal(container.querySelector('form'), formBefore, 'form element must NOT be rebuilt on clear');
    assert.equal(personalityTextarea.value, '新性格', 'edited field value must survive personality clear');
    assert.equal(clearBtn.disabled, true, 'clear button must become disabled after clearing override');
});

test('F0443: saveCatgirlFromPanel skips /l2d PUT when #preview-motion-select is missing, empty, or disabled', async () => {
    const l2dCalls = [];
    const fetchImpl = async (url, init = {}) => {
        if (String(url).startsWith('/api/characters/catgirl/l2d/')) {
            l2dCalls.push({ url: String(url), body: JSON.parse(init.body) });
            return { ok: true, status: 200, json: async () => ({ success: true }) };
        }
        if (String(url) === '/api/characters/voices') {
            return { ok: true, status: 200, json: async () => ({ voices: {} }) };
        }
        if (String(url) === '/api/characters/gptsovits_voices') {
            return { ok: true, status: 200, json: async () => ({ success: true, voices: [] }) };
        }
        return { ok: true, status: 200, json: async () => ({ success: true, character_name: 'NekoA' }) };
    };

    const { ctx, doc } = loadCardFormContext(fetchImpl);
    const overlay = doc.createElement('div');
    overlay.className = 'catgirl-panel-overlay active';
    const container = doc.createElement('div');
    overlay.appendChild(container);
    doc.body.appendChild(overlay);

    ctx.buildCatgirlDetailForm('NekoA', {
        '档案名': 'NekoA',
        model_type: 'live2d',
        live2d: 'mao_pro',
    }, false, container);
    let form = container.querySelector('form');
    await form._voicesLoadPromise;

    // Case 1: #preview-motion-select absent
    await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
    assert.equal(l2dCalls.length, 0, 'must not PUT /l2d when #preview-motion-select does not exist');
    form = container.querySelector('form');
    await form._voicesLoadPromise;

    // Case 2: #preview-motion-select present with 0 options
    const motionSelect = doc.createElement('select');
    motionSelect.id = 'preview-motion-select';
    doc.body.appendChild(motionSelect);
    await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
    assert.equal(l2dCalls.length, 0, 'must not PUT /l2d when #preview-motion-select has 0 options');
    form = container.querySelector('form');
    await form._voicesLoadPromise;

    // Case 3: #preview-motion-select disabled
    const placeholderOpt = doc.createElement('option');
    placeholderOpt.value = '';
    motionSelect.appendChild(placeholderOpt);
    motionSelect.disabled = true;
    await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
    assert.equal(l2dCalls.length, 0, 'must not PUT /l2d when #preview-motion-select is disabled');
    form = container.querySelector('form');
    await form._voicesLoadPromise;

    // Case 4: #preview-motion-select enabled with options -> MUST PUT
    motionSelect.disabled = false;
    const idleOpt = doc.createElement('option');
    idleOpt.value = 'motions/idle.motion3.json';
    motionSelect.appendChild(idleOpt);
    motionSelect.value = 'motions/idle.motion3.json';
    await ctx.saveCatgirlFromPanel(form, 'NekoA', false);
    assert.equal(l2dCalls.length, 1);
    assert.deepEqual(l2dCalls[0].body, { live2d_idle_animation: 'motions/idle.motion3.json' });
});

test('F0353: removeScreenshotFromList immediately excludes item from count, sync, and send within 300ms window', async () => {
    const { doc } = createMiniDom();
    const sentFrames = [];
    const syncedSnapshots = [];
    const scheduledTimers = [];
    let nextTimerId = 1;

    const fakeSetTimeout = (fn, ms) => {
        const id = nextTimerId++;
        scheduledTimers.push({ id, fn, ms, cleared: false });
        return id;
    };
    const fakeClearTimeout = (id) => {
        const t = scheduledTimers.find(x => x.id === id);
        if (t) t.cleared = true;
    };

    const mountIds = [
        'micButton', 'muteButton', 'screenButton', 'stopButton', 'resetSessionButton',
        'returnSessionButton', 'textSendButton', 'textInputBox', 'screenshotButton',
        'screenshots-list', 'screenshot-thumbnail-container', 'screenshot-count', 'clear-all-screenshots',
    ];
    for (const id of mountIds) {
        const el = doc.createElement(id === 'textInputBox' ? 'textarea' : 'div');
        el.id = id;
        doc.body.appendChild(el);
    }

    const appState = {
        dom: {},
        screenshotCounter: 0,
        isTextSessionActive: true,
        isSwitchingCatgirl: false,
        socket: {
            readyState: 1,
            send(raw) { sentFrames.push(JSON.parse(raw)); },
        },
    };

    const win = {
        document: doc,
        location: { pathname: '/' },
        appState,
        appConst: {},
        appUtils: { isMobile: () => false },
        t: null,
        setInterval: () => 1,
        clearInterval: () => {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {},
        showStatusToast() {},
        appendMessage() {},
        resetProactiveChatBackoff() {},
        unlockAchievement: async () => {},
        reactChatWindowHost: {
            setComposerAttachments(list) { syncedSnapshots.push(list.slice()); },
            setOnComposerRemoveAttachment() {},
            setOnComposerImportImage() {},
            setOnComposerScreenshot() {},
            setOnComposerSubmit() {},
        },
        navigator: {},
    };
    win.window = win;

    const ctx = vm.createContext({
        window: win,
        document: doc,
        WebSocket: { OPEN: 1 },
        CustomEvent: class { constructor(type, o = {}) { this.type = type; this.detail = o.detail; } },
        File: class {},
        Image: class {
            set src(v) {
                this._src = v;
                this.naturalWidth = 100;
                this.naturalHeight = 100;
                this.width = 100;
                this.height = 100;
                if (typeof this.onload === 'function') this.onload();
            }
            get src() { return this._src; }
        },
        console,
        setTimeout: fakeSetTimeout,
        clearTimeout: fakeClearTimeout,
    });
    const code = fs.readFileSync(path.join(ROOT, 'static/app/app-buttons.js'), 'utf8');
    vm.runInContext(code, ctx);
    win.appButtons.init();

    const itemA = win.appButtons.addScreenshotToList('data:image/jpeg;base64,AAAA', null, { source: 'screenshot' });
    const itemB = win.appButtons.addScreenshotToList('data:image/jpeg;base64,BBBB', null, { source: 'screenshot' });
    assert.equal(doc.getElementById('screenshot-count').textContent, '2');

    syncedSnapshots.length = 0;
    win.appButtons.removeScreenshotFromList(itemA);

    // Immediately before 300ms animation timer fires:
    assert.equal(itemA.dataset.removing, '1');
    assert.equal(doc.getElementById('screenshot-count').textContent, '1');
    assert.deepEqual(
        Array.from(win.appButtons.getPendingComposerAttachments(), a => a.url),
        ['data:image/jpeg;base64,BBBB']
    );
    assert.equal(syncedSnapshots.length, 1, 'syncPendingComposerAttachments must run immediately');
    assert.deepEqual(Array.from(syncedSnapshots[0], a => a.url), ['data:image/jpeg;base64,BBBB']);

    // Send text + remaining screenshot B within 300ms -> only B and text should be sent (never A)
    await win.appButtons.sendTextPayload('hello', { source: 'react-chat-window' });
    const imageFrames = sentFrames.filter(f => f.action === 'stream_data' && String(f.data || '').startsWith('data:'));
    assert.equal(imageFrames.length, 1);
    assert.equal(imageFrames[0].data, 'data:image/jpeg;base64,BBBB');
    assert.equal(sentFrames.some(f => f.data === 'data:image/jpeg;base64,AAAA'), false);

    // Now test removing the ONLY screenshot and sending empty text within 300ms -> 0 frames sent
    sentFrames.length = 0;
    const itemC = win.appButtons.addScreenshotToList('data:image/jpeg;base64,CCCC', null, { source: 'screenshot' });
    win.appButtons.removeScreenshotFromList(itemC);
    assert.equal(doc.getElementById('screenshot-count').textContent, '0');
    const sentResult = await win.appButtons.sendTextPayload('', { source: 'react-chat-window' });
    assert.notEqual(sentResult, true);
    assert.equal(sentFrames.length, 0, 'removed screenshot must not be sent during 300ms window');

    // Run 300ms cleanup timers -> DOM element removed and container loses .show
    const removeTimers = scheduledTimers.filter(t => t.ms === 300 && !t.cleared);
    assert.ok(removeTimers.length >= 2);
    removeTimers.forEach(t => t.fn());
    assert.equal(doc.getElementById('screenshots-list').children.length, 0);
    assert.equal(doc.getElementById('screenshot-thumbnail-container').classList.contains('show'), false);
});

test('F0355: handleCatgirlSwitch restores chatContainer, reactChatWindowHost, title, and turn globals on rollback', async () => {
    const { doc } = createMiniDom();
    const chatContainer = doc.createElement('div');
    chatContainer.id = 'chatContainer';
    const oldNode = doc.createElement('div');
    oldNode.textContent = 'Old Legacy Bubble';
    chatContainer.appendChild(oldNode);
    doc.body.appendChild(chatContainer);
    doc.title = 'CatA Terminal - Project N.E.K.O.';

    let reactMessages = [
        { id: 'm1', sender: 'user', text: 'hi CatA' },
        { id: 'm2', sender: 'assistant', text: 'meow from CatA' },
    ];
    let watchdogCallback = null;
    let shouldFailAudioClear = false;
    let shouldFailModelLoad = false;
    let hangForever = false;

    const appState = {
        isSwitchingCatgirl: false,
        _currentSwitchAttemptId: null,
        _switchAttemptCounter: 0,
        socket: { readyState: 1, close() {} },
        heartbeatInterval: null,
        incomingAudioEpoch: 0,
        incomingAudioBlobQueue: [],
        pendingAudioChunkMetaQueue: [],
        isRecording: false,
        isTextSessionActive: false,
    };

    const win = {
        document: doc,
        location: { pathname: '/' },
        appState,
        lanlan_config: { lanlan_name: 'CatA', model_type: 'live2d', live3d_sub_type: '' },
        currentGeminiMessage: { id: 'g1' },
        _geminiTurnFullText: 'partial reply',
        _geminiTurnEndSealed: true,
        currentTurnGeminiBubbles: ['b1'],
        currentTurnGeminiAttachments: ['att1'],
        invalidatePendingMusicSearch() {},
        showStatusToast() {},
        clearAudioQueue: async () => {
            if (shouldFailAudioClear) throw new Error('audio clear failed');
            if (hangForever) return new Promise(() => {});
        },
        connectWebSocket() { appState.socket = { readyState: 1, close() {} }; },
        syncVoiceChatComposerHidden() {},
        resetAgentUiForCharacterSwitch: async () => {},
        unlockAchievement: async () => {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {},
        live2dManager: {
            clearEmotion: () => {},
            setModelVisible: () => {},
            initPIXI: async () => {},
            loadUserPreferences: async () => [],
            getCurrentModel: () => null,
            loadModel: async () => {
                if (shouldFailModelLoad) throw new Error('model file missing');
                return {};
            },
            pixi_app: { renderer: {}, ticker: { started: true, stop() {}, start() {}, update() {} } },
        },
        innerWidth: 1280,
        reactChatWindowHost: {
            getState: () => ({ messages: reactMessages.slice() }),
            clearMessages: () => { reactMessages = []; },
            setMessages: (msgs) => { reactMessages = msgs.slice(); },
        },
        _resetReactChatSwitchState() {},
    };
    win.window = win;

    const fakeFetch = async (url) => {
        if (String(url) === '/api/characters') {
            return {
                ok: true,
                json: async () => ({
                    '猫娘': {
                        CatA: { model_type: 'live2d', live2d: 'CatA' },
                        CatB: { model_type: 'live2d', live2d: 'CatB' },
                    },
                }),
            };
        }
        if (String(url).startsWith('/api/characters/current_live2d_model')) {
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    model_type: 'live2d',
                    model_info: { path: '/static/live2d/CatB/CatB.model3.json', name: 'CatB' },
                }),
            };
        }
        return { ok: true, json: async () => ({ success: true }) };
    };

    const ctx = vm.createContext({
        window: win,
        document: doc,
        location: { pathname: '/' },
        fetch: fakeFetch,
        WebSocket: { OPEN: 1, CLOSED: 3, CLOSING: 2 },
        CustomEvent: class { constructor(type, o = {}) { this.type = type; this.detail = o.detail; } },
        console,
        setTimeout(fn, ms) {
            if (ms === 45000) {
                watchdogCallback = fn;
                return 999;
            }
            return setTimeout(fn, ms);
        },
        clearTimeout,
        clearInterval,
        encodeURIComponent,
    });
    const code = fs.readFileSync(path.join(ROOT, 'static/app/app-character.js'), 'utf8');
    vm.runInContext(code, ctx);

    // Case 1: failure in clearAudioQueue before connectWebSocket
    shouldFailAudioClear = true;
    await win.handleCatgirlSwitch('CatB', 'CatA');
    assert.equal(win.lanlan_config.lanlan_name, 'CatA');
    assert.equal(doc.title, 'CatA Terminal - Project N.E.K.O.');
    assert.equal(chatContainer.children.length, 1);
    assert.equal(chatContainer.children[0].textContent, 'Old Legacy Bubble');
    assert.deepEqual(reactMessages.map(m => m.id), ['m1', 'm2']);
    assert.equal(win._geminiTurnFullText, 'partial reply');
    assert.deepEqual(win.currentTurnGeminiBubbles, ['b1']);

    // Case 2: failure in loadModel AFTER connectWebSocket
    shouldFailAudioClear = false;
    shouldFailModelLoad = true;
    await win.handleCatgirlSwitch('CatB', 'CatA');
    assert.equal(win.lanlan_config.lanlan_name, 'CatA');
    assert.equal(doc.title, 'CatA Terminal - Project N.E.K.O.');
    assert.equal(chatContainer.children.length, 1);
    assert.deepEqual(reactMessages.map(m => m.id), ['m1', 'm2']);

    // Case 3: 45s watchdog timeout rollback
    shouldFailModelLoad = false;
    hangForever = true;
    void win.handleCatgirlSwitch('CatB', 'CatA');
    await new Promise(r => setTimeout(r, 10));
    assert.equal(typeof watchdogCallback, 'function');
    assert.equal(reactMessages.length, 0, 'cleared during hang before watchdog');
    watchdogCallback();
    assert.equal(win.lanlan_config.lanlan_name, 'CatA');
    assert.equal(chatContainer.children.length, 1);
    assert.deepEqual(reactMessages.map(m => m.id), ['m1', 'm2']);

    // Case 4: successful switch commits with cleared chat UI
    hangForever = false;
    appState.isSwitchingCatgirl = false;
    await win.handleCatgirlSwitch('CatB', 'CatA');
    assert.equal(win.lanlan_config.lanlan_name, 'CatB');
    assert.equal(reactMessages.length, 0);
    assert.equal(chatContainer.children.length, 0);
});
