/**
 * page_providers.js — Settings → Providers 只读视图.
 *
 * 展示 config/api_providers.json → assist_api_providers 的所有条目.
 * 本页 **不写入** — 修改请直接编辑 JSON 文件 (会被主 app 使用).
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { el } from '../_dom.js';

export async function renderProvidersPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.providers.heading')),
    el('p', { className: 'intro' }, i18n('settings.providers.intro')),
  );

  const res = await api.get('/api/config/providers');
  if (!res.ok) {
    host.append(el('div', { className: 'empty-state' }, i18n('errors.server', res.status)));
    return;
  }

  const providers = res.data.providers || [];
  const card = el('div', { className: 'card' });
  const table = el('table', { className: 'tbl' });
  table.append(el('thead', {},
    el('tr', {},
      el('th', {}, i18n('settings.providers.columns.key')),
      el('th', {}, i18n('settings.providers.columns.name')),
      el('th', {}, i18n('settings.providers.columns.base_url')),
      el('th', {}, i18n('settings.providers.columns.conversation_model')),
      el('th', {}, i18n('settings.providers.columns.summary_model')),
      el('th', {}, i18n('settings.providers.columns.api_key')),
    ),
  ));
  const tbody = el('tbody', {});
  for (const p of providers) {
    tbody.append(el('tr', {},
      el('td', { className: 'tbl-key-field' }, p.key,
        p.is_free_version
          ? el('span', { className: 'badge info', style: { marginLeft: '6px' } },
              i18n('settings.providers.free_tag'))
          : null),
      el('td', {}, p.name),
      el('td', { className: 'mono', style: { fontSize: '12px' } }, p.base_url || '—'),
      el('td', {}, p.suggested_models?.conversation || '—'),
      el('td', {}, p.suggested_models?.summary || '—'),
      el('td', {}, renderKeyCell(p)),
    ));
  }
  table.append(tbody);
  card.append(table);
  host.append(card);
}

function renderKeyCell(p) {
  if (p.is_free_version) return el('span', { className: 'badge info' }, 'free');
  if (!p.api_key_field) return el('span', { className: 'muted' }, '—');
  if (p.api_key_configured) {
    return el('span', { className: 'badge ok', title: p.api_key_field },
      i18n('settings.providers.has_key'));
  }
  return el('span', { className: 'badge err', title: p.api_key_field },
    i18n('settings.providers.no_key'));
}
