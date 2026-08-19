import json
from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
VOICE_POPOVER_LOCAL_LISTENERS = (
    "document:pointerdown",
    "document:keydown",
    "window:resize",
    "window:scroll",
)
VOICE_POPOVER_GLOBAL_LISTENERS = (
    "window:voice-input-lifecycle-changed",
    "window:neko:voice-session-started",
    "window:neko:voice-settings-pending-changed",
    "window:neko:core-api-capability-changed",
    "window:neko:speaker-device-changed",
)


def _voice_popover_sources() -> tuple[str, str]:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")

    permission_start = source.index("async function enumerateAndCacheMediaDevices()")
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


def _device_change_source() -> str:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")
    start = source.index("async function enumerateAndCacheMediaDevices()")
    end = source.index("/** 为浮动弹出框渲染麦克风列表 */", start)
    return source[start:end].strip()


def _install_voice_popover_harness(
    page: Page, *, deferred_permission: bool
) -> None:
    permission_source, render_expression = _voice_popover_sources()
    page.set_content(
        '<div id="live2d-popup-mic" '
        'style="display:flex;opacity:1;position:fixed;left:20px;top:20px"></div>'
        '<button id="outside-target" '
        'style="position:fixed;left:700px;top:500px;width:80px;height:40px">'
        "outside</button>"
    )

    harness = r"""
(() => {
    const listenerBalance = Object.create(null);
    const trackedListenerKeys = new Set(__TRACKED_LISTENER_KEYS__);
    let failWindowListenerType = null;
    function trackListeners(target, prefix) {
        const originalAdd = target.addEventListener.bind(target);
        const originalRemove = target.removeEventListener.bind(target);
        target.addEventListener = function (type, listener, options) {
            const key = prefix + ':' + type;
            if (trackedListenerKeys.has(key)) {
                listenerBalance[key] = (listenerBalance[key] || 0) + 1;
            }
            const result = originalAdd(type, listener, options);
            if (prefix === 'window' && type === failWindowListenerType) {
                failWindowListenerType = null;
                throw new Error('forced voice panel setup failure');
            }
            return result;
        };
        target.removeEventListener = function (type, listener, options) {
            const key = prefix + ':' + type;
            if (trackedListenerKeys.has(key)) {
                listenerBalance[key] = (listenerBalance[key] || 0) - 1;
            }
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
                    { kind: 'audiooutput', deviceId: 'default', label: 'Default pseudo device' },
                    { kind: 'audiooutput', deviceId: 'communications', label: 'Communications pseudo device' },
                    { kind: 'audiooutput', deviceId: 'speaker-a', label: 'Speaker A' },
                    { kind: 'audiooutput', deviceId: 'speaker-b', label: 'Speaker B' },
                ]);
            },
            addEventListener() {},
        },
    });

    const S = {
        speakerVolume: 100,
        speakerGainNode: null,
        selectedSpeakerId: 'default',
        effectiveSpeakerId: 'default',
        selectedSpeakerAvailable: true,
        spatialAudioEnabled: true,
        independentAsrEnabled: true,
        coreApiSupportsIndependentAsr: true,
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
        DEFAULT_SPEAKER_DEVICE_ID: 'default',
        MAX_SPEAKER_VOLUME: 200,
        SPEAKER_VOLUME_KNEE_RATIO: 0.75,
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
    window.__speakerSelections = [];
    window.__speakerSelectionResult = true;
    window.__statusToasts = [];
    window.__unhandledRejectionCount = 0;
    window.addEventListener('unhandledrejection', () => {
        window.__unhandledRejectionCount += 1;
    });
    window.showStatusToast = (...args) => {
        window.__statusToasts.push(args);
    };
    window.selectSpeakerDevice = async (deviceId) => {
        window.__speakerSelections.push(deviceId);
        if (window.__speakerSelectionResult === 'throw') {
            throw new DOMException('device unavailable', 'NotFoundError');
        }
        if (window.__speakerSelectionResult === false) return false;
        S.selectedSpeakerId = deviceId;
        S.effectiveSpeakerId = deviceId;
        S.selectedSpeakerAvailable = true;
        return true;
    };
    window.reconcileSelectedSpeakerDevices = async (devices) => {
        const preferred = S.selectedSpeakerId;
        S.selectedSpeakerAvailable = preferred === 'default'
            || devices.some((device) => (
                device.kind === 'audiooutput' && device.deviceId === preferred
            ));
        S.effectiveSpeakerId = S.selectedSpeakerAvailable ? preferred : 'default';
        return S.selectedSpeakerAvailable;
    };
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
    window.__screenToggleCalls = 0;
    function createScreenShareToggleButton() {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.nekoScreenShareAction = 'toggle';
        button.addEventListener('click', () => { window.__screenToggleCalls += 1; });
        return button;
    }
    let deferScreenSources = false;
    const screenSourceResolvers = [];
    window.renderFloatingScreenSourceList = async (container) => {
        if (deferScreenSources) {
            await new Promise((resolve) => {
                screenSourceResolvers.push(resolve);
            });
        }
        const source = document.createElement('button');
        source.type = 'button';
        source.textContent = 'test-screen';
        container.appendChild(source);
        const filter = document.createElement('input');
        filter.type = 'search';
        filter.className = 'screen-source-title-filter';
        container.appendChild(filter);
        return true;
    };
    let rememberWindowEnabled = true;
    window.__rememberWindowSetCalls = [];
    window.isScreenSourceTitleMatchEnabled = () => rememberWindowEnabled;
    window.setScreenSourceTitleMatchEnabled = (enabled) => {
        rememberWindowEnabled = enabled;
        window.__rememberWindowSetCalls.push(enabled);
    };

    let micPermissionGranted = false;
    let cachedMicDevices = null;
    let cachedSpeakerDevices = null;
    let mediaDeviceEnumerationGeneration = 0;
    let latestMediaDeviceEnumerationPromise = Promise.resolve(null);
    let disposeVoiceRecognitionPopover = null;
    let voiceRecognitionPopoverRenderGeneration = 0;

    __PERMISSION_SOURCE__
    window.renderFloatingMicList = __RENDER_EXPRESSION__;

    window.__voicePopoverTest = {
        state: S,
        capturedErrors,
        listenerBalance,
        setCachedSpeakerDevices(devices) {
            cachedSpeakerDevices = devices;
        },
        setSpeakerSelectionResult(result) {
            window.__speakerSelectionResult = result;
        },
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
        deferScreenSources() {
            deferScreenSources = true;
        },
        resolveScreenSources() {
            deferScreenSources = false;
            while (screenSourceResolvers.length) {
                screenSourceResolvers.shift()();
            }
        },
        pendingScreenSources: () => screenSourceResolvers.length,
        rememberWindowEnabled: () => rememberWindowEnabled,
        failMicVolumeVisualization() {
            failMicVolumeVisualization = true;
        },
        failVoiceControlsSetupOn(type) {
            failWindowListenerType = type;
        },
        pendingPermissions: () => mediaResolvers.length,
        popup: () => document.getElementById('live2d-popup-mic'),
        action: (key) => document.querySelector(
            '[data-neko-mic-main-action="' + key + '"]'
        ),
        voiceAction: () => document.querySelector(
            '[data-neko-mic-main-action="voice-recognition"]'
        ),
        actionRow: (key) => document.querySelector(
            '[data-neko-mic-main-action-row="' + key + '"]'
        ),
        voiceToggle: () => document.querySelector(
            '[data-neko-mic-main-action-row="voice-recognition"] '
            + '.neko-voice-setting-toggle-input'
        ),
        screenToggle: () => document.querySelector(
            '[data-neko-mic-main-action-row="screen"] '
            + '[data-neko-screen-share-action="toggle"]'
        ),
        panel: (key = 'voice-recognition') => document.querySelector(
            '.neko-mic-subwindow[data-neko-mic-action-key="' + key + '"]'
        ),
        ownedPanels: () => document.querySelectorAll(
            '.neko-mic-subwindow[data-neko-sidepanel-owner="live2d-popup-mic"]'
        ),
        panels: () => document.querySelectorAll('.neko-mic-subwindow').length,
    };
})();
"""
    harness = harness.replace(
        "__TRACKED_LISTENER_KEYS__",
        json.dumps(
            [*VOICE_POPOVER_LOCAL_LISTENERS, *VOICE_POPOVER_GLOBAL_LISTENERS]
        ),
    )
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
                voiceActions: document.querySelectorAll(
                    '[data-neko-mic-main-action="voice-recognition"]'
                ).length,
                capturedErrors: [...window.__voicePopoverTest.capturedErrors],
                listenerBalance: { ...window.__voicePopoverTest.listenerBalance },
            };
            const third = await window.renderFloatingMicList(popup);
            window.__voicePopoverTest.voiceAction().click();
            await Promise.resolve();
            const panel = window.__voicePopoverTest.panel();
            return {
                afterOverlap,
                third,
                panelsAfterRerender: window.__voicePopoverTest.panels(),
                panelOwner: panel?.getAttribute('data-neko-sidepanel-owner'),
                panelIsSidePanel: panel?.hasAttribute('data-neko-sidepanel'),
                panelActionKey: panel?.getAttribute('data-neko-mic-action-key'),
                listenerBalanceAfterRerender: {
                    ...window.__voicePopoverTest.listenerBalance,
                },
            };
        }"""
    )

    assert result["afterOverlap"]["renderResults"] == [False, True]
    assert not result["afterOverlap"]["capturedErrors"]
    assert result["afterOverlap"]["panels"] == 0
    assert result["afterOverlap"]["voiceActions"] == 1
    assert result["third"] is True
    assert result["panelsAfterRerender"] == 1
    assert result["panelOwner"] == "live2d-popup-mic"
    assert result["panelIsSidePanel"] is True
    assert result["panelActionKey"] == "voice-recognition"

    expected_global_listeners = {
        key: 1 for key in VOICE_POPOVER_GLOBAL_LISTENERS
    }
    assert {
        key: value
        for key, value in result["afterOverlap"]["listenerBalance"].items()
        if value
    } == expected_global_listeners
    assert {
        key: value
        for key, value in result["listenerBalanceAfterRerender"].items()
        if value
    } == expected_global_listeners


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
def test_hung_core_capability_refresh_does_not_block_microphone_popup(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            window.__voicePopoverTest.state.coreApiSupportsIndependentAsr = null;
            window.refreshCoreApiCapability = () => new Promise(() => {});
            const popup = window.__voicePopoverTest.popup();
            return Promise.race([
                window.renderFloatingMicList(popup).then(async (rendered) => {
                    const panelsBeforeOpen = window.__voicePopoverTest.panels();
                    window.__voicePopoverTest.voiceAction().click();
                    await Promise.resolve();
                    return {
                        rendered,
                        panelsBeforeOpen,
                        panelsAfterOpen: window.__voicePopoverTest.panels(),
                    };
                }),
                new Promise((resolve) => setTimeout(
                    () => resolve({ timedOut: true }),
                    250
                )),
            ]);
        }"""
    )

    assert result == {
        "rendered": True,
        "panelsBeforeOpen": 0,
        "panelsAfterOpen": 1,
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
    assert not {
        key: value
        for key, value in result["listenerBalance"].items()
        if value
    }


@pytest.mark.frontend
def test_voice_popover_setup_failure_disposes_registered_listeners(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            window.__voicePopoverTest.failVoiceControlsSetupOn(
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
    assert not {
        key: value
        for key, value in result["listenerBalance"].items()
        if value
    }


@pytest.mark.frontend
def test_voice_popover_disposes_when_popup_host_is_removed(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            window.__voicePopoverTest.voiceAction().click();
            await Promise.resolve();
            const panelsBeforeRemoval = window.__voicePopoverTest.panels();
            popup.remove();
            await Promise.resolve();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                panelsBeforeRemoval,
                panels: window.__voicePopoverTest.panels(),
                listenerBalance: { ...window.__voicePopoverTest.listenerBalance },
            };
        }"""
    )

    assert result["panelsBeforeRemoval"] == 1
    assert result["panels"] == 0
    assert not {
        key: value
        for key, value in result["listenerBalance"].items()
        if value
    }


@pytest.mark.frontend
def test_voice_popover_toggles_have_accessible_names_and_hints(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            window.__voicePopoverTest.voiceAction().click();
            await Promise.resolve();
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
def test_voice_device_and_screen_actions_share_one_owned_subwindow(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());

            async function openAndSnapshot(key) {
                test.action(key).click();
                await new Promise((resolve) => setTimeout(resolve, 0));
                const panels = Array.from(test.ownedPanels());
                return {
                    count: panels.length,
                    actionKey: panels[0]?.getAttribute(
                        'data-neko-mic-action-key'
                    ),
                    owner: panels[0]?.getAttribute(
                        'data-neko-sidepanel-owner'
                    ),
                    sidePanel: panels[0]?.hasAttribute('data-neko-sidepanel'),
                };
            }

            const voice = await openAndSnapshot('voice-recognition');
            const device = await openAndSnapshot('device');
            const speaker = await openAndSnapshot('speaker-device');
            const screen = await openAndSnapshot('screen');
            const closeButton = test.ownedPanels()[0].querySelector(
                'button[aria-label="Close"]'
            );
            closeButton.click();
            return {
                voice,
                device,
                speaker,
                screen,
                panelsAfterClose: test.ownedPanels().length,
            };
        }"""
    )

    assert result["voice"] == {
        "count": 1,
        "actionKey": "voice-recognition",
        "owner": "live2d-popup-mic",
        "sidePanel": True,
    }
    assert result["device"] == {
        "count": 1,
        "actionKey": "device",
        "owner": "live2d-popup-mic",
        "sidePanel": True,
    }
    assert result["speaker"] == {
        "count": 1,
        "actionKey": "speaker-device",
        "owner": "live2d-popup-mic",
        "sidePanel": True,
    }
    assert result["screen"] == {
        "count": 1,
        "actionKey": "screen",
        "owner": "live2d-popup-mic",
        "sidePanel": True,
    }
    assert result["panelsAfterClose"] == 0


@pytest.mark.frontend
def test_screen_source_subwindow_header_has_remember_window_toggle(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.action('screen').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const panel = test.panel('screen');
            const control = panel.querySelector(
                '.neko-screen-source-remember-control'
            );
            const input = control.querySelector(
                '.neko-screen-source-title-match-toggle'
            );
            const closeButton = panel.querySelector('button[aria-label="Close"]');
            const initiallyChecked = input.checked;
            input.click();
            return {
                controlText: control.textContent,
                initiallyChecked,
                checkedAfterClick: input.checked,
                ariaLabel: input.getAttribute('aria-label'),
                controlBeforeClose: Boolean(
                    control.compareDocumentPosition(closeButton)
                        & Node.DOCUMENT_POSITION_FOLLOWING
                ),
                setterCalls: window.__rememberWindowSetCalls,
                enabledAfterClick: test.rememberWindowEnabled(),
            };
        }"""
    )

    assert result == {
        "controlText": "app.screenSource.rememberWindow",
        "initiallyChecked": True,
        "checkedAfterClick": False,
        "ariaLabel": "app.screenSource.rememberWindow",
        "controlBeforeClose": True,
        "setterCalls": [False],
        "enabledAfterClick": False,
    }


@pytest.mark.frontend
def test_screen_source_subwindow_ignores_leave_and_closes_on_parent_return(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.action('screen').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
        }"""
    )

    filter_input = page.locator(
        '.neko-mic-subwindow[data-neko-mic-action-key="screen"] '
        '.screen-source-title-filter'
    )
    filter_input.focus()
    page.locator(
        '.neko-mic-subwindow[data-neko-mic-action-key="screen"]'
    ).hover()
    page.locator("#outside-target").hover()
    page.wait_for_timeout(360)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 1

    page.evaluate(
        """() => {
            document.querySelector('.screen-source-title-filter').blur();
            window.dispatchEvent(new Event('blur'));
        }"""
    )
    page.wait_for_timeout(360)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 1

    page.locator('[data-neko-mic-main-action="screen"]').hover()
    page.wait_for_function("window.__voicePopoverTest.panels() === 0")


