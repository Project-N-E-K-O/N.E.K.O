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

async function request(method, url, { body, headers } = {}) {
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

  // 尝试 JSON, 失败回退为文本.
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
    const errPayload = {
      type: typeof parsed === 'object' ? parsed?.error_type || 'http_error' : 'http_error',
      message: typeof parsed === 'object' ? parsed?.message || parsed?.detail : String(parsed),
      status: resp.status,
      detail: parsed,
    };
    // 404 / 409 不 toast: 常被调用方自己处理. 其它才 toast.
    if (resp.status >= 500 || [400, 403].includes(resp.status)) {
      toast.err(i18n('errors.server', resp.status), {
        message: errPayload.message || `${method} ${url}`,
      });
    }
    // 广播便于 Diagnostics → Errors 子页 (P19) 收集.
    emit('http:error', { url, method, ...errPayload });
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
