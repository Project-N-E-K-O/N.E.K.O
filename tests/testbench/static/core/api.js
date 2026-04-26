/**
 * api.js — 后端 HTTP + SSE 的瘦封装.
 *
 * 设计目标:
 *   - 所有 GET/POST/PUT/DELETE 走 fetch, 统一返回 `{ ok, status, data, error }`
 *   - 业务错误 (非 2xx) 在这里吐 toast, 调用方仍能收到 `ok:false` 自行处理
 *   - SSE 通过 `openSse(url, { onMessage, onError, onOpen })` 暴露 EventSource
 *     包装, 添加自动错误 toast + 关闭句柄
 *
 * 当前版本够 P03 的 session 端点使用, 后续 phase (SSE /chat/send) 会再加功能.
 */

import { toast } from './toast.js';
import { i18n } from './i18n.js';
import { emit } from './state.js';

// ── http:error burst circuit breaker (P24 hotfix #105, 2026-04-21) ──
//
// 背景: 2026-04-20 Hard Reset 整机卡死 (§4.26 #87) + 2026-04-21 New Session
// 整机卡死 (§4.27 #105) 两次同族事故都遵循同一 cascade:
//   (1) 一次 "全局状态清零" 动作触发 session:change
//   (2) 15+ 订阅者并发 fetch empty-sandbox 端点
//   (3) 部分端点返 409/500 → emit('http:error') → errors_bus.pushError
//   (4) set('errors', ...) → emit('errors:change')
//   (5) 订阅 errors:change 的组件 (page_errors / topbar err badge) 再 fetch
//   (6) 5s polling setInterval 又并发加码
//   (7) DOM renderAll 大量错误条目 + 字符串化 detail → CPU 爆/内存爆
//   (8) 浏览器进程进入 swap → Cursor Electron 跟着卡 → 整机黑屏
//
// Hotfix 1 从**触发入口**封堵 (New/Destroy/Load/Hard Reset 全走 reload).
// Hotfix 2 (本段) 从**爆发链路**封堵 — 万一未来某个新动作又踩同类坑, 有
// 二道防线让浏览器喘息. 实现是窗口计数限流:
//   - 滚动窗口 `_BURST_WINDOW_MS` (1s) 内超过 `_BURST_THRESHOLD` (30) 次
//     http:error, 立即打开熔断;
//   - 熔断期 `_BURST_COOLDOWN_MS` (5s) 内所有 http:error 既**不 toast**也
//     **不 emit 广播**, 只 console.error 一次和附加一条 synthetic 错误到
//     errors 数组 (一条, 不重复) 让用户从 Diagnostics 页看到发生了什么;
//   - 冷却结束后计数清零恢复正常.
// 阈值选择: 正常 UI 操作不会在 1s 内产生 > 5 个 http:error; 30 次已经是
// 明显异常. 5s 冷却窗口足够让 promise queue 清空 + 用户反应过来.
const _BURST_WINDOW_MS = 1_000;
const _BURST_THRESHOLD = 30;
const _BURST_COOLDOWN_MS = 5_000;
const _recentErrTimestamps = [];
let _burstCooldownUntil = 0;

function _noteHttpError() {
  const now = Date.now();
  // 冷却期: 直接返回 'suppressed', 不计数 (避免冷却期内的噪声还算在下一
  // 个窗口里反复触发).
  if (now < _burstCooldownUntil) {
    return 'suppressed';
  }
  _recentErrTimestamps.push(now);
  // 老记录淘汰 (>= _BURST_WINDOW_MS 之前的).
  while (_recentErrTimestamps.length && _recentErrTimestamps[0] < now - _BURST_WINDOW_MS) {
    _recentErrTimestamps.shift();
  }
  if (_recentErrTimestamps.length >= _BURST_THRESHOLD) {
    _burstCooldownUntil = now + _BURST_COOLDOWN_MS;
    _recentErrTimestamps.length = 0;
    if (typeof console !== 'undefined') {
      console.error(
        `[api] http:error burst circuit breaker TRIPPED — `
        + `${_BURST_THRESHOLD} errors in <${_BURST_WINDOW_MS}ms. `
        + `Suppressing toast + emit for ${_BURST_COOLDOWN_MS}ms to prevent `
        + `browser freeze. See P24 §4.27 #105 for the cascade model.`,
      );
    }
    return 'tripped';
  }
  return 'ok';
}

