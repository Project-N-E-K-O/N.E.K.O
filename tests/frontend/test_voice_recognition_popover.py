from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"


def _voice_popover_sources() -> tuple[str, str]:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    permission_start = source.index("async function ensureMicrophonePermission()")
    permission_end = source.index("// 监听设备变化", permission_start)
    permission_source = source[permission_start:permission_end].strip()

    render_marker = "window.renderFloatingMicList = async function"
    render_start = source.index(render_marker)
    render_end = source.index("/** 轻量级更新：仅更新选中状态 */", render_start)
    render_assignment = source[render_start:render_end].strip()
    render_expression = render_assignment.split("=", 1)[1].strip()
    if not render_expression.endswith(";"):
        raise AssertionError("renderFloatingMicList assignment is not terminated")
    return permission_source, render_expression[:-1]


def _install_voice_popover_harness(page: Page, *, deferred_permission: bool) -> None:
    permission_source, render_expression = _voice_popover_sources()
    page.set_content(
        '<div id="live2d-popup-mic" style="display:flex;opacity:1"></div>'
    )

    harness = r"""
(() => {
    const listenerBalance = Object.create(null);
    function trackListeners(target, prefix) {
        const originalAdd = target.addEventListener.bind(target);
        const originalRemove = target.removeEventListener.bind(target);
        target.addEventListener = function (type, listener, options) {
            const key = prefix + ':' + type;
            listenerBalance[key] = (listenerBalance[key] || 0) + 1;
            return originalAdd(type, listener, options);
        };
        target.removeEventListener = function (type, listener, options) {
            const key = prefix + ':' + type;
            listenerBalance[key] = (listenerBalance[key] || 0) - 1;
            return originalRemove(type, listener, options);
        };
    }
    trackListeners(document, 'document');
    trackListeners(window, 'window');
    const capturedErrors = [];
    const originalConsoleError = console.error.bind(console);
    console.error = (...args) => {
        capturedErrors.push(args.map((value) => String(value)).join(' '));
        originalConsoleError(...args);
    };

    const mediaResolvers = [];
    const stream = { getTracks: () => [{ stop() {} }] };
    Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
            getUserMedia() {
                if (!__DEFERRED_PERMISSION__) return Promise.resolve(stream);
                return new Promise((resolve) => mediaResolvers.push(resolve));
            },
            enumerateDevices() { return Promise.resolve([]); },
            addEventListener() {},
        },
    });

    const S = {
        speakerVolume: 100,
        speakerGainNode: null,
        spatialAudioEnabled: true,
        independentAsrEnabled: true,
        independentAsrActive: true,
        independentAsrProvider: 'qwen',
        voiceInputResourceOptimizationEnabled: true,
        voiceInputLifecycleState: 'active',
        voiceSessionStartEpoch: 10,
        noiseReductionEnabled: true,
        microphoneGainDb: 0,
        micGainNode: null,
        selectedMicrophoneId: null,
    };
    const C = {
        DEFAULT_SPEAKER_VOLUME: 100,
        MIN_MIC_GAIN_DB: -5,
        MAX_MIC_GAIN_DB: 25,
    };
    window.appState = S;
    window.appConst = C;
    window.appUtils = {
        dbToLinear: (value) => value,
        valueToKneeTrack: (value) => value,
        kneeTrackToValue: (value) => value,
    };
    window.appSpatialAudio = {
        getEnabled: () => S.spatialAudioEnabled,
        setEnabled: (enabled) => { S.spatialAudioEnabled = enabled; },
    };
    window.appSettings = { saveSettings: () => { window.__saveCalls += 1; } };
    window.__saveCalls = 0;
    window.t = (key) => key;

    function formatGainDisplay(value) { return String(value); }
    function saveSpeakerVolumeSetting() {}
    function saveNoiseReductionSetting() {}
    function saveMicGainSetting() {}
    async function selectMicrophone() {}
    function startMicVolumeVisualization() {}

    let micPermissionGranted = false;
    let cachedMicDevices = null;
    let disposeVoiceRecognitionPopover = null;
    let voiceRecognitionPopoverRenderGeneration = 0;

    __PERMISSION_SOURCE__
    window.renderFloatingMicList = __RENDER_EXPRESSION__;

    window.__voicePopoverTest = {
        state: S,
        capturedErrors,
        listenerBalance,
        resolvePermissions() {
            while (mediaResolvers.length) mediaResolvers.shift()(stream);
        },
        pendingPermissions: () => mediaResolvers.length,
        popup: () => document.getElementById('live2d-popup-mic'),
        panel: () => document.querySelector('[role="dialog"]'),
        panels: () => document.querySelectorAll('[role="dialog"]').length,
    };
})();
"""
    harness = harness.replace(
        "__DEFERRED_PERMISSION__", "true" if deferred_permission else "false"
    )
    harness = harness.replace("__PERMISSION_SOURCE__", permission_source)
    harness = harness.replace("__RENDER_EXPRESSION__", render_expression)
    page.add_script_tag(content=harness)


