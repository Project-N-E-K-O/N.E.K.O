/**
 * page_ui.js — Settings → UI 偏好.
 *
 * P24 Day 7 已接线 (2026-04-22):
 *   - Snapshot limit + 合并窗口 (§12.3.A #3): 真实 input + 保存按钮,
 *     POST /api/snapshots/config. Session-scoped (单会话模型, 无
 *     活跃会话时 input disabled + 提示文案).
 *   - 默认折叠策略表 (§12.3.A #4): 5 种 CollapsibleBlock 类型各一行
 *     (mode: auto/open/closed + threshold input). 持久化到
 *     LocalStorage `testbench:fold_defaults` + 同步写 store.ui_prefs
 *     给订阅者 (LESSONS #20 + §4.23 #78: 消费端**不订阅** ui_prefs:change,
 *     下次 CollapsibleBlock 挂载时读一次, 切页面/刷新后生效).
 *   - 重置当前会话的 fold keys (旧功能保留, 单独按钮).
 *
 * 仍 disabled 的子集 (有设计理由, 非 P24 scope):
 *   - Language switcher: i18n 框架完整但多语种文案未翻译, 非 P24 scope
 *   - Theme dark/light: light palette CSS 工作量 >2d, 非 P24 scope
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { get as getStoreField, set as setStore, store } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';

// ── fold defaults (client-side only) ────────────────────────────────

const FOLD_LS_KEY = 'testbench:fold_defaults';

/** CollapsibleBlock 使用场景白名单. 新增类型时同步加一行. */
const FOLD_TYPES = [
  { id: 'chat_message',   defaultMode: 'auto',   defaultThreshold: 500 },
  { id: 'log_entry',      defaultMode: 'closed', defaultThreshold: 200 },
  { id: 'error_entry',    defaultMode: 'auto',   defaultThreshold: 400 },
  { id: 'preview_panel',  defaultMode: 'auto',   defaultThreshold: 600 },
  { id: 'eval_drawer',    defaultMode: 'auto',   defaultThreshold: 800 },
];

/** 读 LS + 填默认值, 返回完整 fold_defaults map. */
function readFoldDefaults() {
  let parsed = {};
  try {
    const raw = localStorage.getItem(FOLD_LS_KEY);
    if (raw) parsed = JSON.parse(raw) || {};
  } catch { /* 坏 JSON 当空对象处理, 不抛异常打破 Settings 页. */ }
  const result = {};
  for (const t of FOLD_TYPES) {
    const stored = parsed[t.id] || {};
    result[t.id] = {
      mode: ['auto', 'open', 'closed'].includes(stored.mode) ? stored.mode : t.defaultMode,
      threshold: Number.isFinite(stored.threshold) && stored.threshold > 0
        ? Math.max(1, Math.min(10000, Math.floor(stored.threshold)))
        : t.defaultThreshold,
    };
  }
  return result;
}

/** 写 LS 并同步 store.ui_prefs.fold_defaults (给将来订阅者用; 当前无). */
function persistFoldDefaults(map) {
  try {
    localStorage.setItem(FOLD_LS_KEY, JSON.stringify(map));
  } catch (err) {
    toast.err('保存失败 (localStorage)', { message: String(err) });
    return;
  }
  // 合并进 store.ui_prefs 而不是覆盖, 保留其它子字段.
  const current = store.ui_prefs || {};
  setStore('ui_prefs', { ...current, fold_defaults: map });
}

// ── render ──────────────────────────────────────────────────────────

export function renderUiPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.ui.heading')),
    el('p', { className: 'intro' }, i18n('settings.ui.intro')),
  );

  host.append(renderLanguageCard());
  host.append(renderThemeCard());
  host.append(renderSnapshotConfigCard());
  host.append(renderFoldDefaultsCard());
  host.append(renderResetFoldCard());
}

function renderLanguageCard() {
  const langSel = el('select', { disabled: true },
    el('option', { value: 'zh-CN' }, '简体中文'),
  );
  return el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.language_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.language_only_zh')),
    langSel,
  );
}

function renderThemeCard() {
  const themeSel = el('select', { disabled: true },
    el('option', { value: 'dark', selected: true }, i18n('settings.ui.theme_dark')),
    el('option', { value: 'light' }, i18n('settings.ui.theme_light_todo')),
  );
  return el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.theme_label')),
    themeSel,
  );
}

function renderSnapshotConfigCard() {
  const card = el('div', { className: 'card' });
  const body = el('div', { className: 'settings-snapshot-config' });
  card.append(
    el('h3', {}, i18n('settings.ui.snapshot_limit_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.snapshot_limit_hint')),
    body,
  );

  // Initial placeholder until fetch resolves.
  body.append(el('div', { className: 'hint' }, '…'));

  loadAndRenderSnapshotConfig(body).catch((err) => {
    body.innerHTML = '';
    body.append(el('div', { className: 'hint danger' },
      `加载失败: ${err?.message || err}`));
  });

  return card;
}

