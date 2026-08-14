# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Frontend half of the avatar annotation chain, driven through real module code.

Three behaviours are pinned here, each by running the actual function bodies out
of ``static/app/`` under node rather than asserting on source text:

* the multi-display gate, which refuses to place an annotation when it cannot
  tell which monitor a full-screen capture covers;
* the proactive single frame carrying ``avatar_position`` at all;
* the explicit capture-type argument overriding a stale selected source id.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_SCREEN_PATH = PROJECT_ROOT / "static" / "app" / "app-screen.js"
APP_PROACTIVE_PATH = PROJECT_ROOT / "static" / "app" / "app-proactive.js"


def _node() -> str:
    found = shutil.which("node")
    if not found:
        pytest.skip("node not available")
    return found


def _balanced_block_end(source: str, brace: int) -> int:
    depth = 0
    quote = None
    escaped = False
    line_comment = False
    block_comment = False
    index = brace
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
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
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in "'\"`":
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AssertionError("unbalanced block")


def _fn(source: str, name: str) -> str:
    """Extract one function declaration, keeping an ``async`` prefix if present."""
    marker = f"function {name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing JS function {name}")
    prefix = ""
    head = source[max(0, start - 6):start]
    if head.endswith("async "):
        prefix = "async "
    brace = source.find("{", start)
    end = _balanced_block_end(source, brace)
    return prefix + source[start:end + 1]


