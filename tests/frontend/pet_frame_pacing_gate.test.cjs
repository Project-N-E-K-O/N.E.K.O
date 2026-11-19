const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test, mock } = require('node:test');
const vm = require('node:vm');

// pytest 包装层把本文件内容写到临时路径再交给 node 跑，__dirname 不是仓库根。
const fileRoot = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const read = (rel) => fs.readFileSync(path.join(PROJECT_ROOT, ...rel.split('/')), 'utf8');

const FRAME_PACING_SRC = read('static/frame-pacing.js');
const LIVE2D_CORE_SRC = read('static/live2d/live2d-core.js');
const VRM_MANAGER_SRC = read('static/vrm/vrm-manager.js');
const MMD_CORE_SRC = read('static/mmd/mmd-core.js');

// ─────────────────────────────────────────────────────────────────────────────
// 沙盒：真实加载 frame-pacing.js + 目标后端源码；rAF 由测试手动推帧（可指定刷新率），
// setTimeout/setInterval 走 node:test 的 mock timers。
// ─────────────────────────────────────────────────────────────────────────────
function makeTicker(name) {
    return {
        name,
        started: true,
        maxFPS: 0,
        updates: 0,
        startCalls: 0,
        stopCalls: 0,
        update() { this.updates++; },
        start() { this.started = true; this.startCalls++; },
        stop() { this.started = false; this.stopCalls++; },
    };
}

function createSandbox({ pet, targetFrameRate }) {
    const rafQueue = [];
    let rafId = 0;
    const listeners = {};
    const PIXI = {
        live2d: { Live2DModel: class Live2DModel {} },
        Ticker: { shared: makeTicker('shared'), system: makeTicker('system') },
        Application: class Application {
            constructor() { this.ticker = makeTicker('app'); this.stage = {}; this.renderer = {}; }
        },
    };
    const window = {
        PIXI,
        targetFrameRate,
        __LANLAN_IS_ELECTRON_PET__: pet === true,
        innerWidth: 1920,
        innerHeight: 1080,
        screen: { width: 1920, height: 1080 },
        devicePixelRatio: 1,
        location: { hostname: 'localhost', pathname: '/' },
        navigator: { userAgent: 'node' },
        localStorage: { getItem() { return null; }, setItem() {} },
        addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        removeEventListener() {},
        dispatchEvent(evt) { (listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; },
    };
    const document = {
        hidden: false,
        readyState: 'complete',
        getElementById() { return null; },
        addEventListener() {},
        createElement() { return { style: {} }; },
        body: { classList: { contains() { return false; } } },
    };
    // 沙盒时钟：随 pumpFrames 推进；rAF 时间戳与 performance.now() 同源
    let frameClock = 0;
    const sandbox = {
        window,
        document,
        PIXI,
        console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
        performance: { now: () => frameClock },
        navigator: window.navigator,
        localStorage: window.localStorage,
        // 走 globalThis 间接调用，让 node:test 的 mock timers 生效
        setTimeout: (...a) => globalThis.setTimeout(...a),
        clearTimeout: (...a) => globalThis.clearTimeout(...a),
        setInterval: (...a) => globalThis.setInterval(...a),
        clearInterval: (...a) => globalThis.clearInterval(...a),
        requestAnimationFrame(fn) { rafId += 1; rafQueue.push({ id: rafId, fn }); return rafId; },
        cancelAnimationFrame(id) { const i = rafQueue.findIndex((e) => e.id === id); if (i >= 0) rafQueue.splice(i, 1); },
        CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    };
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(FRAME_PACING_SRC, sandbox, { filename: 'frame-pacing.js' });

    // 手动推 n 个 rAF 帧，时间戳按 hz 递增
    const pumpFrames = (n, hz) => {
        for (let i = 0; i < n; i++) {
            frameClock += 1000 / hz;
            const batch = rafQueue.splice(0, rafQueue.length);
            batch.forEach((e) => e.fn(frameClock));
        }
    };
    // 按给定时间戳逐帧推 rAF（时间戳必须单调递增；用于合成抖动的 vsync 序列）
    const pumpAt = (stamps) => {
        for (const t of stamps) {
            frameClock = t;
            const batch = rafQueue.splice(0, rafQueue.length);
            batch.forEach((e) => e.fn(frameClock));
        }
    };
    const now = () => frameClock;
    // 推进 mock 定时器的同时推进沙盒时钟，让 performance.now() 依赖的保持期判定
    // （_hasRenderActivity 的 900ms 窗口）也随时间失效
    // 按 1ms 步进，让每个定时器回调看到的 performance.now() 是它自己的到期时刻，
    // 而不是本次 tick 的终点（跨多个到期点时保持期判定才准确）
    const tick = (ms) => {
        let remaining = Math.max(0, Number(ms) || 0);
        while (remaining > 0) {
            const step = Math.min(1, remaining);
            frameClock += step;
            mock.timers.tick(step);
            remaining -= step;
        }
    };
    const measureRefresh = (hz) => {
        // frame-pacing：load 后延迟 1500ms 才开始采样，采 24 帧
        tick(1500);
        pumpFrames(30, hz);
    };
    const load = (src, name) => vm.runInContext(src, sandbox, { filename: name });
    return { sandbox, window, PIXI, pumpFrames, pumpAt, now, measureRefresh, tick, load, rafQueue, listeners };
}

function withMockTimers(fn) {
    return () => {
        mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
        try { fn(); } finally { mock.timers.reset(); }
    };
}

function setupLive2D(sb) {
    sb.load(LIVE2D_CORE_SRC, 'live2d-core.js');
    const mgr = new sb.window.Live2DManager();
    const app = new sb.PIXI.Application();
    mgr.pixi_app = app;
    mgr.isInitialized = true;
    // 复刻 initPIXI 里对 ticker.stop/start 的包装
    const origStop = app.ticker.stop.bind(app.ticker);
    const origStart = app.ticker.start.bind(app.ticker);
    mgr._tickerOrigStop = origStop;
    mgr._tickerOrigStart = origStart;
    // 与 live2d-core.js initPIXI 里的包装保持一致（下面有契约断言防漂移）
    const ticker = app.ticker;
    ticker.stop = function () {
        if (mgr.pixi_app && mgr.pixi_app.ticker === ticker) {
            mgr._exitIdleTickMode({ restartGlobals: false });
            if (mgr._resolveGlobalTickers()) mgr._holdGlobalTickers();
        }
        return origStop();
    };
    ticker.start = function () {
        if (mgr.pixi_app && mgr.pixi_app.ticker === ticker) { mgr._exitIdleTickMode(); mgr._releaseGlobalTickers(); }
        return origStart();
    };
    return { mgr, app, shared: sb.PIXI.Ticker.shared, system: sb.PIXI.Ticker.system };
}

test('契约：initPIXI 的 ticker.stop/start 包装语义与测试复刻一致（含旧 ticker 身份检查）', () => {
    assert.match(LIVE2D_CORE_SRC, /ticker\.stop = function \(\) \{\s*if \(mgr\.pixi_app && mgr\.pixi_app\.ticker === ticker\) \{\s*mgr\._exitIdleTickMode\(\{ restartGlobals: false \}\);\s*if \(mgr\._resolveGlobalTickers\(\)\) mgr\._holdGlobalTickers\(\);\s*\}\s*return origStop\(\);\s*\};/);
    assert.match(LIVE2D_CORE_SRC, /ticker\.start = function \(\) \{\s*if \(mgr\.pixi_app && mgr\.pixi_app\.ticker === ticker\) \{\s*mgr\._exitIdleTickMode\(\);\s*mgr\._releaseGlobalTickers\(\);\s*\}\s*return origStart\(\);\s*\};/);
});

test('Live2D：PIXI 重建后旧 ticker 的包装不再动当前实例的调度状态', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    sb.measureRefresh(60);
    const { mgr, app: oldApp } = setupLive2D(sb);
    const oldTicker = oldApp.ticker;
    // 模拟 rebuildPIXI：换成新实例并进入定时器模式
    const newApp = new sb.PIXI.Application();
    mgr.pixi_app = newApp;
    mgr._tickerOrigStop = newApp.ticker.stop.bind(newApp.ticker);
    mgr._tickerOrigStart = newApp.ticker.start.bind(newApp.ticker);
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    oldTicker.stop(); // 外部残留引用
    assert.equal(mgr._idleTickMode, true, '旧 ticker 的 stop 不能把当前实例踢出定时器模式');
    oldTicker.start();
    assert.equal(mgr._idleTickMode, true, '旧 ticker 的 start 同理');
    assert.equal(oldTicker.started, true, '旧 ticker 自己的 start/stop 照常生效');
}));

// ─────────────────────────────────────────────────────────────────────────────
// frame-pacing.js
// ─────────────────────────────────────────────────────────────────────────────
test('frame-pacing：非 Pet 页面永远不给定时器驱动帧率', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 30 });
    sb.measureRefresh(60);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), null, '非 Pet 不采样刷新率');
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), null);
}));

test('frame-pacing：Pet 窗口先量出刷新率，配置明显低于刷新率才切定时器驱动', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    const pacing = sb.window.nekoFramePacing;
    assert.equal(pacing.activeTimerTickFps(), null, '刷新率未测出前保守走 rAF');
    sb.measureRefresh(60);
    assert.equal(pacing.getDisplayRefreshHz(), 60);
    assert.equal(pacing.activeTimerTickFps(), 45, '45 < 60×0.9 → 定时器驱动 45fps');
    sb.window.targetFrameRate = 60;
    assert.equal(pacing.activeTimerTickFps(), null, '60fps 配置在 60Hz 屏留在 rAF（vsync 对齐）');
    sb.window.targetFrameRate = 0;
    assert.equal(pacing.activeTimerTickFps(), null, '0=不限帧 留在 rAF');
    sb.window.targetFrameRate = 30;
    assert.equal(pacing.activeTimerTickFps(), 30);
}));

test('frame-pacing：重测超时要作废旧刷新率，回到保守 rAF', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), 45);
    // 跨屏事件触发重测，但 rAF 一帧都不来 → 3s 超时
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    // mock timers 不会在同一次 tick 里跑 tick 期间新排的定时器：先到采样起点，再跨过超时
    sb.tick(1500);
    sb.tick(3000);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), null, '超时后旧值作废');
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), null, '未知刷新率 → rAF');
    // 再来一次重测成功 → 恢复
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.measureRefresh(120);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), 120);
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), 45);
}));

test('frame-pacing：渲染后端定时器驱动时，requestPacedFrame 走定时器而非 rAF', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    const pacing = sb.window.nekoFramePacing;
    let calls = 0;
    // 无后端 / 后端在 rAF 模式：走 rAF
    sb.window.live2dManager = { _idleTickMode: false, _idleTickFps: null };
    assert.equal(pacing.currentTimerTickFps(), null);
    pacing.requestPacedFrame(() => { calls++; });
    assert.equal(sb.rafQueue.length, 1, 'rAF 模式下排 rAF');
    sb.pumpFrames(1, 60);
    assert.equal(calls, 1);
    // 后端定时器驱动 30fps：走 setTimeout(33ms)，不排 rAF
    sb.window.live2dManager = { _idleTickMode: true, _idleTickFps: 30 };
    assert.equal(pacing.currentTimerTickFps(), 30);
    const cancel = pacing.requestPacedFrame(() => { calls++; });
    assert.equal(sb.rafQueue.length, 0, '定时器驱动下不排 rAF');
    sb.tick(32);
    assert.equal(calls, 1, '33ms 前不触发');
    sb.tick(2);
    assert.equal(calls, 2, '按 30fps 周期触发');
    const cancel2 = pacing.requestPacedFrame(() => { calls++; });
    cancel2();
    sb.tick(100);
    assert.equal(calls, 2, '取消函数生效');
    assert.equal(typeof cancel, 'function');
}));

test('frame-pacing：非 Pet 页面 currentTimerTickFps 恒为 null', () => {
    const sb = createSandbox({ pet: false, targetFrameRate: 30 });
    sb.window.live2dManager = { _idleTickMode: true, _idleTickFps: 30 };
    assert.equal(sb.window.nekoFramePacing.currentTimerTickFps(), null);
});