@pytest.mark.frontend
def test_screen_source_subwindow_closes_when_parent_popup_hides(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            const popup = test.popup();
            await window.renderFloatingMicList(popup);
            test.action('screen').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeHide = test.panels();
            popup.style.display = 'none';
            await Promise.resolve();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return { beforeHide, afterHide: test.panels() };
        }"""
    )
    assert result == {"beforeHide": 1, "afterHide": 0}


@pytest.mark.frontend
def test_playback_device_action_position_and_pseudo_device_filtering(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            const column = test.popup().firstElementChild;
            const order = Array.from(column.children).map((node) => (
                node.dataset.nekoMicMainActionRow
                || (node.classList.contains('speaker-volume-container')
                    ? 'speaker-volume'
                    : null)
            )).filter(Boolean);

            test.action('speaker-device').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const options = Array.from(
                test.panel('speaker-device').querySelectorAll('.speaker-option')
            ).map((option) => ({
                deviceId: option.dataset.deviceId,
                text: option.textContent,
                selected: option.classList.contains('selected'),
                pressed: option.getAttribute('aria-pressed'),
            }));
            const speakerB = test.panel('speaker-device').querySelector(
                '.speaker-option[data-device-id="speaker-b"]'
            );
            speakerB.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                order,
                options,
                selections: window.__speakerSelections.slice(),
                selectedSpeakerId: test.state.selectedSpeakerId,
                summary: test.action('speaker-device').querySelector(
                    '.neko-mic-action-sub-label'
                ).textContent,
                summaryLive: test.action('speaker-device').querySelector(
                    '.neko-mic-action-sub-label'
                ).getAttribute('aria-live'),
            };
        }"""
    )

    assert result["order"][:5] == [
        "screen",
        "device",
        "voice-recognition",
        "speaker-device",
        "speaker-volume",
    ]
    assert [option["deviceId"] for option in result["options"]] == [
        "default",
        "speaker-a",
        "speaker-b",
    ]
    assert result["options"][0]["selected"] is True
    assert result["options"][0]["pressed"] == "true"
    assert all(option["pressed"] == "false" for option in result["options"][1:])
    assert result["selections"] == ["speaker-b"]
    assert result["selectedSpeakerId"] == "speaker-b"
    assert result["summary"] == "Speaker B"
    assert result["summaryLive"] == "polite"


