const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const materialsSource = fs.readFileSync(path.join(root, 'static/theme-studio/materials.js'), 'utf8');
const appSource = fs.readFileSync(path.join(root, 'static/theme-studio/app.js'), 'utf8');

function createMiniDom() {
  const byIdRegistry = new Map();
  const docListeners = new Map();

  function makeClassList(node) {
    const tokens = new Set();
    const syncClass = () => {
      node._className = Array.from(tokens).join(' ');
    };
    return {
      add(...names) {
        for (const n of names) if (n) tokens.add(String(n));
        syncClass();
      },
      remove(...names) {
        for (const n of names) tokens.delete(String(n));
        syncClass();
      },
      toggle(name, force) {
        const key = String(name);
        const next = force === undefined ? !tokens.has(key) : !!force;
        if (next) tokens.add(key);
        else tokens.delete(key);
        syncClass();
        return next;
      },
      contains(name) {
        return tokens.has(String(name));
      },
      _replaceFromString(str) {
        tokens.clear();
        for (const part of String(str || '').split(/\s+/)) {
          if (part) tokens.add(part);
        }
        syncClass();
      },
    };
  }

  function createNode(tagName, nodeType = 1) {
    const listeners = new Map();
    const attrs = new Map();
    const style = {
      setProperty(k, v) { this[k] = String(v); },
      removeProperty(k) { delete this[k]; },
    };
    const node = {
      nodeType,
      tagName: String(tagName || '').toUpperCase(),
      children: [],
      parentNode: null,
      dataset: {},
      style,
      value: '',
      checked: false,
      disabled: false,
      hidden: false,
      _id: '',
      _className: '',
      _text: '',
      get id() { return this._id; },
      set id(val) {
        if (this._id && byIdRegistry.get(this._id) === this) byIdRegistry.delete(this._id);
        this._id = String(val || '');
        if (this._id) byIdRegistry.set(this._id, this);
      },
      get className() { return this._className; },
      set className(val) { this.classList._replaceFromString(val); },
      get firstChild() { return this.children[0] || null; },
      get textContent() {
        if (this.nodeType === 3) return this._text;
        if (!this.children.length) return this._text;
        return this.children.map((c) => c.textContent).join('');
      },
      set textContent(val) {
        if (this.nodeType === 3) {
          this._text = String(val ?? '');
          return;
        }
        this.children.length = 0;
        this._text = String(val ?? '');
      },
      appendChild(child) {
        if (!child) return child;
        child.parentNode = this;
        this.children.push(child);
        return child;
      },
      removeChild(child) {
        const idx = this.children.indexOf(child);
        if (idx >= 0) {
          this.children.splice(idx, 1);
          child.parentNode = null;
        }
        return child;
      },
      insertBefore(child, ref) {
        const idx = ref ? this.children.indexOf(ref) : -1;
        child.parentNode = this;
        if (idx >= 0) this.children.splice(idx, 0, child);
        else this.children.push(child);
        return child;
      },
      setAttribute(name, value) {
        const key = String(name);
        const val = String(value);
        attrs.set(key, val);
        if (key === 'id') this.id = val;
        if (key === 'class') this.className = val;
      },
      getAttribute(name) {
        return attrs.has(String(name)) ? attrs.get(String(name)) : null;
      },
      removeAttribute(name) {
        attrs.delete(String(name));
      },
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        const set = listeners.get(type);
        if (set) set.delete(fn);
      },
      dispatchEvent(event) {
        const ev = event && typeof event === 'object' ? event : { type: String(event) };
        ev.target = ev.target || this;
        ev.currentTarget = this;
        if (typeof ev.preventDefault !== 'function') ev.preventDefault = () => {};
        if (typeof ev.stopPropagation !== 'function') ev.stopPropagation = () => {};
        const set = listeners.get(ev.type);
        if (set) {
          for (const fn of Array.from(set)) fn.call(this, ev);
        }
        return true;
      },
      click() {
        this.dispatchEvent({ type: 'click' });
      },
      contains(other) {
        if (other === this) return true;
        return this.children.some((c) => c.contains && c.contains(other));
      },
      closest() { return null; },
      getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    };
    node.classList = makeClassList(node);
    return node;
  }

  function findDescendants(rootNode, predicate, out = []) {
    for (const child of rootNode.children || []) {
      if (predicate(child)) out.push(child);
      findDescendants(child, predicate, out);
    }
    return out;
  }

  const document = {
    readyState: 'loading',
    activeElement: null,
    createElement(tag) { return createNode(tag, 1); },
    createTextNode(text) {
      const node = createNode('#text', 3);
      node._text = String(text ?? '');
      return node;
    },
    getElementById(id) {
      return byIdRegistry.get(String(id)) || null;
    },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, new Set());
      docListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      const set = docListeners.get(type);
      if (set) set.delete(fn);
    },
    hasFocus() { return true; },
  };

  return { document, createNode, findDescendants, docListeners };
}

