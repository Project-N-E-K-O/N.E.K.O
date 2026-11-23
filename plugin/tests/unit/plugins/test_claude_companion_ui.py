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


def test_claude_companion_save_preserves_other_dirty_field_and_inflight_edits() -> None:
    """F0276: saving one field preserves the other dirty field and in-flight post-save typing."""
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
let holdGetStatus = false;
let releaseGetStatus = null;

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
      if (entryId === 'get_status' && holdGetStatus) {{
        await new Promise((resolve) => {{ releaseGetStatus = resolve; }});
      }}
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
  setInterval() {{ return 1; }},
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

  // Scenario 1a: Save #input-name while #input-cooldown is dirty -> preserves #input-cooldown.
  nameInput.value = 'SavedName';
  nameInput.dispatch('input');
  cooldownInput.value = '180';
  cooldownInput.dispatch('input');
  statusPayload = {{ user_name: 'SavedName', cooldown: 60, event_count: 2, port: 48916 }};
  await sandbox.saveUserName();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(nameInput.value, 'SavedName');
  assert.strictEqual(String(cooldownInput.value), '180');

  // Scenario 1b: Save #input-cooldown while #input-name is dirty -> preserves #input-name.
  nameInput.value = 'DirtyName';
  nameInput.dispatch('input');
  statusPayload = {{ user_name: 'SavedName', cooldown: 180, event_count: 3, port: 48916 }};
  await sandbox.saveCooldown();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(String(cooldownInput.value), '180');
  assert.strictEqual(nameInput.value, 'DirtyName');

  // Scenario 2a: Typing into #input-name while post-save get_status is in flight preserves active edit.
  nameInput.value = 'BeforeInflightSave';
  nameInput.dispatch('input');
  statusPayload = {{ user_name: 'BeforeInflightSave', cooldown: 180, event_count: 4, port: 48916 }};
  holdGetStatus = true;
  releaseGetStatus = null;
  await sandbox.saveUserName();
  for (let i = 0; i < 20 && !releaseGetStatus; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseGetStatus, 'post-save get_status should be in flight');
  document.activeElement = nameInput;
  nameInput.value = 'TypingWhileGetStatusInFlight';
  nameInput.dispatch('input');
  holdGetStatus = false;
  releaseGetStatus();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(nameInput.value, 'TypingWhileGetStatusInFlight');

  // Scenario 2b: Typing into #input-cooldown while post-save get_status is in flight preserves active edit.
  document.activeElement = null;
  cooldownInput.value = '200';
  cooldownInput.dispatch('input');
  statusPayload = {{ user_name: 'BeforeInflightSave', cooldown: 200, event_count: 5, port: 48916 }};
  holdGetStatus = true;
  releaseGetStatus = null;
  await sandbox.saveCooldown();
  for (let i = 0; i < 20 && !releaseGetStatus; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseGetStatus, 'post-save cooldown get_status should be in flight');
  document.activeElement = cooldownInput;
  cooldownInput.value = '240';
  cooldownInput.dispatch('input');
  holdGetStatus = false;
  releaseGetStatus();
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(String(cooldownInput.value), '240');
}}

