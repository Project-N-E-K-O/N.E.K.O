/** Numeric v2 小剧场跨页面协议与本地 JSON 请求的最小共享层。 */
(function () {
    'use strict';

    // 这里只收纳选择页与本体运行时完全一致的传输规则，不接管各页面的业务状态和发送目标。
    var MESSAGE_SCHEMA = 'neko.theater.interpage.v1';
    var DEFAULT_TIMEOUT_MS = 30000;
    // Existing worst-case opening: two bodies, two suggestion fills and two reviews (156s).
    var START_TIMEOUT_MS = 180000;
    // Up to three generation passes (initial, missed transition, shared rewrite):
    // each allows four 35s body attempts plus one 35s suggestion fill. Add
    // evaluator/history (24s), four fast reviews (32s), one dispute (30s), and I/O margin.
    // This is only the transport ceiling; it does not add calls or delay fast responses.
    var TURN_TIMEOUT_MS = 660000;

    function defaultTimeoutMs(url) {
        var path = String(url || '').split('?', 1)[0];
        if (path === '/api/theater-numeric/session/input') return TURN_TIMEOUT_MS;
        if (path === '/api/theater-numeric/session/start') return START_TIMEOUT_MS;
        return DEFAULT_TIMEOUT_MS;
    }

    function createId(prefix) {
        var random = window.crypto && typeof window.crypto.randomUUID === 'function'
            ? window.crypto.randomUUID()
            : Math.random().toString(36).slice(2) + Date.now().toString(36);
        return String(prefix || '') + random;
    }

    async function mutationHeaders() {
        // 所有本地修改请求复用主程序的 CSRF 实现，剧场协议不保存也不复制安全令牌。
        var helper = window.nekoLocalMutationSecurity;
        if (!helper || typeof helper.getMutationHeaders !== 'function') return {};
        try { return await helper.getMutationHeaders(); } catch (_) { return {}; }
    }

    async function requestJson(url, options) {
        var opts = options || {};
        var method = opts.method || 'GET';
        // 模型接口的浏览器超时必须覆盖服务端 Evaluator + Actor 合法预算及少量提交开销。
        var timeoutMs = Number.isFinite(opts.timeoutMs) && opts.timeoutMs > 0
            ? opts.timeoutMs
            : defaultTimeoutMs(url);
        var body = Object.prototype.hasOwnProperty.call(opts, 'body')
            ? JSON.stringify(opts.body)
            : undefined;

        async function send() {
            var headers = { 'Content-Type': 'application/json' };
            if (method !== 'GET') Object.assign(headers, await mutationHeaders());
            // 每次发送（含 CSRF 重试）各自拥有有界超时，避免悬空 fetch 永久锁住剧场 busy/phase。
            var controller = typeof AbortController === 'function' ? new AbortController() : null;
            var timeoutId = controller ? window.setTimeout(function () { controller.abort(); }, timeoutMs) : 0;
            try {
                return await fetch(url, {
                    method: method,
                    headers: headers,
                    body: body,
                    signal: controller ? controller.signal : undefined
                });
            } finally {
                if (timeoutId) window.clearTimeout(timeoutId);
            }
        }

        var response = await send();
        if (response.status === 403 && method !== 'GET') {
            // CSRF 过期时只刷新并重试一次，避免一次业务动作产生无界请求循环。
            var helper = window.nekoLocalMutationSecurity;
            if (helper && typeof helper.refreshToken === 'function') {
                await helper.refreshToken();
                response = await send();
            }
        }
        var data = await response.json().catch(function () { return {}; });
        data._status = response.status;
        return data;
    }

    function createMessage(source, message) {
        // 页面只提供自身来源和业务字段，协议版本与时间戳由共享边界统一补齐。
        return Object.assign({
            schema: MESSAGE_SCHEMA,
            source: String(source || ''),
            timestamp: Date.now()
        }, message || {});
    }

    window.nekoTheaterTransport = Object.freeze({
        MESSAGE_SCHEMA: MESSAGE_SCHEMA,
        createId: createId,
        requestJson: requestJson,
        createMessage: createMessage
    });
})();
