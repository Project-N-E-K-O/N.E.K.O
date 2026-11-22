const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../..');
const transportSource = fs.readFileSync(path.join(repoRoot, 'static/jukebox/jukebox/transport.js'), 'utf8');
const managerSource = fs.readFileSync(path.join(repoRoot, 'static/jukebox/jukebox/manager.js'), 'utf8');
const aplayerSource = fs.readFileSync(path.join(repoRoot, 'static/libs/APlayer.min.js'), 'utf8');

function loadJukeboxContext({ withDocument = false } = {}) {
  const titleEl = { innerHTML: '', textContent: '' };
  const authorEl = { innerHTML: '', textContent: '' };
  const dummyEl = () => ({
    innerHTML: '',
    textContent: '',
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => dummyEl(),
    querySelectorAll: () => [],
    appendChild() {},
    setAttribute() {},
    getAttribute: () => null,
  });

  const sandbox = {
    Jukebox: {
      State: { boundPlayer: null },
      bindTextTooltips() {},
      scheduleMarqueeTextUpdate() {},
    },
    window: {
      t: (_key, fallback) => fallback || '',
      location: { origin: 'http://localhost:48911', href: 'http://localhost:48911/' },
      navigator: { userAgent: 'node' },
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {},
    },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    URL,
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  };
  sandbox.window.Jukebox = sandbox.Jukebox;
  sandbox.window.window = sandbox.window;
  sandbox.globalThis = sandbox;

  if (withDocument) {
    sandbox.document = {
      addEventListener() {},
      querySelector: () => null,
      querySelectorAll: () => [],
      getElementById: () => null,
      createElement: () => dummyEl(),
      head: dummyEl(),
      body: dummyEl(),
    };
    sandbox.window.document = sandbox.document;
  }

  vm.runInNewContext(transportSource, sandbox);
  vm.runInNewContext(managerSource, sandbox);
  return { sandbox, titleEl, authorEl };
}

test('F0056: Jukebox.escapeHtml works without document and playAudio escapes song metadata', async () => {
  // Without document (matches jukebox_file_url.test.js environment)
  const noDoc = loadJukeboxContext({ withDocument: false });
  assert.equal(
    noDoc.sandbox.Jukebox.escapeHtml('<img src=x onerror="alert(\'1\')">&'),
    '&lt;img src=x onerror=&quot;alert(&#39;1&#39;)&quot;&gt;&amp;',
  );
  assert.equal(noDoc.sandbox.Jukebox.escapeHtml(null), '');

  const addedItems = [];
  const fakePlayer = {
    options: { loop: 'all' },
    list: {
      clear() {},
      add(items) {
        addedItems.push(...items);
      },
    },
    on() {},
    play() {},
  };
  noDoc.sandbox.Jukebox.getPlayer = () => fakePlayer;
  await noDoc.sandbox.Jukebox.playAudio({
    name: '<img src=x onerror=alert(1)>',
    artist: '"><svg onload=alert(1)>',
    audio: 'songs/test.mp3',
  });
  assert.equal(addedItems.length, 1);
  assert.equal(addedItems[0].name, '&lt;img src=x onerror=alert(1)&gt;');
  assert.equal(addedItems[0].artist, '&quot;&gt;&lt;svg onload=alert(1)&gt;');

  // Verify APlayer sink receives already-escaped strings so innerHTML does not contain raw tags
  const withDoc = loadJukeboxContext({ withDocument: true });
  const aplayerSandbox = {
    window: withDoc.sandbox.window,
    document: withDoc.sandbox.document,
    navigator: { userAgent: 'node' },
    console,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(aplayerSource, aplayerSandbox);
  assert.equal(typeof aplayerSandbox.window.APlayer, 'function');
  const titleTarget = { innerHTML: '' };
  const authorTarget = { innerHTML: '' };
  // Mirror APlayer switch(0) assignment directly on escaped items from playAudio
  titleTarget.innerHTML = addedItems[0].name;
  authorTarget.innerHTML = addedItems[0].artist ? ' - ' + addedItems[0].artist : '';
  assert.ok(!titleTarget.innerHTML.includes('<img'));
  assert.ok(!authorTarget.innerHTML.includes('<svg'));
});

test('F0055: SongActionManager normalizes untrusted action.format across all render tabs', () => {
  const { sandbox } = loadJukeboxContext({ withDocument: true });
  const sam = sandbox.Jukebox.SongActionManager;

  assert.equal(sam.normalizeActionFormat(undefined), 'vmd');
  assert.equal(sam.normalizeActionFormat(''), 'vmd');
  assert.equal(sam.normalizeActionFormat('VRMA'), 'vrma');
  assert.equal(sam.normalizeActionFormat('bvh'), 'bvh');
  assert.equal(sam.normalizeActionFormat('fbx'), 'fbx');
  assert.equal(sam.normalizeActionFormat('x"><img src=x onerror=alert(1)><b class="'), 'other');
  assert.equal(sam.normalizeActionFormat('constructor'), 'other');
  assert.equal(sam.normalizeActionFormat('__proto__'), 'other');
  assert.equal(sam.normalizeActionFormat('toString'), 'other');
  assert.equal(sam.normalizeActionFormat(123), 'other');
  assert.equal(sam.normalizeActionFormat({}), 'other');

  assert.deepEqual(sam.getFormatColorConfig('constructor'), sam.Config.formatColors.default);
  assert.deepEqual(sam.getFormatColorConfig('toString'), sam.Config.formatColors.default);
  assert.deepEqual(sam.getFormatColorConfig(123), sam.Config.formatColors.default);
  assert.deepEqual(sam.getFormatColorConfig('vrma'), sam.Config.formatColors.vrma);

  const xssFormat = 'x"><img src=x onerror=alert(1)><b class="';
  sam.data = {
    songs: {
      s1: { name: 'Song 1', audio: 'a.mp3', defaultAction: 'a1', actions: ['a1'] },
    },
    actions: {
      a1: { name: 'Action 1', format: xssFormat, visible: true },
      a2: { name: 'Action 2', format: 'constructor', visible: true },
      a3: { name: 'Action 3', format: 123, visible: true },
      a4: { name: 'Action 4', format: 'VRMA', visible: true },
    },
    bindings: { s1: { a1: { offset: 0 }, a2: { offset: 0 }, a3: { offset: 0 }, a4: { offset: 0 } } },
  };
  sam.initSelection();
  sam.initBindingSelection();
  sam.syncCheckboxState = () => {};
  sam.bindDragEvents = () => {};
  sam.bindBindingDragEvents = () => {};
  sam.bindFileDropEvents = () => {};
  sam.bindButtonTooltips = () => {};

  const panel = { innerHTML: '', querySelector: () => null, querySelectorAll: () => [] };
  sam.renderSongs(panel);
  assert.ok(panel.innerHTML.includes('sam-action-tag-other'));
  assert.ok(panel.innerHTML.includes('sam-action-tag-vrma'));
  assert.ok(!panel.innerHTML.includes('<img src=x'));

  sam.renderActions(panel);
  assert.ok(!panel.innerHTML.includes('<img src=x'));

  sam.renderBindings(panel);
  assert.ok(panel.innerHTML.includes('sam-action-tag-small-other'));
  assert.ok(panel.innerHTML.includes('sam-action-tag-small-vrma'));
  assert.ok(!panel.innerHTML.includes('<img src=x'));
});
