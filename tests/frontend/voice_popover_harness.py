"""Production-source browser harness for floating voice menu tests."""

import json
from pathlib import Path

from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
APP_SCREEN = ROOT / "static" / "app" / "app-screen.js"
DESKTOP_CAPTURE_PROVIDER = ROOT / "static" / "app" / "desktop-capture-provider.js"
VOICE_POPOVER_LOCAL_LISTENERS = (
    "document:pointerdown",
    "document:keydown",
    "window:scroll",
)
VOICE_POPOVER_GLOBAL_LISTENERS = (
    "window:resize",
    "window:voice-input-lifecycle-changed",
    "window:neko:voice-session-started",
    "window:neko:voice-settings-pending-changed",
    "window:neko:core-api-capability-changed",
    "window:neko:conversation-settings-hydrated",
    "window:neko:speaker-device-changed",
    "window:neko:screen-source-changed",
)


def extract_voice_popover_sources() -> tuple[str, str]:
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


def extract_device_change_source() -> str:
    source = APP_AUDIO_CAPTURE.read_text(encoding="utf-8")
    start = source.index("async function enumerateAndCacheMediaDevices()")
    end = source.index("/** 为浮动弹出框渲染麦克风列表 */", start)
    return source[start:end].strip()


def install_voice_popover_harness(
    page: Page, *, deferred_permission: bool, desktop_bridge: bool = False
) -> None:
    permission_source, render_expression = extract_voice_popover_sources()
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
        localAsrAvailable: true,
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
    function isScreenShareActive() { return !!window.__screenActive; }
    function createScreenShareToggleButton() {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.nekoScreenShareAction = 'toggle';
        button.addEventListener('click', () => { window.__screenToggleCalls += 1; });
        return button;
    }
    let deferScreenSources = false;
    const screenSourceResolvers = [];
    // 可选的桌面来源桥（desktop_bridge=True）：复用缓存的屏幕面板前会用它
    // 核对来源 id。__desktopSources 是「当前存在的来源」，改它模拟窗口被关掉。
    // 默认不装：浏览器分享面板的用例依赖「没有桌面桥」。
    window.__desktopSources = [{ id: 'screen:0:0', name: 'test-screen' }];
    window.__desktopGetSourcesCalls = 0;
    window.__desktopGetSourcesFails = false;
    if (__DESKTOP_BRIDGE__) window.electronDesktopCapturer = {
        sourceEnumerationMayPrompt: false,
        async getSources() {
            window.__desktopGetSourcesCalls += 1;
            if (window.__desktopGetSourcesGate) await window.__desktopGetSourcesGate;
            if (window.__desktopGetSourcesFails) throw new Error('getSources failed');
            return window.__desktopSources.map((source) => ({ ...source }));
        },
    };
    window.__screenRenderOptions = [];
    window.renderFloatingScreenSourceList = async (container, options = {}) => {
        window.__screenRenderOptions.push({
            deferEnumeration: options.deferEnumeration === true,
        });
        if (options.deferEnumeration === true) {
            container.innerHTML = '';
            const load = document.createElement('button');
            load.type = 'button';
            load.dataset.nekoScreenSourceDeferredLoad = '';
            load.addEventListener('click', async () => {
                const rendered = await window.renderFloatingScreenSourceList(
                    container, { ...options, deferEnumeration: false }
                );
                options.onDeferredRender?.(rendered);
            });
            container.appendChild(load);
            return true;
        }
        container.innerHTML = '';
        if (deferScreenSources) {
            await new Promise((resolve) => {
                screenSourceResolvers.push(resolve);
            });
        }
        // 生产渲染在枚举失败 / 空列表时（retryOnFailure）留下当前来源摘要和
        // 重试按钮并返回 false；__screenRenderFailures 计数控制接下来几次失败。
        if (window.__screenRenderFailures > 0) {
            window.__screenRenderFailures -= 1;
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.dataset.nekoScreenSourceDeferredLoad = '';
            container.appendChild(retry);
            return false;
        }
        // 装了桌面桥时按 __desktopSources 生成卡片（与核对用的来源一致），
        // 否则固定一张 test-screen。
        const listed = window.electronDesktopCapturer
            ? window.__desktopSources
            : [{ id: 'screen:0:0', name: 'test-screen' }];
        listed.forEach((listedSource) => {
            const source = document.createElement('button');
            source.type = 'button';
            source.className = 'screen-source-option';
            source.textContent = listedSource.name;
            source.dataset.sourceId = listedSource.id;
            source.dataset.sourceName = listedSource.name;
            // 生产渲染的第二阶段先放 loading 占位再补缩略图；__screenThumbnailsComplete
            // 为 false 时停在 loading，模拟面板在第二阶段期间被拆走（填充放弃）。
            const thumbnail = document.createElement('div');
            thumbnail.className = window.__screenThumbnailsComplete === false
                ? 'screen-source-thumbnail screen-source-thumbnail-loading'
                : 'screen-source-thumbnail screen-source-thumbnail-ready';
            source.appendChild(thumbnail);
            container.appendChild(source);
        });
        const filter = document.createElement('input');
        filter.type = 'search';
        filter.className = 'screen-source-title-filter';
        container.appendChild(filter);
        return true;
    };
    window.__screenThumbnailsComplete = true;
    window.__screenRenderFailures = 0;
    window.__settleScreenThumbnails = () => {
        document.querySelectorAll('.screen-source-thumbnail-loading').forEach((el) => {
            el.className = 'screen-source-thumbnail screen-source-thumbnail-ready';
        });
    };
    let rememberWindowEnabled = true;
    window.__rememberWindowSetCalls = [];
    window.isScreenSourceTitleMatchEnabled = () => rememberWindowEnabled;
    // 模拟另一个窗口改了开关：只改状态，不派发 neko:screen-source-changed。
    window.__setRememberWindowFromOtherWindow = (enabled) => {
        rememberWindowEnabled = enabled;
    };
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
    harness = harness.replace(
        "__DESKTOP_BRIDGE__", "true" if desktop_bridge else "false"
    )
    harness = harness.replace("__PERMISSION_SOURCE__", permission_source)
    harness = harness.replace("__RENDER_EXPRESSION__", render_expression)
    page.add_script_tag(path=str(DESKTOP_CAPTURE_PROVIDER))
    page.add_script_tag(content=harness)
