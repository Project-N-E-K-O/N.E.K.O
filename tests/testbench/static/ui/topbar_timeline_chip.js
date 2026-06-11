/**
 * topbar_timeline_chip.js — Timeline 顶栏 chip (P18).
 *
 * 顶栏 "时间轴" chip 的正式实装, 替代 `topbar.js` 里的占位 (会 toast
 * "P18 未实装"). 设计与 `topbar_stage_chip.js` 对齐: chip 本体点一下
 * 开/关下拉面板, 面板里是最近若干条快照倒序列表 + 每行的 [回退] 按钮.
 *
 * 职责边界:
 *   - 只做**快速回退** (UX: 我刚想撤销最后几步). 完整管理 (重命名 / 批量
 *     删除 / 查看 payload / 清空) 留给 Diagnostics → Snapshots 子页;
 *     chip 底部有 [打开完整时间线] 按钮跳过去.
 *   - 不自己维护订阅抬升的复杂状态 — 每次打开面板都 GET 一次
 *     `/api/snapshots`, 10 条以内直接全量展示. 背后反正有 debounce 合
 *     并过了, 同一 trigger 快速触发不会刷出无穷多条.
 *   - 跟 stage chip 一样, rewind 走 `session_operation(REWINDING)` 独占
 *     锁, 后端实现见 `pipeline/snapshot_store.rewind_to`.
 *
 * 事件:
 *   - 订阅 `session:change`: 切会话 → 清空缓存并关面板.
 *   - 订阅 `snapshots:changed`: 当 diagnostics → Snapshots 页或其它地方
 *     增删快照后, chip 若打开则重拉, 否则只置 dirty 等下次打开时刷.
 *   - 发布 `snapshots:changed`: 自建快照 / 回退成功后, 让 Snapshots 页
 *     和其它订阅方重新拉取.
 *   - 回退成功后也发 `stage:needs_refresh` — 回退会把 stage_state 也一
 *     起恢复, topbar stage chip 需要重拉才能看到.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { store, set, on, emit } from '../core/state.js';
import { el } from './_dom.js';

// 面板里最多显示这么多条 (倒序, 最新在上). 超过的折叠成一条"还有 N 条
// 在完整页" 提示. 10 条对大多数"想撤销最近几步"的 UX 足够宽了.
const PANEL_ROW_LIMIT = 10;

export function mountTimelineChip(host) {
  const container = el('div', { className: 'timeline-chip-wrap dropdown' });
  host.append(container);

  // 缓存: 上次 GET /api/snapshots 的响应. 面板打开时会刷新, 关着只在
  // session:change / snapshots:changed 时置 dirty 等下次打开.
  let lastItems = [];       // metadata 列表 (oldest → newest)
  let lastMaxHot = null;
  let lastDebounceSeconds = null;
  let lastError = null;
  let loading = false;
  let dirty = true;         // 初始未拉, 第一次开面板时要拉
  let fetchInflight = null;

  // 下拉面板 — 与 stage chip 的 dropdown-menu 样式同源.
  const panel = el('div', {
    className: 'dropdown-menu timeline-panel',
    'data-align': 'right',
  });
  let panelOpen = false;

  const chipSlot = el('div', { className: 'timeline-chip-slot' });
  container.append(chipSlot, panel);

  function openPanel() {
    if (panelOpen) return;
    panelOpen = true;
    panel.classList.add('open');
    if (dirty) {
      refresh().catch(() => {});
    } else {
      renderPanel();
    }
  }
  function closePanel() {
    if (!panelOpen) return;
    panelOpen = false;
    panel.classList.remove('open');
  }

  // 文档级点外关闭 + ESC 关.
  document.addEventListener('click', (ev) => {
    if (!panelOpen) return;
    if (container.contains(ev.target)) return;
    closePanel();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closePanel();
  });

  // ── 渲染 ─────────────────────────────────────────────────────────

  function renderChip() {
    chipSlot.innerHTML = '';
    const session = store.session;
    const total = lastItems.length;
    const backupCount = lastItems.filter((it) => it.is_backup).length;

    let body;
    if (!session) {
      body = `${i18n('topbar.timeline.label')}: ${i18n('topbar.timeline.chip_no_session')}`;
    } else if (total === 0) {
      body = `${i18n('topbar.timeline.label')}: ${i18n('topbar.timeline.chip_placeholder')}`;
    } else {
      body = `${i18n('topbar.timeline.label')}: ${i18n('topbar.timeline.chip_summary', total, backupCount)}`;
    }

    const chip = el('button', {
      className: 'chip timeline-chip' + (session ? '' : ' muted'),
      title: session
        ? (panelOpen
          ? i18n('topbar.timeline.chip_collapse_hint')
          : i18n('topbar.timeline.chip_expand_hint'))
        : i18n('snapshots.toast.no_session'),
      onClick: (ev) => {
        ev.stopPropagation();
        if (!session) {
          toast.info(i18n('snapshots.toast.no_session'));
          return;
        }
        if (panelOpen) closePanel();
        else openPanel();
      },
    },
    el('span', {}, body),
    el('span', { className: 'caret' }, '▾'),
    );
    chipSlot.append(chip);
  }

  function renderPanel() {
    panel.innerHTML = '';
    if (!store.session) {
      panel.append(el('div', { className: 'timeline-panel-empty' },
        i18n('snapshots.toast.no_session')));
      return;
    }
    if (loading) {
      panel.append(el('div', { className: 'timeline-panel-empty' },
        i18n('topbar.timeline.panel_loading')));
      return;
    }
    if (lastError) {
      panel.append(el('div', { className: 'timeline-panel-empty' },
        i18n('topbar.timeline.panel_load_failed', lastError)));
      return;
    }

    // 面板标题 + 摘要.
    const header = el('div', { className: 'timeline-panel-header' });
    header.append(
      el('div', { className: 'timeline-panel-title' },
        i18n('topbar.timeline.panel_title')),
      el('div', { className: 'timeline-panel-summary' },
        i18n('topbar.timeline.panel_summary',
          lastItems.filter((it) => !it.is_compressed).length,
          lastItems.filter((it) => it.is_compressed).length,
          lastMaxHot ?? '?')),
    );
    panel.append(header);

    // 机制速记 (一行, 不折叠) — 详细解释交给 Diagnostics 子页.
    // 参数用 `/api/snapshots` 返回的 max_hot / debounce_seconds, 真实
    // 反映当前实例配置, 避免 UI 和后端可调参数漂移.
    panel.append(el('div', { className: 'timeline-panel-mechanism' },
      i18n('topbar.timeline.panel_mechanism_fmt',
        lastMaxHot ?? 30,
        lastDebounceSeconds ?? 5)));

    if (lastItems.length === 0) {
      panel.append(el('div', { className: 'timeline-panel-empty' },
        i18n('topbar.timeline.panel_empty')));
    } else {
      // 倒序 (最新在上). PANEL_ROW_LIMIT 截断 — 超过的给一条"查看更多"跳转.
      const reversed = [...lastItems].reverse();
      const visible = reversed.slice(0, PANEL_ROW_LIMIT);
      const extraCount = reversed.length - visible.length;

      const hint = el('div', { className: 'timeline-panel-hint' },
        i18n('topbar.timeline.panel_hint'));
      panel.append(hint);

      const listEl = el('div', { className: 'timeline-panel-list' });
      for (const item of visible) {
        listEl.append(renderRow(item));
      }
      panel.append(listEl);

      if (extraCount > 0) {
        const more = el('button', {
          className: 'timeline-panel-more',
          onClick: (ev) => {
            ev.stopPropagation();
            openFullPage();
          },
        }, i18n('topbar.timeline.show_more_fmt', extraCount));
        panel.append(more);
      }
    }

    // 底部动作栏: 手动建快照 + 打开完整页.
    const footer = el('div', { className: 'timeline-panel-footer' });
    const manualBtn = el('button', {
      className: 'chip ghost tiny',
      onClick: (ev) => {
        ev.stopPropagation();
        createManual();
      },
    }, i18n('topbar.timeline.panel_manual_btn'));
    const fullBtn = el('button', {
      className: 'chip ghost tiny',
      onClick: (ev) => {
        ev.stopPropagation();
        openFullPage();
      },
    }, i18n('topbar.timeline.panel_open_full'));
    footer.append(manualBtn, fullBtn);
    panel.append(footer);
  }

  function renderRow(item) {
    const row = el('div', {
      className: 'timeline-panel-row'
        + (item.is_backup ? ' backup' : '')
        + (item.is_compressed ? ' cold' : ''),
    });

    // 标签 + 时间.
    const main = el('div', { className: 'timeline-panel-row-main' });
    main.append(
      el('div', {
        className: 'timeline-panel-row-label u-truncate',
        title: item.label || '(unnamed)',
      }, item.label || '(unnamed)'),
      el('div', { className: 'timeline-panel-row-meta' },
        i18n(`snapshots.trigger.${item.trigger}`) || item.trigger,
        ' · ',
        formatCreatedAt(item.created_at),
        ' · ',
        `${item.message_count} msg`,
      ),
    );
    row.append(main);

    // Badges.
    if (item.is_backup || item.is_compressed) {
      const badges = el('div', { className: 'timeline-panel-row-badges' });
      if (item.is_backup) {
        badges.append(el('span', { className: 'badge subtle' },
          i18n('topbar.timeline.panel_backup_badge')));
      }
      if (item.is_compressed) {
        badges.append(el('span', { className: 'badge subtle' },
          i18n('topbar.timeline.panel_compressed_badge')));
      }
      row.append(badges);
    }

    // 操作.
    const actions = el('div', { className: 'timeline-panel-row-actions' });
    actions.append(el('button', {
      className: 'chip ghost tiny',
      onClick: (ev) => {
        ev.stopPropagation();
        doRewind(item);
      },
    }, i18n('topbar.timeline.panel_row_rewind')));
    row.append(actions);

    return row;
  }

  function formatCreatedAt(iso) {
    if (!iso) return '';
    // 只显示 HH:MM:SS — 顶栏 chip 空间小, 日期部分对"最近几步"UX 冗余.
    const m = /T(\d\d:\d\d:\d\d)/.exec(iso);
    return m ? m[1] : iso;
  }

  // ── 数据流 ────────────────────────────────────────────────────────

  async function refresh() {
    if (!store.session) {
      lastItems = [];
      lastMaxHot = null;
      lastDebounceSeconds = null;
      lastError = null;
      dirty = false;
      renderChip();
      if (panelOpen) renderPanel();
      return;
    }
    if (fetchInflight) return fetchInflight;

    loading = true;
    lastError = null;
    if (panelOpen) renderPanel();

    fetchInflight = (async () => {
      const res = await api.get('/api/snapshots', { expectedStatuses: [404] });
      loading = false;
      if (res.ok) {
        lastItems = Array.isArray(res.data?.items) ? res.data.items : [];
        lastMaxHot = res.data?.max_hot ?? null;
        lastDebounceSeconds = res.data?.debounce_seconds ?? null;
        lastError = null;
        dirty = false;
      } else {
        lastItems = [];
        lastMaxHot = null;
        lastDebounceSeconds = null;
        lastError = res.error?.message || `HTTP ${res.status}`;
      }
      renderChip();
      if (panelOpen) renderPanel();
    })().finally(() => { fetchInflight = null; });
    return fetchInflight;
  }

  // ── 动作 ──────────────────────────────────────────────────────────

  async function createManual() {
    if (!store.session) {
      toast.info(i18n('snapshots.toast.no_session'));
      return;
    }
    const raw = window.prompt(i18n('snapshots.prompt.manual_label'), '');
    // 用户取消 prompt 返回 null; 留空 ('') 代表让后端自动命名.
    if (raw === null) return;
    const label = raw.trim() || null;
    const res = await api.post('/api/snapshots', { label });
    if (res.ok) {
      const meta = res.data?.item;
      toast.ok(i18n('snapshots.toast.created_fmt', meta?.label || '(unnamed)'));
      emit('snapshots:changed', { reason: 'manual_create', id: meta?.id });
      refresh().catch(() => {});
    } else {
      toast.err(i18n('snapshots.toast.create_failed_fmt',
        res.error?.message || `HTTP ${res.status}`));
    }
  }

  async function doRewind(item) {
    if (!store.session) {
      toast.info(i18n('snapshots.toast.no_session'));
      return;
    }
    const ok = window.confirm(
      i18n('snapshots.prompt.rewind_confirm', item.label || item.id));
    if (!ok) return;
    const res = await api.post(`/api/snapshots/${item.id}/rewind`, {});
    if (res.ok) {
      const dropped = res.data?.dropped_count ?? 0;
      toast.ok(i18n('snapshots.toast.rewound_fmt',
        item.label || item.id, dropped));
      closePanel();
      // ⚠️ P24 sweep (2026-04-22, §4.27 #105 同族 sweep): rewind 到早期快照
      // (persona.character_name 空 / memory 空 / messages 空) 会复现 New
      // Session 级联风暴 — 所有 session:change 订阅者并发 fetch empty-state
      // 端点 → 409/500 → emit('http:error') → errors_bus 异步 cascade →
      // 浏览器烧穿. LESSONS_LEARNED §7 #20 量化判据 5 项 (messages /
      // session.id / character_name / memory 任一清空) rewind 天生可能全
      // 命中, 不允许 surgical. 对齐 Hard Reset / New Session / Load
      // session 的 reload 模式, 彻底消灭 empty-state 订阅路径.
      // 原 surgical 代码 (set('session', sres.data) + snapshots:changed /
      // stage:needs_refresh emit) 删除, 单次 reload 一站式刷新整个页面.
      setTimeout(() => {
        try { window.location.reload(); }
        catch { /* jsdom / headless */ }
      }, 300);
    } else {
      toast.err(i18n('snapshots.toast.rewind_failed_fmt',
        res.error?.message || `HTTP ${res.status}`));
    }
  }

  function openFullPage() {
    closePanel();
    set('active_workspace', 'diagnostics');
    emit('diagnostics:navigate', { subpage: 'snapshots' });
  }

  // ── 初始化 ────────────────────────────────────────────────────────

  renderChip();
  // 启动时拉一次, 这样 chip 可以显示 "N 条" 而不是永远"无快照".
  refresh().catch(() => {});

  on('session:change', () => {
    lastItems = [];
    lastMaxHot = null;
    lastDebounceSeconds = null;
    lastError = null;
    dirty = true;
    closePanel();
    refresh().catch(() => {});
  });

  // 外部来源改了快照 (Diagnostics 页增删重命名, 或 rewind): chip 若打开
  // 就立即刷新, 关着的话标 dirty 等下次打开时拉.
  on('snapshots:changed', () => {
    dirty = true;
    if (panelOpen) refresh().catch(() => {});
    else {
      // 关着也拉一次轻量的 chip 摘要 — 否则 chip 文字会滞后.
      // GET /api/snapshots 本身不重 (只有 metadata), 所以允许.
      refresh().catch(() => {});
    }
  });
}