test('契约：VRM / MMD 浮动按钮循环在定时器模式下到点直接 update，不再转一次性 rAF 多等一个 vsync', () => {
    for (const [file, fpsExpr] of [
        ['static/vrm/vrm-ui-buttons.js', 'this\\._idleTickFps'],
        ['static/mmd/mmd-ui-buttons.js', 'this\\.core\\._idleTickFps'],
    ]) {
        const src = read(file);
        const start = src.indexOf('const scheduleNext = () => {');
        const end = src.indexOf('const update = () => {', start);
        assert.ok(start > 0 && end > start, file + ' scheduleNext 定位失败');
        const body = src.slice(start, end);
        assert.match(body, new RegExp('const tickFps = Number\\(' + fpsExpr + '\\) > 0 \\? Number\\(' + fpsExpr + '\\) : 30;'), file + ' 周期跟随渲染 tick');
        const cbStart = body.indexOf('this._uiLoopIdleTimeout = setTimeout(() => {');
        const cbEnd = body.indexOf('}, Math.max(4, Math.round(1000 / tickFps)));', cbStart);
        assert.ok(cbStart >= 0 && cbEnd > cbStart, file + ' 定时器回调定位失败');
        const timerCallback = body.slice(cbStart, cbEnd);
        assert.match(timerCallback, /update\(\);\s*$/, file + ' 定时器到点直接 update');
        assert.doesNotMatch(timerCallback, /requestAnimationFrame/, file + ' 定时器回调里不允许再排任何 rAF');
        const outsideTimer = body.slice(0, cbStart) + body.slice(cbEnd);
        assert.equal((outsideTimer.match(/requestAnimationFrame\(/g) || []).length, 1, file + ' 只有 rAF 模式那一处排 rAF');
    }
});

test('契约：演出舞台的 tween / 漂浮 / 呼吸 / 姿态时间线循环通过 frame-pacing 排帧', () => {
    const src = read('static/avatar/avatar-performance-stage.js');
    assert.match(src, /function scheduleStageFrame\(callback\)[\s\S]*?return pacing\.requestPacedFrame\(callback\);/);
    assert.equal((src.match(/tween\.cancelFrame = scheduleStageFrame\(step\);/g) || []).length, 6, 'transition/idleFloat/breathe 三条循环各两处排帧');
    assert.equal((src.match(/cancelFrame = scheduleStageFrame\(tick\);/g) || []).length, 2, '姿态时间线两处排帧');
    assert.doesNotMatch(src, /tween\.rafId/, 'tween 不再持有裸 rAF id');
    assert.doesNotMatch(src, /frameId = window\.requestAnimationFrame\(tick\)/, '姿态时间线不再裸排 rAF');
    // 允许残留的裸 rAF 只剩等待 Live2D 上下文那条有界短循环（check）
    const remaining = (src.match(/window\.requestAnimationFrame\(/g) || []).length;
    assert.equal(remaining, 3, `裸 rAF 只剩 scheduleStageFrame 内一处 + check 两处，实际 ${remaining}`);
});

test('契约：Live2D / VRM / MMD 拖拽回弹（吸附）动画通过 frame-pacing 排帧', () => {
    const l2d = read('static/live2d/live2d-interaction.js');
    assert.match(l2d, /function scheduleLive2DSnapFrame\(callback\)[\s\S]*?return pacing\.requestPacedFrame\(callback\);/);
    const snap = l2d.slice(l2d.indexOf('Live2DManager.prototype._performSnapAnimation = function'), l2d.indexOf('检测并执行自动吸附'));
    assert.equal((snap.match(/scheduleLive2DSnapFrame\(animate\);/g) || []).length, 2, 'Live2D 首帧与续帧都走 paced');
    assert.doesNotMatch(snap, /requestAnimationFrame\(animate\)/, 'Live2D 吸附动画不允许裸排 rAF');
    for (const file of ['static/vrm/vrm-interaction.js', 'static/mmd/mmd-interaction.js']) {
        const src = read(file);
        assert.match(src, /_scheduleSnapFrame\(callback\) \{[\s\S]*?return pacing\.requestPacedFrame\(callback\);/, file + ' 有 paced 排帧 helper');
        assert.equal((src.match(/this\._snapCancelFrame = this\._scheduleSnapFrame\(animate\);/g) || []).length, 2, file + ' 首帧与续帧都走 paced');
        assert.doesNotMatch(src, /_snapAnimationFrameId/, file + ' 不再持有裸 rAF id');
        assert.ok((src.match(/this\._snapCancelFrame\(\);/g) || []).length >= 3, file + ' 取消路径用取消函数');
    }
});

test('契约：反应气泡跟随循环通过 frame-pacing 排帧', () => {
    const src = read('static/avatar/avatar-reaction-bubble.js');
    assert.match(src, /function scheduleFollowFrame\(tick\)[\s\S]*?state\.followPacedCancel = pacing\.requestPacedFrame\(tick\);/);
    assert.match(src, /function stopFollowLoop\(\) \{\s*cancelFollowFrame\(\);/);
    const follow = src.slice(src.indexOf('function extendFollowLoop('), src.indexOf('function syncPositionOnce('));
    assert.doesNotMatch(follow, /requestAnimationFrame\(tick\)/, 'extendFollowLoop 内不允许直接排 rAF');
    assert.equal((follow.match(/scheduleFollowFrame\(tick\);/g) || []).length, 2, '首帧与续帧都走 paced 排帧');
    assert.match(follow, /if \(isFollowFrameScheduled\(\)\) \{\s*return;/, '已排帧判定要同时认 rAF 与定时器句柄');
});

test('契约：麦克风音量监测与口型同步循环通过 frame-pacing 排帧', () => {
    const capture = read('static/app/app-audio-capture.js');
    assert.match(capture, /function scheduleMonitorInputVolume\(\)[\s\S]*?pacing\.requestPacedFrame\(monitorInputVolume\)/);
    const monitorBody = capture.slice(capture.indexOf('function monitorInputVolume()'), capture.indexOf('// ======================== AudioWorklet'));
    assert.doesNotMatch(monitorBody, /requestAnimationFrame\(monitorInputVolume\)/, 'monitorInputVolume 内不允许直接排 rAF');
    assert.equal((monitorBody.match(/scheduleMonitorInputVolume\(\);/g) || []).length, 2, 'mute 分支与常规分支都走 paced 排帧');
    // 麦克风音量可视化：弹窗不可见低频探测、可见时跟随渲染 tick、stop 同时取消两种句柄
    const volumeStart = capture.indexOf('function updateVolumeDisplay()');
    const volumeEnd = capture.indexOf('// 立即更新音量显示状态（用于录音状态变化时立即反映）', volumeStart);
    assert.ok(volumeStart >= 0 && volumeEnd > volumeStart, 'updateVolumeDisplay 区块定位失败');
    const volumeBody = capture.slice(volumeStart, volumeEnd);
    assert.doesNotMatch(volumeBody, /S\.micVolumeAnimationId = requestAnimationFrame\(updateVolumeDisplay\);\s*return;/, '弹窗不可见分支不允许按刷新率排 rAF');
    assert.match(volumeBody, /scheduleMicVolumeFrame\(MIC_VOLUME_HIDDEN_POLL_MS\);/);
    assert.match(volumeBody, /function scheduleMicVolumeFrame\(delayMs\)[\s\S]*?_micVolumePacedCancel = pacing\.requestPacedFrame\(updateVolumeDisplay\);/);
    assert.match(volumeBody, /function stopMicVolumeVisualization\(\) \{\s*cancelMicVolumeFrame\(\);/);
    assert.match(capture, /const MIC_VOLUME_HIDDEN_POLL_MS = 250;/);
    const playback = read('static/app/app-audio-playback.js');
    assert.match(playback, /function scheduleLipSyncFrame\(animate\)[\s\S]*?_lipSyncPacedCancel = pacing\.requestPacedFrame\(animate\);/);
    assert.match(playback, /const pacedByTimer = scheduleLipSyncFrame\(animate\);/);
    assert.match(playback, /if \(!pacedByTimer && \+\+_lipSyncSkipCounter < LIP_SYNC_EVERY_N_FRAMES\) return;/);
    assert.match(playback, /function stopLipSync\(model, owner\) \{[\s\S]*?cancelLipSyncFrame\(\);/);
    const lipSyncBody = playback.slice(playback.indexOf('function startLipSync('), playback.indexOf('function stopLipSync('));
    assert.doesNotMatch(lipSyncBody, /S\.animationFrameId = requestAnimationFrame\(animate\)/, 'startLipSync 内不允许直接排 rAF');
});

test('frame-pacing：采样进行中再来一次重测请求不丢，当前轮结束后紧接着再测', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), 60);
    // 第一次跨屏：采样开始但一帧没推（慢采样中）
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    assert.equal(sb.rafQueue.length, 1, '采样已开始');
    // 第二次跨屏在采样中到达
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    // 当前轮在 120Hz 屏上采完
    sb.pumpFrames(25, 120);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), 120, '当前轮结果先落地');
    assert.equal(sb.rafQueue.length, 1, '排队的第二轮紧接着开始，而不是被丢掉');
    sb.pumpFrames(25, 144);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), 144, '第二轮结果覆盖');
    assert.equal(sb.rafQueue.length, 0, '没有第三轮');
}));

test('frame-pacing：采样中排队的 measureDisplayRefreshRate(onDone) 回调在最后一轮结束后拿到最终结果', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    const pacing = sb.window.nekoFramePacing;
    const firstResults = [];
    const queuedResults = [];
    pacing.measureDisplayRefreshRate((hz) => firstResults.push(hz));
    assert.equal(sb.rafQueue.length, 1, '第一轮采样开始');
    pacing.measureDisplayRefreshRate((hz) => queuedResults.push(hz));
    pacing.measureDisplayRefreshRate((hz) => queuedResults.push(hz * 10));
    assert.equal(queuedResults.length, 0, '排队请求的回调不会被提前调用');
    sb.pumpFrames(25, 60);
    assert.deepEqual(firstResults, [60], '首轮回调拿到首轮结果');
    assert.deepEqual(queuedResults, [], '排队回调要等最后一轮');
    sb.pumpFrames(25, 120);
    assert.deepEqual(queuedResults, [120, 1200], '排队回调都拿到最终轮结果');
    assert.equal(sb.rafQueue.length, 0);
}));

test('frame-pacing：144Hz 屏上 60fps 配置也切定时器驱动', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(144);
    assert.equal(sb.window.nekoFramePacing.getDisplayRefreshHz(), 144);
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), 60);
}));

// ─────────────────────────────────────────────────────────────────────────────
// Live2D
// ─────────────────────────────────────────────────────────────────────────────
test('Live2D 非 Pet：行为不变——活动态 rAF + maxFPS，只碰 app.ticker，不碰全局 shared/system', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 45 });
    const { mgr, app, shared, system } = setupLive2D(sb);
    shared.maxFPS = 999;
    system.maxFPS = 999;
    mgr.boostInteractiveFPS();
    assert.ok(!mgr._idleTickMode);
    assert.equal(app.ticker.started, true);
    assert.equal(app.ticker.maxFPS, 45);
    assert.equal(shared.maxFPS, 999, '非 Pet 不接管 shared');
    assert.equal(system.maxFPS, 999, '非 Pet 不接管 system');
    sb.tick(1000); // 衰减
    assert.equal(mgr._idleTickMode, true, '无活动衰减到空闲低频 tick');
    assert.equal(mgr._idleTickFps, 30);
    assert.equal(app.ticker.started, false);
    assert.equal(shared.maxFPS, 999);
    assert.equal(system.maxFPS, 999);
}));

