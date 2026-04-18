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

/**
 * 把 FastAPI 错误响应体规范化成 `{type, message}`.
 *
 * FastAPI 的 `HTTPException(detail={...})` 会把整个 dict 挂在 `detail` 字段下,
 * 所以需要同时尝试顶层和 `detail` 子对象. 最终 `message` 永远返回**字符串**,
 * 避免下游在 UI 里对对象调 `slice` 之类的字符串方法.
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
  return { type, message };
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
async function request(method, url, { body, headers, expectedStatuses } = {}) {
  const init = {
    method,
    headers: {
      'Accept': 'application/json',
      ...headers,
    },
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let resp;
  try {
    resp = await fetch(url, init);
  } catch (err) {
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
    const { type, message } = extractError(parsed, resp.status);
    const errPayload = { type, message, status: resp.status, detail: parsed };
    const expected = Array.isArray(expectedStatuses) && expectedStatuses.includes(resp.status);

    if (!expected) {
      // toast: 只有"真·服务端/请求错误"才弹, 404/409 留给调用方自己处理.
      if (resp.status >= 500 || [400, 403].includes(resp.status)) {
        toast.err(i18n('errors.server', resp.status), {
          message: message || `${method} ${url}`,
        });
      }
      // 广播便于 Diagnostics → Errors 子页收集; 命中 expectedStatuses 跳过, 避免噪音.
      emit('http:error', { url, method, ...errPayload });
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
  request: (url, { method = 'GET', body, headers, expectedStatuses } = {}) =>
    request(method, url, { body, headers, expectedStatuses }),
};

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
