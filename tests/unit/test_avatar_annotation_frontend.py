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
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"


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


@pytest.mark.parametrize("mode", ["cached", "native", "stream", "retry"])
def test_proactive_chat_discards_frames_when_remembered_identity_changes(mode: str):
    """Every successful async capture route must reject a superseded window frame."""
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    script = """
const mode = '__MODE__';
const results = {};
let current = true;
function makeStream(label) {
  const track = { readyState: 'live', stop() {} };
  return {
    label,
    active: true,
    getVideoTracks: () => [track],
    getTracks: () => [track]
  };
}
const cachedStream = makeStream('cached');
const firstStream = makeStream('first');
const retryStream = makeStream('retry');
const S = {
  selectedScreenSourceId: 'window:old',
  screenCaptureStream: mode === 'cached' ? cachedStream : null,
  screenCaptureStreamLastUsed: null
};
const window = {
  prepareRememberedWindowCapture: async () => ({
    required: true,
    allowed: true,
    isCurrent: () => current
  }),
  detectScreenshotCaptureType: () => null,
  captureDesktopSourceWithTimeout: async () => {
    if (mode === 'native') {
      current = false;
      return { success: true, dataUrl: 'data:image/png;base64,OLD' };
    }
    return { success: false, error: 'not used' };
  },
  maybeClearSourceOnNotFound: () => {},
  scheduleScreenCaptureIdleCheck: () => {}
};
const getDesktopProvider = () => mode === 'native'
  ? { captureSourceAsDataUrl() {} }
  : {};
let acquireCount = 0;
const acquireOrReuseCachedStream = async () => {
  acquireCount += 1;
  if (mode === 'stream') return firstStream;
  if (mode === 'retry') return acquireCount === 1 ? firstStream : retryStream;
  return null;
};
let frameCount = 0;
const captureFrameFromStream = async () => {
  frameCount += 1;
  if (mode === 'retry' && frameCount === 1) return null;
  current = false;
  return { dataUrl: 'data:image/jpeg;base64,OLD' };
};
const fetchBackendScreenshot = async () => ({
  dataUrl: 'data:image/jpeg;base64,BACKEND'
});
__RESOLVE__
__HELPER__
captureProactiveChatScreenshotWithSource().then((shot) => {
  results.data = shot && shot.dataUrl;
  results.via = shot && shot.via;
  console.log(JSON.stringify(results));
});
"""
    script = (
        script
        .replace("__MODE__", mode)
        .replace("__RESOLVE__", _fn(proactive_src, "resolveCaptureTypeFor"))
        .replace(
            "__HELPER__",
            _fn(proactive_src, "captureProactiveChatScreenshotWithSource"),
        )
    )

    out = _run(script)

    assert out.get("data") is None
    assert out.get("via") is None


_SCREENSHOT_ENTRY_TMPL = """
const results = { calls: [] };
class MediaStream {}
const S = {
  selectedScreenSourceId: __SOURCE_ID__,
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: null
};
const U = { isMobile: () => false };
const window = {
  t: () => 'ok',
  appCrop: null,
  fetchBackendInteractiveScreenshot: async () => null,
  prepareRememberedWindowCapture: async () => __PREPARE__,
  acquireOrReuseCachedStream: async () => {
    results.calls.push('coordinate-remembered-window');
    S.selectedScreenSourceId = 'window:correct';
    return null;
  },
  fetchBackendScreenshot: async () => {
    results.calls.push('backend-desktop');
    return { dataUrl: 'data:image/jpeg;base64,BACKEND' };
  }
};
const getDesktopProvider = () => ({});
const setScreenshotCaptureSessionActive = () => {};
const captureDesktopRegionDirectly = async () => __DIRECT__;
const recaptureWithoutNeko = async () => null;
let _captureScreenshotDataUrlBusy = false;
__CAPTURE__
captureScreenshotDataUrl().then((shot) => {
  results.data = shot && shot.dataUrl;
  results.selected = S.selectedScreenSourceId;
  console.log(JSON.stringify(results));
}).catch((error) => {
  results.error = error && error.message;
  results.selected = S.selectedScreenSourceId;
  console.log(JSON.stringify(results));
});
"""


