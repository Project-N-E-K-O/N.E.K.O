from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_BUTTONS = (ROOT / "static" / "app" / "app-buttons.js").read_text(encoding="utf-8")
APP_CROP = (ROOT / "static" / "app" / "app-crop.js").read_text(encoding="utf-8")
CHAT_TEMPLATE = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")


def test_desktop_overlay_precedes_backend_interactive_fallback():
    capture_block = APP_BUTTONS.split(
        "mod.captureScreenshotDataUrl = async function captureScreenshotDataUrl()",
        1,
    )[1].split("window.captureScreenshotDataUrl = mod.captureScreenshotDataUrl", 1)[0]

    assert capture_block.index("captureDesktopRegionDirectly()") < capture_block.index(
        "fetchBackendInteractiveScreenshot()"
    )
    assert "translations: getCropOverlayTranslations()" in APP_BUTTONS


def test_button_and_proxy_results_share_the_compressed_attachment_path():
    assert "mod.enqueueCapturedScreenshotResult = async function" in APP_BUTTONS
    assert "await mod.enqueueCapturedScreenshotResult(result);" in APP_BUTTONS
    assert "window.appButtons.enqueueCapturedScreenshotResult(result)" in CHAT_TEMPLATE


def test_system_overlay_can_make_right_click_exit_immediately():
    assert "opts.rightClickCancelsAll: true" in APP_CROP
    assert "if (rightClickCancelsAll)" in APP_CROP
    assert "rightClickCancelsAll = false;" in APP_CROP


def test_hide_neko_waits_for_the_replacement_image_to_paint_before_restore():
    assert "onRecaptureImageReady" in APP_CROP
    assert "loadImage(newDataUrl, function ()" in APP_CROP
    assert "requestAnimationFrame(function () {" in APP_CROP
    assert "Promise.resolve(onRecaptureImageReady()).catch(function () {});" in APP_CROP


def test_desktop_pin_is_opt_in_and_does_not_enter_chat_attachments():
    assert "opts.allowPin: true" in APP_CROP
    assert "pinBtn.style.display = allowPin ? '' : 'none';" in APP_CROP
    assert "close({ action: 'pin', dataUrl: result });" in APP_CROP
    assert APP_CROP.index("endGrp.appendChild(cancelBtn)") < APP_CROP.index(
        "endGrp.appendChild(pinBtn)"
    ) < APP_CROP.index("endGrp.appendChild(confirmBtn)")
    assert "if (raw.pinned)" in APP_BUTTONS
    assert "if (desktopRegionResult.pinned)" in APP_BUTTONS


def test_successful_desktop_pin_is_not_reported_as_cancelled():
    capture_block = APP_BUTTONS.split(
        "mod.captureScreenshotDataUrl = async function captureScreenshotDataUrl()",
        1,
    )[1].split("window.captureScreenshotDataUrl = mod.captureScreenshotDataUrl", 1)[0]
    pending_block = APP_BUTTONS.split(
        "mod.captureScreenshotToPendingList = async function captureScreenshotToPendingList()",
        1,
    )[1].split("screenshotButton.addEventListener", 1)[0]

    assert "pinned: true" in capture_block
    assert "pinId: desktopRegionResult.pinId || null" in capture_block
    assert pending_block.index("if (result && result.pinned)") < pending_block.index(
        "if (!result)"
    )
    assert pending_block.index("if (result && result.pinned)") < pending_block.index(
        "app.screenshotCancelled"
    )


def test_desktop_pin_failures_never_trigger_a_second_screenshot_fallback():
    unavailable_block = APP_BUTTONS.split(
        "function isDesktopRegionCaptureUnavailable(errorLike)",
        1,
    )[1].split("function normalizeDesktopRegionCaptureResult(raw)", 1)[0]
    direct_capture_block = APP_BUTTONS.split(
        "async function captureDesktopRegionDirectly()",
        1,
    )[1].split("async function recaptureWithoutNeko()", 1)[0]

    assert "message.indexOf('unsupported')" not in unavailable_block
    assert ".toLowerCase().trim()" in unavailable_block
    assert "message === 'unsupported'" in unavailable_block
    assert "terminalError.capability = normalized.capability || null" in direct_capture_block
    assert direct_capture_block.index("if (isDesktopRegionCaptureUnavailable(normalized))") < direct_capture_block.index(
        "throw terminalError"
    )


def test_react_chat_marks_screenshot_capability_ready_after_binding_the_button():
    button_index = APP_BUTTONS.index(
        "screenshotButton.addEventListener('click', mod.captureScreenshotToPendingList);"
    )
    ready_index = APP_BUTTONS.index("window.__NEKO_SCREENSHOT_CAPTURE_READY__ = true;")
    assert button_index < ready_index
