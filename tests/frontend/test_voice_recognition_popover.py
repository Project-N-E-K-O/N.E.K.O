from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
VOICE_POPOVER_GLOBAL_LISTENERS = (
    "document:pointerdown",
    "document:keydown",
    "window:resize",
    "window:scroll",
    "window:voice-input-lifecycle-changed",
    "window:neko:voice-session-started",
    "window:neko:voice-settings-pending-changed",
)


def _voice_popover_sources() -> tuple[str, str]:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    permission_start = source.index("async function ensureMicrophonePermission()")
    permission_end = source.index("// 监听设备变化", permission_start)
    permission_source = source[permission_start:permission_end].strip()

    render_marker = "window.renderFloatingMicList = async function"
    render_start = source.index(render_marker)
    render_end = source.index(
        "/** 轻量级更新：仅更新选中状态 */", render_start
    )
    render_assignment = source[render_start:render_end].strip()
    render_expression = render_assignment.split("=", 1)[1].strip()
    if not render_expression.endswith(";"):
        raise AssertionError("renderFloatingMicList assignment is not terminated")
    return permission_source, render_expression[:-1]


def _install_voice_popover_harness(
    page: Page, *, deferred_permission: bool
) -> None:
    permission_source, render_expression = _voice_popover_sources()
    page.set_content(
        '<div id="live2d-popup-mic" style="display:flex;opacity:1"></div>'
    )

    harness = r"""
(() => {
    const listenerBalance = Object.create(null);
    let failWindowListenerType = null;
    function trackListeners(target, prefix) {
        const originalAdd = target.addEventListener.bind(target);
        const originalRemove = target.removeEventListener.bind(target);
        target.addEventListener = function (type, listener, options) {
            const key = prefix + ':' + type;
            listenerBalance[key] = (listenerBalance[key] || 0) + 1;
            const result = originalAdd(type, listener, options);
            if (prefix === 'window' && type === failWindowListenerType) {
                failWindowListenerType = null;
                throw new Error('forced voice panel setup failure');
            }
            return result;
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
                return new Promise((resolve, reject) => {
                    mediaResolvers.push({ resolve, reject });
                });
            },
            enumerateDevices() {
                return Promise.resolve([
                    { kind: 'audioinput', deviceId: 'test-mic' },
                ]);
            },
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
        voiceSettingsPendingUntilEpoch: null,
        pendingVoiceRouteIndependentAsr: null,
        voiceChatActive: false,
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
    let failMicVolumeVisualization = false;
    function startMicVolumeVisualization() {
        if (failMicVolumeVisualization) {
            throw new Error('forced mic visualization failure');
        }
    }
    function ensureMicPopupScrollbarStyle() {}
    function attachTransientMicPopupScrollbar() { return () => {}; }
    function createScreenShareToggleButton() {
        return document.createElement('button');
    }

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
            while (mediaResolvers.length) {
                mediaResolvers.shift().resolve(stream);
            }
        },
        resolvePermission(index) {
            mediaResolvers.splice(index, 1)[0].resolve(stream);
        },
        rejectPermission(index) {
            mediaResolvers.splice(index, 1)[0].reject(
                new Error('permission rejected')
            );
        },
        failMicVolumeVisualization() {
            failMicVolumeVisualization = true;
        },
        failVoicePanelSetupOn(type) {
            failWindowListenerType = type;
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
def test_overlapping_voice_popover_renders_keep_one_owned_instance(
    page: Page,
) -> None:
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
    assert not result["afterOverlap"]["capturedErrors"]
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
        "window:neko:voice-settings-pending-changed": 1,
    }
    for key, expected in expected_global_listeners.items():
        assert result["afterOverlap"]["listenerBalance"].get(key) == expected
        assert result["listenerBalanceAfterRerender"].get(key) == expected


@pytest.mark.frontend
def test_stale_voice_popover_failure_cannot_clear_new_render(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=True)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            const first = window.renderFloatingMicList(popup);
            const second = window.renderFloatingMicList(popup);
            window.__voicePopoverTest.resolvePermission(1);
            const secondResult = await second;
            const currentMarkup = popup.innerHTML;
            console.warn = () => {
                throw new Error('forced permission failure');
            };
            window.__voicePopoverTest.rejectPermission(0);
            const firstResult = await first;
            return {
                firstResult,
                secondResult,
                markupPreserved: popup.innerHTML === currentMarkup,
                errors: [...window.__voicePopoverTest.capturedErrors],
            };
        }"""
    )

    assert result == {
        "firstResult": False,
        "secondResult": True,
        "markupPreserved": True,
        "errors": [],
    }


