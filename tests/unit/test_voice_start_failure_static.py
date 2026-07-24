import json
import re
import shutil
import subprocess
from pathlib import Path
from tests.static_app_parts import read_path_or_parts

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-capture.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"


def _read(path: Path) -> str:
    return read_path_or_parts(path)


def _js_function_block(source: str, function_name: str) -> str:
    marker = f"function {function_name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing JS function {function_name}")
    brace = source.find("{", start)
    if brace < 0:
        raise AssertionError(f"missing opening brace for JS function {function_name}")

    end = _balanced_js_block_end(source, brace)
    return source[start : end + 1]


def _balanced_js_block_end(source: str, brace: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    regex_literal = False
    regex_char_class = False
    previous_significant: str | None = None

    def can_start_regex(previous: str | None) -> bool:
        return previous is None or previous in "({[=,:;!&|?~^<>"

    index = brace
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
                previous_significant = "operand"
            index += 1
            continue

        if regex_literal:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "[":
                regex_char_class = True
            elif char == "]":
                regex_char_class = False
            elif char == "/" and not regex_char_class:
                regex_literal = False
                previous_significant = "/"
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char == "/" and can_start_regex(previous_significant):
            regex_literal = True
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
            previous_significant = char
        elif char == "}":
            depth -= 1
            previous_significant = char
            if depth == 0:
                return index
        elif not char.isspace():
            previous_significant = char
        index += 1
    raise AssertionError("unterminated JS block")


def _catch_block_after(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing marker {marker!r}")
    match = re.search(r"\bcatch\s*\([^)]*\)\s*\{", source[start:])
    if not match:
        raise AssertionError(f"missing catch block after {marker!r}")
    catch_start = start + match.start()
    brace = source.find("{", catch_start)
    return source[catch_start : _balanced_js_block_end(source, brace) + 1]


def _event_listener_block(source: str, event_name: str) -> str:
    marker = f"window.addEventListener('{event_name}'"
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing event listener {event_name}")
    brace = source.find("{", start)
    if brace < 0:
        raise AssertionError(f"missing event listener body for {event_name}")
    return source[start : _balanced_js_block_end(source, brace) + 1]


def _mic_button_start_flow(source: str) -> str:
    marker = "micButton.addEventListener('click', async function () {"
    start = source.find(marker)
    if start < 0:
        raise AssertionError("missing mic button click listener")
    brace = source.find("{", start)
    return source[start : _balanced_js_block_end(source, brace) + 1]


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
const screenButton = new FakeButton();
const stopCalls = [];
const startScreenCalls = [];
const stopScreenCalls = [];

global.localStorage = {{
  value: null,
  getItem() {{ return this.value; }},
  setItem(_key, value) {{ this.value = String(value); }},
}};

global.window = {{
  appState: {{
    dom: {{
      micButton,
      screenButton,
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
  async dispatchScreenToggle(active) {{
    const handlers = this._listeners.get('live2d-screen-toggle') || [];
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
  startScreenSharing: async function () {{
    startScreenCalls.push('start');
    screenButton.classList.add('active');
  }},
  stopScreenSharing: async function () {{
    stopScreenCalls.push('stop');
    screenButton.classList.remove('active');
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
      startScreenCalls,
      stopScreenCalls,
      screenClasses: screenButton.classList.toArray(),
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


def test_mic_capture_failure_restores_composer_without_outer_voice_start_lifecycle():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    start_mic = _js_function_block(source, "startMicCapture")
    failure = _catch_block_after(start_mic, "S.stream = await navigator.mediaDevices.getUserMedia(constraints);")

    assert "S.voiceStartPending = false;" not in failure
    assert "window.isMicStarting = false;" not in failure
    assert "const hasOuterVoiceStartLifecycle = !!(S.voiceStartPending || window.isMicStarting);" in failure
    restore_start = failure.index("if (!hasOuterVoiceStartLifecycle) {")
    throw_index = failure.index("throw err;")
    restore_block = failure[restore_start:throw_index]
    assert "S.isRecording = false;" in restore_block
    assert "window.isRecording = false;" in restore_block
    assert "S.voiceChatActive = false;" in restore_block
    assert "textInputArea.classList.remove('hidden')" in restore_block
    assert "window.syncVoiceChatComposerHidden(false)" in restore_block
    assert "stopGameVoiceSttGate({ restoreOrdinaryMic: false });" in failure
    assert failure.index("stopGameVoiceSttGate({ restoreOrdinaryMic: false });") < throw_index


def test_floating_mic_popup_keeps_speaker_volume_without_microphone_devices():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    render_start = source.index("window.renderFloatingMicList = async function")
    render_end = source.index("function updateMicListSelection()", render_start)
    render = source[render_start:render_end]

    assert "var hasMicrophoneDevices = audioInputs.length > 0;" in render
    permission_refresh = "audioInputs = await ensureMicrophonePermission();"
    assert "if (!audioInputs || audioInputs.length === 0 || !micPermissionGranted)" in render
    assert permission_refresh in render
    assert render.index(permission_refresh) < render.index(
        "var hasMicrophoneDevices = audioInputs.length > 0;"
    )
    assert "micPopup.appendChild(noMicItem);\n                return true;" not in render

    layout_index = render.index("// ===== 双栏布局 =====")
    speaker_index = render.index("speakerContainer.className = 'speaker-volume-container';")
    gain_guard_index = render.index("if (!hasMicrophoneDevices) {")
    no_devices_index = render.index("noMicItem.textContent = window.t ? window.t('microphone.noDevices')")

    assert layout_index < speaker_index < gain_guard_index < no_devices_index
    assert "leftColumn.appendChild(speakerContainer);" in render
    assert "gainSlider.disabled = true;" in render
    assert "listBody.appendChild(noMicItem);" in render


def test_floating_mic_popup_exposes_screen_share_start_and_stop_action():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    screen_panel = _js_function_block(source, "openScreenSourceSubwindow")

    assert "shareToggleButton.dataset.nekoScreenShareAction = 'toggle';" in screen_panel
    assert "window.startScreenSharing" in screen_panel
    assert "window.stopScreenSharing" in screen_panel
    assert "window.t('buttons.stopShare')" in screen_panel
    assert "panelBody.appendChild(shareToggleButton);" in screen_panel
    voice_guard = "if (!window.isRecording) {"
    start_call = "await window.startScreenSharing();"
    assert voice_guard in screen_panel
    assert "window.t('app.screenShareRequiresVoice')" in screen_panel
    assert screen_panel.index(voice_guard) < screen_panel.index(start_call)


def test_mic_device_subwindow_retries_permission_when_device_cache_is_empty():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    permission = _js_function_block(source, "ensureMicrophonePermission")
    device_panel = _js_function_block(source, "openMicDeviceSubwindow")

    assert "micPermissionGranted && cachedMicDevices && cachedMicDevices.length > 0" in permission
    assert "if (!devices || devices.length === 0 || !micPermissionGranted)" in device_panel
    assert "devices = await ensureMicrophonePermission();" in device_panel


def test_outer_voice_start_failure_clears_pending_flags_before_composer_restore():
    source = _read(APP_BUTTONS_PATH)
    start_flow = _mic_button_start_flow(source)
    catch_split = start_flow.split("} catch (error) {", 1)
    assert len(catch_split) == 2, "missing outer catch in mic button start flow"
    cleanup_marker = "screenButton.classList.remove('active');"
    cleanup_split = catch_split[1].split(cleanup_marker, 1)
    assert len(cleanup_split) == 2, "missing screen button cleanup in mic button failure flow"
    failure = cleanup_split[0]

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


def test_voice_preparing_toast_ignores_module_object_messages():
    source = _read(APP_UI_PATH)
    normalizer = _js_function_block(source, "normalizeVoiceToastMessage")
    toast = _js_function_block(source, "showVoicePreparingToast")

    assert "fallbackKey = 'app.voiceSystemPreparing'" in normalizer
    assert "window.safeT('app.voiceSystemPreparing'" not in normalizer
    assert "translatedFallback.trim() !== fallbackKey" in normalizer
    assert "text === '[object Module]'" in normalizer
    assert "text === '[object Object]'" in normalizer
    assert "window.translateStatusMessage(message)" in normalizer
    assert "msgSpan.textContent = normalizeVoiceToastMessage(message);" in toast
    assert "msgSpan.textContent = message;" not in toast


def test_outer_voice_start_failure_uses_sanitized_toast_message():
    source = _read(APP_BUTTONS_PATH)
    normalizer = _js_function_block(source, "getVoiceStartErrorMessage")
    start_flow = _mic_button_start_flow(source)
    catch_split = start_flow.split("} catch (error) {", 1)
    assert len(catch_split) == 2, "missing outer catch in mic button start flow"
    failure = catch_split[1]

    assert "fallbackKey = 'app.sessionFailed'" in normalizer
    assert "window.safeT('app.sessionFailed'" not in normalizer
    assert "translatedFallback.trim() !== fallbackKey" in normalizer
    assert "text === '[object Module]'" in normalizer
    assert "text === '[object Object]'" in normalizer
    assert "window.translateStatusMessage(error)" in normalizer
    assert "var voiceStartErrorMessage = getVoiceStartErrorMessage(error);" in failure
    assert "window.showVoicePreparingToast(voiceStartErrorMessage);" in failure
    assert "window.showStatusToast(voiceStartErrorMessage, 5000);" in failure
    assert "window.showVoicePreparingToast(error.message)" not in failure
    assert "window.showStatusToast(error.message, 5000)" not in failure


def test_floating_mic_stale_active_state_reenters_main_voice_start_lifecycle():
    source = _read(APP_UI_PATH)
    listeners = _js_function_block(source, "initFloatingButtonListeners")
    mic_toggle = _event_listener_block(listeners, "live2d-mic-toggle")

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


def test_voice_auto_screen_stops_owned_share_even_after_setting_is_disabled():
    result = _run_floating_mic_toggle_scenario(
        """
    localStorage.value = '1';
    S.isRecording = true;
    await window.dispatchMicToggle(true);
    localStorage.value = '0';
    await window.dispatchMicToggle(false);
    return {};
        """
    )

    assert result["startScreenCalls"] == ["start"]
    assert result["stopScreenCalls"] == ["stop"]
    assert result["screenClasses"] == []


def test_voice_auto_screen_does_not_own_cancelled_start():
    result = _run_floating_mic_toggle_scenario(
        """
    localStorage.value = '1';
    window.startScreenSharing = async function () {
      startScreenCalls.push('start');
    };
    S.isRecording = true;
    await window.dispatchMicToggle(true);
    await window.dispatchMicToggle(false);
    return {};
        """
    )

    assert result["startScreenCalls"] == ["start"]
    assert result["stopScreenCalls"] == []
    assert result["screenClasses"] == []


def test_voice_auto_screen_never_stops_user_owned_share():
    result = _run_floating_mic_toggle_scenario(
        """
    localStorage.value = '1';
    await window.dispatchScreenToggle(true);
    S.isRecording = true;
    await window.dispatchMicToggle(true);
    await window.dispatchMicToggle(false);
    return {};
        """
    )

    assert result["startScreenCalls"] == ["start"]
    assert result["stopScreenCalls"] == []
    assert result["screenClasses"] == ["active"]


def test_manual_screen_stop_clears_voice_share_ownership():
    result = _run_floating_mic_toggle_scenario(
        """
    localStorage.value = '1';
    S.isRecording = true;
    await window.dispatchMicToggle(true);
    await window.dispatchScreenToggle(false);
    await window.dispatchMicToggle(false);
    return {};
        """
    )

    assert result["startScreenCalls"] == ["start"]
    assert result["stopScreenCalls"] == ["stop"]
    assert result["screenClasses"] == []
