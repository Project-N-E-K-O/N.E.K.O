/**
 * page_virtual_clock.js — Setup → Virtual Clock 子页 (P06 完整实装).
 *
 * 四块卡片:
 *   1. Live cursor          — 大字显示 clock.now(), 绝对/相对/预设 调整 + Release.
 *   2. Bootstrap             — 会话起点 + initial_last_gap (仅首条消息前用).
 *   3. Per-turn default      — 默认每轮 +Δt, 覆盖模式见 PLAN §关键点 2.
 *   4. Pending / Reset       — 下一轮 stage (delta 或 absolute) + 清零按钮.
 *
 * 显示策略:
 *   - is_real_time=true 时, 每秒用本地 `new Date()` 轻量 tick 显示; 不轮询
 *     后端 (/api/time/cursor 留着供将来 Chat workspace 用).
 *   - 虚拟游标 (is_real_time=false) 的值本身不会自行前进, 所以只需在每次
 *     mutate 后刷新一次.
 *   - tick 通过检查 `subpage.isConnected` 自我熄火, 避免切子页后 setInterval
 *     泄漏 (workspace_setup.js 的 selectPage 只做 innerHTML 清空, 无 unmount
 *     钩子).
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { emit } from '../../core/state.js';
import { el } from '../_dom.js';
import {
  parseDurationText,
  secondsToLabel,
  datetimeLocalValue,
  datetimeLocalToISO,
  formatIsoReadable,
} from '../../core/time_utils.js';

// ── entry ────────────────────────────────────────────────────────────

export async function renderVirtualClockPage(host) {
  host.innerHTML = '';
  host.append(el('h2', {}, i18n('setup.virtual_clock.heading')));
  host.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.intro')));

  // 404 = no session → 静默友好空态 (不算错误).
  const res = await api.get('/api/time', { expectedStatuses: [404] });
  if (res.status === 404) {
    host.append(el('div', { className: 'empty-state' },
      el('h3', {}, i18n('setup.virtual_clock.no_session.heading')),
      el('p', {}, i18n('setup.virtual_clock.no_session.body')),
    ));
    return;
  }
  if (!res.ok) {
    host.append(el('div', { className: 'empty-state err' },
      `${i18n('errors.unknown')}: ${res.error?.message || res.status}`));
    return;
  }

  const ctx = createCtx(host, res.data);
  host.append(
    renderLiveCard(ctx),
    renderBootstrapCard(ctx),
    renderPerTurnCard(ctx),
    renderPendingCard(ctx),
    renderResetCard(ctx),
  );
}

// ── shared context + 刷新整页 ────────────────────────────────────────

function createCtx(host, initial) {
  return {
    host,
    // 最近一次 /api/time 快照; 每个 mutate 都返回完整 clock, 所以就地替换.
    state: initial,
  };
}

/** Replace state + re-render the whole page (simplest + keeps cards in sync). */
function refresh(ctx, newState) {
  ctx.state = newState;
  ctx.host.innerHTML = '';
  ctx.host.append(el('h2', {}, i18n('setup.virtual_clock.heading')));
  ctx.host.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.intro')));
  ctx.host.append(
    renderLiveCard(ctx),
    renderBootstrapCard(ctx),
    renderPerTurnCard(ctx),
    renderPendingCard(ctx),
    renderResetCard(ctx),
  );
}

/**
 * Thin wrapper: ``mutate('/api/time/cursor', 'PUT', body)`` returns true on
 * success (and re-renders the whole page with the fresh clock snapshot),
 * false otherwise. Toasts on error already raised by ``api.js``.
 */
async function mutate(ctx, path, method, body) {
  const res = await api.request(path, { method, body });
  if (!res.ok) return false;
  // P24 §12.5 L2: time_router may return { warning: {...} } when the
  // new cursor rewinds past the last message timestamp. Surface the
  // Chinese message_cn as a warn toast so the user knows subsequent
  // messages will get coerced. We don't abort the mutation — the
  // user already clicked, and coerce + toast on each chat send is
  // the downstream safety net.
  const warning = res.data?.warning;
  if (warning && typeof warning === 'object' && warning.message_cn) {
    toast.warn(warning.message_cn, { duration: 8000 });
  }
  refresh(ctx, res.data);
  // P24 §12.1 event bus audit (2026-04-21): 补 `clock:change` emit —
  // composer:1271 监听此事件重渲染时间显示, 但此前全仓 0 emit (反向 B12
  // 违规). 虚拟时钟页任何成功 mutate (set / advance / bootstrap /
  // per_turn_default / stage_next_turn / reset) 都会产生 clock 变化,
  // composer 如果已 mount 需要同步. payload 传整个新 clock snapshot,
  // composer 也自己再 formatIsoReadable, 所以 payload shape 很宽松.
  emit('clock:change', { clock: res.data?.clock || res.data, source: path });
  return true;
}

