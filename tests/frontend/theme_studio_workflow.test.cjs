const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../static/theme-studio/app.js'), 'utf8');
const colorKey = '--nts-chat-bg';

function editor() {
  const nodes = new Map();
  const listeners = {};
  const storage = new Map();
  const timers = new Map();
  const notices = [];
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, hidden: true, checked: id === 'theme-enabled', value: '',
      classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
      addEventListener() {}, setAttribute() {}, appendChild() {}, focus() {},
      querySelector() { return null; }, querySelectorAll() { return []; }
    });
    return nodes.get(id);
  }
  const document = {
    readyState: 'loading', body: element('body'), getElementById: element,
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    createElement: element, dispatchEvent() {}
  };
  const panel = vm.createContext({
    console, document, navigator: { platform: 'Linux' }, URL, Date,
    location: { href: 'http://localhost:48911/theme_studio', origin: 'http://localhost:48911' },
    addEventListener() {}, confirm: () => true,
    setTimeout() { return 1; }, clearTimeout() {},
    setInterval(fn) { timers.set(1, fn); return 1; },
    clearInterval(id) { timers.delete(id); },
    localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) }
  });
  panel.window = panel;
  vm.runInContext(source, panel, { filename: 'theme-studio/app.js' });
  Object.assign(panel, {
    setBusy(busy) { panel.state.busy = busy; },
    showNotice(kind, message) { notices.push({ kind, message }); },
    showToast() {}, syncAll() {}, applyFilters() {}, setStatus() {},
    updateButtons() {}, updateUndoButtons() {}
  });
  panel.state.palette = { revision: 'v1', name: '', enabled: true, vars: {}, dark_vars: {} };
  return { panel, element, storage, timers, notices, listeners };
}

test('a second revision conflict retains the draft and current preview', async () => {
  const { panel, notices } = editor();
  panel.state.edited.light[colorKey] = '#fafafa';
  panel.state.previewing = 'preview-id';
  const attempts = [];
  panel.callPlugin = async (entry, args) => {
    if (entry === 'check_contrast') return { fails: [] };
    if (entry === 'get_theme') return { ...panel.state.palette, enabled: false, revision: 'v2' };
    assert.equal(entry, 'apply_theme');
    attempts.push({ revision: args.base_revision, sendsEnabled: Object.hasOwn(args, 'enabled') });
    return { applied: false, conflict: { disk_revision: attempts.length === 1 ? 'v2' : 'v3' } };
  };
  panel.confirm = () => true;
  let reloads = 0;
  panel.reloadPalette = async () => { reloads++; return panel.state.palette; };
  await panel.applyTheme();
  assert.deepEqual(attempts, [
    { revision: 'v1', sendsEnabled: false },
    { revision: 'v2', sendsEnabled: false }
  ]);
  assert.equal(reloads, 0);
  assert.equal(panel.state.edited.light[colorKey], '#fafafa');
  assert.equal(panel.state.previewing, 'preview-id');
  assert.ok(notices.some(item => item.kind === 'warn' || item.kind === 'error'));
});

test('preview stop sends the issued ID and the server deadline clears an expired preview', async () => {
  const { panel, timers } = editor();
  panel.state.palette.vars[colorKey] = '#ababab';
  panel.state.cleared.light[colorKey] = true;
  panel.state.edited.dark[colorKey] = '#112233';
  const calls = [];
  let deadline = Math.ceil(Date.now() / 1000) + 27;
  panel.callPlugin = async (entry, args) => {
    calls.push({ entry, ...args });
    return entry === 'start_preview' ? { preview: { id: 'preview-1', expires_at: deadline } } : {};
  };
  await panel.togglePreview({ seconds: 49 });
  assert.deepEqual({ ...calls[0].palette }, { [colorKey]: '' });
  assert.deepEqual({ ...calls[0].dark }, { [colorKey]: '#112233' });
  assert.equal(panel.state.previewing, 'preview-1');
  assert.equal(panel.state.previewDeadline, deadline * 1000);
  await panel.togglePreview();
  assert.equal(calls[1].id, 'preview-1');
  assert.equal(calls[1].entry, 'stop_preview');
  assert.equal(timers.size, 0);
  deadline = Math.floor(Date.now() / 1000) - 1;
  await panel.togglePreview();
  timers.get(1)();
  assert.equal(panel.state.previewing, null);
  assert.equal(timers.size, 0);
  assert.equal(calls.length, 3);
});