def _screenshot_entry_script(*, source_id: str, direct: str, prepare: str) -> str:
    buttons_src = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    return (
        _SCREENSHOT_ENTRY_TMPL
        .replace("__SOURCE_ID__", source_id)
        .replace("__DIRECT__", direct)
        .replace("__PREPARE__", prepare)
        .replace("__CAPTURE__", _fn(buttons_src, "captureScreenshotDataUrl"))
    )


@pytest.mark.unit
def test_manual_screenshot_coordinates_remembered_title_before_direct_frame():
    out = _run(_screenshot_entry_script(
        source_id="'window:reused'",
        prepare="(results.calls.push('coordinate-remembered-window'),"
        " S.selectedScreenSourceId = 'window:correct',"
        " { required: true, allowed: true })",
        direct="(results.calls.push(`direct:${S.selectedScreenSourceId}`), {"
        " dataUrl: 'data:image/png;base64,WRONG', originalDataUrl:"
        " 'data:image/png;base64,WRONG' })",
    ))

    assert out == {
        "calls": ["coordinate-remembered-window", "direct:window:correct"],
        "data": "data:image/png;base64,WRONG",
        "selected": "window:correct",
    }


@pytest.mark.unit
def test_manual_screenshot_does_not_widen_blocked_capture_to_backend_desktop():
    out = _run(_screenshot_entry_script(
        source_id="null",
        direct="null",
        prepare="(results.calls.push('coordinate-remembered-window'),"
        " { required: true, allowed: false })",
    ))

    assert out["calls"] == ["coordinate-remembered-window"]
    assert out.get("data") is None
    assert out["selected"] is None


_REMEMBERED_SCREENSHOT_RACE_TMPL = """
const results = { calls: [], toasts: [] };
class MediaStream {}
let current = true;
const S = {
  selectedScreenSourceId: 'window:old',
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: null
};
const U = { isMobile: () => false };
const window = {
  t: (key) => key,
  showStatusToast: (message) => results.toasts.push(message),
  appCrop: null,
  prepareRememberedWindowCapture: async () => ({
    required: true,
    allowed: __ALLOWED__,
    sourceId: S.selectedScreenSourceId,
    isCurrent: () => current
  }),
  fetchBackendInteractiveScreenshot: async () => {
    results.calls.push('interactive-desktop');
    return __INTERACTIVE__;
  },
  captureDesktopSourceWithTimeout: async () => {
    results.calls.push('direct-window');
    return __DIRECT__;
  },
  maybeClearSourceOnNotFound: () => {},
  acquireOrReuseCachedStream: async () => {
    results.calls.push('stream');
    return __STREAM__;
  },
  captureFrameFromStream: async () => {
    results.calls.push('stream-frame');
    return __FRAME__;
  },
  fetchBackendScreenshot: async () => {
    results.calls.push('backend-desktop');
    return { dataUrl: 'data:image/jpeg;base64,BACKEND' };
  },
  detectScreenshotCaptureType: () => null,
  scheduleScreenCaptureIdleCheck: () => {}
};
const getDesktopProvider = () => __PROVIDER__;
const setScreenshotCaptureSessionActive = () => {};
const captureDesktopRegionDirectly = async () => {
  results.calls.push('region');
  return __REGION__;
};
const recaptureWithoutNeko = async () => null;
let _captureScreenshotDataUrlBusy = false;
__CAPTURE__
const mod = { captureScreenshotDataUrl };
mod.enqueueCapturedScreenshotResult = async () => {
  results.calls.push('enqueue');
};
const screenshotButton = { disabled: false };
const isHomeTutorialInteractionLocked = () => false;
const showHomeTutorialLockedToast = () => {};
const refreshHomeTutorialLockedElement = () => {};
__OUTER__
__RUN__
"""


