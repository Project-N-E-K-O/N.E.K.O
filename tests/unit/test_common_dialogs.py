import json
import shutil
import subprocess
from pathlib import Path

import pytest


COMMON_DIALOGS_PATH = Path(__file__).resolve().parents[2] / "static" / "common_dialogs.js"


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


def _run_common_dialogs_node_scenario(script_body: str) -> dict:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    node_harness = f"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class FakeClassList {{
  constructor(element) {{
    this.element = element;
    this._classes = new Set();
  }}

  add(...names) {{
    for (const name of names) {{
      if (!name) continue;
      this._classes.add(String(name));
    }}
    this.element.className = Array.from(this._classes).join(' ');
  }}

  contains(name) {{
    return this._classes.has(String(name));
  }}
}}

function walk(node, visit) {{
  for (const child of node.children) {{
    visit(child);
    walk(child, visit);
  }}
}}

function matchesClassSelector(element, selector) {{
  if (!selector.startsWith('.')) {{
    return false;
  }}
  const className = selector.slice(1);
  return String(element.className || '')
    .split(/\\s+/)
    .filter(Boolean)
    .includes(className);
}}

function querySelectorAllFrom(root, selector) {{
  const selectors = String(selector)
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);

  const results = [];
  walk(root, (element) => {{
    if (selectors.some((part) => matchesClassSelector(element, part))) {{
      results.push(element);
    }}
  }});
  return results;
}}

class FakeElement {{
  constructor(tagName) {{
    this.tagName = String(tagName || '').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.style = {{}};
    this.attributes = {{}};
    this.textContent = '';
    this.innerHTML = '';
    this.className = '';
    this.classList = new FakeClassList(this);
    this.value = '';
    this.type = '';
    this.placeholder = '';
    this.onclick = null;
    this._listeners = new Map();
  }}

  appendChild(child) {{
    child.parentNode = this;
    this.children.push(child);
    return child;
  }}

  removeChild(child) {{
    const index = this.children.indexOf(child);
    if (index >= 0) {{
      this.children.splice(index, 1);
      child.parentNode = null;
    }}
    return child;
  }}

  setAttribute(name, value) {{
    this.attributes[name] = String(value);
    if (name === 'class') {{
      this.className = String(value);
      this.classList = new FakeClassList(this);
      for (const part of this.className.split(/\\s+/).filter(Boolean)) {{
        this.classList.add(part);
      }}
    }}
  }}

  addEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    handlers.push(handler);
    this._listeners.set(type, handlers);
  }}

  removeEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    const index = handlers.indexOf(handler);
    if (index >= 0) {{
      handlers.splice(index, 1);
    }}
  }}

  dispatchEvent(event) {{
    const handlers = this._listeners.get(event.type) || [];
    for (const handler of [...handlers]) {{
      handler.call(this, event);
    }}
  }}

  closest(selector) {{
    let current = this;
    while (current) {{
      if (matchesClassSelector(current, selector)) {{
        return current;
      }}
      current = current.parentNode;
    }}
    return null;
  }}

  querySelectorAll(selector) {{
    return querySelectorAllFrom(this, selector);
  }}

  querySelector(selector) {{
    const results = this.querySelectorAll(selector);
    return results.length > 0 ? results[0] : null;
  }}

  focus() {{}}

  select() {{}}
}}

const document = {{
  head: new FakeElement('head'),
  body: new FakeElement('body'),
  _listeners: new Map(),
  createElement(tagName) {{
    return new FakeElement(tagName);
  }},
  addEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    handlers.push(handler);
    this._listeners.set(type, handlers);
  }},
  removeEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    const index = handlers.indexOf(handler);
    if (index >= 0) {{
      handlers.splice(index, 1);
    }}
  }},
  dispatchEvent(event) {{
    const handlers = this._listeners.get(event.type) || [];
    for (const handler of [...handlers]) {{
      handler.call(this, event);
    }}
  }},
  querySelectorAll(selector) {{
    const root = {{
      children: [this.head, this.body],
    }};
    return querySelectorAllFrom(root, selector);
  }},
  querySelector(selector) {{
    const results = this.querySelectorAll(selector);
    return results.length > 0 ? results[0] : null;
  }},
}};

const silentConsole = {{
  log() {{}},
  warn() {{}},
  error(...args) {{
    process.stderr.write(args.join(' ') + '\\n');
  }},
}};

global.window = global;
global.document = document;
global.navigator = {{}};
global.console = silentConsole;
global.Blob = class FakeBlob {{
  constructor(parts, options) {{
    this.parts = parts;
    this.type = options && options.type ? options.type : '';
  }}
}};
global.requestAnimationFrame = function (callback) {{
  return setTimeout(() => callback(Date.now()), 0);
}};
global.cancelAnimationFrame = function (id) {{
  clearTimeout(id);
}};
global.safeT = function (_key, fallback) {{
  return fallback || _key;
}};

