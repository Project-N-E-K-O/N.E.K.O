from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

pytestmark = pytest.mark.plugin_unit

HTML_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "music_pusher"
    / "static"
    / "index.html"
)


def _run_music_pusher_ui_test(initial_items_js: str, run_body_js: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")

    html = HTML_PATH.read_text(encoding="utf-8")
    marker = 'const PLUGIN_ID = "music_pusher";'
    script_start = html.index(marker)
    script_end = html.rindex("</script>")
    page_script = html[script_start:script_end]

    harness = f"""
const assert = require('assert');
const vm = require('vm');

const ctx2d = {{
  clearRect() {{}},
  beginPath() {{}},
  arc() {{}},
  fill() {{}},
  save() {{}},
  translate() {{}},
  rotate() {{}},
  ellipse() {{}},
  restore() {{}},
  fillRect() {{}},
  createLinearGradient() {{ return {{ addColorStop() {{}} }}; }},
}};

function makeNode(tag) {{
  const listeners = {{}};
  const el = {{
    tagName: tag.toUpperCase(),
    className: '',
    dataset: {{}},
    children: [],
    style: {{}},
    attrs: {{}},
    classList: {{ add() {{}}, remove() {{}}, toggle() {{}}, contains() {{ return false; }} }},
    _textContent: '',
    _innerHTML: '',
    loadCalls: 0,
    pauseCalls: 0,
    paused: true,
    parentNode: null,
    set textContent(v) {{ this._textContent = String(v ?? ''); }},
    get textContent() {{ return this._textContent; }},
    set innerHTML(v) {{
      this._innerHTML = String(v ?? '');
      if (this._innerHTML === '') {{
        for (const c of this.children) c.parentNode = null;
        this.children = [];
      }} else if (this._innerHTML.includes('class="hint"')) {{
        for (const c of this.children) c.parentNode = null;
        const hint = makeNode('div');
        hint.className = 'hint';
        hint.parentNode = this;
        this.children = [hint];
      }}
    }},
    get innerHTML() {{ return this._innerHTML; }},
    appendChild(child) {{
      return this.insertBefore(child, null);
    }},
    insertBefore(child, refNode) {{
      const prevIdx = this.children.indexOf(child);
      if (prevIdx !== -1) this.children.splice(prevIdx, 1);
      if (refNode === null || refNode === undefined) {{
        this.children.push(child);
      }} else {{
        const idx = this.children.indexOf(refNode);
        if (idx === -1) this.children.push(child);
        else this.children.splice(idx, 0, child);
      }}
      child.parentNode = this;
      return child;
    }},
    remove() {{
      if (this.parentNode) {{
        const idx = this.parentNode.children.indexOf(this);
        if (idx !== -1) this.parentNode.children.splice(idx, 1);
        this.parentNode = null;
      }}
    }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    removeAttribute(k) {{ delete this.attrs[k]; }},
    addEventListener(type, fn) {{ (listeners[type] ||= []).push(fn); }},
    querySelector(sel) {{
      if (sel === 'audio') return this.children.find((c) => c.tagName === 'AUDIO') || null;
      return null;
    }},
    load() {{ this.loadCalls += 1; }},
    pause() {{ this.pauseCalls += 1; this.paused = true; }},
    play() {{ this.paused = false; return Promise.resolve(); }},
    getContext() {{ return ctx2d; }},
  }};
  return el;
}}

const byId = new Map();
for (const id of [
  'header-chip', 'audio-file', 'lyric-file', 'audio-file-name', 'lyric-file-name',
  'upload-title', 'upload-artist', 'online-url', 'online-duration', 'online-title',
  'online-artist', 'music-list', 'queue-list', 'task-list', 'queue-summary',
  'runtime-text', 'stage-text', 'runtime-progress', 'visualizer', 'stage-box-wrap',
  'stage-box', 'stage-canvas', 'playback-pill', 'sync-player', 'attach-prompt-toggle',
  'toast-box', 'bg-canvas', 'btn-upload-only', 'btn-upload-push', 'btn-add-url',
  'btn-push-url', 'btn-create-task', 'btn-stop-all', 'btn-refresh', 'btn-clear-queue',
  'dur-manual-input',
]) {{
  const nodeEl = makeNode(id === 'sync-player' ? 'audio' : (id.includes('canvas') || id === 'visualizer' ? 'canvas' : 'div'));
  nodeEl.id = id;
  byId.set(id, nodeEl);
}}

const document = {{
  body: makeNode('body'),
  head: makeNode('head'),
  getElementById(id) {{
    if (!byId.has(id)) {{
      const fallback = makeNode('div');
      fallback.id = id;
      byId.set(id, fallback);
    }}
    return byId.get(id);
  }},
  querySelector() {{ return null; }},
  querySelectorAll() {{ return []; }},
  createElement(tag) {{ return makeNode(tag); }},
}};

let currentItems = {initial_items_js};

let intervalFn = null;
let runSeq = 0;
const runEntries = new Map();

const sandbox = {{
  console,
  document,
  AbortController,
  URLSearchParams,
  FileReader: function FileReader() {{}},
  window: {{
    location: {{ origin: 'http://127.0.0.1:48916', href: 'http://127.0.0.1:48916/plugin/music_pusher/', search: '' }},
    addEventListener() {{}},
    innerWidth: 1024,
    innerHeight: 768,
    THREE: null,
  }},
  fetch: async (url, init) => {{
    const u = String(url);
    if (u.endsWith('/security/csrf-token')) {{
      return {{ ok: true, status: 200, json: async () => ({{ csrf_token: 'csrf' }}) }};
    }}
    if (u === '/runs') {{
      const req = JSON.parse(init.body);
      const id = 'run-' + (++runSeq);
      runEntries.set(id, req.entry_id);
      return {{ ok: true, status: 200, json: async () => ({{ run_id: id }}) }};
    }}
    const exportMatch = u.match(/\\/runs\\/([^/]+)\\/export$/);
    if (exportMatch) {{
      const entryId = runEntries.get(exportMatch[1]);
      let data = {{}};
      if (entryId === 'get_push_settings') data = {{ attach_prompt_on_push: true }};
      else if (entryId === 'list_music_items') data = {{ items: currentItems }};
      else if (entryId === 'list_schedule_tasks') data = {{ tasks: [], runtime: {{ active: null, next_task: null, playback: null }} }};
      return {{
        ok: true,
        status: 200,
        json: async () => ({{ items: [{{ type: 'json', json: {{ success: true, data }} }}] }}),
      }};
    }}
    if (u.startsWith('/runs/')) {{
      return {{ ok: true, status: 200, json: async () => ({{ status: 'succeeded' }}) }};
    }}
    return {{ ok: true, status: 200, json: async () => ({{}}) }};
  }},
  setInterval(fn) {{ intervalFn = fn; return 1; }},
  clearInterval() {{}},
  setTimeout(fn) {{ return globalThis.setTimeout(fn, 0); }},
  clearTimeout(id) {{ return globalThis.clearTimeout(id); }},
  requestAnimationFrame() {{ return 1; }},
}};
sandbox.window.document = document;
vm.createContext(sandbox);
vm.runInContext({page_script!r} + '\\nthis.__test_state = state; this.__test_attach = attachAudioToVisualizer;', sandbox);

// Waits for what the page does on its own (its first load goes through several
// mocked requests, each a promise and a zero-delay timer), however busy the
// machine is. A fixed short sleep turned red on a loaded CI runner.
async function waitFor(predicate, what, timeoutMs = 10000) {{
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {{
    if (Date.now() > deadline) throw new Error('timed out waiting for ' + what);
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
}}

async function run() {{
{run_body_js}
}}

run().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = run_node_script(node, harness, capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_music_pusher_refresh_reconciles_list_and_preserves_playing_audio() -> None:
    """F0293: 5s refreshAll reuses unchanged audio nodes and releases deleted playing ones."""
    _run_music_pusher_ui_test(
        initial_items_js="""[
  { item_id: 'track-1', title: 'Song One', artist: 'A', duration_sec: 120, url: 'https://example.com/one.mp3' },
  { item_id: 'track-2', title: 'Song Two', artist: 'B', duration_sec: 150, url: 'https://example.com/two.mp3' },
]""",
        run_body_js="""
  await waitFor(() => byId.get('music-list').children.length === 2, 'the first render of the list');
  const musicList = byId.get('music-list');
  assert.strictEqual(musicList.children.length, 2);

  const firstCard = musicList.children[0];
  const firstAudio = firstCard.querySelector('audio');
  const secondAudio = musicList.children[1].querySelector('audio');
  assert.strictEqual(firstAudio.loadCalls, 1);
  assert.strictEqual(secondAudio.loadCalls, 1);

  // Simulate user playing track-1 preview
  await firstAudio.play();
  sandbox.__test_attach(firstAudio);
  assert.strictEqual(firstAudio.paused, false);

  // 1) Refresh with same tracks (even with updated title) must reuse DOM & audio without reload/pause
  currentItems = [
    { item_id: 'track-1', title: 'Song One (Edited)', artist: 'A', duration_sec: 120, url: 'https://example.com/one.mp3' },
    { item_id: 'track-2', title: 'Song Two', artist: 'B', duration_sec: 150, url: 'https://example.com/two-v2.mp3' },
  ];
  assert.ok(intervalFn, '5s refresh interval should be registered');
  await intervalFn();
  assert.strictEqual(musicList.children[0], firstCard);
  assert.strictEqual(musicList.children[0].querySelector('audio'), firstAudio);
  assert.strictEqual(firstAudio.loadCalls, 1, 'unchanged track audio must not reload');
  assert.strictEqual(firstAudio.pauseCalls, 0, 'playing track audio must not pause');
  assert.strictEqual(firstAudio.paused, false);
  assert.strictEqual(firstCard.children[0].textContent, 'Song One (Edited)');
  assert.strictEqual(secondAudio.loadCalls, 2, 'changed URL track reloads its source');

  // 2) Duplicate itemKey node carrying active audio releases preview audio before removal
  const dupCard = makeNode('div');
  dupCard.dataset.itemKey = musicList.children[0].dataset.itemKey;
  const dupAudio = makeNode('audio');
  await dupAudio.play();
  dupCard.appendChild(dupAudio);
  musicList.appendChild(dupCard);
  await intervalFn();
  assert.strictEqual(dupAudio.pauseCalls, 1);
  assert.strictEqual(dupAudio.paused, true);
  assert.strictEqual(dupCard.parentNode, null);

  // 3) Deleting the playing track pauses/unloads it and clears activeElement
  currentItems = [
    { item_id: 'track-2', title: 'Song Two', artist: 'B', duration_sec: 150, url: 'https://example.com/two-v2.mp3' },
  ];
  await intervalFn();
  assert.strictEqual(musicList.children.length, 1);
  assert.strictEqual(firstAudio.pauseCalls, 1);
  assert.strictEqual(firstAudio.paused, true);
  assert.strictEqual(firstCard.parentNode, null);
  assert.strictEqual(sandbox.__test_state.audioViz.activeElement, null);
""",
    )


def test_music_pusher_preserves_playing_item_without_id_when_preceding_removed() -> None:
    """Items without item_id use URL + occurrence keys so deleting earlier items keeps playing cards."""
    _run_music_pusher_ui_test(
        initial_items_js="""[
  { title: 'Alpha', artist: 'A', duration_sec: 100, url: 'https://example.com/a.mp3' },
  { title: 'Beta', artist: 'B', duration_sec: 110, url: 'https://example.com/b.mp3' },
  { title: 'Alpha Twin', artist: 'A2', duration_sec: 120, url: 'https://example.com/a.mp3' },
]""",
        run_body_js="""
  await waitFor(() => byId.get('music-list').children.length === 3, 'the first render of the list');
  const musicList = byId.get('music-list');
  assert.strictEqual(musicList.children.length, 3);
  assert.strictEqual(musicList.children[0].dataset.itemKey, 'url:https://example.com/a.mp3#0');
  assert.strictEqual(musicList.children[1].dataset.itemKey, 'url:https://example.com/b.mp3#0');
  assert.strictEqual(musicList.children[2].dataset.itemKey, 'url:https://example.com/a.mp3#1');

  const alphaTwinCard = musicList.children[2];
  const alphaTwinAudio = alphaTwinCard.querySelector('audio');
  const betaCard = musicList.children[1];
  const betaAudio = betaCard.querySelector('audio');
  await betaAudio.play();
  sandbox.__test_attach(betaAudio);
  assert.strictEqual(betaAudio.loadCalls, 1);
  assert.strictEqual(betaAudio.paused, false);

  // Remove the first Alpha item and its second occurrence; Beta shifts from index 1 to 0.
  currentItems = [
    { title: 'Beta', artist: 'B', duration_sec: 110, url: 'https://example.com/b.mp3' },
  ];
  await intervalFn();
  assert.strictEqual(musicList.children.length, 1);
  assert.strictEqual(musicList.children[0], betaCard);
  assert.strictEqual(musicList.children[0].querySelector('audio'), betaAudio);
  assert.strictEqual(betaAudio.loadCalls, 1, 'playing card without item_id must not reload');
  assert.strictEqual(betaAudio.pauseCalls, 0, 'playing card without item_id must not pause');
  assert.strictEqual(betaAudio.paused, false);
  assert.strictEqual(alphaTwinCard.parentNode, null);
  assert.ok(alphaTwinAudio.loadCalls >= 2, 'deleted occurrence card must release audio');
""",
    )