/**
 * 把 FastAPI 错误响应体规范化成 `{type, message, errors?, busy_op?, ...}`.
 *
 * FastAPI 的 `HTTPException(detail={...})` 会把整个 dict 挂在 `detail` 字段下,
 * 所以需要同时尝试顶层和 `detail` 子对象. 最终 `message` 永远返回**字符串**,
 * 避免下游在 UI 里对对象调 `slice` 之类的字符串方法.
 *
 * 2026-04-22 Day 8 手测 #2 修: 早期版本只返 `{type, message}`, 导致 caller
 * 需要深挖 `res.error.detail.detail.errors` 才能拿到 validation errors 列表
 * 或 `busy_op` 等二级字段; 最常见的错误写法 `res.data?.detail` 在非 2xx 时
 * (data 为 null) 直接返 undefined 被 `String()` 成 `[object Object]`. 本次
 * 把常用的 `errors`/`busy_op` 直接拍平到 `error` 对象上, caller 用
 * `res.error.errors` / `res.error.busy_op` 即可, 不需要解嵌套.
 */
function extractError(parsed, status) {
  if (parsed == null) {
    return { type: 'http_error', message: '' };
  }
  if (typeof parsed === 'string') {
    return { type: 'http_error', message: parsed };
  }
  if (typeof parsed !== 'object') {
    return { type: 'http_error', message: String(parsed) };
  }
  // FastAPI HTTPException: `{detail: {error_type, message}}` 或 `{detail: "plain"}`;
  // 其它: 顶层直接挂 `error_type / message`.
  const detail = parsed.detail;
  const detailIsObj = detail && typeof detail === 'object' && !Array.isArray(detail);
  const type = parsed.error_type
            || (detailIsObj ? detail.error_type : null)
            || 'http_error';
  let message = parsed.message
             || (detailIsObj ? detail.message : null);
  if (message == null) {
    if (typeof detail === 'string') message = detail;
    else message = `HTTP ${status}`;
  }
  // 兜底: 确保 message 一定是字符串 (有些 detail 是嵌套对象, 直接 JSON.stringify).
  if (typeof message !== 'string') {
    try { message = JSON.stringify(message); }
    catch { message = String(message); }
  }
  // 拍平常用二级字段. 保持向后兼容: 额外字段不破坏现有 caller.
  const result = { type, message };
  // errors: ScoringSchemaError / AutoDialogError / 类似批量校验场景
  const errors = parsed.errors
              || (detailIsObj ? detail.errors : null);
  if (Array.isArray(errors) && errors.length > 0) {
    result.errors = errors;
  }
  // busy_op: SessionConflictError 409 场景 (topbar stage chip / reset 用)
  const busyOp = parsed.busy_op
              || (detailIsObj ? detail.busy_op : null);
  if (busyOp) result.busy_op = busyOp;
  return result;
}

/**
 * @param {string} method
 * @param {string} url
 * @param {object} [opts]
 * @param {*}       [opts.body]
 * @param {object}  [opts.headers]
 * @param {number[]} [opts.expectedStatuses]  业务流中"允许失败"的 HTTP 状态码列表.
 *   命中时: 不发 toast, 不向 `errors_bus` 广播 `http:error`, 仍返回 `{ok:false,...}` 让调用方决策.
 *   典型用法: 需要 session 的端点用 `expectedStatuses: [404]` 表示"没会话是已知流程状态".
 */
async function request(method, url, { body, headers, expectedStatuses, signal } = {}) {
  const init = {
    method,
    headers: {
      'Accept': 'application/json',
      ...headers,
    },
  };
  if (signal) init.signal = signal;
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let resp;
  try {
    resp = await fetch(url, init);
  } catch (err) {
    // AbortError 是 caller 主动取消, 不弹 toast / 不广播, 直接返回
    // 标记状态 (status:0 + type:'aborted'). caller 应当忽略这类结果
    // (下一个请求的回调已经接管了 UI). 详见 P24 §14.4 M3.
    if (err && err.name === 'AbortError') {
      return { ok: false, status: 0, data: null, error: { type: 'aborted', message: 'aborted' } };
    }
    toast.err(i18n('errors.network'), { message: `${method} ${url}` });
    return { ok: false, status: 0, data: null, error: { type: 'network', message: String(err) } };
  }

  let parsed = null;
  const ct = resp.headers.get('Content-Type') || '';
  try {
    if (ct.includes('application/json')) {
      parsed = await resp.json();
    } else {
      parsed = await resp.text();
    }
  } catch (err) {
    parsed = null;
  }

  if (!resp.ok) {
    const extracted = extractError(parsed, resp.status);
    // errPayload 把 extractError 的平铺字段 (errors / busy_op 等) 带上,
    // caller 用 `res.error.errors` / `res.error.busy_op` 即可, 无需再深挖
    // `res.error.detail.detail.xxx` (见 2026-04-22 Day 8 手测 #2 修).
    const errPayload = {
      ...extracted,
      status: resp.status,
      detail: parsed,  // 保留原始 parsed 给需要 raw 的 caller
    };
    const expected = Array.isArray(expectedStatuses) && expectedStatuses.includes(resp.status);

    if (!expected) {
      // Circuit breaker: burst 期一律静默, 连 toast 都不弹, 连 emit 都不发.
      // 正常路径: toast (只对 5xx/400/403) + emit broadcast 给 errors_bus.
      const burstState = _noteHttpError();
      if (burstState !== 'suppressed' && burstState !== 'tripped') {
        if (resp.status >= 500 || [400, 403].includes(resp.status)) {
          toast.err(i18n('errors.server', resp.status), {
            message: message || `${method} ${url}`,
          });
        }
        emit('http:error', { url, method, ...errPayload });
      } else if (burstState === 'tripped') {
        // 熔断刚触发这一次, 让 errors_bus 单独写一条 synthetic 错误 (走
        // 独立路径, 不递归 emit('http:error')).
        emit('http:error:burst_tripped', {
          threshold: _BURST_THRESHOLD,
          window_ms: _BURST_WINDOW_MS,
          cooldown_ms: _BURST_COOLDOWN_MS,
          last_url: url,
          last_method: method,
          last_status: resp.status,
        });
      }
      // burstState === 'suppressed': 完全静默. 用户会从 Diagnostics →
      // Errors 的单条 synthetic 条目看到事情发生过.
    }
    return { ok: false, status: resp.status, data: null, error: errPayload };
  }

  return { ok: true, status: resp.status, data: parsed, error: null };
}