@pytest.mark.frontend
def test_overlapping_voice_popover_renders_keep_one_owned_instance(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=True)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            const first = window.renderFloatingMicList(popup);
            const second = window.renderFloatingMicList(popup);
            if (window.__voicePopoverTest.pendingPermissions() !== 2) {
                throw new Error('expected two pending permission requests');
            }
            window.__voicePopoverTest.resolvePermissions();
            const renderResults = await Promise.all([first, second]);
            const afterOverlap = {
                renderResults,
                panels: window.__voicePopoverTest.panels(),
                capturedErrors: [...window.__voicePopoverTest.capturedErrors],
                listenerBalance: { ...window.__voicePopoverTest.listenerBalance },
            };
            const third = await window.renderFloatingMicList(popup);
            return {
                afterOverlap,
                third,
                panelsAfterRerender: window.__voicePopoverTest.panels(),
                listenerBalanceAfterRerender: {
                    ...window.__voicePopoverTest.listenerBalance,
                },
            };
        }"""
    )

    assert result["afterOverlap"]["renderResults"] == [False, True]
    assert not result["afterOverlap"]["capturedErrors"], result["afterOverlap"][
        "capturedErrors"
    ]
    assert result["afterOverlap"]["panels"] == 1
    assert result["third"] is True
    assert result["panelsAfterRerender"] == 1

    expected_global_listeners = {
        "document:pointerdown": 1,
        "document:keydown": 1,
        "window:resize": 1,
        "window:scroll": 1,
        "window:voice-input-lifecycle-changed": 1,
        "window:neko:voice-session-started": 1,
    }
    for key, expected in expected_global_listeners.items():
        assert result["afterOverlap"]["listenerBalance"].get(key) == expected
        assert result["listenerBalanceAfterRerender"].get(key) == expected


@pytest.mark.frontend
def test_voice_settings_pending_clears_only_after_target_session(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            const firstPanel = window.__voicePopoverTest.panel();
            if (!firstPanel) {
                throw new Error('voice panel missing: ' + document.body.innerHTML);
            }
            const firstStatus = firstPanel.lastElementChild;
            const optimizationInput = firstPanel.querySelectorAll(
                'input[type="checkbox"]'
            )[1];
            optimizationInput.checked = false;
            optimizationInput.dispatchEvent(new Event('change', { bubbles: true }));
            const pending = firstStatus.textContent;

            window.dispatchEvent(new CustomEvent('voice-input-lifecycle-changed', {
                detail: { state: 'warm_idle' },
            }));
            const afterLifecycleOnly = firstStatus.textContent;

            window.dispatchEvent(new CustomEvent('neko:voice-session-started'));
            const afterCurrentEpochStart = firstStatus.textContent;

            window.__voicePopoverTest.state.voiceSessionStartEpoch = 11;
            window.dispatchEvent(new CustomEvent('neko:voice-session-started'));
            const afterReadySession = firstStatus.textContent;

            optimizationInput.checked = true;
            optimizationInput.dispatchEvent(new Event('change', { bubbles: true }));
            window.__voicePopoverTest.state.voiceInputLifecycleState = 'blocked';
            window.dispatchEvent(new CustomEvent('voice-input-lifecycle-changed', {
                detail: { state: 'blocked' },
            }));
            const afterFailedStart = firstStatus.textContent;

            window.__voicePopoverTest.state.voiceSessionStartEpoch = 12;
            window.dispatchEvent(new CustomEvent('neko:voice-session-started'));
            const afterBlockedSession = firstStatus.textContent;

            const asrInput = document.querySelector(
                '[aria-controls="' + firstPanel.id + '"] input[type="checkbox"]'
            );
            asrInput.checked = false;
            asrInput.dispatchEvent(new Event('change', { bubbles: true }));
            window.__voicePopoverTest.state.voiceSessionStartEpoch = 13;
            window.dispatchEvent(new CustomEvent('neko:voice-session-started'));
            const afterNativeSession = firstStatus.textContent;

            asrInput.checked = true;
            asrInput.dispatchEvent(new Event('change', { bubbles: true }));
            const beforeDispose = firstStatus.textContent;
            await window.renderFloatingMicList(popup);
            const oldStatusAfterDispose = firstStatus.textContent;
            window.__voicePopoverTest.state.voiceSessionStartEpoch = 14;
            window.dispatchEvent(new CustomEvent('neko:voice-session-started'));

            return {
                pending,
                afterLifecycleOnly,
                afterCurrentEpochStart,
                afterReadySession,
                afterFailedStart,
                afterBlockedSession,
                afterNativeSession,
                beforeDispose,
                oldStatusAfterDispose,
                oldStatusAfterEvent: firstStatus.textContent,
                oldPanelConnected: firstPanel.isConnected,
                panels: window.__voicePopoverTest.panels(),
                listenerBalance: { ...window.__voicePopoverTest.listenerBalance },
            };
        }"""
    )

    pending_key = "microphone.voiceRecognitionSettingsPending"
    assert result["pending"] == pending_key
    assert result["afterLifecycleOnly"] == pending_key
    assert result["afterCurrentEpochStart"] == pending_key
    assert result["afterReadySession"] == "microphone.voiceRecognitionStatusReady"
    assert result["afterFailedStart"] == pending_key
    assert result["afterBlockedSession"] == "microphone.voiceRecognitionUnavailable"
    assert result["afterNativeSession"] == "microphone.voiceRecognitionDisabledHint"
    assert result["beforeDispose"] == pending_key
    assert result["oldStatusAfterDispose"] == pending_key
    assert result["oldStatusAfterEvent"] == pending_key
    assert result["oldPanelConnected"] is False
    assert result["panels"] == 1
    assert result["listenerBalance"]["window:neko:voice-session-started"] == 1