test('Live2D Pet + 配置低于刷新率：活动态也走定时器驱动，衰减只换周期、不回 rAF', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true, '活动态直接进定时器模式');
    assert.equal(mgr._idleTickFps, 45, '周期 = 配置帧率');
    assert.equal(app.ticker.started, false, 'app ticker 的 rAF 链已停');
    assert.equal(shared.started, false, 'shared ticker 已停');
    assert.equal(system.started, false, 'system ticker 已停');
    assert.equal(app.ticker.maxFPS, 0, '定时器模式下 maxFPS 清零，避免抖动丢帧');
    assert.equal(shared.maxFPS, 0);
    assert.equal(system.maxFPS, 0);

    const before = { app: app.ticker.updates, shared: shared.updates, system: system.updates };
    sb.tick(22 * 5);
    assert.ok(app.ticker.updates - before.app >= 4, 'app ticker 按 45fps 手动 update');
    assert.ok(shared.updates - before.shared >= 4, 'shared ticker 一起被手动 update');
    assert.ok(system.updates - before.system >= 4, 'system ticker 一起被手动 update');

    sb.tick(1000); // 衰减
    assert.equal(mgr._idleTickMode, true);
    assert.equal(mgr._idleTickFps, 30, '衰减到地板 30');
    assert.equal(app.ticker.startCalls, 0, '整个过程从未回到 rAF 模式');

    // 空闲态改设置：只换周期
    sb.window.targetFrameRate = 24;
    mgr.setTargetFPS(24);
    assert.equal(mgr._idleTickMode, true);
    assert.equal(mgr._idleTickFps, 24, '地板不超过用户配置');
    assert.equal(app.ticker.startCalls, 0);
}));

test('Live2D Pet + 配置 = 刷新率：rAF 模式接管 shared/system 的 maxFPS（显示器即上限 → 放宽到 90），进出定时器模式正确保存/恢复', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr.boostInteractiveFPS();
    assert.ok(!mgr._idleTickMode, '60@60Hz 留在 rAF');
    assert.equal(app.ticker.maxFPS, 90, '配置 ≥ 刷新率：软件上限放宽到刷新率×1.5，显示器即上限');
    assert.equal(shared.maxFPS, 90, 'Pet 下 shared 也按同一判定');
    assert.equal(system.maxFPS, 90);
    assert.equal(mgr._rafMaxFpsRequested, 60, '判定前的请求值仍是配置帧率');

    sb.tick(1000); // 衰减 → 空闲定时器
    assert.equal(mgr._idleTickMode, true);
    assert.equal(mgr._idleTickFps, 30);
    assert.equal(app.ticker.maxFPS, 0);
    assert.equal(shared.maxFPS, 0);
    assert.equal(system.maxFPS, 0);

    mgr.boostInteractiveFPS(); // 活动 → 回 rAF
    assert.equal(mgr._idleTickMode, false);
    assert.equal(app.ticker.started, true);
    assert.equal(shared.started, true);
    assert.equal(system.started, true);
    assert.equal(app.ticker.maxFPS, 90);
    assert.equal(shared.maxFPS, 90, '退出定时器模式后恢复到配置判定值而不是旧的地板值 30');
    assert.equal(system.maxFPS, 90);

    sb.window.targetFrameRate = 0;
    mgr.setTargetFPS(0);
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, false, '0=不限帧 留在 rAF');
    assert.equal(app.ticker.maxFPS, 0);
    assert.equal(shared.maxFPS, 0);
}));

test('Live2D：外部 ticker.stop()（切 VRM/MMD、pauseRendering）退出定时器模式时不拉起全局 ticker；start() 再拉回', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    shared.maxFPS = 0;
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    assert.equal(shared.started, false);
    app.ticker.stop(); // 切角色 / pauseRendering 走的是包装后的 stop
    assert.ok(!mgr._idleTickMode);
    assert.equal(app.ticker.started, false);
    assert.equal(shared.started, false, 'Live2D 停了，shared 不能被拉起来空跑 rAF');
    assert.equal(system.started, false, 'system 同理');
    app.ticker.start(); // 切回 Live2D
    assert.equal(app.ticker.started, true);
    assert.equal(shared.started, true, '回到 Live2D 时全局 ticker 一并恢复');
    assert.equal(system.started, true);
    assert.equal(shared.maxFPS, 30, '恢复时帧率上限也恢复到配置值');
}));

test('Live2D：真实切角色序列 stop → removeModel 的 start → 最终 stop，最后全局 ticker 仍是停的', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    app.ticker.stop();          // app-character：切走前先停
    assert.equal(shared.started, false);
    app.ticker.start();         // removeModel()：拉起来做清理渲染 → 放开全局 ticker
    assert.equal(shared.started, true);
    assert.ok(!mgr._idleTickMode, '此时不在定时器模式');
    app.ticker.stop();          // app-character：最终停掉 Live2D 给 VRM/MMD 让路
    assert.equal(shared.started, false, '不在定时器模式的 stop 也必须把 Pet 全局 ticker 扣住');
    assert.equal(system.started, false);
    // 扣住期间改帧率设置：放开时全局 ticker 拿到的是新上限，且 shared/system 各自独立恢复
    system.maxFPS = 45; // 模拟两者原本不同（放开后应各回各的，不互相串）
    sb.window.targetFrameRate = 24;
    mgr.setTargetFPS(24);
    app.ticker.start();         // 切回 Live2D
    assert.equal(shared.started, true, 'start 再放开');
    assert.equal(shared.maxFPS, 24, '扣住期间改的设置在放开时生效');
    assert.equal(system.maxFPS, 24, 'system 同样拿到新上限');
    // 非 Pet：stop 不扣全局 ticker（保持既有行为）
    const sb2 = createSandbox({ pet: false, targetFrameRate: 30 });
    const l2 = setupLive2D(sb2);
    l2.app.ticker.stop();
    assert.equal(l2.shared.started, true, '非 Pet 页面 stop 不动全局 ticker');
}));

test('Live2D：shared/system 的帧率上限分别保存、分别恢复，不互相串', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr.boostInteractiveFPS(); // rAF 模式，shared/system = 60
    shared.maxFPS = 50; // 外部把两者改成不同值
    system.maxFPS = 20;
    app.ticker.stop();  // 扣住：各自记下
    assert.equal(shared.maxFPS, 0);
    assert.equal(system.maxFPS, 0);
    app.ticker.start(); // 放开：各回各的
    assert.equal(shared.maxFPS, 50);
    assert.equal(system.maxFPS, 20, 'system 不能被 shared 的保存值覆盖');
}));

test('Live2D：stop() 后直接销毁（不经 start）也必须释放全局 ticker，否则重建后模型 autoUpdate 冻住', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr._startIdleFpsGovernor();
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    app.ticker.stop();
    assert.equal(shared.started, false);
    mgr._stopIdleFpsGovernor(); // destroy / 重建路径
    assert.equal(shared.started, true, '销毁时释放被扣住的 shared');
    assert.equal(system.started, true, '销毁时释放被扣住的 system');
    mgr._stopIdleFpsGovernor();
    assert.equal(shared.startCalls, 1, '释放幂等，不重复 start');
}));

test('Live2D：光标停在悬停范围内（isFocusing 常驻）不算持续活动，保持期过后可进空闲', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 60 });
    const { mgr } = setupLive2D(sb);
    mgr.isFocusing = true;
    assert.equal(mgr._hasRenderActivity(), false, '没有任何指针位移记录时 isFocusing 不算活动');
    // 时间戳 0 是合法值（页面刚加载时 performance.now() 可以是 0），不能被当成"未设置"
    mgr._lastPointerMoveAt = 0;
    assert.equal(mgr._hasRenderActivity(), true, '时间戳为 0 时保持期内仍算活动');
    sb.tick(1000);
    assert.equal(mgr._hasRenderActivity(), false);
    mgr._lastPointerMoveAt = sb.sandbox.performance.now();
    assert.equal(mgr._hasRenderActivity(), true, '刚动过算活动');
    sb.tick(899);
    assert.equal(mgr._hasRenderActivity(), true);
    sb.tick(2);
    assert.equal(mgr._hasRenderActivity(), false, '保持期过后视线目标已收敛，不再算活动');
    mgr._isDraggingModel = true;
    assert.equal(mgr._hasRenderActivity(), true, '拖拽仍无条件算活动');
}));

// ─────────────────────────────────────────────────────────────────────────────
// Live2D: switching from timer-driven ticks back to rAF must not drop model time.
// PIXI v7 Ticker.start() resets lastTime to performance.now(); the ticker below mirrors
// the parts of v7.4.3 Ticker that matter here (start/_requestIfNeeded, update, maxFPS
// gate, minFPS cap) and integrates deltaMS into a model clock.
// ─────────────────────────────────────────────────────────────────────────────
function makePixiV7Ticker(sb, name) {
    const now = () => sb.sandbox.performance.now();
    const t = {
        name,
        started: false,
        lastTime: -1,
        _lastFrame: -1,
        _minElapsedMS: 0,
        _maxElapsedMS: 100,
        _requestId: null,
        deltaMS: 0,
        modelClockMs: 0,
        lastUpdateAt: null,
        get maxFPS() { return this._minElapsedMS ? Math.round(1000 / this._minElapsedMS) : 0; },
        set maxFPS(fps) { this._minElapsedMS = fps === 0 ? 0 : 1000 / Math.max(10, fps); },
        _tick(time) {
            t._requestId = null;
            if (t.started) {
                t.update(time);
                if (t.started && t._requestId === null && t.hasListeners) t._requestId = sb.sandbox.requestAnimationFrame(t._tick);
            }
        },
        // PIXI only (re)requests rAF and resets lastTime when the ticker has listeners (_head.next).
        hasListeners: true,
        _requestIfNeeded() {
            if (this._requestId === null && this.hasListeners) {
                this.lastTime = now();
                this._lastFrame = this.lastTime;
                this._requestId = sb.sandbox.requestAnimationFrame(this._tick);
            }
        },
        start() { if (!this.started) { this.started = true; this._requestIfNeeded(); } },
        stop() {
            if (this.started) {
                this.started = false;
                if (this._requestId !== null) { sb.sandbox.cancelAnimationFrame(this._requestId); this._requestId = null; }
            }
        },
        update(time = now()) {
            if (time > this.lastTime) {
                let e = time - this.lastTime;
                if (e > this._maxElapsedMS) e = this._maxElapsedMS;
                if (this._minElapsedMS) {
                    const n = (time - this._lastFrame) | 0;
                    if (n < this._minElapsedMS) return;
                    this._lastFrame = time - (n % this._minElapsedMS);
                }
                this.deltaMS = e;
                this.modelClockMs += e;
                this.lastUpdateAt = time;
            } else {
                this.deltaMS = 0;
            }
            this.lastTime = time;
        },
    };
    // Real PIXI keeps update() on Ticker.prototype; mirror that so the guard's own-property
    // bookkeeping is exercised the same way.
    const proto = { update: t.update };
    delete t.update;
    Object.setPrototypeOf(t, proto);
    t.originalUpdate = proto.update;
    return t;
}

function setupLive2DWithPixiV7Tickers(sb) {
    const shared = makePixiV7Ticker(sb, 'shared');
    const system = makePixiV7Ticker(sb, 'system');
    sb.PIXI.Ticker.shared = shared;
    sb.PIXI.Ticker.system = system;
    sb.PIXI.Application = class Application {
        constructor() { this.ticker = makePixiV7Ticker(sb, 'app'); this.stage = {}; this.renderer = {}; }
    };
    const ctx = setupLive2D(sb);
    [ctx.app.ticker, shared, system].forEach((t) => t.start());
    return ctx;
}

// Advance wall time in 1ms steps: timers fire at their own due time, rAF runs at 60Hz.
// Like Chromium, rAF callbacks get the frame's begin time (a vsync grid point), and when the
// rAF chain restarts after an idle stretch the first callback comes promptly as a "missed"
// frame stamped with the last vsync, which can be earlier than the timer callback's
// performance.now() that just ran.
function runWallClock(sb, ms, onMs) {
    const frameMs = 1000 / 60;
    let nextFrame = Math.ceil(sb.sandbox.performance.now() / frameMs) * frameMs;
    let rafIdle = sb.rafQueue.length === 0;
    const flush = (time) => {
        const batch = sb.rafQueue.splice(0, sb.rafQueue.length);
        batch.forEach((e) => e.fn(time));
    };
    for (let i = 0; i < ms; i++) {
        sb.tick(1);
        if (onMs) onMs();
        const now = sb.sandbox.performance.now();
        if (now >= nextFrame) {
            const frameBegin = nextFrame;
            while (nextFrame <= now) nextFrame += frameMs;
            flush(frameBegin);
        } else if (rafIdle && sb.rafQueue.length > 0) {
            flush(nextFrame - frameMs);
        }
        rafIdle = sb.rafQueue.length === 0;
    }
}