@pytest.mark.frontend
def test_playback_device_summary_uses_the_latest_cached_devices(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            test.state.selectedSpeakerId = 'restored-speaker';
            test.state.selectedSpeakerAvailable = false;
            await window.renderFloatingMicList(test.popup());
            const summary = test.action('speaker-device').querySelector(
                '.neko-mic-action-sub-label'
            );
            const beforeRestore = summary.textContent;
            test.state.effectiveSpeakerId = 'restored-speaker';
            window.dispatchEvent(new CustomEvent('neko:speaker-device-changed'));
            const failedFallback = summary.textContent;
            test.setCachedSpeakerDevices([
                { kind: 'audiooutput', deviceId: 'default', label: 'Default' },
                {
                    kind: 'audiooutput',
                    deviceId: 'restored-speaker',
                    label: 'Restored Speaker',
                },
            ]);
            test.state.selectedSpeakerAvailable = true;
            window.dispatchEvent(new CustomEvent('neko:speaker-device-changed'));
            const afterRestore = summary.textContent;
            test.state.selectedSpeakerId = 'default';
            test.state.effectiveSpeakerId = 'restored-speaker';
            window.dispatchEvent(new CustomEvent('neko:speaker-device-changed'));
            const failedDefaultRoute = summary.textContent;
            test.state.effectiveSpeakerId = 'default';
            window.dispatchEvent(new CustomEvent('neko:speaker-device-changed'));
            return {
                beforeRestore,
                failedFallback,
                afterRestore,
                failedDefaultRoute,
                successfulDefaultRoute: summary.textContent,
            };
        }"""
    )

    assert result == {
        "beforeRestore": "speaker.unavailableFallback",
        "failedFallback": "speaker.unavailableFallbackFailed",
        "afterRestore": "Restored Speaker",
        "failedDefaultRoute": "speaker.unavailableFallbackFailed",
        "successfulDefaultRoute": "speaker.defaultDevice",
    }


@pytest.mark.frontend
def test_playback_device_event_refreshes_open_option_selection(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.action('speaker-device').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            test.state.selectedSpeakerId = 'speaker-b';
            test.state.selectedSpeakerAvailable = true;
            test.state.effectiveSpeakerId = 'speaker-b';
            window.dispatchEvent(new CustomEvent('neko:speaker-device-changed'));
            return Array.from(
                test.panel('speaker-device').querySelectorAll('.speaker-option')
            ).map((option) => ({
                deviceId: option.dataset.deviceId,
                selected: option.classList.contains('selected'),
                pressed: option.getAttribute('aria-pressed'),
            }));
        }"""
    )

    assert result == [
        {"deviceId": "default", "selected": False, "pressed": "false"},
        {"deviceId": "speaker-a", "selected": False, "pressed": "false"},
        {"deviceId": "speaker-b", "selected": True, "pressed": "true"},
    ]