/**
 * Snapshot config 的 "出厂默认值" — 用户填非法值 / 触发自动重置时回退到
 * 这里. 与后端 ``pipeline/snapshot_store.py`` 的 ``DEFAULT_MAX_HOT=30`` /
 * ``DEFAULT_DEBOUNCE_SECONDS=5`` 保持同源 (手动镜像, 因为前端没有直接
 * 读后端常量的通道).
 */
const SNAPSHOT_CONFIG_DEFAULTS = Object.freeze({
  maxHot: 30,
  debounceSeconds: 5.0,
});

async function loadAndRenderSnapshotConfig(body) {
  // 无 session 场景: endpoint 返 404 (NoActiveSession). UI 显示提示
  // + 控件 disabled, 用户需先建会话才能改.
  const res = await api.get('/api/snapshots', { expectedStatuses: [404] });

  body.innerHTML = '';

  const hasSession = res.ok && res.data;
  const maxHot = hasSession ? (res.data.max_hot ?? SNAPSHOT_CONFIG_DEFAULTS.maxHot)
    : SNAPSHOT_CONFIG_DEFAULTS.maxHot;
  const debounce = hasSession ? (res.data.debounce_seconds ?? SNAPSHOT_CONFIG_DEFAULTS.debounceSeconds)
    : SNAPSHOT_CONFIG_DEFAULTS.debounceSeconds;

  if (!hasSession) {
    body.append(
      el('div', { className: 'hint' },
        i18n('settings.ui.snapshot_limit_no_session')),
    );
  }

  const maxHotInput = el('input', {
    type: 'number',
    min: '1',
    max: '500',
    step: '1',
    value: String(maxHot),
    disabled: !hasSession,
    style: { width: '120px' },
  });
  const debounceInput = el('input', {
    type: 'number',
    min: '0',
    max: '3600',
    step: '0.5',
    value: String(debounce),
    disabled: !hasSession,
    style: { width: '120px' },
  });

  // 2026-04-22 验收期反馈修: "填入非法值之后正常报错, 但是我希望能够
  // 自动重置到默认值" — 错值场景下不让用户"看着一个错值不知道怎么办",
  // 直接回退到 `SNAPSHOT_CONFIG_DEFAULTS`, 并用 toast.warn 说明.
  // 为了消除 "保存按钮太宽" 的视觉 bug, 把按钮放一个 `.form-row-actions`
  // 容器里并限制 inline-block 宽度 + padding (见下方 CSS 块).
  const saveBtn = el('button', {
    className: 'primary',
    disabled: !hasSession,
    onClick: async () => {
      const mhRaw = Number(maxHotInput.value);
      const dsRaw = Number(debounceInput.value);

      // 分别判断两个字段, 被改回默认值的那个让用户看到确切操作.
      let mh = mhRaw;
      let ds = dsRaw;
      const resetFields = [];
      if (!Number.isFinite(mhRaw) || mhRaw < 1 || mhRaw > 500) {
        mh = SNAPSHOT_CONFIG_DEFAULTS.maxHot;
        maxHotInput.value = String(mh);
        resetFields.push(`max_hot → ${mh}`);
      } else {
        mh = Math.floor(mhRaw);
        // normalise 整数形式, 避免用户输入 "30.5" 被后端四舍五入产生
        // "保存后 UI 显示的值跟输入不一致" 的惊讶.
        if (mh !== mhRaw) maxHotInput.value = String(mh);
      }
      if (!Number.isFinite(dsRaw) || dsRaw < 0 || dsRaw > 3600) {
        ds = SNAPSHOT_CONFIG_DEFAULTS.debounceSeconds;
        debounceInput.value = String(ds);
        resetFields.push(`debounce_seconds → ${ds}`);
      }
      if (resetFields.length > 0) {
        toast.warn(
          i18n('settings.ui.snapshot_limit_reset_to_default_fmt',
            resetFields.join(', ')),
          { duration: 5000 },
        );
        // 重置的值若等于当前后端值就不用走网络, 但为了让用户明确看到
        // "已经保存为默认了" 的反馈, 无条件继续请求 (幂等安全).
      }

      saveBtn.disabled = true;
      try {
        const saveRes = await api.post('/api/snapshots/config', {
          max_hot: mh,
          debounce_seconds: ds,
        }, { expectedStatuses: [400, 404] });
        if (saveRes.ok) {
          toast.ok(i18n('settings.ui.snapshot_limit_save_ok_fmt',
            saveRes.data?.max_hot ?? mh,
            saveRes.data?.debounce_seconds ?? ds));
          // 用后端返回值回填 (保证服务器认定的是最终值)
          maxHotInput.value = String(saveRes.data?.max_hot ?? mh);
          debounceInput.value = String(saveRes.data?.debounce_seconds ?? ds);
        } else {
          toast.err(i18n('settings.ui.snapshot_limit_save_err_fmt',
            saveRes.error?.message || `HTTP ${saveRes.status}`));
        }
      } finally {
        saveBtn.disabled = false;
      }
    },
  }, i18n('common.save') || '保存');

  // 保存按钮容器: flex 不让 button 撑满; 用 `.form-row-actions` 已有样式
  // (display: flex + gap + justify-content: flex-start). 2026-04-22 修
  // 验收反馈 "保存按钮的宽度填满了整个容器": 原本按钮直接作 body 的
  // block 子元素 + 上边没限制 width → 继承父宽度. 放进 flex 容器后按钮
  // 按自身 content 收缩到正常大小.
  const actionsRow = el('div', {
    className: 'form-row-actions',
    style: { marginTop: '8px' },
  }, saveBtn);

  body.append(
    field(i18n('settings.ui.snapshot_limit_label'), maxHotInput),
    field(i18n('settings.ui.snapshot_debounce_label'), debounceInput),
    el('div', { className: 'card-hint', style: { marginTop: '4px' } },
      i18n('settings.ui.snapshot_debounce_hint')),
    actionsRow,
  );
}

