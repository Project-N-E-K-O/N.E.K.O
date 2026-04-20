/**
 * state.js — 前端全局状态 + 轻量事件总线.
 *
 * 设计:
 *   - 单一 store 对象 (所有非 UI 瞬态的可序列化状态)
 *   - subscribe(key, fn) / emit(key, payload) 事件发布订阅
 *   - set(path, value) 精细更新 + 自动发 `<path>:change` 事件
 *   - 避免引入状态管理库: 每个 workspace 只订阅自己关心的 key,
 *     事件 key 用同样的点号命名空间
 *
 * 用法:
 *     import { store, set, get, on } from './core/state.js';
 *     on('session:change', (s) => renderTopbar(s));
 *     set('session', { id: '...', name: '...' });
 */

const listeners = new Map();   // key -> Set<fn>

const _state = {
  session: null,         // { id, name, state, busy_op, ... } 或 null
  active_workspace: 'setup',
  errors: [],            // 最近错误队列 (P19 填充; P03 先空)
  ui_prefs: {
    // Settings → UI 控制的偏好, P04 接入持久化, 当前用默认值
  },
};

// ── 访问 ─────────────────────────────────────────────────────────

/** 直接读根字段. */
export function get(key) {
  return _state[key];
}

/** 只读 snapshot; 给想完整读一遍的消费者. 注意是浅复制. */
export function snapshot() {
  return { ..._state };
}

// ── 写入 ─────────────────────────────────────────────────────────

/**
 * 写根字段并发布 `<key>:change` 事件.
 * (不支持嵌套路径, 嵌套字段整块替换即可, 保持心智负担低.)
 */
export function set(key, value) {
  _state[key] = value;
  emit(`${key}:change`, value);
}

// ── 事件总线 ───────────────────────────────────────────────────

/**
 * 订阅事件, 返回退订函数.
 *   const off = on('session:change', fn);
 *   off();  // 停止监听
 */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => off(event, fn);
}

export function off(event, fn) {
  const set = listeners.get(event);
  if (set) set.delete(fn);
}

// ── recursion guard ───────────────────────────────────────────────
//
// 任何一个 listener 里同步调 `set()` 都会再次进 emit; 一串 listener 叠
// 起来可能意外写出 "A 的 listener 改 B, B 的 listener 改 A" 这种
// cross-feedback loop. state.js 原来没有保护, 真跑到这种配置时会把整
// 个 event loop 烧死. 2026-04-20 Hard Reset 诊断时意识到这个空洞.
//
// 实现策略: per-event 深度计数. 同一个 event 如果正处于被处理状态
// (depth > 0) 且再次被 emit, 我们仍然让它跑 (否则 rewind/reset 的正常
// re-entry 会失效), 但超过 `_MAX_EMIT_DEPTH` 时**切断**并打印 stack,
// 给开发者留一条明确的排查线索而不是让浏览器/电脑卡死.
const _emitDepth = new Map();
const _MAX_EMIT_DEPTH = 8;

/** 发布事件. 监听器异常不会互相影响; 超过递归上限会主动熔断. */
export function emit(event, payload) {
  const set = listeners.get(event);
  if (!set) return;
  const depth = (_emitDepth.get(event) || 0) + 1;
  if (depth > _MAX_EMIT_DEPTH) {
    // 不再调用 listener. 这几乎一定是 bug (某个 listener 在 reacting
    // 时又 set 触发同一个 event). 留下明显的 console 痕迹而不是静默
    // 挂浏览器.
    console.error(
      `[state] recursive emit of '${event}' exceeded depth ${_MAX_EMIT_DEPTH}; `
      + `aborting to prevent infinite loop. Fix the listener that synchronously `
      + `re-sets the same key.`,
    );
    return;
  }
  _emitDepth.set(event, depth);
  try {
    for (const fn of set) {
      try {
        fn(payload);
      } catch (err) {
        console.error(`[state] listener for '${event}' threw:`, err);
      }
    }
  } finally {
    if (depth <= 1) _emitDepth.delete(event);
    else _emitDepth.set(event, depth - 1);
  }
}

// 开发期: 暴露到 window 便于在 devtools 里 `__tbState.snapshot()` 检查.
if (typeof window !== 'undefined') {
  window.__tbState = { snapshot, get, set, on, off, emit };
}

/** 便捷导出: 整个 store 对象, 仅供只读访问 (写入走 set). */
export const store = _state;