// ── Card 1: Live cursor ──────────────────────────────────────────────

function renderLiveCard(ctx) {
  const clock = ctx.state.clock;
  const card = el('div', { className: 'card' });
  card.append(el('h3', {}, i18n('setup.virtual_clock.live.heading')));
  card.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.live.intro')));

  // Big "now" display + live/virtual badge.
  const nowLabel = el('div', { className: 'big-now' }, '—');
  const badge = el('span', {
    className: `badge ${clock.is_real_time ? '' : 'primary'}`,
  }, clock.is_real_time
    ? i18n('setup.virtual_clock.live.real_time_badge')
    : i18n('setup.virtual_clock.live.virtual_badge'));
  card.append(el('div', { className: 'now-row' },
    el('span', { className: 'label' }, i18n('setup.virtual_clock.live.now_label')),
    nowLabel,
    badge,
  ));
  startNowTick(ctx, clock, nowLabel);

  // Absolute set row.
  const absInput = el('input', {
    type: 'datetime-local',
    value: clock.cursor ? datetimeLocalValue(clock.cursor) : '',
  });
  const absStatus = el('span', { className: 'status-line' });
  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.live.absolute_label')),
    absInput,
    el('button', {
      className: 'primary',
      onClick: async () => {
        const iso = datetimeLocalToISO(absInput.value);
        if (!iso) { absStatus.className = 'status-line err'; absStatus.textContent = i18n('setup.virtual_clock.status.invalid_datetime'); return; }
        if (await mutate(ctx, '/api/time/cursor', 'PUT', { absolute: iso })) {
          toast.ok(i18n('setup.virtual_clock.status.saved'));
        }
      },
    }, i18n('setup.virtual_clock.live.set_btn')),
    el('button', {
      onClick: async () => {
        if (await mutate(ctx, '/api/time/cursor', 'PUT', { absolute: null })) {
          toast.ok(i18n('setup.virtual_clock.status.cleared'));
        }
      },
    }, i18n('setup.virtual_clock.live.release_btn')),
    absStatus,
  ));

  // Relative advance row.
  const deltaInput = el('input', { type: 'text', placeholder: '1h30m' });
  const deltaStatus = el('span', { className: 'status-line' });
  async function advanceBy(seconds) {
    if (!Number.isFinite(seconds)) {
      deltaStatus.className = 'status-line err';
      deltaStatus.textContent = i18n('setup.virtual_clock.status.invalid_duration');
      return;
    }
    if (await mutate(ctx, '/api/time/advance', 'POST', { delta_seconds: seconds })) {
      toast.ok(i18n('setup.virtual_clock.status.saved'));
    }
  }
  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.live.advance_label')),
    deltaInput,
    el('button', {
      className: 'primary',
      onClick: () => advanceBy(parseDurationText(deltaInput.value)),
    }, i18n('setup.virtual_clock.live.advance_btn')),
    el('button', { onClick: () => advanceBy(300) }, i18n('setup.virtual_clock.live.preset_plus_5m')),
    el('button', { onClick: () => advanceBy(3600) }, i18n('setup.virtual_clock.live.preset_plus_1h')),
    el('button', { onClick: () => advanceBy(86400) }, i18n('setup.virtual_clock.live.preset_plus_1d')),
    deltaStatus,
  ));
  card.append(el('p', { className: 'muted tiny' }, i18n('setup.virtual_clock.live.delta_hint')));

  return card;
}

function startNowTick(ctx, clock, labelEl) {
  // Stamp immediately so the user never sees "—" after the initial fetch.
  const paint = () => {
    if (clock.is_real_time) {
      labelEl.textContent = formatIsoReadable(new Date().toISOString());
    } else {
      labelEl.textContent = formatIsoReadable(clock.cursor);
    }
  };
  paint();
  // Virtual cursor doesn't self-advance, so no need to tick.
  if (!clock.is_real_time) return;
  const id = setInterval(() => {
    // Self-cleanup: label detached → we've been re-rendered or page unmounted.
    if (!labelEl.isConnected) { clearInterval(id); return; }
    paint();
  }, 1000);
}

// ── Card 2: Bootstrap ───────────────────────────────────────────────

