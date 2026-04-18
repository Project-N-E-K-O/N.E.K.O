/**
 * errors_bus.js — 全局错误收集 (临时桥, P04 夹带, P19 会被完整版替换).
 *
 * 设计目标:
 *   - 一处订阅四类错误源 → 统一 shape → 追加到 `store.errors` → emit 'errors:change'
 *     * `http:error`      — `core/api.js` 发, 4xx/5xx HTTP 响应
 *     * `sse:error`       — `core/api.js` 发, EventSource 异常
 *     * window 'error'    — 未捕获 JS 同步异常 (脚本加载失败 / `throw`)
 *     * 'unhandledrejection' — 未捕获的 Promise reject
 *
 *   - store.errors 元素归一化为:
 *         { id, at, source, type, message, url?, method?, status?, detail }
 *
 *   - 容量上限 `MAX_ERRORS`, 超出后从最早的开始丢.
 *
 * 与 P19 的关系:
 *   - P19 会把"收集 + 渲染"搬到 server-side (让多会话 / Browser 崩溃也能回看),
 *     配套 `POST /api/errors` / `GET /api/errors` + JSONL log. 本文件届时要么
 *     删掉, 要么降级成"浏览器本地镜像".
 *   - 本模块只依赖 `core/state.js`, 不依赖任何 UI. 可以在不同子页独立消费.
 */

import { set, on, store, emit } from './state.js';

const MAX_ERRORS = 100;
let _seq = 0;

function nextId() {
  _seq += 1;
  return `e${Date.now().toString(36)}${_seq}`;
}

/** 把任意值压成一行可展示的字符串, 给 UI / 日志用. */
function coerceMessage(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function pushError(entry) {
  const normalized = {
    id: nextId(),
    at: new Date().toISOString(),
    source: 'unknown',
    type: 'Error',
    message: '',
    ...entry,
  };
  // 防御: 某些源 (旧版 api.js / 第三方广播) 可能把对象塞进 message,
  // 下游 UI 若直接调 `.slice` / `.length` 会炸, 这里统一归一化为字符串.
  normalized.message = coerceMessage(normalized.message);
  normalized.type    = typeof normalized.type === 'string' ? normalized.type : coerceMessage(normalized.type);
  const current = store.errors || [];
  const next = [...current, normalized];
  if (next.length > MAX_ERRORS) next.splice(0, next.length - MAX_ERRORS);
  set('errors', next);           // 会触发 errors:change (state.js 内置)
}

/**
 * 清空错误队列. 返回清掉的数量.
 */
export function clearErrors() {
  const n = (store.errors || []).length;
  set('errors', []);
  return n;
}

/**
 * 手动推一条 (便于页面在"本地非异常但值得记录"场景也统一入库).
 */
export function recordError(entry) {
  pushError(entry);
}

/**
 * 启动错误总线. 只应在 app 引导阶段调用一次.
 *
 * 幂等: 重复调用安全 (内部用 `window.__tbErrorsBusMounted` 标记).
 */
export function initErrorsBus() {
  if (typeof window !== 'undefined' && window.__tbErrorsBusMounted) return;
  if (typeof window !== 'undefined') window.__tbErrorsBusMounted = true;

  on('http:error', (payload) => {
    pushError({
      source: 'http',
      type: payload?.type || 'HttpError',
      message: payload?.message || `${payload?.method || ''} ${payload?.url || ''}`.trim(),
      url: payload?.url,
      method: payload?.method,
      status: payload?.status,
      detail: payload?.detail ?? null,
    });
  });

  on('sse:error', (payload) => {
    pushError({
      source: 'sse',
      type: 'SseError',
      message: `SSE 连接异常: ${payload?.url || ''}`,
      url: payload?.url,
      detail: payload,
    });
  });

  if (typeof window !== 'undefined') {
    window.addEventListener('error', (ev) => {
      // 这个事件对静态资源加载失败也会触发 (ev.target !== window).
      const isResourceError = ev.target && ev.target !== window && ev.target.src;
      pushError({
        source: isResourceError ? 'resource' : 'js',
        type: ev.error?.name || (isResourceError ? 'ResourceLoadError' : 'Error'),
        message: ev.error?.message
          || (isResourceError ? `资源加载失败: ${ev.target.src || ev.target.href}` : (ev.message || 'Unknown error')),
        detail: {
          filename: ev.filename,
          lineno: ev.lineno,
          colno: ev.colno,
          stack: ev.error?.stack,
          target_tag: ev.target?.tagName,
          target_src: ev.target?.src || ev.target?.href,
        },
      });
    }, true);

    window.addEventListener('unhandledrejection', (ev) => {
      const reason = ev.reason;
      pushError({
        source: 'promise',
        type: reason?.name || 'UnhandledRejection',
        message: reason?.message || String(reason) || 'Promise 未捕获拒绝',
        detail: {
          stack: reason?.stack,
          raw: typeof reason === 'object' ? null : String(reason),
        },
      });
    });
  }

  // 初始化时若已有旧数据 (热重启 state 保留), 主动广播一次, 让 UI 渲染对齐.
  emit('errors:change', store.errors || []);
}
