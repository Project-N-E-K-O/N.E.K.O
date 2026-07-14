import json
import shutil
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"


def test_startup_default_cat_waits_for_avatar_then_uses_the_existing_goodbye_flow():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "window.addEventListener('neko:startup-default-form'" in source
    assert "if (detail.form === 'cat') requestStartupDefaultCat();" in source
    assert "STARTUP_DEFAULT_CAT_MAX_ATTEMPTS = 300" in source
    assert "#live2d-btn-goodbye, #vrm-btn-goodbye, #mmd-btn-goodbye, #pngtuber-btn-goodbye" in source
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'" in source
    assert "startupDefaultForm: 'cat'" in source


def test_startup_default_cat_retry_is_deferred_and_user_actions_cancel_it():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "if (!state.started)" in source
    assert "isTutorialGuardActive()" in source
    assert "window.isNekoHomeTutorialPending === true" in source
    assert "cancelStartupDefaultCatRequest(false);" in source
    assert "detail.startupDefaultForm !== 'cat' && state.startupDefaultCatRequested" in source
    assert "const handleReturn = () => {\n            // Returning" in source
    assert "cancelStartupDefaultCatRequest(true);" in source
    assert "function consumeStartupDefaultCatRequest()" in source
    assert "consumeStartupDefaultCatRequest: consumeStartupDefaultCatRequest" in source


def test_startup_default_cat_is_cat1_and_has_a_distinct_silence_reason():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "const isStartupDefaultCat = detail.startupDefaultForm === 'cat';" in source
    assert "state.lastReason = isStartupDefaultCat ? 'startup-default-cat' : 'manual-goodbye';" in source
    assert "source: isStartupDefaultCat ? 'startup-default-form' : 'manual-goodbye'" in source
    assert "setVisualTier(TIER_CAT1" in source


def test_startup_default_cat_request_survives_tutorial_and_runs_after_unlock():
    node = shutil.which("node")
    if not node:
        raise AssertionError("node is required to run startup default cat harness")

    # 复现 PC 冷启动直进教程：默认猫咪信号先到，但教程临时 YUI 按钮已经存在。
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync({json.dumps(str(APP_AUTO_GOODBYE))}, 'utf8');
        const listeners = new Map();
        const timers = [];
        const goodbyeEvents = [];
        class CustomEvent {{
          constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }}
        }}
        const window = {{
          location: {{ pathname: '/' }},
          appConst: {{}},
          appState: {{ socket: {{ readyState: 0, send() {{}} }} }},
          live2dManager: {{ _goodbyeClicked: false }},
          vrmManager: {{ _goodbyeClicked: false }},
          mmdManager: {{ _goodbyeClicked: false }},
          isInTutorial: true,
          isNekoHomeTutorialPending: true,
          addEventListener(type, handler) {{
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
          }},
          dispatchEvent(event) {{
            for (const handler of listeners.get(event.type) || []) handler(event);
          }},
          setTimeout(handler) {{ timers.push(handler); return timers.length; }},
          clearTimeout() {{}},
          setInterval() {{ return 1; }},
          clearInterval() {{}},
          waitForStorageLocationStartupBarrier() {{ return Promise.resolve(); }},
        }};
        const tutorialButton = {{}};
        const document = {{
          readyState: 'complete',
          body: {{ classList: {{ contains() {{ return false; }} }} }},
          addEventListener() {{}},
          getElementById(id) {{ return id === 'resetSessionButton' ? {{}} : null; }},
          querySelector(selector) {{ return selector.includes('#live2d-btn-goodbye') ? tutorialButton : null; }},
        }};
        window.document = document;
        window.addEventListener('live2d-goodbye-click', (event) => {{
          goodbyeEvents.push(event.detail);
          window.live2dManager._goodbyeClicked = true;
        }});
        vm.runInNewContext(source, {{ window, document, CustomEvent, WebSocket: {{ OPEN: 1 }}, console, Date, Math, Promise, Set }});

        Promise.resolve().then(() => Promise.resolve()).then(() => {{
          window.dispatchEvent(new CustomEvent('neko:startup-default-form', {{ detail: {{ form: 'cat' }} }}));
          if (goodbyeEvents.length !== 0) throw new Error('tutorial consumed startup cat request');
          if (window.nekoAutoGoodbye.getState().startupDefaultCatRequested !== true) {{
            throw new Error('startup cat request was not kept pending');
          }}
          window.isInTutorial = false;
          window.isNekoHomeTutorialPending = false;
          const retry = timers.shift();
          if (typeof retry !== 'function') throw new Error('missing deferred retry');
          retry();
          if (goodbyeEvents.length !== 1 || goodbyeEvents[0].startupDefaultForm !== 'cat') {{
            throw new Error('startup cat request did not run after tutorial unlock');
          }}
        }}).catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
