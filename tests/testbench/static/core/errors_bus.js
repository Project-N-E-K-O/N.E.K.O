/**
 * errors_bus.js — 全局错误收集 (P04 夹带的客户端总线 + P19 后端同步).
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
 * P19 扩展 — 后端镜像:
 *   - 本地收录的同时, 每条错误异步 POST 到 `/api/diagnostics/errors`, 让
 *     后端 `diagnostics_store` 也有一份. 好处: Browser tab 崩溃/刷新/切会
 *     话后错误仍可在 Diagnostics → Errors 子页回看 (后端 ring buffer
 *     200 条容量, 重启清空).
 *   - 后端同步**失败不递归上报**, 否则会导致错误洪流 (网络挂时每条错误
 *     都会触发新的 http:error). 失败只在 console 记一次 warn.
 *   - 同步本身的 HTTP 请求会绕过 `core/api.js`, 直接用原生 fetch —
 *     否则 POST /api/diagnostics/errors 若失败又经过 api.js 的 toast +
 *     emit('http:error') 就自激反馈.
 */

import { set, on, store, emit } from './state.js';

const MAX_ERRORS = 100;
const BACKEND_SYNC_URL = '/api/diagnostics/errors';
let _seq = 0;
// 同一条错误 (同 source+type+message+url) 在短时间内反复抛, 只上报第一条 —
// 避免网络挂掉时每次 fetch 失败都 POST 一次, 导致后端也跟着淹没. 窗口用
// Map<signature, lastAt>, 30s 去重.
const _recentSyncSigs = new Map();
const SYNC_DEDUPE_MS = 30_000;

function nextId() {
  _seq += 1;
  return `e${Date.now().toString(36)}${_seq}`;
}

function _syncSignature(entry) {
  return [
    entry.source || 'unknown',
    entry.type || 'Error',
    (entry.message || '').slice(0, 160),
    entry.url || '',
    entry.status || '',
  ].join('|');
}

function _shouldSyncToBackend(entry) {
  const now = Date.now();
  // 先清理过期签名, 防止 map 无限增长.
  for (const [sig, t] of _recentSyncSigs) {
    if (now - t > SYNC_DEDUPE_MS) _recentSyncSigs.delete(sig);
  }
  const sig = _syncSignature(entry);
  if (_recentSyncSigs.has(sig)) return false;
  _recentSyncSigs.set(sig, now);
  return true;
}

async function _backendSync(entry) {
  if (typeof fetch !== 'function') return;
  if (!_shouldSyncToBackend(entry)) return;
  // 后端 source 白名单 = {http, sse, js, promise, resource, synthetic}.
  // "unknown" 等保留类会被后端归一化, 这里不拦截.
  try {
    const payload = {
      source: entry.source,
      type: entry.type,
      message: entry.message,
      level: entry.level || 'error',
      url: entry.url,
      method: entry.method,
      status: entry.status,
      session_id: store.session?.id || null,
      user_agent: (typeof navigator !== 'undefined' && navigator.userAgent) || null,
      detail: entry.detail || {},
    };
    // 绕过 core/api.js 的 http:error emit, 避免无限自激.
    await fetch(BACKEND_SYNC_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      // keepalive 让 unload 时 inflight 请求也能发出去 —
      // 对于"页面崩溃前最后一条错误"这类场景很关键.
      keepalive: true,
    });
  } catch (err) {
    // 注意: 这里**不能**再 emit 'http:error' 或 pushError, 否则网络挂时
    // 每次 sync 失败都进一步产生新错误, 无限循环.
    if (typeof console !== 'undefined') {
      console.warn('[errors_bus] backend sync failed:', err?.message || err);
    }
  }
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
  // 异步镜像到后端; fire-and-forget, 失败不影响本地渲染.
  _backendSync(normalized);
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

  // P24 hotfix #105 (2026-04-21): 当 api.js 的 burst circuit breaker 熔断
  // 时, 写入**一条** synthetic 错误让用户在 Diagnostics → Errors 看到
  // "发生过 http:error 风暴", 这条本身不会再触发 emit('http:error') 或
  // 额外 pushError (避免冷却期内自激).
  on('http:error:burst_tripped', (payload) => {
    pushError({
      source: 'synthetic',
      type: 'HttpErrorBurstTripped',
      level: 'warning',
      message: `http:error 风暴熔断: ${payload?.threshold || '?'}/${payload?.window_ms || '?'}ms, `
        + `静默 ${payload?.cooldown_ms || '?'}ms. 最后一次: `
        + `${payload?.last_method || '?'} ${payload?.last_url || '?'} → ${payload?.last_status || '?'}`,
      detail: payload,
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