def _remembered_screenshot_race_script(
    *,
    allowed: str = "true",
    provider: str = "({ captureSourceAsDataUrl() {} })",
    region: str = "null",
    interactive: str = "null",
    direct: str = "null",
    stream: str = "null",
    frame: str = "null",
    run_outer: bool = False,
) -> str:
    buttons_src = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    if run_outer:
        outer = _fn(buttons_src, "captureScreenshotToPendingList")
        run = """mod.captureScreenshotToPendingList = captureScreenshotToPendingList;
mod.captureScreenshotToPendingList().then(() => {
  console.log(JSON.stringify(results));
}).catch((error) => {
  results.error = error && error.message;
  console.log(JSON.stringify(results));
});"""
    else:
        outer = ""
        run = """captureScreenshotDataUrl().then((shot) => {
  results.data = shot && shot.dataUrl;
  results.unavailable = !!(shot && shot.rememberedWindowUnavailable);
  console.log(JSON.stringify(results));
}).catch((error) => {
  results.error = error && error.message;
  console.log(JSON.stringify(results));
});"""
    return (
        _REMEMBERED_SCREENSHOT_RACE_TMPL
        .replace("__ALLOWED__", allowed)
        .replace("__PROVIDER__", provider)
        .replace("__REGION__", region)
        .replace("__INTERACTIVE__", interactive)
        .replace("__DIRECT__", direct)
        .replace("__STREAM__", stream)
        .replace("__FRAME__", frame)
        .replace("__CAPTURE__", _fn(buttons_src, "captureScreenshotDataUrl"))
        .replace("__OUTER__", outer)
        .replace("__RUN__", run)
    )


@pytest.mark.unit
def test_manual_screenshot_discards_direct_frame_after_remembered_source_change():
    out = _run(_remembered_screenshot_race_script(
        direct="(current = false, S.selectedScreenSourceId = 'window:new', {"
        " success: true, dataUrl: 'data:image/png;base64,OLD' })",
    ))

    assert out.get("data") is None
    assert out["unavailable"] is True


@pytest.mark.unit
def test_manual_screenshot_discards_stream_frame_after_remembered_source_change():
    out = _run(_remembered_screenshot_race_script(
        provider="({})",
        stream="({ getTracks: () => [] })",
        frame="(current = false, S.selectedScreenSourceId = 'window:new', {"
        " dataUrl: 'data:image/jpeg;base64,OLD', width: 640, height: 360 })",
    ))

    assert out.get("data") is None
    assert out["unavailable"] is True


@pytest.mark.unit
def test_manual_screenshot_does_not_stop_shared_stream_when_identity_expires_after_acquire():
    out = _run(_remembered_screenshot_race_script(
        provider="({})",
        stream="(S.screenCaptureStream = new MediaStream(),"
        " S.screenCaptureStream.getTracks = () => [{"
        " stop: () => { results.sharedStopped = true; }"
        " }], current = false, S.screenCaptureStream)",
    ))

    assert out.get("data") is None
    assert out["unavailable"] is True
    assert out.get("sharedStopped") is not True


@pytest.mark.unit
def test_manual_screenshot_discards_desktop_region_after_remembered_source_change():
    out = _run(_remembered_screenshot_race_script(
        region="(current = false, S.selectedScreenSourceId = 'window:new', {"
        " dataUrl: 'data:image/png;base64,OLD',"
        " originalDataUrl: 'data:image/png;base64,OLD' })",
    ))

    assert out.get("data") is None
    assert out["unavailable"] is True


@pytest.mark.unit
def test_manual_screenshot_skips_interactive_desktop_for_remembered_window():
    out = _run(_remembered_screenshot_race_script(
        provider="({})",
        interactive="({ dataUrl: 'data:image/png;base64,DESKTOP' })",
    ))

    assert "interactive-desktop" not in out["calls"]
    assert out.get("data") is None
    assert out["unavailable"] is True