const source = fs.readFileSync({json.dumps(str(COMMON_DIALOGS_PATH))}, 'utf8');
vm.runInThisContext(source, {{ filename: {json.dumps(str(COMMON_DIALOGS_PATH))} }});

function wait(ms) {{
  return new Promise((resolve) => setTimeout(resolve, ms));
}}

function overlayCount() {{
  return document.querySelectorAll('.modal-overlay').length;
}}

function modalButtonTexts() {{
  const overlays = document.querySelectorAll('.modal-overlay');
  if (overlays.length !== 1) {{
    return [];
  }}
  return overlays[0].querySelectorAll('.modal-btn').map((button) => button.textContent);
}}

async function runScenario() {{
{script_body}
}}

runScenario()
  .then((result) => {{
    process.stdout.write(JSON.stringify(result));
  }})
  .catch((error) => {{
    process.stderr.write(String(error && error.stack ? error.stack : error));
    process.exit(1);
  }});
"""

    result = subprocess.run(
        [node_executable, "-"],
        input=node_harness,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    if result.returncode != 0:
        raise AssertionError(
            "Node common_dialogs scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    return json.loads(result.stdout)


@pytest.mark.unit
def test_show_decision_prompt_blocks_implicit_dismiss_by_default():
    result = _run_common_dialogs_node_scenario(
        """
    const state = {
      resolved: [],
      overlayCountAfterOutside: null,
      overlayCountAfterEscape: null,
    };

    window.showDecisionPrompt({
      title: 'Implicit Dismiss Guard',
      message: 'pick one',
      buttons: [
        { value: 'start-now', text: 'Start', variant: 'primary' },
        { value: 'later', text: 'Later', variant: 'secondary' },
      ],
    }).then((value) => {
      state.resolved.push(value);
    });

    await wait(10);
    const overlay = document.querySelectorAll('.modal-overlay')[0];

    overlay.dispatchEvent({ type: 'click', target: overlay });
    await wait(10);
    state.overlayCountAfterOutside = overlayCount();

    document.dispatchEvent({ type: 'keydown', key: 'Escape' });
    await wait(10);
    state.overlayCountAfterEscape = overlayCount();

    assert.strictEqual(overlayCount(), 1);
    assert.deepStrictEqual(state.resolved, []);

    overlay.querySelectorAll('.modal-btn')[0].onclick();
    await wait(250);

    assert.strictEqual(overlayCount(), 0);
    assert.deepStrictEqual(state.resolved, ['start-now']);

    return state;
        """
    )

    assert result == {
        "resolved": ["start-now"],
        "overlayCountAfterOutside": 1,
        "overlayCountAfterEscape": 1,
    }


@pytest.mark.unit
def test_show_decision_prompt_serializes_concurrent_prompts():
    result = _run_common_dialogs_node_scenario(
        """
    const state = {
      shown: [],
      resolved: [],
    };

    window.showDecisionPrompt({
      title: 'Queue Test First',
      message: 'first message',
      closeOnClickOutside: false,
      closeOnEscape: false,
      buttons: [
        { value: 'first-ok', text: 'First OK', variant: 'primary' },
      ],
      onShown: function () {
        state.shown.push('first');
      },
    }).then((value) => {
      state.resolved.push({ name: 'first', value });
    });

    window.showDecisionPrompt({
      title: 'Queue Test Second',
      message: 'second message',
      closeOnClickOutside: false,
      closeOnEscape: false,
      buttons: [
        { value: 'second-ok', text: 'Second OK', variant: 'primary' },
      ],
      onShown: function () {
        state.shown.push('second');
      },
    }).then((value) => {
      state.resolved.push({ name: 'second', value });
    });

    await wait(10);
    assert.deepStrictEqual(state.shown, ['first']);
    assert.deepStrictEqual(state.resolved, []);
    assert.strictEqual(overlayCount(), 1);
    assert.deepStrictEqual(modalButtonTexts(), ['First OK']);

    document.querySelectorAll('.modal-overlay')[0].querySelectorAll('.modal-btn')[0].onclick();

    await wait(250);
    assert.deepStrictEqual(state.shown, ['first', 'second']);
    assert.deepStrictEqual(state.resolved, [
      { name: 'first', value: 'first-ok' },
    ]);
    assert.strictEqual(overlayCount(), 1);
    assert.deepStrictEqual(modalButtonTexts(), ['Second OK']);

    document.querySelectorAll('.modal-overlay')[0].querySelectorAll('.modal-btn')[0].onclick();

    await wait(250);
    assert.strictEqual(overlayCount(), 0);
    assert.deepStrictEqual(state.shown, ['first', 'second']);
    assert.deepStrictEqual(state.resolved, [
      { name: 'first', value: 'first-ok' },
      { name: 'second', value: 'second-ok' },
    ]);

    return state;
        """
    )

    assert result == {
        "shown": ["first", "second"],
        "resolved": [
            {"name": "first", "value": "first-ok"},
            {"name": "second", "value": "second-ok"},
        ],
    }
