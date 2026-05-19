/**
 * app-telemetry.js -- 前端埋点 SDK
 *
 * 业务侧通过 ``window.appTelemetry.counter(name, value, dims)`` 等三 API 投递
 * 数据；本模块把它们打成 WS message（action="telemetry"）发回 Python 后端，
 * 由 main_routers/websocket_router.py 的 _handle_ws_telemetry 转交
 * utils.instrument。最终跟 Python 端自己发的 counter 走同一条 HTTP 上报通道。
 *
 * 设计取舍：
 *   - WS 不通时静默丢（buffering 复杂，且 telemetry 不能反过来阻塞业务）。
 *     真正关键的事件（crash、onboarding）该用 HTTP POST 保证投递，本模块
 *     不负责。
 *   - 维度（dims/fields）必须是低基数 enum；后端会过滤掉非 string/number/bool
 *     值，并限制 key/value 长度（见 websocket_router._sanitize_dims）。
 *   - surface 字段自动注入（区分 index 宽屏 / 移动版 / chat.html follower
 *     窗口），调用方无需关心。
 *
 * 依赖：window.appState（提供 S.socket）。在 app-state.js / app-websocket.js
 * 之后加载。
 */
(function () {
    'use strict';

    const S = window.appState;

    /**
     * 当前 surface 名 —— 区分三个上下文（记忆 feedback_chat_three_contexts）：
     *   - chat_window: chat.html follower 窗口
     *   - index_mobile: 手机/平板视图的 index.html
     *   - index_wide: 桌面宽屏 index.html
     * 调用方任何 counter/histogram/event 都会带上这个字段。
     */
    function _surface() {
        try {
            var p = (window.location.pathname || '').toLowerCase();
            if (p.indexOf('chat.html') >= 0) return 'chat_window';
        } catch (_) {}
        try {
            var ua = (navigator && navigator.userAgent) || '';
            if (/mobi|android|iphone|ipad/i.test(ua)) return 'index_mobile';
        } catch (_) {}
        return 'index_wide';
    }

    function _wsReady() {
        return !!(S && S.socket && S.socket.readyState === 1 /* OPEN */);
    }

    function _send(payload) {
        if (!_wsReady()) return false;
        try {
            S.socket.send(JSON.stringify(payload));
            return true;
        } catch (_) {
            return false;
        }
    }

    /**
     * 累加一个计数器。
     *
     * @param {string} name - 指标名（snake_case）
     * @param {number} [value=1] - 增量
     * @param {object} [dims] - 维度（低基数 enum）
     * @returns {boolean} 是否成功投递到 WS（不代表服务端收到）
     *
     * 用例：appTelemetry.counter('chat_message_sent', 1, {input_type: 'text'})
     */
    function counter(name, value, dims) {
        var d = Object.assign({surface: _surface()}, dims || {});
        return _send({
            action: 'telemetry',
            kind: 'counter',
            name: String(name || ''),
            value: (typeof value === 'number') ? value : 1,
            dims: d,
        });
    }

    /**
     * 记录一个分布型测量。
     *
     * @param {string} name - 指标名（如 'click_response_ms'）
     * @param {number} value - 测量值（数字）
     * @param {object} [dims] - 维度
     *
     * 用例：appTelemetry.histogram('avatar_load_ms', performance.now() - t0)
     */
    function histogram(name, value, dims) {
        var v = Number(value);
        if (!isFinite(v)) return false;
        var d = Object.assign({surface: _surface()}, dims || {});
        return _send({
            action: 'telemetry',
            kind: 'histogram',
            name: String(name || ''),
            value: v,
            dims: d,
        });
    }

    /**
     * 记录一个稀疏带 context 事件。
     *
     * @param {string} name - 事件名
     * @param {object} [fields] - 事件字段（同样要低基数）
     *
     * 用例：appTelemetry.event('persona_picker_opened')
     */
    function event(name, fields) {
        var f = Object.assign({surface: _surface()}, fields || {});
        return _send({
            action: 'telemetry',
            kind: 'event',
            name: String(name || ''),
            fields: f,
        });
    }

    window.appTelemetry = {
        counter: counter,
        histogram: histogram,
        event: event,
        surface: _surface,
    };
})();
