import json
import re
import shutil
import subprocess
from pathlib import Path
from tests.static_app_parts import read_path_or_parts

import pytest

from tests.node_harness import run_node_stdin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-capture.js"
APP_SCREEN_PATH = PROJECT_ROOT / "static" / "app" / "app-screen.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
COMMON_UI_PATH = PROJECT_ROOT / "static" / "common_ui.js"


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


def _catch_block_after(source: str, marker: str, binding: str | None = None) -> str:
    """The first ``catch`` block after ``marker``, optionally by its binding.

    Without ``binding`` this returns whatever catch comes first, which silently
    retargets the moment a best-effort ``catch (_)`` teardown is added in
    between -- the assertions then run against three lines of cleanup and fail
    for a reason that has nothing to do with what they check. Name the binding
    when the intent is a specific handler.
    """
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing marker {marker!r}")
    pattern = (
        rf"\bcatch\s*\(\s*{re.escape(binding)}\s*\)\s*\{{"
        if binding
        else r"\bcatch\s*\([^)]*\)\s*\{"
    )
    match = re.search(pattern, source[start:])
    if not match:
        raise AssertionError(
            f"missing catch block{f' (binding {binding!r})' if binding else ''} after {marker!r}"
        )
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

    result = run_node_stdin(
        node_executable,
        node_harness,
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
    failure = _catch_block_after(
        start_mic,
        "const microphoneOpenResult = await openMicrophoneStreamWithFallback(",
        binding="err",
    )

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
    toggle_factory = _js_function_block(source, "createScreenShareToggleButton")

    assert "button.dataset.nekoScreenShareAction = 'toggle';" in toggle_factory
    assert "window.startScreenSharing" in toggle_factory
    assert "window.stopScreenSharing" in toggle_factory
    assert "window.t('voiceControl.stopShare')" in toggle_factory
    voice_guard = "if (!window.isRecording) {"
    start_call = "await window.startScreenSharing();"
    assert voice_guard in toggle_factory
    assert "window.t('app.screenShareRequiresVoice')" in toggle_factory
    assert toggle_factory.index(voice_guard) < toggle_factory.index(start_call)

    # Activation animation: a blue wave fills from the knob, followed by sparkles.
    assert "neko-share-toggle-wave" in toggle_factory
    assert "createShareSparkleLayer()" in toggle_factory
    assert "isScreenShareActive()" in toggle_factory
    cleanup = "pruneShareToggleButtons();"
    register = "shareToggleButtonRegistry.push(button);"
    assert cleanup in toggle_factory
    assert toggle_factory.index(cleanup) < toggle_factory.index(register)
    assert "button.setAttribute('aria-label', accessibleLabel);" in toggle_factory
    assert "button.setAttribute('aria-busy', 'true');" in toggle_factory

    # 合并为一行：迷你胶囊开关嵌在「屏幕共享」设置行右侧（替换 chevron）
    render_start = source.index("window.renderFloatingMicList = async function")
    render_end = source.index("function updateMicListSelection()", render_start)
    render = source[render_start:render_end]
    screen_row = "leftColumn.insertBefore(screenActionButton, firstContent);"
    mic_row = "leftColumn.insertBefore(micActionButton, firstContent);"
    assert screen_row in render and mic_row in render
    assert render.index(screen_row) < render.index(mic_row)
    assert "createScreenShareToggleButton({ mini: true })" in render
    assert "screenActionButton.replaceChild(shareToggleButton, screenActionButton.lastChild);" in render
    assert "leftColumn.insertBefore(shareToggleButton, firstContent);" not in render


def test_screen_share_start_is_single_flight_across_ui_entry_points():
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    source = _read(APP_SCREEN_PATH)
    wrapper = "async " + _js_function_block(source, "startScreenSharing")
    pending_check = _js_function_block(source, "isScreenSharingStartPending")
    cancel_start = _js_function_block(source, "cancelPendingScreenSharingStart")
    assert "var screenSharingStartAttempt = null;" in source

    node_harness = f"""
const assert = require('assert');
const S = {{ screenCaptureStream: null }};
let screenSharingStartAttempt = null;
let startCalls = 0;
let releaseStart;
let discardCalls = 0;
function discardCancelledScreenSharingStart(attempt) {{
  discardCalls += 1;
  return attempt.cancelled;
}}
async function startScreenSharingOnce(attempt) {{
  startCalls += 1;
  await new Promise((resolve) => {{ releaseStart = resolve; }});
  return attempt.cancelled ? 'cancelled' : 'started';
}}
{pending_check}
{cancel_start}
{wrapper}

async function run() {{
  const first = startScreenSharing();
  const second = startScreenSharing();
  await Promise.resolve();
  assert.strictEqual(startCalls, 1, 'concurrent starts must share one capture attempt');
  assert.strictEqual(isScreenSharingStartPending(), true, 'the shared attempt must be observable while pending');
  releaseStart();
  assert.deepStrictEqual(await Promise.all([first, second]), ['started', 'started']);
  assert.strictEqual(isScreenSharingStartPending(), false, 'the attempt must clear after settling');

  let releaseCancelled;
  startScreenSharingOnce = async function (attempt) {{
    startCalls += 1;
    await new Promise((resolve) => {{ releaseCancelled = resolve; }});
    return attempt.cancelled ? 'cancelled' : 'unexpected';
  }};
  const cancelledStart = startScreenSharing();
  await Promise.resolve();
  assert.strictEqual(startCalls, 2, 'the guard must clear after the first attempt settles');
  assert.strictEqual(cancelPendingScreenSharingStart(), true);
  assert.strictEqual(discardCalls, 1, 'cancellation must immediately clean any already-acquired stream');
  assert.strictEqual(isScreenSharingStartPending(), false, 'a cancelled chooser must stop blocking retries immediately');

  let releaseReplacement;
  startScreenSharingOnce = async function (attempt) {{
    startCalls += 1;
    await new Promise((resolve) => {{ releaseReplacement = resolve; }});
    return attempt.cancelled ? 'cancelled' : 'restarted';
  }};
  const replacement = startScreenSharing();
  await Promise.resolve();
  assert.strictEqual(startCalls, 3, 'a replacement start must not reuse the cancelled chooser');
  assert.strictEqual(isScreenSharingStartPending(), true);

  releaseCancelled();
  assert.strictEqual(await cancelledStart, 'cancelled');
  assert.strictEqual(isScreenSharingStartPending(), true, 'the old finally must not clear the replacement attempt');

  releaseReplacement();
  assert.strictEqual(await replacement, 'restarted');
  assert.strictEqual(isScreenSharingStartPending(), false);
}}

run().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    result = run_node_stdin(
        node_executable,
        node_harness,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Node screen-share single-flight scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def test_pending_screen_share_cancellation_releases_the_late_stream():
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    source = _read(APP_SCREEN_PATH)
    remember_stream = _js_function_block(
        source, "rememberScreenSharingAttemptStream"
    )
    discard_start = _js_function_block(
        source, "discardCancelledScreenSharingStart"
    )

    node_harness = f"""
const assert = require('assert');
const window = {{ t: (key) => key }};
const safeT = (key, fallback) => fallback;
const idleTimer = setTimeout(() => {{}}, 1000);
idleTimer.unref();
const S = {{
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: 123,
  screenCaptureStreamIdleTimer: idleTimer,
}};
{remember_stream}
{discard_start}

const track = {{ stopCalls: 0, onended: () => {{}}, stop() {{ this.stopCalls += 1; }} }};
const lateStream = {{
  getVideoTracks: () => [track],
  getTracks: () => [track],
}};
const attempt = {{ cancelled: true, initialStream: null, acquiredStream: null }};
S.screenCaptureStream = rememberScreenSharingAttemptStream(attempt, lateStream);

assert.strictEqual(discardCancelledScreenSharingStart(attempt), true);
assert.strictEqual(track.stopCalls, 1, 'a stream returned after cancellation must be stopped');
assert.strictEqual(track.onended, null, 'late stream cleanup must not run the normal onended path');
assert.strictEqual(S.screenCaptureStream, null);
assert.strictEqual(S.screenCaptureStreamLastUsed, null);
assert.strictEqual(S.screenCaptureStreamIdleTimer, null);
assert.strictEqual(attempt.acquiredStream, null);

const proactiveTrack = {{ stopCalls: 0, stop() {{ this.stopCalls += 1; }} }};
const proactiveStream = {{
  getVideoTracks: () => [proactiveTrack],
  getTracks: () => [proactiveTrack],
}};
const lateTrack = {{ stopCalls: 0, stop() {{ this.stopCalls += 1; }} }};
const secondLateStream = {{
  getVideoTracks: () => [lateTrack],
  getTracks: () => [lateTrack],
}};
const racedAttempt = {{ cancelled: true, initialStream: null, acquiredStream: null }};
rememberScreenSharingAttemptStream(racedAttempt, secondLateStream);
S.screenCaptureStream = proactiveStream;
assert.strictEqual(discardCancelledScreenSharingStart(racedAttempt), true);
assert.strictEqual(lateTrack.stopCalls, 1);
assert.strictEqual(S.screenCaptureStream, proactiveStream, 'a concurrent proactive stream must be preserved');
assert.strictEqual(proactiveTrack.stopCalls, 0);

const cachedTrack = {{ stopCalls: 0, stop() {{ this.stopCalls += 1; }} }};
const cachedStream = {{
  getVideoTracks: () => [cachedTrack],
  getTracks: () => [cachedTrack],
}};
const cachedAttempt = {{ cancelled: true, initialStream: cachedStream, acquiredStream: cachedStream }};
S.screenCaptureStream = cachedStream;
assert.strictEqual(discardCancelledScreenSharingStart(cachedAttempt), true);
assert.strictEqual(cachedTrack.stopCalls, 0, 'cancellation must preserve a pre-existing cached stream');
assert.strictEqual(S.screenCaptureStream, cachedStream);

const throwingTrack = {{ onended: () => {{}}, stop() {{ throw new Error('stop failed'); }} }};
const throwingStream = {{
  getVideoTracks: () => [throwingTrack],
  getTracks: () => [throwingTrack],
}};
const throwingAttempt = {{ cancelled: true, initialStream: null, acquiredStream: throwingStream }};
S.screenCaptureStream = throwingStream;
S.screenCaptureStreamLastUsed = 456;
assert.strictEqual(discardCancelledScreenSharingStart(throwingAttempt), true);
assert.strictEqual(S.screenCaptureStream, null, 'track stop errors must not prevent rollback');
assert.strictEqual(S.screenCaptureStreamLastUsed, null);
assert.strictEqual(throwingAttempt.acquiredStream, null);
"""
    result = run_node_stdin(
        node_executable,
        node_harness,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Node pending screen-share cancellation scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def test_stale_screen_video_play_cannot_replace_the_new_sender():
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    source = _read(APP_SCREEN_PATH)
    start_streaming = _js_function_block(source, "startScreenVideoStreaming")

    node_harness = f"""
const assert = require('assert');
let nativeCaptureGeneration = 4;
let releasePlay;
let intervalCreations = 0;
const video = {{
  videoWidth: 0,
  videoHeight: 0,
  play: () => new Promise((resolve) => {{ releasePlay = resolve; }}),
}};
const document = {{ createElement: () => video }};
const oldTrack = {{}};
const oldStream = {{ getVideoTracks: () => [oldTrack] }};
const replacementStream = {{ getVideoTracks: () => [{{}}] }};
const S = {{
  screenCaptureStream: oldStream,
  screenCaptureStreamLastUsed: null,
  screenCaptureStreamIdleTimer: null,
  videoTrack: null,
  videoSenderInterval: null,
  socket: null,
}};
const C = {{ MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 }};
const WebSocket = {{ OPEN: 1 }};
function scheduleScreenCaptureIdleCheck() {{}}
async function stopLiveVisionStreamIfBlocked() {{ return false; }}
function captureCanvasFrame() {{ throw new Error('stale stream must not capture'); }}
function buildStreamDataMessage() {{ throw new Error('stale stream must not send'); }}
function setInterval() {{ intervalCreations += 1; return {{ id: intervalCreations }}; }}
function clearInterval() {{}}
{start_streaming}

async function run() {{
  startScreenVideoStreaming(oldStream, 'screen');
  S.screenCaptureStream = replacementStream;
  nativeCaptureGeneration += 1;
  releasePlay();
  await Promise.resolve();
  await Promise.resolve();
  assert.strictEqual(intervalCreations, 0, 'a stale video.play continuation must not create a sender');
  assert.strictEqual(S.videoSenderInterval, null);
  assert.strictEqual(S.screenCaptureStream, replacementStream);
}}

run().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    result = run_node_stdin(
        node_executable,
        node_harness,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Node stale screen-video continuation scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def test_every_screen_share_toggle_treats_a_pending_start_as_on():
    screen_source = _read(APP_SCREEN_PATH)
    common_ui_source = _read(COMMON_UI_PATH)
    audio_capture_source = _read(APP_AUDIO_CAPTURE_PATH)

    stop = _js_function_block(screen_source, "stopScreenSharing")
    switch = screen_source.split(
        "window.switchScreenSharing = async function () {", 1
    )[1].split("\n    };", 1)[0]
    assert "cancelPendingScreenSharingStart();" in stop
    assert "if (isScreenSharingStartPending())" in switch

    toggle = common_ui_source.split(
        "window.toggleScreenShare = function () {", 1
    )[1].split("\n};", 1)[0]
    assert "window.isScreenSharingStartPending()" in toggle
    assert "const isActiveOrPending = isActive || isStartPending;" in toggle
    assert "detail: { active: !isActiveOrPending }" in toggle

    inner_toggle = _js_function_block(audio_capture_source, "handleToggleClick")
    assert inner_toggle.index("if (startPending") < inner_toggle.index(
        "if (button._nekoShareBusy) return;"
    )
    assert "if (button._nekoShareCancelBusy) return;" in inner_toggle
    assert "finishShareToggleOperation(cancelGeneration);" in inner_toggle
    assert "await window.stopScreenSharing();" in inner_toggle
    assert "取消待处理启动失败" in inner_toggle

    start_once = "async " + _js_function_block(
        screen_source, "startScreenSharingOnce"
    )
    assert "rememberScreenSharingAttemptStream(attempt" in start_once
    assert "var captureStream = attempt.initialStream;" in start_once
    assert "startScreenVideoStreaming(captureStream, streamInputType);" in start_once
    assert "captureStream.getVideoTracks()[0].onended" in start_once
    onended = start_once.index("captureStream.getVideoTracks()[0].onended")
    stale_guard = start_once.index(
        "if (S.screenCaptureStream !== captureStream)", onended
    )
    onended_stop = start_once.index("stopScreening();", onended)
    assert stale_guard < onended_stop
    activate = start_once.index("screenButton().classList.add('active')")
    activation_guard = start_once.rfind(
        "discardCancelledScreenSharingStart(attempt)", 0, activate
    )
    assert activation_guard > start_once.index("fetchBackendScreenshot()")
    commit_stream = start_once.index("S.screenCaptureStream = captureStream;")
    first_post_capture_guard = start_once.index(
        "if (discardCancelledScreenSharingStart(attempt)) return;",
        start_once.index("// 使用标准的getDisplayMedia"),
    )
    assert first_post_capture_guard < commit_stream


def test_screen_share_toggle_has_blue_wave_and_four_point_sparkles():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    styles = _js_function_block(source, "injectShareToggleStyles")

    # The fill is clipped from the resting knob centre and settles on a blue background.
    assert ".neko-share-toggle-btn .neko-share-toggle-wave" in styles
    assert "clip-path:circle(0 at var(--neko-share-wave-x) 50%)" in styles
    assert "clip-path:circle(var(--neko-share-wave-radius) at var(--neko-share-wave-x) 50%)" in styles
    assert "#61ccff" in styles and "#44b7fe" in styles and "#269fe8" in styles
    assert "#8a5ce8" not in source
    assert "neko-share-toggle-goal" not in source
    assert "neko-share-toggle-fill" not in source
    assert "image-rendering:pixelated" not in source

    # Stars start only after the wave completes and are removed after one pass.
    fx = _js_function_block(source, "createShareWaveFx")
    assert "SHARE_WAVE_FILL_MS" in fx
    assert "setTimeout(startSparkles, SHARE_WAVE_FILL_MS)" in fx
    assert "is-sparkling" in fx
    assert "prefersReducedMotion" in fx
    assert "setInterval" not in fx
    sparkle_factory = _js_function_block(source, "createShareSparkleLayer")
    assert "http://www.w3.org/2000/svg" in sparkle_factory
    assert "M12 1.5C13.6 7.9" in sparkle_factory
    assert "#00aeef" in sparkle_factory
    assert "rgba(255,255,255,0.96)" in sparkle_factory

    # The real toggle uses the wave/sparkle layers; stopping never replays activation.
    toggle_factory = _js_function_block(source, "createScreenShareToggleButton")
    assert "document.createElement('canvas')" not in toggle_factory
    assert "neko-share-toggle-wave" in toggle_factory
    assert "neko-share-toggle-knob" in toggle_factory
    assert "waveFx.activate" in toggle_factory
    assert "waveFx.deactivate" in toggle_factory
    assert ".replay(" not in toggle_factory

    # Full and inline variants keep matching knob positions and wave origins.
    assert ".neko-share-toggle-btn.is-active .neko-share-toggle-knob{left:calc(100% - 36px);}" in styles
    assert ".neko-share-toggle-btn.neko-share-toggle-mini" in styles
    assert ".neko-share-toggle-mini.is-active .neko-share-toggle-knob{left:calc(100% - 21px);}" in styles
    assert "--neko-share-wave-x:20px" in styles
    assert "--neko-share-wave-x:12px" in styles
    assert "--neko-share-wave-radius:148%" in styles
    assert "--neko-share-wave-radius:116%" in styles
    assert "prefers-reduced-motion:reduce" in styles

    prune = _js_function_block(source, "pruneShareToggleButtons")
    assert "btn._nekoShareFxCleanup()" in prune

    # State remains sourced from the hidden #screenButton .active class.
    assert "isScreenShareActive" in source
    assert "MutationObserver" in source
    assert "syncShareToggleButtons" in source


def test_mic_main_action_matches_settings_chevron_and_hover_expands():
    source = _read(APP_AUDIO_CAPTURE_PATH)
    action_button = _js_function_block(source, "createMainActionButton")

    assert "arrow.textContent = '\\u203A';" in action_button or 'arrow.textContent = "\u203A";' in action_button or "arrow.textContent = '\u203A';" in action_button
    assert "fontSize: '16px'" in action_button
    assert "button.dataset.nekoMicMainAction = actionKey;" in action_button
    assert "openMicActionPanel(actionKey, onClick)" in action_button
    assert "button.addEventListener('mouseenter'" in action_button
    assert "scheduleMicActionHoverCollapse()" in action_button
    assert "createMainActionButton(" in source
    assert "'screen'" in source
    assert "openScreenSourceSubwindow" in source
    assert "MIC_ACTION_HOVER_COLLAPSE_MS = 260" in source
    assert "wireMicSubwindowHoverBridge" in source
    assert "textWrap.className = 'neko-mic-action-text';" in action_button
    assert "if (iconText) {" in action_button
    assert "screenActionButton.querySelector('.neko-mic-action-text')" in source
    assert "var screenActionButton = createMainActionButton(\n                null," in source
    assert "var micActionButton = createMainActionButton(\n                null," in source
    subwindow = _js_function_block(source, "createMicSubwindow")
    assert "if (iconText) {" in subwindow
    assert "titleWrap.appendChild(icon);" in subwindow
    assert (
        "window.t ? window.t('microphone.deviceTitle') : 'Select Microphone',\n"
        "                    null,"
    ) in source
    assert (
        "window.t ? window.t('buttons.screenShare') : 'Screen Share',\n"
        "                    null,"
    ) in source


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


def test_voice_start_bails_when_another_start_took_over_the_pending_slot():
    # Codex P2. On mobile the composer stays visible during an audio session, so
    # the user can send text inside the 500ms settle window after an audio ack.
    # app-websocket.js leaves _pendingSessionStartMode owned by that newer text
    # start but settles the audio promise anyway (its timeout is already gone,
    # so nothing else ever would). The audio flow then resumes -- and none of
    # the guards after the await can see what happened: the text ack changes
    # neither voiceSessionStartEpoch nor isMicStarting, so ensureVoiceStartCurrent
    # passes, and it never sets voiceInputRouteBlocked either. The microphone
    # opens and reclaims a lease onto the text session's blocked route.
    source = _read(APP_BUTTONS_PATH)
    start_flow = _mic_button_start_flow(source)

    # The takeover decision now lives in one micStartMustStandDown() check --
    # see tests/unit/test_voice_start_slot_ownership.py for what that check has
    # to consider, which grew well past the pending mode. What this case pins is
    # WHERE the resumed flow consults it.
    await_index = start_flow.index("await sessionStartPromise;")
    guard = "micStartMustStandDown()"
    assert guard in start_flow, (
        "the resumed voice start must notice that another start took the slot"
    )
    guard_index = start_flow.index(guard, await_index)

    # It has to come before BOTH downstream guards, because neither can see a
    # takeover -- that is the whole finding.
    assert guard_index < start_flow.index("ensureVoiceStartCurrent();", await_index)
    assert guard_index < start_flow.index("S.voiceInputRouteBlocked === true")
    assert guard_index < start_flow.index("await window.startMicCapture();")

    # And it must unwind without throwing: the generic catch used to clear
    # S.sessionStartedResolver / Rejecter / _pendingSessionStartMode
    # unconditionally, which would tear down the start that superseded us.
    bail = start_flow[guard_index:start_flow.index("ensureVoiceStartCurrent();", await_index)]
    bail_code = " ".join(
        line for line in bail.splitlines() if not line.strip().startswith("//")
    )
    assert "throw" not in bail_code
    # The newer start owns the shared timeout now; cancelling it is the same
    # cross-start damage this guard exists to prevent.
    assert "clearTimeout" not in bail_code

    # The unwind itself sits inside the check, and is gated there: it is global
    # (mic generation + isMicStarting), so it may not run while a newer AUDIO
    # start is driving that state.
    check = start_flow[start_flow.index("function micStartMustStandDown()"):await_index]
    assert "abortVoiceStartForBlockedRoute" in check
    assert "supersededByAudioStart" in check


def test_selection_change_cancellation_does_not_publish_voice_start_success():
    source = _read(APP_BUTTONS_PATH)
    start_flow = _mic_button_start_flow(source)

    await_marker = "var microphoneStarted = await window.startMicCapture();"
    cancellation_marker = "if (microphoneStarted !== true) {"
    success_marker = "window.dispatchEvent(new CustomEvent('neko:voice-session-started'));"
    assert await_marker in start_flow
    assert cancellation_marker in start_flow
    assert start_flow.index(await_marker) < start_flow.index(cancellation_marker)
    assert start_flow.index(cancellation_marker) < start_flow.index(success_marker)

    cancellation = start_flow[
        start_flow.index(cancellation_marker):start_flow.index(
            "ensureVoiceStartCurrent();",
            start_flow.index(cancellation_marker),
        )
    ]
    assert "microphoneStartCancelled.microphoneStartCancelled = true;" in cancellation
    assert "throw microphoneStartCancelled;" in cancellation

    catch_block = start_flow[start_flow.index("} catch (error) {"):]
    assert "var isMicrophoneStartCancelled = !!(" in catch_block
    assert "!isVoiceStartCancelled && !isMicrophoneStartCancelled" in catch_block
    assert (
        "if (!isVoiceStartCancelled "
        "&& !(error && error.voiceConfigSwitchTimedOut)"
        in catch_block
    ), "selection cancellation must still close the accepted backend voice session"
    assert "else if (!isMicrophoneStartCancelled)" in catch_block