function renderBootstrapCard(ctx) {
  const clock = ctx.state.clock;
  const card = el('div', { className: 'card' });
  card.append(el('h3', {}, i18n('setup.virtual_clock.bootstrap.heading')));
  card.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.bootstrap.intro')));

  const bootInput = el('input', {
    type: 'datetime-local',
    value: datetimeLocalValue(clock.bootstrap_at),
  });
  const gapInput = el('input', {
    type: 'text',
    placeholder: '1h30m',
    value: clock.initial_last_gap_seconds != null ? secondsToLabel(clock.initial_last_gap_seconds) : '',
  });
  const syncToggle = el('input', { type: 'checkbox', checked: true });
  const status = el('span', { className: 'status-line' });

  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.bootstrap.bootstrap_at_label')),
    bootInput,
  ));
  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.bootstrap.initial_gap_label')),
    gapInput,
  ));
  card.append(el('label', { className: 'inline-check' },
    syncToggle,
    ' ',
    i18n('setup.virtual_clock.bootstrap.sync_cursor_label'),
  ));

  card.append(el('div', { className: 'form-row' },
    el('button', {
      className: 'primary',
      onClick: async () => {
        const iso = bootInput.value ? datetimeLocalToISO(bootInput.value) : null;
        if (bootInput.value && !iso) {
          status.className = 'status-line err';
          status.textContent = i18n('setup.virtual_clock.status.invalid_datetime');
          return;
        }
        let gapSeconds = null;
        if (gapInput.value.trim()) {
          gapSeconds = parseDurationText(gapInput.value);
          if (gapSeconds == null || gapSeconds < 0) {
            status.className = 'status-line err';
            status.textContent = i18n('setup.virtual_clock.status.invalid_duration');
            return;
          }
        }
        const body = {
          bootstrap_at: iso,
          initial_last_gap_seconds: gapSeconds,
          sync_cursor: syncToggle.checked,
        };
        if (await mutate(ctx, '/api/time/bootstrap', 'PUT', body)) {
          toast.ok(i18n('setup.virtual_clock.status.saved'));
        }
      },
    }, i18n('setup.virtual_clock.bootstrap.set_btn')),
    el('button', {
      onClick: async () => {
        // Only clear bootstrap_at; keep initial_last_gap unchanged. We
        // pass `null` explicitly (vs. "not set") to signal clearing, and
        // omit initial_last_gap_seconds from the body entirely.
        const res = await api.put('/api/time/bootstrap', { bootstrap_at: null, sync_cursor: false });
        if (res.ok) {
          refresh(ctx, res.data);
          emit('clock:change', { clock: res.data?.clock || res.data, source: '/api/time/bootstrap' });
          toast.ok(i18n('setup.virtual_clock.status.cleared'));
        }
      },
    }, i18n('setup.virtual_clock.bootstrap.clear_bootstrap_btn')),
    el('button', {
      onClick: async () => {
        const res = await api.put('/api/time/bootstrap', { initial_last_gap_seconds: null });
        if (res.ok) {
          refresh(ctx, res.data);
          emit('clock:change', { clock: res.data?.clock || res.data, source: '/api/time/bootstrap' });
          toast.ok(i18n('setup.virtual_clock.status.cleared'));
        }
      },
    }, i18n('setup.virtual_clock.bootstrap.clear_gap_btn')),
    status,
  ));
  card.append(el('p', { className: 'muted tiny' }, i18n('setup.virtual_clock.bootstrap.hint')));

  return card;
}

// ── Card 3: Per-turn default ─────────────────────────────────────────

function renderPerTurnCard(ctx) {
  const clock = ctx.state.clock;
  const card = el('div', { className: 'card' });
  card.append(el('h3', {}, i18n('setup.virtual_clock.per_turn_default.heading')));
  card.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.per_turn_default.intro')));

  // per-turn-default 未设定时, 比起占位符 "—" 更清楚地写明含义
  // ("不自动推进"), 避免测试人员误以为是未加载或错误.
  const isUnset = clock.per_turn_default_seconds == null;
  const currentText = isUnset
    ? i18n('setup.virtual_clock.per_turn_default.unset_value')
    : secondsToLabel(clock.per_turn_default_seconds);
  card.append(el('div', {
    className: isUnset ? 'meta-card-row muted' : 'meta-card-row',
  },
    el('b', {}, `${i18n('setup.virtual_clock.per_turn_default.current_label')}: `),
    currentText,
  ));

  const input = el('input', {
    type: 'text',
    placeholder: '1h30m',
    value: clock.per_turn_default_seconds != null ? secondsToLabel(clock.per_turn_default_seconds) : '',
  });
  const status = el('span', { className: 'status-line' });
  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.per_turn_default.value_label')),
    input,
    el('button', {
      className: 'primary',
      onClick: async () => {
        if (!input.value.trim()) {
          // Empty = clear, same as dedicated button.
          if (await mutate(ctx, '/api/time/per_turn_default', 'PUT', { seconds: null })) {
            toast.ok(i18n('setup.virtual_clock.status.cleared'));
          }
          return;
        }
        const n = parseDurationText(input.value);
        if (n == null || n < 0) {
          status.className = 'status-line err';
          status.textContent = i18n('setup.virtual_clock.status.invalid_duration');
          return;
        }
        if (await mutate(ctx, '/api/time/per_turn_default', 'PUT', { seconds: n })) {
          toast.ok(i18n('setup.virtual_clock.status.saved'));
        }
      },
    }, i18n('setup.virtual_clock.per_turn_default.set_btn')),
    el('button', {
      onClick: async () => {
        if (await mutate(ctx, '/api/time/per_turn_default', 'PUT', { seconds: null })) {
          toast.ok(i18n('setup.virtual_clock.status.cleared'));
        }
      },
    }, i18n('setup.virtual_clock.per_turn_default.clear_btn')),
    status,
  ));
  card.append(el('p', { className: 'muted tiny' }, i18n('setup.virtual_clock.per_turn_default.hint')));
  return card;
}