@pytest.mark.unit
def test_manual_screenshot_reports_remembered_rejection_instead_of_cancellation():
    out = _run(_remembered_screenshot_race_script(
        allowed="false",
        provider="({})",
        run_outer=True,
    ))

    assert out["calls"] == []
    assert out["toasts"] == [
        "app.capturing",
        "app.screenSource.rememberedWindowUnavailable",
    ]


@pytest.mark.unit
def test_hide_neko_recapture_does_not_widen_remembered_window_to_backend_desktop():
    buttons_src = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    script = """
const results = { calls: [] };
class MediaStream {}
const S = {
  selectedScreenSourceId: 'window:old',
  screenCaptureStream: null
};
const window = {
  captureDesktopSourceWithTimeout: async () => {
    results.calls.push('direct-window');
    return null;
  },
  acquireOrReuseCachedStream: async () => {
    results.calls.push('stream');
    return null;
  },
  captureFrameFromStream: async () => null,
  fetchBackendScreenshot: async () => {
    results.calls.push('backend-desktop');
    return { dataUrl: 'data:image/jpeg;base64,BACKEND' };
  },
  maybeClearSourceOnNotFound: () => {},
  showStatusToast: () => {},
  t: (key) => key
};
const getDesktopProvider = () => ({ captureSourceAsDataUrl() {} });
const hideNekoUI = () => null;
const restoreNekoUI = () => {};
__RECAPTURE__
recaptureWithoutNeko({
  required: true,
  allowed: true,
  isCurrent: () => true
}).then((data) => {
  results.data = data;
  console.log(JSON.stringify(results));
});
""".replace("__RECAPTURE__", _fn(buttons_src, "recaptureWithoutNeko"))

    out = _run(script)

    assert out.get("data") is None
    assert "backend-desktop" not in out["calls"]