@pytest.mark.frontend
def test_current_voice_popover_failure_disposes_owned_portal(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            window.__voicePopoverTest.failMicVolumeVisualization();
            const rendered = await window.renderFloatingMicList(popup);
            return {
                rendered,
                panels: window.__voicePopoverTest.panels(),
                errorText: popup.textContent,
                listenerBalance: {
                    ...window.__voicePopoverTest.listenerBalance,
                },
            };
        }"""
    )

    assert result["rendered"] is True
    assert result["panels"] == 0
    assert result["errorText"] == "microphone.loadFailed"
    for key in VOICE_POPOVER_GLOBAL_LISTENERS:
        assert result["listenerBalance"].get(key) == 0


@pytest.mark.frontend
def test_voice_popover_setup_failure_disposes_registered_listeners(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            window.__voicePopoverTest.failVoicePanelSetupOn(
                'neko:voice-settings-pending-changed'
            );
            const rendered = await window.renderFloatingMicList(popup);
            return {
                rendered,
                panels: window.__voicePopoverTest.panels(),
                errorText: popup.textContent,
                listenerBalance: {
                    ...window.__voicePopoverTest.listenerBalance,
                },
            };
        }"""
    )

    assert result["rendered"] is True
    assert result["panels"] == 0
    assert result["errorText"] == "microphone.loadFailed"
    for key in VOICE_POPOVER_GLOBAL_LISTENERS:
        assert result["listenerBalance"].get(key) == 0


@pytest.mark.frontend
def test_voice_popover_disposes_when_popup_host_is_removed(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            popup.remove();
            await Promise.resolve();
            return {
                panels: window.__voicePopoverTest.panels(),
                listenerBalance: { ...window.__voicePopoverTest.listenerBalance },
            };
        }"""
    )

    assert result["panels"] == 0
    for key in VOICE_POPOVER_GLOBAL_LISTENERS:
        assert result["listenerBalance"].get(key) == 0


@pytest.mark.frontend
def test_voice_popover_toggles_have_accessible_names_and_hints(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            return Array.from(
                window.__voicePopoverTest.panel().querySelectorAll(
                    'input[type="checkbox"]'
                )
            ).map((input) => {
                const labelId = input.getAttribute('aria-labelledby');
                const hintId = input.getAttribute('aria-describedby');
                return {
                    labelId,
                    hintId,
                    labelText: labelId
                        ? document.getElementById(labelId)?.textContent
                        : null,
                    hintText: hintId
                        ? document.getElementById(hintId)?.textContent
                        : null,
                };
            });
        }"""
    )

    assert len(result) == 2
    assert all(item["labelId"] and item["labelText"] for item in result)
    assert all(item["hintId"] and item["hintText"] for item in result)