@pytest.mark.frontend
def test_playback_device_false_result_shows_switch_failure(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.action('speaker-device').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            test.setSpeakerSelectionResult(false);
            test.panel('speaker-device').querySelector(
                '.speaker-option[data-device-id="speaker-b"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                selected: test.state.selectedSpeakerId,
                toasts: window.__statusToasts.map((args) => args[0]),
            };
        }"""
    )

    assert result == {
        "selected": "default",
        "toasts": ["speaker.switchFailed"],
    }


@pytest.mark.frontend
def test_playback_device_exception_shows_switch_failure(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.action('speaker-device').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            test.setSpeakerSelectionResult('throw');
            test.panel('speaker-device').querySelector(
                '.speaker-option[data-device-id="speaker-b"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                selected: test.state.selectedSpeakerId,
                toasts: window.__statusToasts.map((args) => args[0]),
                unhandledRejections: window.__unhandledRejectionCount,
            };
        }"""
    )

    assert result == {
        "selected": "default",
        "toasts": ["speaker.switchFailed"],
        "unhandledRejections": 0,
    }


@pytest.mark.frontend
def test_devicechange_discards_an_older_out_of_order_enumeration(
    page: Page,
) -> None:
    device_change_source = _device_change_source()
    page.set_content("<main>devicechange generation harness</main>")
    page.add_script_tag(
        content=(
            r"""
            (() => {
                const pendingEnumerations = [];
                const reconcileCalls = [];
                let deviceChangeListener = null;
                Object.defineProperty(navigator, 'mediaDevices', {
                    configurable: true,
                    value: {
                        enumerateDevices() {
                            return new Promise((resolve) => {
                                pendingEnumerations.push(resolve);
                            });
                        },
                        addEventListener(type, listener) {
                            if (type === 'devicechange') deviceChangeListener = listener;
                        },
                    },
                });
                var cachedMicDevices = null;
                var cachedSpeakerDevices = null;
                var mediaDeviceChangeGeneration = 0;
                var mediaDeviceEnumerationGeneration = 0;
                var latestMediaDeviceEnumerationPromise = Promise.resolve(null);
                window.reconcileSelectedSpeakerDevices = async (devices) => {
                    reconcileCalls.push(devices.map((device) => device.deviceId));
                };
                window.renderFloatingMicList = async () => true;
                __DEVICE_CHANGE_SOURCE__
                window.__deviceChangeTest = {
                    emit: () => deviceChangeListener(),
                    resolve(index, devices) {
                        pendingEnumerations[index](devices);
                    },
                    result: () => ({
                        speakers: (cachedSpeakerDevices || []).map(
                            (device) => device.deviceId
                        ),
                        reconcileCalls: reconcileCalls.slice(),
                    }),
                };
            })();
            """.replace("__DEVICE_CHANGE_SOURCE__", device_change_source)
        )
    )

    result = page.evaluate(
        """async () => {
            const test = window.__deviceChangeTest;
            const older = test.emit();
            const newer = test.emit();
            test.resolve(1, [
                { kind: 'audioinput', deviceId: 'mic-new' },
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'preferred-speaker' },
            ]);
            await new Promise((resolve) => setTimeout(resolve, 0));
            test.resolve(0, [
                { kind: 'audioinput', deviceId: 'mic-old' },
                { kind: 'audiooutput', deviceId: 'default' },
            ]);
            await Promise.all([older, newer]);
            return test.result();
        }"""
    )

    assert result == {
        "speakers": ["default", "preferred-speaker"],
        "reconcileCalls": [["mic-new", "default", "preferred-speaker"]],
    }