for (const pet of [false, true]) {
    test(`Live2D ${pet ? 'Pet' : '非 Pet'}：频繁进出空闲定时器模式，app/shared/system 的模型时钟都不丢时间`, withMockTimers(() => {
        const sb = createSandbox({ pet, targetFrameRate: 60 });
        if (pet) sb.measureRefresh(60);
        const { mgr, app, shared, system } = setupLive2DWithPixiV7Tickers(sb);
        const startedAt = sb.sandbox.performance.now();
        mgr.boostInteractiveFPS();
        assert.ok(!mgr._idleTickMode, '60@60Hz 活动态走 rAF');
        let switches = 0;
        let sinceBoost = 0;
        // 约每 1.5 秒来一次活动：900ms 保持期后进空闲定时器，再被下一次活动拉回 rAF。
        // 间隔每次多错开 7ms，让切换时刻相对 33ms 定时器、16.7ms 帧的相位轮转一遍
        // （切回时既有「上一拍之后过了一段」也有「rAF 帧时间早于上一拍」）。
        let period = 1500;
        runWallClock(sb, 60000, () => {
            sinceBoost += 1;
            if (sinceBoost >= period) {
                sinceBoost = 0;
                period = 1500 + ((switches * 7) % 33);
                if (mgr._idleTickMode) switches += 1;
                mgr.boostInteractiveFPS();
            }
        });
        assert.ok(switches >= 35, `应发生多次定时器 → rAF 切换（实际 ${switches}）`);
        for (const t of [app.ticker, shared, system]) {
            const wall = t.lastUpdateAt - startedAt;
            const drift = t.modelClockMs - wall;
            assert.ok(Math.abs(drift) < 1e-6, `${t.name} 模型时钟漂移 ${drift.toFixed(3)}ms（${switches} 次切换）`);
            assert.equal(t.update, t.originalUpdate, `${t.name}：切回 rAF 的防倒退包装在时间越过后自行撤掉，不随切换次数叠加`);
            assert.ok(!Object.prototype.hasOwnProperty.call(t, 'update'), `${t.name}：撤掉后不留实例属性`);
        }
    }));
}

test('Live2D：退出定时器模式后 lastTime 接着最后一次手动 update；外部暂停后恢复仍沿用 PIXI 的重置', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2DWithPixiV7Tickers(sb);
    mgr.boostInteractiveFPS();
    runWallClock(sb, 1000);
    assert.equal(mgr._idleTickMode, true);
    runWallClock(sb, 50);
    const lastManual = app.ticker.lastTime;
    assert.ok(sb.sandbox.performance.now() > lastManual, '定时器两拍之间');
    mgr.boostInteractiveFPS();
    for (const t of [app.ticker, shared, system]) {
        assert.equal(t.started, true);
        assert.equal(t.lastTime, lastManual, `${t.name}：退出定时器模式不丢上一拍之后的时间`);
    }

    // 外部暂停（pauseRendering）期间时间本就不该推进：恢复时保持 PIXI start() 的重置
    runWallClock(sb, 1000);
    assert.equal(mgr._idleTickMode, true);
    mgr.pauseRendering();
    runWallClock(sb, 5000);
    mgr.resumeRendering();
    const now = sb.sandbox.performance.now();
    for (const t of [app.ticker, shared, system]) {
        assert.equal(t.started, true);
        assert.equal(t.lastTime, now, `${t.name}：暂停后恢复从现在起算，不把暂停时长补进模型时钟`);
    }
}));

test('Live2D：定时器模式下走「确保渲染」的 start(); update(); 之后，rAF 帧时间早于这次 update 也不倒退', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 60 });
    const { mgr, app } = setupLive2DWithPixiV7Tickers(sb);
    mgr.boostInteractiveFPS();
    runWallClock(sb, 1000);
    assert.equal(mgr._idleTickMode, true);
    // 停在距上一拍 ≥10ms 的位置，让下面的 rAF 帧时间落在「上一拍」和「这次 update」之间
    while (sb.sandbox.performance.now() - app.ticker.lastTime < 10) sb.tick(1);
    const t = app.ticker;
    if (!t.started) t.start();
    // 退出后 maxFPS 恢复为地板值，带门时这次 update() 会被挡掉、lastTime 不动；
    // 清掉门，覆盖 update() 真把 lastTime 推到 now 的情况
    t.maxFPS = 0;
    t.update();
    const ensuredAt = sb.sandbox.performance.now();
    assert.equal(t.lastTime, ensuredAt);
    const clockBefore = t.modelClockMs;
    const raf = sb.rafQueue.splice(0, sb.rafQueue.length);
    assert.ok(raf.length > 0, '切回 rAF 后排了下一帧');
    raf.forEach((e) => e.fn(ensuredAt - 5)); // 帧开始时间早于 ensure 路径的 update()
    assert.equal(t.lastTime, ensuredAt, 'lastTime 不倒退');
    sb.rafQueue.splice(0, sb.rafQueue.length).forEach((e) => e.fn(ensuredAt + 12));
    assert.equal(t.modelClockMs - clockBefore, 12, '下一帧只走真实经过的 12ms，不重复计时');
    assert.equal(t.update, t.originalUpdate, '越过后包装撤掉');
}));

test('Live2D：外部 ticker.stop() 退出定时器模式时 app.ticker 不留防倒退包装', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60);
    const { mgr, app } = setupLive2DWithPixiV7Tickers(sb);
    mgr.boostInteractiveFPS();
    runWallClock(sb, 1000);
    assert.equal(mgr._idleTickMode, true);
    mgr.pauseRendering();
    assert.equal(app.ticker.started, false);
    assert.ok(!Object.prototype.hasOwnProperty.call(app.ticker, 'update'), '停住的 ticker 上没有实例 update');
}));

test('Live2D：没有监听者的全局 ticker（PIXI 不重置 lastTime）退出定时器模式也不出错', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 60 });
    const { mgr, system } = setupLive2DWithPixiV7Tickers(sb);
    mgr.boostInteractiveFPS();
    runWallClock(sb, 1000);
    assert.equal(mgr._idleTickMode, true);
    system.hasListeners = false;
    runWallClock(sb, 50);
    const lastManual = system.lastTime;
    mgr.boostInteractiveFPS();
    assert.equal(system.started, true);
    assert.equal(system.lastTime, lastManual, 'start() 没动 lastTime，也不被改动');
    assert.equal(system._requestId, null, '无监听者不排 rAF');
    system.update(sb.sandbox.performance.now());
    assert.equal(system.update, system.originalUpdate, '下一次带时间戳的 update 撤掉包装');
}));

test('契约：Live2D 交互层在坐标变化时记录 _lastPointerMoveAt', () => {
    const src = read('static/live2d/live2d-interaction.js');
    assert.match(src, /if \(pointerMoved\) this\._lastPointerMoveAt = performance\.now\(\);/);
});

test('Live2D：外部 pauseRendering 时不接管；ticker.start() 让位后 governor 自愈回定时器', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 30 });
    sb.measureRefresh(60);
    const { mgr, app } = setupLive2D(sb);
    mgr.pauseRendering();
    mgr.boostInteractiveFPS();
    assert.ok(!mgr._idleTickMode, '外部暂停时不进定时器模式');
    assert.equal(app.ticker.started, false);
    mgr.resumeRendering();
    mgr.boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    app.ticker.start(); // 外部「确保渲染」路径
    assert.equal(mgr._idleTickMode, false, '外部 start() 让位给 rAF');
    mgr._startIdleFpsGovernor();
    sb.tick(1500);
    assert.equal(mgr._idleTickMode, true, 'governor 自愈回定时器模式');
    mgr._stopIdleFpsGovernor();
}));

// ─────────────────────────────────────────────────────────────────────────────
// VRM
// ─────────────────────────────────────────────────────────────────────────────
function setupVRM(sb) {
    sb.load(VRM_MANAGER_SRC, 'vrm-manager.js');
    const mgr = new sb.window.VRMManager();
    mgr.renderer = {};
    mgr.scene = {};
    mgr.camera = {};
    mgr.renders = 0;
    mgr._renderFrame = () => { mgr.renders++; };
    mgr._rafDriver = () => {};
    mgr._animationFrameId = sb.sandbox.requestAnimationFrame(mgr._rafDriver);
    return mgr;
}

test('VRM Pet + 配置低于刷新率：活动态定时器驱动，衰减只换周期', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    const mgr = setupVRM(sb);
    mgr._boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, true);
    assert.equal(mgr._idleTickFps, 45);
    assert.equal(mgr._isLowTickRate(), false, '活动态 45fps 定时器驱动不算低频：medium 隔帧物理照常');
    assert.equal(mgr._animationFrameId, null, 'rAF 链已停');
    sb.tick(22 * 5);
    assert.ok(mgr.renders >= 4, '按 45fps 定时渲染');
    sb.tick(1000);
    assert.equal(mgr._idleTickMode, true);
    assert.equal(mgr._idleTickFps, 30);
    assert.equal(mgr._isLowTickRate(), true, '30fps 地板才绕过隔帧物理');
    // 非标准配置 35fps：隔帧后步长 57ms 超出 50ms clamp，同样按低频全量物理
    mgr._enterIdleTickMode(35);
    assert.equal(mgr._isLowTickRate(), true, '35fps 隔帧步长会超 clamp，不隔帧');
    mgr._enterIdleTickMode(40);
    assert.equal(mgr._isLowTickRate(), false, '40fps 隔帧步长恰为 50ms，允许隔帧');
    assert.equal(sb.rafQueue.length, 0, '从未重新排 rAF');
    // 设置变更事件驱动换周期
    sb.window.targetFrameRate = 24;
    sb.window.vrmManager = mgr;
    sb.window.dispatchEvent({ type: 'neko-frame-rate-changed', detail: { fps: 24 } });
    assert.equal(mgr._idleTickFps, 24);
}));

test('VRM 非 Pet：行为不变——活动态回 rAF', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 45 });
    const mgr = setupVRM(sb);
    sb.tick(0);
    mgr._enterIdleTickMode();
    assert.equal(mgr._idleTickMode, true);
    mgr._boostInteractiveFPS();
    assert.equal(mgr._idleTickMode, false);
    assert.ok(mgr._animationFrameId, '活动态重新排 rAF');
}));

// ─────────────────────────────────────────────────────────────────────────────
// MMD
// ─────────────────────────────────────────────────────────────────────────────
function setupMMD(sb) {
    sb.load(MMD_CORE_SRC, 'mmd-core.js');
    const manager = { _shouldRender: true, _isDisposed: false, renderer: {}, _animationFrameId: null, clock: null, _renderWaiters: [] };
    const core = new sb.window.MMDCore(manager);
    core.renders = 0;
    core._renderFrame = () => { core.renders++; };
    manager._animationFrameId = sb.sandbox.requestAnimationFrame(() => {});
    manager.core = core;
    return { core, manager };
}