// ── Card 4: Pending stage ───────────────────────────────────────────

function renderPendingCard(ctx) {
  const pending = ctx.state.clock.pending || {};
  const card = el('div', { className: 'card' });
  card.append(el('h3', {}, i18n('setup.virtual_clock.pending.heading')));
  card.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.pending.intro')));

  const row = el('div', { className: 'meta-card' });
  if (pending.absolute) {
    row.append(el('div', { className: 'meta-card-row' },
      el('b', {}, `${i18n('setup.virtual_clock.pending.pending_abs_label')}: `),
      formatIsoReadable(pending.absolute),
    ));
  } else if (pending.advance_seconds != null) {
    row.append(el('div', { className: 'meta-card-row' },
      el('b', {}, `${i18n('setup.virtual_clock.pending.pending_delta_label')}: `),
      secondsToLabel(pending.advance_seconds),
    ));
  } else {
    row.append(el('div', { className: 'meta-card-row muted' },
      i18n('setup.virtual_clock.pending.none_label')));
  }
  card.append(row);

  const deltaInput = el('input', { type: 'text', placeholder: '1h30m' });
  const absInput = el('input', { type: 'datetime-local' });
  const status = el('span', { className: 'status-line' });

  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.pending.delta_input_label')),
    deltaInput,
    el('button', {
      className: 'primary',
      onClick: async () => {
        const n = parseDurationText(deltaInput.value);
        if (n == null) {
          status.className = 'status-line err';
          status.textContent = i18n('setup.virtual_clock.status.invalid_duration');
          return;
        }
        if (await mutate(ctx, '/api/time/stage_next_turn', 'POST', { delta_seconds: n })) {
          toast.ok(i18n('setup.virtual_clock.status.saved'));
        }
      },
    }, i18n('setup.virtual_clock.pending.stage_delta_btn')),
  ));
  card.append(el('div', { className: 'form-row' },
    el('label', {}, i18n('setup.virtual_clock.pending.abs_input_label')),
    absInput,
    el('button', {
      className: 'primary',
      onClick: async () => {
        const iso = datetimeLocalToISO(absInput.value);
        if (!iso) {
          status.className = 'status-line err';
          status.textContent = i18n('setup.virtual_clock.status.invalid_datetime');
          return;
        }
        if (await mutate(ctx, '/api/time/stage_next_turn', 'POST', { absolute: iso })) {
          toast.ok(i18n('setup.virtual_clock.status.saved'));
        }
      },
    }, i18n('setup.virtual_clock.pending.stage_abs_btn')),
    el('button', {
      onClick: async () => {
        const res = await api.request('/api/time/stage_next_turn', { method: 'DELETE' });
        if (res.ok) {
          refresh(ctx, res.data);
          emit('clock:change', { clock: res.data?.clock || res.data, source: '/api/time/stage_next_turn' });
          toast.ok(i18n('setup.virtual_clock.status.cleared'));
        }
      },
    }, i18n('setup.virtual_clock.pending.clear_btn')),
    status,
  ));

  return card;
}

// ── Card 5: Reset ───────────────────────────────────────────────────

function renderResetCard(ctx) {
  const card = el('div', { className: 'card' });
  card.append(el('h3', {}, i18n('setup.virtual_clock.reset.heading')));
  card.append(el('p', { className: 'muted' }, i18n('setup.virtual_clock.reset.intro')));
  card.append(el('button', {
    onClick: async () => {
      if (!confirm(i18n('setup.virtual_clock.reset.confirm'))) return;
      if (await mutate(ctx, '/api/time/reset', 'POST', {})) {
        toast.ok(i18n('setup.virtual_clock.status.cleared'));
      }
    },
  }, i18n('setup.virtual_clock.reset.reset_btn')));
  return card;
}