@pytest.mark.frontend
def test_permission_enumeration_cannot_overwrite_a_newer_devicechange_result(
    page: Page,
) -> None:
    device_change_source = _device_change_source()
    page.set_content("<main>shared media enumeration generation harness</main>")
    page.add_script_tag(
        content=(
            r"""
            (() => {
                const pendingEnumerations = [];
                const reconcileCalls = [];
                let deviceChangeListener = null;
                Object.defineProperty(navigator, 'mediaDevices', {
                    configurable: true,
                    value: {
                        async getUserMedia() {
                            return { getTracks: () => [{ stop() {} }] };
                        },
                        enumerateDevices() {
                            return new Promise((resolve) => {
                                pendingEnumerations.push(resolve);
                            });
                        },
                        addEventListener(type, listener) {
                            if (type === 'devicechange') deviceChangeListener = listener;
                        },
                    },
                });
                var micPermissionGranted = false;
                var cachedMicDevices = null;
                var cachedSpeakerDevices = null;
                var mediaDeviceChangeGeneration = 0;
                var mediaDeviceEnumerationGeneration = 0;
                var latestMediaDeviceEnumerationPromise = Promise.resolve(null);
                window.reconcileSelectedSpeakerDevices = async (devices) => {
                    reconcileCalls.push(devices.map((device) => device.deviceId));
                };
                window.renderFloatingMicList = async () => true;
                __DEVICE_CHANGE_SOURCE__
                window.__mediaEnumerationTest = {
                    startPermission: () => ensureMicrophonePermission(),
                    emitDeviceChange: () => deviceChangeListener(),
                    pendingCount: () => pendingEnumerations.length,
                    resolve(index, devices) {
                        pendingEnumerations[index](devices);
                    },
                    result: () => ({
                        microphones: (cachedMicDevices || []).map(
                            (device) => device.deviceId
                        ),
                        speakers: (cachedSpeakerDevices || []).map(
                            (device) => device.deviceId
                        ),
                        reconcileCalls: reconcileCalls.slice(),
                    }),
                };
            })();
            """.replace("__DEVICE_CHANGE_SOURCE__", device_change_source)
        )
    )

    result = page.evaluate(
        """async () => {
            const test = window.__mediaEnumerationTest;
            const waitForPendingCount = async (expected) => {
                for (let attempt = 0; attempt < 1000; attempt += 1) {
                    if (test.pendingCount() >= expected) return;
                    await new Promise((resolve) => setTimeout(resolve, 0));
                }
                throw new Error('expected media enumeration was not observed');
            };
            const permission = test.startPermission();
            await waitForPendingCount(1);
            const deviceChange = test.emitDeviceChange();
            await waitForPendingCount(2);
            test.resolve(1, [
                { kind: 'audioinput', deviceId: 'mic-new' },
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'preferred-speaker' },
            ]);
            await deviceChange;
            test.resolve(0, [
                { kind: 'audioinput', deviceId: 'mic-old' },
                { kind: 'audiooutput', deviceId: 'default' },
            ]);
            const permissionDevices = await permission;
            return {
                permissionDevices: permissionDevices.map(
                    (device) => device.deviceId
                ),
                ...test.result(),
            };
        }"""
    )

    assert result == {
        "permissionDevices": ["mic-new"],
        "microphones": ["mic-new"],
        "speakers": ["default", "preferred-speaker"],
        "reconcileCalls": [["mic-new", "default", "preferred-speaker"]],
    }