test('负数 targetFrameRate 一律归一为 60：Live2D / VRM / MMD 地板都不会算成负数', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: -1 });
    const { mgr, app } = setupLive2D(sb);
    assert.equal(mgr._resolveConfiguredTargetFps(), 60);
    assert.equal(mgr._resolveIdleFps(), 30);
    // setTargetFPS(-1) 不能把负数写进配置 / PIXI maxFPS（豁免分支直接落 maxFPS，PIXI 会钳到 minFPS=10）
    sb.window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__ = true;
    mgr.setTargetFPS(-1);
    assert.equal(sb.window.targetFrameRate, 60, '负数配置归一为 60 再写入');
    assert.equal(app.ticker.maxFPS, 60, '豁免分支写进 PIXI 的是归一化后的值');
    delete sb.window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__;
    assert.doesNotMatch(LIVE2D_CORE_SRC, /this\.pixi_app\.ticker\.maxFPS = window\.targetFrameRate;/, 'initPIXI 也不允许裸写配置进 maxFPS');
    const sb2 = createSandbox({ pet: false, targetFrameRate: -1 });
    const vrm = setupVRM(sb2);
    assert.equal(vrm._resolveConfiguredTargetFps(), 60);
    assert.equal(vrm._resolveIdleFps(), 30);
    vrm._enterIdleTickMode();
    assert.equal(vrm._idleTickFps, 30, '空闲周期不会被负数配置拖到 1fps');
    // rAF 路径也走同一解析，不再直接读 window.targetFrameRate
    const vrmSrc = read('static/vrm/vrm-manager.js');
    const loopBody = vrmSrc.slice(vrmSrc.indexOf('startAnimateLoop() {'), vrmSrc.indexOf('_resolveActiveTimerTickFps() {'));
    assert.match(loopBody, /const configuredFps = this\._resolveConfiguredTargetFps\(\);\s*const targetFps = this\._resolveRafCapFps\(configuredFps\);/);
    assert.doesNotMatch(loopBody, /typeof window\.targetFrameRate === 'number' \? window\.targetFrameRate : 60/, 'rAF 路径不允许绕过归一化直接读配置');
    sb2.window.targetFrameRate = Infinity;
    assert.equal(vrm._resolveConfiguredTargetFps(), 60, 'Infinity 也归一为 60');
    sb2.window.targetFrameRate = NaN;
    assert.equal(vrm._resolveConfiguredTargetFps(), 60, 'NaN 归一为 60');
    const sb3 = createSandbox({ pet: false, targetFrameRate: -1 });
    const { core } = setupMMD(sb3);
    assert.ok([30, 45, 60].includes(core._resolveConfiguredTargetFps()), 'MMD 负数配置退回档位值');
    assert.equal(core._resolveIdleFps(), Math.min(30, core._resolveConfiguredTargetFps()));
}));

test('契约：VRM medium 隔帧物理按实际 delta 兜底，且模式切换不丢/不重复物理时间', () => {
    const src = read('static/vrm/vrm-manager.js');
    assert.match(src, /const VRM_PHYSICS_MAX_STEP_S = 0\.05;/);
    assert.match(src, /if \(quality === 'medium' && canSplitPhysics && !this\._isLowTickRate\(\) && delta \* 2 <= VRM_PHYSICS_MAX_STEP_S\) \{/);
    // 跳过帧累计时间，物理更新时补上（受 clamp）；全量分支冲掉累计并重置奇偶计数
    assert.match(src, /this\._physicsPendingDelta = pendingDelta \+ delta;/);
    assert.match(src, /const physicsStep = Math\.min\(delta \+ pendingDelta, VRM_PHYSICS_MAX_STEP_S\);/);
    assert.match(src, /\} else \{\s*this\._physicsFrameSkip = 0;\s*this\._physicsPendingDelta = 0;\s*this\._updateVrmWithPhysicsStep\(this\.currentModel\.vrm, delta, physicsStep\);/);
    assert.doesNotMatch(src, /vrm\.update\(delta \* 2\)/, '不再盲目用 delta*2，改用实际累计时间');
    assert.doesNotMatch(src.slice(src.indexOf("const quality = window.renderQuality"), src.indexOf('// 6. CursorFollow')), /currentModel\.vrm\.update\(/, '物理段不再直接整体 vrm.update，累计步长只喂弹簧骨');
});

// 把源码里「5. VRM 核心更新」整段 + _updateVrmWithPhysicsStep 抠出来单独执行
// （含 enablePhysics / 画质 / 换模型 / 隔帧分支）
function loadVrmPhysicsStep() {
    const src = read('static/vrm/vrm-manager.js');
    const start = src.indexOf("const quality = window.renderQuality || 'medium';");
    const end = src.indexOf('// 6. CursorFollow', start);
    assert.ok(start > 0 && end > start, 'VRM 物理段定位失败');
    const helperStart = src.indexOf('_updateVrmWithPhysicsStep(vrm, delta, physicsStep) {');
    const helperEnd = src.indexOf('\n    }\n', helperStart) + 7;
    assert.ok(helperStart > 0 && helperEnd > helperStart, '_updateVrmWithPhysicsStep 定位失败');
    const canSplitStart = src.indexOf('_canSplitVrmPhysicsUpdate(vrm) {');
    const canSplitEnd = src.indexOf('\n    }\n', canSplitStart) + 7;
    assert.ok(canSplitStart > 0 && canSplitEnd > canSplitStart, '_canSplitVrmPhysicsUpdate 定位失败');
    const sliceMethod = (name) => {
        const s = src.indexOf(name + '(vrm, delta) {');
        const e = src.indexOf('\n    }\n', s) + 7;
        assert.ok(s > 0 && e > s, name + ' 定位失败');
        return 'this.' + name + ' = function ' + src.slice(s, e) + ';';
    };
    const helperSrc = 'this._canSplitVrmPhysicsUpdate = function ' + src.slice(canSplitStart, canSplitEnd) + ';'
        + sliceMethod('_updateVrmMaterials')
        + sliceMethod('_updateVrmPoseComponents')
        + sliceMethod('_updateVrmWithoutPhysics')
        + 'this._updateVrmWithPhysicsStep = function ' + src.slice(helperStart, helperEnd) + ';';
    return new Function('delta', 'window', 'VRM_PHYSICS_MAX_STEP_S', helperSrc + src.slice(start, end));
}
function makeVrmPhysicsCtx({ componentApi = true } = {}) {
    const ctx = { enablePhysics: true, _isLowTickRate: () => false, simulated: 0, lookAtTime: [], materialTime: [], order: [], currentModel: null };
    ctx.newModel = () => {
        const vrm = {
            update: (d) => { ctx.simulated += d; ctx.lookAtTime.push(d); ctx.materialTime.push(d); ctx.order.push('whole'); },
            lookAt: { update: (d) => { ctx.lookAtTime.push(d); ctx.order.push('lookAt'); } },
            expressionManager: { update() { ctx.order.push('expression'); } },
            humanoid: { update() { ctx.order.push('humanoid'); } },
            nodeConstraintManager: { update() { ctx.order.push('constraint'); } },
            materials: [{ update: (d) => { ctx.materialTime.push(d); ctx.order.push('material'); } }, {}],
        };
        if (componentApi) vrm.springBoneManager = { update: (d) => { ctx.simulated += d; ctx.order.push('spring'); } };
        ctx.currentModel = { vrm };
    };
    ctx.newModel();
    return ctx;
}

test('VRM 分量更新顺序与 three-vrm VRM.update 一致：humanoid → lookAt → 表情 → 约束 → 弹簧骨 → 材质', () => {
    const run = loadVrmPhysicsStep();
    const ctx = makeVrmPhysicsCtx();
    run.call(ctx, 0.0167, { renderQuality: 'high' }, 0.05);
    assert.deepEqual(ctx.order, ['humanoid', 'lookAt', 'expression', 'constraint', 'spring', 'material'],
        '约束/弹簧骨必须在 LookAt 驱动的姿态之后');
    // medium 跳过物理的帧：除弹簧骨外全部分量照常按库顺序推进（约束不能只在另一帧跑）
    const skipCtx = makeVrmPhysicsCtx();
    run.call(skipCtx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.deepEqual(skipCtx.order, ['humanoid', 'lookAt', 'expression', 'constraint', 'material'], '跳过帧只少弹簧骨');
    run.call(skipCtx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.deepEqual(skipCtx.order.slice(5), ['humanoid', 'lookAt', 'expression', 'constraint', 'spring', 'material'], '更新帧完整顺序');
    // 打包版 three-vrm 里 VRM.update 的真实顺序，防止库升级后两边漂移
    const lib = read('static/libs/three-vrm.module.min.js');
    assert.match(lib, /update\(e\)\{super\.update\(e\),this\.nodeConstraintManager&&this\.nodeConstraintManager\.update\(\),this\.springBoneManager&&this\.springBoneManager\.update\(e\),this\.materials&&this\.materials\.forEach/);
});

test('VRM 物理隔帧：累计步长只喂弹簧骨，LookAt / MToon 材质每 tick 只推进当前 delta', () => {
    const run = loadVrmPhysicsStep();
    const ctx = makeVrmPhysicsCtx();
    const frames = [0.0167, 0.0167, 0.0333, 0.0167, 0.0167];
    let elapsed = 0;
    for (const d of frames) { elapsed += d; run.call(ctx, d, { renderQuality: 'medium' }, 0.05); }
    assert.ok(Math.abs(ctx.simulated - elapsed) < 1e-9, '弹簧骨物理时间守恒');
    assert.deepEqual(ctx.lookAtTime, frames, 'LookAt 每帧只拿当前 delta，不吃累计');
    // MToon 材质 UV 动画按 delta 累积：跳过物理的帧也要推进，每帧拿当前 delta 而不是累计步长
    assert.deepEqual(ctx.materialTime, frames, '材质每帧按当前 delta 推进，动画不会变成半速');
    // 旧版 three-vrm（无 springBoneManager.update）：不隔帧，每帧整体 update(delta)，
    // 累计步长不会喂给 lookAt/材质
    const legacy = makeVrmPhysicsCtx({ componentApi: false });
    const legacyFrames = [0.0167, 0.0167, 0.0167];
    for (const d of legacyFrames) run.call(legacy, d, { renderQuality: 'medium' }, 0.05);
    assert.ok(Math.abs(legacy.simulated - 0.0501) < 1e-9, '旧版每帧都整体 update');
    assert.deepEqual(legacy.lookAtTime, legacyFrames, '旧版 lookAt 每帧只拿当前 delta');
    assert.equal(legacy._physicsPendingDelta, 0, '旧版从不累计跳帧时间');
});

test('VRM 物理隔帧：隔帧↔全量切换时物理时间总和 = 实际经过时间', () => {
    const run = loadVrmPhysicsStep();
    const ctx = makeVrmPhysicsCtx();
    // 60fps 隔帧 4 帧 → 切到 30fps 全量 3 帧 → 回 60fps 隔帧 4 帧
    const frames = [0.0167, 0.0167, 0.0167, 0.0167, 0.0333, 0.0333, 0.0333, 0.0167, 0.0167, 0.0167, 0.0167];
    let elapsed = 0;
    for (const d of frames) { elapsed += d; run.call(ctx, d, { renderQuality: 'medium' }, 0.05); }
    assert.ok(Math.abs(ctx.simulated - elapsed) < 1e-9, `物理累计 ${ctx.simulated} 应等于实际 ${elapsed}`);
});

test('VRM 物理隔帧：关物理 / 切 low / 换模型都清掉累计时间，不把旧会话的 pending 套到新模型', () => {
    const run = loadVrmPhysicsStep();
    // 奇数帧后关闭物理 → pending 必须被清
    let ctx = makeVrmPhysicsCtx();
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.equal(ctx._physicsPendingDelta, 0.0167, '奇数帧累计了一帧');
    ctx.enablePhysics = false;
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.equal(ctx._physicsPendingDelta, 0, '关物理时清掉累计');
    assert.equal(ctx._physicsFrameSkip, 0);
    ctx.enablePhysics = true;
    ctx.simulated = 0;
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.equal(ctx.simulated, 0, '重新开启后首帧是干净的「跳过」帧，不会套旧 pending');
    // 奇数帧后切 low 画质 → 同样清
    ctx = makeVrmPhysicsCtx();
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    run.call(ctx, 0.0167, { renderQuality: 'low' }, 0.05);
    assert.equal(ctx._physicsPendingDelta, 0, 'low 画质清掉累计');
    // 奇数帧后换模型 → 新模型不吃旧 pending
    ctx = makeVrmPhysicsCtx();
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    ctx.newModel();
    ctx.simulated = 0;
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.equal(ctx.simulated, 0, '换模型后首帧不套旧 pending');
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05);
    assert.ok(Math.abs(ctx.simulated - 0.0334) < 1e-9, '新模型自己的两帧才合并更新');
    // 累计后的步长仍受 50ms clamp
    ctx = makeVrmPhysicsCtx();
    run.call(ctx, 0.0167, { renderQuality: 'medium' }, 0.05); // 绑定模型 + 奇数帧
    ctx._physicsPendingDelta = 0.05; // 模拟极端累计
    ctx.simulated = 0;
    run.call(ctx, 0.02, { renderQuality: 'medium' }, 0.05);
    assert.equal(ctx.simulated, 0.05, '步长不超过防爆上限');
});

test('MMD：目标帧率接到设置里的帧率滑块，0 = 不限帧', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 45 });
    const { core } = setupMMD(sb);
    assert.equal(core.targetFPS, 45);
    sb.window.targetFrameRate = 0;
    core._refreshTargetFps();
    assert.equal(core.targetFPS, 0);
    assert.equal(core.frameTime, 0, '不限帧时 rAF 路径不再跳帧');
    assert.equal(core._resolveIdleFps(), 30);
    sb.window.targetFrameRate = 24;
    core._refreshTargetFps();
    assert.equal(core.frameTime, 1000 / 24);
    assert.equal(core._resolveIdleFps(), 24);
    delete sb.window.targetFrameRate;
    for (const [mode, expected] of [['low', 30], ['medium', 45], ['high', 60]]) {
        core.performanceMode = mode;
        core._refreshTargetFps();
        assert.equal(core.targetFPS, expected, `没有该配置时退回 performanceMode=${mode} 的档位`);
    }
}));

