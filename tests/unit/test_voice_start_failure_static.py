import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE_PATH = PROJECT_ROOT / "static" / "app-audio-capture.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app-buttons.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_function_block(source: str, function_name: str) -> str:
    marker = f"function {function_name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing JS function {function_name}")
    brace = source.find("{", start)
    if brace < 0:
        raise AssertionError(f"missing opening brace for JS function {function_name}")

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JS function {function_name}")


def _catch_block_after(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing marker {marker!r}")
    catch_start = source.find("catch (err) {", start)
    if catch_start < 0:
        raise AssertionError(f"missing catch block after {marker!r}")
    return source[catch_start:].split("throw err;", 1)[0]


def _run_floating_mic_toggle_scenario(script_body: str) -> dict:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    listeners = _js_function_block(_read(APP_UI_PATH), "initFloatingButtonListeners")
    node_harness = f"""
const assert = require('assert');
const vm = require('vm');

class FakeClassList {{
  constructor() {{
    this.names = new Set();
  }}
  add(...names) {{
    for (const name of names) this.names.add(String(name));
  }}
  remove(...names) {{
    for (const name of names) this.names.delete(String(name));
  }}
  contains(name) {{
    return this.names.has(String(name));
  }}
  toArray() {{
    return Array.from(this.names).sort();
  }}
}}

class FakeButton {{
  constructor() {{
    this.classList = new FakeClassList();
    this.disabled = false;
    this.clickCount = 0;
  }}
  click() {{
    this.clickCount += 1;
  }}
}}

const micButton = new FakeButton();
const stopCalls = [];

global.window = {{
  appState: {{
    dom: {{
      micButton,
      screenButton: new FakeButton(),
      resetSessionButton: new FakeButton(),
      muteButton: new FakeButton(),
      stopButton: new FakeButton(),
      textSendButton: new FakeButton(),
      textInputBox: {{}},
      screenshotButton: new FakeButton(),
    }},
    isRecording: false,
    voiceStartPending: false,
  }},
  _listeners: new Map(),
  isMicStarting: false,
  addEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    handlers.push(handler);
    this._listeners.set(type, handlers);
  }},
  async dispatchMicToggle(active) {{
    const handlers = this._listeners.get('live2d-mic-toggle') || [];
    for (const handler of handlers) {{
      await handler({{ detail: {{ active }} }});
    }}
  }},
  stopMicCapture: async function () {{
    stopCalls.push('stop');
  }},
  startMicCapture: async function () {{
    throw new Error('floating mic toggle must not call startMicCapture directly');
  }},
}};

const S = window.appState;
vm.runInThisContext({json.dumps(listeners)}, {{ filename: 'initFloatingButtonListeners.js' }});
initFloatingButtonListeners();

async function runScenario() {{
{script_body}
}}

runScenario()
  .then((result) => {{
    process.stdout.write(JSON.stringify({{
      result,
      mic: {{
        clicks: micButton.clickCount,
        disabled: micButton.disabled,
        classes: micButton.classList.toArray(),
      }},
      stopCalls,
    }}));
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
            "Node floating mic toggle scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def test_mic_capture_failure_does_not_own_voice_lifecycle_or_composer_restore():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    start_mic = _js_function_block(source, "startMicCapture")
    failure = _catch_block_after(start_mic, "S.stream = await navigator.mediaDevices.getUserMedia(constraints);")

    assert "window.syncVoiceChatComposerHidden(false)" not in failure
    assert "textInputArea.classList.remove('hidden')" not in failure
    assert "S.voiceStartPending = false;" not in failure
    assert "window.isMicStarting = false;" not in failure
    assert "S.voiceChatActive = false;" not in failure
    assert "S.isRecording = false;" not in failure
    assert "window.isRecording = false;" not in failure
    assert "stopGameVoiceSttGate({ restoreOrdinaryMic: false });" in failure


def test_outer_voice_start_failure_clears_pending_flags_before_composer_restore():
    source = _read(APP_BUTTONS_PATH)
    start_flow = source.split("micButton.addEventListener('click', async function () {", 1)[1].split(
        "// ----------------------------------------------------------------\n        // Screen button click",
        1,
    )[0]
    failure = start_flow.split("} catch (error) {", 1)[1].split("screenButton.classList.remove('active');", 1)[0]

    sync_call = "window.syncVoiceChatComposerHidden(preserveGoodbyeUi);"
    assert "S.voiceStartPending = false;" in failure
    assert "window.isMicStarting = false;" in failure
    assert "S.voiceChatActive = false;" in failure
    assert "S.isRecording = false;" in failure
    assert "window.isRecording = false;" in failure
    assert sync_call in failure
    assert failure.index("S.voiceStartPending = false;") < failure.index(sync_call)
    assert failure.index("window.isMicStarting = false;") < failure.index(sync_call)
    assert failure.index("S.voiceChatActive = false;") < failure.index(sync_call)


def test_floating_mic_stale_active_state_reenters_main_voice_start_lifecycle():
    source = _read(APP_UI_PATH)
    listeners = _js_function_block(source, "initFloatingButtonListeners")
    mic_toggle = listeners.split("window.addEventListener('live2d-mic-toggle'", 1)[1].split(
        "// 屏幕分享按钮（toggle模式）",
        1,
    )[0]

    assert "micButton.click();" in mic_toggle
    pending_guard = "if (S.voiceStartPending || window.isMicStarting) {"
    stale_cleanup = "micButton.classList.remove('active');"
    assert pending_guard in mic_toggle
    assert mic_toggle.index(pending_guard) < mic_toggle.index(stale_cleanup)
    assert "micButton.classList.remove('active');" in mic_toggle
    assert "micButton.classList.remove('recording');" in mic_toggle
    assert "micButton.disabled = false;" in mic_toggle
    assert "window.startMicCapture()" not in mic_toggle


@pytest.mark.parametrize(
    ("name", "script_body", "expected"),
    [
        (
            "idle active click enters the main mic lifecycle",
            """
    await window.dispatchMicToggle(true);
    return {};
            """,
            {"clicks": 1, "disabled": False, "classes": [], "stopCalls": []},
        ),
        (
            "recording active click is ignored",
            """
    S.isRecording = true;
    micButton.classList.add('active', 'recording');
    await window.dispatchMicToggle(true);
    return {};
            """,
            {"clicks": 0, "disabled": False, "classes": ["active", "recording"], "stopCalls": []},
        ),
        (
            "pending voice start active click does not restart or clean active state",
            """
    S.voiceStartPending = true;
    micButton.disabled = true;
    micButton.classList.add('active');
    await window.dispatchMicToggle(true);
    return {};
            """,
            {"clicks": 0, "disabled": True, "classes": ["active"], "stopCalls": []},
        ),
        (
            "mic starting active click does not restart or clean active state",
            """
    window.isMicStarting = true;
    micButton.disabled = true;
    micButton.classList.add('active');
    await window.dispatchMicToggle(true);
    return {};
            """,
            {"clicks": 0, "disabled": True, "classes": ["active"], "stopCalls": []},
        ),
        (
            "stale active failed start is normalized before re-entering main lifecycle",
            """
    micButton.disabled = true;
    micButton.classList.add('active', 'recording');
    await window.dispatchMicToggle(true);
    return {};
            """,
            {"clicks": 1, "disabled": False, "classes": [], "stopCalls": []},
        ),
        (
            "inactive toggle during recording stops mic capture",
            """
    S.isRecording = true;
    micButton.classList.add('active', 'recording');
    await window.dispatchMicToggle(false);
    return {};
            """,
            {"clicks": 0, "disabled": False, "classes": ["active", "recording"], "stopCalls": ["stop"]},
        ),
        (
            "inactive toggle while already stopped is ignored",
            """
    await window.dispatchMicToggle(false);
    return {};
            """,
            {"clicks": 0, "disabled": False, "classes": [], "stopCalls": []},
        ),
    ],
)
def test_floating_mic_toggle_actual_state_matrix(name, script_body, expected):
    result = _run_floating_mic_toggle_scenario(script_body)

    assert result["mic"]["clicks"] == expected["clicks"], name
    assert result["mic"]["disabled"] is expected["disabled"], name
    assert result["mic"]["classes"] == expected["classes"], name
    assert result["stopCalls"] == expected["stopCalls"], name
