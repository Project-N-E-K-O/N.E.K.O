import json
import shutil
import subprocess
from pathlib import Path

import pytest


PROMPT_SHARED_PATH = Path(__file__).resolve().parents[2] / "static" / "app-prompt-shared.js"


def _run_prompt_shared_node_scenario(script_body: str) -> dict:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    node_harness = f"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class FakeElement {{}}

global.window = global;
global.Element = FakeElement;
// app-prompt-shared.js 模块顶层会注册 beforeunload/pagehide listener，
// node 的 global 默认没有 addEventListener，这里 noop stub 即可。
global.addEventListener = function () {{}};
global.removeEventListener = function () {{}};
global.document = {{
  visibilityState: 'visible',
  hasFocus() {{
    return true;
  }},
}};
global.navigator = {{}};
global.console = {{
  log() {{}},
  warn() {{}},
  error(...args) {{
    process.stderr.write(args.join(' ') + '\\n');
  }},
}};
global.safeT = function (_key, fallback) {{
  return fallback || _key;
}};

const source = fs.readFileSync({json.dumps(str(PROMPT_SHARED_PATH))}, 'utf8');
vm.runInThisContext(source, {{ filename: {json.dumps(str(PROMPT_SHARED_PATH))} }});

function wait(ms) {{
  return new Promise((resolve) => setTimeout(resolve, ms));
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
            "Node prompt_shared scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    return json.loads(result.stdout)


@pytest.mark.unit
def test_request_prompt_display_prioritizes_higher_priority_requests():
    result = _run_prompt_shared_node_scenario(
        """
    const tools = window.nekoPromptShared.createPromptTools();
    const state = {
      shown: [],
      resolved: [],
    };

    const autostartPromise = tools.requestPromptDisplay({
      key: 'autostart',
      priority: 100,
      shouldDisplay: () => true,
      display: async () => {
        state.shown.push('autostart');
        await wait(10);
        return 'autostart';
      },
    }).then((value) => {
      state.resolved.push({ name: 'autostart', value });
    });

    const tutorialPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 200,
      shouldDisplay: () => true,
      display: async () => {
        state.shown.push('tutorial');
        await wait(10);
        return 'tutorial';
      },
    }).then((value) => {
      state.resolved.push({ name: 'tutorial', value });
    });

    await Promise.all([autostartPromise, tutorialPromise]);
    return state;
        """
    )

    assert result == {
        "shown": ["tutorial", "autostart"],
        "resolved": [
            {"name": "tutorial", "value": "tutorial"},
            {"name": "autostart", "value": "autostart"},
        ],
    }


@pytest.mark.unit
def test_request_prompt_display_reuses_existing_keyed_request():
    result = _run_prompt_shared_node_scenario(
        """
    const tools = window.nekoPromptShared.createPromptTools();
    let shownCount = 0;

    const firstPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 100,
      shouldDisplay: () => true,
      display: async () => {
        shownCount += 1;
        return 'first';
      },
    });

    const secondPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 200,
      shouldDisplay: () => true,
      display: async () => {
        shownCount += 1;
        return 'second';
      },
    });

    const values = await Promise.all([firstPromise, secondPromise]);
    return {
      samePromise: firstPromise === secondPromise,
      shownCount,
      values,
    };
        """
    )

    assert result == {
        "samePromise": True,
        "shownCount": 1,
        "values": ["second", "second"],
    }


@pytest.mark.unit
def test_request_prompt_display_rechecks_priority_after_async_guard():
    result = _run_prompt_shared_node_scenario(
        """
    const tools = window.nekoPromptShared.createPromptTools();
    const state = {
      shown: [],
      resolved: [],
    };

    const lowPromise = tools.requestPromptDisplay({
      key: 'autostart',
      priority: 100,
      shouldDisplay: async () => {
        await wait(50);
        return true;
      },
      display: async () => {
        state.shown.push('autostart');
        return 'autostart';
      },
    }).then((value) => {
      state.resolved.push({ name: 'autostart', value });
    });

    await wait(10);

    const highPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 200,
      shouldDisplay: () => true,
      display: async () => {
        state.shown.push('tutorial');
        return 'tutorial';
      },
    }).then((value) => {
      state.resolved.push({ name: 'tutorial', value });
    });

    await Promise.all([lowPromise, highPromise]);
    return state;
        """
    )

    assert result == {
        "shown": ["tutorial", "autostart"],
        "resolved": [
            {"name": "tutorial", "value": "tutorial"},
            {"name": "autostart", "value": "autostart"},
        ],
    }


@pytest.mark.unit
def test_request_prompt_display_invalidates_stale_same_key_guard_results():
    result = _run_prompt_shared_node_scenario(
        """
    const tools = window.nekoPromptShared.createPromptTools();
    const state = {
      shown: [],
      resolved: [],
    };

    const firstPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 100,
      shouldDisplay: async () => {
        await wait(100);
        return true;
      },
      display: async () => {
        state.shown.push('stale-display');
        return 'stale-display';
      },
    }).then((value) => {
      state.resolved.push({ name: 'first', value });
    });

    await wait(90);

    const secondPromise = tools.requestPromptDisplay({
      key: 'tutorial',
      priority: 100,
      shouldDisplay: () => false,
      display: async () => {
        state.shown.push('fresh-display');
        return 'fresh-display';
      },
    }).then((value) => {
      state.resolved.push({ name: 'second', value });
    });

    await Promise.all([firstPromise, secondPromise]);
    return state;
        """
    )

    assert result == {
        "shown": [],
        "resolved": [
            {"name": "first", "value": None},
            {"name": "second", "value": None},
        ],
    }
