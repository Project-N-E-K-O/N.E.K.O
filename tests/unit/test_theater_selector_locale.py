"""The story selector must re-render its script-written text when the locale arrives.

``theater_selector.js`` starts loading stories on ``DOMContentLoaded`` while
i18next is still fetching its locale file, so the first render uses the Chinese
fallbacks of ``t()``.  The session hint, status badge and saved-performance rows
are written with ``textContent`` (no ``data-i18n``), so ``updatePageTexts()``
cannot fix them.  This scenario runs the real selector against a minimal fake
DOM inside a node ``vm`` context and checks that a later ``localechange``
re-renders those texts in the new language.
"""

from pathlib import Path
import shutil

import pytest

from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
class FakeElement {
  constructor(key) {
    this.key = key; this.children = []; this.dataset = {}; this.attributes = {}; this.hidden = false;
    this.disabled = false; this._text = ''; this.listeners = {};
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false };
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
  click() {}
}
const elements = new Map();
const element = key => { if (!elements.has(key)) elements.set(key, new FakeElement(key)); return elements.get(key); };
const listeners = {}, docListeners = {}, requests = [];
const window = {
  console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
  location: { origin: 'https://local.test', href: 'https://local.test/theater', search: '' },
  history: { replaceState() {} },
  URL, URLSearchParams,
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
  dispatchEvent(event) { for (const fn of listeners[event.type] || []) fn(event); },
  document: {
    getElementById: id => element('#' + id),
    querySelector: selector => element(selector),
    querySelectorAll: () => [],
    createElement: tag => new FakeElement(tag),
    addEventListener: (name, fn) => { docListeners[name] = fn; },
    activeElement: null,
  },
  fetch: url => new Promise(resolve => requests.push({ url, resolve })),
};
window.window = window;
vm.createContext(window);
for (const path of ['static/js/theater_transport.js', 'static/js/theater_selector.js']) {
  vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
}
async function respond(pattern, data) {
  for (let i = 0; i < 5 && !requests.some(r => pattern.test(r.url)); i += 1) await tick();
  const index = requests.findIndex(r => pattern.test(r.url));
  assert.ok(index >= 0, '应当发出请求 ' + pattern);
  const [request] = requests.splice(index, 1);
  request.resolve({ status: 200, ok: true, json: async () => data });
  await tick(); await tick(); await tick();
}
async function run() {
  // i18next 尚未就绪：window.t 不存在，首轮渲染只能使用中文回退文案。
  docListeners.DOMContentLoaded();
  await respond(/\/stories$/, { ok: true, character_id: 'cat_a', stories: [
    { story_id: 'story_a', title: 'Story A', author: 'Author', language: 'zh', revision: 2, display_intro: {} },
  ] });
  await respond(/\/session\/active\?/, { ok: true, session: { session_id: 'session_a', status: 'active', revision: 5 } });
  await respond(/\/memory\/archives\?/, { ok: true, archives: [
    { session_id: 'old_session', revision: 3, episode_status: 'completed', pinned: false },
  ] });
  await respond(/\/memory\/stories$/, { ok: true, character_id: 'cat_a', stories: [] });
  const hint = element('#theater-session-hint');
  const badge = element('#theater-session-badge');
  const archiveRow = () => element('#theater-memory-list').children[0];
  assert.equal(badge.textContent, '演出中', '首轮渲染应使用中文回退文案（场景前提）');
  assert.match(archiveRow().textContent, /记录/);

  window.t = key => 'EN:' + key;
  window.dispatchEvent({ type: 'localechange' });
  assert.equal(hint.textContent, 'EN:theater.sessionHintActive', '会话提示必须随语言就绪重新渲染');
  assert.equal(badge.textContent, 'EN:theater.running', '状态徽章必须随语言就绪重新渲染');
  const row = archiveRow().textContent;
  assert.match(row, /EN:theater\.performanceRecordMeta/, '记忆列表行必须随语言就绪重新渲染');
  assert.match(row, /EN:theater\.viewPerformance/);
  assert.match(row, /EN:theater\.pinPerformance/);
  assert.doesNotMatch(row, /记录|查看|收藏/);
}
run().then(() => process.stdout.write('ok')).catch(error => { console.error(error); process.exitCode = 1; });
"""


def test_selector_rerenders_script_texts_on_localechange():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = run_node_stdin(node, SCRIPT, cwd=str(ROOT), capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, f"Node regression failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "ok", "async scenario did not complete"


def test_standalone_selector_start_does_not_claim_activity():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = SCRIPT.split("async function run() {")[0].replace(
        "fetch: url => new Promise(resolve => requests.push({ url, resolve })),",
        "fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),",
    )
    script = harness + r"""
async function run() {
  docListeners.DOMContentLoaded();
  await respond(/\/stories$/, { ok: true, character_id: 'cat_a', stories: [
    { story_id: 'story_a', title: 'Story A', revision: 2, display_intro: {} },
  ] });
  await respond(/\/session\/active\?/, { ok: true, session: null });
  await respond(/\/memory\/archives\?/, { ok: true, archives: [] });
  await respond(/\/memory\/stories$/, { ok: true, character_id: 'cat_a', stories: [] });
  element('#theater-start-btn').listeners.click();
  await respond(/\/session\/active\?/, { ok: true, session: null });
  await tick(); await tick();
  const start = requests.find(request => request.url.includes('/session/start'));
  assert.ok(start);
  assert.equal(start.url, '/api/theater-numeric/session/start?claim_activity=false');
  assert.equal(start.options.method, 'POST');
  const payload = JSON.parse(start.options.body);
  assert.equal(payload.character_id, 'cat_a');
  assert.equal(payload.story_id, 'story_a');
}
run().then(() => process.stdout.write('ok')).catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = run_node_stdin(node, script, cwd=str(ROOT), capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, f"Node regression failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "ok", "async scenario did not complete"