@pytest.mark.frontend
def test_voice_settings_pending_clears_only_after_target_session(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            const firstPanel = window.__voicePopoverTest.panel();
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
    assert (
        result["listenerBalance"]["window:neko:voice-settings-pending-changed"]
        == 1
    )


@pytest.mark.frontend
def test_voice_popover_keeps_active_route_and_keyboard_access(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            const panel = window.__voicePopoverTest.panel();
            const container = document.querySelector(
                '[aria-controls="' + panel.id + '"]'
            );
            const asrInput = container.querySelector('input[type="checkbox"]');
            const panelInputs = panel.querySelectorAll('input[type="checkbox"]');
            const noiseInput = panelInputs[0];
            const optimizationInput = panelInputs[1];

            window.__voicePopoverTest.state.voiceChatActive = true;
            window.__voicePopoverTest.state.independentAsrActive = true;
            asrInput.checked = false;
            asrInput.dispatchEvent(new Event('change', { bubbles: true }));

            const summary = container.firstElementChild
                .firstElementChild.lastElementChild.textContent;
            container.focus();
            container.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter',
                bubbles: true,
            }));

            return {
                summary,
                noiseDisabled: noiseInput.disabled,
                optimizationDisabled: optimizationInput.disabled,
                panelOpen: container.getAttribute('aria-expanded'),
                focusedNoise: document.activeElement === noiseInput,
            };
        }"""
    )

    assert result == {
        "summary": "microphone.independentAsrSummary",
        "noiseDisabled": False,
        "optimizationDisabled": True,
        "panelOpen": "true",
        "focusedNoise": True,
    }


@pytest.mark.frontend
def test_voice_popover_preserves_cross_window_active_route_across_rerender(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            const state = window.__voicePopoverTest.state;
            await window.renderFloatingMicList(popup);

            // app-settings applies the other window's new preference to S, but
            // the current session remains on the route captured before that
            // preference changed. The shared pending snapshot must survive the
            // popup's owned-disposer rerender.
            state.voiceChatActive = true;
            state.independentAsrActive = true;
            state.pendingVoiceRouteIndependentAsr = true;
            state.voiceSettingsPendingUntilEpoch = 11;
            state.independentAsrEnabled = false;
            await window.renderFloatingMicList(popup);

            const panel = window.__voicePopoverTest.panel();
            const container = document.querySelector(
                '[aria-controls="' + panel.id + '"]'
            );
            return {
                summary: container.firstElementChild
                    .firstElementChild.lastElementChild.textContent,
                status: panel.lastElementChild.textContent,
            };
        }"""
    )

    assert result == {
        "summary": "microphone.independentAsrSummary",
        "status": "microphone.voiceRecognitionSettingsPending",
    }


@pytest.mark.frontend
def test_voice_popover_keyboard_focus_ring_is_visible(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            const panel = window.__voicePopoverTest.panel();
            const container = document.querySelector(
                '[aria-controls="' + panel.id + '"]'
            );
            container.focus();
        }"""
    )

    page.keyboard.press("Enter")

    result = page.evaluate(
        """() => {
            const panel = window.__voicePopoverTest.panel();
            const input = panel.querySelector('input[type="checkbox"]');
            const slider = input.nextElementSibling;
            return {
                focused: document.activeElement === input,
                boxShadow: getComputedStyle(slider).boxShadow,
            };
        }"""
    )
    assert result["focused"] is True
    assert result["boxShadow"] != "none"


@pytest.mark.frontend
def test_shared_audio_capture_script_is_safe_on_web_routes(
    page: Page, running_server: str
) -> None:
    audio_capture_console_errors: list[str] = []
    page_errors: list[str] = []
    script_responses: list[tuple[str, int]] = []

    page.on(
        "console",
        lambda message: audio_capture_console_errors.append(
            f"{message.text} @ {message.location}"
        )
        if (
            message.type == "error"
            and "/static/app/app-audio-capture.js"
            in message.location.get("url", "")
        )
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
    root_audio_capture_console_errors: list[str] = []
    root_page_errors: list[str] = []
    root_script_responses: list[tuple[str, int]] = []
    root_page.on(
        "console",
        lambda message: root_audio_capture_console_errors.append(
            f"{message.text} @ {message.location}"
        )
        if (
            message.type == "error"
            and "/static/app/app-audio-capture.js"
            in message.location.get("url", "")
        )
        else None,
    )
    root_page.on(
        "pageerror",
        lambda error: root_page_errors.append(str(error)),
    )
    root_page.on(
        "response",
        lambda response: root_script_responses.append(
            (response.url, response.status)
        )
        if "/static/app/app-audio-capture.js" in response.url
        else None,
    )
    root_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    root_page.wait_for_function("typeof window.renderFloatingMicList === 'function'")
    assert any(status == 200 for _, status in root_script_responses)
    assert not root_page_errors, root_page_errors
    assert not root_audio_capture_console_errors, "\n".join(
        root_audio_capture_console_errors
    )
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
    assert not audio_capture_console_errors, "\n".join(
        audio_capture_console_errors
    )