@pytest.mark.unit
def test_title_remap_restarts_active_native_sender_on_the_new_source():
    screen_src = _screen_src()
    script = """
const results = { captured: [], sent: [] };
const WebSocket = { OPEN: 1 };
const C = { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 };
const S = {
  selectedScreenSourceId: 'window:old',
  socket: {
    readyState: 1,
    send(payload) { results.sent.push(JSON.parse(payload).source_id); }
  },
  videoSenderInterval: null
};
let nativeCaptureGeneration = 0;
let activeNativeCaptureSourceId = null;
let scheduled = null;
const setTimeout = (callback) => { scheduled = callback; return callback; };
const clearTimeout = () => {};
const clearInterval = () => {};
const provider = {
  nativeFrameCapture: true,
  captureSourceAsDataUrl() {}
};
const window = {
  captureDesktopSourceWithTimeout: async (_provider, _method, sourceId) => {
    results.captured.push(sourceId);
    return { success: true, dataUrl: 'data:image/jpeg;base64,FRAME' };
  },
  showStatusToast: () => {}
};
const localStorage = { setItem() {}, removeItem() {} };
const normalizeScreenSourceTitle = (value) => String(value || '').trim();
const isScreenSourceTitleMatchEnabled = () => true;
const readRememberedWindowTitle = () => 'Editor';
const markScreenSourceSelectionChanged = () => {};
const pushSelectedSourceToMain = () => {};
const updateScreenSourceListSelection = () => {};
const storeRememberedWindowTitle = () => {};
const clearRememberedWindowTitle = () => {};
const resolveDesktopCaptureProvider = () => provider;
const isNativeFrameProvider = (candidate) => !!(
  candidate && candidate.nativeFrameCapture
  && typeof candidate.captureSourceAsDataUrl === 'function'
);
const stopLiveVisionStreamIfBlocked = async () => false;
const canSendLiveVisionStreamFrame = () => true;
const normalizeNativeCaptureDataUrlForStream = async (dataUrl) => dataUrl;
const buildStreamDataMessage = (_dataUrl, _inputType, sourceId) => ({ source_id: sourceId });
const safeT = (_key, fallback) => fallback;
const stopScreenSharing = async () => {};
const resetScreenSharingControls = () => { results.controlsReset = true; };
function stopScreening() {
  nativeCaptureGeneration += 1;
  activeNativeCaptureSourceId = null;
  if (S.videoSenderInterval) clearTimeout(S.videoSenderInterval);
  S.videoSenderInterval = null;
}
function clearSelectedScreenSource() {
  S.selectedScreenSourceId = null;
  markScreenSourceSelectionChanged();
}
__START_NATIVE__
__RESTART_NATIVE__
__RELEASE_CAPTURE__
__IS_CAPTURE_ACTIVE__
__RESTART_CAPTURE__
__STOP_REJECTED_CAPTURE__
__RECONCILE__
(async () => {
  await startNativeScreenStreaming(provider, 'window:old', 'screen');
  reconcileRememberedWindowSource([{ id: 'window:new', name: 'Editor' }]);
  await Promise.resolve();
  await Promise.resolve();
  if (scheduled) await scheduled();
  results.selectedAfterRemap = S.selectedScreenSourceId;
  results.capturedAfterRemap = results.captured.slice();
  reconcileRememberedWindowSource([]);
  if (scheduled) await scheduled();
  results.selectedAfterReject = S.selectedScreenSourceId;
  console.log(JSON.stringify(results));
})();
"""
    script = (
        script
        .replace("__START_NATIVE__", _fn(screen_src, "startNativeScreenStreaming"))
        .replace(
            "__RESTART_NATIVE__",
            _fn(screen_src, "restartActiveNativeCaptureForSourceRemap"),
        )
        .replace(
            "__RELEASE_CAPTURE__",
            _fn(screen_src, "releaseActiveScreenCaptureForSourceChange"),
        )
        .replace(
            "__IS_CAPTURE_ACTIVE__",
            _fn(screen_src, "isScreenSharingActiveForSourceChange"),
        )
        .replace(
            "__RESTART_CAPTURE__",
            _fn(screen_src, "restartActiveCaptureForSourceRemap"),
        )
        .replace(
            "__STOP_REJECTED_CAPTURE__",
            _fn(screen_src, "stopActiveCaptureForRememberedSourceRejection"),
        )
        .replace("__RECONCILE__", _fn(screen_src, "reconcileRememberedWindowSource"))
    )

    out = _run(script)

    assert out["selectedAfterRemap"] == "window:new"
    assert out["capturedAfterRemap"][:2] == ["window:old", "window:new"]
    assert "window:old" not in out["capturedAfterRemap"][1:]
    assert out["selectedAfterReject"] is None
    assert out["captured"] == out["capturedAfterRemap"]