run().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = run_node_script(node, harness, capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_claude_companion_edit_seq_prevents_stale_clear_and_revert_clears_dirty() -> None:
    """Re-editing back to submitted value while save is in flight keeps dirty; reverting to synced value clears dirty."""
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
let runSeq = 0;
const runEntries = new Map();
let releaseSlowNameSave = null;
let releaseSlowCooldownSave = null;
let holdNameSave = false;
let holdCooldownSave = false;

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
      if (entryId === 'set_user_name' && holdNameSave) {{
        await new Promise((resolve) => {{ releaseSlowNameSave = resolve; }});
      }}
      if (entryId === 'set_cooldown' && holdCooldownSave) {{
        await new Promise((resolve) => {{ releaseSlowCooldownSave = resolve; }});
      }}
      let data = {{}};
      if (entryId === 'get_status') data = statusPayload;
      else if (entryId === 'get_events') data = {{ events: [] }};
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
  setInterval() {{ return 1; }},
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

  // 1) Overlapping same-value race on nameInput and cooldownInput:
  // submit 'Bob', edit to 'Charlie' and back to 'Bob' before the old response settles.
  nameInput.value = 'Bob';
  nameInput.dispatch('input');
  holdNameSave = true;
  statusPayload = {{ user_name: 'Bob', cooldown: 60, event_count: 2, port: 48916 }};
  const pendingNameSave = sandbox.saveUserName();
  for (let i = 0; i < 20 && !releaseSlowNameSave; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseSlowNameSave, 'set_user_name should be in flight');
  nameInput.value = 'Charlie';
  nameInput.dispatch('input');
  nameInput.value = 'Bob';
  nameInput.dispatch('input');
  // Simulate server later having a different value when status refreshes.
  statusPayload = {{ user_name: 'OldServerValue', cooldown: 60, event_count: 3, port: 48916 }};
  holdNameSave = false;
  releaseSlowNameSave();
  await pendingNameSave;
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(nameInput.value, 'Bob', 'editSeq guard must keep nameDirty true so loadStatus does not overwrite');

  cooldownInput.value = '90';
  cooldownInput.dispatch('input');
  holdCooldownSave = true;
  statusPayload = {{ user_name: 'OldServerValue', cooldown: 90, event_count: 4, port: 48916 }};
  const pendingCooldownSave = sandbox.saveCooldown();
  for (let i = 0; i < 20 && !releaseSlowCooldownSave; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseSlowCooldownSave, 'set_cooldown should be in flight');
  cooldownInput.value = '150';
  cooldownInput.dispatch('input');
  cooldownInput.value = '90';
  cooldownInput.dispatch('input');
  statusPayload = {{ user_name: 'OldServerValue', cooldown: 75, event_count: 5, port: 48916 }};
  holdCooldownSave = false;
  releaseSlowCooldownSave();
  await pendingCooldownSave;
  await new Promise((r) => globalThis.setTimeout(r, 30));
  assert.strictEqual(String(cooldownInput.value), '90', 'editSeq guard must keep cooldownDirty true');

  // 2) Reverting an edited field back to last synced value clears dirty so polling resumes.
  statusPayload = {{ user_name: 'SyncedName', cooldown: 120, event_count: 6, port: 48916 }};
  nameInput.value = 'SyncedName';
  nameInput.dispatch('input');
  await sandbox.saveUserName();
  cooldownInput.value = '120';
  cooldownInput.dispatch('input');
  await sandbox.saveCooldown();
  await new Promise((r) => globalThis.setTimeout(r, 30));

  nameInput.value = 'TempEdit';
  nameInput.dispatch('input');
  nameInput.value = 'SyncedName';
  nameInput.dispatch('input');
  cooldownInput.value = '300';
  cooldownInput.dispatch('input');
  cooldownInput.value = '120';
  cooldownInput.dispatch('input');

  statusPayload = {{ user_name: 'RemoteUpdated', cooldown: 45, event_count: 7, port: 48916 }};
  await sandbox.loadStatus();
  assert.strictEqual(nameInput.value, 'RemoteUpdated');
  assert.strictEqual(String(cooldownInput.value), '45');
}}