test('MMD rAF 路径：不限帧后切回有限帧率，节流状态不能被 NaN 污染', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 0 });
    const { core, manager } = setupMMD(sb);
    manager._animationFrameId = null;
    // 不限帧：连续 rAF 全部渲染
    core._render();
    sb.pumpFrames(1, 60);
    sb.pumpFrames(1, 60);
    assert.equal(core.renders, 3, '不限帧时每个 rAF 都渲染');
    assert.ok(Number.isFinite(core.lastFrameTime), 'lastFrameTime 必须是有限数');
    // 切回 45fps：60Hz 推 60 帧应只渲染约 45 帧
    sb.window.targetFrameRate = 45;
    const before = core.renders;
    sb.pumpFrames(60, 60);
    const rendered = core.renders - before;
    assert.ok(rendered >= 40 && rendered <= 50, `切回 45fps 后应重新节流，实际渲染 ${rendered}/60`);
    assert.ok(Number.isFinite(core.lastFrameTime));
}));

test('MMD Pet 没有 window.targetFrameRate 时，总闸按 MMD 自己的 performanceMode 回退值走，不按 60', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: undefined });
    delete sb.window.targetFrameRate;
    sb.measureRefresh(144);
    const { core } = setupMMD(sb);
    assert.equal(sb.window.nekoFramePacing.activeTimerTickFps(), 60, '总闸自己的缺省是 60');
    for (const [mode, expected] of [['low', 30], ['medium', 45], ['high', 60]]) {
        core.performanceMode = mode;
        assert.equal(core._resolveConfiguredTargetFps(), expected);
        assert.equal(core._resolveActiveTimerTickFps(), expected, `MMD 把自己解析的 ${mode} 档帧率交给总闸`);
        core._boostInteractiveFPS();
        assert.equal(core._idleTickFps, expected, `活动态定时器周期 = ${mode} 档回退值`);
    }
}));

test('MMD / VRM：时间戳 0 是合法的活动时间戳，不能被当成未设置', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 45 });
    const { core, manager } = setupMMD(sb);
    assert.equal(sb.sandbox.performance.now(), 0);
    assert.equal(core._hasRenderActivity(), false, '未设置时不算活动');
    manager._lastInteractionBoostTs = 0;
    assert.equal(core._hasRenderActivity(), true, 'MMD 交互时间戳 0 在保持期内算活动');
    manager._lastInteractionBoostTs = undefined;
    manager.cursorFollow = { enabled: true, _lastPointerMoveTs: 0, _targetYaw: 0, _currentYaw: 0, _targetPitch: 0, _currentPitch: 0 };
    assert.equal(core._hasRenderActivity(), true, 'MMD 光标时间戳 0 在保持期内算活动');
    sb.tick(901);
    assert.equal(core._hasRenderActivity(), false);

    const sb2 = createSandbox({ pet: false, targetFrameRate: 45 });
    const vrm = setupVRM(sb2);
    assert.equal(vrm._hasRenderActivity(), false);
    for (const key of ['_lastLookAtPointerMoveAt', '_lastCameraChangeAt', '_lastInteractionBoostTs']) {
        vrm[key] = 0;
        assert.equal(vrm._hasRenderActivity(), true, `VRM ${key} = 0 在保持期内算活动`);
        vrm[key] = undefined;
    }
    // CursorFollow 构造/重置时 _lastPointerMoveAt = 0 表示「尚无指针输入」，必须靠 _hasPointerInput 区分
    vrm._cursorFollow = { isEnabled: () => true, _lastPointerMoveAt: 0, _hasPointerInput: false };
    assert.equal(vrm._hasRenderActivity(), false, '尚无指针输入时 0 不算活动');
    vrm._cursorFollow = { isEnabled: () => true, _lastPointerMoveAt: 0, _hasPointerInput: true };
    assert.equal(vrm._hasRenderActivity(), true, '有过指针输入时时间戳 0 在保持期内算活动');
    sb2.tick(901);
    assert.equal(vrm._hasRenderActivity(), false);
}));

test('MMD：交互升帧的 900ms 保持期到期后活动判定失效', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 45 });
    const { core, manager } = setupMMD(sb);
    sb.tick(1000);
    manager._lastInteractionBoostTs = sb.sandbox.performance.now();
    assert.equal(core._hasRenderActivity(), true);
    sb.tick(899);
    assert.equal(core._hasRenderActivity(), true, '保持期内仍算活动');
    sb.tick(2);
    assert.equal(core._hasRenderActivity(), false, '过了 900ms 保持期失效');
    // 光标跟随时间戳同一套窗口
    manager.cursorFollow = { enabled: true, _lastPointerMoveTs: sb.sandbox.performance.now(), _targetYaw: 0, _currentYaw: 0, _targetPitch: 0, _currentPitch: 0 };
    assert.equal(core._hasRenderActivity(), true);
    sb.tick(901);
    assert.equal(core._hasRenderActivity(), false);
}));

test('MMD Pet + 配置低于刷新率：活动态定时器驱动，衰减只换周期', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    const { core, manager } = setupMMD(sb);
    core._boostInteractiveFPS();
    assert.equal(core._idleTickMode, true);
    assert.equal(core._idleTickFps, 45);
    assert.equal(manager._animationFrameId, null);
    sb.tick(22 * 5);
    assert.ok(core.renders >= 4);
    sb.tick(1000);
    assert.equal(core._idleTickMode, true);
    assert.equal(core._idleTickFps, 30);
    assert.equal(sb.rafQueue.length, 0, '从未重新排 rAF');
    sb.window.targetFrameRate = 24;
    sb.window.mmdManager = manager;
    sb.window.dispatchEvent({ type: 'neko-frame-rate-changed', detail: { fps: 24 } });
    assert.equal(core._idleTickFps, 24);
}));