def _run(script: str) -> dict:
    proc = run_node_stdin(_node(), script, capture_output=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _screen_src() -> str:
    return APP_SCREEN_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Multi-display gate
# --------------------------------------------------------------------------

_GATE_PRELUDE = """
const results = {};
function makeEnv(isExtended) {
  const win = {
    screen: { width: 1920, height: 1080 },
    outerWidth: 1200, innerWidth: 1200,
    outerHeight: 900, innerHeight: 860,
    screenX: 0, screenY: 0,
    live2dManager: {
      getModelScreenBounds() {
        return { centerX: 400, centerY: 500, width: 200, height: 400 };
      }
    }
  };
  if (typeof isExtended === 'boolean') win.screen.isExtended = isExtended;
  return win;
}
"""

_GATE_BODY = """
function build(win) {
  const window = win;
  const document = { getElementById() { return null; } };
  const getComputedStyle = () => ({ visibility: 'visible' });
  const mod = {};
  __MULTI_DISPLAY__
  __GET_POS__
  return { getAvatarScreenPosition, isKnownMultiDisplay };
}
"""


def _ttl_line(src: str) -> str:
    return [line for line in src.splitlines()
            if "MULTI_DISPLAY_CACHE_TTL_MS =" in line][0].strip()


def _multi_display_block(src: str) -> str:
    """The gate's real module state plus its two functions, ready to paste."""
    return (
        "var multiDisplayCache = null;\nvar multiDisplayCacheAt = 0;\n"
        + _ttl_line(src) + "\n"
        + _fn(src, "refreshMultiDisplayCache") + "\n"
        + _fn(src, "isKnownMultiDisplay")
    )


def _gate_script(tail: str) -> str:
    src = _screen_src()
    body = _GATE_BODY.replace("__MULTI_DISPLAY__", _multi_display_block(src)).replace(
        "__GET_POS__", _fn(src, "getAvatarScreenPosition")
    )
    return _GATE_PRELUDE + body + tail


@pytest.mark.unit
def test_multi_display_screen_capture_is_not_annotated():
    """Extended desktop: the coordinate math cannot tell which monitor was captured."""
    script = _gate_script("""
const api = build(makeEnv(true));
results.pos = api.getAvatarScreenPosition('screen');
console.log(JSON.stringify(results));
""")
    assert _run(script)["pos"] is None


@pytest.mark.unit
def test_single_display_screen_capture_is_unchanged():
    """The gate must be invisible to single-monitor users -- the common case."""
    script = _gate_script("""
const api = build(makeEnv(false));
results.pos = api.getAvatarScreenPosition('screen');
console.log(JSON.stringify(results));
""")
    pos = _run(script)["pos"]
    assert pos is not None
    # centerX 400/1920; centerY (500 + chromeTop 40)/1080 -- unchanged by the gate.
    assert abs(pos["centerX"] - 400 / 1920) < 1e-9
    assert abs(pos["centerY"] - 540 / 1080) < 1e-9


@pytest.mark.unit
def test_unknown_display_count_keeps_the_previous_behaviour():
    """No isExtended and no Electron bridge -> behave exactly as before the gate."""
    script = _gate_script("""
const api = build(makeEnv(undefined));
results.pos = api.getAvatarScreenPosition('screen');
console.log(JSON.stringify(results));
""")
    assert _run(script)["pos"] is not None


@pytest.mark.unit
def test_viewport_capture_is_untouched_by_the_gate():
    """Browser-tab capture normalizes against the viewport; monitors are irrelevant."""
    script = _gate_script("""
const api = build(makeEnv(true));
results.pos = api.getAvatarScreenPosition('viewport');
console.log(JSON.stringify(results));
""")
    assert _run(script)["pos"] is not None


# --------------------------------------------------------------------------
# buildStreamDataMessage: explicit capture type
# --------------------------------------------------------------------------

_BUILD_TMPL = """
const results = {};
function build(selectedSourceId) {
  const window = { screen: { width: 1920, height: 1080, isExtended: false } };
  const S = { screenCaptureStream: null, selectedScreenSourceId: selectedSourceId };
  const getAvatarScreenPosition = (captureType) =>
    captureType === 'screen' ? { centerX: 0.5, centerY: 0.5, width: 0.1, height: 0.2 } : null;
  __DETECT__
  __BUILD__
  return buildStreamDataMessage;
}
__TAIL__
"""


def _build_script(tail: str) -> str:
    src = _screen_src()
    return (
        _BUILD_TMPL
        .replace("__DETECT__", _fn(src, "detectScreenshotCaptureType"))
        .replace("__BUILD__", _fn(src, "buildStreamDataMessage"))
        .replace("__TAIL__", tail)
    )


@pytest.mark.unit
def test_explicit_capture_type_overrides_a_stale_window_source():
    """A leftover window:* source must not suppress a genuine full-screen frame.

    The default branch is deliberately pinned to the opposite answer, so a fix
    that ignores the explicit argument cannot pass by coincidence.
    """
    script = _build_script("""
const fn = build('window:9');
const explicit = fn('data:image/jpeg;base64,AAA', 'screen', 'window:9', 'screen');
const inferred = fn('data:image/jpeg;base64,AAA', 'screen', 'window:9');
results.explicitHasPos = Object.prototype.hasOwnProperty.call(explicit, 'avatar_position');
results.inferredHasPos = Object.prototype.hasOwnProperty.call(inferred, 'avatar_position');
console.log(JSON.stringify(results));
""")
    out = _run(script)
    assert out["explicitHasPos"] is True
    # Identical arguments minus the explicit type: the window source still wins,
    # so the assertion above cannot pass by falling through to inference.
    assert out["inferredHasPos"] is False


@pytest.mark.unit
def test_explicit_null_capture_type_suppresses_the_position():
    """Passing null means "confirmed unknowable", not "fall back to inference"."""
    script = _build_script("""
const fn = build(null);
const msg = fn('data:image/jpeg;base64,AAA', 'screen', null, null);
results.hasPos = Object.prototype.hasOwnProperty.call(msg, 'avatar_position');
console.log(JSON.stringify(results));
""")
    assert _run(script)["hasPos"] is False


@pytest.mark.unit
def test_camera_frames_never_carry_a_position():
    """Mobile camera shoots the real world; there is no avatar in frame."""
    script = _build_script("""
const fn = build(null);
const msg = fn('data:image/jpeg;base64,AAA', 'camera', null, 'screen');
results.hasPos = Object.prototype.hasOwnProperty.call(msg, 'avatar_position');
console.log(JSON.stringify(results));
""")
    assert _run(script)["hasPos"] is False


# --------------------------------------------------------------------------
# Shared screenshot helper: capture type is decided at capture time
# --------------------------------------------------------------------------

_NATIVE_OK = "({ success: true, dataUrl: 'data:image/png;base64,PNG' })"
_NATIVE_FAIL = "({ success: false, error: 'capture timed out' })"
# Capture succeeds, but the user switches from the window source to a full
# screen while the grab is still in flight.
_NATIVE_OK_THEN_SWITCH = (
    "(function () {"
    " S.selectedScreenSourceId = 'screen:0:0';"
    " return { success: true, dataUrl: 'data:image/png;base64,PNG' };"
    " })()"
)

_HELPER_TMPL = """
const results = {};
let selected = __SOURCE_ID__;
const S = {
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: null,
  get selectedScreenSourceId() { return selected; },
  set selectedScreenSourceId(v) { selected = v; }
};
const window = {
  detectScreenshotCaptureType: (stream, sourceId) => {
    if (sourceId) return sourceId.indexOf('screen:') === 0 ? 'screen' : null;
    return stream ? 'screen' : null;
  },
  captureDesktopSourceWithTimeout: async () => __NATIVE__,
  maybeClearSourceOnNotFound: () => {},
  scheduleScreenCaptureIdleCheck: () => {}
};
const getDesktopProvider = () => ({ captureSourceAsDataUrl: () => {} });
const acquireOrReuseCachedStream = async () => __STREAM__;
const captureFrameFromStream = async () => ({ dataUrl: 'data:image/jpeg;base64,STREAM' });
const fetchBackendScreenshot = async () => ({ dataUrl: 'data:image/jpeg;base64,BACKEND' });
__RESOLVE__
__HELPER__
captureProactiveChatScreenshotWithSource().then((shot) => {
  results.via = shot.via;
  results.captureType = shot.captureType === undefined ? '<missing>' : shot.captureType;
  results.data = shot.dataUrl;
  console.log(JSON.stringify(results));
});
"""


def _helper_script(*, source_id: str, stream: str, native: str = _NATIVE_OK) -> str:
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    return (
        _HELPER_TMPL
        .replace("__SOURCE_ID__", source_id)
        .replace("__STREAM__", stream)
        .replace("__NATIVE__", native)
        .replace("__RESOLVE__", _fn(proactive_src, "resolveCaptureTypeFor"))
        .replace("__HELPER__", _fn(proactive_src, "captureProactiveChatScreenshotWithSource"))
    )


@pytest.mark.unit
def test_helper_pairs_capture_type_with_the_native_frame_it_grabbed():
    """Switching source while the native grab is in flight must not re-label it."""
    out = _run(_helper_script(
        source_id="'window:9'", stream="null", native=_NATIVE_OK_THEN_SWITCH,
    ))
    assert out["via"] == "native"
    # Grabbed from window:9 -> must stay unannotatable even though S now says screen:0:0.
    assert out["captureType"] is None


@pytest.mark.unit
def test_helper_marks_backend_fallback_as_full_screen():
    """pyautogui grabs the whole desktop, whatever stale source id is lying around."""
    out = _run(_helper_script(
        source_id="'window:9'", stream="null", native=_NATIVE_FAIL,
    ))
    assert out["via"] == "backend"
    assert out["captureType"] == "screen"
    assert out["data"].endswith("BACKEND")


# The source is cleared mid-capture (what maybeClearSourceOnNotFound does) while
# a cached stream is left behind -- the one combination where handing the stream
# to the classifier flips the answer.
_NATIVE_OK_THEN_CLEAR_SOURCE = (
    "(function () {"
    " S.selectedScreenSourceId = null;"
    " S.screenCaptureStream = {};"
    " return { success: true, dataUrl: 'data:image/png;base64,PNG' };"
    " })()"
)


@pytest.mark.unit
def test_helper_never_classifies_a_native_frame_by_a_leftover_stream():
    """A native grab must be judged by the source id it was captured from.

    With the source cleared and a stale stream around, classifying from live
    state answers "full screen" for a frame that actually holds one window --
    i.e. annotates an image with no avatar in it.
    """
    out = _run(_helper_script(
        source_id="'window:9'", stream="null", native=_NATIVE_OK_THEN_CLEAR_SOURCE,
    ))
    assert out["via"] == "native"
    assert out["captureType"] is None


# --------------------------------------------------------------------------
# Proactive single frame
# --------------------------------------------------------------------------

_FRAME_TMPL = """
const results = { sent: [] };
const window = {
  screen: { width: 1920, height: 1080, isExtended: false },
  appUtils: { isMobile: () => false },
  detectScreenshotCaptureType: (stream, sourceId) => {
    if (sourceId) return sourceId.indexOf('screen:') === 0 ? 'screen' : null;
    return stream ? 'screen' : null;
  },
  captureDesktopSourceWithTimeout: async () => __NATIVE__,
  maybeClearSourceOnNotFound: () => {}
};
const WebSocket = { OPEN: 1 };
const S = {
  isRecording: true,
  socket: { readyState: 1, send: (payload) => results.sent.push(payload) },
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: null,
  selectedScreenSourceId: __SOURCE_ID__
};
let proactiveVisionFrameInFlight = false;
const isProactiveVisionEnabledNow = () => true;
const stopProactiveVisionDuringSpeech = () => {};
const getDesktopProvider = () => ({ nativeFrameCapture: true, captureSourceAsDataUrl: () => {} });
const acquireOrReuseCachedStream = async () => __STREAM__;
const captureFrameFromStream = async () => ({ dataUrl: 'data:image/jpeg;base64,STREAM' });
const fetchBackendScreenshot = async () => ({ dataUrl: 'data:image/jpeg;base64,BACKEND' });
const normalizeNativeCaptureDataUrlForStream = async () => 'data:image/jpeg;base64,NATIVE';
const getAvatarScreenPosition = (captureType) =>
  captureType === 'screen' ? { centerX: 0.5, centerY: 0.5, width: 0.1, height: 0.2 } : null;
__DETECT__
__BUILD__
__FRAME__
sendOneProactiveVisionFrame().then(() => {
  console.log(JSON.stringify(results));
});
"""


def _frame_script(*, source_id: str, stream: str, native: str = _NATIVE_OK) -> str:
    screen_src = _screen_src()
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    return (
        _FRAME_TMPL
        .replace("__SOURCE_ID__", source_id)
        .replace("__STREAM__", stream)
        .replace("__NATIVE__", native)
        .replace("__DETECT__", _fn(screen_src, "detectScreenshotCaptureType"))
        .replace("__BUILD__", _fn(screen_src, "buildStreamDataMessage"))
        .replace("__FRAME__", _fn(proactive_src, "sendOneProactiveVisionFrame"))
    )


@pytest.mark.unit
def test_backend_fallback_frame_carries_avatar_position():
    """Native capture failed but a window:* source lingers, so the grab is the whole desktop.

    The avatar is necessarily in that image, and the leftover source id must not
    demote it to "window capture, do not annotate".
    """
    out = _run(_frame_script(
        source_id="'window:9'", stream="null", native=_NATIVE_FAIL,
    ))
    assert len(out["sent"]) == 1
    msg = json.loads(out["sent"][0])
    assert msg["input_type"] == "screen"
    assert msg["data"].endswith("BACKEND")
    assert msg["avatar_position"] is not None


@pytest.mark.unit
def test_stream_frame_carries_avatar_position():
    out = _run(_frame_script(source_id="null", stream="({})"))
    msg = json.loads(out["sent"][0])
    assert msg["data"].endswith("STREAM")
    assert msg["avatar_position"] is not None


@pytest.mark.unit
def test_native_frame_is_converted_to_jpeg_before_sending():
    """Backend screen-data validation hard-rejects anything that is not JPEG."""
    out = _run(_frame_script(source_id="'screen:0:0'", stream="null"))
    msg = json.loads(out["sent"][0])
    assert msg["data"].startswith("data:image/jpeg;base64,")
    assert msg["data"].endswith("NATIVE")
    assert msg["avatar_position"] is not None


@pytest.mark.unit
def test_window_source_frame_is_not_annotated():
    """A genuine window capture has no avatar in it; annotating would be a lie."""
    out = _run(_frame_script(source_id="'window:9'", stream="null"))
    msg = json.loads(out["sent"][0])
    assert msg["data"].endswith("NATIVE")
    assert "avatar_position" not in msg


@pytest.mark.unit
def test_native_frame_keeps_the_source_it_was_captured_from():
    """Switching source mid-capture must not re-label the frame already in hand.

    The frame was grabbed from a window; re-reading the (now changed) selected
    source would call it a full-screen grab and annotate an image that contains
    no avatar at all.
    """
    out = _run(_frame_script(
        source_id="'window:9'", stream="null", native=_NATIVE_OK_THEN_SWITCH,
    ))
    msg = json.loads(out["sent"][0])
    assert msg["data"].endswith("NATIVE")
    # Captured from window:9, so it must stay unannotated despite the switch.
    assert "avatar_position" not in msg


@pytest.mark.unit
def test_display_topology_change_is_picked_up_after_the_cache_ttl():
    """A monitor attached after startup must re-arm the gate, not stay cached forever.

    Only reachable when ``screen.isExtended`` is unavailable, which is exactly
    when the Electron bridge fallback is in use.
    """
    src = _screen_src()
    ttl_ms = int(re.search(r"=\s*(\d+)", _ttl_line(src)).group(1))
    # Pin the value itself. The harness below reads the constant out of the
    # module, so a test driven purely by derived timings would keep passing if
    # the TTL were changed to a minute -- the assertion has to name the number.
    assert ttl_ms == 5000, f"multi-display cache TTL changed to {ttl_ms}ms"

    multi = _multi_display_block(src)
    script = """
const results = {};
const TTL = __TTL__;
let displayCount = 1;
let now = 1000;
Date.now = () => now;
const window = {
  screen: { width: 1920, height: 1080 },   // no isExtended -> bridge fallback
  electronScreen: { getAllDisplays: async () => new Array(displayCount).fill({}) }
};
__MULTI__
const settle = () => new Promise((r) => setImmediate(r));
(async () => {
  isKnownMultiDisplay();
  await settle();
  results.singleDisplay = isKnownMultiDisplay();

  // A second monitor is attached; without a TTL the cached false sticks forever.
  displayCount = 2;

  // Exactly TTL since the last lookup: the check is strictly greater-than, so
  // this must still serve the cached answer.
  now += TTL;
  isKnownMultiDisplay();
  await settle();
  results.atTtl = isKnownMultiDisplay();

  // One millisecond past it: the very first instant a refresh is allowed.
  now += 1;
  isKnownMultiDisplay();
  await settle();
  results.pastTtl = isKnownMultiDisplay();
  console.log(JSON.stringify(results));
})();
""".replace("__MULTI__", multi).replace("__TTL__", str(ttl_ms))
    out = _run(script)
    assert out["singleDisplay"] is False
    # Bounded self-heal window: stale right up to the boundary, fresh just past it.
    assert out["atTtl"] is False
    assert out["pastTtl"] is True


@pytest.mark.unit
def test_failing_display_bridge_is_still_throttled():
    """A rejecting bridge leaves the value unknown; that must not defeat the TTL.

    The gate sits on the screenshot path, which runs about once a second during
    continuous sharing, so an unthrottled retry means one IPC per frame plus
    overlapping in-flight requests.
    """
    src = _screen_src()
    ttl_ms = int(re.search(r"=\s*(\d+)", _ttl_line(src)).group(1))
    multi = _multi_display_block(src)
    script = """
const results = {};
const TTL = __TTL__;
let calls = 0;
let now = 1000;
Date.now = () => now;
const window = {
  screen: { width: 1920, height: 1080 },   // no isExtended -> bridge fallback
  electronScreen: {
    getAllDisplays: async () => { calls += 1; throw new Error('bridge down'); }
  }
};
__MULTI__
const settle = () => new Promise((r) => setImmediate(r));
(async () => {
  // Twenty frames inside one TTL window: the bridge must be asked exactly once.
  for (let i = 0; i < 20; i += 1) {
    isKnownMultiDisplay();
    await settle();
    now += 100;
  }
  results.callsWithinWindow = calls;
  results.stillFalse = isKnownMultiDisplay();

  now += TTL;
  isKnownMultiDisplay();
  await settle();
  results.callsAfterTtl = calls;
  console.log(JSON.stringify(results));
})();
""".replace("__MULTI__", multi).replace("__TTL__", str(ttl_ms))
    out = _run(script)
    assert out["callsWithinWindow"] == 1
    # Unknown stays unknown, and unknown means "behave as before the gate".
    assert out["stillFalse"] is False
    assert out["callsAfterTtl"] == 2
