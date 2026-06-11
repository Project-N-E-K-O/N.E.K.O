/**
 * render_drift_detector.js — dev-only 渲染漂移检测.
 *
 * 设计背景 (P24 §3.5 / AGENT_NOTES §4.23 #72):
 *   项目累计踩过 6+ 次 "state mutation 后漏调 renderAll / 漏刷 DOM" 类
 *   bug (P09/P11/P12/P13/P16/P17), 记作 §3A B1 漏守. 根因是 state 层
 *   没有把"渲染一致性" 作为硬约束, 依赖开发者"记得" 每次 mutation 后
 *   调 renderAll(). 本 detector 做**运行期 derived-state drift 检测**:
 *   声明 "derived state checker" (函数: 从 store 推导出的预期值 vs
 *   DOM 查出的实际值), 当 state emit 后 DOM 没跟上就 warn.
 *
 * 与 Day 6B `DEBUG_UNLISTENED` / smoke Rule 5 的区别:
 *   - Rule 5 (构建期静态 smoke)    : 查 emit / on 的 drift, **静态**
 *   - DEBUG_UNLISTENED (运行期)   : 查 dead emit, **运行期但无 checker**
 *   - 本 detector (运行期 +checker) : 查 "state 改了但 DOM 没刷", 填剩余空白
 *
 * 不变式 (纯检测, 不修正):
 *   - 检测到漂移时仅 console.warn (加完整 checker context), 不自动 renderAll
 *   - 违反"纯净" 原则时只报警, 让开发者自己判断并修 (renderAll 漏调? 还是
 *     故意 partial update? 还是 checker 本身需要更新?)
 *
 * 触发:
 *   - URL 带 `?dev=1` 或 `window.__DEBUG_RENDER_DRIFT__ = true` 才启用;
 *     生产模式零开销 (主入口直接 return).
 *
 * API:
 *   - initRenderDriftDetector()                   — app.js boot 一次
 *   - registerChecker({ name, event, check })     — 各页 mount 时注册
 *   - unregisterChecker(name)                     — 各页 teardown 时解注 (可选)
 *
 * 每次 emit 后 microtask 执行一遍 subscribe 了这个 event 的 checker,
 * 若 check() 返回 `{ok: false, ...}` 就 warn + 把 drift record push 到
 * `window.__renderDriftLog__` (dev-only, 方便控制台翻查).
 *
 * 注意:
 *   - 不覆盖 contenteditable / textarea 这类 per-keystroke 的 state 变化,
 *     那类场景走 edge-trigger 模式 — 本 detector 专门盯 emit-driven 类
 *   - 每个 checker 每次漂移只 warn 一次 (per (event, name) 对), 直到 check()
 *     返回 ok 才 reset — 避免同一个 bug 刷屏
 */

import { on } from './state.js';

// ── dev-mode gate ──────────────────────────────────────────────────
function isDevRenderDriftMode() {
  if (typeof window === 'undefined') return false;
  if (window.__DEBUG_RENDER_DRIFT__ === true) return true;
  try {
    return new URLSearchParams(window.location.search).get('dev') === '1';
  } catch {
    return false;
  }
}

// ── registry ───────────────────────────────────────────────────────
//
// key: checker name (must be unique), value: { event, check, _lastDriftKey }
// `_lastDriftKey` is the last dedupe key we've warned on; once check()
// returns ok again (or a different drift shape), reset and allow next warn.
const _checkers = new Map();

// per-event ticked flag to avoid scheduling multiple microtasks for the
// same event when several listeners re-emit the same key in one tick
const _scheduled = new Set();

/**
 * 注册一个 derived-state checker.
 *
 * @param {object} spec
 * @param {string} spec.name   unique name, used for unregister and dedupe
 * @param {string} spec.event  state.emit event to subscribe, e.g. 'session:change'
 * @param {() => {ok: boolean, detail?: string, driftKey?: string}} spec.check
 *        same-tick callable; return `{ok: true}` when derived state matches DOM,
 *        `{ok: false, detail, driftKey}` when it doesn't. `driftKey` dedupes
 *        repeated identical drifts (optional; defaults to detail).
 */