test('undo removes keys introduced by the applied theme in each palette', async () => {
  const { panel } = editor();
  const before = { name: 'old', enabled: false, vars: { a: 'pink' }, dark_vars: { b: 'blue' }, materials: { preset: 'smoke' } };
  panel.lastApplyUndo = panel.makeApplyUndo(before, '2', '', { revision: 'v2' });
  let restored;
  panel.callPlugin = async (entry, args) => {
    if (entry === 'get_theme') return { revision: 'v2', vars: { a: 'red', c: 'yellow' }, dark_vars: { d: 'gold' } };
    assert.equal(entry, 'apply_theme');
    restored = JSON.parse(JSON.stringify(args));
    return { applied: true };
  };
  panel.reloadPalette = async () => panel.state.palette;
  await panel.undoLastApply();
  assert.deepEqual(restored, { base_revision: 'v2', name: 'old', enabled: false, materials: { preset: 'smoke' }, vars: { a: 'pink', c: '' }, dark_vars: { b: 'blue', d: '' } });
  assert.equal(panel.lastApplyUndo, null);
});

test('restoring a palette-only draft preserves the live name and enabled flag', () => {
  const { panel, element, storage } = editor();
  panel.state.palette.name = 'Library theme';
  panel.state.palette.enabled = false;
  panel.renderMeta({ discard: true });
  storage.set(panel.DRAFT_KEY, JSON.stringify({ v: 1, ts: Date.now(), revision: 'v1', edited: { dark: { [colorKey]: '#123456' } } }));
  panel.restoreDraftIfAny();
  assert.equal(panel.state.edited.dark[colorKey], '#123456');
  assert.equal(element('theme-name').value, 'Library theme');
  assert.equal(element('theme-enabled').checked, false);
});

test('equivalent colors do not create changes in either palette', () => {
  const { panel } = editor();
  panel.state.palette.vars[colorKey] = '#fff';
  panel.state.palette.dark_vars[colorKey] = '#000';
  panel.markEditedForMode('light', colorKey, 'rgb(255,255,255)');
  panel.markEditedForMode('dark', colorKey, 'rgb(0,0,0)');
  assert.equal(panel.dirtyCount(), 0);
  panel.markEditedForMode('light', colorKey, 'rgba(255,255,255,0.5)');
  assert.equal(panel.dirtyCount(), 1);
});

test('applying a palette scheme can be undone as a single edit across both modes', () => {
  const { panel } = editor();
  panel.renderChanges = () => {};
  panel.state.edited.light[colorKey] = '#112233';
  const generated = panel.NekoPaletteSchemes.apply('ocean');
  assert.ok(Object.keys(generated.vars).length > 1);
  assert.ok(Object.keys(generated.darkVars).length > 1);
  panel.undoStep();
  assert.deepEqual({ ...panel.state.edited.light }, { [colorKey]: '#112233' });
  assert.deepEqual({ ...panel.state.edited.dark }, {});
});

test('open dialogs suppress editor-wide apply, preview and undo shortcuts', () => {
  const { panel, element, listeners } = editor();
  // Other widgets are independent of document-level keyboard dispatch.
  panel.initQuickPresets = () => {};
  panel.wireEvents();
  const actions = [];
  panel.applyTheme = () => actions.push('save');
  panel.togglePreview = () => actions.push('preview');
  panel.undoLastApply = () => actions.push('undo');
  panel.lastApplyUndo = {};
  panel.state.edited.light[colorKey] = '#223344';
  function key(value, modifiers) {
    const event = { key: value, target: element('body'), preventDefault() { this.defaultPrevented = true; }, ...modifiers };
    listeners.keydown.forEach(handler => handler(event));
  }
  for (const id of ['modal', 'compare-modal', 'trash-modal', 'shortcut-sheet']) {
    element(id).hidden = false;
    key('s', { ctrlKey: true });
    key('p', { altKey: true });
    key('z', { altKey: true });
    element(id).hidden = true;
  }
  assert.deepEqual(actions, []);
  key('s', { ctrlKey: true });
  key('p', { altKey: true });
  key('z', { altKey: true });
  assert.deepEqual(actions, ['save', 'preview', 'undo']);
});

test('the dark comparison column uses only the values the dark loader applies', async () => {
  const { panel } = editor();
  panel.callPlugin = async (entry, args) => {
    if (entry === 'theme_json') return {payload: JSON.stringify({vars: {[colorKey]: 'white'}, dark_vars: {}})};
    assert.equal(entry, 'check_contrast');
    assert.equal(args.standalone, true);
    assert.deepEqual({...args.dark}, {});
    return {rows: []};
  };
  const comparison = await panel.compareLoad('light-only');
  assert.equal(comparison.light[colorKey], 'white');
  assert.equal(comparison.dark[colorKey], undefined);
});

test('undo is unavailable if a different theme preceded a library switch', () => {
  const { panel } = editor();
  assert.equal(panel.makeApplyUndo({revision: 'earlier'}, '', '', {
    previous_revision: 'scheduled', revision: 'selected'
  }), null);
});

test('an idle second window preserves changes saved by the original editor', () => {
  const { panel, storage } = editor();
  panel.restoreDraftIfAny();
  const raw = JSON.stringify({v: 1, ts: Date.now(), edited: {light: {[colorKey]: '#000'}}});
  storage.set(panel.DRAFT_KEY, raw);
  panel.saveDraftNow();
  assert.equal(storage.get(panel.DRAFT_KEY), raw);
});