function renderFoldDefaultsCard() {
  const card = el('div', { className: 'card' });
  card.append(
    el('h3', {}, i18n('settings.ui.fold_defaults_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.fold_defaults_hint')),
  );

  const current = readFoldDefaults();
  const tableHost = el('div', { className: 'settings-fold-table' });
  card.append(tableHost);

  for (const t of FOLD_TYPES) {
    const row = el('div', { className: 'settings-fold-row' });
    const label = el('div', { className: 'settings-fold-row-label' },
      i18n(`settings.ui.fold_defaults_row.${t.id}`) || t.id);

    const modeSel = el('select', {
      onChange: (ev) => {
        current[t.id].mode = ev.target.value;
        persistFoldDefaults(current);
      },
    },
      el('option', { value: 'auto' },
        i18n('settings.ui.fold_defaults_mode.auto')),
      el('option', { value: 'open' },
        i18n('settings.ui.fold_defaults_mode.open')),
      el('option', { value: 'closed' },
        i18n('settings.ui.fold_defaults_mode.closed')),
    );
    modeSel.value = current[t.id].mode;

    const thInput = el('input', {
      type: 'number',
      min: '1',
      max: '10000',
      step: '50',
      value: String(current[t.id].threshold),
      style: { width: '100px' },
      onChange: (ev) => {
        const v = Number(ev.target.value);
        if (Number.isFinite(v) && v >= 1 && v <= 10000) {
          current[t.id].threshold = Math.floor(v);
          persistFoldDefaults(current);
        }
      },
    });
    const thLabel = el('span', {
      className: 'settings-fold-row-threshold-label',
      style: {
        opacity: modeSel.value === 'auto' ? '1' : '0.45',
      },
    }, i18n('settings.ui.fold_defaults_threshold_label'));

    // 让阈值 input 只在 mode === 'auto' 时活跃. 其它模式视觉弱化但不禁
    // 用 — 用户切回 auto 时立即恢复, 不丢失上次输入值.
    const updateThresholdAffordance = () => {
      const dim = modeSel.value !== 'auto';
      thInput.style.opacity = dim ? '0.45' : '1';
      thLabel.style.opacity = dim ? '0.45' : '1';
    };
    updateThresholdAffordance();
    modeSel.addEventListener('change', updateThresholdAffordance);

    row.append(label, modeSel, thLabel, thInput);
    tableHost.append(row);
  }

  return card;
}

function renderResetFoldCard() {
  return el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.reset_fold')),
    el('button', {
      style: { marginTop: '8px' },
      onClick: () => clearFoldKeys(),
    }, i18n('settings.ui.reset_fold')),
  );
}

function clearFoldKeys() {
  const session = getStoreField('session') || {};
  const sessionId = session.id || '';
  const prefix = sessionId ? `fold:${sessionId}:` : 'fold:';
  const toRemove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) toRemove.push(k);
  }
  for (const k of toRemove) localStorage.removeItem(k);
  toast.ok(i18n('settings.ui.reset_fold_ok'), { message: `${toRemove.length} keys removed` });
}
