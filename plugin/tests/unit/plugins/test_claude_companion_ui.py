from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

pytestmark = pytest.mark.plugin_unit

HTML_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "claude_companion"
    / "static"
    / "index.html"
)


def test_claude_companion_status_polling_preserves_unsaved_inputs() -> None:
    """F0276: 15s status polling must not overwrite focused or dirty input fields."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")

    html = HTML_PATH.read_text(encoding="utf-8")
    script_start = html.rindex("<script>") + len("<script>")
    script_end = html.rindex("</script>")
    page_script = html[script_start:script_end]

    harness = f"""
const assert = require('assert');
const vm = require('vm');

function makeElement(id, tag = 'div') {{
  const listeners = {{}};
  return {{
    id,
    tagName: tag.toUpperCase(),
    value: '',
    textContent: '',
    innerHTML: '',
    className: '',
    classList: {{ add() {{}}, remove() {{}} }},
    addEventListener(type, fn) {{
      (listeners[type] ||= []).push(fn);
    }},
    dispatch(type) {{
      for (const fn of (listeners[type] || [])) fn({{ target: this }});
    }},
  }};
}}

const elements = new Map([
  ['status-dot', makeElement('status-dot')],
  ['status-text', makeElement('status-text')],
  ['event-count', makeElement('event-count')],
  ['user-name', makeElement('user-name')],
  ['port-num', makeElement('port-num')],
  ['cooldown-val', makeElement('cooldown-val')],
  ['input-name', makeElement('input-name', 'input')],
  ['input-cooldown', makeElement('input-cooldown', 'input')],
  ['timeline-container', makeElement('timeline-container')],
  ['toast', makeElement('toast')],
]);

let intervalCallback = null;
const docListeners = {{}};
const document = {{
  activeElement: null,
  getElementById(id) {{
    return elements.get(id) || makeElement(id);
  }},
  querySelectorAll() {{ return []; }},
  createElement() {{
    return {{
      _text: '',
      set textContent(v) {{ this._text = String(v || ''); }},
      get innerHTML() {{ return this._text; }},
    }};
  }},
  addEventListener(type, fn) {{
    (docListeners[type] ||= []).push(fn);
  }},
}};

let statusPayload = {{ user_name: 'Alice', cooldown: 60, event_count: 1, port: 48916 }};
let eventsPayload = {{ events: [] }};
let runSeq = 0;
const runEntries = new Map();

const sandbox = {{
  console,
  document,
  AbortController,
  URLSearchParams,
  window: {{ location: {{ origin: 'http://127.0.0.1:48916', search: '' }} }},
  fetch: async (url, init) => {{
    const u = String(url);
    if (u.endsWith('/security/csrf-token')) {{
      return {{ ok: true, status: 200, json: async () => ({{ csrf_token: 'csrf' }}) }};
    }}
    if (u.includes('/ui-api/locale')) {{
      return {{ ok: true, status: 200, json: async () => ({{ locale: 'en' }}) }};
    }}
    if (u.includes('/ui-api/i18n/')) {{
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }}
    if (u.endsWith('/runs')) {{
      const req = JSON.parse(init.body);
      const id = 'run-' + (++runSeq);
      runEntries.set(id, req.entry_id);
      return {{ ok: true, status: 200, json: async () => ({{ run_id: id }}) }};
    }}
    const exportMatch = u.match(/\\/runs\\/([^/]+)\\/export$/);
    if (exportMatch) {{
      const entryId = runEntries.get(exportMatch[1]);
      let data = {{}};
      if (entryId === 'get_status') data = statusPayload;
      else if (entryId === 'get_events') data = eventsPayload;
      else if (entryId === 'set_user_name') data = {{ user_name: statusPayload.user_name }};
      else if (entryId === 'set_cooldown') data = {{ cooldown_seconds: statusPayload.cooldown }};
      return {{
        ok: true,
        status: 200,
        json: async () => ({{ items: [{{ label: 'trigger_response', json: {{ success: true, data }} }}] }}),
      }};
    }}
    if (u.includes('/runs/')) {{
      return {{ ok: true, status: 200, json: async () => ({{ status: 'succeeded' }}) }};
    }}
    return {{ ok: true, status: 200, json: async () => ({{}}) }};
  }},
  setInterval(fn) {{
    intervalCallback = fn;
    return 1;
  }},
  clearInterval() {{}},
  setTimeout(fn) {{ return globalThis.setTimeout(fn, 0); }},
  clearTimeout(id) {{ return globalThis.clearTimeout(id); }},
}};
sandbox.window.document = document;
vm.createContext(sandbox);
vm.runInContext({page_script!r}, sandbox);

async function run() {{
  for (const fn of (docListeners['DOMContentLoaded'] || [])) {{
    await fn();
  }}
  await new Promise((r) => globalThis.setTimeout(r, 30));

  const nameInput = elements.get('input-name');
  const cooldownInput = elements.get('input-cooldown');
  assert.strictEqual(nameInput.value, 'Alice');
  assert.strictEqual(String(cooldownInput.value), '60');

  // 1) User edits both inputs (including blurred after edit) -> polling must NOT clobber.
  document.activeElement = nameInput;
  nameInput.value = 'UnsavedBob';
  nameInput.dispatch('input');
  document.activeElement = null;
  cooldownInput.value = '120';
  cooldownInput.dispatch('input');

  assert.ok(intervalCallback, 'refreshTimer should be registered');
  await intervalCallback();
  await new Promise((r) => globalThis.setTimeout(r, 30));

  assert.strictEqual(nameInput.value, 'UnsavedBob');
  assert.strictEqual(String(cooldownInput.value), '120');

  // 2) Saving clears dirty state so polling resumes syncing; focused clean input stays protected.
  statusPayload = {{ user_name: 'SavedBob', cooldown: 60, event_count: 2, port: 48916 }};
  await sandbox.saveUserName();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(nameInput.value, 'SavedBob');

  document.activeElement = nameInput;
  nameInput.value = 'TypingNow';
  statusPayload = {{ user_name: 'ServerOverwrite', cooldown: 60, event_count: 3, port: 48916 }};
  await intervalCallback();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(nameInput.value, 'TypingNow');
}}

run().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = run_node_script(node, harness, capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