function makePresetSpecParams(blurVal) {
  return {
    global: { blur: blurVal },
    lighting: {},
    refraction: {},
    surface: {},
    animation: {},
  };
}

function makeBaseMaterials(blurVal, customPresets) {
  return {
    version: 1,
    enabled: true,
    preset: 'custom',
    quality: 'balanced',
    lock: false,
    ...makePresetSpecParams(blurVal),
    dark: {},
    components: {},
    custom_presets: customPresets,
  };
}

function createStudioHarness(initialMaterials) {
  const { document, createNode, findDescendants, docListeners } = createMiniDom();

  const materialsRoot = createNode('div');
  materialsRoot.id = 'materials-root';

  const themeNameInput = createNode('input');
  themeNameInput.id = 'theme-name';
  const themeEnabledInput = createNode('input');
  themeEnabledInput.id = 'theme-enabled';
  const metaNameNode = createNode('span');
  metaNameNode.id = 'meta-name';
  const metaUpdatedNode = createNode('span');
  metaUpdatedNode.id = 'meta-updated';

  const winListeners = new Map();
  const windowObj = {
    location: { origin: 'http://127.0.0.1:48911', pathname: '/theme_studio', search: '' },
    document,
    i18n: { isInitialized: true },
    __NTS_PANEL_BOOTED__: true,
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, new Set());
      winListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      const set = winListeners.get(type);
      if (set) set.delete(fn);
    },
    NekoPanel: {
      call(entryId) {
        if (entryId === 'material_spec') {
          return Promise.resolve({
            version: 1,
            params: [
              { key: 'blur', section: 'global', kind: 'px', min: 0, max: 60, default: 22, label: '模糊半径' },
            ],
            presets: {
              clear: { label: '清透', desc: 'Clear', ...makePresetSpecParams(14) },
            },
            quality: [{ id: 'balanced', label: '均衡' }],
            current: JSON.parse(JSON.stringify(initialMaterials)),
          });
        }
        if (entryId === 'preview_materials') {
          return Promise.resolve({ vars: {}, warnings: [] });
        }
        return Promise.resolve({});
      },
      toast() {},
    },
  };
  windowObj.window = windowObj;
  windowObj.top = windowObj;

  let nextTimerId = 1;
  const context = vm.createContext({
    window: windowObj,
    document,
    navigator: { clipboard: null },
    localStorage: {
      _data: new Map(),
      getItem(k) { return this._data.has(k) ? this._data.get(k) : null; },
      setItem(k, v) { this._data.set(k, String(v)); },
      removeItem(k) { this._data.delete(k); },
    },
    setTimeout() { return nextTimerId++; },
    clearTimeout() {},
    setInterval() { return nextTimerId++; },
    clearInterval() {},
    console,
    Promise,
    JSON,
    Object,
    Array,
    String,
    Number,
    Boolean,
    Math,
    Date,
    RegExp,
    Error,
    isFinite,
    parseFloat,
    parseInt,
    encodeURIComponent,
  });

  vm.runInContext(appSource, context, { filename: 'static/theme-studio/app.js' });
  const beforeMatHandlers = new Set(docListeners.get('DOMContentLoaded') || []);
  vm.runInContext(materialsSource, context, { filename: 'static/theme-studio/materials.js' });
  const matDomHandlers = Array.from(docListeners.get('DOMContentLoaded') || [])
    .filter((fn) => !beforeMatHandlers.has(fn));

  return {
    window: windowObj,
    document,
    materialsRoot,
    themeNameInput,
    themeEnabledInput,
    metaNameNode,
    metaUpdatedNode,
    findDescendants,
    async bootMaterials() {
      for (const fn of matDomHandlers) fn();
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

test('submitPayload submits only added/edited custom_presets and keepEdits sync reconciles concurrent changes', async () => {
  const basePresets = {
    DeletedByB: makePresetSpecParams(10),
    EditedByB: makePresetSpecParams(12),
    EditedByA: makePresetSpecParams(16),
  };
  const initial = makeBaseMaterials(18, basePresets);
  const harness = createStudioHarness(initial);
  await harness.bootMaterials();

  const { NekoMaterials, __ntsPanel } = harness.window;
  NekoMaterials.sync({ enabled: true, materials: JSON.parse(JSON.stringify(initial)) });
  assert.equal(NekoMaterials.isDirty(), false);

  const clickTab = (label) => {
    const nav = harness.document.getElementById('mt-tabs');
    const tab = nav.children.find((b) => b.textContent === label);
    assert.ok(tab, `missing tab ${label}`);
    tab.click();
  };

  // Modify blur via the base tab slider control.
  clickTab('基础材质');
  const pane = harness.document.getElementById('mt-pane');
  const rangeInput = harness.findDescendants(pane, (n) => n.tagName === 'INPUT' && n.getAttribute('type') === 'range')[0];
  assert.ok(rangeInput, 'expected blur range input');
  rangeInput.value = '26';
  rangeInput.dispatchEvent({ type: 'input' });
  assert.equal(NekoMaterials.isDirty(), true);

  // Add a brand-new custom preset AddedByA on the presets tab.
  clickTab('预设与画质');
  const saveCustomPreset = (name) => {
    const input = harness.document.getElementById('mt-preset-name');
    const saveRow = input.parentNode;
    const saveBtn = saveRow.children.find((n) => n.tagName === 'BUTTON');
    input.value = name;
    saveBtn.click();
  };
  saveCustomPreset('AddedByA');

  // Edit blur again and overwrite local draft preset EditedByA so it differs from BASELINE.
  clickTab('基础材质');
  const rangeAgain = harness.findDescendants(pane, (n) => n.tagName === 'INPUT' && n.getAttribute('type') === 'range')[0];
  rangeAgain.value = '29';
  rangeAgain.dispatchEvent({ type: 'input' });
  clickTab('预设与画质');
  saveCustomPreset('EditedByA');

  // submitPayload includes ONLY AddedByA and EditedByA; DeletedByB and EditedByB are excluded.
  const payload = NekoMaterials.submitPayload();
  assert.deepEqual(Object.keys(payload.custom_presets).sort(), ['AddedByA', 'EditedByA']);
  assert.equal(payload.custom_presets.EditedByA.global.blur, 29);
  assert.equal(payload.global.blur, 29);

  // currentMaterialsPayload() delegates to submitPayload(), whereas snapshot() keeps the full library.
  const panelPayload = __ntsPanel.currentMaterialsPayload();
  assert.deepEqual(Object.keys(panelPayload.custom_presets).sort(), ['AddedByA', 'EditedByA']);
  const fullSnap = NekoMaterials.snapshot();
  assert.deepEqual(
    Object.keys(fullSnap.custom_presets).sort(),
    ['AddedByA', 'DeletedByB', 'EditedByA', 'EditedByB'],
  );

  // Normal save readback clears dirty state when no edits happened during the save.
  const diskAfterSave = makeBaseMaterials(29, {
    EditedByB: makePresetSpecParams(12),
    EditedByA: makePresetSpecParams(29),
    AddedByA: makePresetSpecParams(26),
  });
  NekoMaterials.sync({ enabled: true, materials: diskAfterSave }, { submitted: fullSnap });
  assert.equal(NekoMaterials.isDirty(), false);

  // Concurrent edit while save is in flight (keepEdits=true):
  // Panel A sets blur=32 and submits, then edits blur=35 before readback returns.
  clickTab('基础材质');
  const rangeSave = harness.findDescendants(pane, (n) => n.tagName === 'INPUT' && n.getAttribute('type') === 'range')[0];
  rangeSave.value = '32';
  rangeSave.dispatchEvent({ type: 'input' });
  const inflightSnap = NekoMaterials.snapshot();

  rangeSave.value = '35';
  rangeSave.dispatchEvent({ type: 'input' });

  // Meanwhile, Panel B deleted EditedByA on disk and added AddedByB.
  const concurrentDisk = makeBaseMaterials(32, {
    EditedByB: makePresetSpecParams(40),
    AddedByA: makePresetSpecParams(26),
    AddedByB: makePresetSpecParams(15),
  });
  NekoMaterials.sync({ enabled: true, materials: concurrentDisk }, { submitted: inflightSnap });

  assert.equal(NekoMaterials.isDirty(), true);
  const afterKeepEdits = NekoMaterials.snapshot();
  assert.equal(afterKeepEdits.global.blur, 35);
  assert.equal('EditedByA' in afterKeepEdits.custom_presets, false, 'concurrent delete must not remain in draft');
  assert.equal(afterKeepEdits.custom_presets.EditedByB.global.blur, 40);
  assert.equal(afterKeepEdits.custom_presets.AddedByB.global.blur, 15);
  assert.deepEqual(Object.keys(NekoMaterials.submitPayload().custom_presets), []);
});

test('renderMeta tracks name and enabled drafts independently across palette readbacks', () => {
  const initial = makeBaseMaterials(22, {});
  const harness = createStudioHarness(initial);
  const { __ntsPanel } = harness.window;

  __ntsPanel.state.palette = { name: 'Old', enabled: false, updated_at: '2026-11-26T10:00:00Z' };
  __ntsPanel.renderMeta({ discard: true });
  assert.equal(harness.themeNameInput.value, 'Old');
  assert.equal(harness.themeEnabledInput.checked, false);
  assert.equal(__ntsPanel.metaDirtyCount(), 0);

  // User edits only the theme name while enabled remains false.
  harness.themeNameInput.value = 'RenamedDraft';
  assert.equal(__ntsPanel.metaDirtyCount(), 1);

  // Scheduled theme switch enables the newly selected theme on disk and triggers readback without discard.
  __ntsPanel.state.palette = { name: 'Scheduled', enabled: true, updated_at: '2026-11-26T12:00:00Z' };
  __ntsPanel.renderMeta();

  assert.equal(harness.themeNameInput.value, 'RenamedDraft');
  assert.equal(harness.themeEnabledInput.checked, true);
  assert.equal(__ntsPanel.state.metaName, 'Scheduled');
  assert.equal(__ntsPanel.state.metaEnabled, true);
  assert.equal(__ntsPanel.metaDirtyCount(), 1);

  // Reverse case: discard resets both, then user flips only enabled=false while disk renames the theme.
  __ntsPanel.renderMeta({ discard: true });
  assert.equal(harness.themeNameInput.value, 'Scheduled');
  assert.equal(harness.themeEnabledInput.checked, true);
  assert.equal(__ntsPanel.metaDirtyCount(), 0);

  harness.themeEnabledInput.checked = false;
  assert.equal(__ntsPanel.metaDirtyCount(), 1);

  __ntsPanel.state.palette = { name: 'Evening', enabled: true, updated_at: '2026-11-26T18:00:00Z' };
  __ntsPanel.renderMeta();

  assert.equal(harness.themeNameInput.value, 'Evening');
  assert.equal(harness.themeEnabledInput.checked, false);
  assert.equal(__ntsPanel.state.metaName, 'Evening');
  assert.equal(__ntsPanel.state.metaEnabled, true);
  assert.equal(__ntsPanel.metaDirtyCount(), 1);
});
