import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es")


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_templates_install_desktop_capture_provider_before_consumers() -> None:
    index_html = read_text("templates/index.html")
    chat_html = read_text("templates/chat.html")

    provider_script = "/static/app/desktop-capture-provider.js"
    assert index_html.index(provider_script) < index_html.index(
        "/static/avatar/avatar-ui-popup.js"
    )
    assert chat_html.index(provider_script) < chat_html.index(
        "/static/app/app-screen.js"
    )
    assert index_html.index(provider_script) < index_html.index(
        "/static/app/app-websocket.js"
    )
    assert chat_html.index(provider_script) < chat_html.index(
        "/static/app/app-websocket.js"
    )


def test_provider_prefers_tauri_and_preserves_electron_fallback() -> None:
    provider = read_text("static/app/desktop-capture-provider.js")

    assert "window.tauriDesktopCapturer" in provider
    assert "window.electronDesktopCapturer" in provider
    assert provider.index(
        "if (window.tauriDesktopCapturer)"
    ) < provider.index(
        "if (window.electronDesktopCapturer)"
    )


def test_provider_timeout_executes_electron_bridge_without_changing_its_contract() -> None:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("Node.js is required for the desktop capture provider contract")

    provider_source = json.dumps(read_text("static/app/desktop-capture-provider.js"))
    node_harness = f"""
global.window = {{}};
eval({provider_source});

(async () => {{
  const electronProvider = {{
    shell: 'electron',
    captureSourceAsDataUrl(sourceId, options) {{
      return Promise.resolve({{
        success: true,
        dataUrl: 'data:image/jpeg;base64,electron',
        sourceId,
        shell: this.shell,
        options
      }});
    }}
  }};
  window.electronDesktopCapturer = electronProvider;

  if (window.getDesktopCaptureProvider() !== electronProvider) {{
    throw new Error('Electron fallback provider was not selected');
  }}

  const result = await window.captureDesktopSourceWithTimeout(
    window.getDesktopCaptureProvider(),
    'captureSourceAsDataUrl',
    'screen:electron',
    {{ quality: 80 }}
  );
  if (!result.success
      || result.sourceId !== 'screen:electron'
      || result.shell !== 'electron'
      || result.options.quality !== 80) {{
    throw new Error('Electron capture call contract changed');
  }}

  const tauriProvider = {{ captureSourceAsDataUrl() {{ return Promise.resolve(null); }} }};
  window.tauriDesktopCapturer = tauriProvider;
  if (window.getDesktopCaptureProvider() !== tauriProvider) {{
    throw new Error('Tauri provider was not preferred');
  }}
  delete window.tauriDesktopCapturer;
  if (window.getDesktopCaptureProvider() !== electronProvider) {{
    throw new Error('Electron fallback was not restored');
  }}

  let timeoutCode = null;
  try {{
    await window.captureDesktopSourceWithTimeout(
      {{ captureSourceAsDataUrl() {{ return new Promise(() => {{}}); }} }},
      'captureSourceAsDataUrl',
      'screen:hung',
      undefined,
      5
    );
  }} catch (error) {{
    timeoutCode = error && error.code;
  }}
  if (timeoutCode !== 'DESKTOP_CAPTURE_TIMEOUT') {{
    throw new Error('Hung capture was not bounded by the shared timeout');
  }}
}})().catch((error) => {{
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
            "Desktop capture provider contract failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def test_native_frame_capture_is_used_by_stream_and_screenshot_paths() -> None:
    screen = read_text("static/app/app-screen.js")
    buttons = read_text("static/app/app-buttons.js")
    proactive = read_text("static/app/app-proactive.js")
    websocket = read_text("static/app/app-websocket.js")
    avatar_popup = read_text("static/avatar/avatar-ui-popup.js")

    assert "nativeFrameCapture" in screen
    assert "startNativeScreenStreaming" in screen
    assert "ensureModelVisibleForScreenSharing" in screen
    assert "desktopProvider.captureSourceAsDataUrl" in buttons
    assert "desktopProvider.captureSourceAsDataUrl" in proactive
    assert "resolveDesktopCaptureProvider()" in websocket
    assert "window.electronDesktopCapturer" not in websocket

    for consumer in (screen, buttons, proactive, avatar_popup):
        assert "window.tauriDesktopCapturer" not in consumer
        assert "window.electronDesktopCapturer" not in consumer

    for consumer in (screen, buttons, proactive, websocket):
        assert "window.captureDesktopSourceWithTimeout(" in consumer
        assert "await desktopProvider.captureSourceAsDataUrl(" not in consumer
        assert "await dc.captureSourceAsDataUrl(" not in consumer


def test_native_frame_stream_lifecycle_preserves_source_and_cancels_stale_frames() -> None:
    screen = read_text("static/app/app-screen.js")
    native_stream = screen[
        screen.index("async function startNativeScreenStreaming"):
        screen.index("// ======================== getMobileCameraStream")
    ]
    select_source = screen[
        screen.index("async function selectScreenSource"):
        screen.index("// ======================== updateScreenSourceListSelection")
    ]

    assert "activeNativeCaptureSourceId = sourceId" in native_stream
    assert native_stream.count("if (!isCurrentNativeCapture()) return false;") >= 3
    assert "var captureSocket = S.socket" in native_stream
    assert "captureSocket === S.socket" in native_stream
    assert "captureSocket.send(JSON.stringify(" in native_stream
    assert native_stream.count("await stopScreenSharing(true);") >= 4
    assert "normalizeNativeCaptureDataUrlForStream(result.dataUrl)" in native_stream
    assert "buildStreamDataMessage(streamDataUrl, inputType, sourceId)" in native_stream
    normalize_index = native_stream.index(
        "normalizeNativeCaptureDataUrlForStream(result.dataUrl)"
    )
    current_after_normalize_index = native_stream.index(
        "if (!isCurrentNativeCapture()) return false;",
        normalize_index,
    )
    socket_after_normalize_index = native_stream.index(
        "if (!isCaptureSocketOpen()) {",
        current_after_normalize_index,
    )
    send_index = native_stream.index(
        "captureSocket.send(JSON.stringify(",
        socket_after_normalize_index,
    )
    assert normalize_index < current_after_normalize_index < socket_after_normalize_index < send_index
    assert "window.captureDesktopSourceWithTimeout(" in native_stream
    assert "'captureSourceAsDataUrl'" in native_stream
    assert "data:image/jpeg;base64," in screen
    assert "(S.screenCaptureStream || activeNativeCaptureSourceId)" in screen
    assert "var isNativeCaptureActive = activeNativeCaptureSourceId !== null;" in select_source
    assert (
        "var isScreenSharingActive = isNativeCaptureActive || "
        "!!(stopBtn && !stopBtn.disabled);"
    ) in select_source


def test_capture_consumers_handle_late_bridges_and_native_failures() -> None:
    websocket = read_text("static/app/app-websocket.js")
    proactive = read_text("static/app/app-proactive.js")
    screen = read_text("static/app/app-screen.js")

    assert "reannounceCaptureBridgeWhenReady(_thisSocket, 0)" in websocket
    assert "socket !== S.socket" in websocket
    assert "CAPTURE_BRIDGE_REANNOUNCE_MAX_ATTEMPTS" in websocket
    assert "catch (directError)" in proactive
    assert "原生捕获失败，尝试后端兜底" in proactive
    assert "var proactiveVisionFrameInFlight = false;" in proactive
    assert "if (proactiveVisionFrameInFlight) return;" in proactive
    assert "proactiveVisionFrameInFlight = false;" in proactive
    assert screen.count("!isNativeFrameProvider(desktopProvider)") >= 2
    assert (
        "opts.allowPrompt && !isNativeFrameProvider(desktopProvider)"
    ) in screen
    assert "resetScreenSharingControls();" in screen
    assert "if (stop) stop.disabled = true;" in screen


def test_capture_failure_copy_exists_in_all_supported_locales() -> None:
    for locale in LOCALES:
        payload = json.loads(read_text(f"static/locales/{locale}.json"))
        screen_source = payload["app"]["screenSource"]

        assert screen_source["notAvailable"]
        assert screen_source["captureFailed"]
