/**
 * page_ui.js — Settings → UI 偏好.
 *
 * 本期 (P04) 仅占位. 真正的接线在:
 *   - Theme  → 当前只支持暗色, 浅色将在 UI 迭代中加入 (`@media prefers-color-scheme`).
 *   - Snapshot limit / fold defaults → P18 / P08 落地后才有意义.
 * 唯一立刻可用的是"清除当前会话的 localStorage fold 键", 帮测试人员重置折叠状态.
 */

import { i18n } from '../../core/i18n.js';
import { get as getStoreField } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';

export function renderUiPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.ui.heading')),
    el('p', { className: 'intro' }, i18n('settings.ui.intro')),
  );

  // Language.
  const langSel = el('select', { disabled: true },
    el('option', { value: 'zh-CN' }, '简体中文'),
  );
  host.append(el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.language_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.language_only_zh')),
    langSel,
  ));

  // Theme.
  const themeSel = el('select', { disabled: true },
    el('option', { value: 'dark', selected: true }, i18n('settings.ui.theme_dark')),
    el('option', { value: 'light' }, i18n('settings.ui.theme_light_todo')),
  );
  host.append(el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.theme_label')),
    themeSel,
  ));

  // Snapshot limit (P18 占位).
  host.append(el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.snapshot_limit_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.snapshot_limit_hint')),
    field('', el('input', { type: 'number', value: 30, disabled: true, min: '1' })),
  ));

  // 默认折叠策略占位.
  host.append(el('div', { className: 'card' },
    el('h3', {}, i18n('settings.ui.fold_defaults_label')),
    el('div', { className: 'card-hint' }, i18n('settings.ui.fold_defaults_hint')),
    el('button', {
      style: { marginTop: '8px' },
      onClick: () => clearFoldKeys(),
    }, i18n('settings.ui.reset_fold')),
  ));
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
