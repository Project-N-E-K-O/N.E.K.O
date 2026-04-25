/**
 * page_api_keys.js — Settings → API Keys 子页.
 *
 * 展示 tests/api_keys.json 各字段的 "已填 / 未填" 状态. 后端保证**从不**回显
 * 明文 key — 浏览器只拿到 boolean + 字段名. 点 [Reload] 强制后端 re-read.
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { formatIsoReadable } from '../../core/time_utils.js';
import { el } from '../_dom.js';

export async function renderApiKeysPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.api_keys.heading')),
    el('p', { className: 'intro' }, i18n('settings.api_keys.intro')),
  );

  const body = el('div', {});
  host.append(body);

  async function refresh(force) {
    body.innerHTML = '';
    const res = force
      ? await api.post('/api/config/api_keys/reload', {})
      : await api.get('/api/config/api_keys_status');
    if (!res.ok) {
      body.append(el('div', { className: 'empty-state' },
        i18n('errors.server', res.status)));
      return;
    }
    const status = res.data;
    body.append(renderStatusCard(status, refresh));
  }

  await refresh(false);
}

function renderStatusCard(status, refresh) {
  const card = el('div', { className: 'card' });

  const meta = el('div', { className: 'kv-list' });
  meta.append(
    el('dt', {}, i18n('settings.api_keys.path_label')),
    el('dd', {}, status.path + (status.exists ? '' : ` ${i18n('settings.api_keys.path_missing')}`)),
  );
  if (status.last_mtime) {
    // P25 Day 2 hotfix (2026-04-23): stop displaying UTC wall clock
    // where the tester expects local. ``toISOString()`` is UTC-only,
    // so for an Asia/Shanghai browser (UTC+8) the displayed "last_mtime"
    // was 8 hours behind the actual file mtime. Match the Diagnostics
    // Logs/Errors pages by routing through :func:`formatIsoReadable`.
    const ts = formatIsoReadable(new Date(status.last_mtime * 1000));
    meta.append(
      el('dt', {}, i18n('settings.api_keys.last_read')),
      el('dd', {}, ts),
    );
  }
  if (status.load_error) {
    meta.append(
      el('dt', {}, i18n('settings.api_keys.load_error_label')),
      el('dd', { style: { color: 'var(--err)' } }, status.load_error),
    );
  }
  card.append(meta);

  const reloadBtn = el('button', {
    onClick: async () => {
      reloadBtn.disabled = true;
      try {
        await refresh(true);
        toast.ok(i18n('settings.api_keys.reload'));
      } finally {
        reloadBtn.disabled = false;
      }
    },
    style: { marginTop: '8px', marginBottom: '12px' },
  }, i18n('settings.api_keys.reload'));
  card.append(reloadBtn);

  // 主表: known fields + providers 关联.
  const providerInverse = {};
  for (const [prov, field] of Object.entries(status.provider_map || {})) {
    if (!providerInverse[field]) providerInverse[field] = [];
    providerInverse[field].push(prov);
  }

  const table = el('table', { className: 'tbl' });
  table.append(el('thead', {},
    el('tr', {},
      el('th', {}, i18n('settings.api_keys.columns.field')),
      el('th', {}, i18n('settings.api_keys.columns.provider')),
      el('th', {}, i18n('settings.api_keys.columns.status')),
    ),
  ));
  const tbody = el('tbody', {});
  for (const [field, present] of Object.entries(status.known || {})) {
    const linked = providerInverse[field] || [];
    tbody.append(el('tr', {},
      el('td', { className: 'tbl-key-field' }, field),
      el('td', { className: 'muted' }, linked.join(', ') || '—'),
      el('td', {},
        el('span', { className: 'badge ' + (present ? 'ok' : 'err') },
          present ? i18n('settings.api_keys.status_present') : i18n('settings.api_keys.status_missing'))),
    ));
  }
  table.append(tbody);
  card.append(table);

  if ((status.extra || []).length) {
    card.append(el('h3', { style: { marginTop: '16px' } },
      i18n('settings.api_keys.extra_heading')));
    const extra = el('ul', { style: { margin: 0, paddingLeft: '20px' } });
    for (const field of status.extra) {
      extra.append(el('li', { className: 'tbl-key-field' }, field));
    }
    card.append(extra);
  }

  return card;
}
