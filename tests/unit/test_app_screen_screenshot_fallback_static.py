from pathlib import Path

import pytest


APP_SCREEN_JS = Path(__file__).resolve().parents[2] / "static" / "app" / "app-screen.js"


@pytest.mark.unit
def test_backend_screenshot_remains_a_safe_one_shot_fallback():
    source = APP_SCREEN_JS.read_text(encoding="utf-8")
    fallback = source.split("async function fetchBackendScreenshot()", 1)[1].split(
        "mod.fetchBackendScreenshot = fetchBackendScreenshot;",
        1,
    )[0]

    assert "json.reason" in fallback
    assert "json.error" not in fallback
    assert "e && e.message" not in fallback
    assert "if (json && json.success && json.data)" in fallback
    assert "供截图、主动视觉等一次性取帧场景使用" in source


@pytest.mark.unit
def test_manual_screen_share_never_polls_the_backend_screenshot_endpoint():
    source = APP_SCREEN_JS.read_text(encoding="utf-8")
    start_once = source.split("async function startScreenSharingOnce(attempt)", 1)[1].split(
        "mod.startScreenSharing = startScreenSharing;",
        1,
    )[0]

    assert "fetchBackendScreenshot()" not in start_once
    assert "进入后端 pyautogui 轮询模式" not in start_once
    assert "streamError.name = 'NotReadableError'" in start_once
    assert "用户没有选择的其它窗口" in start_once


@pytest.mark.unit
def test_linux_portal_screen_share_does_not_reenumerate_sources_during_fallbacks():
    source = APP_SCREEN_JS.read_text(encoding="utf-8")
    start_once = source.split("async function startScreenSharingOnce(attempt)", 1)[1].split(
        "mod.startScreenSharing = startScreenSharing;",
        1,
    )[0]
    acquire_once = source.split("async function acquireOrReuseCachedStream(opts)", 1)[1].split(
        "mod.acquireOrReuseCachedStream = acquireOrReuseCachedStream;",
        1,
    )[0]

    assert "sourceEnumerationMayPrompt = desktopSourceEnumerationMayPrompt" in start_once
    assert "(selectedSourceId || hasRememberedWindowTitle)" in start_once
    assert "&& desktopProvider && !sourceEnumerationMayPrompt" in start_once
    assert "if (!sourceEnumerationMayPrompt)" in start_once
    assert "if (!desktopSourceEnumerationMayPrompt(desktopProvider))" in acquire_once
    assert "Linux Portal 每次枚举都可能再次弹系统窗口" in start_once


@pytest.mark.unit
def test_manual_screen_share_resolves_remembered_title_before_capture():
    source = APP_SCREEN_JS.read_text(encoding="utf-8")
    start_once = source.split("async function startScreenSharingOnce(attempt)", 1)[1].split(
        "mod.startScreenSharing = startScreenSharing;",
        1,
    )[0]

    assert "reconcileRememberedWindowSource(currentSources)" in start_once
    assert "thumbnailSize: { width: 0, height: 0 }" in start_once
    assert "rememberedWindowNeedsPicker" in start_once
    assert "if (!sourceStillExists && !rememberedWindowNeedsPicker)" in start_once
    assert "rememberedWindowNeedsSelection = true;" in start_once
    assert "if (rememberedWindowNeedsSelection)" in start_once
    assert "app.screenSource.rememberedWindowUnavailable" in start_once
    assert "停止本次启动并等待用户重新选择" in start_once
