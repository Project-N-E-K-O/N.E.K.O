/**
 * app-spatial-audio.js — 多屏立体声 + 距离衰减
 *
 * 听者锚点：主屏中心（屏幕坐标系）。
 * 声源锚点：当前 Pet 窗口中心（屏幕坐标系）。
 *
 * Pan：dx / (主屏宽度 / 2)，clamp 到 [-1, 1]
 * Gain：以主屏为参考圆，主屏内全音量；越过主屏后线性衰减，floor = SPATIAL_AUDIO_MIN_GAIN
 *
 * 更新时机：
 *   1) 音频图建立（attach）后立即一次
 *   2) electron-display-changed 事件
 *   3) 兜底轮询（SPATIAL_AUDIO_POLL_MS）—— 拖拽/程序化移动均覆盖
 */
(function () {
    'use strict';

    var S = window.appState;
    var C = window.appConst;
    if (!S || !C) {
        console.warn('[Spatial] appState / appConst 未就绪，跳过初始化');
        return;
    }

    var STORAGE_KEY = 'neko_spatial_audio_enabled';

    function loadEnabledSetting() {
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            if (saved === null || saved === undefined) {
                return C.DEFAULT_SPATIAL_AUDIO_ENABLED;
            }
            return saved === 'true' || saved === '1';
        } catch (_) {
            return C.DEFAULT_SPATIAL_AUDIO_ENABLED;
        }
    }

    function saveEnabledSetting(enabled) {
        try {
            localStorage.setItem(STORAGE_KEY, enabled ? 'true' : 'false');
        } catch (_) { /* noop */ }
    }

    S.spatialAudioEnabled = loadEnabledSetting();

    // 主屏信息缓存。Electron 下通过 IPC 拉取；非 Electron / 浏览器环境下用 window.screen 兜底。
    function refreshPrimaryDisplay() {
        if (window.electronScreen && typeof window.electronScreen.getPrimaryDisplayInfo === 'function') {
            return window.electronScreen.getPrimaryDisplayInfo().then(function (info) {
                if (info && info.bounds) {
                    S.spatialPrimaryDisplay = info;
                }
                return S.spatialPrimaryDisplay;
            }).catch(function () {
                return S.spatialPrimaryDisplay;
            });
        }
        // 非 Electron 兜底：把主屏视为 (0,0)-(screen.width, screen.height)
        if (!S.spatialPrimaryDisplay) {
            S.spatialPrimaryDisplay = {
                bounds: { x: 0, y: 0, width: window.screen.width || 1920, height: window.screen.height || 1080 },
                workArea: { x: 0, y: 0, width: window.screen.width || 1920, height: window.screen.height || 1080 }
            };
        }
        return Promise.resolve(S.spatialPrimaryDisplay);
    }

    function getCurrentWindowBounds() {
        if (window.nekoPetDrag && typeof window.nekoPetDrag.getBounds === 'function') {
            return window.nekoPetDrag.getBounds().catch(function () { return null; });
        }
        // 浏览器兜底：window.screenX/Y + window.outerWidth/outerHeight
        return Promise.resolve({
            x: window.screenX || 0,
            y: window.screenY || 0,
            width: window.outerWidth || window.innerWidth || 0,
            height: window.outerHeight || window.innerHeight || 0
        });
    }

    function clamp(v, lo, hi) {
        if (v < lo) return lo;
        if (v > hi) return hi;
        return v;
    }

    /**
     * 根据窗口与主屏几何，计算 pan ∈ [-1,1] 和 gain ∈ [floor, 1]
     * - pan 仅看水平方向，dx / (primary.width / 2)
     * - gain 看二维欧氏距离：以主屏中心为圆心、主屏 half-width 为参考半径
     *   - 距离 ≤ 1.0 ref：gain = 1（主屏内不衰减）
     *   - 距离 > 1.0 ref：每 1 ref 衰减 SPATIAL_AUDIO_FALLOFF_RATE，floor = SPATIAL_AUDIO_MIN_GAIN
     */
    function computePanAndGain(winBounds, primary) {
        if (!winBounds || !primary || !primary.bounds) {
            return { pan: 0, gain: 1 };
        }
        var pb = primary.bounds;
        var primaryCenterX = pb.x + pb.width / 2;
        var primaryCenterY = pb.y + pb.height / 2;
        var winCenterX = winBounds.x + winBounds.width / 2;
        var winCenterY = winBounds.y + winBounds.height / 2;
        var refDist = Math.max(1, pb.width / 2);

        var dx = winCenterX - primaryCenterX;
        var dy = winCenterY - primaryCenterY;
        var pan = clamp(dx / refDist, -1, 1);

        var dist = Math.sqrt(dx * dx + dy * dy);
        var nd = dist / refDist;
        var gain;
        if (nd <= 1) {
            gain = 1;
        } else {
            gain = Math.max(C.SPATIAL_AUDIO_MIN_GAIN, 1 - (nd - 1) * C.SPATIAL_AUDIO_FALLOFF_RATE);
        }
        return { pan: pan, gain: gain };
    }

    function applyPanAndGain(pan, gain) {
        if (!S.spatialPannerNode || !S.spatialDistanceGainNode) return;
        if (!S.spatialAudioEnabled) {
            // 关闭时：pan=0 / gain=1，等同 passthrough
            pan = 0;
            gain = 1;
        }
        var ctx = S.spatialPannerNode.context;
        var t = ctx.currentTime;
        var ramp = C.SPATIAL_AUDIO_RAMP_SECONDS;
        try {
            // setTargetAtTime 比 linearRamp 更平滑，与 speakerGainNode 保持一致风格
            S.spatialPannerNode.pan.setTargetAtTime(pan, t, ramp);
            S.spatialDistanceGainNode.gain.setTargetAtTime(gain, t, ramp);
        } catch (_) {
            // 极端情况下 fallback 直接赋值
            S.spatialPannerNode.pan.value = pan;
            S.spatialDistanceGainNode.gain.value = gain;
        }
    }

    function updateOnce() {
        if (!S.spatialPannerNode || !S.spatialDistanceGainNode) return;
        // 关闭时只需把节点拉回 passthrough，不必查询位置
        if (!S.spatialAudioEnabled) {
            applyPanAndGain(0, 1);
            return;
        }
        Promise.all([refreshPrimaryDisplay(), getCurrentWindowBounds()]).then(function (results) {
            var primary = results[0];
            var bounds = results[1];
            var r = computePanAndGain(bounds, primary);
            applyPanAndGain(r.pan, r.gain);
        });
    }

    function startPolling() {
        stopPolling();
        S.spatialPollTimer = setInterval(updateOnce, C.SPATIAL_AUDIO_POLL_MS);
    }

    function stopPolling() {
        if (S.spatialPollTimer) {
            clearInterval(S.spatialPollTimer);
            S.spatialPollTimer = null;
        }
    }

    // ======================== 公共 API ========================

    var mod = {};

    /** 由 initializeGlobalAnalyser 在节点创建后调用 */
    mod.attach = function () {
        // 立即一次更新，把 panner/gain 拉到正确值
        updateOnce();
        startPolling();
    };

    mod.setEnabled = function (enabled) {
        S.spatialAudioEnabled = !!enabled;
        saveEnabledSetting(S.spatialAudioEnabled);
        // 立刻应用新状态：开 → 重新计算并启动轮询；关 → 拉回 passthrough 并停轮询
        if (S.spatialAudioEnabled) {
            updateOnce();
            startPolling();
        } else {
            stopPolling();
            applyPanAndGain(0, 1);
        }
    };

    mod.getEnabled = function () { return !!S.spatialAudioEnabled; };

    /** 主动刷新（外部事件触发） */
    mod.refresh = function () { updateOnce(); };

    // 屏幕配置变化（拔插显示器、分辨率变化等）→ 重新拉主屏信息再算
    window.addEventListener('electron-display-changed', function () {
        S.spatialPrimaryDisplay = null; // 失效缓存
        updateOnce();
    });

    window.appSpatialAudio = mod;

    // 若 audio graph 已经先一步建好（脚本顺序异常），主动 attach 一次
    if (S.spatialPannerNode && S.spatialDistanceGainNode && !S.spatialPollTimer) {
        mod.attach();
    }
})();