export const api = {
  get:    (url, opts = {}) => request('GET',    url, opts),
  post:   (url, body, opts = {}) => request('POST',   url, { ...opts, body }),
  put:    (url, body, opts = {}) => request('PUT',    url, { ...opts, body }),
  patch:  (url, body, opts = {}) => request('PATCH',  url, { ...opts, body }),
  delete: (url, opts = {}) => request('DELETE', url, opts),
  // Generic escape hatch: ``api.request('/x', { method: 'PUT', body })``.
  // 方便需要动态 method 的调用点 (如 Virtual Clock page 的 mutate() helper).
  request: (url, { method = 'GET', body, headers, expectedStatuses, signal } = {}) =>
    request(method, url, { body, headers, expectedStatuses, signal }),
};

/**
 * makeCancellableGet(url, opts?) — GET wrapper that auto-aborts the previous
 * in-flight request before issuing the new one. Returns a *callable* that
 * invokes the same GET each call with the same url/opts:
 *
 *     const reloadErrors = makeCancellableGet('/api/diagnostics/errors');
 *     reloadErrors();                       // ← first fetch starts
 *     // user clicks [Refresh] again fast
 *     reloadErrors();                       // ← aborts prev, new fetch starts
 *     await reloadErrors({ extraOpts: { expectedStatuses: [404] } });
 *
 * Use case: toolbar refresh buttons, rapid filter-chip clicks, polling loops
 * where "last click wins" is the right UX, not "last response wins". Fixes
 * the classic fetch-response-ordering race (P24 §14.4 M3). AbortError is
 * swallowed in request(): the old Promise resolves to `{ok:false, error.type:'aborted'}`
 * and the caller can detect & ignore via `if (res.error?.type === 'aborted') return;`.
 *
 * Not for mutations (POST/PUT/DELETE) — aborting those mid-flight makes the
 * server-side state ambiguous (did it commit or not?). For mutations, queue
 * them or disable the button until response lands.
 */
export function makeCancellableGet(url, baseOpts = {}) {
  let currentController = null;
  return async function cancellableGet(extraOpts = {}) {
    if (currentController) {
      try { currentController.abort(); } catch (_) { /* ignore */ }
    }
    const controller = new AbortController();
    currentController = controller;
    const res = await api.get(url, {
      ...baseOpts,
      ...extraOpts,
      signal: controller.signal,
    });
    if (currentController === controller) currentController = null;
    return res;
  };
}

/**
 * openSse(url, { onMessage, onError, onOpen }) -> closer
 *   返回一个关闭函数, 调用即断开连接.
 *
 * 后端用 EventSource 规范发送 SSE. `onMessage(dataStr, ev)` 可自行 JSON.parse.
 */
export function openSse(url, { onMessage, onError, onOpen } = {}) {
  const es = new EventSource(url);
  if (onOpen) es.addEventListener('open', onOpen);
  if (onMessage) es.addEventListener('message', (ev) => onMessage(ev.data, ev));
  es.addEventListener('error', (ev) => {
    if (onError) onError(ev);
    else toast.err('流式连接异常', { message: url });
    emit('sse:error', { url });
  });
  return () => es.close();
}