@pytest.mark.frontend
def test_newer_enumeration_reconciles_after_an_older_route_is_blocked(
    page: Page,
) -> None:
    device_change_source = _device_change_source()
    page.set_content("<main>media enumeration reconciliation ownership harness</main>")
    page.add_script_tag(
        content=(
            r"""
            (() => {
                const pendingEnumerations = [];
                const reconcileCalls = [];
                let deviceChangeListener = null;
                let releaseFirstReconciliation;
                const firstReconciliationGate = new Promise((resolve) => {
                    releaseFirstReconciliation = resolve;
                });
                let reconciliationTail = Promise.resolve();
                let effectiveDevices = [];
                Object.defineProperty(navigator, 'mediaDevices', {
                    configurable: true,
                    value: {
                        async getUserMedia() {
                            return { getTracks: () => [{ stop() {} }] };
                        },
                        enumerateDevices() {
                            return new Promise((resolve) => {
                                pendingEnumerations.push(resolve);
                            });
                        },
                        addEventListener(type, listener) {
                            if (type === 'devicechange') deviceChangeListener = listener;
                        },
                    },
                });
                var micPermissionGranted = false;
                var cachedMicDevices = null;
                var cachedSpeakerDevices = null;
                var mediaDeviceChangeGeneration = 0;
                var mediaDeviceEnumerationGeneration = 0;
                var latestMediaDeviceEnumerationPromise = Promise.resolve(null);
                window.reconcileSelectedSpeakerDevices = (devices) => {
                    const deviceIds = devices.map((device) => device.deviceId);
                    const operation = reconciliationTail.then(async () => {
                        reconcileCalls.push(deviceIds);
                        if (reconcileCalls.length === 1) {
                            await firstReconciliationGate;
                        }
                        effectiveDevices = deviceIds;
                    });
                    reconciliationTail = operation.catch(() => {});
                    return operation;
                };
                window.renderFloatingMicList = async () => true;
                __DEVICE_CHANGE_SOURCE__
                window.__mediaReconciliationTest = {
                    startPermission: () => ensureMicrophonePermission(),
                    emitDeviceChange: () => deviceChangeListener(),
                    pendingCount: () => pendingEnumerations.length,
                    reconcileCount: () => reconcileCalls.length,
                    resolve(index, devices) {
                        pendingEnumerations[index](devices);
                    },
                    releaseFirstReconciliation() {
                        releaseFirstReconciliation();
                    },
                    result: () => ({
                        microphones: (cachedMicDevices || []).map(
                            (device) => device.deviceId
                        ),
                        speakers: (cachedSpeakerDevices || []).map(
                            (device) => device.deviceId
                        ),
                        reconcileCalls: reconcileCalls.slice(),
                        effectiveDevices: effectiveDevices.slice(),
                    }),
                };
            })();
            """.replace("__DEVICE_CHANGE_SOURCE__", device_change_source)
        )
    )

    result = page.evaluate(
        """async () => {
            const test = window.__mediaReconciliationTest;
            const waitFor = async (predicate, message) => {
                for (let attempt = 0; attempt < 1000; attempt += 1) {
                    if (predicate()) return;
                    await new Promise((resolve) => setTimeout(resolve, 0));
                }
                throw new Error(message);
            };

            const deviceChange = test.emitDeviceChange();
            await waitFor(
                () => test.pendingCount() >= 1,
                'expected the devicechange enumeration'
            );
            test.resolve(0, [
                { kind: 'audioinput', deviceId: 'mic-old' },
                { kind: 'audiooutput', deviceId: 'default' },
            ]);
            await waitFor(
                () => test.reconcileCount() === 1,
                'expected the older reconciliation to block'
            );

            const permission = test.startPermission();
            await waitFor(
                () => test.pendingCount() >= 2,
                'expected the newer permission enumeration'
            );
            test.resolve(1, [
                { kind: 'audioinput', deviceId: 'mic-new' },
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'preferred-speaker' },
            ]);
            test.releaseFirstReconciliation();
            await Promise.all([deviceChange, permission]);
            return test.result();
        }"""
    )

    assert result == {
        "microphones": ["mic-new"],
        "speakers": ["default", "preferred-speaker"],
        "reconcileCalls": [
            ["mic-old", "default"],
            ["mic-new", "default", "preferred-speaker"],
        ],
        "effectiveDevices": ["mic-new", "default", "preferred-speaker"],
    }


