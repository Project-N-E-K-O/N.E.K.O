/**
 * 诊断观测（前端侧）—— 配合 main_routers/debug_router.py 一起用。
 *
 * 背景：用户报「N.E.K.O 开了两三天后变卡」，光看后端 counter 不够——很多
 * leak 在 renderer（Live2D RAF、setInterval 残留、DOM 节点增长、JS 堆）。
 * 本脚本周期性把这些 counter 通过 POST /api/debug/health/client 推回后端，
 * 后端会跟服务端 snapshot 一起写进同一份 ring buffer / jsonl，导出一个文件
 * 就能看到完整曲线。
 *
 * 默认关 —— 避免给所有用户加任何 console 噪音 / 网络请求。
 * 开启方法（在 Electron devtools / 浏览器 console 跑）：
 *   localStorage.setItem('NEKO_DEBUG_HEALTH', '1');
 *   location.reload();
 * 关闭方法：
 *   localStorage.removeItem('NEKO_DEBUG_HEALTH');
 *   location.reload();
 *
 * 这个文件必须**尽早加载**——它 monkey-patch window.setInterval / setTimeout
 * 来计数活跃 timer。在业务代码之后加载就抓不到早期注册的那些。
 */
(function () {
    'use strict';

    var ENABLED = false;
    try {
        ENABLED = (window.localStorage && window.localStorage.getItem('NEKO_DEBUG_HEALTH') === '1');
    } catch (e) {
        // SecurityError / disabled storage —— 静默退出，不影响主功能
        return;
    }
    if (!ENABLED) return;

    // ── 1. Monkey-patch setInterval / setTimeout 计数 ─────────────────────
    // 注意：必须在所有业务代码加载前先 patch，否则前期注册的 timer 漏抓。
    // 我们记录的是「当前还活着的 timer id 集合」大小，不是历史总数。
    //
    // 双清：浏览器 timer id 池在多数实现里 setInterval / setTimeout 共享，
    // 且本仓库已有 cross-clear 用法（static/app-ui.js: setTimeout 拿 id →
    // clearInterval(id) 清掉）。所以两个 clear wrapper 都必须同时从两个 set
    // 删——否则 cross-clear 会让对应 set 里残留死 id，counter 假性单调涨，
    // 反过来污染本来要诊断的 leak 信号。
    var _liveIntervals = new Set();
    var _liveTimeouts = new Set();
    var _origSetInterval = window.setInterval;
    var _origClearInterval = window.clearInterval;
    var _origSetTimeout = window.setTimeout;
    var _origClearTimeout = window.clearTimeout;

    window.setInterval = function () {
        var id = _origSetInterval.apply(window, arguments);
        _liveIntervals.add(id);
        return id;
    };
    window.clearInterval = function (id) {
        _liveIntervals.delete(id);
        _liveTimeouts.delete(id);  // 见上：cross-clear 兼容
        return _origClearInterval.call(window, id);
    };
    window.setTimeout = function () {
        var args = Array.prototype.slice.call(arguments);
        var origCb = args[0];
        // 如果不是函数（极少见，setTimeout 接受字符串），直接透传不计数
        if (typeof origCb !== 'function') {
            return _origSetTimeout.apply(window, args);
        }
        var idHolder = { id: 0 };
        args[0] = function () {
            _liveTimeouts.delete(idHolder.id);
            try { return origCb.apply(this, arguments); } catch (e) { throw e; }
        };
        var id = _origSetTimeout.apply(window, args);
        idHolder.id = id;
        _liveTimeouts.add(id);
        return id;
    };
    window.clearTimeout = function (id) {
        _liveTimeouts.delete(id);
        _liveIntervals.delete(id);  // 见上：cross-clear 兼容
        return _origClearTimeout.call(window, id);
    };

    // ── 2. RAF 频率统计 ─────────────────────────────────────────────────
    // 累计最近 60s 内的 RAF 触发次数（理想是 60×60=3600；远低于说明渲染器卡）。
    var _rafCount = 0;
    var _rafCountWindowStart = performance.now();
    var _origRAF = window.requestAnimationFrame;
    var _trackedRAF = function (cb) {
        _rafCount += 1;
        return _origRAF.call(window, cb);
    };
    window.requestAnimationFrame = _trackedRAF;

    function _snapshotRAF() {
        var now = performance.now();
        var elapsedMs = Math.max(1, now - _rafCountWindowStart);
        var fps = _rafCount * 1000 / elapsedMs;
        _rafCount = 0;
        _rafCountWindowStart = now;
        return fps;
    }

    // ── 3. Snapshot 收集 + 推送 ────────────────────────────────────────
    function collectSnapshot() {
        var snap = {
            ts: Date.now() / 1000,
            location: location.pathname,
            live_intervals: _liveIntervals.size,
            live_timeouts: _liveTimeouts.size,
            raf_fps_60s: _snapshotRAF(),
            dom_nodes: 0,
            js_heap_mb: null,
        };
        try {
            snap.dom_nodes = document.getElementsByTagName('*').length;
        } catch (e) { /* noop */ }
        try {
            // Chromium-only：performance.memory.usedJSHeapSize
            if (performance && performance.memory && performance.memory.usedJSHeapSize) {
                snap.js_heap_mb = performance.memory.usedJSHeapSize / (1024 * 1024);
            }
        } catch (e) { /* noop */ }
        // 各 lanlan 的 ws / proactive 状态：尽量抓但绝不能抛
        try {
            if (window.S) {
                snap.ws_state = window.S.socket ? window.S.socket.readyState : null;
                snap.proactive_backoff_level = window.S.proactiveChatBackoffLevel;
                snap.proactive_running = !!window.S.isProactiveChatRunning;
                snap.is_recording = !!window.S.isRecording;
            }
        } catch (e) { /* noop */ }
        try {
            if (window._agentTaskMap && typeof window._agentTaskMap.size === 'number') {
                snap.agent_task_map_size = window._agentTaskMap.size;
            }
        } catch (e) { /* noop */ }
        return snap;
    }

    function pushSnapshot() {
        var snap;
        try { snap = collectSnapshot(); } catch (e) { return; }
        try {
            console.log('[debug_health]', JSON.stringify(snap));
        } catch (e) { /* noop */ }
        try {
            fetch('/api/debug/health/client', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(snap),
                // keepalive 让快关窗口时最后一发也能送出
                keepalive: true,
            }).catch(function () { /* 失败不重试——下一轮再说 */ });
        } catch (e) { /* noop */ }
    }

    // ── 4. 周期触发 ──────────────────────────────────────────────────
    // 5 分钟节奏，跟后端 watchdog 对齐。首次延迟 30s——给业务代码 warm up
    // 时间，避免抓到全是 0 / N/A 的首屏快照。
    setTimeout(function () {
        pushSnapshot();
        setInterval(pushSnapshot, 5 * 60 * 1000);
    }, 30 * 1000);

    // 关窗口前再补一发（keepalive）——长会话最后一刻的状态最值钱
    window.addEventListener('beforeunload', function () {
        try { pushSnapshot(); } catch (e) { /* noop */ }
    });

    console.log('[debug_health] enabled (set localStorage.NEKO_DEBUG_HEALTH=0 + reload to disable)');
})();