@pytest.mark.frontend
def test_shared_audio_capture_script_is_safe_on_web_routes(
    page: Page, running_server: str
) -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []
    script_responses: list[tuple[str, int]] = []

    page.on(
        "console",
        lambda message: console_errors.append(
            f"{message.text} @ {message.location}"
        )
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "response",
        lambda response: script_responses.append((response.url, response.status))
        if "/static/app/app-audio-capture.js" in response.url
        else None,
    )

    root_page = page.context.new_page()
    root_script_responses: list[tuple[str, int]] = []
    root_page.on(
        "response",
        lambda response: root_script_responses.append((response.url, response.status))
        if "/static/app/app-audio-capture.js" in response.url
        else None,
    )
    root_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    root_page.wait_for_function("typeof window.renderFloatingMicList === 'function'")
    assert any(status == 200 for _, status in root_script_responses)
    root_page.close()

    page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.renderFloatingMicList === 'function'")
    page.wait_for_timeout(500)

    assert any(status == 200 for _, status in script_responses)
    assert page.locator(
        "#live2d-popup-mic, #vrm-popup-mic, #mmd-popup-mic"
    ).count() == 0
    assert page.locator('[id$="-voice-recognition-settings"]').count() == 0
    assert not page_errors, page_errors
    assert not console_errors, "\n".join(console_errors)
