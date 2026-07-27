import json
from pathlib import Path


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


def test_native_frame_capture_is_used_by_stream_and_screenshot_paths() -> None:
    screen = read_text("static/app/app-screen.js")
    buttons = read_text("static/app/app-buttons.js")
    proactive = read_text("static/app/app-proactive.js")
    websocket = read_text("static/app/app-websocket.js")
    avatar_popup = read_text("static/avatar/avatar-ui-popup.js")

    assert "nativeFrameCapture" in screen
    assert "startNativeScreenStreaming" in screen
    assert "desktopProvider.captureSourceAsDataUrl" in buttons
    assert "desktopProvider.captureSourceAsDataUrl" in proactive
    assert "resolveDesktopCaptureProvider()" in websocket
    assert "window.electronDesktopCapturer" not in websocket

    for consumer in (screen, buttons, proactive, avatar_popup):
        assert "window.tauriDesktopCapturer" not in consumer
        assert "window.electronDesktopCapturer" not in consumer


def test_native_frame_stream_lifecycle_preserves_source_and_cancels_stale_frames() -> None:
    screen = read_text("static/app/app-screen.js")
    native_stream = screen[
        screen.index("async function startNativeScreenStreaming"):
        screen.index("// ======================== getMobileCameraStream")
    ]

    assert "activeNativeCaptureSourceId = sourceId" in native_stream
    assert native_stream.count("if (!isCurrentNativeCapture()) return false;") >= 3
    assert "var captureSocket = S.socket" in native_stream
    assert "captureSocket === S.socket" in native_stream
    assert "captureSocket.send(JSON.stringify(" in native_stream
    assert "buildStreamDataMessage(result.dataUrl, inputType, sourceId)" in native_stream
    assert "(S.screenCaptureStream || activeNativeCaptureSourceId)" in screen


def test_capture_failure_copy_exists_in_all_supported_locales() -> None:
    for locale in LOCALES:
        payload = json.loads(read_text(f"static/locales/{locale}.json"))
        screen_source = payload["app"]["screenSource"]

        assert screen_source["notAvailable"]
        assert screen_source["captureFailed"]