@pytest.mark.frontend
def test_voice_action_uses_shared_260ms_hover_collapse(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    page.evaluate(
        """async () => {
            await window.renderFloatingMicList(
                window.__voicePopoverTest.popup()
            );
        }"""
    )

    page.locator(
        '[data-neko-mic-main-action="voice-recognition"]'
    ).hover()
    page.wait_for_function("window.__voicePopoverTest.panels() === 1")
    page.locator(
        '.neko-mic-subwindow[data-neko-mic-action-key="voice-recognition"]'
    ).hover()
    page.wait_for_timeout(320)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 1

    page.locator("#outside-target").hover()
    page.wait_for_timeout(100)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 1
    page.wait_for_function(
        "window.__voicePopoverTest.panels() === 0", timeout=2000
    )


@pytest.mark.frontend
def test_voice_action_rerender_clears_the_previous_hover_timer(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    page.evaluate(
        """async () => {
            await window.renderFloatingMicList(
                window.__voicePopoverTest.popup()
            );
        }"""
    )

    page.locator(
        '[data-neko-mic-main-action="voice-recognition"]'
    ).hover()
    page.wait_for_function("window.__voicePopoverTest.panels() === 1")
    page.locator("#outside-target").hover()
    page.wait_for_timeout(50)

    page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            await window.renderFloatingMicList(test.popup());
            test.voiceAction().click();
            await Promise.resolve();
        }"""
    )
    page.wait_for_timeout(320)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 1


@pytest.mark.frontend
def test_stale_screen_open_cannot_relabel_a_new_voice_subwindow(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            const popup = test.popup();
            await window.renderFloatingMicList(popup);
            test.deferScreenSources();
            test.action('screen').click();
            if (test.pendingScreenSources() !== 1) {
                throw new Error('expected the old screen panel to be pending');
            }

            await window.renderFloatingMicList(popup);
            test.voiceAction().click();
            await Promise.resolve();
            const newVoicePanel = test.panel();
            const beforeResolve = {
                count: test.ownedPanels().length,
                actionKey: newVoicePanel?.getAttribute(
                    'data-neko-mic-action-key'
                ),
            };

            test.resolveScreenSources();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const currentPanel = test.ownedPanels()[0];
            return {
                beforeResolve,
                afterResolve: {
                    count: test.ownedPanels().length,
                    samePanel: currentPanel === newVoicePanel,
                    actionKey: currentPanel?.getAttribute(
                        'data-neko-mic-action-key'
                    ),
                },
            };
        }"""
    )

    expected = {"count": 1, "actionKey": "voice-recognition"}
    assert result["beforeResolve"] == expected
    assert result["afterResolve"] == {
        **expected,
        "samePanel": True,
    }


@pytest.mark.frontend
def test_action_row_controls_are_siblings_and_do_not_cross_trigger(page: Page) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)
    page.evaluate(
        """async () => {
            await window.renderFloatingMicList(
                window.__voicePopoverTest.popup()
            );
        }"""
    )

    structure = page.evaluate(
        """() => {
            const test = window.__voicePopoverTest;
            const voiceAction = test.voiceAction();
            const voiceToggle = test.voiceToggle();
            const voiceRow = test.actionRow('voice-recognition');
            const screenAction = test.action('screen');
            const screenToggle = test.screenToggle();
            const screenRow = test.actionRow('screen');
            return {
                voiceRowTag: voiceRow?.tagName,
                voiceSiblings: voiceAction?.parentElement === voiceRow
                    && voiceToggle?.closest('label')?.parentElement === voiceRow,
                voiceNested: voiceAction?.contains(voiceToggle),
                checkboxInsideButton: voiceToggle?.closest('button') !== null,
                screenToggleTag: screenToggle?.tagName,
                screenSiblings: screenAction?.parentElement === screenRow
                    && screenToggle?.parentElement === screenRow,
                screenNested: screenAction?.contains(screenToggle),
            };
        }"""
    )
    assert structure == {
        "voiceRowTag": "DIV",
        "voiceSiblings": True,
        "voiceNested": False,
        "checkboxInsideButton": False,
        "screenToggleTag": "BUTTON",
        "screenSiblings": True,
        "screenNested": False,
    }

    screen_toggle = page.locator(
        '[data-neko-mic-main-action-row="screen"] '
        '[data-neko-screen-share-action="toggle"]'
    )
    screen_toggle.focus()
    page.keyboard.press("Space")
    assert page.evaluate("window.__screenToggleCalls") == 1
    assert page.evaluate("window.__voicePopoverTest.panels()") == 0

    asr_input = page.locator(
        '[data-neko-mic-main-action-row="voice-recognition"] '
        '.neko-voice-setting-toggle-input'
    )
    asr_input.hover()
    page.wait_for_timeout(50)
    assert page.evaluate("window.__voicePopoverTest.panels()") == 0

    asr_input.click()
    page.wait_for_timeout(50)
    result = page.evaluate(
        """() => ({
            panels: window.__voicePopoverTest.panels(),
            preference: window.__voicePopoverTest.state.independentAsrEnabled,
            saveCalls: window.__saveCalls,
        })"""
    )
    assert result == {"panels": 0, "preference": False, "saveCalls": 1}

    asr_input.focus()
    page.keyboard.press("Space")
    result = page.evaluate(
        """() => ({
            panels: window.__voicePopoverTest.panels(),
            preference: window.__voicePopoverTest.state.independentAsrEnabled,
            saveCalls: window.__saveCalls,
        })"""
    )
    assert result == {"panels": 0, "preference": True, "saveCalls": 2}

    voice_action = page.locator(
        '[data-neko-mic-main-action="voice-recognition"]'
    )
    voice_action.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("window.__voicePopoverTest.panels() === 1")
    asr_input.hover()
    page.wait_for_timeout(320)
    result = page.evaluate(
        """() => ({
            panels: window.__voicePopoverTest.panels(),
            preference: window.__voicePopoverTest.state.independentAsrEnabled,
            checked: window.__voicePopoverTest.voiceToggle().checked,
            saveCalls: window.__saveCalls,
        })"""
    )
    assert result == {
        "panels": 1,
        "preference": True,
        "checked": True,
        "saveCalls": 2,
    }
    page.locator("#outside-target").hover()
    page.wait_for_function("window.__voicePopoverTest.panels() === 0")