def _run_active_media_stream_reconciliation(
    sources: list[dict[str, str]],
    *,
    play_pending: bool = False,
) -> dict:
    screen_src = _screen_src()
    script = """
const results = { sent: [], stopped: false, intervalCleared: false };
const WebSocket = { OPEN: 1 };
const C = { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 };
let senderTick = null;
let resolvePlay = null;
const playPromise = __PLAY_PROMISE__;
const track = { readyState: 'live', stop() { results.stopped = true; } };
const stream = {
  active: true,
  getVideoTracks: () => [track],
  getTracks: () => [track]
};
const S = {
  selectedScreenSourceId: 'window:old',
  screenCaptureStream: stream,
  screenCaptureStreamLastUsed: null,
  screenCaptureStreamIdleTimer: null,
  socket: { readyState: 1, send(payload) { results.sent.push(JSON.parse(payload)); } },
  videoSenderInterval: null,
  videoTrack: null,
  isRecording: true
};
let nativeCaptureGeneration = 0;
let activeNativeCaptureSourceId = null;
const document = {
  createElement: () => ({
    srcObject: null,
    autoplay: false,
    muted: false,
    videoWidth: 100,
    videoHeight: 100,
    play: () => playPromise
  })
};
const setInterval = (callback) => { senderTick = callback; return callback; };
const clearInterval = () => { results.intervalCleared = true; };
const clearTimeout = () => {};
const setTimeout = (callback) => callback;
const localStorage = { setItem() {}, removeItem() {} };
const window = { showStatusToast: () => {} };
const normalizeScreenSourceTitle = (value) => String(value || '').trim();
const isScreenSourceTitleMatchEnabled = () => true;
const readRememberedWindowTitle = () => 'Editor';
const markScreenSourceSelectionChanged = () => {};
const pushSelectedSourceToMain = () => {};
const updateScreenSourceListSelection = () => {};
const storeRememberedWindowTitle = () => {};
const clearRememberedWindowTitle = () => {};
const resolveDesktopCaptureProvider = () => null;
const isNativeFrameProvider = () => false;
const stopLiveVisionStreamIfBlocked = async () => false;
const captureCanvasFrame = () => ({ dataUrl: 'data:image/jpeg;base64,OLD' });
const buildStreamDataMessage = (dataUrl) => ({ dataUrl });
const scheduleScreenCaptureIdleCheck = () => {};
const safeT = (_key, fallback) => fallback;
const resetScreenSharingControls = () => { results.controlsReset = true; };
const stopButton = () => ({ disabled: false });
const screenButton = () => ({ classList: { contains: () => true } });
const cancelPendingScreenSharingStart = () => false;
const startScreenSharing = async () => { results.restarted = true; };
function stopScreening() {
  nativeCaptureGeneration += 1;
  activeNativeCaptureSourceId = null;
  if (S.videoSenderInterval) clearInterval(S.videoSenderInterval);
  S.videoSenderInterval = null;
}
function clearSelectedScreenSource() {
  S.selectedScreenSourceId = null;
  markScreenSourceSelectionChanged();
}
__START_STREAM__
__RESTART_NATIVE__
__RELEASE_CAPTURE__
__IS_CAPTURE_ACTIVE__
__RESTART_CAPTURE__
__STOP_REJECTED_CAPTURE__
__RECONCILE__
(async () => {
  startScreenVideoStreaming(stream, 'screen');
  __RECONCILE_SEQUENCE__
  await Promise.resolve();
  await Promise.resolve();
  if (senderTick) await senderTick();
  results.selected = S.selectedScreenSourceId;
  console.log(JSON.stringify(results));
})();
"""
    script = (
        script
        .replace("__START_STREAM__", _fn(screen_src, "startScreenVideoStreaming"))
        .replace(
            "__RESTART_NATIVE__",
            _fn(screen_src, "restartActiveNativeCaptureForSourceRemap"),
        )
        .replace(
            "__RELEASE_CAPTURE__",
            _fn(screen_src, "releaseActiveScreenCaptureForSourceChange"),
        )
        .replace(
            "__IS_CAPTURE_ACTIVE__",
            _fn(screen_src, "isScreenSharingActiveForSourceChange"),
        )
        .replace(
            "__RESTART_CAPTURE__",
            _fn(screen_src, "restartActiveCaptureForSourceRemap"),
        )
        .replace(
            "__STOP_REJECTED_CAPTURE__",
            _fn(screen_src, "stopActiveCaptureForRememberedSourceRejection"),
        )
        .replace("__RECONCILE__", _fn(screen_src, "reconcileRememberedWindowSource"))
        .replace("__SOURCES__", json.dumps(sources))
        .replace(
            "__PLAY_PROMISE__",
            (
                "new Promise((resolve) => { resolvePlay = resolve; })"
                if play_pending
                else "Promise.resolve()"
            ),
        )
        .replace(
            "__RECONCILE_SEQUENCE__",
            (
                "reconcileRememberedWindowSource(__SOURCES__); resolvePlay();"
                if play_pending
                else (
                    "await Promise.resolve(); await Promise.resolve(); "
                    "reconcileRememberedWindowSource(__SOURCES__);"
                )
            ).replace("__SOURCES__", json.dumps(sources)),
        )
    )
    return _run(script)