// ─────────────────────────────────────────────────────────────────────────────
// 契约：页面侧对「坐标没变的 pointermove」不再升帧 / 不再算作光标活动
// ─────────────────────────────────────────────────────────────────────────────
test('契约：Live2D 交互层只有坐标真变了才升帧', () => {
    const src = read('static/live2d/live2d-interaction.js');
    const movedDecl = src.indexOf('const pointerMoved = pointer.x !== this._lastMouseX || pointer.y !== this._lastMouseY;');
    const lastAssign = src.indexOf('this._lastMouseX = pointer.x;');
    assert.ok(movedDecl >= 0, 'pointerMoved 判定缺失');
    assert.ok(movedDecl < lastAssign, 'pointerMoved 必须在覆盖 _lastMouseX 之前计算');
    const guarded = src.match(/pointerMoved && typeof this\.boostLinuxX11InteractiveFPS === 'function'/g) || [];
    assert.equal(guarded.length, 2, '悬停范围内 + 全屏跟踪 两处升帧都必须受 pointerMoved 门控');
    assert.doesNotMatch(
        src.slice(src.indexOf('const onPointerMove = '), src.indexOf('const onBlur = ')),
        /\n\s+if \(typeof this\.boostLinuxX11InteractiveFPS === 'function'\) \{\n\s+this\.boostLinuxX11InteractiveFPS\(\);/,
        'onPointerMove 内不允许残留无门控的升帧'
    );
});

test('VRM legacy 视线跟随（CursorFollow 未加载）：重复坐标不续期活动保持期', withMockTimers(() => {
    const sb = createSandbox({ pet: false, targetFrameRate: 60 });
    const vrm = setupVRM(sb);
    vrm.isMouseTrackingEnabled = () => true;
    vrm._setLookAtTargetByMouse = () => {};
    // 从源码抠出 legacy handler 的闭包体，避免依赖 THREE 初始化
    const src = VRM_MANAGER_SRC;
    const start = src.indexOf('this._mouseMoveHandler = (event) => {');
    const end = src.indexOf('document.addEventListener(\'mousemove\', this._mouseMoveHandler', start);
    assert.ok(start > 0 && end > start, 'legacy handler 定位失败');
    // 必须在沙盒 realm 里构造，handler 读到的才是沙盒的 performance.now()
    const install = vm.runInContext('(function () {' + src.slice(start, end) + '})', sb.sandbox);
    install.call(vrm);
    sb.tick(1000);
    vrm._mouseMoveHandler({ clientX: 10, clientY: 20 });
    const first = vrm._lastLookAtPointerMoveAt;
    assert.equal(first, sb.sandbox.performance.now(), '首次坐标记录时间戳');
    sb.tick(500);
    vrm._mouseMoveHandler({ clientX: 10, clientY: 20 });
    assert.equal(vrm._lastLookAtPointerMoveAt, first, '同坐标重复事件不刷新时间戳');
    sb.tick(500);
    assert.equal(vrm._hasRenderActivity(), false, '保持期过后可进空闲');
    vrm._mouseMoveHandler({ clientX: 11, clientY: 20 });
    assert.equal(vrm._lastLookAtPointerMoveAt, sb.sandbox.performance.now(), '坐标一变立即刷新');
    assert.equal(vrm._hasRenderActivity(), true);
}));

test('契约：VRM / MMD 光标跟随对坐标没变的事件不刷新活动时间戳', () => {
    const vrm = read('static/vrm/vrm-cursor-follow.js');
    assert.match(vrm, /const moved = e\.clientX !== this\._rawMouseX \|\| e\.clientY !== this\._rawMouseY;/);
    assert.match(vrm, /if \(moved\) this\._lastPointerMoveAt = now;/);
    assert.doesNotMatch(vrm, /\n\s+this\._lastPointerMoveAt = now;\n/, '不允许无条件刷新');
    const mmd = read('static/mmd/mmd-cursor-follow.js');
    assert.match(mmd, /if \(e\.clientX !== this\._rawMouseX \|\| e\.clientY !== this\._rawMouseY\) \{\n\s+this\._lastPointerMoveTs = now;/);
    assert.equal((mmd.match(/this\._lastPointerMoveTs = now;/g) || []).length, 1, '时间戳只在坐标变化门控内刷新一处');
});

test('契约：frame-pacing.js 只在 index.html（Pet 窗口页面）加载', () => {
    const index = read('templates/index.html');
    assert.match(index, /<script src="\/static\/frame-pacing\.js\?v=\{\{ static_asset_version \}\}"><\/script>/);
    for (const tpl of ['model_manager.html', 'character_card_manager.html', 'card_maker.html', 'chat.html']) {
        assert.doesNotMatch(read('templates/' + tpl), /frame-pacing\.js/, tpl + ' 不应加载帧率总闸');
    }
});

// ─────────────────────────────────────────────────────────────────────────────
// rAF 帧率上限：配置帧率 ≥ 显示器刷新率时放宽软件节流（frame-pacing.rafCapFps）
// ─────────────────────────────────────────────────────────────────────────────
const PIXI_MIN_SRC = read('static/libs/pixi.min.js');
const PIXI_TARGET_FPMS = 0.06;

// 按 PIXI 7.4.3 Ticker 的 update / maxFPS / minFPS / _requestIfNeeded 逐句复刻（下面有契约断言防漂移）。
// 只记录每次真正 emit 的 deltaMS，用来数跳帧和双倍 delta。
class Pixi743Ticker {
    constructor() {
        this._minElapsedMS = 0;
        this._maxElapsedMS = 100; // minFPS 默认 10
        this.minFPS = 10;
        this.speed = 1;
        this.lastTime = -1;
        this._lastFrame = -1;
        this.emitted = [];
    }
    get maxFPS() { return this._minElapsedMS ? Math.round(1e3 / this._minElapsedMS) : 0; }
    set maxFPS(t) {
        if (t === 0) this._minElapsedMS = 0;
        else { const e = Math.max(this.minFPS, t); this._minElapsedMS = 1 / (e / 1e3); }
    }
    start(now) { this.lastTime = now; this._lastFrame = this.lastTime; }
    update(t) {
        let e;
        if (t > this.lastTime) {
            e = this.elapsedMS = t - this.lastTime;
            if (e > this._maxElapsedMS) e = this._maxElapsedMS;
            e *= this.speed;
            if (this._minElapsedMS) {
                const n = (t - this._lastFrame) | 0;
                if (n < this._minElapsedMS) return;
                this._lastFrame = t - n % this._minElapsedMS;
            }
            this.deltaMS = e;
            this.deltaTime = this.deltaMS * PIXI_TARGET_FPMS;
            this.emitted.push(e);
        } else {
            this.deltaTime = this.deltaMS = this.elapsedMS = 0;
        }
        this.lastTime = t;
    }
}

// 可复现的伪随机（mulberry32）
function seededRandom(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6D2B79F5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

// 合成 vsync 时间戳：第 i 帧 = start + i × 1000/hz + U(-jitter, +jitter)，不累积漂移
function syntheticVsync({ hz, jitterMs, frames, seed, start = 10000 }) {
    const rand = seededRandom(seed);
    const period = 1000 / hz;
    const out = [];
    for (let i = 1; i <= frames; i++) out.push(start + i * period + (rand() * 2 - 1) * jitterMs);
    return out;
}

// 把时间戳喂给 PIXI 7.4.3 ticker，返回跳帧率（rAF 回调里整帧没执行的比例）和双倍 delta 帧数
function pixiSkipStats(maxFPS, stamps, start = 10000) {
    const ticker = new Pixi743Ticker();
    ticker.maxFPS = maxFPS;
    ticker.start(start);
    stamps.forEach((t) => ticker.update(t));
    const skipped = stamps.length - ticker.emitted.length;
    const period = (stamps[stamps.length - 1] - start) / stamps.length;
    const doubled = ticker.emitted.filter((d) => d > period * 1.5).length;
    return { skipRate: skipped / stamps.length, skipped, doubled, frames: stamps.length };
}

test('契约：Pixi743Ticker 复刻与 static/libs/pixi.min.js 的 Ticker 实现一致', () => {
    assert.ok(PIXI_MIN_SRC.includes('if(t>this.lastTime){if(e=this.elapsedMS=t-this.lastTime,e>this._maxElapsedMS&&(e=this._maxElapsedMS),e*=this.speed,this._minElapsedMS){const n=t-this._lastFrame|0;if(n<this._minElapsedMS)return;this._lastFrame=t-n%this._minElapsedMS}this.deltaMS=e,this.deltaTime=this.deltaMS*'),
        'update：截成整数 ms 比较，跳帧时直接 return、不更新 lastTime');
    assert.ok(PIXI_MIN_SRC.includes('else this.deltaTime=this.deltaMS=this.elapsedMS=0;this.lastTime=t}'));
    assert.ok(PIXI_MIN_SRC.includes('set maxFPS(t){if(t===0)this._minElapsedMS=0;else{const e=Math.max(this.minFPS,t);this._minElapsedMS=1/(e/1e3)}}'));
    assert.ok(PIXI_MIN_SRC.includes('get maxFPS(){return this._minElapsedMS?Math.round(1e3/this._minElapsedMS):0}'));
});

test('frame-pacing.rafCapFps：仅 Pet + 刷新率已测出 + 配置 ≥ 刷新率×0.9 时放宽到 max(配置, 刷新率×1.5)，其余原样', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const pacing = sb.window.nekoFramePacing;
    assert.equal(pacing.rafCapFps(60), 60, '刷新率未测出：原样');
    sb.measureRefresh(60);
    assert.equal(pacing.getDisplayRefreshHz(), 60);
    assert.equal(pacing.RAF_CAP_HEADROOM_RATIO, 1.5);
    assert.equal(pacing.rafCapFps(60), 90, '60@60Hz → 90');
    assert.equal(pacing.rafCapFps(), 90, '不传参数按 window.targetFrameRate');
    assert.equal(pacing.rafCapFps(75), 90, '高于刷新率 → 90');
    assert.equal(pacing.rafCapFps(240), 240, '配置本身已高于余量：原样（只放宽、不收紧）');
    assert.equal(pacing.rafCapFps(59), 90, '略低于刷新率（≥ ×0.9，总闸留在 rAF）：同样放宽');
    assert.equal(pacing.rafCapFps(54), 90, '恰好 ×0.9：放宽');
    assert.equal(pacing.rafCapFps(53), 53, '明显低于刷新率（总闸切定时器）：原样');
    assert.equal(pacing.activeTimerTickFps(53), 53, '两道判定同一阈值：原样的一侧正好是定时器驱动');
    assert.equal(pacing.activeTimerTickFps(54), null, '放宽的一侧正好留在 rAF');
    assert.equal(pacing.rafCapFps(30), 30, '地板 30：原样');
    assert.equal(pacing.rafCapFps(0), 0, '0 = 不限帧：原样');
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.measureRefresh(144);
    assert.equal(pacing.rafCapFps(60), 60, '144Hz 屏上 60 低于刷新率：原样');
    assert.equal(pacing.rafCapFps(144), 216);
    assert.equal(pacing.rafCapFps(165), 216);

    const np = createSandbox({ pet: false, targetFrameRate: 60 });
    np.measureRefresh(60);
    assert.equal(np.window.nekoFramePacing.rafCapFps(60), 60, '非 Pet：原样');
    // 即使非 Pet 页面手动调了导出的测量函数、刷新率有值，也不改上限
    np.window.nekoFramePacing.measureDisplayRefreshRate();
    np.pumpFrames(30, 60);
    assert.equal(np.window.nekoFramePacing.getDisplayRefreshHz(), 60);
    assert.equal(np.window.nekoFramePacing.rafCapFps(60), 60, '非 Pet 有刷新率也原样');
}));

test('frame-pacing.rafCapFps：换屏后、新屏重测完成前不信旧刷新率；跨换屏的旧采样也不算', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const pacing = sb.window.nekoFramePacing;
    sb.measureRefresh(60);
    assert.equal(pacing.rafCapFps(60), 90);
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    assert.equal(pacing.rafCapFps(60), 60, '换屏事件一到就回到配置上限（新屏可能是 144Hz）');
    assert.equal(pacing.getDisplayRefreshHz(), 60, 'activeTimerTickFps 用的旧值本 PR 不动');
    sb.measureRefresh(60);
    assert.equal(pacing.rafCapFps(60), 90, '新屏重测完成后恢复');

    // 一轮采样在换屏前开始、换屏后结束：时间戳混了旧屏，不能作数
    pacing.measureDisplayRefreshRate();
    sb.pumpFrames(5, 60);
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.pumpFrames(30, 60); // 旧一轮采满结束
    assert.equal(pacing.rafCapFps(60), 60, '旧一轮的结果不解除换屏后的保守态');
    sb.measureRefresh(60); // 换屏后排的那轮
    assert.equal(pacing.rafCapFps(60), 90);

    // 换屏后重测超时：刷新率作废 → 原样
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    sb.tick(3000);
    assert.equal(pacing.rafCapFps(60), 60);
}));

test('Live2D Pet：60@60Hz 的 rAF 上限放宽；PIXI 7.4.3 合成时间戳下跳帧率从 >0 降到 0', withMockTimers((t) => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60);
    const { mgr, app, shared, system } = setupLive2D(sb);
    mgr._isDraggingModel = true; // 持续活动，留在 rAF
    mgr.boostInteractiveFPS();
    assert.ok(!mgr._idleTickMode, '留在 rAF');
    assert.equal(app.ticker.maxFPS, 90);
    assert.equal(shared.maxFPS, 90);
    assert.equal(system.maxFPS, 90);

    const rows = [];
    for (const jitterMs of [0.1, 1, 2]) {
        let beforeSkipped = 0, afterSkipped = 0, afterDoubled = 0, total = 0;
        for (let seed = 1; seed <= 5; seed++) {
            const stamps = syntheticVsync({ hz: 60, jitterMs, frames: 3600, seed });
            const before = pixiSkipStats(60, stamps);
            const after = pixiSkipStats(app.ticker.maxFPS, stamps);
            beforeSkipped += before.skipped;
            afterSkipped += after.skipped;
            afterDoubled += after.doubled;
            total += stamps.length;
        }
        rows.push(`PIXI 60Hz ±${jitterMs}ms: 改前 maxFPS=60 跳帧 ${(100 * beforeSkipped / total).toFixed(2)}%，改后 maxFPS=${app.ticker.maxFPS} 跳帧 ${(100 * afterSkipped / total).toFixed(2)}%（${total} 帧）`);
        assert.ok(beforeSkipped / total > 0.005, `复刻的 PIXI 节流在 ±${jitterMs}ms 下应能复现跳帧`);
        assert.equal(afterSkipped, 0, `±${jitterMs}ms：改后不跳帧`);
        assert.equal(afterDoubled, 0, `±${jitterMs}ms：改后没有双倍 delta`);
    }
    rows.forEach((r) => (t && t.diagnostic ? t.diagnostic(r) : console.log(r)));
}));

test('刷新率被低估时（采样时 rAF 被降频），放宽后的上限仍有界：远低于真实刷新率', withMockTimers((t) => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(60); // 实际是 120/144Hz 屏，采样时被降到 60
    const { mgr, app } = setupLive2D(sb);
    mgr._isDraggingModel = true;
    mgr.boostInteractiveFPS();
    const cap = app.ticker.maxFPS;
    assert.equal(cap, 90);
    for (const hz of [120, 144]) {
        const stamps = syntheticVsync({ hz, jitterMs: 0.5, frames: hz * 30, seed: 3 });
        const stats = pixiSkipStats(cap, stamps);
        const fps = (stamps.length - stats.skipped) / 30;
        const msg = `低估：真实 ${hz}Hz、cap ${cap} → 实际 ${fps.toFixed(1)}fps（配置 60）`;
        (t && t.diagnostic ? t.diagnostic(msg) : console.log(msg));
        assert.ok(fps <= 90.5, msg + '：不超过刷新率×1.5');
        assert.ok(fps < hz * 0.8, msg + '：明显低于真实刷新率（不是直接放开到 0）');
    }
}));

test('Live2D：配置明显低于刷新率 / 刷新率未知 / 非 Pet 时 maxFPS 照旧', withMockTimers(() => {
    // 45 < 60×0.9：活动态由总闸切定时器驱动；退出定时器时要恢复的 rAF 上限保持 45
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    const a = setupLive2D(sb);
    a.mgr._isDraggingModel = true;
    a.mgr.boostInteractiveFPS();
    assert.equal(a.mgr._idleTickMode, true, '定时器驱动');
    assert.equal(a.mgr._idleTickSavedMaxFPS, 45, '待恢复的 rAF 上限照旧');
    assert.equal(a.mgr._resolveRafCapFps(30), 30, '地板 30 照旧');

    const sb2 = createSandbox({ pet: true, targetFrameRate: 60 }); // 不测刷新率
    const b = setupLive2D(sb2);
    b.mgr._isDraggingModel = true;
    b.mgr.boostInteractiveFPS();
    assert.equal(b.app.ticker.maxFPS, 60, '刷新率未知：保持配置上限');
    assert.equal(b.shared.maxFPS, 60);

    const sb3 = createSandbox({ pet: false, targetFrameRate: 60 });
    sb3.measureRefresh(60);
    const c = setupLive2D(sb3);
    c.mgr._isDraggingModel = true;
    c.mgr.boostInteractiveFPS();
    assert.equal(c.app.ticker.maxFPS, 60, '非 Pet：保持配置上限');
}));