@pytest.mark.frontend
def test_core_without_independent_asr_shows_native_effective_view_and_keeps_preference(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const test = window.__voicePopoverTest;
            const state = test.state;
            state.coreApiSupportsIndependentAsr = false;
            const popup = test.popup();
            await window.renderFloatingMicList(popup);
            const container = test.voiceAction();
            container.click();
            await Promise.resolve();

            const panel = test.panel();
            const asrInput = test.voiceToggle();
            const panelInputs = panel.querySelectorAll('input[type="checkbox"]');
            const noiseInput = panelInputs[0];
            const optimizationInput = panelInputs[1];
            const summary = () => container.querySelector(
                '.neko-mic-action-sub-label'
            ).textContent;
            const status = () => panel.querySelector(
                '.neko-voice-recognition-status'
            ).textContent;

            const nativeView = {
                preference: state.independentAsrEnabled,
                asrChecked: asrInput.checked,
                asrDisabled: asrInput.disabled,
                optimizationChecked: optimizationInput.checked,
                optimizationDisabled: optimizationInput.disabled,
                noiseDisabled: noiseInput.disabled,
                summary: summary(),
                status: status(),
            };

            // Even a synthetic change event must not mutate or persist the
            // preference while the effective control is disabled.
            asrInput.checked = true;
            asrInput.dispatchEvent(new Event('change', { bubbles: true }));
            const afterDisabledChange = {
                preference: state.independentAsrEnabled,
                checked: asrInput.checked,
                saveCalls: window.__saveCalls,
            };

            state.coreApiSupportsIndependentAsr = true;
            window.dispatchEvent(new CustomEvent(
                'neko:core-api-capability-changed'
            ));
            const restoredView = {
                preference: state.independentAsrEnabled,
                asrChecked: asrInput.checked,
                asrDisabled: asrInput.disabled,
                optimizationChecked: optimizationInput.checked,
                optimizationDisabled: optimizationInput.disabled,
                summary: summary(),
                status: status(),
            };
            return { nativeView, afterDisabledChange, restoredView };
        }"""
    )

    assert result["nativeView"] == {
        "preference": True,
        "asrChecked": False,
        "asrDisabled": True,
        "optimizationChecked": False,
        "optimizationDisabled": True,
        "noiseDisabled": False,
        "summary": "microphone.voiceRecognitionDisabled",
        "status": "microphone.voiceRecognitionNativeCoreHint",
    }
    assert result["afterDisabledChange"] == {
        "preference": True,
        "checked": False,
        "saveCalls": 0,
    }
    assert result["restoredView"] == {
        "preference": True,
        "asrChecked": True,
        "asrDisabled": False,
        "optimizationChecked": True,
        "optimizationDisabled": False,
        "summary": "microphone.independentAsrSummary",
        "status": "microphone.voiceRecognitionStatusReady",
    }


@pytest.mark.frontend
def test_voice_settings_pending_clears_only_after_target_session(
    page: Page,
) -> None:
    _install_voice_popover_harness(page, deferred_permission=False)

    result = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            window.__voicePopoverTest.voiceAction().click();
            await Promise.resolve();
            const firstPanel = window.__voicePopoverTest.panel();
            const firstStatus = firstPanel.querySelector(
                '.neko-voice-recognition-status'
            );
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

            const asrInput = window.__voicePopoverTest.voiceToggle();
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
    assert result["panels"] == 0
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

    before_open = page.evaluate(
        """async () => {
            const popup = window.__voicePopoverTest.popup();
            await window.renderFloatingMicList(popup);
            const container = window.__voicePopoverTest.voiceAction();
            const asrInput = window.__voicePopoverTest.voiceToggle();

            window.__voicePopoverTest.state.voiceChatActive = true;
            window.__voicePopoverTest.state.independentAsrActive = true;
            asrInput.checked = false;
            asrInput.dispatchEvent(new Event('change', { bubbles: true }));

            const summary = container.querySelector(
                '.neko-mic-action-sub-label'
            ).textContent;
            container.focus();
            return {
                summary,
                actionTag: container.tagName,
                actionFocused: document.activeElement === container,
                panelsBeforeEnter: window.__voicePopoverTest.panels(),
            };
        }"""
    )
    page.keyboard.press("Enter")
    after_open = page.evaluate(
        """() => {
            const panel = window.__voicePopoverTest.panel();
            const panelInputs = panel.querySelectorAll('input[type="checkbox"]');
            return {
                panels: window.__voicePopoverTest.panels(),
                noiseDisabled: panelInputs[0].disabled,
                optimizationDisabled: panelInputs[1].disabled,
                actionKey: panel.getAttribute('data-neko-mic-action-key'),
            };
        }"""
    )

    assert before_open == {
        "summary": "microphone.independentAsrSummary",
        "actionTag": "BUTTON",
        "actionFocused": True,
        "panelsBeforeEnter": 0,
    }
    assert after_open == {
        "panels": 1,
        "noiseDisabled": False,
        "optimizationDisabled": True,
        "actionKey": "voice-recognition",
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
            const container = window.__voicePopoverTest.voiceAction();
            container.click();
            await Promise.resolve();

            const panel = window.__voicePopoverTest.panel();
            return {
                summary: container.querySelector(
                    '.neko-mic-action-sub-label'
                ).textContent,
                status: panel.querySelector(
                    '.neko-voice-recognition-status'
                ).textContent,
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
            const container = window.__voicePopoverTest.voiceAction();
            container.focus();
        }"""
    )

    page.keyboard.press("Enter")

    result = page.evaluate(
        """() => {
            const panel = window.__voicePopoverTest.panel();
            const input = panel.querySelector('input[type="checkbox"]');
            input.focus();
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