@pytest.mark.unit
def test_title_remap_stops_active_media_stream_before_it_sends_the_old_source():
    out = _run_active_media_stream_reconciliation([
        {"id": "window:new", "name": "Editor"},
    ])

    assert out["selected"] == "window:new"
    assert out["stopped"] is True
    assert out["sent"] == []


@pytest.mark.unit
def test_title_remap_invalidates_media_stream_while_video_play_is_pending():
    out = _run_active_media_stream_reconciliation(
        [{"id": "window:new", "name": "Editor"}],
        play_pending=True,
    )

    assert out["selected"] == "window:new"
    assert out["stopped"] is True
    assert out["sent"] == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sources", "expected_status"),
    [
        ([], "missing"),
        (
            [
                {"id": "window:new-a", "name": "Editor"},
                {"id": "window:new-b", "name": "Editor"},
            ],
            "ambiguous",
        ),
    ],
)
def test_rejected_remembered_window_stops_active_media_stream(
    sources: list[dict[str, str]],
    expected_status: str,
):
    out = _run_active_media_stream_reconciliation(sources)

    assert out["selected"] is None, expected_status
    assert out["stopped"] is True, expected_status
    assert out["sent"] == [], expected_status


@pytest.mark.unit
def test_hide_neko_recapture_restores_hidden_ui_when_identity_expires_during_delay():
    buttons_src = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    script = """
const results = { domHidden: false, satellitesHidden: false, satellitesRestored: false };
let current = true;
class MediaStream {}
const S = { selectedScreenSourceId: 'window:old', screenCaptureStream: null };
const setTimeout = (callback) => { current = false; callback(); };
const window = {
  captureDesktopSourceWithTimeout: async () => null,
  acquireOrReuseCachedStream: async () => null,
  captureFrameFromStream: async () => null,
  fetchBackendScreenshot: async () => null,
  maybeClearSourceOnNotFound: () => {},
  showStatusToast: () => {},
  t: (key) => key
};
const desktopProvider = {
  async hideNekoWindows() {
    results.satellitesHidden = true;
    return { hiddenIds: [7] };
  },
  async restoreNekoWindows(ids) {
    results.satellitesRestored = ids.length === 1 && ids[0] === 7;
    results.satellitesHidden = false;
  }
};
const getDesktopProvider = () => desktopProvider;
const hideNekoUI = () => { results.domHidden = true; return { saved: true }; };
const restoreNekoUI = () => { results.domHidden = false; };
__RECAPTURE__
recaptureWithoutNeko({
  required: true,
  allowed: true,
  isCurrent: () => current
}).then((data) => {
  results.data = data;
  console.log(JSON.stringify(results));
});
""".replace("__RECAPTURE__", _fn(buttons_src, "recaptureWithoutNeko"))

    out = _run(script)

    assert out.get("data") is None
    assert out["domHidden"] is False
    assert out["satellitesHidden"] is False
    assert out["satellitesRestored"] is True


_PROACTIVE_REMEMBERED_TMPL = """
const results = { calls: [] };
const S = {
  selectedScreenSourceId: __SOURCE_ID__,
  screenCaptureStream: null,
  screenCaptureStreamLastUsed: null
};
const window = {
  detectScreenshotCaptureType: () => null,
  captureDesktopSourceWithTimeout: async (_provider, _method, sourceId) => {
    results.calls.push(`direct:${sourceId}`);
    return __NATIVE__;
  },
  maybeClearSourceOnNotFound: () => {},
  prepareRememberedWindowCapture: async () => __PREPARE__
};
const getDesktopProvider = () => __PROVIDER__;
const acquireOrReuseCachedStream = async () => {
  results.calls.push('coordinate-remembered-window');
  S.selectedScreenSourceId = 'window:correct';
  return null;
};
const captureFrameFromStream = async () => null;
const fetchBackendScreenshot = async () => {
  results.calls.push('backend-desktop');
  return { dataUrl: 'data:image/jpeg;base64,BACKEND' };
};
__RESOLVE__
__HELPER__
captureProactiveChatScreenshotWithSource().then((shot) => {
  results.data = shot && shot.dataUrl;
  results.via = shot && shot.via;
  results.selected = S.selectedScreenSourceId;
  console.log(JSON.stringify(results));
});
"""