export function registerChecker(spec) {
  if (!isDevRenderDriftMode()) return; // prod no-op
  if (!spec || typeof spec.name !== 'string' || typeof spec.event !== 'string'
      || typeof spec.check !== 'function') {
    console.warn('[render-drift] registerChecker: invalid spec', spec);
    return;
  }
  if (_checkers.has(spec.name)) {
    console.warn(`[render-drift] checker '${spec.name}' already registered; overwriting.`);
  }
  _checkers.set(spec.name, { event: spec.event, check: spec.check, _lastDriftKey: null });
}

export function unregisterChecker(name) {
  if (!isDevRenderDriftMode()) return;
  _checkers.delete(name);
}

// ── drift detection core ───────────────────────────────────────────
function _runCheckersForEvent(event) {
  for (const [name, entry] of _checkers.entries()) {
    if (entry.event !== event) continue;
    let result;
    try {
      result = entry.check();
    } catch (err) {
      console.warn(
        `[render-drift] checker '${name}' threw during check:`, err,
        '— treating as non-drift; fix the checker itself.',
      );
      continue;
    }
    if (!result || result.ok === true) {
      if (entry._lastDriftKey !== null) {
        // recovered: reset dedupe so next drift warns again
        entry._lastDriftKey = null;
      }
      continue;
    }
    const driftKey = result.driftKey ?? result.detail ?? 'unknown';
    if (entry._lastDriftKey === driftKey) continue; // same drift, already warned

    entry._lastDriftKey = driftKey;
    const detail = result.detail || '(no detail)';
    console.warn(
      `[render-drift] '${name}' drifted after '${event}': ${detail}. `
      + `Check that the corresponding renderAll / DOM update ran. `
      + `(Once per unique drift shape; see P24 §3.5.)`,
    );
    if (typeof window !== 'undefined') {
      if (!Array.isArray(window.__renderDriftLog__)) {
        window.__renderDriftLog__ = [];
      }
      window.__renderDriftLog__.push({
        name, event, detail, driftKey, at: Date.now(),
      });
    }
  }
}

function _scheduleCheck(event) {
  if (_scheduled.has(event)) return;
  _scheduled.add(event);
  // microtask so the listener (renderAll) has a chance to run first;
  // only *after* it runs do we check whether DOM lines up with store.
  queueMicrotask(() => {
    _scheduled.delete(event);
    _runCheckersForEvent(event);
  });
}

// ── init ───────────────────────────────────────────────────────────
let _initialized = false;

export function initRenderDriftDetector() {
  if (!isDevRenderDriftMode()) return; // prod no-op
  if (_initialized) return;
  _initialized = true;

  // Subscribe to the set of events that derived-state checkers care about.
  // To avoid double-subscribing, we lazily subscribe per-event on first
  // registerChecker... but checkers can also register before init fires
  // (defensive). So we enumerate _known_ events the bus uses plus any
  // already-registered event.
  const knownEvents = new Set([
    'session:change',
    'active_workspace:change',
    'ui_prefs:change',
    'errors:change',
    'clock:change',
    'messages:changed',
    'snapshots:changed',
  ]);
  for (const entry of _checkers.values()) knownEvents.add(entry.event);

  for (const ev of knownEvents) {
    on(ev, () => _scheduleCheck(ev));
  }

  if (typeof window !== 'undefined') {
    window.__renderDrift = {
      listCheckers: () => Array.from(_checkers.keys()),
      runNow: (event) => _runCheckersForEvent(event),
      getLog: () => (window.__renderDriftLog__ || []).slice(),
      clearLog: () => { window.__renderDriftLog__ = []; },
    };
    console.info(
      '[render-drift] detector enabled (dev mode). '
      + 'Use window.__renderDrift.listCheckers() / .runNow() / .getLog() to inspect.',
    );
  }
}