test('Live2D：刷新率测出 / 换屏时，rAF 驱动中的上限由 governor 重判', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const { mgr, app, shared } = setupLive2D(sb);
    mgr._isDraggingModel = true;
    mgr._startIdleFpsGovernor();
    assert.ok(!mgr._idleTickMode, '留在 rAF');
    assert.equal(app.ticker.maxFPS, 60, '刷新率测出前：保持配置上限');
    sb.measureRefresh(60);
    sb.tick(300);
    assert.equal(app.ticker.maxFPS, 90, '测出 60Hz 后一个 governor 周期内放宽');
    assert.equal(shared.maxFPS, 90);

    // 活动刚停、还在衰减保持期（rAF 驱动、无活动）也要重判
    mgr._isDraggingModel = false;
    mgr.boostInteractiveFPS();
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(300);
    assert.ok(!mgr._idleTickMode, '仍在保持期');
    assert.equal(app.ticker.maxFPS, 60, '换屏后未重测：回到配置上限');
    assert.equal(shared.maxFPS, 60);
    mgr._stopIdleFpsGovernor();
}));

test('契约：initPIXI 写 maxFPS 也经过刷新率判定', () => {
    assert.match(LIVE2D_CORE_SRC, /this\.pixi_app\.ticker\.maxFPS = this\._resolveRafCapFps\(this\._resolveConfiguredTargetFps\(\)\);/);
});

test('MMD Pet：60@60Hz rAF 路径放宽节流；合成抖动时间戳下改前跳帧、改后每帧都渲染', withMockTimers((t) => {
    // MMD 节流按精确帧间隔比较并对齐时间格：是否跳帧取决于每帧抖动与起始帧相位的先后，
    // 所以按多个种子取平均。改前 = 同一代码在没有刷新率判定时（非 Pet）的行为。
    const rows = [];
    for (const jitterMs of [0.1, 1, 2]) {
        const sum = { before: 0, after: 0, total: 0 };
        for (let seed = 1; seed <= 5; seed++) {
            for (const pet of [false, true]) {
                const sb = createSandbox({ pet, targetFrameRate: 60 });
                sb.measureRefresh(60);
                const { core, manager } = setupMMD(sb);
                sb.rafQueue.splice(0);
                manager._animationFrameId = null;
                const start = sb.now();
                core._render();
                if (pet) assert.equal(core.frameTime, 1000 / 90, 'Pet 60@60Hz：frameTime 按 90 算');
                const rendersBefore = core.renders;
                const stamps = syntheticVsync({ hz: 60, jitterMs, frames: 3600, seed, start });
                sb.pumpAt(stamps);
                sum[pet ? 'after' : 'before'] += stamps.length - (core.renders - rendersBefore);
                if (pet) sum.total += stamps.length;
            }
        }
        assert.ok(sum.before / sum.total > 0.005, `±${jitterMs}ms：未判定（非 Pet）时 MMD 节流也会跳帧，实际 ${sum.before}`);
        assert.equal(sum.after, 0, `±${jitterMs}ms：改后不跳帧`);
        rows.push(`MMD 60Hz ±${jitterMs}ms: 节流 60 跳帧 ${(100 * sum.before / sum.total).toFixed(2)}%，放宽到 90 跳帧 ${(100 * sum.after / sum.total).toFixed(2)}%（${sum.total} 帧）`);
    }
    rows.forEach((r) => (t && t.diagnostic ? t.diagnostic(r) : console.log(r)));
}));

test('MMD：配置低于刷新率时照旧节流；刷新率变化时 frameTime 跟着重判，targetFPS 保持配置值', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 45 });
    sb.measureRefresh(60);
    const { core } = setupMMD(sb);
    core._refreshTargetFps();
    assert.equal(core.targetFPS, 45);
    assert.equal(core.frameTime, 1000 / 45);
    sb.window.targetFrameRate = 60;
    core._refreshTargetFps();
    assert.equal(core.targetFPS, 60);
    assert.equal(core.frameTime, 1000 / 90);
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    core._refreshTargetFps();
    assert.equal(core.targetFPS, 60);
    assert.equal(core.frameTime, 1000 / 60, '换屏未重测：配置不变也要恢复节流');
}));

test('VRM：rAF 上限与 Live2D / MMD 同一判定', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const mgr = setupVRM(sb);
    assert.equal(mgr._resolveRafCapFps(60), 60, '刷新率未知：原样');
    sb.measureRefresh(60);
    assert.equal(mgr._resolveRafCapFps(60), 90);
    assert.equal(mgr._resolveRafCapFps(45), 45);
    assert.equal(mgr._resolveRafCapFps(0), 0);
    const np = createSandbox({ pet: false, targetFrameRate: 60 });
    np.measureRefresh(60);
    assert.equal(setupVRM(np)._resolveRafCapFps(60), 60);
}));

test('刷新率被测高 1Hz（60Hz 屏测成 61）：配置 60 仍放宽，不落进「留在 rAF 但不放宽」的空档', withMockTimers((t) => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    sb.measureRefresh(61);
    const pacing = sb.window.nekoFramePacing;
    assert.equal(pacing.getDisplayRefreshHz(), 61);
    assert.equal(pacing.activeTimerTickFps(60), null, '60 ≥ 61×0.9：留在 rAF');
    assert.equal(pacing.rafCapFps(60), 92, '同时放宽到 round(61×1.5)');
    const { mgr, app } = setupLive2D(sb);
    mgr._isDraggingModel = true;
    mgr.boostInteractiveFPS();
    assert.ok(!mgr._idleTickMode);
    assert.equal(app.ticker.maxFPS, 92);
    const stamps = syntheticVsync({ hz: 60, jitterMs: 1, frames: 3600, seed: 11 });
    const before = pixiSkipStats(60, stamps);
    const after = pixiSkipStats(app.ticker.maxFPS, stamps);
    const msg = `测成 61Hz、真实 60Hz ±1ms：不放宽跳帧 ${(100 * before.skipRate).toFixed(2)}%，放宽后 ${(100 * after.skipRate).toFixed(2)}%`;
    (t && t.diagnostic ? t.diagnostic(msg) : console.log(msg));
    assert.ok(before.skipped > 0);
    assert.equal(after.skipped, 0);
    for (const sbx of [sb]) {
        const mmd = setupMMD(sbx).core;
        mmd._refreshTargetFps();
        assert.equal(mmd.frameTime, 1000 / 92, 'MMD 同一判定');
        assert.equal(setupVRM(sbx)._resolveRafCapFps(60), 92, 'VRM 同一判定');
    }
}));

test('frame-pacing：采样超时后按退避重试（每块屏最多 2 次），成功后放宽恢复', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const pacing = sb.window.nekoFramePacing;
    sb.measureRefresh(60);
    assert.equal(pacing.rafCapFps(60), 90);
    // 换屏后窗口被挡住：rAF 一帧都不来
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    sb.tick(3000);
    assert.equal(pacing.getDisplayRefreshHz(), null, '第一次超时');
    sb.tick(5000);
    sb.tick(3000);
    assert.equal(pacing.getDisplayRefreshHz(), null, '第一次重试也超时');
    // 第二次重试时 rAF 恢复
    sb.tick(5000);
    sb.pumpFrames(30, 60);
    assert.equal(pacing.getDisplayRefreshHz(), 60, '重试成功');
    assert.equal(pacing.rafCapFps(60), 90);

    // 重试次数有上限：一直测不到时，两次重试后不再排定时器
    // （rAF 不来时每一轮采样都在 rafQueue 里留一个请求，用它数轮次）
    sb.rafQueue.splice(0);
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    for (let i = 0; i < 3; i++) { sb.tick(3000); sb.tick(5000); }
    assert.equal(sb.rafQueue.length, 3, '首测 + 2 次重试');
    sb.tick(60000);
    assert.equal(sb.rafQueue.length, 3, '超过重试上限后不再采样');
    assert.equal(pacing.rafCapFps(60), 60, '测不到：保持配置上限');
    // 再换一次屏：重试额度按屏重新计
    sb.rafQueue.splice(0);
    sb.window.dispatchEvent({ type: 'electron-display-changed' });
    sb.tick(1500);
    for (let i = 0; i < 3; i++) { sb.tick(3000); sb.tick(5000); }
    assert.equal(sb.rafQueue.length, 3, '新屏同样首测 + 2 次重试');
}));

test('frame-pacing：换屏前开始的采样在换屏后超时，不占新屏重试额度、不推迟新屏 1.5s 后的重测', withMockTimers(() => {
    const sb = createSandbox({ pet: true, targetFrameRate: 60 });
    const pacing = sb.window.nekoFramePacing;
    sb.measureRefresh(60);
    pacing.measureDisplayRefreshRate(); // 旧屏一轮（rAF 不再来）
    sb.tick(2000);
    sb.rafQueue.splice(0);
    sb.window.dispatchEvent({ type: 'electron-display-changed' }); // 新屏重测排在 1.5s 后
    sb.tick(1000); // 旧一轮 3s 超时
    assert.equal(pacing.getDisplayRefreshHz(), null);
    assert.equal(sb.rafQueue.length, 0);
    sb.tick(500);
    assert.equal(sb.rafQueue.length, 1, '新屏重测按 1.5s 照常开始，没被旧一轮的重试推到 5s');
    sb.pumpFrames(30, 60);
    assert.equal(pacing.rafCapFps(60), 90);
}));

test('VRM 真实 animateLoop：放宽后的上限严格执行（低估成 60、rAF 实际 96Hz 时不超过 90fps），正常 60Hz 仍逐帧渲染', withMockTimers((t) => {
    const run = ({ pet, hz, jitterMs, seconds, seed }) => {
        const sb = createSandbox({ pet, targetFrameRate: 60 });
        sb.measureRefresh(60);
        sb.window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__ = true; // 不起空闲 governor，只看 rAF 节流
        sb.load(VRM_MANAGER_SRC, 'vrm-manager.js');
        const mgr = new sb.window.VRMManager();
        let renders = 0;
        mgr.renderer = { render() { renders++; } };
        mgr.scene = {};
        mgr.camera = {};
        sb.rafQueue.splice(0);
        mgr.startAnimateLoop();
        const stamps = syntheticVsync({ hz, jitterMs, frames: Math.round(hz * seconds), seed, start: sb.now() });
        sb.pumpAt(stamps);
        return { renders, frames: stamps.length, fps: renders / seconds };
    };
    const under = run({ pet: true, hz: 96, jitterMs: 0.3, seconds: 10, seed: 5 });
    const normal = run({ pet: true, hz: 60, jitterMs: 1, seconds: 10, seed: 6 });
    const nonPet = run({ pet: false, hz: 96, jitterMs: 0.3, seconds: 10, seed: 5 });
    const rows = [
        `VRM 低估：测成 60、rAF 实际 96Hz → ${under.fps.toFixed(1)}fps`,
        `VRM 60Hz ±1ms：${normal.renders}/${normal.frames} 帧渲染`,
        `VRM 非 Pet 96Hz（配置 60，原 0.9 容差路径）→ ${nonPet.fps.toFixed(1)}fps`,
    ];
    rows.forEach((r) => (t && t.diagnostic ? t.diagnostic(r) : console.log(r)));
    assert.ok(under.fps <= 90.5, rows[0] + '：不超过放宽后的上限 90');
    assert.ok(under.fps >= 89.5, rows[0] + '：也不能被过度限帧（例如误按配置或其它更低的间隔节流）');
    assert.equal(normal.renders, normal.frames, '60Hz 测准时逐帧渲染，不跳帧');
    // 非 Pet 不放宽，且仍走原有 0.9 容差路径：96Hz（10.4ms）< 15ms 门槛，恰好隔帧渲染 = 48fps
    assert.ok(Math.abs(nonPet.fps - 48) < 0.5, rows[2] + '：非 Pet 行为与改前一致');
}));