def _proactive_remembered_script(
    *, source_id: str, provider: str, native: str, prepare: str
) -> str:
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    return (
        _PROACTIVE_REMEMBERED_TMPL
        .replace("__SOURCE_ID__", source_id)
        .replace("__PROVIDER__", provider)
        .replace("__NATIVE__", native)
        .replace("__PREPARE__", prepare)
        .replace("__RESOLVE__", _fn(proactive_src, "resolveCaptureTypeFor"))
        .replace(
            "__HELPER__",
            _fn(proactive_src, "captureProactiveChatScreenshotWithSource"),
        )
    )


@pytest.mark.unit
def test_proactive_screenshot_coordinates_remembered_title_before_direct_frame():
    out = _run(_proactive_remembered_script(
        source_id="'window:reused'",
        provider="({ captureSourceAsDataUrl() {} })",
        native="({ success: true, dataUrl: 'data:image/png;base64,WRONG' })",
        prepare="(results.calls.push('coordinate-remembered-window'),"
        " S.selectedScreenSourceId = 'window:correct',"
        " { required: true, allowed: true })",
    ))

    assert out == {
        "calls": ["coordinate-remembered-window", "direct:window:correct"],
        "data": "data:image/png;base64,WRONG",
        "via": "native",
        "selected": "window:correct",
    }


@pytest.mark.unit
def test_proactive_screenshot_does_not_widen_blocked_capture_to_backend_desktop():
    out = _run(_proactive_remembered_script(
        source_id="null",
        provider="({})",
        native="null",
        prepare="(results.calls.push('coordinate-remembered-window'),"
        " { required: true, allowed: false })",
    ))

    assert out == {
        "calls": ["coordinate-remembered-window"],
        "data": None,
        "via": None,
        "selected": None,
    }


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
  prepareRememberedWindowCapture: async () => __PREPARE__,
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


def _frame_script(
    *,
    source_id: str,
    stream: str,
    native: str = _NATIVE_OK,
    remembered_capture: str = (
        "({ required: false, allowed: true, isCurrent: () => true })"
    ),
) -> str:
    screen_src = _screen_src()
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    return (
        _FRAME_TMPL
        .replace("__SOURCE_ID__", source_id)
        .replace("__STREAM__", stream)
        .replace("__NATIVE__", native)
        .replace("__PREPARE__", remembered_capture)
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
def test_speech_time_frame_does_not_widen_remembered_window_to_backend_desktop():
    out = _run(
        _frame_script(
            source_id="'window:9'",
            stream="null",
            native=_NATIVE_FAIL,
            remembered_capture=(
                "({ required: true, allowed: true, isCurrent: () => true })"
            ),
        )
    )

    assert out["sent"] == []


@pytest.mark.unit
def test_proactive_vision_enable_does_not_accept_backend_for_remembered_window():
    proactive_src = APP_PROACTIVE_PATH.read_text(encoding="utf-8")
    script = """
const results = { backendCalls: 0, streamCalls: 0 };
const window = {
  prepareRememberedWindowCapture: async () => ({
    required: true,
    allowed: true,
    isCurrent: () => true,
  }),
};
const fetchBackendScreenshot = async () => {
  results.backendCalls += 1;
  return { dataUrl: 'data:image/jpeg;base64,BACKEND' };
};
const acquireOrReuseCachedStream = async () => {
  results.streamCalls += 1;
  return null;
};
__ACQUIRE__
(async () => {
  results.acquired = await acquireProactiveVisionStream();
  console.log(JSON.stringify(results));
})();
""".replace(
        "__ACQUIRE__",
        _fn(proactive_src, "acquireProactiveVisionStream"),
    )

    assert _run(script) == {
        "backendCalls": 0,
        "streamCalls": 1,
        "acquired": False,
    }


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