run().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = run_node_script(node, harness, capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_claude_companion_inflight_revert_to_synced_and_status_ordering() -> None:
    """Reverting to old lastSynced while a save is pending keeps dirty; stale status polls are ignored."""
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
let runSeq = 0;
const runEntries = new Map();
let releaseSlowNameSave = null;
let releaseSlowCooldownSave = null;
let releaseSlowGetStatus = null;
let holdNameSave = false;
let holdCooldownSave = false;
let holdGetStatus = false;

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
      if (entryId === 'set_user_name' && holdNameSave) {{
        await new Promise((resolve) => {{ releaseSlowNameSave = resolve; }});
      }}
      if (entryId === 'set_cooldown' && holdCooldownSave) {{
        await new Promise((resolve) => {{ releaseSlowCooldownSave = resolve; }});
      }}
      if (entryId === 'get_status' && holdGetStatus) {{
        await new Promise((resolve) => {{ releaseSlowGetStatus = resolve; }});
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            items: [{{
              label: 'trigger_response',
              json: {{ success: true, data: {{ user_name: 'StalePoll', cooldown: 33, event_count: 1, port: 48916 }} }},
            }}],
          }}),
        }};
      }}
      let data = {{}};
      if (entryId === 'get_status') data = statusPayload;
      else if (entryId === 'get_events') data = {{ events: [] }};
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
  setInterval() {{ return 1; }},
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

  // 1) Name revert-during-flight: Alice -> Bob (save pending) -> Alice before save resolves.
  nameInput.value = 'Bob';
  nameInput.dispatch('input');
  holdNameSave = true;
  statusPayload = {{ user_name: 'Bob', cooldown: 60, event_count: 2, port: 48916 }};
  const pendingNameSave = sandbox.saveUserName();
  for (let i = 0; i < 20 && !releaseSlowNameSave; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseSlowNameSave, 'set_user_name should be in flight');
  nameInput.value = 'Alice';
  nameInput.dispatch('input');
  holdNameSave = false;
  releaseSlowNameSave();
  await pendingNameSave;
  await new Promise((r) => globalThis.setTimeout(r, 30));
  document.activeElement = null;
  await sandbox.loadStatus();
  assert.strictEqual(nameInput.value, 'Alice', 'reverted input while save was in flight must remain dirty and not be overwritten by Bob');

  // 2) Cooldown revert-during-flight: 60 -> 90 (save pending) -> 60 before save resolves.
  cooldownInput.value = '90';
  cooldownInput.dispatch('input');
  holdCooldownSave = true;
  statusPayload = {{ user_name: 'Bob', cooldown: 90, event_count: 3, port: 48916 }};
  const pendingCooldownSave = sandbox.saveCooldown();
  for (let i = 0; i < 20 && !releaseSlowCooldownSave; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseSlowCooldownSave, 'set_cooldown should be in flight');
  cooldownInput.value = '60';
  cooldownInput.dispatch('input');
  holdCooldownSave = false;
  releaseSlowCooldownSave();
  await pendingCooldownSave;
  await new Promise((r) => globalThis.setTimeout(r, 30));
  document.activeElement = null;
  await sandbox.loadStatus();
  assert.strictEqual(String(cooldownInput.value), '60', 'reverted cooldown while save was in flight must remain dirty and not be overwritten by 90');

  // 3) Out-of-order loadStatus(): stale response arriving after newer loadStatus() must be ignored.
  statusPayload = {{ user_name: 'FreshBob', cooldown: 120, event_count: 4, port: 48916 }};
  nameInput.value = 'FreshBob';
  nameInput.dispatch('input');
  await sandbox.saveUserName();
  cooldownInput.value = '120';
  cooldownInput.dispatch('input');
  await sandbox.saveCooldown();
  await new Promise((r) => globalThis.setTimeout(r, 30));

  holdGetStatus = true;
  releaseSlowGetStatus = null;
  const staleLoad = sandbox.loadStatus();
  for (let i = 0; i < 20 && !releaseSlowGetStatus; i += 1) {{
    await new Promise((r) => globalThis.setTimeout(r, 5));
  }}
  assert.ok(releaseSlowGetStatus, 'stale get_status should be in flight');
  holdGetStatus = false;
  statusPayload = {{ user_name: 'LatestServerName', cooldown: 180, event_count: 5, port: 48916 }};
  await sandbox.loadStatus();
  assert.strictEqual(elements.get('user-name').textContent, 'LatestServerName');
  assert.strictEqual(String(elements.get('cooldown-val').textContent), '180');
  assert.strictEqual(nameInput.value, 'LatestServerName');
  assert.strictEqual(String(cooldownInput.value), '180');

  releaseSlowGetStatus();
  await staleLoad;
  assert.strictEqual(elements.get('user-name').textContent, 'LatestServerName');
  assert.strictEqual(String(elements.get('cooldown-val').textContent), '180');
  assert.strictEqual(nameInput.value, 'LatestServerName');
  assert.strictEqual(String(cooldownInput.value), '180');
}}

run().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = run_node_script(node, harness, capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
